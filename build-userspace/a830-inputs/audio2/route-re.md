# route-re：13T 音频路由三道关卡的静态逆向结论（iPadOS 17.7.11 / 21H461，J120 产品 + iPhone16,1 身份）

作者键：route-re。日期：2026-10-31。全部结论来自静态逆向（IDA 9.5 idalib + IDAPython，ipsw 桩符号表），没有碰手机。
地址约定：MediaExperience、CoreAudio、libVibeSynthEngine 用 dyld 缓存未滑动地址（21H461 的缓存是 **arm64，不是 arm64e**，
没有指针认证，`dyld_v1   arm64`）；VirtualAudio（`/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin`，不在缓存里）用镜像内偏移
（镜像基址 0）；audiomxd 用 `0x1000xxxxx`。

| 镜像 | UUID | 未滑动 __TEXT |
|---|---|---|
| MediaExperience | `AE5AC6D3-7A84-3C86-842B-BA740530C839` | `0x198747000` |
| VirtualAudio | `23C2D062-74B1-3E5B-81AB-645D1658D573` | `0x0` |

复现脚本与关键反编译输出：`build-userspace/a830-inputs/audio2/route-re-tools/`（`run.sh DB 片段.py 输出`，idalib 只读打开，不保存）；
新建的 CoreAudio 数据库：`build-userspace/a830-inputs/audio2/ida-coreaudio/CoreAudio.i64`（带符号）。

---

## 0. 结论先行

1. **关卡一（`'srzl'`）是最可能的阻塞点，但成因不是“被禁用的守护进程卡住了 MediaExperience 初始化”。**
   从 audiomxd 启动到 `FigVAEndpointManagerCreate` 设置 `'srzl'` 的整条路径（755 个可达函数，含块与 ObjC 方法）里，
   找不到对 CommCenter、bluetoothd、identityservicesd、locationd、routined、fairplayd、PerfPowerServices 的**同步**依赖；
   IDS 只是 dlopen 加异步派发，蓝牙端点管理器在 `'srzl'` **之后**才创建。
   真正的结构性风险是：**MediaExperience 只设一次 `'srzl'`、不检查返回值、永不重试；而 VirtualAudio 在自己的延迟初始化
   完成前（插件对象 `+108 != 2`）拒绝一切 SetPropertyData**（`sub_1E154` @`0x1e210` 检查，@`0x232ac` 抛
   “VirtualAudio PlugIn is not initialized yet”）。只要 MX 的那一次调用落在 VA 发布默认 VAD 与 `+108 = 2` 之间（竞态），
   或 MX 的 9 秒等待先超时（VA 初始化被拖慢），`'srzl'` 就永久丢失：VA 设备过滤器停在“只收六个 UID”，
   我们的设备永远不被收下，所有路由变更被拒绝——与 07:01、07:08 两次运行的现象完全吻合（VA 已初始化、设备在 HAL 里、
   默认 VAD 无输出端口、`vcsp=0`、整轮运行状态不变）。
2. **关卡二（设备创建）在 `'srzl'` 到位后应当通过。** 独占模式 `'oink'` 完全由 HAL 实现（插件不参与），
   VA 的 Thunderbolt 设备创建读到的属性插件都答了（sheng 上同一插件已证明），身份（iPhone）不进入这条路径。
3. **关卡三（路由）在 `'srzl'` 到位后大概率也通过**：J120 路由库有 `'pap '→'ptbo'`，`'ptbo'` 的标志 `0x616` 使其“已连接即可路由”。
   但路由变更必须经 MediaExperience 的串行化（VA 调 MX 的监听器 → 派发到 AudioToolbox 的控制队列 ACQ → 再回调 VA 的 `'iupd'`），
   ACQ 若被堵，表现会是“S H 1 T 但 out=0”。
4. **静音开关在 Thunderbolt 端口上不会原生**：VA 只在静音模式下裁掉标志含 `0x8` 的端口（`sub_679CC`+`sub_194340`），
   `'ptbo'` 是 `0x616`，不含 `0x8`，所以 Ambient/SoloAmbient/铃声类别在静音时仍会从我们的设备发声。冒充 “Speaker” 能拿到原生行为，
   但 `Device_Speaker_Aspen` 需要 TapStream、硬件音量范围、静音控件、遥测数据源，今晚不可行。
5. **与触感组的硬冲突**：一旦设备树出现名为 `haptics` 的节点（`DeviceSupportsHaptics` 变真），VA 延迟初始化会**最长等 8 秒**找
   `'pact'` 端口（`sub_14CC58` @`0x14d630`），VA 初始化完成时间被推到 MX 9 秒 VAD 等待的边缘：只要 MX 比 VA 的延迟初始化
   早约 1 秒开始等，MX 就会超时并在 VA 未就绪时设 `'srzl'`，`'srzl'` 丢失；即便不丢，开机后音频就绪也推迟 8 秒。
   加 haptics 节点之前，必须保证 UID 为 `Actuator` 的 HAL 设备在 HAL 初始化时就已发布（`Actuator` 正好在“srzl 之前允许的六个 UID”里）。

推荐改动（第 6 节详述，按把握排序）：R1 只读探针加 MediaExperience 进度标记与 ACQ 存活检测（零风险，早上就能定位到具体步骤）；
R2 默认关闭的 `'srzl'` 修复（引导参数打开，仅在“MX 已尝试 + VA 已初始化 + 监听器为空”三条件同时成立时，把 MX 自己的
两个监听器补装上，一次性，U 行记录）；R3 内核打印 VA 抛异常/失败调用的追踪条目；R4 触感协调约束。

---

## 1. 现场证据回顾（07:01 与 07:08 两次运行，完全一致）

`工具/gpu-test-loop/runs/20261031-070114-pkgD-longpress-1/merged.log`：

