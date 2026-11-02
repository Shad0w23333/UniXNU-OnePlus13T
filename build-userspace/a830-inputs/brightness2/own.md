**结论**

- `panel: start` 的文字恢复不了。但有三条独立证据把内容钉死：`ver` 是 0，全部 28 个读数都是个位数，实际就是全 0。
- 读数不是“DSI 没启用”。版本寄存器是硬件常量，读到 0 说明读事务没拿到真实寄存器，但总线正常应答了，没有挂死也没有中止。
- 13T 上 XNU 从未把任何 MDSS 或 INTF 寄存器读出非零，也从未写过一次。“寄存器接口是活的”这件事没有任何正面证据。
- 解释排序：c、d 基本排除。a 和 b 现有证据不能直接分开，我更偏向 a 的变体，即寄存器访问口没开或被 SM8750 的硬件电源管理挡住。b 里 Gunyah S2 这一支基本排除，XPU 读作 0 留作第二候选。
- 我工作期间多出一轮 `20261031-223924-stage1-full-1`（完整包），已一并纳入。它在 1183 s 的 panic 是 `vnode_rele_ext`，与显示无关。

下文 RUN1 指 `工具/gpu-test-loop/runs/20261031-221300-stage1-smoke-1`，RUN2 指 `工具/gpu-test-loop/runs/20261031-223924-stage1-full-1`。

---

## 1. panel 行与日志缺口

**直接看到的记录【真机】**

| 轮 | 时间 | seq | merged.log 行 | 内容 |
|---|---|---|---|---|
| RUN1 | 46.496 s | 4045–4047 | 3131–3133 | 全 0；`rc -19 mode 0 power 1/1 stopped` |
| RUN2 | 46.506 s | 4190–4192 | 3266–3268 | 同上 |
| RUN2 | 646.516 s | 6633–6635 | 5709–5711 | 同上 |

- `shown 1433 goal 1433` 是亮度值 0x599，不是帧数。
- `diag: r1@78 io in:2 pan:0`（RUN1 merged.log:3697，RUN2 同样）确认没有已注册的 PaganiPanel 服务。
- 全部 48 轮里只有这两轮有 `panel:` 行。

**能否恢复：不能【真机】**

- 解压后的窗口原始字节里，`first DSI`、`first DCS`、`no dcs`、`S3910 touch`、`ARMPlatform` 全部 0 次命中。
- `panel:` 在 RUN1 是 27 次（9 个槽各 3 行），RUN2 是 51 次，全是 46 s 之后的 report 组。
- `ufs/msgbuf.txt` 最早时间 RUN1 107 s、RUN2 943 s，没有面板内容。`console-ramoops-0.bin` 只有终态。
- 丢失过程：
  - 槽 0 在 1.194 s，事件环写到 0x2691。
  - 槽 1 在 1.526 s，环已绕回，写头 0x2e61，最老记录是 seq 260（在 0x2e93）。
  - GPU 追踪在约 15 ms 内冲完 32 KiB。面板行差约 2000 字节就能留下。
- `panel: start` 在 226–240，不在 827–1034。后者和 1039–1042、1047–1242、1247–1517 都是 GPU 追踪。

**内容为什么被钉死（RUN1、RUN2 结果相同）**

1. 逻辑【真机+读码】：rc 是 -19 且 mode 是 0。
   - 内核 f7f9908f 反汇编 `PaganiPanel::start`：`cmn w0,#0x13` 后 `cbz w21` 跳过强制发送，w21 就是 logState 返回的 version。
   - 若 `ver` 非 0，强制路径不可能留下 -19（`PanelBrightness.h:145`）。
   - 所以启动时 `ver==0`。
2. 条数【真机】：追踪器自报 `g done ... lines 1696`。
   - seq 238..1977 共 1740 条，减去可见的 44 条 G 行正好 1696，所以第一条 g 行是 seq 238。
   - 于是 226–237 恰好 12 条，等于代码里 `ver==0`、不强制这条路径的 12 条记录。
   - 若走强制路径会多 `panel: forced` 两行。
