# iOS 17.7（21H461）传感器消费方逆向：内核要发布什么（键：ios-sensors）

- 日期：2026-10-31。状态：**离线静态逆向**（IDA 9.5 无界面 + Hex-Rays，ipsw 3.1.740 `dyld disass/class-dump`），没有碰手机；第 7 节列出明早上机要核对的点。
- 输入：OS 卷 `osmnt-21H461` 的 `/usr/libexec/backboardd`、`/usr/libexec/locationd`、`/System/Library/HIDPlugins/*`；SystemOS 共享缓存（`systemos-21H461/.../dyld_shared_cache_arm64*` + `dsc-21H461.a2s`）里的 libMobileGestalt、CoreMotion、CoreBrightness、BaseBoard、SpringBoard、AppleSPULib；原版内核缓存 `UniXNU/out/ipsw/21H461/kernelcache.decompressed`（sha256 `f18b4df8…69de7`，与 `original_stage7.OriginalImage` 钉死的值一致）。
- 地址约定：
  - backboardd / locationd：各自 Mach-O 的虚拟地址（基址 0x100000000）。
  - 共享缓存镜像：未滑动的缓存地址（与 `wallpaper/notes/root-cause.md` 同一口径）。
  - 内核：给两个值。“IDA 地址”是 IDA 直接载入 `kernelcache.decompressed` 的地址；“项目地址” = IDA 地址 − 0x1000000，即 `OriginalImage`/`build-pagani-peripherals.py` 用的口径（核对：`IOHIDEventDriver::dispatchEvent` IDA `0xfffffff005aface8` = 项目 `driver[198]` `0xfffffff004aface8`）。
- MobileGestalt 键名：混淆键 = base64(MD5("MGCopyAnswer"+键名)) 的前 22 字符。libMobileGestalt 里共 1129 个混淆键，用整个共享缓存的字符串做字典反解出 449 个，本文用到的全部在内。

---

## 0. 结论速览

1. **身份层是“半个 iPhone”**：`ProductType`/`DeviceClass`/`DeviceClassNumber` 是 iPhone16,1 / iPhone / 1（来自根节点 `model`），但 `IODeviceTree:/product` 的 20 字节 `product-id` 仍是 J120（`e9fefef6…749f0ec3`，`UniXNU/profiles/os/ipados-17.7.11-21H461/profile.toml` 第 90 行，为了音频路由数据库保留）。CoreMotion 与 locationd 都用 `MGIsDeviceOfType(product-id)` 选机型表，结果是 **硬件类型 62（J120，iPad Pro 12.9 二代）**：运动能力位 `motionCaps = 0x18A230FA`，提供者 `motionProviders = 23 (0x17)`。下面所有 CoreMotion 行为都由这两个数决定，与 iPhone16,1 无关。
2. **用“普通 IOHIDEventService”就能打通的**（不需要模拟 SPU）：
   - 自动亮度：CoreBrightness（跑在 backboardd 里）只认用途对 `{0xFF00, 0x04}` 的服务、type 12 事件，`Level` 是整数 lux。
   - 通话熄屏：backboardd 只认 `{0xFF00, 0x08}` 且 `Placement = 1` 的服务、type 14 事件，靠近 = 掩码 `0x40`。
   - 原始加速度 / 陀螺 / 磁力（`CMMotionManager` 的 raw 数据；locationd 和各 App 进程内的 CoreMotion）：`{0xFF00,0x03}` / `{0xFF00,0x09}` / `{0xFF00,0x0A}`，`Built-In = true`，type 13 / 20 / 21。
3. **J120 表把三件事交给 SPU（AOP 固件）**，只有普通 HID 服务做不出来：
   - 自动旋转：要 `{0x20, 0x8A}` 方向服务，**并且**该服务的“祖父”对象要实现 AppleSPULib 插件协议（`IOCFPlugInTypes` = `7ACF5332-1A35-4893-87CB-BA64E1887FAE`，用户客户端类型 `'ashd'` 0x61736864，选择子 4 与 6）。
   - deviceMotion（姿态、重力、用户加速度）：CLSensorFusionService*SPU，吃 `{0xFF0C, 0x01}`（CMA 服务）的厂商自定义事件（报告类型 2/3/4，长度 64/34/34 字节）。
   - 罗盘航向（CLHeading、指南针 App）：能力位有 0x10/0x20 → 用 CLCompass7（基于 deviceMotion），原始磁力计不够。
4. **必须遵守的坑**：**不能**先单独发布 `{0x20, 0x8A}` 方向服务。CoreMotion 在 backboardd 进程内跑 CLOrientationNotifier；服务一出现，“新服务回调” `0x1941e18e4` 无条件调用 `sendConfiguration` `0x1941e3c8c`，后者在 `0x1941e3d70` 处 `ldr x0,[x19,#0x180]; ldr x8,[x0]` 不判空地解引用 SPU 驱动接口。祖父对象打不开 AppleSPULib 插件时这里是空指针 → **backboardd 每次开机都崩**（静态分析结论；SpringBoard 经 `-[BKAccelerometerInterface systemAppSetOrientationEventsClient:…]` 一要方向事件就会触发；locationd 里同一份代码也会崩）。方向服务只能和第 3 点的 `'ashd'` 用户客户端一起上，默认关。
5. **J120 表下本来就没有、内核补不出来的**：抬起唤醒（`+[CMWakeGestureManager isWakeGestureAvailable]` 要能力位 0x4，J120 没有）、计步 / 距离 / 楼层（`CMPedometer` 要 0x1000000 / 0x4000000，J120 没有）。只能换 product-id（影响面大，见 1.4）或做内核侧替代（抬起 → 模拟电源键亮屏，见 2.10）。
6. **MobileGestalt**（App 安装的 `UIRequiredDeviceCapabilities`、设置界面开关都看它）：
   - `accelerometer` / `gyroscope`：IODeviceTree 里**有没有名为 `accel` / `gyro` 的节点**（任意路径，递归按名字匹配）。当前 13T 的 ADT 没有（导出到 ADT 的硬件只有 `UniXNU-sm8750-common/dts/sm8750.dtsi` 第 68 行的 `intc`，平台节点只有 chosen/cpus/psci/arm-io/defaults/options/product/buttons）→ 两个都是 false → 声明需要加速度计 / 陀螺仪的 App 装不上。【评审更正】不用静态补丁（`integrate/ios-sensors-adt-nodes.diff.do-not-apply`，日常包会在没有数据源时报“有加速度计”）：`pagani_sensors_start` 在返回前按启动参数创建 `/product/accel`、`/product/gyro`，只在模式 3。
   - `magnetometer`：DeviceClassNumber ∈ {1,3} → 现在已是 true。
   - `proximity-sensor`：DeviceClassNumber == 1 → 现在已是 true。
   - `ambient-light-sensor`：注册表里存在 `PrimaryUsagePage = 0xFF00 && PrimaryUsage = 4` 的 IOService → 我们的光感服务一注册就是 true（要尽早注册，见 2.1）。
   - `PhosphorusCapability`（气压计，locationd 用它决定 CMAltimeter）：节点名 `pressure`。13T 没有气压计（`ssc-facts.md` 第 1 节），**不要加**。

