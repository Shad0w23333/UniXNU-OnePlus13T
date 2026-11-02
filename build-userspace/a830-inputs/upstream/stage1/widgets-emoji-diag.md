# 第 1 阶段：小组件、emoji、诊断

全程没有上机，没有运行 adb、fastboot，没有跑整核，没有提交。没有碰 Metal、Turnip、翻译层、`NativeAdrenoABI.h`、`UniXNU/out`，也没有碰另一工作流的文件。

证据标记：**【主机实测】** 本次在本机跑出来；**【读码】** 读 XNU 或驱动源码；**【逆向】** 读 21H461 共享缓存或内核缓存，地址是未滑动地址；**【推断】**、**【推测】** 没有直接证据。**本文没有任何一项在 13T 上验证过。**

---

## 0. 结论

1. **小组件的因果链第一环走通了，而且不是上游注释说的那条路。** 关键不是 `fsgetpath`，是能力位 `VOL_CAP_FMT_PATH_FROM_ID`。没有它 CoreServicesInternal 根本不保留文件 ID，`-[FSNode initWithURL:flags:error:]` 带标志 1 时返回 nil，`_LSAddBundlePlugins` 于是跳过插件可执行文件解析，插件平台为 0。详见 1.2。这把 `merge-plan.md` a.5 的“前半段只有上游一句注释”改成了【逆向】。把握从“中高”上调，但**结果仍未上机**。
2. **补丁只对 `/Applications` 视图开启**。其余 13 个 bindfs 视图的能力位、挂载标志、`vfs_vget` 与打补丁前完全一样。理由见 1.4：它们不需要，开了没有收益，只有变数。
3. **我没有照抄上游的 `bindfs_vget`，修了四处**，其中 `BIND_ROOT_INO` 和 `vn_getpath` 两处在我们这里会出错。见 1.3。
4. **emoji 的修改带来一个调研没写到的副作用**：内核 slot 189 也读点距，`CADisplay.physicalSize` 从 0 变成约 5.74 × 2.64 英寸。范围查清了，不影响合成。见 2.3。
5. 诊断加了 `chronod`、`ax`、`vg`、普查两行，`check-paganidiag-host.py` 全过，seed、layout 两项的脚本问题已修。
6. **新发现的风险：** 视图里 101 个扩展都会恢复注册，不止 25 个小组件。见 1.6。**新线索：** `/System/Library/AppPlaceholders` 有同标识符的占位，LS 可能取到占位，普查的 `oth` 专门看这个。

---

## 1. 小组件：`phone-bindfs-vget.patch`

### 1.1 改了什么

文件：`UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-bindfs-vget.patch`（222 行），已追加到同目录 `series` 末尾。

| 文件 | 改动 |
| --- | --- |
| `bsd/miscfs/bindfs/bindfs.h` | 加 `BINDM_PATH_FROM_ID`，`BIND_ROOT_INO` 移到这里 |
| `bsd/miscfs/bindfs/bind_vnops.c` | 删掉本地的 `BIND_ROOT_INO` |
| `bsd/miscfs/bindfs/bind_vfsops.c` | `bindfs_mount` 按标志决定；`bindfs_vfs_getattr` 只对没开的挂载抹能力位；新增 `bindfs_vget` 和计数 |
| `bsd/kern/kern_exec.c` | `native_system_apps_show` 在挂载前后置 `native_bindfs_file_ids`；U 行加 `file ids N` |

启动参数 `pagani-bindfs-vget`：不写等于 1，只有要求的视图（`/Applications`）开；0 全关，回到打补丁前；2 所有启动期视图都开，留给“别的视图也重要”这种实验。

**默认开的理由：** 只影响 `pagani-system-apps=1` 才存在的这一个视图，下层是 Data 上的副本。打补丁前的行为就是缺陷本身，默认关等于没修。不占命令行字节。

### 1.2 21H461 上走通的因果链【逆向】

