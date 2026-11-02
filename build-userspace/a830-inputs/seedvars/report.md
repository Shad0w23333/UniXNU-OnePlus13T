# seedvars 报告（2026-10-31）

范围：`功能盘点.md` 3.2 的 seedvars 工作流。只做了源码、主机测试与静态分析，没有碰手机，没有提交，没有改共享集成文件。

## 0. 结论

1. **工具已完成**：`工具/edit-phone-seed-launchd.py` 能在现成种子的副本上改禁用表与偏好，`--check` 只读列出。31 个用例全过（含真实种子 `s3-full` 1 个）；变异测试 7 个，6 个一开始就被抓到，第 7 个补了用例。
2. **现在没有一个守护进程可以直接并入日常包**。值得试的三个都要单变量上机：先 `PerfPowerServices`，再 `locationd`，`CommCenter` 只为对比设置页。
3. **`bluetoothd`、`identityservicesd`、`routined`、`watchdogd` 保持禁用**。`watchdogd` 放出来会让内核 panic，工具默认拒绝。
4. **偏好键都核实了**，但有三处和计划的设想不同：
   - 自动锁定时长不是写 SpringBoard 的键就生效，要靠数据迁移把旧键 `SBAutoLockTime` 转成 `maxInactivity`，种子要有迁移哨兵（`s3-full` 有）。
   - MobileAsset 在客户版系统上**没有**“关自动下载”的开关，唯一有效的是把服务器地址指到本机，副作用是手动下载也不通。
   - SOS 有总开关：`com.apple.SOS` 域的 `SOSDeviceSupportedKey`，一个键就关掉全部按键手势。
5. 音频不需要放出任何任务：`audio2/route-re.md` 第 22 行查过，这几个守护进程都不是音频路由的同步依赖。

证据标记：【静态】= 21H461 反汇编，地址是共享缓存或该二进制的虚拟地址；【日志】= 手机运行日志；【参考】= UniXNU-sheng 同版记录；【推断】= 没有直接证据。

## 1. 工具用法

```
edit-phone-seed-launchd.py [--enable 标签[,标签]] [--disable 标签[,标签]] [--auto-lock [秒]] [--keep-idle-sleep-block]
                           [--no-auto-updates] [--keep-mobile-asset] [--sos-off] [--language zh-Hans]
                           [--force] [--kernel 内核] [--trust 信任包] [--no-budget] [--scratch 目录] SEED.dbrdisk OUTPUT.dbrdisk
edit-phone-seed-launchd.py --check [--json] [--scratch 目录] SEED.dbrdisk
```

工具保证的事，都有用例覆盖：
- SEED 不变；OUTPUT 已存在时拒绝覆盖；失败时不留半成品。
- 编辑前后各算一次整卷摘要（路径、类型、权限、内容），差异必须正好是本次点名的文件，多一个就失败。macOS 自己写的 `.fseventsd` 等不计。
- 卷 UUID 编辑前后一致；打包后再只读挂载 OUTPUT 回读：UUID、禁用表、每个键、新文件属主必须是 uid 501（mobile）。
- `.json` 带上 `plugin_cdhash`、`plugin`；`--trust` 原样复制信任包（内容是数据，CDHash 不变），可直接接 `A830_PREBUILT_SEED` + `A830_OWNER_TRUST`。
- 载荷预算与壁纸工具同规则：FAT 192 MiB 的 95%，超了删掉 OUTPUT 并报错。

默认拒绝、要 `--force` 才放行的：`--enable com.apple.watchdogd`、`--disable com.apple.DumpPanic`（诊断守护进程靠它）、`--disable` 一个 OS 卷 LaunchDaemons 里没有的标签。`--enable` 一个没被禁用的标签一律报错。

`--enable` 的做法是删掉键（与上游 `add-gpu-plugin-data.py` 一致）。这 7 个任务的 plist 都没有 `Disabled=true`，删键即放行。

## 2. 偏好键核对（全部【静态】，文件都在 `/private/var/mobile/Library/Preferences/` 下）

### 2.1 `--auto-lock`

