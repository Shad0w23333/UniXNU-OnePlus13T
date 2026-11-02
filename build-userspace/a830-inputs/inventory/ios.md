# iOS 侧需求盘点（工作流子任务 key = `ios`）

- 日期：2026-10-31 凌晨。只读调研：没碰手机，没改任何源码树，没提交。系统卷 `osmnt-21H461` 只读。
- 对象：iPadOS 17.7.11（21H461）根卷 + iPhone16,1 身份（根 `model iPhone16,1`、`target-type J120`、`/product` 为 iPhone 15 Pro、
  `artwork-device-subtype 2556`，见 `工具/make-a830-layout.sh` 174–201 行）。
- 问题：每个用户可见功能，iOS 17.7 用户态要内核/IOKit/设备树给它什么；最便宜、最正确的接入点在哪；现在缺哪一层。
- 证据来源与标记：
  - 【plist】`osmnt-21H461/System/Library/xpc/launchd.plist`（launchd 实际读取的已签名守护进程表，561 项；已导出
    `inventory/ios-data/launchd-conditions.tsv`）、`System/Library/LaunchDaemons/*.plist`、`System/Library/HIDPlugins/*/Info.plist`。
  - 【KC】J120 原厂内核缓存 `UniXNU/out/ipsw/21H461/kernelcache.decompressed` 的 `__PRELINK_INFO`（237 个 kext 的 IOKitPersonalities，
    已导出 `inventory/ios-data/kexts.json`）。
  - 【MG】libMobileGestalt 处理函数静态分析（共享缓存 21H461 未滑动地址；解析工具 `inventory/ios-tools/mga.py`，全表
    `inventory/ios-data/mg-all.tsv`，818 个处理函数，其中 400 个有名字）。混淆名用 `base64(md5("MGCopyAnswer"+name))` 反查
    （键名表借用振动组 `haptics/ios/mg/mgkeys-deobf.tsv`，设备树名表取自 iPhone 16 设备树 `haptics/ios/ref-d47/d47.json`）。
  - 【D47】iPhone 16（D47AP，iOS 18）的苹果设备树，用来看“真 iPhone 上这些节点长什么样”（`ios-data/d47-names.txt`）。
  - 【bin】二进制字符串/导入/反汇编（`locationd`、`gpsd`、`bluetoothd`、`wifid`、`powerd`、`launchd`、`backboardd`、`thermalmonitord`，
    以及共享缓存中提出的 `CoreBrightness`、`CoreMotion`、`CMCapture`、`IOKit` 等，提取件在草稿目录，不入库）。
  - 【日志】`工具/gpu-test-loop/runs/*/merged.log`；【种子】`build-userspace/a830-inputs/2026-10-31-pkgAB/phone-island-apps.json`。
  - 【推断】= 我的判断，未经二进制或真机证实。
- 并行工作只引用、不重复：振动（`a830-inputs/haptics/`）、声音可行性（`haptics/sound/FEASIBILITY.md`）、Find My 纹理（`findmy/`）、
  音量键路由（`volume/`）、小组件（`widgets/`）、默认壁纸（`wallpaper/`）；硬件事实以同目录 `hardware.md` 为准，现状证据见 `evidence.md`，
  代码盘点见 `code.md`。

---

## 0. 结论速览（先看这里）

1. **四道“门”决定一个功能在 iOS 里是否存在**，与有没有驱动无关：
   - **种子禁用表**（`/private/var/db/com.apple.xpc.launchd/disabled.plist`）：当前种子禁用 9 个任务：`com.apple.CommCenter`、
     `com.apple.PerfPowerServices`、`com.apple.bluetoothd`、`com.apple.fairplayd.A2`、`com.apple.identityservicesd`、
     `com.apple.iomfb_fdr_loader`、`com.apple.locationd`、`com.apple.routined`、`com.apple.watchdogd`【种子 `disabled_jobs`】。
     **locationd 被禁用意味着 CoreMotion（加速度、陀螺、指南针、屏幕自动旋转、计步、抬起唤醒）和全部定位服务一律不存在**，
     做多少驱动都看不到效果。
   - **launchd 的设备树门控 `_LimitLoadToDeviceTree`**：launchd 用 MIG `io_device_tree_entry_exists_with_name`（消息号 0xb47，
     `launchd` 0x10003d9f0 → 0x100049524）问内核“设备树里有没有叫这个名字的节点”，内核实现是
     `IODTFindMatchingEntries(根, kIODTRecursive, name)` 按节点名递归匹配（`xnu/iokit/Kernel/IOUserClient.cpp` 6406 行）。
     不满足就整条任务不加载（错误码 0x8a，`launchd` 0x1000042c0）。受影响的：**`com.apple.wifid` 要名为 `wlan` 或 `marconi-wifi` 的节点**；
     `com.apple.CommCenter`（蜂窝版）要 `baseband`，没有时加载同名的 `CommCenter-noncellular`；`com.apple.biometrickitd.mesa` 要 `mesa`；
     `threadradiod` 要 `fillmore`；`SepUpdateTimer` 要 `hilo`；`iomfb_bics_daemon` 要 `hilo`/`display-hilo`/`twodbl`【plist + bin】。
     **当前 13T 的 ADT `/arm-io` 下只有 `intc` 一个节点**（`UniXNU-sm8750-common/dts/sm8750.dtsi:68` 是唯一的 `unixnu,adt-name`），
     所以即使 Wi‑Fi 驱动做好，按现在的 ADT，**wifid 也不会被 launchd 启动**。
   - **MobileGestalt 能力键**：Settings 的 Wi‑Fi/蓝牙行、相机 App、手电筒按钮、自动亮度、方向等是否出现，由 MG 答案决定，而这些答案
     大多直接读设备树（见第 3 节表）。例如 `wifi` 要 `/chosen/mac-address-wifi0`，`accelerometer` 要 `/arm-io` 下名为 `accel` 的节点，
     `ambient-light-sensor` 要一个 `PrimaryUsagePage 0xFF00 / PrimaryUsage 4` 的 IOService。
   - **内核里没有苹果的外设 kext**：13T 板级 `[kernel.prelink] roots`（`UniXNU-Pagani_SM8750-gpu/board.toml`，注释写明“取代 OS 档案的
     roots”）只有 12 个根：IOStorageFamily、apfs、AppleMobileFileIntegrity、CoreTrust、sandbox、ApplePMGR、AppleFSCompressionTypeZlib、IOSurface、
     IOMobileGraphicsFamily、IOHIDFamily、IOHIDEventDriver、IOGPUFamily（加上它们的依赖）。`AppleSPU`、`AppleALSColorSensor`、`AppleEmbeddedGPS`、
     `AppleBluetooth`、`AppleH9CameraInterface`、`AppleMesaSEPDriver`、`FairPlayIOKit`、`IOUSBDeviceFamily` 等都在 J120 内核缓存里但**没有装载**【KC】。
     所有“苹果硬件服务”都得由我们自己的驱动按 iOS 期待的类名/属性/事件发布。
