# 增量审查：探针修复与关机进度补丁

审查对象：`BootProgress.c` 的 S1、S2、S3 修复，`phone-stall-probe.patch` 的 `max + 1`，新补丁 `phone-shutdown-progress.patch`，`phone-checkdirs-kernproc.patch` 和 `phone-app-exec-count.patch` 的注释改动。

内核：`aa94decf280c22a9…`（`kernel.macho`），已确认就是两张阶段 1.5 镜像里的内核（`payload.json` 的 `kernel_sha256`，07:26 打包）。

全程只读。没有运行 adb、fastboot，没有碰手机，没有编译。除本文件外只在 `scratchpad/delta` 写了临时文件。

证据标注：【二进制】反汇编或字节比较，【读码】源码，【真机】运行日志，【未验证】读码得不出结论。

## 结论

- **没有发现 crash、hang、storage 级问题。** 这批新代码不会让关机路径或启动路径多出新的崩溃点。
- **发现 1 条 wrong-data（D1）：** S1 修复只盖住卡死的前约 2 秒。没有触摸、没有键盘配对的卡死，超过最后一帧 2 秒后采样器睡下，M 记录又停，那几秒的数据仍然丢。`stage15-probe.md` 第 82 行的判据也因此说得过满。这一条不影响安全，不值得单独重编。
- 其余要核对的问题全部核过，结论写在下面各节。

| 编号 | 严重度 | 一句话 |
|---|---|---|
| D1 | wrong-data | 无确定信号的卡死，2 秒后仍然没有 M，S1 原文举的“帧停 3 秒”第 3 秒依旧丢失 |

## 一、二进制核对

### 与上一版内核逐函数比较

用 `preflight-critic` 留下的 `k2`（`8c042601`）和这次的 `aa94decf` 做归一化比较，只比 `__TEXT_EXEC` 和 `__KLD`。

| 结果 | 函数数 |
|---|---:|
| 完全相同 | 46244 |
| 只差地址、行号 | 755 |
| 指令序列不同 | 11 |

11 个指令不同的函数：

| 来源 | 函数 | 归属 |
|---|---|---|
| 本批 | `_unmount_callback`、`_fps_observe`、`_stall_tick`、`_stall_look_system`、`_boot_progress_stall_thread_times` | 本文 |
| UfsLog | `_ufs_log_writer_tick`、`_boot_log_export_put` | 不在本批 |
| 面板探针 | `panelProbeThread`、`panel_probe_sample_phase` | 不在本批 |
| Adreno830 | `stageMetal`、`smmuFaultSink` | 不在本批 |

新增真实函数只有两个：`_pagani_shutdown_progress`、`panelProbeQuiet`。“只在新版出现”的另外 17 个名字（`_bzero`、`K256` 等）在旧符号表里都有，是这次反汇编覆盖到了 `__hib_text` 的差别，不是新代码。

**“只改了注释”成立：** `_checkdirs_callback` 逐条相同，`_load_init_program`、`_exec_activate_image` 指令数相同，只差地址和行号，都不在 11 个之内。

**没动的路径确实没动：** `_boot_progress_root`、`_ramoops_log_root`、`_poll`、`_reboot_kernel`、`_vfs_unmountall`、`_vfs_iterate`、`_stall_target_task` 逐条相同。

### 新逻辑确实在镜像里

| 项 | 结果 |
|---|---|
| `0xfffffff00bd35c84` | `b _boot_progress_root`（`0x140f5fec`，目标 `0xfffffff00c10dc34`），是真实调用 |
| `_unmount_callback` | 3 处 `bl _pagani_shutdown_progress`，`x1` 都是 `x19`（`mntname`），`x2` 分别是 `ldrsw [x20,#0xc]`、同上、`sxtw w21`（`error`） |
| `_boot_progress_stall_thread_times` | 忙路径 `add w23, w20, #1` |
| `_stall_tick` 内联的 `stall_look_keyboard` | `mov w3,#0x20`，`cmp w0,#0x20`，`b.hi` 早退 |
| `_stall_look_system` | 两段内联 retry，读 `stall_rs+0x10`、`stall_sb+0x10`（`looked`），阈值 `hz/1000*1000` |
| `_fps_observe` 内联的 `stall_second` | 帧、触摸都为 0 时读 `0xfffffff00c3aec20`（`stall_armed`），为 0 才跳过 M |

