# 上游合并方案：复核与分阶段计划（2026-10-31）

复核对象是同目录八路调研（`adreno740-delta.md`、`adrenometal-delta.md`、`ipados27-parity.md`、`pagani-a830.md`、`nongpu-upstream.md`、`widgets-keyboard.md`、`app-launch-jitter.md`，以及只存在于工作流回复里的亮度报告）。

全程没有碰手机，没有运行 adb 或 fastboot，没有修改我们的源码树和上游克隆，没有提交。试验都在草稿目录 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/review/`。

证据标记：

- **【复核实测】** 本次我在本机重新跑出来的结果。
- **【读码】** 我读源码确认。
- **【逆向】** 我读 21H461 共享缓存的反汇编或反编译结果确认，地址都是未滑动地址。
- **【日志】** `工具/gpu-test-loop/runs/` 的真机日志。
- **【报告】** 原报告的结论，我没有独立复现。
- **【推测】** 没有直接证据。

工作量都是推测。这份文件里没有任何一项在 13T 上运行过。

---

## 0. 复核结论一览

1. “能合并”成立，但对象要说清：能合的是 AdrenoMetal 和翻译层改动，以及思路可移植的少量内核优化。上游没有 A830 驱动，也不存在换模块这回事。
2. 试合并规模我独立复现了，7 个文件 27 处、6 个文件 21 处，数字完全一致。工具链可行。
3. “用户态可单独升级”要收窄：AdrenoMetal 前端、插件、桥接可以，Turnip 后端不能整体替换。
4. 第一批里的“ABI 头同步”应该拿掉：第一、二步都不需要它，改它还可能让着色器包失效。
5. 亮度的 IOKit 类名、属性名有扎实逆向证据，字节序缺陷也确认。不确定的只剩现场 DSI 状态。
6. 小组件的 bindfs 假设比原报告证据更强，也比它写的“把握高”更弱：后三环我在 21H461 上读到了，第一环没有走通。
7. 键盘报告证明的是“触感不卡主线程”，没有证明“触感本身不慢”。用户说的正是“回馈慢”，而完整包是第一次开马达。
8. 最大的实际阻塞不是代码，是两轮完整包都没有日志。根因之一是 30 分钟定时复位，这个改一个参数就能缓解。
9. iPadOS 27 不换的结论成立。

---

## a) 被推翻或需要修正的结论

### a.1 “上游某优化我们没有”（在 `Adreno830` 里亲自核实）

**成立的【读码】：**

| 结论 | 核实位置 |
| --- | --- |
| 池清零在 Memory 锁内 | `NativeAdrenoUserClient.cpp:286-288` 取锁，`NativeAdrenoMemory.cpp:182` `bzero` |
| 连续搜索无上限无退避 | `NativeAdrenoUserClient.cpp:296-299` |
| 别名查两遍 | `NativeAdrenoVm.cpp:460-464` |
| 等围栏入口和出口取连接锁 | `NativeAdrenoPublish.cpp:672,684` |
| 故障记给所有有在途作业的连接 | `NativeAdrenoPublish.cpp:1204-1205` |
| `native_adreno_vm_now_us` 真机恒空 | 只有 `tests/test_vm.cpp:256` 赋值，所以 VM 耗时计数恒 0 |
| 整段关抢占 | `NativeAdrenoCpu.cpp:52-64` |

**需要修正的：**

1. **`865495f`（自有 BO 走 IO 一致）不是简单的“已有，不需要”。** 代码确实有等级 2（`NativeAdrenoVm.h:43` 附近），但完整包的日志一直是 `coh 1`【日志】，启动参数里也没有 `pagani-gpu-coherent=2`。所以准确说法是“机制已有，从未启用，收益没评估过”。自有 BO 现在是写合并（`NativeAdrenoUserClient.cpp:303-305`）。上游这个开关同样默认关。这是一个没人评估过的选项，不是当前缺陷，放到第 3 阶段按数据决定。

2. **`2c40ae9`（已在环上的同步不等 CPU）不是“上游有、我们没有”。** `adrenometal-delta.md` 5.3 节这样写，第 3 步还把它列成候选。我们的默认行为已经是跳过：`UniXNU/patches/mesa/turnip-darwin-kmd.patch:1264-1272`，`cpu_waits` 默认 false，已解析的同步直接 `continue`【读码】。差别只剩未解析的同步：我们等完成，上游等提交。这一项从候选里去掉，只留这个小差别。

3. **池清零移出 Memory 锁的收益要打折。** `BoAlloc` 全程持连接锁，移出 Memory 锁只解开跨进程串联。`keep_room` 的 64 MiB 分配发生在 CA 渲染线程上，那次等待是渲染线程自己的，这个改动救不了它。所以它值得做，但不要指望它消除 backboardd 自己的 ±64 MiB 卡顿。真正针对这一点的是翻译层的块增长（`0020-growing-memory-blocks`，第 2 阶段自带）和空块迟滞。【读码，后半句推测】

4. **ABI 头同步应从第一批删掉**（`adreno740-delta.md` 第 8 节第 6 项）。理由见 a.2 第 3 条。

5. **主机测试结果比报告更干净。** 我把 `a830-min-port.diff` 对应的试验副本放到旁边带 `UniXNU` 链接的目录重跑，**44 通过、0 失败、1 跳过，51 个内核对象通过**【复核实测】。报告里的 1 失败确实只是路径问题。这只说明能编译、不破坏用例，耗时收益仍然没测。

6. **`ArmCpu` 的 `parkCore` 没有 `ipados27-parity.md` 第 4 节写的那么紧迫。** `PaganiArmCpu/ArmCpu.cpp:1038-1039` 的 `wfe` 只在副核启动失败时才到达，真机八核都起来了。排到最后。

### a.2 “用户态可以单独升级 / 必须连内核一起升级”

**ABI 层结论成立【复核实测】：**

- `Adreno830/NativeAdrenoABI.h` 与上游 `5c8c145` 逐字节相同。
- 工作区 6 份副本 SHA-256 都是 `20ac9298…`。
- 与上游 main 的差异正是报告列的那些：删 `NativeAdrenoAbiMaxBoBytesShift`、`NativeAdrenoAbiMaxQueues`，加方法 17、18、两个 Query 标志和两个结构，版本号仍是 2。
- 方法 17、18 在我们这里走 `inputSize` 返回 `SIZE_MAX` 再答 `kIOReturnUnsupported`（`NativeAdrenoUserClient.cpp:574-575`），分发表到 16 为止（`NativeAdrenoPublish.cpp:895-911`）。

**但“新用户态不依赖内核的任何新能力”（`adreno740-delta.md` 3.4）说过头了，要分三层：**

| 层 | 能否单独升级 | 证据 |
| --- | --- | --- |
| AdrenoMetal 前端、插件、桥接 | **能** | 合到 `d6ca655` 的结果用**现有** ABI 头语法编译通过（`metal/cc.sh` 用 `-I Adreno830`）【复核实测】。第 1、2 步都不用动 ABI 头 |
| metal2vulkan | 能，但见 a.3 | 只依赖 Vulkan 接口，有 Mesa 补丁依赖（0020 对 `turnip-render-mode-for-attachment-memory`） |
| Turnip Darwin 后端 | **不能整体替换，只能逐项摘** | 见下 |

**上游 `turnip-darwin-kmd.patch` 换到我们内核上的语义问题【读码】：**

1. **没有提交前的 `dsb sy`。** 我们有（`turnip-darwin-kmd.patch:1229-1230`，注释写的是 Normal 非缓存命令流的写入要先完成，`pandora_dsb.py` 的结论）。上游全文没有一处 `dsb`。这是硬件现象换来的修复，整体替换会悄悄丢掉。
2. **Busy 处理不同。** 我们的 `darwin_wait_for_room`（`:1188-1210`）注释写明，等 `inflight[0]` 会立刻返回，64 轮很快耗尽，变成设备丢失。上游（`:1586-1592`）仍是等最老一个，否则 `sched_yield`。我们的内核有准入控制，非渲染客户端只有 1 个保留作业（`NativeAdrenoSubmit.cpp:41-48`），Busy 比上游常见得多。换过去至少是空转，最坏回到旧问题。
3. **我们自有的接口会丢：** `tu_darwin_memory_stats`（`MemoryReport.h` 用）、`coherent` 映射选项、`TU_DARWIN_CPU_WAITS`，以及四个 `turnip-a830-*` 补丁的上下文。
4. **BO 缓存假设系统内存。** 上游缓存“没有字节和条目上限”，靠内存压力回吐（补丁 `:351-353`）。我们是固定 1024 MiB 池，还有窗口上限。`MemoryPressure` 这个信号也不反映池满。

所以第 3 阶段的说法应该是“把上游后端的某几项移植进我们的补丁”，不是“升级后端”。

**第 3 点影响决策：ABI 头同步不是免费的。** `AGENTS.md` 要求头文件逐字节一致，Turnip 也包含它。而 Turnip 的 `pipelineCacheUUID` 是 `BLAKE3(LC_UUID of libvulkan_freedreno.dylib, …)`（`build-userspace/make-shader-cache-a830.py:12-14`）。头一动就要重编 Turnip，只要 `LC_UUID` 变了，种子里的着色器包就全部失效，界面每条管线现场翻译，这正是 PROGRESS 第 473 行那次回归。第一、二步都不需要这个头，就不要动它。

### a.3 试合并冲突规模与工具链

**成立【复核实测】：** 我用当前工作树重新做快照（树与 `116beb1` 完全相同，29 个文件，+2282/-105），独立重跑：

| 目标 | 冲突文件 | 冲突处 |
| --- | --- | --- |
| `origin/main`（`831a9dc`） | 7 | 27 |
| `d6ca655` | 6 | 21 |

- 合并结果 `metal/resolved2` 与 `repo2` 无差异，前端和插件在 `arm64-apple-ios17.0`、`-Wall -Wextra -Werror` 下语法编译零错误。
- 容量日志核对过：`atlas2/slot4.log` 87 MiB 通过，`atlas/slot4.log` 54 MiB 失败。
- `cargo +1.98.0` 可用，`aarch64-apple-ios` 目标已装。
- `git -C metal/m2v branch -a --contains 624bab39` 只有本地 `upstream-pin`，确实不在上游分支上。

**工具链小修正：** `llvm-as` 不在 `PATH` 上（`which` 找不到），在 `/opt/homebrew/opt/llvm/bin/`，第一步就要用（`shaders/scanout_convert.ll`），构建脚本要显式指定。

**需要修正的：**

1. **“翻译层不换”低估了第一步。** `720ef1c` 确实不变，但桥接补丁从 16 个变成 37 个（`git ls-tree` 核对），多出 21 个。其中 0025–0037 全是绘制路径改动（复用绘制状态、提前重置命令池、`0036-calibrate-off-the-commit-path` 等），加上默认开启的编码线程。这些都没有上过 A830 真机。用户目前最满意的就是流畅度，所以第一步是**有实质风险的改动**，不是低风险过渡。它必须单独成包，并留回退。

2. **第一步的主机门槛还没跑全。** 报告列的 `check-adreno-userspace-host.py`、`check-env-seeds-a830.py` 没有在合并结果上跑过，`make-shader-cache-a830.py` 也没改、没跑。“已验证的中间落点”只覆盖第 2.4 节那张表。

3. **QoS 补丁不需要手工改。** `app-launch-jitter.md` 6.5 节写“行号不同，手工改”。我把 `0023-quality-of-service-on-ios.patch` 实际打到当前翻译层（`build-userspace/tree/.cache/gpu-reference/metal2vulkan`）的副本上，`patch -p1` 干净应用，改 `exit_guard.rs` 第 767、777-778、783、904、909、924-925 行，共 6 处 `cfg`【复核实测】。到 `d6ca655` 的 37 补丁副本同样干净。**编号陷阱：** `d6ca655` 里的 0023 是 `heap-texture-size-a-multiple-of-its-alignment`，与 main 的 0023 不是一回事，所以 `adrenometal-delta.md` 3.1 节“第一步可单独试 0023”必须按补丁名取，并重新编号。

4. **`app-launch-jitter.md` 的 QoS 优先级自相矛盾。** 摘要说下一包做，第 7 节放到 P1。结论：放第 2 阶段，与合并一起重做一次着色器包，不要做两次。

5. **“只移植 `0f11e64` 的 `holdImport`”和第一步合并是两个互斥的方案。** `adc18c5`（在 `d6ca655` 之前）已把“保持 1 秒、最多 64 个”改成“表面活着就留着，无数量和时间上限”，`547312c` 又修了它带来的 backboardd 崩溃。走第一步就不用单独移植。但我们是固定窗口，App 只有 124 MiB，无上限不安全。`surfaceBuffer` 失败时裁剪重试是必须的，这一点原报告写了。

6. **Pagani “明显更流畅”不是翻译层证据。** Run 88 同一轮把频率从 Run 79 的 222 MHz 提到 389 MHz，还换了 BO 和页表（`STAGE6.md:1313,1345-1349,1384`）。他的基线是最高约 15 FPS，和我们不可比。`adrenometal-delta.md` 第 0 节第 8 条不应当作流畅度收益的依据，只能说明能跑。

**已排除的怀疑：** `d6ca655` 没有 Pagani 报告提到的“每 2 ms 暂停渲染线程”问题，`repo2/NativeBridgeMetalFrontend.mm:225-227` 的采样器仍是默认关。`METAL2VULKAN_QUEUE_PRIORITY` 在 37 补丁的翻译层里搜不到，是空操作。第一步的 GMEM 默认值没变（仍是 `TU_DEBUG=nocb`，`repo2/NativeAdrenoMetalPlugin.mm:314`）。第一步的 21 个新补丁我逐个看了头部或搜索了 Mesa 依赖，没有发现硬依赖。

### a.4 亮度：IOKit 类名、属性名有没有逆向证据

**有，而且扎实。** 地址我用 `ipsw dyld a2s` 独立解析过【逆向】：

| 报告说法 | 核实结果 |
| --- | --- |
| `findBacklight` `0x1b62a0ca0` | `-[CBDisplayContaineriOS findBacklight]` |
| 匹配条件 | 反编译里就是 `{IOPropertyMatch: {backlight-control: kCFBooleanTrue}}` |
| 一致性检查 | `IOObjectConformsTo(service, "AppleARMBacklight")` |
| `setupInternalModules` `0x1b62a3d40`、`DisplayCreateWithID` `0x1b62accc0`、`...LegacyOnThread` `0x1b62c3964`、`CBEDRServerStartServer` `0x1b6256fd4` | 全部对得上 |
| 字符串 | `IODisplayParameters`、`new-backlight-architecture`、`FadePeriod`、`backlight-level`、`no backlight found` 都在 CoreBrightness |

**字节序缺陷确认【读码】：** `PeripheralIO.h` 写 `0xff000051u|((level&255)<<8)|((level>>8)<<16)`，线上是 `51 低 高`。设备树 `51 0d bb`、`51 0f fe`、`qcom,mdss-dsi-bl-inverted-dbv` 都是高字节在前。

**放行补丁确认：** `zzwb` 在 `arch/arm64` 的 series 里，`IOUserClient.cpp` 工作树已打上，全驱动树只有 `PaganiTorch`、`PaganiAudio`、`Adreno830` 设了 `NativeSandboxGrantProcess`，`PaganiPanel` 没有。

**小修正：**

1. 服务不存在的原因少列了一条：`Stage7Probe.cpp:107-112` 还要先过 `AppleARMPlatform` 加载和类注册，失败同样没有服务。原型的 `logState` 在 `start` 里，走不到这一步就没有记录，建议把这两步的结果也写进 U 记录。
2. 首次发布 `backlight-control` 的 CA 卡死风险是这份方案里最危险的单点（sheng int-236）。它和其他修复同包时，必须有一个只去掉 `pagani-panel` 的回退镜像。
3. `brightness.md` 文件不存在，全文只在工作流回复里，原型只在草稿目录。见第 d 节。

结论：方案可用。“DSI_CTRL bit0 为什么读到 0”只能上机定，这点报告说得对。

### a.5 小组件和键盘根因的证据强弱

#### 小组件：把“把握高”改成“中高”，并写清哪一环有证据

报告的表写“高”，第 7 节又写逐步路径没走通，两处不一致。我把这条因果链逐环核了：

| 环节 | 证据 | 强度 |
| --- | --- | --- |
| 我们的 bindfs 无 `vfs_vget`，抹掉两个能力位 | `bind_vfsops.c:420,424,551-561`，`.vfs_vget` 不存在 | 确认【读码】 |
| `fsgetpath` 对 bind 挂载失败 | `mount_lookupby_volfsid` 要 `MNTK_PATH_FROM_ID`（`vfs_subr.c:7580`），否则 `ENOTSUP`（`vfs_syscalls.c:13604`） | 确认【读码】 |
| `_kCFURLFileIDKey` 被 LS 使用 | `-[FSNode getFileIdentifier:error:]` | 确认【逆向】 |
| **注册时为什么丢平台或扩展点** | **21H461 上没走通** | **只有上游注释（iPadOS 27）** |
| 插件平台来自 Mach-O 解析 | `_LSAddBundlePlugins` → `FSNode` → `open` → `_LSParseLoadCommands`（`0x187d9e94c`），写 `LSExecutablePlatformKey`（`0x187d6af84`），`buildBundleData:` 写入偏移 0x14（`0x187d2d2d0`） | 确认【逆向】，新证据 |
| 平台决定能否找到扩展点 | `_LSPluginCheckExtensionPoint` 取偏移 0x14 的平台，调 `_LSExtensionPointFindWithStringID`（`0x187cb934c`、`0x187cb9364`） | 确认【逆向】，同一偏移是同一字段属推断 |
| 找不到就过不了校验 | `_LSPluginFindWithPlatformInfo` 的块里先做这个检查（`0x187cb91d0`），失败跳过 | 确认【逆向】 |
| LS 报错时 chronod 判 restricted | `sub_1D1631628` 出错分支返回 `requireValid` 本身，然后打 `Ignoring restricted extension` | 确认【逆向】 |

结论：**后半段在 21H461 上确实存在，比原报告更扎实。前半段仍是上游一句注释。** 另有四点要改：

1. **“无平台”本身不是死因。** `_LSPluginIsValid` 对平台 0 是放行的（`0x187cb98c0-c8`：`orr w8,w24,#2; cmp w8,#2`）。真正卡住的是用平台去查扩展点。这与上游注释不矛盾，但报告的表述不够准。