2. **最便宜的正确接入点（今晚能写、主机能测的部分）**：
   - 传感器：照 `PaganiInput` 的做法发布 `IOHIDDevice` + 运行时 `gIOCatalogue->addDrivers` 给原厂 `IOHIDEventDriver` 加个性，
     顶层集合用苹果厂商页用法（加速度 0xFF00/0x03、陀螺 0xFF00/0x09、指南针 0xFF00/0x0A、接近 0xFF00/0x08、光感 0xFF00/0x04，
     证据见第 2 节），并在 ADT `/arm-io` 加名为 `accel`、`gyro` 的节点（MG）；**同时把 locationd 从禁用表里放出来**。数据源（ADSP SSC）是多周工作。
   - Wi‑Fi：照搬 sheng 的 `WifiNet.cpp`（同一 21H461，`IO80211Controller`/`IO80211Interface` 注册项 + `SIOCGA80211/SIOCSA80211`），
     **再补两样 iOS 门**：ADT 里一个名为 `wlan` 的节点（launchd）和 `/chosen/mac-address-wifi0`（MG `wifi`/`WifiAddressData`；wifid 没有它会中止加入）。
   - 电池：`PaganiBattery`（`IOPMPowerSource` 子类）方向正确，powerd 按 `IOServiceMatching("IOPMPowerSource")` 找；MG `HasBattery` 来自
     powerd 的 IOPS（`Type = InternalBattery` 且 `Is Present`）。只差上机。
3. **按 iOS 原生功能看做不到（D）**：蜂窝/信号格/通话（CommCenter 要苹果基带协议）、Touch ID / Face ID（SEP）、NFC/Apple Pay（Stockholm + SEP）、
   GNSS 定位（gpsd 只认 `/dev/cu.gps` 博通协议、SPU GNSS 直通或 PCIe 基带三种接口，13T 的 GNSS 在高通基带 MPSS 里）、
   App Store 第三方应用（`amfi-only-platform-code = 1` + 无 `FairPlayIOKit`）、苹果相机栈（`AppleH1xCamIn` 用户客户端协议 + ISP 固件）。
4. **其中有“绕路”的**：定位可走 Wi‑Fi 定位（locationd 经 MobileWiFi/CoreWiFi 拿 wifid 扫描结果，联网向苹果查询），前提是 Wi‑Fi 与 locationd；
   相机理论上可走 iPadOS 17 的 CMIO 扩展/外接相机路径（`CMCapture` 有 `BWFigCaptureDeviceVendor` 的 CMIO 设备支持），手电筒只能挂在相机栈上，都是多周以上。

---

## 1. 横向机制（所有功能共用）

### 1.1 launchd 读什么、怎么门控

- 正常启动时 launchd 只读已签名缓存 `/System/Library/xpc/launchd.plist`（`PaganiDiag/README.md` 已核实；覆盖层改不了任务表），
  再用 `/private/var/db/com.apple.xpc.launchd/disabled.plist` 禁用（种子脚本 `UniXNU-Pagani_SM8750-gpu/scripts/prepare-ram-data.py` 20–30 行、
  `工具/make-phone-seed.py --disable-jobs`）。
- launchd 支持的条件键（`/sbin/launchd` 字符串）：`_LimitLoadToDeviceTree`/`_LimitLoadFromDeviceTree`、`_LimitLoadToVariant`/`From`、
  `_LimitLoadToBootMode`/`From`、`_LimitLoadToClarityMode`/`From`、`LimitLoadToDeveloperMode`、`_LimitLoadToBootarg`/`From`、`LimitLoadToHardware`。
- 设备树门控的确切语义（反汇编核实）：值可以是字符串或字符串数组；对每个名字调 `io_device_tree_entry_exists_with_name`，内核
  `IODTFindMatchingEntries` 在 IODeviceTree 平面全树递归、按名字（`compareNames`，`OSUnserialize` 把裸词解析成字符串）比较，跳过带
  `AAPL,ignore` 的节点。**所以节点放在哪一层都行，名字对就行**。已被平台专家转成 nub 的节点（如 `/arm-io` 子节点变成 `IOPlatformDevice`），
  `compareName` 走 `IODTCompareNubName`，`name`、`compatible`、`device_type`、`model` 任一相等都算匹配（`IODeviceTreeSupport.cpp` 981 行、
  `IOPlatformExpert.cpp` 2303 行），所以 `compatible` 写成 `wlan` 也行。
- 带 IOKit 启动事件的守护进程（`com.apple.iokit.matching`，节选，全表见 `ios-data/launchd-conditions.tsv`）：

| 任务 | 等的 IOKit 对象 | 对我们意味着 |
| --- | --- | --- |
| `com.apple.biometrickitd.mesa` | `IOProviderClass AppleBiometricServices` | 没有就不启（且设备树也要 `mesa`） |
| `com.apple.nfcd` | `AppleStockholmSPMINotification` | NFC 控制器事件；平时按 Mach 服务按需起 |
| `com.apple.mobile.lockdown` | `IOPortTransportState`（`TransportType` 2/3、`Active`、`DataRole 1`），或 `IOResources AppleUSBCableType=USBHost` | USB 设备模式连电脑的入口 |
| `com.apple.ptpd` | `IOUSBDeviceInterface USBDeviceFunction=PTP` | USB 相册导入 |
| `com.apple.ioupsd` | `IOPMPowerSource` 带 `AccessoryDetails`；HID 用法页 0x84/0x85 | 外接电池/UPS，不涉及内置电池 |
| `com.apple.mobilerepaird` | `AppleH13CamIn`/`AppleH16CamIn` 的 `CmClValidationStatus`、`AppleAuthCPRelay isTrustedForUI=false` 等 | 不发布这些类就不会弹“未知部件” |
| `com.apple.wifiFirmwareLoader` | `IONameMatch AppleBCMWLANCoreFirmwareLoader` | 博通专用，与我们无关 |
| `com.apple.watchdogd` | `IOWatchdog` | 种子已禁用 watchdogd |
| `com.apple.nearbyd` | `AppleSPURoseDriver`、`IONameMatch rose` | UWB，13T 没有 |
| `com.apple.iomfb_fdr_loader` | 任意服务的 `IOMFBLaunchFDR` | 种子已禁用 |

### 1.2 当前 ADT 的内容与生成器约束

- 生成器 `UniXNU/scripts/unixnu/devicetree.py` `generate()`：顶层只有 `chosen`、`cpus`、`psci`、`arm-io`、`defaults`、`options`、`product`、
  `buttons`；`/arm-io` 的子节点**只能**来自硬件 DTS 里带 `unixnu,adt-name` 的节点（`reg` 可选，只复制 `compatible`/`device_type`/`reg`/
  中断等已知属性，其余属性丢弃）；`[adt.product.<子节点>.<类型>]` 可以在 `/product` 下建子节点（OS 档案已用 `/product/audio`）；
  `[adt.chosen.<类型>]` 可以加 `/chosen` 属性（`bytes` 类型可写 MAC）。
- 13T 现状：`/chosen`（random-seed、dram、unique-chip-id/chip-id 0x8011、root-matching、ephemeral-storage、text-fixups、4 个安全状态值、
  display-scale/rotation）、`/cpus`、`/psci`、`/arm-io/intc`、`/defaults`（`sleep-disabled 1`、`kern.maxvnodes`）、`/options`、
  `/product`（身份 + `/product/audio wireless-splitter`、`product-id`）、`/buttons`（`hold voldown volup`、`home-button-type 2`、
  `function-button_ringerab`）。**没有** `wlan`、`bluetooth`、`baseband`、`aop`/`accel`/`gyro`、`isp`、`sep`、`haptics`、
  `mac-address-*`、`gps-capable`、`builtin-mics`、`/product/camera`。
- 要加 `/arm-io/<名>` 节点：在 13T DTS 写一个 `unixnu,adt-name = "<名>"` 的节点（可不带 `reg`）。要加顶层 `/baseband` 之类则需要改生成器
  （**不要加 `baseband`**，见 6.12）。
- 注意：MobileGestalt 把答案缓存在 Data 的 `com.apple.MobileGestalt.plist`；种子脚本 `prepare_userspace` 已处理这三个缓存路径，每次开机全新。

