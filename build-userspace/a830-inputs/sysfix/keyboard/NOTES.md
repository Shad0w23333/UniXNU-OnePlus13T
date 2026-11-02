# 键盘每键卡顿与候选词为空：证据重读、逆向结论与修复（sysfix 键 keyboard）

- 范围：iPadOS 17.7.11（21H461）根卷 + iPhone16,1 身份（J120 根）。只读逆向与主机测试；没碰手机、没提交、没写 `UniXNU/out-gpu`、没改共享接线文件（Stage7Probe、components.toml、board.toml、defconfig、series、make-a830-layout.sh、make-phone-seed.py 都只给差异/新文件）。
- 工具：`ipsw dyld disass/symaddr/xref/str`（`dsc-21H461.a2s` 符号缓存）、IDA 9.5 无头（MobileGestalt、kbd 主程序）。苹果文件与摘录只在本地（`re/`）。
- 记号：`A` = `runs/20261031-063106-pkgA-diag-cc-f2-1`（唯一开了 `pagani-kbtrace=3` 且打过字的一轮），`D` = `…070114-pkgD-longpress-1`，`D2` = `…070836-pkgD2-widgets-wallpaper-1`。证据写成“轮次 seq 日志秒 原文”。`@…ms` 是 mach 时间（比日志秒多约 58.9 s）。

## 0. 结论一览