`kernel.macho` 与 `mach.development.bcm2837.unstripped` 在上述 7 个函数范围内逐字节相同。

## 二、`unmount_callback` 的运行上下文

**调用者**：只有 `reboot_kernel` 的两处（`kern_shutdown.c:272`、`:287`），另有 `imageboot.c:910`，这台机器不走镜像启动。

**线程和锁**：launchd 的 `reboot(2)` 线程，线程上下文，开中断，可抢占。`vfs_iterate` 在回调时只持有该 `mp` 的 iterref（`vfs_subr.c:7411`），`mount_lock` 已放开（`:7426` 之前），没有别的锁。

| 调用点 | 行 | 这一刻 `mp` | 用了什么 | 判定 |
|---|---|---|---|---|
| `unmount skipped` | 4261 | 持 iterref，活着 | `mntname`、`uip` | 安全 |
| `unmount enter` | 4265 | 持 iterref，活着 | `mntname`、`uip` | 安全 |
| `unmount returned` | 4270 | **可能已释放**，根卷在 `vfs_syscalls.c:3060` `zfree(mount_zone, mp)` | `mntname`、`error` | 安全，没碰 `mp` |

`mntname` 是 `:4255` 从 `ZV_NAMEI` 分配的独立缓冲区，`:4256` 用 `strlcpy` 拷入，`:4279` 才 `zfree`，三个调用点都在它之前。第三处【二进制】：`mov x21, x0` 已把 `mp` 寄存器覆盖成 `error`，`x1` 取的是 `x19`，生成代码里也不可能再用 `mp`。

`uip` 指向 `vfs_unmountall` 栈上的 `ui`，整个回调期间有效。

### 两遍卸载

| 阶段 | 行 | launchd | 其他进程 | 文件系统 |
|---|---|---|---|---|
| `proc_shutdown(TRUE)` | 233 | 活着，`:745` 提前返回 | 被杀 | 全部还在 |
| 第一遍 `vfs_unmountall(TRUE)` | 272 | **活着，未挂起** | 已无 | 尾部先卸；跳过 `MNT_ROOTFS` 和 `MNTK_SYSTEM`（根卷、devfs、SystemOS），其余 `dounmount` |
| `proc_shutdown(FALSE)` | 279 | `:752` 挂起 | 已无 | 只剩根卷、devfs、SystemOS |
| 第二遍 `vfs_unmountall(FALSE)` | 287 | 已挂起，调用线程仍在内核里走完 | 已无 | SystemOS → devfs → 根卷 |

【真机】`20261031-223924-stage1-full-1` 的 panic 落在关机路径上，`preflight-refcount.md` 的账本推断它在第二遍的根卷卸载里。日志没有调用栈，“第二遍”仍是推断，这次的 R 记录正好能确认。第一遍及其之前的上下文（调用线程、`vfs_iterate`）无论如何都已实际跑过。

**读日志提示：** SystemOS 也挂在 `/`（【真机】`SystemOS vnode lookup enter value=0x0: /`）。所以每一遍都会出现两个名字同为 `/` 的挂载。第二遍的顺序是：第一个 `/` 是 SystemOS，中间 `/dev`，最后的 `/` 才是根卷。看到 `unmount enter: /` 时要看 `/dev` 过了没有。

## 三、`boot_progress_root` 全路径

### 这条路不是启动早期专用

【真机】上一轮 1161.906 s 还有 `R stage7 value=0x13: display client`，panic 屏显示 `root: stage7`、`root seq 0x61`、`root stage age 0x15`。这条路经 `boot_progress_display_status`（`Stage7Probe.cpp:74`）在系统运行 19 分钟后走过，屏幕段里的 `root_stage`、`root_sequence`、`root_tick` 都更新了。而且【二进制】`_boot_progress_root` 和 `_ramoops_log_root` 与那一版逐条相同。

### 逐段

