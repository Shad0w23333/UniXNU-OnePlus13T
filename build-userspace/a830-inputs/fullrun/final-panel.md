# 终审：面板探针 flush、`vmr` 拆分、页故障行额度

审查对象：内核 `75e75d4e`（`kernel.macho` 的 sha256 与 `测试镜像/boot-A830-阶段15-首测.payload.json`、`boot-A830-阶段15-完整包.payload.json` 的 `kernel_sha256` 一致），源码 `PanelProbe.h`、`PaganiPanel.cpp`、`UfsLog.c`、`UfsLog.h`、`Adreno830Status.{h,cpp}`、`NativeAdrenoPublish.cpp`、`NativeAdrenoVm.cpp`、`Adreno830Start.cpp`、`A830Smmu.{h,cpp}`。

全程只读。除本文件外没有改项目文件，没有运行 adb、fastboot，没有碰手机，没有编译内核。

## 结论

- **没有发现 crash、hang、storage、wrong-data 级别的问题。** B、C、D 三项在源码和反汇编上都与设计一致。
- 有 **1 条 minor**：P0 的 `panel: p0 lat` 行没有享受到这次 flush（发现 1）。不值得为它单独重编。
- 这次审查不需要重打镜像。

| 核对问题 | 结果 |
|---|---|
| 每次寄存器读都在写线程已停的时段里吗 | 是。flush 后的 GCC 重读和相位采样都在 `quiet(1)` 之后 1 s |
| flush 期间写几轮 | 放开的 3.5 s 里通常 1 轮，偶尔 2 轮；再暂停时最多补 1 上升沿轮 |
| 最后一条写命令到下一次读的余量 | 典型约 500 ms，最坏 `500 − R1 − R2 − L`，按真机数据约 480 ms，与 `enter` 相同 |
| P1 被跳过时 P2、P3 会读错块吗 | 不会。`read_gates` 每次先读 GCC，门闩关着时除 GCC 外什么都不读；P3 看 `p->phase`，为 0 直接跳过 |
| P3 第二次 `p3_open` 仍紧贴读之前吗 | 是。`flush → p3_open → 读`，中间没有睡眠 |
| 时间线相容吗 | 相容。P3 结束约 355.04 s，离 405 s 降频 50 s，离 420 s 复位 65 s |
| hold 总时长 | 12 段，合计约 12.17 s，最长一段约 1.11 s（上一版 10 段，约 11.16 s，最长 2.03 s） |
| `reserve_objects`、`reserve_spans` 的折叠、比较、清零 | 都跟上了。三个翻译单元的字段偏移一致 |
| `meterLine` 宽度与 Row 上限 | 最宽 186 / 309，Row 上限 319，不截断 |
| `g.lock` 外读计数 | 是，但只是对齐的 32 位读，最多旧 1 s，没有危害，也不是这次引入的 |
| `fault_lines[gpu]` 下标 | 正确，`cset` 只会是 0 或 1，数组 2 项 |
| `g830` 被整体清零吗 | 内核里不会。唯一的重建在 `ADRENO830_HOST_TEST` 里，符号表没有这些函数 |
| `board` 为空时 `smmuFaultSink` 会崩吗 | 走不到。空 `board` 在 S0 就被拒，S3 才建下半部 |

---

## 一、B：PanelProbe 的 flush

### 1. 每次读是否在停写时段

`panel_probe_flush`（`PanelProbe.h:134-138`）的顺序是 `quiet(0)`、睡 3500、`quiet(1)`、睡 1000，与 `panel_probe_enter` 只差一条日志。