| 项 | 结论 | 证据 |
| --- | --- | --- |
| `SBDisableAutoDim`、`SBDontDimOrLockOnAC` | 域 `com.apple.springboard`，`SBIdleTimerDefaults` 绑定 | `-[SBIdleTimerDefaults _bindAndRegisterDefaults]` 0x1900a6cec |
| `SBDisableIdleSleep` | `SBPowerDefaults` 绑定，只控制 SpringBoard 是否持有“阻止空闲睡眠” | 0x190075284；`-[SBMainWorkspace _initializeAndObserveDefaults]` 0x1cfa822d0 → `SBWorkspaceSetPreventIdleSleepForReason` |
| 系统睡眠 | 去掉上一项也不会真睡：ADT `sleep-disabled=1`（`board.toml` 第 180 行）让内核拒绝 | XNU `IOPMrootDomain::checkSystemSleepAllowed` 的 `gSleepDisabledFlag` |
| 真正决定时长的键 | ManagedConfiguration 的 `maxInactivity`（`MCFeatureAutoLockTime`） | 块 0x1d00557c8 `effectiveValueForSetting:` |
| 没有 MC 值时 | iPhone 类 60 s、iPad 类 300 s；超过 3600 s 算永不 | `updateIdleTimerSettingsForAutoLockTimeout:` 0x1cfb1db0c，常量 0x1d022fb20=3600、0x1d022f810=300 |
| 下限 | 30 s（`rangeMinimum`），默认 60 | `ManagedConfiguration.framework/defaultSettings.plist` |
| 种子里怎么设 | 写旧键 `SBAutoLockTime`（整数，秒） | `-[MCMigrator _migrateSettings]` 0x100037dfc（profiled） |

为什么不直接写 MC 的文件：`UserSettings.plist` 在系统组容器里（`MCSystemProfileStorageDirectory`），容器 UUID 开机才生成，种子的 `containers/Shared/SystemGroup` 是空的。`SBAutoLockTime` 走苹果自己的迁移路径：`MCProfile.migrator` → `migrateWithContext:` → `migrateWithPostMigrationTaskQueue:` → `_migrateSettings`，读出后写 `maxInactivity` 并删掉旧键。profiled 以 mobile 运行，读得到这个文件。

没有迁移哨兵时工具给警告，自动锁屏仍然有效，只是时长是默认 60 s。

`--keep-idle-sleep-block` 是备用项，保留 `SBDisableIdleSleep`。去掉它以后，powerd 会第一次走“内核拒绝睡眠”这条路；现在 SpringBoard 一直持有阻止断言，这条路从没在手机上走过【推断】。

### 2.2 `--no-auto-updates`

| 域 | 键 | 含义 | 证据 |
| --- | --- | --- | --- |
| `com.apple.softwareupdateservicesd`（mobile，any host） | `SUDisableAutoDownload=true` | “下载 iOS 更新”关 | `-[SUPreferences disableAutoDownload]` 0x1fde0ddd8；域与用户见 `_copyPreferenceForKey:ofType:` 0x1fde0eb50 |
| 同上 | `SUAutomaticUpdateV2Enabled=false` | “安装 iOS 更新”关 | 0x1fde0e198；`enableAutomaticDownload:` 0x1fde0eef4 同时写这两个键 |
| 同上 | `SUAutoInstallSecurityResponse=false`、`SUAutoInstallSystemDataFiles=false` | 安全响应与系统数据文件 | `-[SUPreferences init]` 的键表 |
| `com.apple.MobileAsset`（mobile，any host） | `MobileAssetServerURL`、`PallasUrlOverrideV2` = `https://127.0.0.1:9/` | 新旧目录服务器都指到本机的拒绝连接端口 | `checkPreferencesForOverride:` 0x1cef36f88；`+[DownloadManager getPallasUrl:assetType:]` 0x1cef425b8 |

SU 这些键没有内部版门控：`_loadPreferences` 只判断键是否存在。

MobileAsset 必须说清楚：
- `-[MADAutoAssetControlManager _initializeCachedPreferences]` 0x1cefc2350 开头就判断 `_MAPreferencesIsInternalAllowed`（MG `LBJfwOEzExRxzlAnSuI7eg`），客户版直接跳过。所有 `AutoAsset*` 偏好，包括名字最像开关的 `AutoAssetScheduledBackupTriggersDisabled`，**在这里都不起作用**。
- 这两个服务器键不受门控，是客户版上唯一能挡住下载的偏好。
- 副作用：手动下载（Siri 语音、词典、翻译语言包）也不通，“检查更新”也会失败。只想关软件更新时加 `--keep-mobile-asset`。

读取顺序：`_MAPreferencesCopyValue` 0x1cef3710c 依次看 root 的偏好、托管偏好、mobile 的偏好；种子写的是最后一个。

### 2.3 `--sos-off`

