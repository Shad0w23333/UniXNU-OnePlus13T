# 一加 13T Wi‑Fi（peach）移植事实表

> **2026-11-01 更正（`../round-1101/wifi.md` 第 1、3.4 节，来自 PKX110 的 dmesg 和真实 modem_a 卷）**：M8/M9 固件位置已定：全部在 modem_a（`/vendor/firmware_mnt/image/...`），但安卓用的 BDF 和 regdb 来自 `/odm/etc/wifi/peach`。
> 第 7 节的 M3、TME 争议：安卓发 `phy_ucode20.elf`、`aux_ucode20.elf`；TME-Lite 请求返回 `result 1, err 83`，**失败也继续**。冷启动校准后是主机下电再上电并重新加载固件，不是固件自己重启。

更新：2026-10-31（工作流步骤 wifi-facts）。范围：PKX110 / SM8750 上 WLAN 芯片 peach（PCI `17cb:110e`，peach_v2）
接到 XNU 所需的全部板级与芯片事实，并与 sheng（小米平板 6S Pro，WCN7850）的 `WifiPcieTables.h` / `WifiProbe.cpp` 逐项对照。
配套：`PaganiWifiTables.h`（同一批事实做成表）、`morning-facts.sh`（早晨只读核对）、`facts-check/`（表格自检）。

规则：GPL 源码（Linux ath12k、cnss2、qcacld、MHI、pwrseq、pci-msm）只取事实，不抄代码；专有固件不进仓库；不写手机存储。

## 0. 标记与来源

| 标记 | 含义 | 具体来源（版本） |
| --- | --- | --- |
| 【实机】 | 本机运行中设备树（`/sys/firmware/fdt`，sha256 `13b8b74a…`，反编译为 `a830-inputs/dt/running.dts`）与安卓属性 | `一加13T原厂备份/设备信息/` |
| 【厂商源码】 | 一加官方开源，同一固件线 PKX110_16.0.10.500 | `OnePlusOSS/android_kernel_oneplus_sm8750` 分支 `oneplus/sm8750_b_16.0.0_oneplus_13t` @`745702d5`（pci-msm.c、gcc-sun.c、MHI boot.c）；`OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750` 同名分支 @`6a196f79`（cnss2、cnss_utils、fw-api/hw/peach、qca-wifi-host-cmn、wlan-devicetree `oplus/pagani_sun-peach-cnss-v8-overlay.dts` → `sun-peach-cnss-v8.dtsi`） |
| 【主线】 | Linux 主线 | torvalds master @`36aad21d`（2026-10-31 取）：sm8750.dtsi、gcc-sm8750.c、tcsrcc-sm8750.c、phy-qcom-qmp-pcie.c、pinctrl-sm8750.c、interconnect/qcom/sm8750.c；Linux v7.0.14 MHI host（Ath12kWifi 编译时拉的版本） |
| 【pagani】 | renhiyama/pagani-linux @`2635bc17`（Linux 7.1 跑在本机上，自带 peach 的 ath12k 支持） | 只取事实；其代码里还留着“peach M4 debug：固件在射频初始化时 RDDM 崩溃”的调试分支，说明那条路未必已跑通 |
| 【sheng】 | UniXNU-sheng @`08cde20e` | `package/WifiPcieTables.h`、`WifiProbe.cpp`、`MhiBoot.c`；Ath12kWifi @`f6bf561f` |
| 【推导】 | 由上面事实计算得出，规则写在旁边 | |
| 【待核】 | 今晚确定不了，`morning-facts.sh` 读回；代码里必须是启动参数或表项 | |

许可证备注：cnss2/pci-msm/pwrseq 为 GPL-2.0-only；ath12k 为 BSD-3-Clause-Clear；fw-api、qca-wifi-host-cmn 为 ISC；
主线 DTS 为 BSD-3-Clause；gcc-sm8750.c 为 GPL-2.0-only OR BSD-2-Clause。以上一律只当事实引用。

## 1. 结论摘要

1. **PCIe 实例**：WLAN 在 **PCIe0**（`pcie@1c00000`），本机唯一启用的 PCIe 控制器【实机】。PARF/PHY 地址与 sheng 相同，
   但 **DBI/ATU/配置窗口/MEM 窗口整体从 0x6000_0000 挪到 0x4000_0000**；PHY 是 **QMP v7 布局**（PCS 在 0x400），
   初始化序列必须用本机设备树的 128 条，不能用 sheng 的表（只有 38 条 serdes 一样）。
2. **时钟/GDSC/复位**：GDSC 与 BCR 偏移同 sheng，分支投票位同 sheng；**halt 寄存器、管道时钟多路选择、两个 RCG 全部后移**，
   **GDSC 折叠投票寄存器换成 0x5214c（bit0/bit2）**，参考时钟开关换成 **TCSR 0xf204008 bit0**。
3. **引脚**：WLAN_EN **gpio16**、PERST **gpio102**、CLKREQ **gpio103**（功能 1）、WAKE **gpio104**、SW_CTRL gpio18/19、
   SOL gpio202/203【实机】。BT_EN 在 PMIC 上（PM8550VE‑F gpio3），WLAN 用不到。
4. **供电**：peach 由 **7 路 RPMh 电源**供电：L3F 1.8 V、L2F 1.2 V、**L8B 2.8 V（共享天线，没有它校准会失败）**、S4D 0.876 V、
   S5F 0.876 V、S3G 1.86 V、S7I 1.312 V（**S7I 同时是 UFS 的 vccq 上级，绝不能降压或关闭**）【实机+pagani】。
   另有 AOSS QMP 的 WLAN PDC 消息（可选）。PCIe PHY 的两路 LDO 与 UFS PHY 共用，NativeUfs 工作时已上电【推导】。