---

## 1. 身份层与机型表

### 1.1 MobileGestalt 相关答案（libMobileGestalt，缓存地址）

答案表项结构：`+0` 混淆键 C 字符串，`+8` 答案函数，`+0x98` 标志（`sub_1a020e29c` 的用法）。

| 键名 | 混淆键 | 表项 | 答案函数 | 判定逻辑 | 13T 现状 |
| --- | --- | --- | --- | --- | --- |
| accelerometer | `39ZkJVEsL4pmCXbg+89QmQ` | `0x1e7c7a2d8` | `0x1a0229fd8` | `sub_1a0207afc(_, "rwnje21oPXH375umjxV6YQ"(=accel), 'a')`：`-[MGIOKitHelper createTreeIterator:"IODeviceTree"]`（`IORegistryCreateIterator(…, kIORegistryIterateRecursively)`）+ `copyDeviceTreeStructureNext:withFirstChar:`（`0x1a020c8a4`，对每个条目的名字算混淆值比对）；第一个参数 `"IODeviceTree:/arm-io"` 实际没用到 | **false**（没有 accel 节点） |
| gyroscope | `4+qmMh9JBDh72Nq6fD64RQ` | `0x1e7c95d58` | `0x1a022bd0c` | 同上，节点名 `gyro`（`mH5IrpyGkVtmgdqs68KHRg`），首字母 `'g'` | **false** |
| PhosphorusCapability（气压计） | `7mV26K/1a+wTtqiunvHMUQ` | `0x1e7c52058` | `0x1a021f88c` | 同上，节点名 `pressure`（`znvmheFkjr6hiqIK9TrCVw`），首字母 `'p'` | false，应保持 |
| magnetometer | `ELLSWiPOyh0roYBypZFHFA` | `0x1e7c45f38` | `0x1a021bb2c` | `DeviceClassNumber`（`0x1a020ea38`）== 1 或 == 3 | true |
| proximity-sensor | `5v2p6i7PyIMdWOK4n/+G4A` | `0x1e7c5e998` | `0x1a0223524` | `DeviceClassNumber` == 1 | true |
| ambient-light-sensor | `lo3szoQ4sLy7o3+ZD0GcAQ` | `0x1e7c6aa18` | `0x1a02279bc` | `IOServiceGetMatchingService({IOPropertyMatch:{PrimaryUsagePage:0xff00, PrimaryUsage:4}})` 非 0 | 取决于光感服务何时注册 |
| DeviceProximityCapability | `9RryeqF6OCWlbaXP2Qsoag` | `0x1e7c557f8` | `0x1a02205ac` | 节点 `rose`（UWB 芯片），**与接近传感器无关** | false，保持 |
| DeviceSupportsAOP | `HHF6YgqEQ9Kd7MBSVnLsgg` | `0x1e7c48e18` | `0x1a021d1bc` | `IODeviceTree:/arm-io/aop` 下 `'r'` 开头的条目（混淆名 `hh/NoWpEoq1VF4lGDdPUpw`） | false，不要伪造 |
| CompassCalibration / ProximitySensorCalibration / AmbientLightSensorSerialNumber | 略 | `0x1e7c66d78` / `0x1e7c3f9f8` / `0x1e7c6a6f8` | 读 FDR/设备树 | 校准与序列号，非功能开关 | 不处理 |

- 混淆名的意义：这些键比较的是“条目名的混淆值”，所以节点可以放在任何位置。`/product` 不是总线节点，平台专家不会给它的子节点建 nub，任何 kext 都匹配不到 → 放 `/product/accel`、`/product/gyro` 最安全。ADT 生成器 `UniXNU/scripts/unixnu/devicetree.py` 的 `platform_node` 支持子表，`node()` 自动加 `name` 属性；补丁已用 `merge(os_profile, board)` + `platform_node` 实跑过，生成 `/product/{audio,accel,gyro}`。
- 补丁针对 **GPU 线** `UniXNU-Pagani_SM8750-gpu/board.toml`（`工具/make-a830-layout.sh` 的 `BOARD=` 就是它），`patch --dry-run` 通过，`make-a830-layout.sh` 用到的三个替换锚点仍各只出现一次。
- 缓存：MobileGestalt 把答案写进 `…/com.apple.MobileGestalt.plist`；13T 的 Data 每次开机全新，所以每次开机重算。`ambient-light-sensor` 是查注册表的，**光感服务必须在用户态第一次问之前就注册**（不要等 ADSP 起来再注册，服务先挂上、数据晚到）。

### 1.2 product-id → 硬件类型 → 能力位

- `MGIsDeviceOfType`（`0x1a020d5c0`）逐字节比较 20 字节；设备一侧取自 `0x1a0209ffc`：`IODeviceTree:/product` 的 `product-id`（必须是 20 字节 CFData）。所以真正决定 CoreMotion 机型表的是 `product-id`，不是 `model`。
- locationd `CLPlatformInfo::queryEmbeddedSystemHardware`（`0x1007a1ff0`）：J120 的 product-id（前 16 字节常量 `0x101AAB4EC`，末 4 字节立即数 `0xC30E9F74`）→ `return 62`。CoreMotion 自带同一张表（`0x193e8b468`，常量 `0x19425FD45`）→ 同样 62。
- `queryMotionCaps`：locationd `0x1007ac620`、CoreMotion `0x193e7e0c4`：`case 46, 62, 148 → 0x18A230FA`。返回前与 `0x3FFFFDFFFFBFEFF` 相与，bit 8 由默认值 `OverrideMotionCapEclipseService` 决定。
- `motionProviders`：locationd `0x1001fe5f8`、CoreMotion `0x193e93618`：62 → 23。

能力位（`motionCaps`，由使用点反推）：