- `31.776 audio: open 1 by audiomxd pid 92`：插件在 HAL 初始化时加载（audiomxd 主函数里 `AudioToolboxServerInit` 期间）。
- `32.623 audio plug loads=1 opens=1/0 … sets=0 writes=0 … unk=0`：之后整轮再没打印（摘要键含未知属性计数、音量设置次数，
  键不变就不打印）→ 整轮**没有任何未答属性、没有一次音量设置、IO 从未开始**；audiomxd 没有重启（opens 只有 1）。
- `52.643 audio route #1 r1 dev=2 va=63 vad=72 ours=35 vold=1 run=0 vcsp=0/0 vc=0 cat=csav mode=imdf out=0 …`，
  之后 60/150/400 秒的定时探针都没有新行（行内容哈希不变才不打印）→ 状态从 52 秒到运行结束都没变。

新推论（本次逆向得出）：

- **`cat=csav mode=imdf` 不说明 MediaExperience 做过什么**：这是 VA 的出厂默认。`InitFunc_32` @`0x410a8c` 把
  `xmmword_5CE908` 置为 `"vascfdmi"`，`New_VirtualAudio_PlugIn` @`0x15e624` 把它拷到插件对象 `+224/+228`，
  `'rctx'`/`'inpm'` 原样返回这两个字（`sub_518EC` 中 `case 'rctx'` 返回 `*(a1+224)`）。
- **但它证明 VA 的延迟初始化在 52 秒前已完成**：VA 的 GetPropertyData 对除 `'vain'` 外所有选择子先检查 `+108 == 2`
  （@`0x51a24`，不满足则 @`0x55a28` 抛“not initialized yet”）。探针成功读到 `'rctx'`、`'inpm'`、`'aprc'`，所以 VA 已初始化。
- `q=13`（音量控件被读次数）不能区分“VA 是否建过我们的设备”：HAL 自己在发布时就会读（`HALS_PlugInControl::RefreshCache`
  @`0x1b6920e24` 每次读 `'clas' 'bcls' 'cscp' 'celm'` 四个，`UpdateControls` @`0x1b6759848` 还会再刷），探针本身也读。
- `unk=0` 也不能区分：HAL 对插件设备的 HasProperty 默认分支只有在选择子属于插件的自定义属性时才转给插件
  （`HALS_PlugInDevice::HasProperty` @`0x1b6750448`，默认分支 `HALS_Device::HasProperty`，否则经 `this+1552` 虚表 `+24`
  = `HALS_PlugInObject::IsCustomProperty` @`0x1b6abbe38` 判断，只有自定义属性才转给插件），
  VA 问 `'ssc#'` 之类不会落到插件的未知属性计数里。

---

## 2. 关卡一：从 audiomxd 启动到 `'srzl'`

### 2.1 完整路径（全部在 audiomxd 主线程上）

| 步骤 | 地址 | 说明 |
|---|---|---|
| audiomxd `start` | `0x100002dbc` | `wd_endpoint_register`（watchdogd 已禁用 → 空操作，**挂死也不会被看门狗重启**） |
| `AudioToolboxServerInit` | 调用点 `0x1000032fc` | HAL 在这里初始化，加载 VirtualAudio 与我们的插件（31.776 s 的 open） |
| `MXInitialize` | 调用点 `0x100003444`，函数 `0x19886ad24`（导出） | `dispatch_once(&MXInitialize.onceToken @0x1edd49f70)` |
| `__MXInitialize_block_invoke` | `0x19886ad4c` | → `cmsmInitializeCMSessionManager` |
| `cmsmInitializeCMSessionManager` | `0x1988b3ccc` | 见下表 |
| audiomxd 自己的 XPC 监听 | `xpc_connection_create_mach_service` 调用点 `0x100003a34` | **在 MXInitialize 返回之后**才创建 |

`cmsmInitializeCMSessionManager` 内、`FigVAEndpointManagerCreate`（调用点 `0x1988b4398`）之前的调用顺序与依赖：

| 调用点 | 函数 | 阻塞/外部依赖结论 |
|---|---|---|
| `0x1988b3d0c` | `CMSMDeviceState_UpdateDeviceConfiguration`（once） | 进程判定、MG 读值；无外部等待 |
| `0x1988b3d10/14` | 偏好迁移（`com.apple.mediaexperience`/`com.apple.springboard` 偏好） | 本地 CFPreferences |
| `0x1988b3d24` | `-[MXMediaEndowmentManager loadMediaEndowments]` | 本地 |
| `0x1988b3dac/b8` | 看门狗队列登记、`gCMSM`（`0x1edd499e8`）互斥量创建 | 无 |
| `0x1988b3ea0..b0` | `FigRouteDiscoveryManagerInit/Start`、`FigRoutingManagerInit`、`CMScreenInitialize`、`MX_FrontBoardServices_Initialize` | 只有 MX 内部队列上的 `MXDispatchSync`；FrontBoard 只建 `FBSDisplayLayoutMonitor`；`CMScreen*` 经 BackBoardServices（backboardd 在跑） |
| `0x1988b3eb4..c0` | `CMSM_IDS_Initialize`/`IDSClient`/`IDSServer`/`IDSConnection_Initialize` | **只有 dlopen IDS.framework、取类、`MXDispatchAsync`**；identityservicesd 被禁不会卡这里 |
| `0x1988b3ec4` | `MX_CoreServices_Initialize` | 异步 |
| `0x1988b3f44..54` | `PVMInitialize`、`CMSystemSoundMgr_Initialize`、`CMSMDeviceState_Initialize`、`MX_IOKit_Initialize` | MG 读值、notify 注册、MX 内部 `MXDispatchAsyncAndWait`；无外部守护进程 |
| `0x1988b40a8/b0` | `notify_register_dispatch("com.apple.appletv.audiostart")`、`cmsmGetAudioDeviceStart` | 无 |
| `0x1988b40b4/bc` | `FigVibratorIsVibratorAvailable`，为真才调 `FigVibratorInitialize` | 现在为假（见 2.4），跳过 |
| `0x1988b4328..80` | `FigRoutingManagerCopySystemContext` 等 | MX 内部 |
| `0x1988b4398` | **`FigVAEndpointManagerCreate`** `0x1987fad58` | 见 2.2 |
| `0x1988b43a4` | `FigRoutingMangerCreateBluetoothEndpointManager` | **在 srzl 之后**；bluetoothd 被禁不影响 srzl |

