# 一加 13T（PKX110，SM8750 sun/pagani）硬件全盘点

- 日期：2026-10-31；工作流子任务 key = `hardware`。只读调研：没碰手机，没改任何源码树，没有提交。
- 证据等级：【DT】= 运行中设备树反编译 `build-userspace/a830-inputs/dt/running.dts`（行号）；【属性】= `一加13T原厂备份/设备信息/系统属性.txt`；
  【分区】= `一加13T原厂备份/分区表/分区列表.txt`（单位 512 字节扇区，UFS 实际 4 KiB 块 = 扇区 / 8）；【VB】= `vendor_boot_a.img`
  的 vendor ramdisk（第一阶段内核模块名与 `fstab.qcom`，解包在草稿目录，只取名字，不入库）；【日志】= `工具/gpu-test-loop/runs/*/merged.log`；
  【推断】= 我的判断；【未知】= 需要在安卓下只读导出（第 5 节），今晚不碰手机。
- GPL 源码（Linux、KGSL、安卓）与安卓模块只作事实；Apple 与厂商固件只留本地。
- 并行工作只引用不重复：马达 `UniXNU-Pagani-Drivers/drivers/PaganiHaptics` 与 `a830-inputs/haptics/`；声音可行性
  `a830-inputs/haptics/sound/FEASIBILITY.md`（音频硬件细节以它为准）；Find My、音量键路由、小组件、壁纸各自目录。

## 0. 结论速览

1. **传感器全部在 ADSP 的 SSC（传感器 PD `msm/adsp/sensor_pd`）后面**：apps 侧 QUP 上没有任何加速度/陀螺/磁力/光感/接近芯片节点，
   所有 I3C 主控都 disabled。芯片型号设备树里没有（只在 `/vendor/etc/sensors/config/*.json` 与 persist 注册表里）。所以所有传感器、自动亮度、
   原彩、抬起唤醒、计步都**卡在 ADSP 启动 + SSC 客户端协议 + 一个给 DSP 读文件的 FastRPC 服务**上：多周工作（C）。
2. **ADSP 是总闸**：电池管理器（充电、PD、UCSI/USB 角色）、扬声器/麦克风、传感器都要它；`PaganiAdsp` 已写好但**从未在手机上跑过**
   （所有运行日志里没有一行 `[adsp]`；最新包启动参数没有 `pagani-adsp`）。
3. **今晚能直接落地的硬件（A）只有电量计**：NFG8011B 在 I2C hub SE1 `0x55`，`PaganiBattery` 已接线、B 包镜像已打好（待一轮上机）。
4. **代码可写、要上机调的（B）**：马达（PMIH010x HAP530，并行组在做）、手电筒的硬件侧（PM8550 闪光灯模块，SPMI 写，与马达同一套写闸门）、
   扬声器内核测试音（ADSP + TFA9873，声音组）、温度上报、RTC 读时间。
5. **多周（C）**：Wi‑Fi（peach PCIe `17cb:110e`，上游 ath12k 不认）、传感器、USB 设备模式（DWC3 只到 HS）、iOS 出声与麦克风、相机原始取流、
   GNSS（在基带里）、持久存储。
6. **按 iOS 原生功能看基本做不到（D）**：Touch ID（屏下光学指纹，SPI 归 TZ，iOS 要 SEP）、Face ID、Apple Pay/NFC 支付（SEP + 苹果安全元件）、
   iOS 原生蜂窝/通话（CommCenter 要苹果基带协议与激活）、红外遥控（iOS 无此 API）、UWB（本机没有）。

## 1. 总线与控制器总览

### 1.1 QUP（GENI）串行引擎：只列 status okay 的及其子设备

| 引擎 | 地址 | 协议 | 引脚 | 子设备 | 我方驱动 |
| --- | --- | --- | --- | --- | --- |
| QUP1 SE0 | `0xa80000` | I2C 400 kHz | gpio32 SDA / gpio33 SCL（`qup1_se0`） | `nq@28` `qcom,sn-nci`：NXP **SN220T** NFC（DT 24373） | 无 |
| QUP1 SE3 | `0xa8c000` | I2C | gpio44/45 | `wcd939x_i2c@e` `qcom,wcd939x-i2c`：WCD939x 的 USB‑C 模拟音频切换（USBSS）（DT 24551） | 无 |
| QUP1 SE4 | `0xa90000` | SPI 19 MHz | gpio48–51，复位 gpio161，ATTN gpio162 | `synaptics_tcm_hbp@0` S3910 触摸（DT 24759） | `PaganiInput`（真机已验证） |
| QUP1 SE5 | `0xa94000` | SPI（只用 MOSI gpio53），5 MHz | gpio53 | `consumerIr@0` `oplus,kookong_ir_spi` 红外发射（DT 24864） | 无 |
| QUP1 SE7 | `0xa9c000` | 调试 UART（`serial0`，115200） | gpio62 TX / gpio63 RX | 安卓用 `qcom_geni_serial.con_enabled=0` 关了控制台 | 无（需测试点） |
| QUP2 SE1（DT 标签 se9） | `0x884000` | I2C 1 MHz | gpio4/5 | 前摄 `cam-sensor3@0x20`、`eeprom3@0x54`、`actuator3`（DT 25035） | 无 |
| QUP2 SE6（标签 se14） | `0x898000` | 4 线 HS UART | gpio24 CTS / 25 RTS / 26 TX / 27 RX（RX 兼唤醒，GIC SPI 461 + TLMM gpio27） | 蓝牙（peach BT） | 无 |
| I2C hub SE1 | `0x984000` | I2C 100 kHz | gpio66/67（`i2chub0_se1`） | `bq27541-battery@55` = **NFG8011B** 电量计（`oplus,batt_nfg8011b`）（DT 25560） | `PaganiBattery`（已接线，未上机） |
| I2C hub SE3 | `0x98c000` | I2C 400 kHz | gpio70/71 | `sc8547-charger-slave@6A` 副电荷泵 SC8547D，IRQ gpio171（DT 25611） | 无 |
| I2C hub SE4 | `0x990000` | I2C 400 kHz | gpio72/73 | `tfa98xx@34`（复位 gpio2）、`tfa98xx@35`（复位 gpio164），TFA9873 功放，供电 L15B（DT 25651/25664） | `PaganiSpeaker`（未接线） |
| I2C hub SE8 | `0x9a0000` | I2C 400 kHz | gpio206/207 | `oplus,sc8547d@6A` 主电荷泵 SC8547D，IRQ gpio163（DT 25750） | 无 |

- 其余 QUP SE（含所有 `qcom,geni-i3c` 主控）全部 disabled。**apps 侧没有任何传感器芯片**【DT】。
- TLMM `pinctrl@f000000`（`qcom,sun-tlmm`，引脚寄存器从 `0xf100000` 起每脚 0x1000）`qcom,gpios-reserved = <36 37 38 39 74>`：
  这 5 脚归安全世界，【推断】36–39 是指纹的安全 SPI，74 可能是指纹/eSE 中断（DT 6688 起）。

### 1.2 SPMI PMIC（仲裁器 v7 `0xc42d000`；core `0xc400000`、chnls `0xc500000`、observer `0xc440000`、intr `0xc4c0000`；第二条总线 `0xc432000` disabled）

