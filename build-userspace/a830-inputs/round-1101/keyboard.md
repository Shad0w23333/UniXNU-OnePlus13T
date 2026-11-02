# 打字卡顿：根因与修复（round-1101）

运行：`工具/gpu-test-loop/runs/20261101-110201-stage15-smoke-1`（下称本轮）；对照 `20261031-223924-stage1-full-1`（下称上轮）。
地址全是 21H461 共享缓存未滑动地址，可用 `ipsw dyld disass $DSC --cache $A2S --vaddr <地址>` 复现。证据等级：【实测】跑了命令或读了日志，【读码】反汇编/反编译，【推断】由前两者推出。

## 0. 结论

1. **确实有一个环，但闭合点不是写偏好**。环在 `-[AFDictationConnection forcedOfflineDictationIsAvailableForLanguage:synchronous:]`（`0x19fae6178`）：没有网络时它读 `com.apple.assistant.support` 的 `Offline Dictation Status`；这个键在这台机器上不存在，取到 nil，`_knownOfflineInstalledLanguages` 于是永远是 nil，**每一次调用都重新异步发一次 `_availabilityChanged`**。这个回调到主线程后发 `UIKeyboardDictationAvailabilityDidChangeNotification`，观察者调用 `dictationIsFunctional`，后者又走到这里。【读码】
2. **一个通知产生 M 个新通知，所以是指数增长，不是匀速空转**。主队列每轮排空时处理上一轮攒下的全部通知，下一轮数量乘 M。这就是上轮备忘录 3137→6281→12571 ms 首尾相接、精确翻倍的原因（M=2），也解释了本轮 Spotlight 约 3 倍的增长、单次回调内存涨几百 MB、最后被 jetsam 杀。【读码 + 实测拟合】
3. **键为什么不存在**：`assistantd` 只有在 `AFOfflineDictationCapable()` 为真时才写这个键；它查 MobileGestalt 的 `OfflineDictationCapability`，来源是 ADT `/product` 的 `offline-dictation`。我们合成的 `/product` 没有这个属性（也没有 `dictation`、`assistant`）。真 iPhone 有网时走不到这里；飞行模式下这个键由 assistantd 写好了，只发一次回调。【读码 + 实测】
4. **修复只改种子偏好，不动内核**：写 `com.apple.assistant.support.plist` 的 `Offline Dictation Status = {}`（必须）；同时写 `AppleKeyboards`、`AppleKeyboardsExpanded`、`DictationLanguagesEnabled`，去掉每圈的放大器（强烈建议，见第 4 节）。
5. **两处任务描述与事实不符**：种子语言是 `en`/`en_US`，不是 zh-Hans【实测】；`Dictation Enabled` 之类的开关关不掉这条路（第 6 节）。

## 1. 环的每一跳

| # | 位置 | 做什么 | 证据 |
|---|---|---|---|
| 1 | `-[UISystemKeyboardDockController updateDockItemsVisibility]` `0x18a2d45d4` | 只要 `isCurrentEditResponderInEditingMode` 为真，就无条件调用 `+dictationIsFunctional`（`0x18a2d4a5c`），不看 `shouldShowDictationKey` | 【读码】 |
| 2 | `+[UIDictationController dictationIsFunctional]` `0x18a2d689c` | 取 `currentInputModeForDictation` 的 `dictationLanguage`，最后调 `dictationConnection dictationIsAvailableForLanguage:`（`0x18a2d69c4`）。`dictationIsEnabled` 只在设备锁定时才看 | 【读码】 |
| 3 | `-[AFDictationConnection dictationIsAvailableForLanguage:synchronous:]` `0x19fa5f6c4` | `AFNetworkAvailability isAvailable` 为假就进 `forcedOffline...`（`0x19fa5f970`） | 【读码】 |
| 4 | `forcedOfflineDictationIsAvailableForLanguage:synchronous:` `0x19fae6178` | 见下方伪代码。这是闭合点 | 【读码】 |
| 5 | block `0x19fae9fdc` → `_availabilityChanged` → `_tellSpeechDelegateAvailabilityChanged` | 在 `_internalQueue` 上执行，再投递到 `_delegateQueue`；`initWithInstanceContext:` 把它设为主队列（`0x19fa5e55c`） | 【读码】 |
| 6 | `-[UIDictationConnection dictationConnnectionDidChangeAvailability:]` `0x18a39ab9c` → `-[UIDictationController ...]` `0x18a39abf4` → `postNotificationName:userInfo:` `0x18a39ac90` | 发 `UIKeyboardDictationAvailabilityDidChangeNotification` | 【读码】样本 140.802、173.903 帧里都有【实测】 |
| 7 | 观察者：`updateDockItemsVisibility`（`viewDidLoad` 注册，`0x18a0411a4`）、`refreshForDictationAvailablityDidChange`、`_keyboardDictationAvailabilityDidChangeNotification:`、搜索栏 `dictationAvailabilityDidChange:` | 回到第 1 跳 | 【读码】 |