种子实际禁用的任务（`2026-10-31-pkgAB/phone-island-apps.json` 的 `disabled_jobs`）：`com.apple.CommCenter`、`com.apple.PerfPowerServices`、
`com.apple.bluetoothd`、`com.apple.fairplayd.A2`、`com.apple.identityservicesd`、`com.apple.iomfb_fdr_loader`、`com.apple.locationd`、
`com.apple.routined`、`com.apple.watchdogd`。

方法：`route-re-tools/s13.py`、`s15.py` 从 `cmsmInitializeCMSessionManager` 出发（截止 `0x1988b4398`），沿 BL、全局块 invoke、
函数指针引用、`objc_msgSend$选择子` 到同名 ObjC 实现遍历 6 层，共 755 个函数；扫描阻塞原语（`dispatch_sync`、信号量、
`xpc_*_reply_sync`、`CFRunLoopRun`、`usleep` 等）和守护进程关键字（CoreTelephony/CommCenter、IDS、Bluetooth、CoreLocation、
routined、FairPlay、PowerLog）。结果：同步原语只有 MX 自己队列上的 `MXDispatchSync`/`MXDispatchAsyncAndWait`；
守护进程关键字全部落在属性处理函数或异步回调里（例如 `MXCoreSessionSetProperty` 的 “EnableBluetoothRecording”），
初始化路径不同步等待任何被禁的任务。被禁任务的 MachServices 在 launchd 里不登记，客户端 `bootstrap_look_up` 会立即失败，
所以即便有同步调用也是“快速失败”，不是挂死。**结论：重新打开 CommCenter/bluetoothd/identityservicesd/locationd/routined/fairplayd
不是关卡一的修复手段，证据不支持。**

MobileGestalt 中随 iPhone 身份变化、且在这条路径上的答案：`DeviceClassNumber`（1）、`ringer-switch`（J120 为假 →
`CMSMDeviceState_Initialize` 不去调 `BKSHIDServicesGetRingerState`）、`ReleaseType`、`nVh/gwNpy7Jv1NOk00CMrw`——都只是读值，不阻塞。

### 2.2 `FigVAEndpointManagerCreate` 内部（`0x1987fad58`）

- 功能开关 `MX_FeatureFlags_IsStartupSequenceChangeEnabled` @`0x1988022c0` 读的是 `os_feature("VirtualAudio","startup_sequence_change")`；
  `System/Library/FeatureFlags/Domain/VirtualAudio.plist` 里没有这一项 → **关闭 → 走旧启动顺序**：
  1. 进入即把端点管理器写进 `qword_1EDD49A40`（进度标记 A）。
  2. `vaemAddAudioDevicesChangedListener` @`0x1987f3788`：注册 `'dev#'` 监听并立刻调用一次 `vaemAudioDevicesChanged` @`0x1987f32d0`
     （用 `'duid'` 把 `VirtualAudioDevice_Default` 翻成 ID）。
  3. 若还没有默认 VAD：在 `vadInitializationCompleteSemaphore` 上**最多等 9 秒**，超时只记日志、继续往下走。
  4. 逐个查其余 VAD 的 ID；`vaemGetVirtualAudioPlugin` @`0x1987fbeb8` 把 VA 插件对象 ID 缓存到 `dword_1EDD49A7C`（进度标记 B）。
  5. `vaemUpdateCurrentOutputRoutesInfo`。
  6. **`'srzl'` := `{vaemVADSerializationListener @0x1987aed74, NULL}`**（`CMSMVAUtility_AudioObjectSetPropertyData` 调用点 `0x1987fb8c4`），
     紧接着 `'rwbu'` := `{vaemReceiverWillBeUsedListener @0x1987ff4e0, NULL}`（`0x1987fb8fc`）。返回值都不看。
  7. `qword_1EDD49A4C := 'csav','imdf'`（写入点 `0x1987fb908`，进度标记 C：“srzl 调用已经发生”）。
  8. 之后：`'prts'` 监听、默认 VAD 的 `'vcsp'`（输出/输入）监听、数据源监听等。
- `'srzl'` 全程只有这一个调用点（`vaemVADSerializationListener` 唯一交叉引用 `0x1987fb890`），**没有重试**。

### 2.3 VirtualAudio 接受 `'srzl'` 的前提（为什么会“设了也白设”）

