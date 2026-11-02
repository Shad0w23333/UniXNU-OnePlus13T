# 上游 iPadOS 27 GPU 更新调查（feat/ipados27-gpu-parity）

- 时间：2026-10-31。上游快照 `参考资料/上游-20261031`，UniXNU main `407db1a`。
- 范围：只读。没有碰手机，没有改我们的任何源码树和上游克隆。
- 试验目录：`/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/ipados27-parity/`（`clones/` 裸克隆 DarwinBoot 等本地没有的模块，`xnu-12377/` 试打补丁，`apply-trial*.sh`、`hunks10063.py` 是脚本）。
- 证据标记：**【读码】** 读文件或提交确认；**【实测】** 本次在电脑上跑命令得到；**【上游自述】** 提交说明或文档这样写，我没有独立复现；**【推测】** 我的判断。上游的性能数字都是 sheng（A740）上测的，不是 13T。
- 简写：U = `参考资料/上游-20261031/UniXNU-UniXNU`；SH = `.../UniXNU-UniXNU-sheng`；PG = `.../IssacL04-UniXNU-Pagani_SM8750`；AM = `.../UniXNU-AdrenoMetal`；A7 = `.../UniXNU-Adreno740`；XW = 我们打过补丁的 XNU 工作树 `UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work`；BRINGUP = `SH/patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch`。

## 0. 结论

1. “iOS 27 的 GPU 驱动”不是一个新驱动，也没有 A830 支持。它是 sheng（A740）把系统换到 iPadOS 27 时，顺带对 Adreno740、AdrenoMetal、Turnip、翻译器做的一轮大改，分支名 `feat/ipados27-gpu-parity`，现在已全部进 main。
2. 按提交逐条看，大部分改动与 iPadOS 版本无关（内存、页表、锁、恢复、翻译器、线程优先级）。Pagani 作者已在 **iPadOS 17.7.11 + A830** 上接入过同一批代码（PG `README.md:7`、`STAGE6.md:1347`），所以不换系统也能拿到这些改动。
3. 真正只属于 27 的是：xnu-12377 内核、arm64e/PAC、fileset 内核集合、半浮点帧缓冲、各处 24A446 地址。
4. **现阶段不建议换根卷。** 换系统要把 13T 全部内核驱动、22 个设备补丁、9 处 dyld 修补和 iPhone 身份那批界面修复重走一遍，量级是几周到更长；用户现在抱怨的键盘延迟、小组件、亮度、声音、Wi-Fi 都不会因为换系统变好。
5. Oryon 大概率满足 PAC 要求，但**没有实测**：LLVM 的 `oryon-1` 描述带 PAuth，安卓内核开了 `CONFIG_ARM64_PTR_AUTH_KERNEL`，却没有任何一次读过 `ID_AA64ISAR1_EL1` 或 `/proc/cpuinfo` 的 Features 行。
6. `patches/xnu/` 相对我们的基线**零变化**（逐字节相同，只少我们自己的 `native-arch-timer-frequency.patch`）。有用的修复藏在 sheng 的设备补丁 BRINGUP 里，其中 bindfs 的 `vfs_vget` 与我们的 `/Applications` 视图处境完全一样，上游注释直接点名“小组件扩展被 chronod 判为受限”，**这是这次调查里对小组件最值得先试的一条线索**（推测，未验证）。
7. 模块升级：ArmCpu 只值得摘两处（`parkCore`、`waitClear`）；XnuPrelink 和 DarwinBoot 对 17.7.11 没有收益；ArmSmmu4K 是破坏性接口变更，必须和 Adreno830 内存层一起动；GopFramebuffer 我们根本没编进内核，只用它的头文件。

## 1. “parity”到底是什么

### 1.1 定义

- 仓库里**没有任何文档定义 parity**。全部提交只有四条合并记录带这个词（U `bd5e13e`、A7 `98396b9`、AM `d0512ed`、SH `233c011`）【实测】。
- 第三方设备记录把它当“sheng 的 iPadOS 27 分支”引用：U `docs/devices/uke.md:299`、`docs/devices/asteroids.md:413,418,419`、`docs/devices/alioth.md:318,334`【读码】。
- 我的理解【推测】：让 GPU 栈在 iPadOS 27 上达到 17 上的同等可用。依据：AM `11420f4` 说明写“so the render server draws BGRA as on 17”；AM `NativeAdrenoMetalPlugin.mm:371` 写“As on iPadOS 17”；U `docs/zh/porting/arm64e.md:10-12` 的政策是每个驱动和优化必须同时适用于 17 和 27，只对一个目标有效的改动不予合并。
- 实际内容比名字宽得多：OS 适配 + 驱动大改 + 翻译器升级 + 大量诊断，全部混在一条线上。
- 上游没有 A830 驱动：`U/components.toml` 只有 Adreno740，`hardware` 是 A740。所谓“合并”对我们意味着把思路和代码移植进 Adreno830，不存在直接换模块这回事【读码】。

### 1.2 分支与 main 的关系【实测】