| SID | 型号（DT 名） | 关键外设 | 用途 |
| --- | --- | --- | --- |
| 0 | PMK8550 | `pon_hlos@1300`（PWRKEY irq 7 = KEY_POWER，RESIN irq 6 = KEY_VOLUMEUP）、`rtc@6100`/`0x6200`、`vadc@9000`（ADC5 gen3，大量热敏电阻）、SDAM `0x7000…0xb600`、GPIO `0xb800`、PWM | 电源/音量加键、RTC、ADC、复位记录 |
| 1 | PM8550 | GPIO `0x8800`（**gpio6 = 音量减**，gpio11 = eSIM 检测）、`qcom,flash_led@ee00`（`qcom,qti-pm8350c-flash-led`）、LPG 红绿蓝通道、BCL `0x4700`、温度告警 | 音量减、**闪光灯/手电筒** |
| 3 / 5 / 6 / 8 | PM8550VE（D/F/G/I） | GPIO；**F（SID5）gpio3 = BT_EN/复位，gpio6 = BT FMD 时钟，gpio7 = 面板电源 gpio1**；I（SID8）gpio5 = 主摄复位 | 稳压器、杂项 GPIO |
| 9 | PM8550VS（J） | GPIO、温度 | 稳压器 |
| 4 | PMD802x | `amoled-ecm@f900`；`qcom-amoled`（IBB/ELVDD/OLEDB）**disabled** | OLED 电源监测 |
| 7 | **PMIH010x** | `qcom,hv-haptics@f000`（`0xf000/0xf100/0xf200`，irq `fifo-empty` = SID7 PID 0xf0 irq1）、`eusb2-repeater@fd00`、BCL `0x4700`、GPIO（gpio10 = eSIM 使能） | **马达、充电器 PMIC、eUSB2 中继** |
| 0xc / 0xd | PM8010（M/N） | LDO（`ldom1..7` 经 RPMh） | 相机供电 |
| 0xa | PMR735D | GPIO、温度 | 射频 |

- PaganiInput 读 PON（PPID `0x013`）与 PM8550 GPIO6（PPID `0x18d`）走 observer 只读通道；马达草稿 `HapticsSpmi.c` 对 SID7 两个 APID 走 EE0 读写通道并有白名单【代码】。
  闪光灯（SID1 `0xEE00`）要同一套“EE0 归属检查 + 白名单写”。

### 1.3 中断控制与 IPC

- GICv3 `0x16000000`；PDC（唤醒）`interrupt-controller@b220000`（phandle 0x25，TLMM 的 `wakeup-parent`）；PCIe 专用 PDC `pdc@b360000`（`qcom,sun-pcie-pdc`）；IPCC `0x406000`（mbox：ADSP 3、MPSS 2、CDSP 6、SPSS 16、APSS 自 ping 8）。
- SMEM `0x81d00000`（2 MiB），cmd-db `0x81c60000`，AOSS QMP `power-controller@c300000`，TCSR mutex `syscon@1f40000`。
- SMP2P：`qcom,smp2p-adsp`、`-cdsp`、`-modem`、`-soccp`，`qcom,smp2p_sleepstate`（告诉 ADSP 睡眠状态，传感器用）。

## 2. 各硬件块

### 2.1 远程处理器（全部 PAS，固件经 TZ 认证）

| 处理器 | 节点 | 固件名（DT） | 保留内存 | GLINK / 通道 | 用途 |
| --- | --- | --- | --- | --- | --- |
| ADSP（含原 SLPI/SSC） | `remoteproc-adsp@03000000` `qcom,sun-adsp-pas`（DT 13050） | `adsp.mdt` + `adsp_dtb.mdt` | `adspslpi_region@9ee00000`（0x5080000）、`q6_adsp_dtb_region@9ed80000`（0x80000）、`adsp_mhi_region@82380000` | 边 `lpass`，remote-pid 2：`IPCRTR`、`PMIC_RTR_ADSP_APPS`、`PMIC_LOGS_ADSP_APPS`、`adsp_apps`（GPR）、`fastrpcglink-apps-dsp`（6 个 compute CB，SID `0x1003..0x1008`） | 音频、充电/电池管理/UCSI、传感器 |
| CDSP（NSP） | `remoteproc-cdsp@32300000` `qcom,sun-cdsp-pas` | `cdsp.mdt` + `cdsp_dtb.mdt` | `cdsp_region@9d200000`（0x1900000）、`q6_cdsp_dtb_region@9eb00000` | 边 `cdsp`，pid 5：`IPCRTR`、`cdsprmglink-apps-dsp`、FastRPC | AI/相机后处理；iOS 不需要 |
| MPSS（基带） | `remoteproc-mss@04080000` `qcom,sun-modem-pas` | `modem.mdt` + `modem_dtb.mdt` | `mpss_region@8ba00000`（0xf600000 = 246 MiB）、`q6_mpss_dtb_region@9b000000`、`dsm_partition_1/2`（`0x84a00000`/`0x89300000`）、`ipa_fw`/`ipa_gsi` | 边 `mpss`，pid 1：`IPCRTR`、`DS`（AT 口 `at_mdm0`） | 蜂窝、SIM、**GNSS**（`gps/gps_service` 在 modem root PD） |
| SoCCP | `remoteproc-soccp@a3380000` `qcom,sun-soccp-pas` | DT 没写固件名 | `soccp_region@9ec00000`（0x180000）、`soccp_sdi_region@823a0000` | SMP2P 状态 stop/wakeup/sleep，`pong` 中断 | 【未知】SM8750 新增的 SoC 控制处理器；是否已由 XBL/UEFI 拉起、ADSP 是否依赖它，没查到；我方代码里没有任何 SoCCP 记录 |
| SPSS（安全处理器） | `remoteproc-spss@1880000` `qcom,sun-spss-pas` | `spss1p.mdt`（量产）/`spss1d`/`spss1t` | `spss_region_region@9b0a0000` | `spcom`：`sp_kernel`、`sp_ssr` | 安卓密钥/弱 SE；iOS 不用 |

- 固件位置：`modem_a`（sde6，起始 38960 扇区、716800 扇区 = 350 MiB；即 LUN4 第 4870 个 4 KiB 块起 89600 块），vfat，安卓挂
  `/vendor/firmware_mnt`（【VB】fstab：`ro,shortname=lower`）。`image/` 下有 `adsp*.mdt/.bNN`、`cdsp*`、`modem*`、pd-mapper 描述
  `adspr.jsn`、`adsps.jsn`（传感器 PD）、`adspua.jsn`、`battmgr.jsn`、`cdspr.jsn`、`modemr.jsn`（PaganiAdsp `AdspQrtr.c` 注释：2026-10-30 读过，QMI 实例 ADSP 74、CDSP 76、modem 180）。
- 安卓驱动名【VB】：`qcom_q6v5_pas`、`qcom_q6v5`、`rproc_qcom_common`、`qcom_glink_smem`、`qcom_glink`、`qrtr`、`qrtr-smd`、`smp2p`、`smp2p_sleepstate`、`pdr_interface`、`glink_pkt`。
- 我方：`PaganiAdsp`（SMEM、MDT、PAS、SMP2P、GLINK、QRTR、服务定位器，`pagani-adsp=1` 才跑，默认关）。**真机从未运行**：全部运行日志无 `[adsp]` 行；最新包（`runs/20261031-070836-pkgD2-widgets-wallpaper-1`）`-v` 行无 `pagani-adsp`。
  PROGRESS 待测清单第 11 项“ADSP 探针，单独一轮”。CDSP/MPSS/SoCCP/SPSS：无代码。