### 1.3 原厂 IOHIDEventDriver 能直接用的范围（传感器、按键的接入点）

- 原厂 `IOHIDEventDriver`（`com.apple.iokit.IOHIDEventDriver` 只有个性，类在 IOHIDFamily 里，两者都在 13T 的预链接根里）出厂个性只有 4 条：
  `Peppy Touch Display`（0x0C/0x01，FIFO）、`HID Buttons`（0x0B/0x01）、`HID Keyboard Driver`（0x01/0x06、0x01/0x07）、
  **`HID Sensor`（0xFF0C/0x03）**；IOHIDFamily 另有游戏手柄（0x01/0x04、0x01/0x05）【KC】。
- `PaganiInput` 已证明的做法：自己的 `IOHIDDevice` 子类 + 运行时 `gIOCatalogue->addDrivers()` 加一条针对自己 `Product` 的个性
  （`UniXNU-Pagani-Drivers/drivers/PaganiInput/HidEvents.h` `installPersonality()`），原厂 `IOHIDEventDriver` 解析报告描述符并派发事件。
  传感器沿用同一做法最省事：报告描述符的顶层应用集合用苹果厂商页用法（决定服务的 `PrimaryUsagePage/PrimaryUsage`，iOS 按它匹配），
  集合里的数据字段用 HID 传感器页（0x20）的标准用法（加速度 X/Y/Z 等）。**【推断】原厂 IOHIDEventDriver 对 0x20 页数据字段的解析
  要对照 IOHIDFamily 开源（APSL，`IOHIDEventDriver::parseElements`/`handleAccelReport` 一类）确认**；它支持 `ReportInterval`、
  `BatchInterval`、`MaxFIFOEvents`、`SensorPropertySupported` 这些属性名（J120 内核缓存字符串里有）。
- HID 事件类型编号（共享缓存 `IOKit` 的事件名表顺序）：AmbientLightSensor = 12、Accelerometer = 13、Proximity = 14、Temperature = 15、
  Gyro = 20、Compass = 21；表里还有 AtmosphericPressure、MotionActivity、MotionGesture（编号不在此断言）。

---

## 2. 传感器：iOS 期待的服务形状（证据汇总）

| 传感器 | 用法页/用法（iPhone 上） | 证据 | MG 键与来源 | iOS 消费者 |
| --- | --- | --- | --- | --- |
| 加速度 | 0xFF00 / 0x03 | 【D47】`/arm-io/aop/iop-aop-nub/accel` `device-usage-page = 0xFF00`、`device-usage = 3`（原始字节核对） | `accelerometer` = `/arm-io` 下名为 `accel` 的节点（处理函数 0x1a0229fd8，名称哈希 `rwnje21oPXH375umjxV6YQ`，首字母 `a`） | locationd（`CLAccelerometer` → `CLIoHidInterface`）、`CLOrientationNotifier`、backboardd `BKAccelerometerInterface` |
| 陀螺 | 0xFF00 / 0x09 | 【D47】`gyro` `device-usage 9` | `gyroscope` = `/arm-io` 下名为 `gyro` 的节点（0x1a022bd0c） | locationd `CLGyro`、AP 融合 `CLSensorFusionService6AxisAP` |
| 指南针 | 0xFF00 / 0x0A | 【D47】`compass` `device-usage 10`；HID 插件 `AppleSPUAccCompassPlugin`/`AppleSPUDispCompassPlugin` 的 `Matching` = `DeviceUsagePairs 65280/10`、`Transport SPU` | `magnetometer` = 设备类为 iPhone 或 iPad（0x1a021bb2c，**我们恒为真**）；`CompassCalibration` = `compass` 节点的 `compass-calibration` | locationd `CLCompassInterface`、指南针 App |
| 接近 | 0xFF00 / 0x08 | 【D47】`prox` `device-usage 8`；CoreBrightness 里 0xFF00/0x08 旁边是 `Built-In`、`Placement` | `proximity-sensor` = 设备类为 iPhone（0x1a0223524，**恒为真**） | backboardd `BKHIDProximityEventProcessor`、locationd `CLProximityNotifier` |
| 环境光 | 0xFF00 / 0x04 | J120 KC：`AppleSPUALS*`、`AppleSPUCT7xx` 全是 `DeviceUsagePairs 65280/4`；MG 处理函数 0x1a02279bc 直接构造 `IOPropertyMatch {PrimaryUsagePage 0xFF00, PrimaryUsage 4}` | `ambient-light-sensor` = 存在上述 IOService | CoreBrightness（在 backboardd 里）：读 `ALSIntegrationMode`、`Built-In`、`Placement`、`ALSSensorType` 等 |
| 气压 | 0x14 / 0x31 | 【D47】原始设备树 `pressure` 的 `device-usage-page = 0x14`、`device-usage = 0x31`（JSON 把 0x31 显示成字符“1”，已按原始字节核对） | （`pressure` 键处理函数不在表内） | locationd `CLPressureDataManager`；“device does not support Baro” |
| 高 g 加速度 | 0xFF00 / 0x1E | 【D47】`hgaccel` | — | 车祸检测，不做 |
| 手势/抬起唤醒 | 0xFF0C / 0x03 | J120 KC：`AppleSPUGestureDriver` 与原厂 `HID Sensor` 个性都是 65292/3 | — | CoreMotion `CMWakeGesture*`、locationd `CLGestureService` |
| 计步/活动 | SPU 协处理器 | locationd 的 `CLMotionCoprocessor*`、`CLStepCountInterface`、`CLSensorFusionService*SPU` | `DeviceSupportsAOP` = `/arm-io/aop` 的 `role` | CMPedometer、健康 |

补充事实：
- locationd 同时有 SPU 路径（`AppleSPUHIDDevice`、`AppleSPUFastpathDriver/Interface`、`CLIoHidFastPathDevice`、`CLSPUHIDDriverInterface`、
  `CLSensorFusionService3/6/9AxisSPU`）和 AP 路径（`CLIoHidInterface`、`CLSensorFusionServiceAP`、`CLSensorFusionService6AxisAP`、
  `CLSensorFusionServiceLegacy`）【bin】。缺加速度时它会记 “No accelerometer; not detecting orientation!” 和 “… not detecting gestures”，
  说明**方向检测和手势检测在 locationd 里、并以加速度服务为前提**。
- backboardd 的 `BKOrientationManager` 用 CoreMotion 的 `CMDeviceOrientationManager`（字符串 `@"CMDeviceOrientationManager"`）：
  **屏幕自动旋转 = locationd 运行 + 加速度服务存在**。
- 13T 硬件：所有运动/环境传感器在 ADSP 的 SSC（传感器 PD）后面，ALS 在屏下（`ro.display.underscreen.lightsensor.support=1`），
  详见 `hardware.md` 2.2–2.3。apps 侧拿不到任何传感器芯片。

---

## 3. MobileGestalt：本次相关能力键的来源（21H461，静态分析）

“来源”栏的路径都是 IODeviceTree；“名称哈希”表示 libMobileGestalt 比较的是 `base64(md5("MGCopyAnswer"+属性名))`，括号里是我反查出的原名。

