# 卡顿探针上机前审查

审查对象：`phone-stall-probe.patch`（sha256 前缀 `58e491ed`）、`StallProbe.h`、`BootProgress.c` 探针胶水段（1366–1987 行）、`UfsLog.c`、`Test/stall.c`、`Test/stall-glue.c`、`Test/stall-threads.c`。

内核为 05:46 重编结果：`kernel.macho` sha256 前缀 `8c042601`。符号和反汇编核对用的是 `build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped`。

本次只读：没有运行 adb、fastboot，没有碰手机，没有编译，除本文件外没有改任何文件。主机测试没有重跑。

## 结论

**没有发现会造成 panic、死锁或死等的问题。** 探针的每个 XNU 调用、每个跨线程读取、每个等待都逐条核对过，下面写了依据。

发现 3 条数据类问题，都不影响上机安全：

| 编号 | 严重度 | 一句话 |
|---|---|---|
| S1 | wrong-data | 完全卡死、没有帧也没有触摸的那几秒，M 记录不写，而且这几秒的数据被清掉，事后补不回来 |
| S2 | wrong-data | 目标进程查到了任务、没查到线程时，进程一旦重启，这个目标永远卡在死任务上，整轮都是 `no rs`，并一直持有死任务的引用 |
| S3 | minor | `task_lock_try` 失败被当成“kbd 没有线程”，把 kbd 目标丢掉，并清空最忙线程的基线 |

S1 影响这一轮最想看的数据，建议下次编译时带上。S2、S3 发生概率低，可以不为它们单独重编。

## 一、核对依据

| 事实 | 取值 | 依据 |
|---|---|---|
| XNU 基线 | xnu-10063.141.1 | 构建树 `git log` |
| 配置 | `DEVELOPMENT_ARM64_BCM2837` | `compile_commands.json` |
| `MACH_ASSERT` | 1，`assert()` 全部生效 | `osfmk/DEVELOPMENT/mach_assert.h` |
| `MACH_LDEBUG` | 0 | `osfmk/DEVELOPMENT/mach_ldebug.h` |
| `SCHED_HYGIENE_DEBUG` | 未启用，关中断和关抢占的时长不会触发 panic | 符号表没有 `interrupt_masked_debug_mode` |
| PAC、PPL、KASAN | 都没有。内核地址不带签名 | `-arch arm64`，`XNU_MONITOR` 未定义，符号表无 KASAN |
| UBSan 最小运行时 | 开。有符号溢出执行 `brk #0xff00` | `___ubsan_handle_add_overflow_minimal` 反汇编 |
| 内核栈 | 16 KiB，上下各一页保护页 | `osfmk/mach/arm/vm_param.h:328`，`osfmk/kern/stack.c:173` |
| 调度器 | Clutch，单 pset | `sched_clutch.c` 里 `multiple_psets_enabled = FALSE` |
| 物理读取路径 | 每次走 per-CPU 拷贝窗口，不走物理映射 | `__ARM_PTE_PHYSMAP__`、`__ARM_COHERENT_IO__` 都未定义；反汇编 `_ml_phys_read_data` 确认 |

链接结果已用 `nm` 核对：

- XNU 里 4 个弱钩子都被 BootProgress 的强定义取代：`_boot_progress_stall_run` 等落在 `0xfffffff00c1145ac` 一带；`_pagani_stall_dispatch` 的 `bl` 目标就是这个地址。
- `boot_progress_stall_kbt` 同样落在 BootProgress。
- BootProgress 里 15 个 `#pragma weak` 的 XNU 函数全部有定义，没有绑到空地址的。这一点很重要：运行时只检查了 `boot_progress_stall_abi`，其余函数指针不判空。
- `pagani_input_touch_events` 全局只有一份。

## 二、首测已经证明了什么

首测日志第 134 行（0.133 s）记录了 `stall probe: ramoops M/Q records`，说明 `pagani_stall_on` 在 0.133 s 已置位，到 4.85 s 崩溃为止：

| 已在真机上跑过 | 从未跑过 |
|---|---|
| `thread_dispatch` 钩子：内核线程、空闲线程、pid 1 的用户线程路径 | `stall_tick` 全部内容 |
| `cpu_signal_internal`、`cpu_signal_handler_internal`、`cpu_idle` 钩子 | `boot_progress_stall_thread_read`、`regs_here`、`xcall`、`task_find`、`thread_find`、`thread_times`、`map`、`map_read`、`image`、`cpu_read` |
| `stall_second` 的数值部分（panic 显示 `poll 5`），含 `boot_progress_stall_vm_read` | M、Q 行的格式化输出 |
| `stall_touched`、`stall_configure` | `pagani_kbtrace` 新增路径 |