| 位 | 含义 | 证据 | J120 |
| --- | --- | --- | --- |
| 0x4 | 手势服务（抬起唤醒） | CoreMotion `0x19423daa8`：只有该位才 `new CLGestureService` | 0 |
| 0x8 | deviceMotion6Service（SPU 6 轴） | locationd `0x10125a3b0` 断言；`0x1012594c0` 选择 | 1 |
| 0x10 | deviceMotionService（SPU 9 轴） | `0x10125a458` 断言；`0x1012594c0` | 1 |
| 0x20 | 罗盘选择 CLCompass7 的条件之一 | `0x10026c9b0` | 1 |
| 0x40 | 方向由 SPU 算、经 `{0x20,0x8A}` HID 服务送来 | CoreMotion `0x1941e0ea8`、locationd `0x10044a190` | 1 |
| 0x80 | deviceMotion3Service（SPU 3 轴） | `0x10125a304` 断言 | 1 |
| 0x80000 | “没有陀螺仪” | `-[CMMotionManager isGyroAvailable]` `0x19410ea38` | 0 |
| 0x1000000 | SPU 计步 | `+[CMPedometer isStepCountingAvailable]` `0x193e7dc14` | 0 |
| 0x4000000 | 楼层 | `+[CMPedometer isFloorCountingAvailable]` `0x193eaa81c` | 0 |

提供者位（`motionProviders`）：0x1 罗盘 / 磁力（`CLHeadingCompass` 能力 `-[CLCapabilityMonitor hasCapability:]` `0x100d0f5a0`、`CLMagnetometer`、`isMagnetometerAvailable` `0x19410f3fc`）；0x4 加速度（`CLAccelerometer::openHidDevice` `0x100070c74`，`isAccelerometerAvailable` `0x19410d5e4`）；0x10 气压（`CLPressure` `0x1008b2d78`、`+[CLBarometerCalibration isNotificationSupported:]`）；0x20 CLHA（`{0xFF00,0x1E}`，`0x1006cba60`）；0x2、0x8 未细查。J120 = 0x17：罗盘、加速度、气压三项有（气压还要 `PhosphorusCapability`）。

### 1.3 BSPlatform deviceClass

BaseBoard `-[BSPlatform deviceClass]` `0x18f6e57a0` → 块 `0x18f6fcc04`：`MGCopyAnswer("DeviceClassNumber")` 经表 `0x18f7715f0 = [0,0,2,3,-1,4]` 映射，iPhone(1) → 0。backboardd 只在 deviceClass == 0 时才建接近传感器包装（2.2），现在的身份满足。

### 1.4 换 product-id 的选项（不建议今晚做）

- 机型表里有不带 SPU 位的类型，例如 29：caps `0x10A02201`（没有 0x8/0x10/0x40/0x80），providers 23 → 方向由加速度在 AP 上算（`CLOrientationNotifier::startDetection` `0x1941e1910`）、deviceMotion 用 AP 融合（`CLSensorFusionService9Axis`）、罗盘用 CLCompass6（直接吃磁力计）。这样只靠普通 HID 服务就能让自动旋转、姿态、航向全部工作。
- 代价：`product-id` 同时决定 `MGGetProductType`，音频服务器路由库、MediaExperience 等按它查表（profile.toml 注释写明保留 J120 就是为此），换掉要单独评估。抬起唤醒与计步在类型 29 下也没有（caps 无 0x4、0x1000000）。
- 换成 D83（iPhone 15 Pro 自己的 id）会得到全套 SPU 能力（含手势、计步），但需要模拟的 SPU 命令更多。

---

## 2. 各功能：消费方 → 匹配 → 事件 → 内核最小对象

HID 事件字段号（`IOHIDEventGet*Value` 的第二个参数）= `(type << 16) | index`。

### 2.1 自动亮度（环境光）

**消费方**：CoreBrightness（在 backboardd 内；`BKDisplayBrightnessController` 用 `BrightnessSystem`）。

- `-[BLControl startHIDSystemClient]` `0x1b626a424`：`IOHIDEventSystemClientCreateWithType(…, 1)`，`SetMatchingMultiple` 用途对 `{0xFF00,0x04}`、`{0x20,0x41}`、`{0xFF00,0x08}`、`{0x0D,0x0C}+Built-In`、`{0x0B,0x01}`、`{0xFF00,0x62}`、`{0x80,0x01}`，`RegisterEventBlock` 收事件。
- `-[CBABModuleiOS addHIDServiceClient:]` `0x1b625f348`：`IOHIDServiceClientConformsTo(svc, 0xFF00, 4)` → `-[CBALSNode initWithALSServiceClient:]`（`0x1b620e558`）→ `placement == 2` 走后置光感（AABRear），否则当前置 → 初始化 AABC 并发 `ALSServiceReady`（“ALS service for internal display ready”）。
- `-[CBALSNode initALSProperties]` `0x1b620e910` 读服务属性：`Orientation`、`Placement`（缺省 0）、`ALSSensorType`、`ALSFastIntegrationTime`、`ALSSuperFastIntegrationTime`、`ALSSlowIntegrationTime`；`isColorSupported` `0x1b620f6e0` 读 `crgb`；都可缺省（只打“Failed to load …”日志）。`setReportInterval:` `0x1b620fe30` 写 `ReportInterval`。
- `-[CBALSEvent initWithHIDEvent:andService:]` `0x1b619daf4`：**事件 type 必须是 12**；照度 = `IOHIDEventGetIntegerValue(ev, 0xC0000)`（整数 lux）。`0xC0006`（ColorSpace）== 1 时才取 XYZ（`0xC0007..0xC0009` ÷ π）、色温 `0xC000A` 和子事件里的 SPU 厂商数据（`0x10004`）；不是 1 只打一条日志、按无颜色处理，不影响自动亮度。
- `ColourSensorFilterPlugin` 只匹配 `IONameMatched` 为 `als,ct709/ct711/ct720/tcs3490` 的服务，不会碰我们。
- 亮度链：AABC 把 lux 映射成 nits（引用的 `DefaultCurves.plist` 在卷上不存在，用内置默认曲线；用户拖滑块会改曲线），经显示模块写到 AppleARMBacklight → PaganiPanel 现有的 `brightness` 属性（0…0xdbb）。手动亮度已通，自动亮度复用同一出口。

**内核最小对象**：

| 项 | 值 |
| --- | --- |
| 服务 | 一个 IOHIDEventService（可复用原版 IOHIDEventDriver，挂在我们的 IOHIDDevice 子类上） |
| 用途 | 顶层集合 `0xFF00/0x04` → `PrimaryUsagePage=0xFF00`、`PrimaryUsage=4`、`DeviceUsagePairs=[{0xFF00,4}]` |
| 属性 | `Built-In=true`；`Placement` 不要等于 2（省略即 0）；其余可省 |
| 事件 | type 12，`IOHIDEvent::ambientLightSensorEvent(ts, level=lux(UInt32), ch0..ch3, 0)`（IDA `0xfffffff005afba78` / 项目 `0xfffffff004afba78`），经驱动 vtable 槽 198 `dispatchEvent` 送出 |
| 设置 | 接受 `ReportInterval`（µs）；光感是变化触发型，可忽略或作最小间隔 |
| 换算 | SSC `ambient_light` data[0]（lux，float）→ 四舍五入为整数 |