### 2.2 运动与环境传感器（加速度、陀螺、磁力计、气压、计步、霍尔）

- **位置**：全部挂在 ADSP 岛内的 SSC 上（`adspslpi_region` 名字即“ADSP + SLPI 合并”；pd-mapper 有 `adsps.jsn` → `msm/adsp/sensor_pd`）。
  ADSP 的 QRTR 节点声明 `qcom,no-wake-svc = <0x190>`（服务 400 = SSC 客户端服务 `SNS_CLIENT`）【DT 13087】。apps 侧没有任何传感器节点【DT】。
- **芯片型号【未知】**：HLOS 设备树只有 `oplus_sensor`（`oplus,sensor-devinfo`）下一个虚拟 `mag_fusion`（`sensor-type 6`、`is-virtual-sensor`）【DT 34424】，
  不说明物理磁力计是否存在。公开规格库普遍列出加速度、陀螺、电子罗盘、接近、环境光，没有列气压计；以 `dumpsys sensorservice` 为准（第 5 节）。
- 霍尔：没有翻盖霍尔。名为 `gpio-hall-sensor` 的节点其实是 **Plus 键**（`oplus-magic-buttom`，TLMM gpio97，`linux,code 0x2df`）【DT 33531】。
- 配置与注册表：`/vendor/etc/sensors/config/*.json`（vendor 在 super 里，erofs/ext4【VB fstab】）、`/mnt/vendor/persist/sensors/registry`（persist = sda2，ext4）。
  DSP 通过 apps 侧的 FastRPC 文件服务（安卓守护进程 `sscrpcd`/`adsprpcd`）读这些文件【推断，常规高通做法】。
- 安卓：传感器 HAL（服务名被 SELinux 挡住没出现在属性里）、`oplus_sensor_deviceinfo`、`oplus_sensor_feedback`（`sensor_fb`：smem id 0x260）、
  `oplus_sensor_interact`（`ssc_interactive`）、守护进程 `oplus_sensor_fb`【属性】。
- iOS 消费者：传感器协处理器框架 `AppleSPU`（iPad 根卷有 `AppleSPU.kext/PlugIns/AppleSPULib.plugin`、`FastpathLib.plugin`）；
  `locationd`（CoreMotion、计步 `CMPedometer`）、`backboardd`（方向、抬起）。iPhone 15 Pro 的运动数据全部来自 AOP/SPU 的 HID 服务。
- 我方：无。
- 需要：① PaganiAdsp 上机跑通到 GLINK/QRTR；② 传感器 PD 起来（服务定位器已有 `msm/adsp/sensor_pd` 表项）；③ apps 侧 FastRPC 文件服务
  （给 DSP 读 json/注册表；需要 ext4 只读读取 persist、erofs/LP 读 super 里的 vendor，或把这些文件放进 Data 种子）；④ SSC 客户端：
  QMI 服务 400 上的 protobuf 消息（SUID 查询、attributes、enable/stream）；⑤ 内核里按 iOS 期待的 HID 事件服务发布加速度/陀螺/磁力/计步。
- 可行性：**C**（多周；全部依赖 ADSP，今晚只能写不能验）。

### 2.3 环境光（ALS）与接近（屏幕附近）

- 屏下环境光：`ro.display.underscreen.lightsensor.support=1`、`ro.oplus.lcd.display.screen.underlightsensor.region=716,89,788,150`
  （屏幕像素坐标，前摄挖孔右侧）、`ro.display.underscreen.lightsensor.screenshot.period=50`、`ro.vendor.oplus.sensor.high_pwm_rgb=true`
  （RGB 色光传感器，适配高频 PWM 调光）、`ro.vendor.oplus.sensor.brl_receive=true`【属性】。
- 屏下光感要扣除屏幕自身发光：`ssc_interactive` 有 `receive_screenshot_info`、`need_lb_algo`、`sup-hbm-mode`、`report_blank_mode`、
  `primary_lb_brl_info`【DT 34410】——安卓把光感区域的画面内容和亮度持续送给 SSC。**不送这些数据，亮屏时读数会被屏幕光污染**【推断】。
- 接近：`persist.sys.sensor.disable.fusion_phone_prox=false`（OPLUS 的“通话融合接近”）；物理接近芯片型号与位置【未知】。
- 同样全部在 SSC 后面；芯片型号【未知】。
- iOS 消费者：`backboardd`/CoreBrightness（自动亮度、原彩）、通话贴脸熄屏（接近），都来自 SPU HID 服务。
- 我方：亮度控制有（`PaganiPanel`：DCS 0x51，最大 0xdbb），没有光感输入。
- 可行性：**C**（在 2.2 的基础上，再加屏幕内容补偿；不补偿只能在暗屏/熄屏时给大致值）。

### 2.4 按键（电源、音量、Plus 键）

| 键 | 硬件 | 安卓 | 我方 |
| --- | --- | --- | --- |
| 电源 | PMK8550 PON `0x1300` PWRKEY，SPMI irq SID0 PID 0x13 irq 7，`KEY_POWER` | `qcom-pon`（`qcom,pmk8350-pwrkey`） | PaganiInput（PON bit7），真机已验证短按/长按 |
| 音量加 | PMK8550 RESIN，irq 6，`KEY_VOLUMEUP`（0x73） | `qcom,pmk8350-resin` | PaganiInput（PON bit6） |
| 音量减 | PM8550 GPIO6（PPID 0x18d，上拉，低有效），`gpio-keys`，`KEY_VOLUMEDOWN`，可唤醒 | `gpio_keys` | PaganiInput（PMIC GPIO） |
| Plus 键 | TLMM gpio97，低有效，外部上拉，`BTN_TRIGGER_HAPPY32`（0x2df），可唤醒 | `gpio-hall-sensor`/`oplus-magic-buttom` | PaganiInput：当静音开关用，用户确认“左侧 Plus 键正常” |

- 现状：硬件侧全部可用；“音量条不出”是 iOS 音频路由问题（`audio route … out=0 … run=0`【日志 `runs/20261031-070114-pkgD-longpress-1`】），由音量键路由组处理。
  Plus 键改为操作按钮是 iOS 侧工作（PROGRESS 第 10 项）。
- 可行性：硬件 **已完成**；按键均为轮询（8 ms），GIC/PDC 中断唤醒未做（与休眠 2.17/2.19 相关）。

### 2.5 马达（线性马达）

- PMIH010x（SID7）HAP530_HV：`qcom,hv-haptics@f000`，`reg = <0xf000 0xf100 0xf200>`（CFG/PATTERN/HBOOST），`fifo-empty` 中断；
  `qcom,vmax-mv = 1260`、`qcom,lra-period-us = 5882`（170 Hz）、`qcom,fifo-vmax-mv = 7450`、`qcom,brake-pattern = [ff 3f 1f]`、
  `qcom,vibrator-type = 0x329`（“0809” X 轴线性马达）、`qcom,rt-imp-detect`、`oplus,livetap_support`、6 个 effect + 9 个 primitive【DT 12115】；
  `ro.oplus.haptic.lra_*` 160–180 Hz 档位【属性】。另有 `swr_haptics@f0170220`（`qcom,pmih010x-swr-haptics`）挂在 WSA2 SoundWire 上（音频触感通路）【DT 32560】。
