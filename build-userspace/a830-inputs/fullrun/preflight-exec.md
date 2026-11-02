# 上机前审查：exec 路径钩子与启动提升消费端

审查对象：内核 `8c042601`（`kernel.macho`，11-01 05:46 重编完成）、工作树 `.cache/xnu-work`、种子 `final/s6-final.dbrdisk`（`39e635cb`）。全部只读，没有碰手机。

## 一、结论

- **本范围内没有发现新的 crash 或 hang。** 修过的 `.app/` 计数在新内核里已经反汇编核对，路径指针在调用点有效。
- 只有两条 minor，都不影响启动，**不需要为它们重编内核或重做种子**：
  - E1：判据“`SystemOS root refs` 是 use 2 和 3”已被真机日志推翻，实测是 8 和 7。不改的话下一轮会被误读成修复失败。
  - E2：补丁注释和本次任务的前提“应用都由 xpcproxy exec”不对。真机上应用和扩展由 launchd 直接 spawn。计数不受影响。
- 启动提升消费端：没有积压、回绕、开机拉满的问题。
- `powerlogHelperd` 禁用：没有 launchd 依赖，PowerLog 客户端不会阻塞或崩溃。`EnableMLTapTyping=false` 阶段 1 已在真机跑过。

## 二、新补丁 `phone-app-exec-count.patch`

### 2.1 指针生命周期（`bsd/kern/kern_exec.c`，行号按当前工作树）

| 时刻 | 位置 | 事实 |
|---|---|---|
| 分配 | L2758 `execargs_alloc` | `ip_strings` 从这里开始有效 |
| 写路径 | L2763 `exec_save_path` | 失败直接 `goto bad_notrans`，走不到计数 |
| 可能改路径 | L2768 `pagani_diag_launch` | 失败同样 `goto bad_notrans` |
| 使用 | L2905 `strnstr(excpath, ...)` | 只在 `error == 0` 分支，`bad:` 之前 |
| 释放 | L2927 `execargs_free` | 在 `bad_notrans:` 之后，晚于使用点 |

调用方只有两处：`posix_spawn` L4793、`__mac_execve` L5803。`process_signature` 在这之后（L5222、L5838），所以旧补丁放在那里才读到 NULL。

### 2.2 各路径逐项

| 情况 | 结论 | 依据 |
|---|---|---|
| `posix_spawn` 普通 spawn | 有效 | 真机上应用和扩展走这条，见 2.4 |
| `POSIX_SPAWN_SETEXEC` | 有效 | 同一函数，只有 `p` 改取 `current_proc()`（L2755），计数不用 `p` |
| `__mac_execve` | 有效 | 同一函数；`load_init_program` 也走这里，`/sbin/launchd` 不含 `.app/` |
| 脚本解释器（`-3`） | 有效，指向解释器路径 | `exec_shell_imgact` L698-700 重写同一偏移；解释器路径上限 `IMG_SHSIZE`，返回值虽被忽略但实际不会失败；即使失败，`memset` 已清零，`excpath` 是空串，不是野指针 |
| `encapsulated_binary`（`-2`） | 不变 | 只重读 `ip_vdata`，在 `ip_strings + NCARGS + PAGE_SIZE`，不与路径重叠 |
| `pagani_diag_launch` 换路径 | 有效 | L2630 重置，L2631 写入常量路径，地址仍是 `ip_strings + 16`，以 NUL 结尾 |
| `exec_save_path` 的 NUL | 有保证 | `copyinstr`/`copystr` 成功才返回 0，超长返回错误 |
| `exec_mach_imgact` 内重存路径 | 不适用 | L1301 在 `#if __x86_64__` 内，arm64 没有这段 |
| `exec_add_*`、`exec_copyout_strings` | 不改路径 | 只往 `ip_strendp` 后追加，或拷出 |
| `badtoolate` 且非 spawn | 计数多 1，无害 | L2150 处把 `error` 改成 0，进程随后被杀 |

### 2.3 新内核反汇编核对

- `_exec_activate_image` 在 `0xfffffff00bd39790`。两处 `bl _strnstr` 在 `0xfffffff00bd3a160`、`...a178`，前面有 `cbnz w23`（`error`）判断。
- `x0` 来自 `x22`；`x22` 在 `0xfffffff00bd39de0` 从 `[sp, #0x28]`（`excpath`）载入，**在内联的 `pagani_diag_launch` 之后**，所以换路径也被取到。之后到调用点之间 `x22` 没有被改写，`namei`、`exec_mach_imgact` 都保留被调用者保存寄存器。
- `x1` 指向的字符串从镜像读回是 `.app/\0.appex/\0`。
- 扫了 `0xfffffff00bd33000..bd40000` 整段：`_strnstr` 只有这两处调用，`_process_signature` 里已经没有。