```objc
// 0x19fae6178，由反汇编还原；_knownOfflineInstalledLanguages 在 +0x18
if (_knownOfflineInstalledLanguages == nil) {
    AFPreferences *p = [AFPreferences sharedPreferencesWithInstanceContext:_instanceContext]; // 每次新建实例
    _knownOfflineInstalledLanguages = [[p offlineDictationStatus] keysOfEntriesPassingTest:...]; // 状态为 nil 时结果也是 nil
    dispatch_async(_internalQueue, ^{ [self _availabilityChanged]; });                       // 每次都发
}
return AFOfflineDictationLanguageForKeyboardLanguage(lang, _knownOfflineInstalledLanguages) != nil;
```

- 这个 ivar 只在这一个函数里写，别处不会把它变成非 nil【读码，全文搜 `[x, #0x18]`】。
- `[nil keysOfEntriesPassingTest:]` 为 nil，`[@{} keysOfEntriesPassingTest:]` 是非 nil 的空集合，二进制 plist 读回的空字典同样如此【实测：本机 macOS 小程序，输出 `__NSSetI` 与 `0x0`；iOS 17 的行为是推断，但 assistantd 自己就会写 `@{}`，见第 3 节】。
- **运行时直接证据**：样本 162.292 的主线程停在 `-[AFPreferencesNotificationCenter addObserverForDarwinName:usingBlock:] + 160`，返回地址 `0x19fa5e110` 是“名字已存在，计数加一”那条分支【实测 + 读码】。这只会由新建的 `AFPreferences` 实例走到，单例不会。键盘已经出来 35 秒了还在新建实例，正是这个分支在反复执行。
- 回调不是网络抖动引起的：`AFNetworkAvailability _availabilityChanged`（`0x19fa60a28`）只在首次或可用位变化时通知观察者【读码】，而本轮 140.802 与 173.903 相隔 33 秒都在这个回调里，这段时间主线程几乎一直被排空占着，很难发生能重置状态的焦点切换【推断】。AssistantServices 里调 `_availabilityChanged` 的只有 `networkAvailability:isAvailable:` 和上面这个 block【读码，全文搜索】。
- `UIKit` 特性开关 `dictation_sls` 不在 `FeatureFlags/Domain/UIKit.plist` 里【实测】，所以 `setDictationSLSLanguagesEnabled:` 那条写 AF 偏好的路不走；环与写偏好无关。

## 2. 为什么是指数，为什么内存涨，为什么 kbd 没事

- 通知在主队列上，一次排空处理排空开始时已在队列里的块，新块进下一次排空【推断，libdispatch 的主队列排空语义；与下面的栈一致】。一次排空是一个 run loop 回调，共用一个自动释放池。
- 所以设本轮有 N 个通知、每个通知引发 M 次 `dictationIsAvailableForLanguage:`，下一轮就是 N×M 个，耗时也乘 M；两轮之间几乎没有空隙。
- 栈的位置吻合：140.802 与 173.903 的 `dictationConnnectionDidChangeAvailability:` 帧指针都是 `0x16f8208b0`，离 `-[UIApplication _run]` 帧（`0x16f821950`）只有 `0x10a0` 字节，说明它就是 run loop 主队列回调里的顶层调用，不是嵌在按键处理里【实测】。
- 自动释放池只在排空结束才弹出：样本 157.852 正是 `AutoreleasePoolPage::releaseUntil` ← `__CFRunLoopPerCalloutARPEnd`，同时 avail 从 1097 MB 跳到 1421 MB【实测】。第二个 Spotlight 同样在 194→195 s 从 1428 跳到 1593 MB【实测】。
- 按键卡住是排队造成的：kbd 回复早到了，主线程要等这一轮排空结束才处理，所以 `kin` 只有 0–1 ms、`in` 却是几秒到几十秒【实测，上轮】。本轮 `dn 51`、`up 2600` 是主线程卡住时积压的触摸一次涌入的结果【推断】。
- Spotlight 死因：`exit status=9 ns=1 code=2`，`ns=1` 是 `OS_REASON_JETSAM`，`code=2` 是 `JETSAM_REASON_MEMORY_HIGHWATER`【读码，`kern_memorystatus.h:292`】；上限是日志里的 `active 300->512 soft`【实测】；两次都杀在 avail≈942–949 MB【实测】。

**增长倍数拟合**【实测数据，推断模型】：

| 场景 | 观测 | 拟合 |
|---|---|---|
| 上轮备忘录 | `in` 3137、6281、12571 ms，比值 2.002、2.001 | M=2；每个通知约 12571/2⁷≈98 ms |
| 本轮 Spotlight[361] | 键盘 127.44 s；排空边界 137.48（`first dn`）与 157.85；最后一轮 20.4 s | 由 30.4 = 20.4×r/(r−1) 得 r≈3；反推上一边界 137.45，实测 137.479；每个通知约 20.4/3⁵≈84 ms |
| 本轮 Spotlight[417] | 键盘 182.35 s；边界约 194.5；216.4 s 被杀 | r=3 时下一轮要到约 218.7 s，先被杀了 |