- 安卓：`qcom-hv-haptics`、`oplus_haptic`、`haptic_feedback`、`leds-qpnp-vibrator-ldo`【VB】。
- 我方：`PaganiHaptics`（并行组，主机测试通过、未接线、未上机），Stage7Probe 已有 `pagani_haptics_start` 弱钩子。
- 可行性：**B**（不需要 ADSP；SPMI 写 + 安全闸门已写，需上机调）。

### 2.6 音频（扬声器、听筒、麦克风）——细节以声音组 `FEASIBILITY.md` 为准

- 扬声器：两颗 TFA9873（I2C hub SE4 `0x34` 上/`0x35` 下），数据走副 MI2S（TLMM gpio121 SCK、122 DATA0、123 WS、124 DATA1），
  SoC 出时钟；上方那颗兼作听筒【推断：只有两颗功放】。WSA884x/WSA883x 全部 disabled，`qcom,wsa-max-devs = 0`【DT 32708】。
- 编解码：WCD939x（`qcom,wcd939x-codec`），RX 从机在 RX SoundWire（`0x01170224`），TX 从机在 VA SoundWire（`0x01170223`）；复位 gpio101；
  供电 L15B 1.8 V、L2I 1.2 V、BOB；micbias 2.7 V；`qcom,msm-mbhc-usbc-audio-supported`（USB‑C 模拟耳机，无 3.5 mm）。
- 麦克风：`qcom,audio-routing` 只有模拟麦 AMIC1–5，DMIC 引脚全 disabled → 全是 WCD939x 模拟麦【DT】。
- LPASS：`lpass-cdc` VA `0x7660000`、TX `0x6ae0000`、RX `0x6ac0000`、WSA `0x6b00000`（disabled）、WSA2 `0x6aa0000`（只挂马达）；BT SoundWire `0x6ca0000`；
  LPI TLMM `0x7760000`；声卡 `qcom,sun-asoc-snd`，`sun-mtp-snd-card`。
- 全部经 ADSP（GPR `adsp_apps`、APM 建图、PRM 时钟）。
- 安卓：`audioserver`、`vendor-qti-media-c2audio-hal`、`wcd_usbss_i2c`、`snd-usb-audio-qmi`（其余在 vendor_dlkm）；TFA 容器 `/odm/firmware/tfa98xx.cnt`。
- 我方：`PaganiAudio`（静音后端在跑：`audio: up mode 1 backend silent …`【日志】）、`PaganiSpeaker`（未接线）、`PaganiAdsp`（未上机）。
- 可行性：内核测试音 **B**；iOS 出声 **C**（路由问题未解，`out=0`）；麦克风 **C**。

### 2.7 相机（三颗传感器 + CAMSS）

| 传感器 | 总线/地址 | 芯片 ID（`ftm_setting_compatible`） | 型号判断 | 外设 | 时钟/复位 | CSIPHY |
| --- | --- | --- | --- | --- | --- | --- |
| `cam-sensor0`（cell 0） | CCI0 master 1 | reg 0x0016 = **0x0906** | 索尼 IMX906 类 5000 万主摄（宣传名可能 LYT‑700，【未核实】） | `ois0`、`actuator0`、`eeprom0`、闪光 `camera-flash1` | MCLK1 gpio90；复位 PM8550VE‑I gpio5 | 1 |
| `cam-sensor2`（cell 2） | CCI0 master 0 | 先写 0xfcfc=0x4000，再读 reg 0x0000 = **0x38E5** | 三星 S5KJN5 5000 万长焦 | `actuator2`、`eeprom1`、闪光 `camera-flash3` | MCLK0 gpio89；复位 gpio178 | 5 |
| `cam-sensor3`（cell 3） | **QUP2 SE1 I2C `0x884000` 地址 0x20**（不是 CCI） | reg 0x0016 = **0x0615** 或 **0x0471**；`swremosaic_sensor_id = 0x471` | 前摄，索尼 IMX471（1600 万）或 IMX615（3200 万）两种供货；`aon-camera-id = 0`（常开人脸检测） | `actuator3`、`eeprom3@0x54` | MCLK4 gpio93；复位 gpio7 | 4 |

- 朝向：三颗的 `sensor-position-yaw` 都是 180，只能靠 roll（主摄/长焦 90，前摄 270）与型号判断前后【DT】。
- 供电：PM8010（SID 0xc/0xd）LDO `ldom1..7`、L14B、L1I；`cam_clk` = `gdsc@adf134c`（相机 GDSC）。
- CAMSS：CPAS `0xac13000`；CCI0/1/2 `0xac7b000/0xac7c000/0xac7d000`；CSIPHY0–5 `0xada9000…0xadb3000`；CSID0–2 `0xad26000/0xad29000/0xad2c000`、
  IFE0–2 `0xac86000/0xac96000/0xaca6000`、CSID/IFE‑lite0/1 `0xad6c000/0xad71000`；ICP0 `0xac05000`（固件 `CAMERA_ICP`）、ICP1（`CAMERA_ICP_1`）；
  IPE0 `0xac42000`、OFE `0xac2a000`、JPEG `0xac25000`/DMA `0xac26000`；保留内存 `camera_region@9b300000`、`camera_2_region@9bb00000`（各 8 MiB）。
- 安卓：`ro.hardware.camera=oemlayer`、`cameraserver`、`camera_extension`、`oplus,camera_extension`，传感器驱动/调参在 vendor/odm（未导出）。
- iOS 消费者：`mediaserverd`/相机捕获服务 + Apple ISP 驱动与 ISP 固件（iPad 根卷另有 `dietapplecamerad`、`realitycamerad`、`ptpcamerad`）。
- 我方：无。
- 可行性：原始取流（CCI 上电 + CSIPHY/CSID/IFE RDI 出 Bayer + 软件去马赛克）**C**（多周）；iOS 原生相机（要 Apple ISP 接口与 ISP 固件）**D**。

### 2.8 闪光灯 / 手电筒

- PM8550（SID1）`qcom,flash_led@ee00`（`qcom,qti-pm8350c-flash-led`），中断 `led-fault-irq`、`all-ramp-down-done-irq`、`all-ramp-up-done-irq`；
  `flash_0..3` 每路最大 1500 mA（闪光 1280 ms），`torch_0..3` 每路最大 500 mA，`led_switch_0..2`；相机实际用 `flash_0/1` + `torch_0/1` + `led_switch_2`（双路）【DT 11361、34747】；
  温控降额 `qcom,thermal-derate-current = <200 500>`；热敏 `pm8550_cam_flash_therm`（VADC 0x145）。
- 安卓：`leds-qcom-flash`、`leds-qti-flash`【VB】。
- iOS：控制中心手电筒走 AVFoundation 的手电筒接口 → 媒体服务 → 相机 ISP 的补光灯；没有相机时 iOS 判定无手电筒【推断】。
- 我方：无（PROGRESS：“没有相机、手电筒”）。
- 需要：硬件侧——SPMI 写 SID1 `0xEE00` 外设（与马达共用 EE0 归属检查与白名单写的做法），开 torch 通道、电流限 ≤ 500 mA/路、超时与温度保护；
  iOS 侧——让手电筒能力为真并把开关请求接到内核服务（并行 iOS 组定方案）。
- 可行性：硬件 **B**；端到端 **C**（取决于 iOS 侧挂接方式）。

