# F4：s7b-final 的守护进程禁用清单

依据 `jitter.md` 第 4.3 节第 1 档，共 52 项。运行数据来自 `工具/gpu-test-loop/runs/20261101-110201-stage15-smoke-1`。
证据等级：【实测】跑了命令或读了日志，【读码】反汇编或反编译，【推断】由前两者推出。**s7b-final 没有上过真机。**

## 0. 结论

- 52 项逐项核对后，**禁用 33 项，保留 19 项**。种子的禁用表从 10 项变成 43 项。
- 保留的 19 项碰到了你给的护栏：Wi‑Fi、声音、小组件、键盘，或者在开机路径上。“有疑问的不禁”我是从严执行的，所以比 jitter.md 少了三分之一。
- 这是和 s7-final 对比用的**变体**，不是它的下一版。s7-final 验证打字，s7b-final 验证启动减负。

出镜像时这样指定：

```
A830_PREBUILT_SEED=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/final/s7b-final.dbrdisk
A830_OWNER_TRUST=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/final/s7b-final-owner-trust.bundle
```

信任包与 s7-final、s6-final 字节相同：改的只是数据，没有新代码。

重新生成：`工具/make-a830-final-seed.sh s7`，它只重做 s7-final 和 s7b-final，不动 s6-final。清单写在脚本的 `F4_DISABLE` 里。

## 1. 怎么核对的

1. 52 个标签全部在 OS 卷的 `System/Library/LaunchDaemons` 里，`--disable` 的标签检查也通过了；本轮每个都实际启动过一次，没有崩溃重启【实测】。
2. 从 plist 取出 183 个 Mach 服务名，反查 OS 卷 2775 个可执行文件、共享缓存，以及 `staged_system_apps` 里 29 个预装应用的 159 个 Mach-O（含 37 个小组件和意图扩展），并区分字符串在代码里还是只在 entitlement 里【实测】。
3. 用这份结果对四条护栏和开机路径上的进程做交叉：wifid、configd、mDNSResponder、nehelper、symptomsd、apsd；audiomxd、mediaplaybackd、mediaremoted、heard；chronod、PosterBoard；kbd、InputUI、`TextInputCore`；SpringBoard、backboardd、dasd、lockdownd、keybagd、mobileactivationd、datamigrator【实测】。
4. 护栏上的任务逐个核对，都不在 s7b-final 的禁用表里：`com.apple.wifid`、`com.apple.wifip2pd`、`com.apple.configd`、`com.apple.mDNSResponder.reloaded`、`com.apple.mDNSResponderHelper.reloaded`、`com.apple.symptomsd`、`com.apple.nehelper`、`com.apple.nesessionmanager`、`com.apple.apsd`、`com.apple.WirelessRadioManager`、`com.apple.nsurlsessiond`、`com.apple.mediaserverd`、`com.apple.audiomxd`、`com.apple.mediaplaybackd`、`com.apple.mediaremoted`、`com.apple.accessibility.heard`、`com.apple.chronod`、`com.apple.TextInput.kbd`、`com.apple.SpringBoard`、`com.apple.backboardd`、`com.apple.dasd`、`com.apple.biomed`【实测】。这个系统卷没有叫 `networkd` 的任务，本轮也没有这个进程【实测】。
5. launchd 配置之间没有 `OtherJobEnabled` 这类依赖。这些任务的 `KeepAlive` 只有 `Crashed` 和 `PathState` 两种，任务被禁用后都不起作用【实测】。
6. 风险最大的两处读了代码：CoreAnalytics 客户端，datamigrator 对 `OTACrashCopier` 的等待【读码】。

三点口径：

- 小组件：chronod、PosterBoard 不引用这 33 项。全部 42 个小组件和 Today 类扩展里，34 个完全不引用，本轮样本里出现的日历、天气、世界时钟、股票、音乐都在其中。有引用的 8 个全部只在 entitlement 里：屏幕使用时间 2 个（`UsageTrackingAgent`）、钱包（`financed`）、App Store（`askpermissiond`、`adprivacyd`）、家庭 3 个（`amsengagementd`、`betaenrollmentd`）、新闻的 `NewsTodayIntents`（`promotedcontentd`）【实测】。前三类添加到桌面后可能为空【推断】。
- `nehelper` 和 `CommCenter` 里命中的服务名不算依赖。那是身份表，一行名字、一行路径、一行 bundle ID，例如 `parsecd`、`/System/Library/PrivateFrameworks/CoreParsec.framework/parsecd`、`com.apple.parsecd`【实测】。
- 引用只说明“可能调用”，不说明“没有它就不行”。禁用后服务没有注册，客户端连接时立刻失败、不会排队，这是 jitter.md 的结论，也和现有 10 项禁用任务的运行情况一致，但对这 33 项是【推断】。真正的风险是客户端不处理失败而崩溃，这要上机才知道。