| 位置 | 内容 |
|---|---|
| `sub_1E154`（VA 插件 SetPropertyData）@`0x1e210` | 对**所有**选择子先检查插件对象 `+108 == 2`，否则 @`0x232ac` 抛 “VirtualAudio PlugIn is not initialized yet” |
| 同函数 `case 'srzl'`（`0x1e8f4` 一带） | 还要求 `+112`（RoutingManager）非空；通过后存监听器，日志 “SerializationContext is ready. Allowing all devices”（`0x1e9a8`），把 DeviceListener 过滤器换成 `dword_5CEDB8`（模式 3、空集合 = 全收） |
| `sub_14C498`（VA 的 `'inot'` 监听） | HAL 报告初始化结束（`kAudioHardwarePropertyIsInitingOrExiting == 0`）时 `dispatch_async(VA 初始化队列, sub_14CC58)`（`0x14c6d8` 一带）；开关关闭时还会经插件虚表 `off_5A8970+72`（= `sub_518EC`，GetPropertyData）**同步读自己的 `'vain'`**，`'vain'` 在初始化队列上 `dispatch_sync`，即在这个 HAL 通知回调里等 VA 初始化做完 |
| `sub_14CC58`（延迟初始化） | `+108 = 1` @`0x14cca0` → 建 DeviceManager（`sub_203740`，过滤器初值 `dword_5CED98` = 只收 Null_Device、Actuator、Halogen、Hawking、Flicker、Penrose）→ RoutingManager（写 `+112`）→（若 DeviceSupportsHaptics）等 `'pact'` 最多 8 秒 → 以 `csav/imdf` 建立初始路由并**发布 VAD**（`sub_9F87C`/`sub_A569C` @`0x14dd00/0x14dd10`，`sub_BC878` “Publishing object ID”）→ 若干收尾 → `+108 = 2` @`0x14e3ac` |
| `sub_9F198` @`0x9f530` | 没有有效串行化上下文时拒绝任何路由变更（“Attempt to change routes prior to establishing a valid serialization context”） |

于是有两条让 `'srzl'` 永久丢失的路径：

- **竞态**：MX 第 2 步那一次立即检查恰好看到了刚发布的默认 VAD（VA 处在 `0x14dd10` 之后、`0x14e3ac` 之前），MX 不等待，
  几毫秒后设 `'srzl'`，此时 VA 仍是 `+108 == 1` → 抛异常 → 丢失。窗口很短，但在固定的硬件与启动时序上可能每次都落在同一侧。
  缓解因素（未验证）：如果 HAL 在同一条串行通知线程上分发 `'inot'` 与 `'dev#'`，VA 在 `'inot'` 回调里的同步 `'vain'` 等待
  会把 MX 的 `'dev#'` 回调推迟到 VA 初始化完成之后，只剩“立即检查”这一条竞态缝。
- **超时**：VA 初始化开始得晚或做得慢，超过 MX 的 9 秒等待 → MX 超时后照常设 `'srzl'` → 抛异常 → 丢失。
  这一条在“VA 初始化被系统性拖慢”时是**确定性**的（见 2.4 的 8 秒等待）。

### 2.4 触感相关的两处隐患（必须与触感组协调）

- `sub_14CC58` @`0x14d630`：`MGGetBoolAnswer("cBy4BcYs5YWtFHbBpt4C6A")`（= `DeviceSupportsHaptics`，任一名为 `haptics` 的设备树节点）为真时，
  记 “Beginning loop to wait for actuator port”（`0x14d71c`），每 100 ms（`usleep` @`0x14d7fc`）找一次 `'pact'` 端口，最多 8 秒。
  当前设备树里没有 `haptics` 节点（板级与系统配置都搜不到），所以今天不触发；**触感方案一旦加节点而 `Actuator` 设备没在 HAL 初始化时就位，
  VA 初始化至少慢 8 秒；MX 只要比 VA 的延迟初始化早约 1 秒进入 9 秒等待就会超时，在 VA 未就绪时设 `'srzl'` 而丢失。**
  （VA 在旧启动顺序下于 `'inot'` 回调里同步等自己初始化完成，若 HAL 用同一通知线程，MX 的 `'dev#'` 回调也会被推迟到那之后，
  只能靠 9 秒超时之前 VA 完成。）
- `FigVibratorIsVibratorAvailable` @`0x19884de9c`：先看 `libVibeSynthEngine` 的 `VibeSynthEngineHasHardwareSupport` @`0x203cfcba8`
  （传统振动开关，或 `CAHALAudioSystemObject::GetAudioDeviceForUID` 能查到设备），再看 IOService 名 `vibrator`。为真时
  `FigVibratorInitialize` @`0x19884df28` 会在 MX 初始化里**同步**调用 `VibeSynthEngineInitialize`（`LegacyVibeEngine::setupAudio`），
  位置在 `FigVAEndpointManagerCreate` 之前。触感方案若让这一条变真，需要先确认 `setupAudio` 不会阻塞，否则关卡一会被堵在 MX 内部。

### 2.5 关卡一的判断与最小正确修复

- 可能性：在三道关卡里排第一。它是唯一能同时解释“VA 已初始化、设备在 HAL 中、没有任何失败属性、两次运行完全相同”的路径；
  而“守护进程卡住 MX 初始化”这一版本没有静态证据。两次运行完全相同，说明若真是关卡一，更像是固定启动时序下每次落在同一侧
  （或走了超时路径），而不是偶发；反过来，如果早上 R1 显示 `S`（监听器在），问题就在关卡二/三，第 5 节的表能直接分出是哪一关。
- 最小正确修复：**不是**重新打开守护进程，也不是改 MobileGestalt；而是在确认“MX 已经走到并做过 `'srzl'` 调用、VA 已初始化、
  监听器为空”之后，把 MX 自己本该装上的两个监听器补装上（R2）。这一步产生的状态与 MX 那次调用成功时完全相同：
  VA 之后的每次路由变更都会调用 `vaemVADSerializationListener` → `MXDispatchAsync` 到 ACQ → 在 `gCMSM` 锁下对 VA 设
  `'iupd'`（`__vaemVADSerializationListener_block_invoke` @`0x198750f14`）→ 拿回路由变更信息交给 `vaemVADRouteChangeListener`，
  这正是正常流程。该块用的 VA 插件 ID 来自 `dword_1EDD49A7C`，所以修复必须以进度标记 B 已置位为前提。
