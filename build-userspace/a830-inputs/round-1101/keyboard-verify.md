# keyboard.md 对抗复核

结论：**核心结论成立**。闭合点、指数增长、“必须项”修复都经得起独立复核；但修复没有上机，仍只算读码成立。报告里有 3 处论证错误或无依据的数字，`DictationLanguagesEnabled` 这项修复有一个缺口（第 3 节）。

本次只新建了这个文件。种子是展开到临时目录后只读挂载的，已卸载并删除；手机没有碰。

## 1. 亲自复核后成立的

| 结论 | 结果 | 等级 |
|---|---|---|
| `forcedOffline...`（`0x19fae6178`）：`+0x18` 为 nil 时每次新建 `AFPreferences`，读状态，再向 `+0x48` 队列发 `_availabilityChanged` | 反汇编与报告伪代码一致；`synchronous` 参数没有用到 | 【读码】 |
| `+[AFPreferences sharedPreferencesWithInstanceContext:]` 每次 alloc | 确认；`sharedPreferences` 才是单例 | 【读码】 |
| `offlineDictationStatus` 只接受字典，否则返回 nil | 确认（`0x19fa5fff4`） | 【读码】 |
| 通知来源只有 `networkAvailability:isAvailable:` 块和 `forcedOffline` 块 | `_tellSpeechDelegateAvailabilityChanged` 只有 `_availabilityChanged` 一个调用者，投递到 `+0xd0`，即主队列 | 【读码】 |
| 网络回调只在首次或可用位变化时通知 | 确认（`0x19fa60a28`） | 【读码】 |
| 写偏好不是闭合点 | `-[TIPreferencesController setValue:forPreferenceKey:]` 新旧值相等就直接返回，`{}` 只写一次 | 【读码】 |
| 运行时确实走到这条路 | 样本 162.292 的返回地址 `0x19fa5e110` 是“计数加一”分支；样本 167.895 的 `_AFDictationLanguageForKeyboardLanguage + 88` 只有 `_AFOfflineDictationLanguageForKeyboardLanguage` 和 `_AFOverrideLanguageCode` 两个调用者 | 【实测 + 读码】 |
| `dictationIsFunctional`、`isDictationLanguageEnabled:`、`enabledInputModeIdentifiers:`、`stateForDictationKey:` 的描述 | 全部与反汇编一致，`+320` 确为默认路径 | 【读码】 |
| 两次 `status=9 ns=1 code=2`、avail 942/949 MB、157→158 s 与 194→195 s 跳变、`kin` 0–1 ms、3137/6281/12571、汇总时刻差、71 个 app 样本、73 次 `why=dn/up/in` | 全部对得上 | 【实测】 |
| 种子现状 | sha `39e635cb…` 与镜像一致；无三个 assistant plist；`en`/`en_US`；键盘相关键都缺 | 【实测】 |
| 检查脚本 | 对 s6 报 4 项、退出 1；对生成的偏好目录全部符合、退出 0；`--lang zh-Hans` 报不符 | 【实测】 |
| `Edit` 能新建 plist，`--trust` 产出 owner-trust，预算有检查 | 确认 | 【读码】 |

**报告未定的两项可以收窄：**

- 第 12 节第 4 项可以关闭：21H461 的 `-[NSDictionary keysOfEntriesWithOptions:passingTest:]` 返回 `[NSSet setWithSet:[NSMutableSet set]]`，空字典得到非 nil 空集合【读码】。`_AFOfflineDictationLanguageForKeyboardLanguage` 对集合只用 `containsObject:`，空集合与 nil 一样返回 nil，听写不会显得可用【读码】。
- 第 12 节第 6 项可以升级：上面两个样本说明运行时到了 `forcedOffline`，而这条路只在 `isAvailable` 为假时进入【实测 + 读码】。

**全局域兜底可行**：`_AFPreferencesValueForKeyWithContext` 用 `CFPreferencesCopyAppValue`，默认上下文 `AFInstanceContextHost` 的子域为 nil【读码】；本机用不存在的域读 `AppleLocale` 能取到全局值【实测，macOS】。

**第 4 节关于 XPC 的推断有了直接证据**：kbd 在 CPU 上的样本落在 `__NSXPCSerializationCreateObjectInDictionaryForKey_block_invoke`（173.903）和 `_xpc_payload_create_from_mach_msg`（179.914）【实测】。

## 2. 被反驳或要改的

1. **`r≈3` 的“反推上一边界 137.45，实测 137.479”是循环论证**。137.45 = 157.85 − 20.4，而 20.4 正是 157.85 − 137.48。一个方程解一个未知数，不算验证。
   但 `r≈3` 本身有更好的证据，报告没用上：上轮 `Spotlight[419]` 的 `dn` 最大值 2319 → 6909 ms，比值 2.98；由 939.691 回推起点 936.21，键盘在 936.03–936.08；下一轮要到约 967.4，而 963.25 被杀。帧率也在 947 s 恢复【实测】。`Spotlight[479]` 在键盘出现后 26.9 s 被杀，也落在模型的下一个边界（约 31 s）之前【实测 + 推断】。报告应改引这组数据。
