# 终版内核二进制核对与改动 A 审查

审查对象：内核 `75e75d4e901baf3a…`（`kernel.macho`，11-01 08:14 构建）。比较基线是 `aa94decf280c22a9…` 的反汇编。

全程只读。没有运行 adb、fastboot，没有碰手机，没有编译内核。除本文件外，只在 `scratchpad/final-binary` 写了临时文件。主机测试也编译在那里。

证据标注：【二进制】反汇编或字节比较，【读码】源码，【回放】用真机 F 记录重算，【估算】按格式推算，【未验证】读码得不出结论。

## 结论

- **四处改动都在 `75e75d4e` 里，指令语义和源码一致。**【二进制】
- **逐函数比较没有发现预期之外的改动。** 47029 个函数里，46943 个完全相同，75 个只差地址，11 个指令序列不同。这 11 个里 6 个是改动本身，5 个是 `fault_lines` 变长后结构体偏移 +8 的连带结果。新增符号只有 `_stall_m_until`，没有消失的符号。
- **改动 A 没有发现 crash、hang、storage、wrong-data 级问题。** 任务列的七个问题全部核过，结论在第四节。
- **发现 2 条 minor，都在 `stall-report.py` 的呈现层，不影响内核，不值得单独重编。**

| 编号 | 严重度 | 一句话 |
|---|---|---|
| R1 | minor | 尾巴秒的 `free` 是当下值，报告 VM 列仍写“最小 free” |
| R2 | minor | `-/-` 只看 `tick` 真假，采样器这一秒只跑了一拍时会盖住 `stuck` 计数 |

**比较的边界：** `aa94decf` 的二进制已经不在磁盘上。比较用的是它的反汇编，只覆盖代码段，数据段无法逐字节比较（第一节）。

## 一、比较基线

任务说可比较的旧内核在 `测试镜像/boot-A830-阶段15-首测.img` 里。实际情况：

| 时刻 | 事件 |
|---|---|
| 约 08:15 之前 | 我读到 `payload.json` 的 `kernel_sha256` 还是 `aa94decf` |
| 08:15:03 | 主会话的 `make-stage15.sh` 重打了首测包 |
| 08:15:11 | 重打了完整包 |
| 之后 | 我解包首测镜像，得到的内核是 `75e75d4e`，已不是旧内核 |

两张镜像我都解包算过哈希，里面的 `kernel.macho` 都是 `75e75d4e`。【二进制】

`scratchpad/critic` 里的 `8c042601` 只剩反汇编，而且比 `aa94decf` 更旧。

实际用的基线是 `scratchpad/delta/k4.s` 和 `k4.nm`，也就是增量审查留下的 `aa94decf` 反汇编。它的来历靠时间确认：

- `kernel-build-a830-integrate9.log` 07:17:09 结束，内核哈希前缀 `aa94de`。
- `k4.s` 生成于 07:40，文件头写的是同一路径的 `kernel.macho`。
- 下一次构建 `integrate10` 08:14:16 结束，内核哈希前缀 `75e75d`。

中间没有别的构建，所以 `k4.s` 就是 `aa94decf`。这是时间推断，不是哈希验证。

新内核用同样的 `llvm-objdump --no-show-raw-insn -d` 和同一份 `norm.py` 处理。两边都是 `__TEXT_EXEC,__text`、`__TEXT_EXEC,__hib_text`、`__KLD,__text` 三节。

**比较不到的部分：** 数据段和 prelink 段没有旧字节可比，只能间接看。

- 符号表逐项比较，偏移都能解释（第二节）。
- `emit_prelink.py` 的命令行两次完全相同（SHA-1 一致），输入 `kernelcache.decompressed` 是 10-29 的文件。
- 这次是增量构建，`integrate10` 没有 `prepare XNU` 和逐个打补丁的步骤。

## 二、逐函数比较

| 结果 | 函数数 |
|---|---:|
| 总数（新旧相同） | 47029 |
| 完全相同 | 46943 |
| 只差地址 | 75 |
| 指令序列不同 | 11 |