| 段 | 行 | 上下文与结论 |
|---|---|---|
| `ramoops_log_root` | `RamoopsLog.c:1433` | `lock_log(0)` 关中断自旋，上限 2^18 次，超时丢记录不死等。`scratch` 512 字节在锁内用，`put_char` 有界。写的是已映射的保留内存，不碰文件系统 |
| `irq_save` | `BootProgress.c:198` | 试锁，不自旋。别的 CPU 持 `console_lock` 就跳过屏幕段，R 记录已经写了，不丢 |
| `busy` | 471 | 只在 `console_lock` 内变化，拿到锁时一定是 0，除非 panic |
| `copy_line`、`record_stage` | 252、260 | 都有界。`root_detail` 192，`stages[]` 80，`entry` 的拼接判了 `n + 3 < STAGE_BYTES` |
| 屏幕绘制 | 484–491 | 见下 |

### 屏幕绘制会不会写到不属于它的内存

**这两张镜像根本不画。** `payload.json` 都是 `pagani-overlay=0`，`overlay_off()`（`:79`）为真，`clear_line`、`glyph_pixels` 第一行就返回，`putc_raw` 只改 `row`、`column`。

**即使 `pagani-overlay=1`：** `display_owned` 为 1 后 `clear_line`（`:297`）和 `glyph`（`:345`）也都返回。`display_owned` 只有“第一帧拷贝就失败”才清（`Stage7Probe.cpp:186`）。

**即使画了：**

- `framebuffer` 是 `early_boot_progress_vm_ready`（`:1134`）的 `ml_io_map_wcomb` 映射，全文件没有解除映射的地方。
- `boot_progress_bind`（`:397`）在物理地址相同时保持这个别名。
- 显示驱动用自己的 `IOMemoryMap`（`GOP mapping ready`），不动这份映射。
- 每次写都判了 `y + LINE > height`、`x + CELL > width`。`row` 绕回有 `(row + 1) * LINE > height` 保护。

### 长路径

`f_mntonname` 最长 1023 字符，`strlcpy` 保证有结尾。三处都安全截断：ramoops 511 字符（超 200 拆 `+` 记录），`root_detail` 191，`stages[]` 79。这台机器的挂载点都很短。

### 并发

关机时的并发来源：`boot_progress_exit`、`poll`、`boot_progress_gpu_rows`、`boot_progress_cpu_row`。

- 屏幕侧 `console_lock` 是试锁，`status_store` 这类等待者自旋时已开中断，持锁方这条路 overlay 关时只有几次字符串拷贝。
- ramoops 侧 `lock_word` 单独一把，两把锁在这条路上不嵌套（`ramoops_log_root` 放锁后才 `irq_save`）。
- 持锁都关中断，不会被同 CPU 的中断重入。

没有死锁路径，也没有共享缓冲区串写。

## 四、根卷卸载之后日志还能不能写

**ramoops：** 写 `copies[]`（保留 DRAM 的映射），和文件系统无关。`log_enabled` 只在早期初始化时变化，关机不动它。

**`UfsLog.c` 写线程：** 不依赖任何已卸载的东西。

| 依赖 | 来源 | 卸载后 |
|---|---|---|
| 窗口 LBA | `gLogRegionFirst`，打开时算好 | 内存里，不变 |
| 写入 | `hostWrite4k` → `gHost`，`hostInWindow` 判窗口（`RootCommands.h:373`） | 不经 vnode、IOMedia |
| GPT 检查 | `hostRead4k` 直读 LBA 1 | 同上 |
| `msgbufp` | 内核内存 | 不变 |
| 线程 | `IOSleep`、`lck_mtx` | 不变 |

`NativeUfs` 没有 `handleClose`、`systemWillShutdown`、`setPowerState` 的覆写，`doEjectMedia` 返回不支持。根设备 `VNOP_CLOSE` 不会停控制器。

**最后一次快照：** `reset_now` → `ufs_log_quiesce` → `last_snapshot`（`UfsLog.c:669`）读的是 ramoops，R 记录会进去。【真机】`20261031-221300-stage1-smoke-1` 这条路以 state 6 走通过。注意最后快照不含 msgbuf，这正是把进度写成 R 记录的理由。

**如果卡在卸载里：** `poll` 和写线程都还活着，R 记录约 3 s 内上盘（`UFS_LOG_PERIOD_MS`），所以卡住也看得到停在哪个挂载。

## 五、S1：`stall_armed` 的全部转换