| 环节 | 位置 | 内容 |
| --- | --- | --- |
| 1 | bindfs | 抹掉 `PATH_FROM_ID`、`PERSISTENTOBJECTIDS`（`bind_vfsops.c:420,424`，【读码】） |
| 2 | CoreServicesInternal `CreateMountInfo`，常量 `0x18175e0c0`、`0x18175e130` | `valid` 和 `capabilities` 都有 0x4000 时置标志位 51（0x4000 左移 37），即 `kCFURLVolumeSupportsPathFromID`。`volumeSupportsPathFromID`（`0x1817539cc`）读的就是这一位 |
| 3 | `parseAttributeBuffer` `0x1817311c0` | 位 51 变成属性标志 0x8000 |
| 4 | 同上 `0x18173125c`–`0x181731268` | **标志 0x8000 为 0 时不把文件 ID 存进 +0x80** |
| 5 | `createFileIDValue` `0x181734598` | 读 +0x80，为 0 就返回成功但值为空 |
| 6 | `-[FSNode getResourceValue:forKey:options:error:]` `0x187c919c8`–`0x187c91a04` | 值为空且选项 bit 1 没置，报 -10813，返回 NO |
| 7 | `-[FSNode getFileIdentifier:error:]` `0x187cccc08` | 取 `_kCFURLFileIDKey`，选项 1，所以上一步失败 |
| 8 | `-[FSNode checkResourceIsReachableAndReturnError:]` `0x187cccbe0` | **函数体就是调 `getFileIdentifier:error:`** |
| 9 | `-[FSNode initWithURL:flags:error:]` `0x187c84a08`–`0x187c84a18` | 标志 bit 0 时做可达检查，失败返回 nil |
| 10 | `_LSAddBundlePlugins` `0x187d9e88c`–`0x187d9e8a0` | 以标志 1 给插件可执行文件建 FSNode，nil 就跳到 `0x187d9e978`，`_LSParseLoadCommands`（`0x187d9e94c`）不执行 |
| 11 | `_LSRegisterPlugins` `0x187d980b0` | 取 `LSExecutablePlatformKey`，没有就保持 0 |
| 12 | 之后 | 见 `merge-plan.md` a.5：平台 0 找不到扩展点，`requireValid` 失败，chronod 打 `Ignoring restricted extension` |

这条链解释了之前对不上的几件事：

- **应用本体为什么能开。** `_LSCreateRegistrationDataForDirectoryNode` 对主包走 `_LSAddExecutableFormatInfo`，不经过第 9 环的可达检查，只有嵌套插件走第 10 环。【逆向】
- **WebKit 扩展为什么经 `/System/Cryptexes/OS` 视图能用。** 它们是独立注册的包，走 `_LSRegisterPluginNode`，不是 `_LSAddBundlePlugins`。【逆向，“所以不受影响”是推断】
- **上游注释的“经 fsgetpath 解析”不是这条路的关键。** 只保留能力位、不实现 `vfs_vget` 也能让第 10 环过去。但能力位说了能按 ID 找路径，`fsgetpath` 就必须答得上：CoreServicesInternal 在 `0x181736608`、`0x1817390b4` 见到这一位就会调 `fsgetpath`。所以两者都要。

### 1.3 与上游 `bindfs_vget` 的差异

上游来源 `UniXNU-UniXNU-sheng/patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch`，BSD-2-Clause-Patent，注释保留了出处。