- 原版 IOHIDEventDriver **没有**光感的报告描述符解析（见 3.2），只能自己建事件。
- 建议实现 `copyEvent(type 12)` 返回最近一次事件：`-[CBALSNode copyEvent]` `0x1b620f93c` 会主动拷贝当前值，IOHIDEventDriver::copyEvent（IDA `0xfffffff005af6314`）不认识光感事件。
- 屏下光感会被屏幕自身的光污染，需要 AP 把亮度/熄亮屏信息喂回 SSC（`ssc-facts.md` 结论 5），否则自动亮度会自激。

### 2.2 接近（通话熄屏）

**消费方**：backboardd。

- `+[BKHIDUISensorConfiguratorFactory sensorConfiguratorForCurrentPlatform]` `0x100059458`：deviceClass == 0 才建接近包装。
- `+[… _proximityServiceWrapper]` `0x1000595a4`：匹配 `{DeviceUsagePage: 0xFF00 (NSNumber 0x1000ef798), DeviceUsage: 8 (0x1000ef7b0)}`。
- 选包装的块 `0x10005972c`：**`Placement` 必须是 1**（否则“ignoring discrete prox service … not top placement”，整个服务被忽略）；`NeedsHostState` 为真 → `BKHIDUIHostStateProximityServiceWrapper`，否则 `BKHIDUIDiscreteProximityServiceWrapper`；`com.apple.AppleProxSupport/hid-driver` 开调试日志时写 `EnableDebugMode=true`。
- 离散包装 `-applyUIMode:` `0x100041e28`：`asyncSetProperties:{DetectionMode: 模式号[, ScreenUnlocked: true]}`（亮屏且退出口袋模式时才带 `ScreenUnlocked`）；若模式带 `postEventWithCurrentDetectionMask`，另外还会经 `asyncSetProperty:forKey:andDelayForSeconds:` 在 0.06 s 后再写一次 `DetectionMode = 3`（常量 `0x1000ef6d8`，“pocket”）。驱动对每次写入 `DetectionMode` 都应按新模式重配，并立即补报一次当前掩码。模式名表 `0x1000E4498`：0 off、1 passive、2 active、3 pocket、4 monitoring、5 passivePocket；`BKSHIDUISensorMode.proximityDetectionMode` 1…13 经 `dword_1000ADCAC = [1,4,2,0,0,2,0,0,0,0,0,4,5]` 映射，`estimatedProximityMode` → 3。
- 主机状态包装 `0x100080034` + `0x1000803f0`：写 `HostStateNotification = {ScreenOn, ProxDetectionMode, DisplayState}`。我们不设 `NeedsHostState`，走离散包装即可。
- `resetCalibration` 写 `ReceiverProximityReset=true`。
- `-[BKHIDProximityEventProcessor processEvent:sender:dispatcher:]` `0x100042cf8`：`mask = IntegerValue(0xE0000)`；检测激活时“有物体”= `mask & 0x40`（模式 6 时 `& 0x240`）；未激活时只看 `0x400`；掩码为 0 时把“HID zero mask”发给各目标。
- 其他订阅者：CoreBrightness（光感遮挡）、locationd（`0x100bb9340`）也匹配 `{0xFF00,8}`。

**内核最小对象**：

| 项 | 值 |
| --- | --- |
| 用途 | `{0xFF00, 0x08}` |
| 属性 | `Placement = 1`（数字，放在 personality 里就会进服务的属性表）、`Built-In=true`；不设 `NeedsHostState` |
| 事件 | type 14，`IOHIDEvent::proximityEvent(ts, mask(UInt16), level, 0)`（IDA `0xfffffff005afbc3c` / 项目 `0xfffffff004afbc3c`）：靠近 `mask=0x40`，离开 `mask=0` |
| 设置 | `DetectionMode`：0 → 停 SSC 接近流；非 0 → 开（变化触发，新请求会立即报一次当前值）。`ScreenUnlocked`、`ReceiverProximityReset` 收下即可 |

- 原版 IOHIDEventDriver 的描述符接近路径（`handleProximityReport` IDA `0xfffffff005af950c`）只会发 `mask = (值 != 0)` 即 0/1，backboardd 要的是 0x40 → **不能用描述符路径，只能自己建事件**。
- `DetectionMode` 等自定义键到不了设备：`IOHIDEventDriver::setProperties`（IDA `0xfffffff005af5988`）只把 `ReportInterval`/`MaxFIFOEvents`/`BatchInterval`/`SniffControl` 写进描述符元素，其余交给 `IOHIDEventService::setProperties` → `setSystemProperties`。内核侧要么接管该服务实例的 `setProperties` 槽（同 PaganiInput 接管 `dispatchEvent` 的做法），要么自己写 IOHIDEventService 子类。
- SSC 一侧可用 `proximity`（NEAR/FAR）或 OPLUS 融合的 `phone_prox`（`ssc-facts.md` 第 7 节）。

### 2.3 加速度计

**消费方**：

- locationd `CLAccelerometer::openHidDevice` `0x100070c74`（providers & 4）：CLIoHidInterface 用途对 `{0xFF00,0x03}`，匹配属性 `{Built-In:true}`（常量字典 `0x102373450`，默认值 `IMULocationID` 存在时再加 `LocationID`）；事件回调 `0x100144524`：type 必须 13，`IntegerValue(0xD0003) == 1`（摇晃）丢弃，否则取 `FloatValue(0xD0000..0xD0002)` + 时间戳。
- App 进程内 CoreMotion：`CLAccelerometer::openHidDevice` `0x193e9309c`，同一用途对。
- backboardd `-[BKAccelerometerInterface processEvent:…]` `0x100012d20`（旧式 BKSAccelerometer 客户端）读 `0xD0000..0xD0003`；`-[BKOrientationManager _initWithDeviceOrientationManager:]` `0x100083790` 用 `BKIOHIDServiceMatcher(0xFF00, 3, builtIn)` 拿服务，只为写 `ReportInterval`（double，µs，`0x10001b75c`）和 `Shake = {ThresholdX/Y/Z, ThresholdPeriod=20000}`（`0x10001b8b4`）。
- locationd 写 `ReportInterval`（long，µs，`0x100071168`）与 `BatchInterval`（µs，限在 10000…1000000，`0x1000710d8`）。

**内核最小对象**：`{0xFF00,0x03}`，`Built-In=true`；type 13，`X/Y/Z` 单位 g。两条路都行：
- 描述符路径：原版 IOHIDEventDriver 认 Sensor 页 `0x453/0x454/0x455`（`parseAccelElement` IDA `0xfffffff005af37ec`，`handleAccelReport` `0xfffffff005af88d0`），数值取 `getScaledFixedValue(kIOHIDValueScaleTypeExponent=2)`，即“原始值 × 10^单位指数”，所以描述符里单位指数要配成结果正好是 g（例：毫 g + 指数 −3）。
- 直接建事件：`accelerometerEvent(ts, x, y, z, type=0, subType=0, sequence, 0)`（IDA `0xfffffff005afb970` / 项目 `0xfffffff004afb970`，x/y/z 为 IOFixed g）。