| 仓库 | parity 顶 | main 组件版本 | 与 main 的树差异 | 相对我们基线的非合并提交 |
| --- | --- | --- | --- | --- |
| UniXNU | `d266658` | `407db1a` | parity 是 main 祖先，main 多打包和设备记录 | `ae68700..d266658` 89 个 |
| Adreno740 | `10ac0ea` | `149ef6d` | 相同 | `5c8c145..` 37 个 |
| AdrenoMetal | `a0a34fa` | `a0f0287` | 相同 | `ce5e19e..` 67 个 |
| GopFramebuffer | `74ff4c4` | `19ef3a7` | 相同（变基副本） | `33e941a..` 7 个 |
| ArmCpu | `4bd3230` | `93a5030` | 相同（变基副本） | `9066b53..` 2 个 |
| ArmSmmu4K | `1811b6d` | `83969cd` | 相同（变基副本） | `3ba9245..` 4 个 |
| XnuPrelink | `76452c6` | `b74ed7c` | 相同（变基副本） | `f876e3e..` 3 个 |
| DarwinBoot | `3e3a8a9` | `3c75734` | 相同（变基副本） | `3325e35..` 1 个 |
| UniXNU-sheng | `d0eecd4` | `6b11bb5` | parity 是 main 祖先 | 约 55 个（从 `1092bee` 起） |

注意：U `6034d5f` 的说明写明，那次合并只是把各分支顶“挂进历史”，树没有变。`git branch --contains` 显示已合并，不代表代码在 main 的树里【读码】。

改动规模【实测】：Adreno740 非测试代码 32 个文件 +3147/-692；AdrenoMetal 非补丁 16 个文件 +4418/-309，桥补丁 34 个文件 +1978/-2120；`patches/mesa` 三个文件 +1645/-107。翻译器从 `720ef1c` 升到 `624bab39`（development 分支）。

### 1.3 指定提交

| 提交 | 内容 | 对我们 |
| --- | --- | --- |
| `7b02190` | 新增 9 个、改 4 个 xnu-12377 补丁，arm64e 系列和 24A446 配置 | 只对 27 |
| `ab11a8c` | 26 个脚本：`--target ios27`、`rust-pac-rewrite.py`、`ioaccelerator-base-abi-24A446.json`、种子和载荷步骤 | 绝大部分只对 27；`make-gpu-addon.py --prime-list` 这类通用 |
| `cffc2dd` | 模块指针；GopFramebuffer、AppleKeyStoreState 的 `os_abi` 加 24A446 | 无直接价值 |
| `4b57b67` | `turnip-gmem-mutable-swap.patch`、`unixnu --os 17\|26\|27` | 补丁是给 sheng 的 BGRA 帧缓冲用的，且上游默认不用（见 1.5 表后） |
| `90ca56a` | `unixnu release`、`[os_overrides]`、`templates/package.sh` | 打包框架，可以不跟 |
| `d266658` | 文档：PAC 要求、打包脚本规范、sheng 配置改为 27 | 要读，不用合 |

### 1.4 只对 iPadOS 27 有意义【读码】

| 项 | 证据 | 为什么只对 27 |
| --- | --- | --- |
| xnu-12377 + Darwin 26 调用 | `U/profiles/os/ipados-27.0.1-24A446/profile.toml:4-7`、`patches/xnu-12377/darwin26-*.patch` | 根卷是 Darwin 26 |
| fileset 内核集合 | XnuPrelink `111bcf5`、`b74ed7c`，`fileset_*.py` 约 2600 行 | 26 起 kernelcache 是 `MH_FILESET`，去符号 |
| arm64e 加载与根策略旁路文件 | DarwinBoot `3c75734`；`kernel.macho.vroot` | 17 不用 |
| 半浮点帧缓冲 | GopFramebuffer `35ce1b7`、`NativeHalfCodes.h`；AM `GpuScanoutPresenter.h:4,57` | 27 的 render server 选 RGhA，17 就是 8 位 BGRA |
| `framebuffer_pixel_format` 系列修补 | AM `NativeAdrenoMetalPlugin.mm:87-110,370-427`，`9bad6f5`、`d2aa73f` | 地址全是 24A446 |
| MTLCompiler 版本目录 | AM `NativeBridgeMetalFrontend.mm:813-819` | 27 没有顶层链接 |
| QuartzCore 新选择子 | 同文件 `:955,:986,:2783`（`setResponsibleTaskIDs:count:` 等） | 17 从不发 |
| 液态玻璃短形式管线 | 同文件 `:4185` | 27 才有 |
| `sys_dcache_flush` 只剩屏障 | 同文件 `:1319` | 27 的行为 |
| Rust 的 PAC 边界 | `bridge/RustPacBoundary.c`、`rust-pac-rewrite.py` | arm64e 进程才需要 |
| 写合并显示表面跳过维护 | A7 `NativeAdrenoMemory.h:49`，GopFramebuffer `NativeLegacyDisplay.cpp:625` | 27 的表面是写合并【上游自述】 |
| ArmCpu 的 `ml_processor_register` 次序、Edge 开关 | ArmCpu `eb0f053`、`93a5030` | 新内核才在注册里启动处理器 |

### 1.5 对 17.7.11 也有用【读码】

A7 列出的数字都来自提交说明【上游自述】。我们的 Adreno830 走的是内存池加每连接锁（M10），实现路线不同，下面每项都要逐函数核对，这里只判断“值不值得看”。