2. **“它的设计和我们一样”不准确。** sheng 的视图下层是**密封的 OS 卷**（`zzzz-sheng-ipados27-bringup.patch:284-287`，“mounted while /private/var still resolves to the OS volume”）。我们是先把应用**复制进 Data**，再从 Data 挂载（`phone-system-apps-view.patch`：`native_staged_apps_copy` 之后调 `native_system_apps_show`）。`bindfs_vget` 的逻辑不依赖这一点，`VFS_VGET` 走 Data 卷，`fsgetpath` 对 `ENOENT` 还有联合回退（`vfs_syscalls.c` 的 `CONFIG_UNION_MOUNTS` 分支）【读码】。但上游的验证结果不能直接算到我们头上。

3. **“restricted before first launch” 是另一条分支，不是同一因果。** 我特意查了，因为我们的 Data 每次开机全新，如果它针对“应用没启动过”就是新根因。结果是这条分支对 `isInternal` 的扩展直接放行（ChronoCore `sub_1d18dcd90`，`0x1d18dceb0`）【逆向】，系统应用的扩展应该走不到。`isInternal` 的取值我没追，所以只算“大概率无关”。

4. **之前的 RenderBox 结论要降级。** “画成黑的”从没有真机证据，D、D2 的日志里 `chronod`、`WidgetRender`、任何 `Widget` 字样都没有【复核实测 grep】。主机复现证明的是那个缺陷存在，不是它就是真机原因。修了它现象没变，就是证据。