注意：XNU 内部 `cpu_immediate_xcall` 的调用者只有 POST 测试和 `RamoopsLog.c:203` 的复位停核。普通 `cpu_xcall` 的调用者也只有 kpc、kperf、monotonic、`ml_cpu_down`。所以**探针很可能是这台机器上第一次走 `cpu_handle_xcall` 这条路**。这条路我专门核对了，见第五节，结论是没问题，但上机时要看 `x=` 字段。

## 三、逐段运行上下文

| 代码 | 上下文 | 调用的东西 | 判定 |
|---|---|---|---|
| `pagani_stall_dispatch`（`bsd_kern.c:1804`，`sched_prim.c:3791`、`3839`） | splsched，持 `self` 的 thread lock（`sched_prim.c:3764` 取锁、`3857` 放锁） | `get_threadtask`（`zone_require_ro`）、`task_get_proc_raw`、`proc_require`（`zone_id_require`）、`task_pid`、`proc_best_name`、`boot_progress_stall_run` | 全部无锁、不睡眠，访问的都是已固定的 zone 内存。`self` 是正在运行的线程，任务和 proc 都活着。任务已清 `has_proc` 时 `p` 为 NULL，`name` 留空，`stall_name_words` 判空 |
| `boot_progress_stall_run` → `stall_note_run`（`StallProbe.h:287`） | 同上，本 CPU 唯一写者 | 只有原子读写 | 合法 |
| `boot_progress_stall_idle`（`arm64/cpu.c`） | 空闲线程，关中断 | 一次读写 | 合法，首测已跑 |
| `boot_progress_stall_ipi_send`（`cpu_common.c`） | 任意 CPU，关中断 | `mach_absolute_time`、原子加、读写 | 合法。位置在 `PE_cpu_signal` 之前，每个印戳后面都确实跟一个 SGI |
| `boot_progress_stall_ipi_recv` | 目标 CPU，中断上下文 | `mach_absolute_time`、原子交换 | 合法 |
| `stall_xcall` → `boot_progress_stall_regs_here` | 目标 CPU，关中断。也可能从发送方的 drain 路径进来 | `current_thread`、`cpu_int_state`、`get_user_regs`、`stall_regs_copy`、`ml_static_unslide` | 无锁。只比较 `handle` 指针，不解引用。drain 路径下 `cpu_int_state` 为 NULL，返回 0，显示为 `mv`。`stall_regs_copy` 用了 `q0`，但 XNU 每次异常入口都保存 NEON（`exception_asm.h:166`） |
| `boot_progress_stall_kbt`（`BootProgress.c:1523`） | 用户线程的 `kdebug_trace` 系统调用，已放开 `pagani_kbt_lock` | `stall_open_take`（关中断，有界自旋）、`thread_call_enter` | 合法 |
| `stall_frame` → `stall_arm` | 显示路径，可能关中断 | `thread_call_enter` | 合法，内部自己关中断取组锁，允许从中断上下文调用 |
| `stall_tick`（`BootProgress.c:1765`） | 线程调用，优先级 95，可睡眠，不持我们的锁 | 见下 | 合法 |
| `boot_progress_stall_thread_read`（`bsd_kern.c:1896`） | 同上，内部 splsched + thread lock | `thread_get_runq`（断言持锁，确实持有）、读字段、`stall_kernel_frames` | 合法 |
| `boot_progress_stall_task_find`（`bsd_kern.c:1966`） | 线程调用 | `lck_mtx_try_lock(&tasks_threads_lock)`，放锁后才调 `get_task_map_reference`（`task_lock`）、`vm_shared_region_get`（`task_lock` + 共享区互斥锁）、`vm_shared_region_deallocate`、`vm_map_deallocate` | 不嵌套。可能阻塞，但线程调用里允许 |
| `boot_progress_stall_thread_find`、`thread_times`（`bsd_kern.c:2021`、`2062`） | 线程调用 | `task_lock_try`、`bsd_getthreadname`、`recount_thread_time_mach`、`thread_reference` | 持 task lock 时没有取任何其他锁，也没有 thread lock |
| `boot_progress_stall_map_read`（`bsd_kern.c:2104`） | 线程调用 | `pmap_find_phys`、`pmap_valid_address`、`ml_phys_read_double_64` | 见第四节 |
| `boot_progress_stall_image`（`bsd_kern.c:2125`） | 优先级 4 的线程调用 | `fill_procregioninfo_onlymappedvnodes`（用入参的任务，不依赖当前任务）、`vnode_getwithvid`、`vnode_getname`、`vnode_put` | 可阻塞，单独的线程调用，阻塞了只影响自己 |
| `boot_progress_stall_cpu_read`（`bsd_kern.c:2132`） | 线程调用，无锁 | `sched_clutch_runq_count`、`rt_runq_count` | 都是纯读字段，没有持锁断言（`sched_clutch.c:2597`、`sched_prim.c:150`） |
| `stall_second`、`boot_progress_stall_vm_read` | 1 Hz 观察者线程调用 | `counter_load`（逐 CPU 求和） | 合法，首测已跑 |