| 键（处理函数） | 来源 | 13T 现在 | 要它为真需要 |
| --- | --- | --- | --- |
| `wifi`（0x1a021e95c） | `WifiAddressData` 有值且不是 `RegionalBehaviorNoWiFi` | 假 | `/chosen/mac-address-wifi0` |
| `WifiAddressData`（0x1a0228284） | 正常系统读 `/chosen` 的 `mac-address-wifi0`；Restore 系统读 syscfg `WMac` | 无 | 6 字节 MAC（sheng 由 `unique-chip-id` 哈希出本地管理地址，`WifiProbe.cpp` 886–925 行） |
| `WifiChipset` | `/product` 的 `wifi-chipset`（哈希） | 无 | 可不填 |
| `bluetooth`（0x1a0226750） | `BluetoothAddressData` | 假 | `/chosen/mac-address-bluetooth0` |
| `BluetoothAddressData`（0x1a021d1d4） | `/chosen` 的 `mac-address-bluetooth0`；Restore 读 syscfg `BMac` | 无 | 只在蓝牙真能用时再加 |
| `bluetooth-le` | `/product` 的 `bluetooth-le` | 无 | 同上 |
| `HasBaseband`（0x1a0211a3c） | 任意名为 `baseband` 的节点（`Node %@ found`） | 假 | **保持为假** |
| `telephony`、`cellular-data`、`contains-cellular-radio`、`CellularTelephonyCapability`、`data-plan` | 都由 `HasBaseband`（和设备类）派生 | 假 | — |
| `gps`（0x1a0220548） | `/product` 的 `gps-capable`（哈希 `hK/qlAUuu8u6SvIRkKmjQQ`） | 假 | 没有 GNSS 前**不要加**（【推断】会让 locationd 去拉起 gpsd 并反复失败） |
| `accelerometer` / `gyroscope` | `/arm-io` 子树里名为 `accel` / `gyro` 的节点（真 iPhone 上在 `/arm-io/aop/iop-aop-nub/` 下，故为递归查找） | 假 | 加节点（与 HID 服务一起） |
| `magnetometer` / `proximity-sensor` | 设备类（iPhone 恒真） | 真 | 硬件没有也报“有” |
| `ambient-light-sensor` | `PrimaryUsagePage 0xFF00`、`PrimaryUsage 4` 的 IOService | 假 | 光感 HID 服务 |
| `DeviceSupportsAOP` | `/arm-io/aop` 的 `role` | 假 | 不需要 |
| `camera-flash` | `/product/camera` 的 `flash` | 假 | 只有手电筒能用时再加 |
| `auto-focus-camera` | `/product/camera` 的 `auto-focus` | 假 | — |
| `RearCameraCapability` / `rear-facing-camera` | `/arm-io/isp` 的 `camera-rear`（哈希） | 假 | — |
| `still-camera` | 名为 `isp` 的节点的 `camera-front` | 假 | 相机 App 出现与否 |
| `nfc` / `NFCRadio` | 名为 `stockholm` 的节点 + `supports-nfc-reader-mode` / `nfcWithRadio` | 假 | 保持为假 |
| `HasSEP` | `/arm-io/sep` 节点存在（`name`） | 假 | 保持为假 |
| `DeviceMesaType` | `/arm-io/mesa` 的 `mesaType` | 无 | — |
| （生物识别能力，`jWdMTTxiAZc+KNO6Bz2jNg`） | `AppleBiometricServices` 服务 | 无 | — |
| `gas-gauge-battery`（0x1a0218bf4） | `IOPMPowerSource` 的 `BatteryInstalled`，或 `AppleHDQGasGaugeControl` 服务 | 无（A 包）/有（开电池） | `PaganiBattery` |
| `HasBattery`（0x1a0228794） | powerd 的 IOPS 里 `Type = InternalBattery` 且 `Is Present` | 同上 | 同上 |
| `BatteryIsCharging` | `IOPMPowerSource` 的 `IsCharging` | 同上 | 同上 |
| `DeviceSupportsBatteryInformation` | `/product` 的 `supports-lotx`（D47 为 1） | 假 | 可选 |
| `MicrophoneCount` / `microphone` | `/product` 的 `builtin-mics`（D47 为 4） | 0 / 假 | **麦克风采集做通后**加 `[adt.product.u32] builtin-mics` |
| `ringer-switch` | `/buttons` 的 `function-button_ringerab`（哈希 `Ah5yfSlY2yycIQb92HbmrA`） | 真 | 已配置 |
| `RingerButtonCapability` | `/buttons` 的 `function-button_ringeren` | 假 | 操作按钮相关（完美化清单第 10 项） |
| `DeviceSupportsHaptics` / `haptics` / `DeviceSupportsClosedLoopHaptics` | 名为 `haptics` 的节点；`/product/haptics` 的 `supports-3rd-party-haptics`、`closed-loop` | 假 | 振动组草案 `haptics/ios/draft/board-haptics.toml` |
| `DeviceSupportsTwilight` | `/backlight` 的 `supports-twilight` | 假 | — |
| `siri-gesture`、`personal-hotspot` | `/product` 同名属性 | 假 | 不需要 |
| `AirplaneMode` | `com.apple.radios.plist` 偏好 | — | — |

---

## 4. 内核与 powerd/守护进程各自认的类名（节选）

- **powerd**（`System/Library/CoreServices/powerd.bundle/powerd`）`IOServiceMatching` 的参数：`IOPMPowerSource`（多处）、`AppleSmartBattery`、
  `AppleARMPMUPowerSource`（带 `IOServiceFirstMatch`）、`AppleSMC`、`AppleBTM`、`IOPortTransportStateUSB`、`AppleAuthCPRelay`、`IODTNVRAM`、
  `IOHIDSystem`；`IORegistryEntryFromPath`：`IODeviceTree:/`、`IODeviceTree:/options`、`IOPower:/IOPowerConnection/IOPMrootDomain`【bin】。
  powerd 读的电池键：`CurrentCapacity`、`MaxCapacity`、`IsCharging`、`ExternalConnected`、`ExternalChargeCapable`、`FullyCharged`、
  `AtCriticalLevel`、`BatteryInstalled`、`Voltage`、`Amperage`、`InstantAmperage`、`Temperature`、`CycleCount`、`DesignCapacity`、
  `NominalChargeCapacity`、`AppleRawCurrentCapacity`、`AppleRawMaxCapacity`、`TimeRemaining`、`AdapterDetails`、`BatteryData`、`ChargerData`、
  `NotChargingReason`、`MaximumCapacityPercent`、`PermanentFailureStatus`、`ITMiscStatus`、`PresentDOD`、`GasGaugeFirmwareVersion`、
  `ChargeLimit`/`ChargeInhibit` 等，输出 IOPS 字典键 `Current Capacity`、`Max Capacity`、`Is Charging`、`Is Present`、`Is Charged`、
  `Power Source State`、`Type`、`Time to Empty`、`Time to Full Charge`、`Battery Provides Time Remaining`【bin】。
- **locationd** 匹配/引用的类：`AppleSPUHIDDevice`、`AppleSPUFastpathDriver`、`AppleSPUFastpathInterface`、`IOPMPowerSource`、`AppleBaseband`、
  `AppleEmbeddedBluetoothSensors`、`AppleH4CamIn`、`IOPlatformExpertDevice`；Wi‑Fi 经 `MobileWiFi`（`WiFiDeviceClient*`）与 `CoreWiFi`
  （`CWFInterface`）向 wifid 取；GNSS 经 `gpsd`（`#gpsdClient`）【bin】。
- **gpsd**（`/usr/libexec/gpsd`，Mach 服务 `com.apple.gpsd`，`KeepAlive AfterInitialDemand + SuccessfulExit=false`）：三种芯片接口——
  串口 `/dev/cu.gps`（`#comm,configured,flowCtrl,…,baud`，配合 `AppleEmbeddedGPSControl` 用户客户端控电/复位，`#gpio,setResetHigh`）、
  SPU 直通（`fGNSSPassthroughInterface`，`GetGnssChipPowerState`）、PCIe 基带（`#pcie,#bbReset`，`libPCITransport`）；
  芯片协议由 `gnssDevLibName` 选的设备库负责【bin】。