我把 `panelProbeThread` 全部反汇编还原成事件序列（脚本 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/final-panel/trace_probe.py`）。`host` 在 `sp+0xa8`，`log/sleep/ticks/display/quiet` 在 `0x1f0/0x1f8/0x200/0x208/0x210`。

| 阶段 | 序列 | 关键地址 | 读都在 `quiet=1` 内吗 |
|---|---|---|---|
| P0 | GCC、TE、DSI 三次 enter，各块读在 enter 之后 | `0xfffffff00c0fb898` 阶段末 `quiet(0)` | 是 |
| P1 前半 | GCC、DSI 已入过直接跳过（`tbnz w19,#0`、`#2`），DISP 走 enter，然后 GCC 门闩和四组读 | `...ba50` GCC，`...ba84`–`...bc68` DISP | 是 |
| P1 flush | `quiet(0)`、睡 `0xdac`、`quiet(1)`、睡 `0x3e8` | `...bca0`、`...bcb0`、`...bcc0`、`...bcd0` | 期间没有读 |
| P1 后半 | GCC 重读，门闩判断，`panel_probe_sample_phase` | `...bcdc`、`...bce0`–`...bcf0`、`...bcfc` | 是，`quiet(1)` 之后整 1 s |
| P2 | 五块各自 enter，`read_gates` 在 enter 之后 | `...bef8`、`...c024`、`...c198`、`...c304`、`...c494` | 是 |
| P3 前半 | INTF enter，`p3_open`，三个 INTF 读 | `...c678`、`...c688`–`...c6a8` | 是 |
| P3 flush | 同 P1 | `...c6d8`、`...c6e8`、`...c6f8`、`...c708` | 期间没有读 |
| P3 后半 | `p3_open`，三个 INTF 读 | `...c710`、`...c720`–`...c740` | 是 |

- 门闩编译正确：`0xfffffff00c0fbce0 tbz w0,#0`，加 `0xfffffff00c0fbce4 and w8,w0,w27`（`w27=0x80000002`，在 `...ba58` 赋值），比较 `0x80000000`，与 `panel_probe_gcc_on` 等价。
- 所有退出路径都汇到阶段末的 `quiet(0)`：P1 两个跳过分支到 `...bde0`，P3 的 `cbz w0` 两处到 `...c788`。`began` 在 P1 入口存到 `sp+0x50`（`...ba70`），`...bddc` 恢复，P2 的等待时刻不受影响。
- 线程走完时 `quiet` 一定是 0，没有新路径把它留在 1。

线上别的写者没变化：`boot_log_export_put` 在 `held` 时返回 -9。首测启动参数没有 `sheng-data-persist`。

### 2. flush 期间写线程的行为

设 flush 开始（`quiet(0)`）为 F，此前的暂停开始为 T = F − 1000 ms。写线程 tick 约 501.3 ms 一拍，`UFS_LOG_PERIOD_MS` 3000，`burst` 12 KiB（`UfsLog.c:349`）。

**放开的 3.5 s 内：**
- 上升沿轮把 `next_round` 推到 T+δ+3000（δ 在 0 到 500 ms），也就是 F+2000+δ。它在同一 tick 网格上，所以第 6 拍在 F+2008+δ，最晚约 F+2508 触发普通轮。
- 这一轮之后 `next_round` 超过 F+5000，不会再有普通轮，除非 `events >= burst`。
- 如果上升沿那拍 `fresh` 为 0，`next_round` 没有被推后，第一轮可以早到 F 之后第一拍，3 s 后还能再来一轮，这就是 2 轮的情况。

**再暂停时：** 第一拍看到 `held && !w.held`，只要 `fresh` 就补 1 上升沿轮。

**所以一次 flush 写 1 到 3 轮，常见 2 轮。** 一轮是槽的若干连续段、可能的 msgbuf、头、flush。真机一轮约 1 ms，历史最大 7.1 ms。

**余量。** 设 `quiet(1)` 在 T'，读在 T'+1000：

| 相位 | 最后一条写命令结束 |
|---|---|
| 普通情况：T' 之后第一拍做上升沿轮 | T' + δ + R，δ ≤ 500 |
| 最坏：T' 之前一拍读到 held=0 且正好有一轮（只在上面“2 轮”的相位或 burst 时发生） | T' + R1 + 500 + L + R2 |

最坏余量是 `500 − R1 − R2 − L`，结构上与 `enter` 完全一样。按真机数据（L 约 1.3 ms，R 约 1 ms，最大 7.1 ms）约 480 ms。