### 2.4 路径是否真实

**更正前提（E2）**：`20261031-223924` 这一轮 530 个 pid 里，333 个经 xpcproxy，196 个由 launchd 直接 spawn。应用和扩展都属于后者，例如 pid 451：只有 `launchd exec load_machfile` 和 `News exec process signature`，没有 xpcproxy 行（`merged.log:7242-7245`）；守护进程才走 xpcproxy（`8808-8816`）。

两条路都进 `exec_activate_image`，所以计数不受影响。匹配能力有真机旁证：

- 【真机】`k180` 行 `ax 76/27`（`merged.log:4658`）由 `pagani_diag_extension(*excpath)` 算出，用的就是同一个 `excpath`。
- 【真机】同期直接 spawn、名字含 Widget 的进程 28 个，去掉 `WidgetRenderer_Default` 正好 27，经 xpcproxy 的 0 个。所以直接 spawn 的 `excpath` 确实带 `.appex/`。
- `.app/`：日志没有打印过应用主程序的完整路径，这一点是【推测】。依据是应用可执行文件必在 `.app` 目录内，而且 `PlugIns` 路径本身也含 `.app/`。下一轮用判据验证，见第七节。

## 三、钩子逐项

“已跑过”指阶段 1 完整包 `20261031-223924` 的真机证据，这些代码在阶段 1 之后没有改动。

| 钩子 | 位置 | 指针与生命周期 | 真机执行证据 |
|---|---|---|---|
| `boot_progress_process_stage` load_machfile | L1354/1356 | `p->p_name` 在 proc 内 | 已跑过，862 次 |
| `pagani_diag_launch` | L2768 | 同上一节；`imgp` 只作身份比较 | `merged.log:1559` |
| `pagani_diag_activated` | L2934 | `ndp` 已释放但不用它；`imgp`、`p` 由调用方持有 | `act 0` |
| `pagani_diag_launch_constraints` | L9862 | `imgp->ip_vp` 到 exec 结束才 `vnode_put`；旧 vnode 只比指针和 vid，不解引用 | 已跑过 |
| `pagani_diag_signature` | L10027 | reason 在后面 `psignal` 消费之前读取 | `sig 0` |
| `boot_progress_process_stage` signature | L9843、L10007、L10009 | 同上 | 已跑过 |
| `boot_progress_exit` | `kern_exit.c:1901` | `p_exit_reason` 到回收才释放 | 已跑过 |
| `boot_progress_critical_exit` | `kern_exit.c:1866` | BootProgress 里是空函数 | 无需执行 |
| `boot_progress_crash_signal` | `kern_sig.c:3108` | 当前线程、proc 已解锁；`copyin` 在线程上下文 | X 记录，`merged.log:6366` 起 |
| `boot_progress_user_abort`、`user_breakpoint` | `sleh.c` | 空函数 | 无需执行 |
| `pagani_kbtrace_note` | `kdebug.c:1038` | `p` 来自系统调用；调用方传的是当前进程 | v2 已跑过；v3 只有主机测试 |
| `boot_progress_stall_kbt` | `kdebug.c:2865` | 只传 pid 和组号；锁外调用，`stall_mode` 先判 | **未执行**，要到键盘事件 |
| `pagani_stall_dispatch` | `sched_prim.c:3792/3840` | proc 与 task 同块分配，线程持 task 引用；`run.name` 立即拷走 | 崩溃轮 0.133 s 起开启，含 pid 1 用户线程 |
| `boot_progress_stall_ipi_*`、`idle` | `cpu_common.c:511/598`、`cpu.c:570` | 只传 CPU 号 | 同上 |
| checkdirs 修复、`vfs_setmntsystem` | `vfs_syscalls.c:2522`、L7710 | 启动时走过；`MNTK_SYSTEM` 只影响卸载和根重命名三处 | 启动段已跑，关机段未跑 |

fork 路径：`kern_fork.c` 没有项目改动，governor 只读 `nextpidversion`。