5. **SMMU/MSI**：流号同 sheng（根端口 0x1400、端点 0x1401）【实机】；安卓走 GIC ITS，XNU 继续用 DesignWare iMSI → **SPI 141**
   （主线 "msi0"，与 sheng 同号）【主线】。PCIe 在本机是 dma-coherent。
6. **peach 相对 WCN7850 的关键差异**（ath12k 视角）：重映射窗口寄存器 **BAR+0x3278**（WCN7850 是 0x310c）；UMAC 寄存器布局
   = **QCC2072**（已用 peach 自己的 fw-api 头文件核对）；CE/MHI/WMI/服务映射 = WCN7850；固件文件是 **SBL(512 KiB)+第二个 ELF**
   拼接，BHIe 要从 512 KiB 处开始（v7.0.14 的 MHI 已自动判断）；QMI 多出 **TME‑Lite 补丁**（`tmel_peach_20.elf`）、**AUX 微码**、
   **QDSS 配置下载**、**冷启动校准一轮**；M3 的处理厂商与 pagani 说法相反【待核】。
7. **固件位置**：文件名已确定（`peach/amss20.bin` 等，见第 8 节），**放在 modem_a 的 FAT 还是 /vendor 今晚确定不了**【待核】。
   在 modem_a 就用 PaganiAdsp 现成的 NativeUfs+FatVolume 就地读；在 /vendor（super→erofs）就照 GPU 固件的办法，
   本地 sha256 钉住后 `.incbin` 进内核，不进仓库。两种都做成表项+启动参数，不需要重写。
8. **Ath12kWifi 现状**：组件按 Linux v7.0.14 的 ath12k 编译，**不认 peach**；要加一个 peach 硬件条目（见第 7 节清单）。

## 2. PCIe0 控制器

| 项 | 13T 值 | 来源 | sheng（SM8550） |
| --- | --- | --- | --- |
| compatible | 安卓 `qcom,pci-msm`（drv-name `cesta`）；主线 `qcom,pcie-sm8750`+`qcom,pcie-sm8550` | 实机/主线 | `qcom,pcie-sm8550` |
| PARF | 0x01c00000，0x3000 | 实机 | 同 |
| PHY | 0x01c06000，0x2000（gen3x2，v7） | 实机/主线 | 同地址，v5 布局 |
| DBI（dm_core） | **0x40000000**，0xf1d | 实机 | 0x60000000 |
| ELBI | 0x40000f20，0xa8 | 实机 | — |
| iATU | **0x40001000**，0x1000（展开式，每区 0x200） | 实机 | 0x60001000 |
| 配置窗口 | **0x40100000**，1 MiB | 实机 | 0x60100000 |
| I/O 窗口 | 0x40200000，1 MiB | 实机 | — |
| MEM 窗口 | **0x40300000**，0x3d00000（主线给到 0x23d00000） | 实机/主线 | 0x60300000，0x3d00000 |
| 控制器 MHI 块 | 0x01c03000，4 KiB（仅调试） | 主线 | — |
| pcie_sm（CESTA 状态机） | 0x01d07000，0x7000；内核参数 `pci-msm-drv.pcie_sm_regs=0x1D07000,0x1040,0x1048,0x3000,0x1` | 实机 | 无；**不要碰**（读未供时钟的块会卡总线） |
| 通道数 / 速率 | RC x2，最高 Gen3；端点实际训练结果【待核 M3】 | 实机/主线 | sheng 实测 Gen2 x1 |
| 各 Gen 投票（qcom,bw-scale） | Gen1/2：cx/mx ≥ 64（LOW_SVS），rchng 19.2 MHz；Gen3：cx/mx ≥ 256（NOM），rchng 100 MHz | 实机（主线 OPP 一致） | 未投 |
| aux-clk-freq | 20（MHz，写 DBI 0xb40；msm_pcie 做，主线不做） | 实机/厂商源码 | — |
| ep-latency | 10 ms（PHY 就绪后的等待） | 实机/厂商源码 | 用 100 ms PERST 低电平 |
| 中断 | global SPI 140；msi0–7 SPI 141–148；INTA–D SPI 149–152 | 实机（140、149–152）/主线（141–148） | msi0 SPI 141 |
| 安卓的 MSI | `msi-map` → GIC ITS 0x16040000，DeviceID 0x1400/0x1401；`qcom,pci-msi`@0x17110040（SPI 768–799）为 disabled 且地址与单元名矛盾，**不可用** | 实机 | — |
| iMSI 寄存器（DBI） | ADDR_LO 0x820、ADDR_HI 0x824、INTR0_ENABLE 0x828、MASK 0x82c、STATUS 0x830，每组 +0xc | DesignWare（同 sheng） | 同 |
| PARF 寄存器 | SYS_CTRL 0x0、PM_CTRL 0x20、PHY_CTRL 0x40、MHI_CLOCK_RESET_CTRL 0x174、AXI_HALT 0x1a8、LTSSM 0x1b0、DBI_BASE 0x350/0x354、SLV_ADDR_SPACE 0x358/0x35c、ATU_BASE 0x634/0x638、DEVICE_TYPE 0x1000（RC=4）、BDF_TO_SID 表 0x2000、BDF_TO_SID_CFG 0x2c00 | 厂商源码/主线 | 同 |
| DBI 寄存器 | COMMAND 0x4、总线号 0x18、MEM base/limit 0x20、PCIe 能力 0x70、链路控制/状态 0x80、链路控制 2 0xa0、PORT_DEBUG0/1 0x728/0x72c（L0 = 0x11，DEBUG1 bit4 链路已起） | DesignWare | 同 |
| SMMU 流 | 请求者 0x0 → 0x1400，0x100 → 0x1401（`iommu-map`，`qcom,smmu-sid-base 0x1400`）；端点 BDF 哈希（CRC‑8 多项式 7）= **0x15** | 实机/推导 | 同 |
| dma-coherent | 是 | 实机/主线 | — |

