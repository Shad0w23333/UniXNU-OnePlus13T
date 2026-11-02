# AdrenoMetal 上游差异评估与试合并报告

- 日期：2026-10-31（本机时钟）
- 范围：`ce5e19e..origin/main`（`831a9dc`），我们的工作树 `UniXNU/drivers/AdrenoMetal`（含未提交修改和未跟踪文件）
- 全程只读：没有碰手机，没有写我们的源码树和上游克隆，没有提交或推送。所有试验都在草稿目录。
- 证据标记：【读码】读源码确认；【实测】本次在本机主机上实际跑过（没有真机）；【提交说明】上游提交里的说法，未独立验证；【他人记录】Pagani 仓库 `STAGE6.md` 的运行记录；【推测】没有直接证据。
- 简写：`SP` = `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/metal`；FE = `NativeBridgeMetalFrontend.mm`；PL = `NativeAdrenoMetalPlugin.mm`。上游文件行号都指 `origin/main`。

## 0. 结论速览

1. **能合。** 到 `831a9dc` 的试合并有 7 个文件、27 处冲突，全部解开后在 `arm64-apple-ios17.0`、`-Wall -Wextra -Werror` 下通过编译，Rust 桥接带 fat LTO 编出发行版，插件成功链接【实测】。
2. **真实增量是 67 个提交，不是约 80 个。** 159 个非合并提交按标题去重得 92 个，其中 24 个已在我们基线历史里，`784edef` 的内容也已在 `ce5e19e`。main 的树与 `origin/feat/ipados27-gpu-parity` 完全相同（`59c43b7d`）【实测】。
3. **最大风险不在代码冲突，在翻译层。** `720ef1c → 624bab39` 跨 799 个提交，`crates/` 三个包 1428 个文件变化。上游 24 个补丁能干净应用；我们的 0018 要三方解开，0017 要手工移植，两者都已在草稿里移植并通过编译【实测】。
4. **直接到 main 有一处实测回退：** A830 主机模拟里，App 的 124 MiB 窗口能放的 1 MiB 纹理从 87 MiB 降到 54 MiB，`texture_atlas_host` 的 slot 4 判 FAIL。原因之一已定位到桥接补丁 0021 的备用块（10 s）；另有约 1 s 的延迟释放，原因未定位【实测】。
5. **有一个已验证的中间落点：** 合到 `d6ca655`（前 39 个提交），翻译层仍是 `720ef1c`。这条路 21 处冲突，同样编译链接通过；A830 模拟三个窗口全过，App 窗口仍是 87 MiB【实测】。
6. **这一步拿到大部分功能缺口：** `newLibraryWithSource:`、拼接库（Core Image）、曲面细分、带 LOD 钳制的采样器、管线反射、堆大小答复、MPS 需要的属性。主机 Core Image 测试：我们当前树崩溃，两条合并路径都通过【实测】。这只说明缺口存在；Mac 的 Core Image 不等于 21H461 的。
7. **小组件能否因此修好未知。** 上游有几项直接相关（链接函数、拼接库、IOSurface 使用计数），但没有证据能断定其中哪一项就是我们现在的原因【推测】。
8. **流畅度收益要保守看。** 编码线程是上游在 iPadOS 17 上测的：桌面拖动约 61→67 fps【提交说明】。Pagani 在同款手机、同一 iPadOS 17.7.11 上用 `624bab39` 反馈“明显更流畅”，但也出现**颜色跳变**，切 `sysmem` 才恢复，后续读数 28–40 FPS【他人记录】。
9. **很多“iOS 27”内容我们用不上：** 8-bit 帧缓冲、半浮点转换、PAC 边界、EDR 开关都只在 `__arm64e__` 或 iPadOS 27 路径。mimalloc 已被上游自己撤销。
10. **上游 main 原样在 iOS 17 目标下编不过：** `patchFramebufferBranch` 未使用触发 `-Werror`【实测】，Pagani Run 87 也撞到【他人记录】。
11. **`624bab39` 已不在 metal2vulkan 任何分支或标签上**（`development` 被改写过）【实测】，要先留本地副本。上游 24 个补丁也能用在 master 可达的 `0dfdbcd5` 上，编译通过【实测】。
12. **推荐：分三步整体跟进，不做逐提交挑拣。** 第一步到 `d6ca655`（外加 `547312c` 的前端部分和 `dfe4eeb`），不换翻译层；第二步换翻译层到 main，先解决窗口回退；第三步才动 Turnip 和内核。详见第 8 节。

## 1. 基线与方法

- 克隆：`SP/repo`（来自 `参考资料/上游-20261031/UniXNU-AdrenoMetal`）。
- 我们的快照：分支 `ours-base`（`116beb1`），在 `ce5e19e` 上用 rsync 覆盖工作树后提交，29 个文件，+2282/-105。
- 试合并：分支 `trial-merge`，保留在冲突状态，便于复核。第二条路径在 `SP/repo2`，分支 `trial-d6ca655`。
- 去重：非合并提交 159 个，按标题唯一 92 个。24 个标题已在 `ce5e19e` 的历史里（rebase 副本），例如 `39ba232` 对应 `461a842`。`784edef` 标题不在基线，但 `ce5e19e:PrimeShaders.h:134` 已是 `commits > 8`。合并提交 `d0512ed` 的说明也写明其余分支都已并入或被取代。
- 作者分布（67 个）：HuJK 29、bluseliu50 22、rpone 16。HuJK 这批在 10-24 到 10-27，代码注释按 21H461 写，FE:4380 也保留 iPadOS 17.7.11 的基类大小检查，所以这批可认为是在 iPadOS 17 上开发的【读码+推测】。后两位从 10-28 起主要面向 iPadOS 27。