下一轮 Spotlight[361] 要约 60 s，这就是“几十秒一个字”。M 的逐观察者分解我没能完全确定（第 12 节）。

## 3. 进入条件，以及真 iPhone 为什么不会

进入这个环要同时满足：
1. 文本正在编辑（第 1 跳的条件），或其他路径调到 `dictationIsAvailableForLanguage:`；
2. 没有网络：`AFNetworkAvailability isAvailable` 为假（看 `nwi` 里带 DNS 标志的接口）【读码】；本机无 Wi‑Fi、蜂窝【实测，功能现状】；
3. `Offline Dictation Status` 不是字典。

第 3 条的来源【读码，assistantd 用 IDA 反编译】：

```objc
// +[ADDictationOfflineStatusObserver sharedDictationOfflineStatusObserver] 的 once 块（0x100083AE4）
if (AFOfflineDictationCapable()) { 新建观察者; [obs fetchOfflineDictationStatusIgnoringCache:NO asynchronously:YES]; }
else { 只打一行日志 "Offline Dictation is not supported."; }
// fetch 成功时 _offlineDictationStatusStringToDictionary: 总返回字典（可为空），再写 prefs
// 资产查询特定失败时 setOfflineDictationStatus:@{}，日志 "Resetting offline dictation status prefs"
```

`AFOfflineDictationCapable()`（`0x19fa63fbc`）= `MGGetBoolAnswer("OfflineDictationCapability")` 或 `Enable Offline Dictation Override`【读码】。`inventory/ios-data/mg-all.tsv` 第 607 行：它读 `IODeviceTree:/product` 的 `offline-dictation`；第 726 行 `dictation` 读同一节点的 `dictation`。

本轮 payload 里 `/product` 的全部属性【实测，直接解析 `payload.img` 的 ADT】：`product-name`、`product-description`、`artwork-device-idiom`、`artwork-display-gamut`、`compatible-device-fallback`、`model`、`artwork-device-subtype`、`artwork-scale-factor`、`thin-bezel`、`product-id`、子节点 `audio`。没有 `offline-dictation`、`dictation`、`assistant`。真机 D47 的设备树名字表里三个都有（`inventory/ios-data/d47-names.txt` 第 278、732、1598 行）【实测】。

所以真 iPhone 上：有网时第 3 跳直接返回；飞行模式时状态是字典，`_knownOfflineInstalledLanguages` 第一次就变成非 nil，只发一次回调，环不闭合【读码 + 推断】。**这条路要“无网 + 无离线听写能力”同时成立，正常 iOS 17 设备碰不到，所以 Apple 没留保护。**

## 4. 放大器：决定每一圈有多贵

它们不构成环，但你提的第二个假设（缺 `AppleKeyboards`）在这里是对的。

**默认路径【读码 + 实测】**：`enabledInputModeIdentifiers:`（`0x18a2ace14`）先读 `AppleKeyboardsExpanded`，为假就走默认路径。样本 159.781 的返回地址 `+320`（`0x18a2acf54`）正是这条路，说明运行时这个键确实为假。默认路径完全没有缓存：
- `-[TIInputModeController defaultEnabledInputModesForCurrentLocale]`（`0x191739c38`）的缓存判断要比较 `self.preferredLanguages`，但 `setPreferredLanguages:` 在 TextInput、UIKitCore、AssistantServices 的全文反汇编里没有调用者，ivar 恒为 nil，**缓存永不命中**；
- 之后还要 `setEnabledInputModes:` 等一串赋值和 `updateUserSelectableInputModes`。

设了这两个键后，走 `0x18a2ad0e0` 的比较：`enabledInputModes` 与偏好数组相等就直接返回（`0x18a2ad5b4`）。要命中，偏好里的字符串必须是规范形式，`UIKeyboardInputModeGetIdentifierWithKeyboardLayouts` 对已带合法 `sw`、`hw` 的标识原样返回【读码】。

**`isDictationLanguageEnabled:`（`0x18a2ac914`）【读码】**：`DictationLanguagesEnabled` 里没有这个语言就调 `updateEnabledDictationLanguages:`。因为 MG `dictation` 为假，`AFDictationCapable` 为假，`supportsInputMode:` 为假，算出来的字典是空的，下一次仍然找不到这个键，**每次调用都重算**。重算里又有 `activeDictationSupportedInputModeIdentifiers`、`activeDictationLanguages` 两轮默认路径。样本 149.839 在键盘出现 22 秒后仍在 `updateEnabledDictationAndSLSLanguagesWithCompletionBlock:` 里，与此一致【实测】。