`+[SOSManager deviceSupportsSOS]` 0x1b15d5a6c：默认值取 `MGGetBoolAnswer("5lAK1Xp+ezh1Qu+4jnHAOg")`，若 `com.apple.SOS` 域有 `SOSDeviceSupportedKey` 就用它覆盖。`-[SBCombinationHardwareButton _configureSOSGestureBehaviors]` 0x1cfbd2b24 在它为假时把侧键加音量长按、连按两个观察者都关掉。

| 文件 | 键 | 作用 |
| --- | --- | --- |
| `com.apple.SOS.plist` | `SOSDeviceSupportedKey=false` | 总闸，经 `NSUserDefaults` 套件读 |
| `ByHost/com.apple.SOS..plist` | `SOSCallWithVolumeLockHoldKey=false` | 长按自动呼叫；不设时 D2x 以后默认**开** |
| 同上 | `SOSCallWithSideButtonPressesKey=false` | 5 次连按自动呼叫 |
| 同上 | `SOSAutomaticCallCountdownEnabledKey=false` | 倒计时 |
| 同上 | `SOSPlayAudioDuringCountdownKey=false` | 倒计时响声 |

后四个是双保险，走 `+[SOSUtilities _sosPrefsValueForKey:]` 0x1b15d5bcc（`CFPreferencesCopyValue(键, "com.apple.SOS", "mobile", kCFPreferencesCurrentHost)`），落在 ByHost 目录。文件名中间确实是两个点：`_CFPrefsGetPathForTriplet` 0x187ed633c 用 `"%@.%@.plist"`，而 iOS 上 `_CFXPreferencesGetByHostIdentifierString` 0x18800b830 返回空字符串。

连按触发机制本身关不掉：`SOSTriggerMechanismKey=3` 在没有 Home 键、非印度机型上会被 `+[SOSUtilities currentSOSTriggerMechanism]` 0x1b15dcf58 改写回 2（5 次），只能靠总闸。

### 2.4 `--language zh-Hans`

`.GlobalPreferences.plist`：`AppleLanguages = ["zh-Hans-CN", "en"]`、`AppleLocale = "zh_CN"`，保留原有 `CAEnable*` 键。
- 每次开机的 `InternationalSupportMigrator` 不会改回去：`-[ISRootMigrator performMigration]` 0x1ba009768 抹除场景只写 schema 版本；前后版本相同时不满足追加区域变体的条件。
- 根卷有 `SpringBoard.app/zh_CN.lproj` 和 `Fonts/LanguageSupport/PingFang.ttc`。
- 本机用 `Bundle.preferredLocalizations` 核对过，`zh-Hans-CN` 能匹配 `zh_CN` 和 `zh-Hans` 两种目录名。
- 没设 `AppleKeyboards`，默认键盘由系统按语言选，会带拼音，`kbd` 内存可能上升【推断】。

## 3. 守护进程逐个分析

先说一个对所有任务都成立的机制。任务被禁用时，客户端查 Mach 服务立刻失败；放出后，只要服务存在，客户端的消息就排队等它。所以**一个“启动就退出”的按需任务比“被禁用”更糟**。手机上已有实证【日志】：`powerlogHelperd` 没有 KeepAlive、没有 RunAtLoad，只靠 Mach 按需拉起，却以 10–20 s 间隔反复启动并退出（`m8-acd` 274.7–354.9 s、`bisect-A` 394.8–414.9 s，`status=100` 即 `exit(1)`，`bisect-A` 里每次约 10 ms）。`fairplayd.A2` 也是这样，每 10 s 一次，曾造成设置页 10 s 停顿。

内存上限来自 `com.apple.jetsamproperties.J120.plist`（活跃软上限 / 非活跃硬上限，MiB）；这张表在手机上是生效的（`iconservicesagent` 被杀过）。

### 3.1 `com.apple.PerfPowerServices`：可以试，排第一
- 配置：`KeepAlive=true`，用户 mobile；内存 20/16。
- 启动路径【静态】：`main` 0x100003d68 → `EnterSandbox`（iOS 上只打一行日志，0x1b79b500c）→ `+[PowerlogCore setupCore]` 0x1b7961464 → 运行循环。
- 没发现硬件等待：`-[PLCoreStorage init]` 只查钥匙包状态，首次解锁前注册 `keybagd.first_unlock` 通知，解锁后主动退出一次重来，这是正常设计。`PowerlogLiteOperators` 的阻塞与断言点我只抽查了，基带、蓝牙相关的集中在按需路径。
- 数据库在 `systemgroup.com.apple.powerlog/Library/BatteryLife/`，也就是内存盘。
- 风险：①16 MiB 硬上限很紧，超限被杀后 `KeepAlive=true` 变成 10 s 循环；②**同框架的 `powerlogHelperd` 在手机上 `exit(1)` 循环，原因没查清**。10 ms 不像 `stopPowerlogHelperd` 的正常时序【推断】。若根因在 PowerlogCore 初始化，`PerfPowerServices` 也会中招，上机第一眼看这个。
- 价值：设置 > 电池的用量图，**要配合 `pagani-battery=1` 和 battery 工作流的字段才有东西可看**。