## 2. 试合并：到 `831a9dc`

### 2.1 结果

`git merge origin/main` 后 7 个文件冲突、27 处。另有 43 个文件自动合入：22 个新增、10 个删除、9 个修改、2 个重命名。`SP/repo/NativeBridgeMetalFrontend.mm` 合并后约 4800 行。

### 2.2 全部冲突

行号是 `SP/repo` 冲突状态下的文件行。解法已全部落在 `SP/resolve.py`。

| 文件 | 行 | 性质 | 建议解法 |
| --- | --- | --- | --- |
| `GpuScanoutClient.h` | 108–117 | 我们用 `NGPSwap2`（目标偏移、状态带），上游加 `swap.format` | 取我们的 |
| `GpuScanoutClient.h` | 125–132 | 我们有翻页计数、`marked`；上游只校验 BGRX | 取我们的 |
| `GpuScanoutPresenter.h` | 4–17 | 文件头注释 | 取我们的 |
| `GpuScanoutPresenter.h` | 48–55 | `Frame` 字段：我们 `targetOffset`、`bandY0/1`；上游 `format` | 取我们的；整个文件用 `ours-base` 版本 |
| `PrimeShaders.h` | 40–49 | PR 行注释和计数：我们加状态 4，上游加 `native_prime_functions_resumed` | 两边都要 |
| `bridge/NativeMetalObjects.h` | 249–253 | 同一函数的注释 | 取上游 |
| `bridge/NativeMetalObjects.h` | 280–287 | 字段：我们 `resolve`，上游 `resolve_texture` + `resolve_filter` | 取上游（超集） |
| `bridge/NativeMetalObjects.rs` | 2418–2432 | 同上 | 取上游 |
| PL | 8–17 | 文件头：我们是名单，上游是“每个进程都给设备” | 取我们的 |
| PL | 29–34 | 头文件 | 两边都要 |
| PL | 210–232 | 我们的 `listedClient()`，上游已删 | 取我们的 |
| PL | 261–276 | `clientFailure`：我们发报告行并 `delete gpu`；上游排 6 个延迟长文 | 两边都要；**上游延迟块还要用 gpu**，只在没排长文时释放 |
| PL | 300–304 | `clientDevice` 入口的名单判断 | 取我们的 |
| PL | 583–589 | `gDiagnostics` 与注释 | 取我们的 |
| PL | 626–630 | 遥测线程开关：`.diag` 文件 对 `SHENG_DIAG` | 取我们的；见 2.3 的 S7 |
| FE | 39–51 | 头文件 | 取上游（超集） |
| FE | 161–165 | 计数器：`gCompletedErrors` 对 `gWaitTimeouts` | 两个都留 |
| FE | 662–675 | `NBLOG` | 取上游（`GLOG`、stderr 开关） |
| FE | 680–690 | 我们复制过的同一函数，上游抽出 `isRenderServer()` | 取上游 |
| FE | 1343–1368 | 我们的按方向维护，上游 `flushSurfaces` + `kSurfaceFlush` | 取我们的 |
| FE | 2318–2330 | 绑定纹理：我们记 `writtenSurfaces`，上游加 `ENCODE`、`dumpBind`、`watchTexture` | 手工并：先记录，再 `ENCODE` |
| FE | 2651–2660 | blit：我们 `written:`，上游 `ENCODE` + `std::array` | 取上游写法，补 `written:2` |
| FE | 2674–2723 | 三个 blit 方法，上游还加 PVRTC 解码和纹理日志 | 取上游，补 `written:` 参数 |
| FE | 3040–3055 | 解析附件：我们记写入集，上游字段名不同 | 用上游字段，保留写入集 |
| FE | 3141–3162 | `finish`：我们数错误，上游加源竞争诊断 | 两边都要 |
| FE | 3192–3198 | `submit`：`cleanSurfaces` 对 `flushSurfaces` | 用 `cleanSurfaces`，留上游两行诊断 |
| FE | 4470–4493 | `supportsFamily:`：`flagArgAnswer` 对 `familyForCaller` | 取我们的；见 S4 |

### 2.3 git 没报、但必须处理的语义冲突

这些地方自动合并“成功”，留着不改要么编不过，要么运行错。