- **bluetoothd**（`/usr/sbin/bluetoothd`）：设备树决定传输（“DeviceTree transport = 0x%08x”“DeviceTree speed = %d”；D47 的
  `/arm-io/bluetooth` 是 `compatible bluetooth,n88`、`transport-encoding 7`、`local-mac-address`），串口传输找 `/dev/cu.bluetooth` 或
  `/dev/uart.bluetooth`（“No valid serial port found (tried cu.bluetooth and uart.bluetooth)”），支持 H4/H4BC/H4DS/H5，也有 PCIe
  （`AppleConvergedIPCInterface`、`pcie_mempool.c`）；芯片识别 `BCM_4387`/`BCM_4388` 等，固件目录 `/usr/share/firmware/bluetooth/`、
  BlueTool 脚本 `/etc/bluetool/`；“Couldn't find a platform / chipset / firmware combination for the HW”；另有“running inside a Virtual
  Machine, so skipping chip initialization”的分支【bin】。
- **wifid**（`/usr/sbin/wifid`）链接 `IO80211.framework`，读一大批 `IO80211Interface*` 属性名；sheng 的 `WifiNet.cpp` 按 wifid 的请求实现了
  “`IO80211Controller` + `IO80211Interface`（`IOInterfaceNamePrefix en`）+ 接口上的 `SIOCGA80211/SIOCSA80211`（power、scan、associate、state、
  ssid、bssid、channel、rssi、country…）+ `SIOCGIFMEDIA` 报 `IFM_IEEE80211`”这一层【`WifiNet.cpp` 260–380 行】。sheng 真机证实的是联网
  （DHCP、测速），wifid 是否参与见第 5 节。
- **CMCapture**（mediaserverd 里的相机栈）只认 `AppleH6CamIn`、`AppleH9CamIn`、`AppleH10CamIn`、`AppleH13CamIn`、`AppleH16CamIn`；
  手电筒是 `FigFlashlight`（`com.apple.coremedia.flashlight`），`-[BWFigCaptureDeviceVendor copyStreamForFlashlightWithPosition:…]`
  要一个相机流；另有 CMIO 扩展设备（`_updateCMIOExtensionDevices`、`captureSourceIsExternalCamera`）【bin】。
- **CoreBrightness**：光感服务匹配 0xFF00/0x04，读 `ALSIntegrationMode`、`Built-In`、`Placement`、`ALSSensorType`、`ALSSensorMin/Max`，
  事件派发后才有 “ALS service for internal display ready”【bin】。
- **thermalmonitord**：按产品读 `System/Library/ThermalMonitor/<board>-Info.plist`，根卷只有 `J120AP`、`J121AP`、`J207AP`、`J208AP`
  （我们 `target-type J120`，用 J120AP）；没传感器时记 “No sensors found”；还要 `clpc` 服务（“clpc service not present…”，无害）【bin】。

---

## 5. 当前状态的直接证据（只列 iOS 侧相关）

- 种子禁用表：见 0.1。来源 `phone-island-apps.json` `disabled_jobs`，原因注释 “Same-build hardware wait/crash loops documented by
  UniXNU-sheng and UniXNU-tb128fu”，文档 `UniXNU/docs/zh/guide/skip-setup.md` 89–92 行：“Setup 依赖的服务在这个平台上不运行……移除阻塞首次启动的服务”。
- 运行日志里能看到的 iPhone 身份相关崩溃：`routined` SIGABRT（`getMOSettingsManagerClass`，已禁用）；PosterBoard abort（壁纸组）；
  FindMy SIGABRT（VectorKit 空纹理，Find My 组）；`com.apple.SensorKit.*` 的 SIGKILL 跟随 MobileSMS 崩溃，不是传感器硬件问题
  （`runs/20261030-213341-island-apps-1` 7834–8504 行）。
- 无硬件时会崩或空转的守护进程（汇总）：

| 守护进程 | 现象 | 原因 | 现在的处理 |
| --- | --- | --- | --- |
| `routined` | SIGABRT（`getMOSettingsManagerClass`） | iPad 根卷缺 iPhone 专属类 | 种子禁用 |
| `PosterBoard` | 每 10 s abort 两次（旧轮次） | 缺 `~iphone` 壁纸集合清单 | 壁纸组修复中 |
| `FindMy` | SIGABRT（VectorKit 空纹理） | Metal 纹理分配失败 | Find My 组修复中 |
| `MobileSMS` 及随之被杀的 `com.apple.SensorKit.*` | SIGSEGV（`CI::GLContext::init`） | 没进 Metal 客户端名单的 App 回落 OpenGL ES | Core Image 文本修补 |
| `locationd`、`CommCenter`、`bluetoothd`、`identityservicesd`、`PerfPowerServices`、`watchdogd`、`iomfb_fdr_loader`、`fairplayd.A2` | 原作者记录的“等硬件/崩溃循环” | 缺 SPU/基带/蓝牙芯片/SEP 等 | 种子禁用（13T 上未单独复现过） |
| `gpsd` | 【推断】若设 `gps-capable`，locationd 会拉起它，打不开 `/dev/cu.gps` 后按 `SuccessfulExit=false` 反复重启 | 无 GNSS 接口 | 不设 `gps-capable` |
| `wifid` | 【推断】有 `wlan` 节点而无 `IO80211Interface` 时行为未知 | — | 节点与驱动同时上 |

- 没有任何一轮日志出现 wifid/bluetoothd/locationd/gpsd 的退出记录；但早期开机的 S exec 记录全部被环形缓冲覆盖（最早保留到 61 s 以后），
  **无法从日志确认 wifid 是否运行**。按 1.1 的 launchd 分析，没有 `wlan` 节点时它不会被加载。
- sheng 的 ADT（`scripts/make-sheng-adt.py`、`board.toml` 的 `[adt]`、`dts/sheng.dts`）同样没有 `wlan` 节点，但其网络文档说
  `sheng-wifi-aj=0` 时“让设置应用负责加入网络”。sheng 的真机证据（`porting.toml` network 段）是“开机 31 s 内拿到 DHCP 地址、测速”，
  `WifiNet.cpp` 里还有“无人值守测试加入”（`gUserJoin` 注释），并且“系统无法保存网络密码”——【推断】sheng 已证实的联网很可能是
  **内核自动加入 + configd 的 DHCP**，并不需要 wifid；“设置 App 加入网络”这条未见设备证据。该矛盾未最终解决，早上第一件可核实的事（第 8 节）。
  MIG 编号已按 `device.defs`（64 位用户态条件）核对：2887 = `io_device_tree_entry_exists_with_name`。

---

## 6. 逐项盘点

格式：事实 → 现状 → 已有代码 → 缺口（按层）→ 可行性 → 最便宜接入点。可行性：A 今晚可写完（源码 + 主机测试 + 编进镜像）；
B 今晚可写，但要上机调几轮；C 多周；D 这块硬件上做不到。

### 6.1 加速度计、陀螺仪、屏幕自动旋转
- 事实：见第 2 节；屏幕方向 = locationd 的 `CLOrientationNotifier` + backboardd 的 `CMDeviceOrientationManager`。
- 现状：locationd 被种子禁用；无任何传感器服务；MG `accelerometer`/`gyroscope` 为假；屏幕不会转。
- 已有代码：`PaganiInput` 的 IOHIDDevice + 个性注入模式；`PaganiAdsp`（未上机）。
- 缺口：内核——数据源（ADSP SSC：传感器 PD、QMI 服务 400 `SNS_CLIENT` 的 protobuf、给 DSP 读配置的 FastRPC 文件服务，见 `hardware.md` 2.2）；
  IOKit——HID 服务 `PrimaryUsagePage 0xFF00`、`PrimaryUsage 3/9`，事件类型 13/20（单位按 IOHIDEvent：g、rad/s）；资源——ADT `/arm-io/accel`、
  `/arm-io/gyro` 节点；配置——种子放出 `com.apple.locationd`。
