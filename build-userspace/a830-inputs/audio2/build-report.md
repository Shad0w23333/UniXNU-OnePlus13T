# audio2 构建报告（对抗审查后）

键：audio-review。日期：2026-10-31。全部结论来自主机测试和 21H461 静态逆向，**没有碰手机**，也没有提交。

## 0. 结论

- 默认路径（新选项全关）与当前镜像行为等价，主机测试和编译都过。
- 审查确认 7 个问题，全部已就地修好（第 3 节）。最要紧的两个：
  1. 闭环开关会在没有 Tunings 叠加层时打开，所有触感客户端报 -43；现在做不到了。
  2. 马达设备依赖内核环映射成功才发布，失败时 VirtualAudio 会空等 8 秒；现在设备在 `Initialize` 时就列出。
- R1、R2 的字节、地址、载荷已对照 21H461 的 dyld 缓存逐项核对，正确，没有改动。
- **主线必须重新集成**：新版 XNU 补丁、重编内核、换新插件 CDHash（第 4 节）。不集成新补丁时 `/product/haptics` 永远不会创建，触感等于没开，但不会变砖。

## 1. 现有产物

| 内容 | 位置 |
|---|---|
| 插件（R1 探针、R2 修复、Actuator 设备） | `UniXNU-Pagani-Drivers/drivers/PaganiAudio/hal/PaganiAudioPlugIn.c`、`hal/PaganiAudioMxLayout.h` |
| 内核服务与桥接 | `drivers/PaganiAudio/PaganiAudio.cpp`、`PaganiAudioHaptics.{c,h}`、`PaganiAudioEngine.c`、`PaganiAudioAbi.h` |
| 马达驱动与 iOS 开关 | `drivers/PaganiHaptics/`（`HapticsCore.c`、`HapticsIos.{c,h}`、`PaganiHapticsIos.cpp`） |
| 叠加层生成器 | `工具/make-phone-haptics-overlay.py` |
| 新插件 | `build-userspace/a830-inputs/audio2/out/hal/PaganiAudio.driver`，CDHash `b961a1e85d00e8e5c03d6bcd2f9062bf54f940bf` |
| 叠加层 | `audio2/out/haptics-overlay/`（30 个文件，这次没变，不用重生成） |
| 集成件 | `audio2/integrate/phone-haptics-overlays.patch`（已更新）、`series.diff`、`make-phone-seed.diff`（这两个没变） |

## 2. 各关卡为什么通过

### 2.1 audiomxd / VirtualAudio 崩溃或挂死

**R2 的字节和地址**（直接解析 `dyld_shared_cache_arm64*`，不是只看 IDA 提取件）：

| 项 | 地址 | 结果 |
|---|---|---|
| `MXInitialize` 16 字节 | `0x19886ad24` | 一致 |
| `vaemVADSerializationListener` 32 字节 | `0x1987aed74` | 一致 |
| `vaemReceiverWillBeUsedListener` 12 字节 | `0x1987ff4e0` | 一致 |
| 10 个进度全局 | `0x1edd49xxx`、`0x1f4a7f678` | 都在可读写的 `.33.dylddata` 映射里，初值 0 |

- 缓存是 `dyld_v1 arm64`，没有指针认证，裸地址可用（J120 是 A10X，与此相符）。
- 载荷：MediaExperience 在 `0x1987fb898..0x1987fb8fc` 写 `{函数, NULL}`，16 字节，地址 `{'srzl','glob',0}` 和 `{'rwbu','glob',0}`，顺序先 `srzl` 后 `rwbu`。插件完全相同。
- VirtualAudio 的读写都按 16 字节校验（`0x1e904`、`0x52030`），读回是 `{函数, 上下文}`（`0x52088`）。
- 过滤器路径：`sub_904FC` 返回 `qword_5CED90`，`+0x58` 是 DeviceListener（`0x1e9c8`），`+0x88` 是模式（`sub_1D6258`）。
- 监听器在探针串行队列上设置，与 MediaExperience 的线程无关。VirtualAudio 回调时再由 `MXDispatchAsync` 转到 ACQ。
- 一次性：`gSrzlTried` 在检查字节之前置位，字节不符或失败都不重试。VirtualAudio 的属性入口有捕获块，异步工作走 ExceptionBarrier，抛异常不会带死进程。
- 差异：MediaExperience 实际走的是 AudioToolbox 的 `AQMESetAudioObjectPropertyData`（`gCMSM+0x28`），R2 直接调 HAL。这个包装只是先找 AQ 节点再调 HAL，对插件对象等价。
- C 位不是严格证明：`cmsmSetVADRouteConfiguration` 也写 `0x1edd49a4c`。就算 C 提前置位，R2 写的也是 MediaExperience 自己要写的值，无害。