| 编号 | 位置 | 问题 | 处理 |
| --- | --- | --- | --- |
| S1 | `NativeMetalObjects.rs:2221,2242` | `nmb_command_buffer_metal_error_code` 定义两次 | 删我们的；上游版本带 `FailureOrigin` 过滤 |
| S2 | `NativeMetalObjects.rs` `resolve_of` | 引用已不存在的 `a.resolve` | 删函数 |
| S3 | `nmb_render_encoder_new` | 颜色、深度、模板的解析字段各赋值两遍 | 删我们那几段 |
| S4 | FE 4513–4516 | 上游用 `method_setImplementation` 装 `familyForCaller`，与我们的 `flagArgAnswer` 叠加 | 删这段；否则 `gFamilyAnswer` 未定义 |
| S5 | PL 261–276 | `delete gpu` 后延迟块继续用 `gpu`，释放后使用 | 见 2.2 |
| S6 | `PrimeShaders.h` RGhA 变体 | 每个渲染对多编一条 RGBA16Float 管线，注释写明针对 iPadOS 27 的帧缓冲。21H461 上是白费 | 用 `__arm64e__` 包住 |
| S7 | PL `if (diag) pthread_create(&monitor…` | 自动合入后，我们的 monitor 线程（含内存行）只有 `sheng-diag=1` 才启动 | 恢复无条件启动 |
| S8 | PL `else unsetenv("SHENG_DIAG")` | 启动时清掉变量，`NativeAdrenoMetal.env` 设不了它；`.diag` 也打不开上游新诊断 | 改成 `bootArgSet(...) \|\| diagnostics()` |
| S9 | `bridge/present_host.mm:29` | `{…, 0, NGP_BGRX8888}` 按位置初始化，到我们的 `Frame` 上变成 `targetOffset=1`。编译不报错，测试会判 `kBadFrame` | 恢复我们的写法 |
| S10 | `bridge/scanout_host.mm` | 依赖 `NativeHalfCodes.h`，我们没有 | 不进测试清单 |
| S11 | `bridge/patches/` | 上游新 0017、0018 与我们的同号并存，不报冲突 | 我们的改号排到最后 |
| S12 | `check-env-seeds-a830.py:192-195` | 解析 `setenv("TU_DEBUG","…",0)`。合并后默认值变成表达式，还多了两处覆盖式 `setenv`，脚本会判失败 | 同步改脚本 |
| S13 | PL 96 | `patchFramebufferBranch` 在 arm64 下未使用，`-Werror` 失败 | 加 `__arm64e__` 或 `[[maybe_unused]]` |
| S14 | FE 3853 | `gpuCoreCount` 固定 8。Pagani 按 A830 改成 6【他人记录，`STAGE6.md:1376`】 | 建议改 6；只影响 MPS 的内核选择【提交说明】 |

### 2.4 解开后的验证

都是主机验证，**没有 GPU 真实执行 Turnip 命令流**。`SP/resolved` 是到 main 的解开结果，`SP/resolved2` 是到 `d6ca655` 的结果（含第 8 节的两个挑拣）。

| 验证项 | 当前树 | 路径 A：`d6ca655` + `720ef1c` | 路径 B：`831a9dc` + `624bab39` |
| --- | --- | --- | --- |
| 前端与插件语法（iOS 17，`-Werror`） | 通过 | 通过 | 通过（按 S13 修一处后） |
| Rust `check`（`aarch64-apple-ios`） | 通过 | 通过（37 + 我们 2 个补丁） | 通过（24 + 我们 2 个补丁） |
| 发行版 + 链接（fat LTO） | — | 3 分 16 秒，插件 16.0 MB | 3 分 55 秒，插件 18.5 MB |
| `frontend_host` `nil_args_host` `surface_flush_host` `msaa_resolve_host` `surface_import_host`（MoltenVK） | — | 全过 | 全过 |
| `coreimage_host`（MoltenVK） | **崩溃**：`Failed to load 'ci_filters'`，`-[NBTexture isDrawable]` 未识别 | 通过 | 通过 |
| `renderbox_host`（MoltenVK） | — | 通过，`withheld 1` | 通过，`withheld 1` |
| A830 模拟 slot 4（App，124 MiB） | 87 MiB，通过（旧记录 `findmy/tree/build/texture-atlas-host/slot4.log`） | **87 MiB，通过** | **54 MiB，失败** |
| A830 模拟 slot 1 / slot 0 | 256 / 256 | 256 / 256，通过 | 256 / 256，通过 |

说明：

- 路径 A 的结果是挑拣 `547312c`（前端）和 `dfe4eeb` 之后重跑的。
- 路径 B 的发行版和链接是在加入我们 2 个补丁之前做的；类型检查和主机测试包含这 2 个补丁，MoltenVK 那几项还带着下面的“小堆不留备用块”试验改动。
- 当前插件 21.7 MB（`tree/build/adreno-metal-plugin`，含预热清单段）。上面两个体积没带该段，比较时要留意。结论只是合并不会撑爆启动载荷预算【实测，粗略】。

A830 模拟里两条路径都报告 `Adreno (TM) 830 (raytracing disabled)`，即新翻译层能在我们 gen8 路径的 Turnip 上开设备【实测】。

**路径 B 的 slot 4 回退，进一步试验结果**（`SP/atlas`）：

| 条件 | 容量 |
| --- | --- |
| 原样，释放后立刻探测 | 54 MiB |
| 原样，等 12 s | 78 MiB |
| 小堆不保留备用块（`block_bytes != BLOCK_BYTES` 时不进 `spare`），立刻探测 | 54–57 MiB |
| 同上，等 1.5 s / 3 s | 81 MiB |
| 同上，等 6 s / 12 s | 84 MiB |

结论：备用块保留确实占窗口，但不是全部。还有约 1 s 内释放不及时的部分，原因未定位。`place()` 只在 `release_kept` 后重试一次（打完补丁后的 `memory.rs:2275`）。Turnip 上 `parking` 为空，`let_go` 是同步释放，所以不是 `freed_memory.rs` 的等待【读码】。窗口用尽会让 `newTexture` 返回 nil，Find My 的 VectorKit 就是这样崩的（PROGRESS 08:00 条）。

## 3. 67 个提交分类

“17”列：是否对 iPadOS 17.7.11 有意义。“已有”：我们是否已有等价修改。提交号取 main 祖先链上的一份（另一份是 rebase 副本）。