- 结构性替代方案（不推荐今晚）：打开 `VirtualAudio/startup_sequence_change`，MX 会先同步等 `'vain'`（VA 完全初始化）再往下走，
  竞态从结构上消失；但这条路径同时会跳过 `'dev#'` 监听注册（只在旧路径的 else 分支里注册），属于 Apple 未正式启用的流程，
  且需要改种子（共享文件），风险大于 R2。

---

## 3. 关卡二：设备创建（`'srzl'` 到位之后）

### 3.1 过滤与重新评估

- DeviceListener（虚表 `off_5AD190`，建于 `sub_203740` @`0x205f40..0x205fc0`，挂在 DeviceManager `qword_5CED90` 的 `+0x58`）
  用 `'rdv#'` 取设备列表（`sub_1D71B0`），`sub_1D6528` 计算新旧设备；被拒的设备由 `sub_153BC4` 从本次取得的集合里删掉，
  函数末尾再把这个集合整体存为缓存（`sub_75DA4(a1+96, …)`），所以被拒设备不进缓存，下次更新仍算“新设备”。
  过滤器常量由 `InitFunc_46` @`0x41171c` 建立：`dword_5CED98 = 2`（只收六个 UID），`dword_5CEDB8 = 3`（排除空集合 = 全收）。`'srzl'` 设入后 `sub_1D6258`（虚表 `+24`）改过滤器并异步再跑一次更新 → 早先被拒的我们的设备会被重新评估并收下。
  这与真机内建设备（Codec/Speaker 也不在“六个 UID”里）的工作方式一致。
- 过滤器当前模式可以直接读：`*(u32*)(*(u64*)(*(u64*)(VA基址+0x5CED90) + 0x58) + 0x88)`：2 = 仍是“只收六个”，3 = 已全放行。

### 3.2 创建步骤与读写的属性

| 步骤 | 地址 | 对我们设备的读写 |
|---|---|---|
| 工厂 `DeviceFactory_Aspen` | `sub_1D0484` | 先比特殊 UID，再读 `'tran'`；`'thun'`（以及 `'pci '`）→ 新建 504 字节对象 |
| `Device_HAL_Common` 构造 | `sub_1BF4E0`，`'oink'` 读 @`0x1bf650` | 读 `'oink'`（必须成功且 4 字节）；值为 -1 → 设 `'oink'` 取得独占；值等于本进程 pid → 抛 “Mediaserverd already has hog mode”；其他 pid → 记错继续 |
| 虚表换成 `off_5A8600`（Device_Thunderbolt_Aspen） | 日志 “Created DriverKit device” | — |
| `CreateControlsAndPorts` | `sub_147564`（虚表 `+136`） | 两个方向 `sub_1BDCC8`：`'stm#'`、各流 `'sfma'`、`'taps'`；有输出流 → `sub_1C4B90` 查 `'vold'`/`'vdb#'`（有则建真实音量控件，否则 NullControl、`vcsp` 恒 0）；`sub_1820FC` 读 `'lnam'`；端口 UID = 设备 UID；输出端口描述符 `qword_5CE7C8` = {`'ptbo'`,`'pcwi'`,`'epun'`, 标志 `0x616`}；`'ssc#'` 输出（`0x147adc`，无则不建子端口）；输入侧 `sub_1C4EF0`、端口 `'ptbi'`（标志 `0x617`） |
| 端口构造/接入 | `sub_183344`、`sub_1C08B0` | 端口一建好即“已连接”（`+504 = 1`） |
| 之后可能的写 | `sub_1BAE64`（虚表 `+48`，通用 Set 包装）、`sub_81E8`（`'nsrt'`）、`sub_1E2160/2AF4/2E70`（`'sfmt'`/`'pft '`）、`sub_610C`（`'fsiz'`，HAL 实现） | 插件只接受 48000 Hz、输出 2 声道/输入 1 声道；sheng 上同一组合已被接受 |

### 3.3 独占模式（`'oink'`）由 HAL 实现，插件不参与

- `HALS_PlugInDevice::SetPropertyData` @`0x1b675554c` 的默认分支：只要 `HALS_Device::HasProperty` 认得就交给
  `HALS_Device::SetPropertyData` @`0x1b6b4a89c`；`'oink'` 分支（`case 1869180523`）在命令门里执行块 @`0x1b6b597fc`：
  **忽略传入的值**，取调用方 pid（进程内调用无 HALS_Client 时用 `getpid()`）：当前拥有者等于该 pid → 释放为 -1；
  当前为 -1（或进程内调用）→ 设为该 pid；被别的进程拥有 → 返回 `'!hog'`。读取走 `HALS_Device::GetHogModeOwner` @`0x1b6b5f6b0`
  （拥有者字段在对象 `+0x98`，`_ReadHogModeOwnerStore` @`0x1b6b516ec` 直接返回它）；空闲为 -1，sheng 上 VA 能取得独占也印证了这一点。
- 所以 VA 传 -1、audiomxd 自己的 pid 都没问题；插件里不需要（也不应该）实现 `'oink'`。HAL、VA、我们的插件都在 audiomxd 里，
  探针用 `getpid()` 比较是对的（结果 H）。

### 3.4 逐项对照（VA 会读/写的 vs 插件的回答）

| 属性 | 插件回答 | 判定 |
|---|---|---|
| `'uid '`、`'tran'`(=`'thun'`)、`'lnam'`(Speaker) | 有 | 通过 |
| `'oink'` | HAL 实现 | 通过 |
| `'stm#'`、流 `'sfma'`/`'pfta'`/`'sfmt'`/`'pft '`、`'sdir'`、`'term'`、`'schn'`、`'ltnc'` | 有 | 通过 |
| 流 `'taps'` | 空列表 | 通过（sheng 同） |
| `'vold'`（输出）→ 音量控件；`'vdb#'` → 控件 `'lcdr'` | 有（-36..0 dB） | 通过，`vcsp` 路由后为 1 |
| `'mute'`、`'ssc#'`/`'ssrc'` | 无（HAL 直接答“没有”，不转插件） | 通过（建 NullControl/无子端口） |
| `'nsrt'` 设为 48000 | 接受 | 通过；若设其他速率会被拒（sheng 未遇到） |
| `'cust'`、插件 `'box#'`/`'clk#'`、`'accs'`、`'auto'` | 空/0 | 通过 |
| `'dsds'`（VA 自己对象上的端口发现） | 与插件无关 | — |

