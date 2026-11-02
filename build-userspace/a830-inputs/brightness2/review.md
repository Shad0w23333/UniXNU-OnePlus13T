# 亮度修复方案审查

全程只读，没有运行 adb 或 fastboot，没有改动任何文件，也没有新建临时文件。

## 0. 结论

- 方案的主体结构可用：先探针，A 看探针结果，B 兜底。大部分地址和数值我逐条核对过，是对的。
- 有 4 处必须改，否则可能挂死、冻屏或开不了桌面：
  1. P3 重读了 12:15 挂死快照的头几个寄存器，门闩也不可靠。
  2. `=833` 的“自动转软调暗”对 -110 不成立。
  3. `Client::open` 无条件预建计算管线，回退 `=65` 救不回来。
  4. 探针放进用户完整包，会打断用户正在进行的测试。
- 判读表漏了最便宜的一个候选：`GCC_DISP_AHB_CLK` 本身没开。
- 根因表把“AHB 关”列到 55%，和方案自己的门闩逻辑矛盾。A1 不该预设为最省的主线。
- 顺序要改：`=67` 是 A 和 B 共同的前提，应排在最前。

## 1. 核对通过的事实

| 项 | 结果 |
|---|---|
| 【真机】`mask=1`，`pagani-panel=65`，首测 997 字节，完整包 998 字节 | 对（两轮 `merged.log` 第 1、6 行） |
| 三位数掩码 998/999 字节，上限 `len>1000` | 对（`UniXNU/scripts/unixnu/payload.py:35`，DarwinBoot 缓冲 1024） |
| 67、193、195、321、449、577、833、81 的位运算 | 全对 |
| D 记录要 `pagani-ramoops` 位 1 | 对（`RamoopsLog.c:1449`，`RamoopsLog.h:111`），额外 19 字节也对 |
| `=67` 不改代码可验 CA：放行、重试 600×100 ms 后停 | 对（`PanelBrightness.h:127,214`，`PaganiPanel.cpp:184`） |
| `disp_crm` 的时钟是 dispcc 索引 6（`DISP_CC_MDSS_AHB_CLK`） | 对（`running.dts:9135` 节点，`iface_clk` 也是它） |
| dispcc 全部偏移（`0x80b0`、`0x8360`、`0x8150`、`0x8034/38/04/44`、`0x8180`、`0x8108`、`0x81b8`、`0xc004/08/0c`） | 对 |
| GPIO 86 地址 `0xf156004` | 对（TLMM 引脚基址 `0x0f100000`，`SpeakerBoard.h:25-26`；不在保留脚内） |
| RSCC `0x2d0`，wrapper `+0/+0x24/+0x40`，SCC，CRMC 偏移 | 对（`sde_cesta_hw.c`） |
| 隧道上限 `0x7005`，要改三处 | 对（`NativeAdrenoPublish.h:286`，`.cpp:105,871`） |
| `NativeAdrenoPublish.cpp:977` 是标记流 | 对 |
| `runtime_id` 只取翻译器 pin 和补丁状态 | 对（`build-adreno-metal-bridge.py:89`） |
| 插件重编不动 Turnip 时着色器包不失效 | 【读码+本机实查】两棵树 Turnip LC_UUID 都是 `1D562715…`，等于 `s1b-cache.json` 的 `device_build_id` 后半 |
| 翻译器 720ef1ce 能读 `air64_v28`、不透明指针 | 夹具 `runtime_examples.ll` 是同一 triple，`llvm-as` 也在 |
| UFS 快照 3.5 s 内带走面包屑 | 对（有新记录且到期就写，`UfsLog.c:554-559`；145 轮/418 s） |
| 无人值守时屏幕一直亮到 P3 | 【真机】smoke 每分钟一帧，到 404 s 仍有，P3 不会因“显示关”被跳过（间接证据） |

需要打折扣的两条：
- “两次开机至少 5 个时刻全 0”：直接日志只有 46.496、46.506、646.516 三处。`start` 那两处是由 `rc -19 mode 0` 推出来的，强制路径没走，所以 `version==0`。结论成立，但其中两点是推断。
- “第二阶段 ClockDxe 重写 CRMC”：我只确认了 `ClockDxe.efi` 里有 `disp_cesta`、`SDE_CRMC` 字符串，没有验证调用时序。

## 2. 必须改的问题

### 2.1 P3 重读挂死快照的头几个寄存器（挂死风险）