### 11 个指令不同的函数

| 函数 | 指令数 | 归属 |
|---|---|---|
| `_fps_observe` | 6208→6219 | A |
| `panelProbeThread` | 1449→1492 | B |
| `adreno830_status_meter` | 3285→3327 | C |
| `adreno830_status_clients` | 106→108 | C，结构体大 4 字节 |
| `_native_adreno_client_tick` | 515→514 | C |
| `smmuFaultSink` | 364→371 | D |
| `dcvsMemoryRow` | 66→66 | C、D 连带 |
| `stageFirmware` | 237→237 | D 连带 |
| `stageGmu` | 325→323 | D 连带 |
| `bindGmuServices` | 87→88 | D 连带 |
| `__GLOBAL__sub_I_Adreno830Start.cpp` | 1222→1202 | D 连带 |

**连带的原因：** `fault_lines` 从一个 `uint32_t` 变成两个，占 `0x2690..0x2697`。`smmu_claimed` 从 `0x2694` 移到 `0x2698`。`gmu_memory` 要 8 字节对齐，从 `0x2698` 移到 `0x26a0`。所以它之后的 `g830` 字段全部 +8。

这 5 个函数逐条看过：

- **`stageGmu`、`stageFirmware`、`bindGmuServices`：** 除偏移 +8 外，只有寻址方式变了。比如 `str q0,[x19,#0x3f20]` 的偏移变成 `0x3f28` 后不再是 16 的倍数，编译器改用 `mov`、`add`、`stp`。
- **`dcvsMemoryRow`：** 栈上的 `Adreno830StatusClients` 从 `0xd8` 字节变成 `0xdc`，栈帧 `0x130→0x140`，清零尾部从 `str xzr` 变成 `stur q0,[x22,#0xcc]`。
- **全局构造函数：** 静态累加的写入量差 16 字节（12984 对 12968），所以写了一个小解释器把新旧两版实际执行一遍。两版在 `g830` 内都写 65649 字节，按偏移规则映射后区间完全相同，值没有差异，都调用两次 `_bzero`。那 16 字节在 `g830` 之外，是函数序言少保存一对寄存器。

### 75 个只差地址的函数

归一化把立即数也抹掉了，所以这 75 个也逐行分了类：

| 差异 | 行数 | 解释 |
|---|---:|---|
| `g830+X` 偏移 +8 | 146 | 旧偏移 `0x2698..0x12638` |
| `g830+0x2694` 偏移 +4 | 2 | `smmu_claimed` |
| 立即数 +8 | 149 | 结构体或 bss 偏移 |
| 立即数 +1 | 2 | `gmuMap` 里按 8 字节缩放的下标，`0x5b9→0x5ba` |
| 立即数 +4 | 2 | `0x2694→0x2698`，以及 `_adreno830_start` 里相对 `smmu_claimed` 的 `0xf65c→0xf660`（绝对偏移 `0x11cf0→0x11cf8`，仍是 +8） |
| `adr` 绝对地址 | 11 | 跳转表，函数内相对偏移全部不变 |

和 `g830` 无关的函数（`_stall_tick`、`_ufs_log_writer_tick`、`_wdog_alive`、`_wdog_pet_thread`、`_ramoops_log_*`、`_gunyah_wdog_after_failed_reset`、`A830Trace::operation`）另做了一遍精确核对。把差异行的目标地址解析成符号加偏移，532 行里 529 行新旧相同。剩下 3 行是脚本把基址寄存器追丢了，逐个按实际页基址算过，目标分别是 `_lock_suspect`、`_panic_line+0xcc`、`_console_line+0xcc`，新旧的符号和偏移都相同，同样只是后移 8 字节。

`_stall_input`、`cmdDbSnapshot`、`gPersonalities` 名下的几行也是同样的跟踪假象，指令本身不变或同样 +8。

### 符号布局

用 `k4.nm` 和新符号表比较了 69753 个符号，地址偏移分成 19 段，每一段都有来由：

