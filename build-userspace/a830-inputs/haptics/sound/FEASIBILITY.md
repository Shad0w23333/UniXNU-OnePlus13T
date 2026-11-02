# 一加 13T（PKX110，SM8750）真实声音可行性研究：扬声器优先，其次麦克风

- 日期：2026-10-31；工作流子任务 key = `sound`。
- 只读调研：没有碰手机，没有改任何活动源码树（驱动仓库、设备仓库、`UniXNU/out-gpu`），没有提交。
- 标注：【事实】都带出处（文件与行号）；【推断】是我的判断；【未知】要取证（第 7 节）。
- GPL 源码（13T 自己的 audio-kernel、OPLUS tfa98xx、SM8850 Pad 3 Pro 转储）只读取事实，没有抄代码；拉下来的副本在 `src/13t/`，来源与校验值见 `sources.txt`。
- 简写：DT = `build-userspace/a830-inputs/dt/running.dts`（手机运行中设备树反编译）；DRV = `UniXNU-Pagani-Drivers/drivers`；SHENG = `参考资料/UniXNU-sheng`（小米平板 6S Pro 12.4，SM8550，同一 iPadOS 21H461，commit 08cde20）。

## 0. 结论速览

1. **可行，而且扬声器这条路大半已经写好。** DRV 里有三块：`PaganiAdsp`（ADSP 启动、GLINK、IPC 路由与服务定位器）、`PaganiSpeaker`（两颗 TFA9873 功放、GENI I2C、GPR/APM 建图、PRM 时钟、SMMU 直通、周期缓冲、接 PaganiAudio 的 `adsp` 后端）、`PaganiAudio`（iOS HAL 插件与内核环形缓冲）。全部照 SHENG 移植，而 SHENG 在 SM8550 上**扬声器出声、麦克风录音、iOS 应用里播放与录音流畅都有真机证据**【事实：SHENG `porting.toml` 的 audio 区 evidence：“Speaker test tone … all six amplifiers PLL-locked”“Playback and recording smooth from the start”】。
2. **13T 上一行都还没在真机跑过。** `PaganiAdsp` 从没开过（`Adreno830/docs/PROGRESS.md` 第 610 行“ADSP 探针，单独一轮”仍在待测）；`PaganiSpeaker` 没接进内核（Stage7Probe 没有 `pagani_speaker_start`，`UniXNU-Pagani_SM8750-gpu/board.toml` 没有这个组件，没有构建脚本和 README）；三个目录在驱动仓库里都是未跟踪文件（`git status` 显示 `?? PaganiAdsp/ PaganiAudio/ PaganiSpeaker/`）。
3. **眼下最大的拦路虎不在 DSP，而在 iOS 路由。** 今天 07:01 的上机日志（`工具/gpu-test-loop/runs/20261031-070114-pkgD-longpress-1/merged.log`）：插件被 audiomxd 加载并打开（`audio: open 1 by audiomxd pid 92`），但路由探针是
   `audio route #1 r1 dev=2 va=63 vad=72 ours=35 vold=1 run=0 vcsp=0/0 vc=0 cat=csav mode=imdf out=0 .... .... ports=0 vol=1000 q=13`，
   插件行 `writes=0`。也就是说在 iPhone16,1 身份下，VirtualAudio 的默认虚拟设备**没有任何活动输出端口**（`out=0`），我们的设备从没跑起来（`run=0`），`vcsp` 读成功且为 0 → SpringBoard 认为当前路由没有音量控件 → 音量条不出【事实：日志；字段含义见 DRV `PaganiAudio/README.md`】。SHENG 是 iPad 身份，Thunderbolt 端口会被选为默认路由；13T 的 iPhone 身份不选【推断】。**不解决这一条，DSP 全通了也只能放内核测试音，iOS 的系统声、键盘声、音乐都出不来。**
4. **建议顺序**：与 iOS 无关的内核侧先走（功放 I2C 探测 → ADSP → APM → 建图 → 内核测试音，M1–M4），同时逆向 iOS 路由（M0，与触感组的 VirtualAudio 逆向是同一块代码）；两边会合后 iOS 出声（M5）。粗估：内核测试音 1–2 周（含上机），iOS 出声再 1–2 周（看 M0），麦克风再 2–4 周，“原生完美”（OPLUS 调音、扬声器保护、iOS 原生内置设备）是月级工程【推断】。

## 1. 13T 音频硬件（全部出自 DT，除非另注）

### 1.1 ADSP 与 GPR

| 项 | 值 | 出处 |
| --- | --- | --- |
| ADSP | `adsp_pas: remoteproc-adsp@03000000`，`qcom,sun-adsp-pas`，固件 `adsp.mdt` + `adsp_dtb.mdt`；cx/mx 投票 `0x180`（384 级）；`qcom,qmp`；中断 wdog/fatal/handover/ready/stop-ack/shutdown-ack；互连 `rproc_ddr`、`crypto_ddr` | DT 13050–13071 |
| 预留内存 | `q6_adsp_dtb_region@9ed80000`（0x80000）；`adspslpi_region@9ee00000`（0x5080000，ADSP 与传感器合并） | DT 557、563 |
| GLINK 边 | `label = "adsp"`，`qcom,glink-label = "lpass"`，remote-pid 2，IPCC mbox `<0x20 0x03 0x00>` | DT 13072–13081 |
| GPR | `audio_gpr: qcom,gpr`，通道 `adsp_apps`，`qcom,intents = <0x200 0x14>`（20 个 512 字节），`qcom,ch-sched-rt`；子节点 `spf_core`（reg 3）、`audio-pkt`（reg 0x17，`apr_audio_svc`）、`q6prm`（reg 7，`qcom,sleep-api-supported = 1`） | DT 13099–13127 |
| ADSP 其他通道 | `IPCRTR`、`PMIC_RTR_ADSP_APPS`、`PMIC_LOGS_ADSP_APPS`、`fastrpcglink-apps-dsp` | DT 13084–13131 |
| 固件位置 | `modem_a` = sde6，起始 38960 扇区、716800 扇区（350 MiB），即 LUN4 第 4870 个 4 KiB 块起 89600 块，FAT 卷里的 `image/adsp*.mdt/.bNN` | `一加13T原厂备份/分区表/分区列表.txt` 第 91 行；DRV `PaganiAdsp/README.md` |
| 动态模块分区 | `dsp_a` = sde9（131072 扇区，64 MiB），安卓挂成 `/vendor/dsp` | 分区列表.txt 第 122 行；挂载点为【推断】 |
| VOOC 快充 PHY | `oplus,adsp-voocphy` **disabled**（开 ADSP 不会顺带跑 OPLUS 快充算法） | DT 33549–33552 |

