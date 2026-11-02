# USB 与充电现状、USB 数据通道路线（round-1101）

- 日期：2026-11-01。手机全程在安卓，只做了只读查询；没有重启手机，没有改任何现有源码、种子、镜像，没有提交。
- 证据等级：【实测】= 跑了命令或读了日志；【读码】= 读了源码、反汇编或二进制字符串；【推断】= 由事实推出，未直接验证。
- 反汇编和字符串分析针对 21H461 的 J120 内核缓存（sha256 `f18b4df8…`）和 `UEFI-Pagani_SM8750/Binaries/pagani`（来自一加 13s 的 `BOOT.MXF.2.5.1 PAKALA` 固件）。13T 原厂 `uefi` 分区没有分析。
- GPL 代码（Linux、KGSL）只取了寄存器偏移和顺序这类事实，本文不含其代码。

## 0. 结论

1. **能充电，但是 PMIC 硬件在自己跑，没有任何软件在管。** ADSP 没有启动，自定义 UEFI 把 `PILDxe`、`GLinkDxe` 注释掉了，`PaganiBattery` 是 `charge=0` 只读。【实测+读码】
2. **“能充”的证据只有一段 25 秒的窗口**（296.5 到 321.6 s，平均 +311 mA，100 % 电量）。中段电量的充电能力、长时间是否停充，都没有测过。【实测】
3. **功率大约 4 W 量级**（下限 1.4 W，上限未知），亮屏时净入约 1.4 W，从 20 % 充满要十几个小时。这不是日用水平，要快必须靠 ADSP 和 PD。【推断】
4. **USB 目前完全不工作，原因是 XNU 里根本没有 USB 设备驱动**：30 个预链接 kext 里没有一个 USB 类，ADT 没有 USB 节点，也没有自研驱动，所以 `DCTL.RUN` 没人置位，D+ 上拉不出来，Mac 什么都看不到。充电只走 VBUS 和 CC，和这条数据路径无关。【实测+读码+推断】
5. **最大的新发现：J120 内核缓存自带 DWC3 设备驱动 `AppleUSBXDCI`。** 它和高通控制器是同一套寄存器模型，平台相关部分只有 `AppleT8011USBXDCI` 那 8 KB 代码。`lockdownd`、`ptpd` 已经在跑。离线预链接 `IOUSBDeviceFamily + AppleUSBXDCI + AppleUSBDeviceMux` 成功（30 到 34 个组件）。之前“iOS 正规路径要写完整 `IOUSBDeviceController` 子类”的判断要改写：可以子类化 `AppleUSBXDCI`。【实测+读码】
6. **这条路最大的未知是 DMA 一致性**：`AppleUSBXDCI` 用缓存内存、按一致性 DMA 建 `IODMACommand`，而本项目的 UFS 走旁路时是靠显式缓存维护的。不解决就会读到旧 TRB。【读码+推断】
7. **USB 网络比 Wi‑Fi 风险小，但只是调试和文件通道，不能替代 Wi‑Fi。** 网卡接入已有现成做法（sheng 的 `ifnet` 加注册项，配合系统自带的 `addNetworkInterface`）；sm-t733、sweet 有 ECM 先例。【读码】
8. **路线建议**：下一包先带只读探针（P0），接着做 CDC-ACM 控制台（P1，调试收益最大），P1 跑通后用一轮验证 DMA 一致性，再决定先走自研 ECM（P2）还是直接走 `AppleUSBXDCI` + Mux（P3）。主机模式放到 ADSP 之后。
9. **两个容易踩的坑**：SM8750 的 GCC USB 偏移和 SM8550 不一样，不能直接抄 `QcomXnuUsbDebug` 的 `Sm8550.h`（第 4.2 节）；启动参数已经 996 字节，上限 1000，新增任何开关都要先腾位置（第 8.5 节）。

## 1. 充电现状

### 1.1 PaganiBattery 的实际输出【实测】

含 `[battery]` 的运行只有 4 轮。`log=1` 按配额记录，所以每轮只有开头两次读数和状态变化。

| 运行 | 时刻 s | mv | 瞬时 mA | 平均 mA | 温度 °C | rm/fcc | flags | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1031-2213（约 412 s） | 1.96 | 4457 | 0 | 68 | 29.0 | 5942/5942 | 0x20 | ext=0 chg=0 |
| | 3.98 | 4441 | -294 | 27 | 28.9 | 5942/5942 | 0x60 | 不变 |
| 1031-2239（1203 s，panic 结束） | 1.98 | 4453 | 0 | 87 | 33.4 | 5936/5936 | 0x20 | ext=0 chg=0 |
| | 4.00 | 4442 | -256 | 43 | 33.4 | 5936/5936 | 0x60 | 全程没有变化 |
| 1101-0539（4.86 s 即 panic） | 2.02 | 4440 | 0 | 94 | 28.5 | 5940/5940 | 0x20 | ext=0 chg=0 |
| | 4.04 | 4434 | -124 | 72 | 28.5 | 5940/5940 | 0x20 | **ext=1 chg=1** |
| 1101-1102（420 s） | 2.05 | 4457 | 0 | 57 | 28.1 | 5898/5898 | 0x20 | ext=0 chg=0 |
| | 4.07 | 4436 | -367 | -367 | 28.2 | 5897/5898 | 0x60 | 不变 |
| | 296.51 | 4457 | 未记录 | **+311** | 40.4 | 5847/5898 | 未记录 | **ext=1 chg=1**，`adapter=0xe0004007`（未知类型） |
| | 321.56 | 4413 | 未记录 | **-562** | 41.0 | 5846/5898 | 未记录 | ext=0 chg=0 |

由此得出：

- 电流符号正确，充电为正。【实测】
- 4 到 296 s 掉了 50 mAh，平均放电约 616 mA，约 2.7 W。这是 GPU 桌面亮屏、手持操作的整机功耗。【实测，算术】
- `iOS` 侧直接证据：diag 的 `r1`、`r2` 行都是 `InternalBattery 100/100 chg0 Battery_Power`，发生在插电之前；下一轮 diag 在 600 s，本轮 420 s 就复位了。所以**插电窗口内没有 iOS 侧日志**，闪电只有用户肉眼确认。【实测】
- 窗口内没有马达、声音之类的反馈记录，这不说明问题，因为 iOS 的音频后端本来就是静音。【实测】
- `1101-0539` 那次 `ext=1` 不能算充电证据：瞬时 -124 mA，靠启动时残留的平均电流 +72 mA 触发，4.86 s 后就 panic 了。【实测】
- `flags` 的 bit 6（bq28z610 布局的 DSG）在第二次读数放电时有三轮为 1，`1101-0539` 那轮为 0（瞬时 -124 mA）。开机第一次读数四轮都是 0x20，插电窗口内的 `flags` 没有记录，所以 `BatteryGauge.h` 说的“一轮充电就能定”还没做到。【实测】
- 安卓现在的基准：`status=Full`，4446 mV，34.4 °C，`usb` 5.144 V、84 mA，`usb_type=SDP`，`input_current_limit=2000 mA`；OPLUS 日志 `chg_type=pd_sdp chg_mode=dcp spec_icl=2000 spec_fcc=2200`，`adapter_power=10000`。这说明 Mac 口在安卓下靠 ADSP 的 PD 协商到 5 V 2 A。【实测】

