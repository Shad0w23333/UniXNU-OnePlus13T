# 默认生效改动审查（键 default-on-review，2026-10-31 10:30–11:10）

范围：下一张镜像里**不加启动参数就生效**的改动。没碰手机、没提交、没改共享集成文件（Stage7Probe、components.toml、board.toml、patches/xnu/series、make-a830-layout.sh、make-phone-seed.py）、没写 `UniXNU/out-gpu`、没动 PaganiAudio / PaganiHaptics / PaganiArmDTPlatform。苹果文件只在本机读。审查中途内核被重建（10:59，`a00cbabd…`），rtc 的 `RamoopsLog` 改动也合入了，下面的结论已按这两处重新核过。

## 0. 结论

**没有发现会让现在能用的手机开不了机、卡死或桌面坏掉的缺陷，七项都可以默认开着上机。** 确认并修掉两个小问题（都不在桌面主路径上）；一个风险只有上机才有数据（第 2 项的内存余量）。

| # | 对象 | 结论 | 处理 |
| --- | --- | --- | --- |
| 1 | NativeKeyStoreEvents（钥匙包） | 无阻断缺陷；securityd、keybagd、MobileKeyBag 的消费路径逐条看过，不新增循环或阻塞。有一处输入校验缺口 | **已修 F1**：带外输出超过 1 MiB 直接拒绝，不再整段上锁页 |
| 2 | phone-gpu-client-memlimits.patch | 代码无缺陷；512 MB 是上限不是预留，自身不会造成风暴 | 未改，留风险 R1（A 包内存低点离压力线只差约 170 MiB） |
| 3 | PaganiArmCpu 调速重试 | 无死锁、无热循环，温控路径不变，模式 1 搬动等价 | 未改 |
| 4 | PeripheralIO.h 共享锁 | 自旋有界，超时路径正确，与 PaganiBattery/PaganiWifi 协议兼容；最终内核只有一个锁字且可写 | 未改 |
| 5 | phone-keyboard-trace.patch v2 | 选项关时零行为变化；下一张镜像不带 `pagani-kbtrace` | 未改 |
| 6 | Stage7Probe、BootProgress 复位 | 弱默认都被强定义取代；`pagani-reset-seq` 缺省（含 rtc 合入的 `pagani-halt` 差异）时复位路径与之前逐行等价 | 未改 |
| 7 | AdrenoMetal | 不会拖慢或弄坏 backboardd；两侧布局一致；发现一处非桌面路径的回归 | **已修 F2**：无纹理附件上残留的 resolve 纹理不再让整个 pass 被拒 |

## 1. NativeKeyStoreEvents

已查（`NativeKeyStoreEvents.cpp`、`sysfix/keybag/re/*.s`、本次重新反汇编）：

- **用户客户端校验**：标量个数与 int32 范围、句柄、类别、长度（16..512、8 对齐）、内联/带外输入、输出容量，全部在碰内核缓冲前检查。解包 `m - 8 ≤ 512` 后才进 `plain[512]`，包装 `wrapped[520]` 够用。XNU 的 `is_io_connect_method` 里 `structureInput`/`structureOutput` 恒非空，`scalarOutputCount` 上限 16，与代码假设一致。
- **libaks 契约**：对照 `_aks_wrap_key` / `_aks_unwrap_key` / `_aks_lock_device` / `_aks_prederived_is_enabled` 反汇编复核：`[bag, class]` 都是符号扩展，wrap 标量输出 1 个，unwrap 无标量输出，0x1f 带外 0x8000 字节。
- **RFC 3394**：按算法逐步核对；第 4.6 节向量与 RFC 一致；变异与 1920 个篡改块的测试已覆盖。
- **corecrypto 第一次调用**：`g_crypto_funcs` 由真实的 `com.apple.kec.corecrypto` 注册（`kernel.json` 的 `module_entrypoints` 里有）。`kernelAesUsable()` 先查模式、块长、上下文大小，再跑自检，防的正是 `aes_encrypt_key` 的 panic。`aes_encrypt_ctx` 约 280 字节；真编译后栈帧 `wrapKey` 0x280、`rfc3394Wrap` 0x1b0，合计不到 2 KiB，安全。任何一步失败的后果都是回 `0xe00002d8`，securityd 得到 `errSecNotAvailable`，等于今天的行为。
- **锁**：只有首次建钥匙时持 `IOLock`；其中的 `boot_progress_trace` 是有界自旋、不睡眠；钥匙建好后只读。
- **17/35 描述符改动**：prepare → writeBytes → complete 成对；libaks 这两个选择子走内联，默认路径不变。
- **securityd 与其它消费者**（重点是“以前失败、现在成功”的分支）：
  - 种子 `/private/var/Keychains`、`/private/var/keybags` 都是空的（只读挂载 `s3-full.dbrdisk` 核对），没有旧条目会被解码错误删除。
  - `MKBDeviceGetGracePeriod` 现在回空字典而不是 NULL；唯一静态导入者 ManagedConfiguration 的 `MCKeybagCurrentPasscodeGracePeriod` 用 ObjC 取值，缺键得 0，与 NULL 同。
  - `MKBKeyBagMementoIsSupported`、`AKSIdentityCopyGroupUUID`：缺键走假/错误分支，无空解引用。
  - `aks_prederived_is_enabled`：`pdps` 缺失时 `_ccder_decode_tl(NULL)` 开头 `cbz x2` 返回 0，不崩，结果“未启用”。
  - keybagd 三处 `aks_get_configuration`：`is_user_primary` 缺键走错误日志；`report_seshat_usage` 缺键得 -1，从函数名看只影响遥测；另一处被锁状态位 0x2000 挡在前面，到不了。
  - `MKBLockDevice`：ManagedConfiguration 与 SpringBoardFoundation 都不看返回值、不等后续状态变化；ClarityBoardFoundation、SoftwareUpdateServicesUI 也导入它，但属于辅助访问和软件更新的路径，没逐行看。
  - securityd 里还回 `0xe00002c7` 的选择子（0x2b ACL、0x17、0x28 `aks_generation`、0x81–0x84）都是按需路径，失败映射到 `errSecNotAvailable`，和今天一样；没看到会重试的路径，但这是读代码的判断，未实测。
  - CKKS/Octagon/SOS 都以账户为前提，这台机器没有账户；条目写成功是 iOS 的常态路径。全量枚举所有守护进程做不到，所以 `pagani-keybag=0` 保留为回退。