2. **“每个通知约 98 ms / 84 ms”没有依据**。几何级数只定比值，不定世代数，`2⁷`、`3⁵` 是随手选的。kbd 工作线程在 W 状态的 `wt` 平均 0.11 ms（62 个样本），即每秒约 4500 次以上唤醒【实测】。按每个通知至少两条 XPC 估算，每个通知是毫秒量级，比报告低一到两个数量级【推断】。这个数不影响修复，但不能用来估算修好后的按键开销。
3. **第 7 节“属主 501，模式 0600”不准确**：`.GlobalPreferences.plist`、`backboardd.plist` 是 0644，部分文件组是 20【实测】。`apply_edit` 保持原模式，无实际影响。
4. **第 10 节“任何进程，只要进入带 dock 的文本编辑且无网”仍未证实**：
   - 备忘录回推的起点比键盘晚 4.8 s，且报告漏了一点：首键 `up` 在 827.94→829.64 已经卡了 1696 ms，这早于回推的 830.6 s，不属于翻倍序列【实测】。
   - Freeform 有 `task 44`、`ac 4` 却没有环，“没有进入编辑”只是猜测。
   - 上轮 `Spotlight[485]` 键盘出现后 36 s 内没有超限记录，可能只是用户很快离开，无法判定。

## 3. 修复缺口（报告未发现）

**`DictationLanguagesEnabled` 这一项不稳，切一次 emoji 键盘就会被清空**【读码 + 推断】：

1. 切换后 `finishSetKeyboardInputMode:...` → `updateKeyboardDockViewVisibility` → `updateDockItemsVisibility` → `dictationIsFunctional`。
2. `currentInputModeForDictation` 是 emoji，`languageWithRegion` 为 `emoji`（`TIInputModeGetLanguageWithRegion` 在 `-@` 处截断），`TextInput_emoji.bundle` 没有默认听写语言表。
3. `isDictationLanguageEnabled:` 找不到 `emoji` 键，调 `updateEnabledDictationLanguages:NO`。
4. 新字典只由 `activeDictationSupportedInputModeIdentifiers` 构建，此机为空，旧键不保留；`setEnabledDictationLanguages:{}` 与种子值不等，于是覆盖。
5. 之后 `en_US` 也不在字典里，放大器全部回来，直到重启。环不会回来，但每次 `dictationIsFunctional` 又要重算一次并发一条 XPC。

`-[UIKeyboardPreferencesController saveInputModes:]` 也会触发同样的重算，并强制写入（参数为 YES）；用户在设置里增删键盘时，字典同样会被重写【读码】。报告的脚本打字判据碰不到这条路。

修改建议：

- `DICTATION_OFF` 加 `emoji=False`，检查脚本同步。
- 真机判据加一步：切到 emoji 再切回，再看 `updateEnabledDictationLanguages:`、`NSXPCEncoder` 样本和 kbd 的 `wt`。
- 检查脚本同时核对 `.GlobalPreferences` 里的 `Offline Dictation Status`，现在漏了这一项。

## 4. 替代解释

| 替代解释 | 判断 | 依据 |
|---|---|---|
| 定时器退避造成翻倍 | 排除 | 主线程几乎全程在 CPU 上，单次连续在核最长 322 ms；比值在备忘录是 2.00，在 Spotlight 是 2.98，同一个定时器解释不了这种差别 |
| 触摸积压的线性负载（`up 2600`） | 排除 | 备忘录 `up` 停在 301 时排空仍在翻倍。`up` 约为 0.6–1.0×`dn²`（5 组数据），是积压的结果，不是原因【推断】 |
| 写偏好或 Darwin 通知闭合 | 排除 | 相同值短路；样本里是 `dictationConnnectionDidChangeAvailability:` 帧 |
| 网络抖动 | 排除 | 通知条件见第 1 节 |
| 存在第二条独立闭环 | 未完全排除 | `siriPreferencesChanged` 等也发同名通知；样本里没出现，修复后看这个通知是否还在 |

## 5. 仍未证实

- 修复的真机效果。
- 沙盒应用能否读 `com.apple.assistant.support`，全局域兜底也没在设备上测过。**必须单测备忘录或设置搜索框**：Spotlight 是 no-container，它通过不代表沙盒应用通过。
- `isEqualToArray` 快路径能否命中（读码可行）。
- `KeyboardMigrator` 对这些键的实际处理：字符串确认它读 `AppleKeyboards`、`AppleKeyboardsExpanded`，未逐行复核；datamigrator 本轮在 26.9 s、157.0 s 都有 exec【实测】。上机要核对键是否被改写。
- M 的逐观察者分解和备忘录起点。
- 判据“`dictationConnnectionDidChangeAvailability:` 每次聚焦至多一两次”没有计数手段，只能靠采样；建议改用 `M lat fg`、`M cpu ipis`、kbd 的 `wt` 判断。

另：上轮 1147 s 之后的系统级杀进程和 `vnode_rele_ext` panic 不能归到这个环。`Spotlight[485]` 全程没有超限记录，同期还有 `AegirPoster` 崩溃循环【实测】。修好打字不代表这个 panic 也解决。