端点 BAR0：WCN7850 是 2 MiB；peach 的大小与标志【待核 M4】。按 sheng 的放法（窗口内第一个按大小对齐的地址），2 MiB 时落在 0x40400000【推导】。

## 3. PCIe PHY（QMP gen3x2，v7）

- 布局【主线】：serdes 0x0、pcs 0x400、pcs_misc 0x800、tx 0x1000、rx 0x1200、tx2 0x1800、rx2 0x1a00。
  PCS 控制：SW_RESET 0x400、PCS_STATUS1 0x414（bit6 PHYSTATUS，=1 未就绪）、POWER_DOWN_CONTROL 0x440、START_CONTROL 0x444
  （设备树 `qcom,phy-status-offset 0x414`、`phy-status-bit 6`、`phy-power-down-offset 0x440` 一致【实机】）。
- 初始化序列：设备树 `qcom,phy-sequence` 共 **128 条**（每条延时 0）【实机】，原样放在 `PaganiWifiTables.h` 的 `phy::kSequence`：
  先 POWER_DOWN=3，再 serdes 38 条、rx1 29 条、`0x1800=0`、tx1 7 条、rx2 29 条、tx2 7 条、pcs 7 条、pcs_misc 7 条，
  最后 SW_RESET=0、START=3。
- 与 sheng 比：**serdes 38 条与 sheng 的 `kSerdes` 完全相同（顺序和值）**；TX/RX/PCS/PCS_MISC 是另一套寄存器（v7），全部不同；
  sheng 的“第二通道 = 第一通道偏移 +0x600/+0x800”在这里是 +0x800（tx 0x1000→0x1800，rx 0x1200→0x1a00）。
- 与主线 `sm8750_qmp_gen3x2_pcie_*_tbl` 比（本机序列是实机验证过的那份）：RX_MODE_01_HIGH4、RX_MODE_10_HIGH4（两通道）
  本机 0x8f、主线 0x9c；本机写 0x5e4=0x0f、主线写 EQ_CONFIG2 0x5e0=0x0f；本机多 0x8f8=0x1f 和 0x1800=0。
- PHY 供电：厂商设备树里没有（安卓由 CESTA 管）。主线 MTP：vdda-phy = L1F 0.88 V、vdda-pll = L3G 1.2 V；本机这两路也是
  UFS PHY 的 refgen 与 pll 电源【实机】，NativeUfs 在读根卷时它们必然已开【推导】。pagani-linux 写的是 L3I/L2I（自称照抄 sm8650-qrd），未证实。

## 4. GCC、TCSR、GDSC、复位（主线 gcc-sm8750.c 与厂商 gcc-sun.c 逐个核对一致）

| 项 | 13T | sheng（SM8550） |
| --- | --- | --- |
| GCC 基址 | 0x100000（0x1f4200 字节；用到的都在 0x80000 内） | 同 |
| PCIe0 GDSC / PHY GDSC | 0x6b004 / 0x6c000（父：cx.lvl S1J / mx.lvl） | 同 |
| GDSC 折叠投票 | **0x5214c**：bit0 = PCIe0，**bit2** = PHY（置 1 保持折叠）【实机 `qcom,collapse-vote`】 | 0x52020：bit0 / bit3 |
| 分支投票 0x52008 | bit0 slv_axi、1 mstr_axi、2 cfg_ahb、3 aux、4 pipe、5 slv_q2a、6 cnoc_pcie_sf_axi（掩码 0x7f） | 同 |
| 分支投票 0x52000 | bit11 qmip_pcie_ahb、12 aggre_noc_pcie_axi、19 ddrss_pcie_sf_qtb、20 cfg_noc_pcie_anoc_ahb、22 phy_rchng | sheng 只投 12/19/22 |
| halt 寄存器（bit31 CLK_OFF） | slv_axi 0x6b020、mstr_axi **0x6b030**、cfg_ahb **0x6b040**、aux **0x6b044**、pipe **0x6b054**、slv_q2a 0x6b01c、cnoc_sf **0x10058**、aggre_noc **0x10068**、ddrss_sf **0x1007c**、phy_rchng **0x6b064**、qmip 0x6b018、cfg_noc_anoc 0x10050 | 0x6b020、0x6b02c、0x6b038、0x6b03c、0x6b048、0x6b01c、0x10030、0x1003c、0x1004c、0x6b054 |
| 硬件门控分支 | slv_axi、mstr_axi、cfg_ahb、cnoc_sf、qmip、aggre_noc、ddrss_sf、cfg_noc_anoc（空闲时可能读到 CLK_OFF） | 类似 |
| 管道时钟多路选择 | **0x6b080**：0 = PHY 管道，2 = TCXO | 0x6b070 |
| aux RCG | **0x6b084**，CFG=0（TCXO 19.2 MHz） | 0x6b074 |
| rchng RCG | **0x6b068**，CFG=(6<<8)\|5（GPLL0_OUT_EVEN/3 = 100 MHz；主线 assigned-clock-rates 100 MHz） | 0x6b058，同值 |
| BCR | PCIE_0 0x6b000、PCIE_0_PHY 0x6c01c（设备树 `<gcc 6>`/`<gcc 9>`：厂商编号 = 主线编号 + 2，用视频/UFS 复位号交叉验证）；另有 LINK_DOWN 0x6c014、NOCSR_COM_PHY 0x6c020、PHY_NOCSR_COM_PHY 0x6c028 | 0x6b000、0x6c01c |
| PCIe 参考时钟开关 | **TCSR 0x0f204008 bit0**（tcsrcc-sm8750 偏移 0） | 0x1fd5000+0x100 bit0 |