更贵的是：只要有编辑对象，算完就走 `setEnabledDictationLanguages:`，在应用里是 `TIPreferencesControllerClient forwardInvocation:`，每次发一条 NSXPC 消息给 kbd【读码】。任务里提到的 `NSXPCEncoder` 样本、`M lat fg` 每秒约 1 万次调度、`M cpu ipis` 每秒约 1.4 万次，很可能就是这个【推断，没有直接样本把调度次数对到这条消息】。

## 5. 任务里的假设，逐条核对

| 假设 | 结论 |
|---|---|
| 听写可用性通知→更新 dock→`dictationIsFunctional`→…→再次触发可用性变化，是反馈环 | **对**，而且是指数的 |
| 闭合处是“写偏好或发 XPC” | **错**。闭合处是 `forcedOffline...` 里无条件的异步回调。XPC 是放大器 |
| 种子缺 `AppleKeyboards`，走昂贵默认路径 | **对**，但它只是放大器，补上也不能断环 |
| 用户语言是 zh-Hans | **与种子不符**：s6-final 是 `AppleLanguages=[en]`、`AppleLocale=en_US`【实测】 |
| `skip_setup_tool` 在 `UniXNU-Pagani_SM8750-gpu/scripts/` | 路径不对，实际在 `UniXNU/scripts/seed-data-skip-setup.py`【实测】 |

## 6. 确切的开关

| 键 | 文件（用户 mobile） | 读者 | 类型 | 作用 |
|---|---|---|---|---|
| `Offline Dictation Status` | `/var/mobile/Library/Preferences/com.apple.assistant.support.plist`（域 `com.apple.assistant.support`） | 每个带键盘的客户端进程，AssistantServices 内 `CFPreferencesCopyAppValue` | 字典，`{}` 即可 | **断环** |
| `AppleKeyboards` | `.GlobalPreferences.plist` | TIPreferencesController（客户端与 kbd） | 字符串数组 | 与下一项一起走快路径 |
| `AppleKeyboardsExpanded` | 同上 | 同上 | 整数 1（`updateInputModes:` 写 1，模拟器实物也是 1；`boolValue` 判断） | 同上 |
| `DictationLanguagesEnabled` | `com.apple.keyboard.preferences.plist` | 同上 | 字典，语言→布尔 | 去掉每次重算和 XPC |

键到域的对应来自 `-[TIPreferencesController _configurePreferences]`（`0x19173d0ac`）；UIKit 的 `_UIDictationLanguagesEnabled` 字符串值确为 `DictationLanguagesEnabled`【读码】。

**关不掉这条路的开关**【读码】：
- `com.apple.assistant.support:Dictation Enabled`、`Assistant Enabled`：`dictationIsFunctional` 只在设备锁定时看前者，第 1 跳不看；默认本来就是假。
- 限制项 `AFDictationRestricted`、MG `dictation`：只影响 `dictationEnabled`，不影响 `dictationIsFunctional`。
- `UIKit` 的 `Dictation Allowed`：只在 `canPerformDictation`、搜索栏话筒那条路上。

**输入模式标识的写法**：
- `zh_Hans-Pinyin@sw=Pinyin10;hw=US` 在系统卷里**找不到依据**，不要用。解析规则是 `名字@键=值;键=值`，键只有 `sw`、`hw`（`TIInputModeGetComponentsFromIdentifier` `0x1917396f4`）【读码】。
- UIKitCore 里直接有字面量 `en_US@sw=QWERTY;hw=Automatic`（`0x18a45f4f0`）和 `emoji@sw=Emoji`（`0x18a406ed8`）【读码】。
- 中文：系统卷 `TextInput_zh.bundle/InputMode_zh.plist` 的 `zh_CN` 默认是 `zh_Hans-Pinyin@sw=Pinyin-Simplified`、`zh_Hans-Pinyin@sw=Pinyin10-Simplified`、`zh_Hans-HWR`、`en_US`【实测】。本机模拟器（iOS 26/27，只作格式佐证）实物：`['zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic','emoji@sw=Emoji','en_US@sw=QWERTY;hw=Automatic']`，`ApplePasscodeKeyboards` 不带 `hw`【实测】。
- 听写语言键：`UIDefaultDictationLanguages` 的 `zh_Hans` 是 `zh_CN`、`yue_CN`、`wuu_CN`；`en` 包没有这一项，所以 `en_US` 键盘用自身 `en_US`【实测 + 读码】。模拟器的 `DictationLanguagesEnabled` 也正好是这四个键【实测】。

## 7. 种子现状

只读方法：把 `final/s6-final.dbrdisk` 展开到临时目录，`hdiutil attach -readonly`，未碰原文件【实测】。这个种子就是本轮用的：`boot-A830-阶段15-首测.payload.json` 的 `data_ramdisk_sha256` 等于 `s6-final.json` 的 `encoded_sha256`（`39e635cb…`）。