### 2.9 显示扩展（AOD、HDR、亮度、刷新率）

- 面板 `qcom,mdss_dsi_panel_AA594_P_7_A0020_dsc_cmd`（DT 41120）：命令模式 DSC 1.2（10 bpc、8 bpp、slice 608×20），1216×2640，
  时序 `sa_fhd_120/90/60` 与 `oa_fhd_120`，时钟 1.096 GHz；物理 67×146 mm；`qcom,qsync-enable`、`qsync-min-refresh-rate = 30`、
  ADFR 最低帧率命令（`adfr/hpwm/bigdc-adfr-min-fps-0..5`）；`qcom,mdss-dsi-panel-hdr-enabled`（色域原色、peak/average/blackness）；
  AOD：`qcom,mdss-dsi-lp1-command`/`nolp`、`aod-high/low-mode`、`aod-off-compensation`；HBM：`hbm-on/off/max`；
  局部高亮（光学指纹按压图标）`lhbm-pressed-icon-on/off`；ESD 读 0x0a == 0x9c；亮度 DCS 0x51，正常最大 0xdbb、HBM 到 0xffe、默认 0x599、
  DC 调光阈值 0x7a8；`oplus,ofp-fp-type = 0x490`（屏下光学指纹配合）。
- 引脚/供电：复位 gpio98，TE gpio86（ADFR 测试 TE gpio85），面板电源 PM8550VE‑F gpio7；vddio L12B、vci L13B、vdd L11B、tvdd L4B。
- 控制器：MDP `0xae00000`（`qcom,sde-kms`），DSI0 `0xae94000`（v2.9）/PHY0 `0xae95500`（v7.2），DSI1 `0xae96000`，CESTA `0xaf30000`；
  DP `0xaf54000` + DP PLL `0x88eb000` 存在但 USB 只到 HS（2.16），DP 外接不可用【推断】。
- 我方：`NativeMobileDisplay`（CPU 呈现，软件 120 Hz）、`PaganiPanel`（亮度、显示开关、可选睡眠序列）。
- 可行性：亮度 **已完成**；面板睡眠序列 **B**（代码在，默认关）；自动亮度依赖 2.3（**C**）；AOD（iOS 常亮显示要低功耗刷新链路）**C**；
  HDR/EDR 输出 **C**（CPU 呈现链路没有 HDR 元数据）；硬件 TE/真实可变刷新率 **B/C**（D2 翻页阶梯在待测清单）。

### 2.10 Wi‑Fi（peach，PCIe）

- `qcom,cnss-peach@b0000000`（DT 42409）：`supported-ids = <0x110e>`（`17cb:110e`，peach_v2，WCN7880 系列【路线图】），在 **PCIe0**
  `pcie@1c00000`（`qcom,pci-msm`，parf `0x1c00000`、PHY `0x1c06000`、DBI `0x40000000`、conf `0x40100000`、`pcie_sm` `0x1d07000`，
  PHY 初始化序列就在 DT 的 `qcom,phy-sequence` 里，`qcom,pcie-phy-ver = 0x5e`，`qcom,drv-name = "cesta"`，SMMU SID 基 `0x1400`）。
- 引脚：PERST gpio102、WAKE gpio104、CLKREQ gpio103、**WLAN_EN gpio16**、SW_CTRL gpio18/gpio19、SOL gpio202（host）/gpio203（dev）；
  BT_EN 在 PM8550VE‑F gpio3。
- 供电（RPMh，PDC 表 `qcom,pdc_init_table` 把 s5f 设 876 mV）：PMU 映射 `VDDD_AON_0P9`/`VDDA_RFA_0P9`/`VDDD_WLCX`/`VDDD_BTCX` = s5f、
  `VDDA_RFA_1P9` = s3g、`VDDA_RFA_1P3`/`VDDA_PCIE_*` = s7i、`VDDD_WLMX_0P9` = s4d；io L3F 1.8 V、io12 L2F 1.2 V、天线共享 L8B 2.8 V。
- 安卓：`cnss2`（`cnss_pci`）+ `/vendor/lib/modules/qca_cld3_wlan.ko`（`ro.product.oplus.wlan.ko`）+ `cnss-daemon`（QMI WLFW 文件服务：BDF/regdb）、
  `wificond`、`wpa_supplicant`【属性】；第一阶段模块有 `mhi`、`qrtr-mhi`、`pcie-pdc`【VB】。
- 固件：在 `/vendor/firmware`（super → vendor）【路线图“固件从本机 /vendor 导出”】，文件名【未知】。
- 可移植代码：sheng `package/WifiProbe.cpp`（PCIe0 上电/链路、WCN7850 pwrseq 时序）、`MhiBoot.c`、`WifiNet.cpp`（Apple80211 网络服务，sheng 上 WPA2/DHCP/上网有真机证据）、
  组件 `UniXNU/Ath12kWifi`（`f6bf561`，支持 qcc2072/qcn9274/wcn7850，**不含 peach**；本机目录为空，需要拉取）；参考 renhiyama/pagani-linux 的 ath12k peach 补丁（GPL，只取事实）。
- 我方：无。
- 可行性：**C**（PCIe 上电与 PHY 序列可今晚写，peach 的 QMI/固件差异、MHI、BDF 都要多轮上机）。

### 2.11 蓝牙（peach BT）

- `bt_peach`（`qcom,peach-bt`）：HS UART QUP2 SE6 `0x898000`（gpio24–27）；BT 复位/使能 PM8550VE‑F gpio3、FMD 时钟 gpio6、SW_CTRL gpio18；
  时钟 `bt_rf_clk1`（RPMh clk）；供电与 Wi‑Fi 共用 PMU 轨；`nvmem` 用 PMK8550 SDAM 的 `fmd-set/fmd-chg-pon/fmd-cnt2-stop`【DT 42477】。
  音频卸载走 BT SoundWire（`btfmswr_slave`）。
- 固件：`bluetooth_a`（sde7，FAT），安卓挂 `/vendor/bt_firmware`（只读 vfat）【VB fstab、属性】；文件名【未知】（可用 PaganiAdsp 的 `FatVolume.c` 直接列目录）。
- 安卓：`btpower`（vendor_dlkm）、`oplus_btuart_ux`【VB】。
- iOS：`bluetoothd`、`bluetoothuserd`；它只驱动苹果自家控制器的私有传输【推断】。
- 我方：无。
- 可行性：**C**（偏难：HCI 层本身简单，难在让 iOS 蓝牙栈接受非苹果控制器）。

### 2.12 蜂窝基带、SIM、数据通路

- MPSS 见 2.1；IPA `qcom,ipa@3e00000`（GSI；`qcom,ipa_fws` 为 `qcom,pil-tz-generic`、PAS id 0xf、固件 `ipa_fws`，节点 disabled【DT 32985】，加载方式【未知】）；`qcom,rmnet-ipa`；
  RMTFS `qcom,rmtfs_sharedmem@0`（4 MiB 动态，`qcom,client-id 1`），后端分区 `modemst1`/`modemst2`/`fsg`/`fsc`（sdf9–sdf12）；
  `qcom,memshare`（modem 客户端 1/5/6，分别 5 MiB/16 MiB/4 MiB）。