历次回到安卓时 `level` 都是 100，读数在开机后 15 到 26 秒取得，UI 电量滞后，不能证明 XNU 期间没掉电。`Charger voltage` 落在 4741 到 5196 mV，只说明回到安卓后在充。【实测+推断】

### 1.2 iOS 显示闪电的条件

`BatteryProps.h` 记录的 powerd 与 BatteryCenter 逻辑：闪电 = `Is Charging ? Power Source State == "AC Power" : Show Charging UI`；`Power Source State` 由 `ExternalConnected` 决定，`Show Charging UI` 来自 `AppleRawExternalConnected`。【读码】

`PaganiBattery` 在 `ext=1 chg=1` 时把这几个键都置真，所以 296.5 到 321.6 s 窗口内条件满足，和用户看到的一致。【读码+实测】

但不开 ADSP 时 `ext/chg` 完全由电量计电流推断（`BatteryCore.c` 的 `infer`），有三类问题：【读码】

| 情况 | 结果 |
| --- | --- |
| 插着线但负载大于输入，净电流为负（比如 5 V 500 mA 的电脑口带 GPU 桌面） | **漏报**：显示没插电 |
| 电量 100 %，插线后电流在 -50 到 +50 mA 之间 | **漏报**：只有先判成在充才可能转成 Full |
| 开机头几秒平均电流残留为正 | **误报**：`1101-0539` 就是这样 |
| 判定有延迟 | 插拔要 2 到 12 秒才反映，因为每 10 s 读一次，再 2 s 确认 |

### 1.3 到底是谁在管充电

| 环节 | 事实 | 等级 |
| --- | --- | --- |
| ADSP 固件 | 启动参数没有 `pagani-adsp`，日志 `[battery] start ... adsp=0 charge=0` | 实测 |
| 安卓设备树 | 充电归 `qcom,pmic_glink` 下 `msm/adsp/charger_pd`，通道 `PMIC_RTR_ADSP_APPS` | 读码 |
| 自定义 UEFI | `oneplus13tPkg` 的 `DXE.inc`、`APRIORI.inc` 里 `PILDxe`、`PILProxyDxe`、`GLinkDxe` 全部注释掉，无法加载 ADSP | 读码 |
| `QcomChargerDxe` | 有 `ChargerPlatform_ChkChgFwLoadRequired`、`Load Chg Fw`、`Chg Fw Load not required boot to HLOS`、`pmic_glink_set_uefi_load_adsp` | 读码 |
| 低电量路径 | 这些字符串说明**原厂 UEFI 在低电量时会自己加载充电固件**；电量正常时不加载 | 读码+推断 |
| 寄存器保留 | 进 XNU 的路径是 `adb reboot bootloader`（WARM_RESET）后 `fastboot boot`，PMIC 配置大概率沿用上次 ADSP 留下的值 | 推断 |
| 浮充电压 | 4457 mV 时还能进 +311 mA，所以此时 FV 高于 4457 mV，不是低默认值。安卓 `voltage_ocv=4550000` | 实测+推断 |
| PD | 协议在 ADSP 里，没有 ADSP 就没有 PD 合约，只有 5 V | 读码+推断 |

结论：**手机在 XNU 下由 PMIH010x 的充电硬件状态机自主充电，使用复位后留下的配置；软件既不读它的状态，也不下命令。** 这解释了为什么之前担心的“没有 ADSP 就不充电”没有发生，但这个状态依赖上一次启动留下的寄存器。从电量耗尽或冷启动进来时行为可能不同。低电量时原厂 UEFI 可能已拉起 ADSP，那时 XNU 下 ADSP 是活的，`PaganiAdsp` 的启动假设就不成立。这两点都没测过。【推断】

### 1.4 功率估算【推断】

- 窗口内电池净入 +311 mA × 4.457 V ≈ **1.4 W**，这是确定的下限。
- 拔线后 -562 mA，全程平均 -616 mA，折合整机 2.5 到 2.8 W。若窗口内负载不变，电池侧总输入约 3.9 到 4.1 W，加上转换损耗，约 5 V 0.8 到 0.9 A。
- 这个数已超过 SDP 的 500 mA，所以输入限流不是 500 mA 档，或者窗口内负载更低。**平均电流滞后，且不知道用户当时插的是 Mac 口还是充电头**，这个估算只到量级。
- 换算：6260 mAh 约 24 Wh。亮屏净入 1.4 W 时，20 % 到 100 % 约 13 小时；息屏若净入约 3 W，约 6 小时。息屏功耗没有测过；本端口不能休眠（`sleep-disabled=1`），整机待机功耗比真手机高。
- sheng 同代实测可对照（带 ADSP）：Mac 口 PD 5 V 1.1 A，电池 +0.25 到 +0.4 A，和这里的 +311 mA 同量级；PPS 8.72 V 1.3 A 时电池 +1.99 A。【读码，`porting.toml`】

### 1.5 风险

**过热**：【实测+读码】
- 本轮 5 分钟内电池从 28.1 °C 升到 41.0 °C；1203 s 那轮回安卓时 42.3 °C。
- 安卓设备树的温区是 -10/0/5/12/16/35/45/53 °C，关机阈值 72 °C，紧急 77 °C（`oplus_spec,batt-them-thr`、`poweroff_high_batt_temp`）。
- XNU 下**没有任何软件温控在管充电**：OPLUS 的策略不在，`PaganiBattery` 的温控只在 `charge≥1` 且有 ADSP 时生效，iOS 的 thermalmonitord 没有电池温度来源。
- 高负载边玩边充时可能超过 45 °C。PMIC 自身的硬件保护（JEITA、结温限流）在 XNU 下配置成什么样**不知道**。
- 当前风险中等：充电功率只有约 4 W，且发热主要来自 SoC。

**充电器看门狗**：【推断，未验证】
- 现有数据答不了。最长连续带电观察只有 25 秒。XNU 跑过 1203 s 没有复位，但那轮没有证据显示在充。
- 高通这类充电器的看门狗咬人的后果通常是停充，不是复位，所以就算存在也是“充着充着停了”，不是安全问题。
- 安卓 dmesg 里的 `[PMICWD]` 是 OPLUS 的 PMIC 看门狗，和充电器看门狗不是一回事。XNU 1203 s 没被它复位。【实测】

**电量耗尽**：XNU 期间净放电时没有低电量保护链路。`功能盘点.md` 已列，这里不重复。

### 1.6 最便宜的改进：读 PMIC 状态，不写任何东西

