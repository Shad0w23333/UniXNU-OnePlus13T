审查 7 项全部成立，都已修掉。第 2 项和第 6 项的改法替你做了决定，都是偏保守的选择，见下方说明。全程没有上机，没有运行 adb 或 fastboot，没有提交，没有改 `UniXNU/out-gpu`，也没有链接内核或出镜像。下文的寄存器行为都来自模型或读码，不是真机结果。

## 改动文件

都在 `/Users/xiaochoumao/unixnu-13t/` 下：

- `UniXNU-Pagani-Drivers/drivers/PaganiPanel/PanelProbe.h`
- `UniXNU-Pagani-Drivers/drivers/PaganiPanel/PanelBrightness.h`
- `UniXNU-Pagani-Drivers/drivers/PaganiPanel/PaganiPanel.cpp`
- `UniXNU-Pagani-Drivers/drivers/PaganiPanel/README.md`
- `UniXNU-Pagani-Drivers/drivers/PaganiPanel/Test/panel-probe.cpp`
- `UniXNU-Pagani-Drivers/drivers/PaganiPanel/Test/panel-brightness.cpp`
- `UniXNU-Pagani_SM8750-gpu/scripts/test-peripherals.py`

`PeripheralIO.h` 和 `Mapping.h` 这一轮都没动，231-232 行的挂死警告还在。

## 处理结果

| # | 问题 | 核实 | 处理 |
|---|---|---|---|
| 1 | 相位采样被抢占后挤成一团 | 成立，已复现 | 下一组从本组实际时刻算起。新增最大间隔 `gap`，超过 8333 µs（120 Hz 一帧）判不全开。修后 14 ms 抢占时 `us=29502`、判不全开，8 ms 时 `us=23502`，所有间隔不再挤压 |
| 2 | 门闩只算一次，后续发送没检查 | 成立，已复现（第一次 tick 写 15 次 DSI） | 带 bit 7、10、11 的启动，版本 0 也算形状不符，整轮扣住，发送时不读也不写 DSI。`=1219` 复现写入归零。`=67` 不变 |
| 3 | 映射或起线程失败时日志留不下 | 成立 | 映射失败等到 P0 时刻再打。起线程失败进 `earlyLines`，第一次报告重打。`a0 map failed` 有同样的毛病，一并修了 |
| 4 | 比规格严，可能白跑 P3 | 成立 | MDP、byte、pclk 放宽到规格：enable 位加同组非 0。AHB、rscc 保留 CLK_OFF 检查（比规格严），因为它们承载寄存器读。新增 `panel: p1 clk_off` 记录次数，事后可判读 |
| 5 | 接线只有文本断言 | 成立，4 个变异全部漏过 | 新增 `pagani_panel_start_dcs`、`pagani_panel_send_packet`，由主机测试实跑。`dcsHeld` 改为 `run.held`。其余接线逐句写死断言 |
| 6 | `=65` 有差异 | 成立 | 门闩只对 bit 7、10、11 生效 |
| 7 | README 两处不准 | 成立 | README 已改。“tick 不碰 DSI”只对写成立，下表已更正 |

**两个替你做的决定：**

- **第 6 项：**
  - 无条件门闩实际废掉了 Build 50 的强制发送路径，也改变了 bit 2 的语义，所以按规格第 7 条的“必须完全一样”处理。
  - 代价是：不带新位的值，万一版本非 0 且形状不符，仍走旧路径。13T 现在读 0，不受影响。
  - 想改回无条件，只需删 `pagani_panel_dcs_gate` 第一行，但相关测试要同步改。
- **第 2 项：** 审查原文把这项留给你决定，我选了不改 `=67`、只收紧带探针位的值。

## 测试数字

- **`test-peripherals.py --tree ...`**：退出码 0，5 项全部 PASS。
  - 亮度：126171 项检查。
  - 电源序列：314 项检查。
  - 探针：45744756 项检查。
- **探针主路径（模型内）**：
  - 4962 组 DSI。
  - 单行最坏 116 字节，上限 120。
  - P3 在 350.5 s 结束。
- **`=65` 差分**：64 种旧位组合 × 有无 bit 12 × 8 种控制器状态。门闩路径与原 `pagani_panel_first_dcs` 的返回值、模式、写入序列、强制次数逐项相同。
- **逻辑变异**：共 67 个，65 个运行失败，1 个编译失败，1 个存活。
  - 存活的是等价变异：enable 位为 1 时同组必然非 0。这说明“同组不全为 0”这个条件在代码里是冗余的，不是漏洞。
  - 第一轮还漏了 RSCC 的 CLK_OFF，已补用例。
- **接线变异**：共 15 个，全部被发现，审查指出的 4 个都在内。
- **单文件内核编译**：`PaganiPanel.cpp` 退出码 0。
  - 仍比驱动脚本多加 `-Wno-unnecessary-virtual-specifier`。
  - 审查提醒的 `build-stage7-driver.py` 原样标志编译失败，我没有处理，出包时留意。

## 推荐 `A830_PANEL`

| 场景 | 值 | 命令行字节 |
|---|---|---|
| 首测（带定时复位） | `1217` | 999 |
| 完整包，首测未跑前 | `65` | 998 |
| 完整包，首测 P0、P1 跑通后 | `193` | 999 |
| 首测显示 GCC 位为 0 时，下一轮首测 | `3265` 或 `2113` | 999 |

`1219` 现在也不写 DSI，但 CA 卡死的风险没变，所以首测不带 bit 1。`=67` 按审查建议单独先测。完整包不带 bit 10。

## 各位组合的读写清单