- SIM：DSDS（`persist.radio.multisim.config=dsds`），采样时两卡都 ABSENT；eSIM 检测 PM8550 gpio11、eSIM 使能 PMIH010x gpio10、
  SIM2 检测 PM8550VE‑D gpio4；射频线检测 TLMM gpio47；基带版本 `Q_V1_P14`，`gsm.network.type=NR_SA`【属性、DT】。
- 安卓：`qti-modem-daemon-0/1`、`rild.libpath=/vendor/lib64/libril-qc-hal-qmi.so`、`time_daemon`、`ssgqmigd`【属性】。
- iOS：`CommCenter`（iPad 根卷带蜂窝版组件）+ 苹果基带传输与激活。
- 我方：无。
- 可行性：iOS 原生信号/通话/蜂窝数据 **D**（CommCenter 要苹果基带协议与激活流程）；自研“只启动 modem + QMI 读信号/收短信”**C**（多周，且对用户价值有限）。

### 2.13 GNSS / 定位

- 没有独立 GNSS 芯片节点：GNSS 在 MPSS 里（pd-mapper `gps/gps_service` 在 `msm/modem/root_pd`），对外是 QMI LOC 服务；安卓 `gnss_service`、
  `loc_launcher`；`sys.qca1530=detect`（外置 GNSS 探测，本机无）【属性】。
- iOS：`gpsd` + `GNSSPassthroughLib.plugin`（AppleSPU 直通，期待苹果/博通 GNSS）、`locationd`。
- 可行性：卫星定位 **C/D**（先要启动 modem + QMI LOC，再把位置喂给 locationd）；Wi‑Fi 定位随 Wi‑Fi（**C**，依赖 2.10）。
  `routined` 等 iPhone 专属守护进程崩溃是软件问题（PROGRESS 第 20 项）。

### 2.14 NFC 与安全元件

- `nfc_chipset`：`chipset-24821 = "SN220T"`（本机 prjname 24821）【DT 33464】；`nq@28`（`qcom,sn-nci`）在 QUP1 SE0 I2C `0x28`，
  IRQ gpio75、VEN gpio34、CLKREQ gpio35、`nxp,sn-vbat` gpio174；`ro.nfc.port=I2C`；`st54spi_gpio`（`st,st54spi_gpio`）power_nreset gpio46【DT 34788】；
  `ro.product.cuptsm=ONEPLUS|ESE|01|02`（有 eSE）。
- 安卓：`oplus_nfc`【VB】、`oplusnfc_aidl_hal_service`、`nfcExtnsService`【属性】。
- iOS：`nfcd`（iPad 根卷有 plist），内核侧 NFC 驱动在 iPhone 内核缓存里，iPad 内核缓存里大概率没有【推断】。
- 可行性：Apple Pay/钱包 **D**（需要 SEP 与苹果安全元件小程序）；读标签 **C/D**（价值低，不建议）。

### 2.15 指纹（屏下光学）

- `fingerprint_dev`（`oplus,fp_spi`，status ok）：复位 gpio78，供电 L9B 3.008 V（`ldo-config`），`notify_tpinfo_flag = 1`（按压由触摸上报）、
  `optical_irq_disable_flag = 1`；面板侧 `oplus,ofp-*` 与 `lhbm` 按压高亮【DT 33492、41120】。传感器 SPI 不在 HLOS 设备树里（归 TZ，
  对应保留脚 36–39【推断】）。`qcom,qbt_handler` 是高通超声波指纹的通用节点，本机未用。
- 安卓：`oplus_bsp_uff_fp_driver`【VB】、`fps_hal`、`fpay_hal`、`face_hal`（人脸解锁用前摄）【属性】。
- iOS：Touch ID = `AppleBiometricSensor.kext`/`AppleMesaLib.plugin` + `biometrickitd.mesa` + SEP。
- 可行性：**D**（iOS 生物识别必须经 SEP；本机指纹数据只在 TZ 的 TA 里）。

### 2.16 红外遥控

- `consumer_ir_core`（`oplus,kookong_ir_core`，vdd L9B 3.06 V）+ `consumerIr@0`（`oplus,kookong_ir_spi`，QUP1 SE5 SPI MOSI gpio53，5 MHz）【DT 34436、24864】。
- 安卓：`oplus_sensor_ir_core`、`oplus_sensor_kookong_ir_spi`【VB】。
- 可行性：硬件简单（SPI 发波形），但 **iOS 没有红外 API → D（无消费者）**。

### 2.17 USB 与 Type‑C

- DWC3 `ssusb@a600000`（`qcom,dwc-usb3-msm`）/ `dwc3@a600000`（`snps,dwc3`）：**`maximum-speed = "high-speed"`（只有 USB 2.0）**、`dr_mode = "otg"`、
  `usb-role-switch`；中断 `pwr_event_irq` GIC SPI 130、`dp/dm_hs_phy_irq` 走 PDC 14/15、`ss_phy_irq` PDC 17，dwc3 核心 SPI 133；SMMU SID `0x40`。
- PHY：eUSB2 M31 `hsphy@88e3000`（`qcom,usb-m31-eusb2-phy`，vdd L2D、vdda12 L3G、refgen L1F）+ PMIH010x `eusb2-repeater@fd00`
  （带 `param-override-seq`）；QMP USB/DP 组合 PHY `ssphy@88e8000` **disabled**。
- Type‑C/PD：`qcom,ucsi-glink`、`qcom,altmode-glink` 在 `qcom,pmic_glink` 下，经 ADSP 充电 PD（`PMIC_RTR_ADSP_APPS`）；WCD939x USBSS 管模拟耳机。
- 安卓：`dwc3-msm`（vendor_dlkm）、`phy-msm-m31-eusb2`、`repeater-qti-pmic-eusb2`、`ucsi_qti_glink`、`altmode-glink`、`usb_f_gsi` 等【VB】；
  `ro.boot.usbcontroller=a600000.dwc3`【属性】。UEFI 上游 paganiPkg 有 USB 设备模式（路线图）。
- iOS：`IOUSBDeviceFamily.kext`、`IOUSBHostFamily.kext`（iPad 根卷有），需要一个 DWC3 设备控制器子类。
- 我方：无（PROGRESS 第 19 项“iOS 下 USB 不工作”）。
- 可行性：**C**（DWC3 设备模式 + eUSB2/中继初始化 + 无 ADSP 时强制设备角色；只需 HS，比 USB3 简单）。

### 2.18 电池、充电、电源管理 PMIC

- 电量计：**NFG8011B**（OPLUS 按 bq27541 标准命令读），I2C hub SE1 `0x55`，100 kHz，gpio66/67（`oplus,ic_type 0x0c`）【DT 25560】；
  电池设计容量 6260 mAh（PaganiBattery 默认）。
- 充电器：PMIH010x（SID7）由 ADSP 充电 PD 的 battmgr 管（`qcom,pmic_glink`，`qcom,protection-domain = "tms/servreg", "msm/adsp/charger_pd"`，
  子节点 `qcom,battery_charger`（`oplus,hal_sm8350`）下有 `oplus,pm8550_charger`、`oplus,adsp_gauge`（只实现副板 NTC）、`oplus,adsp_pps`）【DT 13451】。
  PMIH010x ADC：`vph_pwr`、`vbat_sns_qbg`、`iin`、`ichg_fb`、`chg_temp`、USB/电池连接器 NTC。