`stall_second` 只由 1 Hz 的 `fps_observe` 调用，**M 的上限和预检算的一样：每秒 4 行，最多 896 B/s**。`stall_armed` 卡在 1 也只是把这个上限持续下去，不会更快。

| 转换 | 位置 | 结果 |
|---|---|---|
| 0 → 1 | `stall_arm`（`:1491`），来自帧、触摸、kbt begin | 带 `thread_call_enter` |
| 1 → 0 | 只有 `stall_tick` 末尾（`:1876`） | 之后再查一次帧数，不丢唤醒 |
| 保持 1，继续 tick | `:1868` | 见下表 |
| 保持 1，不再 tick | `:1778`、`:1866` 的 `stall_halted` | 只在 panic 或 `reset_begun` |

保持醒着的条件都有界：

| 条件 | 上限 |
|---|---|
| `newest`、`touched` 在窗口内 | 最后一帧或触摸后 2 s |
| `stall_gap_waiting` | 确定信号后到 8 s 阈值用完 |
| `open` | 永不结束的配对 64 个样本后放弃，约 2 分钟（`stall_kbt_take`） |
| 取不到 `stall_open_lock` | 只有那一拍 |

**卡在 1 的情况：**

- `stall_dead` 或 `stall_halted`：只在 panic 或复位时。panic 时 `poll` 第一行就返回。复位时 `pagani-reset-wdog=1` 最多等约 10 s，这段 M 只进 ramoops 环，UFS 最后快照已经写完，无害。
- `stall_ticking`：只有 `stall_tick` 自己置位，中间没有提前返回，ONCE 线程调用不重入，卡不住。
- 线程调用没跑：`stall_tick` 阻塞在 `task_lock` 上时 M 继续写，这正是想要的。

**console 环：** 不会被冲掉。无帧且醒着的秒数占比很小，上限与预检相同，UFS 窗口不受影响。

**主机工具：** 无帧的一秒里 `M lat` 可能是一个空的 `lat`，`parse_lat` 能处理。没有 F 行时 `join_frames` 跳过，`stall-report.py` 用 `%s` 打 `None`，不会抛异常。

## 六、S2：`stall_target_retry`

**`looked` 的语义**：上次按名字查的时刻，成功失败都记。`t->task && !t->thread` 只可能是“查到任务后线程没找到”，因为 `stall_target_drop` 同时清两个，所以 `looked` 不会被别的路径弄旧。

**沿调用顺序走，线程名永远找不到时：**

| 调用 | 动作 |
|---|---|
| t0 | 查到任务，`map` 1 行，线程找不到，`looked` 刚设，不丢 |
| t0 + 不足 1 s | 任务还在，再找线程，不丢 |
| t0 + 1 s 以后 | 再找线程，还是没有，丢弃 |
| 下一次 | `looked` 已过期，马上重查，`map` 1 行 |

所以 `map` 至多每秒 1 行，且至多每两次 `stall_look_system` 1 行。这个函数只在有卡顿或看门狗信号时才调，每次通常跟一个 7 行的样本，`map` 占比不超过约 7%，不会耗尽预算。`map` 用 `STALL_LEVEL_HIGH`，超预算只是不写，目标照样持有。`Q map` 重复写时 `run.maps[pid]` 只是覆盖。

**重查的代价**：`task_find` 每秒至多一次，都在线程调用里，和原来同样的锁序。`stall_target_drop` 可能是最后一个引用，线程调用上下文合法。

正常路径不受影响：线程找到后 `t->thread` 非空，retry 不动作。首次 try-lock 失败时，同一次调用里先重找线程，找到了就不丢。

## 七、S3：`max + 1`

- **调用者只有一个**：`BootProgress.c:1744`。另一处定义在 `Test/stall-glue.c`。
- **边界**：合法返回值不超过 `max`，`n == 32` 不触发早退。任务超过 32 个线程时循环 `n == max` 停，返回 32。【二进制】`b.hi`。
- **ABI**：`BOOT_PROGRESS_STALL_ABI` 只含版本和结构大小，语义变化没有反映在里面。但 BootProgress 和 XNU 对象链在同一个镜像里，【二进制】两边都是新的。`_boot_progress_stall_abi` 不在 11 个之内，`stall_configure` 的检查和上一轮一样通过。
- **弱符号**：`#pragma weak` 被 `bsd_kern.o` 的强定义取代，`_stall_tick` 里是直接 `bl`。