**新增验证：** `bindfs_vget` 三处改动我用 `bind_vfsops.o.json` 里的真实编译参数，在 xnu-10063 上试编通过（`review/bindfs/bind_vfsops.o`，只有 `kalloc_data` 宏的两条既有类型告警）【复核实测】。`ipados27-parity.md` 第 6 节“都没有编译”这一条，这个补丁可以划掉。注意 `MNTK_PATH_FROM_ID` 会作用于所有 bindfs 视图（守护进程、壁纸、键盘、音频、控制中心），不只 `/Applications`，副作用没评估。

**“先单独上 bindfs”和用户的“修完再测”冲突。** 解决办法是第 1 阶段：bindfs 和其他非 GPU 修复同包，但这个包里**不动 Metal**。这样小组件现象变化时，归因只有 bindfs 一个来源。

#### 键盘：反馈路径没有被排除

`widgets-keyboard.md` 2.2 节和 `sysfix/keyboard/NOTES.md` 第 6 条的结论是“触感和音效不会卡主线程”【读码，我没有推翻】。但这只回答“会不会拖慢文字”，没有回答“反馈本身快不快”：

- 用户原话是“按一个键都要**回馈**好久”。
- `_UIKBFeedbackGenerator` 是 iPhone 身份才走的路径（`_isVisceralEnabled` 要求 MG `ipad` 为假）。iPad 身份下“键盘完全正常”，这是一个确定差异。
- 完整包是**第一次**开 `pagani-haptics=1 pagani-haptics-ios=2`。触感经 CoreHaptics、音频 HAL 的 `Actuator` 设备到马达，起播前还有 8 ms 预填充和 4 ms 轮询（`PaganiHaptics/README.md`）。引擎按需启动的耗时从未测过。
- 上一轮的抱怨是“打字卡”，这一轮是“回馈慢”，两者未必是一回事。