本轮确认 XNU 能直接读充电器：【实测】
- `[battery] spmi sid7` 表里 `26:0 27:0 28:0 29:0 2a:0 2b:0 2c:0` 说明 `0x726..0x72c` 这些外设有 EE0 通道，同时 `fd:0` 说明 eUSB2 中继 `0x7fd` 也有。
- `[haptics]` 行证明同一通道在 XNU 下读写正常（`ready ... en=1 writes=16`）。

这意味着不开 ADSP 也能知道真实的插线状态。安卓 dmesg 的 `sm8450_st_dump` 恰好包含一次 VBUS 掉电的样本（20.485 s，`usb_in_status=0`），可以和常态对比：【实测】

| 寄存器 | 有 VBUS（147 次样本） | VBUS 消失（1 次样本） | 推测含义 |
| --- | --- | --- | --- |
| `0x2910` | 0x11 | 0x04 | USBIN 实时状态，可作插线判据 |
| `0x2906` | 0x02 | 0x00 | USBIN 状态 |
| `0x2908` | 0x0f | 0x00 | USBIN 状态 |
| `0x2b06` | 0x02（曾有 0x04） | 0x00 | Type-C 汇端 Rp 档位 |
| `0x2b0b` | 0x2b / 0xab | 0x43 | Type-C 连接状态 |
| `0x2606` 低 3 位 | 3（5 s）、4（chg_en=1）、7（chg_en=0） | 7 | 充电阶段：快充、截止、关闭 |
| `0x2952` / `0x2706` | 0x1e → 0x0a → 0x28 | | 输入限流，×50 mA 为 1500/500/2000 mA，和 OPLUS 日志吻合 |

含义列是【推断】，比对依据是样本和 OPLUS 日志；寄存器文档没有公开。建议实现成只读日志，先记录，确认后再用于判定。

## 2. USB 现状

### 2.1 硬件链路

| 环节 | 事实 | 等级 |
| --- | --- | --- |
| 速率 | 只有 USB 2.0。设备树 `maximum-speed = "high-speed"`，`ssphy@88e8000` `status = "disabled"`；Mac `bcdUSB=0x0200`、`Device Speed=2` | 实测 |
| 控制器 | `dwc3@a600000`，`dr_mode=otg`，SMMU SID 0x40，核心中断 SPI 133（INTID 165），`pwr_event` SPI 130（INTID 162） | 读码 |
| 电源 | `gcc_usb30_prim_gdsc`（`0x139004`），时钟见 4.2 | 读码 |
| HS PHY | M31 eUSB2 `0x88e3000`，长度 0x29c；vdd L2D 880 mV，vdda12 L3G 1.2 V，refgen L1F 880 mV；时钟 `rpmhcc` + `tcsrcc` | 读码 |
| 中继 | PMIH010x SID7 `0xfd00`；vdd18 L15B，vdd3 L5B；安卓每次退出低功耗都写 `0x51..0x57` | 实测（dmesg） |
| 中继参数 | 设备模式 `51=0f 52=01 53=07 54=04 55=03 57=03`；主机模式 `51=07 52=02 53=07 54=01 55=00 57=01` | 读码 |
| 模拟开关 | `qcom,wcd_usbss`：WCD939x USBSS（QUP1 SE3 I2C 0x0e）在 D+/D- 路径上，UEFI 里也有 `usb_shared_wcd_i2c_reset` | 读码 |
| EUD | `0x88e0000`，`eud_enable_reg 0x88e2000`，`eud_detect_reg 0xc278000`，`qcom,secure-eud-en` | 读码 |
| Type-C/PD | `qcom,ucsi-glink`，在 ADSP 里；手机默认是汇端，CC 由 PMIC 硬件处理 | 读码 |

### 2.2 为什么 XNU 期间 Mac 看不到手机

三层原因：

1. **没有驱动**：本内核 30 个预链接组件里没有 USB 类【实测，`kernel.json`】；ADT 没有 `usb-complex`、`usb-device`、`otgphyctrl`【读码】；`UniXNU-Pagani-Drivers/drivers` 里没有 DWC3 驱动【实测】。
2. **没有上拉**：HS 设备要 `DCTL.RUN=1` 且 PHY 工作，D+ 才有上拉。没人置位，Mac 就当端口空着。【推断】
3. **固件已经退场**：`fastboot boot` 之后原厂 UEFI 收尾，Mu UEFI 又自己起一遍。退场时控制器具体什么状态要探针才知道（2.3）。

充电正常和 USB 不通不矛盾：VBUS、CC 走 PMIC，D+/D- 走 DWC3。

这和 `usb-not-enumerated-after-xnu-reset` 那条记忆是另一个问题：那是**回到安卓后**偶尔认不到，要插拔线。原因没有查清，大概率和 XNU 复位时控制器、中继或 PD 状态没有收尾有关【推断】。有了 USB 驱动，正常收尾后也许能消失。

### 2.3 UEFI 留下什么状态

Mu UEFI 带了一整套高通 USB 驱动：`UsbConfigDxe`、`UsbfnDwc3Dxe`、`UsbInitDxe`、`XhciDxe`、`UsbDeviceDxe`、`UsbMsdDxe`。【读码】

反汇编结论：【读码】

- `UsbConfigDxe` 的 ExitBootServices 处理（`0x4b08`）：关事件；调 core 操作表 +0x60，即 `SSUsb1SetPhyNonDrivingMode(core, 1)`，参数 1 时是空操作；释放内存；原本会 detach HAL IOMMU 域，`README` 的补丁把这个跳过了。**它不关 GDSC、不关时钟、不复位 PHY。**
- 这个补丁（`0x4bb0` 的 `cbz` 改成 `b`）意味着 Mu 退出后 SID 0x40 的 SMMU 映射可能还在。
- `UsbfnExitBootService`（`0x1c50`）主要释放 TRB 和内存。我没有看到它直接写 `DCTL`，但没有逐行确认。
- `SSUsb1InitCommon` 有 `skipped due to RunStop bit set 0x%X` 分支，`UsbStartDevice` 有 `already enumerate in mode %d, skip`，说明固件自己就考虑了“控制器已在运行”的交接。
- 固件内含完整 PHY 流程：`InitHSUSBPhy`、`Repeater_init`、`usb_repeater_i2c_tune`、`usb_shared_process_phy_reg_array`，配置来自 `hs_phy_cfg`、`repeater_cfg`、`usb_spmi_cfg` 等 DT 节点（这些数据不在 `Binaries/pagani` 的文件里）。

**不确定**：Mu 这条启动路径究竟有没有真的启动 USB。DXE 有 USB 键盘和大容量存储，但 UniXNU 菜单跳过了部分 BDS 流程。控制器可能处于 Device 停止、Host 停止或掉电。所以 P0 必须先读。