### 3.1 性能

| 提交 | 内容 | 17 | 已有 | 处理 |
| --- | --- | --- | --- | --- |
| `1353601` `6c7b0a0` `0dd1516` `f1f51bf` | 编码线程：渲染服务器默认开，批量闭包，FE 1967–2070。提交说明：拖动约 61→67 fps，显示线程 90%→34%；GFXBench Driver Overhead 2 掉 12%，所以其他进程不开 | 是 | 无 | 合 |
| `aba35f1` | 绘制诊断文本只在开启时构建 | 是 | 无 | 合 |
| `c363547` `028c0a1` | mimalloc 加上又撤销，因 iPadOS 27 上 arena 预留的大 mmap 触发异常 | 净零 | — | 不需要 |
| `0f11e64` `adc18c5`（部分） `547312c`（前半） | 持有 IOSurface 导入，表面活着就留着。`547312c` 修了一处释放后读取（backboardd 崩溃） | 是 | 无 | 合，补重试（第 7 节） |
| `f42d44a` | 桥接 0021：备用图像块（10 s）+ 空间维护线程 | 是 | 无 | 第二步，须改 |
| `017398d` | 桥接 0020：写入的宿主页纹理任何尺寸都就地 | 是 | 无 | 第二步，**依赖 Mesa 补丁** |
| `ded1122` | Droid-VM 绘制路径 0025–0037，在旧翻译层上 | 是 | 部分 | 第一步随合并进入 |
| `19e5f40` `8c86579` | 翻译层到 `0dfdbcd`、`624bab39`。提交说明：37 个里上游已自带 21 个 | 是 | 无 | 第二步 |
| `a0a34fa`（0023、0024） | iOS 上也用 QoS 类；提交说明：回调线程原先最多晚 50 ms | 是 | 无 | 第二步；第一步可单独试 0023 |
| `7824091` `fc5ca0b` | 高队列优先级、按进程转发。前端都走 `dlsym`，缺符号就是空操作 | 需配套 | 无 | 代码随合并进入，第三步才生效 |
| `11420f4` `805659b` `278333f`（部分） `d1b8b33`（后半） | 帧缓冲保持 8 位：`__arm64e__` 下改 24A446 地址（PL 370–427） | 否 | 不需要 | 不适用 |

### 3.2 正确性

| 提交 | 内容 | 17 | 已有 | 处理 |
| --- | --- | --- | --- | --- |
| `3b4acf1` | 混合 WIP：`nmb_function_new_specialized_zero_rest`（有用）、半浮点转换和 PAC 边界（27） | 部分 | 无 | 合；27 的部分不启用 |
| `c3b5517` | 旋转、解析、间接 dispatch、错误码 | 是 | 解析部分有 | 取上游 |
| `5c03327` | GPU 上下文丢失后结束渲染服务器 | 是 | 有 | 一样，取上游写法 |
| `dc89d3f` | no-copy 按页身份判重，修 CA 缓存里的旧页。提交说明：渲染服务器 262 个范围里 4 个需重映射 | 是 | 无 | 合 |
| `489d61c` `885ce63` `77d40b8` | 运行时编译源码、拼接库，16 MiB 栈 | 是 | 无 | 合（Core Image 依赖） |
| `7677e0d` | `supportsFeatureSet:` 不再恒 NO | 是 | 无 | 合 |
| `1e65fae` | 带 LOD 钳制的采样器和范围绑定；我们缺，调用会未识别选择子 | 是 | 无 | 合 |
| `c4534d9` | 曲面细分管线、因子缓冲、patch 绘制 | 是 | 无 | 合 |
| `624b1ce` | PVRTC 软解为 RGBA8 | 是 | 无 | 合 |
| `29d1cdb` | 管线反射（我们恒为 nil） | 是 | 无 | 合 |
| `aff537e` | 压缩格式线性布局、`supportsFamily` 对 RenderBox、IOSurface 使用计数、`b3a8`、堆大小答复；4 s 等待上限后来被撤销 | 是 | RenderBox 部分有 | 合 |
| `0f11e64`（其余） | `gpuCoreCount`、`bitCodeHash`、`userDictionary`、协议声明（MPS 检查） | 是 | 无 | 合 |
| `f7bf252` | 渲染管线的链接函数，`setResponsibleProcess:` 等 | 是 | 无 | 合（可能与小组件有关【推测】） |
| `adc18c5` | `waitUntilCompleted` 不设上限、编译器路径、局部拒绝释放资源、调度状态 | 是 | 无 | 合 |
| `d6ca655` `dfe4eeb` | Core Image 内核按名给出、`externFunctionNames`。说明写的是 iPadOS 27 的 Core Image，但 21H461 也有这些 metallib | 待证 | 无 | 合，无害 |
| `8c86579`（0017） | 堆纹理大小取对齐倍数 | 是 | 无 | 第二步 |

### 3.3 着色器缓存与预编译

| 提交 | 内容 | 17 | 已有 | 处理 |
| --- | --- | --- | --- | --- |
| `a3896f2` | 源码编译结果缓存到进程 Caches | 是 | 无 | 合 |
| `7183e58` | 预热按函数续作（`prime-fn`）、RGhA 变体、直方图 | 部分 | 无 | 第二步合，RGhA 门控（S6） |
| `19e5f40` `0f6050f` `d1b8b33`（前半） | 主机预编译经 userdata 导入：加了又删，最终只在设备上编 | 净零 | 我们走 `m2vpack` | 不需要；`importPack` 仍在 |
| `b1a4d0e` | `sheng-gpu-prime=0` | main 的插件已无此判断（PL:570 无条件起线程） | 我们有 `METAL2VULKAN_PRIME=0` | 保留我们的 |
| `e25c5a4` | 预热不算“问题” | 是 | 无 | 合 |