| 区域 | 偏移 | 来由 |
|---|---:|---|
| `__TEXT` 里 `__cstring` 之后 | +31 | 正好是 `"panel: p1 phase skipped gcc %x"` 加结尾 NUL |
| `__TEXT_EXEC`，从 `_native_adreno_publish` 到 `_writer_thread` | -4 到 +332 | 各函数大小变化累加 |
| `_early_boot_progress_entry` 起 | 0 | 对齐吸收 |
| `publishLock` | +4 | `runClients` 大 4 字节 |
| `g830` | 0 | 对齐吸收 |
| `cmdDbSnapshot` 一段 | +8 | `g830` 大 8 字节 |
| `zapMetadata` 起 | 0 | 对齐吸收 |
| `_stall_second_line` 到 `_last_state` | +8 | 插入 `_stall_m_until` |
| `_early_boot_progress_stack` 起 | 0 | 对齐吸收 |

`_stall_m_until` 在 `0xfffffff00c3af290`，前面的 `_stall_kbt_limits` 到这里正好结束，后面是 `_stall_second_line`，没有重叠。

`kernel.macho` 和 `mach.development.bcm2837.unstripped` 在 `_fps_observe`、`panelProbeThread`、`smmuFaultSink`、`adreno830_status_meter`、`adreno830_status_clients`、`_native_adreno_client_tick`、`_stall_tick`、`_ufs_log_writer_tick` 范围内逐字节相同。

## 三、四处改动在二进制里的证据

以下地址前缀都是 `0xfffffff00c`。

### A：`_fps_observe` 内联的 `stall_second`

`_fps_observe` 只有 7 处差异，全部属于 A。门控这段：

| 地址 | 指令 | 含义 |
|---|---|---|
| `1176fc` | `cset w9, ne` | `frames != 0`，来自 `_fps_scan+0` |
| `117708` | `str w9,[sp,#0xc]` | 存布尔值 |
| `117710` | `mrs x28, CNTFRQ_EL0` | `hz` |
| `1179c0`、`117b44` | `str`/`ldr x28,[sp,#0xa8]` | 循环里借用 `x28`，出循环恢复 |
| `1180e4` | `bl _mach_absolute_time` | `now`，在 `x0` |
| `1180ec` | `ldr w8,[_stall_touches]` | 这一次观察的触摸数 |
| `118100` | `mov w8,#0xa` / `madd x8,x28,x8,x0` | `now + hz * 10`，64 位 |
| `11810c` | `str x8,[_stall_m_until]` | 写 |
| `118120` | `ldr x8,[_stall_m_until]` | 读 |
| `118124`、`118128` | `cmp x0,x8` / `b.lo` | 无符号比较，未到期就写 M |
| `118130`、`118134` | `ldr w8,[_stall_armed]` / `cbz` | 到期且采样器睡着才跳过 |

和 `if (frames || stall_touches) stall_m_until = now + hz * STALL_M_TAIL_S; if (now >= stall_m_until && !armed) return;` 一一对应。

### B：`panelProbeThread`

基本块被编译器重排了，序列比较看不清，改用指令多重集比较。差异是寄存器改名（`w27→w26`、`w26→w25`）加下面这些：`blr x8` 多 8 次，`mov w1,#0xdac` 从 12 处到 14 处，`#0x3e8` 从 13 处到 14 处，新字符串 1 条。

函数入口确认了宿主函数的栈槽：`[sp,#0x1f0]` 是 `panelLog`，`[sp,#0x1f8]` 是 `panelProbeSleep`（尾跳 `_IOSleep`），`[sp,#0x210]` 是 `panelProbeQuiet`（`stlr` 写 `_pagani_panel_probe_quiet`）。

**P1，`0fbc94` 起，紧跟 `p1 link` 日志之后：**

