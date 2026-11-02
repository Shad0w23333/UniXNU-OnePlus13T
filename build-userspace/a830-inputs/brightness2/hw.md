# SM8750 上 DSI0 读全 0 的硬件调研结果

全程只读，没有动手机。我抓取的源码和 IDA 库都在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/dsi-zero`。

## 结论

1. **读 0 是静态现象**：不是帧间隙碰巧读到的。
2. **最可能的含义**【推测】：MDSS 有电，数据通路在跑，但 CPU 读寄存器这条路上缺一个时钟或接口域。它不是断电型故障，断电通常表现为挂死。
3. **公开规律**【读码】：GDSC 没电时读寄存器会崩溃，GDSC 有电但时钟没开时读到 0。
4. **没找到是谁关的时钟**【读码】：DisplayDxe 退出时不关 DSI 和核心时钟，第二阶段 ClockDxe 也没有关 `disp_cc_mdss_ahb_clk` 的调用路径，XNU 侧也没有代码碰 dispcc。
5. **13T 与 zorn 的已知差异**：第二阶段 ClockDxe 会在屏幕运行时重写 CESTA 的 CRMC。这是目前最大的嫌疑点，但只是【推测】。
6. **最关键的一步**：读 dispcc。读数能判断问题在 MDSS 内，还是整段显示配置空间都读 0。现有的 `pagani-dpu-probe=4` 不用改代码就能做第一步。

## 新增证据

- **又一轮现场**【真机】：`工具/gpu-test-loop/runs/20261031-223924-stage1-full-1/merged.log:3266-3268`（46.506 s）和 `:5709-5711`（646.516 s）同样全 0。
- **启动时版本号也是 0**【真机+读码】：日志是 `rc -19 mode 0`。按 `PanelBrightness.h:144-150`，版本号非 0 就会走强制发送，强制发送不可能返回 -19。
- **采样覆盖不同帧相位**【真机+推测】：两次开机至少有 5 个独立时刻。链路占空比约 70–88%（`PaganiPanel/README.md:38`、面板 XML 的 1.096 Gbps × 4 lane）。即使间隙按 40% 算，5 次全落在间隙里的概率也只有 1%。
- **读取很快**【真机】：14 个寄存器加格式化在 1 ms 内完成，排除了每次读都走长超时。
- **总线响应正常**【推测】：CPU 没有收到中止异常，说明这些读拿到了正常响应和 0 数据。

## 1. 访问 DSI 寄存器的依赖

| 资源 | 地址 | 谁控制（sun） | 依据 |
|---|---|---|---|
| `GCC_DISP_AHB_CLK` | `0x127004` | Linux 常开；是 dispcc 的 iface | `gcc-sun.c:3696,3708`；`running.dts:9510-9511` |
| `DISP_CC_MDSS_AHB_CLK` | `0xaf080b0` | `mdss_mdp` 的 `iface_clk`，经 `sde_power` | `running.dts:34812-34813`；`dispcc-sun.c:1260-1276` |
| `DISP_CC_MDSS_NON_GDSC_AHB_CLK` | `0xaf0c004` | CESTA 依赖项 | `dispcc-sun.c:1908` |
| `DISP_CC_MDSS_MDP_CLK` 与 RCG | `0xaf08010`、`0xaf08150` | `sde_cesta` 的 `branch_clk`、`core_clk`，由 CRM 管 | `running.dts:42175-42176`；`dispcc-sun.c:908-926` |
| `GCC_DISP_HF_AXI_CLK` | `0x127008` | `sde_power` 的 `gcc_bus` | `running.dts:34812-34813`；`gcc-sun.c:1872` |
| MDSS 核心 GDSC | `0xaf09000` | 只有 `sde_cesta` 引用，带 `HW_CTRL\|HW_CTRL_SKIP_DIS` | `running.dts:42169`；`dispcc-sun.c:2036-2045` |
| 寄存器总线投票 | APPSS → `SLAVE_DISPLAY_CFG` | `qcom,sde-reg-bus` | `running.dts:34803-34814` |
| 电源轨 | MMCX、MX | `vdd_mm`、`vdd_mx` | `running.dts:9508-9509` |

- **核心时钟的归属变了**：DSI 节点不再列 `mdp_core`、`iface`、`bus`（`running.dts:41889`），本机也没有 `mnoc`、`core_mmss` 时钟，`dsi_ctrl.c:820-840` 取不到就置空。
- **Android 的做法**：读写前先 `pm_runtime_resume_and_get`（`dsi_ctrl.c:3668-3671`），连续闪屏时也一样，注释写明读寄存器空间要先投 GDSC（`dsi_display.c:5833`）。
- **主线的做法**：DSI 版本在 runtime PM 下读（`dsi_host.c:218-226`），父节点 mdss 同时打开 AHB、AXI、MDP 时钟（`sm8750.dtsi:3358-3360`）。
- **主线明确注释**：`msm_mdss.c:277` 写着 "Register access requires MDSS_MDP_CLK"。这句话针对 MDSS 寄存器，是否适用于 DSI 没有公开说明。
- **link 时钟**：`byte0`（`0xaf08034`）、`byte0_intf`（`0xaf08038`）、`pclk0`（`0xaf08004`）、`esc0`（`0xaf08044`）归 `dsi_clk_manager` 管，发 DCS 必须开。

## 2. 动态门控与 CESTA

**硬件门控开关**【读码】
- `DISP_CC_MISC_CMD`（`0xaf0f000`）：bit 4 是 MDP 时钟门控（`dispcc-sun.c:2216-2217`）。
- 同一寄存器的 bit 5/6 是 pixel，bit 7/8 是 byte，bit 9/10 是 PHY（`dsi_ctrl_hw_2_2.c:192-214`）。
- Android 默认只开 pixel 和 PHY（`dsi_display.c:145`）。
- 切换 `DSI_EN` 和软复位时，要先用 `DSI_CTRL_DYNAMIC_FORCE_ON` 强开内部时钟（`dsi_ctrl_hw_cmn.c:26,827-841`）。

**自动刷新时**【读码】
- Android 在自动刷新或闪屏期间关闭 CESTA 硬件睡眠（`sde_encoder_phys_cmd.c:2794`）。
- UEFI 没有配置 SCC 的代码。

**帧间隙读到什么**
- 没有公开资料直接说明 DSI 的情况。
- 按第 4 节的规律，时钟断时应读 0，GDSC 断时应挂死，版本寄存器不存在读旧值的问题【推测】。
- 本机证据已排除帧间隙是主因。

**`sde_cesta` 是什么**【读码】
- 它是 RSCC 外壳、每路显示的 SCC 和 CRM 的驱动。
- 它按帧投 MDP 时钟和带宽，管 MDP 门控，并把 GDSC 交给硬件（`sde_cesta_hw.c`、`sde_cesta.c:818-879`）。
- `RSCC_SEQ_PWR_CTRL_STATUS` bit 12 表示 GDSC 塌缩成功（`sde_cesta.c:127`，偏移 `0x2d0`）。
- 上游补丁讨论把它描述为接收投票、管理成组时钟的新硬件；同一讨论也说时钟不靠它也能工作。主线目前仍手动管 MDP 时钟（`dispcc-sm8750.c:695-698`）。

**固件用不用它**【读码】
- DisplayDxe 不用：没有 CESTA 字符串，也没有直接访问 dispcc 或 RSCC 的地址。
- ClockDxe 用：`sub_C520` 是 CESTA 初始化，`sub_C1BC` 是实例初始化。
- 本地 Binaries 里抽查的机型中，只有 Pakala 及更新的平台含 `disp_cesta`，zorn 所属的 SM8650（Lanai，以 giulia 为代表）没有。

## 3. 固件核对

**引导链**【读码】
- 第一阶段是原厂 XBL 和 ABL。
- 第二阶段是 Silicium（`board.toml:95-102`），`USE_CUSTOM_DISPLAY_DRIVER=0`，加载 SimpleFbDxe 而不加载 DisplayDxe（`oneplus13t.dsc:28`、`APRIORI.inc:100-102`）。
- 所以 DisplayDxe 的退出逻辑只在 ABL 退出启动服务时跑一次。

分析的二进制来自 13s 的 `CPH2723_16.0.3.501`，与 13T 实机固件可能有小差异。

**DisplayDxe**（RVA，反编译见 brightness 目录的 `all.c`）
- `sub_15B8` 是退出启动服务回调，用 `CreateEvent(0x201)` 注册（`all.c:423`）。
- `sub_222C4` 是 `MDPExitBoot`。显示 0、1 未设 `0x1000000` 标志时置 `v3=1`，跳过反初始化和面板下电（`all.c:28644-28758`）。
- `sub_21FC0(1)` 是 `MDPTerm`：只关 DP 时钟，不停 VBIF，不关核心、DSI、RSCC 时钟（`all.c:28536-28641`）。
- **结论**：退出时 DSI 时钟保持开启，控制器不反初始化。
- `sub_15204` 向 `DSI_CTRL` 写 `0x1F1`（`0xae94004`，DSI1 为 `0xae96004`），含 DSI_EN、4 条数据 lane 和时钟 lane，由 DSI 初始化调用（`all.c:16596-16597`）。
- `sub_15144` 之后可加 `CMD_MODE_EN`（`all.c:18173`），这与 zorn 读到的 `0x1f5` 一致。
- `sub_150E8` 写 `DSI_CLK_CTRL`：打开时写 `0x23F`（调用点 `all.c:17341`），DSI 关闭时写 0（`all.c:16891`）。

**ClockDxe 补丁**【读码】
- `Binaries/pagani/README.md:18` 写明去掉了 CESTA 依赖关闭调用，原因是要保持屏幕亮着。
- 实际差异在文件偏移 `0xC6B0`：`BL sub_C494` 改成 `MOV W0, WZR`。
- `disp_cesta` 实例表在 `0x3E4B0`，依赖项是 `gcc_disp_ahb_clk`、`disp_cc_mdss_non_gdsc_ahb_clk`、`disp_cc_mdss_rscc_ahb_clk`、`disp_cc_mdss_rscc_vsync_clk`（`0x3E8A8`）。
- `disp_cc_mdss_ahb_clk` 不在依赖列表里。

**CESTA 重写的内容**【读码】
- `sub_C1BC` 向 CRMC（`0xaf27800`）写入：`+0x56C` 99 个字，`+0x4EC` 28 个字，再写 168 项配置。
- 配置里有 `+0x2A0=0x108150`、`+0x29C=0x108154`，指向 MDP RCG。
- 这发生在屏幕运行时，实际影响未知【推测】。

**其他**
- RscDxe 对 `sde` 实例（`0xaf20000`）加载 5 个字的序列并触发（`sub_15E8`、`sub_2D30`、`sub_2DA8`），显示依赖不反初始化。
- ClockDxe 里 `Clock_Disable`（`sub_5300`）只被协议包装和已打掉的 `sub_C494` 调用，也没有退出启动服务事件。

## 4. 未开时钟的块：挂死还是读 0

- **时钟断**【读码】：上游维护者 Baryshkov 称，在他测过的平台上，MDP_CLK 未开时读 HW_REV 返回 0。高通的审查者原本预期会崩溃。这些是较老的 MDP5 平台，推到 SM8750 属【推测】。
- **GDSC 断**【读码】：提交 `5d8c0417` 记录，MDSS_GDSC 未开时读版本号会让 db410c 崩溃。
- **12:15 挂死**【推测】：那轮读了 CTL 1–5、闲置管线和 SMMU。INT2 GDSC 在设备树里没有消费者，未上电的块或 SMMU 最可疑。这与 DSI 读 0 不挂死并不矛盾，但无法确定具体是哪一次读挂的。
- **Gunyah**【读码】：公开代码里，S2 未映射且不是 vdevice 时，会向客户机注入同步外部中止（`trap_dispatch.c:117-122`、`exception_inject.c:119-228`），不会返回 0。
- **硬件中止路由**【读码】：`TEA=0`，同步外部中止不进 EL2。物理 SError 由 `AMO=1` 路由到 EL2，再注入 RAS 虚拟机或终止。
- **Gunyah 局限**：实际镜像可能含私有模块，读延迟测试可以旁证。
- **XPU 和 NoC**：没找到公开资料证明 XPU 违例或 NoC 超时会读 0。`sde_cesta.c:874` 只提到 NOC 问题的风险。

## 5. 发 DCS 的最少条件

**前提**【读码】
1. 寄存器可读：`DSI_HW_VERSION` 应为 `0x20090000`（主线 v2.9，`dsi_cfg.h:34`）。
2. 核心时钟、link 时钟和 GDSC 都在（`dsi_ctrl.c:3654-3714`）。
3. `DSI_CTRL` 有 DSI_EN 和 CMD_MODE_EN，lane 已使能。
4. `DSI_CLK_CTRL` 维持 `0x23F`。发 DCS 不需要强开 `DYNAMIC_FORCE_ON`。
5. `TRIG_CTRL` 的 DMA 触发选软件（`dsi_ctrl_hw_cmn.c` 的 `init_cmddma_trig_ctrl`）。
6. TPG FIFO 空闲。

**发送序列**【读码】（`dsi_ctrl_hw_cmn.c:957-1030`）
1. `TPG_CTRL=0x30006`。
2. 把命令写入 `0x17C`；奇数字要补 0。
3. 配 `DMA_CTRL`：bit 28 必须置，bit 26 是 LP。
4. 写长度，再向 `0x90` 写 1 触发。
5. 等 `INT_CTRL` bit 0，然后复位 FIFO。

**不需要的操作**【读码】
- 亮度路径不暂停自动刷新。
- 命令模式下不需要 TE 窗口，DMA 调度只用于视频模式或自定义调度（`dsi_ctrl.c:1609-1611`）。
- `wait_for_cmd_mode_mdp_idle` 只用在动态时钟切换（`dsi_display.c:9515`）。

**现有代码**
- 版本号为 0 时不强制发送（`PanelBrightness.h:145`），这是对的，应保留。

## 6. dispcc 可读寄存器

**前置条件**：`0x127004` 的 bit 31 为 0（AHB 时钟确实在跑）。

| 地址 | 寄存器 | 预期 |
|---|---|---|
| `0xaf080b0` | `MDSS_AHB_CBCR` | bit 0=1，bit 31=0 |
| `0xaf08010` | `MDSS_MDP_CBCR` | 帧内应开 |
| `0xaf08034`、`0xaf08038`、`0xaf08004`、`0xaf08044` | byte0、byte0_intf、pclk0、esc0 | 开 |
| `0xaf08030` | vsync | 开 |
| `0xaf0c004`、`0xaf0c00c`、`0xaf0c008` | non_gdsc、rscc_ahb、rscc_vsync | 开 |
| `0xaf09000`、`0xaf09004` | CORE GDSCR、CFG_GDSCR | bit 31 PWR_ON=1，bit 1 看是否硬件控制；CFG 的 bit 16 上电完成 |
| `0xaf0b000` | INT2 GDSCR | 多半关 |
| `0xaf08360`、`0xaf08364` | AHB RCG 的 CMD、CFG | CMD bit 31 ROOT_OFF=0 |
| `0xaf08150`、`0xaf08154` | MDP RCG 的 CMD、CFG | 同上 |
| `0xaf0f000` | `DISP_CC_MISC_CMD` | 看第 2 节的门控位 |

**CBCR 位**：bit 31 CLK_OFF，bit 30:28 NOC FSM，bit 1 硬件门控，bit 0 使能。sheng 的记录提醒，空闲 AHB 分支在硬件门控下可能显示 CLK_OFF，要把使能位和门控位一起看。

**可能挂死的读**
- `gcc_disp_ahb_clk` 断时读 dispcc。
- `rscc_ahb` 断时读 RSCC、CRM。
- INT2 域、CTL 1..5、闲置管线、apps SMMU。

## 7. 各解释的排序

| 解释 | 判断 |
|---|---|
| AHB 或接口时钟域断，GDSC 有电 | 最可能【推测】；MDP 和 DSI 配好后，自动刷新不需要 CPU 访问寄存器 |
| CESTA 重初始化的副作用 | 这是上一项的可能成因【推测】，也是 SM8750 独有项 |
| XPU 或访问控制 | 无公开依据，dispcc 读数可排除一半 |
| Gunyah 私有 vdevice | 可能性低，靠读延迟旁证 |
| 帧间隙门控 | 不是主因，5 个时刻全 0 |
| S2 未映射 | 公开代码是注入中止，排除 |
| 地址错、映射错 | 设备树、面板 XML 和 DisplayDxe 一致，同样映射方式的其他外设工作正常，排除 |

## 8. 最小实验（挂死风险从低到高）

按“修完再测”，1–5 可以合成一包，在 45 s 的 report 定时器里依次执行，避开开机早段的日志缺口。每步读之前先写一条 U 记录，挂死时最后一行就是现场。

| # | 风险 | 动作 | 预期 | 判读 |
|---|---|---|---|---|
| 1 | 同现状 | 读 `0xae94000`：连读 4096 次，再每 100 µs 读 200 次，用 `CNTVCT` 打点 | 全 0 | 出现 `0x20090000` 说明与帧相关，要记相位；延迟明显高于实验 2 的基线则疑似陷入 EL2 |
| 2 | 约等于 0 | 读 `0x127000`、`+4`、`+8`，同样测延迟 | `+4` bit 0=1、bit 31=0 | `+4` 关着说明根因在 GCC 侧；`+8` 是 `HALT_SKIP`，bit 31 不作判据 |
| 3 | 低 | 第 6 节全表，只读，对 AHB、MDP、byte、pclk 的 CBCR 每 500 µs 采 32 次。第一步可直接用 `pagani-dpu-probe=4`；命令行已 998 字节，要先去掉一项参数 | CORE GDSC 为 ON，AHB 在 | 见下表 |
| 4 | 低 | `rscc_ahb` 开着才读：`0xaf202d0`；`0xaf30000`、`+0x24`、`+0x40`；`0xaf31000` 的 `+0/+4/+8/+0x10`；`0xaf27800` 的 `+0xC/+0x18/+0x40/+0x280/+0x2A8` | SCC 未启用（`SCC_CTRL` 为 0 或 `0xf0`），CRMC `+0xC`=1 | SCC 在跑说明 CESTA 确实在管帧；`0x2d0` bit 12=1 说明 GDSC 塌缩成功过 |
| 5 | 低至中 | 读 `0xae95000..c`（PHY ID，DisplayDxe 也读），再读 `0xae96000` | 非 0；DSI1 为 `0x20090000` | PHY 非 0 而 DSI0 为 0，是控制器专属问题；都 0 是 MDSS AHB 段问题 |
| 6 | 中 | `pagani-dpu-probe=1`（INTF_1 计数），再读 `0xae00000` | 约 `FC 120/s`、`AR 80000001` | 全 0 说明整段 MDSS 读 0；挂死时看面包屑定位分界 |
| 7 | 中（首次写） | 仅当实验 3 显示 AHB 关：`0xaf080b0 \|= 1`，轮询 bit 31 不超过 1 ms，再读版本号 | 读到 `0x20090000`、`ctrl 0x1f5` | 恢复即根因确认；若 RCG ROOT_OFF 则不动，改查 PLL1 |
| 8 | 中 | 固件对照：a）在 ClockDxe 前后和 `ExitBootServices` 前各读版本号与 AHB CBCR（`MemoryMapLib` 要先加 MDSS 和 dispcc 映射）；b）测试固件把 `0x3E588` 改 0，跳过 `disp_cesta` 的 `sub_C1BC`，或去掉 RscDxe | 找到变 0 的时间点 | ClockDxe 后变 0 即定位到 CESTA 初始化；b 恢复也同样确认 |
| 9 | 中高 | 寄存器可读后才走现有 `pagani_panel_first_dcs` | `rc 0`，`writes` 增长 | 同值写通过后，再改亮度值 |

**实验 3 判读**

| 读数 | 判读 |
|---|---|
| dispcc 全 0 | 整段 `qhs_display_cfg` 读 0，查 GCC、NoC、访问控制 |
| AHB 使能为 0 或 CLK_OFF=1（已排除硬件门控） | iface 断，做实验 7 |
| AHB 开，MDP 全程关 | 查 CRM 和 `MISC_CMD` bit 4 |
| 全部开着 | 转查权限和接口，做实验 5、8 |
| 挂死 | dispcc 不可达，回到实验 2 的读数 |

**不要做**：寄存器读恢复之前不写 `DSI_CLK_CTRL`，不切换 `DSI_EN`，不做 DSI 软复位，不用 `pagani-dpu-probe=3`。

来源：
- [上游 MDSS 讨论（HW_REV 读 0）](https://lkml.iu.edu/hypermail/linux/kernel/2402.0/08897.html)
- [提交 5d8c0417（GDSC 未开导致崩溃）](https://github.com/torvalds/linux/commit/5d8c0417ea62fed3cec7f5daed06a20477efeb39)
- [SM8750 dispcc 讨论（CESTA）](https://lkml.iu.edu/hypermail/linux/kernel/2412.0/05323.html)
- [Linux 主线](https://github.com/torvalds/linux)
- [Gunyah Hypervisor](https://github.com/quic/gunyah-hypervisor)
- [一加内核](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8750)
- [一加模块与 display-drivers](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750)