### 3.4 诊断

全部默认关闭：FE:217 `diagOn()` 依赖 `SHENG_DIAG`，其余看 `/private/var/ngpu/debug/*` 或环境变量【读码】。关闭时代价是一次分支。

`10f8b8d` `42c4c42` `1928076` `56948c8` `30cd15a` `0cbcd0f` `1d68494` `18a074b` `fa52ed5` `f9a4b0c` `e25c5a4`（行） `94af9e8` `ca7faaf` `1653279` `b1a4d0e`（站点） `0e63b28` `44d2621` `8c7297e` `de38c6f` `eb4b68f` `7b885c2` `809075b` `7e0d3d2` `4250dde` `77cb207` `f46fc88` `a0a34fa`（`log-gpu`）

其中 `f46fc88` 让 NSLog 和管线日志跟随 `log-gpu=1`，相当于默认少写日志，是顺带的正收益【读码】。Turnip 环形缓冲相关的诊断要配套 Turnip 才有数据。

## 4. 与我们已有修改的逐项对比

| 主题 | 我们 | 上游 | 判断 |
| --- | --- | --- | --- |
| RenderBox 家族答复 | `FamilyAnswer.h`，`flagArgAnswer`（FE:1920），带单测和计数 | FE:4330 `familyForCaller`，`strstr` 路径 | 行为等价（都是 Apple3 起不告诉 RenderBox）。**我们更好**：路径末段精确匹配、有 `withheld` 计数、有主机测试 |
| 客户端报告 | `ClientReport.h` `MemoryReport.h`：`0x7ffe` 报告行，名字、状态、内存 | PL `clientFailure` 头尾截断 + 长文分段，`startPassReporter` | 互补，两边都留。我们的能进 G 行，上游的长文更完整 |
| 谁能拿设备 | `ClientRule.h` 名单、`@apps` `@extensions` | 所有进程 | **必须留我们的**：我们的内核是窗口表，默认 4 GiB 只有 12 个槽（`NativeAdrenoPublish.h:26-28`） |
| 块大小 | 0018：块不超过堆的 1/16 | 0013：从 4 MiB 翻倍到 64 MiB | 互补。`next_block_bytes` 被 `block_bytes` 封顶，所以两者叠加后：124 MiB 窗口固定 4 MiB，大窗口才增长【读码，已编译】 |
| 保留命令缓冲 | 0017：`KEPT=16`，备用命令池上限 | 无，`spares.rs` 仍是“永不释放” | **留我们的**，要手工移植（已在 `SP/m2v` 做过） |
| 备忘录上限 | `METAL2VULKAN_MEMO_MIB`，默认 32 MiB | 默认 256 MiB | 留我们的，字段在 `624bab39` 仍存在 |
| 表面缓存维护 | `SurfaceMaintenance.h`：按方向、看内核一致性级别 | iOS 27 关掉，否则双向 `sys_dcache_flush`（FE:1319-1343） | **我们更好**：iOS 17 上上游等于没改 |
| 解析附件 | 2026-10-31 自加 | `c3b5517` 超集，含深度模板过滤 | **取上游**，保留我们的写入集 |
| 设备丢失 | 复制了上游逻辑 | 同源，错误码带 `FailureOrigin` | 取上游 |
| 预热 | `roomToPrime`、`METAL2VULKAN_PRIME` | `prime-fn`、`quiet_problems` | 互补，都留；上游没有内存余量保护 |
| 配置开关 | `.env` `.diag` `.clients`，随种子 | `sheng-diag` `log-gpu` `sheng-tu-debug` 和 `/private/var/ngpu/*` | 留我们的并接通上游开关（S8） |
| 显示交接 | 状态带、翻页 | 半浮点、`scanout_opaque` | **留我们的**。`scanout_opaque` 还绕过保留行，Pagani 为此打过补丁【他人记录】 |
| 扫描线程自动释放池、PF 行限频 | `NativeMetalBridgePhase.h` | 无 | 留我们的（上游未改此文件，自动保留） |
| 堆别名 | 无 | 也无（FE:1000 `isAliasable` 恒 NO） | 两边都缺。zeta/Pagani 有补丁，但 Pagani 的在加密输入里读不到【他人记录】 |

## 5. 合并后构建需要的变化

### 5.1 translator 与补丁

| 项 | 第一步（`d6ca655`） | 第二步（main） |
| --- | --- | --- |
| metal2vulkan | 不变，`720ef1c` | `624bab3918de5b978e1101517abca2b4fa426565` |
| 补丁 | 37 个 + 我们 2 个 | 24 个 + 我们 2 个 |
| 我们的 0017/0018 | `patch -F3` 能应用（已验证） | 0018 三方解开（两段常量都留），0017 的 `spares.rs` 手工移植 |
| 改号 | 排到最后 | 排到最后，例如 0025、0026 |
| `scripts/data/gpu-metal-vulkan-inputs.json` | 不变 | 改 `commit`，加 `branch`，更新 `files` 哈希，去掉 `vk12-bare.json` |
| `build-userspace-a830.sh:25` | 不变 | 改 `M2V_PIN` |