- 可行性：C（数据源）。iOS 侧发布层与 ADT 节点本身是 A，但没有数据只能出假值，不应发布。
- 接入点：IOHIDDevice + 原厂 IOHIDEventDriver（与触摸同法），不要去模拟 `AppleSPUHIDDevice`/Fastpath。

### 6.2 指南针（磁力计）
- 事实：0xFF00/0x0A；HID 插件 `AppleSPUAccCompassPlugin` 等只匹配 `Transport SPU`，我们不必匹配它们；MG `magnetometer` 恒真。
- 现状/缺口/可行性：同 6.1（C）。13T 是否有物理磁力计未知（安卓 DT 只有虚拟 `mag_fusion`，`hardware.md` 2.2）。
- 接入点：同 6.1，用法 0x0A，事件类型 21（µT）。

### 6.3 环境光（自动亮度、原彩）
- 事实：MG 要 0xFF00/0x04 服务；CoreBrightness 读 `ALSIntegrationMode`/`Placement`/`Built-In`；13T 光感在屏下，要扣屏幕自发光。
- 现状：无；自动亮度不可用（亮度手动可调，`PaganiPanel`）。
- 缺口：同 6.1 的 SSC 数据源 + 屏幕内容补偿；IOKit 光感 HID 服务（事件类型 12）。
- 可行性：C。接入点：IOHIDDevice（0xFF00/0x04），`Built-In = true`。

### 6.4 接近传感器（通话贴脸熄屏）
- 事实：0xFF00/0x08；backboardd `BKHIDProximityEventProcessor`；MG `proximity-sensor` 恒真（所以系统以为有）。
- 现状：无服务；没有通话功能，影响很小。可行性：C（SSC）。接入点：IOHIDDevice（0xFF00/0x08，事件类型 14）。

### 6.5 气压计
- 事实：iPhone 上是用法页 0x14、用法 0x31 的 SPU 服务（D47 原始字节）；locationd 无气压时记 “device does not support Baro”。
- 现状：13T 公开规格里没有气压计（`hardware.md` 2.2，【未知】待安卓 `dumpsys sensorservice` 确认）。
- 可行性：D（大概率没有硬件）。

### 6.6 计步、运动活动、抬起唤醒
- 事实：计步与活动由运动协处理器算（`CLMotionCoprocessor*`、`CLStepCountInterface`、`CLSensorFusionService*SPU`）；抬起唤醒是 SPU 手势
  （`AppleSPUGestureDriver` 0xFF0C/0x03、CoreMotion `CMWakeGesture*`、locationd `CLGestureService`）。locationd 有
  “No accelerometer; not detecting gestures”，【推断】可能存在基于加速度的 AP 侧手势路径。
- 现状：无。缺口：要么在内核里自己算手势/步数并伪装 SPU 事件（协议未知），要么等 AP 路径证实。
- 可行性：C–D。接入点：先做 6.1，再看 locationd 在只有 AP 加速度时能否给出手势/步数（真机日志）。

### 6.7 电池电量与充电状态
- 事实：powerd 按 `IOPMPowerSource` 找电池（第 4 节），状态栏/锁屏充电/设置 > 电池都来自 powerd 的 IOPS；MG `HasBattery` 也走 IOPS；
  `gas-gauge-battery` 读 `BatteryInstalled`。
- 现状：A 包不带电池驱动，电量显示不准；B 包（`pagani-battery=1`，`测试镜像/boot-A830-B包-A加电池.img`）从未上机；
  `PerfPowerServices` 被禁用（设置 > 电池的用量图会空）。
- 已有代码：`UniXNU-Pagani-Drivers/drivers/PaganiBattery`（`PaganiBattery : IOPMPowerSource`，发布 `CurrentCapacity`、`MaxCapacity=100`、
  `IsCharging`、`ExternalConnected`、`ExternalChargeCapable`、`AtCriticalLevel`、`BatteryInstalled`、`Voltage`、`Amperage`、`CycleCount`、
  `TimeRemaining`、`FullyCharged`、`InstantAmperage`、`Temperature`、`AppleRawCurrentCapacity`、`AppleRawMaxCapacity`、`NominalChargeCapacity`、
  `DesignCapacity`、`AdapterDetails`；先有电量才 `registerService()`，0 % 不报）。
- 缺口：上机验证；可选补 `BatteryData` 字典（设置 > 电池健康用）与 `MaximumCapacityPercent`；不要发布 `AppleAuthCPRelay`
  （否则 `mobilerepaird` 可能弹“未知部件”）；充电控制（无 ADSP 时 PMIC 看门狗，见 `hardware.md` 2.18）。
- 可行性：B（iOS 接口正确，差一轮上机）。接入点：现有 `PaganiBattery`。

### 6.8 振动与触感（只引用）
- 由振动组负责：`a830-inputs/haptics/`（硬件 `hw/PaganiHaptics`：PMIH010x HAP530 经 SPMI；iOS：MG `DeviceSupportsHaptics` = 名为 `haptics`
  的节点，`/product/haptics` 的 `supports-3rd-party-haptics`、`closed-loop`，草案 `ios/draft/board-haptics.toml`；VirtualAudio 的 Actuator
  设备与 `/Library/Audio/Tunings/<board>/Haptics` 预设）。本盘点不重复。可行性按该组结论（B）。

### 6.9 声音（扬声器、麦克风、音量键）（只引用）
- 由声音组与音量组负责：`haptics/sound/FEASIBILITY.md`、`volume/`。iOS 侧现状：`PaganiAudio` 插件被 audiomxd 加载，但 VirtualAudio 不给它活动路由
  （`audio route … out=0`），音量条不出（`PaganiAudio/README.md`）。
- 本盘点补充一个 MG 事实：**麦克风能力 `microphone`/`MicrophoneCount` 来自 `/product` 的 `builtin-mics`**（D47 = 4），录音类 App 与听写判断
  有无麦克风会用到；麦克风采集做通后在 `[adt.product.u32]` 加 `builtin-mics`。
- 可行性：B–C（按声音组）。

### 6.10 Wi‑Fi
- 事实：wifid 只在设备树有 `wlan`/`marconi-wifi` 节点时加载（1.1）；MG `wifi` 要 `/chosen/mac-address-wifi0`；wifid 用 IO80211 框架，
  sheng 在同一 21H461 上联网成功（扫描、WPA2、DHCP、测速；“系统无法保存网络密码”，【推断】走的是内核自动加入，见第 5 节）。locationd、状态栏信号格、Wi‑Fi 定位都依赖 wifid。
- 现状：13T 无 Wi‑Fi 驱动；ADT 无 `wlan`、无 `mac-address-wifi0`；`wifip2pd` 在跑（日志有 exec），wifid 是否在跑未知（5 节）。
- 已有代码：sheng `package/WifiProbe.cpp`（PCIe、MSI、DMA、固件回调、`publishMac()` 运行时写 `/chosen/mac-address-wifi0`）、`WifiNet.cpp`
  （iOS 侧，可直接用，`kHardwareVersion` 写死 `WCN7850` 要改）、`drivers/Ath12kWifi`（不认 peach `17cb:110e`，见 `code.md`）。