| # | 嫌疑 | 结论 | 关键证据 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | `FPS 9 … SLOW 497ms gap 500 … copy 0.7`（“GPU 忙 9%”） | **不是打字卡顿**：是 583 s 以后屏幕静止、只剩周期动画（Spotlight 输入框光标闪烁一类）时的帧间隔 | 582.923 s 之后再无任何 `kbt` 行（任何触摸都会在 4 s 内出汇总行）；628–661 s 每秒同一行 `FPS 9 (9-67) | ft 111 p99 476 max 497ms | gpu 9`；`J jitter 240.x ms … skipped 9` 从 594.372 到 653.662 s 每 5 s 一条一模一样；GPU 一直 `GPU RUN 160M b1`（最低档、忙 1%）；`BootProgress/FrameRate.h` `FRAME_IDLE_MS = 1000`：小于 1 s 的间隔都算 jitter、进 SLOW。`gpu 9` 是“这一秒 9 帧走 GPU 路径”，不是 9 % | 不需要修；判读规则写进第 5 节 |
| 2 | `cand 0/0`（“kbd 预测无回应”） | **计数器选错了路径**：`cand` 只数 `-[UIKeyboardImpl generateCandidatesAsynchronouslyWithRange:selectedCandidate:]`（0x2bae00a4/a8），英文 QuickType/自动更正根本不走这里 | `-[UIKeyboardImpl generateCandidatesWithOptions:]`（0x18a486bc4）只有 `usesCandidateSelection`（中日文等带候选选择的输入法）才调它，否则走 `autocorrectionPreferenceForTraits` → `-[UIKeyboardAutocorrectionController setNeedsAutocorrection]`（0x2bae008c/0x2bae0090），旧探针不数这对点 | 探针 v2 新增 `ac`（自动更正请求往返）与 kbd 侧 `kac`；用户也从没说过预测栏是空的 |
| 3 | 每键约 0.5 s 停顿 | **真实，主因在 kbd 的自动更正请求占住 UIKit 键盘任务队列**，A 轮又叠加 CPU 被钉在开机档 | 机制见 3.1；A 轮心跳整段 `cpu 1986MHz`（241–581 s，调速器没在工作；D 正常时是 3513/954/3303 MHz 变化）；`Spotlight[347]` 第一个键：`dn @496553` → `up @496554`（抬起与按下 1 ms 内一起被处理＝主线程此前被占）→ `dn-done @496707`（154 ms）→ `in @496735` → `up-done @497125`（抬起后 572 ms 才完成） | ①关掉内联补全（种子偏好，3.2）；②CPU 调速器修复（另一路）；③探针 v2 早上量化 |
| 4 | 第一次用键盘时整段卡住 | **真实**：`Spotlight[321]` 有 9 次抬起整整约 27 s 没完成 | `A 6181 251.408 kbt Spotlight[321] sum ev 19 set 2 dn 3/1 dr 2 up 9/0 task 15/15 in 0/0`，直到 `A 6414 278.280 kbt Spotlight[321] first up-done @337207ms`；首个 `dn-done @308321ms`（约日志 249.4 s） | 原因只能缩到“kbd 侧首次就绪”（每次开机 Data 全新、没有任何键盘缓存，模型冷加载）；探针 v2 的 `ksync`/`kin`/`kac` 第一次耗时会直接给出；暂无安全的数据修法，列为待测 |
| 5 | 变换器语言模型走 ANE（身份是 A17 Pro） | **没有走**：系统判定无 ANE，用 CPU 模型 | MobileGestalt `HasAppleNeuralEngine`（表项 0x1e7c51e78，答案函数 0x1a0211a4c）＝`AppleNeuralEngineSubtype > 0`；`AppleNeuralEngineSubtype`（表项 0x1e7c42798，0x1a0207ef0）＝在 IODeviceTree 里递归找 `ane-type`，我们的 ADT 没有 → 空 → 假。LanguageModeling 0x1a0e75428 只在 subtype > 31 时置位，置位才把 `_cpu.espresso` 改成 `_ane.espresso`（0x1a0ebf698 `cbz w20`）；TextInput `isH13ANEPresentOnIOS` 同一规则；这个根卷里也根本没有 aned | 不用修。**不要往 ADT 里加 `ane-type`**（加了就会去找不存在的 aned） |
| 6 | 按键音（坏掉的音频路由）、键盘触感 | **不会卡键**：全部异步 | iPhone 身份：`+[_UIKBFeedbackGeneratorPartner _isVisceralEnabled]` ＝ 功能开关 `keyboard_visceral`（开）且 MG `ipad` 为假 → 用 `_UIKBFeedbackGenerator`；按下时 `playKeyFeedbackIfNecessaryForTouchDownOnKey:touchInfo:` → `playKeyClickSoundForKey:` → `actionOccurred:atLocation:`，后者只 `dispatch_async` 到私有队列 `com.apple.UIKit.UIKBFeedbackGenerator`（`_performOnQueue:`）；这个类的所有方法里没有 `dispatch_sync`/信号量等待；纯声音模式走系统声音（`keyboard_ch_audio_only` 开关未开） | 不用修。触感偏好键是 `com.apple.keyboard.preferences` `KeyboardVisceral`（默认无值＝关）；震动链路通了再开，开了也不会卡打字 |
| 7 | `pagani-kbtrace=3` 本身 | **单靠它造不成 0.5 s**：每个放行的埋点只多一次立即返回的系统调用（`kdebug_enable` 为 0） | `kdebug_trace64` 先进 `pagani_kbtrace_note`（类型不认识就返回），再 `kdebug_enable == 0` 返回；`kdebug_trace_string` 在 `kdebug_enable == 0` 时第一行就返回 | 早上改用 `pagani-kbtrace=1`（只放行键盘子类），并用 v2 的计时直接看 |
| 8 | kbd 崩溃、被杀 | **没有** | `D 4855 104.796 S pid=304 kbd exec …` 之后到 157 s 没有 `exit pid=304`；所有轮次没有 kbd/InputUI 的 X 或异常退出；kbd 的 launchd `JetsamMemoryLimit 8192` | — |
| 9 | iPhone 身份缺语言资源 | **不缺** | `LinguisticData/RequiredAssets_en.bundle`（`__RequiredByOS`，随系统装）带 `en.lm/unilm.bundle`（CPU/ANE 两套网络）、montreal、lm_gram3 等；与机型无关 | — |

## 1. 证据重读

### 1.1 A 轮的 `FPS 9 … SLOW 500`
- 最后一批键盘行：`A 7824 582.923 U kbt SpringBoard[27] sum ev 2206 …`、`A 7825 582.923 U kbt Spotlight[351] sum ev 0 set 1 dn 0/0 …`（351 是第三个 Spotlight 进程，键盘弹出后一个键也没按）。之后到 660 s 复位没有任何 `kbt` 行。旧探针在第一个未汇报事件后 4 s 必出汇总（`PAGANI_KBT_FLUSH_MS 4000`），所以 583–660 s 屏幕上没有触摸。
- 同一时段：`A 8189 628.410 F FPS 9 (9-67) | ft 111 p99 476 max 497ms | gpu 9 cpu 0 | jit 2 | SLOW 497ms gap 500 …`，此后每秒同一行；`A 7923 594.372 J jitter 240.2ms gpu at 593660ms gap 242 … skipped 9`，每 5 s 一条（限流：`FRAME_JITTER_PERIOD_S 5`，`skipped 9`＝这 5 s 里另有 9 次 ≥50 ms 的间隔，正好每秒两次）；`A 8181 628.009 G … GPU RUN 342M b9` 之后一直 `GPU RUN 160M b1 g1/b2`。
- `FrameRate.h`：`FRAME_JITTER_MS = 50`、`FRAME_IDLE_MS = 1000`（“below this: from here on the compositor had nothing to show”）。静止画面上每秒两次、间隔约 0.5 s 的小动画（光标一亮一灭）就会得到“9 帧/秒、最长 500 ms”。**这组数字说明不了打字快慢。**