**结论：没有会让创建抛异常或跳过设备的不匹配项。** 身份相关的分支（`IsiPhone` `sub_240FD0` 的调用者都是 CPMS 触感/扬声器热预算、
立体声录音、Siri 插话；`DeviceClassNumber==1` 只影响备用 VAD 类型 3，`AlternateVAD.cpp`）都不进入 Thunderbolt 设备的创建。
若早上出现 “S h …”，说明创建在 HAL 之外抛了异常，需要 R3 打印出抛出点。

---

## 4. 关卡三：路由

### 4.1 默认输出如何选出

- 初始路由：延迟初始化以 `csav/imdf` 建立（2.3）。我们的端口接入后，VA 的端口更新（`sub_198660`）重新计算当前类别的路由；
  这一步必须走串行化（2.5 所述 `'iupd'` 往返）。
- J120 路由库（产品号 64，`sub_1DB5F0` → `sub_21D2A0` → `sub_3BA97C`）对默认 VAD 输出 `'pap '` 有一串候选连接：
  `'psto','paps','papa','phdm','ppsd','puso','puio','ptbo','plou','phpw','pscp'…`（`'pap '→'ptbo'` 规则建于 `sub_3BA97C` 反编译第 9143 行附近，
  `0x3d26c4` 处常量）。只有 `'ptbo'` 已连接时，它就是唯一候选。
- `'ptbo'` 标志 `0x616`：不含 `0x40` → “可路由”等于“已连接”（端口虚表 `0x5A9F48` `+160`）；类别过滤 `sub_194F00` 保留含 `0x400`
  或可路由的端口 → 不会被类别规则剔除。sheng 上同一库、同一插件已路由成功，说明 MediaPlayback（以及与其共用 `'pap '` 候选的
  SoloAmbient、Ambient、系统声音 VAD）都能落到 `'ptbo'`。

### 4.2 路由执行依赖 ACQ

`gSerialQueue`（`0x1f4a7f678`）是 AudioToolbox 通过 `MXSetSerialQueue` 交给 MX 的音频控制队列（ACQ，`__MXGetSerialQueue_block_invoke`
里的“AudioControlQueue (ACQ) is being used before being set by AudioToolbox”）。VA 的每次路由变更都要在 ACQ 上执行 `'iupd'`。
初始化末尾派发到 ACQ 的块（`__cmsmInitializeCMSessionManager_block_invoke*`：拷贝当前路由、Bluetooth notify 注册与
`FigRoutingManagerPerformPostInitOperations`（AirPlay 聚合端点）、默认 CMSession 创建）都没有看到对被禁守护进程的同步等待，
但 ACQ 是否存活值得在早上直接测（R1）。

### 4.3 静音开关为什么不原生

- `sub_679CC`（连接裁剪）在 SystemSettingsManager 报告静音、且类别受铃声开关影响时，用 `sub_194340` 删除源或目的端口
  **标志含 `0x8`**（端口 `+168`）的连接。
- 各端口描述符（`InitFunc_*` 静态初始化，`route-re-tools/v29.py`）：

| 端口 | 标志 | 含 0x8（静音时被裁掉） |
|---|---|---|
| `'ptbo'` Thunderbolt 输出 | `0x616` | 否 |
| `'phpw'` 有线耳机 | `0x416`/`0x616` | 否 |
| `'puio'` USB 输出 | `0x61e` | 是 |
| `'puso'` | `0x61e` | 是 |
| `'plou'` 线路输出 | `0x41e` | 是 |
| `'pscp'` | `0x61e` | 是 |
| `'phdt'` | `0x68f` | 是 |

- 所以以 Thunderbolt 身份出现时，静音模式不会让 Ambient/SoloAmbient/铃声类别在我们的设备上静音（系统提示音另由
  `CMSystemSoundMgr` 按 `SystemSoundRingerSettings` 处理，那部分仍正确）。原生行为需要含 `0x8` 的端口：
  USB（`'puio'`）要 HAL 不肯从插件接受的 `'lcID'`（此前 i529 已证实）；`'mbus'`/`'hdmi'` 传输类型走别的设备类（`sub_40E2E8`、
  虚表 `off_5C2220`），要求未逆向；`Speaker` 见 4.5。
- 静音状态本身：`UseMXSilentMode` 为 FeatureComplete，J120 无硬件铃声开关（`ringer-switch` 假）→ 静音是 MX 的持久状态，
  由 SpringBoard（控制中心；iPhone 15 Pro 身份下是操作按钮）设置。要让一加的三段式拨键驱动它，需要输入驱动把拨键报成 HID
  铃声开关/操作按钮并让 `ringer-switch` 为真——不属于音频插件。

### 4.4 铃声音量

J120 与 D83 的 `VolumeButtonsDefaultBehaviour` 都是 `ChangesRingerVolume = false`，`DefaultVolumeCategory` 都是 `Ringtone`，
所以按键调的是媒体音量、铃声音量在设置里调——这本身就是原生行为。差别在路由键：J120 的 `RoutesThatShareVolumes` 有
`Thunderbolt → LineOut`，`CMSessionPresets.RouteDefaultVolumes.LineOut = 0.3`（扬声器是 `Default 0.5`）；D83 没有 Thunderbolt 一项。
即：以 `'ptbo'` 出现时，音量记忆归在 “LineOut” 名下、初值 0.3，HUD 显示通用输出图标而不是扬声器。