**马达设备进入 VirtualAudio 的路径**：

- `DeviceFactory_Aspen`（`sub_1D0484`）对 UID `Actuator` 走 `Device_HAL_Common`，与扬声器同一构造。
- `Device_Actuator_Aspen`（`sub_1489EC`）只在设备声明 `'ssrc'` 输入数据源时才等待。插件是纯输出，没有这条。
- 流枚举 `sub_1BDCC8` 对 0 条输入流直接跳过，不抛异常。
- 设备属性答案与扬声器逐项对齐，自定义属性列表和 `taps` 都答空。

**MediaExperience 启动路径里的 `FigVibrator`**（route-re R4 第二条，本次关掉）：

- `VibeSynthEngineHasHardwareSupport` 查的 UID 是 `VirtualAudioDevice_Actuator`（`0x203cfc534`），route-re 7 的未知项解决了。
- 闭环时 `sUseLegacyVibe` 为真（这个变量名是反的），直接返回真，`VibeSynthEngineInitialize` 尾调用 `HapticServerManager_VibeEngineInitialize`，那个函数只返回 0。
- 非闭环时走 `LegacyVibeEngine`，可达 58 个函数里没有等待、睡眠、同步派发类调用；异常在 `0x203cfe998` 一带捕获并转成错误码。
- AudioToolbox 里 `ProductSupportsClosedLoopHaptics` 只有 `loadSynthPreset` 和 `HardwareSupportsHaptics` 两个调用点，都在客户端运行时，开机路径不受影响。

### 2.2 Actuator 在 HAL 初始化时就位

| 情况 | 结果 |
|---|---|
| 内核选项在，环正常 | 列出，IO 正常 |
| 内核选项在，版本块、映射或环头不对 | **仍列出**，IO 返回未运行（修复后） |
| 连不上内核服务，MobileGestalt 说有触感 | **仍列出**，IO 返回未运行（修复后） |
| 连得上，内核没开选项 | 不列出。此时也不会有节点 |
| 连不上，MobileGestalt 说没有 | 不列出 |
| `pagani-haptics` 关闭 | 计划被拒、无节点、无叠加层、无设备，与今天相同 |

MobileGestalt 只在连不上服务时才问，问的就是 VirtualAudio 在 `0x14d630` 要问的同一个键。默认路径上连得上服务，所以一次也不问。

旧答案不会留下：`native_gestalt_cache_drop` 每次开机都删 MobileGestalt 缓存，所以关掉触感后下次开机答案会回到“无”。

### 2.3 桥接

**转换与上限**

- Float32 到 int16：`toS16` 钳位到 ±32767/-32768，NaN 在 arm64 上得 0。
- int16 到 int8：`round(x × 127 × full / cap)`，钳位到 ±127，从不输出 -128。
- 流的 VMAX 就是 cap，驱动再过一遍 `pgh_core_vmax`（`pagani-haptics-vmax-mv`、硬上限 7450、5 V 钳位、低电量规则）。驱动压得更低时实际电压只会更低。

**不会停不下来**

| 情况 | 结果 |
|---|---|
| 客户端停止 | 取到 `stop_frame` 后结束，排空，环清零 |
| 音频服务器崩溃 | `clientClose` 收回用户计数，同上 |
| 音频线程卡住 | 环读出 0，100 ms 后结束流 |
| 内核线程卡住 | FIFO 放完即停（最多约 1 秒） |
| 驱动队列满，结束命令丢失 | FIFO 干 100 ms 后 `underrun` 停 |
| 长时间响 | 通电预算在驱动线程里强制停 |

**不阻塞音频线程**：`writeActuator` 只读写共享内存，没有锁和系统调用。

**节奏与延迟**（从音频服务器写入首个响帧算起）

| 阶段 | 时间 |
|---|---|
| 写在到期前 8 ms，内核提前 1.3 ms 取，2 ms 一轮 | 6.7–8.7 ms |
| 驱动预缓冲 | 8 ms（原来 20 ms） |
| 驱动等待轮询 | 0–2 ms |
| 总线启动 | 约 1–3 ms（首次改 VMAX 再多约 1 ms） |
| **合计** | **约 18–22 ms，低于 30 ms 目标**（原来约 30–34 ms） |

`audio haptics ... lat=` 只量到流开始，不含预缓冲和总线启动，读数要加约 10 ms。