### 1.2 LPASS 编解码（`spf_core_platform`）

| 块 | 地址 | 要点 | 出处 |
| --- | --- | --- | --- |
| `lpass-cdc` | — | `qcom,lpass-cdc-version = 7`，4 个宏；时钟 `lpass_core_hw_vote`、`lpass_audio_hw_vote` | DT 32289 |
| 时钟资源管理 | — | tx/rx/rx_tx/va/wsa2/wsa2_tx core clk；`qcom,fs-gen-sequence = <0x3000 1 1, 0x3004 3 3, 0x3004 3 1, 0x3080 2 2>` | DT 32300 起 |
| VA 宏 | 0x7660000 | 其下 VA SoundWire 主控 `swrm-io-base 0x7630000`（master id 3，always-on，5 端口，带唤醒中断），挂 **`wcd939x-tx-slave`**（`reg = <0x0e 0x1170223>`） | DT 32308–32347 |
| TX 宏 | 0x6AE0000 | 没有 SoundWire 子节点（TX 从机在 VA 主控上，同 SM8550 的布局） | DT 32349 |
| RX 宏 | 0x6AC0000 | RX SoundWire 主控 0x6AD0000（master id 2，12 端口），挂 `wcd939x-rx-slave`（`<0x0e 0x1170224>`） | DT 32358–32392 |
| WSA 宏 | 0x6B00000 | **disabled**；其下 wsa884x/wsa883x 全部 disabled；声卡 `qcom,wsa-max-devs = 0` | DT 32394 起、32708 起 |
| WSA2 宏 | 0x6AA0000 | ok；WSA2 SoundWire 主控 0x6AB0000（master id 4）上只有 **`swr_haptics@f0170220`（`qcom,pmih010x-swr-haptics`，status ok）**，WSA 喇叭全 disabled | DT 32492–32600 |
| BT SoundWire | 0x6CA0000 | 蓝牙用，不相关 | DT 32681 起 |
| LPI TLMM | 0x7760000，23 脚 | 时钟同上两个投票；TX SWR：clk gpio0、data gpio1/2/14（func1、2 mA、slew 1、数据 bus-hold）；RX SWR：clk gpio3、data gpio4/5 | DT 29212；phandle 0x901–0x904、0x8fb–0x8fd |

### 1.3 编解码器 WCD939x 与麦克风

| 项 | 值 | 出处 |
| --- | --- | --- |
| `wcd939x-codec` | rx/tx 从机如上；复位 `msm_cdc_pinctrl@32` → 主 TLMM **gpio101**（active 输出高、sleep 输出低）；供电 cdc-vdd-rx/tx/buck = **L15B 1.8 V**，cdc-vdd-px = **L2I 1.2 V**，mic-bias = **BOB1**（≥3.008 V）；micbias1–4 = **2700 mV**；`qcom,split-codec` | DT 32602–32705、2083/2098、8149/8199/8593 |
| `wcd_usbss` | WCD939x 的 USB-C 模拟切换，I2C 0x0e @ `qupv3_se3` 0xa8c000（只有 USB-C 模拟耳机才用） | DT 24551 |
| 麦克风 | `qcom,audio-routing` 有 AMIC1–5（Analog Mic1–5，分别接 MIC BIAS1/2/4/3/1）、VA AMIC1–5、TX DMIC0–3；但 `cdc_dmic01/23/45/67_pinctrl` 全 disabled | DT 32742、32882–32925 |
| 推断 | 13T 只用 WCD939x 的模拟麦（AMIC），走 VA 主控上的 SoundWire 进 TX/VA 宏解码器，再经 codec DMA 进 ADSP；哪一路 AMIC 是主麦【未知】 | — |

### 1.4 扬声器功放

| 项 | 值 | 出处 |
| --- | --- | --- |
| 功放 | 两颗 `nxp,tfa98xx`：**0x34**（`tfa_channel 0`，复位 gpio2）、**0x35**（`tfa_channel 1`，复位 gpio164），在 `qupv3_hub_i2c4: i2c@990000`（I2C hub SE4，400 kHz，中断 SPI 468）；供电 `tfa9874_vdd-supply` = L15B；阻抗参数 `tfa_default_mohm 6000`、范围 5300–7500 | DT 25633–25676 |
| 型号 | 容器 `/odm/firmware/tfa98xx.cnt`：5699 字节，OPPO/TFA/9873，sha256 `8574cf32…0a4e`，补丁 `TFA9873N1B0_dummy.patch`（无片上固件） | 2026-10-30 从手机读取，记录在 DRV `PaganiSpeaker/Tfa9873.h` 注释；**本机已无原件**（草稿目录被清空），需重新取 |
| 数据口 | 副 MI2S：`oplus,audio-drv` 的 `oplus,speaker-i2s-id = 1`、DAI `tfa98xx-aif-4-34/35`、`oplus,speaker-vendor = "nxp"`；声卡 `qcom,sec-mi2s-gpios` → `sec_i2s1_pinctrl` → 主 TLMM **gpio121 SCK、gpio123 WS、gpio122 DATA0、gpio124 DATA1**（功能 `i2s1_*`、4 mA；DATA1 的活动态还带下拉） | DT 42646–42651、32728、32940、1553–1680 |
| 格式 | `/odm/etc/backend_conf.xml`：`MI2S-LPAIF-RX-SECONDARY 48000 2 32`；容器设备项：2 个 32 位槽、每帧 64 BCK、I2S 延迟 1、0x34 播槽 0、0x35 播槽 1、48 kHz | 均为 2026-10-30 从手机读取，记录在 DRV `PaganiSpeaker/SpeakerBoard.h`、`Tfa9873.c`；原件需重新取 |
| 不在用的 | WSA884x/883x 全 disabled；安卓开机日志里的 `wsa884x_dlkm`、`oplus_audio_aw882xx`、`oplus_audio_aw87xxx` 只是 OPLUS 通用模块被加载，本机 DT 没有对应启用节点 | `build-userspace/a830-inputs/pkgC-run.log`；DT 检索 |