| 类别 | main 提交 | 内容与上游数字 | 我们现状 |
| --- | --- | --- | --- |
| 用户 BO 走系统内存 | A7 `c8806db`、`4c75ccb`、`14e4a7d`、`01ed692`、`cb9b44f` | 可分页内存 + wire，取消固定池；分配在锁外（原来 5454 次持锁 4.0 s）；释放在锁外（原来 38 ms）；连续内存只要到 128 KiB（原来 124 次搜索 21.7 s） | 工作树无 `allocateSystem`；仍是 `pagani-gpu-pool=1024` 池 |
| 页表与映射 | A7 `2c3df1e`、`d834731`、`28c652d`、`36c4e44`、`a087fca`；ArmSmmu4K `48dbd6c`、`9fcf406` | 每进程页表、按需分配表页、地址空间到 2^ias；BoMap 原来持锁 70 ms；导入别名检查原来占映射时间 79%；批量映射原来 4.8 s | 工作树无 `NativeAdrenoTablePages`；按进程页表还是待办（`docs/PROGRESS.md:552`） |
| 等待 | A7 `dd43abd` | 轮询 fence 不拿设备锁 | 需核对 |
| 恢复 | A7 `1105b0e`、`3713f8a`、`d77091c` | msm 式挂死检查与重启重放；只丢挂死的命令缓冲 | 有 `A830Recovery`，方案不同 |
| 对象与连接上限 | A7 `1c18419`、`bfc5841` | 表随需求增长，取消每进程连接上限 | 仍是 32 槽位（`Adreno830/NativeAdrenoPublish.h:41`） |
| CP 抢占 | A7 `3f4cb94`、`18d79d7` | 两环抢占，默认关（`sheng-gpu-preempt=1`） | 上游也处在试验阶段，先不碰 |
| 翻译器线程优先级 | 桥补丁 `0023-quality-of-service-on-ios` | 把命令缓冲回调线程的 QoS 条件从仅 macOS 扩到 iOS。U `docs/zh/porting/gpu.md:112` 把默认 QoS 列为慢帧原因之一 | 我们的 `720ef1c` 同样只在 macOS 生效（`build-userspace/tree/.cache/gpu-reference/metal2vulkan/crates/metal2vulkan-runtime/src/exit_guard.rs:767,783,904`），改动很小，**性价比最高** |
| 内存块增长 | 桥补丁 `0013`、`0021`，AM `e0997b0` | 小进程不再一上来持有 64 MiB 块；空闲镜像块复用。`gpu.md:113` 记录 94–112 MiB 块锁定要 281–340 ms | 与“打开应用小抖动”的症状吻合【推测】 |
| 绘制路径 | 桥补丁 `0006`、翻译器升级本身 | AM `4599584` 说上游已自带 37 个旧补丁中的 21 个 | 我们 18 个补丁里 0017、0018 是自己的，要重新适配 |
| Metal 语义 | AM `34b5920`、`34abea7`、`a11aeb9`、`ec2e1d1`、`4d789e9`、`01bfb25` | 等待、导入、堆、Core Image 按名取核、RenderBox 回答、表面导入保活 | 部分我们已自己修（`FamilyAnswer.h`、`0018`），有重叠 |
| 通用 Metal 覆盖 | AM `fe79bdf`、`20eb8da`、`0a12aac`、`09bce82`、`be310f9`、`c891ece` 等 | 运行时编译、曲面细分、采样器、着色器缓存、render server 编码线程默认开 | `03e04a6` 的 mimalloc 已被 `deab8bd` 撤掉，别带回来 |
| Turnip | U `c7353ea`、`83cce1d`、`bdcf746`、`caffd5d`、`b624df5`、`2c40ae9`、`159eb82` | 红黑树地址分配、尺寸分级 BO 缓存、内存压力回收、已在环上的同步不等待 | 与我们的 `turnip-a830-*` 重叠，需核对 |
| 诊断 | A7 `90ba1e3`、`59171b2` 等，AM `69712c0`、`94db828` | 帧路径时间线 | 方法可借鉴，`gpu.md:96-113` 的分段法值得照做 |

**用户现象对照**【推测】：打开应用小抖动对应“内存块”和“锁外分配”两组；小组件见第 3 节；键盘按键延迟在 parity 里**没有**直接相关的提交；亮度、声音、Wi-Fi 与 parity 无关。

**GMEM 补丁**：U `docs/zh/porting/gpu.md:90-93` 写明打补丁后 GPU 时间并不优于系统内存（均值 17.8 ms 对 17.1 ms，p95 56.1 对 52.1），主屏仍有一帧红蓝互换，所以默认不用。Pagani 接入后也出现颜色跳变，换 `sheng-tu-debug=sysmem,nocb` 才恢复（PG `STAGE6.md:1384,1390`）。这条不要优先。

### 1.6 Pagani 接入的结果和已知陷阱

Pagani 固定的版本：Adreno740 `7fa13d4`、AdrenoMetal `77cb207`、ArmSmmu4K `1811b6d`、GopFramebuffer `74ff4c4`、metal2vulkan `624bab39`（PG `STAGE6.md:1347-1349`）。

**不要把它当成“比我们好”的证据**：用户对 Run 88 主观约 60 帧、应用动画约 20 帧（`STAGE6.md:1384,1392`）；Run 90 滑动时 28–40 次 GPU 呈现每秒（`README.md:9`）。我们现在的包用户评价“基本跟原生没区别”。这说明接入可行，不说明接入后更流畅，两边的频率和配置都不同。

他们踩到的问题，我们接入时几乎都会遇到【上游自述，PG】：