模型按新顺序重写，读窗口用真实长度，每组 4000 次随机相位，脚本 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/final-panel/sim2.py`：

| tick 迟到均值 | 一轮耗时 | 其他日志间隔 | flush 后读的最小余量 P1 / P3 | 全部读的最小余量 | 读与轮重叠 | flush 前的行没赶上读 |
|---|---|---|---|---|---|---|
| 1.3 ms | ≤1.5 ms | 2 s | 497.6 / 497.5 ms | 494.1 ms | 0 | 0 |
| 5 ms | ≤7 ms | 2 s | 485.8 / 475.2 ms | 453.0 ms | 0 | 0 |
| 20 ms | ≤7 ms | 0.5 s | 391.3 / 347.4 ms | 290.8 ms | 0 | 0 |
| 100 ms（极端） | ≤7 ms | 0.5 s | 4.1 / 14.2 ms | 3.8 ms | 14 | 0 |
| 200 ms（极端） | ≤7 ms | 0.5 s | 3.3 / 3.4 ms | 0.4 ms | 81 | 0 |

- 后两行远超真机，只说明余量靠的是 tick 不严重迟到。这一点 `enter` 本来就如此，这次没有变差。
- flush 前写下的行（P1 四组、P3 第一行）在所有组合下都赶在读之前上盘。flush 的目的达到了。
- 一次 flush 的轮数分布（第一行组合，P1）：放开期 1 轮不补 1427、放开期 1 轮补 1 轮 1871、放开期 2 轮不补 522、放开期 2 轮补 1 轮 180。没有其他日志时全是放开期 1 轮。

### 3. P1 被跳过时对 P2、P3 的影响

| 情况 | `dispcc` | `phase` | P2 | P3 |
|---|---|---|---|---|
| 第一次门闩关 | 0 | 0 | `p2 skipped no p1`，不 enter，不暂停 | `p3 skipped no full phase` |
| flush 后门闩关（新路径） | 1 | 0 | 五块照常 enter，见下 | `p3 skipped no full phase` |
| 相位读完但不全开 | 1 | 0 | 同上 | 同上 |
| 相位全开 | 1 | 1 | 正常 | 正常 |

新路径的 P2：
- `panel_probe_read_gates` 每次先读 GCC，`gcc_on` 为假立刻返回，`rscc_on`、`ahb_on`、`gdsc_on` 都是 0。反汇编 `0xfffffff00c0fbf04 tbz w0,#0` 和 `...bf14 b.eq` 直接跳到 skipped 分支，不读 DISP。
- 五块的门都不成立，全部打 skipped 行。**除 GCC 外一个寄存器都不读。** GCC 是 P0 已连读 1024 次的块。
- 如果 15 s 后 GCC 又开了，P2 按各块自己的门读，和改动前“相位不全开”走的是同一条路。P2 的门不依赖 `p->phase`，所以这里没有新依赖。

`p->phase` 在 `panel_probe_run` 里初始化为 0，新路径不写它，P3 在 INTF 的 enter 之前就返回。

这条路径的 P2 仍有 5 次暂停、5 × 4.5 s，只是日志变多，无害。

### 4. P3 第二次 `p3_open`

反汇编 `0xfffffff00c0fc708` 睡 1000 → `...c710 bl p3_open` → `...c714 cbz w0` → `...c720` 读 INTF。中间没有睡眠。`p3_open` 内是显示状态、GCC、三个 DISP 读、64 组相位约 16 ms，返回后直接读。门闩判定 `gap` 用 `#0x208e`（8334，`gap_us<=8333`）。

第一次读到的 `te`、`frame`、`tear` 保存在 `x19–x21`，跨 flush 不变，第二行照常打印。`fc A->B` 的间隔现在约 4.52 s。两行各有自己的日志时刻，算频率时用记录时刻差，不要写死 1 s。工具里没有解析这两行的代码。

### 5. 时间线和 hold（探针零点按 1.50 s）