## 2. 禁用的 33 项

“开机就起”指 plist 里有 `RunAtLoad`，这 7 项不靠别人唤起。

### 分析与上报（8 项）

| 标签 | 本轮启动（秒） | 开机就起 | 作用 | 核对结果 | 用户损失 |
| --- | --- | --- | --- | --- | --- |
| `com.apple.analyticsd` | 23.0 |  | 诊断与用量汇总 | 客户端是所有链接 CoreAnalytics 的进程，kbd 也在内。读码确认客户端按“守护进程不在”设计：有 `XPC connection invalidated (daemon unloaded/disabled)` 分支，建连只在 `Client::initConnection` 一处，消息重试上限 5 次，没有每事件重连【读码】 | 无 |
| `com.apple.aggregated` | 15.2，27.3 退出 | 是 | 旧式计数汇总 | 没有 Mach 服务，自己退出 | 无 |
| `com.apple.aggregated.addaily` | 27.5，42.7 退出 |  | 日汇总任务 | 客户端只有同批禁用的 analyticsd，自己退出 | 无 |
| `com.apple.wirelessinsightsd` | 28.0 |  | 蜂窝侧统计 | 客户端是 `WirelessInsights`、诊断扩展、设置。wifid 不引用，CommCenter 本来就禁用 | 无 |
| `com.apple.OTATaskingAgent` | 24.3 |  | 诊断任务下发 | 客户端 `CrashReporterSupport`、`osanalyticshelper`。写崩溃日志不依赖它【推断】 | 无 |
| `com.apple.OTACrashCopier` | 15.2 | 是 | 崩溃日志转存 | `com.apple.datamigrator` 引用它的 `com.apple.OSASubmission.client`，这是最坏风险，已读码排除：`DMRebootAnalyticsUploader` 只在 `migrationRebootCount == 1` 时创建，等待上限 60 秒【读码】 | 无；日志留在原目录 |
| `com.apple.betaenrollmentd` | 28.0 |  | 测试版注册 | 客户端只有 `Seeding` | 无 |
| `com.apple.UsageTrackingAgent` | 59.3 |  | 屏幕使用时间统计 | 客户端 `ScreenTimeAgent`、`dmd`、`DeviceActivityReportService`、`MobileSafari`。chronod 不引用 | 屏幕使用时间没有数据，它的小组件可能为空 |

### 更新、广告、商业（6 项）

| 标签 | 本轮启动（秒） | 开机就起 | 作用 | 核对结果 | 用户损失 |
| --- | --- | --- | --- | --- | --- |
| `com.apple.softwareupdateservicesd` | 27.8 |  | 软件更新服务 | 客户端都在更新流程里；`triald`、`dmd`、`storagedatad` 的引用失败即返回【推断】 | 设置的软件更新页不可用，要看是报错还是转圈 |
| `com.apple.mobile.softwareupdated` | 15.2 | 是 | 软件更新执行 | 客户端 `MobileSoftwareUpdate`、`ASPCarryLog` | 同上 |
| `com.apple.ap.adprivacyd` | 28.0 |  | 广告隐私 | 客户端 App Store、设置、`AdCore` | 设置的 Apple 广告页可能为空 |
| `com.apple.ap.promotedcontentd` | 50.2 |  | 推广内容 | 客户端 App Store、`AdPlatforms`、同批的 adprivacyd；`Weather.app`、`Stocks.app`、`News.app` 主程序有 entitlement，它们的小组件没有 | 无 |
| `com.apple.attributionkitd` | 28.0 |  | 广告归因 | 客户端 `appstored`、App Store | 无 |
| `com.apple.amsengagementd` | 44.4 |  | 媒体服务互动消息 | 三个服务名写在 `AppleMediaServices` 里，音乐、App Store 都用；离线本来无内容，失败即返回【推断】 | 要看 App Store、音乐打开有没有卡 |

### 没有无线、账户或硬件就无用（19 项）