**锁序**：全程没有嵌套。没有“持 thread lock 取 task lock”，没有“持 task lock 取 thread lock”，`tasks_threads_lock` 只 try，放掉才取 task lock。`stall_open_lock`、`lock_word` 都是关中断的叶子锁，持锁时不调用任何可能取别的锁的函数。

**缺页**：钩子里访问的 BootProgress `.bss`、zone 内存、per-CPU 数据全部固定映射。用户内存只走物理路径。

**断言**（`MACH_ASSERT=1`）：`thread_get_runq` 的持锁断言满足；`get_saved_state_*` 的 64 位断言已由 `is_saved_state64` 先判；`cpu_datap` 的上限断言满足；`thread_call_thread` 末尾的抢占级别检查满足，`stall_tick` 所有路径抢占平衡。

**ONCE 线程调用**：核对了 `_pending_call_enqueue`、`_delayed_call_enqueue`、`thread_call_finish`（`thread_call.c:710`、`754`、`1564`）。运行中再 `thread_call_enter_delayed` 只置 `RESCHEDULE` 并记下截止时间，结束后重新入队，不会丢，也不会立刻重入空转。`stall_armed` 清 0 后的竞争走同一条路。

## 四、跨线程读取

### 内核栈（`stall_kernel_frames`，`bsd_kern.c:1861`）

门槛：持目标线程的 thread lock，`!on_core && continuation == NULL && kernel_stack != 0`。

**栈什么时候会被拿走**，全部核对过：

| 路径 | 条件 | 为什么读不到 |
|---|---|---|
| `thread_dispatch` 里的 `stack_free`（`sched_prim.c:3489`） | `continuation != NULL`，不持 thread lock | 门槛要求 `continuation == NULL` |
| `stack_handoff`（`sched_prim.c:3134`） | 要求被换下的线程带 continuation | 同上 |
| `thread_deallocate_complete` | 引用归零 | 我们持有引用 |
| `stack_collect` 解除映射（`stack.c:342`） | 只处理空闲栈缓存 | 见下面的时序窗口 |

**线程回到 CPU 上要经过谁**：`thread_invoke` 第 3039 行要先取新线程的 thread lock。采样器持锁期间，下核的线程上不了核，所以“在核上”不会从假变真。

**时序窗口**：`thread_block_reason` 走 `thread_lock(self)` 之后才改 `active_thread`，采样器看到“不在核上”时一定也能看到 `continuation`。`thread_run` 路径（`sched_prim.c:3992`）设 `continuation` 后没经过 `self` 的锁，理论上存在极短的乱序窗口。即使碰上，后果也只是读到已交接或已进空闲缓存的栈：这块内存仍然映射。解除映射要等周期性的 `stack_collect` 从全局空闲表里回收（`stack.c:310`、`342`），不可能落在这次约 1 µs 的遍历里。

**边界**：`fp` 必须 8 字节对齐，落在 `[kernel_stack, kernel_stack + kernel_stack_size)`，严格递增，最多 5 帧。反汇编确认生成代码就是这样（`_boot_progress_stall_thread_read`，`0xfffffff00b954238` 起）。这段内存全部映射，保护页在范围外。

`thread->machine.kpcb` 只在 `DebuggerCall`、`DebuggerXCall` 期间非空（`sleh.c:1035`、`model_dep.c:1249`），那时线程一定在核上，所以这条分支实际走不到，也就不存在悬空指针风险。

