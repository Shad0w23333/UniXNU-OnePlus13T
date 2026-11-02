# 阶段 1.5 上机前补漏审查

审查范围：阶段 1 完整包（内核 `f7f9908f`，种子 `684396ce`）到阶段 1.5（内核 `8c042601`，种子 `39e635cb`）的全部差异。

全程只读。没有运行 adb、fastboot，没有碰手机，没有编译内核，除本文件外没有改项目文件。临时产物都在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/critic`。

证据标注：【二进制】从镜像解出的产物逐字节或逐函数比较，【读码】源码，【真机】运行日志，【推测】没有直接证据。

## 结论

- **没有发现新的 crash、hang 或写坏数据的问题。** 五份报告已列出的发现不重复。
- **差异清单是完整的。** 这次不靠文件时间，而是从 `测试镜像/boot-A830-阶段1-完整包.img` 解出阶段 1 的内核、设备树、种子，与新产物直接比较。除清单内的内容，其余都相同。
- **五位审查者没覆盖到的改动，查完都没有问题。** 这些是 `native-ufs-diagnostics` 的去掉、设备树哈希的变化、`Stage7Probe` 的 `vl` 事件、`CrashTrace.h` 的拆分、调频的 `checkDue` 和 `why` 字段、`A830Fence` 与 `NativeAdrenoMeter.h`、构建脚本的删除列表。
- **顺带关掉了三个遗留的未验证项**，见第五节。
- **本文写完时，`测试镜像/boot-A830-阶段15-*.img` 里还是崩溃那版内核 `876e0f15`，不能直接上机。** 打包后按第七节核对。

## 一、差异总表

| 项 | 阶段 1 | 阶段 1.5 | 差异 | 依据 |
|---|---|---|---|---|
| BootShim 加 UEFI 固件 | `b8037957` | 相同 | 无 | 【二进制】解压后逐字节比较 |
| 设备树 DTB（镜像尾部） | `c63c09d1` | 相同 | 无 | 【二进制】 |
| Android 启动头 | | | 只有压缩后长度字段 | 【二进制】 |
| 信任缓存 | `9a4701ef` | 相同 | 无 | 【二进制】 |
| ADT | `53f8737c` | `31be71f6` | 只有 `/chosen` 的 `pagani-build-time`、`random-seed`。`text-fixups` 没变 | 【二进制】逐节点逐属性解析 |
| `unixnu.toml` | | | 只有 `args` 一行 | 【二进制】 |
| 启动参数 | 998 字节 | 996 字节 | 四处，见下 | `payload.json` |
| 种子 | `684396ce` | `39e635cb` | `disabled.plist` 多一个键 | 第三节 3.3 |
| 预链接扩展代码 | | | `__PRELINK_TEXT`、`__PLK_TEXT_EXEC` 逐字节相同 | 【二进制】 |
| 预链接扩展数据 | | | 差异都是小的固定位移，符合内核变大后重新绑定符号；没有逐条核对 | 【二进制】 |
| 内核自身 | `f7f9908f` | `8c042601` | 见下 | 【二进制】 |

启动参数的四处差异：

| 参数 | 阶段 1 | 阶段 1.5 首测 | 覆盖 |
|---|---|---|---|
| `native-ufs-diagnostics=1` | 有 | 去掉 | 无人覆盖，见 3.1 |
| `pagani-panel` | 65 | 1217 | 面板审查 |
| `pagani-reset-after` | 5400 | 420 | 见下 |
| `pagani-input-script=1` | 无 | 有 | 键盘审查 |

`pagani-reset-after=420` 不是新路径：【真机】`20261031-221300-stage1-smoke-1` 就是 420 s，以 `state 6` 正常走完，`ufslog last snapshot before the reset: timer`。新增的只有 `reset_begun`、`reset_coming` 两个字。

### 内核逐函数比较

方法：`llvm-objdump` 反汇编两版内核，解析 `adrp` 加偏移，把地址换成符号，按函数比较。

| 结果 | 函数数 |
|---|---:|
| 完全相同 | 46136 |
| 只差地址、断言行号、类大小、结构偏移 | 734 |
| 指令序列不同 | 88 |
| 新增 | 53，其中 2 个是改签名 |
| 消失 | 2，就是那 2 个改签名 |

734 个里的立即数差异我全部过滤过：去掉行号型的小增量后，只剩 `PaganiPanel` 类大小 `0x438` 到 `0x5a0`、`AGXDeviceUserClient` 类大小 `0x128` 到 `0x168`、`boot_progress_display_event` 的上限 3 到 4。

88 个里约 14 个是噪声：`_OUTLINED_FUNCTION_*`、`_kdbg_test.cold.*`、`_lz4_decode_asm` 的代码内数据等。其余按来源分：

| 来源 | 函数 | 覆盖 |
|---|---|---|
| `phone-app-exec-count` | `exec_activate_image` | exec 审查 |
| `phone-checkdirs-kernproc` | `checkdirs_callback`、`load_init_program` | 引用计数审查 |
| `phone-stall-probe` | `thread_dispatch`、`cpu_idle`、`cpu_signal_*`、`pagani_kbtrace_*`、`boot_progress_stall_*` | 探针审查 |
| BootProgress | `poll`（拆出 `fps_observe`）、`boot_progress_frame*`、`_start`、`_panic`、`_crash`、`early_boot_progress_init`、`stall_*` | 探针审查，加 3.5 |
| RamoopsLog | `emit_text`、`ramoops_log_rows`、`ramoops_log_vm_ready`、`reset_now` | 部分，加 3.5 |
| UfsLog | `ufs_log_quiesce`、`ufs_log_writer_tick`、`writer_stats`、`boot_log_export_put` | 部分，加 3.5 |
| Stage7Probe | `native_legacy_vsync_observed` | **无人覆盖**，3.4 |
| NativeUfs、NativeRamBlock | `NativeUfsBlock::start`、`NativeRamBlock::start`、`declareSolidState` | 固态审查 |
| PaganiArmCpu | `cpufreqRun`、`controlCheck`、`controlProbe`、`setLevel`、`putWhy` | exec 审查只查了启动提升，3.6 |
| PaganiInput | `tick`、`start`、`PeripheralWindow::map` | 键盘审查 |
| PaganiPanel | `start`、`tick`、`report`、`sendPacket`、探针线程 | 面板审查，3.8 |
| Adreno830 | `A830Dcvs::*`、发布层、状态层、`stage*` | GPU 审查 |
| Adreno830 | `A830Fence::takeQueueAt`、`queuePass`、`NativeAdrenoMeter.h` | **无人覆盖**，3.7 |

崩溃轮内核 `876e0f15` 到新内核 `8c042601`：真实差异只有 `exec_activate_image` 和 `process_signature`，其余只差行号和地址。重编没有带进别的东西。

XNU 补丁只有三个新增，`series` 末尾与文件时间一致。`UniXNU-sm8750-common`、`UEFI-Pagani_SM8750` 没有晚于阶段 1 的文件。

## 二、文件清单的覆盖判定

| 路径 | 判定 |
|---|---|
| `patches/xnu/series` 末尾三个补丁 | 已覆盖 |
| `board.toml`（01:09） | `make-a830-layout.sh` 临时改写后还原，不是真改动，ADT 比较已确认 |
| `Adreno830/*`（代码） | GPU 审查覆盖；`A830Fence`、`NativeAdrenoMeter.h` 由本文补 |
| `Adreno830/tests`、`docs`、`build` | 不上机 |
| `BootProgress/*` | 探针审查加本文 |
| `Stage7Probe.cpp`、`PeripheralStatus.h` | 本文 |
| `PaganiArmCpu/*` | exec 审查加本文 |
| `PaganiPanel/*`、`PaganiInput/*`、`NativeUfs`、`NativeRamBlock` | 已覆盖；二进制差异与源码差异一致 |
| `UniXNU/scripts/build-xnu-4k-baseline.sh` | 本文 3.9 |
| `工具/make-a830-final-seed.sh`、`test-edit-phone-seed-launchd.py` | 已覆盖，3.3 补比对 |
| `工具/gpu-test-loop/*` | 主机侧。崩溃轮日志已被它们解码，`M`、`Q` 类型字母不与现有类型冲突 |

## 三、没被覆盖的改动，亲自核对的结果

### 3.1 去掉 `native-ufs-diagnostics=1`

唯一解析点 `NativeUfs.cpp:1769`，只设 `gRootDiagnosticEnabled`。全部读者：

| 行 | 作用 | 关掉后 |
|---|---|---|
| 851 | 传输失败时读控制器寄存器存诊断 | 失败时少读几个寄存器 |
| 949、984 | 启动时读几个寄存器存诊断 | 不读，结果没有别的读者 |
| 1272 | 根卷发布失败时 panic | 不 panic，只留 `native_ufs_root_failure` |

读路径、写路径、日志窗口都不看这个开关。【真机】崩溃轮已经不带这个参数跑过根卷发现、挂载和 Data 种子拷贝。

唯一的行为变化：万一根卷发布失败，不再 panic，停在启动页，到 420 s 定时复位。首测这样反而不丢控制权。

### 3.2 设备树

【二进制】按节点解析两份 ADT，21 个节点全部一致，只有 `/chosen` 的两个属性不同。`pagani-build-time` 只被 `pagani-rtc=1` 启动时读一次；`random-seed` 每次打包都变。

### 3.3 种子

`preflight-exec.md` 说阶段 1 种子已被覆盖、无法比对。它其实在阶段 1 镜像的 FAT 里，已解出，哈希 `684396ce` 与 `payload.json` 一致。

- 块级：展开后约 14080 个非零块，13806 个同偏移相同，71 个内容相同只是挪了位置，只有约 200 个新内容。这个量级符合一次 APFS 事务的元数据加一个小文件。
- 文件级：只读挂载，能读到的 319 项里只有 `db/com.apple.xpc.launchd/disabled.plist` 不同，584 到 631 字节。
- 多出的 47 字节正好是一个 `com.apple.powerlogHelperd` 键加一行 `<true/>`。

所以用户态相对阶段 1 只差这一个任务被禁用，插件、Turnip、着色器包都没变。

缺口：`root`、`logs` 等约 30 个目录是 0700，当前用户读不进去，这部分靠块级比较和工具自带的卷摘要检查兜底。

### 3.4 `PAGANI_DISPLAY_VSYNC_LATE`

- `PeripheralStatus.h:96`：枚举加在 `PAGANI_DISPLAY_EVENTS` 之前，已有事件编号不变，所以没重编的调用方不受影响。
- `BootProgress.c:135`：两个数组都用这个枚举定大小，上限判断在 `:838`。
- `Stage7Probe.cpp:169`：`native_legacy_vsync_observed(3, ...)` 只做一次原子加，任何上下文都合法。调用点是 `NativeLegacyDisplay.cpp:273` 的 `vsyncTick`。
- 【真机】崩溃轮的 F 行已带 `vl 0`，读路径跑过。

### 3.5 BootProgress、RamoopsLog、UfsLog、CrashTrace

- `CrashTrace.h`：`crash_walk` 拆成 `crash_walk_some`，只多一个 `frames` 参数。崩溃路径传 `CRASH_FRAMES`（16），探针传 `STALL_FRAMES`（8），都不超过 `frame[16]`。
- `area_of`：`M` 进 console 环，`Q` 进 events 环。`M`、`Q` 与现有类型不重复。
- `ramoops_log_fps`：`extra` 现在最长 176 字节，`put_str` 受 `struct text` 上限约束，`emit_text` 自动拆行，不会溢出。
- `reset_begun`：`reset_now` 第一步置位，只被 `stall_halted` 读。
- `reset_coming`：`ufs_log_quiesce` 置位后，`writer_round` 和 `boot_log_export_put` 都不再发新命令。写线程继续走循环但不写，不空转，也不影响 `last_flush`。
- `UFS_LOG_EARLY_TICK_MS`：前 30 s 每 100 ms 看一次环，`w.hz * 30000` 不溢出。`due` 的条件没变，所以只是更早发现 12 KB 突发，固定的 32 个槽会更快用完，这是设计。环形部分还有 477 个槽。
- `stall_touched`、`stall_fps_extra` 在 `poll` 线程调用里运行，都只读计数。【真机】崩溃轮 F 行已带 `tch 0 last - vl 0`。

### 3.6 调频

`gov.domain[d].why`、`asked` 都是 `uint8_t`，赋值路径覆盖全部分支。`whyRun` 用 `clip(…, 255)`。

`putWhy` 写进 160 字节的 `scmi::Text`，有上限。最长的行约 110 字符。

`checkDue`：等一个没有设档的窗口，最多多等 `kCheckWaitMs`（1 s），之后无条件检查。`c->retry.checked` 的恢复路径不等待。不会饿死检查。

### 3.7 A830Fence 与计量

`extLongest` 是局部变量，经 `atomicMax` 写入 `q_ext_max_`，结算时取走，只进日志。

`NativeAdrenoCallClassOf` 的 `default` 返回 `Adreno830ClassOther`，数组大小是 `Adreno830CallClasses`，任何方法号都不越界。`NativeAdrenoMeterRaise` 是有界的比较交换。`native_adreno_meter_fence` 定义在 `NativeAdrenoMemory.cpp:11`。

`runCore` 的两个调用者（`NativeAdrenoPublish.cpp:371`、`:939`）传的都是真实连接对象，`noteCall` 写的是对象内的数组。

### 3.8 面板探针线程的生命周期（本次崩溃的同类问题）

`start` 在没有发布背光时返回 `true`、服务留存，这条路在首测必走。我专门看了探针线程是否引用服务对象：

- `PaganiPanel.cpp:190`：线程参数是 `stages` 这个数值，不是 `this`。
- 10 个窗口和 `host` 都在线程自己的栈上（栈帧 `0x370`），退出时逐个释放。
- `panelProbeDisplay` 只读静态变量 `requestedPower`。
- 线程函数返回后由 `Call_continuation` 终止线程。

所以服务对象是否存在不影响线程。

### 3.9 `build-xnu-4k-baseline.sh`

删除列表加了 `bsd_kern`、`sched_prim`、`cpu`、`cpu_common`、`kern_sig`、`kdebug`，全部是 `unlink(missing_ok=True)`。这只影响构建，不进镜像。

`XNU_NATIVE_ADRENO_ARCHIVE` 没设时取原默认值，日常线行为不变。

【二进制】新内核里 `thread_dispatch`、`cpu_idle`、`cpu_signal_internal`、`cpu_signal_handler_internal` 都有对钩子的 `bl`，`pagani_kbtrace_note` 的调用在 `.cold.1` 里，所以这些对象确实带 `XNU_BOOT_PROGRESS` 编译过。

### 3.10 面板参数位

`1217` 的位 7、10 只被 `pagani_panel_probe_stages` 和 `pagani_panel_guarded` 读，其余选项函数都用各自的位，没有重叠。`Stage7Probe.cpp:126` 只看位 0。

## 四、沿真实调用顺序的二次抽查

这一节不是重复审查，只挑最像这次崩溃的点，按“这一行这个指针还活不活”再看一遍。

| 指针或值 | 使用点 | 为什么有效 |
|---|---|---|
| `excpath` | `kern_exec.c:2905` | 【二进制】两处 `bl _strnstr` 在 `error` 判断之后，释放在其后的 `bad_notrans:` |
| `head.name` 为 NULL | `stall_tick` 的 gap、ipi 分支 | `stall_line_head` 只在 `h->pid` 非 0 时读它 |
| `head.why` | 同上 | 三个分支都赋值；`crash_put_str` 本身也判空 |
| `c->fp` | `stall_sample_thread` | `map` 为空时置 0，`crash_walk_some` 直接返回 |
| `c->image` | `stall_trace_take` | 置 NULL，不走映射查询 |
| `stall_image.task` | 低优先级线程调用 | 取时持引用，`stall_image_finish` 释放；`crash_trace_image` 的 `context` 确实是 task，不是 map |
| `run.name` | `stall_note_run` | 当前线程的 proc，【真机】pid 1 已跑过 |
| `input->iova` | `notePast4G` | GPU 审查已查 |
| `frame_clock()` 与 `last_dispatch` | Q 行的 `wt=` | 都是 `mach_absolute_time`，`frequency()` 取 `CNTFRQ`，同一时间基准 |

## 五、给其他报告的补充和更正

1. **`preflight-exec.md` 第八节“阶段 1 种子无法比对”可以关掉**，见 3.3。
2. **`preflight-gpu.md`“S3 在 T0SZ=30 下通过没有真机证据”可以缩小。**
   - 【真机】S3 从阶段 1 的 382 ms（1.536 到 1.918）变成崩溃轮的 425 ms（1.534 到 1.959），多 43 ms；S4 到 S10 都是约 33 ms，没变。
   - 【二进制】默认值确实从阶段 1 的 `mov w8,#0x4` 变成 `#0x10`（`_pagani_gpu_start`）。
   - 多出来的时间符合页表从 2053 页变成 8209 页。之后 S7、S8 全过，`GPU RUN` 正常。
   - 【推测】这说明 16 GiB 表和 T0SZ=30 在真机上已经通过。4 GiB 以上的地址仍然没有被访问过。
3. **这些新代码已经在真机上执行过：** `gpu sec` 行（seq 2069）、`secondLine`、F 行尾部、`SystemOS root refs`。
4. **`preflight-stall.md` 第一节的 UBSan 可以再放宽。** `san/memory/ubsan_minimal.c` 默认标志是 `UBSAN_M_NONE`，有符号溢出只计数后继续，不 panic，除非启动参数带 `-ubsan_force_panic` 或发生在启动早期。`_stall_tick`、`_boot_progress_stall_map_read`、`A830Dcvs::decide` 里各有检查，都不致命。
5. **`stage15-probe.md` 的“渲染线程名未确认”有了一半旁证。** `coreanimation.render-server` 这个字符串在 21H461 共享缓存的 `.03` 分片里。它是不是 `pthread` 名字，要看 `Q th rs`。

## 六、发现

**没有新发现。** 考虑过、查后排除的假设：

| 假设 | 排除依据 |
|---|---|
| 探针线程在 `start` 返回后用到已释放对象 | 线程不持有对象，3.8 |
| `Device Characteristics` 在别的驱动上引出新路径 | 只有两个块设备的 `start` 变化，其余二进制相同 |
| 去掉诊断参数改变读路径 | 3.1 |
| 新参数位撞上旧选项 | 3.10 |
| 设备树或固件被连带改动 | 第一节 |
| 重编带进了别的改动 | 崩溃轮到新内核只差 exec 的两个函数 |
| `reset backup timer` 行在崩溃轮消失是新问题 | 见下 |

`reset backup timer` 的情况：阶段 1 两轮都在 1.17 s 记了这行，崩溃轮 4.85 s 内没有。代码上这行只有观察者不在第一定时器的 CPU 上才写，否则下一轮再试（`RamoopsLog.c:158`）。它可能落在缺失的序号 226–243 里，也可能这几轮都在 CPU 0。无论哪种都不影响定时复位，看门狗还有兜底。下一轮看到这一行即可。

## 七、打包后、上机前核对

1. 运行 `build-userspace/a830-inputs/fullrun/make-stage15.sh` 重打两张镜像。
2. 核对 `测试镜像/boot-A830-阶段15-首测.payload.json`：

| 字段 | 期望 |
|---|---|
| `kernel_sha256` | `8c04260179804246911584747a4235bfa8a37d13fa3ca0e123a0e7bdd71edf2c` |
| `data_ramdisk_sha256` | `39e635cb804d75f57ee3bc1c0495838ac21d27b58279ef26ea4009ccd61fc15f` |
| `trust_cache_sha256` | `9a4701ef5bf9ad1fdcb30d6283bac283f82fc1879b6e2915a85e70ba9b1c0842` |
| `kernel_args` | 996 字节，含 `pagani-panel=1217 … pagani-reset-after=420 … pagani-input-script=1` |

`adt_sha256` 每次打包都会变，这是预期。

3. `stall-report.py` 和 `symbolize-crashes.py` 用的 `--kernel` 要是 `8c042601` 这一份。

## 八、仍未验证

- 这次的比较只能证明差异清单完整，不能证明新代码在真机上正确。launchd 之后的新路径依然都没跑过，结论来自读码和反汇编。
- 预链接扩展数据段的差异没有逐条核对。
- 种子里 0700 目录的文件没有逐个比对。
- 第五节第 2 条的 16 GiB 判断来自耗时推断，日志里没有直接的光圈行（G1）。
- 探针的 MMIO 读、交叉调用第一次运行、4 GiB 以上的 GPU 访问，这些风险由各自的报告覆盖，本文没有新证据。

## 九、实际做了什么

- 解开阶段 1 和崩溃轮两张镜像的 gzip、FAT，取出内核、ADT、种子、信任缓存、`unixnu.toml`。
- 写脚本解析 ADT，比较 Mach-O 各段，逐函数归一化比较三版内核的反汇编（`f7f9908f`、`876e0f15`、`8c042601`）。
- 只读挂载两版种子生成清单，并做块级比较。
- 读 `phone-stall-probe.patch`、`phone-app-exec-count.patch` 全文，`PaganiPanel.cpp` 全文，`Mapping.h`，`PanelProbe.h` 头部，`BootProgress.c` 差异和探针胶水，`RamoopsLog.c`、`UfsLog.c` 的相关段，`CrashTrace.h` 差异，`CpuFreqGovernor.h`、`ArmCpu.cpp` 差异，`NativeAdrenoPublish.*`、`Adreno830Status.*`、`A830Dcvs.*`、`A830Fence.*`、`NativeAdrenoMeter.h` 差异，`ubsan_minimal.c`。
- 比较阶段 1 完整包与崩溃轮前 4.9 s 的日志，看崩溃轮 `msgbuf` 的错误行。
- 五份 `preflight-*.md` 和三份 `stage15-*.md` 全部读过，发现不重复列出。