### 3.2 `com.apple.locationd`：可以试，排第二，不能和 `identityservicesd` 同时放
- 配置：`RunAtLoad`、`KeepAlive{SuccessfulExit:false}`、**`ThrottleInterval=3`**、root；内存 30/23。崩溃循环周期是 3 s。
- 等待点【静态】：导入表里没有 `IOServiceWaitForMatchingService`、`IOKitWaitQuiet`、`xpc_connection_send_message_with_reply_sync`。IOKit 匹配都是“查到就用”（`wlan`、`IOPMPowerSource`、`AppleBaseband` 等 17 处）。`dispatch_semaphore_wait`/`dispatch_group_wait`/`sleep`/`usleep` 共 20 处，大多在按需路径（健康、地图道路、跌倒记录），没看到开机路径上等传感器或 Wi‑Fi 的。
- 传感器：`CLSensorFusionService6AxisSPU` 等只有能力位不符才断言（0x10125a3b0），J120 的位是符合的。没有 `gps-capable` 就不会拉起 gpsd。
- 主要风险是**同步等待环**【参考】：sheng 在硬件上看到 `identityservicesd`、`contextstored`、`locationd` 互相同步 XPC 等待（`参考资料/UniXNU-sheng/package/BootWatchdogPolicy.c` 第 17 行注释），连带 SpringBoard、chronod、liveactivitiesd 卡住（`UniXNU-Pagani_SM8750-gpu/STAGE6.md` 第 593 行）。这也说明 locationd 在同版、同样没有 SPU 的板子上能跑起来，问题是卡住，不是一启动就崩。
  - 现在 `identityservicesd` 被禁用，环上少一条边，单独放 locationd 时对它的请求立刻失败【推断，边的方向没有静态确认】。
  - 反过来，locationd 一旦卡住，影响的是 SpringBoard：CoreLocation 有同步通道 `com.apple.locationd.synchronous`（`CLConnectionClient::sendMessageSync`）。
- 没有明确的崩溃证据，但它有 1033 处 `__assert_rtn`，静态排除不完。
- 价值：定位设置页；后续传感器、Wi‑Fi 定位的前提。现在放出后没有可见功能。

### 3.3 `com.apple.CommCenter`：能试，但只为对比设置页，不进日常
- 配置：同名两份，靠 `baseband` 节点互斥。`board.toml` 与 DTS 里没有这个节点，加载 noncellular 那份。`KeepAlive=true`，用户 `_wireless`；内存 36/20。
- 关键发现【静态】：**我们不是 Wi‑Fi iPad 那条路**。`TelephonyCapabilitiesGetHardwareModel` 的块 0x196132b04 读 `HasBaseband`（假）后比较 `DeviceName`：`iPad` 走 “Detected a WiFi iPad”；我们的身份是 iPhone，落到 **“Detected an unsupported non-baseband device”**，产品和射频都是 0。这条路苹果设备上不会出现。
- 后果：`capabilities::ct::supportsCellRadio()` 为假，方向对。CommCenter 0x10034ae90 记一条 fault（`device has no RadioModuleType`）后继续走，不是崩溃点。但它有 1379 处断言，“未知产品”这条路没人跑过，崩了就是 10 s 循环。
- 价值：几乎没有。状态栏不会有信号。“看设置页是否变快”目前没有静态依据（`ios.md` 6.21 也没找到设置页同步阻塞的证据）。

### 3.4 `com.apple.bluetoothd`：不放
- 【静态】`main` 0x100099d90 起依次查 `/dev/cu.bluetooth`、`/dev/uart.bluetooth`、名为 `marconi-bt` 和 `bluetooth` 的 IOService，都没有就走 0x10009a36c 的 `errx(0, "No bluetooth on this device.")`。
- 退出码 0，`KeepAlive{SuccessfulExit:false}` 不重启。但它有 14 个 Mach 服务，客户端很多，消息一排队就按需拉起，拉起就退出，和 `powerlogHelperd` 一样循环【推断，依据上面的日志模式】。客户端会从“立刻失败”变成“一直等”。
- 内核里 BTAudioHALPlugin 忙重试修复针对的正是无蓝牙后端的情况（`STAGE6.md`）。
- 价值：零，没有蓝牙驱动。