## 八、构建两遍

| 问题 | 结论 |
|---|---|
| 第一遍未使用参数会不会变错误 | 不会。宏是 `boot_progress_root(...) ((void)0)`（`early_boot_progress.h:26`），`-Weverything` 下有告警，但 `BUILD_WERROR` 默认 0（`build-xnu-4k-baseline.sh:820`），`unixnu` 驱动、`scripts`、本地配置和设备配置里都没有设置 `XNU_BUILD_WERROR`。编译记录里没有 `-Werror` |
| 第一遍有没有实际跑过这个补丁 | **没有。** `kernel-bootstrap.log` 停在 10-30，这次是增量第二遍。新树才走第一遍，结论来自读脚本 |
| 第二遍是否一定重编 `kern_exec.o` | 是。`kern_exec` 在删除列表（`:109`），`identity` 含 `libBootProgress.a` 的哈希，每次归档变化都删。这次日志和时间戳也确认了 |
| `vfs_subr.o` | 不在删除列表，所以补丁把函数放在 `kern_exec.c`，设计正确。这次它也重新编译了（日志有 `CC vfs_subr.o`，对象时间 07:16），但它本来也只需要外部引用 |
| 最终是否真实调用 | 是，见第一节 |
| 第一遍能否链接 | 能。`pagani_shutdown_progress` 在两遍里都有定义，第一遍函数体为空 |
| 有没有比内核新的源码 | 没有。`drivers`、`patches/xnu`、`package`、`scripts` 里除测试和文档外没有晚于 07:17 的文件 |

## 九、发现

### D1（wrong-data）S1 修复只盖住卡死的前约 2 秒

**位置**：`UniXNU-Pagani-Drivers/drivers/BootProgress/BootProgress.c:1962`，`:1868`；`StallProbe.h:62`；`build-userspace/a830-inputs/fullrun/stage15-probe.md:82`。

**沿真实顺序走一遍**：

1. t0：最后一帧，`stall_armed` 为 1，`stall_tick` 每 10 ms 一次。
2. 之后没有触摸，没有键盘配对，GPU 可能在忙。`stall_gap_sign` 最多给 `STALL_SIGN_GPU`，`stall_gap_waiting` 要求 `SURE`，所以为假。
3. 第一个 `now - t0 >= 2 s` 的 tick：`open`、`stall_gap_waiting`、`newest`、`touched` 四个条件全假，`:1876` 把 `stall_armed` 清 0。
4. 下一次 `poll` → `fps_observe` → `stall_touched`（无触摸）→ `stall_second(0)`：`:1920` `stall_second_take` 照常翻转 epoch、取走差值，然后 `:1962` 三个条件全为 0，返回。这一秒的数据没了。
5. 之后每一秒都如此。恢复时 `stall_frame` 重新唤醒，下一条 M 只含恢复那一秒。

**后果**：观察者每秒一次，相位任意，无触摸的卡死里 M 只多写 1 到 2 次。`preflight-stall.md` S1 原文的例子“帧停 3 秒且没有触摸”，第 3 秒依旧丢失；5 秒的卡死丢 3 秒。这几秒的调度延迟、IPI、UFS 读时间恰好是 S1 想保住的。同一段里采样器也睡了，3 s、8 s 阈值的 Q 样本也没有，这部分是原有设计（`StallProbe.h:53`），不是本次引入。

有触摸或有未结束的键盘配对时没有这个问题，确定信号让采样器撑到 8 s，配对撑到结束。实际上用户卡住时多半会点屏幕，所以缺口主要在“安静等待”的场景，比如点开应用后不动，等几秒。

**读错的风险**：`stage15-probe.md:82` 写“这几秒照样有 M 记录”，不通过时“看 `stall_armed` 是否真的醒着”。上机看到 M 停了，按这条判据会以为探针有问题，其实是设计如此。

**为什么主机测试抓不到**：`stall-glue.c` 直接置 `stall_armed` 再调 `stall_second`，只验证了门控本身，没有走“睡下之后卡死还在继续”这段时序。

**建议**：