**F1（已修，低）**：`writeOutput` 在 `prepare()` 前没有限制描述符长度。`prepare()` 会锁住调用者给的整段缓冲（最大近 4 GiB），而写入只有几十字节；任何能打开 AppleKeyStore 的进程都能借此短暂上锁大量内存。现在超过 1 MiB 回 `kIOReturnBadArgument`，libaks 最大只用 32 KiB。`Test/keybag.cpp`、`Test/boundary.cpp` 已加用例（1 MiB 通过、1 MiB+1 拒绝且不 prepare）。

预期修正：`AKSIdentityCopyGroupUUID` 传的是负的 uid，这类 0x1f 会回 `kIOReturnNotFound`，所以 R 行里的 0x1f 不一定降到 0。看对应的 `keybag: refused configuration … bag -N` 行即可，不是故障。

## 2. phone-gpu-client-memlimits.patch

已查（构建树 `kern_memorystatus.c`、`IOUserClient.cpp`）：

- **标志位** `0x00400000` 未被占用；`p_memstat_state` 只有 fork 时整体清零（子进程本来就不是 GPU 客户端），其余都是按位操作；用户可见状态走 `memorystatus_build_state`，不暴露这一位。
- **锁**：调用点在 `setTerminateDefer` 之后、不持任何锁；`task_set_phys_footprint_limit_internal` 在 `proc_list_lock` 下调用是现有用法；对 `current_task` 的 `ledger_check_new_balance` 只置 AST；U 行的 `boot_progress_trace` 不睡眠，可在 `proc_list_lock` 下调用。
- **打开路径**：只有成功的打开才到钩子，进度标记是被拒的打开。`MetalPluginName` 在 `kMetalPrepare` 设好、`registerService` 之前。加速器重写了 `getProperty`，每次成功打开多记一次诊断计数，无害。
- **所有写入点**：`memorystatus_update` 的 spawn 分支、`memorystatus_set_memlimit_properties_internal` 都有底线；其余只升不降（`memorystatus_raise_memlimit` 系列）或读已抬高的值。RunningBoard 读回再设幂等。
- **内存能不能被 512 MB 拖垮**：
  - 底线不预留内存，只改“多大才杀”。受影响的只是上限低于 512 MB 且确实打开 GPU 的进程：iconservicesagent、PosterBoard、WidgetRenderer_Default、海报与小组件扩展。App 的上限本来就更高。
  - 6 GiB 窗口的阈值（3–6 GiB 档）：临界 4% ≈ 245 MiB，空闲线 9% ≈ 552 MiB，压力线 15% ≈ 921 MiB。
  - 全局 jetsam 仍按优先级带工作，先杀的正是空闲带的扩展，杀掉后只在需要时才拉起，不是紧循环。软上限抬高后，这些进程不再是高水位优先候选，这是唯一的策略变化。