### 1.5 声卡与时钟

| 项 | 值 | 出处 |
| --- | --- | --- |
| `sun_snd` | `qcom,sun-asoc-snd`，`qcom,model = "sun-mtp-snd-card"`；**`asoc-codec-names = "msm-stub-codec.1", "lpass-cdc", "wcd939x_codec", "swr-haptics"`**；`qcom,mi2s-audio-intf = 1`、`tdm-audio-intf = 0`、`auxpcm-audio-intf = 1`；`qcom,msm-mi2s-master` 全 1（SoC 出时钟）；`qcom,mi2s-clk-attribute` 全 1（COUPLE_NO）；`qcom,mi2s-tdm-is-hw-vote-needed` 全 1；`qcom,dedicated-wsa2`；同时有 `swr-haptics-unsupported` 和 `oplus-swr-haptics-supported`；支持 USB-C 模拟耳机，无 3.5 mm | DT 32708–32760 |
| 13T 内核的后端 | `SEC_MI2S_RX`、`SEC_MI2S_TX`、`TX_CDC_DMA_TX_3/4`、`VA_CDC_DMA_TX_0/1/2`、`SEC_TDM_RX/TX_0` 等 | `src/13t/.../asoc/sun.c` 1228–1380（GPL，只取事实） |
| PRM 时钟号（`qcom,codec-lpass-clk-id`） | va_core 0x307、wsa_core 0x309、wsa2_core 0x310、rx_core 0x30e（22.5792 MHz）、rx_core_tx 0x312、**tx_core 0x30c**、wsa_core_tx 0x314、wsa2_core_tx 0x316、rx_mclk2_2x 0x318、bt_swr 0x31a/0x31b（24.576 MHz），其余 19.2 MHz；`vote_lpass_core_hw` 源 9、`vote_lpass_audio_hw` 源 0xb | DT 42543–42637、32951、32958 |

### 1.6 内存、SMMU、虚拟化

| 项 | 值 | 出处 |
| --- | --- | --- |
| `msm_audio_ion` | `iommus = <apps_smmu 0x1001 0x80>, <apps_smmu 0x1041 0x20>` → SID 0x1001/0x1081/0x1041/0x1061；`qcom,smmu-sid-mask = <0 0xf>`；IOVA 窗 0x10000000 起 | DT 29196 |
| apps SMMU | `apps_smmu: apps-smmu@15000000`，`qcom,qsmmu-v500` | DT 24164 |
| HLOS 内存 | 安卓的 memory 节点含 0xbe360000–0xd4e23000 → **0xD0000000 是 HLOS 内存**（PaganiSpeaker 把 64 KiB 周期缓冲放在这里） | DT 第 21 行 |
| 虚拟化 | Gunyah，HLOS 是 VMID 3 | DT 42655 起 |

## 2. 应用处理器能直接碰什么，什么只能经 ADSP

| 资源 | XNU（与安卓同在 HLOS VM）能否直接访问 | 依据 |
| --- | --- | --- |
| TFA9873 寄存器（I2C hub SE4 0x990000、GCC 时钟、主 TLMM 引脚、L15B） | **能，不需要 ADSP** | 安卓 HLOS 用 `i2c-geni` 驱动这条总线【事实：DT 25633】；同类 I2C hub（SE1，电量计）在 13T 的上机结果还没出（B 包）【未知】 |
| 主 TLMM（gpio2/164 功放复位、121–124 I2S、101 WCD 复位） | 能 | 触摸、按键已在用主 TLMM【事实】 |
| RPMh 稳压器（L15B、L2I、BOB1） | 能（apps RSC DRV-2） | 触摸供电已用同一套【事实】 |
| apps SMMU 里音频 SID 的直通 | 能写 SMR/S2CR（与 UFS、触摸同一个规划器 `SmmuTakeover.h`）；**不能**建转换型上下文 | SHENG：给音频流编程转换型上下文会让整机复位，Hypervisor 保留着那些上下文银行【事实，SM8550】；13T 未验证【未知】 |
| LPASS 编解码宏、SoundWire 主控、LPI TLMM 寄存器 | 物理上映射给了 HLOS（安卓 Linux 直接 ioremap），**但只能在 ADSP 的 PRM 批准 LPASS core/digital codec 硬件投票之后访问**，否则 LPASS 无电无时钟（读写会挂总线或同步异常） | DT 这些节点都挂着两个投票时钟【事实】；13T `audio-ext-clk-up`/`audio_prm` 把投票翻译成 PRM 命令【事实】；SHENG：“宏与数字编解码投票让 LPASS 寄存器可访问”【事实，SM8550】；13T 已知有 SMEM 页对 XNU 同步异常（`Adreno830/docs/ARCHITECTURE.md` 4.1.2）→ 不能假定 HLOS 树里的每个区域都真的映射了【事实】 |
| MI2S/LPAIF 数据搬运、codec DMA、LPASS 时钟树与电源 | **只能经 ADSP** | AudioReach 平台的 HLOS 没有 LPAIF DMA 驱动，时钟与电源都走 PRM【事实：13T audio-kernel 只有 PRM 请求接口】；寄存器级直驱 LPAIF 没有公开资料、还会和 ADSP 抢资源【推断：不可行】 |