`mobile/Library/Preferences` 全部 plist：

- `.GlobalPreferences.plist`：`AppleLanguages=[en]`、`AppleLocale=en_US`、三个 `CAEnable*=false`。**没有** `AppleKeyboards`、`AppleKeyboardsExpanded`、`ApplePasscodeKeyboards`。
- `com.apple.keyboard.preferences.plist`：`KeyboardInlineCompletion=false`、`KeyboardVisceral=true`。**没有** `DictationLanguagesEnabled`。
- `com.apple.keyboard.plist`、`com.apple.keyboard.ContinuousPath.plist`：之前的三个键。
- **没有** `com.apple.assistant.support.plist`、`com.apple.assistant.plist`、`com.apple.assistant.backedup.plist`。
- 属主 501，模式 0600，二进制 plist（`-owners on` 下核对）。

Setup Assistant 本该写的【读码】：`Setup.app` 里有 `BuddyKeyboardLanguageOrderSelector`、`saveInputModes:`、`saveDeviceUnlockPasscodeInputModes`。`-[UIKeyboardPreferencesController saveInputModes:]`（`0x18aace084`）= `updateInputModes:`（写 `AppleKeyboards`、`AppleKeyboardsExpanded=1`，必要时 `AppleKeyboardsIncludeIntl`）+ `updateEnabledDictationLanguages:YES`。`seed-data-skip-setup.py` 只写 `com.apple.purplebuddy*`、`com.apple.springboard`、`com.apple.migration`、`com.apple.managedconfiguration.notbackedup`，这些键都没有。

`Offline Dictation Status` 不属于 Setup，是 assistantd 写的（第 3 节）。

数据迁移不会破坏修复：种子带 `datamigrator/migrate`，`KeyboardMigrator` 只转换旧标识、缺 emoji 才补、缺 `ApplePasscodeKeyboards` 时由 `AppleKeyboards` 生成【读码】；`Siri.migrator` 的字符串里没有 `Offline Dictation Status`【实测】。

## 8. 语音相关服务状态

merged.log 的 S 记录【实测】：`assistantd` 22.684、`mobileassetd` 23.689、`assistant_cdmd` 25.831、`corespeechd` 25.835、`siriknowledged` 25.838、`sirittsd` 30.389、`siriinferenced`/`siriactionsd` 41.560、`languageassetd` 48.988、`localspeechrecognition` 49.799、`voiced` 55.660、`translationd` 113.833。全部只有 exec，没有 `exit`，也没有崩溃记录；日志里同 pid 的 `exit xpcproxy status=0` 是 xpcproxy 变身，不是退出。

结论：**可用性抖动与这些服务无关**。环完全在客户端进程内部（UIKit + AssistantServices），不经过它们。assistantd 活着，只是按设计不写这个键。

## 9. 备忘录翻倍核对（上轮）

【实测】kbt 汇总：

| 汇总时刻 | `in` 完成数 | `in` 最大 ms | `up` 最大 ms | kbd `kin` |
|---|---|---|---|---|
| 836.806 | 4 | 3137 | 3166 | 平均 0，最大 1 |
| 843.128 | 5 | 6281 | 6310 | 同上 |
| 855.729 | 6 | 12571 | 18886 | 同上 |

- 汇总时刻差 6322、12601 ms，与 6281、12571 只差约 40 ms，所以三段确实首尾相接。
- 这能由本机制解释：`in` 的结束要等主线程处理 kbd 回复，而回复只能排在当前主队列排空之后。下一段立刻开始。翻倍来自 M=2。kbd 一侧 0–1 ms，排除 kbd。
- 比值是 2.00 而不是随便一个数，因为 M 是整数。定时器退避解释不了 CPU 和缺页，本轮样本主线程几乎全程在 CPU 上（71 个样本里只有 1 个在缺页等待）。
- 模型预测下一段约 25.1 s，到约 880.8 s。汇总 888.788 的 `dn` 最大 52795、`ac` 最大 55918 ms 与之不矛盾，但不能算证实。856 s 后帧率恢复是系统界面在动，用户已离开备忘录【推断】。
- 离开后环为什么停：`+onWillResignActive` → `releaseConnection`，回调对象没了；`updateDockItemsVisibility` 也因不在编辑而提前返回【读码】。

**还差什么**：
1. 用 2 的幂往回推，环应从约 830.6 s 开始，而键盘 825.7 s 就有了，差约 4.8 s。可能早期 M 不是 2（比如编辑状态还没建立时只有一个观察者生效），我没有证据定论。
2. M=2 中第二个调用的来源只读到候选（第 12 节）。
上轮没有 Q 采样，无法用栈确认；本轮的 Spotlight 样本可以。

## 10. 影响面，以及它是不是 jitter 的来源