这是【推测】，但验证只要几十秒。设置里关掉键盘反馈的声音和触感，看变化。这一步应该排在所有键盘分析之前，而不是报告里 5 步对照的第 4 步。

其余结论我核了 `usesMLTapTyping`（`0x1d93146d4`：要求 `userInterfaceIdiom==0`、非硬件键盘、非分离、非浮动）、`EnableMLTapTyping` 字符串在 TextInputCore，都成立【逆向】。但“ML 点按是主因”仍是推测，每键耗时没有任何数据。**不要在没有 `kbt` 数据前默认关掉它**，它影响打字纠错。

`KeyboardInlineCompletion=false` 和调速器修复都已在完整包里，用户仍说慢，说明这两项不是全部原因。

#### emoji：修法可以带，根因说法要降级

- 链路确认【逆向】：`-[UIScreen _ensureComputedMainScreenDPI]` 读 `main-screen-pitch`，答案为空或非数字时两个缓存值保持 0，**没有兜底**。`_itemFramesForBoundingSize:` 用 `_nativePointsPerMillimeter` 算位置和尺寸。
- `getDotPitch` 无 `dot-pitch` 属性时返回 `kIOReturnNotFound`【读码】。
- 但按这条链，换算系数是 0 或负数，按钮**尺寸塌到接近 0**，左按钮中心贴到 0。用户说的却是“有点小错位”，图标显然还看得见。两者只有在图标不被裁剪时才勉强对得上【推测】。
- 所以这是“便宜、方向正确、值得带上”的修改，不能写成“已找到根因”。**一张照片就能判断，到现在没有人看过实物。**