对“能否沿用已初始化的 PHY，只接管控制器”的回答：**同类设备都是这么做的，但 13T 要探针证实**。`QcomXnuUsbDebug` 明确要求“启用前固件完成 USB 供电、时钟和 PHY 初始化”，它的检查项就是 GDSC、5 个时钟、复位、DEVICE 角色、PHY 状态。alioth、munch、nabu 用的都是这个模块。fuxi 走 UEFI 里 `KEEP_PHY` 的路子，但它的 UEFI 收尾一直没有验证通过（`DEVCTRLHLT=0`）。【读码，上游记录】

### 2.4 这条路对调试的价值

现在的日志只能在复位后读 ramoops 和 UFS 窗口，`records` 还有环形覆盖缺口。本轮 merged.log 的缺失序号就有 830-1037、1050-1244、1249-1429 等。ACM 控制台的好处：

- 实时看日志，不用等复位；
- panic 时轮询清空，上游 `QcomXnuUsbDebug` 的预算约 2 秒；
- 有回传通道后，可以下命令复位，省掉 420 s 的固定等待；
- 上游 alioth 的经验：USB 控制台是唯一能保留记录到面板不再刷新之后的通道，但它自己死于首个错误（`d->failed` 锁死），要做重试。

## 3. iOS 这边已经有什么

### 3.1 内核缓存里的 USB 组件【实测，kext 列表与匹配字典】

```text
AppleT8011USBArbitrator   IONameMatch usb-complex,t8011  on AppleARMIODevice
AppleT8011USBPhy          IONameMatch otgphyctrl,t8011   on AppleARMIODevice
AppleT8011USBXDCI         IONameMatch usb-device,t8011   on AppleEmbeddedUSBNub
  : AppleUSBXDCI : IOUSBDeviceController
IOUSBDeviceConfigurator   IOPropertyMatch ConfigurationType = standardBringup / ncmBringup / ...
  standardBringup  Interfaces = [AppleUSBMux]
  ncmBringup       Interfaces = [AppleUSBNCMControl, AppleUSBNCMData]
AppleUSBDeviceMux         USBDeviceFunction = AppleUSBMux，要 IOBSD
AppleUSBDeviceNCM         USBDeviceFunction = AppleUSBNCMControl/Data
AppleUSBEthernetDevice    USBDeviceFunction = AppleUSBEthernet
```

此外还有完整的主机栈：`IOUSBHostFamily`、`AppleUSBXHCI`、`AppleUSBHostT8011`、`AppleUSBHub`、`IOUSBMassStorageDriver`、`AppleUSBAudio`、`usb.cdc.ecm/ncm/acm`。

之前 alioth 的分析把 `AppleSynopsysOTGDevice` 当作设备控制器。它匹配的是 `usb-device,t7000`（DWC2 系）；J120 实际用的是 `usb-device,t8011` 的 `AppleT8011USBXDCI`。

### 3.2 AppleUSBXDCI 就是 DWC3 驱动【读码，反汇编】

寄存器访问走虚函数（槽 246 读、247 写、250 读改写），偏移以 `0xC000` 为基：

| 偏移 | DWC3 寄存器 | 用法 |
| --- | --- | --- |
| 0x110 | GCTL | `PRTCAPDIR` 掩码 0x3000 写 2（设备） |
| 0x120、0x128 | GSNPSID、GUID | 只读，打印 |
| 0x200 | GUSB2PHYCFG | 清 bit 6（SUSPHY） |
| 0x700 | DCFG | 速度、地址、`NUMP`（掩码 0x3e0000） |
| 0x704 | DCTL | 写 `0x40000000`（CSFTRST） |
| 0x708、0x70c | DEVTEN、DSTS | |
| 0x710、0x714 | DGCMDPAR、DGCMD | |
| 0x720 | DALEPENA | |
| 0x80c 起 | DEPCMD | `sendEndpointCommand` |

它有完整的 TRB 池、EP0 状态机、事件处理、等时传输和 `tunable_setting`。外部调用全是通用 KPI：`IOBufferMemoryDescriptor`、`IODMACommand`、`IOFilterInterruptEventSource`、`IOTimerEventSource`、`IOCommandGate`。**没有 PMGR、DART、AIC 的直接调用。** 启动参数 `force-usb-highspeed` 正好是 13T 需要的。

平台子类只有约 8 KB 代码，重写 19 个槽，其中有实际内容的：

| 槽 | 名称 | T8011 做的事 | 13T 要做的事 |
| --- | --- | --- | --- |
| 86 | `start` | 映射寄存器写 `this+0x230/0x238`，读 `phy-id`，再调父类 | 映射 `0x0a60c000`，电源与 SMMU |
| 132、267 | `message`、`messageGated` | 线缆类型消息 | 接 VBUS 判定 |
| 179、255 | 线缆判断 | 比较 4 种线缆类型 | 看 PMIC 的 VBUS 状态 |
| 190 | `setCurrentLimit` | `function-usb_500_100` | 空操作 |
| 196、197 | reset、connection done | 记录速度，改 `DCTL` | 透传 |
| 256、257 | `powerOn`、`powerOff` | 经 `AppleUSBPhyServiceClient` 上下电 PHY | 我们的 PHY 序列 |
| 258 | 配置 | 把 `{4, 750, 16, 3}` 写入 `this+0x2410`，首字段是 interrupter 数 | 高通只有一根线，要写 1 |
| 264、265 | | 空函数 | 空函数 |

代码在 `this+0x2410 < 4` 时有另一条分支，说明少于 4 个 interrupter 是支持的路径，但我没有走完这条分支。【读码，未逐行】

项目已有子类化先例：358 槽的显示 ABI、`PaganiSensors` 的 HID 类，都是绑定固定内核缓存再晚注册。【读码】

### 3.3 用户态

- `lockdownd` 本轮 22.7 s 已启动，日志里没有退出记录；`ptpd`、`configd` 也在跑。【实测】
- 所以 `功能盘点.md` 说的“lockdownd 等 `IOPortTransportState`”不准确。`com.apple.mobile.lockdown.plist` 的 `IOPortTransportState` 匹配是拉起事件之一，它已经被别的事件拉起了。`IOPortTransportState` 这个类在 `IOAccessoryManager` 里。【实测+读码】
- `lockdownd` 字符串：`AppleUSBDeviceMux`、`USBDeviceFunction`、`AppleUSBMux`、`IOUSBDeviceControllerForceOffBus`、`Pinged configd, USB is now enabled`。【读码】
- 上游 alioth 的记录：`usbmuxd` 是 Mac 侧程序，设备侧是 `lockdownd` 监听 TCP 62078，`AppleUSBDeviceMux` 只做 `sock_connect`。本机字符串（`sock_socket`、`handleMuxTCPInput`）与此一致。【读码】这意味着 Mux 一通，**Mac 可以转发到手机本地任意 TCP 端口**。
- `IOUSBDeviceController` 的 `prepareDefaults` 读 `<类名>-configuration` 或 `IOUSBDeviceController-configuration` 启动参数，也读 provider 的 `configuration-string`，再写到 `ConfigurationType`。所以**不靠用户态也能上线**：给 nub 一个 `configuration-string=standardBringup` 就够。VID/PID 等来自 `vendor-id`、`product-id`、`product-string`。【读码】