**一句话：控制面（功放、引脚、电源，以及投票后的编解码器寄存器）在 AP；数据面（采样搬运、时钟、LPASS 电源）只能经 ADSP。**

## 3. SM8750 上的 ADSP 音频栈（AudioReach / SPF）

### 3.1 传输层

- GLINK 通道 `adsp_apps` 上跑 GPR：24 字节头（版本 0、头长 6 字、总长、目的/源域 ADSP=2/APPS=3、源/目的端口、token、opcode）【事实：DRV `PaganiSpeaker/PaganiGpr.h`；`src/13t/.../include/ipc/gpr-lite.h` 41、57–58】。DSP 给的 intent 小（SHENG 在 SM8550 上看到 24 个 128 字节，申请才给 4 KiB），我们的 `QcomGlink.c` 第 469 行会向 DSP 申请大 intent【事实】。
- 端口：APM 模块实例 1，PRM 模块实例 2；`spf_core` 用源端口 3 发 `APM_CMD_GET_SPF_STATE`（0x01001021），回 0x02001007，状态 1 = 就绪【事实：`src/13t/.../dsp/spf-core.c` 30–34、129】。13T 的 `audio_prm` 从源端口 7（`GPR_SVC_ASM`）发 PRM 命令【事实：`src/13t/.../dsp/audio_prm.c` 183、236】；SHENG 实测从 PRM 自己的端口 2 发才被接受（自己的端口第一次投票被拒，status 1）【事实，SM8550】→ PaganiSpeaker 默认 2，可用 `pagani-speaker-prm-port=7` 切。
- 安卓用户态经 `/dev/aud_pasthru_adsp`（audio-pkt）直接写完整 GPR 包，内核只在 `APM_CMD_SHARED_MEM_MAP_REGIONS` 时把 ION 句柄换成地址【事实：`src/13t/.../ipc/audio-pkt.c` 79、383–440】。
- audio PD：ADSP 的 `audio_pd` 起来前要问 AP 的服务定位器 `avs/audio` → `msm/adsp/audio_pd`（QMI 实例 74）；`PaganiAdsp/AdspQrtr.c` 已实现，表来自 13T modem_a 的 `image/*.jsn`（2026-10-30 读）【事实：代码注释】。

### 3.2 最小播放图（与 DRV `PaganiSpeaker/PaganiReach.c` 一致；SHENG 在 SM8550 上实测可用）

1. 设备子图 0x4005（一个 GC 容器 0x0B001001，栈 8192，域 ADSP，低时延）：`MODULE_ID_I2S_SINK` 0x0700100A（IID 0x6051），参数：
   - `PARAM_ID_I2S_INTF_CFG` 0x08001019 = {lpaif_type 0（LPAIF），intf_idx 1（SECONDARY），sd_line 1=SD0 或 2=SD1，ws_src 1（内部）}；
   - `PARAM_ID_HW_EP_MF_CFG` 0x08001017 = {48000, 32 位, 2 声道, 定点}；`PARAM_ID_FRAME_SIZE_FACTOR` 0x08001018 = 1。
2. 流子图 0x4001：`WR_SHARED_MEM_EP` 0x07001000（0x6001）→ `PCM_DEC` 0x07001005（0x6002，16 位交织）→ `PCM_CNV` 0x07001003（0x6003，32 位 Q31、去交织不打包）→ 连到 0x6051；WR_SHMEM 的 `MEDIA_FORMAT` 0x0800100C。
3. 共享内存：`APM_CMD_SHARED_MEM_MAP_REGIONS` 0x0100100C（池 3、1 段），DSP 收 32 位地址、高位放 SID 低 4 位（0x1001 & 0xf = 1）；每个周期 `DATA_CMD_WR_SH_MEM_EP_DATA_BUFFER_V2` 0x0400100A，回 0x05001004【事实：PaganiReach.h；SHENG 结论】。
4. 顺序：PRM 位时钟 → PREPARE 设备、PREPARE 流 → START 设备、START 流 → 功放上电（此时 BCK 已在，PLL 才能锁）→ 4 个 10 ms 周期在途；停止反序【事实：`SpeakerCore.c` 345–430】。
5. **不需要**：ACDB、AMDB 动态模块、FastRPC、codec DMA、LPASS-CDC 宏寄存器、SoundWire（扬声器走 I2S，不经 WCD/WSA）。

### 3.3 PRM（电源与资源管理）

- 位时钟：`PRM_CMD_REQUEST_HW_RSC` 0x0100100F / `RELEASE` 0x01001010，`PARAM_ID_RSC_AUDIO_HW_CLK` 0x0800102C，`CLOCK_ID_SEC_MI2S_IBIT` 0x102，频率 = 48000 × 2 × 位宽（32 位时 3.072 MHz），属性 COUPLE_NO（1），root 默认 0【事实：`src/13t/.../asoc/msm_common.c` 465–476；`src/13t/.../include/dsp/audio_prm.h` 199、209、324、586】。
- 硬件投票：13T 的 q6prm 带 `qcom,sleep-api-supported`，所以 **LPASS core 投票走 `PARAM_ID_RSC_CPU_LPR` 0x08001A6E**，digital codec 投票走 `PARAM_ID_RSC_HW_CORE` 0x08001032（`HW_CORE_ID_DCODEC` 2）【事实：13T `audio_prm.c` 167–281、`audio_prm.h` 212/217、601/604】。
- MI2S 在 48 kHz 时 13T 内核**不投** audio 硬件票（只有分数采样率或 QUIN 接口才投）【事实：`msm_common.c` 434、478】→ 扬声器路径不需要硬件投票；麦克风路径（要访问宏、SoundWire、LPI 寄存器）需要。SHENG 在 SM8550 上投的是 HW_CORE 1/2 两票；13T 的 core 那票参数号不同，移植麦克风时要改【推断】。