### a.6 换 iPadOS 27 的结论

**不换的结论成立。** 我核实了：

- `profiles/os/ipados-27.0.1-24A446/profile.toml` 第 4-7 行明写没有 Darwin 26 源码，内核是 12377 加调用；`--allow-missing` 在 `extra_args` 里。
- `arm64e.md` 列的受支持设备只有 sheng，状态是“开发中”。文档同时写着“只对一个目标有效的改动不予合并”，所以上游也在维护 17。
- `patches/xnu` 与我们相比只差 `native-arch-timer-frequency.patch`（我们多的）。
- `task_get_gpu_role` 在我们的 XNU 工作树 `osfmk`、`bsd` 里搜不到。

**小修正：**

1. 第 1.5 节“仍是 32 槽位”误导。`NativeAdrenoMaxClients=32` 是常量，4 GiB 表实际只有 12 个窗口有效（`NativeAdrenoPublish.h:23-41`）。`adreno740-delta.md` 写的 12 个是对的。
2. “3–5 周”是推测，上游没有 13T 的任何数据，建议只说“几周以上，含大量上机”。
3. 对用户要说清一句：**不换系统也能拿到这批改动里有用的部分**，这正是他问题的答案。

### a.7 其他

- **命令行上限是 1000，不是 1023。** `nongpu-upstream.md` 11.2 节写 981/1023 已过期（`UniXNU/scripts/unixnu/payload.py:35-36`，PROGRESS:472）。完整包 986 字节。`pagani-kbtrace=1` 要 17 字节，确实放不下。`pagani-audio-mx=1` 是只读诊断（`PaganiAudio/README.md:27`），去掉它正好够，不必急着上 `PE_builtin_boot_args`。
- **系统内存 BO 的两份报告意见相反。** `adreno740-delta.md` 说唯一能明显省内存，`app-launch-jitter.md` 说不建议。裁决：现在不做。`sc` 从未出现，池最高用到 921/1024，没有证据说明池是瓶颈。而且 10063 上还缺 `pmap_sync_wimg` 那段。
- **`pagani-gpu-status=2` 和 `=3` 互斥。** 抖动要 MEM、IRQ 行，小组件要客户端报告行。第 1 阶段保持 3，把要量的数字放进 `gpu meter` 行（`AGENTS.md` 规定不加新的每秒行）。

---

## b) 推荐的合并方案