### 2.4 陀螺仪

locationd `CLGyro::openHidDevice` `0x100ab39d0`：`{0xFF00,0x09}` + `{Built-In:true}`；回调 `0x100ab3d04`：type 20，`FloatValue(0x140000..0x140002)` + `IntegerValue(0x140004)`。单位 rad/s（CMRotationRate）。内核：描述符 `0x457..0x459`（`parseGyroElement` `0xfffffff005af3908`）或 `gyroEvent`（IDA `0xfffffff005afb9c8`，参数同加速度）。

### 2.5 磁力计与罗盘

- 原始磁力：locationd `CLMagnetometer::setMagnetometerInterval` `0x100016698`（providers & 1）：`{0xFF00,0x0A}` + `{Built-In:true}`（默认值 `MagLocationID` 存在时加 `LocationID`）；回调 `0x1001396dc`：type 21，`FloatValue(0x150000..0x150002)`，µT。内核：描述符 `0x485..0x487`（`parseCompassElement` `0xfffffff005af3a24`）或 `compassEvent`（IDA `0xfffffff005afba20`）。建议送未校准值（SSC `mag`），iOS 自己做零偏。
- 航向：罗盘工厂 `0x100b09e20`：`caps & 0x20 || caps & 0x10` → CLCompass7（`0x100ad8070`，吃 deviceMotion 服务的厂商事件 `0x10013a86c`，“CLCompass7 requires deviceMotionService”），否则 CLCompass6（`0x1007d83d4`，直接吃磁力计）。J120 → CLCompass7 → **航向依赖 2.8**。
- `supportsCompass`（`0x1007ac3f8`，`{IOPropertyMatch:{name:"compass"}}`）只影响 providers 在 22/23、54/55 之间切换的机型，J120 固定 23，无关。
- 不要给磁力服务设 `Transport="SPU"`：`AppleSPUAccCompassPlugin`/`AppleSPUDispCompassPlugin` 匹配 `{0xFF00,0x0A}` + `Transport=SPU` + `compass-acc-compensation`/`compass-disp-coex` 属性，只为真 SPU 设计。

### 2.6 气压计（13T 没有）

locationd `CLPressure::setPressureUpdateInterval` `0x1008b2d78`（providers & 0x10）：`{0x20,0x31}`；回调 `0x1008b30dc`：type 31，`FloatValue(0x1F0000)`，单位 kPa（CoreMotion 里压力样本表 `PressureSample(… degC FLOAT, kPa FLOAT)`）。`CLPlatformInfo::supportsBarometer`（`0x1007a9628`）= `MGCopyAnswer("PhosphorusCapability")`，可被默认值 `ForceSupportBarometer` 覆盖。13T 不加 `pressure` 节点、不发布该服务 → CMAltimeter 不可用，这是对的。若将来有：`atmosphericPressureEvent(ts, level IOFixed kPa, sequence, 0)`（IDA `0xfffffff005afc934`），SSC 的 hPa ÷ 10。

### 2.7 自动旋转（设备方向）

**链路**：backboardd `-[BKOrientationManager init]` `0x10008374c` 建 `CMDeviceOrientationManager` → `startDeviceOrientationUpdatesToQueue:withHandler:`（`0x10001a850`）→ CoreMotion **在 backboardd 进程内**建 CLOrientationNotifier 单例（块 `0x1941e016c`：`operator new(416)` + `"CLOrientationNotifier"`）→ `numberOfSpectatorsChanged` `0x1941e0ea8`：

- `caps & 0x40`（J120 为真）→ CLIoHidInterface 订阅 `{0x20,0x8A}`（+`Built-In`），注册事件回调 `0x1941e155c`、新服务回调 `0x1941e18e4`、移除回调，然后 `openHidDriverInterface` `0x1941e2d30`：取方向服务的父、祖父（`IORegistryEntryGetParentEntry` 两次），对祖父 `IOCreatePlugInInterfaceForService(祖父, 7ACF5332-1A35-4893-87CB-BA64E1887FAE, IOCFPlugInInterfaceID)` → `QueryInterface` → 接口表 `+0x40` Open → 存到 `this+0x178/+0x180` → `sendConfiguration`。
- `caps & 0x40` 为假 → `startDetection` `0x1941e1910` 用加速度（+陀螺）在 AP 上算方向。
- 事件回调 `0x1941e155c`：type 必须是 10；`usage = IntegerValue(0xA0004)`，要求在 100…106，方向 = usage − 100（0 未知、1 竖直、2 倒置、3 横向左、4 横向右、5 面朝上、6 面朝下，与 UIDeviceOrientation 同序），即 AppleVendorMotion 页的 0x64…0x6A。
- `sendConfiguration` `0x1941e3c8c`：`SendCommand(0x20, {4, formFactor, handleCover}, 3, out, &3)`（接口表 `+0xA0`）。**`0x1941e3d70` 处对 `this+0x180` 不判空**，新服务回调 `0x1941e18e4` 每次服务出现都调它 → 见结论 4。
- locationd 里有同一实现（`0x10044a190`，用途对 `0x8A00000020`、caps & 0x40），同样的风险：新服务回调 `0x10044a7e4` → `sendConfiguration` `0x10044cb9c`，`0x10044cc78`/`0x10044cc7c` 同样不判空解引用 `this+0x180`。

**AppleSPULib**（`/System/Library/Extensions/AppleSPU.kext/PlugIns/AppleSPULib.plugin`，代码在共享缓存）：

