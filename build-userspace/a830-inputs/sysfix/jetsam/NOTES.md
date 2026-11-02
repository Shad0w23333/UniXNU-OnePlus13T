# sysfix/jetsam：GPU 客户端的单进程内存上限（2026-10-31 夜）

键：jetsam。只交付新文件，没碰手机、没改共享集成文件、没写 `UniXNU/out-gpu`、没提交。

## 0. 结论

1. **iconservicesagent 被杀的是“非活跃硬上限 6 MB”，不是“活跃 20 MB”。** J120 的 jetsam 配置里它自己的条目只有
   `ActiveSoftMemoryLimit 20`（软，超了不杀）和 `JetsamPriority 140`；`InactiveHardMemoryLimit` 来自同一节的
   `Daemon/Override/Global`（6 MB，硬）。它是按事务记脏的守护进程（`EnableTransactions true`、`EnablePressuredExit true`），
   干完活变“干净”就算非活跃，6 MB 硬上限立刻生效，下一次分配内存就被按单进程上限杀掉（退出原因 `ns=1 code=7` =
   `JETSAM_REASON_MEMORY_PERPROCESSLIMIT`）。证据：`runs/20261031-070114-pkgD-longpress-1/merged.log`
   seq 4816 `100.521 S pid=301 Preferences exec`，seq 4950 `119.216 S exit pid=101 iconservicesagen status=9 ns=1 code=7`。
2. **我们 GPU 驱动的缓冲区映射不计入 phys_footprint**（与插件 `MemoryReport.h` 注释和盘点里的推断相反）：BO 的 CPU 视图是物理内存
   描述符（`kIOMemoryTypePhysical64`），经设备分页器映射，对象是 external，既不记 `iokit_mapped` 也不记 `phys_footprint`。
   所以“改账本”不是解法；占用来自客户端进程自己的堆（Metal→Vulkan 翻译、Turnip、着色器编译与管线缓存、Core Image）。
3. **修法：XNU 补丁 `phone-gpu-client-memlimits.patch`**——打开过 Metal 加速器用户客户端的进程标为 GPU 客户端，它的每个正数上限
   （活跃、非活跃）都抬到至少 512 MB，谁来设都一样；软/硬（是否致命）保持原样，-1/0 不动。默认开，不需要新启动参数；
   `pagani-jetsam=0` 关（回到 Apple 的上限），`pagani-jetsam=<MB>` 改底线（64..4096）。失败只写 U 行，没有 panic。

## 1. 上限从哪来（21H461 逆向，地址均为 21H461 本机文件）

### 1.1 launchd（`/sbin/launchd`）

- `sub_100047230`：读 `/private/etc/com.apple.xpc.launchd.limits.plist`、personas、`foreground_user_endpoints.plist`，
  取 `sysctl hw.targettype`，再调 `sub_10000CAC8` 读 `/System/Library/LaunchDaemons/com.apple.jetsamproperties.<targettype>.plist`
  （格式串 `%s.%s%s.plist`，先试 `/AppleInternal/...`；`NonUI` 版与机型版同时存在会报错）。它由 `sub_1000278C0` 调用，
  紧跟 `os_variant_init_4launchd`、`_os_trace_update_with_datavolume_4launchd`，即 Data 卷就绪之后读一次。
- 取 `Version4` 下 `Daemon/Angel/App/DriverKit/Extension/XPCService/SystemXPCService/SystemApp` 八节，每节 `Default` 与 `Override`。
  格式错（不是字典、缺 `Version4`）走 `sub_10003DBF4`（`__noreturn`，launchd 直接中止）。
- 任务归类 `sub_10000CD10`：`__Angel` → Angel；DriverKit；`UIKitApplication:`/`AlternateSystemApplication:` 标签 → App/SystemApp；
  其余 → Daemon；名字取 `_JetsamPropertiesIdentifier` 或 Label。
- 合并 `sub_10000CF20`：每个键依次取 **Override[名字] → Override[Global] → 任务自己的 JetsamProperties → Default[名字] →
  Default[Global]**（`adr x1, "Global"` 已在反汇编核对）。软/硬同时给出时 `sub_10000D344` 取小的，负数视为没给。
- 签名 xpc 缓存 `/System/Library/xpc/launchd.plist` 里这几个任务（iconservicesagent、SpringBoard、backboardd、chronod、
  `LaunchAngels/com.apple.PosterBoard`）都**没有** `JetsamProperties` 键，上限全部来自机型 plist；iconservicesagent 的任务只有
  `POSIXSpawnType Adaptive`、`EnableTransactions true`、`EnablePressuredExit true`、`RunAtLoad false`。
