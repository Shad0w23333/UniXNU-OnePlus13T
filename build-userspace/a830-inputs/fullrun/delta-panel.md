# 增量审查：面板探针调序、DSI 门闩与 UFS 写线程暂停

审查对象：内核 `aa94decf`（`kernel.macho` 的 sha256 与 `测试镜像/boot-A830-阶段15-首测.payload.json` 的 `kernel_sha256` 一致），源码 `PanelProbe.h`、`PaganiPanel.cpp`、`UfsLog.c`、`UfsLog.h`。全程只读，没有运行 adb、fastboot，没有碰手机。

## 结论

- **没有发现 crash、hang、storage、wrong-data 级别的问题。** 这批改动在源码和反汇编上都与设计一致。
- 有 **1 条 minor**：同一块里已经读出的值，要等下一次 `quiet(0)` 才能上 UFS（发现 1）。只在 P1 相位或 P3 第二次采样挂死时才有影响，不值得为它单独重编。
- 五个核对问题的结果：

| 问题 | 结果 |
|---|---|
| 每次读都在写线程已停的时段里吗 | 是。最后一条写命令到读的余量约 480 ms，要 tick 迟到加两轮耗时达到约 500 ms 才会贴上 |
| 暂停影响静默、panic、定时复位、看门狗、导出吗 | 复位和 panic 路径完全不看这个字，最后快照照常写。导出只多一种可重试的 -9 |
| 弱定义是同一个地址吗 | 是。`_pagani_panel_probe_quiet` 只有一个定义，地址 `0xfffffff00c2193ac`，全内核 3 处引用 |
| 时间线相容吗 | 相容。脚本最晚约 261 s 结束（`at` 进位时约 262 s），P0 面包屑 266.0 s，首读 270.5 s，P3 结束约 351.5 s，离 420 s 还有 68 s |
| 两个零点差多少 | 脚本的等效零点约 0.55–0.60 s，探针约 1.50 s，差约 0.9 s，上界 1 s 左右 |

---

## 一、每次读是否都落在停写时段

### 1. 写线程这一侧

`ufs_log_writer_tick`（`UfsLog.c:546-570`），`pagani-ufslog` 线程，优先级 `BASEPRI_DEFAULT`，不持锁，每拍 `IOSleep(500)`（启动后 30 s 内 100 ms，复位前 2 s 内 250 ms）。

设 `quiet(1)` 的时刻为 T。走四种相位：

| 相位 | 写线程行为 | 最后一条写命令结束 |
|---|---|---|
| T 之前一拍已读到 held=0，且一轮在途 | 这轮不中止，写完后 `w.held=0`，睡 500 ms；下一拍 `held && !w.held`，补一轮（上升沿轮） | T + R1 + 500 + L + R2 |
| T 刚过，下一拍在 500 ms 后 | 同上，只有上升沿轮 | T + 500 + L + R2 |
| 这一拍 `fresh` 为 0 | 不写，`w.held=1` | 更早，且盘上已是最新 |
| 上升沿轮之后 | `due = !w.held = 0`，不再写，`w.held` 保持 1 | 不再有写 |

读在 T + 1000 ms。余量是 `500 − R1 − R2 − L`。

- **真机数据**：
  - tick 迟到：`stage1-full` 30 轮用时 90.23 s，每拍约 501.3 ms。
  - 一轮耗时：常态小于 1 ms，偶见 2.5 ms，历史最大 7.1 ms（启动期）。
  - 单条命令：最大 4.8 ms。
  - 所以余量约 480 ms。