### 3.4 ACDB / 拓扑数据

- **安卓上谁加载**：内核不解析 ACDB。用户态 PAL → AGM → GSL（`libar-gsl`）→ ACDB 软件（`libar-acdb`）读 `acdb_cal.acdb` + `workspaceFileXml.qwsp`（+ 可选 delta），按键值（GKV/CKV，来自 `usecaseKvManager.xml`、`resourcemanager*.xml`）取出子图、模块、连线和标定，经 `/dev/aud_pasthru_adsp` 发 `APM_CMD_GRAPH_OPEN`/`SET_CFG`；动态模块走 AMDB（ACDB 里有 AMDB 注册与开机加载表），ADSP 经 FastRPC 远程文件系统从 HLOS 读 `.so`【事实：`src/audioreach-graphservices/acdb/api/acdb.h` 的命令表 `ACDB_CMD_GET_GRAPH`(0xACDB0001) … `GET_AMDB_BOOTUP_LOAD_MODULES_V2`(0xACDB0020)；`src/13t/.../ipc/audio-pkt.c`】。
- **在手机哪里**：【未知】，见第 7 节 A1/A2。OPLUS 属性 `ro.oplus.audio.odm.para = Pagani_20250507_V16`（`设备信息/系统属性.txt`）提示调音包在 ODM【推断】。
- 上游 Linux 不用 ACDB，用 ALSA 拓扑文件由内核建图（PaganiReach 的字段布局就取自上游 `audioreach.h`）【事实】。
- **最小路径不需要 ACDB**（SHENG 就是手写图出声）。要“原生调音”有现成工具：ACDB 软件是 BSD-3-Clause 开源（AudioReach/audioreach-graphservices），可在 Mac 上离线读手机的 ACDB，导出扬声器、麦克风用例的真实图和标定，顺带直接回答“扬声器数据走 SD0 还是 SD1”（I2S 端点的 `sd_line`）。
- 验证：已下载高通 SM8750 参考 ACDB（`src/audioreach-conf/qcom/qli/sm8750/acdbdata/`），`tools/acdb_chunks.py` 在分块层面完整解析（26 块：HEAD、GKVT、GKVL、SGIT、POOL…）；里面有 codec DMA 端点（`CODEC_DMA_SINK` 26 处、`CODEC_DMA_SOURCE` 20 处、`PARAM_ID_CODEC_DMA_INTF_CFG` 5 处），**没有 I2S**（参考机用 WSA 喇叭）→ 13T 的 I2S 图必须读手机自己的 ACDB【事实】。参考机同样用 WCD939x，它的麦克风图可当 13T 麦克风移植的模板【推断】。`.qwsp` 是加密或压缩的，不是明文 XML【事实】。

### 3.5 Codec DMA 与功放设置

- 扬声器不走 codec DMA（I2S 端点，LPAIF type 0）。
- 麦克风走 codec DMA：`MODULE_ID_CODEC_DMA_SOURCE` 0x07001024 + `PARAM_ID_CODEC_DMA_INTF_CFG` 0x08001063 = {lpaif_type, intf_idx, 通道掩码}；SHENG 用 RXTX(1)/TX3(4)【事实：`SHENG/package/ShengAudioReach.c` 41–47、190–205】；13T 内核有 `TX_CDC_DMA_TX_3/4` 与 `VA_CDC_DMA_TX_0/1/2` 两套后端，实际用哪个看 resourcemanager 配置【未知】。
- 功放：OPLUS 驱动里 TFA9873（REV 低字节 0x73）是 **probus 设备**（`is_probus_device = 1`，DAI 为 TDM，片上无 DSP）【事实：`src/13t/.../tfa98xx_v6.14.2/src/tfa_dsp.c` 397–406】；它的“DSP 消息”交给宿主 DSP（内核 `send_tfa_cal_apr` 是空的弱实现，实际由用户态下发）【事实：SM8850 转储 `tfa98xx.c` 831–840】；OPLUS 机器驱动有扬声器保护模块实例号控件（`oplus_sp_miid`、`oplus_sp_pcm_id`）【事实：13T `sun.c` 2276–2341】→ **安卓上 TFA9873 的 SpeakerBoost 保护跑在 ADSP 图里，并经 SEC MI2S TX（DATA1）回传电流/电压反馈**【推断，把握较高；解析手机 ACDB 可证实】。
- 我们的最小路径：功放旁路（不发 DSP 消息）、关 I/V 回传（`TDMCSE = TDMVSE = 0`，不往 DATA1 上驱动），**没有喇叭保护**，必须靠数字限幅护喇叭（见 4.2、6）。

## 4. 现有代码审阅（只读）

### 4.1 PaganiAdsp（ADSP 启动核心）

- 13 个阶段：映射 SMEM → 读 modem_a 固件 → 勘察 SMEM 分区 → SMP2P → IPCC 门铃 → AOSS QMP `load_state adsp on` → lcx/lmx 代理投票 → PAS 0x24（dtb）→ PAS 1 → auth_and_reset → SMP2P ready → GLINK → 通道打开；运行期服务 SMP2P、GLINK、IPC 路由和服务定位器【事实：`PaganiAdsp/README.md`】。
- 默认关（`pagani-adsp=1`），`pagani-adsp-stop=N` 可在任一阶段停；1–3 阶段只读。
- 局限：没有子系统重启、没有看门狗中断、没有互连带宽投票（安卓投 `rproc_ddr`/`crypto_ddr`，SHENG 不投也起来了）；apps↔ADSP 的 SMEM 分区在 13T 上从没读过【事实：README】。
- 13T 已知 SMEM 第 0x9e000 页对 XNU 同步异常，代码带访问护栏【事实：`QcomSmem.h` 注释】。安卓 Linux 本身就要读写 apps↔ADSP 分区（GLINK 条目 478–480、SMP2P），所以这块大概率映射给了 HLOS【推断】。