- **条件**：任何进程，只要进入带 dock 的文本编辑且无网。按代码，备忘录、设置搜索框、Safari 地址栏、信息都在范围内【推断】。实测看到的只有 Spotlight 两次、上轮备忘录一次。
- **本轮没触发的**：Freeform（283 s，`task 44`、`ac 4`）和 SpringBoard（326.9 s，`task 10`）之后 `flt` 回落，没有持续负载【实测】，大概是没有进入编辑。所以 SpringBoard 主线程被这条环卡住只是可能，本轮 218 个样本里 `sb` 栈没有一帧出现这些符号，含这些符号的 31 帧全在 `app Spotlight[361]`【实测】。
- **iPhone 身份才暴露**：`UISystemKeyboardDockController` 是 dock 专属；`stateForDictationKey:` 在 `enabledDictationLanguages` 为空时直接返回。身份换成带 dock 的 iPhone 后才有无条件的第 1 跳【推断】。
- **jitter**：只占一部分。
  - 是来源：带键盘界面的长卡；这段时间一个大核满载；avail 掉到约 940 MB 触发 jetsam，杀进程后重启又带来一波 IO。
  - 不是来源：本轮 99 次卡顿里前三名是它，第 4 名起的 `gap` 多数是 render server 与 SpringBoard 都在等待（`mach_msg2 port receive`），栈里没有这条环的符号【实测，stall-report】。全局 jitter 不能指望这个修复解决。

## 11. 修复方案

### 11.1 要写的内容（只改种子）

| 文件（`mobile/Library/Preferences/`） | 键 | 值 |
|---|---|---|
| `com.apple.assistant.support.plist`（新建，0600，属主同目录） | `Offline Dictation Status` | `{}`（字典） |
| `.GlobalPreferences.plist` | `Offline Dictation Status` | `{}`，兜底，见下 |
| `.GlobalPreferences.plist` | `AppleKeyboards` | `["en_US@sw=QWERTY;hw=Automatic", "emoji@sw=Emoji"]` |
| `.GlobalPreferences.plist` | `AppleKeyboardsExpanded` | 整数 `1` |
| `com.apple.keyboard.preferences.plist` | `DictationLanguagesEnabled` | `{en_US: false, zh_CN: false, yue_CN: false, wuu_CN: false}` |

- 必须项只有第一行。其余是同一次种子步骤、同样是数据，建议一起带上，符合“修完再测”。
- 第二行兜底：`CFPreferencesCopyAppValue` 的搜索链包含全局域【推断，文档语义】。Spotlight 有 `com.apple.private.security.no-container`，读域没问题【实测】；备忘录没有这项，也没有 shared-preference 例外【实测】。普通沙盒应用能否读 `com.apple.assistant.support` 我没能直接证实，只知道沙盒配置集合里有这个字面量【实测】，且 `Dictation Enabled` 同域、键盘要读【推断】。上真机一轮成本高，多一个键无害。
- 语言是 zh-Hans 时再加：`AppleLanguages=["zh-Hans-CN","en"]`、`AppleLocale="zh_CN"`（`--language zh-Hans` 已有），`AppleKeyboards=["zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic","emoji@sw=Emoji","en_US@sw=QWERTY;hw=Automatic"]`。建议**先按 en 验证修复，再单独切语言**：中文键盘在这套系统上还没测过，混在一起分不清。
- 不要写 `Installed=true`，否则听写会显得可用，按了也用不了。
- 不要给 ADT 加 `dictation`/`assistant`：没有必要，还会打开更多路径。

### 11.2 改哪个脚本（方案，本轮未改）

`工具/edit-phone-seed-launchd.py` 已有 `Edit(file, set, remove)`，能建新文件并自动处理 0600 和属主，不需要新脚本：

```python
TEXT_INPUT = {'en': ['en_US@sw=QWERTY;hw=Automatic', 'emoji@sw=Emoji'],
              'zh-Hans': ['zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', 'emoji@sw=Emoji', 'en_US@sw=QWERTY;hw=Automatic']}
DICTATION_OFF = dict(en_US=False, zh_CN=False, yue_CN=False, wuu_CN=False)

# recipe(...) 增加参数 text_input=None：
if text_input is not None:
    if text_input not in TEXT_INPUT:
        raise SeedError('--text-input takes ' + ', '.join(sorted(TEXT_INPUT)))
    edits.append(Edit('.GlobalPreferences.plist', {'AppleKeyboards': list(TEXT_INPUT[text_input]), 'AppleKeyboardsExpanded': 1,
                                                   'Offline Dictation Status': {}}, ()))
    edits.append(Edit('com.apple.keyboard.preferences.plist', {'DictationLanguagesEnabled': dict(DICTATION_OFF)}, ()))
    edits.append(Edit('com.apple.assistant.support.plist', {'Offline Dictation Status': {}}, ()))
```

同时：`main()` 加 `--text-input`，传给 `recipe`；`VIEWS` 加这三个文件，`--check` 才看得见；`test-edit-phone-seed-launchd.py` 加一条用例。