### 4.5 冒充 “Speaker” 是否更稳

`Device_Speaker_Aspen` 的 `CreateControlsAndPorts`（`sub_335498`）要求：TapStream 与策略一致（策略来自 `sub_38A0F4`：
`DeviceSupportsSiriBargeIn` 或（`IsiPhone` 且 `DeviceSupportsStereoAudioRecording`）或功能开关 `expanseEnabled`，
后者在 `VirtualAudio.plist` 里存在 → 很可能要求**必须有** TapStream，否则抛 “TapStream is enabled by policy but not found”）、
`'vdb#'`（输出硬件音量范围，读不到就抛）、静音控件、输出数据源与遥测数据源（`spV`/`spI`，设不上就抛 “Failed to set telemetry data sources”）。
插件要新增 tap 输入流、数据源控件与遥测数据源才能过，且每一项都是 J120 四扬声器的产品策略。**今晚不推荐。**
保留 Thunderbolt，接受 4.3/4.4 的差异，是当前最稳的选择。

---

## 5. 早上如何用探针确认每一关

现有 t1 四个字母（`hal/PaganiAudioPlugIn.c` `pagani_audio_probe_why`）：`[S|s|?][H|h|x|?][0-9|+|?][T|t]`。
加上 R1 建议的 MX 进度位后的判读：

| 现象 | 结论 | 对应动作 |
|---|---|---|
| `out≥1`，`ptyp=ptbo`，`vcsp=1` | 三关全过 | 按音量键，`sets` 应增长 |
| `s`，进度位 C=1 | 关卡一：`'srzl'` 已调用但被 VA 拒（竞态/超时） | 带 `pagani-audio-srzl=1` 重启，R2 修复；之后应变为 `S H 1 T` |
| `s`，A=1、C=0 | MX 卡在 `FigVAEndpointManagerCreate` 内（不可能是 9 秒等待本身） | 看 B 位：B=0 卡在 VAD 查询前后；B=1 卡在 `vaemUpdateCurrentOutputRoutesInfo` |
| `s`，A=0 | MX 卡在更早的步骤 | 最高置位的进度位指出卡在哪一步（见 R1 表） |
| `?`（读 `'srzl'` 失败） | VA 延迟初始化没完成/失败 | 看 VA 过滤器模式与 `'rctx'` 是否可读 |
| `S h 0 t` | 关卡二：放行了但没建成（创建抛异常） | 打开 `pagani-audio-trace=1`，R3 打印抛出点（VA 偏移）对照本文地址 |
| `S H 0 t` | 独占了但没建端口 | 同上 |
| `S H 1 T`，`out=0`，ACQ 存活 | 关卡三：路由策略没选它 | 看 `cat/mode` 是否被改成别的类别 |
| `S H 1 T`，`out=0`，ACQ 不存活 | ACQ 被堵，串行化路由执行不了 | 需要找堵住 ACQ 的块 |

---

## 6. 改动清单（按把握排序）

### R1（把握高，零风险）只读探针扩展：MediaExperience 进度位、VA 过滤器模式、ACQ 存活

文件：`drivers/PaganiAudio/hal/PaganiAudioPlugIn.c`（探针）、`PaganiAudioAbi.h`（探针结构加字段，`PAGANI_AUDIO_PROBE_OFFSET + sizeof <= 4096` 仍成立）、
`PaganiAudio.cpp`（`routeRow` 多打一列 `mx=0x%x filt=%u`）。按“新功能默认关闭”的规矩，用新的控制页 options 位，由引导参数
`pagani-audio-mx=1` 打开（早上的测试镜像带上它）；不带时探针行为与现在完全相同。

- 定位 MediaExperience：`slide = (uintptr_t)dlsym(RTLD_DEFAULT, "MXInitialize") - 0x19886ad24`；
  校验 `MXInitialize` 前 16 字节 = `e8a62af008b947f91f0500b141000054`（另可校验镜像 LC_UUID `AE5AC6D3-…-0530C839`）。
  任一不符：进度字记 `0x80000000`（未校验），不读任何私有地址。
- 进度位（每个都是 MX `__data` 里初值为 0 的全局，按初始化顺序）：

| 位 | 地址（+slide） | 含义 |
|---|---|---|
| 0 | `0x1edd49f70` u64 ≠ 0 | `MXInitialize` 已开始 |
| 1 | `0x1edd49f70` u64 == ~0 | `MXInitialize` 已完成 |
| 2 | `0x1edd499e8`（`gCMSM`）≠ 0 | 过了互斥量创建 |
| 3 | `0x1edd49ff8`（`gCMScreen.1`）≠ 0 | 过了 `CMScreenInitialize` |
| 4 | `0x1edd49750`（`gIDSServer`）≠ 0 | 过了 IDS 三件套 |
| 5 | `0x1edd49a90` ≠ 0 | 过了 `MXVW_CreateVectorCMSRouteInfo`（设备状态、IOKit、静音状态之后） |
| 6 | `0x1edd49b60` ≠ 0 | 过了 `FigRoutingManagerCopySystemContext`（紧挨 `FigVAEndpointManagerCreate`） |
| 7 | `0x1edd49a40` ≠ 0 | A：进入 `FigVAEndpointManagerCreate` |
| 8 | `0x1edd49a7c` u32 ≠ 0 | B：VA 插件 ID 已缓存（`'srzl'` 之前一步） |
| 9 | `0x1edd49a4c` u32×2 ≠ 0 | C：`'srzl'`/`'rwbu'` 调用已返回 |
| 10 | `0x1f4a7f678`（`gSerialQueue`）≠ 0 | ACQ 已由 AudioToolbox 设入 |
| 11 | ACQ 上 `dispatch_async_f` 的空块在 2 秒内执行 | ACQ 存活 |
| 12–13 | VA 过滤器模式（3.1 的读法，校验 VA LC_UUID `23C2D062-…-1658D573`） | 2 = 只收六个，3 = 全放行 |

  U 行示例：`audio mx=0x0fff filt=3`。全部只读（ACQ 存活检测只投一个什么都不做的块）。