1. 最终链接缺 `native_pmap_user_mappings`、`native_gpu_dcvs`（`STAGE6.md:1366`）。前者在上游只存在于 12377 的 BRINGUP，17 系列没有；后者在 sheng 的板级代码（SH `package/GpuPowerProbe.cpp`）。
2. AM main 在 iOS 17 目标下**编译不过**：`patchFramebufferBranch` 没有 `__arm64e__` 保护，唯一调用在 `__arm64e__` 内，而插件用 `-Wall -Wextra -Werror`（`NativeAdrenoMetalPlugin.mm:96,370,394`，`STAGE6.md:1370`）【读码已核对】。
3. `scanout_opaque` 路径和半浮点转换绕过状态带，覆盖我们的日志行（`STAGE6.md:1386`）。我们有 `NGP_METHOD_BAND`，同样会中招。
4. `gpuCoreCount` 上游写死 8，A830 要改 6（`STAGE6.md:1353` 所在段）。
5. Mesa 补丁顺序：提交背压补丁与 GMEM 补丁的计时改动重叠（`STAGE6.md:1368`）。

我自己再补一条 Pagani 记录里没有的：A7 `NativeAdrenoMemory.h:73` 明说 `pmap_batch_set_cache_attributes` “也清缓存行”，这依赖 BRINGUP 第 961 行的 `pmap_sync_wimg` 补丁。XW `osfmk/arm/pmap/pmap.c:10776` 附近只有 RT 才走刷新路径，也就是说**把系统 BO 搬到 10063 而不带这一块，写合并页上可能残留脏行**【读码，后果是推测】。

上游“17 和 27 都维护”的说法要打折扣：除上面第 2 点，U `dc8e5de` 的说明写着 iPadOS 17 构建此前会因缺 `os_volume_uuid` 停下，到 11-01 才修。

## 2. 把根卷换到 iPadOS 27

### 2.1 上游 27 路径的真实形态

- 没有 Darwin 26 的 XNU 源码。内核是 `xnu-12377.121.6`（Darwin 25）加补丁补调用和 KPI（`profile.toml:5-7`，`darwin26-syscalls.patch` 476 行、`darwin26-kpi.patch` 549 行）【读码】。
- Apple 原版 kext 从 J820 的 fileset 里取，靠代码相似度把 kext 调用配到我们编的内核；配不上的函数绑到 `unixnu_fileset_unmapped`，运行到就 panic（`profile.toml:36-39` 的 `--allow-missing`，`fileset-unmapped-call.patch`）。XnuPrelink 的自述写得很直白：配对是从去符号代码推出来的，能生成不代表 ABI 或启动兼容（`README.md`）【读码】。
- 非 Apple 硬件上全部进程共用一套 PAC 密钥，PAC 不做进程隔离（`arm64e.md:54-56`）。
- sheng 上确实跑起来了：SH `e9865b0`（10-28）“sheng boots iPadOS 27 24A446”，`gpu.md:108-113` 有 27 上的实测。但 SH `porting.toml` 的 `arm64e-os` 区域仍写 `planned` 和“内核未启动”，是过期记录。

### 2.2 PAC/arm64e 要求与 Oryon

要求来自 `U/docs/zh/porting/arm64e.md:16-24`。

| 要求 | 13T 状态 | 证据 |
| --- | --- | --- |
| 每核 FEAT_PAuth（地址 + 通用密钥） | **未实测，大概率满足** | 八核都是 implementer `0x51` part `0x001`（10-29 安卓抓取，会话记录 `5442641b` 第 365 行）；`dalvik.vm.isa.arm64.variant=oryon`（`一加13T原厂备份/设备信息/系统属性.txt:81`）；本机 clang 23 `-mcpu=oryon-1` 有 `__ARM_FEATURE_PAUTH=1`、`__ARM_FEATURE_BTI=1`【实测】。LLVM 的描述只是间接证据，而且是 X Elite 那一代 |
| EL1 能写五组密钥不陷入 | **未实测** | 我们 XNU 跑 EL1 在 Gunyah 之下。sheng 同样在 Gunyah 下通过了，但 SM8750 的 Gunyah 是否清 `HCR_EL2.APK/API` 没人测过 |
| PACIA/AUTIA 每核可用 | **未实测** | 安卓内核 6.6.89 有 `CONFIG_ARM64_PTR_AUTH=y`、`CONFIG_ARM64_PTR_AUTH_KERNEL=y`（从 `boot_a.img` 取 IKCONFIG【实测】），内核命令行没有 `arm64.nopauth`。这是通用 GKI 配置，只说明“有就会用”，不证明硬件有 |
| 已有 SoC 支持 | 满足（社区） | `UniXNU-sm8750-common`；但没有 xnu-12377 系列 |
| 原生 4 KiB 一级翻译 | 满足 | 17.7.11 已在用 |

SME：`arm64e.md:61,82` 把“dyld 缓存里的 SME 代码是否按能力选择”列为待确认。sheng 的 CPU 没有 SME 也进了桌面，这条实际上不构成阻碍【推测】。Oryon 同样没有 SVE，Apple 的通用代码不依赖它【推测】。

**确认办法**：安卓下 `adb shell cat /proc/cpuinfo | grep -m1 Features` 看有没有 `paca pacg`，十秒钟、不用重启（等用户方便时做，本次未执行）。要彻底确认再加一个内核探针：每核读 `ID_AA64ISAR1_EL1`、`ID_AA64ISAR2_EL1`，做一次 PACIA/AUTIA 往返。我们的 `PaganiDarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:653-655` 已经打印 `ID_AA64MMFR0/PFR0`，但没读 ISAR1。

### 2.3 根卷来源