| # | 上游 | 这里 | 原因 |
| --- | --- | --- | --- |
| 1 | 对 ID 2 直接问下层 | `BIND_ROOT_INO` 返回视图根 | bindfs 把根报成 2。APFS 上 ID 2 是 Data 卷根，路径不在绑定树里，上游返回 ENOENT，`fsgetpath` 回退到被覆盖卷，`/Applications` 被解析成 `/`【读码】 |
| 2 | `vn_getpath` | `vn_getpath_fsenter` | `vn_getpath` 带 `BUILDPATH_NO_FS_ENTER`，`VFS_VGET` 刚造的 vnode 没有缓存父节点时直接 ENOENT【读码】。这是最可能的实际失败：vnode 被回收后再解析就时好时坏 |
| 3 | 下层根不持 iocount | `vnode_getwithref` 持住 | `build_path` 要求 iocount【读码】 |
| 4 | 用调用者 `ctx` 走路径 | `vfs_context_kernel()`，加 `NOCROSSMOUNT` | `VFS_VGET` 本来不做鉴权，权限由 `fsgetpath_internal` 之后的 `mac_vnode_check_fsgetpath` 和 `BUILDPATH_CHECKACCESS` 负责。用调用者上下文时沙盒会决定文件 ID 能否解析。沙盒的路径钩子本身可能走到这里（`vfs_subr.c` 注释说 MAC 钩子用 `vn_getpath_fsenter`），用内核上下文也避开了递归 |
| 5 | 所有 bindfs 挂载 | 只有带 `BINDM_PATH_FROM_ID` 的 | 见 1.4 |
| 6 | 无 | 计数 `native_bindfs_vget_calls/resolved` | 上机直接看有没有人按 ID 解析 |
| 7 | 从 `bindm_rootvp` 走路径时不持 iocount | 走路径期间持住 | `vnode_lookupat` 的起点要调用者持有。原来靠 usecount 和 `vfs_busy` 兜底，没有实际后果，审查后顺手改 |

### 1.4 所有 bindfs 视图【读码】

`native_gpu_addon_mount` 和 `native_cryptex_os_bind` 是仅有的两个入口，共 14 个视图。

| # | 挂载点 | 下层 | 来源补丁 | union | 完整包 | 里面有 LS 关心的包吗 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `/System/Library/Extensions` | `ngpu/addons` | `zzy-native-gpu-addon-mount` | 是 | 开 | 无，Metal 插件走 dlopen |
| 2 | `/System/Library/LaunchDaemons` | `ngpu/daemons` | 同上 | 是 | 开 | 无 |
| 3 | 壁纸 `.ca` 目录 | `ngpu/wallpaper` | 同上 | 是 | 种子无此目录，不挂 | 无 |
| 4 | `/Library/Wallpaper/Collections` | `ngpu/wallpaper-collections` | `phone-wallpaper-collections` | 是 | 开 | 无 |
| 5 | `TextInputUI.framework` | `ngpu/textinputui-layouts` | `phone-keyboard-layouts` | 是 | 开 | 无 |
| 6 | `/System/Library/Audio/Plug-Ins/HAL` | `ngpu/audio` | `pagani-audio-addon-mount` | 是 | 开 | 无，HAL 自己枚举 |
| 7 | `ControlCenterUI.framework` | `ngpu/controlcenterui` | `phone-controlcenter-settings` | 是 | 开 | 无 |
| 8 | `/Library/Audio/Tunings/J120` | `ngpu/tunings-j120` | `phone-haptics-overlays` | 是 | 开 | 无 |
| 9 | `MediaExperience.framework/J120` | `ngpu/mediaexperience-j120` | 同上 | 是 | 开 | 无 |
| 10 | `/System/Library/MediaCapture` | `ngpu/mediacapture` | `phone-torch-overlay` | 是 | 不开 | 无 |
| 11 | `/private/preboot` | `ngpu/preboot` | `zzyb-native-cryptex-os-view` | 是 | 开 | 下面又挂了 12、13 |
| 12 | `.../Cryptexes/OS/System` | `/System` | 同上 | 否 | 开 | 4 个 WebKit 扩展，当前能用 |
| 13 | `.../Cryptexes/OS/usr` | `/usr` | 同上 | 否 | 开 | 无 |
| 14 | `/Applications` | `/private/var/staged_system_apps` | `phone-system-apps-view` | 是 | 开 | **29 个应用，101 个扩展，25 个小组件** |

扫描 SystemOS 卷【主机实测】：只有 4 个 `.appex`，全是 web-browser-engine，没有小组件，没有带 `PlugIns` 的应用。

### 1.5 路径变化逐项分析