| 日志时间 | 事件 |
|---|---|
| 266.0 s | `panel: p0 gcc next` |
| 270.5 / 275.0 / 279.5 s | GCC / TE / DSI 首读 |
| 279.6 s | P0 结束 |
| 286.0 s | `panel: p1 dispcc next` |
| 289.5 / 290.5 s | 暂停 / GCC 门闩和四组读 |
| 290.5–294.0 s | **flush：放开** |
| 294.0 / 295.0 s | 暂停 / GCC 重读和相位 |
| 295.02 s | P1 结束 |
| 310.5、315.0、319.5、324.0、328.5 s | P2 五块首读 |
| 346.0 s | `panel: p3 intf next` |
| 349.5 / 350.5 s | 暂停 / 第一次 `p3_open` 和读 |
| 350.52–354.02 s | **flush：放开** |
| 354.02 / 355.02 s | 暂停 / 第二次 `p3_open` |
| 355.04 s | P3 结束，`probe end` |
| 405 s | GPU 降频（`RESET_PARK_SECONDS=15`，`RamoopsLog.c:286`） |
| 420 s | 定时复位 |

- P1 结束到 P2 面包屑 11 s，P2 到 P3 17.5 s，阶段不重叠。常量 `0x145959c00`、`0x15c78fc00`、`0x18a3fbc00` 对应 284500、304500、344500 ms，时刻没变。
- 复位前 2 s 的 `UFS_LOG_FINAL_MS` 不重叠。P3 最后几行在 `quiet(0)` 后 3 s 内上盘，约 358 s。
- 挂死分析与上一轮相同。启动 CPU 上挂死时咬合最晚从约 381 s 变成约 385 到 388 s，仍早于 405 s。

| 阶段 | 暂停段 | 上一版 | 这一版 |
|---|---|---|---|
| P0 | 3 | 约 3.11 s | 约 3.11 s |
| P1 | 2 | 约 1.02 s（1 段） | 约 2.02 s |
| P2 | 5 | 约 5.00 s | 约 5.00 s |
| P3 | 2 | 约 2.03 s（1 段） | 约 2.04 s |
| 合计 | 12 | 约 11.16 s（10 段） | **约 12.17 s** |
| 最长一段 | | 2.03 s（P3） | **1.11 s（P0 的 DSI）** |

最长暂停减半，对事件环更有利。`pagani-panel=65` 的完整包不起探针线程，不受影响。

---

## 二、C：`vmr` 拆成两项

| 路径 | 位置 | 核对结果 |
|---|---|---|
| 计数 | `NativeAdrenoVm.cpp:544-551` | 都在 `VM_LOCKED` 内，`[0]` 对象，`[1]` 区段，这次没动 |
| 读取 | `NativeAdrenoPublish.cpp:1369` | 【二进制】`0xfffffff00bffbe78`、`...be7c` 两次 `ldr`，`...be88 stp w10,w8,[x19,#0x38]` |
| 折叠 | `Adreno830Status.cpp:525` | `runClients=*clients` 整体拷贝，`foldCalls` 只管 `calls`。这两项是开机累计，本来就不进窗口。【二进制】拷贝长度 `0xdc`，含 `#0xcc` 处的尾段 |
| 输出 | `Adreno830Status.cpp:616` | 【二进制】`0xfffffff00c0180c8 ldr [#0xa18]`、`...8170 ldr [#0xa1c]`，各自封顶 99999，中间是 `/` |
| 比较 | `Adreno830Status.cpp:652-654` | `strcmp(line,meterLast)` 比的是文本。任何一项变了都写新行，封顶后不再变 |
| 清零 | `Adreno830Status.cpp:498-499` | `runClientsSet=false`、`meterLast[0]=0`。`runClients` 不清，但只在 `Set` 时用。`*out={}` 和 `clients{}` 都扩到 `0xdc`（`native_adreno_client_tick` 的 `stur q0,[x19,#0xcc]`，`dcvsMemoryRow` 的 `stur q0,[x22,#0xcc]`） |

**跨翻译单元的布局。** 这是这项改动唯一可能静默出错的地方，逐字段核过：