- Info.plist：`CFPlugInTypes` `7ACF5332…` 与 `3BC5CC87-845E-48AB-A9C2-9436001BA68A` → 工厂 `44F6B535-C083-49EF-BFF0-138653A21786`（`AppleSPULibFactory` `0x21ac2c0b4`）：`7ACF5332…` → `AppleSPUHIDDeviceClass`，`3BC5CC87…` → `AppleSPUHIDDriverClass`。
- `AppleSPUHIDDeviceClass`：`Start` `0x21ac29598` → `IOServiceOpen(service, task, 0x61736864 'ashd', &conn)`；`Open` `0x21ac29708` → `IOConnectCallMethod(conn, 4)`，无参数；`SendCommand` `0x21ac2a0b0` → `IOConnectCallMethod(conn, 6, scalarIn[1]={命令}, structIn=载荷, structOut=回复)`。接口表 `0x22f82ba20`：`+0x40` Open、`+0x48` Close、`+0xA0` SendCommand（转发桩 `0x21ac29248`）。
- `AppleSPUHIDDriverClass`（locationd 的 CLSPUHIDDriverInterface 用）：`Start` `0x21ac2adbc` 同样 `'ashd'`；`Open` `0x21ac2af2c` → 选择子 3 + `IOConnectMapMemory(type 0)`（映射失败只清零，不算错）；`SendCommand` `0x21ac27b5c` → 选择子 0。
- 原版对应的内核类是 `AppleSPUHIDDriver`（事件服务）→ `AppleSPUHIDInterface` → `AppleSPUHIDDevice` + `AppleSPUHIDDeviceUserClient`。【评审更正】AppleSPU 不在 13T 的预链接根里（board.toml `[kernel.prelink] roots`），这些类名没有被占用；但也不必重名：backboardd、locationd 都有沙盒（`seatbelt-profiles`），用的是现成的 XNU 补丁 `zzwc-native-adreno-sandbox-grant-all`，在方向设备上设 `NativeSandboxGrantAll=true`。`Probe` 恒返回 0，`Start` 只调 `IOServiceOpen`，已反汇编确认，没有类型检查。

**内核最小对象（第二阶段，默认关）**：

1. 方向服务：`{0x20,0x8A}`，`Built-In=true`；type 10。两条路：
   - 直接建：`orientationEvent(ts, 1 /*CM 用途型*/, 0)`（IDA `0xfffffff005afcd70` / 项目 `0xfffffff004afcd70`）后 `setIntegerValue(0xA0004, 0x64+方向)`（事件 vtable 槽 24，偏移 192，与 PaganiInput 已核验的槽号一致）。
   - 描述符：顶层 `0x20/0x8A` 集合里放 7 个 1 位变量输入 `0xFF0C` 用途 `0x64..0x6A`（独热）；IOHIDEventDriver（`handleDeviceOrientationReport` `0xfffffff005af91b4`，`parseDeviceOrientationElement` `0xfffffff005af3be8`）在某位由 0 变 1 时发事件。
2. 方向服务的祖父对象：属性 `IOCFPlugInTypes = {"7ACF5332-1A35-4893-87CB-BA64E1887FAE" = "AppleSPU.kext/PlugIns/AppleSPULib.plugin"}`；`newUserClient` 接受类型 `0x61736864`；用户客户端 `externalMethod`：选择子 4 → 成功；选择子 6 → 记录命令号与载荷（BootProgress 一行），成功、无回复；其他 → `kIOReturnUnsupported`。
3. 方向计算：在内核里用重力方向加滞回判 6 种状态（竖直 y≈−1、倒置 y≈+1、横向左 x≈−1、横向右 x≈+1、面朝上 z≈−1、面朝下 z≈+1，均为 iOS 约定的 g），或用 SSC `device_orient`（只有四个竖直方向）补 z 轴判断。
4. 两部分必须同时启用；只要 2 没准备好，就不注册 1。

### 2.8 deviceMotion（姿态 / 重力 / 用户加速度）

- `CLDeviceMotion::enableSensorFusionWithMode` `0x1012594c0`（模式位：1 加速度、2 陀螺、4 罗盘、0x10 建 GYTT）：模式 2 → caps&0x80 ? 3AxisSPU : Legacy(AP)；模式 3/67 → caps&8 ? 6AxisSPU : caps&0x10 ? 6AxisAP : Legacy；模式 7/15/143 → caps&0x10 ? 9AxisSPU : 9Axis(AP)。J120 全走 SPU。
- SPU 数据：`CLSPU::CLSPU` `0x10061f414` 订阅 `{0xFF0C,0x01}`（“CMA 服务”，厂商自定义事件 → `onCMAServiceIohidEvent` `0x100136050` 按报告类型分发）和 `{0xFF0C,0x07}`；设备运动报告解析 `0x10013abb4`：首字节类型 1…4，长度表 `0x101aa1700 = {5, 64, 34, 34}`；`populateDeviceMotion` `0x10052891c`：2 = 9 轴（含磁/航向）、3 = 6 轴、4 = 3 轴。配置经 `CLDeviceMotionControl::configure` `0x1007b91e0` → AppleSPULib `SendCommand`。
- 内核建厂商事件：`vendorDefinedEvent(ts, usagePage(u16), usage(u16), version, data, length, options)`（IDA `0xfffffff005afc730`）。
- 状态：报告逐字段布局与命令集还没逆完，列为第三阶段。不做的后果：`CMMotionManager.deviceMotion` 不出数据，罗盘航向不可用；原始加速度 / 陀螺 / 磁力不受影响。

### 2.9 计步 / 楼层

`+[CMPedometer isStepCountingAvailable]` `0x193e7dc14`：caps & 0x1000000，否则 `+[CMMotionUtils featureAvailability:]`；`isFloorCountingAvailable` `0x193eaa81c`：caps & 0x4000000。J120 都没有 → 健康 App 不会有步数。内核无法单独补上（SSC 的 `pedometer` 有数据，但 iOS 不会来取）。

### 2.10 抬起唤醒

- SpringBoard `-[SBLiftToWakeController _reconsiderEnablement]` `0x1cfd452e0`：要 `[CMWakeGestureManager isWakeGestureAvailable]` 且用户偏好 `supportLiftToWake` → `_startObservingIfNecessary` `0x1cfd455a8`：`[[CMWakeGestureManager sharedManager] setDelegate:]` + `startWakeGestureUpdates`。
- `+[CMWakeGestureManager isWakeGestureAvailable]` `0x1941026a4`：要 CLGestureService 存在（caps & 0x4，块 `0x19423daa8`），且 `DeviceClass != "iPad"`（块 `0x193e7b760`）。J120 caps 没有 0x4 → 不可用，设置里也不会出现“抬起唤醒”。
- 内核侧替代（需要用户拍板）：屏幕灭着（PaganiPanel 的 `pagani_screen_state` bit0）且 SSC `pick_up_motion`/`tilt` 触发、接近为远时，经 PaganiInput 按键设备注入一次 120 ms 电源键（与 InputScript 的 `wake` 相同手法）。效果等同按侧键亮屏，系统不会把它当“抬起唤醒”。

---

## 3. 内核侧可用接口（21H461 原版 IOHIDFamily）

### 3.1 事件构造函数（非虚函数，内核缓存里无符号；按事件类型掩码与数据布局认定）