```
0fbc94  quiet(0)            0fbcd4  ldp x0,x8,[sp,#0xb0]; mov w1,#4; blr x8      GCC_DISP_AHB_CLK 重读
0fbca4  sleep(0xdac)        0fbce0  tbz w0,#0 -> 0fbdc0
0fbcb4  quiet(1)            0fbce4  and w8,w0,w27; cmp w8,#0x80000000; b.eq -> 0fbdc0
0fbcc4  sleep(0x3e8)        0fbcfc  bl panel_probe_sample_phase
```

`w27` 在第一道门控 `0fba58` 置为 `0x80000002`，到这里没有变。`and`、`cmp` 这一组就是 `panel_probe_gcc_on`。`0fbdc0` 写 `panel: p1 phase skipped gcc %x`，然后走到 `0fbde0` 的 `quiet(0)` 返回。旧内核在 `p1 link` 日志之后直接进 `panel_probe_sample_phase`。

**P3，`0fc6cc` 起，紧跟 `p3 intf te %x fc %x int %x` 日志之后：** `quiet(0)`、`sleep(0xdac)`、`quiet(1)`、`sleep(0x3e8)`，再 `bl panel_probe_p3_open`。旧内核这里只有 `sleep(0x3e8)`。

### C：`gpu meter` 行

源码用 `Row` 链式拼接，没有单一格式字符串，` vmr ` 在二进制里是逐字符写的。

| 位置 | 内容 |
|---|---|
| `_native_adreno_client_tick` | 旧版 `add w8,w9,w8` 相加后存一个字段；新版 `stp w10,w8,[x19,#0x38]`，`[0]` 进 `reserve_objects`，`[1]` 进 `reserve_spans` |
| `018064` 到 `0180ac` | 写 `0x20 0x76 0x6d 0x72 0x20`，即 ` vmr ` |
| `0180c8` | 读 `runClients+0x38`，钳到 99999 |
| `01815c` | 写 `0x2f`，即 `/` |
| `018170` | 读 `runClients+0x3c`，钳到 99999（`#0x869f`、`movk #1`） |
| `018208` 起 | 写 ` pf ` |
| `018244` | 读 `runClients+0x40`，钳到 9999 |

`adreno830_status_clients` 复制到 `0xcc+0x10=0xdc`，后面的读取偏移全部 +4。`runClients` 的全部引用只在 `adreno830_status_meter`、`adreno830_status_clients`、`adreno830_status_publish`。总览页读的字段都在 `0x38` 之前，所以没变是对的。

`工具` 目录（不含运行日志）里没有解析 `vmr` 的代码，新格式不影响它们。

### D：`smmuFaultSink`

```
023e10  cmp w20,w21 ; cset w20,eq             gpu = fault.cb == board->cb_gpu（w20、w21 跨调用保存）
023e18  adrp x21 ; add x21,=g830
023e20  add x8,x21,w20,uxtw #2 ; ldr w8,[x8,#0x2690]
023e28  cmp w8,#7 ; b.hi                      fault_lines[gpu] < 8
...     bl _boot_progress_trace
        add x8,x21,x20,lsl #2 ; ldr w9,[x8,#0x2690] ; add w9,w9,#1 ; str w9,[x8,#0x2690]
```

判断和自增用同一个下标，`[1]` 是 GPU 的 bank，`[0]` 是其他 bank。

## 四、审查改动 A

本节的 `0xc…` 是 `0xfffffff00c…` 的简写。

### 1. `stall_m_until` 的读写者是否只有观察者线程：是

- 扫描全部代码段（`adrp` 配 `add`、`ldr`、`str`），引用只有 `0xc11810c` 的写和 `0xc118120` 的读，都在 `_fps_observe` 里。【二进制】
- 扫描还报了一个 `0xc11a830`，看过上下文，是 `add x1,x26,x17` 重算了 `x1` 而脚本没清基址，属于假命中。
- `_fps_observe` 只有一个调用点，在 `_poll` 的 `0xc113078`。`_poll` 的地址只在 `_boot_progress_start` 取过一次。【二进制】
- `poll_call` 用 `THREAD_CALL_OPTIONS_ONCE` 分配，只从 `boot_progress_start` 和 `poll` 末尾进入。XNU 里 ONCE 的语义是“执行中再次提交，推迟到返回之后”（`thread_call.h:77`、`thread_call.c:714`），所以它不和自己并发。线程可能换，但前后两次之间有 thread call 的锁，不需要原子操作。【读码】
- `stall_touches` 同理，4 处引用全在 `_fps_observe`。