3. 字节【真机+推测】：226..259 在事件环里占 2050 字节，两轮相同（RUN1 0x2691→0x2e93，RUN2 0x2695→0x2e97）。
   - 下表的 30 条重建合计正好 2050 字节，差值 0。
   - 按 zorn 那样的活控制器取值代入是 2118 字节，多 68 字节。
   - 不确定度 1 字节：`c300034` 的值未记录，我按 3 位十六进制算。若是 2 位，28 个读数里至多一个是两位数。

| seq | 重建内容 |
|---|---|
| 226–227 | R `S3910 touch started`；R `AppleARMPlatform load`（value 0） |
| 228 | `panel: first DSI read next` |
| 229–230 | `panel: start ver 0 ctrl 0 st 0 fifo 0 dma 0 trig 0` 与 `lanes 0 int 0 clk 0 tpg 0 ack 0 to 0 sec 0 split 0` |
| 231 | `panel: first DCS next` |
| 232–233 | `panel: no dcs ...` 两行，同样全 0 |
| 234 | `panel: requests 0 writes 0 retried 0 shown 1433 goal 1433 rc -19 mode 0 power 1/1 off 0 on 0` |
| 235–237 | R `DSI panel control started`；U `panel: stage7 AppleARMPlatform load 0, class registered, service started`；R `GPU hand-off presenter` |
| 238–240 | `g start`、`g0 INPUTS begin`、`g0 INPUTS end ok` |
| 245–259 | `g1 POWER begin` + QMP 描述符读取；seq 259 的尾巴还残留在环里：`g1.0 AOSS r c30002c #3 =ffff0000` |

- 读取时刻：report 定时器在 line 183 设 45 s，46.496−45 = 约 1.49 s【真机+读码】。
- 服务确实起来了：45 s 处没有 `panel: stage7` 重打行，只有服务没起来才会重打（`Stage7Probe.cpp:134-136`）。
- DSI 读取次数：RUN1 共 43 次，RUN2 共 57 次，都没有挂死或中止。

## 2. 全部运行里的 DPU、INTF、TE 记录

- 48 轮里记录为 0 的有 24 轮。按镜像元数据核对，只有 `20261030-121553-m8-gmem-dpu-1` 带 `pagani-dpu-probe=1`。
- 带 `pagani-te`、`pagani-te-ar-off`、`pagani-fb-flip` 的 0 轮。
- 该探针轮没有留下任何一行：`TE`、`FC`、`AR`、`CTL`、`SSPP`、`MDP SID` 全 0 条。两条 `D?` 是位衰减的假阳性（实为 GPU 行）。
- 所以回答是：没有任何 DPU 或 INTF 寄存器读出过非零，也没有增长数据。FRAME_COUNT、TE、自动刷新配置在 13T 上从未有过读数。
- `PROGRESS.md:286` 写“INTF_1 在 13T 上还没读过”，`:573` 写“D2 翻页阶梯一步没跑”，与此一致。
- 唯一的时序相关行是 RUN1 seq 221 `software display refresh Hz 0x78`，这是软件定时器，不是 TE。

## 3. “读寄存器挂死”的全部事实

- **只有一件事**：2026-10-30 12:15，运行 `20261030-121553-m8-gmem-dpu-1`（`PROGRESS.md:239-243`、`NativeDpu.h:11-14`、`DpuProbe.cpp:6-11`）。
- **读了什么【读码】**：显示启动后 `IOSleep(150000)`，依次读 MDSS 顶层 `0xae00010`、MDP 中断 `0xae01010/14`、INTF_1、CTL0..5、十个管线，再读 apps SMMU 全局、SMR、S2CR、上下文组。
- **什么时候挂【真机】**：最后核验记录是 seq 4561，150.847 s 的 G 行。之后没有记录、没有 panic，约 160 s 回到安卓，早于 360 s 定时复位。
- **挂在哪个寄存器：未知**。行要等快照读完才发布，所以一个值都没留下。“探针读挂”靠时间吻合推断，日志本身不证明。
- **危险排序是二手的**：`dpu-safety.md` 磁盘上已找不到，`DpuProbe.cpp:9-11` 与 `PROGRESS.md:286` 的排序还不一致。
- **什么没挂【真机】**：DSI0 那 14 个偏移（RUN1、RUN2 共约 100 次读）；Build 50 的 offset 4。
- **需要纠正的注释**：`PeripheralIO.h:231-232` 写“a read of a block without power hangs this SoC”，这没有被证明。DSI 读 0 不挂死本身就是反例或至少是例外。
- `NativeDpu.h:12` 和 `PaganiPanel/README.md:38` 说 INTF_1 在计时、自动刷新每帧在发，这是读 DT 和 XML 得来的，13T 上没测过。