- 生效：`sub_100011408` → `posix_spawnattr_setjetsam_ext(flags, priority, active, inactive)`（xpcproxy 也导入同一函数，最终在目标
  程序的 exec 上生效），内核 `kern_exec.c` → `memorystatus_update(..., update_memlimit=TRUE, ...)`。服务转储字符串
  `jetsam memory limit (active, %s) = %d MB` 里 flags 位 4/8 即活跃/非活跃“硬”（致命）。

### 1.2 RunningBoard（共享缓存 `RunningBoard`）

- `-[RBJetsamPropertyManager _jetsamTryPath:target:extension:]`（0x205b8f0ac）读同一个文件（日志 `Loaded jetsamproperties from %@`）；
  `_addJetsamValuesForSection:fromPlist:toDatabase:` 只收各节 `Override`（含该节 `Global`）里带 `MemoryLimit` 的键。
- 键名 `-[RBJetsamProperties _keyForCategory:strength:]`：`"%@%@MemoryLimit"`，类别 `Active`/`Inactive` 或客户端指定的
  （如 `PosterProvider_Rendering_Nominal`），找不到换另一个强度、再退回 `Inactive`。
- `-[RBProcess _lock_applyMemoryLimits]`（0x205b5c69c）：先 `memorystatus_control(8 GET_MEMLIMIT_PROPERTIES)`；
  `preventBaseMemoryLimitReduction` 且没有显式值时，**把内核当前的非活跃上限读回来当新值**；然后
  `memorystatus_control(7 SET_MEMLIMIT_PROPERTIES)`，活跃=非活跃=该值，Hard 为致命。功能开关 `RunningBoard/dynamic_memory`
  在 `/System/Library/FeatureFlags/Domain/RunningBoard.plist` 为 `Enabled true`。→ 这条“读回再设”的环路决定了内核侧只能做
  **底线**（幂等），不能做“加量”（每轮会再加一次）。

### 1.3 J120 的数值（`python3 -I jetsam-limits.py` 输出）

| 进程 | 管理者 | 类别/条目 | 活跃 | 非活跃 | 成为 GPU 客户端后 |
| --- | --- | --- | --- | --- | --- |
| iconservicesagent | launchd | Daemon/com.apple.iconservices.iconservicesagent（非活跃来自 Override/Global） | 20 软 | **6 硬** | 512 软 / 512 硬 |
| chronod | launchd | Daemon/com.apple.chronod | 98 软 | 45 硬 | （不是 GPU 客户端） |
| SpringBoard | launchd | Daemon/com.apple.SpringBoard | 415 软 | 320 软 | （插件规则 `!SpringBoard`，不是） |
| backboardd | launchd | Daemon/com.apple.backboardd | 无 | 无 | 无 |
| PosterBoard | RunningBoard | Angel/com.apple.PosterBoard | 350 软 | 350 软 | 512 软 / 512 软 |
| WidgetRenderer_Default | RunningBoard | SystemApp/com.apple.chrono.WidgetRenderer-Default | 98 软 | 45 软 | 512 软 / 512 软 |
| 海报扩展 | RunningBoard | Extension/com.apple.posterkit.provider | 15 软 | **15 硬** | 512 软 / 512 硬 |
| 小组件扩展 | RunningBoard | Extension/com.apple.widgetkit-extension | **30 硬** | **30 硬** | 512 硬 / 512 硬 |
| 其余扩展 | RunningBoard | Extension/Global | **6 硬** | **6 硬** | 512 硬 / 512 硬 |

海报扩展的状态类别：`Rendering_Nominal 30`、`Rendering_Entitled 200`、`Rendering_Photos 250`、`Updating_Entitled 30`、
`Updating_Photos 250`（都是软）、`Editing -1`。

- 推论：WidgetRenderer_Default 与 PosterBoard 只有软上限，**不会**因单进程上限被杀（超了只收内存警告、在内存紧张时排在高水位候选里）；
  所以“小组件黑”不是被 jetsam 杀，D2 没有它的退出记录与此一致。补丁后它们的软上限到 512 MB，若早上小组件仍黑且日志里没有
  `jetsam: WidgetRenderer_Default ... over ...`，就可以把 jetsam 从小组件问题里排除。
- 真正会被杀的是硬上限小的：iconservicesagent（已发生）、海报扩展（壁纸修好、开始用 Metal 渲染后非活跃 15 MB）、
  小组件扩展（30 MB）、其余扩展（6 MB）。

## 2. GPU 内存怎么记账（XNU 10063.141.1 源码，构建树 `.cache/xnu-work`）