### 3.4 离线预链接实验【实测】

输出全部放在草稿目录，没有动工具和构建树；NCM 与主机两项用的是草稿里改过的工具副本。

| 新增根 | 工具 | 结果 | 组件 |
| --- | --- | --- | --- |
| 无（现状） | 原版 | | 30 |
| `IOUSBDeviceFamily`、`AppleUSBXDCI`、`AppleUSBDeviceMux` | 原版 | 成功 | **34** |
| 上面 + `AppleUSBEthernetDevice` | 原版 | 成功 | 36（带入 `IONetworkingFamily`） |
| 上面改 `AppleUSBDeviceNCM` | 原版 | 失败：`Missing declared dependency com.apple.driver.watchdog` | |
| `AppleUSBDeviceNCM` | 副本放宽 2 处，补 4 个根 | 成功 | 51 |
| NCM + XHCI、大容量存储、音频、HID、Hub | 同上 | 成功 | 59 |

NCM 失败的链条：`AppleUSBDeviceNCM` → `usb.networking` → `IOUSBHostFamily` → `AppleSMC` → 缺失的 `watchdog`、`IOACPIFamily`。补全后会带入 `AppleSMC`、`RTBuddy`、`AppleARMPMU`、`IOPCIFamily`、`IOSkywalkFamily` 等，运行时风险大。

**只是链接通过，运行时完全没有验证**，工具报告也写着 `runtime_qualified=False`。

**集成冲突**：`PaganiWifiNet.cpp` 在内核里自建了名为 `IONetworkInterface`、`IONetworkStack` 的类。一旦预链接真的 `IONetworkingFamily`，两者同名冲突。所以 iOS 路径要用 34 组件方案，不带 `IONetworkingFamily`。【读码+推断】

## 4. 设备模式驱动的最小量

### 4.1 上电到枚举的清单

下列是冷启动的完整清单。P0 证实固件已做的步骤可以跳过。

1. **电压**：RPMh 投 L2D、L3G、L1F、L15B、L5B，取设备树下限。`Sm8750Platform/Rpmh.h` 已有。
2. **电源域与时钟**：见 4.2。
3. **中继**：SPMI EE0 写 `0x7fd46` 的 bit 7（`EUSB2_RPTR_EN`），写 tune 表，等 `0x7fd08` bit 7（`RPTR_OK`）。偏移来自上游的 `qcom,pm8550b-eusb2-repeater`，PMIH010x 是否相同要 P0 读回确认；tune 值用设备树的，不用上游默认表。
4. **M31 PHY**，上游顺序：稳压器 → 中继 → 参考时钟 → 复位 5 µs → 开 override、`POR`、`PHY_ENABLE`、清 `PLL_EN`、`FSEL_SEL`、`FSEL=6`（38.4 MHz）→ tune（`HSTX_PE=0 SWING=7 SLEW=0 PLL_LOCK=0`）→ 置 `SUSPEND_N`、`SLEEPM`，清 `SIDDQ`、`POR`、override。偏移 0x3c、0x50、0x54、0x64、0x94、0xb8、0x154、0x15c、0x168、0x16c、0x17c。
5. **QSCRATCH**：`HS_PHY_CTRL` 的 bit 20、28 软件置 VBUS 有效（SM8550 是 `0xf8810`，SM8750 要核对；邮件列表里 13T 同款 SoC 的 glue 基址是 `0xa6f8800`，方向一致）。只用 USB2 时可能还要把 PIPE 时钟切到 UTMI【推断，待核】。
6. **控制器**：`DCTL.RUN=0` 等 `DEVCTRLHLT`，`CSFTRST`，`GCTL.PRTCAPDIR=2`，`GUSB2PHYCFG` 清 `SUSPHY`、`ENBLSLPM`，`DCFG` 高速和 `NUMP`，事件缓冲，`DEVTEN`，`DEPSTARTCFG`，EP0 两个方向，`DALEPENA=3`，`DCTL.RUN=1`，挂 SETUP TRB。
7. **中断**：`armcpu_spi_attach(165, false, ...)`，已有接口。首版用轮询更稳。
8. **DMA**：见 4.3。

### 4.2 GCC 偏移：不能抄 SM8550【读码】

`QcomXnuUsbDebug` 的 `Sm8550.h` 里 `QUD_GCC_CBCR_OFFSETS = {0x39018, 0x39088, 0x3908c, 0x39028, 0x39024}`。SM8750 的上游 `gcc-sm8750.c` 是：

| 时钟 | SM8750 偏移 | SM8550 | 同否 |
| --- | --- | --- | --- |
| `gcc_usb30_prim_master_clk` | 0x39018 | 0x39018 | 同 |
| `gcc_aggre_usb3_prim_axi_clk` | **0x39090** | 0x39088 | **不同** |
| `gcc_cfg_noc_usb3_prim_axi_clk` | 0x3908c | 0x3908c | 同 |
| `gcc_usb30_prim_mock_utmi_clk` | **0x3902c** | 0x39028 | **不同** |
| `gcc_usb30_prim_sleep_clk` | **0x39028** | 0x39024 | **不同** |

其他：`GCC_USB30_PRIM_BCR 0x39000`，`gcc_usb30_prim_gdsc 0x39004`（设备树 `0x139004` 印证），`gcc_usb3_phy_gdsc 0x50018`，`gcc_usb30_prim_master_clk_src 0x39030`（200 MHz 由 GPLL0/3），`mock_utmi_clk_src 0x39048`（19.2 MHz）。设备树的时钟号 `0x9c=156` 与上游 `GCC_USB30_PRIM_MASTER_CLK` 一致。

### 4.3 SMMU 与缓存

| 方案 | 做法 | 状态 |
| --- | --- | --- |
| 沿用固件映射 | `UsbConfigDxe` 补丁跳过了 detach，SID 0x40 可能还挂着 | P0 看 SMR、S2CR、SCTLR |
| 共用 UFS 旁路银行 | 照 `SpeakerSmmu.h`：只写一个 S2CR、一个 SMR，指向流 0x60 的银行 | 代码有，扬声器这步还没上机 |
| 私有 S1 域 | 上游 `QcomUsbDmaDomain.c`，只映射 32 KiB，不走 BYPASS | 来源设备有历史记录，独立模块尚未重新验证；13T 要 Gunyah 接受 |

- 设备树 `dma-coherent`，靠 S1 页属性保证一致性。走旁路时 NativeUfs 是显式 `cacheClean`、`cacheCleanInvalidate`。所以**旁路下要按非一致性处理**。【读码+推断】
- 自研驱动好办：TRB、事件环、EP0 放非缓存内存或显式维护。
- `AppleUSBXDCI` 不好办：`IOBufferMemoryDescriptor` 选项 `0x13`（缓存内存），`IODMACommand::withSpecification` 的 `mappingOptions=0`，**没有 `kNonCoherent`**，它假定硬件一致。【读码】这是 B 路最大的技术风险。可能的解法是给 SID 0x40 建恒等映射的 S1 域，用 WB 属性走安卓那条一致性通路，但要验证 Gunyah 接受，且要覆盖 XNU 的 6 GiB。
- DMA 地址：XNU 内存在 `0x8c0000000` 以上，超 32 位。TRB 高位够用，要核对 SMMU 的地址宽度检查（`speaker_smmu_plan` 已做）。