| 字段 | 偏移 | 写（Publish） | 读（Status） |
|---|---|---|---|
| `window_overflows` | `0x34` | `str w8,[x19,#0x34]` | `#0xa14` |
| `reserve_objects` | `0x38` | `stp w10,w8,[x19,#0x38]` | `#0xa18` |
| `reserve_spans` | `0x3c` | 同上 | `#0xa1c` |
| `faults_alike` | `0x40` | `stp ...,[x19,#0x40]` | `#0xa20` |
| `faults_polled` | `0x4c` | `stp ...,[x19,#0x48]` | `#0xa2c` |
| `calls` | `0x50` | `str w9,[x19,#0x50]` | `foldCalls` 读 `[x0,#0x50]` |
| `second_calls` | `0xa0` | `str w10,[x19,#0xa0]` | `#0xa80` |
| `second_top_calls` | `0xa4` | `ldr w17,[x19,#0xa4]` | `#0xa84` |
| `second_top` | `0xa8` | | `#0xa88` |
| `second_classes` | `0xbc` | `stp w8,w9,[x19,#0xbc]` | |
| 总长 | `0xdc` | | |

`runClients` 在 `0xfffffff00c35c9e0`。Publish、Status、Start 三个翻译单元都是新头文件编出来的。

**宽度。** 按格式逐段累加：无调用数字 186，带调用数字 186 + 123 = 309，与 `test_status.cpp:1145`、`:1283` 的字面量一致（我量了字符串，没有跑这个测试）。

- `Row r(line,Adreno830StatusMeterBytes-1)`，上限 319。【二进制】`cmp x9,#0x13e` 后写，末尾 NUL 在界内。
- 186 不超过 `RAMOOPS_LOG_TEXT_MAX`（200），一条记录放得下。
- 309 由 `emit_text` 切成 200 + 109 两条，第二条是 `+` 记录。`boot_progress_trace` 没有长度上限。

**`g.lock` 外读计数。** 对。`native_adreno_vm_reserve_refused` 的读在 `IOLockUnlock(g.lock)` 之后（`:1359` 解锁，`:1369` 读）。这是对齐 32 位 `volatile` 读，读者只有 DCVS 线程，写者在 VM 锁下递增。两项之间可能错开一次映射，最多旧 1 s，而且是累计量，下一秒就追上。这和 `native_adreno_slot_pressure`、`native_adreno_fault_attribution` 同一种读法，不是缺陷。

**工具。** `工具/gpu-test-loop` 没有解析 `vmr` 的代码，`工具/analyze-a830-run.sh` 只 `grep` 再截 260 字符。`0/0` 的新格式不会弄坏任何解析。

**读日志要知道的一点（不算缺陷）：** 一次映射只计一项。对象先判，两边都超限时只计对象。`vmr N/0` 不等于区段绝对没到顶。

---

## 三、D：`fault_lines` 分桶

| 问题 | 结果 | 证据 |
|---|---|---|
| 下标 | 正确 | `Adreno830Start.cpp:843-849`。【二进制】`0xfffffff00c023e14 cset w20,eq`，`...3e20 add x8,x21,w20,uxtw #2`，`ldr w8,[x8,#0x2690]` |
| 跨调用的比较值 | 有效 | `w20`、`w21` 是被调用者保存寄存器，`native_adreno_note_page_fault` 之后仍可用 |
| 上限 | 每桶 8 条 | `cmp w8,#0x7`、`b.hi` |
| 递增 | 只在写行之后 | `bl _boot_progress_trace` 之后 `add x8,x21,x20,lsl #2`，读改写 `[x8,#0x2690]` |
| 类型 | 同宽比较 | `fault.cb` 和 `cb_gpu` 都是 `uint8_t`（`[x1,#0x16]`、`[board,#0x1b1]`） |
| 布局 | 无重叠 | `smmu_fault_call` 在 `g830+0x2688`，`fault_lines` 在 `+0x2690..0x2697`，`smmu_claimed` 在 `+0x2698`，后面成员对齐到 `+0x26a0`，整体后移 8 字节 |
| 并发 | 没有竞争 | `drainFaults` 只有 `smmuFaultWork` 一个调用者，`kCallAllocate` 用 `THREAD_CALL_OPTIONS_ONCE` |

**初始化。** `g830` 在 `__bss`（`0xfffffff00c35cac0`），`fault_lines` 没有默认成员初始值，保持 0。