### 1.2 真正打字时的数字（A 轮，开着 kbtrace=3）
- `Spotlight[347]`（435.5 s 启动）第一个键：`first dn @496553ms`、`first up @496554ms`、`first dn-done @496707ms`、`first in @496735ms`、`first up-done @497125ms`。人手按下到抬起不可能只有 1 ms：按下与抬起是被一起处理的，说明这之前主线程/任务队列被占着；抬起任务又用了 572 ms 才结束。
- `Spotlight[321]` 第一次用键盘：`dn 3/1 … up 9/0 … in 0/0`（251.4 s），第一个 `up-done` 在 278.28 s。9 个抬起任务等了约 27 s，期间一次 `handleKeyboardInput` 都没有发出（`in 0/0`）→ 卡在发给 kbd 之前的环节（输入管理器未就绪一类）。
- 之后的稳态没有逐键时间（旧探针只有计数）。用户原话“能工作了，但输入极其卡、可能不到一帧”。
- CPU：A 轮全部心跳 `cpu 1986MHz`（如 `A 6734 321.077 H … cpu 1986MHz on 3`、`A 7722 561.335 H … cpu 1986MHz on 1`），和 D2 调速器 41.8 s 自行关闭后的读数一样；D 调速器正常时是 `cpu 3513MHz`/`954MHz`/`3303MHz`。所以 A 整段打字时性能核约 2.0 GHz、超大核约 1.0 GHz。

## 2. 逆向：21H461 一个键经过哪些等待

### 2.1 UIKit（App 进程里，键盘布局在 App 自己进程，`kbt Spotlight[…] dn/up` 即证）
- 按下：`-[UIKeyboardLayout touchDown:]`（0x2bae006c）把任务加进 `UIKeyboardTaskQueue`，完成点 0x2bae0070；按键音/触感在这里异步发出（见 0 表 #6）。
- 抬起：`-[UIKeyboardLayout touchUp:]`（0x18ac3aa84 处 0x2bae007c）→ `touchUpTaskForTouchState:` → `addTask:`；任务用 `childWithContinuation:` 包着，**执行上下文交回父级时才发 0x2bae0080**（`___28-[UIKeyboardLayout touchUp:]_block_invoke_2`）。队列在交回之前不跑下一个任务。
- 输入：`-[UIKeyboardImpl handleKeyboardInput:executionContext:]`（0x18ac082ec，0x2bae0084）→ kbd 的 `handleKeyboardInput:keyboardState:completionHandler:`，回复后 0x2bae0088。
- **自动更正/预测（关键）**：`-[UIKeyboardAutocorrectionController setNeedsAutocorrection]`（0x18a535ae4，0x2bae008c，arg1 为随机令牌）→ `addDeferredTask:` → `requestAutocorrectionWithExecutionContext:` → `-[UIKeyboardImpl generateAutocorrectionWithExecutionContext:]` → `generateAutocorrectionsWithCompletionHandler:`（发给 kbd）→ kbd 回复后 `transferExecutionToMainThreadWithTask:` → 0x2bae0090 → `returnExecutionToParent`。**这个延后任务在 kbd 回复之前一直占着键盘任务队列**，下一键的按下/抬起任务只能排队。kbd 一次自动更正请求要多久，下一个键就最多晚多久。
- 其它同步等待（都在键盘代码里，已逐个看过）：`-[UIKeyboardTaskQueue lockWhenReadyForMainThread]`（0x18a47ba78）先等 50 ms、之后每次 1 s，超时打 “timeout waiting for task on queue” 并 SimulateCrash “Keyboard queue task timeout detected”；`-[UIKeyboardAutocorrectionController autocorrectionList]` 在有未完成请求时 `waitUntilAllTasksAreFinished`；`acceptAutocorrectionWithCompletionHandler:…` 只对非 `com.apple.` App 且键盘界面在独立进程时转 run loop 等；`_UIKBRTRecognizer` 的信号量等待属于 iPad 手掌休息功能，受内部偏好 `_UIRestablePadKeyboardsEnabled` 控制（客户版关），与我们无关。