- 缺口：内核——peach（WCN7880 系列）的 ath12k 支持、SM8750 PCIe 链路与 MSI（GIC ITS，见 `code.md`）、固件；IOKit——照搬 WifiNet；
  资源/配置——ADT 加 `wlan` 节点（DTS `unixnu,adt-name = "wlan"`），`/chosen/mac-address-wifi0`（`[adt.chosen.bytes]` 或运行时写，按 sheng）。
- 可行性：C（芯片与 PCIe）；iOS 侧接线 A。接入点：sheng 的 `IO80211Controller`/`IO80211Interface` + Apple80211 ioctl；**节点与 MAC 必须和驱动同时上**
  （先上节点会让 wifid 在没有接口时空跑，行为未验证）。分两步：第一步内核自动加入固定网络 + configd 走 DHCP 就能上网（sheng 已证实，
  不需要 wifid）；第二步放 `wlan` 节点与 MAC 让 wifid 接管（设置 > Wi‑Fi、状态栏图标与信号格、记住密码、Wi‑Fi 定位都要它）。

### 6.11 蓝牙
- 事实：bluetoothd 按设备树（`/arm-io/bluetooth` 的 `transport-encoding` 等）选传输，串口找 `/dev/cu.bluetooth`/`/dev/uart.bluetooth`，
  芯片初始化按博通/苹果芯片（`BCM_4387/4388`、BlueTool 脚本、`/usr/share/firmware/bluetooth/`）；MG `bluetooth` 要
  `/chosen/mac-address-bluetooth0`。13T 是 peach BT（高通）在 QUP2 SE6 的 4 线 HS UART（`hardware.md` 2.11）。
- 现状：bluetoothd 被种子禁用；无驱动。
- 缺口：内核——GENI UART 驱动 + `IOSerialBSDClient`（出 `/dev/cu.bluetooth`）、高通 BT 固件下载（rampatch/NVM，HCI 厂商命令）；
  用户态——bluetoothd 对非博通芯片的初始化路径（会发博通 VSC），大概率需要拦截/伪装；AirPods、Apple Watch 等还要苹果私有特性。
- 可行性：C–D。接入点：串口 HCI（H4）+ 在内核里先完成高通芯片初始化，再看 bluetoothd 的“默认芯片初始化”分支能否接受（未验证）。

### 6.12 蜂窝网络、信号格、SIM、通话短信
- 事实：`com.apple.CommCenter` 两个变体同名：有 `baseband` 节点加载蜂窝版，没有加载 `CommCenter-noncellular`；MG `telephony` 等都由
  `HasBaseband` 派生；苹果基带走 `AppleBasebandPCI`（`baseband-pcie`）/`AppleBasebandN71`（`baseband,n41`）与 ARI/QMI 私有协议、激活、BBUpdater。
- 现状：CommCenter（两个变体）都被种子禁用；无基带节点 → 状态栏无蜂窝、无电话。
- 缺口：高通 MPSS 启动（PAS）、IPA 数据通路、CommCenter 兼容层——无公开协议。
- 可行性：D。建议：**不加 `baseband` 节点**；可以单变量试放出 `com.apple.CommCenter`（此时是 noncellular 变体），看设置页是否变快或变慢（第 8 节）。

### 6.13 GPS 与定位服务
- 事实：gpsd 只接三种芯片接口（4 节）；MG `gps` = `/product gps-capable`；locationd 还能用 Wi‑Fi 扫描结果联网定位（`CLPIndoorWifiScan`、
  `CLWifiAPLocation` 一类，经 MobileWiFi/CoreWiFi）。13T 的 GNSS 在高通基带里（`hardware.md` 2.13）。
- 现状：locationd、routined 被禁用；无 GNSS。
- 缺口：GNSS——MPSS + QMI LOC + 把位置喂给 locationd（gpsd 协议不可用；`com.apple.locationd.simulation` 模拟接口是【推断】的旁路）；
  Wi‑Fi 定位——先有 Wi‑Fi，再放出 locationd，保持 `gps-capable` 不设。
- 可行性：GNSS D（按 iOS 原生接口）；Wi‑Fi 定位 C（随 Wi‑Fi）。放出 locationd 本身是 B（单变量上机，看是否等硬件空转）。

### 6.14 相机
- 事实：CMCapture 只认 `AppleH{6,9,10,13,16}CamIn`（ISP 固件 + 私有用户客户端）；MG `still-camera` = `isp` 节点的 `camera-front`；
  iPadOS 17 的 CMCapture 支持 CMIO 扩展设备（外接相机）。13T：三颗 MIPI 传感器 + CAMSS/Spectra（`hardware.md` 2.7）。
- 现状：无；相机 App 不出现（MG 为假），控制中心无手电筒。
- 缺口：内核——CAMSS（CSIPHY/CSID/IFE）、CCI、传感器上电与出原始帧、软件去马赛克；iOS——要么仿 `AppleH1xCamIn`（协议未知，D），
  要么走 CMIO 扩展/外接相机（需要找到 iPadOS 17 的外接相机提供者并伪装成它，C+，未验证）。
- 可行性：D（原生相机栈）/C+（外接相机旁路）。

### 6.15 手电筒
- 事实：控制中心手电筒 → `AVFlashlight` → mediaserverd `FigFlashlight` → `copyStreamForFlashlight…` 要相机流；MG `camera-flash` =
  `/product/camera flash`。LED 硬件在 PM8550 闪光灯模块（SPMI 写，`hardware.md` 2.8），硬件侧可做（B）。
- 现状：无按钮。
- 缺口：iOS 侧没有不经相机栈的手电筒接口。
- 可行性：C–D（受 6.14 牵连）。不建议只加 `flash` 属性让按钮出现（点了没反应）。

### 6.16 NFC / Apple Pay
- 事实：nfcd 等 `AppleStockholmSPMINotification`；MG `nfc` 要 `stockholm` 节点；Apple Pay 要 SEP + 苹果安全元件。13T 是 NXP SN220T（`hardware.md` 2.14）。
- 可行性：D。保持 MG 为假即可，不会卡。

### 6.17 Touch ID / Face ID / 屏下指纹
- 事实：`biometrickitd.mesa` 要设备树 `mesa` 节点 + `AppleBiometricServices`；`AppleMesaSEPDriver` 挂在 `sep-endpoint,sbio`（SEP）；
  MG `HasSEP` = `/arm-io/sep`。iPad 根卷没有 Face ID（pearl）守护进程。
- 现状：设置里只有“密码”；钥匙串/数据保护由 `sheng-aks=1` 模拟（`evidence.md` 第 6 节）。
- 可行性：D（比对在 SEP 内）。

### 6.18 查找（Find My）
- 事实：App 崩溃是 VectorKit 拿不到 Metal 纹理（Find My 组已定位并修了 metal2vulkan 块大小，`findmy/notes/NOTES.txt`）；完整功能还要
  Apple ID 登录（`findmydeviced`/`searchpartyd`）、定位（6.13）、蓝牙（离线查找网络，6.11）；`identityservicesd` 被禁用。
- 可行性：打开不崩 = A/B（Find My 组）；定位与网络功能 = D（受 6.20、6.11 限制）。

### 6.19 Siri 与听写
- 事实：需要麦克风（6.9）和网络（6.10）；“嘿 Siri”需要 AOP 语音唤醒（`/product/audio supports-always-listening`），我们没有。
- 可行性：C（随声音与 Wi‑Fi）；语音唤醒 D。