## 5. 引脚（TLMM：引脚 n 的 CTL 在 0x0f100000 + 0x1000·n，IO 在 +4；CTL：上下拉 1:0、功能 5:2、驱动 8:6、OE 9、eGPIO 11/12）

| 用途 | 引脚 | 功能 | 安卓状态（cnss/pcie pinctrl）【实机】 | sheng |
| --- | --- | --- | --- | --- |
| WLAN_EN | gpio16 | gpio | 工作：输出高、16 mA、上拉；休眠：输出低、2 mA、下拉 | gpio80 |
| SW_CTRL | gpio18 | 1（wcn_sw_ctrl） | MPM 唤醒脚 | gpio82 |
| WLAN SW_CTRL | gpio19 | 1（wcn_sw） | MPM 唤醒脚 | — |
| PERST# | gpio102 | gpio | 2 mA、下拉，低有效 | gpio94 |
| CLKREQ# | gpio103 | 1（pcie0_clk_req_n） | 2 mA、上拉 | gpio95 |
| WAKE# | gpio104 | gpio | 2 mA、上拉，低有效 | gpio96 |
| host SOL | gpio202（eGPIO 脚） | gpio | 4 mA、下拉；cnss 上电后输出 0 | — |
| dev SOL | gpio203（eGPIO 脚） | gpio 输入 | 下降沿 = 芯片出错 | — |
| BT_EN | PM8550VE‑F（SID5）gpio3，PPID 0x58a | — | WLAN 不需要 | gpio81 |
| 32 kHz 睡眠时钟 | PMK8550（SID0）gpio3，PPID 0x0ba，func1 输出（`alt_sleep_clk`，PMIC GPIO 控制器默认状态） | — | 安卓开机时由 pinctrl 设好；XNU 下是否已设【待核 M18】 | 同（sheng 在阶段 2 补设） |

TLMM 的 `qcom,gpios-reserved` 是 36–39、74（与 WLAN 无关）；TUI 虚拟机占用的引脚里也没有上面这些【实机】。

## 6. 供电、RPMh、AOSS

### 6.1 七路输入电源（cnss `qcom,cnss-peach` 的 supply；PMU 引脚映射见 `kPmuVregMap`）

| 上电顺序 | cnss supply | RPMh 资源 | 稳压器 | PPID（回读用） | cnss 范围 | 取值 | 共享 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | vdd-wlan-io | ldof3 | pm_v8f_l3 | 0x5c3 | 1.8 V，30 mA | 1800 mV | BT vdd18-aon；always-on |
| 2 | vdd-wlan-io12 | ldof2 | pm_v8f_l2 | 0x5c2 | 1.2 V，30 mA | 1200 mV | BT vdd12-io；always-on |
| 3 | vdd-wlan-ant-share | ldob8 | pm_humu_l8 | 0x1c8 | 2.8 V（一加覆盖层） | 2800 mV | sdhc_2（disabled） |
| 4 | vdd-wlan-aon | smpd4 | pm_v8d_s4 | 0x3a4 | 0.876–1.036 V | 876 mV | BT dig、aon |
| 5 | vdd-wlan-dig | smpf5 | pm_v8f_s5 | 0x5a7 | 0.876–1.0 V | 876 mV | BT rfa0p75、aon |
| 6 | vdd-wlan-rfa1 | smpg3 | pm_v8g_s3 | 0x6a1 | 1.86–2.0 V | 1860 mV | BT rfa1p8 |
| 7 | vdd-wlan-rfa2 | smpi7 | pm_v8i_s7 | 0x8ad | 1.312–1.34 V | 1312 mV | **UFS vccq-parent**、BT rfa1p25 |

- 来源：设备树【实机】、`sun-peach-cnss-v8.dtsi`+一加覆盖层【厂商源码】；PPID 来自设备树 `regulator-ocp-notifier` 的
  `periph-<sid><页>` 名单【实机】：本机 PMIC 上 **SMPS n 在页 0x9b+3(n−1)，LDO n 在页 0xc0+n**（sheng 的 SM8550 PMIC 是 LDO15→0x4e，布局不同）。
  回读同 sheng：STATUS 0x08 bit7、EN_CTL 0x46 bit7、0x40/0x41 设定电压（mV）。
- 设备树里 S4D/S5F/S7I 的默认下限是 500/500/1224 mV；pagani-linux 把它们抬到 876/876/1312 mV（cnss 的下限），注释说 S4D（aon）
  停在 0.5 V 时芯片 PMU 一直不释放，S5F、S7I 是按 peach 规格抬的【pagani】。
  L8B 没开时，射频校准报 “ytx_acc power fail”、发射通路不可用【pagani】。
- 每路按 sheng 的经验发三条 VRM 请求：模式（+8，写 7 = NPM/HPM）、电压（+0，mV）、使能（+4）。`Sm8750Platform/Rpmh.h`
  的 `sm8750_rpmh_vrm_enable()` 已经就是这个顺序；DRV‑2 串行用现成的 `sm8750_rpmh_drv2_owner`（与 GPU、ADSP 共用）。
- 参考时钟：pagani-linux 的 PMU 节点投票 `RPMH_RF_CLK1`；cnss 的 WLAN 节点不列时钟（只有 BT 节点投 RF_CLK1）。
  不开 BT 驱动时 RF_CLK1 是否开着【待核 M16】。
- 时序：WLAN_EN 拉高后 cnss 等 1 ms；pwrseq（wcn7850/peach）上电后等 50 ms【主线/pagani】；sheng 用 WLAN_EN 先低 150 ms 再高、等 60 ms。

### 6.2 RPMh 与互连