### 2.2 kbd（TextInputCore）
- kbd 每个操作都打 0x2bae0034（开始）/0x2bae0038（结束），**arg2 是操作号**（`re/textinputcore-kdebug-0x2bae.txt`）：0x00 syncToKeyboardState、0x01 自动更正请求（XPC 入口 → 回复）、0x03 候选、0x07 键位命中测试、0x10 内联补全、0x11 内层自动更正、0x14 候选打分、0x16 Favonius 束搜索、0x64/0x65 按键输入（0x65 = 进核心 → 回调）、0xc8 内联补全的语言模型、0xc9/0xca 条件概率（批量）、0xcb 预测、0xcc 带词干预测、0xcd 存根预测。
- 自动更正请求的收尾 `-[TIKeyboardInputManager finishGeneratingAutocorrectionsWithCandidates:candidateRange:candidateHandler:]`（0x1d9300b64）：`shouldGenerateInlineCompletions` 为真时 `dispatch_group_enter` + `finishGeneratingInlineCompletions:completion:syncResponse:`（操作 0x10），再加 ResponseKit 主动建议，最后 `dispatch_group_notify` 到主队列才算请求结束。`KB::LanguageModelContainer::conditional_likelihood_batched` 里是 `dispatch_group_wait(…, DISPATCH_TIME_FOREVER)`（0x1d928a7e8）。**没有任何超时兜底，模型算多久请求就多久。**
- `shouldGenerateInlineCompletions`（0x1d9389a24）：不在改已有词 → `keyboardState.inlineCompletionEnabled` → 功能开关 `inline_completions_everywhere`（开）。UIKit 侧 `-[UIInlineTextCompletionController _inlineCompletionPreference]`（0x18a2ac424）＝ `KeyboardPrediction` 且 `KeyboardInlineCompletion`；后者在 `-[TIPreferencesController _configurePreferences]`（0x19173d1fc）注册于 `com.apple.keyboard.preferences`，**默认真**（即 设置 > 通用 > 键盘 > “行内显示预测”）。
- 模型：`RequiredAssets_en.bundle/AssetData/en.lm/Info.plist` 第 9 项 `unilm.bundle/unilm_joint_cpu.espresso`，`MontrealModelType CoreLM`，`ForwardPassLimit 32`；网络 `LOCALHOST-2023-05-01-CPU-quant`，int8 权重 31,841,856 字节，254 层，词表 15000×512，`SupportedBatchSizesAndSequenceLengths {1: [128]}`。TextInputCore 功能开关 `transformer_lm_for_keyboard_english_input`、`InlineCompletions`、`inline_completions_everywhere` 都开。iPhone 15 Pro 上这类网络跑在 ANE；13T 上（正确地）跑在 CPU。内联补全是束搜索，最多 32 次前向。
- 其它带超时的 kbd 路径（都不在普通打字路径上）：贴纸候选 200 ms（`TIStickerCandidateGenerator`，只在 `shouldOfferStickers`，即 App 支持贴纸时，如信息）；`-[TITransientLexiconManager getOnce:]` 最长等 30 s（`dispatch_time` 0x6FC23AC00 ns），只由 kbd 的词库服务 `-[TILexiconDataHandler requestLexiconFromBundlePath:completionHandler:]` 调用（kbd 主程序 0x100008300，IDA）；Hide My Email/自动填充凭据各有超时，只在对应输入框。

### 2.3 一句话机理
按一个字母 → UIKit 发起一次自动更正/预测请求（延后任务占住键盘任务队列）→ kbd 在 CPU 上跑整型变换器（预测、候选打分，外加内联补全的最多 32 次前向）→ 回复前下一个键排队。A 轮 CPU 又被钉在 2.0/1.0 GHz，于是“每键约半秒、像不到一帧”。

## 3. 修复（本键交付）