### 4.4 上游已知坑（kebab PR #7、QcomXnuUsbDebug）【读码】

- `DEPCMDPAR0` 是地址高 32 位，写反取错 TRB。
- 先 `RUN`，再挂第一个 SETUP TRB。
- 无数据阶段用 STATUS2，有数据阶段用 STATUS3。
- `DCFG.NUMP` 要按 `GHWPARAMS7` 计算，上限 16；`QcomXnuUsbDebug` 没有设置。
- 重配置时 `DEPSTARTCFG` 用索引 2，保留 EP0。
- `SET_LINE_CODING` 的 OUT 数据阶段不能复用 SETUP 的 TRB 槽。
- 事件计数掩码 0xfffc 并拒绝不合理值。
- 缓冲先交给设备，再发布 TRB。
- 每个端点只有 1 个 4 KiB TRB，`QcomXnuUsbDebug` 够控制台用，ECM 要改环。
- 该 PR 记录“首次 DMA 后板子进 EDL”，说明 DMA 路径配错的后果是整机层面的。

## 5. 两条路比较

人日按熟悉本项目的连续工作估算，都是【推断】。

| 项 | A：自研内核 CDC（ACM 后 ECM） | B：子类化 `AppleUSBXDCI` + Mux |
| --- | --- | --- |
| 代码来源 | `QcomXnuUsbDebug` 的 DWC3 核心（MIT，派生自 m1n1，`Dwc3Regs.h` 带 TI BSD 声明），加 13T 平台层 | Apple 驱动原样，自写平台子类和 nub |
| 新代码量 | ACM 约 800 行；ECM 再加 800 到 1200 行 | 平台子类约 400 到 800 行，加 ABI 校验脚本 |
| 预链接 | 不动 | 新增 4 个组件（34 个） |
| PHY 与电源 | 自己做 | 同样要做，两边共用 |
| DMA 一致性 | 自己控制，风险小 | 假定硬件一致，**未解决** |
| 中断 | 可轮询 | 要让 nub 支持 `IOFilterInterruptEventSource`，桥接到 `armcpu_spi_attach` |
| 高层协议 | 自己写描述符、CDC 请求 | 现成 |
| panic 时可用 | 可以，轮询路径 | 不行，工作循环已停 |
| 收益 | 实时日志、控制通道、网卡 | Finder/Xcode 识别、`idevicesyslog`、AFC、`installd`、配对、任意 TCP 转发 |
| 费力点 | 速度优化，描述符细节 | 267 槽 ABI、`lockdownd` 配对对假 AKS 的依赖、身份不一致（iPhone16,1 与 J120） |
| 工期 | ACM 3 到 5 天，ECM 3 到 5 天 | 5 到 10 天，前提是 P1 已跑通 |
| 上机轮数 | ACM 4 到 8，ECM 3 到 5 | 6 到 12 |

判断：

- **A 的 ACM 必做**，因为它是调试通道，其他工作都受益，且和 B 共用 PHY、SMMU 成果。
- **B 对“装 App”最有用**：用户问的“能不能装 App”，走 `installd` 要 Mux 和 lockdown。ECM 只能给网，不能直接给这些。
- **A 的 ECM 是否要做取决于一致性结果**。如果 S1 恒等域能让 `AppleUSBXDCI` 跑通，Mux 的 TCP 转发已能覆盖文件和调试，ECM 只剩“用 Mac 的网”这一项收益，可以推迟。如果一致性走不通，ECM 就是主力。
- 两者互斥占用控制器，用启动参数选其一（比如 `pagani-usb=1` 自研，`=2` Apple 栈）。

ECM 与 NCM：Mac 自带两者（macOS 27 已加载 `com.apple.driver.usb.cdc.ecm/ncm/acm`，另有 `AppleUserECM.dext`）。【实测】自研先做 ECM，一帧一个传输，简单，sm-t733、sweet 都是 ECM。NCM 聚合后吞吐更好，但要多写 NTB 打包，只有 ECM 跑满后才值得。USB 2.0 理论 bulk 约 53 MB/s，ECM 实际大概 20 到 35 MB/s。【推断】

## 6. USB 网络和 Wi‑Fi

### 6.1 网卡怎么进系统【读码】

不需要 `IONetworkingFamily`。sheng 的 `WifiNet.cpp` 和本仓库 `PaganiWifiNet.cpp` 的做法：

1. `ifnet_allocate_extended`、`ifnet_attach`、`bpf_attach`，`ether_demux` 等标准回调；
2. 自建 `IONetworkInterface` 注册项（`BSD Name`、`IOInterfaceName`、`IOInterfaceNamePrefix`、`IOInterfaceType`、`IOInterfaceUnit`、`IOMACAddress`）；
3. 自建 `IONetworkStack` 和用户客户端，configd 的 InterfaceNamer 要等它；
4. 用 `kev_post_msg` 发链路事件。

sheng 实机证据：开机 31 s 拿到 DHCP 地址，ADT 没有 `wlan` 节点。【读码，`porting.toml`】

系统侧有现成机制：`com.apple.usb.networking.addNetworkInterface.plist` 在 `IONetworkInterface` 出现时拉起 `/usr/sbin/addNetworkInterface`，参数里的控制器名单有 `AppleUSBDeviceNCMData`、`AppleUSBECMData`、`AppleUSBEthernetHost`、`IOUserNetworkEthernet` 等，命中就 `SCNetworkSetAddService` 建服务。所以**自建控制器类用名单里的名字**，不必改种子。这个机制我没有上机验证，且 `addNetworkInterface` 按类名查找的方式要核对。

要和 `PaganiWifiNet.cpp` 合并这几个类，不能各定义一份。Wi‑Fi 占 `en0` 时 USB 用 `en1`。

地址：`configd` 走 DHCP。Mac 开“互联网共享”就有 DHCP 和 NAT，手机能经 Mac 上网；不开时双方落到 169.254 链路本地地址。sm-t733 的 ECM 跑通过 `debugserver` 采集，sweet 用 `192.168.7.1/24` 手配。【读码，上游 README】

### 6.2 对比

| 项 | USB ECM | Wi‑Fi（`pagani-wifi=6`） |
| --- | --- | --- |
| 硬件风险 | 控制器 fastboot 下就在用，同类十几台设备跑通 | PCIe、MHI、QMI、固件，从未上机，`wifi/review.md` 列了低端窗口、SPI 141、PHY 供电等未知 |
| 代码现状 | 没有，但有 MIT 底子 | 已写完，等上机 |
| 依赖 | 要线、要 Mac | 要路由器和 `join.txt` |
| 对日用 | 没用 | 必需 |
| 对开发 | 很大 | 一般 |