### 2. `frame_clock` 和 `frequency` 的单位：一致

- `frame_clock` 是 `mach_absolute_time`。它走 `_ml_get_timebase`，也就是 `CNTVCT_EL0` 加每 CPU 的基准值，单位是计数器 tick，没有缩放。【二进制】
- `frequency` 是 `mrs CNTFRQ_EL0`，二进制里就是 `0xc117710`。
- 运行日志头写着 `hz=19200000`，所以 10 秒是 192000000 tick。【真机】
- `stall_tick` 也是同样的 `hz` 和 `now` 组合。

### 3. `hz * 10` 是否溢出：不溢出

`hz` 是 `uint64_t`，枚举常量按通常算术转换进 64 位，二进制是 `madd x8,x28,x8,x0`。乘积 192000000，远低于 2^64。`now` 加上它，要约 3 万年后才会溢出。比较用的是无符号的 `b.lo`。

### 4. 启动早期 `stall_second` 的行为：没变

- `stall_m_until` 在 bss，初值 0。没有帧也没有触摸时 `now >= 0` 恒真，门控退化成旧的 `!armed`。
- 第一次有帧或触摸时，`stall_m_until` 大于 `now`，走写 M 的路径，和旧版一样。
- 差别只在最后一次活动之后的 10 秒内。
- 新增的 `frame_clock()` 在观察者线程上下文，`fps_observe` 开头已经调过，没有新的时序依赖。
- `stall_mode` 在 `boot_progress_start` 里、`poll_call` 创建之前设好，第一次观察之前就定了，之后不再变。

### 5. `stall_touched` 是否一定先于 `stall_second`：是

源码里两者在 `fps_observe` 中顺序调用，中间没有 `return`。上面唯一的提前返回是 `boot_progress_trace_enabled()`，它让两者一起跳过。

二进制也核对了：`stall_touched` 内联在 `0xc117018`，所有路径都先经过它再到 `0xc117700`。到 `0xc117700` 只有两条路：

- 从 `0xc1176fc` 顺序下来。
- `0xc117174` 的短路分支。它把 `w9` 置 0，只有 `fps==0` 才走到，语义正确。

`stall_touched` 第一句看 `stall_mode`，`stall_second` 也看，而 `stall_mode` 在第一次观察前已定，不会出现前者跳过、后者照跑而读到旧值的情况。

### 6. 对 console 环和 UFS 写入量的影响：小，不构成 storage 风险

**先修正一个数。** 最后一次有帧的观察记作 P0，新版继续写 P0+1 到 P0+9，共 9 次 36 行，调度抖动时偶尔多一次，这就是“40 行左右”。但 P0+1 时采样器还醒着（`STALL_ARMED_MS` 是 2 s），旧版本来就写。所以相对旧版的净增是 8 次、32 行。host 测试 `stall-glue.c` 的 `s < STALL_M_TAIL_S` 也是这个边界。

| 项 | 数值 | 来源 |
|---|---:|---|
| console 环 | 28668 字节（`0x6ffc`） | 【真机】日志头 |
| 每条记录开销 | 34 字节 | 【读码】 |
| 每次观察四条的典型长度 | 约 513 字节 | 【估算】 |
| 每次观察的上限 | 896 字节，`4×(190+34)` | 【读码】`STALL_LINE=190` |
| 每次活动结束净增 | 4.1 到 7.2 KB，环的 14% 到 25% | 【估算】 |

**M 行从未在真机上出现过。** 保存的所有运行里都没有 `kind` 为 `M` 的记录，所以典型长度只能按格式估。

**用真机 F 记录回放新旧门控：**