- 主机测试：在 `scripts/check-pagani-audio-host.py` 里用假内存镜像覆盖“未开始/卡在第 n 步/完成/校验失败”几种组合，断言进度字编码。

### R2（把握：在“s + C=1”时高；其他情况下不会动作）`'srzl'` 补装，默认关闭

- 开关：引导参数 `pagani-audio-srzl=1`（内核把它放进控制页 options 新位，插件读取；缺省 0，不做任何事）。
- 触发条件（全部满足，且每次启动最多一次）：R1 校验通过；进度位 C=1（因此 B=1，`dword_1EDD49A7C` 可用）；
  VA 已初始化（`'rctx'` 可读）；`'srzl'` 读出的 proc 为 0；距插件加载 ≥ 20 秒（与现有首个探针同一时刻）。
- 动作：在探针队列上，经 `dlsym("AudioObjectSetPropertyData")`：
  1. 校验 `0x1987aed74+slide` 前 32 字节 = `ff4301d1f44f03a9fd7b04a9fd030191f30300aa29f8fe97e50300aae87329b0`；
     `0x1987ff4e0+slide` 前 12 字节 = `e00600b000040191dda32414`（这些 ADRP/BL 是 PC 相对的，缓存整体滑动不变）。
  2. 对 VA 插件对象设 `{'srzl', glob, 0}` = `{0x1987aed74+slide, NULL}`（16 字节）。
  3. 成功后设 `{'rwbu', glob, 0}` = `{0x1987ff4e0+slide, NULL}`。
  4. 读回 `'srzl'`、VA 过滤器模式，写 U 行：`audio srzl repair st=%d/%d before=s after=%c filt=%u`。
- 预期效果：VA 放行全部设备 → 建 `Device_Thunderbolt_Aspen`、独占、接入 `'ptbo'` → 路由变更经 MX 串行化执行 →
  默认 VAD 输出端口为 `'ptbo'`、`vcsp=1` → SpringBoard 允许音量变化。
- 不触发的情况：`S`（已正常）、C=0（MX 没走到，补装监听器也无法工作，因为块里要用 MX 缓存的 VA ID）、校验失败。
- 主机测试：用桩 HAL 函数验证“只有条件全满足才调用一次、写入的 16 字节内容、U 行文本”，并断言默认关闭时零调用。

### R3（把握高，零风险）把 VA 的抛出点打印出来

`pagani-audio-trace=1` 时插件已把 VA 的失败调用（类型 4）与 C++ 抛出（类型 8，选择子位放 VA 内偏移）写进追踪段，但内核从不打印。
在 `PaganiAudio.cpp` 的摘要里加一行（类型 4/8 的前 8 条）：`audio vaerr n=%u 0x%x… ` —— 偏移可直接对照本文（例如 `0x232ac` = 未初始化、
`0x9f530` = 无串行化上下文、`sub_1BF4E0` 内 = 独占失败）。这让“S h”“S H 0”这类关卡二故障自带原因。

### R4（硬约束，与触感组协调）

- 在 `Actuator` HAL 设备（端口 `'pact'`）能在 HAL 初始化时随插件一起发布之前，**不要**给设备树加名为 `haptics` 的节点；
  否则 VA 延迟初始化等 8 秒（`0x14d630`），MX 的 9 秒等待随时可能超时，`'srzl'` 丢失（且开机音频就绪推迟 8 秒）。
- 让 `FigVibratorIsVibratorAvailable` 变真之前，先确认 `VibeSynthEngineInitialize`/`LegacyVibeEngine::setupAudio` 不会在 MX 初始化里阻塞。
- 若确需先加节点：同时打开 R2，并在早上检查 VA 日志里的 “Found actuator port!”/“Could not find actuator port after 8 seconds”。

### R5（今晚不改）静音开关的原生行为

记录在案：Thunderbolt 端口不受静音裁剪（4.3）。要原生需要含 `0x8` 的端口；Speaker 路线代价见 4.5；`'mbus'`/`'hdmi'` 路线需另做逆向。
系统提示音与键盘音不受影响（`CMSystemSoundMgr` 按铃声设置处理）。

### R6（不推荐）

- 不要为音频重新打开 CommCenter、bluetoothd、identityservicesd、locationd、routined、fairplayd、PerfPowerServices：没有证据表明关卡一依赖它们。
- 不要今晚启用 `VirtualAudio/startup_sequence_change`（2.5）。
- 不要实现插件侧 `'oink'`（HAL 已实现，3.3）。

---

## 7. 不确定之处（如实标注）

- HAL 是否在同一条串行线程上分发 VA 的 `'inot'` 回调与 MX 的 `'dev#'` 回调——决定竞态窗口大小，未逆向确认。
- 07:01 与 07:08 两次运行里 MX 到底停在哪一步、`'srzl'` 是否在 VA 里——静态无法判定，R1 早上一次就能回答。
- `VibeSynthEngineHasHardwareSupport` 的设备 UID 已在审查中确认：写死在 `CAHALAudioSystemObject::GetAudioDeviceForUID` 里，是 `VirtualAudioDevice_Actuator`（`0x203cfc534`）；闭环时不查，直接返回真。详见 `build-report.md`。
- J120 路由库中 `'pap '→'ptbo'` 覆盖的类别组没有逐条展开（依据是 sheng 上同库同插件已路由成功）。