原则：

1. **按用户记忆里的“修完再测”，不出零碎包。** 但把“会让着色器包失效的改动”和“不会的”分开，这样第 1 阶段不必动用户满意的流畅度。
2. **每个阶段都带回退镜像。**
3. **任何写手机存储的动作都要用户先同意。** `测试清单.md` 明写“不往手机存储写数据”。

### 阶段 0：不上机，先把现场和事实拿到（半天）

**改什么：**

- 把草稿目录的成果搬到不会清空的位置（清单见第 d 节末）。
- `624bab39`、`0dfdbcd5` 做 `git bundle`。
- 给 `UniXNU`、`UniXNU-Pagani-Drivers`、`Adreno830` 的工作树留本地快照（要用户同意才提交）。
- 请用户做三个不用重编的小事（见第 c 节末尾）。

**主机验证：** `git bundle verify`，核对搬出文件的校验和。

**用户可感知变化：** 无。但三个答案能省掉大量猜测。

**风险：** 无。

### 阶段 1：非 GPU 修复整包，不动 Metal 和着色器包（1–2 天，上机 1 次）

**不重编插件和 Turnip，种子里的着色器包保持有效。** 这是本阶段最重要的约束，流畅度风险最小。

| 类别 | 改动 | 来源 | 目标症状 |
| --- | --- | --- | --- |
| XNU | `bindfs_vget` 三处 | sheng，已试编 | 小组件 |
| 驱动 | 亮度原型（`PaganiPanel`、`PeripheralIO.h`、`Stage7Probe`），加 Stage7 两步进日志 | 自写 | 亮度 |
| 驱动 | `getDotPitch` 答 460 | 自写 | emoji |
| 驱动+XNU | 调速器：`kTouchMs` 从 100 提到 200–300，加应用启动提升，都做成启动参数 | 自写，策略参考 sheng | 抖动，键盘 |
| 内核 | 调用计量、VM 时钟接线、池清零移锁、连续搜索限 128 KiB、别名只查一次、分段关抢占 | 上游思路。池清零、连续搜索、别名三项已试验 | 抖动，定位 |
| 诊断 | `pagani_diag_watched` 加 `chronod`，小组件扩展普查 | 自写 | 判读 |
| 参数 | `pagani-reset-after` 从 1800 提到 5400，去掉 `pagani-audio-mx=1`，加 `pagani-kbtrace=1` | — | 拿到日志 |

**不做：** ABI 头同步、QoS、`holdImport`、`EnableMLTapTyping`（等 `kbt` 数据）、kobject 放行（没有日志说需要）。

连续搜索的退避要改成时间（上游 5 s），试验版用的是分配次数。`finishPooled` 失败路径要补清理。

**主机验证：**

- `python3 -I tests/run-host-tests.py` 全过（含 51 个内核对象）。
- `Test/panel-brightness.cpp`、`Test/panel-power.cpp`、`scripts/test-peripherals.py`。
- `scmi-perf` 主机测试加调速器用例。
- 完整内核链接和预链接（`make-a830-package.sh`），亮度报告明确说这一步没做。
- 命令行不超过 1000 字节，载荷过 `unixnu payload` 预算（上次只剩约 8.2 MB）。

**回退镜像两个：** 新内核去掉 `pagani-panel`（查 CA 卡死）；原 `稳妥回退包`（查内核）。

**用户可感知变化（都是预期，不是保证）：**

- 亮度滑块生效，前提是 DSI 命令能发出去。
- 系统应用的小组件出内容，前提是 bindfs 是真因。
- emoji 排可能归位。
- 打开应用的抖动可能减轻。
- 键盘可能好一些。
- **这次测完能拿到日志**，下一步才不用猜。

**风险：**

- CA 卡死（中，有回退）。
- bindfs 能力位影响其他视图（低到中，未评估）。
- 调速器更热（低，温控上限保留）。
- 同一包改动多，但各自有独立现象，归因问题不大。

### 阶段 2：AdrenoMetal 合到 `d6ca655`（2–3 天，上机 1–2 次）

**改什么：**

- 39 个提交，加 `547312c` 前端部分和 `dfe4eeb`，按 `resolve2.py` 解 21 处。
- 我们的 0017、0018 排到 37 个之后，加 QoS 补丁（按名取，重新编号）。
- 语义修复 S5、S7、S8、S9、S12、S14（`gpuCoreCount=6`）。
- `surfaceBuffer` 失败时裁剪重试，给持有导入定上限。
- `llvm-as` 路径、`NMB_SCANOUT_AIR`、重做 `m2vpack`。
- 留 `no-encode-thread` 对照种子。

**不动：** 内核、ABI 头、Turnip、翻译层基线。

**主机验证：**

- `adrenometal-delta.md` 2.4 节整张表重跑，尤其 `texture_atlas_host` 三个 slot。
- `coreimage_host`、`renderbox_host`、`surface_import_host`。
- **这次必须补上** `check-adreno-userspace-host.py`、`check-env-seeds-a830.py`、`make-shader-cache-a830.py` 的严格消费者检查。

**用户可感知变化：**

- 一批 App 和界面画对（Core Image、图片、曲面细分、管线反射）。
- 小组件第二层的缺口（`DVMLinearTextureLayout`、`b3a8`、`fb2070d`）随之补上，能不能解决显示问题未知。
- 流畅度可能小幅提升，也可能回退。