**PAC**：这个内核没有 PAC，`lr` 是明文。`VM_KERNEL_UNSLIDE` 对非内核地址返回 0，不会 panic，读到陈旧垃圾值只会显示 `k:0`。

### PCB（`get_user_regs`）

`thread->machine.upcb` 在 `machine_thread_destroy` 才释放，也就是 `thread_deallocate_complete`，我们持有引用。线程正在运行时读到的是过期但有效的内存。内核线程 `upcb` 为 NULL，已判空。

### 线程名

`pth_name` 由 `uthread_cleanup_name` 释放，只在 `uthread_destroy` 里调用（`kern_fork.c:1557`），也就是线程最终销毁时。线程还在 `task->threads` 上、持 task lock 时不会发生。

### 用户内存

不走缺页路径。`pmap_find_phys` 在线程调用里走 `pmap_find_pa_internal`，取 pmap 共享锁后软件遍历页表，范围外返回 0（`pmap_vtophys` 判 `pmap->min/max`），无效表项（含压缩标记）返回 0。`pmap_valid_address` 过滤掉非托管页。`ml_phys_read_double_64` 按对齐地址不会跨页，不触发 `ml_phys_read_data` 的跨页 panic。`map` 引用保住 pmap 不被销毁。页面刚被释放时读到的是别的内容，但不会缺页，帧链检查会停下。

`c->fp` 只有在 `map` 非空且 `regs->valid` 时才非 0，`crash_walk_some` 见到 0 直接返回，所以不会拿 NULL 的 `map` 去读。`c->image` 为 NULL，`crash_where` 不会触发 4 字节读（那个读会被 `map_read` 的 8 字节对齐检查拒绝）。

### 引用与释放

`thread_deallocate`、`task_deallocate`、`vm_map_deallocate` 都可能是最后一个引用，都在线程调用上下文里释放，合法。`stall_tick` 不在任何自旋锁或关中断区里释放。

## 五、IPI、定时器、等待

| 等待点 | 上限 | 说明 |
|---|---|---|
| `stall_ask` | 494 µs（`19 × 500` 个 tick） | 开中断、可抢占。上一个问题没答案时立刻返回 `to`，不再发 |
| `cpu_immediate_xcall` | 不等 | `SIGPxcallImm` 已挂起时 `KERN_ALREADY_WAITING` 立刻返回。CAS 失败只因别的位变化，下一轮成功 |
| `stall_open_take` | 65536 次 `yield` | 关中断。持锁方只做几次读写 |
| `lock_log` | 2^18 次 | 关中断。超时丢记录，不死等 |
| `thread_lock` | XNU 自身超时 | 持有时间不足 1 µs |

**交叉调用这条路第一次跑，专门核对了**：

- `xcall_ack_timeout_abstime` 在 `arm_init.c:523` 初始化，此前 477 行 `rtclock_early_init` 已通过回调设好 `rtclock_sec_divisor`，值为 6 s，不是 0。如果是 0，`cpu_signal_internal` 的断言和超时判断都会立刻 panic，这里不会。
- `cpu_number` 来自 `processor->cpu_id`，等于 `ArmCpu` 注册的 `log_id`，不超过 `ml_get_max_cpu_number()`。`cpu_datap` 非空已先判。`func`、`param` 非空。这三条都是 `cpu_xcall_internal` 的 panic 条件。
- `cpu_handle_xcall` 读 `cpu_imm_xcall_p0/p1`，发送方后写参数时接收方有回绕重读，是 XNU 原有设计。
- 目标 CPU 关中断时，答案晚到只写静态槽。槽保持 `ASKED`，不接受新问题，不会串。
- 采样器线程迁移到目标 CPU 上时，xcall 发给自己，开中断后执行，线程不匹配，显示 `mv`。

**延迟 IPI**：这个配置打开了 `CONFIG_SCHED_DEFERRED_AST`。`IOCPU::signalCPUDeferred` 默认直接调 `signalCPU`，`ArmCpu` 没有重写，`signalCPUCancel` 默认空操作。所以每个印戳后面都有真实 SGI，取消也收得回来，`pending` 不会留下假的“卡住”。

**`SIGPxcallImm` 被探针占用的副作用**：`RamoopsLog.c:203` 的复位停核可能碰上 `KERN_ALREADY_WAITING`，少停一个核。只在 `pagani-reset-seq` 含 `0x8` 时走到，当前启动参数没有。

