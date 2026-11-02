按规格实现完成，主机测试全部通过，`PaganiPanel.cpp` 单文件内核编译通过。没有上机，没有运行 adb、fastboot，没有提交，也没有改 `UniXNU/out-gpu`。没有链接内核，也没有出镜像。

## 改动文件

| 文件 | 改动 |
|---|---|
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/PanelProbe.h` | 新文件。P0 到 P3、各门闩、面包屑、A0，不含内核头 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/Test/panel-probe.cpp` | 新测试 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/PanelBrightness.h` | 4 个新位、`pagani_panel_probe_stages`、`pagani_panel_gcc_ahb`、`pagani_panel_dcs_gate` |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/PaganiPanel.cpp` | 探针线程、A0、门闩接线、`sendPacket` 拦截、早期日志在第一次报告时重打 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/Test/panel-brightness.cpp` | 门闩与新位测试 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/README.md` | 位表、Start 一节、探针一节 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiInput/Mapping.h` | `map` 加可选参数，默认行为不变 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/scripts/test-peripherals.py` | 并入 `panel-probe`，新增 `start` 顺序、只读映射等文本断言 |

`PeripheralIO.h` 没有动，231-232 行的挂死警告还在。

## 测试结果

- **`test-peripherals.py --tree ...`**：退出码 0，5 项全部 PASS。
  - PaganiPanel 亮度：125007 项检查。
  - 电源序列：314 项检查。
  - 探针：39471561 项检查。
- **探针主路径（模型内）**：
  - 4962 组 DSI，间隔不小于 20 µs。
  - 相位 64 组，日志时长 15751 µs。
  - 单行最坏 101 字节，上限 120。
  - P3 在 350.5 s 结束，总定时是 420 s。
- **变异测试**：45 个变异全部被发现，44 个靠运行失败，1 个靠 `static_assert`。第一轮漏了 4 个，已补测试。
- **单文件内核编译**：
  - `PaganiPanel.cpp` 用构建记录加 `-Wall -Wextra -Werror` 编译无诊断。
  - 比驱动脚本多加了 `-Wno-unnecessary-virtual-specifier`，因为 XNU 自己的 `OSSymbol.h` 在当前编译器下触发这条警告。
  - 无 FP/SIMD。计数器确认是 `mrs x0, CNTPCT_EL0`。
  - `_kernel_thread_start`、`_thread_deallocate`、`_vsnprintf`、`_strlcpy`、`_bzero`、`_IOSleep` 在已有内核符号表里都有。
- **`PaganiInput.cpp` 同样能编过**：有 3 条既有警告，这次编译未加 `-Werror`，也没有逐条核对。
- **只读映射**：`kIOMapReadOnly` 只读了 XNU 源码路径，没有实测。
- **目标文件**：`/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/panel-probe/obj/PaganiPanel.o`

## 各位组合实际发生的读写

以下寄存器读数都是模型或读码结果，不是真机日志。门闩均在该块的 3.5 s 睡眠之后读取。

| 值 | 读 | 写 |
|---|---|---|
| `=65` | 同现状：启动 14 个 DSI 偏移，`0x4` 一次（返回 -19），`no dcs` 再 14 个，之后 report 按现有节奏读 | 0。不起线程，日志行不变 |
| `=193`（加 bit 7） | `=65` 全部，加 P0、P1 | 0 |
| `=1217`（再加 bit 10） | 再加 P2、P3 | 0 |
| bit 10 不带 bit 7（如 `=1089`） | 探针 0 次寄存器读，只打 `p2 skipped no p1` 和 `p3 skipped no full phase` | 0 |
| `=2113`（`1+64+2048`，A0） | `0x127004`，bit 0 为 0 时轮询最多 1000 次，再读 DSI `0x0`；然后 14 个偏移，不走首条 DCS，所以少一次 `0x4` | 最多 1 次：`0x127004 = 读数|1`。bit 0 已是 1 时不写，也不读 DSI |
| `=6209`（再加 bit 12） | 同上 | 同上；三项形状全过才走首条 DCS，这是第一次写 DSI，序列见 `PeripheralIO.h:194-212`。不过则按 -19 |
| bit 12 不带 bit 11（`=4161`） | 同 `=65` | 0 |
| A0 加 bit 1、不带 bit 12 | 同 `=2113`，tick 在 `sendPacket` 返回 -19，不碰 DSI，600 次后停 | 只有 A0 那 1 次 |

各段明细：

- **P0**：
  - `0x127004` 读 1025 次。
  - `0x127008` 读 1 次。
  - TE `0xf156004` 读 1024 次，再加每圈一次。
  - DSI `0x0` 读 1024 次，再加约 5000 组 `0x0,4,8`。
- **P1**：
  - GCC 门闩 1 次。
  - dispcc 25 个寄存器各 1 次。
  - 64 组 `80b0、8010、8034、8004、9004` 加 DSI `0x0`。
- **P2**：
  - 每块先读门闩，即 GCC 加 dispcc 三个寄存器。
  - 块内依次是 RSCC 1 个、wrapper 3 个、SCC 4 个、CRMC 5 个、PHY 4 个。
- **P3**：
  - 门闩、64 组相位、INTF `+0,+0xac,+0x298`。
  - 隔 1 s 后再读门闩和 64 组相位，然后读 `+0xac,+0x298,+0x2b4`。
- **全程不读**：MDSS 顶层、DSI1、CTL、SSPP、SMMU。测试里写死了地址白名单。

## 需要你知道的实现决定

1. **`=65` 有一处理论差异，也改变了原有的强制发送**。规格第 6、7 条在这里有冲突，我选了让门闩无条件生效。真机上 ver 一直是 0，这时门闩不介入，读写和日志与现在相同。只有 ver 非 0 且形状不符时，才从尝试发送改为 -19，包括 Build 50 那种 `ctrl` bit 0 为 0、原本会强制发送的情况。
2. **“64 次全开”按五项算**：ahb、mdp、byte、pclk、GDSC。byte 或 pclk 若在帧间隙显示 CLK_OFF 且无硬件门控，P3 会被跳过。这是偏保守的选择，日志会写清楚是哪项。
3. **比规格更严的地方**：
   - dispcc 的“在跑”还要求 CLK_OFF 为 0，或处于硬件门控。
   - wrapper 同时要 rscc 和 GDSC。
   - P3 每次读 INTF 之前都重新跑一轮 64 组相位。
4. **日志与 plan 的差异**：
   - `p2 rsc/wrap/scc` 拆成三行，挂死时前面的值不丢。
   - P3 多一条中间行。
   - P1 phase 行多了 `gdsc` 和 `us`。
5. **A0 的处理**：
   - 用一个可写窗口，用完即释放，这是“全部只读映射”的唯一例外。
   - `start` 里不睡 3.5 s。
   - 开机这一刻的 `a0` 行和 `dcs held` 行可能被 GPU 追踪冲出环形日志，所以在 46 s 第一次报告时重打一次。
6. **没做的**：plan 里的 E 段，因为规格位表不含它，而且 `BootProgress` 不归我改。
7. **文档**：README 新写的探针说明和门闩说明都标明未上机。