| 事件 | IDA 地址 | 项目地址 | 参数 | 数据区布局（相对事件数据起点） |
| --- | --- | --- | --- | --- |
| Accelerometer(13) | `0xfffffff005afb970` | `0xfffffff004afb970` | ts, x, y, z (IOFixed), type, subType, sequence, options | +0x10 x、+0x14 y、+0x18 z、+0x1C type、+0x20 subType、+0x24 sequence |
| Gyro(20) | `0xfffffff005afb9c8` | `0xfffffff004afb9c8` | 同上 | 同上 |
| Compass(21) | `0xfffffff005afba20` | `0xfffffff004afba20` | 同上 | 同上 |
| AmbientLightSensor(12) | `0xfffffff005afba78` | `0xfffffff004afba78` | ts, level, ch0, ch1, ch2, ch3, options | +0x10 level、+0x14..+0x20 ch0..ch3 |
| AmbientLightSensor(12，带颜色) | `0xfffffff005afbb58` | `0xfffffff004afbb58` | ts, level, colorSpace(u8), options, double c0, c1, c2 | +0x25 colorSpace、+0x28/+0x30/+0x38 颜色分量 |
| Proximity(14) | `0xfffffff005afbc3c` | `0xfffffff004afbc3c` | ts, detectionMask(u16), level, options | +0x10 mask、+0x14 level |
| Proximity(14，变体) | `0xfffffff005afbd00` | `0xfffffff004afbd00` | 同上 | +0x12 置 1 |
| Temperature(15) | `0xfffffff005afbdc8` | `0xfffffff004afbdc8` | ts, level, options | +0x10 |
| VendorDefined(1) | `0xfffffff005afc730` | `0xfffffff004afc730` | ts, usagePage, usage, version, data, length, options | +0x10 页、+0x12 用途、+0x14 版本、+0x18 长度、+0x1C 数据 |
| AtmosphericPressure(31) | `0xfffffff005afc934` | `0xfffffff004afc934` | ts, level (IOFixed), sequence, options | +0x10 level、+0x14 sequence |
| Orientation(10) | `0xfffffff005afcd70` | `0xfffffff004afcd70` | ts, orientationType, options | +0x20 type；用途另用 setIntegerValue(0xA0004) |

- 送出：`IOHIDEventDriver::dispatchEvent` = 驱动 vtable 槽 198（偏移 1584），项目已核验（`build-pagani-peripherals.py` `qualify_events`）。
- 事件 vtable（37 槽，项目已核验）：槽 18 取类型、23 取整数、24 设整数（偏移 192，方向用途就是它写的）、25 取定点；偏移 208（槽 26）是写倾角字段用的写定点值接口（推测 setFixedValue，未单独核验）。
- `IOHIDEventDriver::copyEvent`（IDA `0xfffffff005af6314`）只处理 type 1（厂商）、3（键盘）、10（方向：倾角型与 CM 用途型都能返回当前值）；光感、接近等自建事件的 `copyEvent` 要自己补。
- 这些构造函数要像 PaganiInput 那样按字节钉死后再绑定（建议把上表每个函数的前 32 字节加进资格检查）。

### 3.2 IOHIDEventDriver 的描述符支持（`parseElements` IDA `0xfffffff005af18b8` 依次调 18 个解析函数）

| 功能 | 解析函数 | 认的用途 | 报告处理 |
| --- | --- | --- | --- |
| 加速度 | `0xfffffff005af37ec` | Sensor 页 `0x453..0x455`；或 `0xFF0C` 页 9..11（父集合 `0x73`/`0x79`，作 type/subType/sequence） | `0xfffffff005af88d0` |
| 陀螺 | `0xfffffff005af3908` | `0x457..0x459`；`0xFF0C` 9..11（父 `0x76`/`0x7A`） | `0xfffffff005af8b38` |
| 磁力 | `0xfffffff005af3a24` | `0x485..0x487`；`0xFF0C` 9..11（父 `0x83`/`0x8B`） | `0xfffffff005af8da0` |
| 温度 | `0xfffffff005af3b40` | `0x434` | `0xfffffff005af9008` |
| 设备方向 | `0xfffffff005af3be8` | `0xFF0C` 页 `0x64..0x6A`（CM 用途型）；Sensor 页 `0x47F..0x481`（倾角型） | `0xfffffff005af91b4` |
| 传感器属性 | `0xfffffff005af3d84` | **特征**元素：Sensor 页 `0x30E`（ReportInterval）、`0x31A`（MaxFIFOEvents）、`0x31B`（报告延迟 = BatchInterval）；`0xFF15/1`（SniffControl） | `setProperties` `0xfffffff005af5988` |
| 接近 | （存放在 +488 的元素数组） | — | `0xfffffff005af950c`，掩码只有 0/1，不可用 |
| 光感、气压 | **无** | — | 只能自己建事件 |

- 属性写入：`ReportInterval` 等经命令门调 `element->setValue(值, kIOHIDValueOptionsUpdateElementValues=4)`（`0xfffffff005af5ef4`），会对设备发一次特征报告（`setReport`），值就是 µs 原数，无单位换算。用描述符路径的传感器靠它把采样率传到驱动（我们的 IOHIDDevice 子类需要实现 `setReport`，该槽要像其他覆盖槽一样先做资格检查）。
- `handleStart` 里 `parseElements` 一个元素都不认就失败（“failed to parse elements”）；光感/接近若想复用 IOHIDEventDriver 当服务壳，描述符里至少放一个它认的元素（例如上面的传感器属性特征元素）。

---

## 4. 最小对象清单（汇总）

| 传感器 | 用途对 | 关键属性 | 事件 | 单位与换算（SSC → iOS） | 实现 | 阶段 |
| --- | --- | --- | --- | --- | --- | --- |
| 环境光 | `{0xFF00,0x04}` | Built-In；Placement≠2 | 12，`Level` 整数 | lux 四舍五入 | 自建事件 + 驱动 dispatch；最好实现 copyEvent | 一 |
| 接近 | `{0xFF00,0x08}` | **Placement=1**；Built-In | 14，mask 0x40/0 | NEAR→0x40，FAR→0 | 自建事件；接管 setProperties 拿 DetectionMode | 一 |
| 加速度 | `{0xFF00,0x03}` | Built-In | 13，X/Y/Z | g：**iOS = −Android/9.80665**（平放面朝上 z = −1） | 描述符或自建 | 一 |
| 陀螺 | `{0xFF00,0x09}` | Built-In | 20，X/Y/Z | rad/s，轴与符号同安卓（右手系） | 描述符或自建 | 一 |
| 磁力 | `{0xFF00,0x0A}` | Built-In；不要 Transport=SPU | 21，X/Y/Z | µT，同轴同号，送未校准值 | 描述符或自建 | 一 |
| 方向 | `{0x20,0x8A}` | Built-In；**祖父必须有 'ashd'** | 10，`0xA0004` = 0x64..0x6A | 由重力判 6 态 | 自建或描述符 + 祖父用户客户端 | 二（默认关） |
| deviceMotion | `{0xFF0C,0x01}` | — | 1（厂商），报告 2/3/4 | 四元数等，布局待逆 | 自建厂商事件 + 命令 | 三 |
| 气压 | `{0x20,0x31}` | — | 31 | — | 13T 无，不发布 | — |
| MobileGestalt | — | 节点 `accel`、`gyro` | — | — | 【评审更正】运行时创建（`pagani_sensors_hid_node`），模式 3 才有；静态 diff 作废 | 一 |