**视图 1–13（默认）：没有任何变化。** `bindm_flags` 不带新标志，能力位照旧抹掉，`mnt_kern_flag` 不置，`mount_lookupby_volfsid` 找不到它，`VFS_VGET` 走不进来；即使进来也返回 ENOTSUP，和原来 `vfs_vget` 为空时一样。

**视图 14：**

| 路径 | 打补丁前 | 打补丁后 |
| --- | --- | --- |
| `fsgetpath(视图, ID)` | `mount_lookupby_volfsid` 失败，ENOTSUP | 走 `bindfs_vget`，返回 `/Applications/...` |
| `fsgetpath(视图, 2)` | ENOTSUP | 返回 `/Applications`（上游这里会答 `/`） |
| ID 不在视图里 | ENOTSUP | ENOENT，`fsgetpath_internal` 的 union 分支转问根卷，这是 union 的本意 |
| `openbyid_np` | 同 `fsgetpath` | 同上，需要特权 |
| volfs `/.vol/...` | **不存在** | **不存在**：本次内核的 143 个 `-D` 里没有 `CONFIG_VOLFS`（`vfs_syscalls.o.json`，【主机实测】），`vfs_getrealpath` 没编进来 |
| `getattrlist` 文件属性（`FILEID`、`OBJID`、`PARENTID`、`FSID`） | 已返回下层 ID 和 bindfs fsid | **不变**，内核本来就答 |
| `getattrlist` `ATTR_VOL_CAPABILITIES` | 两位为 0 | 两位为 1。这是唯一变化的属性，也是修复本身 |
| `ATTR_CMN_FULLPATH`、`vn_getpath_fsenter` | 有 `v_parent` 时不变；无 `v_parent` 时 ENOENT | 有 `v_parent` 时不变；无时经 `bindfs_vget` 找回 |
| `build_path` 的 `fixhardlink` | 不触发 | 不触发：bindfs 从不调 `vnode_setmultipath`，没有 `VISHARDLINK` |
| `vfs_fsevents` 硬链接分支 | 不触发 | 同上，只读挂载也不产生事件 |
| `vnode_getparent_byid` | 不到 | 不到：本次内核没有 `CONFIG_FILE_LEASES`，且只在创建或截断时走 |
| nullfs `null_get_lowerparent` | — | 没有 nullfs 叠在 bindfs 上 |

用户态谁会看到变化【逆向，枚举 3354 个镜像的未定义符号】：`fsgetpath` 的导入者只有 FileProvider、CloudDocs 一族、CoreServicesInternal、MobileSpotlightIndex、PhotoLibraryServices、QuickLookThumbnailingDaemon、SpaceAttribution、ChunkingLibrary、MMCS、GenerationalStorage、ktrace、AppleTracingSupportSymbolication 和 `libdyld.dylib`。其中和 `/Applications` 有关的是 CoreServicesInternal，以及 dyld【推测：dyld 按卷 UUID 用文件记录，bindfs 不提供 `f_uuid`，这点没有变】。

### 1.6 风险（都是【推测】）

1. **恢复注册的不止小组件。** 视图里 101 个扩展：25 小组件、16 intents、15 通知内容、10 Spotlight 索引、6 诊断、5 分享、5 QuickLook 等。它们目前都是平台 0。修好后 Spotlight 索引、intents 这类可能开始被拉起，开机早段 CPU 和内存压力会上升，用户满意的流畅度**可能**受影响。回退：`pagani-bindfs-vget=0`，不用重编。上机看 `ax`。
2. **`AppPlaceholders` 占位。** 根卷 `/System/Library/AppPlaceholders` 有 29 个占位应用，里面的小组件扩展标识符和视图里一样，只是没有可执行文件。`zzy-staged-system-apps.patch:11` 写过只有占位时小组件是空白的。如果 LS 同时登记了两边并取到占位，修好文件 ID 也可能还是空白。我没有查清 lsd 是否扫描这个目录。普查 `oth` 大于 0 就是这种情况。
3. **union 回退的同号碰撞。** 调用者如果拿“目录的 fsid + 目录项的 ino”组合，根卷条目会先落到 Data 卷。ID 不存在时回退正确，ID 恰好在视图里时会答错。要有人这样拼才会发生，只在视图 14 上存在。
4. **第 12 环之后**的 chronod、WidgetRenderer、RenderBox 链路这次没动。`ok 25/10` 之后还可能卡在第二层，见 `widgets-keyboard.md` 1.5。