4 ms 的 FIFO 轮询配 2 ms 的服务线程：稳态下 FIFO 里约有 7 ms 余量，两个线程合计卡住超过这个数才会欠载。欠载只是短暂停顿并被计数，不伤硬件。

### 2.4 阶段 A/B 与叠加层

- 顺序已核实：`bsd_autoconf`（`bsd_init.c:928`）里的 `native_legacy_display_late_probe` 同步调 Stage7Probe 的启动函数，早于 `bsd_utaskbootstrap`（`:1209`）。
- 节点现在由 `load_init_program` 里的提交步骤创建，那是 launchd 之前的最后一站，MobileGestalt 第一次提问时节点已在。
- 闭环只在 `hapticengineconfig.plist` 确实可见时写入，否则退回阶段 A 并写日志。
- 无节点的三种情况：插件不在根卷上、内核服务没提供马达、镜像没走到提交步骤（比如 `sheng-gpu-addon` 不是 1）。

scratch 页使用（每个调用都先写自己的缓冲再用，互不依赖）：

| 用途 | 偏移 | 时机 |
|---|---|---|
| SystemOS、Data 挂载 | `0x100..0x34f` | 最早，已结束 |
| 系统叠加层 | `0x400`、`0x600`、`0x800`、`0xb00`、`0xd00` | 之前 |
| 音频叠加层 | `0xe40..0xf58` | 之前 |
| 控制中心 | `0x800..0x921` | 之前 |
| **Tunings 叠加层** | `0x000..0x006`、`0x020..0x03b`、`0x100..0x11e` | 本段 |
| **振动表叠加层** | `0x200..0x206`、`0x220..0x260`、`0x300..0x326` | 本段 |
| cryptex 视图 | `0xa00..0xb..`、`0xc00..` | 之后 |
| MobileGestalt 缓存、LS 库 | `0xc00..` | 之后 |
| 系统应用视图 | `0x800`、`0xc00` | 之后 |
| launchd 的 argv | `0x000..` | 最后 |

launchd 环境区 `0x400..0x6ff` 没碰。挂载现在要求 `pagani-haptics-ios` 为 1 或 2 **且** `pagani-haptics=1`，比任务要求的 `>=1` 更严。

### 2.5 选项全关时

- 内核：`pagani_audio_start` 多解析几个不存在的参数，`gActuator=0`，内存仍是 3 块，线程不跑桥接，多写一个“修订块”到控制页未用区（旧字段偏移由 `abi` 测试对照 sheng 逐字段核对）。
- 插件：不列马达设备，不问 MobileGestalt，不映射第 4 块内存，选择子 5/6 仍被拒。第一轮 `plugin` 测试就是这条路径。
- 马达：`pagani_haptics_ios_publish(0, 0)` 立即返回。
- XNU：解析不到参数就跳过，不挂载、不提交；`overlays:` 日志行多一小段（缓冲 200 字节够用）。

## 3. 确认并修好的问题

| # | 严重度 | 问题 | 修复 |
|---|---|---|---|
| 1 | 高 | `closed-loop` 在 Stage7Probe 就写入，叠加层缺失或没挂上时闭环照开，客户端全部 -43 | 节点推迟到 launchd 前创建，闭环以预设文件可见为前提，否则退回阶段 A |
| 2 | 高 | 节点存在但插件不在根卷或内核服务失败时，VirtualAudio 空等 8 秒，还可能丢 `srzl` | 提交时要求服务提供马达且插件可见，否则不建节点 |
| 3 | 高 | 插件把“设备是否列出”和“环是否可用”绑死，映射或校验失败就没有设备 | 拆开：选项在就列出，无环时只拒绝 IO；连不上服务时问 MobileGestalt |
| 4 | 中 | 首个样本实际到马达约 30–34 ms，超过目标，`lat=` 看不出来 | 预缓冲 20 ms 改 8 ms，共 18–22 ms |
| 5 | 中 | 3600 mV 是板级 6 周期（35 ms）短效果的电压，流却能把它保持几秒，如铃声振动，板上持续电压是 1260 mV | 桥接加持续电平限制：高于 1260 mV 的部分有 80 ms 额度，按 1/4 速度恢复，用完后钳到 1260 mV，可用 `pagani-audio-haptics-sustain-mv` 调 |
| 6 | 低 | `pagani-haptics-ios=3` 会提供 Actuator 却不建节点，条件不一致 | 内核只接受 1 或 2 |
| 7 | 低 | 叠加层只看 `pagani-haptics-ios`，马达关闭时也换振动表 | 挂载加上 `pagani-haptics=1` 条件 |