### 6.20 Apple ID、iCloud、App Store
- 事实：`fairplayd.A2` 被禁用；内核没有 `FairPlayIOKit`（J120 内核缓存有，未预链接，且依赖苹果硬件密钥）；ADT `amfi-only-platform-code = 1`
  只允许信任缓存里的平台代码。`adid`（anisette）、`akd`、`apsd` 等在根卷上，都要网络。
- 现状：无网络；“提示/提醒事项/邮件只有缓存图标、要去 App Store 下载”是同一原因（App Store 分发的应用装不上也跑不了）。
- 可行性：App Store 第三方应用 D；Apple ID 登录【未知】（要先有网络再试，大概率被设备验证拦下，C–D）。

### 6.21 设置页卡顿、守护进程空转（横向）
- 事实：被禁用的 9 个任务（0.1）是“等硬件/崩溃循环”的止血；它们的客户端（设置的电池、蓝牙、蜂窝、信息/FaceTime 页）拿不到服务。
  已证实的卡顿原因在 `evidence.md`（WebKit 进程被 jetsam 杀、PosterBoard 崩溃同步卡顿）；本次静态分析**没有**找到某个设置页因缺硬件而同步阻塞的证据。
- 建议：单变量放出 `com.apple.CommCenter`（noncellular）与 `com.apple.locationd`，各看一轮设置页响应与守护进程退出记录（第 8 节）。
- 可行性：B（只改种子禁用表）。

### 6.22 USB（连电脑、配件）
- 事实：连电脑靠 lockdownd 的 `IOPortTransportState` 启动事件 + `IOUSBDeviceFamily` 的 `IOUSBDeviceConfigurator`（`standardBringup` = 一个
  `AppleUSBMux` 接口）+ `AppleUSBDeviceMux`（`USBDeviceFunction AppleUSBMux`）；J120 内核缓存里都有，但未预链接，控制器驱动是
  `AppleT8011USBXDCI`（`usb-device,t8011`）。13T 是 DWC3（`hardware.md` 2.17）。
- 可行性：C。接入点【推断】：预链接 `IOUSBDeviceFamily`、`AppleUSBDeviceMux`、`AppleUSBDeviceNCM`，自写一个 `IOUSBDeviceController` 子类驱 DWC3
  设备模式，再发布 `IOPortTransportState`；USB 主机/配件同理要 `IOUSBHostFamily` 一侧。

### 6.23 休眠与省电
- 事实：powerd 读 `IODeviceTree:/options`、`IOPMrootDomain`；ADT `/defaults sleep-disabled = 1`；种子关了自动变暗/锁屏/空闲睡眠（`evidence.md`）。
- 可行性：B（内核里 `pagani-idle`、`pagani-screenoff` 已有、默认关、未上机）；iOS 侧不需要新服务，真正系统睡眠要平台挂起，C。

### 6.24 CPU 调度（6 性能核 + 2 超大核）
- 事实：生成器支持 `unixnu,cluster-type`（`'E'`/`'P'`）写进 ADT `cpu*/cluster-type`；iOS 用户态只经 `hw.nperflevels`/`hw.perflevelN.*` 等
  sysctl 与 QoS 感知；thermalmonitord/powerd 想要的 `clpc` 服务缺失只是告警。
- 可行性：B–C（内核调度器配置，非 iOS 用户态问题）。

### 6.25 持久存储
- 事实：Data 是内存盘，`/chosen ephemeral-storage = 1` 让 datamigrator 迁移系统应用；持久化后要去掉该属性，否则行为不同。
- 可行性：C（另有设计工作）。

### 6.26 默认壁纸、小组件、键盘卡顿（只引用）
- 壁纸：`wallpaper/notes/root-cause.md`（WallpaperKit 只认小写 `any` 的 `logicalScreenClass`，以及 HEIC 无硬件解码）；小组件：`widgets/`；
  键盘：PROGRESS 的 F2 与 `kbt` 追踪。本盘点不重复。

### 6.27 温度与温控
- 事实：thermalmonitord 用 `J120AP-Info.plist`，读温度要 HID 温度服务（事件类型 15）或 SMC；没有时 “No sensors found”。内核里 GPU/CPU 已有自己的温控。
- 可行性：B（可选：把 TSENS 温度以 HID 温度服务发布，让系统热状态有依据）。

---

## 7. ADT 改动建议汇总（都要和对应驱动同时上，不要单独上）

| 改动 | 作用 | 前提 |
| --- | --- | --- |
| DTS 节点 `unixnu,adt-name = "wlan"`（可不带 `reg`） | launchd 加载 wifid | Wi‑Fi 驱动发布 `IO80211Interface` |
| `/chosen/mac-address-wifi0`（6 字节） | MG `wifi`、`WifiAddressData`；wifid 加入网络需要 | 同上；可照 sheng 由 `unique-chip-id` 生成 |
| DTS 节点 `accel`、`gyro`（`unixnu,adt-name`） | MG `accelerometer`、`gyroscope` | 传感器 HID 服务 + 放出 locationd |
| `[adt.product.u32] builtin-mics = N` | MG `microphone` | 麦克风采集 |
| `[adt.product.haptics.u32] supports-3rd-party-haptics`、`closed-loop` | 振动/触感 | 振动组方案 |
| 不加：`baseband`、`gps-capable`、`/product/camera flash`、`mesa`、`stockholm`、`sep` | 避免拉起会空转/失败的守护进程或出现点了没用的按钮 | — |

---

## 8. 早上上机核对清单（iOS 侧，单变量；用户说“修完美再测”，这里只列要测什么）

1. **wifid 到底跑没跑**：看 `ps`/`launchctl list` 一类输出要用户态工具；现成通道是 `PaganiDiag`（`/private/var/ngpu/bin/paganidiag`，
   经 `diag:` 行写 ramoops）——给它加一行“列出 wifid、bluetoothd、locationd、CommCenter 的 pid”最省事。用来解决 5 节的矛盾。
2. 放出 `com.apple.locationd`（只改种子禁用表）：看 locationd 的 exec/exit 记录与 CPU 占用（RUN/CPU 行），确认它在没有传感器、没有 Wi‑Fi 时不空转；
   之后传感器与 Wi‑Fi 定位才有意义。
3. 放出 `com.apple.CommCenter`（noncellular 变体）：看设置页响应是否变化、有无崩溃循环。
4. B 包电池：`[battery] state published pct=…`，状态栏电量与设置 > 电池是否出现（MG `HasBattery`）。

---

## 9. 本目录的工具与派生数据（本地，不入库）

- `ios-tools/mga.py`：libMobileGestalt 处理函数摘要（C 串、CFString、哈希反查、调用）；`mgaddr.py`/`mgcf.py`：按地址摘要/解析 CFString。
  用法：`python3 -I mga.py <libMobileGestalt.dylib> <mgtable.tsv> <mgkeys-deobf.tsv> <names.txt> [正则]`（输入在 `haptics/ios/bin`、`haptics/ios/mg`）。
- `ios-tools/callargs.py`：列出对某导入函数的每次调用及其前面装入 x0–x2 的常量/字符串（powerd 的类名表由它得出）。
- `ios-tools/pairs.py`：在 `__text` 里找 HID 用法页/用法立即数对（CoreBrightness 的 0xFF00/4、0xFF00/8 由它得出）。
- `ios-tools/prelink.py`：解析内核缓存 `__PRELINK_INFO`（带 ID/IDREF 的 XML）为 JSON。
- `ios-data/mg-all.tsv`（MG 全表摘要）、`kexts.json`（J120 kext 个性）、`launchd-conditions.tsv`（带条件/IOKit 启动事件的任务）、
  `d47-names.txt`（D47 设备树名字表，哈希反查用）。