- **一轮的结尾**：`writer_round` 以 `timed_write(0,1,…) && timed_sync()` 结束，上升沿轮的 flush 也在余量之内。
- **下降沿之后**：`quiet(0)` 后第一拍回到普通判定 `now >= next_round || …`。上升沿轮把 `next_round` 推到 T+δ+3 s，所以下一块的面包屑最晚约 3 s 后上盘，仍在 3.5 s 之内。万一赶不上，下一个上升沿轮也会把它带走。
- **面包屑的极小窗口**：`quiet(0)` 和写日志行之间隔几微秒。如果那一拍正好落在中间，这一轮带不走面包屑，3 s 后的下一轮或上升沿轮带走。
- **模型**：把 tick 逻辑和探针顺序各写成模型，随机 4000 组相位，见 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/delta/sim.py`。结果：

| tick 平均迟到 | 一轮耗时 | 读与最后一轮结束的最小间隔 | 读落在轮内 | 读前面包屑未上盘 |
|---|---|---|---|---|
| 1.3 ms | ≤1.5 ms | 494.5 ms | 0 | 0 |
| 5 ms | ≤7 ms | 459.9 ms | 0 | 0 |
| 20 ms | ≤7 ms | 321.0 ms | 0 | 0 |
| 100 ms（极端） | ≤7 ms | 0.9 ms | 0 | 0 |

### 2. 探针这一侧，逐个读核对

`panel_probe_enter` 的顺序：`quiet(0)`、写行、睡 3500、`quiet(1)`、睡 1000。

| 阶段 | enter | 读 | 是否在 quiet=1 内 |
|---|---|---|---|
| P0 | GCC、TE、DSI 都是新块，各走一遍 | 各块延迟读、GCC 两值、100 ms 采样（DSI 加 TE） | 是 |
| P1 | GCC、DSI 已入过，直接返回，不动 quiet；DISP 是新块，走一遍 | 全部在 DISP 的 enter 之后，包括 GCC 门闩和相位里的 DSI | 是 |
| P2 | 五块都是新块 | 每块 `read_gates` 在该块 enter 之后 | 是 |
| P3 | INTF 是新块 | 两次 `p3_open` 加四个寄存器，中间 `sleep 1000` 期间 quiet 不落 | 是 |

- **跳过路径**：P1 门闩不开、P2 无 P1、P3 无相位，都不读后面的块，`panel_probe_run` 在阶段末补 `quiet(0)`。
- **反汇编**：`panelProbeThread` 的序列是 `quiet 0 → 日志 → sleep 0xdac → quiet 1 → sleep 0x3e8`，共 10 块。阶段结束的 `quiet 0` 在 `0xfffffff00c0fbcf8`（P0）、`0xfffffff00c0fbad8`（P1，含跳过分支）、`0xfffffff00c0fc49c`（P2）、`0xfffffff00c0fc674`（P3）。常量 `0x145959c00 = 284500 × 19200`、`0x18a3fbc00 = 344500 × 19200`，确认是新时刻。
- **DSI 门闩**：`panel_probe_sample_phase` 里 `group` 已提前，`version` 只在 `panel_probe_gdsc_on(cfg, group)` 时读。全零组不读 DSI，`dsi nz` 与 `off gdsc` 合起来能算出读了几次。
- **不属于探针线程的读**：`PaganiPanel::report` 的 `logState` 读 DSI，不在暂停内，但它不与探针重叠。见第四节。
- **内存序**：`stlr` 保证前面的设备读完成后 `quiet(0)` 才可见，写线程不会抢在读之前恢复。

### 3. 线上还有没有别的写者

`hostWrite4k` 有三个调用者：`native_ufs_log_write`、`native_ufs_export_write`、`native_ufs_store_write`。最后一个要 `sheng-data-persist`，首测命令行没有。panic 写走 `native_ufs_log_panic_write`，只在复位或 panic 路径上。所以停住写线程和导出，线上就没有写。

---

## 二、暂停对各路径的影响

| 路径 | 是否看 quiet | 走读结果 |
|---|---|---|
| `ufs_log_quiesce` | 否 | `reset_coming=1`，停喂狗，`last_flush` 认领传输，写最后快照。真机已验证：`stage1-smoke` 有 `ufslog last snapshot before the reset: timer`，头状态 6 |
| `ufs_log_panic` | 否 | 走 `last_flush`，头状态 5。崩溃那轮验证过 |
| `w.panic_on` | 否 | 看门狗和定时复位都已武装（0.134 s），`ufslog open` 行没有 `no last snapshot` |
| `UFS_LOG_FINAL_MS` | 间接 | `held` 为 1 时 `due` 不看 `last_seconds`。正常流程 351.5 s 已结束，不重叠。挂死时最后 2 s 没有提前轮，靠最后快照 |
| `boot_log_export_put` | 是 | 只在 256 KiB 暂存满或收尾时判断，`held` 为真返回 -9，`export_generation=0`，不写。调用方拿到 `kIOReturnBadArgument`，这一份从头再来（`UfsLog.h:47`）。看过的三轮 `export_bytes` 都是 0 |
| 统计行 | 间接 | `key presses` 含暂停次数，全跑完是 10 次（3+1+5+1）。每 30 轮才一条，最后一条约在 350 s 前后，复位前通常只数到 P2，也就是 9。没有 Python 工具解析这一项 |
| 环 | 间接 | 暂停最长约 2.03 s（P3）。按预检的 9 KB/s 上限也只有 18 KB，小于 32 KiB 的事件环。暂停期间不触发提前轮，放开后 `events >= burst` 会补 |

### 探针读挂死后（quiet 一直是 1）

写线程不再写，`w.held=1`，`due=0`。其余按挂死位置分：

| 挂死位置 | 之后发生什么 | UFS 上留下什么 |
|---|---|---|
| 非启动 CPU | 第一定时器在 CPU 0，420 s 正常触发 `reset_now("timer")`，静默、最后快照、主动咬狗 | 状态 6，最后快照含事件环。240–420 s 真机约 0.2 KB/s，150 s 约 27 KB，环基本装得下 |
| 启动 CPU | 心跳停，3 s 后扣留喂狗，最后一次喂狗后 30 s 咬合。P3 最晚约 381 s，早于 420 s | 状态 2，最后一行是该块的面包屑 |
| 整条总线卡住 | 代码跑不动。2026-10-30 12:15 的记录是约 10 s 后冷复位 | 同上。线上没有写，这正是改动的目的 |
| 同步外部中止 | panic，走 `ufs_log_panic`，20 s 后复位 | 状态 5 |

都不依赖 quiet 放开。`last_flush` 有上界：认领最多 1 s，全程最多 2 s，超时照常复位。

---

## 三、弱定义和名字修饰

| 项 | 结果 |
|---|---|
| 定义 | `llvm-nm -m`：`fffffff00c2193ac (__DATA,__data) weak external _pagani_panel_probe_quiet`，只有一个 |
| 修饰 | C++ 一侧写了 `extern "C"`，符号就是 `_pagani_panel_probe_quiet`。全局变量本来也不修饰 |
| 写 | `__ZL15panelProbeQuietPvj`：`0xfffffff00c0fc808 stlr w1, [x8]`，x8 指向该地址 |
| 读 | `_ufs_log_writer_tick`：`0xfffffff00c13e3ac ldar`；`_boot_log_export_put`：`0xfffffff00c13f56c ldar` |
| 全内核引用 | 扫描 `__TEXT_EXEC` 的 adrp 配对，只有上面 3 处 |
| 段 | `__DATA,__data` 可写。相邻的 `pagani_screen_state`、`native_ufs_*` 缓存都在运行时被写过 |
| 回调接线 | `host.quiet` 指向 `0xfffffff00c0fc800`（`panelProbeThread` 前段），调用时第二个参数是 0 或 1 |

---

## 四、时间线

### 两个零点

| 量 | 证据 | 数值 |
|---|---|---|
| 日志零点的 mach 时间 | 日志头 `counter=` | 61.40–61.46 s |
| 按键驱动起来 | `physical buttons started` | 1.09 s |
| 面板 `start` 末尾 | `panel: report` 在 46.50 s，减 45 s | 约 1.50 s，`startProbe` 紧随其后 |
| 触摸 `scriptArm` | `Stage7Probe.cpp` 按键、触摸、面板顺序执行；具体行落在环的缺口里 | 1.09–1.50 s 之间 |
| 脚本 `at` | `at += nowMs()/1000` 截断到整秒：240 + floor(62.5–62.96) = 302 | 日志时间 240.54–240.60 s |

所以脚本等效零点约 0.55–0.60 s，探针线程零点约 1.50 s，**差约 0.9 s**。

如果启动慢到 `零点 + 触摸时刻 ≥ 63`，`at` 变 303，脚本约 241.5 s 开始，差值缩到接近 0。这个方向只让脚本更晚，最坏情况下余量少 1 s。

### 脚本长度

按 `InputScript.h` 累加：屏幕已亮 17.27 s，屏幕灭且等满 3 s 为 20.27 s，加 8 ms 定时器粒度约 0.1 s。结束于约 258–261 s（`at` 进位时再加 1 s，约 262 s，与 `PanelProbe.h` 文件头写的一致）。

`kbt` 汇总行在事件后 1 到 4 s 内刷新（`PAGANI_KBT_FLUSH_MS`，探针补丁改短后是 1 s），不会拖到探针之后。

### 合并时间线（探针按 1.50 s 起算）

| 日志时间 | 事件 |
|---|---|
| 46.5 s | `panel: report`，读 DSI 寄存器（`logged` 置位） |
| 240.6 s | 脚本开始 |
| 约 258–261 s | 脚本结束 |
| 266.0 s | `panel: p0 gcc next` |
| 269.5 / 270.5 s | 暂停 / GCC 首读 |
| 275.0 / 279.5 s | TLMM 首读 / DSI 首读 |
| 约 279.6 s | P0 结束，放开写线程 |
| 286.0 / 290.5 s | P1 面包屑 / dispcc 首读 |
| 310.5、315.0、319.5、324.0、328.5 s | P2 五块首读 |
| 346.0 / 350.5 / 351.5 s | P3 面包屑 / 第一次 / 第二次采样 |
| 约 351.55 s | `panel: probe end` |
| 405 s | GPU 降频 |
| 420 s | 定时复位 |

- 脚本结束到 P0 面包屑至少约 4 s，到首读至少约 8.5 s，到第一次暂停前还有普通轮把脚本数据全部写走。挂死也丢不了脚本数据。
- 阶段之间不重叠：P0 结束到 P1 面包屑 6.4 s，P1 到 P2 15.5 s，P2 到 P3 17.5 s。
- 全程暂停合计约 11 s。

### reporter

`pagani_panel_report_due` 在 `registers = display_on && (!logged || (stopped && quiet))` 时读 DSI。真机两次都在 46.5 s 和 646.5 s，中间 `seen` 不变，不出行。646.5 s 晚于 420 s，所以首测探针窗口里 reporter 不读寄存器。

只有 46.5 s 时屏幕是灭的，`logged` 才不置位，第一次读才会后移到亮屏后的整分钟。真机这一刻 `power 1/1`。

---

## 五、发现

### 发现 1（minor）：同一块里已读出的值，要等下一次 `quiet(0)` 才上 UFS

- **位置**：`PanelProbe.h:296`（P1 相位）、`PanelProbe.h:386`（P3 两次采样之间），说明在 `PanelProbe.h:8-9`、`README.md` 的 “Values go out as soon as they are read”。
- **路径**：
  1. P1 的 `enter(DISP)` 后 quiet=1。
  2. 四组寄存器读完，四行写进 ramoops，这时 `due=0`，不会有新一轮。
  3. 接着 `panel_probe_sample_phase` 读 64 组。
  4. 如果这里挂死，quiet 不再放开，这四行不会进入普通轮。
  5. P3 同理：第一行 `panel: p3 intf te …` 写完后睡 1 s，quiet 不落，第二次采样挂死时第一行也上不了盘。
- **影响**：
  - 非启动 CPU 挂死：420 s 的最后快照能带走，没有损失。
  - 启动 CPU 挂死：只剩 ramoops，咬合复位通常保得住。
  - 单 CPU 挂死后固件很快冷复位（12:15 那种，约 10 s）：UFS 上只有面包屑。改动前写线程还会在 3 s 内把这些行写出去，这是这批改动带来的窄小退步。
- **概率**：要 P0 的 DSI 读和 P1 前半都过了，相位才挂，读的又是刚读过的寄存器。P1 的四组读本身只有几毫秒，改动前也赶不上一轮。
- **修法**（可选，下次重编时顺带）：在 `panel_probe_sample_phase` 之前、P3 两次采样之间，各走一遍 `quiet(0)`、写行、睡 3.5 s、`quiet(1)`、睡 1 s。P1 多 4.5 s，P3 的间隔从 1 s 变成 4.5 s，不影响时间线。**不建议为此单独重编和再请用户同意一次。**

---

## 六、未验证

- **写线程的最坏调度延迟**：只有平均值（每拍约 501.3 ms）。优先级 31，没有 `L` 的最大值。只有 `R1 + R2 + L` 接近 500 ms 且同时读挂死，才会影响存储。
- **挂死对其他 CPU 的影响**：TLBI 同步、交叉调用是否连带卡住是闭源硬件行为。12:15 的记录更像整机卡住。
- **`gHost.lock` 持有者被探针线程抢占后 CPU 挂死**：认领会等 1 s 后失败，这一轮没有最后快照，复位照常。概率未知。
- **触摸启动的准确时刻**：相关行落在环的缺口里，只有 1.09–1.50 s 的上下界。
- **P3 的 `display_on`**：`stage1-full` 无输入时 646.5 s 仍是 `power 1/1`，但脚本解锁后是否触发自动锁屏没有真机数据。锁屏了 P3 会打 `display 0` 跳过。这一项时刻没变，只是提醒。
- **看门狗半程 bark 在挂死时的表现**：没有这种组合的真机记录。
- **UFS 设备内部行为**：flush 完成 1 s 后复位是否完全无风险，是固件行为。
- 探针的所有 MMIO 读仍然没有上过机。

---

## 七、上机时顺带看

| 看什么 | 期望 |
|---|---|
| `input script: done` | 不晚于约 262 s，且不是 `abort` |
| `panel: p0 gcc next` | 约 266 s |
| `ufslog rounds … key presses N` | 最后一条统计行通常 N=9；P1 门闩不开时 N=4（DISP 的暂停在门闩之前已发生）；探针没走到时 N=0 |
| 头 `state` | 正常 6。state 2 且最后一行是 `… next`，说明这一块挂死在启动 CPU 或整机 |
| `longest round` | 仍是毫秒级。若到几十毫秒以上，回头看余量 |

## 顺带

`测试清单.md:9-10` 写的镜像 sha 前缀是 `a51b866c`、`ea7c6214`，当前文件是 `4304e64c`、`a018b6ba`。这不是代码问题，但给用户之前要更新。