问题 5 属于保守设计：静态上无法证明 3600 mV 持续驱动一定损坏马达，但板上唯一的持续驱动数据是 1260 mV。如果早上觉得持续振动太弱，把参数往上调即可，不用重编。

## 4. 主线要做的集成

1. 用 `audio2/integrate/phone-haptics-overlays.patch` 覆盖 `UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-haptics-overlays.patch`。series 不用改。新版相对旧版多了提交调用和更严的挂载条件，已用真实编译记录编过，0 错误，告警数与基线相同（57）。
2. **重编内核**。10:19 的 `libPaganiAudio.a`、`libPaganiHaptics.a` 是旧源码，构建树里的驱动目录是符号链接，已指向新源码。
3. 种子换新插件：`add-pagani-audio-data.py --plugin audio2/out/hal --trust ... --replace ...`，并去掉旧 CDHash `f1bc6785…`（如果它已进种子）。
4. 新 XNU 补丁必须配新驱动：补丁引用 `pagani_haptics_ios_commit`，静态链接不接受未定义的弱引用。反过来，旧补丁配新驱动能链接，但永远不建节点。

## 5. 早上的检查

建议参数（75 字节，在启动参数余量内）：

```
pagani-audio-mx=1 pagani-audio-srzl=1 pagani-haptics=1 pagani-haptics-ios=2
```

按盘点的顺序走：先 `pagani-haptics=2` 干跑，再 `=1 pagani-haptics-ios=1`，再 `=2`。

### 5.1 启动行

```
overlay audio stage 5 status 0
overlay haptics tunings stage 5 status 0, vibrations stage 5 status 0
audio: haptics actuator offered: ring 16384 frames, fifo 8000 Hz, full scale 7450 mV, cap 3600 mV, sustained 1260 mV, silence 100 ms
[haptics] ios stage B: /product/haptics created (supports-3rd-party-haptics=1 closed-loop=1)
```

| 看到 | 含义 |
|---|---|
| `[haptics] ios node not created: the audio plug-in is not on the root` | 种子没带插件 |
| `[haptics] ios node not created: the audio service does not offer the actuator` | 内核音频服务没起来 |
| `[haptics] ios stage A ...: no closed-loop: hapticengineconfig.plist is not visible` | 种子没带叠加层，已安全退回 |
| 完全没有 `[haptics] ios` 行 | 内核不含新 XNU 补丁，或 `sheng-gpu-addon` 不是 1 |

### 5.2 探针字母（`audio route ... out=0 t0 t1` 的 t1）

四个字母：`[S|s|?][H|h|x|?][0-9|+|?][T|t]`。

| 现象 | 结论 | 动作 |
|---|---|---|
| `out≥1`，`ptbo`，`vcsp=1/0 vc=1` | 三关全过 | 按音量键，`sets` 应增长 |
| `s` 且 `p` 的第 9 位为 1 | 关卡一：调用过但被拒 | 带 `pagani-audio-srzl=1` 应自动修好 |
| `s` 且第 7 位 1、第 9 位 0 | MediaExperience 卡在 `FigVAEndpointManagerCreate` 内 | 看第 8 位 |
| `s` 且第 7 位 0 | 卡在更早的步骤 | 最高置位指出卡在哪 |
| `?` | VirtualAudio 没初始化完 | 看 `filt` 与 `cat` |
| `Sh0t` / `SH0t` | 关卡二：没建成 | 开 `pagani-audio-trace=1`，看 `audio vaerr` 偏移 |
| `SH1T` 且 `out=0` | 关卡三，或 ACQ 被堵 | 看 `p` 第 11 位 |

### 5.3 `audio mx` 行

```
audio mx p=0x3fff filt=3 acq=2 srzl=3 st=0/0 after=S filt=2>3 n=1
```

| 字段 | 判读 |
|---|---|
| `p` | 第 0–9 位启动进度；7 进入 `FigVAEndpointManagerCreate`；8 缓存插件 ID；9 `srzl`/`rwbu` 已调用；10 ACQ 已设；11 ACQ 存活；12–13 过滤器 |
| `p=0x3fff` | 全过且过滤器已放行（`0x2fff` 是过滤器还停在 2） |
| `p` 最高位 1 | 代码不是 21H461，什么也没读 |
| `srzl` | 1 不需要；2 等待；3 修好；4 失败看 `st`；5 字节不符拒绝 |
| `after=S filt=2>3` | 修复后监听器在，过滤器放开 |
| `n` | 尝试次数，最多 1 |