`工具/make-a830-final-seed.sh` 末尾加一步（上一步的 `--check` 之后）：

```bash
S7=$F/s7-final
rm -f "$S7.dbrdisk" "$S7.json" "$S7-owner-trust.bundle"
python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --text-input en --trust "$S6-owner-trust.bundle" "$S6.dbrdisk" "$S7.dbrdisk"
```

出镜像时 `A830_PREBUILT_SEED=$F/s7-final.dbrdisk A830_OWNER_TRUST=$F/s7-final-owner-trust.bundle`。偏好是数据，信任包不变。内核和 ADT 不用改。

### 11.3 主机验证

下面的脚本已在临时目录测过【实测】：对 `s6-final.dbrdisk` 报 4 项不符合、退出码 1；对按 11.1 生成的偏好目录报全部符合、退出码 0；对同一目录加 `--lang zh-Hans` 报语言不符。它只读，用 `python3 -I -B` 运行。

```python
#!/usr/bin/env python3
"""只读核对 Data 种子里键盘修复需要的偏好，不改种子，不碰手机。
  check-keyboard-seed.py SEED.dbrdisk [--lang en|zh-Hans]
  check-keyboard-seed.py --prefs-dir DIR [--lang en|zh-Hans]     DIR = mobile/Library/Preferences 的拷贝
退出码：0 全部符合，1 有缺项。"""
import argparse, plistlib, subprocess, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/DarwinBoot/Test')

KEYBOARDS = {'en': ['en_US@sw=QWERTY;hw=Automatic', 'emoji@sw=Emoji'],
             'zh-Hans': ['zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', 'emoji@sw=Emoji', 'en_US@sw=QWERTY;hw=Automatic']}
LANGUAGE = {'en': (['en'], 'en_US'), 'zh-Hans': (['zh-Hans-CN', 'en'], 'zh_CN')}
DICTATION = {'en_US', 'zh_CN', 'yue_CN', 'wuu_CN'}

def load(prefs, name):
    path = prefs / name
    return plistlib.loads(path.read_bytes()) if path.is_file() else None

def check(prefs, lang):
    bad = []
    support = load(prefs, 'com.apple.assistant.support.plist')
    status = None if support is None else support.get('Offline Dictation Status')
    if not isinstance(status, dict):
        bad.append(f'com.apple.assistant.support:Offline Dictation Status 应为字典，现在是 {status!r}（环的闭合条件）')
    elif any(isinstance(v, dict) and v.get('Installed') for v in status.values()):
        bad.append('Offline Dictation Status 里有 Installed=true：会让听写看起来可用')
    glob = load(prefs, '.GlobalPreferences.plist') or {}
    if glob.get('AppleKeyboards') != KEYBOARDS[lang]:
        bad.append(f'.GlobalPreferences:AppleKeyboards 应为 {KEYBOARDS[lang]}，现在是 {glob.get("AppleKeyboards")!r}')
    if int(glob.get('AppleKeyboardsExpanded') or 0) != 1:
        bad.append(f'.GlobalPreferences:AppleKeyboardsExpanded 应为 1，现在是 {glob.get("AppleKeyboardsExpanded")!r}')
    if (glob.get('AppleLanguages'), glob.get('AppleLocale')) != LANGUAGE[lang]:
        bad.append(f'语言不是 {lang}：{glob.get("AppleLanguages")!r} {glob.get("AppleLocale")!r}')
    keyboard = load(prefs, 'com.apple.keyboard.preferences.plist') or {}
    enabled = keyboard.get('DictationLanguagesEnabled')
    if not isinstance(enabled, dict) or not DICTATION <= set(enabled) or any(enabled.values()):
        bad.append(f'com.apple.keyboard.preferences:DictationLanguagesEnabled 应含 {sorted(DICTATION)} 且全为 false，现在是 {enabled!r}')
    return bad

def from_seed(seed, lang):
    import packed_ramdisk
    logical, extents = packed_ramdisk.regions(seed.read_bytes())
    with tempfile.TemporaryDirectory(prefix='kbseed-') as td:
        raw, mount = Path(td) / 'seed.raw', Path(td) / 'mnt'
        with raw.open('wb') as f:
            f.truncate(logical)
            for offset, data in extents:
                f.seek(offset)
                f.write(data)
        mount.mkdir()
        out = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-readonly', '-imagekey', 'diskimage-class=CRawDiskImage',
                                                      '-nobrowse', '-owners', 'off', '-mountpoint', str(mount), '-plist', str(raw)],
                                                     stderr=subprocess.DEVNULL))
        device = out['system-entities'][0]['dev-entry']
        try:
            return check(mount / 'mobile/Library/Preferences', lang)
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True, stdout=subprocess.DEVNULL)

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('seed', nargs='?', type=Path)
    p.add_argument('--prefs-dir', type=Path)
    p.add_argument('--lang', choices=sorted(KEYBOARDS), default='en')
    a = p.parse_args()
    bad = check(a.prefs_dir, a.lang) if a.prefs_dir else from_seed(a.seed, a.lang)
    for line in bad:
        print('缺：' + line)
    print('全部符合' if not bad else f'{len(bad)} 项不符合')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
```