### 1.7 打补丁与编译【主机实测】

- 在草稿副本里 `git clone --shared` 基线，按 `xnu-inputs.json` 加 `series` 的顺序用 `git apply`（与 `scripts/unixnu/patches.py` 相同，不容错）打完 **85 个补丁，全部干净**，包括重新生成的 `phone-diag-sysctl.patch`。这一步现在固定在测试里：`scripts/test-bindfs-vget-patch.py --inputs <xnu-inputs.json>`。
- 打完后用真实编译记录试编，告警数与打补丁前逐一比较：

| 文件 | 补丁前 | 补丁后 |
| --- | --- | --- |
| `bind_vfsops.c` | 2 行（`vm_kern.h` 枚举告警、缺 include 目录，都不在本文件） | 2 行，相同 |
| `bind_vnops.c` | 21 | 21 |
| `kern_exec.c` | 53 | 53 |

- `scripts/test-bindfs-vget-patch.py`，6 项全过：只碰这 4 个文件；排在 `phone-system-apps-view.patch` 之后；只有系统应用视图发起；`--xnu` 能打到构建树副本；从补丁里抽出 `bindfs_vget`、能力过滤、挂载判定，用桩 VFS 在 ASan、UBSan 下跑全部分支（根 ID、下层根、绑定树内、同前缀兄弟目录、下层错误、下层根已失效、视图根已失效、两条取路径失败、查找失败、绑定在卷根、未开的挂载、挂载已卸），并核对 iocount 和路径缓冲全部平衡，再跑 11 种启动参数组合（挂载成功才计 `file ids`）。
- 种子检查：`final/s4-diag.dbrdisk` 里**没有** LS 数据库，也没有 `ls-store-rebuilt-1` 标记，所以 LS 每次开机重建，补丁第一次开机就生效【主机实测，只读挂载】。
- **没做：** 完整内核链接，那是主线的事。

---

## 2. emoji：`getDotPitch` 答 460

### 2.1 改动

| 文件 | 改动 |
| --- | --- |
| `NativeMobileDisplay/NativeLegacyDisplay.cpp` | 提供者没有 `dot-pitch` 属性时答 `native_legacy_dot_pitch()`，为 0 才返回 `kIOReturnNotFound` |
| `NativeMobileDisplay/NativeLegacyRequest.h` | 纯函数 `native_legacy_dot_pitch` 和 `NATIVE_LEGACY_DOT_PITCH` |
| `NativeMobileDisplay/Test/request-state.c` | 加用例 |
| `NativeMobileDisplay/README.md` | 记启动参数和副作用 |

启动参数 `pagani-dot-pitch`：不写等于 460，0 回到旧行为，其他值直接用。提供者若有 `dot-pitch` 属性仍以属性为准。

**默认开的理由：** 460 是真值（6.32 英寸 1216×2640 算得 459.9，iPhone 15 Pro 同值），旧行为让 MobileGestalt 拿到 -1。不占命令行字节。

### 2.2 有没有别的路径依赖 `kIOReturnNotFound`

**内核【逆向】**，`IOMobileGraphicsFamily`，地址是内核缓存里的原始地址：

| 位置 | 是什么 | 结论 |
| --- | --- | --- |
| `0xfffffff006778168` + `0xfffffff00677a554` | 用户客户端 `get_dot_pitch` | 把值和返回码原样交给用户态，不判断具体错误 |
| `0xfffffff0067982bc`（**slot 189**） | 显示面积 | 任何错误都变成 `kIOReturnUnsupported`，点距为 0 才答 `kIOReturnNotFound`，**不依赖 slot 188 的具体错误码** |
| `0xfffffff00678c8d0`、`0xfffffff00678c908`、`0xfffffff0067b27f4` | 同偏移 0x5e0 的调用 | 参数个数和接收者都不同，是别的类【推断】 |