**R1（留下，需上机数据）**：`avail` 低点 D2 2231 MiB（170 s）、D 1789 MiB（140 s）、**A 1094 MiB（571 s）**，A 只比压力线高约 170 MiB，比空闲线高约 540 MiB。修好壁纸后十来个海报扩展可能同时用 Metal，每个多几十 MB 就会进压力区。这是正常回收，不是风暴，但会看到空闲带被杀。没有单进程实测，所以不改默认值；若 `avail` 长期低于 235000 页，或出现成串的 `jetsam: … over …`，用 `pagani-jetsam=256` 试（不用重编）。

## 3. PaganiArmCpu

已查 `ScmiPerf.h`、`CpuFreqGovernor.h` 与基线的完整差异、`ArmCpu.cpp`：

- **无锁死**：`gCpuFreqLock` 只有调频线程自己用；`transfer()` 时序未改（等通道与等应答各不超过超时）。
- **无热循环**：主循环 `IOSleep(window)`；启动重试每次至少睡 1 ms、总次数受 48 次预算限制；退避期 `controlReady` 为假时不发消息。
- **预算**：设置连串 48 次、无应答 6 次、复查每轮 4 次；`sets` 不被读成功清零。只有复查 BUSY、没有设置时不会关，但也只是每 5 s 4 次，不是循环。
- **温控**：`governorStep` 每窗口照跑，热上限仍即时算出；退避最长 1.28 s 内推迟下发，CPUCP 固件自身限频不受影响。硬失败仍回开机档，与以前相同。
- **模式 1 搬动**：5 s 复查从线程循环搬进 `controlWindowCpus`，条件与周期等价；下一张镜像用的是模式 2。
- `armcpu_cpufreq_state` 没有其他读者，启动等待期间保持 1 无副作用。

无确认缺陷。

## 4. PeripheralIO.h

- 自旋上限 5000 × 10 µs = 50 ms；超时后照写并计入 `sm8750_gcc_vote_unlocked`，不会挂死。
- 与 PaganiBattery（CAS 取、`store 0` 放）、PaganiWifi（CAS 取、CAS 放）兼容；启动顺序上触摸最先，实际无竞争。
- 触摸的位 `0x043c0000` 与旧代码相同；位已置时不写寄存器。写同值本来就是空操作。
- 最终内核里 `_sm8750_gcc_vote_owner`、`_sm8750_gcc_vote_unlocked` 各一个，`(__DATA,__data) weak external`，可写（核对 `f38f9e4b` 与 `a00cbabd` 两个内核）。
- 引用者 PaganiInput、PaganiPanel、PaganiSpeaker 的测试都过；内核零告警。`dts/sm8750.dtsi` 的差异都是 `status = "disabled"` 的节点。

无确认缺陷。留一条文字问题：Stage7Probe 里“touch setup's unlocked update”、PaganiBattery 里“touch 和 speaker 不持锁”的注释已过时。

## 5. phone-keyboard-trace.patch v2（选项关）

- `kdebug_trace64` 多一次 `__improbable` 分支；`kdebug_reset` 后的恢复与 commpage 分支都先判 `pagani_kbtrace_on`；`kdebug_init` 里只多一次启动参数解析。
- 唯一常驻成本：约 11.7 KB bss 和一把未使用的自旋锁。
- 下一张镜像的参数里没有 `pagani-kbtrace`（看的是 `测试镜像/…灵动岛-系统应用.payload.json`）。
- 对未打补丁的 kdebug.c 全部 5 项测试通过，sha1 `7fac08e1…` 能精确还原。

无确认缺陷。

## 6. Stage7Probe 与 BootProgress

- **弱默认**：`pagani_{gpu,adsp,battery,audio,speaker,haptics,wifi,sensors}_start` 在最终内核里都是强定义。speaker、wifi、sensors 不带参数时立刻返回。
- **复位路径**：`reset_seq_mask == 0` 时 `reset_now` 的 `held` 恒 0，`gunyah_wdog_before_reset`、`psci_reset2()` 条件与旧代码相同，`ramoops_reset_seq_status()` 回 NULL。rtc 合入的 `reset_power_off` 默认 0，新增分支都不走。

无确认缺陷。

## 7. AdrenoMetal

- **`supportsFamily:` 成本**：只有 `cmd` 是该选择子、参数在 Apple3..AppleLast、且运行时回真时才调 `dladdr`，实际只有 Apple3。QuartzCore 21H461 全部 19 个调用点都在 `new_metal_context`（11）、`load_pipeline_state`（2）、`load_pipeline`（4）、`warmup_shaders`（2），没有每帧路径。本机量 `dladdr` 约 12 µs，4 线程并发没有明显的锁竞争。commit `31867dc` 说的“每秒几千次询问”走的是别的选择子，在 `cmd != family` 处直接返回。
- **线程安全**：`dladdr` 线程安全，调用时不持 `askAnswer` 的互斥锁，计数是原子量。`objc_msgSend$supportsFamily:` 存根不改 LR，调用者判定成立。QuartzCore 的回答不变。
- **resolve 布局**：C 与 Rust 字段一致，`NmbRenderPassDescriptor pass={}` 清零。反汇编种子里的插件确认 Rust 侧步长 0x70（`depth` 0x380、`stencil` 0x3f0、`width` 0x460），没有新旧混编。QuartzCore 根本不设 `resolveTexture`，backboardd 路径不变。
- **ClientReport / MemoryReport**：backboardd 只是监控线程每 10 s 多发一行，`tu_darwin_memory_stats` 全是原子读；每帧路径没有新增。客户端报告线程只给按名字列出的进程。
- `self.status` 和 `nmb_command_buffer_metal_error_code` 每次完成各一次，成本可忽略。