| 项 | 13T | sheng |
| --- | --- | --- |
| apps RSC DRV‑2 | **0x16520000**，TCS 0xd00、间距 0x2a0、ACTIVE TCS 0–2【实机】 | 0x17a20000 |
| cmd-db | 0x81c60000，0x20000【实机】 | 同 |
| PCIe→DDR 路径 BCM | xm_pcie3 →（**SN4**）qns_pcie_mem_noc → gem_noc qnm_pcie（SH1）→ llcc → ebi（MC0 keepalive、ACV）【主线】 | SN7 |
| cnss 带宽表 | 9 档 × 2 路径（KBps），见 `kBusBw`；空闲档 2250/800000 与 2250/2188800【实机】 | — |

### 6.3 AOSS QMP（消息 RAM 0xc300000，`Sm8750AossQmp`）

- 设备树 `qcom,pdc_init_table` 四条：`{class: wlan_pdc, ss: rf, res: s5f.m, enable: 1}`、`… s5f.v, enable: 1}`、`… s5f.v, upval: 876}`、`… s5f.v, dwnval: 876}`【实机】。
- QMI 目标能力里固件给出 PMU 参数后，cnss 按 `pmu_vreg_map` 映射到 SoC 电源，发 OL‑CPR：唤醒值 = roundup(固件值,4) − 16 + 30（upval），
  睡眠值 = roundup(固件值,4) − 16 + 10（dwnval），`ss` 取 `vreg_pdc_map`（s5f/s3g/s7i → rf，s4d → bb）【厂商源码】。
  pagani-linux 直接发固定值：s5f 814、s4d 822、s7i 1314、s3g 1866（upval）【pagani】。
- 这些只影响芯片自己请求睡眠/唤醒时 PDC 怎么切电压；APPS 的 RPMh 投票在，电就在。sheng 一条都没发也能用。建议做成 `pagani-wifi-pdc` 选项。

## 7. peach 与 WCN7850 的差异（ath12k 风格驱动关心的部分）

| 方面 | WCN7850（ath12k 上游 / sheng） | peach（本机） | 依据 |
| --- | --- | --- | --- |
| PCI ID | 17cb:1107 | 17cb:110e；cnss 只支持 peach_v2 | 实机/厂商源码 |
| 版本读取 | TCSR_SOC_HW_VERSION 0x1b00000（经窗口 0x310c） | 同一地址，但必须经 0x3278 窗口；pagani-linux 干脆跳过、直接定为 hw2.0 | 厂商源码/pagani |
| **重映射窗口寄存器** | BAR+0x310c | **BAR+0x3278**（=QCC2072）；使能位 0x40000000、偏移>>19 取 6 位、窗口起点 0x80000、范围 0x7ffff | 厂商源码 reg.h |
| 芯片内部地址基 | PCIe 包装 0x1e00000、GCC 0x1e40000、WLAON 0x1f80000、CE 0x1b80000、UMAC 0xa00000 | 完全相同（fw-api hw/peach/v1，WCSS_VERSION 2544）；SRAM 到 0x1868000 | 厂商源码 |
| UMAC 寄存器布局 | wcn7850_regs（REO2SW1 0x4e4、SW2TCL1 0x900、REO cmd 0x28c、MISC 0xb7c、TCL status 0xd38） | **= qcc2072_regs**（0x500、0x918、0x2a8、0xba0、0xd50，用 peach 头文件逐个核对过） | 厂商源码/pagani |
| RX 描述符 | hal_rx_desc_wcn7850 | 紧凑 TLV（rx_msdu_end_compact），pagani 用 QCC2072 的描述符 | 厂商源码/pagani |
| CE | 9 个，WCN7850 配置与服务映射 | 同（qca-wifi-host-cmn 对 peach 直接选 KIWI 配置），影子寄存器 v3 | 厂商源码 |
| MHI 通道 | IPCR 20/21（64 元素，事件环 1） | cnss：LOOPBACK 0/1、IPCR 20/21、卫星 ADSP 50/51/70/71（不需要）；不支持 DIAG | 厂商源码 |
| MHI 事件环 | 2 个：控制 32（MSI1）、数据 256（MSI2） | cnss 4 个（多 BW_SCALE 32、卸载 256 客户端管理）；pagani 用 WCN7850 的 2 个能起 | 厂商源码/pagani |
| SBL / BHIe | SBL 512 KiB 走 BHI，BHIe 传整个镜像（sheng 的 MhiBoot 如此） | **amss20.bin = SBL(前 512 KiB) + 第二个 ELF**；BHIe 只传 512 KiB 之后（厂商 MHI `tme_supported_image`；v7.0.14 主线按“512 KiB 处是 ELF 头”自动处理） | 厂商源码/主线 |
| MHI 超时 / RDDM | 2000 ms / 0x780000 | cnss 10000 ms / 0x780000 | 厂商源码 |
| MSI | ath12k 16 个（MHI 3、CE 5、DP 8） | cnss 32 个（MHI 3、CE 10、WAKE 1、DP 18），只用 MSI 不用 MSI-X | 厂商源码 |
| DMA 掩码 | ath12k 流式 36 位、**一致性 32 位**（`dma_set_coherent_mask(32)`：BHI 缓冲、MHI 上下文与环、CE/HAL 环都在这里） | cnss 两者 36 位，但在 SMMU S1 后面，IOVA 只在 0x18000000–0xb0000000；三条跑通的路（安卓、pagani-linux、sheng）给芯片的一致性地址都在 4 GiB 以下 | 厂商源码/主线（2026-10-31 审查更正） |
| QMI 服务 | WLFW 0x45 实例 1 | 同；客户端 ID 0x4b4e454c | 厂商源码 |
| host_cap 特性位 | QDSS_CFG_MISS、PCIE_PERST_NO_PULL | pagani：DRV、WLAN_EN、AUX_UC；内部睡眠时钟位同 WCN7850 | pagani |
| **TME‑Lite** | 无 | 目标能力之后发 TME_LITE_INFO（0x5b，类型 0 补丁），文件 `tmel_peach_20.elf`；按需 OEM fuse（类型 1） | 厂商源码 |
| M3 | m3.bin（QMI 0x3c） | **cnss 发 `peach/phy_ucode20.elf`**；pagani 说那样会 RDDM，改发空 M3 且 host_cap m3_support=0。**哪个对【待核】** | 厂商源码 vs pagani |
| AUX 微码 | 无 | `peach/aux_ucode20.elf`（QMI 0x5a），仅当固件能力里声明 aux 支持 | 厂商源码 |
| 软 SKU 许可 | 无 | `cnss_softsku_peach.pfm`（QMI 0x60，可选，文件不存在就跳过） | 厂商源码 |
| QDSS 配置 | 无（报 QDSS_CFG_MISS） | 下载 `peach/qdss_trace_config_perf_v2.cfg`（回退 `…config_v2.cfg`，QMI 0x44）；pagani：不发就不给 FW_READY | 厂商源码/pagani |
| 冷启动校准 | 无 | `qcom,wlan-cbc-enabled`：FW_READY 后先 CAL_REPORT（0x26）→ 模式 CALIBRATION → 等 CAL_DONE（0x3e）→ 模式 OFF → 固件重启后再正常起 | 实机/厂商源码/pagani |
| 板级数据 | board-2.bin | `peach/bdwlan.elf`（ELF 类型）；一加按 board_id/地区/芯片挑名字（第 8 节） | 厂商源码 |
| 上电芯片侧复位 | ath12k：LTSSM 0x1e081b0=0x111、GCC 热复位、清中断 0x1e08228、QFPROM 0x1f8031c bit2、清 warm entry 0x1f80504、读清 0x1f8060c、全局复位 0x3008 bit0 | cnss 正常上电**不写这些**（只出现在崩溃转储的寄存器访问表里；QFPROM 电源控制要 `qcom,set-wlaon-pwr-ctrl`，本机设备树没有）；热复位寄存器 peach 未知：WCN7850 0x1e40304，ath12k 的 QCC2072 表 0x1e65304（pagani 用这个） | 厂商源码/pagani |