- 官方 IPSW，`profile.toml:17`：`iPad17,1,iPad17,2,iPad17,3,iPad17,4_27.0.1_24A446_Restore.ipsw`。HEAD 返回 200，11,654,176,243 字节【实测】。这是 M5 iPad（`chip_id` 33090 即 0x8142，`j820ap`）。
- 远程读 ZIP 目录：最大两个镜像 `043-68950-668.dmg.aea` 7.69 GiB、`043-69086-713.dmg.aea` 2.19 GiB，都是 AEA 加密【实测】。解密要 macOS 的 `aea` 和 `ipsw fw aea --key`（`U/scripts/unixnu/ipsw.py:196-209`），本机两个工具都在。
- `unixnu` 分区 64 GiB（`board.toml:132-133`）【实测计算】，放得下；原始大小要解开才知道。
- 换根卷必须重写 `unixnu` 分区，这与“不往手机存储写数据”的现行规则冲突，要用户明确同意。现有 17.7.11 的 OS 卷就在这个分区里，同时保留两套要改窗口布局，我没看到上游有同分区放两套系统的做法（未细查）。
- iPad7,1（我们现在的根）不会有 27。我们想要的 iPhone 文件（如 `KBLayouts_iPhone.dat`）要另取 iPhone 的 iOS 27 IPSW，iPhone16,1 是否有 27 我没核实。

### 2.4 内核补丁试打【实测】

取 `xnu-12377.121.6`（`ac9718f`）。上游 arm64e 系列 64 个、SoC 2 个、OS 4 个、sheng 设备 8 个全部干净通过。在此之上按我们的顺序累计打（GNU patch，fuzz 2）：

| 结果 | 补丁（失败块/总块） |
| --- | --- |
| 全过（含模糊） | `native-arch-timer-frequency`、`ticket-lock-diagnostics`、`phone-staged-apps-migration`、`phone-staged-apps-trace`、`phone-boot-facts`、`zzwc-…-grant-all`、`phone-keyboard-trace`、`phone-diag-sysctl`、`phone-system-apps-view`、`phone-gpu-client-memlimits` |
| 部分失败 | `sm8750-smp` 3/6（`start.s`、`sched_clutch.c`、`board_config.h`）；`pagani-bootstrap` 2/7；`zzzz-boot-progress-panic-output` 2/3；`zzzz-early-boot-progress` 5/17；`zzzzz-root-panic-progress` 2/16；`zzzzzz-vm-bootstrap-progress` 4/19；`crash-trace` 1/3；`phone-haptics-overlays` 2/3；`phone-controlcenter-settings` 2/3 |
| 全失败 | `zzzzzzz-early-framebuffer-page-tables` 1/1；`phone-wallpaper-collections` 2/2；`phone-keyboard-layouts` 2/2；`pagani-audio-addon-mount` 2/2；`phone-torch-overlay` 2/2 |

合计 128 块失败 32 块，其中 14 块集中在 `kern_exec.c` 的同一片挂载代码。这是下限：基线借用了 sheng 的设备层，前面补丁失败也会连带后面。文本冲突是小头（**几天**【推测】），语义才是大头：这批补丁都假定 21H461 的路径和文件。13T 真做时还要自己写一份 bring-up，BRINGUP 有 2002 行。

### 2.5 内核驱动

全部要重编为 arm64e，且对 24A446 的 ABI 重新绑定。上游的做法：子类虚表每个槽位按原版判别值重签，sheng 的 HID 有 220 个判别值（SH `porting.toml` 输入区），`gen-hid27-abi.py` 从 kernelcache 抽 207 个槽；显示有 `legacy-display-24A446.json`（32 KB）和 520 行的 `build-gop-legacy-display-27.py`；存储换成 `build-sheng-source-storage.py`；SH 记录还写着 16 位搜索判别值会撞到同一个 24 位签名，导致触摸失效【读码】。

我们要走同样流程的组件：`NativeUfs`、`NativeMobileDisplay`（358 槽虚表）、`PaganiInput`、`Adreno830`（换 `ioaccelerator-base-abi-24A446.json` 方案）、`NativeKeyStoreEvents`、`PaganiSensors`（10 个内核地址字面量）、`PaganiAudio`、`PaganiWifi`、`PaganiTorch`、`PaganiBattery`、`PaganiHaptics`、`PaganiArmCpu`、`PaganiDarwinBoot`。上游的 27 专用脚本写死 sheng，不能直接用。

用户态：Turnip `--target ios27`、插件 arm64e、Rust PAC 改写、`PaganiAudio` 的 HAL 必须 arm64e（SH 记录：`dlsym` 返回已签名指针，音频时钟的取法要改）、诊断守护进程。

### 2.6 界面修复要重做多少

完整包里有 **9 处 dyld 字节修补**：配置文件 4 处（`profile.toml:41`），加键盘 F2、Core Image、灵动岛（`工具/make-a830-layout.sh:151,163,174`）和 `ui-rest/integrate/vt-fixups.txt` 2 处。地址和字节全部对应 21H461，换系统全部作废。