| 标签 | 本轮启动（秒） | 开机就起 | 作用 | 核对结果 | 用户损失 |
| --- | --- | --- | --- | --- | --- |
| `com.apple.icloud.fmflocatord` | 25.0 |  | 朋友定位上报 | locationd 已禁，无处取位置。客户端只有 `accountsd` 和身份表，查找 App 不直接用 | 无 |
| `com.apple.mediastream.mstreamd` | 65.3，105.4 退出 |  | 照片流 | 客户端只有 `MediaStream`，自己退出 | 无 |
| `com.apple.videosubscriptionsd` | 52.6 |  | 电视服务商账户 | 客户端 `VideoSubscriberAccount`、设置、`watchlistd` | 设置的电视服务商页不可用 |
| `com.apple.sociallayerd` | 48.5 |  | 与您共享 | `MobileSafari`、`MobileSMS`、`imagent` 声明过；离线无内容 | 无 |
| `com.apple.softposreaderd` | 48.5 |  | iPhone 收款 | 没有 NFC 能力 | 无 |
| `com.apple.coreidvd` | 49.0 |  | 身份核验 | 客户端在钱包和账户流程里 | 无 |
| `com.apple.financed` | 49.4 |  | FinanceKit | 客户端在钱包里；`Maps.app`、`MobileMail.app`、`suggestd`、`destinationd` 的引用失败即返回【推断】 | 钱包小组件可能为空 |
| `com.apple.announced` | 30.7 |  | 通知播报 | 要耳机和 Siri | 无 |
| `com.apple.dt.remotepairingdeviced` | 26.8 |  | Xcode 远程配对 | `lockdownd` 有代码引用，只在主机请求时用 | USB 通了、要用 Xcode 时放开 |
| `com.apple.cmfsyncagent` | 28.5 |  | 拦截名单同步 | `CommunicationsFilter` 查询失败时按未拦截处理【推断】；`Maps.app` 有 entitlement | 无 |
| `com.apple.facetimemessagestored` | 49.9 |  | FaceTime 留言 | 客户端 `callservicesd`、`InCallService` | 无 |
| `com.apple.studentd` | 22.7 | 是 | 课堂 | 客户端 `ClassroomKit`、`profiled`、设置 | 无 |
| `com.apple.familynotification` | 15.2 | 是 | 家庭共享通知 | 客户端只有 `FamilyNotification` | 无 |
| `com.apple.askpermissiond` | 15.2 | 是 | 询问购买 | 客户端在商店流程里 | App Store 小组件可能为空 |
| `com.apple.accessoryupdaterd` | 15.2 | 是 | 配件固件更新 | `auearlyboot` 引用它，但本轮 14.76 s 就退出了，早于它 15.24 s 启动，不存在依赖【实测】 | 无 |
| `com.apple.mobilerepaird` | 30.9 |  | 维修记录 | 客户端 `CoreRepairKit`、nfcd | 设置“关于”的维修信息可能为空 |
| `com.apple.eyereliefd` | 37.1 |  | 屏幕距离 | 要原深感摄像头 | 无 |
| `com.apple.voicebankingd` | 59.5 |  | 个人声音 | `SiriAUSP` 只在个人声音路径上，普通发声不走【推断】 | 设置的个人声音页不可用 |
| `com.apple.biomesyncd` | 36.4，47.0 退出 |  | Biome 跨设备同步 | `biomed` 保留；`dasd` 只有 entitlement，自己退出 | 无 |

## 3. 保留的 19 项

这些是 jitter.md 表里有、这次不禁的。不是说禁了一定出事，而是**有疑问**，而且这一轮不值得多一个变量。Wi‑Fi 通了、声音稳定之后，这里面的上报类可以作为第二批再评估。