**风险：** 中。21 个新翻译层补丁和编码线程都没上过 A830。回退就是第 1 阶段的镜像。

### 阶段 3：按数据决定（每项 2 天到 1 周，各自上机）

只有第 1 阶段的计量和日志指向它们才做：

| 项 | 触发条件 |
| --- | --- |
| 内核第二批：等围栏免锁、导入 `prepare/complete` 出锁、`mapLocked` 维护出 VM 锁 | 计量显示锁等待 |
| 故障只记给 CP 停留作业的连接 | 稳定性项，可随时做，有 `h` 非零就提前 |
| 把上游 Turnip 的 memoryless 和 BO 缓存**摘**进我们的补丁 | MEM 行显示 backboardd 内存或分配频率是问题；先做好连续搜索限制 |
| `pagani-gpu-coherent=2` 评估 | CPU 侧 BO 读写被量出是瓶颈 |
| 翻译层到 `624bab39` | 窗口回退解决（slot 4 不低于 62 MiB），`render-mode` 补丁在 hostsim 跑通，备好 `sysmem,nocb` 对照种子。不带 `turnip-gmem-mutable-swap` |

**用户可感知变化：** 省内存、更稳，可能减少长时间使用后的卡顿。

**风险：** 中到高。翻译层升级必须单独一包，GMEM 颜色跳变要单变量验证。

### 阶段 4：现在不做

系统内存 BO、缩小固定池、按需页表和每进程页表、CP 抢占、任务优先级、方法 17/18、iPadOS 27。理由见 a.6、a.7 和 `adreno740-delta.md` 第 4 节，重新考虑的条件在该报告第 8 节。

### 并行轨道：声音、WiFi、蓝牙（与合并无关）

上游**没有新代码可合**【报告，读码抽查了启动参数】。完整包没有 `pagani-adsp`、`pagani-speaker`、`pagani-wifi`，不响、不联网是预期。按 `测试清单.md` 第二轮逐级走，每步要用户在场。一条值得带上的新事实：同样两颗 TFA9873 的 asteroids 是换 SD1 才出声，测试音在一次启动里走 SD0、SD1 两条线。蓝牙上游完全没有实现，这一项目前没有路线。

### 日志通道（贯穿全程，要用户决定）

| 办法 | 代价 | 效果 |
| --- | --- | --- |
| 定时复位延后到 90 分钟，测完长按 | 改一个参数 | 第 1 阶段就用。长按真机零翻转【日志】 |
| UFS 日志窗口（`sheng-log=1`，`unixnu` 尾部 32 MiB） | **第一次由 XNU 写手机存储，必须用户同意**；要改 `NATIVE_UFS_READ_ONLY`，panic 路径要自写 | 不依赖长按，还能接 `BootDiagExport` 拿全系统 os_log |

建议先走第一个。第二个等用户表态后单独做，先只开窗口不写，再写一块标记回安卓核对。

---

## c) 给用户的一页话结论

**能不能合并？能，但不是你想的那种合并。**

上游说的“iOS 27 GPU 驱动”不是一个可以直接换上的新驱动，也不支持我们的 A830。它是作者把另一台 A740 手机升到 iPadOS 27 时，顺手对驱动和翻译层做的一大轮改动。里面大部分和系统版本无关，所以**不用换 iOS 27 也能拿到有用的部分**。

**合并什么：**

- Metal 这层大约 40 个提交。我重新跑过，冲突 21 处，都有解法，能编译通过。它能补上一批缺的功能，比如 Core Image、部分图片格式、曲面细分，有些 App 和小组件的画面可能因此变对。
- 翻译层的线程优先级补丁，几行，能减少回调线程被抢。
- 几个内核小优化，减少进程之间互相等待。
- 上游的一处文件系统修复。**这是目前小组件最有希望的线索**，已经确认能在我们的内核上编译。

**不合并什么：**

- **不换 iPadOS 27。** 你现在抱怨的键盘、小组件、亮度、声音、WiFi 都不是 17 造成的，换了不会变好，还要把所有驱动和界面修复重做一遍，少说几周。
- **不合 CP 抢占、每进程页表、系统内存这些大改。** 它们解决的问题我们现在没有证据遇到，风险却很大。
- **不把上游的 Turnip 后端整个换过来。** 它和我们的内核有几处行为不同，其中一处是我们修过的硬件问题，直接换会退回去。
- **最新版翻译层先不上。** 另一位作者在同款手机上用它出现了颜色跳变，我们的主机测试里应用可用的显存也变少了，要等解决后单独测。

**为什么这样分：** 你现在最满意的是流畅度，这是我们自己的驱动带来的，上游这批改动里有风险的部分都不该拿它去赌。所以先修不碰 Metal 的问题，再单独合 Metal，每一步都留回退包。

**下一包预计能动的：** 亮度、小组件、emoji 位置、打开应用的抖动、键盘。这些都是有依据的预期，**不是保证**，因为到现在都没有真机日志证实。声音和 WiFi 上游没有新东西，要按原计划一步步上机打开。蓝牙上游也没有实现。

**有三件事你现在就能帮忙，不用重编：**