**F2（已修，低，非桌面路径）**：前端把 resolve 纹理放在 `if(!a.texture)return true;` 之前交给运行时，而运行时对“无纹理附件带 resolve”回 `NotImplemented(Render)`，整个命令缓冲失败；Metal 是忽略这种附件的。用 MoltenVK 主机测试复现：修前 `FAIL leftover resolve texture on attachment 1`，修后通过。这是今晚加 resolve 时引入的回归，只会碰到复用描述符的客户端。`bridge/msaa_resolve_host.mm` 已加用例。

## 8. 改动清单

差异：`build-userspace/a830-inputs/default-on-review/source-changes.diff`。

| 文件 | 内容 | 何时生效 |
| --- | --- | --- |
| `UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents/NativeKeyStoreEvents.cpp`、`README.md`、`Test/keybag.cpp`、`Test/boundary.cpp` | F1 | 已进内核 `a00cbabd`（10:55 改，10:58 编入） |
| `UniXNU/drivers/AdrenoMetal/NativeBridgeMetalFrontend.mm`、`bridge/msaa_resolve_host.mm` | F2 | **种子 `s3-full` 里还是旧插件**。不急，下次重建插件时带上；CDHash 会变，信任包要随之重做 |
| `sysfix/jetsam/test-gpu-client-memlimits-patch.py`、`sysfix/keyboard/test-phone-keyboard-trace.py` | 只改测试：构建树已打补丁后，这两个测试默认路径会失败；现在先在副本上撤掉补丁再测 | 立即 |

`sysfix/keybag/NativeKeyStoreEvents.diff` 与 `notes.md` 里的 sha256 已比现文件旧，以工作区为准。

## 9. 留下未改

1. R1：内存余量（第 2 节）。
2. 注释过时，不影响行为：Stage7Probe（共享文件）、PaganiBattery 的 `BatteryI2c.h` 与 `PaganiBattery.cpp`；`MemoryReport.h`、插件和 `NativeMetalObjects.rs` 里“映射算进 footprint”的说法与 jetsam 笔记第 2 节相反。
3. `inventory/code-run-host-tests.sh` 还没有 `kskeybag` 一行（文件归盘点）。
4. 主机工具 `carenderer_host` 修前修后都失败（Mac 的 QuartzCore 渲不出像素），不在任何检查脚本里，和这次改动无关，不能当桌面证据。桌面的真实证据仍是 D2 上机。

## 10. 主机测试（修复后全量）

全部通过（日志在草稿目录，不外传）：

- BootProgress：ramoops、crash、gunyah-wdog、console（含 rtc 合入后的 `RamoopsLog`）
- PaganiArmCpu：spi-gate、scmi-perf 2246649 项、psci-idle 2634553 项
- PaganiPanel panel-power 314 项；PaganiInput peripherals；PaganiSpeaker hw 248 项、gccvote 162646 项
- NativeKeyStoreEvents：两套（ASan/UBSan），另跑 `-O2` 与 ThreadSanitizer；`compile-keystore.py` 真编译 0 错误 0 警告，234 个符号都在内核里
- jetsam：10 项含 `--compile` 真编译；键盘探针 5 项
- AdrenoMetal：surface_maintenance、owner_environment、client_rule、family_answer、client_report；MoltenVK 上 msaa_resolve、nil_args、renderbox、present、frontend_host；iOS 目标编译通过

## 11. 上机看什么、怎么回退

| 看什么 | 正常 | 不对时 |
| --- | --- | --- |
| `keybag:` U 行 | `pagani-keybag=1`、`up: 10 class keys`、`first wrap by …` | `keybag: off …` 是回到今天的行为；系统异常用 `pagani-keybag=0` |
| `avail` 与 `jetsam:` U 行 | `avail` 大多高于 235000 页；没有成串 `over … limit` | `pagani-jetsam=256`，或 `=0` 回到苹果上限 |
| `cpufreq:` U 行 | 没有 `cpufreq: off:`，有 BUSY 时能看到 `retry ok` | `pagani-cpufreq-retry=0` |
| 启动后 `CPU` 行 | `err` 小，不再锁死在 1986/1012 MHz | 留日志 |