| 运行 | 时长 | F 秒 | 活动结束 | M 秒 旧→新 | 净增字节（513 到 896 B/次） | console 总量增幅 |
|---|---:|---:|---:|---|---|---:|
| `stage1-full` | 1203 s | 434 | 15 | 471→687 | 108 到 189 KB | 11% 到 16% |
| `stage1-smoke` | 420 s | 25 | 8 | 40→148 | 54 到 94 KB | 17% 到 29% |

smoke 比例高，是因为它活动少、分散，每次活动都带一条完整的尾巴。这一轮在回放里约有 35% 的时间在写 M。

**UFS：**

- M 进 console 环，不进 events 环，不挤那里的 Q、J、U 和大部分 S 记录。`ramoops_log_line` 没有限流。
- 写线程只在 `sequence` 变化时写。`stage1-full` 是 1181 s、394 轮，已经是每 3 秒一轮的上限，尾巴在这种运行里不增加轮数，只让每轮多一两个变化块。
- 完全静止的阶段，每次活动结束最多多 3 轮。每轮是两三个数据块加一个头块和一次 flush，共约 40 KB。【估算】
- 槽的推进阈值是 24 KB，`stage1-full` 约多 4.5 到 8 个槽。
- 提前落盘阈值是 12 KB，也就是环每 3 秒要写入 12 KB。M 最多 0.9 KB/s，不会触发。
- **最坏情形：** 90 分钟全程都在写 M，按上限算。console 约 1.6 KB/s，每 15 到 16 秒推进一个槽，5400 s 约 350 个槽，小于环形区的 477 个（窗口共 509 个，固定 32 个），不会回绕。【估算】
- 这个最坏情形和旧版“全程都有帧”的最坏情形相同。尾巴没有提高写入速率上限，只增加了处在这个速率的时间比例。

**环的保留时间：** 只有 M 时 console 环能存 32 到 56 秒。尾巴会更早把活动末尾的 F、G、C 行挤出内存环，但 UFS 槽已经留下它们。两张阶段 1.5 镜像都有 `sheng-log=1`。

### 7. `stall-report.py` 对 `second.tick` 为 None 的旧日志：安全

`watched = '%d/%d' % (...) if second.tick else '-/-'`，`None` 和 `0.0` 都走 `-/-`，不抛异常。其余 `sum(second.hungry or [])`、`s.tick or 0.0` 也都防了 `None`。

在草稿目录实际跑了四种日志，都是退出码 0：

| 日志 | IPI 列 |
|---|---|
| `ipi` 行没有 `tick` | `1.0/-/-` |
| 没有 `tick`，但 `idleq`、`stuck` 非 0 | `1.0/-/-`，总览仍计入 |
| `ipi` 行只有 `max` | `1.0/-/-` |
| `tick 0.0` 且 `stuck 1` | `1.0/-/-`，总览仍计入 |

实际上 `None` 没有来源：保存的运行里没有 M 记录，当前格式 `tick` 总是打印，`ipi` 行最长约 171 字符，到不了 190 的截断线。

`tests/test_stall.py` 覆盖了 `tick 0.0`，没覆盖 `None`。

其他消费方：`symbolize-crashes.py` 只用 `Q`，`ramoops_format.py` 只有名字映射，`gpu-loop.py` 没有依赖 M 的逻辑。`second_at`、`join_frames` 对没有 F 的尾巴秒都能处理，这时 `fps` 是 `None`，只在 `--timeline` 里显示成 `fps=None`。旧版采样器醒着而没帧的秒也是这样。

## 五、发现

### R1 minor：尾巴秒的 `free` 被当成最小值

`stall_free_min` 只由采样器更新（`BootProgress.c:1794`）。采样器睡着时 `stall_second` 换出的是 `UINT32_MAX`，`free` 就是观察那一刻的 `vm_page_free_count`。`StallProbe.h` 的注释写明了这一点，但报告 VM 列的说明还是“最小 free”，`README.md` 的 `M io` 也是。

看报告的人会把尾巴秒的数当成这一秒的最小值，内存压力高的时候可能低估。