1. **键盘：** 设置 > 声音与触感 > 键盘反馈，把声音和触感都关掉，再打字。告诉我慢的是震动、声音，还是字出来慢。
2. **小组件：** 告诉我它是黑块、空白占位，还是小组件库里根本没有。再试着加一个“电池”或“照片”小组件，看它和天气、日历有没有区别。
3. **emoji 那排：** 拍一张键盘的照片给我。

另外，这两轮日志全丢了，因为手机 30 分钟自动重启抢在长按之前。下一包我把时间延长到 90 分钟，测完请长按电源加音量下。

---

## d) 调研遗漏了什么

1. **没有人问过用户现象到底长什么样。** “小组件用不了”是黑块、占位还是列表里没有，对应完全不同的根因。“小错位”没人看过照片。“回馈”指文字、高亮还是震动，没人确认。八路调研里最有判别力的信息，用户一句话就能给。
2. **日志问题的最便宜解法没人列。** 各报告都写“长按”或“上 UFS”。但两轮失败的直接原因是 `pagani-reset-after=1800` 到点先复位（`runs/20261031-134632`、`20261031-174545`，`HARD_RESET`/`PS_HOLD`）。第二轮的日志在调研之后才落盘，所以各报告只提到第一轮。
3. **键盘反馈路径本身的延迟**，见 a.5。`kbt` 探针也量不到触感，只有用户对照能答。
4. **调速器和键盘的关系。** 抖动报告只把 `kTouchMs` 和打开应用联系起来。kbd 的计算正好在触摸后跑，100 ms 的提频对打字同样重要，第 1 阶段一个改动可以试两个问题。
5. **着色器包失效这条约束没有贯穿到内核报告。** ABI 头同步被列成“低风险、头 2 行”，没有考虑到 Turnip 重编后 `LC_UUID` 变化的连锁后果。
6. **Turnip 后端的语义差异**（`dsb sy`、Busy 处理）。三份报告都比较了功能，没有比较这两处硬件和准入相关的行为。
7. **`d6ca655` 这 21 个新翻译层补丁的 A830 风险**没有单独评估，尤其和我们 0017（限制保留命令缓冲）与上游 0035（归还时重置命令池）的交互。补丁能打上、能编译，语义没人看。
8. **第 1、2 阶段的完整内核链接**都没做。亮度原型、`bindfs_vget`、内核最小移植目前都只到目标文件。
9. **`MNTK_PATH_FROM_ID` 对其他 bindfs 视图的影响**没人评估。
10. **启动载荷预算。** 上次余量约 8.2 MB，`adrenometal-delta.md` 只粗略比较了插件体积，没算新着色器包。
11. **`gpuCoreCount=6` 的真值。** 上游和 Pagani 都是常量，我们这里没有运行时验证，只影响 MPS 选内核。
12. **许可证。** Pagani 的 `gpu-user-mappings.patch` 改编自 Droid-VM，许可证没查。方案不用它，所以不阻塞，但以后做系统 BO 时要查。`Ath12kWifi` 的 NOASSERTION 对外发镜像前要评估。

**要在草稿目录清空前搬走的（`upstream-merge/` 下）：**

- `metal/resolve.py`、`metal/resolve2.py`、`metal/0025-*.rebased.patch`、`metal/0026-*.rebased.patch`
- `metal/m2v` 里的 `624bab39`（做成 bundle）
- `adreno740/trial/a830-min-port.diff`
- `brightness/proto/` 全部，以及工作流回复里的亮度报告全文（写成 `upstream/brightness.md`）
- `pagani-a830/dcvs-sim/dip.cpp`（可选）

本文里的新证据都写在正文，`review/` 目录丢失不影响结论，但 `bindfs_vget` 试编的做法要记：取 `build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bind_vfsops.o.json` 的 `arguments`，把源文件和 `-o` 换成副本路径，去掉依赖输出选项。

---

## 附：原报告里核实成立的主要结论

| 结论 | 我怎么核的 |
| --- | --- |
| 上游分支内容都在 main | 只抽查提交次序：`0f11e64`、`adc18c5`、`1353601`、`aff537e` 在 `d6ca655` 前，`017398d`、`f42d44a`、`547312c`、`dfe4eeb`、`fc5ca0b` 在后 |
| 桥接补丁 0020 的 Mesa 依赖只影响第二步 | 第一步补丁清单和头部 |
| 触摸提频 100 ms | `CpuFreqGovernor.h:77` |
| UFS 只读开关和日志窗口 | `RootCommands.h:195,299`、`NativeUfs.cpp:1196` 起 |
| 完整包没开音频硬件 | `boot-A830-完整包.payload.json` |
| ACTLR 注释缺本机依据 | `PaganiGpuBoard.cpp:72` 只有 `measured` 字样，`A830Smmu.cpp:195` 指向 K90 |
| CP 抢占不做 | 结论合理，寄存器差异未逐项复核 |

## 未复核

按决策影响取舍，这些我没有逐项核：CP 抢占的 A8xx 寄存器表、DCVS 的主机模拟、WiFi 与音频的细节、`ipados27-parity.md` 第 2.4 节的补丁试打数字、35 个小组件扩展的分布统计、Toucan 与 TokenID 的模型判定。