## 4. 运行时写过哪些 MDSS 寄存器

**代码允许写的范围【读码】（`NativeDpu.h:140-152`）**

| 寄存器 | 位置 | 门槛 |
|---|---|---|
| INTF_1 `TEAR_SYNC_CONFIG_VSYNC`、`TEAR_AUTOREFRESH_CONFIG` | `NativeLegacyDisplay.cpp:497-515`（TE 锁相还会经 `panelTo120` 读） | `pagani-te=1` 且 `pagani-te-ar-off=1` |
| 活动管线 SRC0/SRC1、CTL0 flush | `NativeLegacyDisplay.cpp:322-329`，门槛在 :377 | `pagani-fb-flip=1` 且 `sheng-fb-gpu=1` |
| `MDP_INTR_CLEAR` | `DpuProbe.cpp:165` | 翻页试验通过后 |
| DSI 命令 FIFO 一组 | `PeripheralIO.h:198-204` | 首条 DCS 通过检查 |

- `NativeGOPPresenter.cpp` 不碰任何寄存器。`:136-140` 只是 `pagani-fb-flip=1` 时多映射两块扫描缓冲。
- 48 轮的结果【真机】：上述门槛没有一轮打开，MDSS 写入 0 次，DSI 写入 0 次（`writes 0`，rc -19 表示什么都没写）。
- 所以“写入是否生效过”的答案是从未发生，SSPP 翻转和 `pagani-fb-flip` 都没跑过。
- 屏幕能更新只说明固件留下的 MDP 还在扫描内存（CPU 或 GPU 写 `0xfc800000`），不能说明寄存器接口活着。
- 另外，`ver==0` 门闩（`PanelBrightness.h:145`）决定了写路径至今没试过，“写不通”同样没有证据。

## 5. Build 50 与映射方式

**Build 50【真机】**

- 2026-10-26 用户照片：`DSI disabled r=e00002d8`（`UniXNU-Pagani_SM8750/STAGE6.md:677`）。
- 当时代码 `modules/Pagani/drivers/PaganiPanel/PaganiPanel.cpp:75-78` 只读 offset 4，bit 0 为 0 就退出。
- 我们只知道 bit 0 清零，`v=` 没记录下来。
- 那时还没有 GPU 驱动和 UFS 日志，所以这两者都不是读 0 的原因。

**映射对比**

| 项 | PaganiPanel（`Mapping.h:9-15`） | MDSS（`DpuProbe.cpp:277-291`） |
|---|---|---|
| 调用 | `IODeviceMemory::withRange` + `map` | 相同 |
| 选项 | `kIOMapAnywhere\|kIOMapInhibitCache`，反汇编确认 `0x101` | 相同 |
| 内存属性 | `VM_WIMG_IO`，非内存地址为 Device-nGnRnE | 相同 |
| 基址 | 反汇编确认 `0xae94000`，4 KiB 和 16 KiB 都对齐 | `0xae00000` |
| 大小 | 0x1000 | 0x90000，不含 DSI |
| 前置条件 | 无 | 要求 GOP 控制台已起 |
| 访问 | 读后 `dmb sy`，写后 `dsb sy` | 过访问守卫，无屏障 |

- 差别只在基址、大小和屏障，内存属性没有差别。
- `logState` 反汇编确认 14 个偏移为 0、4、8、c、3c、84、a8、110、11c、15c、68、c0、2cc、330，与 `dsi_ctrl_reg.h` 一致。
- 同一内核里 `PeripheralWindow` 还映射 SPMI、GCC、QUP、TLMM、RSC、cmd-db，都读到真实数据。按键启动要求 SPMI 版本号 ≥0x70000000，seq 223 证明它通过了。
- asteroids 用的是逐字相同的映射写法。

## 6. 各解释逐条判断

**先排除的公共情形：块空闲或未使能。** 只读版本寄存器和 `CLK_CTRL` 一起是 0，活控制器不可能这样。zorn 同偏移读到 `0x20080000`、`0x1f5`【真机·他机】。