### 3.5 `com.apple.identityservicesd`：不放
- 配置：`RunAtLoad`、`KeepAlive{SuccessfulExit:false}`，用户 mobile；内存 60/60。
- iMessage、FaceTime、Find My 的身份服务，要网络、Apple ID、激活、钥匙串、FairPlay，现在都不可用，离线没有功能。
- 是同步等待环的一员【参考】，放出来会把 3.2 少掉的那条边补上。

### 3.6 `com.apple.routined`：不放（确定崩溃）
- 【日志】`20261030-213341-island-apps-1`：441.03 s SIGABRT（status=6）。
- 【静态】`libcoreroutine.dylib` 带必需软链接 `softlink:r:path:/System/Library/PrivateFrameworks/MomentsOnboardingAndSettings.framework/...`，这个框架在 iPad 根卷和共享缓存里都不存在。种子改不了。
- 没有 KeepAlive，但 RunAtLoad 加近 20 个通知事件（含锁屏状态），每个事件都会再崩一次。

### 3.7 `com.apple.watchdogd`：绝不放
- 【静态】`/usr/libexec/watchdogd` 0x100006768 查 `IOWatchdog` 服务，找不到时在非虚拟机上走 0x10000ab10 崩溃。内核带的 `AppleARMWatchdogTimer` 匹配 `wdt,s5l8960x`，13T 的 ADT 没有这个节点。
- plist 带 `_PanicOnCrash{PanicOnConsecutiveCrash:true}`，`KeepAlive` 隐含启动一次。
- 【日志】第 33 次真机：`watchdogd[144]` 退出后内核 panic（`STAGE6.md` 第 510–518 行）。
- 硬件看门狗已由 `pagani-wdog` 负责。

## 4. 推荐

| 任务 | 建议 | 风险 | 价值 |
| --- | --- | --- | --- |
| `PerfPowerServices` | 单变量试，排第一，配 `pagani-battery=1` | 中低 | 电池用量图 |
| `locationd` | 单变量试，排第二；`identityservicesd` 必须保持禁用 | 中 | 为传感器、Wi‑Fi 定位铺路，现在看不到功能 |
| `CommCenter` | 只在想对比设置页时试 | 中 | 几乎没有 |
| `bluetoothd`、`identityservicesd` | 保持禁用 | — | 现在没有 |
| `routined` | 保持禁用 | 确定崩溃 | — |
| `watchdogd` | 保持禁用 | **panic** | — |

偏好类建议：`--sos-off` 声音做通时并入日常（没有蜂窝，SOS 只会响警报然后拨不出去）；`--no-auto-updates` Wi‑Fi 联网前并入日常（Data 只有 2 GiB 内存盘）；`--auto-lock` 先单变量验证；`--language zh-Hans` 可选，先单变量看键盘与内存。

## 5. 验证结果

- 最终版：`SEED_TEST_FINAL=…/final/s3-full.dbrdisk python3 -B 工具/test-edit-phone-seed-launchd.py`，31 个用例全过。
- 变异测试 7 个：保留应删的键、改别的文件、ByHost 文件名写错、不检查是否已禁用、不截断、忽略预算，都被抓到；“回读不查值”一开始没被抓到，补了 `test_the_read_back_of_output_catches_a_wrong_seed`。
- 真实种子试跑（`s3-full.dbrdisk`，编码 sha256 `3baf1cc9…`，10:59 的版本）：全部选项一起开，`changed` 正好 8 项（禁用表、6 个 plist、新建的 `ByHost` 目录），UUID 仍是 `1726EAC0-2168-4118-AFD6-19E12FE306E7`，`disabled.plist` 仍是 `0:0 644`，新文件属主 uid 501，`s3-full` 运行前后 sha256 不变。
- 载荷余量：全部选项一起开约 6.8 MB（内核 127.5 MB、种子 56.8 MB，上限 182.4 MiB）；单变量约 6.8–6.9 MB。
- 变体脚本在草稿目录试跑了 `prefs`、`autolock`、`perfpower`，正常。

## 6. 其他

- 草稿目录里有一个不是本轮挂上的只读挂载 `seedvars-inspect/mnt`（`s1.raw`），看名字是上一轮 seedvars 留下的；我不能确认归属，没有动它。
- `final/s3-full` 在我工作期间被主会话重新合成过（10:59），变体要在最新种子上生成。