- 全局构造 `__GLOBAL__sub_I_Adreno830Start.cpp` 只写默认成员初始值，用的都是 0，且在任何 fault 之前。
- 全仓库没有 `memset(&g830…)`、`*d=…`。`g830.~Adreno830Device()` 和 `new (&g830)` 只在 `adreno830_start_reset`，`adreno830_device_for_test` 也一样，都在 `#ifdef ADRENO830_HOST_TEST` 里。**内核符号表里这几个符号 0 个。**
- 没有哪条路径在开机中途清掉额度，这和“每次开机”的语义一致，恢复也不清。

**`board` 为空。**
- `adreno830_start` 先写 `g830.board=board`，`g830.started` 保证只进一次。
- `stageInputs` 的 `validateBoard` 在 `board` 为空或 magic、version 不对时返回错误（`:933-937`），`runStages` 在 S0 就停。
- `smmu_fault_call` 只在 S3 分配，`fault_notify` 只在 S3 的 `init` 之后才有。`poll()` 还要 `surveyed_` 和 `b.reset`。**没有任何路径能在 `board` 无效时走到 sink。**
- `board` 是 `PaganiGpuBoard.cpp:21` 的文件级静态对象，生命周期覆盖整机。

`smmuFaultSink` 里 `g830.board->cb_gpu` 这句这次没动。

**额度。** 最多 16 行，每行不超过 90 字符，总共不到 1.5 KB，对 32 KiB 事件环没有影响。`pagani-diag=1`、`pagani-gpu-status=3`、`pagani-ramoops=1` 两张镜像都有。

---

## 四、改动范围核对