## 六、日志量

### Q 记录有全局限速，M 没有预算但量有上限

| 记录 | 限制 | 最坏值 |
|---|---|---|
| Q | 两个池各 20 行/秒，1 秒突发，整轮各 2000 行 | 40 行/秒 × 224 字节 = 9 KB/s；整轮 896 KB |
| M | 1 Hz，每次 4 行，没有帧也没有触摸就不写 | 896 B/s |

每条样本的行数和 `STALL_SAMPLE_LINES`（7）对得上，`map`、`img` 另行计入预算，没有绕过限速的写入路径。所有行都不超过 190 字符，不产生 `+` 续行（`test_lengths` 用最坏值测过）。

### 真机基线

`runs/20261031-223924-stage1-full-1`，1181 s，活跃段（750–1100 s）：console 环约 750–850 B/s，events 环 130–540 B/s，用了 40 个 UFS 槽。

| 环 | 大小 | 现在能回看 | 加上 M/Q 最坏 |
|---|---|---|---|
| console（M 写这里） | 28668 字节 | 约 35 s | 约 17 s |
| events（Q 写这里） | 32768 字节 | 1–3 分钟 | 3.6 s |

**这只影响 ramoops 环本身，不丢 UFS 日志里的数据：**

- 有帧或有写入时，环每涨 12 KB 就提前一轮，写线程每 500 ms 看一次。9 KB/s 下约 1.4 s 一轮，环转一圈要 3.6 s，够用。
- UFS 窗口 509 个槽，固定 32 个，环形 477 个，每槽 24 KB，约 11 MB。按 1.5 KB/s 算能存约 2 小时。7 分钟的跑测和 90 分钟的长跑都不会覆盖。
- panic 记录是最新写入的，不会被 Q 挤掉，而且有最后一次快照。

**残余风险**：UFS 写线程优先级只有 31，如果它被饿住超过约 2.5 s，同时 Q 又是最高速率，events 环里更老的记录会丢。但 Q 的真实速率远低于上限：一次卡顿最多 6 个样本，单个键盘配对最快 500 ms 一个样本，约 2.6 KB/s，此时环能撑 12 s。我判断不算问题。

`pagani_kbt` 的 U 记录不受这些限速管，本次把 flush 从 4 s 改到 1 s，打字时 U 记录变成 4 倍，典型约 1.4 KB/s，也进 events 环。量可接受，只是这部分和 Q 叠加。

## 七、开销（估算，未实测）

| 路径 | 频率 | 单次开销 | 说明 |
|---|---|---|---|
| dispatch 钩子 | 每次切换 | 约 100–200 ns | 5 次函数调用加 20–40 次读写，2 个 `dmb` |
| IPI 钩子 | 每个 IPI | 约 100 ns | 目标 CPU 的 `sent`、`pending` 缓存行会跨核迁移 |
| 空闲钩子 | 每次进空闲 | 约 1 ns | 一次读写 |
| `stall_tick` 空转 | 屏幕活动时 100 Hz | 本体数微秒，加两次切换共约 10 µs | 线程优先级 95，会抢占 UI 和渲染线程 |
| 一个样本 | 最多约 3 次/秒 | 约 0.5–1 ms | 含最多 2 次 500 µs 等待（目标 CPU 关中断时才碰到） |
| 记录写入 | 每行 | 关中断约 10–30 µs | 36 份副本逐字节写 |

**判断：够小，不会自己造出能看见的卡顿。** 但有一点读数时要记得：采样器自身每秒贡献约 100 次定时器、100 次唤醒和相应的 IPI，会出现在 `M cpu` 的 `ipis`、`ipir`、`tmr` 和 `idle` 里，这是基线，不是系统行为。

## 八、pagani-kbtrace 新增部分

逐段核对 `pagani_kbtrace.h` 和 `kdebug.c`：