Ath12kWifi（@f6bf561f，按 Linux v7.0.14 编 ath12k）加 peach 需要的事实清单：PCI ID 0x110e；硬件条目（固件目录、256 KiB 板级数据、
aux 微码开、QMI 实例 1、内部睡眠时钟）；窗口寄存器 0x3278；HAL 寄存器与 RX 描述符取 QCC2072；CE/MHI/WMI/环掩码取 WCN7850；
TME‑Lite、AUX、QDSS、CAL_REPORT 四个 QMI 消息与冷启动校准一轮；M3 策略做成开关；芯片侧复位各写入做成位掩码开关。

## 8. 固件：名字、位置、就地读取

| ath12k 请求名 | 安卓（cnss2）请求名 | 传输 | 备注 |
| --- | --- | --- | --- |
| amss.bin | `peach/amss20.bin`（回退 `amss20.bin`） | MHI BHI+BHIe | 必需 |
| board.bin | `peach/bdwlan.elf` 或变体 | QMI BDF（ELF） | 一加规则：board_id=0xff → 按地区：国行 `bdwlan.elf`，印度/欧洲/美国 `bdwlan.b0i/.b0e/.b0a`；芯片 ES1 位 → `bdwlang.*`；board_id<0xff → `bdwlan.e<id>`；设备树没有 `oplus_bdf` 节点【实机】 |
| regdb.bin | `peach/regdb.bin` | QMI BDF（REGDB） | 必需 |
| m3.bin | `peach/phy_ucode20.elf` | QMI M3 | 见第 7 节争议 |
| aux_ucode.bin | `peach/aux_ucode20.elf` | QMI AUX | 固件声明支持时 |
| tmel_peach_20.elf | `tmel_peach_20.elf`（**无 peach/ 前缀**） | QMI TME（补丁） | 必需 |
| qdss_trace_config_v2.cfg | `peach/qdss_trace_config_perf_v2.cfg`（回退 `peach/qdss_trace_config_v2.cfg`） | QMI QDSS | 用户版固件需要 |
| — | `cnss_softsku_peach.pfm`（无前缀） | QMI SKU | 可选 |
| — | `peach/peach_sec.dat`、`peach_rpr.bin`、`peach_dpr.bin` | QMI TME（可选文件） | 仅按需 |

- 前缀规则：`qcom,same-dt-multi-dev` 使 cnss 用 `peach/` 前缀【实机+厂商源码】；peach_v2 选 “20”/“_v2” 名【厂商源码】。
- 位置：安卓把 modem_a（sde6）挂在 `/vendor/firmware_mnt`（vfat，`dev.mnt.blk.vendor.firmware_mnt=sde6`），bluetooth_a 挂在
  `/vendor/bt_firmware`【实机属性】。文件到底在 `/vendor/firmware_mnt/image/peach/`、`/vendor/firmware_mnt/image/` 还是
  `/vendor/firmware/peach/`（super→vendor，erofs）**今晚无证据**；盘点文档里“在 /vendor”的说法来自路线图的计划，不是实测。【待核 M8/M9】
- 就地读取（在 modem_a 时）：NativeUfs `native_ufs_part_find/read`（modem_a = LUN4，首 4 KiB 块 4870，89600 块）+ PaganiAdsp 的
  `FatVolume.c`（长/短名不分大小写，`image/peach/amss20.bin` 这种路径直接可用）。表 `kFatDirs`：先 `image/peach` 再 `image`；分区先 modem_a 再 modem_b。
- 在 /vendor 时：不能就地读（要 LP 元数据 + erofs 读取器）；照 GPU 固件的现成做法：本地缓存 + JSON 钉 sha256 + `.incbin` 进内核
  （`UniXNU-Pagani_SM8750-gpu/scripts/data/sm8750-gpu-inputs.json` 的格式），固件不进仓库。sheng 那种往 `oops` 分区写容器的办法**不允许**（不写手机存储）。