旧内核本体不在磁盘上：`boot-A830-阶段15-首测.payload.json` 已是 `75e75d4e`，`scratchpad/critic` 里只有 `k1`–`k3` 的反汇编文本和符号表，没有内核文件（超过 50 MB 的文件都按文件头看过）。上一轮留下了全文反汇编 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/delta/k4.s`，地址与 `delta-*.md` 记录的 `aa94decf` 一致（例如 `_ufs_log_writer_tick` 在 `0xfffffff00c13e2b8`），我用它比较。它的来源只能靠地址判断，没有哈希证明。

47029 个函数逐个规范化比较：

- 去掉地址和数据页偏移后，145 个不同。
- 其中 134 个再去掉立即数后相同，只是偏移变了：`g830` 成员后移 8 字节，新静态变量让 BSS 平移。UfsLog、RamoopsLog、GunyahWdog 的函数都在这一类。
- **结构不同的只有 11 个：**

| 函数 | 原因 |
|---|---|
| `panelProbeThread` | B |
| `native_adreno_client_tick`、`adreno830_status_clients`、`adreno830_status_meter`、`dcvsMemoryRow` | C，结构多 4 字节 |
| `smmuFaultSink` | D |
| `stageFirmware`、`stageGmu`、`bindGmuServices`、`__GLOBAL__sub_I_Adreno830Start.cpp` | D 的连带：`g830` 成员偏移变化改变了立即数编码和指令选择 |
| `fps_observe` | A，不在本报告范围 |

`_ufs_log_writer_tick` 除数据偏移外没变。按相邻符号估长度时 `_writer_thread` 从 2064 变 1732 是假象：它后面紧跟页对齐的 `_early_boot_progress_entry`，长度里含填充，起点只是平移了 `0x14c`。它不在上面的差异列表里。

所有相关源文件的修改时间都早于内核（`08:14:16`），最晚的是 `Adreno830Status.cpp`（`08:09:25`）。

---

## 五、发现

### 发现 1（minor）：P0 的 `panel: p0 lat` 行没有 flush

- **位置**：`PanelProbe.h:225-227`，规则在 `PanelProbe.h:13-14`。
- **路径**：
  1. `enter(DSI)` 后 `quiet=1`。
  2. 延迟读完，`panel: p0 lat gcc … te … dsi …` 写进环，这时写线程已停。
  3. 直接进 `panel_probe_sample`，100 ms 内读约 5000 组 DSI `0x0/0x4/0x8` 和 TE。
  4. 如果这里挂死，这一行不会进普通轮。
- **二进制**：`0xfffffff00c0fb624` 写行，`...b6f4` 读 TE，`...b764`–`...b784` 读 DSI，`...b850`、`...b888` 写行，`...b898` 才 `quiet(0)`。中间没有 `quiet(0)`。
- **为什么算同一类**：文件头说在“看得比上次更多的读”之前让写线程走一轮，举的是 P1 相位和 P3 第二次。P0 的取样比延迟循环多读两个寄存器，频率也高得多，正是注释第 31 行担心读过滤器计数的地方。
- **影响**：
  - 只损失这一行里的三个延迟值。块的面包屑已在盘上，挂死位置仍清楚。
  - 非启动 CPU 挂死：420 s 的最后快照带走，没有损失。
  - 启动 CPU 挂死：ramoops 通常保得住。
  - 只有固件很快冷复位（12:15 那种）才丢。这时延迟值恰好最有用，它能说明读是否陷入 EL2。
- **概率低**：`PaganiPanel::start` 在 1.5 s 已读过 `0x0/0x4/0x8`，`report` 在 46.5 s 又读过，寄存器不是新的，只有频率是新的。
- **修法**（可选，下次重编时顺带）：`panel: p0 lat` 之后加一句 `panel_probe_flush(p);`。P0 结束从 279.6 s 变成 284.1 s，仍早于 P1 的 286.0 s，hold 多约 1 s，`Test/panel-probe.cpp` 的 P0 时序要同步。**不建议为此单独重编。**

没有其他发现。

---

## 六、未验证

- 探针的所有 MMIO 读没有上过机。门闩在真机上是否足够，只有首测能回答。
- 写线程的最坏调度延迟：仍只有均值（每拍约 501.3 ms），没有 `L` 的最大值。
- 一轮耗时在卡顿探针满载时的表现。真机数据来自没有 `M`、`Q` 大量输出的轮次。
- 上表“其他日志间隔”是假设值，290–355 s 的真实日志速率这版没有真机数据，只影响轮数分布，不影响余量上界。
- `delta/k4.s` 的出处只靠地址判断，见第四节。
- `test_status`、`test_start` 我只读了断言，没有运行。`Test/panel-probe.cpp` 我编到草稿目录跑过一遍（59427402 项通过），本报告的结论不依赖它。
- `fps_observe`、`StallProbe.h`（改动 A）不在本报告范围。

## 七、上机时顺带看

`delta-panel.md` 第七节的 `key presses` 期望值要改。全跑完现在是 12 次（3+2+5+2）。

| 走法 | 全程 N |
|---|---|
| 全跑完 | 12 |
| P1 第一次门闩不开 | 4 |
| flush 后门闩关（`panel: p1 phase skipped gcc …`） | 10 |
| 相位不全开（`p3 skipped no full phase`） | 10 |
| P3 第一次 `p3_open` 不过 | 11 |
| 探针没走到 | 0 |

统计行每 30 轮才一条。按首测 420 s 约 145 轮估算，最后一条大约落在 348 s 附近，所以全跑完时看到的通常是 10，可能是 11。这是估算，不是判据。

| 看什么 | 期望 |
|---|---|
| `panel: p1 link …` 的记录时刻 | 约 290.5 s，并且在 UFS 上；相位行约 295.0 s |
| `panel: p3 intf te … fc A int C` | 约 350.5 s，在 UFS 上 |
| `fc A->B` | 约 4.52 s，120 Hz 时约 542 |
| `gpu meter … vmr A/B` | 两个数分开；`B` 判断 262144 是否够 |
| `gpu fault cb…` | CB0 和其他 bank 各最多 8 行 |

## 八、顺带（不算发现）

- `PaganiPanel/README.md:74` 的行清单没有列新行 `panel: p1 phase skipped gcc V`，也没有列 `panel: p3 intf te T fc A int C`。其余描述都已更新。
- `Adreno830/docs/PROGRESS.md:480` 的“P3 约 351 s 结束”是 `aa94decf` 的数，现在约 355 s。
- `测试清单.md` 和 `Adreno830/docs/PROGRESS.md` 在本次审查期间（08:24:58）被更新过，校验值已是首测 `11503699`、完整包 `13d2b14c`、内核 `75e75d4e`，与我算出的一致。源码和内核没有变。