### 4.2 PaganiSpeaker（扬声器路径，**任务描述里没提，但是关键**）

- 已写：`SpeakerHw.c`（引脚、I2C hub 时钟与 RCG、GENI 400 kHz、事务）、`Tfa9873.c`（识别、按 REV 0x0a73/0x0b73 写寄存器表、容器的 33 个设备字段、`speaker` 配置、上电/下电/状态程序）、`PaganiGpr.c`/`PaganiReach.c`（GPR 编解码、建图、MEM_MAP、写缓冲、PRM 时钟）、`SpeakerCore.c`（12 个阶段的启动、播放、泵、故障回退）、`SpeakerBackend.c`（PaganiAudio 的 `adsp` 后端）、`SpeakerTone.c`（开机测试音）、`PaganiSpeaker.cpp`（内核线程、窗口映射、L15B 经 DRV-2、SMMU 直通、0xD0000000 写合并映射）。`Test/` 有主机模型测试（core/amp/hw/protocol/tone），但没有运行器。
- **问题清单**：
  1. 没接线：Stage7Probe 没有 `pagani_speaker_start` 钩子；`board.toml` 没有组件；`scripts/` 为空、没有 README 和构建配方【事实】。
  2. 与 PaganiBattery 在 GCC 投票寄存器 0x52008 上读改写竞争（PaganiBattery `README.md` 49–50 行已记录）→ 接线前要共用一把锁【事实】。
  3. SD 线默认 SD0（gpio122）；SHENG 板子实测用 SD1（`ShengAudioReach.c` 182）；13T 哪一根【未知】→ `pagani-speaker-sd=2` 可切，最好用手机 ACDB 定。
  4. 没有限幅：测试音默认衰减 30 dB，但 iOS 满音量时会直接满幅进功放；在没有 SpeakerBoost 的情况下建议加 −6 dBFS 峰值天花板 + 软限幅，长时间满幅测试先别做【推断】。
  5. 周期缓冲 PA 0xD0000000：在安卓 HLOS 内存里【事实】；UEFI/XNU 是否完全不碰，要在 UEFI 内存图核对【未知】（`SpeakerBoard.h` 断言“XNU 窗口从 0x8c0000000 起，从不用它”）。

### 4.3 PaganiAudio（iOS 侧）

- 插件以 Thunderbolt 传输类型出现，一出一入，软件音量（平方 dB 曲线，标量 0 即静音），内核环 65536 帧、周期 480 帧、提前量 14400 帧；后端可经 `pagani_audio_set_backend()` 换成 `adsp`【事实：`PaganiAudio/README.md`、插件源码】。
- 真机（今天 07:01）：开机 2.6 s 服务起来（`backend silent`）、叠加层挂上、31.8 s audiomxd 打开、主机时钟 19.2 MHz 修正生效；**但 52.6 s 的路由行显示没有活动输出端口、设备从未运行**（见 0.3）【事实】。
- 把 PCM 送到 ADSP 的内核部分已经齐了：`SpeakerBackend.c` 在服务线程上把环里的周期取出（`pagani_audio_engine_take`）写进 DSP 槽、DSP 回一个补一个、测提前量、漂移重同步、DSP 卡住重启三次、通道断了退回静音后端【事实：`SpeakerBackend.h/.c`】。**缺的是 iOS 真的往环里写**（M0）。

## 5. 分层计划、里程碑与工作量（工作量与轮数都是【推断】）