- `pagani_kbt_count` 提前返回只在 `pid <= 0` 或 `kind` 非法时，这时 `t->follow` 不清零，但调用方已过滤负 `kind`，用户进程 `pid` 都大于 0，走不到。
- `pagani_kbtrace_note` 栈帧 3008 字节（反汇编 `sub sp, sp, #0xbc0`），系统调用路径浅，16 KiB 栈够用。
- `*s = (struct pagani_kbt_slot){ .pid = pid }` 在 `kdebug.c` 里编译，XNU 有 `memset`，不受 BootProgress 不能调 `memset` 的限制。
- `pagani_kbt_summary_as` 在自旋锁内格式化两行，约几微秒。
- `pagani_kbtrace_flush` 的 `for (;;)` 每轮弹出一项，队列最多十几项，有界。
- `pagani_kbt_long_note` 拷 `name` 两边都是 17 字节。
- `boot_progress_stall_kbt` 在放开 `pagani_kbt_lock` 之后调用，没有嵌套自旋。

没有发现问题。

## 九、字段对照

`bsd_kern.c` 用到的字段全部在 `MACH_KERNEL_PRIVATE` 下，**没有一个在 `DEVELOPMENT`、`DEBUG` 或其他配置开关里**，所以和构建配置无关。补丁没有给任何 XNU 结构体加字段，苹果预链接 kext 依赖的布局不变。

| 结构 | 字段 | 定义位置 | 条件 |
|---|---|---|---|
| `thread` | `state`、`sched_pri`、`base_pri`、`last_run_time`、`last_made_runnable_time`、`last_processor`、`wait_event`、`continuation`、`kernel_stack`、`task_threads`、`active:1`、`thread_id`、`block_hint`、`t_tro`、`th_recount` | `kern/thread.h:392–976` | 无 |
| `machine_thread` | `upcb`、`kpcb` | `arm/thread.h:107`、`109` | 无 |
| `task` | `active`、`threads`、`tasks`、`map`、`shared_region`、`mach_header_vm_address` | `kern/task.h:175–546` | 无 |
| `processor` | `state`、`active_thread`、`idle_thread`、`processor_set`、`cpu_id`、`last_dispatch` | `kern/processor.h:330–367` | 无 |
| `vm_shared_region` | `sr_slide`、`sr_first_mapping`、`sr_base_address`、`sr_size` | `vm/vm_shared_region.h:240–248` | 无 |
| `arm_kernel_saved_state` | `fp`、`lr` | `mach/arm/thread_status.h:391`、`392` | 无 |

反汇编里的偏移和结构体顺序一致：`continuation` 在 `0xd8`，`kernel_stack` 在 `0xe8`，`kpcb` 在 `0x120`，`state` 在 `0x1f8`，`thread_id` 在 `0x508`。`TH_IDLE` 是第 7 位，`TH_RUN` 是第 2 位，和 `StallProbe.h:974` 的注释一致。

`bsd_kern.c` 和 `StallProbe.h` 两份结构体逐字段比对过，顺序和类型相同，ABI 检查在真机上也已通过。

## 十、发现

### S1 完全卡死的几秒没有 M 记录（wrong-data）

- 位置：`/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/BootProgress.c:1951`
- 路径：`poll` → `fps_observe` → `stall_second(fps_scan.fps)`。第 1910 行 `stall_second_take` 无条件翻转 epoch，把这一秒的最大延迟、IPI 最大值和全部差值取走。第 1951 行 `if (!frames && !stall_touches) return;` 直接返回。
- 后果：帧停 3 秒且没有触摸时，第 2、3 秒的 `M lat`、`M cpu`、`M ipi`、`M io` 全部丢失，下一次写出来的只是恢复后那一秒的差值。这几秒里线程能跑却没跑、哪个核关了中断、UFS 读了多久，恰好是最想看的数据。键盘配对长期不结束且没有新触摸时也一样。
- 为什么主机测试抓不到：这是写进测试的预期行为，`stall-glue.c:502–504` 明确断言“没有帧就没有记录”，头文件第 61 行也这么写。只有真实卡顿才能看出它的代价。
- 建议：第 1951 行改成 `if (!frames && !stall_touches && !__atomic_load_n(&stall_armed, __ATOMIC_RELAXED)) return;`。采样器醒着就写。动画结束后最多多写 2 秒，有确定信号时最多多写到信号结束。

### S2 目标卡在死任务上（wrong-data）