| 修复 | 现在靠什么 | 换 27 后 |
| --- | --- | --- |
| iPhone 身份与灵动岛 | ADT 覆盖；`make-a830-layout.sh` 第 180 行起；状态栏字面量修补 | 根变成 J820，jetsam、音频、触感按 target-type 取表，全要重摸；修补重新逆向 |
| 键盘 | `KBLayouts_iPhone.dat`（21H16）+ 405.33 点布局族 + F2 | 要 27 的 iPhone 文件；`.dat` 格式未核实；F2 重新逆向。用户现在抱怨的延迟也不会自动消失 |
| Core Image 无 GL、VT 解码修补 | 3 处修补 | 重查 27 是否还走这些路径 |
| 控制中心 | 从 `~ipad` 派生 `~iphone` plist | 文件结构要重查 |
| 壁纸 | 叠加层 + `phone-wallpaper-collections.patch`（文本全失败） | 上游单独做了 `--wallpaper27`、`--wallpaper-floating`（SH `scripts/pack27-gpu.sh:7`），说明格式变了，重做 |
| 系统 App | staged apps + `/Applications` 视图 | 补丁文本能过，要重验 27 根卷的目录和名单（`make-phone-seed.py` 写死 21H461 的 29 个 App） |
| 小组件 | `FamilyAnswer.h` 等 | 上游 `4d789e9` 有对应回答，要重验 |
| 触感 | J120 叠加层 | 路径改 J820，重做 |
| 音量、音频 HAL、手电筒、传感器 | 21H461 专用路径和构造器 | 重做 |
| jetsam 下限、诊断 sysctl | 内核补丁 | 文本能过，小改 |
| 种子 | `build-userspace`、`工具/make-phone-seed.py` | 按 24A446 重建；上游有 `seed-data-skip-setup.py` 的 27 分支和 `make-nosetup-seed27.py` |

### 2.7 值不值

**现阶段不值。** 理由按分量排：

1. **解决不了用户现在的问题。** 键盘延迟、小组件、亮度、声音、Wi-Fi 都不是 17.7.11 造成的。声音、Wi-Fi 卡在 ADSP 和 PCIe，与系统版本无关。
2. **GPU 收益不需要换系统。** 见 1.5、1.6。
3. **很可能更慢**【推测，有上游数据支持】：`gpu.md:92-93` 里 sheng 上 iPadOS 27 的锁屏，每个命令缓冲 GPU 时间均值约 17 ms，比 120 Hz 的一帧还长。27 的界面更重（液态玻璃），我们这边用户已经满意流畅度，换过去有回退风险。
4. **地基不稳**：没有对应 XNU 源码，kext 调用靠推断配对，配不上就是运行时 panic。
5. **代价大**：第 2.4–2.6 节全部重走。上游从 10-26 动手、10-28 起机、到 11-01 还在调帧，这是两个提交者在已经完整的 sheng 上做的；13T 驱动更多，还叠着 iPhone 身份。回到今天的功能面**乐观 3–5 周，含大量上机**【推测】。用户对反复重启很敏感（`docs/PROGRESS.md` 规则段）。

换系统的好处是真实的：新界面、上游主力维护方向（`arm64e.md:10`）。建议的时机：ADSP、声音、Wi-Fi、存储都在 17.7.11 上定型之后再评估。现在只做一次 `/proc/cpuinfo` 确认，把结果记进 `porting.toml`。

## 3. XNU 补丁

### 3.1 `patches/xnu/`：零新增【实测】

- 我们 `UniXNU/patches/xnu` 与上游 main 逐文件比较：**内容全部相同**，上游只少一个文件，就是我们自加的 `native-arch-timer-frequency.patch`。`profiles/arch/arm64/.../series` 也只差这一行。
- `git diff ae68700 origin/main -- patches/xnu` 为空。17 配置只多 `2cdf5d9`、`dc8e5de` 两处（`[userdata]`、`[root]`）。
- 12377 同名补丁与 10063 版本的新增行对比：除 `source-corecrypto14-bootstrap`（fileset 下让原版 corecrypto 成为唯一提供者）外，都是移植差异，没有 10063 缺的修复。
- 12377 独有的 18 个补丁：`native-4k-block-split`、`native-4k-shared-region-slide`（v5 slide 按 16K 分片）、`trustcache-runtime-layout`、`native-security-init`、`fileset-unmapped-call`、`arm64e-threaded-starts`、`darwin26-*`、`native-pac-keys`、`public-*`、`bcm2837-machine-config`、`no-apple-cpu-counters`、`apple-only-core-type`、`optional-kernel-support-archive` 都只服务 12377/fileset，对 10063 **没有用**【读码】。

### 3.2 真正有用的在设备层

以下来自 BRINGUP（SH `patches/xnu-12377/`，行号是该补丁的行号）。用脚本把 BRINGUP 拆成 101 块，逐块对 XW 副本做 dry-run，89 块可落（含模糊）【实测】。只说明文本能打上，**都没有编译和上机**。

| 优先 | 修复 | 位置 | 试打 | 与我们的关系 |
| --- | --- | --- | --- | --- |
| **高** | bindfs 支持按文件 ID 查找：加 `bindfs_vget`，保留 `PERSISTENTOBJECTIDS`/`PATH_FROM_ID`，设 `MNTK_PATH_FROM_ID` | 408-506 | 4/4 过 | 见下 |
| **高** | 放行 `io_connect_map_memory_into_task`（2863、2864）过沙盒 kobject 过滤，只限 `AGXDeviceUserClient` | 646-660、1316-1335、1485-1501 | 3 个文件，`ipc_kobject.c` 第 2 块要手工 | 见下 |
| 中 | `hw.pagesize` 读取绕过沙盒：WebKit GPU 进程的沙盒不让读，页大小变成 -1，GPU 用户态拒绝所有 IOSurface | 376-377 | 过 | 要先核对 17 上是否复现 |
| 中 | `owner-dataprotect=1`：无内容保护的卷上模拟数据保护类 | 534 起 | 4/4（1 模糊） | PosterBoard 见不到保护类就判数据库不可用、没有壁纸。我们有没有同样症状要查 |
| 中 | `pmap_batch_set_cache_attributes` 改属性时清缓存行 | 961 | 模糊 | **只要移植系统 BO 就必须带** |
| 中 | `native_pmap_user_mappings` | 988-1052 | 过 | 系统 BO 链接需要；PG `patches/xnu/gpu-user-mappings.patch` 已适配 10063，可直接参考 |
| 低 | `PE_builtin_boot_args`：内核内置启动参数 | 1961 | 过 | 我们命令行常年贴近 1000 字节上限（`docs/PROGRESS.md:605`），工程上很实用 |
| 低 | `SIGPdisabled` 恢复 | 798-828、1151 | 模糊 | 上游见过随机挂起和空闲核 100% 忙。我们日志里 CPU 负载正常（`ld 15/5%`），当防御用 |
| 低 | `HAS_16BIT_ASID` | 1998 | 模糊 | XW 已有这个宏的全部逻辑。硬件 ASID 255 个，进程数不超过时没有收益（`pmap.c:4572-4595`） |
| 不要 | `wimg_to_pte` 的 `pa_valid`、`cpu_start` 缓存刷新 | 915、- | - | 前者针对多段 DRAM；后者 `PaganiArmCpu/ArmCpu.cpp` 的 `cleanToMemory` 已做 |