- 不重编也要做：把 `stage15-probe.md:82` 和 `StallProbe.h:62` 改成“最后一帧或触摸后约 2 s 内有 M，有确定信号时最多 8 s，之后到恢复前没有”。
- 下次本来就要重编时再带：让 M 的门控不依赖采样器。在 `stall_second` 里记一个时刻，有帧或触摸时设为 `now + 10 s`，门控改成 `if (!frames && !stall_touches && !stall_armed && now >= stall_m_until) return;`。这样不增加 100 Hz 采样器的开销，上限仍是 896 B/s，每次活动结束多写约 6 到 9 KB。
- 不要靠延长 `STALL_ARMED_MS` 解决，那会让每次动画结束都多跑若干秒 100 Hz 采样。

## 十、考虑过、查后排除的假设

| 假设 | 排除依据 |
|---|---|
| `unmount returned` 用了已释放的 `mp` | 第二节，【二进制】 |
| `boot_progress_root` 假设帧缓冲还在启动状态 | 第三节，【真机】1161 s 走过 |
| 卸根后写线程失败或依赖文件系统 | 第四节 |
| `stall_armed` 卡 1 冲掉 console 环 | 第五节，上限不变 |
| `render-server` 找不到时 `map` 耗尽预算 | 第六节，至多每秒 1 行 |
| `stall_app` 也有 S2 那种死任务问题 | 需要“查到任务时线程已全退出”这个极窄窗口；有线程时走 `stall_sample_thread` 的丢弃路径。即使发生，配对 64 个样本后放弃，有界 |
| 无帧秒里 `M lat` 为空导致主机解析崩 | 第五节 |
| 新补丁打乱前序补丁 | `series` 第 27 项，树里内容正确，逐函数比较无意外 |
| 注释改动混进了代码 | 第一节，逐函数比较 |

## 十一、未验证，上机时看

- **根卷 `dounmount` 之后的路径从未在真机走过。** `preflight-refcount.md` 已记。这次多了 R 记录，能直接看到走到哪一步。判据：`unmount returned value=0x0: /` 出现两次（SystemOS 一次、根卷一次），中间夹 `/dev`，之后 `ufslog last snapshot before the reset: …`。
- **闭源部分**：根设备 `VNOP_CLOSE` 经 IOMedia、IOBlockStorageDriver 往下时对 `NativeUfs` 的影响。读码 `NativeUfs` 没有相关覆写，闭源层的行为【未验证】。
- **`host_reboot` 之后的 `systemWillShutdown` 这一轮**，`NativeUfs` 没覆写，但闭源驱动的行为【未验证】。最后快照在这之后，万一被什么拖住，R 记录还是已经由常规轮次写过（周期 3 s）。
- **新树第一遍编译这个补丁**没有实际跑过，见第八节。
- **`pagani-overlay=1` 下这条路的绘制**，这两张镜像不走，没有真机证据，第三节是读码结论。
- **渲染线程名**、**M、Q 行第一次真机格式化**：预检已列，这批改动没有带来新证据。

## 十二、实际做了什么

- 读 `BootProgress.c` 全文、`StallProbe.h` 全文、`UfsLog.c` 全文，`RamoopsLog.c` 的锁、发送、复位和记录段，`NativeUfs` 的 `LogInterface.h`、`RootCommands.h` 相关段，`Stage7Probe.cpp` 的显示交接。
- 读 XNU：`vfs_iterate`、`unmount_callback`、`vfs_unmountall`、`reboot_kernel`、`proc_shutdown`、`dounmount` 的相关段、`early_boot_progress.h`、`video_console.c` 的绑定点、`MakeInc.def` 告警段。
- 读 `build-xnu-4k-baseline.sh`、`build-boot-progress.py`、编译记录、构建日志。
- 读 `stall_format.py`、`gpu-loop.py`、`ramoops_format.py` 对 M、R 记录的解析。
- 读 `preflight-stall.md`、`preflight-critic.md`、`preflight-refcount.md`、`stage15-probe.md`，发现不重复列出。
- 反汇编 `aa94decf`，与 `8c042601`、`876e0f15` 逐函数比较；`kernel.macho` 与未剥离内核按函数比较字节。
- 查 `20261031-223924-stage1-full-1`、`20261031-221300-stage1-smoke-1`、`20261101-053955-stage15-smoke-1` 的日志。