### 3.1 kbd 侧工作量：关掉内联补全（数据修复，默认开启，可在设置里恢复）
- 偏好 `com.apple.keyboard.preferences` `KeyboardInlineCompletion = false`。效果：kbd 的每次自动更正请求不再跑内联补全的束搜索（操作 0x10/0xc8），预测栏、自动更正、拼写都保留；用户想要可在“设置 > 通用 > 键盘 > 行内显示预测”打开。不碰任何苹果二进制、不改签名与信任缓存，不影响稳定性。
- 现成种子用 `apply-keyboard-prefs.py`（新工具，见第 4 节）；以后新做的种子用 `make-phone-seed.diff`（`--keyboard-prefs` 默认带上这一项，`--keep-inline-completions` 可不带）。
- 实测：对 `final/s1-metal2-wallpaper.dbrdisk` 做过一次副本（输出在草稿目录，非交付物），回读 `com.apple.keyboard.preferences {KeyboardInlineCompletion: false}`，带属主只读挂载核对属主 501、权限 0600（与原有键盘偏好文件一致）。

### 3.2 CPU 频率
- A 轮的 1986 MHz 由 sysfix/cpufreq 负责（SCMI BUSY 不再整轮关闭）。这一项对打字的影响和 3.1 叠加，两者都要。

### 3.3 探针 v2（我们自己的代码，替换 `phone-keyboard-trace.patch`）
- `kbtrace/phone-keyboard-trace.patch`（对原始 xnu-10063.141.1 `bsd/kern/kdebug.c`，新增 `bsd/kern/pagani_kbtrace.h`），文件名与 series 里的一致，直接替换内容即可。
- 变化：`kdebug_trace64` 把 arg2 也交给 `pagani_kbtrace_note`；计时改为微秒；新增计数 `ac`（0x2bae008c/90）与 `kop`（0x2bae0034/38）；新增成对计时 13 组：App 侧 `dn up in ac cand task`，kbd 侧 `ksync kin kac kcand khit kic klm`；每组 完成数/平均ms/最大ms/≥100ms 次数/≥400ms 次数，未配对的开始 `q<n>`、丢失 `l<n>`。汇总行在原格式后追加 ` ac a/b kop c/d`，另起一行 `kbt <进程>[pid] lat …`。每进程每组排队 8 个开始（多了丢最老的并计 `l`），全部静态 11,720 字节，锁内只做常数时间的环操作。行长：汇总最长 273、App 计时行最长 298、kbd 计时行最长 349 字节（缓冲 400，超长时 BootProgress 自己拆续行）。
- 主机测试 `test-phone-keyboard-trace.py`（5 项全过）：①头文件单测（-O0/-O2，ASan/UBSan，内核告警集加 `-Wimplicit-fallthrough`）；②补丁在原始 kdebug.c（sha1 7fac08e1…，由树里已打 v1 的文件反向应用 v1 得到，树只读）上干净应用且头文件一致；③从补丁后的 kdebug.c 切出函数在桩环境跑完整场景（含 kbd 操作号、`ac` 配对、ktrace 复位后自恢复）；④用构建自己的编译记录 `kdebug.o.json`（`tools/real-compile.py`，目标文件丢弃）真实编译：0 错误，14 条告警与 v1 完全相同（都是 xnu 自身的枚举/函数类型转换宏告警，没有新增）；⑤用 ipsw 从 21H461 的 UIKitCore/TextInputCore 重读 12 个埋点的调试号与操作号。

## 4. 交付物与接线（主线合入时做，本键没动共享文件）