| 标签 | 本轮启动（秒） | 护栏 | 为什么留 |
| --- | --- | --- | --- |
| `com.apple.wifianalyticsd` | 28.0 | Wi‑Fi | `wifid` 的 entitlement 里有它。下一步就要让 Wi‑Fi 上线，这时不改 wifid 的环境 |
| `com.apple.geoanalyticsd` | 75.0 | Wi‑Fi | `wifid`、`WirelessRadioManagerd` 的 entitlement 里有它。本轮 75.0 s 才启动，在第一帧之后，留着对启动也没有损失 |
| `com.apple.audioanalyticsd` | 41.5 | 声音、键盘 | `audiomxd`、`mediaremoted`、`heard`、`sirittsd`、`InputUI` 的 entitlement 里都有它 |
| `com.apple.rtcreportingd` | 27.0 | 声音 | `mediaplaybackd`、`CAReportingService` 的 entitlement 里有它，`audioanalyticsd` 也引用 |
| `com.apple.triald` | 24.3 | 键盘 | `TextInputCore` 有 `TITrialManager`、`DDSTrialProvider` 和“Starting trial manager for A/B testing”，`Spotlight.app` 有 `com.apple.trial.client`；`sirittsd`、`symptomsd` 也声明了 |
| `com.apple.parsecd` | 42.2 | 键盘路径 | Spotlight 建议走它，jitter.md 也标了要验证。Spotlight 是打字测试主路径，这一轮不让它多一个变量 |
| `com.apple.parsec-fbf` | 44.4 | 键盘路径 | `parsecd` 的反馈通道，随 parsecd 一起留 |
| `com.apple.tipsd` | 52.6 | 小组件 | Tips 小组件依赖它（jitter.md 标了“可能空”），`SpringBoard` 的 entitlement 里也有 |
| `com.apple.icloud.searchpartyd` | 45.5 | 小组件、查找 | `FindMy.app` 和它的小组件、`PeopleWidget_iOSExtension`、`SpringBoard` 都引用。“查找不崩”还是待测项，不和它混 |
| `com.apple.findmy.findmybeaconingd` | 60.8 | 查找 | `searchpartyd` 的搭档，只禁一半的行为不明，一起留 |
| `com.apple.nearbyd` | 50.1 | 查找 | `FindMy.app` 直接引用它的 `BTRanging`、`companion`、`diagnostics` 三个服务，处理方式与查找那一组一致 |
| `com.apple.icloud.fmfd` | 41.7 | 小组件、键盘路径 | `PeopleWidget_iOSExtension`、`SpringBoard` 引用，`Spotlight.app` 的 sandbox 例外里也有 |
| `com.apple.watchlistd` | 49.0 | 键盘路径 | `Spotlight.app` 的 sandbox 例外里有 `com.apple.watchlistd.xpc` |
| `com.apple.tvremoted` | 28.0 | 声音 | `mediaremoted` 的代码里有 `com.apple.tvremotecore.xpc`，用在 `ExternalDeviceServer` 路径上；音量和播放控制都经过 mediaremoted【推断】，没读完这条路径之前不动 |
| `com.apple.CallHistorySyncHelper` | 49.9 | 开机 | `CallHistoryDataMigrator` 引用它。迁移每次开机都跑，出问题的代价是开机 |
| `com.apple.siriknowledged` | 25.8 | 语音、开机 | jitter.md 的分类不对，它不是“没账户就没用”：提供 `com.apple.siri.uaf.service` 和 `com.apple.siriknowledged.koa.donate`，客户端有 `assetsd`、`healthd`、`medialibraryd`、`coreduetd`、`com.apple.siri.embeddedspeech` |
| `com.apple.nfcd` | 22.7 | 开机 | `com.apple.nfcd.hwmanager` 写在 `keybagd`、`mobileactivationd`、`lockdownd`、`SpringBoard`、`com.apple.datamigrator`、`com.apple.migrationpluginwrapper` 的 entitlement 里，都在开机路径上 |
| `com.apple.seld` | 22.7 | 开机 | 和 nfcd 一组：`com.apple.seld.xpc.events` 是 seserviced 的启动事件 |
| `com.apple.seserviced` | 27.5 | 开机 | 和 nfcd 一组：`SpringBoard` 的 entitlement 里有它 |

## 4. 对 jitter.md 的更正

1. **“占 229 个任务的 23%”的口径不对**。229 是 launchd 任务数，而进程里还有 139 个扩展和辅助进程。按本轮实际启动次数，52 项在第一帧前只占 47/338，即 14%【实测】。
2. **`siriknowledged` 分错了类**：它提供本地服务，不是“没有无线或账户就无用”（见第 3 节）。
3. **`nfcd` 的影响面被低估**：钥匙包、激活、迁移都声明了它的服务（见第 3 节）。没有 NFC 硬件，不等于没有 nfcd 就没事。

## 5. 预期收益

下面是**本轮实测的这 33 项在启动里的占比**，这是能确定的部分。不算 `xpcproxy` 这个中间步骤，全程启动进程 453 次。

| 时段（秒） | 全部进程启动 | 这 33 项 | 占比 |
| --- | --- | --- | --- |
| 14–33，第一波 | 189 | 20 | 11% |
| 33–48.5，低谷 | 26 | 4 | 15% |
| 48.5–60，第二波 | 141 | 8 | 6% |
| 第一帧（57.3）之前合计 | 338 | 30 | 9% |