修复成功后约 5 秒应有一条新的 `audio route` 显示 `ptbo`。

### 5.4 触感计数

触发一次振动或键盘敲击后：

```
audio haptics runs=1/0 io=1/0 frames=N loud=N peak=in/out streams=1/1/0 fail=0/0 sent=N/0 clip=N lat=9000/10000us
audio haptics plug loads=1 vad=N hog=1 writes=N frames=N peak=N ahead=380..400 drop=0/0 unk=0 ....@0 fmt=N stale=0 skip=0 fifo=8000 cap=3600 lim=0
[haptics] stream a=8000 b=3600 mv=3600 ...
[haptics] stop drained ... underruns=0
```

| 现象 | 含义 |
|---|---|
| `vad=0` | VirtualAudio 没建 `VirtualAudioDevice_Actuator`，马达端口没成，查 `audio vaerr` |
| `hog=1` | 音频服务器收下了设备 |
| `runs=0` | 没有客户端启动 IO；阶段 A 可能是第 6 节第 1 条 |
| `fail=n/-19` | 马达驱动没跑（干跑、身份拒绝或故障） |
| `lim>0` | 持续限制生效，振感弱就调高 `pagani-audio-haptics-sustain-mv` |
| `unk>0` | 音频服务器写了插件不认识的属性，已接受并记下选择子 |
| `underruns` 持续增长 | 预缓冲偏小，把 `STREAM_PREBUFFER_MS` 调回 12–16 |
| `drop` 增长 | 取帧太早或 IO 周期迟到 |

### 5.5 MobileGestalt 的答案

当前没有现成工具直接读，用节点和间接证据判断。

| 键 | 阶段 A | 阶段 B | 依据 |
|---|---|---|---|
| `DeviceSupportsHaptics`（`cBy4BcYs5YWtFHbBpt4C6A`） | 是 | 是 | 任一名为 `haptics` 的节点 |
| `VibratorCapability` | 是 | 是 | 同上 |
| `deviceSupports3RdPartyHaptics` | 是 | 是 | `supports-3rd-party-haptics` |
| `DeviceSupportsClosedLoopHaptics`（`gqDnklGQnpv5ilgh5uHckw`） | 否 | 是 | `closed-loop`=1 |

间接证据：`[haptics] ios stage` 行、`vad≠0`；阶段 B 时设置里“声音与触感”出现键盘触感选项，打字能触发 `streams` 增长。

## 6. 残留风险（如实标注）

1. **阶段 A 可能完全不振**：MediaExperience 启动时只查一次 `VirtualAudioDevice_Actuator`，`dispatch_once` 不重试。如果当时 VirtualAudio 还没发布它，这次开机就没有振动。阶段 B 不走这个判断。早上看到 `runs=0` 就直接上 `=2`。
2. **快速连续打字**：结尾 100 ms 静音也算通电时间，连续高频触发约 10–50 秒后预算耗尽，触感会断续，预算恢复后自动继续。这是限制不是故障。
3. **IO 常驻时的功耗**：只要有客户端保持 IO，服务线程就 2 ms 一轮。
4. **`'pact'` 端口未经实机验证**：VirtualAudio 的工厂路径静态上走得通，J120 路由库是否完整接受这个端口没有逐条展开。即使出错，也只是端口没成，VirtualAudio 的 ExceptionBarrier 不会让进程崩。
5. **持续限制的数值**：1260 mV 和 80 ms 是按板级数据取的保守值，手感要上机才知道。
6. **R2 只补两个监听器**：VirtualAudio 未初始化期间 MediaExperience 若还丢了别的属性写入，这里没有补。早上看 `out` 是否真的变成 1。
7. 主机测试不能证明马达真的动，也不能证明 iOS 路由真的走通。

## 7. 测试结果

全部在修复后的活树上重跑通过：

| 项 | 结果 |
|---|---|
| abi | 77 |
| engine | 140 |
| bridge | 99（新增持续电平） |
| plugin | 215 |
| plugin actuator | 110 |
| plugin listed（expected/header/revision/memory） | 23/22/22/22（新增） |
| glue | 1626 |
| vibrator、barriers、trust | 通过 |
| 马达 spmi | 9047 |
| 马达 core | 92809 |
| 马达 ios | 195（新增提交步骤） |
| 马达 args | 通过 |
| 安全变异 | 31 个全杀 |
| 内核归档（真实编译记录，输出在临时目录） | PaganiAudio、PaganiHaptics 都过 |
| `kern_exec.c`（打新补丁后） | 0 错误，告警 57，与基线相同 |