上游 24 个补丁在 `624bab39` 和 `0dfdbcd5` 上都能全部应用；在 `origin/master` 上 0012、0023 失败，在 `origin/development` 上 0010、0012、0023 失败【实测】。

补丁对照（据 `19e5f40` 说明，未逐个核实）：

- 改号保留：0001–0004、0006→0005、0015→0011。
- 被 `HostPageTexturePolicy::InPlaceWrites` 取代：0008、0016（新 0014、0015、0020）。
- 上游已自带而删除：0005、0007、0009–0014。

### 5.2 脚本

| 文件 | 变化 |
| --- | --- |
| `scripts/build-adreno-metal-bridge.py` | 更新 `PATCHES`；用 `llvm-as` 汇编 `shaders/scanout_convert.ll`，传 `NMB_SCANOUT_AIR`（两步都要，`NativeMetalObjects.rs:119` 用 `include_bytes!`）；可选 `lto = "fat"`、`codegen-units = 1` |
| `scripts/build-adreno-metal-plugin.py` | 我们不用 `ios27`、`rust-pac-rewrite.py`、`RustPacBoundary.c`、`gen-turnip-replay.py`。保留 `ABI_DIR` 和 anchor，不取上游写死 `drivers/Adreno740` 的版本 |
| `scripts/build-turnip-darwin.py` | 上游的 `-Dtu-build-id` 可取：管线缓存 UUID 不再随构建变。要配合 Mesa 选项，放第三步。保留我们的 `abi_dir` |
| `check-env-seeds-a830.py` | S12 |
| `make-shader-cache-a830.py` | 见 5.4 |
| `check-metal2vulkan-pin.py` | 我们没有。若从上游取，`624bab39` 过不了 `merge-base --is-ancestor`，要放宽 |

### 5.3 Mesa

- Mesa 提交两边相同（`85fda3b3`），分歧全在补丁。上游 11 个，我们 13 个。
- 上游有、我们没有：`turnip-render-mode-for-attachment-memory.patch`、`turnip-gmem-mutable-swap.patch`。
- 我们有、上游没有：`turnip-a8xx-backports` `turnip-a830-upstream` `turnip-a830-fixes` `turnip-a830-memory`。这四个是 A830 必需，不能丢。
- `turnip-darwin-kmd.patch`：同文件 9 个目标文件相同，但新建的 `tu_knl_darwin.cc` 上游 1609 行、我们 1242 行，分歧 872 行。上游有 size-class BO 缓存、红黑树 VA、内存压力回收、队列按需增长、memoryless 懒分配、`no CPU wait on syncs already on the ring`。我们有 `coherent` 映射选项、`tu_darwin_memory_stats`（`MemoryReport.h` 用）、`TU_DARWIN_CPU_WAITS`。
- 叠加试验（`SP/mesa-trial`，基于我们已打补丁的源码）：`render-mode` 干净应用；`gmem-mutable-swap` 只有 `tu_knl_darwin.cc` 7 块里 5 块被拒，其余都能应用【实测】。没有编译和运行。
- **硬依赖：** 桥接 0020 明确依赖 `turnip-render-mode-for-attachment-memory`（补丁注释）。没有它，写入的宿主页纹理会在 GMEM 存储时越出行尾【读码，未验证现象】。A830 的 `gmem_align` 是 16×4，与 `tile_store_fits` 假设一致（`freedreno_dev_info.py:148`）【读码】。
- 软依赖：前端对 Turnip 的所有新符号都走 `dlsym`（FE:386-461、4610-4636），缺失时相应遥测为空，不报错【读码】。`memreport` 用的 `tu_darwin_memory_stats` 上游没有，换后端时必须保留。

### 5.4 着色器缓存包

- **容器格式不变**：`SHEXP001` + `M2VCACH1`（`CacheExport.h` 只是把读取源抽象了一层）。
- **内容必须重做**：换翻译层后 `SEMANTICS_VERSION` 等全变，`M2VPPLC` 版本 1→2（帧头布局没变，名字变），`M2VVPIP` 魔数变 `\x02`。第一步翻译层不换但补丁变了，`NMB_RUNTIME_ID` 和源码摘要也变，同样要重做【读码】。

`make-shader-cache-a830.py` 的修改：

| 行 | 修改 | 哪一步 |
| --- | --- | --- |
| 112–113、124–125 | `this_build()` 在 `624bab39` 不再哈希 `M2V_RUNTIME_SOURCE_ID`（`shader_cache/mod.rs:310-319`），`require` 会失败 | 第二步 |
| 157 | `env` 加 `NMB_SCANOUT_AIR` | 两步 |
| 161 | `--offline --locked`：先更新 Cargo.lock | 第二步 |
| `JOURNALS` | 决定 `prime-fn` 是否进包 | 第二步 |

### 5.5 依赖与工具链

| 项 | 需要 | 本机 |
| --- | --- | --- |
| Rust | `cargo +1.98.0`（翻译层自己写 1.98.1，脚本显式指定 1.98.0，已通过） | 已装，含 `rust-std-aarch64-apple-ios` |
| LLVM | `llvm-as` ≥ 21 | 23.1.4 |
| Xcode SDK | iOS 17 目标 | iPhoneOS27.0 SDK，已通过 |
| Cargo 依赖 | 第二步新增 6 个包：`sha2 0.11.1` `digest 0.11.3` `block-buffer 0.12.1` `crypto-common 0.2.2` `cpufeatures 0.3.1` `hybrid-array 0.4.15` | 需联网抓一次，更新 `adreno-metal-bridge.Cargo.lock` |
| 仅 ios27 | `arm64e` 的 `build-std`、`llvm-objcopy` | 不需要 |