- 33 项里 29 项启动后一直常驻到日志结束，另外 4 项自己退出（`aggregated`、`addaily`、`mstreamd`、`biomesyncd`）【实测】。
- 33 个程序文件合计 25 MB，这是代码页入量的上限，不是实际值【实测】。
- 后台进程的调度竞争会减少，但第二波主要是小组件、海报和索引，这 33 项只占 6%，**不要指望它解决第二波**。

下面是**估计，不是测量**，把握低：

| 项 | 估计 | 怎么来的 |
| --- | --- | --- |
| 常驻内存 | 省约 115–175 MB | 沿用 jitter.md 隐含的每进程 4–6 MB，乘 29 个常驻进程。日志里没有逐进程内存 |
| 第一帧 | 提前约 2–4 秒 | jitter.md 按 23% 估 5–10 秒，这里按实测占比 9% 同比缩小。第一帧前还有 9.3 秒和 15 秒两段原因不明的等待，它们可能比这些更重要 |

## 6. 上机怎么判断

和 s7-final 同一流程对比。

| 看什么 | 期望 |
| --- | --- |
| `pid=N <程序名> exec` | 这 33 个程序都不再出现 |
| 任何进程的 exec 次数 | 没有反复重启，尤其是保留下来的客户端 |
| `X crash` | 不比 s7-final 多。多出来的就是客户端不处理失败，这是本清单最主要的风险 |
| 第一帧时间、57–61 秒的 `rq` 峰值 | 下降 |
| 100 秒时的 `avail` | 比 s7-final 高，这能验证第 5 节的内存估计 |
| 设置：通用、软件更新、关于、屏幕使用时间、辅助功能 | 能打开，不卡死；页面空着可以接受 |
| App Store、音乐、Safari | 能打开，不比 s7-final 慢 |
| Spotlight 打字、备忘录打字 | 和 s7-final 一样快 |
| 查找、小组件、声音、Wi‑Fi | 和 s7-final 没有差别 |

## 7. 出问题时怎么办

s7b-final 比 s7-final 差时，先退回 s7-final，它不受这张清单影响。

要定位是哪一项，按 `F4_DISABLE` 的三组分批：在 s7-final 上用 `工具/edit-phone-seed-launchd.py --disable <一组标签> --trust s7-final-owner-trust.bundle s7-final.dbrdisk <输出>` 生成新种子。jitter.md 本来就建议一次一批，这里一次放 33 项是为了少跑几轮，代价是出问题时要多分一次。

## 8. 没有验证的

- **全部收益和全部风险都没有上机验证**，第 5 节的估计和第 2 节的【推断】都一样。
- 第 9 节之外的客户端只查到“引用”这一层，没有逐个读失败处理。
- “默认小组件”是按本轮样本里出现的进程判断的，没有读桌面布局。
- `mediaremoted` 的 `ExternalDeviceServer` 路径没有读完，所以 `tvremoted` 留着。

## 9. 对抗复核（读了调用点，表不变，33 项都留下）

上面第 1 节的方法只到“字符串引用”。复核按“这个进程真的链接了哪个客户端框架”重查：解析共享缓存全部 3354 个镜像和 OS 卷 1595 个 Mach-O 的依赖，和本轮实际运行过的 402 个进程求交集，再读调用点【读码】。挂起的主要风险是“无错误回调的 NSXPC 代理加无限等待”，所以把 59 个客户端框架里的等待点和代理写法全扫了一遍【读码】。

**第 2 节写错或漏掉的：**