- 快充：两颗 SC8547D 电荷泵（主：hub SE8 `0x6A` IRQ gpio163；副：hub SE3 `0x6A` IRQ gpio171），80 W SUPERVOOC/UFCS/PPS 由 OPLUS 框架
  （`oplus_chg_v2`、`oplus,vooc`、`oplus,ufcs_charge`、`oplus,pps_charge`）跑；`oplus,adsp-voocphy` disabled。无线充电节点不存在
  （`idt9418` 与 `smb1501` 只出现在高通参考的 `pmic_glink_debug`/`glink-adc` 里）【DT】。
- 安卓：`qti_pmic_glink`、`qti_battery_debug`、`charger-ulog-glink`、`oplus_chg_v2`、`oplus_gauge_i2c_rst`、`bcl_pmic5`、`bcl_soc`【VB】；
  `oplus_charger_hal_service`【属性】；`ro.boot.bootreason=shutdown,battery`（采样那次开机前的关机原因是电量耗尽）。
- sheng 结论（同代 SM8550）：**ADSP 固件自己会跑充电和 PD/PPS 协商，不需要主机请求**（`参考资料/UniXNU-sheng/porting.toml` power 区）。
- 我方：`PaganiBattery`（电量计直读 + battmgr；`pagani-battery=1` 或 `pagani-adsp=1` 才跑），PROGRESS 02:12“电池接线已合入”，
  B 包 `测试镜像/boot-A830-B包-A加电池.img`（`66bb5f81`）待测；最新运行没有开（`-v` 行无该参数）→ 这就是“电量显示不准”的直接原因（PROGRESS 01:15）。
- 风险：没有 ADSP 时没人喂充电器看门狗/跑检测，可能不充电；夜间测试曾疑似电池耗尽（PROGRESS 01:35）。
- 可行性：电量显示 **A**（代码与镜像都在，等一轮上机）；充电/PD/USB 角色 **B**（随 ADSP 上机）；OPLUS 80 W 快充 **C/D**（要移植 OPLUS 策略，不建议）。

### 2.19 温度与温控

- TSENS：`tsens0@c228000`（15 路）、`tsens1@c229000`（7）、`tsens2@c22a000`（16）、`tsens3@c22b000`（9），各有 uplow/critical 中断；
  PMIC 温度告警（每颗 PMIC 一个）；PMK8550 VADC 热敏：skin、cam_flash、wlan、pa1/pa2、rear_tof、xo、gpio04、slave_cp、USB 左右、副板、电池连接器、
  副电池连接器、USB 连接器、SVOOC MOS 等【DT 12545 起】；BCL：PM8550/PMIH010x 三级、`vbat`、`socd`；共 93 个温控区。
- 安卓：`qcom_tsens`、`qcom-spmi-adc5-gen3`、`qcom-spmi-temp-alarm`、`qti_qmi_sensor_v2`、`thermal-engine`【VB、属性】。
- iOS：`thermalmonitord`（读热压力/温度服务）。
- 我方：Adreno830 的 `Sm8750Tsens`（GPU 温控 70/60 °C）；没有给 iOS 的温度服务。
- 可行性：**B**（TSENS 读数已有代码，可再读 VADC 的 skin/电池温度，发布给 iOS 热压力）。

### 2.20 RTC 与系统时间

- PMK8550 `rtc@6100`（`qcom,pmk8350-rtc`，RTC 0x6100、alarm 0x6200，irq SID0 PID 0x62）【DT 12530】；安卓用 `rtc-pm8xxx` + 高通 `time_daemon`
  （PMIC RTC 由 TZ 保护不让写，安卓在 /data 记偏移）【推断，常规高通做法】。
- 我方：没有任何 RTC 读取（全仓检索无结果）；iOS 开机后的时间来源未记录【未知，早上看一眼 iOS 显示的日期】。
- 可行性：**B**（SPMI observer 只读读 RTC 秒计数，设置 XNU 日历时钟；偏移要靠联网或用户设置）。

### 2.21 存储

- UFS `ufshc@1d84000` + PHY `ufsphy_mem@1d80000` + ICE `shared_ice`；三星 KLUFG8RHHD‑B0G1 512 GB；LUN0 = sda（userdata 等），LUN4 = sde（固件分区）。
  SD 卡 `sdhci@8804000` disabled（本机无卡槽）。
- 我方：`NativeUfs`（只读根卷窗口、分区查找、`native_ufs_part_read`）；`PaganiPersistentData`（设计中，PROGRESS 第 15 项）。
- 可行性：持久 Data **C**（要开 UFS 写入与写闸门）。

### 2.22 视频编解码、EVA、JPEG

- Iris `qcom,vidc@aa00000`（`qcom,sm8750-vidc-v2`，SPI 174，`video_region@9c300000`），EVA/CVP `qcom,cvp@ab00000`（固件 `evass`，`cvp_region@9cb00000`），
  相机 JPEG 见 2.7。
- iOS：硬件编解码靠 Apple 自家 AVD/AVE 驱动；目前走软件解码。
- 可行性：**C/D**（收益不在“功能完美”清单里，不建议投入）。

### 2.23 LED、UWB 与其他

- PM8550 LPG `led-controller`（`qcom,pm8350c-pwm`）有 red/green/blue 三路【DT 11323】；本机是否有可见指示灯【未知，推断没有】。
- UWB：无硬件（`bt_uwb_en` 只是 PM8550VE‑F gpio3 的引脚名）。
- 调试 UART（2.1 表 QUP1 SE7）只在测试点上【推断】。
- CPU：簇 0 六核（`cpu@0..500`，capacity 1792）、簇 1 两核（`cpu@10000/10100`，capacity 1894），PSCI；调频经 CPUCP/SCMI（`qcom,scmi`、`qcom,cpucp@0x17830000`）。
  我方 `PaganiArmCpu`（8 核在线，对称调度，PROGRESS 第 12 项）。

## 3. 内存保留区（运行中设备树 `/reserved-memory`，只列 no-map 的固定区）

| 区 | 基址 | 大小 | 用途 |
| --- | --- | --- | --- |
| gunyah_hyp | 0x80000000 | 0x1200000 | Gunyah |
| cpucp_pdp | 0x81200000 | 0x200000 | CPUCP |
| aop_image / aop_cmd_db | 0x81c00000 / 0x81c60000 | 0x60000 / 0x20000 | AOP、cmd-db |
| aop_tme_uefi_merged | 0x81c80000 | 0x74000 | AOP/TME/UEFI |
| smem | 0x81d00000 | 0x200000 | SMEM |
| pdp_ns_shared / cpucp_scandump | 0x81f00000 / 0x82000000 | 0x100000 / 0x380000 | |
| adsp_mhi / soccp_sdi / pmic_minii_dump | 0x82380000 / 0x823a0000 / 0x823e0000 | 0x20000 / 0x40000 / 0x80000 | |
| pvm_fw / global_sync / tz_stat | 0x824a0000 / 0x82600000 / 0x82700000 | 0x100000 各 | |
| dsm_partition_1 / _2 | 0x84a00000 / 0x89300000 | 0x4900000 / 0xa80000 | 【推断】modem DSM |
| mpss / q6_mpss_dtb | 0x8ba00000 / 0x9b000000 | 0xf600000 / 0x80000 | 基带 |
| ipa_fw / ipa_gsi | 0x9b080000 / 0x9b090000 | 0x10000 / 0xa000 | IPA |
| gpu_microcode | 0x9b09a000 | 0x2000 | GPU ZAP |
| spss / spu_tz_shared / spu_modem_shared | 0x9b0a0000 / 0x9b280000 / 0x9b2c0000 | 0x1e0000 / 0x40000 / 0x40000 | SPSS |
| camera / camera_2 | 0x9b300000 / 0x9bb00000 | 0x800000 各 | 相机 ICP |
| video / cvp | 0x9c300000 / 0x9cb00000 | 0x800000 / 0x700000 | Iris / EVA |
| cdsp / q6_cdsp_dtb | 0x9d200000 / 0x9eb00000 | 0x1900000 / 0x80000 | CDSP |
| soccp | 0x9ec00000 | 0x180000 | SoCCP |
| q6_adsp_dtb / adspslpi | 0x9ed80000 / 0x9ee00000 | 0x80000 / 0x5080000 | ADSP（含传感器） |
| xbl_ramdump | 0xb8000000 | 0x1c0000 | |
| gunyah_hyp1 | 0xbd930000 | 0x800000 | Gunyah |
| hwfence-shmem | 0xd4e23000 | 0x2dd000 | |
| tz_merged | 0xd8000000 | 0x600000 | TZ |
| llcc_lpi | 0xff800000 | 0x800000 | |
| trust_ui_vm / oem_vm（reusable） | 0xf3800000 / 0xf7c00000 | 0x4400000 / 0x4c00000 | 安卓的 TUI/OEM 虚机 |