- 大小估计：WCN7850 的 amss 约 5–6 MB（sheng 按 48×128 KiB 预留）；peach 的实际大小【待核 M8】。安卓给这个 PCIe 端点挂了
  32 MiB 可复用 DMA 池（`cnss_wlan_region`，4 MiB 对齐、4 GiB 以下）【实机】，RDDM 0x780000；XNU 侧的 DMA 区按 ≥32 MiB 准备【推导】。

## 9. 早晨核对清单（`morning-facts.sh`，全部只读）

| 编号 | 要读的事实 | 脚本输出 | 缺了怎么办（代码里的形态） |
| --- | --- | --- | --- |
| M1 | 运行中设备树 sha256 仍是 13b8b74a… | A3 | 不一致就重新反编译、重比本表 |
| M2 | 端点 vendor/device/修订号/子系统 ID | B1 | 子系统 ID 只影响 board-2 命名，不影响上电 |
| M3 | 实际链路速率与宽度（及日志里的训练记录） | B1、E1 | `pagani-wifi-gen` 默认 2（Gen3 需要先投 cx/mx ≥ NOM） |
| M4 | BAR0 大小与 64 位标志 | B1、`files/ep-…config.bin` | 代码按探测到的大小映射，上限表项 `kBar0BytesExpected` |
| M5 | MSI 能力（可请求向量数、64 位、每向量屏蔽）、MSI-X、L1SS、AER | SUMMARY M5 | iMSI 只用控制器 0 的 32 个向量；不够时 `pagani-wifi-msi=0` 轮询 |
| M6 | 安卓实际拿到几个 MSI | C1、B1 msi_irqs | 只作对照 |
| M7 | 端点所在 IOMMU 组、BAR 在 /proc/iomem 的位置 | C2、C3 | 只作对照 |
| M8 | 固件文件全路径、大小、sha256 | D4、D5 | 文件名表 `kFirmware`；`pagani-wifi-fwdir`、`pagani-wifi-bdf` 覆盖 |
| M9 | 固件在 modem_a（FAT）还是 /vendor | D1、D4、SUMMARY M9 | `pagani-wifi-fwpart`；在 /vendor 则走内核内嵌 |
| M10 | 固件版本串（QC_IMAGE_VERSION 等） | D6 | 记录进 porting 记录 |
| M11 | qcacld INI（`WCNSS_qcom_cfg.ini`）与搜索路径 | D1、D3、files/ | 只作参考（6 GHz、省电等配置） |
| M12 | chip_id / board_id / 地区（决定 BDF 名） | E1、E2 | `pagani-wifi-bdf` |
| M13 | 实际选中的 BDF、是否 peach_v2 | E1、E2 | 同上 |
| M14 | 冷启动校准、TME/AUX/QDSS 是否发生 | E1、E2（cnss 的调试级日志看不到，能看到多少算多少） | `pagani-wifi-cbc`、`pagani-wifi-m3` |
| M15 | Wi‑Fi 开着时七路电源的电压/状态/用户 | F1 | 表 `kRails` 的取值 |
| M16 | RF_CLK1 / LN_BB 是否开着（DEBUGFS=1） | G1 | 必要时由驱动投票 RF_CLK1 |
| M17 | PCIe 互连投票（DEBUGFS=1） | G2 | BCM 名 `kPcieNocBcm` |
| M18 | WLAN_EN/SW_CTRL/PERST/CLKREQ/WAKE/SOL 与 PMK8550 gpio3 的引脚状态（DEBUGFS=1） | H1 | 阶段 1 只读这些引脚并记日志 |

运行：`sh morning-facts.sh`（可加 `DEBUGFS=1`；`PULL_FW=1` 把固件拉到本地 `phone/…/firmware/`，只留本地）。
今晚用本地替身（假 adb/su）完整演练过一遍，引号、管道与汇总解析都正常；真机还没跑过。

## 10. 与 sheng 的逐项对照（`WifiPcieTables.h` / `WifiProbe.cpp` 的常量 → 13T）