清单里 `sha2 = { version = "0.10", features = ["asm"] }` 第二步后对运行时无效，运行时已用 0.11 的运行时检测【读码】。可以删，也可以留着不影响。

## 6. 新用户态对内核的要求

**AdrenoMetal 本身（前端、插件、桥接）对内核没有新的强制要求。** 第 2 节的合并结果用我们现在的 Adreno830 即可运行【读码】：

- `NativeAdrenoAbiVersion` 仍是 2；Query、BoAlloc、BoMap、Submit 等线路格式未变。
- 显示交接：保留我们的 `GpuScanoutClient.h`，就没有新的 `NGP_*` 要求。若改用上游版本，则需要 `NGP_RGBA16F_*`、`NativeHalfCodes.h`，以及放弃状态带和翻页，不建议。
- `kern.bootargs` 读取失败时返回 false；`/private/var/ngpu/*` 不存在时等于未开启。
- 内核方法 `0x7101`（userdata 导入）已被上游删除，不需要。

**只有换上 Turnip 新后端才涉及内核。** 都是可选的：

| 项 | 编号 | 记录 | 缺失时 | 我们现状 |
| --- | --- | --- | --- | --- |
| 进程 GPU 优先级 | `NativeAdrenoMethodSetPriority = 17` | `NativeAdrenoAbiPriority {foreground, background}`，取值 0–5，前台 3 拒绝 | `(void)dw_call`，忽略 | 无。未知方法在我们的 `inputSize`（返回 `SIZE_MAX`）或 `dispatch`（`kIOReturnUnsupported`）被拒 |
| 进程日志 | `NativeAdrenoMethodNote = 18` | `NativeAdrenoAbiNote {kind, count, words[30]}` | 返回 false | 无 |
| Query 标志 | `NativeAdrenoParamsOwnedCoherent = 2` | 上游原样使用 | 走写合并 | 我们的 kmd 已读 2 |
| Query 标志 | `NativeAdrenoParamsMemoryPressure = 4` | BO 缓存据此回吐 | 只靠 OS 内存压力 | 无 |

要真正生效，方法 17 还要有内核侧：上游是 `setPriority` + 两环 CP 抢占（`3f4cb94`，`sheng-gpu-preempt=1`），按进程 GPU 角色选环。这是 A7xx 实现，A830 要重做，Pagani 也明确没有引入【他人记录，`STAGE6.md:1407`】。

新增方法时要改的位置：`NativeAdrenoABI.h`（方法号、结构）、`NativeAdrenoPublish.cpp:895` 的 `table[]`（加两项）、`NativeAdrenoUserClient.cpp` 的 `inputSize/outputSize`，并在 `runTimed` 前截获（上游写法见 `UniXNU-Adreno740/NativeAdrenoPublish.cpp:764-790`）。Turnip 走隧道，不需要新的沙盒选择子。

上游 Turnip 的行为假设也要注意【提交说明】：

- `caffd5d` 去掉 fence 等待上限，依赖内核 hangcheck。
- `2c40ae9` 依赖“单一有序环 + prologue 冲刷和 WFI”。
- `159eb82` memoryless 依赖稀疏 VMA。

这些要在第三步按 A830 逐项核对，Adreno740 的完整差异见同目录 `adreno740-delta.md`。

## 7. 风险清单

| 风险 | 级别 | 依据 | 对策 |
| --- | --- | --- | --- |
| App 窗口容量回退（87→54 MiB） | 高（第二步） | 2.4 实测 | 小堆不留备用块；找出约 1 s 的延迟释放；或给 `place()` 加退避重试；或改用 `pagani-gpu-va=16`（App 窗口 448 MiB） |
| GMEM 下颜色跳变 | 高（第二步） | Pagani Run 88：`sysmem,nocb` 后恢复，根因未定【他人记录】；上游 PL 317-320 注释也提到红蓝互换 | 第二步带 `TU_DEBUG=sysmem,nocb` 对照种子。我们的手机种子 `data-a830-phone-cache` 是 `TU_DEBUG=nocb`（PROGRESS:364）；`s5-final` 由后续种子叠加而来，是否沿用要再核对 |
| 持有导入吃窗口 | 中 | 导入留到表面销毁，只有内存压力才全放（FE:1065-1100）；上游没有“窗口满”这个概念【读码】 | `surfaceBuffer` 导入失败时 `trimHeldImports(true)` 后重试一次 |
| 编码线程隐藏时序问题 | 中 | 我们没测过；扫描线程也走同一条队列【读码】 | 留开关：`gEncodeThread` 是静态初始化，读不到 `.env`，只能用 `/private/var/ngpu/debug/no-encode-thread` 或改成惰性读取 |
| `waitUntilCompleted` 无上限 | 低 | 基线本来就是 `wait(_handle,0)`【读码】 | 无 |
| `624bab39` 不可达 | 中 | `git branch -a --contains` 为空，merge-base 是 `0dfdbcd5`；`development` 上有同标题不同哈希的提交【实测】；Pagani 同样发现【他人记录】 | 先做 `git bundle` 留档；备选 `0dfdbcd5`（已验证能应用和编译） |
| 上游近期未在 iOS 17 验证 | 中 | S13 编译失败；后 28 个提交多面向 27 | 第一步止于 `d6ca655`；第二步要有 iOS 17 真机回归 |
| 翻译层行为变化 | 中 | 799 个提交，含大量数学和转换改动；Pagani 上机可用但不等于我们的场景 | 第二步单独一包，便于二分 |
| 诊断代码量 | 低 | FE 净增约 3000 行，多为诊断，默认关 | 接受 |