- DDR：region0 `0x80000000` 2 GiB、region1 `0x880000000` 6 GiB、region2 `0xa00000000` 8 GiB（共 16 GiB）；XNU 用 `0x8c007c000+0x180000000`【日志头】，
  ramoops 在 `0x880000000`。声音组把 DSP 周期缓冲放在 `0xD0000000`（HLOS 内存）。

## 4. 固件与资源位置表

| 内容 | 分区（UFS） | 安卓挂载 | 文件系统 | 我方能否读 |
| --- | --- | --- | --- | --- |
| ADSP/CDSP/MPSS/IPA/相机 ICP/EVA 等 PIL 固件、pd-mapper `*.jsn` | `modem_a` sde6（LUN4 块 4870，89600 块） | `/vendor/firmware_mnt` | vfat | **能**：NativeUfs + PaganiAdsp `FatVolume.c` |
| 蓝牙补丁/NVM | `bluetooth_a` sde7（扇区 755760，24576） | `/vendor/bt_firmware` | vfat | **能**（同上，文件名待列） |
| DSP 动态库（FastRPC skel 等） | `dsp_a` sde9（扇区 782384，131072 = 64 MiB） | `/vendor/dsp` | ext4 | 需要只读 ext4 读取器 |
| 传感器注册表、校准、WLAN MAC 等 | `persist` sda2（扇区 64，131072） | `/mnt/vendor/persist` | ext4 | 需要只读 ext4 |
| WLAN 固件、传感器 json、相机/音频调参、GPU 固件 | `super` sda14 → vendor/odm | `/vendor`、`/odm` | erofs（或 ext4） | 需要 LP 元数据 + erofs 读取器，或预先放进 Data 种子（只留本地） |
| 基带 EFS | `modemst1`/`modemst2`/`fsg`/`fsc`（sdf9–sdf12） | rmtfs 守护进程 | 原始 | 需要 RMTFS 服务 |
| TFA 容器 | super → odm `/odm/firmware/tfa98xx.cnt` | | | 声音组：原件需重新取 |
| 触摸固件 | `/odm/firmware/tp/24821/`（`vendor.oplus.touch.customifplib.path`） | | | 不需要（不刷固件） |

## 5. 早上可选的安卓只读导出（由用户执行，不重启、不写手机）

以下全部是安卓下 `adb shell` 只读命令（需要 root 的已标出），能补齐上面的【未知】：

1. `dumpsys sensorservice | head -120`：物理传感器清单（厂商/型号/量程），决定 2.2/2.3。
2. `ls /vendor/etc/sensors/config/`（root）与 `ls -R /mnt/vendor/persist/sensors | head`：SSC 配置与注册表文件名。
3. `ls -l /vendor/firmware /vendor/firmware/*peach* 2>/dev/null; ls /vendor/firmware_mnt/image | head -80; ls /vendor/bt_firmware/image`：WLAN/BT/PIL 固件名。
4. `cat /sys/kernel/debug/remoteproc/*/name; cat /sys/class/remoteproc/*/state`（root）：SoCCP 是否由 HLOS 启动、ADSP/CDSP/MPSS 状态。
5. `cat /sys/class/power_supply/battery/uevent`：对照 PaganiBattery 读数。
6. `getprop | grep -iE 'camera.*(sensor|module)|cam.*vendor'` 与 `ls /odm/lib64/camera | head`：相机模组名（核对 IMX906/S5KJN5/IMX471‑615）。

## 6. 可行性汇总

| 功能 | 硬件 | 依赖 | 可行性 |
| --- | --- | --- | --- |
| 按键（电源/音量/Plus） | PON + PM8550 GPIO6 + TLMM gpio97 | 无 | 已完成（音量条是 iOS 路由问题） |
| 电量显示 | NFG8011B @ hub SE1 0x55 | 无 | **A**（代码与 B 包已就绪，待一轮上机） |
| 马达 | PMIH010x HAP530 | SPMI 写 | **B**（并行组） |
| 手电筒 | PM8550 flash `0xEE00` | SPMI 写 + iOS 挂接 | 硬件 **B** / 端到端 **C** |
| 温度上报 | TSENS + VADC | 无 | **B** |
| 系统时间 | PMK8550 RTC | 无 | **B** |
| ADSP 启动 | remoteproc-adsp | 固件在 modem_a | **B**（总闸） |
| 充电/PD/USB 角色 | PMIH010x + battmgr + UCSI | ADSP | **B** |
| 扬声器（内核测试音） | TFA9873 ×2 + MI2S | ADSP | **B** |
| iOS 声音 / 麦克风 | 同上 + WCD939x | ADSP + iOS 路由 | **C** |
| 传感器（运动、计步） | SSC | ADSP + FastRPC 文件服务 + SSC 协议 | **C** |
| 光感/接近/自动亮度/原彩 | SSC 屏下 RGB | 同上 + 屏幕内容补偿 | **C** |
| Wi‑Fi | peach PCIe 17cb:110e | PCIe0 + MHI + QMI + 固件 | **C** |
| USB（连电脑） | DWC3 HS + eUSB2 | 设备控制器驱动 | **C** |
| 相机（原始取流） | CAMSS + 3 传感器 | ICP 可不用 | **C**；iOS 原生相机 **D** |
| 蓝牙 | peach BT UART | iOS 蓝牙栈 | **C**（偏难） |
| GNSS | 在 MPSS 内 | 启动基带 + QMI LOC | **C/D** |
| 蜂窝信号/通话 | MPSS + IPA + SIM | CommCenter | **D**（iOS 原生） |
| AOD / HDR | 面板支持 | 显示链路 | **C** |
| 持久存储 | UFS | 写入闸门 | **C** |
| Touch ID / Face ID / Apple Pay | 光学指纹（TZ）、前摄、SN220T + eSE | SEP | **D** |
| 红外遥控 | kookong SPI | iOS 无 API | **D**（无消费者） |
| UWB | 无 | — | **D** |