| 里程碑 | 内容 | 前置 | 通过标志（日志/现象） | 工作量 | 上机轮数 | 风险 |
| --- | --- | --- | --- | --- | --- | --- |
| **M0 iOS 路由**（与 DSP 并行） | 弄清 iPhone 身份下 VirtualAudio 为什么不给我们的 Thunderbolt 端口任何活动路由（类别 `csav` 下 0 个端口）：逆向 `Device_Thunderbolt_Aspen` 与路由配置（RoutablePorts/AllowedPortTypes/BuiltInPortsForRouteConfiguration…）；候选改法：插件改报内置传输类型并补苹果的端口描述（SHENG 的结论：内置设备需要苹果端口描述），或仿 iPhone 内置设备（`Device_Speaker_Aspen`/`Device_Codec_Aspen`）；诊断对照：同一镜像改回 iPad 身份看路由是否出现 | 无 | `audio route … out≥1 … vc=1`，`audio plug … writes>0`，音量条出现 | 逆向 3–7 人日；若走 BuiltinAudioPlugin（`ASDTIOA2Device` / `IOAudio2DeviceUserClient`）原生路线 2–4 周 | 2–4 | **高** |
| **M4a 功放 I2C 探测**（最早可做，不要 ADSP） | `pagani-speaker=1 pagani-speaker-stop=5`：引脚、L15B、I2C hub SE4、两颗复位、读 REV、写初始化程序并回读校验，功放保持掉电 | PaganiSpeaker 接线 | `[spk]` 行：两颗 REV 低字节 0x73、verify 通过 | 接线 1 人日 + 0.5 人日 | 1–2 | 中低 |
| **M1 ADSP 起来** | `pagani-adsp=1`，`pagani-adsp-stop=3`（只读）→ 5 → 7 → 全程 | 无 | `[adsp]` 到阶段 13；`qrtr hello`；定位器应答 `avs/audio` | 0.5–2 人日 | 1–3 | 中 |
| **M2 GPR + APM 就绪** | 开 `adsp_apps`，`APM_CMD_GET_SPF_STATE` 轮询（PaganiSpeaker 阶段 6–7） | M1、接线 | SPF state = 1 | 0.5 人日 | 1 | 中低（audio PD） |
| **M3 打开图** | 阶段 8–10：4 个音频 SID 直通、映射 0xD0000000、两个子图 OPEN + SET_CFG + MEM_MAP | M2 | MEM_MAP_DONE 拿到句柄，所有 basic result status 0 | 1–3 人日 | 2–4 | 中（SMMU、参数被拒） |
| **M4 内核测试音** | `pagani-audio-tone=1`：PRM 时钟 → START → 功放上电 → 440 Hz、−30 dB、500 ms | M3、M4a | 用户听到；`amp … state 9 clks 1 plls 1`；sent≈played | 2–5 人日 | 3–6 | 中（SD 线、TDM 格式、爆音） |
| **M5 iOS 出声** | 把 `adsp` 后端交给 PaganiAudio；校提前量；加数字天花板 | M0、M4 | 系统声、键盘声、音乐有声；`audio svc` 的 taken/played 增长、late≈0 | 1–3 人日 | 1–3 | 中 |
| **M6 音量/静音/路由** | 音量曲线、渐入渐出防爆音、闲置 5 s 后功放下电与图停止、听筒（只开顶部 0x34）、横屏左右声道 | M5 | 主观听感、无爆音 | 2–5 人日 | 2–3 | 低中 |
| **M7 麦克风** | PRM 投票（CPU_LPR + DCODEC）与 TX/VA 核心时钟（0x30c/0x307，19.2 MHz）→ LPI 引脚（gpio0/1/2/14）→ VA 宏 fs-gen（DT 序列）→ VA SoundWire 主控 0x7630000 枚举 WCD939x TX 从机 → WCD939x 上电（L15B、L2I、BOB；gpio101 复位）、micbias 2.7 V、AMIC→ADC → TX/VA 解码器 → codec DMA TX 图（CODEC_DMA_SOURCE → PCM_CNV → PCM_ENC → RD_SHMEM 0x07001001）→ PaganiAudio 采集环 | M3 | 语音备忘录录到声音；周期全回、非零样本比例高 | 10–20 人日 | 6–12 | **高**（WCD939x ≠ SHENG 的 WCD938x；lpass-cdc v7 偏移；主麦映射未知） |
| **M8 原生完美**（可选） | ① 离线解析手机 ACDB，回放 OPLUS 的扬声器/麦克风图与标定（EQ、MBDRC、Fluence 降噪）；② 若保护模块是动态模块，要 FastRPC + 远程文件系统；③ SpeakerBoost 与 I/V 反馈（SEC MI2S TX）；④ iOS 原生内置设备（BuiltinAudioPlugin 的 IOAudio2 协议或 VirtualAudio 的 Speaker/Codec/Actuator 设备） | M5、M7 | — | 1–3 个月 | 多 | 高 |

日历粗估：内核测试音（M4a、M1–M4）约 1–2 周；iOS 出声再 1–2 周，主要看 M0；麦克风再 2–4 周；M8 月级。

## 6. 最危险的未知数（按风险排序）与化解办法

1. **iPhone 身份下 iOS 不路由到我们的设备**（今天的真机证据为负）。办法：逆向 VirtualAudio 的路由选择（触感组已把 `VirtualAudio`、`MediaExperience`、`audiomxd` 等抽到 `haptics/ios/bin/`）；做插件变体（内置传输类型 + 端口描述）；iPad 身份对照。这一项也决定触感能不能走“执行器”音频设备（第 8 节）。
2. **ADSP 在 Gunyah 下由 XNU 首次启动**（13T 从未跑过；SMEM 有会异常的页；PAS 认证约 38 MiB 镜像；IPCC 中断或轮询）。办法：分阶段停；第一轮只到阶段 3（只读）。
3. **音频 SID 直通能否被 Gunyah 接受、周期缓冲地址是否安全**。办法：看阶段 8–9 日志；核对 UEFI 内存图里 0xD0000000 的类型与占用。
4. **I2S 数据线（SD0/SD1）与 TDM 格式**。办法：解析手机 ACDB 里 `PARAM_ID_I2S_INTF_CFG` 的 `sd_line`；不行就 SD0、SD1 各一轮试听；位宽已知 32（backend_conf 与容器一致）。
5. **喇叭安全**：没有 SpeakerBoost 就没有偏移/温度保护。办法：数字天花板 + 软限幅、短时低电平测试、状态行盯 TFA 的温度与 OCP/UVP 位。
6. **麦克风链**：WCD939x 寄存器序列、lpass-cdc v7、SoundWire 主控版本、AMIC→ADC→解码器→codec DMA 的映射。办法：先取 mixer_paths/resourcemanager 与 ACDB 定映射；寄存器事实从 13T 的 GPL 源码（`wcd939x`、`lpass-cdc`、`swr-mstr-ctrl`）只取事实；高通 SM8750 参考 ACDB 的麦克风图作模板。
7. **开 ADSP 的副作用**：电池管理器（charger PD）、传感器 PD 跟着起来（要和 PaganiBattery 协调）；没有子系统重启，ADSP 崩了音频和电池管理都要等重启。
8. **共享资源竞争**：GCC 0x52008（与 PaganiBattery）、DRV-2（与触摸、GPU）、SCM/QMP（与 GPU）。PaganiAdsp 已有锁字；PaganiSpeaker 的 GCC 读改写还没加锁。
9. **时延与功耗**：4 × 10 ms 在途 + DSP 提前量（SHENG 测 1600–2000 帧，33–42 ms）→ 总时延约 60–80 ms【推断】，键盘声会略迟；可降到 2 × 5 ms。闲置时要停图、放时钟、功放掉电。

## 7. 手机上可以只读取证的内容（adb + su）