| sheng 常量 | sheng 值 | 13T 值 | 结论 |
| --- | --- | --- | --- |
| kGcc / kTlmm | 0x100000 / 0xf100000 | 同 / 同（引脚页起点） | 同 |
| kParf | 0x1c00000 | 同 | 同 |
| kDbi / kAtu / kConfig | 0x60000000 / 0x60001000 / 0x60100000 | 0x40000000 / 0x40001000 / 0x40100000 | **改** |
| kMemBase / kMemSize | 0x60300000 / 0x3d00000 | 0x40300000 / 0x3d00000 | **改** |
| kPhy | 0x1c06000（v5） | 0x1c06000（v7） | 地址同，**布局改** |
| wifi_phy::kSwReset/kPcsStatus1/kPowerDownControl/kStartControl | 0x200/0x214/0x240/0x244 | 0x400/0x414/0x440/0x444 | **改** |
| kTx2 / kRx2（相对通道 1） | +0x800 / +0x800 | +0x800 / +0x800（tx 0x1000→0x1800，rx 0x1200→0x1a00） | 差值同，基址改 |
| kSerdes | 38 条 | 38 条，完全相同 | 同 |
| kTx / kRx / kPcs / kPcsMisc | v5 表 | 用设备树的 v7 序列 | **全换** |
| kTcsrClkref | 0x1fd5000 +0x100 bit0 | 0xf204008 +0 bit0 | **改** |
| kVote8 / kVote8Bits | 0x52008 / 0x7f | 同 | 同 |
| kVote0 / kVote0Bits | 0x52000 / bit12、19、22 | 同 + bit11、20 | 补两位 |
| kGdsc / kPhyGdsc | 0x6b004 / 0x6c000 | 同 | 同 |
| kCollapse（位） | 0x52020（bit0、bit3） | 0x5214c（bit0、bit2） | **改** |
| kBranches（halt） | 0x6b020,0x6b02c,0x6b038,0x6b03c,0x6b048,0x6b01c,0x10030,0x1003c,0x1004c,0x6b054 | 0x6b020,0x6b030,0x6b040,0x6b044,0x6b054,0x6b01c,0x10058,0x10068,0x1007c,0x6b064 | **改** |
| kPcieBcr / kPhyBcr | 0x6b000 / 0x6c01c | 同 | 同 |
| kPipeMux / kAuxRcg / kRchngRcg | 0x6b070 / 0x6b074 / 0x6b058 | 0x6b080 / 0x6b084 / 0x6b068 | **改**（CFG 值同） |
| kWlanEn / kPerst / kClkReq / WAKE / SW_CTRL | 80 / 94 / 95 / 96 / 82 | 16 / 102 / 103 / 104 / 18（另 19） | **改** |
| CLKREQ 功能号 | 1 | 1（pcie0_clk_req_n） | 同 |
| PARF/DBI 寄存器偏移 | pcie-qcom.c | 同 | 同 |
| kSpmiCore/Obs/Chn/Cnfg | 0xc400000/0xc440000/0xc500000/0xc42d000 | 同 | 同 |
| kSleepClkPpid | 0x0ba（PMK8550 gpio3） | 同 | 同 |
| kRegPages | SMPS 0x9b+3(n−1)，LDO 0x40+(n−1) | SMPS 同，**LDO 0xc0+n** | 改 LDO |
| kRails | smpg5 850、ldob15 1800、smpg2 850、smpe4 950、smpg4 1300、smpg6 1860 | ldof3 1800、ldof2 1200、ldob8 2800、smpd4 876、smpf5 876、smpg3 1860、smpi7 1312 | **全换** |
| kRscDrv / kRscTcs | 0x17a20000 / 0xd00 | 0x16520000 / 0xd00（间距 0x2a0） | **改** |
| kCmdDb | 0x81c60000 | 同 | 同 |
| BCM | SN7 | SN4 | **改** |
| SMMU 流 / BDF 哈希 | 0x1400、0x1401 / CRC‑8 | 同 / 同（端点 0x15） | 同 |
| iMSI 寄存器 / kMsiSpi | 0x820… / 141 | 同 / 141 | 同 |
| chipRead/chipWrite 窗口寄存器 | 0x310c | **0x3278** | **改** |
| MHI 块在 BAR 的位置 | 0x1e0e100→BAR+0 | 同 | 同 |
| chipPowerUp 各写入 | ath12k 的 WCN7850 序列 | cnss 不做；热复位地址未知 | **改成可选位掩码** |
| findFirmware（oops 分区 WFW1 容器） | 写过分区 | 不允许写 | **改成就地读 modem_a 或内嵌** |
| mhiBring 的 BHIe 表 | 整个镜像 | 512 KiB 起（有 ELF 头时） | **改** |
| boardMac | unique-chip-id 推导 | 同（安卓用 NV MAC，`use-nv-mac`） | 同 |

## 11. 给驱动的约束：缺的事实只能是启动参数或表项

建议的启动参数（全部默认关，名字已放进 `PaganiWifiTables.h` 的 `bootarg` 命名空间）：

| 参数 | 作用 | 默认 |
| --- | --- | --- |
| `pagani-wifi=N` | 0 关；1 只读勘察（GDSC/分支/引脚/PMIC 回读）；2 上电+链路训练+读端点 ID；3 映射 BAR、读 MHI/BHI；4 自己的 MHI 引导（调试用）；5 Ath12kWifi 协议栈 | 0 |
| `pagani-wifi-gen` | 链路速率上限 | 2 |
| `pagani-wifi-fwpart` / `pagani-wifi-fwdir` | 固件分区 / FAT 目录 | modem_a,modem_b / image/peach,image |
| `pagani-wifi-bdf` | BDF 文件名 | 按 board_id 规则 |
| `pagani-wifi-m3` | 0 空 M3（pagani），1 phy_ucode20.elf（cnss） | 1 |
| `pagani-wifi-chipreset` | ath12k 芯片侧复位各写入的位掩码（热复位默认不写） | 不含热复位 |
| `pagani-wifi-pdc` | 1 发设备树四条 PDC 消息，2 再发 OL‑CPR | 0 |
| `pagani-wifi-msi` | 0 轮询，1 iMSI→SPI 141 | 1 |
| `pagani-wifi-cbc` | 冷启动校准一轮 | 1 |

每一步失败都应是“阶段号 + 错误码”记在状态里，并在 BootProgress ramoops 日志写一行 `[wifi] U …`，绝不 panic。
阶段 1 只读；读未上时钟的块会卡总线，所以 PARF/DBI 只在 GDSC 开且 cfg_ahb/aux/mstr/slv 四个分支在跑时才读（同 sheng 的 kNeeded）。

## 12. 产物与自检

- `PaganiWifiTables.h`：本文全部数字的表格版（C++，命名空间 `pagani_wifi`）。
- `facts-check/check-tables.sh`：主机 C++17/C++2b `-Wall -Wextra -Werror -Wconversion` 编译并运行自检（PHY 128 条、各段条数、
  BDF 哈希 0x15、分支投票位、电源范围），再用内核树 `IOPlatformExpert.cpo.json` 的 XNU 编译记录（只读）编成 ARM64 目标文件。
  2026-10-31 结果：两种主机标准均 0 失败（摘要 912fb297）；内核参数编译通过（`Mach-O 64-bit object arm64`）。
- `morning-facts.sh`：只读取证脚本；`sh -n` 通过；本地替身演练通过；汇总解析用合成数据验证过（合成数据不是事实）。