- BO 的 CPU 视图：`Adreno830/NativeAdrenoPublish.cpp` `AGXDeviceUserClient::clientMemoryForType` 用
  `IOMemoryDescriptor::withAddressRanges(..., kIOMemoryTypePhysical64, nullptr)`。
- 物理描述符映射：`iokit/Kernel/IOMemoryDescriptor.cpp` `memoryReferenceCreate` 的“_task == 0, physical”分支建
  `device_pager_setup` + `mach_memory_object_memory_entry_64(host, false /*internal*/, ...)`；`osfmk/vm/vm_object.c`
  `vm_object_memory_object_associate` 把对象设为 `internal = FALSE`。
- `osfmk/vm/vm_map.c`（约 3225 行）：`vmkf_iokit_acct` 只对 `object->internal` 的条目记 `iokit_mapped` 与 `phys_footprint`；
  `osfmk/vm/vm_fault.c`（3437 行）只对 internal 对象传 `PMAP_OPTIONS_INTERNAL`；`osfmk/arm/pmap/pmap.c`（6386–6443 行）
  非 internal 的托管页记 `external`，不进 `phys_footprint`。→ **我们的 BO 映射不算 footprint**，插件 MEM 行的 `m<mapped>`
  不在 `f<footprint>` 里（`MemoryReport.h` 的注释应更正，那个文件不归本键）。
- 导入（`NativeAdrenoMemory::importTask`，用户地址）是客户端自己的内存，本来就算它的。IOSurface 当纹理时插件调
  `IOSurfaceGetBaseAddress` 把整块映射进本进程（`NativeBridgeMetalFrontend.mm` `newTextureWithDescriptor:iosurface:plane:`）；
  这部分算不算导入方，要看 IOSurface.kext 是否给内存打了所有者账本标签（打了就记在创建者身上，映射方不记）——**未证实**。
  底线方案对两种情况都成立。

## 3. 为什么不用数据叠加

技术上可行：内核在 `kern_exec.c` `native_owner_data_prepare` 里先挂 Data、再做 `native_gpu_addon_prepare`（已经把
`/private/var/ngpu/daemons` 只读联合挂到 `/System/Library/LaunchDaemons`，adrenoprobed 就靠它），然后才启动 launchd；launchd 与
RunningBoard 都按路径读这个文件。没选它的原因：要放一份改过的 Apple 文件（111 KB，只能本地）；文件名跟着 `hw.targettype` 走
（身份已经换过好几次）；只能按名字逐个列，漏掉的扩展照样 6 MB；**文件一旦写坏 launchd 直接中止 → 内核 panic → 开机循环**。
launchd 还有 `load-jetsam-properties`（按挂载点合并 `Library/JetsamProperties/com.apple.jetsamproperties.DDI.plist`），但要有特权的
XPC 客户端，没有现成的。

## 4. 补丁 `phone-gpu-client-memlimits.patch`

- 只加不删，两个文件（都是已打完整个 series 之后的状态，`git apply` 干净，sha256 `763c39b2…`）：
  - `bsd/kern/kern_memorystatus.c`（+158 行）：标志位 `P_MEMSTAT_NATIVE_GPU_CLIENT 0x00400000`（XNU 未用，测试核对）；
    `native_gpu_memlimit_floor_mb()`（启动参数 `pagani-jetsam`，只读一次）；`native_gpu_memlimit_floor_locked()` 放在两处
    “外来上限写入 proc 之前”：`memorystatus_update` 的 posix_spawn 分支、`memorystatus_set_memlimit_properties_internal`
    （覆盖 RunningBoard 的 SET、高水位、任务上限、DEVELOPMENT 的 increase）；`native_memorystatus_gpu_client()`：标记并立即抬高
    已有上限、重写缓存与账本（照 `memorystatus_raise_memlimit` 的做法）；`memorystatus_log_exception` 里加一行 U 记录。
  - `iokit/Kernel/IOUserClient.cpp`（+10 行）：`is_io_service_open_extended` 成功路径最后（失败分支 `break` 之后），服务带
    `MetalPluginName`（A830 加速器，`Adreno830Start.cpp` kMetalPrepare 设置）时调用 `native_memorystatus_gpu_client()`（弱引用）。
    插件的进度标记是被拒的 open，不会触发；Turnip 生产路径打开的正是这个服务（`tu_knl_darwin.cc` 默认 `IOAcceleratorES`，类型 0x4147）。