### 11.4 真机判据

建议用脚本打字，结果才能横向比较：保留 `pagani-input-script=1`，240–262 s 不要碰屏幕（本轮 240.839 被真实触摸中止），或用 `pagani-input-script-at=<秒>` 改时间。

修好后应看到：

| 项 | 本轮 | 期望 |
|---|---|---|
| Spotlight 退出 | 两次 `status=9 ns=1 code=2` | 没有 |
| 键盘出现后、不动时的 `flt` | 约每秒 1 万，持续 | 几秒内回到每秒 1000 以下 |
| avail | 每秒掉 20–40 MB，并有 +165～+324 MB 的跳变 | 稳定，没有大跳变 |
| `M lat fg` 调度 | 每秒约 1 万 | 每秒几千以下 |
| `M cpu ipis` 合计 | 每秒约 1.4 万 | 明显下降 |
| `kbt lat dn/up/in` | 几秒到几十秒 | `in` 最大 < 100 ms，`dn`、`up` 正常按键 < 100 ms |
| `Q why=dn/up/in` 采样 | 73 次，栈在输入模式与听写 | 没有或极少 |
| 栈 | `enabledInputModeIdentifiers: + 320`、`updateEnabledDictationLanguages:`、`addObserverForDarwinName:` | 都不再出现 |
| `dictationConnnectionDidChangeAvailability:` | 反复出现 | 每次聚焦至多一两次 |
| 连续打字 | 成倍变慢 | 第 20 个字和第 1 个一样快 |

只修了环而没修放大器时，现象是不再指数恶化，但每个键仍要几十毫秒以上，这时看第 4 节。

### 11.5 偏好关不住时的备用方案

按顺序：

1. **某些应用还在循环**（Spotlight 好了、备忘录没好）：说明沙盒挡了读取，且全局域兜底也没生效。先核对这些应用的栈是否仍有 `addObserverForDarwinName:`。
2. **让系统自己写**：`com.apple.assistant.support` 加 `Enable Offline Dictation Override=true`，或 `board.toml` 的 `[adt.product.u32]` 加 `offline-dictation = 1`（要重出镜像）。两者都让 `AFOfflineDictationCapable()` 为真，assistantd 启动时去查资产并写字典。风险是 `SFEntitledAssetManager` 在这里的行为没测过，也可能带出其他按能力判断的路径。
3. **假网络**：让 `nwi` 出现带 DNS 的接口，`isAvailable` 为真就不进这条路。副作用大（听写显示可用，别的服务以为有网），等 Wi‑Fi 自然解决，不建议单独做。

注意：Wi‑Fi 一旦可用，这个环也会自己消失，但无网时（飞行模式、断网）又会出现，所以种子修复要保留。

## 12. 没有查清的

1. **M 的逐观察者分解**【不确定】。dock 确定贡献 1 次（`dictationIsFunctional` 的最后一跳；`isDictationLanguageEnabled:` 因语言未启用不调可用性）。第二个候选是 `UISystemInputAssistantViewController` 重建条目时 `unmodifiableSystemAssistantItem:` 的块（`dictationInputModeIsFunctional`）或 `canPerformDictation`；Spotlight 的第三个可能来自搜索栏。静态读不出运行时走了哪几条，但不影响修复：`Offline Dictation Status` 非 nil 后 M 变成 0。
2. **上轮环的起点偏差约 4.8 s**（第 9 节）。
3. **Spotlight 的 r≈3 只有两个边界**，是拟合，不是测量。本轮 137.5 s 的弹出只回收 52 MB，比 417 号同规模一轮的 165 MB 少，说明 avail 跳变受同时发生的其他分配影响，不能精确对应。
4. **`[@{} keysOfEntriesPassingTest:]` 在 iOS 17 上非 nil** 是在本机 macOS 测的，iOS 上是推断，依据是 assistantd 自己写 `@{}`。如不放心，把值换成 `{"en-US": {"Installed": false}}` 也能满足判断，结果同样是空集合。
5. **普通沙盒应用对该域的读取权限**未直接证实，已用全局域兜底。
6. **`nwi` 的实际内容**没看到，“无网所以 `isAvailable` 为假”是推断；即便为真也不会进这条路，那样环根本不存在，与实测矛盾。
7. **修好后的下一个瓶颈未知**。`kin`、`khit` 现在很快，但自动更正、语言模型在真正打字时的负载还没被观察过，之前都被这个环盖住了。`dn 51`、`up 2600` 的重复 `touchUp:` 机制本身还在，只是积压不会再发生。