- 位置：`BootProgress.c:1577` 和 `1716–1721`
- 路径：`stall_look_system` → `stall_target_task` 查到 `backboardd` 的任务 → `boot_progress_stall_thread_find` 返回 NULL（`task_lock_try` 失败，或渲染线程还没起名）→ `t->task` 已设、`t->thread` 为 NULL → 进程退出重启 → 第 1577 行 `if (t->task) return 1` 永远成立，不再按名字查 → `thread_find` 在空线程表上永远返回 NULL。
- 能释放这个目标的只有 `stall_sample_thread`（要求 `t->thread` 非空）和键盘路径，`stall_rs`、`stall_sb` 走不到。
- 后果：这一轮之后所有样本都是 `no rs` 或 `no sb`，并一直持有死任务的引用。不会崩。
- 概率：低。`stage1-full` 那一轮的日志里，`backboardd`、`SpringBoard` 只在关机时退出过。
- 为什么主机测试抓不到：假实现的 `boot_progress_stall_thread_find` 从不因锁忙失败，`test_exit` 只覆盖了有线程的 `stall_app`。
- 建议：`stall_look_system` 末尾对 `stall_rs`、`stall_sb` 加一步：`if (t->task && !t->thread && now - t->looked >= hz / 1000 * STALL_LOOKUP_MS) stall_target_drop(t);`，下次按名字重查。

### S3 task lock 忙被当成 kbd 没有线程（minor）

- 位置：`BootProgress.c:1735–1739`，`phone-stall-probe.patch` 对应 `bsd_kern.c:2062–2072`
- 路径：`boot_progress_stall_thread_times` 在 `task_lock_try` 失败时返回 0 → `stall_busiest(..., 0)` 把基线 `count` 清 0 → `tid` 为 0，线程置空 → 第 1739 行 `if (!n) stall_target_drop(&stall_kbd)` 把任务也丢了。
- 后果：这次样本是 `no kbd`。下次重查多写一条 `map` 记录，并且基线清零后“最忙线程”按累计时间选，而不是按上次以来的增量。
- 为什么主机测试抓不到：假实现的 `boot_progress_stall_thread_times` 没有锁忙状态（`stall-glue.c:249`）。
- 建议：锁忙时返回 `max + 1`，调用方见到 `n > STALL_BUSY_THREADS` 直接返回，保留上次的线程和基线。

## 十一、注释和实现不一致（不影响运行，不算发现）

- `StallProbe.h:33–36` 和 `bsd_kern.c` 的注释说用户内存“through the physical aperture”。这个配置下 `ml_phys_read_data` 每次都走拷贝窗口：关抢占，写一条 PTE，刷一次 TLB，读，清 PTE，再刷一次 TLB。一个样本最多读 32 个字，约 64 次广播 TLB 失效，每个字关抢占几微秒。按样本频率算影响很小，是安全的，只是和注释不符。
- `stall_kernel_frames` 注释说 `kpcb` 对应“被抢占”，实际只在调试器路径非空。被抢占的线程走 `else` 分支，按调用链推算，`skip = 2` 跳过 `machine_switch_context` 和 `thread_invoke`，所以 `k=` 第一项是 `thread_block_reason`。可读，只是占掉一个槽位。
- `StallProbe.h:299` 的注释说多数切换不用重写名字。实际上空闲线程 `pid` 是 -1，线程和空闲之间每次切换都会重写。开销很小。
- `boot_progress_stall_map` 里的 `get_task_map_reference` 取 `task_lock`，会阻塞。阻塞时采样器只是晚一点，不持我们的锁，不会死等别的东西。上一轮审查已记录过。

## 十二、未验证，上机时要看

以下都是读码得不出结论的，不是已知问题：

- **交叉调用第一次在这台机器上跑。** 读码没问题。看 `Q th` 的 `x=`：多为 `ok` 正常；全是 `no` 说明发不出去；全是 `to` 说明处理函数没跑到。
- **`idleq`、`ipi` 样本和卡顿样本共用一个预算，且都按最高档。** 如果真机上这两类很吵，可能先把 2000 行用完。单 pset 下 `rq` 是全核共享的计数，空闲核看到它非 0 的情况比多 pset 多。【未验证】看 `M io` 的 `q` 增长速度和 `why=idleq` 的数量。
- **优先级 95 的采样器对帧时间的实际影响。** 第七节是估算。按 `stage15-probe.md` 的判据，和上一轮比帧率与顺滑度。
- **渲染线程名是否真含 `render-server`。** 代码有回退，但没有在 21H461 真机上确认过。
- **`M ipi` 的 `tick`** 接近 10 ms。`thread_call_enter_delayed` 用的是默认紧急度，定时器合并会带来约 1 ms 偏差，这是正常的。