| 文件 | 用途 | 合入方法 |
| --- | --- | --- |
| `kbtrace/phone-keyboard-trace.patch` | 探针 v2 内核补丁 | 覆盖 `UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-keyboard-trace.patch`（series 不变）；同时可覆盖 `工具/phone-keyboard-trace/` 的同名补丁与头文件。注意 TREE 里的 kdebug.c 已打 v1：重编前构建系统要从原始源码重新打补丁（与 v1 合入时同样处理） |
| `kbtrace/pagani_kbtrace.h`、`test_kbtrace.c`、`harness.c`、`make-patch.sh` | 头文件（与补丁内逐字节相同）、单测、桩测试、由“原始 + 改后”重生补丁 | 放进 `工具/phone-keyboard-trace/` 或留在此处 |
| `test-phone-keyboard-trace.py` | v2 主机测试 | `python3 -I sysfix/keyboard/test-phone-keyboard-trace.py` |
| `apply-keyboard-prefs.py` | 把键盘偏好写进**现成**种子副本（不重做种子）；`--check` 只读查看 | 在最终种子（音频/触感叠加之后）上：`python3 -I sysfix/keyboard/apply-keyboard-prefs.py --trust 最终种子-owner-trust.bundle 最终种子.dbrdisk 最终种子-kb.dbrdisk`，再把 `A830_PREBUILT_SEED`/`A830_OWNER_TRUST` 指向新文件。偏好是数据，信任包不变 |
| `make-phone-seed.diff` | 新做种子时 `--keyboard-prefs` 默认带 `KeyboardInlineCompletion=false`，`--keep-inline-completions` 关闭 | `patch -p1` 应用到仓库根（已 `--dry-run` 通过） |
| `re/` | 逆向摘录与脚本（`fn.py`、`grepfn.py`、`idaq.*`、`ida_mgentry.py`、各 `*.s`/`*.txt`） | 只在本地，苹果派生内容不外传 |

启动参数建议（早上的包）：把 `pagani-kbtrace=3` 换成 `pagani-kbtrace=1`（16 字节）。不再需要 `=3`。

## 5. 早上最省事的检查（区分“探针拖慢”与“真卡”）

前提：早上的包带探针 v2、`pagani-kbtrace=1`、调速器修复、偏好修复（内联补全关）。

1. 开机后 60 s 内进设置，在顶部搜索框慢速打 `hello world how are you`（约 25 键），停 5 s；再下拉 Spotlight 打同一句，停 5 s；然后长按取日志（零翻转）。
2. 用户只回答一句：顺不顺（和 iPad 身份时比）。
3. 读日志里的 `kbt … lat` 行（设置是 `Preferences[…]`，Spotlight 是 `Spotlight[…]`，kbd 是 `kbd[…]`）：
   - `ac` 平均 < 80 ms、`up` 平均 < 30 ms，而用户仍说卡 → 卡在显示/合成（看同时段的 J 行，`gap` 主导）或触摸上报，不在键盘栈。
   - `ac`/`kac` 平均 ≥ 150 ms 或 ≥400 ms 次数 > 0 → kbd 计算是瓶颈；看 `klm`（语言模型）与 `kic`（内联补全，应为 0 次，若不为 0 说明偏好没生效）。下一步可再关 `KeyboardPrediction`（预测栏）做对照。
   - `up` 有 `q`（排队未完成）且 `ksync`/`kin` 首次很长 → 第一次就绪问题（第 0 表 #4）。
   - `task` 平均/最大大 → App 主线程自己慢（Spotlight 搜索刷新等），与 kbd 无关。
4. 只想判断 `kbtrace=3` 是不是元凶：同一张包把参数里的 `pagani-kbtrace=1` 去掉再跑一次让用户打字；顺了就是探针、仍卡就是真问题（v2 在 `=1` 下每键只多几次立即返回的系统调用，比 `=3` 少得多）。
5. 不要再用 `F FPS … SLOW` 判断打字快慢：没有触摸的时候它记的是空闲动画。

## 6. 风险与未决
- 关内联补全是用户可见的功能取舍（灰色行内补全消失）；有设置开关可恢复。若 v2 显示 `kac` 很小，可在下一版种子用 `--keep-inline-completions` 恢复。
- 第一次用键盘约 27 s 的停顿原因未定；可能与每次开机 Data 全新（键盘动态词典、模型缓存都要重建）有关。若 v2 证实是 `ksync`/`kin` 首次耗时，后续可考虑在种子里预置 kbd 缓存（需要一次真机生成的缓存，今晚做不到）。
- 探针 v2 静态占用 11.7 KB、每个放行埋点多一次加锁与常数时间环操作；kbd 的语言模型埋点（0xc9 等）每键可能几十到几百次，仍是微秒级。
- 触感工作流若打开键盘触感（`KeyboardVisceral`），不会卡打字（全异步），但在 `Actuator` 设备与闭环配置就绪前不会有感觉。
- 不要给 ADT 加 `ane-type`：MobileGestalt 会因此报有 ANE，LanguageModeling 会去加载 `_ane.espresso`，而本根卷没有 aned。