| 项 | 第 2 节 | 实际 | 结论 |
| --- | --- | --- | --- |
| `biomesyncd` | `dasd` 只有 entitlement | `dasd` 直接链接 `BiomeSync`、`ContextSync`：`-[_DASRemoteDeviceActivityMonitor initWithDaemon:]` 启动时建 `ContextSyncClient`，`updateRecentlyUsedPhotosAppDevices` 活动里用 `BMSyncService` | 都是 `synchronousRemoteObjectProxyWithErrorHandler:`，服务不在时走错误回调返回，`registerForUpdates` 失败不致命。安全 |
| `mobilerepaird` | 客户端 `CoreRepairKit`、nfcd | 漏了 `wifid`：`WiFiManagerCreate` 里 `-[SystemHealthManager postComponentStatusEventFor:status:withReply:]`（`libSystemHealth.dylib`，同样链接它的还有 passd、nearbyd） | `remoteObjectProxyWithErrorHandler:`，异步，回调只写日志。安全 |
| `wirelessinsightsd` | wifid 不引用 | 成立，但漏了 `symptomsd`：`SymptomEvaluator` 用 `WISServerConnection` | 建连 `xpc_connection_create_mach_service`，没有重试循环。安全 |
| `softwareupdateservicesd` | 没提 SpringBoard | `SpringBoard`、`CoverSheet` 链接 `SoftwareUpdateServices(UI)` | SpringBoard 只在测试项里引用 `SUDescriptor`，没有 `SUManagerClient`；`CoverSheet` 不引用。`profiled` 只用 `SUUtility`。安全 |
| `mobile.softwareupdated` | 没提开机路径 | `nfcd`、`MSUDataMigrator`、`MSUEarlyBootTask` 链接 `MobileSoftwareUpdate` | `nfcd` 只弱引用一个解析函数，后两个不导入它的连接函数。安全 |

**逐个排除的无限等待（`DISPATCH_TIME_FOREVER`）：**

| 位置 | 为什么走不到或不卡 |
| --- | --- |
| `-[OSASubmissionClient submitWithOptions:]`（`OTACrashCopier` 的服务） | 错误回调不释放信号量，真走到会永远卡。但入口先看 `optInApple`（`MCFeatureDiagnosticsSubmissionAllowed`），默认假，种子没有配置文件。ReportCrash 用的是不等待的 `submitWithOptions:resultsCallback:`；osanalyticshelper 的调用在自己的紧急提交队列上，就算打开诊断提交也只卡这个队列 |
| `-[MSConnection …]`（`assetsd` 经 `PhotoLibraryServices`） | 它连的 `com.apple.mediastream.mstreamd` 不在 `mstreamd` 的 MachServices 里，禁用前后都连不上，行为不变。`com.apple.mediastream.sharing` 走 `MSASConnection`，发送有 `maxRetryCount` 上限 |
| `-[VSAccountStore firstAccount]`、`accounts`（`Preferences`、`appstored`、`watchlistd`） | 等的是框架内嵌 XPC 服务 `com.apple.VideoSubscriberAccount.DeveloperService`，不是 `videosubscriptionsd` |
| `-[MobileAccessoryUpdater …]`（`auearlyboot`） | `auearlyboot` 只导入日志函数；`UARPUpdaterService*` 是 accessoryupdaterd 自己拉起的 XPC 服务，禁用后不运行 |
| `-[FAFamilyNotifier _pendingNotificationsWithClientIdentifier:]` | 用 `remoteObjectProxyWithErrorHandler:` |
| `-[CRKSynchronousConfigurationSource …]`（ClassroomKit） | 框架内只有它自己的初始化器引用；`profiled` 只用数据类 `CRKClassroomConfiguration`，`sharingd` 只用 `CRKShareTarget*` |

**其余核对：**

- `datamigrator` 的 `migrationRebootCount` 只在 `-[DMRebootTimer _timerFired]` 里加一，种子里没有这个值，`DMRebootAnalyticsUploader` 不会创建。
- `CoreAnalytics`：回复是 `XPC_ERROR_CONNECTION_INVALID` 时走“不重试”分支并释放信号量，不会堆积；只有连接被打断才按延迟重试。
- `SocialLayer` 的同步代理失败只写日志；`TrialServer` 里的 `com.apple.softwareupdateservicesd` 是偏好域，不是服务调用。
- 全部 517 个任务的 `KeepAlive` 子键是 `Crashed`、`PathState`、`SuccessfulExit`、`AfterInitialDemand`，没有 `OtherJobEnabled`。保留任务里提到这些标签的只有 `com.apple.jetsamproperties.*`，那是 jetsam 配置。
- 退出记录里（`20261101-110201-stage15-smoke-1`）本轮样本的屏幕使用时间、钱包、App Store 小组件都没运行，第 1 节“添加到桌面后可能为空”的影响面成立。

仍然只是读码：这些结论说明“不会卡、不会循环”，不能代替第 6 节的上机对比。Swift 写的客户端（`FinanceKit`、`AdAttributionKit`、`FaceTimeMessageStore`、`TextToSpeechVoiceBankingSupport`）扫不出等待点，只按“链接它的是哪些进程”判断，都不在 SpringBoard、backboardd、kbd、wifid、chronod 的直接链接里。