阶段 1 之后进 XNU 的补丁只有三个：`phone-checkdirs-kernproc`、`phone-app-exec-count`、`phone-stall-probe`。其余补丁的修改时间都早于阶段 1 构建。`phone-stall-probe` 在 exec、fork、exit、signal 路径上没有改动。

`kbt` 表变大后，`pagani_kbtrace_note` 栈帧约 3.1 KiB（反汇编 `sub sp, sp, #0xbc0`），在系统调用浅栈上，没有问题。

链接核对：`boot_progress_stall_run` 等符号在新内核里解析到 BootProgress 的强定义，不是 XNU 的弱空函数。

## 四、启动提升消费端

文件：`drivers/PaganiArmCpu/ArmCpu.cpp:454-457`、`CpuFreqGovernor.h:397-414,425-429`。

| 问题 | 结论 |
|---|---|
| 字段顺序 | 花括号初始化 `{procs, asks, screen, spawns}` 与结构体一致 |
| 回绕 | 只用 `!=` 比较，`int` 转 `uint32_t`；要在 20 ms 内恰好加 2^32 次才会漏判 |
| 第一次采样的积压 | 不会触发：`governorStep` 首窗口把四个计数全部记为已见（`primed`），调频线程又在 0.21 s 就启动，早于 launchd |
| 开机一直拉满 | 不会：`near` 要求 `touchAt != 0`，`asked` 目前没有调用者（`pagani_cpufreq_boost` 只有定义）。【真机】阶段 1 第一条 launch 在 763 s，就是第一次触摸之后；崩溃轮 0 条 |
| 计数语义 | 每次成功激活加 1，含签名随后被拒的。launchd 的 spawn 和 xpcproxy 的 SETEXEC 不会对同一应用重复计：应用不经 xpcproxy，守护进程路径不含 `.app/` |
| 时间门 | 【真机】News 871.898 s exec，Preferences 877.526 s，对应 launch 行距上次触摸都是 20 ms 左右，远小于 1000 ms |
| 符号 | `_pagani_app_execs`、`_nextpidversion` 都已链接；崩溃轮调频线程已读过 4.8 s |

判读提醒，不算缺陷：`.appex/` 扩展的崩溃重启也算应用启动。阶段 1 里 `AegirPoster` exec 12 次，靠近触摸时仍会出现 `an app start`，不代表过滤失效。

## 五、种子

### 5.1 powerlogHelperd

- **launchd 依赖**：在 `/System/Library/xpc/launchd.plist` 全量搜索三个服务名，只出现在 `com.apple.powerlogHelperd` 自己的条目里。该任务没有 `RunAtLoad`、`KeepAlive`、`_PanicOnCrash`。
- **策略确实生效**：【真机】`k180 ps` 显示 `bluetoothd:- locationd:- CommCenter:-`、`PerfPowerServices:-`，同一 `disabled.plist` 机制。
- **最终种子**：只读挂载核对，禁用 10 项含 `com.apple.powerlogHelperd`，属主 `0:0`，模式 `644`，`disabled.migrated` 在。种子 `39e635cb` 与镜像 `.payload.json` 一致；信任缓存 `9a4701ef` 与阶段 1 相同。
- **`com.apple.powerlogHelperd.XPCService.xpc` 的客户端**：`PowerLog.framework`。
  - `-[PLClientLogger xpcConnectionWithClientID:withKey:withPayload:]`（`0x1a3038a70`）：`clientID` 为 48、50 或 `talkToPowerlogHelper` 时连它，否则连 `plxpclogger`。
  - 两个事件块（`0x1a304683c`、`0x1a3046944`）收到 `XPC_ERROR_CONNECTION_INVALID` 只置一个字节，不重试、不 abort。
  - 同步路径 `xpcSendMessageWithReply:...`（`0x1a30386ec`）用 `xpc_connection_send_message_with_reply_sync`，服务不存在时立即返回错误。
  - 【推断】`plxpclogger` 所属的 `PerfPowerServices` 早已禁用，所以同一函数的失败分支已在真机上长期运行，没有见到客户端因此崩溃。日志没有直接记录这一点。
- **`com.apple.PerfPowerMetricMonitor.xpc`**：从缓存的导入表看，框架只被 `PowerlogHelperdOperators`、`DVTInstrumentsFoundation`、`powerlogHelperd` 自己链接，普通进程不依赖它。没有排除 `dlopen`。
- **`...delegate...proactivenotifications`**：【推断】这个通知包的引用在 `PowerlogHelperdOperators` 里，helperd 不运行就不会发这类通知，也不会有回调。