- 【读码】`DpuProbe.cpp` 的 `fullSnapshot` 第一条读就是 `kHwIntrStatus`，即 `0xae00010`，随后是 MDP 中断、INTF_1。头部注释写明旧探针也是这个顺序。
- P3 读的 `mdss intr` 正是这个地址，`mdss ver` 紧邻，INTF_1 也在序列前段。
- 方案说挂死“只有时间吻合”，这句话没错。但没有任何证据说明挂点在后面的 CTL、SSPP、SMMU，这几个寄存器没有被排除。
- 【真机】`runs/20261030-121553-m8-gmem-dpu-1` 日志停在 150.847 s，紧贴探针首读时刻（约 151.4 s），约 160 s 冷复位，1 位掉位率 23.5%。
- 失败场景：346 s 读 `0xae00010` 挂住总线，P3 其余数据全丢，手机冷复位，USB 可能要插拔。
- 这两个读的决策价值也很小。判读表里“MDSS 整段读 0”用 PHY 和 INTF 就能判。
- DSI1 同理：设备树 `qcom,dsi-ctrl-num=0`，它不在显示通路里，读到什么都不改变下一步。
- 改法：P3 删掉 MDSS 顶层和 DSI1，只留 INTF_1 的 4 个寄存器。

### 2.2 门闩位用错，且是单次快照（挂死风险）

- 【读码】一加内核 `dispcc-sun.c:2043`：core GDSC 标志是 `POLL_CFG_GDSCR | RETAIN_FF_ENABLE | HW_CTRL | HW_CTRL_SKIP_DIS`。
- 【读码】`gdsc.c:33,36,68-69,79`：这类 GDSC 的上电状态看 `CFG_GDSCR`（`+4`）的 bit 16，不是 `GDSCR` 的 bit 31。
- 但 `NativeDpu.h` 的 `kGdscPwrOn` 写的是 bit 31，方案若沿用就用错了位，门闩可能放行也可能白跳。
- 另外 `HW_CTRL` 表示硬件可自行塌缩。读门闩时开着，不代表读 INTF 那一刻还开着。
- 现在要查的症状本身就是“读回 0”。方案只给了 GCC 的门闩公式，dispcc 的“在跑”没有公式。
- 失败场景：dispcc 也整段读 0 时，`CLK_OFF=0` 看起来像“时钟在跑”，门闩放行。
- 改法：
  - 门闩一律要求 enable 位为 1，且同组读数不全为 0。
  - GDSC 用 `0xaf09004` bit 16。
  - 把 GDSC 和 `mdp` 加进 P1 的相位采样。只有 64 次全开才进 P3。

### 2.3 P2 门闩不够（挂死风险）

- 【读码】`sde_cesta.c:1297-1320`：安卓先打开电源域，再碰 wrapper 和 SCC。设备树 `running.dts:42167` 节点有 `power-domains = <0x42 0x00>`。方案只看 `rscc` 时钟，不够。
- “PHY 与 DSI 同属一段地址，不设门闩”这个理由不成立，地址相邻不等于电源域或时钟相同。
- 而且方案把 AHB 门闩当作防挂主手段，唯独放过 PHY，自相矛盾。
- 失败场景：PHY 排在 P2 第一个，一旦挂住，RSCC、SCC、CRMC 全部拿不到。
- 改法：PHY 挪到 P2 末尾并加 AHB 门闩。wrapper 和 SCC 加 GDSC 门闩。

### 2.4 `=833` 对 -110 不是安全回退（冻屏风险）

- 【读码】`PeripheralIO.h:156-158,206-210`：非强制路径 -110 不恢复寄存器，`0x15c` 留在 `0x30006`，`0x3c` 也被改过，命令留给引擎。
- A1 让 `ver` 和 `ctrl` 恢复后走的正是这条路径。
- 【推测】失败场景：首条 DCS 超时，DMA 卡在链路上，自动刷新的帧可能被堵，屏幕冻住。此时软调暗照常发布，系统以为一切正常，只能重启。
- 第 4 节风险表写“用 `=833` 时自动转软调暗”，这个说法不成立。
- 改法：
  - 只有 -19、-16 才自动转 B，这两种没写过寄存器。
  - -110 一律不发布，留现场。
  - A1 第 2 步的“确认三项”必须是发送前的代码门闩，不能只记日志。现有发送路径不看 `trig`。
  - A1 只在带定时复位的首测里验证。

### 2.5 `Client::open` 预建管线让回退失效（开不了桌面）