建议：在 `COLUMNS` 的 VM 说明里加一句，或者像 IPI 列一样在 `tick` 为 0 时标记。这是工具改动，不用重编内核。

### R2 minor：`-/-` 只看 `tick` 的真假

`stall_tick_max` 要两拍才有间隔。采样器刚醒、这一秒只跑了一拍时，`tick` 是 0，但这一拍可能已经数到 `stuck`。`idleq` 要连续两拍，不受影响。

这时表格显示 `-/-`，实际上有人看过而且看到了。上面第 4 种日志就是这个情况。概率很低：要观察恰好落在 10 ms 的窗口里，又正好有一个悬而未决的 IPI。这时总览的合计不受影响，还有 `why=ipi` 的 Q 采样。

建议：`if second.tick or any(second.hungry or []) or any(second.stuck or [])`。这样同时覆盖 `None` 但有数的情形。

### 确认没有问题的项

| 项 | 结论 |
|---|---|
| `StallProbe.h` 注释和常量 | 同步，`STALL_M_TAIL_S = 10`，第 62 到 64 行的说法和行为一致 |
| `stage15-probe.md` 第 82 行 | 已按 A 更新，和二进制一致 |
| 增量审查的 D1 | 这次改动解决了，无信号的卡死 10 秒内都有 M。超过 10 秒仍然没有，这是设计，头文件写明了 |
| host 测试 | `stall-glue`、`stall` 在草稿目录重新编译运行，全部通过（ASan、UBSan） |

## 六、没验证的

- **M 行的真实长度和格式。** 真机上还没出现过，字节数都是估算。阶段 1.5 首轮的 `M` 记录能直接校准第 6 节。
- **尾巴在真机上的效果。** 没上机，门控逻辑只有二进制核对和 host 测试支撑。
- **`aa94decf` 的数据段和 prelink 段。** 没有旧字节，只有符号表和构建命令的间接证据（第一节）。
- **基线身份。** `k4.s` 是 `aa94decf` 靠构建时间推断，没有哈希。
- **B、C、D 的逻辑是否正确。** 本文只确认它们在二进制里且和源码一致，语义审查不在这次任务内。

## 七、实际做了什么

- 从两张阶段 1.5 镜像解出内核，确认都是 `75e75d4e`。
- 反汇编新内核，用同一归一化脚本和 `k4` 逐函数比较，分类全部差异，比较符号布局。
- 写注释反汇编脚本，读了 `_fps_observe`、`panelProbeThread`、`smmuFaultSink`、`adreno830_status_meter`、`_mach_absolute_time`、`_ml_get_timebase` 的相关段。
- 写解释器执行新旧全局构造函数，比较写入的字节。
- 全代码段扫描 `_stall_m_until`、`_stall_touches`、`runClients`、`_poll`、`_fps_observe` 和探针宿主函数的引用。
- `kernel.macho` 和未剥离内核按函数比较字节。
- 读 `BootProgress.c` 的相关段、`StallProbe.h`、`FrameRate.h` 的 `frame_ring_scan`、`UfsLog.c`、`UfsLog.h`、`RamoopsLog.c` 的 `emit`、环大小和 `ramoops_log_line`、`PanelProbe.h`、`stall_format.py`、`stall-report.py`、`Test/stall-glue.c`、`tests/test_stall.py`、XNU 的 `thread_call.h`。
- 用 `stage1-full`、`stage1-smoke` 的 F 记录回放新旧门控。
- 在草稿目录编译运行 `stall-glue`、`stall`，用 `python3 -B` 跑 `stall-report.py` 的四种日志，没有在工具目录留下 `__pycache__`。

**一处偏离只读的地方：** 我对 `Adreno830` 和 `UniXNU-Pagani-Drivers` 跑过一次不带 `--no-optional-locks` 的 `git status`。两边的 `.git/index` 没有被改写（修改时间仍是 10-29），但 `Adreno830/.git` 目录的修改时间变成了 08:17，应该是临时锁文件造成的。工作树和仓库内容没有变化。