| 值 | 读 | 写 |
|---|---|---|
| `=65` | 启动 14 个 DSI 偏移，`0x4` 一次（-19），`no dcs` 14 个，之后 report 按现有节奏读。不起线程，日志不变 | 0 |
| `=67` | 同 `=65`，发布后 tick 走原发送路径 | 版本 0 时 tick 若读到 ctrl bit 0 仍会写 DSI，这是现有行为 |
| `=4161` | 同 `=65` | 0 |
| `=193`（版本 0） | 14 + 14 + report，少一次 `0x4`，多 `dcs held shape`（46 s 重打），加 P0、P1 | 0 |
| `=1217`（版本 0） | `=193` 全部，加 P2、P3 | 0 |
| `=1089` | 启动同 `=193`，探针 0 次读，只打 `p2 skipped no p1`、`p3 skipped no full phase` | 0 |
| `=195`、`=1219`（版本 0） | 同 `=193`、`=1217`，发布后 tick 不碰 DSI，600 次后停。`logState("retry")`、`logState("failed")` 各读 14 个 | 0 |
| `=2113` | `0x127004`，bit 0 为 0 时轮询最多 1000 次，再读 DSI `0x0`；14 + 14 + report；`dcs held a0` | 最多 1 次：`0x127004 = 读数|1`。bit 0 已是 1 时不写，也不读 DSI |
| `=2115` | 同 `=2113`，tick 不写 DSI，`retry`、`failed` 仍读 | 同 `=2113` |
| `=3265` | `=2113` 全部，加 P0 到 P3 | 同 `=2113` |
| `=6209` | 同 `=2113`，形状不符则 `dcs held shape` | 同 `=2113`。三项形状全过才走首条 DCS，这是第一次写 DSI，序列见 `PeripheralIO.h:194-212` |

版本读成 `0x2009xxxx` 且形状全过时，`=193`、`=1217` 不被扣住，首条 DCS 照常走，与 `=65` 相同。这是规格第 6 条的放行条件。

**各段读取明细：**

- **P0：**
  - `0x127004` 读 1025 次，`0x127008` 读 1 次。
  - TE `0xf156004` 读 1024 次，再加每圈一次。
  - DSI `0x0` 读 1024 次，再加约 5000 组 `0x0,4,8`。
- **P1：** GCC 门闩 1 次，dispcc 25 个寄存器各 1 次，64 组 `80b0、8010、8034、8004、9004` 加 DSI `0x0`（2026-11-01 起，DSI 只在同一组读到 GDSC 开时才读；P0 到 P3 整体挪到键盘脚本之后，从 266 s 开始，读之前写线程暂停，`PanelProbe.h`）。
- **P2：** 每块先读门闩（GCC 加 dispcc 三个寄存器），块内依次是 RSCC 1 个、wrapper 3 个、SCC 4 个、CRMC 5 个、PHY 4 个。
- **P3：** 门闩加 64 组相位，读 INTF `+0,+0xac,+0x298`；隔 1 s 再过一遍门闩和相位，读 `+0xac,+0x298,+0x2b4`。
- **全程不读：** MDSS 顶层、DSI1、CTL、SSPP、SMMU。

## 判读表

| 现象 | 结论 | 下一步 |
|---|---|---|
| `p0 gcc ahb` bit 0 为 0，或 `p1 skipped gcc` | `GCC_DISP_AHB_CLK` 没开 | 下一轮带 A0 |
| `a0 gcc` 之后 `ver` 为 `0x2009xxxx` | A0 就是修复 | 加 bit 12 单独验证首条 DCS |
| A0 后 `ver` 仍为 0 | GCC 这位不是根因 | 看 P1 |
| P0 `nz>0` | 帧相关 | A2 |
| `p0 lat` 中 DSI 明显高于 GCC | 疑似陷入 EL2 | 按访问控制方向查 |
| dispcc 的 `ahb` 使能为 0，或 CLK_OFF=1 且非硬件门控 | 接口时钟断 | A1，只在首测验证 |
| dispcc 全 0，GCC 正常 | 整段配置空间读 0 | B |
| 全部开，PHY 非 0，DSI 为 0 | 控制器专属，疑似访问控制 | B |
| 全部开，PHY、INTF 都是 0 | MDSS 整段读 0，CESTA 嫌疑 | B，再做固件对照 |
| INTF `fc` 增长，只有 DSI 为 0 | 子块问题 | B |
| `clk_off` 接近 64 但 INTF 正常 | 帧时钟门控属正常，放宽正确 | 无 |
| `gap` 超过 8333 或 `p3 skipped ... gap` | 线程被抢占，这次 P3 没跑 | 重测，不算证据 |
| 挂死或中止 | 面包屑定位分界 | 回 `=65`，走 B |

## 未验证项

- `kIOMapReadOnly` 只读过 XNU 源码路径，真机没跑过。
- 探针线程没上过机，`kernel_thread_start` 和 `CNTPCT` 节奏也都没上过机。
- P1 到 P3 的读取是否挂死未知。门闩能否防挂，主机测试回答不了。
- 8333 µs 的上限假定 120 Hz。若实际更低，只会更保守。
- MDP、byte、pclk 在帧间隙是否真的读到 CLK_OFF=1，只来自 `hw.md` 读码。
- AHB 的 CLK_OFF=1 是否真的危险没有证据，保留严格判定只是保守选择。
- 探针窗口的 UFS 落盘余量很小，3.5 s 这个数未在真机验证。
- A0 这一位写入后的实际效果未知。

## 临时文件

都在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/panel-fix/` 下：

- 修改前的备份
- 变异脚本 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/panel-fix/mutate2.py`
- 目标文件 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/panel-fix/obj/PaganiPanel.o`

我想顺手删掉复现二进制，被安全检查拦下，这些文件留在原处，要清理请你自己删。