- 方案写“`Client::open` 里预建管线，首帧不卡”，这是无条件执行。
- 插件在种子里，与 `pagani-panel` 无关，所以 `=65`、`=81` 也会走这段代码。
- 失败场景：翻译器或 Turnip 编这个新着色器时在 backboardd 里崩溃，render server 反复重启，桌面起不来。
- 风险表写“回 `=65`，运行时失败自动退回 blit”，这只覆盖返回错误，不覆盖崩溃。
- 改法：只在内核第一次下发 `dim<256` 时才建管线。首帧慢一点可以接受。

### 2.6 探针进完整包（用户在用时挂死或 panic）

- 第 4 节表里探针有 999 字节的完整包档，第 5 节推荐“下一包”。
- P1 到 P3 在 226–346 s 执行，正是用户测试的时段。dispcc 在 13T 的 XNU 上从未读过（全部运行摘要里没有 `pagani-dpu-probe`）。
- 一个 bit 7 同时管 P0 到 P3，无法只开低风险段，也不符合“新硬件路径默认关、分阶段、带定时复位”。
- 改法：拆成两位。
  - bit 7（128）：E、P0、P1。
  - bit 10（1024）：P2、P3。
  - 首测 `1+64+128+1024=1217`，999 字节，合法。
  - 完整包只带首测已经跑通的部分。

### 2.7 P0 热循环偏激（【推测】）

- 方案把 XPU 或 NoC 断连列到约 25%，又把 P0 的风险写成“几乎无”。这两者不一致。
- 若读 0 来自访问控制，一次 100 ms 无延时循环约十万次违规读，每次可能产生安全中断。
- 失败场景：安全侧中断风暴或看门狗触发，50 s 就复位，后面什么都没有。
- 改法：DSI 每 20 µs 读一组，共读 100 ms。TE 可以高频采样。

## 3. 判读与根因的问题

### 3.1 漏了 `GCC_DISP_AHB_CLK`

- 【读码】一加内核 `gcc-sun.c:3696-3708`：Linux 开机无条件置 `0x27004` bit 0。dispcc 的 `iface` 就是它（`<0x34 0x0f>`，索引 15）。
- XNU 没写过这一位，也没人看过它的值。
- 方案在门闩不满足时只打 `p1 skipped` 然后跳过，判读表没有这一行。
- 这是 `E` 和 `P0` 就能回答、风险最低的问题。
- 改法：判读表加一行。若 GCC 这位是关的，修复是单寄存器写（A0），排在 A1 前面。

### 3.2 55% 的前提自相矛盾

- 方案一边说 AHB 关导致读 0，一边用“AHB 关就不读 INTF”防挂死。
- 【真机】DSI0 读 0 不挂。【读码】`PeripheralIO.h:231-232` 写的是读未上电块会挂。
- 同一条件不能既返回 0 又挂死。若 AHB 关真的只读 0，P3 门闩就没有依据。若 AHB 关会挂，55% 就高估了。
- 所以第 5 节“A 优先、约 20 行”不能预设。`p0 lat` 的结果要先看，它能区分慢超时和就近应答。

### 3.3 不要改 `PeripheralIO.h:231-232`

方案要把那条注释改成“未被证明”。在 P3 跑通之前不要动。证据弱，但这是唯一的挂死前科，改了会放开后来者的手脚。

### 3.4 `E` 分不开固件和 XNU 启动代码

`E` 在 XNU 内读，A3 的 CESTA 嫌疑它覆盖不到。若 P1 显示全开但 DSI 仍为 0，建议下一步在 DarwinBoot 退出启动服务前后各读一次 `ver`，这样能分开固件退出阶段和 XNU 之后。

### 3.5 相位采样的时间不准

- `README.md` 明确写 `IODelay` 在这个系统上会阻塞。
- “每 250 µs 一组，覆盖约两帧”实际间隔不稳定，得到的相位不可靠。
- 改法：用 `CNTPCT` 忙等，并把实际总时长写进日志。

## 4. 路线 B 的问题

### 4.1 B 的前提未验证

B 同样要发布 `backlight-control`。CA 若卡死，B 也不能用，所以 `=67` 要先于 B 开工。

### 4.2 `presentDim` 没处理状态带和目标偏移

- 方案自己的调研已指出 `scanout_opaque` 绕过状态带（`ipados27-parity.md:109`）。
- 【他人记录】Pagani 的现场也证实了（`STAGE6.md:1386`）。
- `present()` 现在按 `copyRuns` 分段，还处理 `targetOffset`，方案的着色器没有 y 偏移。
- 失败场景：开 overlay 的诊断包里，调暗一次就把状态文字盖掉。
- 改法：用缓冲区偏移按 run 分两次派发。

### 4.3 静止重绘可能画回旧帧