**d. DSI0 没在工作，内容走别的路：排除**

- `running.dts:42287-42288` 的 `qcom,dsi-ctrl=<ctrl0 ctrl1>`，面板 ctrl-num 为 0。
- 面板 XML `DSIControllerMapping 00`（:52-54）。
- DisplayDxe `sub_15204` 对控制器 0 写基址 +0x94004。
- 命令模式面板没有别的数据通路。
- 就算 DSI0 闲置，也该读到版本号而不是 0。

**c. 映射或偏移错误：基本排除**

- 基址、选项、偏移都在本次内核反汇编里确认，与 DT、固件、厂商头文件一致。
- 同一映射类同一启动读到真值，同一写法在 SM7635 上连写都通【真机·他机】。
- 残余风险：DSI 这段地址在 13T 上从未读出过任何非零值，所以无法做正向确认。

**b. 访问控制让读返回 0：第二候选**

- 支持：读 0 且不挂死是过滤型访问的典型表现【推测】。跨 5 天、1.49 s、46.5 s、646.5 s 完全不变。
- 反对：
  - XNU 跑在安卓用的同一个 HLOS 虚拟机里，安卓内核直接映射这段地址。
  - 本机被拒访问的已知结果是同步外部中止：SMEM 页 `ARCHITECTURE.md:419`【真机】；S2CR 写 `ESR=0x96000010`，`:556`【真机·同 SoC】。
  - asteroids、zorn 也在 Gunyah 下，读到真值。
- 判断：Gunyah S2 未映射会中止，这一支基本排除。XPU 读作 0 没有本机先例，但未排除。

**a. 时钟或电源导致读回 0（含 SM8750 硬件投票变体）：第一候选，未证实**

- 支持：
  - 厂商驱动读寄存器空间前先投票，注释写明 `Vote for gdsc required to read register address space`（`dsi_display.c:5833`，只取事实）。
  - DT 有 `iface_clk`、`qcom,sde-reg-bus`，我们一票没投。
  - 差异点落在 SM8750 独有层【推测】：`qcom,sde_cesta`（`running.dts:42167`）、`disp_crm`（`:9136`）、GDSC 的 `qcom,support-hw-trigger`（`:9707-9733`）。
  - 帧走 MDP、byte、pixel 时钟，AHB 只管寄存器口，“屏幕在动、寄存器读不到”本身不矛盾【推测】。
- 反对：
  - 固件连续闪屏退出分支（`sub_21FC0` 的 `a1&1` 分支）只关 DP 时钟，不关 DSI 和 MDP【读码，假定固件链里是同版本 DisplayDxe】。
  - XNU 没有代码碰 disp_cc。
  - 本 SoC 上未开时钟的已知结果是同步外部中止（`ARCHITECTURE.md:472`，未标实测）或挂死，没有读 0 的先例。

**e. 其他：**

- 读取时机不对：已被多个时刻和两轮结果排除。
- 驱动读错：已被反汇编排除。
- 12:15 挂死与 DSI 读 0 并存，说明不同块反应不同，更像逐块的时钟或电源差异，不像一道统一过滤【推测】。但这要以挂死确实发生在 MDSS 内为前提，SMMU 同样可能是挂死点。

## 能分开 a、b 的最小只读探测（未执行）

| 探测 | 判读 |
|---|---|
| `pagani-dpu-probe=4`（现成，不碰 MDSS） | GDSC 状态与 CTL 字；写入类型是否为硬件控制 |
| 新增读 `disp_cc_mdss_ahb_clk` 的 CBCR | 关着支持 a；开着而 DSI 仍读 0 支持 b |
| `pagani-dpu-probe=1`（现成） | FC 增长：MDSS 口活、只有 DSI 暗；同样全 0：整片不可达；挂死：退回 =4 |

按“修完再测”并进下一包。`pagani-gpu-trace=0` 能让启动期面板行留在日志里，但 46 s 的 report 已足够。

复现脚本在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/panel-evidence/scripts/`，其中字节核算是 `sum.py` 和 `acct.py`。

未做项：没有运行 adb 或 fastboot，没有改任何源码、脚本、种子、镜像，也没有提交。