**许可证：** AdrenoMetal 是 MIT（`LICENSE`），可移植。`bridge/patches/*` 改的是 metal2vulkan，许可证 `GPL-2.0-or-later OR LGPL-3.0-or-later`（`Cargo.toml:19`，新旧版本相同），补丁沿用其条款。这与我们现在的用法没有变化。本次没有复制 Linux、KGSL、Android 的 GPL 代码。

## 8. 推荐

**整体跟进，分三步；不逐提交挑拣，也不一步到顶。**

不挑拣的理由：`ENCODE` 改写了几乎所有编码器方法，正确性提交和它交织在一个文件里。逐个挑拣的冲突会比整体合并多，而整体合并只有 21–27 处，且都已解开过。

不一步到顶的理由：翻译层、Turnip 行为和窗口回退同时变，出问题分不清。用户当前的体验已经“基本跟原生没区别”，不能拿这个去赌。

### 第 0 步：留档（不动手机）

1. 把 `624bab39` 和 `0dfdbcd5` 做成 `git bundle`，放到不会被清空的目录。
2. 把 `SP` 里的 `resolve.py`、`resolve2.py` 和两个 `*.rebased.patch` 拷出去。**草稿目录在会话重启时会被清空。**

### 第 1 步：合到 `d6ca655`，不换翻译层（已验证可行）

内容：

- 合并 39 个提交，21 处冲突按 2.2 的同样决定解开。
- 挑入 `547312c` 的前端部分（持有导入崩溃，必须要）和 `dfe4eeb`。两者已在 `SP/repo2` 验证；`547312c` 的插件部分不要。
- 我们的 0017、0018 排到 37 个之后。
- 补上：S5、S6（若带入）、S7、S8、S9、S12、S14，以及 `surfaceBuffer` 失败重试。
- 构建：`NMB_SCANOUT_AIR`，重做 `m2vpack`。

通过门槛（全部主机）：第 2.4 节那张表，外加 `check-adreno-userspace-host.py` 和 `check-env-seeds-a830.py`。

上机看什么（按“修完再测”，并进下一个完整包，不单独出测试包）：

- 打开应用的抖动是否改善。
- 小组件、设置页图标、Core Image 相关界面。
- 留 `no-encode-thread` 对照种子。

预期：补齐一批 API 缺口；编码线程可能带来小幅帧率提升。**不承诺修好小组件。**

### 第 2 步：跟到 main + `624bab39`

前置条件：

1. 解决窗口回退，`texture_atlas_host` slot 4 恢复到不低于 62 MiB，目标接近 87。
2. Mesa 加 `turnip-render-mode-for-attachment-memory`（0020 的硬依赖），先在 hostsim 跑通。
3. 准备 `sysmem,nocb` 对照种子。
4. 修 `make-shader-cache-a830.py`，更新 Cargo.lock。

内容：剩余 28 个提交里，取 0023 QoS、0021（改后）、预热续作、诊断。不启用 `__arm64e__` 路径、半浮点转换。

这一步单独一包。颜色跳变一旦出现，先用 `sysmem` 对照确认。

### 第 3 步：Turnip 后端与内核（按需）

只有前两步量出 Turnip 或内核是瓶颈才做。候选按价值排序【推测】：

1. size-class BO 缓存和红黑树 VA。
2. `2c40ae9` 的等待优化（对照我们现有的 `TU_DARWIN_CPU_WAITS`）。
3. memoryless 懒分配。
4. 方法 17/18。

都要逐项核对 A830 路径，并保留 `tu_darwin_memory_stats` 和 `coherent` 选项。这部分与 `adreno740-delta.md` 一起规划。

### 不做的事

- 不采用“每个进程都给设备”，保留 `ClientRule.h`。
- 不采用上游的 `GpuScanoutPresenter.h` 和 `GpuScanoutClient.h`。
- 不采用 `kSurfaceFlush`。
- 不引入 mimalloc。
- 不移植 8-bit 帧缓冲和 EDR 相关的地址修补。

## 附：草稿目录内容

都在 `SP` 下，**会话重启会丢**。

| 路径 | 内容 |
| --- | --- |
| `repo` | 到 main 的合并，保留冲突状态 |
| `repo2` | 到 `d6ca655` 的合并，已解开并挑拣（`ca53daa`） |
| `resolved`、`resolved2` | 两条路径解开后的源码 |
| `resolve.py`、`resolve2.py` | 冲突解法脚本 |
| `m2v` | `624bab39` + 24 个补丁 + 我们 2 个 + 备用块试验（`997d51d1`） |
| `m2v-old` | `720ef1c` + 37 个补丁 + 我们 2 个 |
| `m2v-0df` | `0dfdbcd5` + 24 个补丁 |
| `0025-*.rebased.patch`、`0026-*.rebased.patch` | 我们两个补丁在 main 路径上的移植结果 |
| `atlas`、`atlas2`、`ht0`、`ht1`、`ht2` | 主机测试结果 |
| `mesa-trial` | Mesa 补丁叠加试验 |