命令都在 `phone-readonly-checklist.sh`（**没有执行过**；只能在用户同意、手机空闲、没有测试在跑时运行；手机上不写任何东西，结果只落在本目录 `phone/`，专有数据只放本地）。

| 编号 | 读什么 | 回答哪个未知 |
| --- | --- | --- |
| A1/A2 | `find /vendor /odm /system_ext` 找 `*.acdb`、`*.qwsp`、`backend_conf*.xml`、`resourcemanager*.xml`、`mixer_paths*.xml`、`usecaseKvManager*.xml`、`card-defs*.xml`、`kvh2xml*.xml`、`audio_policy_configuration*.xml`、`*tfa*.cnt` 并拉回；列 `acdbdata` 目录 | ACDB 位置与版本；扬声器 SD 线（经 ACDB 解析）；麦克风 AMIC→ADC→解码器→codec DMA 映射；SpeakerBoost 模块与反馈后端 |
| A3 | `tfa98xx.cnt` 的大小与 sha256 | 与 PaganiSpeaker 写死的容器字段是否同一份（期望 5699 字节、`8574cf32…0a4e`） |
| A4 | `/vendor/lib/rfsa/adsp`、`/odm/lib/rfsa/adsp`、`/vendor/dsp` 列表 | 保护/音效是否为动态模块（决定 M8 要不要 FastRPC） |
| A5 | `/vendor/firmware_mnt/image` 里 `adsp*`、`*.jsn` 与校验值 | PaganiAdsp 读固件的期望是否成立 |
| A6 | `/proc/asound/cards`、`/proc/asound/pcm` | 声卡名、是否有触感 PCM |
| B1 | `dmesg` 中 tfa98/adsp/remoteproc/gpr/spf/prm/audio_pkt/lpass/swr/wcd939/haptic 行 | TFA REV（0x0a73/0x0b73/0x1a73）、ADSP 加载耗时、WCD939x 枚举 ID、“oplus swr haptics support”是否打印 |
| B2 | `/sys/class/remoteproc/*` 名称、状态、固件 | ADSP 固件名与状态 |
| B3 | `/proc/iomem` | 0xD0000000 是否为 System RAM |
| B4 | `/sys/kernel/iommu_groups/*/devices` | 安卓是否给音频建了转换型上下文（对照 SHENG 的“只能直通”） |
| B5 | `getprop` 里 audio/acdb/agm/pal/haptic 相关 | 用例键值、OPLUS 开关 |
| B6/B7 | 有没有 tinymix/agmmixer；debugfs 是否已挂（只看不挂） | 第 C 节能否执行 |
| C1–C5 | 用户在安卓放铃声、再录音时：引脚复用（gpio2/101/121–124/164）、TFA 的 debugfs、tinymix 全表、录音时的 dmesg | 副 MI2S 引脚确实在用；TFA 实际 TDM 设置；录音时的 ADC/解码器/codec DMA 路由 |
| D（默认不执行） | 打开 gpr/audio_pkt 的 dynamic_debug | 只能拿到 opcode/端口/长度；**不是只读**，要用户单独同意；推荐改用 ACDB 离线解析 |

已经从手机读过、但本机原件已丢的：`/odm/firmware/tfa98xx.cnt`、`/odm/etc/backend_conf.xml`、modem_a 的 `image/*.jsn`（2026-10-30，见 PaganiSpeaker/PaganiAdsp 注释）。下次取证顺带重新拉回、存本目录 `phone/`。

## 8. 与震动（用户第一优先）有关的交叉发现

- DT 的声卡同时有 `swr-haptics-unsupported` 和 `oplus-swr-haptics-supported`。13T `sun.c` 2029–2060：前者只是不加高通自带的 swr-haptics 后端；后者（OPLUS 扩展）**仍把 WSA2 codec DMA 后端加进声卡**。再加上 WSA2 SoundWire 主控上 `swr_haptics@f0170220` 是 status ok、音频路由有 `HAP_IN → WSA2_HAPT OUT`【事实】→ 安卓上 OPLUS 很可能把部分触感波形当音频流经 ADSP → WSA2 codec DMA → WSA2 宏 → SoundWire → PMIH010x 触感播放【推断，取证：B1 的 dmesg、A6 的 PCM 列表】。所以“SoundWire 触感不支持”只对高通默认路径成立。对 XNU：SPMI FIFO 直驱（hw 组方案）不依赖 ADSP，仍是首选；SoundWire 路线要整套音频栈（M1–M3 加 WSA2 宏与 SoundWire），不建议先做。
- iOS 侧：VirtualAudio 里有 `Device_Actuator_Aspen`、`Device_HapticDebug_Aspen`、`Device_Speaker_Aspen`、`Device_Codec_Aspen`（`haptics/ios/strings/VirtualAudio.txt`）——iPhone 的 Taptic Engine 是作为音频“执行器”设备进 VirtualAudio 的。M0 的路由逆向和触感组的 iOS 逆向是同一块代码，建议合并做。

## 9. 本目录文件

| 路径 | 内容 |
| --- | --- |
| `FEASIBILITY.md` | 本文 |
| `phone-readonly-checklist.sh` | 手机只读取证命令（未执行） |
| `tools/acdb_chunks.py` | ACDB 分块粗看工具（`python3 -I tools/acdb_chunks.py FILE`） |
| `src/13t/` | 13T 自己的 audio-kernel（`sun.c`、`msm_common.c`、`audio_prm.c/.h`）与 OPLUS `tfa_dsp.c` 副本（GPL，只取事实） |
| `src/audioreach-graphservices/` | ACDB 软件接口头 `acdb.h`、LICENSE、README（BSD-3-Clause） |
| `src/audioreach-conf/` | 高通 SM8750 参考 ACDB、工作区文件、card-defs、`kvh2xml.h`（BSD-3-Clause，只放本地） |
| `sources.txt` | 上述副本的 URL 与 sha256 前缀 |