**USB 网络大概率比 Wi‑Fi 先通，但它不解决日用上网。** 两边不互相阻塞，都要做。【推断】

## 7. 主机模式、U 盘、耳机

可行性：**可以做，优先级低，必须排在 ADSP 之后。**

| 项 | 要什么 | 判断 |
| --- | --- | --- |
| VBUS 输出 | PMIC 升压。安卓走 ADSP 和 UCSI；EE0 虽有 `0x727` 通道，但不建议绕过 | 这是唯一有硬件风险的一步：充电器在线时开升压会对灌 |
| 角色切换 | CC 从汇端切 DRP/源端，安卓走 `ucsi-glink` | 要 ADSP |
| 控制器 | DWC3 host + `AppleUSBHostT8011` 或子类；`AppleEmbeddedUSB` 里有 `force-usb-host`，但它要 Apple 的仲裁器 | 同样要 XHCI 平台子类 |
| 中继 | 改用 `host-param-override-seq`，上游还要 `FORCE_EN_5/VAL_5` | 已知 |
| 预链接 | 59 组件方案，工具要放宽，带入 `AppleSMC` 等 | 运行时风险大 |
| U 盘 | `IOUSBMassStorageDriver`，文件 App 外接盘 | 主机通了就有 |
| USB-C 数字耳机 | `AppleUSBAudio`，要音频路由配合 | 主机通后可试 |
| USB-C 模拟耳机 | WCD939x USBSS + MBHC + ADSP 音频 | C 级，`功能盘点.md` 已列 |
| 外接显示 | QMP 关闭 | 不可用 |

这一层在“接近日用”目标里排在 Wi‑Fi、蓝牙、相机、声音之后。

## 8. 分阶段计划

### P0：只读探针（随下一包，不单独上机）

- 内容：
  - GCC：`0x39004` GDSC、4.2 节的 5 个 CBCR、`0x39000` BCR、`0x39030` RCG。
  - **只有 GDSC 和时钟都开时**才读 DWC3：`GSNPSID`、`GCTL`、`GUSB2PHYCFG`、`GUSB3PIPECTL`、`DCFG`、`DCTL`、`DSTS`、`GHWPARAMS0-7`、QSCRATCH。
  - PHY：`0x88e3000` 的几个寄存器，同样要先确认参考时钟。
  - 中继：`0x7fd08`、`0x7fd46`、`0x7fd51-57`，观察通道读。
  - 充电器：1.6 节的寄存器，开机一次，插拔各一次。
  - SMMU：SID 0x40 的 SMR、S2CR、银行 SCTLR、TTBR0。
- 判据：拿到完整一张表。
- 决策：
  - GDSC、时钟都开，`PRTCAPDIR=2`，PHY 未掉电：P1 走继承路线。
  - 控制器在 Host 模式：要先停 xHCI。
  - 掉电：P1 走冷启动路线，工期加 2 天。
  - `GSNPSID` 高 16 位是 `0x3331` 的话，B 路兼容性要重新评估。
- 风险：读未上电寄存器会总线错误。靠先读 GCC 防护；`pagani-wdog=30` 能兜底复位。

### P1：CDC-ACM 控制台

- 新驱动 `PaganiUsb`：移植 `Dwc3.c`，保留 MIT 与 TI 声明；平台层用 4.2 的偏移；加 `NUMP`、重试；日志接 `RamoopsLog` 的同一路输出。
- 判据：
  1. Mac `ioreg` 看到 480 Mb/s 设备，`/dev/tty.usbmodem*` 能读；
  2. 420 s 全程无丢包（有计数器）；
  3. 手动触发 panic，日志到 `END` 标记；
  4. 拔插线 5 次能恢复；
  5. GPU 桌面、触摸、UFS 回归无变化。
- 风险：DMA 路径配错会整机复位甚至进 CrashDump 或 9008，用户要在场，首次 DMA 单独一轮。

### P1.5：一致性验证（一轮）

在 P1 的驱动上放一块缓存内存做事件环和 TRB，不做缓存维护，看 DMA 是否能看到。分别试旁路银行和 S1 恒等域。

判据：连续 1000 次传输无错。通过则 P3 优先，失败则 P2 优先。

### P2：ECM 网卡

- 内容：bulk 环形 TRB、中断、`ifnet`、注册项，描述符 ACM + ECM 复合（IAD）。
- 判据：
  1. Mac 出现网卡，`ping` 平均小于 2 ms；
  2. 手机拿到地址；
  3. 持续 10 分钟 ≥ 15 MB/s 无错；
  4. 开互联网共享后手机能解析和访问外网；
  5. GPU 卡顿统计（`stall-report`）不恶化。

### P3：iOS 正规路径

- 内容：预链接 34 组件方案；`PaganiUSBXDCI : AppleUSBXDCI`（ABI 校验照 358 槽显示类）；自建 nub 提供中断、`configuration-string=standardBringup`、`vendor-id=0x05ac`、`product-id` 取 iPad 段；`force-usb-highspeed`；interrupter=1。
- 判据：
  1. `ioreg` 看到 Mux 接口；
  2. Mac `usbmuxd` 列出设备；
  3. 配对对话框出现；
  4. `idevicesyslog` 有输出；
  5. `AFC` 能传文件；
  6. `ideviceinstaller` 能装一个测试包（这一步还受代码签名工作线影响）。
- 风险：`IOUSBDeviceFamily` 启动时的未知依赖；配对依赖 AKS 和激活状态，本端口都是替身；Mac 可能按 ProductType 拉 DDI。

### P4：充电完善（并行）

1. `pagani-battery-log=2` 测一轮：电量 50 到 70 %，开机前插好，分别 Mac 口和充电头各 30 分钟。**判据**：`rm` 持续上升，电流不中途掉到 0；看门狗问题由此回答。
2. 把 1.6 节状态接进判定，替代纯电流推断。
3. ADSP 上机后走 PD，这才是提速的路。80 W 私有快充不做。
4. 温度护栏：先只告警，确认寄存器后再考虑超温挂起输入。

### 8.5 启动参数预算

本轮 `-v` 行 **996 字节**，上限 1000。【实测】`pagani-usb=1` 就要 13 字节。要么删调试项（`pagani-input-script=1` 22 字节、`pagani-kbtrace=1` 17 字节），要么把默认值放进 `/chosen` 或编译默认。这条不解决，下一包加不了开关。

### 8.6 风险表