扫描范围是这个家族附近（`0xfffffff006770000`–`0xfffffff0067c0000`）。全内核同偏移的虚调用有 185 处，其余都在别的扩展里，没有逐个看；13T 上的帧缓冲只有 `NativeLegacyDisplay` 这一个对象。

**用户态【逆向】：** `IOMobileFramebufferGetDotPitch` 全缓存只有 `libMobileGestalt.dylib` 导入。`sub_1a0215704` 失败时只打日志，点距保持 -1。

**仓库【读码】：** `UniXNU/scripts/check-system-framebuffer-client.py` 要求 `x0==0xe00002f0`，但它校验的是 `build/darwinboot-owner-hvf-…-dotpitch/gdb-stop.json` 这份 HVF 旧录像和 `UniXNU/drivers/GopFramebuffer`，不看 13T 的 `NativeMobileDisplay`，也不在 13T 的构建流程里。

结论：**没有路径依赖这个错误码。**

### 2.3 调研没写到的副作用

slot 189 用点距算物理尺寸：`out[0]=高/点距`，`out[1]=宽/点距`。

- 用户态入口 `IOMobileFramebufferGetDisplayArea`，**只有 QuartzCore 导入**。`CA::WindowServer::IOMFBDisplay::set_physical_and_canvas_sizes`（`0x1896df84c`）不看返回值，直接把结果存进 +0x6580。
- 所以 `CADisplay.physicalSize` 从 (0, 0) 变成约 (5.74, 2.64) 英寸。真机本来就有值，这是回到正常路径，但确实是变化。
- 影响范围：QuartzCore 内部只有 `panel_physical_size` 和 `_CADisplayPrintDisplays`，**渲染路径不读它**。
- 全缓存引用选择子 `physicalSize` 的镜像只有 8 个：ARKitCore、CarKit、CarPlaySupport、QuartzCore、RealityFusion、SpaceAttribution、SpringBoardFoundation、UIKitCore。UIKitCore 那处是 `_UIFocusEventRecognizer`，不是显示。SpringBoardFoundation 是 `_SBDisplaysWithSizePredicate` 和 `SBDisplayScaleMapping`，后者只被 `SBExternalDisplayDefaults` 调用，都是外接显示相关【逆向，审查复核】。
- 值的顺序是高在前，和 Apple 代码一致，我没有真机数据对比。

MobileGestalt：点距 460 会让 `sub_1a0215704` 走到 `screen parameters are unexpected`，多一条 fault 日志（`-1` 时被 `0x1a02158ec` 跳过）。1216×2640 不在屏幕表里，类别仍是 -1，但点距写进结果（`0x1a021594c`），`main-screen-pitch` 是 460。这条日志无害，看日志时不要误判。

### 2.4 结果【主机实测】

- `Test/request-state.c` 和 README 列的另外 7 个 C++ 主机测试全过。
- `build-stage7-display.py` 配方在草稿树里完整跑通，含 358 项虚表和原始 ABI 检查。`NativeLegacyDisplay.cpp` 零告警（`-Wall -Wextra -Werror`）；日志里 3 条告警都在 `PaganiInput/HidEvents.h`，与上次构建相同。
- **用户说的“小错位”是不是这个，仍未确认。** `merge-plan.md` a.5 的保留意见成立：这是便宜且方向正确的修改，不是已找到根因。

---

## 3. 诊断

### 3.1 改动

