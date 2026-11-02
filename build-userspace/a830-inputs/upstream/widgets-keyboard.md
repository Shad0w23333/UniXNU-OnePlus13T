# 小组件、键盘、emoji 排：不依赖新日志的调查与日志判读表

- 范围：iPadOS 17.7.11（21H461）根卷 + iPhone16,1 身份（J120 根），完整包 `测试镜像/boot-A830-完整包.img`（内核 `fd380975`，种子 `final/s5-final.dbrdisk`）。
- 方法：只读。读我们的源码、上游克隆（`参考资料/上游-20261031/`）、21H461 共享缓存反汇编（`ipsw dyld disass` + `dsc-21H461.a2s`，IDA 9.5）、历次 `runs/`。没有碰手机，没有改任何源码树，种子只读挂载。中间产物在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/wk/`。
- 记号：**【读码】** = 反汇编或源码里直接读到；**【上游】** = 上游注释或文档写的，不是我们这里验证的；**【推测】** = 推理，未验证。地址都是 21H461 未滑动地址。
- 完整包那一轮（`runs/20261031-134632-a830-full-1-1`）读回 0 条记录（1880 s 定时复位，`no copy to read`），所以这一轮没有任何日志可用。也就是说，“小组件仍不能用”和“键盘慢”目前都只有用户口述。

## 0. 结论

| # | 问题 | 结论 | 把握 | 修法 |
| --- | --- | --- | --- | --- |
| 1 | 小组件 | **最可能不是 GPU 问题**：`/Applications` 的系统应用视图是 bindfs 联合挂载，我们的 bindfs 没有 `vfs_vget`，还抹掉了 `PERSISTENTOBJECTIDS`/`PATH_FROM_ID`。上游明确写过这一条会让 LaunchServices 把挂载里的应用注册成“无平台、插件无扩展点”，小组件扩展对 chronod 显示为 restricted | 高（我们代码缺失是【读码】，后果是【上游】，21H461 上结果未实测） | 移植 `bindfs_vget` 三处改动；不需要动 GPU |
| 2 | 键盘反馈 | 按键音和触感**没有**在主线程同步等待（【读码】）。真正的等待点是 UIKit 键盘任务队列排队等 kbd 回复。内联补全关掉后，kbd 每键仍有预测（Toucan，CPU）和 **iPhone 惯用手才走的 ML 点按打字** | 中（路径是【读码】，每段耗时要 `pagani-kbtrace=1` 才知道） | 用户现在就能做的对照：设置里关“预测”“自动改正”；种子开关 `EnableMLTapTyping=false` |
| 3 | emoji 那排 | 底部 Dock 的 emoji/麦克风按物理毫米定位，毫米换点要用屏幕点距。我们驱动没有 `dot-pitch`，MobileGestalt 拿到 -1 | 中高（链路是【读码】，“这就是用户看到的错位”是【推测】） | 让 `NativeMobileDisplay` 回答 `dot-pitch = 460` |

用户不用等日志就能做的两个判定，第 5 节有细节：

- **小组件**：主屏默认第二个堆叠上滑到“照片”，或在小组件库加“电池”“Safari”“屏幕使用时间”。这些在根卷上；默认的天气、日历、地图、备忘录都在 bindfs 视图里。根卷的正常、视图里的不正常，就是根因 1。
- **键盘**：换到“备忘录”打字，再到设置里依次关“预测”“自动改正”。关完立刻变快，就是 kbd 计算量。

## 1. 小组件

### 1.1 现有证据有多少

- 全部 `runs/*/merged.log` 没有一行 `WidgetRender`、`chronod`。事件环只保留开机较晚的窗口，所以这**不能**说明它们没运行，只说明我们从未证明它们运行过。
- 历史 08:20 的结论（Apple3 → 私有格式 → 命令缓冲被拒）是主机上复现的，用的是 macOS 的 RenderBox。主机测试没有 chronod、没有 LaunchServices、没有扩展、没有跨进程，所以它**证明不了**真机小组件这条路通了。
- D2（用户专测小组件）`seq 4841..5192` 连续无缺口（`records.jsonl`），覆盖 112.2–173.7 s，没有一个小组件扩展 exec。这是弱证据：用户具体何时在看小组件不清楚，开机早段又被冲掉。
- 全部日志出现过 84 个进程名。系统应用视图里的 106 个 `.appex` **一个都没出现过**；根卷或系统目录的扩展（海报、`ProactiveShareSheetLighthouseBackgroundPlugin` 等）出现过。同样是弱证据，方向和根因 1 一致。

### 1.2 上游怎么说（sheng / DroidVM）

- 上游没有一句“sheng 上小组件完全正常”的结论。可引用的是：`docs/devices/droidvm.md:16,21`（DroidVM：小组件渲染器走 Metal 绘制）；`docs/devices/sheng.md:386`（iPadOS 27 有 “widget page” 的帧耗时测量）；`sheng.md:403`（i570：WidgetRenderer 报告一个被拒的命令缓冲，没崩）。
- **最关键的是 `UniXNU-UniXNU-sheng/patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch`（提交 `41168fa`，2026-10-28）**：
  - `:440-446` 【上游】LaunchServices 按卷和文件 ID 记录 bundle，再经 fsgetpath 解析；没有 `vfs_vget` 时，绑定挂载里的 bundle 解析不回来，于是被注册成**无平台、插件无扩展点**（注释原文 “a widget extension then looks "restricted" to chronod”）。
  - `:416-417` 给挂载加 `MNTK_PATH_FROM_ID`；`:426-432` 保留 `VOL_CAP_FMT_PERSISTENTOBJECTIDS | VOL_CAP_FMT_PATH_FROM_ID`（注释：没有它们 CoreFoundation 不给文件 ID）；`:449-496,506` 实现 `bindfs_vget`。
  - `:284-287` 它的设计和我们一样：staged apps 以只读联合视图挂到 `/Applications`，“bindfs's vfs_vget lets LaunchServices resolve them by file ID”。
- `UniXNU-UniXNU/patches/xnu/zzy-staged-system-apps.patch:11` 【上游】只有 `/System/Library/AppPlaceholders` 占位时，这些应用的小组件保持空白。这解释了更早 iPad 身份下的小组件问题。

### 1.3 我们和上游的差异（小组件相关）

基线 `ce5e19e`。“无”表示我们树里没有。

| 上游提交 | 内容 | 我们 | 与小组件的关系 | 建议 |
| --- | --- | --- | --- | --- |
| `41168fa`（sheng） | `bindfs_vget` 三处改动 | **无**：`bind_vfsops.c:420,424` 抹能力，`:551-561` 无 `vfs_vget` | **直接** | **必须合** |
| `4d789e9` | `supportsFamily` 对 RenderBox 封顶 | 有，`FamilyAnswer.h` 同一阈值（Apple3 起答 NO） | 已覆盖 | 不用动 |
| `4d789e9` | `newTextureWithBytesNoCopy:…` 与 `newTextureLayoutWithDescriptor:…`（`DVMLinearTextureLayout`） | 无，`NativeBridgeMetalFrontend.mm:1714-1723` 直接返回 nil | **中**：RenderBox `RB::TextureCache::prepare_from_texture_data` 的无拷贝分支调它，返回 nil 后这张图没有纹理（`widgets/ida/RenderBox.sel.c:7-90`，【读码】）；ImageIO 也用 layout（`re/ImageIO.selrefs.txt:97`）。CGImageTextureData 型图片（ATX/ASTC）画不出来 | 合 |
| `4d789e9` | 两平面 `b3a8` 表面（行字节为 0 时自己算） | 无，`refuseSurface(3,…)` 直接拒绝（`:1765`） | 中低：【上游】myron 上 App Library 那层因此没画 | 合，先看 `tex` 行有无拒绝 |
| `4d789e9` | 渲染服务器 IOSurface 使用计数（`countUse`） | 无 | 低中：影响生产者判断表面是否空闲，【推测】不会造成全黑 | 合 |
| `4d789e9` | `METAL2VULKAN_SHADER_CACHE_OPTIONAL` | 无，失败时报 `no-cachedir` 且无设备（`NativeAdrenoMetalPlugin.mm:232`） | 低：上游注释点名的是 WebKit GPU 进程；WidgetRenderer 在上游加这条之前就拿得到设备 | 合，便宜 |
| `4d789e9` | 完成等待 4 s 上限 | 无 | 无关 | **不要合**：`34b5920` 已撤销 |
| `4d789e9` | 海报进程开 XR10、`targetDeviceInfo` 不响应 | 无 | 无关小组件 | 按需 |
| `fb2070d` | 按地址重新导入页，不复用同地址旧 no-copy 缓冲 | 无 | 中低：【上游】渲染服务器在旧映射地址上映新内存时 GPU 画出旧页。小组件跨进程表面可能同类 | 合 |
| `fe79bdf`、`20eb8da` | 运行时编译源码、函数拼接 | 无，`NBDeviceMethods` 没有这两个方法，落到 `_MTLDevice` 基类 | 窄：21H461 RenderBox 只有 `RB::FunctionLibrary::function`（自定义着色器）调拼接（`RenderBox.sel.c:695-698`）。【上游】`20eb8da` 说 Core Image 核也靠它，影响设置页图标字形 | 单独评估，不是小组件主因 |
| `34b5920` | 补丁 0022、0023（mipmap、堆纹理对齐） | 无 | 低 | 随 GPU 合入批处理 |

### 1.4 全链路逐环检查

| 环节 | 13T 现状 | 可能失败点 | 判断 |
| --- | --- | --- | --- |
| LaunchServices 注册 | `/Applications` = bindfs 联合（`zzy-native-gpu-addon-mount.patch:38,40`，`phone-system-apps-view.patch`）；`FSNode getFileIdentifier:` 取 `_kCFURLFileIDKey`（CoreServices，【读码】） | 视图里的 bundle 文件 ID 解析不回来，插件无扩展点 | **首要嫌疑** |
| chronod 启动 | 按需启动（`RunAtLoad false`，`com.apple.chronoservices`），`KeepAlive.Crashed` | 崩溃循环 | 无证据；`diag ps` 目前不含它 |
| 扩展校验 | `ExtensionManager.localExtension` → `sub_1D1631628` → `LSApplicationExtensionRecord initWithBundleIdentifier:requireValid:error:`（0x1d16314ec）；失败且 `requireValidExtensions` 时打 `Ignoring restricted extension %{public}s`（0x1d162f0e8）（【读码】） | 记录无效 → 扩展被忽略 | 与第一环同一条因果 |
| 描述符 | `Ignoring descriptor discovery for %{public}@ because it's restricted before first launch.`（ChronoCore 0x1d176f1c4）；SpringBoard 侧 `No descriptor found for widget` | 小组件库没有这些应用；主屏默认堆叠无描述符 | 同上 |
| 钥匙包/数据保护 | `DataProtectionMonitor`、`Pending task due to data protection level requirements`。`native_aks_lock_state()` 恒 0（`Stage7Probe.cpp:202`），bit 2 为 4，`MKBDeviceUnlockedSinceBoot` 为真 | 任务挂起等解锁 | **基本排除**（【读码】） |
| 启动约束/信任 | 抽查 chronod、WidgetRenderer、天气、时钟、日历、提醒、电池 7 个二进制都在静态信任缓存；`sheng-no-lc=1` 已开 | exec 被拒 | **排除** |
| 扩展进程 | 经 xpcproxy 起，没有 `.appex` 从视图里跑起来的记录 | 沙盒、容器、jetsam | 第一环修好才轮到它 |
| App Group 容器 | 应用本体能开，说明 containermanagerd 能工作；扩展自身未验证 | 扩展容器 | 【推测】问题不大 |
| 归档/快照 | `~/Library/Caches/com.apple.chrono*`（权限表）；用 `NSFileProtectionKey`（ChronoKit 0x1d15cc2a0） | 【上游】`owner-dataprotect`：非内容保护卷上类别读回为 0，PosterBoard 因此判数据存储不可用。chrono 是否同样敏感**未查清** | 次要，第二梯队 |
| 内容数据 | 天气 `NSWidgetWantsLocation=true`，但 `locationd` 被禁、无网络 | 单个小组件只有占位 | 修好后天气仍可能空，不要误判 |
| WidgetRenderer_Default | 在根卷 `/Applications`（联合下层，不受 bindfs 影响）；名单点名；大窗口 352 MiB（`NativeAdrenoPublish.h:152`） | 没启动、设备失败 | 看 `diag ps` 与 G 行 |
| GPU 地址窗口 | 124 MiB 只给普通 app/扩展；WidgetRenderer 不在此列。扩展只归档、一般不用 GPU | 窗口耗尽 | 不是首因；有 `0018` 兜底 |
| 客户端规则 | `@apps @extensions !SpringBoard !com.apple.WebKit.*` 已写入种子；`WidgetRenderer_Default` 点名，有 10 s 状态行 | SpringBoard 没有 Metal 设备 | 见下一行 |
| SpringBoard 显示 | 实时场景之外还有快照：`Read astc snapshot`（ImageIO，`com.apple.atx`，0x1b7eb4e38）、`Read caar snapshot`、`No snapshot image found or couldn't be opened (data protection)` | Data 每次开机全新，首次没有快照；后续 ATX 走无 Metal 的 SpringBoard | 【推测】不是首因 |
| jetsam | WidgetRenderer 98/45 是软上限；Metal 客户端下限 512 MB | 被杀 | S 行看 `ns=1 code=7` |

### 1.5 最可能的三个根因

**根因 1（首选）：LaunchServices 经 bindfs 注册失败，扩展被 chronod 忽略。**

支持它的事实：

- 默认主屏布局 `SpringBoard.app/DefaultIconState.plist` 的两个小堆叠是【天气、地图、备忘录】和【日历、新闻、照片】，外加今日视图的日历。除“照片”外全在 staged apps（bindfs 视图）里。
- 35 个小组件扩展里，25 个在视图中，10 个在根卷（屏幕使用时间、钱包、电池 ×2、Game Center、联系人、App Store、睡眠、Safari、照片）。
- 时间线也吻合：占位阶段小组件空白 → 06:39 C 包用 bindfs 视图把应用“修好” → D 包小组件仍黑。修 RenderBox 没有改变现象。
- 没有修 bindfs 之前，RenderBox 再对也没有内容可画。

确认办法：第 5 节的 A/B，或把修好的内核上机看小组件库是否出现天气、日历，S 行是否出现 `WeatherWidget`、`CalendarWidgetExtens`。

移植要点：只动 `bsd/miscfs/bindfs/bind_vfsops.c`，三处都是上游原样的小改动；`vnode_lookupat`（`bsd/sys/vnode.h:2127`）、`mount_lookupby_volfsid`（`vfs_subr.c:7580`，要求 `MNTK_PATH_FROM_ID`）在我们 xnu 里都有。Sheng 仓库是 BSD-2-Clause-Patent，改的是 APSL 文件，可以照事实重写或按其许可合入。上游实测的是 xnu-12377/iPadOS 27，xnu-10063 上要自己测。注意 `MNT_UNION`：bindfs 只回答上层文件 ID，下层根卷文件仍走根卷。

**根因 2：内容有了，但 WidgetRenderer 这一步画不出来。** 子项：图片走 `prepare_from_texture_data` 的无拷贝分支拿到 nil（1.3 表）；主机没测到的 21H461 RenderBox 路径。确认：G 行 `WidgetRender c/e/x/w`。

**根因 3：跨进程合成或环境依赖。** 例如 `b3a8` 被拒、旧页导入（`fb2070d`），或第二梯队的数据保护类读回。确认：`tex x… s…/…` 的拒绝数，以及修完根因 1 后是否仍黑。

修复顺序建议：先单独上 `bindfs_vget`，看现象；不要和 GPU 合入混在一包，否则分不清是谁修好的。

## 2. 键盘

### 2.1 一个键经过什么（iPhone 身份，【读码】）

1. 按下：`-[UIKeyboardLayout touchDown:]`（0x18ac39e40）把任务加进键盘任务队列。
2. `-[UIKeyboardLayoutStar performHitTestForTouchInfo:…]`：先 `playKeyFeedbackIfNecessaryForTouchDownOnKey:touchInfo:`（反馈在这里发出），再看 `canHandleKeyHitTest`。种子的 `KeyboardDisableHitTesting=true` 由 kbd 读（`TIGetKeyboardDisableHitTestingValue`，0x1d931209c），让这一项为假，所以不走 kbd 命中测试，本地返回键位。
3. `touchDownWithKey:…` 里 `setState:forKey:` 显示按键高亮和气泡。
4. 抬起：`touchUp:` → `handleKeyboardInput:` 发给 kbd，**文字要等 kbd 回复才出现**。
5. 之后 `-[UIKeyboardImpl generateCandidatesWithOptions:]`（0x18a486bc4 起）决定是否 `setNeedsAutocorrection`。这个延后任务在 kbd 回复前占着队列（`sysfix/keyboard/NOTES.md` 2.1）。

### 2.2 同步等待：有哪些，没有哪些

对 UIKitCore 所有键盘、触感、反馈相关函数搜了 `dispatch_sync`、信号量、`group_wait`、`waitUntilAllTasksAreFinished`、`lockWhenReady`、同步 XPC（结果 87 条，`wk/kb_waits.txt`）。

- **反馈路径没有同步等待（【读码】）**：iPhone 身份 `+[_UIKBFeedbackGeneratorPartner feedbackGeneratorWithView:]` 返回 `_UIKBFeedbackGenerator`；`playKeyClickSoundForKey:` 只调 `actionOccurred:atLocation:`，后者 `_performOnQueue:` 派到私有队列 `com.apple.UIKit.UIKBFeedbackGenerator`。CoreHaptics 引擎的创建、启动、播放都在私有队列。这个类的所有方法里没有上述等待。所以 `--keyboard-haptics` 和未就绪的音频路由**卡不住主线程**，最多让引擎反复重建，浪费一点 CPU。`AudioServicesPlaySystemSound` 只在少数特殊键分支出现（`_playSystemSound:` 0x44f、0x463），字母键不走。
- **会阻塞主线程的等待点都指向任务队列**：`-[UIKeyboardTaskQueue lockWhenReadyForMainThread]`，调用者有 `waitUntilAllTasksAreFinished`（来自 `-[UIKeyboardImpl prepareForSelectionChange]`，再上一层是 `selectionWillChange:`）、`-[UIKeyboardAutocorrectionController autocorrectionList]`、`performTask:`。也就是说 kbd 慢的时候，主线程可能被直接拖住。这比“只是下一键排队”更严重，也能解释 A 轮 `dn`、`up` 相隔 1 ms（【推测】，另一种解释见 2.5）。
- `TIImageCacheClient` 是 mmap 本地读，不经 kbd，排除。`TextInput.framework` 除 `+[TIRecentInputs …]` 外无同步 XPC。
- kbd 侧唯一常驻同步是 `KB::LanguageModelContainer::conditional_likelihood_batched` 的 `dispatch_group_wait`（NOTES 已列）。其余都在自动填充、词库等非打字路径。

### 2.3 kbd 每键做什么：更正 sysfix 笔记

- **候选打分**不走变换器：`determineModelType`（LanguageModeling 0x1a0e16554，【读码】）里，`en.lm/Info.plist` 没有 `UseForConditionalProbability`，且 H13ANE 标志（`byte_1EDEA6458`，`sub_1A0E75428`：子类型 >31）在 13T 为假，于是 “Falling back to default model type TokenID”，即 `montreal.dat`（LSTM）。NOTES 第 3 条和 2.3 节“候选打分跑整型变换器”不成立。
- **预测和补全**走 Toucan：`KB::LanguageModelStr::load` 在 `s_use_lm_transformer=-1`（默认）时把 `kLanguageModelForCompletionsAndPredictionsKey` 设为 Toucan；LanguageModeling 在 `SupportsCompletionsAndPredictions=true` 且选项为 Toucan 时建 Toucan 会话，这里**没有 ANE 门控**（`sub_1a0ddc59c`，【读码】）。所以关掉内联补全只去掉 0x10/0xc8 这部分，预测栏 0xcb/0xcc 还在 CPU 上跑。每键跑几次前向未知（【推测】是主要开销之一）。
- **ML 点按打字只在 iPhone 惯用手下启用**：`-[TIKeyboardInputManager(ResultSpecializations) usesMLTapTyping]`（0x1d93146e8）要求 `userInterfaceIdiom == 0`，且非硬件键盘、非分离、非浮动（【读码】）。走 `InputTranscoder`（`en.cpml/tap/`，`beamSize 40`、`maxCandidates 20`、`lmAlpha 0.8`），由 `update_autocorrection_candidates_with_ensemble` 接入。这是 iPad 身份和 iPhone 身份的一个确定差异，而 PROGRESS 记录“iPad 身份下键盘完全正常”。`models.plist` 写 `useNeuralEngine=true`，但创建时只把模型 URL 交给 `MRLNeuralNetworkCreate`，实际后端未知。
- `KeyboardTransformerLMForAutocorrection`（`com.apple.keyboard`，默认 2，映射表 `(1,0,2)`）：在 13T 上帮不上忙，设 0 得到的就是现状 TokenID，设 1 反而强制 ToucanCPU。**不要用。**
- 功能开关 `transformer_lm_for_keyboard_english_input` 只用于多语言判定（`_TIIsTransformerLMEnglishMultilingualEnabled`），关它不会停掉 Toucan。

### 2.4 根因排序

1. **kbd 每键计算量 + 任务队列串行化**（把握中）。开销来自 Toucan 预测和/或 ML 点按。
2. **App 自己的主线程慢**（把握中低）：测试清单让用户在设置搜索框打字，每键都触发搜索。`task` 组会显示。这一条和键盘无关，换备忘录就能分开。
3. **系统手势门控**（把握低）：`_UISystemGestureGateGestureRecognizer` 超时 0.75 s（0x18a2f34cc 处常量 `0x2cb41780` 纳秒），无 Home 键身份下四边 30 pt 内的触摸可能被延迟（`_edgesForSystemGesturesTouchDelay`、`_edgeForTouch:`）。只影响贴边的键。比较 q/p 和 g/h 的手感即可。
4. **合成或触摸延迟**（把握低）：用户说整体流畅，且 NOTES 已排除探针本身。

### 2.5 开关表

| 开关 | 位置 | 作用 | 代价 |
| --- | --- | --- | --- |
| “预测” | 设置 > 通用 > 键盘；`KeyboardPrediction`（`com.apple.keyboard.preferences`） | 停 Toucan 预测 | 没有预测栏 |
| “自动改正” | 同上；`KeyboardAutocorrection` | 与上一项同关时，`generateCandidatesWithOptions:` 走 `clearAutocorrectionAndNotifyObservers:`，**不再发请求**（0x18a486f4c 起，【读码】） | 无纠错 |
| `EnableMLTapTyping = false` | `com.apple.keyboard`（kbd 用 `CFPreferencesGetAppBooleanValue` 读，0x1d931c764，仅英文分支） | 关 ML 点按，退回传统路径 | 点按纠错略弱；种子工具 `keyboard-prefs.py` 已能写这个域 |
| 键盘反馈“声音”“触感” | 设置 > 声音与触感 > 键盘反馈；`KeyboardVisceral` | 排除反馈路径 | 无 |

建议下一包：种子加 `EnableMLTapTyping=false`，保留预测。若 `klm` 仍高，再决定是否默认关预测。关预测是用户可见的功能取舍，由用户拍板。

启动参数注意：完整包命令行已 986 字节，上限 1000，`pagani-kbtrace=1` 加空格要 17 字节，**放不下**。要去掉一项，例如 `pagani-audio-mx=1`。

## 3. emoji 那排错位

判断：用户说的是键盘**下方 Dock** 的 emoji 和麦克风按钮（Face ID 机型的布局），不是键盘按键层。

链路（【读码】）：

1. `+[UIKeyboardDockView _itemFramesForBoundingSize:]`（0x18a2d5b38）用**物理毫米**定位：按钮中心距边 7.0435 mm，尺寸 4.9625 × 5.0661 mm，再减去 `__UIKBGetDockItemTouchPaddingForCurrentDevice` 的毫米内边距。
2. 毫米换点用 `-[UIScreen _nativePointsPerMillimeter]` = `main-screen-pitch / 25.4 / nativeScale`（`_ensureComputedMainScreenDPI` 0x18a2d5e94）。
3. `main-screen-pitch` 来自 `IOMobileFramebufferGetDotPitch`（MobileGestalt `sub_1A0215704`，`MGIOMFBSupport.c`）。初值 -1，失败时保持 -1。
4. 我们的 `getDotPitch`（`NativeMobileDisplay/NativeLegacyDisplay.cpp:1011-1018`）读提供者的 `dot-pitch` 属性；全仓库没有任何地方提供它，所以返回 `kIOReturnNotFound`。上游 `check-system-framebuffer-client.py:53` 也把这当成“真实缺失”。

结果：点距无效，按钮框架算出来接近零尺寸或位置不对。MobileGestalt 最终把 -1 转成什么答案（负数还是空）没有追到底，两种情况都是错的。

其他候选（可能性更低）：`KBLayouts` 的 405 族只横向缩放，按键层不受影响；F2 修补只影响键盘框架推导；安全区 34 pt 由系统给出。iPhone 键盘资源不是 iPad 根卷缺的那种问题，因为这个 Dock 不读 `KBLayouts`。

修法：`getDotPitch` 回答 460（13T 6.32 英寸 1216×2640 算得 459.9，与 iPhone 15 Pro 表项相同），或在 `NativeMobileDisplay` 启动时发布 `dot-pitch` 属性。按公式，左按钮中心约 42.5 pt，按钮约 30×30.6 pt（未减内边距）。

副作用评估：`_pointsPerInch` 其他用户只有 `___preferredIdiomSubtypeForIdiom`、`UIImageDeviceSpecificName`、`rivenSizeFactor`、`floatingWidth/Height`，都是修正方向。1216×2640 不在 MobileGestalt 的屏幕表里（表内已知 37 项，`mg_screen.c`），会多一条 `screen parameters are unexpected` 故障日志，屏幕类别仍是 -1，与现状相同。

## 4. 日志判读表

前提：`pagani-gpu-status=3`、`pagani-diag=1`、`pagani-kbtrace=1`；测完**长按**取日志（定时复位那轮读回 0 条）。

### 4.1 小组件

| 看哪里 | 值 | 结论 |
| --- | --- | --- |
| `U` 行 `system apps view stage … status …` | `stage 5 status 0` | 视图挂载成功，继续往下看 |
| `S` 行 `exec process signature enter` | 出现 `WeatherWidget`、`CalendarWidgetExtens`、`WorldClockWidget`、`com.apple.mobilenote`（进程名截到 20 字符） | 扩展能启动，根因 1 不成立或已修好 |
| 同上，并且用户确实打开过小组件库 | 只有根卷扩展（`PhotosReliveWidget`、`BatteriesWidgetExten`、`SafariWidgetExtensio`） | **根因 1 成立** |
| 同上 | 视图内外都没有小组件扩展 | 查 chronod：是否运行、是否崩 |
| `diag: k<s> ps` | `WidgetRenderer:-` | 渲染器没起来，问题在 SpringBoard 或 RunningBoard 拉起 |
| 同上 | `WidgetRenderer:<pid>/N`，N 大 | 崩溃循环，看 `S exit` 的 status、ns、code |
| `S exit pid=… status=9 ns=1 code=7` | 出现在小组件进程 | jetsam |
| G 行 | `WidgetRender no-cachedir`、`no-device`、`no-turnip` | 设备失败，合 `SHADER_CACHE_OPTIONAL` 或查 Turnip |
| G 行 `WidgetRender c<提交>/<拒绝> e x w f r m` | `c` 长期 0 | 没东西可画，问题在内容链路 |
| 同上 | `c>0 e0 x0 w1` | RenderBox 在正常工作，黑不在 Metal 客户端，查合成 |
| 同上 | `e>0` 或 `x>0` | 根因 2，看同进程 `problem …` 文本 |
| 同上 | `w0` 且 `c>0` | `supportsFamily:` 封顶没有命中，查调用方映像名 |
| G 行 `problem` 文本含 `newTextureWithBytesNoCopy` | 有 | 合 `DVMLinearTextureLayout` |
| `tex x… s…/… p…` | 第二个数（拒绝）>0 | 根因 3，看 `SurfaceRefusal` 的 `step` 和格式 |

### 4.2 键盘（`kbt <进程>[pid] lat <组> 完成/平均ms/最大ms/≥100ms/≥400ms`）

| 看哪里 | 值 | 结论 |
| --- | --- | --- |
| `kbd` 进程的 `kac` | 平均 ≥150 ms 或 ≥400 ms 次数 >0 | kbd 计算是瓶颈，继续看 `klm` |
| `kbd` 的 `klm` | 高，且 `kic` 为 0 | 语言模型（Toucan 预测），用“关预测”对照 |
| `kac` 高而 `klm` 低 | — | 时间在 LM 之外，最可能是 ML 点按或 Favonius，用 `EnableMLTapTyping=false` 对照 |
| `kic` | 次数 ≠0 | 内联补全偏好没有生效 |
| App 进程的 `ac` | 明显大于 `kac` | kbd 排队（kbd 串行），不是单次慢 |
| App 进程的 `dn` | 平均 ≥100 ms | 按下任务被前一键的请求堵住，或主线程被占 |
| `dn` 正常、`up` 或 `in` 高 | — | 文字慢、高亮不慢，kbd `kin` 是关键 |
| `khit` | 次数 >0 | `KeyboardDisableHitTesting` 没生效 |
| App 的 `task` | 平均或最大大，而 `ac`、`kac` 小 | App 主线程自己慢（搜索页），换备忘录复测 |
| `ac`<80、`up`<30、用户仍说慢 | — | 卡在显示或触摸，看同时段 `J` 行，或查边缘门控 |
| `ac` 为 0 | 且用户已关预测和自动改正 | 对照成立；若仍慢则与 kbd 无关 |
| `first up-done` 比 `first dn` 晚十几秒以上 | 只发生在第一次 | 第一次就绪问题（NOTES #4），不是每键问题 |

不要用 `F FPS … SLOW` 判断打字快慢（NOTES 第 5 节）。

### 4.3 emoji

肉眼：Dock 按钮中心离左右边各约 42 pt，大小约 30 pt，能点中。现有日志看不到这一项；如加诊断，打印 `MGCopyAnswer("main-screen-pitch")`，修好后应为 460。

## 5. 给用户的最小对照（不用改镜像）

**小组件（30 秒）**

1. 主屏小组件堆叠上下滑到“照片”。
2. 长按主屏 → 加号，看列表里有没有 天气、日历、时钟、提醒事项、备忘录。
3. 加一个“电池”或“Safari”或“屏幕使用时间”。

判读：第 2 步缺这些应用、第 3 步能显示 → 根因 1。根卷小组件也黑 → 根因 2 或 3 也存在，修 bindfs 后还要继续查。

**键盘（2 分钟，一轮有效，Data 重启即丢）**

1. 备忘录里打一句，和设置搜索框比较。差很多 → 根因 2（搜索页）。
2. 设置 > 通用 > 键盘，关“预测”，回备忘录打字。
3. 再关“自动改正”“检查拼写”。
4. 设置 > 声音与触感 > 键盘反馈，关声音和触感。
5. 按 q/p 和 g/h 比较。

## 6. 下一包建议（按优先级）

1. 内核：`bindfs_vget`（小组件）。先单独验证。
2. 驱动：`dot-pitch = 460`（emoji）。
3. 种子：`EnableMLTapTyping=false`；启动参数加 `pagani-kbtrace=1`，为此去掉一项。
4. 诊断：`pagani_diag_watched` 加 `chronod`，再加一份小组件扩展的进程普查（上游 `BootProgress.c` 的 census 思路），否则事件环一冲就看不到。
5. Metal：`DVMLinearTextureLayout`、`b3a8`、`countUse`、`fb2070d`、`SHADER_CACHE_OPTIONAL`，走 GPU 合入批次，不和第 1 项同包。

## 7. 没查清的

- bindfs 在 21H461 上的实际后果没有实测，依据是上游注释和我们代码同样缺失。LaunchServices 内部从文件 ID 到“无扩展点”的逐步路径没有在 21H461 上走通，只确认了 `FSNode` 用文件 ID、chronod 用 `requireValid`。
- 上游注释来自 iPadOS 27；根卷“照片”等 10 个扩展在我们这里到底能不能显示，用户没有反馈过。
- `ChronoKit` 的文件保护读回是否受非内容保护卷影响，未查清。
- Toucan 与 ML 点按每键耗时、InputTranscoder 实际后端，都没有测量。
- `main-screen-pitch` 为 -1 时 MobileGestalt 最终答案的具体形态没有追到；用户说的“小错位”也未看到实物，判断来自用户描述“底下那个 emoji 的地方”。
- 特殊键分支 `_playSystemSound:`（AudioToolbox）是否同步没有细查，字母键不走。