| 风险 | 后果（事实依据） | 缓解 |
| --- | --- | --- |
| PHY 时钟没起 | `CSFTRST` 不自清；Apple 驱动也有 `Core reset timed out` | 有界等待，失败退出 |
| `FSEL` 错 | PLL 不锁，枚举失败；上游回读不符只打日志 | 回读，打日志 |
| tune 错 | 信号质量差，枚举不稳，不伤硬件【推断】 | 用设备树值 |
| 中继未就绪 | `RPTR_OK` 超时，上游返回错误 | 超时退出 |
| 访问未上电寄存器 | 总线错误，整机复位【推断，高通常见】 | 先看 GCC，再访问 |
| SMMU 映射错 | sheng 给音频流配上下文时整机复位；kebab 首次 DMA 进 EDL | 单独一轮，用户在场，不写全局寄存器 |
| 写错 SPMI | 中继只影响 USB；**充电器寄存器写错后果未知** | 写白名单只放 `0x7fd46`、`0x7fd51-57`，照 `HapticsSpmi.c` |
| 碰 EUD | USB 路由改变，LDO 常开（UEFI 有 `EUD is active. PRIM USB2PHY LDO ON`） | 绝不写 `0x88e2000`、`0xc278000` |
| USBSS 开关不在 USB 通路 | D+/D- 断开，枚举失败，不伤硬件【推断】 | 枚举失败时先查它 |
| 过早上拉 | 可能干扰 PMIC 的 BC1.2 检测，误判成 SDP，限流变小【推断】 | 等 VBUS 稳定约 2 秒再 `RUN` |
| 开 OTG 升压 | 与外部 VBUS 对灌 | 主机模式放到 ADSP 之后 |
| 新驱动拖慢系统 | 轮询占 CPU，加重卡顿 | 5 ms 工作线程，限流，看 `stall-report` |

## 9. 先例与许可

| 项目 | SoC | 内容 | 用法 |
| --- | --- | --- | --- |
| `QcomXnuUsbDebug`（xiaoleGun、FFT-infra） | SM8550 基线 | CDC-ACM、控制、截图，MIT，派生自 m1n1 | 可带署名移植，逐文件保留声明 |
| alioth、munch | SM8250 | 控制台实机枚举 `1209:316d`，记录了 SMR 回收 | 参考 |
| nabu-usb-cdc | SM8150 | S2CR BYPASS 被改写，改走固件旁路银行；实测 Full Speed | 参考，MIT |
| picasso PR | SM7250 | `1209:0001` 480 Mb/s，恢复全部缓冲日志 | 参考 |
| kebab PR #7 | | EP0 修复清单 | 参考 |
| sm-t733 | SM7325 | **USB 调试与 ECM，日志、调用栈采样、`debugserver`** | `NativeUsbDebug` 原作者保留所有权利，**不能移植** |
| sweet（zeroninevibedslop） | SM7150 | **CDC-ECM 实时 UDP 遥测**，iOS 16 | 参考 |
| giulia | SM8650 | USB 终端工具已进主仓库 `scripts/debug_terminal.py` | Mac 侧工具可参考 |
| 13T 同 SoC（hummer、dada 等） | SM8750 | 设备表摘要里没有 USB（未逐个读仓库） | 无 |
| sheng | SM8550 | 快照里没有 DWC3 驱动 | 只有网卡接入可用 |

本轮规则明确可移植的是 sheng（BSD-2-Clause-Patent）和 Adreno740、AdrenoMetal（MIT）。`QcomXnuUsbDebug` 的文件头是 MIT，同属宽松许可，但不在这份名单里，移植前要确认。其他上游代码逐文件看许可。

## 10. 未确认项

1. 296 s 时用户插的是 Mac 口还是充电头，功率多大。
2. 充电器看门狗是否启用，浮充电压、JEITA、输入限流在 XNU 下的实际值。
3. Mu UEFI 这条路径有没有启动 USB，XNU 入口时控制器、PHY、SMMU 的状态。
4. `GSNPSID` 是 DWC_usb3 还是 DWC_usb31。
5. M31 在 Android 驱动里的序列是否和上游一致（这次只看了上游）。
6. WCD939x USBSS 默认通路。
7. 只用 USB2 时 PIPE 时钟的处理。
8. `AppleUSBXDCI` 单 interrupter 分支、`IOUSBDeviceFamily` 启动路径、`addNetworkInterface` 按类名匹配的细节（只读了部分）。
9. 回到安卓后偶尔认不到 USB 的根因。
10. 中段电量的实际充电速度，这是用户最关心却最缺数据的一项。

## 11. 实验记录

- 安卓只读：`dumpsys battery`、`/sys/class/power_supply/*`、`/sys/class/oplus_chg/*`、`dmesg`、`getprop`、`/proc/iomem`；`debugfs` 未挂载，没有挂载，没有读 DWC3 寄存器。
- 设备身份：本文引用的安卓数据都采自 13T（序列号 `SERIAL_13T`，`PKX110`），`dmesg` 里有 `ro.product.model=PKX110`。写完报告核对时，Mac 上的 USB 设备已换成另一台（`SERIAL_OTHER_DEVICE`，`OPD2513`），13T 不在 USB 上；我对那台只跑了 `uptime`、`getprop`、`date`、`dumpsys battery` 的只读查询，它的数据没有进本文。这次变化不是本工作流的操作造成的。
- Mac：`ioreg -p IOUSB`、`kmutil showloaded`、`/System/Library/DriverExtensions`。
- 反汇编：`cstool`、`llvm-objdump`；类结构：`research/tools/kext_classes.py`（`--kext` 同时传父类才能解析槽）。
- 预链接：`python3 -B kits/NativePrelink/emit_prelink.py --output <草稿目录>`，脚本与 JSON 在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/prelink/`，会话重启后可能被清空。

## 来源

- [QcomXnuUsbDebug](https://github.com/xiaoleGun/QcomXnuUsbDebug)，[FFT-infra 分支](https://github.com/FFT-infra/QcomXnuUsbDebug)
- [nabu-usb-cdc](https://github.com/Lux12138/nabu-usb-cdc)
- [unixnu-sm-t733](https://github.com/RigoLigoRLC/unixnu-sm-t733)
- [UniXNU-sweet（zeroninevibedslop）](https://github.com/zeroninevibedslop/UniXNU-sweet)
- [UniXNU-kebab PR #7](https://github.com/GFander/UniXNU-kebab/pull/7)
- [picasso USB console PR](https://github.com/YinCQL/UniXNU-picasso/pull/1)
- [Linux phy-qcom-m31-eusb2.c](https://raw.githubusercontent.com/torvalds/linux/master/drivers/phy/qualcomm/phy-qcom-m31-eusb2.c)、[phy-qcom-eusb2-repeater.c](https://raw.githubusercontent.com/torvalds/linux/master/drivers/phy/qualcomm/phy-qcom-eusb2-repeater.c)、[gcc-sm8750.c](https://raw.githubusercontent.com/torvalds/linux/master/drivers/clk/qcom/gcc-sm8750.c)（只取事实）
- [SM8750 USB 补丁系列](https://lists.infradead.org/pipermail/linux-phy/2025-April/021385.html)
- 本地：`参考资料/上游-20261031/UniXNU-UniXNU/docs`（alioth、munch、fuxi、sweet、giulia 记录，`devices.toml`）