| 文件 | 改动 |
| --- | --- |
| `PaganiDiag/kernel/pagani_diag.h` | `pagani_diag_watched` 加 `chronod`；`pagani_diag_extension`；状态行加 `ax`、`vg` |
| `PaganiDiag/kernel/kern_exec-diag.inc` | 统计扩展 exec；读 `bindfs_vget` 计数（弱符号，补丁顺序在后） |
| `PaganiDiag/kernel/phone-diag-sysctl.patch` 与 `UniXNU-Pagani_SM8750-gpu/patches/xnu/phone-diag-sysctl.patch` | 用 `make-diag-patch.py` 重新生成，两份相同 |
| `PaganiDiag/DiagCore.h/.cpp` | `WidgetEntry`、`census_values`、`wk`、`wkv` 行，摘要加 `wk`，`round_probes` 加 `wid` |
| `PaganiDiag/PaganiDiag.mm` | 探针 `wid` |
| `PaganiDiag/paganidiag.entitlements`、`scripts/build-paganidiag.py` | 加 `com.apple.private.coreservices.canmaplsdatabase`，chronod 也有 |
| `PaganiDiag/Test/*`、`README.md` | 用例、文档、修 seed 和 layout |

### 3.2 输出

kernel 状态行尾部：`ax <扩展exec>/<小组件扩展exec> vg <文件ID请求>/<解析成功>`。这两组数在 ring 冲掉 S 行之后还在，每 300 s 重发。

“小组件扩展”的规则：`PlugIns/` 之后含 `Widget`、不含 `Intent`。在 21H461 的 `/Applications` 和 `AppPlaceholders` 共 239 个扩展上实测【主机实测】：35 个 WidgetKit 扩展命中 33 个（`NewsTag`、`NewsToday2` 名字不含 `Widget`），没有误中。初版不排除 `Intent`，会把 6 个 intents 服务（`FindMyWidgetIntentsItems` 等，只在配置小组件时运行）算成小组件，可能误判小组件扩展已拉起。内核的 `ax` 和守护进程的 `wid` 用同一规则。

daemon 一轮最多多两行，没变化不重发：

```
r1 sum @61 … data 1422/2047M wk 35/35
r1@61 wk n35 fs 25/10 reg 25/10 ok 25/10 ep 25/10 pf 2:35 oth 0 err 0 ls 35 pt 2
r1@61 wkv cap 4001 fg 0/0 fid 1 chr 1 ext 3 wid 1
```

普查直接照 chronod 的方式问 LS（`initWithBundleIdentifier:requireValid:error:`），不是间接推断。

### 3.3 判读（补充 `widgets-keyboard.md` 4.1）

| 看到 | 结论 |
| --- | --- |
| U 行 `system apps view stage 5 status 0, ... file ids 1` | 视图挂上且开了文件 ID。`file ids 0`：`pagani-bindfs-vget=0`，或挂载失败（看 status，只有挂载成功才计数）。行里没有 `file ids` 字段：内核没进补丁 |
| `wkv cap 0 fid 0` | 补丁没生效，1.2 的链还断着 |
| `wkv cap 4001 fid 1`，`wk pf 2:35 ok 25/10` | 第一层修好，往下看 `chr`、`ax`、G 行 |
| `wk pf 0:25 2:10 ok 0/10` 且 `cap 4001` | 能力位在但平台仍丢，1.2 的结论要重审 |
| `wk oth>0` | LS 取到了占位，新根因（1.6 第 2 条） |
| `wk reg 0/10` | LS 根本没登记视图里的扩展 |
| `wkv fg e45` 且 `cap 4001` | 不该出现：能力位在但 vget 不通 |
| k 行 `vg` 只有探针自己的每轮 1–2 次，`fid 1`，`ok 25/10` | 没有别人按 ID 解析，只靠能力位就过了，符合 1.2 |
| k 行 `ax` 第二个数为 0，用户又确实打开过小组件库 | 小组件扩展没被拉起，查 chronod。第一个数涨而第二个不涨，是别的扩展在跑（intents、Spotlight 等） |
| `chr 0` 且 `ps` 里 `chronod:-` | chronod 没跑，按需启动，先确认是否看过小组件 |
| `pt -` 或 `ls 0` | 扩展点本身没注册 |

### 3.4 替换种子

守护进程改动后流程不变，只换这一个二进制：