坐标系：iOS 与安卓的设备坐标轴相同（竖持时 +X 向右、+Y 向上、+Z 指向用户）；差别只在加速度的符号约定（iOS 报重力方向，安卓报支撑力）和单位。所有事件时间戳用采样时刻的 mach 绝对时间（SSC 的时间戳要换算，见 `ssc-facts.md`）。

---

## 5. 与内核驱动的接口约束（给 PaganiSensors）

1. 第一阶段只发布：光感、接近、加速度、陀螺、磁力五个服务，全部在驱动启动时注册（不等 ADSP），ADSP 起来后再出数据；`pagani-sensors=N` 关时一个都不注册。
2. 每个服务一个 IOHIDDevice 实例（像 PaganiInput 的触摸设备），personality 按用途对限定，并像 PaganiInput 的 `installPersonality` 一样把 `PrimaryUsagePage`、`PrimaryUsage`、`DeviceUsagePairs`、`Built-In=true` 显式写进 personality（这些键会并进 IOHIDEventDriver 的属性表，用户态匹配直接看它们）；接近另带 `Placement=1`。不要设 `Transport="SPU"`。
3. 事件经原版 IOHIDEventDriver 的 `dispatchEvent`（槽 198）送出；光感、接近必须自建事件，加速度 / 陀螺 / 磁力可以走描述符。
4. 方向服务与 `'ashd'` 用户客户端同属第二阶段，必须一起启用；单独启用方向服务会让 backboardd 空指针崩溃。
5. 没有的功能（抬起唤醒、计步、楼层、气压）不要伪造任何服务或 MobileGestalt 键。

---

## 6. 交付物

- 本文：`/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/sensors/ios-sensors.md`。
- 【评审更正】静态 ADT 补丁已作废，改名为 `integrate/ios-sensors-adt-nodes.diff.do-not-apply`，不要应用。节点改由驱动运行时创建。
- 证据：`/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/sensors/re-ios/`（本文引用的反编译 / 反汇编片段：backboardd 接近与方向、MobileGestalt 答案与 449 个反解键名、locationd 能力表与 HID 订阅、CoreMotion 方向通知器与 `sendConfiguration`、内核 IOHIDEvent 构造函数与 IOHIDEventDriver 解析 / 处理 / `setProperties`、CoreBrightness 光感事件）。

---

## 7. 待上机核对

1. `ioreg -l` / `hidutil list`：五个服务的 `PrimaryUsagePage/PrimaryUsage/DeviceUsagePairs/Built-In/Placement` 是否如表；`MGCopyAnswer` 的 `accelerometer/gyroscope/ambient-light-sensor/proximity-sensor/magnetometer` 是否全为真。
2. 自动亮度：log stream 过滤 `com.apple.CoreBrightness`，看到“ALS service for internal display ready”和照度随环境变化；屏下光感是否被屏幕亮度自激。
3. 接近：通话（VoIP 听筒路由）时 backboardd 日志出现 `send mode active/… to proximity service`，遮挡后熄屏、触摸被屏蔽。
4. 加速度符号：平放面朝上 `CMAccelerometerData.z ≈ −1`；陀螺右手系；磁力单位 µT。
5. 方向（第二阶段）：先确认 `'ashd'` 用户客户端被 AppleSPULib 打开（“Opened hid driver interface”日志），再看 backboardd 的 “Current raw accelerometer device orientation changed to …”。另需核对 backboardd / locationd 的沙盒是否限制用户客户端类名（`iokit-user-client-class`）。
6. 气压单位 kPa（若将来有气压计）、陀螺 0x140004 字段的含义（locationd 当作无符号整数传给 `CLGyro::onGyroData`）。

---

## 附录 A：描述符样例（按 3.2 的解析规则写，未上机验证）

加速度（陀螺把 `0x453..0x455` 换成 `0x457..0x459`、顶层用途换成 `0x09`，单位 mrad/s；磁力换成 `0x485..0x487`、顶层 `0x0A`，建议单位指数 −1 即 0.1 µT）：

```
06 00 FF        Usage Page (0xFF00)            ; 顶层用途对 {0xFF00,0x03}
09 03           Usage (0x03)
A1 01           Collection (Application)
85 01             Report ID (1)
05 20             Usage Page (Sensor)
0A 53 04          Usage (0x0453)               ; X
0A 54 04          Usage (0x0454)               ; Y
0A 55 04          Usage (0x0455)               ; Z
16 01 80          Logical Minimum (-32767)
26 FF 7F          Logical Maximum (32767)
55 0D             Unit Exponent (-3)           ; 1 计数 = 0.001 g，IOHIDEventDriver 用 kIOHIDValueScaleTypeExponent 取值
75 10 95 03       Report Size (16), Report Count (3)
81 02             Input (Data,Var,Abs)
85 02             Report ID (2)
0A 0E 03          Usage (0x030E)               ; ReportInterval（µs）
0A 1B 03          Usage (0x031B)               ; 报告延迟 = BatchInterval（µs）
15 00             Logical Minimum (0)
27 FF FF FF 7F    Logical Maximum (0x7FFFFFFF)
55 00             Unit Exponent (0)
75 20 95 02       Report Size (32), Report Count (2)
B1 02             Feature (Data,Var,Abs)       ; 用户态写属性 → setReport(特征, 报告 2)
C0              End Collection
```

方向（第二阶段，必须与 `'ashd'` 祖父一起用）：

```
05 20           Usage Page (Sensor)            ; 顶层用途对 {0x20,0x8A}
09 8A           Usage (0x8A)
A1 01           Collection (Application)
85 01             Report ID (1)
06 0C FF          Usage Page (0xFF0C)
19 64 29 6A       Usage Minimum (0x64), Usage Maximum (0x6A)
15 00 25 01       Logical Minimum (0), Logical Maximum (1)
75 01 95 07       Report Size (1), Report Count (7)
81 02             Input (Data,Var,Abs)         ; 独热：0x65 竖直 … 0x6A 面朝下
75 01 95 01 81 03 Input (Const)                ; 补齐 8 位
C0              End Collection
```

光感、接近用 IOHIDEventDriver 当服务壳时：顶层用途对分别是 `{0xFF00,0x04}`、`{0xFF00,0x08}`，集合里只放上面那组 `0x030E/0x031B` 特征元素（让 `parseElements` 通过、顺便收到 `ReportInterval`），事件全部由驱动自建后调 `dispatchEvent`。