- 方案只在 `:731` 清记录。
- 失败场景：offer 被拒后走 CPU 路径（`:797` 之后），`shownMemory` 仍是更早的帧。此时拖滑块，屏幕退回旧内容，直到下一帧。
- 【真机】两轮的 F 行都是 `cpu 0`，所以这种情况少见，但修复只有一行。
- 改法：CPU 路径也清记录。

其余小改动：
- `stop()`（`:580`）要清 `dimRedraw`，并释放 `shownSurface`。
- `:888` 的提前返回要补 `stageIdle`，否则状态停在 COPIED，诊断读数不准。

### 4.4 “表面不会被改动”没有证据

- 方案标了【读码】，但没给行号。
- `:943` 的注释写的是 family 已“released its current reference”。代码里也没有 IOSurface 使用计数。
- 若刚退役的表面已归还 CA，重绘时会读到正在渲染的下一帧。
- 这一点我没能证实，也没能证伪，要在上机时看拖滑块是否闪。

### 4.5 线协议头有两份

- 方案只说改 `NativeGOPWire.h`。
- 内核用 `UniXNU-Pagani-Drivers/drivers/NativeMobileDisplay/`，插件用 `UniXNU/drivers/GopFramebuffer/`（`build-userspace-a830.sh:48`，`build-adreno-metal-plugin.py:32`）。
- 现在两份 md5 相同，只改一份会编译失败或协议漂移。

### 4.6 种子流程必须写清

- “重做种子”不能跑 `build-userspace-a830.sh`，它从 `BASE_SEED` 重建全部种子，而 `make-a830-final-seed.sh:2` 写明成品种子的底层已经不在了。
- 也不能只重跑 `make-a830-final-seed.sh`。插件在 `s1b-cache` 里，它不换插件。
- 失败场景：新内核配旧插件，调暗不生效，也没有报错。
- 改法：用 `build-userspace/a830-inputs/findmy/scripts/deploy-findmy-plugin.py` 的 `--seed`、`--trust` 换进 `s6-final`，并确认 Turnip 不重编。

### 4.7 其他

- 【他人记录】Pagani 的 GMEM 加 `scanout_opaque` 组合出现过颜色跳变，切 SYSMEM 恢复，具体原因未定。我们是 GMEM 加 blit 没有这个问题，调暗路径是新组合，验收要专门看颜色。
- 【推测】“自动验收 `requests>0`”不一定成立。无人值守时 CoreBrightness 可能不发请求，`requests 0` 分不清是沙盒拒绝还是没人动滑块。至少要有一次人工拖动。

## 5. 修正后的推荐

**先做：**
1. `A830_PANEL=67` 首测，不改代码，只出镜像，定时复位。先回答 CA 是否卡死，这决定 B 值不值得写。可以由回路自动跑，滑块那一步等用户方便时补。
2. 下一个统一包的首测，值用 `1217`（或再加 2 得 `1219`）：
   - E、P0 读 `0x127004`，DSI 按节奏采样。
   - P1 读 dispcc，相位采样带 GDSC 和 `mdp`。
   - P2 把 PHY 放最后，SCC 和 wrapper 加 GDSC 门闩。
   - P3 只读 INTF_1，要求相位全开且显示开。
3. 首测通过后，完整包只带已证明安全的位，不带 bit 10。
4. 按结果定修复：
   - GCC 关：A0。
   - dispcc AHB 关：A1，先在首测验证，-110 不自动转 B。
   - 其余情况：交付 B（`=321`）。
5. 同时写 B，但等第 1 步通过再合入。按第 4 节修：
   - 管线懒加载。
   - 分段派发。
   - CPU 路径清记录。
   - 两份线协议头都改。
   - 用换插件脚本出种子。

**不做：**
- 不读 MDSS 顶层、DSI1、CTL、SSPP、SMMU。
- 不在完整包里跑 P2、P3。
- 不做 100 ms 无延时热循环。
- 不让 -110 自动转软调暗。
- 不在 `Client::open` 无条件建管线。
- 不提前宣布 A 是主线。
- 不改 `PeripheralIO.h:231-232` 的挂死警告。
- A3（跳过 `disp_cesta`）不进自动首测。

## 6. GPL 参考源码位置（只取事实）

- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/dsi-zero/src/opk/drivers/clk/qcom/dispcc-sun.c`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/dsi-zero/src/opk/drivers/clk/qcom/gdsc.c`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/ext/op-kernel/drivers/clk/qcom/gcc-sun.c`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/dsi-zero/src/opm/msm/sde_cesta.c`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/dsi-zero/src/opm/msm/sde_cesta_hw.c`