**关于小组件（最值得先看）**：

- 上游注释（BRINGUP:441-445）：LaunchServices 按卷和文件 ID 记录包里的文件，再用 `fsgetpath` 解析回来；没有 `vfs_vget` 时，经 bind 挂载看到的每个包都解析失败，于是注册成没有平台、插件没有扩展点，“a widget extension then looks "restricted" to chronod”。
- 我们的处境相同【读码】：`phone-system-apps-view.patch` 用 `native_gpu_addon_mount` 把 `/private/var/staged_system_apps` 叠到 `/Applications`，后者是 bindfs 联合挂载（`zzy-native-gpu-addon-mount.patch:38-47`，`MNT_RDONLY | MNT_UNION`）。XW `bsd/miscfs/bindfs/bind_vfsops.c:420,424` 明确去掉了这两个能力，`bindfs_vfsops` 表里也没有 `.vfs_vget`（552-560 行）。`MNTK_PATH_FROM_ID`、`vnode_lookupat`、`VFS_VGET` 在 10063 都有。
- 我们之前的结论把小组件全黑归因于 RenderBox 的像素格式（`docs/PROGRESS.md:492`），`FamilyAnswer.h` 修的是这一层。用户说修完仍不能用，说明可能还有下一层。这条是推测，**需要 `chronod`、`pluginkit` 或 `lsregister` 这一路的日志验证**，本次没有对应日志（`runs/20261031-134632-a830-full-1-1/merged.log` 是空的）。
- kobject 过滤：10063 有这条路径（XW `osfmk/kern/ipc_kobject.c:593-601`），21H461 的 Sandbox 里有 `mach-kernel-endpoint` 操作和 `io_connect_map_memory_into_task` 字符串【实测，kernelcache 偏移 0x100ec2b、0x1010a4c】。上游注释说沙盒 Metal 客户端（`iconservicesagent`、海报扩展）到不了这个调用。我们的 `iconservicesagent` 作为 Metal 客户端跑到过 jetsam（`phone-gpu-client-memlimits.patch` 的注释），没见到映射被拒的记录，但这不算证明；小组件扩展是否被挡要看日志。

### 3.3 其他层

- SH `patches/xnu/`（上游 17 用）：`zzyf-sheng-amp.patch` 开 Edge 把大小核分簇，对应我们待办“调度认得 2+6 核”，但 SM8750 全是 Oryon，且我们的 Clutch 单 pset 是修 panic 换来的（`sm8750-smp.patch`），现在不动。`zzye-sheng-cpu-idle.patch` 我们不需要，调速器已用 `processor_info`。`zzyd` 是音频叠加挂载，我们有等价物。
- PG `patches/xnu/series` 相对 `cdd273d` 只多 `gpu-user-mappings.patch` 一个。
- 上游 `sm8550-common` 的 17 系列无变化（`a9f45dc` 只加 12377）。

## 4. 模块版本对比

`components.toml` 里的版本和实际构建用的不是一回事。我们 `UniXNU/components.toml` 还写着旧版本，子模块实际更新；而且我们的 `board.toml:21` 选的多是社区分支，不是这几个官方模块【实测】。

| 模块 | 我们表中 | 我们实际 | 上游 main | 我们是否编进内核 |
| --- | --- | --- | --- | --- |
| ArmCpu | `9066b53` | 子模块同 | `93a5030`（+2） | **否**，用 `PaganiArmCpu`（工作树已改 540 行） |
| GopFramebuffer | `cf3b429` | 子模块 `33e941a`，`NativeGOPWire.h` 有本地改动 | `19ef3a7`（+7） | **否**，用 `NativeMobileDisplay`，只用头文件 |
| XnuPrelink | `f876e3e` | 子模块同 | `b74ed7c`（+3） | **否**，用 `NativePrelink`，`emit_prelink.py` 与基线逐字节相同 |
| ArmSmmu4K | `8bb168a` | 子模块 `3ba9245` | `83969cd`（+4） | **是**，`Adreno830/scripts/build-adreno830.py:331` 编 `kits/ArmSmmu4K` |
| DarwinBoot | `3325e35` | 子模块同 | `3c75734`（+1） | **否**，用 `PaganiDarwinBoot`（与基线差 92 行） |

许可证：UniXNU、ArmCpu、GopFramebuffer、XnuPrelink、DarwinBoot、sheng、sm8550-common、Pagani 都是 BSD-2-Clause-Patent；Adreno740、AdrenoMetal、ArmSmmu4K 是 MIT。都可以移植，保留版权声明即可【实测】。