```
python3 -B drivers/PaganiDiag/scripts/build-paganidiag.py --out <DIR>
python3 -B drivers/PaganiDiag/scripts/replace-diag-in-seed.py --build <DIR> --trust <旧 owner-trust.bundle> <旧种子> <新种子>
```

新 CDHash `b10cdc40149fd416246f3bf8185a989be0044907`（当前源码，主机构建）。**`chronod`、`ax`、`vg` 在内核里，要新内核才有；只换种子时普查两行照常。**

### 3.5 结果【主机实测】

`Test/run.sh` 全部通过，退出码 0：

- `test_core` 692 项、`test_kernel` 21721 项，`-O0`、`-O2`，ASan + UBSan。
- `patch`：补丁就是脚本生成的，后续补丁能打上（脚本现在也读设备 `series`，构建之后才加进去的 `phone-bindfs-vget.patch` 算在内），`kern_exec.c` 53 条告警不增加。
- `build`：269296 字节，4 KiB 页，arm64 iOS 17.0。
- `imports`：191 个导入都在 21H461 共享缓存。`dlsym` 用到的 `fsgetpath`、`proc_listallpids`、`proc_pidpath` 另外查过，都在 `libsystem_kernel`。
- `flow`：在本机真实的 macOS LaunchServices 上跑通同一套私有调用，`wk n3 fs 0/3 ok 0/3 pf 1:3`，`wkv cap 4001 fg 0/0 fid 1`。这验证了选择子和 ARC 用法，**不等于 iOS 上结果正确**。
- `policy`、`trust`、`replace`（`replace` 报告载荷余量 5971982 字节）通过。
- `seed`、`layout`：**修好了。** 原因是 `patched_copy` 对已合入的差异跑 `patch`，Apple 的 `patch` 当成反向补丁自动撤销，测的其实是旧脚本（`layout` 里 `sheng-no-lc=1` 就是这么来的）。现在先 `--reverse --dry-run` 判断，已合入就直接用。`make-a830-final-seed.diff` 正反都打不上，但没有检查项用它，没动。

---

## 4. 新启动参数

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `pagani-bindfs-vget` | 1 | 0 全关；1 只 `/Applications`；2 所有启动期 bindfs 视图 |
| `pagani-dot-pitch` | 460 | 0 回到 `kIOReturnNotFound`；其他值直接用 |

两个都不用写进命令行。回退时分别多 21、19 字节。

---

## 5. 未验证

1. 以上全部没有上机。小组件、emoji 的现象变化都是预期，不是结果。
2. 新内核完整链接和预链接。
3. `pagani-bindfs-vget=2` 只有模型测试，没有内核运行过。
4. `bindfs_vget` 在真实 APFS、真实 union 下的行为。上游验证过的是 xnu-12377、下层为 OS 卷，我们是 xnu-10063、下层为 Data 上的副本，还改了四处。
5. `canmaplsdatabase` 授权在 owner 信任下是否生效；不生效时 `wid` 探针会走 XPC 或报错，只影响这两行。
6. lsd 是否登记 `AppPlaceholders`。
7. 101 个扩展恢复后的开机负载。
8. `physicalSize` 变化对 SpringBoard 的实际影响。

## 6. 对 `merge-plan.md` 的修正

- a.5 表格第 4 行“注册时为什么丢平台或扩展点：21H461 上没走通”：**已走通**，见 1.2。
- a.5 表格第 3 行“`_kCFURLFileIDKey` 被 LS 使用”：更准确地说，缺它时 `FSNode` 可达检查失败，这才是断点，不是 `fsgetpath`。
- a.5“`bindfs_vget` 三处已试编”：试编的是上游原样，那一版在我们这里有 1.3 第 1、2 条的问题，不要用。
- 第 1 阶段表“风险：bindfs 能力位影响其他视图（低到中，未评估）”：已评估，默认只开一个视图，其余零变化。新风险见 1.6。
- emoji 的修改不只影响 MobileGestalt，还影响 `CADisplay.physicalSize`，见 2.3。