- U 行（BootProgress 弱引用，没有也能工作）：
  - `jetsam: gpu client <名> pid <n> active <旧>-><新> soft|hard inactive <旧>-><新> soft|hard floor 512`（前 48 条；账本写失败时
    结尾是 `ledger error <kr>`，总会写）
  - `jetsam: floor <名> pid <n> active a->a' inactive i->i' MB`（之后的设置被抬高时，前 32 条）
  - `jetsam: <名> pid <n> over active|inactive soft|hard limit <L> MB, footprint <F> MB[, gpu client]`（任何进程首次越过某个上限，
    每进程每上限一次，前 64 条）
- 锁：标记函数在 IOKit 打开路径的末尾、未持任何锁时取 `proc_list_lock`；与现有代码同序调 `task_set_phys_footprint_limit_internal`。

## 5. 测试（主机）

- `python3 -I build-userspace/a830-inputs/sysfix/jetsam/test-gpu-client-memlimits-patch.py --compile`：10 项全过。
  - 形状：只动两个文件、零删除行、底线调用的位置、IOKit 调用在成功路径、标志位未被 XNU 占用。
  - 行为：补丁加进 `kern_memorystatus.c` 的代码原样抽出，配上构建树里真实的 `SET_/CACHE_` 宏与桩，ASan/UBSan 下跑 12 组用例
    （启动参数 11 种取值、-1/0 不动且幂等、iconservicesagent 20 软/6 硬且干净 → 512/512 且账本按非活跃致命写、活跃进程、
    无上限进程、launchd/退出中/已退出/空 proc 拒绝、账本失败、行数上限、RunningBoard 读回再设 10 轮不涨、超限行格式、长名字、
    无 BootProgress）；变异检查（默认改 256、`>0` 改 `>=0`）都会失败。
  - 构建树：两文件副本上 `git apply --check` 与实际应用、重复应用被拒；`--compile` 用构建自己的编译记录（`compile_commands.json`）
    编补丁前后两版，**0 错误、0 新诊断**（kern_memorystatus.c 补丁前后都是 114 条既有诊断，IOUserClient.cpp 都是 53 条）。
- 补丁可由 `tools/apply-edits.py` 从构建树副本逐字节重新生成（已核对相同）。

## 6. 集成（主会话做；本键不改共享文件）

1. `cp build-userspace/a830-inputs/sysfix/jetsam/phone-gpu-client-memlimits.patch UniXNU-Pagani_SM8750-gpu/patches/xnu/`
2. `git -C UniXNU-Pagani_SM8750-gpu apply build-userspace/a830-inputs/sysfix/jetsam/series.diff`（series 末尾加一行
   `phone-gpu-client-memlimits.patch`，排在 `phone-system-apps-view.patch` 之后）。
3. 照常重编内核（patch 列表变了，`patches.py` 会重建 XNU 工作树）。不需要新启动参数、不改种子、不改用户态。
   需要回到旧行为时加 `pagani-jetsam=0`（14 字节）。

## 7. 早上上机看什么

- 开机后 U 行应出现 `jetsam: gpu client iconservicesagent ... inactive 6->512 hard floor 512`，以及 WidgetRenderer_Default、
  PosterBoard、海报扩展各自的行。
- 打开“设置”翻几页：不应再有 `S exit pid=… iconservicesagen status=9 ns=1 code=7`。
- 有 `jetsam: <名> ... over ... limit` 的看是谁、多少 MB：GPU 客户端超过 512 说明底线不够（改 `pagani-jetsam=768` 之类）；
  非 GPU 客户端的超限是原有行为。
- 开 `pagani-gpu-status=3` 时客户端报告行的 `r<left>` 应约等于 512 − `f<footprint>`。

## 8. 风险

- 512 MB 是估计值：没有这些进程在我们栈上的实测 footprint；靠超限 U 行和启动参数调。
- GPU 客户端泄漏时会长到 512 MB 才被单进程上限杀；全局 jetsam（按优先级带）仍在。最坏几个客户端同时很大会造成内存紧张。
- 用户态（RunningBoard、`launchctl print`）看到的仍是 Apple 的数，内核实际是底线后的数；RunningBoard 会把读回的数当新值再设，
  底线幂等不会越涨越高（测试已覆盖）。
- `0x00400000` 位将来若被 XNU 占用会冲突（换 XNU 版本时测试会报）。
- 没上机：IOKit 打开路径多了一次取 `proc_list_lock`，代码与锁序按现有模式写、已编译验证，未经真机。
- 说明：08:49 在构建树 `.cache/xnu-work` 上跑过一次 `git status`，git 刷新了 `.git/index` 的 stat 缓存（内容未变、补丁签名不受影响）；
  之后只读源文件、编译输出都写到临时目录，没有其他写入 `UniXNU/out-gpu`。