### ArmCpu

- `eb0f053`：`parkCore` 把失败的副核停在 WFI 并关定时器和中断。注释说 Gunyah 拦 WFI 并阻塞 vCPU，但 WFE 在客户机里跑，停在 WFE 的 vCPU 会占住物理核直到看门狗重启手机。**我们 `PaganiArmCpu/ArmCpu.cpp:1038-1039` 正是 `for(;;) wfe`**，同样在 Gunyah 下。建议移植。
- 同一提交的 `waitClear`：计数按轮询次数加时间，避免 vCPU 被调度走时 100 ms 超时误判。我们 `ArmCpu.cpp:169-180` 还是纯时间。建议移植。
- IRQ 排空计数：诊断，可选。
- `clusterType`、`ml_processor_register` 次序、`ml_cpu_init_completed`：服务 27 和 Edge，17.7.11 单 pset 不需要。
- SPI IPI 挂起位：`93a5030` 已撤回，别移植。
- **影响**：低风险，只动副核失败路径和 GIC 轮询；不涉及内核 ABI。

### GopFramebuffer

- 直接升级对内核**无影响**，因为没编进去。
- 对用户态**有影响**：AM main 的 `GpuScanoutPresenter.h:17` 包含 `../GopFramebuffer/NativeHalfCodes.h`，并用 `NGP_RGBA16F_*`。升 AdrenoMetal 必须同时合并 `NativeGOPWire.h`，保留我们的 `NGP_METHOD_BAND`、`NGP_METHOD_WAIT_SWAP2`。
- 功能上：`bf854d2` 我们已有（`NativeGOPMappingCache.h`，六项）；`cb4459b`、`570a932` 是 sheng 的 DPU 直出和诊断；半浮点只对 27；`19ef3a7` 是诊断计数。**没有需要移植到 `NativeMobileDisplay` 的功能**。

### XnuPrelink

- 三个提交全是 fileset。`emit_prelink.py` 对普通 arm64 只改了检查顺序。**对 17.7.11 零收益**，不升。

### ArmSmmu4K

- `9fcf406`（每次扫描每个 2 MiB 叶表只走一次）和 `48dbd6c`（按需页表）有价值，但**接口破坏**：`ArmSmmu4KLiveArena` 去掉 `mapping`、`capacity_pages`，改用 `ArmSmmu4KTablePages` 回调（`ArmSmmu4KLive.h`）。Adreno830 的 `NativeAdrenoVm.cpp:33,287` 直接用 `capacity_pages`，单独升级编不过【读码】。
- `ttbr0_switched`、`ArmSmmu4KWriteContextTtbr0`、`detached` 是每进程页表的基础。
- **影响**：必须和 Adreno830 的内存层一起动。若只想要提速，可单独把 `9fcf406` 的“记住上一张叶表”思路移植到旧接口上，这个改动局部。

### DarwinBoot

- `3c75734`：解析器接受 arm64e 子类型和 fileset，加 `.vroot` 根策略旁路文件。U `docs/zh/drivers/darwin-boot.md` 还写“不支持 `MH_FILESET`”，与代码不一致，以代码为准。
- 对 17.7.11 **零收益**，而且改加载器要重编固件，风险不小。不升。

### 其他

`RamBlock` `4be2d7e`（持久 Data 重建条件）、`VerifiedRoot` `974f043`、`VirtioMmioBlock` `4e1013e` 都服务 27 的存储路径和持久 Data，等做持久存储时再看。

## 5. 建议顺序

1. **先验证小组件线索**：下一轮日志看 `chronod`、扩展注册；若吻合，把 bindfs 三处改动做成 10063 补丁（文本已确认能落）。kobject 放行同批考虑。
2. **低风险小改**：ArmCpu `parkCore`/`waitClear`；翻译器 QoS（`0023` 的思路可直接搬到我们的 `720ef1c`）；`hw.pagesize`（先确认症状）；`PE_builtin_boot_args`。
3. **用分段法量抖动**：照 `gpu.md:98-106` 拆“打开应用”这段时间，再决定动哪块。不要先大改。
4. **翻译器升级到 `624bab39`**：要重新适配我们的 `0017`、`0018`。量大但边界清楚，翻译器与 A830 无关。
5. **Adreno830 内存层**（系统 BO、按需页表、每进程页表、ArmSmmu4K 一起）：最大的一块，建议参考 Pagani 的 A830 落地，并带上 3.2 的 `pmap_sync_wimg` 和 `native_pmap_user_mappings`。
6. **iPadOS 27**：只做 `/proc/cpuinfo` 确认和记录。

## 6. 未核实清单

- Oryon 的 `ID_AA64ISAR1/2_EL1`、Gunyah 对 PAC 密钥的处理、SM8750 的 `paca pacg`。
- iPhone16,1 的 iOS 27 IPSW、27 的 `KBLayouts` 格式、27 控制中心和壁纸的文件结构。
- 第 3.2 节所有补丁都只做过文本试打，没有编译。
- 小组件与 bindfs、kobject 的关系是推测，没有 13T 日志。
- `hw.pagesize`、`owner-dataprotect` 在 21H461 上是否同样复现。
- 第 1.5 节“我们现状”是关键字级别的检索，没有逐函数比对 Adreno830 与 Adreno740。
- 工作量估计全部是推测。
- 上游数字来自 sheng 的 iPadOS 27，不能直接换算到 13T。