### 5.2 EnableMLTapTyping=false

阶段 1 的种子已经带这个偏好（`PROGRESS.md:495`），`20261031-223924` 跑了 1181 s。这一包没有新风险。`--sos-off` 同样早已在阶段 1 里。

## 六、发现

### E1（minor）判据 `SystemOS root refs` 是 use 2 和 3 已被真机日志推翻

- 位置：`stage15-small.md:134`；同样的假设在 `phone-checkdirs-kernproc.patch:9` 和 `test-checkdirs-kernproc-patch.py:214,401`。
- 路径：`load_init_program` → `native_owner_cache_prepare` → L7679 `boot_progress_root`。
- 证据：崩溃轮内核已含这个补丁，`merged.log:1343` 写的是 `old ... use 8, root ... use 7`，不是 2 和 3。
- 为什么主机测试抓不到：测试的起始计数 `oldroot.v_usecount = 3` 是假设值，不是测量值。谁持有另外几个引用，无法在主机上得知。
- 影响：不影响启动。8 和 7 既不能证明修复失败，也不能证明修复成功。修前没有这一行，没有对照。
- 建议：判据改成“记录基线 8/7，修复是否有效只看滑动关机有没有 `_vnode_rele_internal` 的 panic”。注释和测试下次动到再改，不需要为此重编。

### E2（minor）注释和任务前提“xpcproxy 执行所有应用”不对

- 位置：`phone-app-exec-count.patch:10`（`kern_exec.c:351`）。
- 路径：launchd `posix_spawn` → `exec_activate_image`（`p` 是新 proc），不经 `POSIX_SPAWN_SETEXEC`。
- 证据：见 2.4。
- 为什么主机测试抓不到：测试只验证路径字符串的匹配条件，不模拟谁发起 exec。
- 影响：计数正确。只是以后若有人写依赖 `xpcproxy` 名字的钩子会判断错。`pagani_diag_launch` 已同时接受 launchd 和 xpcproxy，不受影响。
- 建议：下次改这个补丁时把注释改成“launchd 或 xpcproxy”。

## 七、下一轮怎么读

| 现象 | 含义 |
|---|---|
| 启动越过 `pid=1 launchd exec process signature accepted`，没有 `strnstr` panic | 修复生效 |
| 点开应用后出现 `cpufreq: launch N: an app start` | `.app/` 匹配成立，补上第 2.4 节的【推测】 |
| 点开应用从不出现这一行，但 `ax` 在涨 | `.app/` 的前提错了，看应用路径 |
| 日志没有 `powerlogHelperd` 的 exec 行 | 禁用生效 |
| `SystemOS root refs` 仍是 8/7 左右 | 正常，不是失败 |

## 八、未验证

- 新内核越过 launchd 之后没有上机，全部结论来自读码、反汇编、旧日志。
- `.app/` 对应用主程序路径的匹配没有直接日志，见 2.4。
- `boot_progress_stall_kbt` 和 `pagani_kbt` v3 的 XNU 侧只编译过，没执行过。
- 921 s 第一次唤起 helperd 的客户端是谁，没查出来。不影响结论，它现在只会立即收到错误。
- `root use 8/7` 里多出来的引用是谁持有的，没查。
- 阶段 1 种子 `684396ce` 已被覆盖，无法逐文件比对。能确认的只有信任缓存相同，以及本步工具的摘要检查“只改 `disabled.plist` 和 SOS 偏好”。
- 探针采样线程本身不在本次范围内，只核对了它与 exec、exit 相关的部分。

## 九、本次实际做了什么

- 通读 `kern_exec.c` 全部项目差异，以及 `kern_exit.c`、`kern_sig.c`、`kdebug.c`、`bsd_kern.c`、`pagani_diag.h`、`pagani_kbtrace.h`、`crash-trace.patch`、`phone-stall-probe.patch`、`phone-checkdirs-kernproc.patch`。
- 用 `llvm-objdump` 反汇编新内核的 `exec_activate_image` 和 `kern_exec` 所在区段。
- 用 `ipsw` 和自写脚本查共享缓存 `21H461` 的 PowerLog 客户端。
- 对比 `BootProgress`、`CpuFreqGovernor.h` 与阶段 1 前后的副本。
- 运行 `edit-phone-seed-launchd.py --check`（只读挂载，临时文件在草稿目录）。
- 在草稿目录编了一个只打印结构大小的小程序。除本报告外没有改动任何文件。
