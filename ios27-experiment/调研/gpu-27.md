# 13T 的 iPadOS 27 GPU 路线调研（A830，24A446）

时间：2026-11-01。全程没有碰手机，没有运行 adb、fastboot，没有下载文件，没有提交。除本报告和临时目录外，最终没有留下任何改动（过程中有两处差错，已处理，见附录 B）。

证据等级：

- **【实测】** 本次在这台 Mac 上跑命令得到。
- **【读码】** 读了文件或提交说明，给出位置。上游和第三方的提交说明是“他们自己这么写”，我没有复现，文中写“上游自述”。
- **【推断】** 由前两者推出，没有直接验证。工作量全部是推断。

简写：

| 简写 | 路径 |
| --- | --- |
| `U/` | `参考资料/上游-20261031/UniXNU-UniXNU/` |
| `SH/` | `参考资料/上游-20261031/UniXNU-UniXNU-sheng/` |
| `AM/` | `参考资料/上游-20261031/UniXNU-AdrenoMetal/`（行号指快照 main `831a9dc`） |
| `A7/` | `参考资料/上游-20261031/UniXNU-Adreno740/` |
| `GF/` | `参考资料/上游-20261031/UniXNU-GopFramebuffer/` |
| `PG/` | `参考资料/上游-20261031/IssacL04-UniXNU-Pagani_SM8750/` |
| `A8/` | `Adreno830/` |
| `OURS-AM/` | `UniXNU/drivers/AdrenoMetal/`（基线 `ce5e19e` 加未提交改动） |
| `BU/` | `build-userspace/` |
| `BRINGUP` | `SH/patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch` |
| `DV` | GitHub `Droid-VM/UniXNU-droidvm`，用 `gh api` 读提交说明，本地没有快照 |

**关于 27 的二进制证据：** 本机没有 24A446。能直接看的是 Xcode 设备支持目录 `~/Library/Developer/Xcode/iOS DeviceSupport/iPhone19,4 27.1 (24A94425)/arm64e/Symbols`，这是 **iOS 27.1、iPhone、`arm64e.x1` 切片**，带符号。它和 24A446 同一大版本，不是同一构建。文中“27.1 反编译”都指它，21H461 对照用 `BU/a830-inputs/widgets/ida/QuartzCore.asm`。

---

## 0. 结论

1. **软件显示路径：代码还在，但没有人在 27 上见过它出桌面。** 27.1 的 QuartzCore 没有 Metal 时回退 `CA::OGL::SWContext`，逻辑和 21H461 一样，帧缓冲强制 8 位 BGRA【实测】。但两次 24A446 无 GPU 启动都没有桌面：sheng 是“三次交换，屏幕停在苹果标志”，DroidVM 是“一帧黑屏，之后没有帧”，加上 Metal 后 2.4 小时就画出锁屏【读码，上游和第三方自述】。根因没人查过。判断：**可能可行，未证实，不能当确定的里程碑**。设备上也没有软件 Metal。
2. **建议把 S1 改成限时探针，不要当 S2 的前提。** 显示后端（M）两边都要，所以先做。探针最多两轮，不出桌面就直接进 S2。
3. **内核 GPU 驱动几乎不用改。** `Adreno830` 全部 41 个源文件，加 `PaganiGpuBoard.cpp`、`ArmSmmu4K`、`AdrenoPM4`，用 xnu-12377 头文件编成 arm64e，`-Wall -Wextra -Werror` 零错误，**一行源码没动**【实测】。要改的只有 `build-adreno830.py` 和构建胶水。
4. **Turnip 不用改。** 我们带 `turnip-a830-*` 的 Mesa 编成 `arm64e-apple-ios27.0`，700 步通过，产物导出正确【实测】。`dsb sy` 和 Busy 处理都与 OS 无关，27 上照留。
5. **翻译器不必升到 624bab39。** 上游 10-28 在 27 上渲染时还是 `720ef1c`，10-29 才换【读码】。我们现在的 `720ef1c` 加 18 个补丁，能把 27.1 模拟器的 QuartzCore 库翻译并经 A830 Turnip 编出 50 条管线【实测】。
6. **S2 的最大工作项是 AdrenoMetal 前端。** 我们 2193 行，上游 4719 行，27 必需的内容大多已在 `d6ca655`，之后还差约 7 项。这块和 17 路线的阶段 2 是同一件事，先在 17 上做完，27 这边能省一大半。
7. **最大风险是内存和排队，不是移植。** sheng 的 1536 MiB 池在 27 上填满过；我们 17.7.11 上 1024 MiB 池已用到 81–92%。27 上一个海报扩展的任务约 77 ms，环上没有优先级。两条都不能靠搬代码解决。
8. **题目里的前提要更新：** 默认 GPU 地址窗口已是 16 GiB、32 个连接窗口（10-31 起），4 GiB、12 窗口只是回退表（`A8/Adreno830Board.h:158-169`、`A8/NativeAdrenoPublish.h:30-36`）【读码】。
9. **附带发现：** `sys_dcache_flush` 只是 `dsb ish; ret`，这不是 27 的变化，21H461 也一样【实测】。上游注释和 `ipados27-parity.md:70` 都写错了。这意味着我们 17 上 `SurfaceMaintenance.h` 的完成时失效一直是空操作，见 2.3 节。

---

## 1. 内核侧

### 1.1 绑定对比

两份 ABI 数据：`UniXNU/scripts/data/ioaccelerator-base-abi.json`（21H461）、`U/scripts/data/ioaccelerator-base-abi-24A446.json`（24A446）【读码】。

| 项 | 21H461（我们） | 24A446（上游） |
| --- | --- | --- |
| `IOAcceleratorES` 大小 | 136 字节 | 136 字节 |
| 虚表项数 | 168 | 168 |
| `operational_overrides` | 空 | 空 |
| `metadata_overrides` | 0、1、7 | 0、1、7 |
| metaclass | `0xfffffff006aeb688` | `0xfffffe000bb3f290` |
| 构造函数别名 | `0xfffffff004bd3b20`、`…b40` | `0xfffffe000a14d990`、`…9c8` |
| 非 deleting 析构 | `0xfffffff004bd3b64` | `0xfffffe000a14da00` |
| `getMetaClass` | `0xfffffff004bd3bac` | `0xfffffe000a14da70` |
| 地址基准 | 绝对地址 | 未滑动地址加 `collection_slide` `0x1f000000000` |
| 符号绑定 | `.set` 绝对符号（`A8/scripts/build-adreno830.py:231`） | 函数走 `bti c; b <名>.original` 跳板，metaclass 走 GOT（`U/scripts/build-adreno740-accelerator.py:93-112`） |
| 目标 | `arm64-apple-ios17.0`（`build-adreno830.py:235`） | `kernel_target.TRIPLE`，即 `arm64e-apple-ios27.0` |
| 内核形态 | `MH_EXECUTE` 预链接 | `MH_FILESET`，XnuPrelink 放置 |

**类的形状完全没变。** 上游的审计说明写明两份构造函数都调 `IOService::IOService(const OSMetaClass *)`，只存签名虚表（DA 键，`0xcda1`）。所以 `AGXAccelerator : IOAcceleratorES` 这个写法照用【读码】。

`AGXAccelerator` 这个类名和 `AGXDeviceUserClient` 这个连接类名都要保留：

- 27 的内核 kobject 放行直接比较类名 `"AGXDeviceUserClient"`（`BRINGUP:652`）。
- 我们和上游都靠这个名字过原版沙盒（`A8/NativeAdrenoPublish.cpp:13` 附近）。

`11-01` 的上游提交改了一处：跳板不再减 `native-slide`，由 XnuPrelink 放置后重算分支（`U` `474cfee` 的 `build-adreno740-accelerator.py`，`SH` `6dafef9`）。上游自述 27 镜像里有 266 条固定分支，其中 GPU 两条【读码】。**抄的时候要抄 `474cfee` 之后的版本**，本地快照里还是旧写法。

### 1.2 实测：Adreno830 直接过编译

做法：从 `ios27-experiment/inputs/xnu-12377.git` 的 HEAD 展开头文件，用 21H461 内核的 C++ 编译记录，把目标换成 arm64e 和 iOS 27.0，包含路径把 12377 放在前面。MIG 生成头等 12377 没有的文件，回退到 10063 的 `EXPORT_HDRS`。一个样本文件统计：12377 的头 467 次，10063 回退 56 次，后者几乎全是生成头和 `string.h`【实测】。

| 对象 | 结果 |
| --- | --- |
| `Adreno830` 全部 41 个源文件（18 继承 + 15 Gen8 + 7 平台 + 加速器），真实 `-c` | **41/41 通过**，`-Wall -Wextra -Werror` 用项目策略 |
| 目标文件 | `MH_MAGIC_64 ARM64 E KER00`，`LC_BUILD_VERSION` minos 27.0 |
| `PaganiGpuBoard.cpp` | 语法检查通过 |
| `ArmSmmu4K*.c` 8 个、`AdrenoPM4.c` | 语法检查通过 |
| 外部 C 符号（84 个非 C++ 名） | 抽查 21 个内核符号，在 12377 源码里都搜得到 |

**限制：** 这是编译，不是链接，更不是运行。配置宏沿用 10063 的记录。结论只能说“源码级 KPI 没有断”，这和 Adreno740 全文没有一处 `__arm64e__`、`ptrauth`、`IOUserClient2022` 一致（`rg` 零命中）【实测】。

`A8` 里没有函数指针强转、没有手写 `blr`，内联汇编只有 `dsb`、`dmb`、`dc`、`smc`、MMIO 读写【实测，`rg`】，所以 PAC 没有明显的下手点。

### 1.3 Adreno830 要改什么

| # | 位置 | 改法 | 来源 | 量 |
| --- | --- | --- | --- | --- |
| 1 | `A8/scripts/build-adreno830.py:159-172` `audit()` | 接受 arm64e 子类型和 27.0，用 `kernel_target.check_object` | `U/scripts/kernel_target.py` | S |
| 2 | `build-adreno830.py:213-243` `stage_accelerator()` | 选 24A446 计划，加 FILESET 分支；arm64e 对象解析要清子类型（`build-adreno740-accelerator.py:20-24`） | `build-adreno740-accelerator.py`，取 `474cfee` 版 | S |
| 3 | 数据文件 | 拷 `ioaccelerator-base-abi-24A446.json`，kernelcache 必须是同一份（sha256 `38e6d9fa…`） | 上游 | S |
| 4 | `UniXNU-Pagani_SM8750-gpu/scripts/build-pagani-gpu.py` | 编译记录和固件 `.incbin` 汇编换目标 | 自写 | S |
| 5 | `A8/NativeAdrenoPublish.h:156` 授权名单 | 加 `SBRendererService`（DV `8277f558`：无 Metal 设备时 `NUDevice_iOS` 断言）。或走 `NativeSandboxGrantAll`，上游 27 的写法是 `"*"`（`SH/package/GpuPowerProbe.cpp:1359`） | 上游 | S |
| 6 | 导入内存维护 | 所有者已映射为非缓存的导入跳过维护（`A7` `2c3df1e`，`A7/NativeAdrenoMemory.h:49`），27 的显示 surface 是写合并。这是省 CPU，不是必需 | A7 思路 | S |

**不用改的：** `NativeAdrenoAccelerator.cpp`、`NativeAdrenoPublish.cpp` 的 `externalMethod` 和隧道、`Adreno830Start.cpp` 的 `kMetalPrepare`。`A8/Adreno830Start.cpp:254-296` 与 `SH/package/GpuPowerProbe.cpp:1331-1359` 是同一流程。

**XNU 侧，已在 `xnu-12377.git` 基线里（BRINGUP）的：**

| 项 | 位置 | 对我们 |
| --- | --- | --- |
| kobject 放行 `io_connect_map_memory_into_task`（2863、2864） | `BRINGUP:646-653,1313-1330,1495-1501` | 必需。沙盒客户端要映射 BO |
| `NativeSandboxGrantProcess` 支持 `"*"` | `BRINGUP:635-638` | 我们的 `zzwc` 是另一种写法，二选一 |
| `hw.pagesize` 绕过沙盒 | `BRINGUP:371-377` | WebKit GPU 进程要 |
| 叠加挂载的存在性探针改 `AGXMetalG17G.bundle` | `BRINGUP:245-246` | 根是 J820 就照用 |
| `pmap_batch_set_cache_attributes` 清行 | `BRINGUP:956-962` | 我们的池不走这里，留着无害 |
| `CACHE_ATTRINDX_RT` 改写合并、47 位用户地址 | `BRINGUP:1239-1247,1203-1212` | 对 GPU 窗口无影响，GPU VA 是独立的 |
| `native_pmap_user_mappings` | `BRINGUP:988-1052` | 只有系统内存 BO 才用，我们现在不需要 |

### 1.4 可搬与 A830 专有

**可以直接搬：** 上面的数据文件和构建分支。

**不搬：** `A7` 的系统内存 BO、每进程页表、CP 抢占。27 不要求它们：上游 10-28 第一次在 27 上跑时，BO 还是固定池，系统内存 BO 是 10-29 的 `c8806db`【读码】。

**A830 专有部分与 OS 无关【实测 + 读码】：** `A830Regs.h`、`A830Gmu.cpp`、`A830Hfi.cpp`、`A830Cp.cpp`、`A830Zap.cpp`、`platform/sm8750/*`、固件全部过了编译，只经 `Adreno830Os` 和 `Adreno830Board` 两张表碰内核。固件是 `gen80000` 四个镜像，按 SHA-256 钉死，走 `__DATA_CONST,__const`（`build-pagani-gpu.py:128`），和 OS 无关。

运行时仍依赖的 OS 相关件在板级，不在 `A8`：`armcpu_spi_*`（`PaganiArmCpu`）、`Stage7Probe`、`native_gop_handoff_*`（`NativeMobileDisplay`）。这些归 `drivers-27.md`。

### 1.5 fileset 的体积约束

这是 17 上没有的新限制。XnuPrelink 把原生内核的段放进原 kernelcache 的范围，`__DATA` 放在根 `__DATA` 起点，放不下就报错（`XnuPrelink/fileset_layout.py:88-107,154-165`）【读码】。上游踩过两次：

- sheng：0.9 MiB 的剖析环放不下（`SH` `f75689c`）。
- DroidVM：9 MiB 的 virtio GPU 队列改成运行时分配（DV `23af1b22`）。

`A8` 的 `__DATA` 约 370 KiB，其中 `__bss` 346 KiB【实测，arm64e 目标文件】。我们 17 的整核 `__DATA` 是 1.85 MiB【实测，`size -m`】。到底还剩多少，要有 24A446 才能算。放不下就把大静态数组改成运行时分配，这是 S 级改动，但整包里驱动多，大概率要做一轮【推断】。

---

## 2. 插件和前端

### 2.1 27 专有项逐项

“在 `d6ca655`”表示合到 `merge-plan.md` 阶段 2 的落点就有。代际列都是“无关”，除非注明。

| 项 | 上游做法 | 位置 | 我们 | 在 `d6ca655` | 代际 |
| --- | --- | --- | --- | --- | --- |
| `framebuffer_pixel_format` 修补 | `sheng-fb-bgra=1` 时把 `0x184461928` 的 `tbnz w8,#0`（原字 `0x37000608`）改 NOP；函数 `0x184461838`，判虚拟化字节 `0x1e701bdd9`，slide 由 `CARenderServerStart` `0x1846dce88` 算 | `AM/NativeAdrenoMetalPlugin.mm:86-110,394` | 无 | 否 | 无关，但地址只对 24A446 |
| 选项写入 | 环境 `CA_DISPLAY_EDR_HEADROOM=1`、`CA_FP16_MIN_HEADROOM=1e30`、`CA_ALLOW_EDR_MIRRORING=0`，再写存储值 `0x1e7021014`、`0x1e7021104`、`0x1e702116c+156` | `AM/NativeAdrenoMetalPlugin.mm:370-391` | 无 | 只有第一项 | 同上 |
| 半浮点帧 | 显示驱动 CPU 转换 `convertHalfRows`；GPU 客户端 `scanout_convert.ll` | `GF/NativeLegacyDisplay.cpp:110-133`、`AM/GpuScanoutPresenter.h:4-9` | 无 | 部分 | 无关 |
| BGRA 模式 | `sheng-fb-bgra=1` 时 `TU_DEBUG=sysmem,nocb`；6dafef9 起是 27 默认参数 | `AM/NativeAdrenoMetalPlugin.mm:317-321`、`SH` `6dafef9` 的 `board.toml` | 无 | 否 | **A830 要单测**，见第 3 节 |
| MTLCompiler 版本目录 | 先读 `Metal` 自带字符串里的路径，再回退最高版本 | `AM/NativeBridgeMetalFrontend.mm:813-850` | 无（`rg MTLCompiler` 零命中） | 是 | 无关 |
| QuartzCore 新选择子 | `setResponsibleTaskIDs:count:`、`setResponsibleProcess:`；未知选择子走 `missingSignature` 答 nil | `AM/NativeBridgeMetalFrontend.mm:955-975,986-987,2783-2784` | 无 | 是 | 无关 |
| 液态玻璃短形式 | `newComputePipelineStateWithDescriptor:error:`，缺它 render server 中止 | `AM/NativeBridgeMetalFrontend.mm:4185-4188` | 只有带 options 的长形式（`OURS-AM/NativeBridgeMetalFrontend.mm:1809`） | 是 | 无关 |
| `_MTLDevice` 大小检查 | arm64e 下放宽 | `AM/NativeBridgeMetalFrontend.mm:4381-4386` | `OURS-AM/NativeBridgeMetalFrontend.mm:1968` 仍是 `!=536`，27 上直接不给设备 | 是 | 无关 |
| Core Image 库 | `file_type 1` 库按名给函数；`externFunctionNames` | 翻译器补丁 `core-image-kernel-libraries`；`AM` `a11aeb9`、`ec2e1d1` | 无 | 前者是，后者否 | 无关 |
| RenderBox | 对 RenderBox 不报 Apple3 以上；链接函数；拒绝编码器 | `AM/NativeBridgeMetalFrontend.mm:4330-4333` | `OURS-AM/FamilyAnswer.h`，判定更严 | — | 留我们的 |
| IOSurface ABI | 内核：6 个 getter 加 `create`、`allocate`，无 allocation getter；用户态无变化 | `SH/scripts/legacy-display-24A446.json`、`build-gop-legacy-display-27.py:316-358` | 显示驱动要接 | 不涉及 | 无关 |
| Rust PAC 边界 | 见第 4 节 | `AM/bridge/RustPacBoundary.c` | 无 | 是 | 无关 |
| 全局分配器 | mimalloc 在 27 上让 render server 退出，撤掉 | `AM` `deab8bd` | 没引入 | 是 | 无关 |
| `gpuCoreCount` | 写死 8 | `AM/NativeBridgeMetalFrontend.mm:3853` | 要改 6（`PG/STAGE6.md:1353` 所在段） | — | **A830 专有** |
| 帧缓冲 alpha | BGRA 帧走 `scanout_opaque` 强制不透明，否则液态玻璃周围有半透明色块 | `AM/GpuScanoutPresenter.h:6-8,66` | 我们走 blit 加状态带 | 否 | **13T 要测** |

`d6ca655` 之后还要单摘的约 7 项：`dfe4eeb`（`externFunctionNames`）、`547312c`（导入表）、`11420f4`（FP16 阈值）、`d1b8b33`（阈值选项）、`805659b`（镜像开关）、`278333f`（BGRA、`scanout_opaque`）、`7183e58`（priming）。`merge-plan.md` 阶段 2 已列出前两项。这些是 `feat/ipados27-gpu-parity` 上的哈希，main 上对应 `ec2e1d1`、`add14db`、`d2aa73f`、`0055c37`、`9bad6f5`、`f72c5d6`、`1d19b75`。

**语法试编【实测】：** 我们的和上游的 `NativeAdrenoMetalPlugin.mm`、`NativeBridgeMetalFrontend.mm`，用 iPhoneOS27.0 SDK、`arm64e-apple-ios27.0`、`-Wall -Wextra -Werror` 都过。这只说明 API 没断，不说明能用，比如第 1968 行的大小检查编译不会报。

### 2.2 与代际无关、可以直接用的

上表除标 A830 的行，全部是 Metal 和 CoreAnimation 层的事，与 A740 无关【读码】。`framebuffer_pixel_format` 这组地址绑定 24A446 的共享缓存，所以 **根必须和 sheng 用同一个 IPSW**（`iPad17,1…4_27.0.1_24A446`），换构建要重找。

`27.1` 反编译确认了这个函数的逻辑：`CADeviceIsVirtualized` 为真时直接落到 `RGhA`（`0x1843c293c`）【实测】。这解释了为什么 `vmm-present=1` 必须和这个修补配套。上游试过把 `vmm-present` 对用户态改成 0，backboardd 启动就连崩三次（`SH` `cc62bf4`）【读码】。

### 2.3 更正：`sys_dcache_flush` 不是 27 的变化

上游注释写“iPadOS 27's sys_dcache_flush is a barrier alone”（`AM/NativeBridgeMetalFrontend.mm:1319`），`ipados27-parity.md:70` 照抄成“27 的行为”。实测两边一样【实测】：

| 系统 | 地址 | 指令 |
| --- | --- | --- |
| 21H461（`systemos-21H461` 共享缓存） | `0x1dd56c888` | `dsb ish; ret` |
| 27.1（`libsystem_platform.dylib`） | `0x18c1ed7bc` | `dsb ish; ret` |

所以：

- **27 这边不用为此改任何东西。** 我们 `OURS-AM/NativeBridgeMetalFrontend.mm:513-520` 的清洗是自己写的 `dc cvac`，27 上照常工作，XNU 开着 `SCTLR_UCI_ENABLED`（`osfmk/arm64/proc_reg.h:763`）。
- **17 这边有个现存问题：** 完成时的失效走 `sys_dcache_flush`（同文件 `:531`），它什么也没做。`SurfaceMaintenance.h` 的注释说这一步“clean and invalidate”，实际只有一条屏障。CPU 若在 GPU 写的时候预取了旧行，读回会是旧数据。修法是改成自己写的 `dc civac`，或者确认没人读后整段删掉省 CPU。`bridge/NativeMetalBridge.rs:213,216` 同样。这不属于 27，请主线判断。

---

## 3. Turnip

**`--target ios27` 就是换三元组和输出目录**（`U/scripts/build-turnip-darwin.py:19,48-52`）。Mesa 固定版本两边相同，都是 `85fda3b`，`patches/mesa` 里两边都没有 `ptrauth` 或 `arm64e`【实测】。

**试编【实测】：** 把 `BU/tree/.cache/mesa-work`（已打全部补丁，含 `turnip-a830-*`）拷到临时目录，用上游的 cross 配置和选项，ABI 目录指 `Adreno830`：

| 项 | 结果 |
| --- | --- |
| ninja | 700 步，退出 0 |
| 产物 | `libvulkan_freedreno.dylib` 18,071,528 字节（arm64 版 17,879,232） |
| 头 | `ARM64 E USR00`，minos 27.0，sdk 27.0 |
| 导出 | `vk_icd*` 3 个 |
| 内容 | 含 `Adreno (TM) 830`，chip id `0x44050001` 命中 1 次 |

只编译没运行。我们的 `build-turnip-darwin.py` 没有 `ios27`，要加上。`tu-build-id` 这个 Mesa 选项我们已有（`meson.options:777`，试编时的副本），只是脚本没传；上游脚本用源码哈希固定它（`U/scripts/build-turnip-darwin.py:99-104`）。

**补丁分叉的处理：**

| 补丁 | 27 上怎么办 | 理由 |
| --- | --- | --- |
| `turnip-a8xx-backports`、`turnip-a830-upstream`、`turnip-a830-fixes`、`turnip-a830-memory` | 原样保留 | 都是 gen8 和 ir3 修复，与 OS 无关 |
| 我们的 `turnip-darwin-kmd.patch` | 原样保留，不换成上游版 | `merge-plan.md` a.2 的结论对 27 同样成立 |
| 上游 `turnip-gmem-mutable-swap.patch` | 先不带 | 见下 |
| 上游 `turnip-render-mode-for-attachment-memory.patch` | 只有换 `624bab39` 才需要 | 0020 的依赖 |
| 脚本传 `-Dtu-build-id` | 建议摘 | 让 `pipelineCacheUUID` 不随 `LC_UUID` 变，着色器包少失效 |

**语义差异的取舍：**

- **`dsb sy`：留。** 我们有两处：`turnip-a830-fixes.patch:64-67`（`tu_EndCommandBuffer`）和 `turnip-darwin-kmd.patch:1229-1230`（提交线程）。原因是写合并命令流跨线程提交时 CP 读到半截，这是 SoC 和互连的行为，换 OS 不变【读码 + 推断】。上游全文没有 `dsb`，只说明 A740 上没撞到。
- **Busy：留。** 我们的 `darwin_wait_for_room`（`turnip-darwin-kmd.patch:1194-1211`）配合内核准入（`A8/NativeAdrenoSubmit.cpp:41-47`：非渲染客户端只有自己那个保留作业）。上游（`U/patches/mesa/turnip-darwin-kmd.patch:1586-1592`）等最老一个，等到已完成的 fence 会立刻返回。27 上客户端更多，Busy 只会更常见【推断】。
- **BO 缓存假设系统内存，不搬。** 我们是固定池。

**GMEM 要单独验。** 上游 27 默认 `sysmem,nocb`，因为 BGRA 帧缓冲下 GMEM 会红蓝互换，打补丁后也不比系统内存快（`U/docs/zh/porting/gpu.md:90-93`）。Pagani 在 **17 + `624bab39`** 上也遇到 GMEM 颜色跳变（`PG/STAGE6.md:1384,1390`）。我们 **17 + `720ef1c`** 上 GMEM 正常。所以这个颜色问题更可能跟翻译器有关，不是跟 27 有关【推断】。这也是留在 `720ef1c` 的另一条理由。第一轮用默认的 GMEM 种子，出色块就退 `sysmem,nocb`。

---

## 4. 翻译器

### 4.1 版本

**27 不要求 `624bab39`。** 时间线【读码】：

| 时间（+0800） | 事件 |
| --- | --- |
| 10-28 | `SH` `e9865b0`：sheng 起 24A446 |
| 10-28 22:49 | `AM` `34b5920`：修 27 的 MTLCompiler 目录 |
| 10-28 | `AM` `34abea7`：提到 iconservicesagent 崩在 27 的 Core Image |
| 10-29 11:44 | `U` `c93e965`、`AM` `19e5f40`：才离开 `720ef1c` |
| 10-29 14:30 | `U` `16d7ff9`：到 `624bab39` |

也就是说 10-28 那批 27 上的渲染修复，都是在 `720ef1c` 上做的。

**实测：** 用现成的 `BU/shader-cache/gen/shader_cache_gen`（`720ef1c` 加我们 18 个补丁的主机运行时，A830 身份的主机 Turnip），喂 27.1 模拟器的 QuartzCore `default.metallib`（2.7 MB，`Apple metal version 32023.921`，含 `GlassBackgroundUniforms` 等）：

| 库 | 计算函数 | 渲染函数 | 成管线 | 拒绝 | 耗时 |
| --- | --- | --- | --- | --- | --- |
| 21H461 QuartzCore | 15 | 41 | 27 | 29 | 约 1.6 s |
| 27.1 模拟器 QuartzCore | 54 | 29 | 50（另 2 个 zeroed） | 33 | 2.5 s |

结论：`720ef1c` 能读 27 的 AIR，A830 编译器也吃得下。**只是下限：** 这是模拟器库、priming 的通用描述符（第一条拒绝是 `std_vert0_lpf` 缺顶点属性，不是翻译失败），没有覆盖函数常量变体。计算函数从 15 涨到 54，说明 27 把一批工作挪到了 compute（背景捕获、亮度），这对上面的性能有影响。

建议：**S2 第一轮留在 `720ef1c`，补丁用 `d6ca655` 的 37 个加我们的 0017、0018、QoS。** 到 `624bab39` 有实测回退（App 窗口纹理容量 87→54 MiB，`adrenometal-delta.md` 第 0 节第 4 条）和 GMEM 颜色问题，放到 S2 之后。

### 4.2 27 需要的补丁

在 `d6ca655` 的编号：

| 补丁 | 27 的相关性 |
| --- | --- |
| `0024-core-image-kernel-libraries` | **必需。** `ci_stdlib`、`ci_filters` 的 `file_type 1`，否则 iconservicesagent 崩 |
| `0017-optional-shader-cache-directory` | 沙盒进程（WebKit GPU 进程）要 |
| `0018-srgb-extended-range-blend-rounded-model` | XR 混合，星空壁纸等 |
| `0019-llvm-bitreverse` | `reverse_bits` |
| `0020-growing-memory-blocks` | 省内存，27 更需要 |
| `0023-heap-texture-size-…` | 上游写明是 27 的 iconservicesagent 触发 |
| 我们的 `0017`、`0018`（限保留命令缓冲、块不超过堆的 1/16） | 窗口表下必需，排到最后 |
| 名为 `quality-of-service-on-ios` 的 QoS 补丁 | 建议带，`gpu.md:112` 列为慢帧原因 |

### 4.3 Rust 的 PAC 边界

rustc 的 arm64e 代码不带指针认证，所以要两头补（`AM/bridge/RustPacBoundary.c:1-11`）【读码】：

- **Rust 调 C：** `U/scripts/rust-pac-rewrite.py`（76 行）把链接后属于 Rust 对象的 `blr/br Xn` 改成 `bl/b nmb_pac_call_xN`，后者 `xpaci` 再跳。
- **C 调 Rust：** 用 `llvm-objcopy --redefine-sym` 把 6 个带回调的导入改到签名包装（`U/scripts/build-adreno-metal-plugin.py:39-46`）：`pthread_create`、`_tlv_atexit`、`atexit`、`_dyld_register_func_for_add_image`、`_Unwind_Backtrace`、`dlsym`。Vulkan 调试回调走包装的 `vkGetInstanceProcAddr`。

这两个文件与 GPU 代际无关，可以原样拿。要注意：

- `arm64e-apple-ios` 在 `rustc 1.98.0` 的目标列表里，但没有预编译 std，要 `-Z build-std`，而本机 1.98.0 **没装 `rust-src`**【实测】。这步要下载，本次没做，所以 Rust 桥的 arm64e 构建没有实测。
- 我们的 0017、0018 只改内存和命令缓冲逻辑，没有新回调【推断】。以后加回调要记得加进重命名表。
- 插件是单切片 arm64e。系统进程没问题，但 **第三方 App 是 arm64，大概率载不了这个 bundle**，就拿不到 Metal 设备【推断】。上游没处理。要支持就得出 fat 包：插件、桥、Turnip 各编两份，S 到 M。

### 4.4 着色器包

现状：`BU/make-shader-cache-a830.py` 在 Mac 上用主机 Turnip 生成包，`BU/shader-cache-tools/macho_uuid.py` 让主机 Turnip 带设备 Turnip 的 `LC_UUID`。上游 `0055c37` 放弃了主机生成，只在设备上 priming，再用 `make-m2vpack.py` 合并导出【读码】。我们的办法在 27 上仍然可用，要重做：

| 项 | 要做的 |
| --- | --- |
| 输入 | `make-shader-cache-a830.py:56-58` 的 `ROOT_IMAGE`、`SYSTEMOS_DMG`、`OS_DMG` 换 24A446 |
| 预热列表 | 重新生成。21H461 是 161 行（`A8/docs/inputs/userspace/prime-list-21H461.txt`），sheng 的 27 是 162 个库（`SH/porting.toml:169`），路径都要从 27 的 OS 和 SystemOS 取 |
| cryptex | render server 的沙盒读不到 cryptex 路径，要在 payload 里带一份 QuartzCore 库（`U/docs/zh/drivers/adreno-metal.md:18-20`） |
| `LC_UUID` | arm64e Turnip 的 `LC_UUID` 新，全部缓存重算。摘 `tu-build-id` 后主机和设备同一身份更简单 |
| 变体 | BGRA 模式不需要 `PrimeShaders.h:273` 的 RGBA16Float 变体；走半浮点才要 |
| 交付 | 我们走 Data 种子，上游走 `make-gpu-addon.py` 的 addon。`PaganiDarwinBoot` 不收 addon 旁路文件（`BU/build-userspace-a830.sh` 第 7 步注释），27 用哪条要跟 `storage-27.md` 一起定 |
| 校验 | `check-env-seeds-a830.py`、严格消费者检查都要跑 |

**不要把包当启动前提。** 上游 27 的 `pack27-gpu.sh` 不带包也能起，只是首次慢（`U/docs/zh/drivers/adreno740.md:91` 记录 17 上冷启动 43 s 左右编 77 条）。

---

## 5. 软件显示路径（里程碑顺序的关键）

### 5.1 结论

| 问题 | 回答 | 证据 |
| --- | --- | --- |
| 27 的 QuartzCore 还有软件渲染吗 | **有。** 和 21H461 同构 | 【实测】 |
| 没有 Metal 时 backboardd 会崩吗 | 代码路径上不会，但会有 30 s 等待 | 【实测】 |
| 有人在 24A446 上不带 GPU 见过桌面吗 | **没有。** 两次都没有 | 【读码】 |
| 设备上有软件 Metal 吗 | **没有** | 【实测】 |
| S1 可行吗 | **未证实。** 我给一半把握 | 【推断】 |

### 5.2 没有 Metal 时 CoreAnimation 的行为

以下是 27.1 反编译，21H461 对照一致【实测】：

1. `CA::WindowServer::AccelServer::AccelServer` 构造时调 `renderer()`（`0x18458d230`）。
2. `AccelServer::renderer()`（`0x1843781c8`）调 `CAMetalContextCreate()`，返回空就返回空。
3. `CAMetalContextCreate` 的 once 块（`0x18471e62c`）先等 `IOAcceleratorES` 服务出现，**最多循环 300 次，每次调用一个参数为 100000 的等待函数**，按 `usleep` 理解约 30 s（调用目标在缓存外，没解析）。每个进程第一次调用都会等。21H461 同样有这段，上限同为 `0x12B`（`QuartzCore.asm:513949` 起）。
4. `AccelServer::render_update()`（`0x1843b9d0c`）拿不到渲染器就调 `Server::render_update()`。21H461 相同（`QuartzCore.asm:219998` 起）。
5. `Server::render_update()`（`0x1846d8058` 所在函数）取 `Server::sw_renderer()`，设一个标志，再调 `Display::render_display()`。21H461 的这个函数（`QuartzCore.asm:912764` 起）结构一致，只是偏移不同。
6. `sw_renderer()`（`0x1846d7dbc`）构造 `CA::OGL::SWContext`，打一行日志 “Failed to initialize Metal or OpenGLES contexts!”。这是日志，不是中止。
7. `render_display()` 调 `update_surface(1, sw_renderer_p(), …)`（虚函数偏移 1248，`SWContext` 返回 1）。`current_page_surface()` 里这个参数为真时格式强制 `'BGRA'`（`0x1843c2480`）。21H461 的 `current_page_surface` 在同一位置有 `#0x42475241` 的条件选择（`QuartzCore.asm:366747` 起第 163–164 行）。

第 7 步说明软件路径 **绕开了 RGhA**，不需要 `framebuffer_pixel_format` 修补。这和 `GF/NativeLegacyDisplay.cpp:110-111` 的注释吻合：“picks it … once a Metal device renders”。

`SWContext::make_buffer_current`（`0x18462671c`）接受 `BGRA`、`L008` 等格式。软件格式表里有 `RGBAh`，符号里有 `blur_surface`、`rect_sdf_sampler`、`supercircle_sdf_sampler`。其他调用 `CAMetalContextCreate` 的地方（`CASDFGenerator init`、`allocate_iosurface`、`get_ctx` 块）都判了空【实测】。`Glass*Filter::can_render` 没有问是不是 Metal，液态玻璃在软件路径上画成什么样不清楚。

### 5.3 运行记录

| 来源 | 环境 | 现象 | 位置 |
| --- | --- | --- | --- |
| sheng，10-28 上午 | 24A446，`offline-legacy-cpu-present=1` 这组参数 | 300 秒内三次交换完成，无 panic，**屏幕显示苹果标志**，桌面未验证 | `U/docs/zh/porting/display.md:53-56`，提交 `218e1e8` |
| DroidVM，10-29 16:53Z | 24A446，同一组 `offline-legacy-*`，含 `cpu-present`，无 GPU | backboardd 驱动主显示，SpringBoard 停在 `UIApplicationMain`，**“one black frame is presented and no more: this kernel has no GPU, and the render server composites with Metal”** | DV `1c32e7c6` |
| DroidVM，10-29 19:16Z | 加 virtio GPU、AdrenoMetal、Turnip | **Metal 画出锁屏** | DV `23af1b22` |
| DroidVM，10-30 | 壁纸 | `SBRendererService` 的 NeutrinoCore 没有 Metal 设备时断言中止 | DV `8277f558` |
| 上游，10-29 | 27 的 Core Image | 没有 Metal DAG 时回退是空桩，iconservicesagent 崩 | `AM` `ec2e1d1` |

17 上的对照：

| 来源 | 现象 | 位置 |
| --- | --- | --- |
| 13T | `pagani-gpu=0` 是 CPU 合成镜像 | `UniXNU-Pagani_SM8750-gpu/board.toml:42`、`A8/docs/PROGRESS.md:746-750` |
| DroidVM（SM8650） | 启动约一分钟到锁屏，按 Home 到主屏幕，CPU 渲染 | `U/docs/zh/devices/droidvm.md:16` |
| 小米 8 SE | 约 19 fps CPU 呈现；跳过 Setup 时 PosterBoard SIGABRT 循环，不跳过才进得了 Setup.app | `U/docs/zh/devices/sirius-dddqmmx.md:7,58` |
| alioth | 没有 Metal 时壁纸和海报不画，App 窗口无内容 | `U/docs/zh/devices/alioth.md:461` |

### 5.4 怎么解释这个矛盾

服务端代码和 17 一样，27 上却不出画面。**DroidVM 作者的解释“render server 用 Metal 合成”只说对了一半：** 代码里明明有回退【实测】。他没有继续查，2.4 小时后 Metal 通了就走了。我认为更可能的原因在客户端【推断】：

- **SpringBoard 没有提交内容。** “一帧黑”很像 CA 自己的空白上下文（`Server::create_blank_context`），之后没有更新就没有交换。SpringBoard 可能在等海报或 `SBRendererService`。小米 8 SE 在 17 上的症状是同一类。
- **30 s 等待叠加。** 每个进程第一次碰 CA 加速路径都等 30 s，SpringBoard、海报扩展串起来，观察窗口短就什么也看不到。发布一个不带插件的 `IOAcceleratorES` 就能跳过，alioth 在 17 上这样做过，没有崩溃（`U/docs/zh/devices/alioth.md:437`）。
- **sheng 那次可能是数据迁移。** `SH/scripts/make-nosetup-seed27.py:5-6` 写着 27 的首启迁移让全部核忙 20 分钟以上。300 秒的苹果标志可能只是还没到。

这些都没有验证。也不能排除 27 的 SpringBoard 真的有一处硬依赖 Metal。我查了 27.1 的 `SpringBoard`、`CoverSheet`、`PosterKit`、`UIKitCore`，符号里没有直接调 `MTLCreateSystemDefaultDevice`，`SpringBoardFoundation`、`SpringBoardUIServices`、`PosterBoard`、`NeutrinoCore`、`RenderBox`、`CoreUI` 有【实测】。这说明不了调用失败时会怎样。

### 5.5 软件 Metal

- `MTLSimDriver.framework` 只在模拟器运行时里，设备 27.1 的 `PrivateFrameworks` 没有【实测】。
- 设备 `Metal` 里有 `MTLCreateSimulatorDevice` 的 dlopen 路径，打不开就返回空（`0x19fe7e0e8`）【实测】。它本来就是转发到宿主 GPU 的，不是软件实现。
- `Extensions` 里 Metal 相关的只有 AGX 和 `AppleMetalGLRenderer`（架在 Metal 上的 GL）【实测】。
- 17 上 Core Image 无 Metal 时走 GL 崩溃（`U/docs/zh/devices/sirius-huajifruit.md:389`）。27 上 `ec2e1d1` 记录的回退是空桩，无 Metal 时大概率也崩【推断】。

### 5.6 判定实验（一次上机就能分清）

配置：24A446，无 GPU addon，`offline-legacy-*` 全组，加 `sheng-log=1 boot-ui-trace=1`。

| 设计点 | 做法 | 为什么 |
| --- | --- | --- |
| Setup | **不跳过 Setup** | 小米 8 SE 的经验，绕开海报 |
| 迁移 | 用 `make-nosetup-seed27.py --keep-migration` 的思路关掉。这个脚本的基底是跳过 Setup 的种子，和上一行有冲突；关不掉就等够 30 分钟再判 | 否则可能只是在等迁移 |
| 加速器 | 发布不带插件的 `AGXAccelerator`，或接受 30 s | 排除等待干扰 |
| 偏好 | 写 `CAEnableDeepFramebuffer`、`CAEnableFramebufferCompression`、`CAEnableIOSurfaceCompression` 为 false，`CAEnableMedina=false`（DV `1c32e7c6`：否则 SpringBoard 在安全指示层中止） | 排除已知中止 |
| 观察 | backboardd、SpringBoard 的栈；`swap` 次数和格式；`Failed to initialize` 日志 | 区分服务端不画和客户端不提交 |

**判据：** 出 Setup 或锁屏画面就算 S1 成立。两轮内不出，停，转 S2。

就算成立也只是调试手段。13T 上 CPU 拷贝一帧 143–285 ms（`A8/docs/PROGRESS.md:244`），Pagani 记录 12.25 MiB 用 386 ms、约 2 fps（`PG/STAGE6.md:1179`），加上软件合成，只能看画面，不能当日用桌面【读码 + 推断】。

### 5.7 对里程碑的影响

- **17 上“先 CPU 后 GPU”的顺序，到 27 上不再有优势。** 软件路径未证实，而 GPU 路径有两个独立实证，内核和 Turnip 又都已编过。
- **显示后端两边都要**，所以它是真正的第一步。
- **DroidVM 从显示起来到 Metal 锁屏只用了 2.4 小时**（`1c32e7c6` → `23af1b22`），这是有现成 Metal 栈的结果。我们的 A830 栈也现成，只差 27 专项。

---

## 6. 工作分解

**前提 P：** 27 内核在 13T 上到 launchd，含 fileset、arm64e、SMP、UFS、根卷、日志。这部分见 `drivers-27.md`（到 L 约 3 周）和 `storage-27.md`，不计入下表。PAC 见 `pac-oryon.md`。额外一条佐证：DroidVM 的 arm64e 内核在 8 Elite Gen 5 的 Gunyah 客户机里起到了 launchd（DV `cd71f02c`、`b56ce8ce`）。虽是 VM，但说明同系列 CPU 在 Gunyah 下 PAC 能用【读码 + 推断】。

工作量：**S** 一天内，**M** 几天，**L** 一周以上。全部【推断】。

### S1：软件显示探针

| # | 工作 | 量 | 来源 | 备注 |
| --- | --- | --- | --- | --- |
| D1 | `NativeLegacyDisplay.cpp` 接 24A446 ABI | M | `GF/`、`SH/scripts/build-gop-legacy-display-27.py`（取 `6dafef9` 版）、`legacy-display-24A446.json` | 试编只有 4 个错【实测】：`isTimingEnabled` 改 `cancelSwapsAfterTimeout`、`LEAF` 换 `NATIVE_LEGACY_LEAVES`、加默认 surface。上游版同配置 0 错，作为对照。与 `drivers-27.md` 5.8 重合，不重复计 |
| D2 | `build-stage7-display.py` 的 27 分支 | S | 同上 | 34 个 21H461 字面量换 JSON |
| D3 | `Stage7Probe` 接 27 流程 | S | `SH/package/LegacyDisplayProbe.cpp` | 客户端插桩在 ptrauth 下要关 |
| D4 | Data 种子偏好和不跳过 Setup 的变体 | S | `SH/scripts/make-nosetup-seed27.py`、DV `seed-data-prefs.py` | |
| D5 | 发布不带插件的 `IOAcceleratorES` | S | K1、K2 的子集 | 可选，跳过 30 s 等待 |
| D6 | 上机判定，两轮封顶 | M | 5.6 节 | 含读栈 |

S1 合计：编码约一周，且大部分是 S2 也要的。

### S2：经 A830 的 Metal 桌面

| # | 工作 | 量 | 来源 | 备注 |
| --- | --- | --- | --- | --- |
| K1 | `build-adreno830.py` arm64e 化 | S | `build-adreno740-accelerator.py`（`474cfee`） | 1.3 节 1–3 |
| K2 | 板级胶水、固件汇编 | S | 自写 | 试编已过 |
| K3 | `__DATA` 放不下时改运行时分配 | S–M | 自写 | 1.5 节 |
| K4 | XNU 补丁：`zzwc`、`gpu-client-memlimits`、挂载探针路径 | S | 基线已有大半 | `ios27-experiment/device/…/patches/xnu-12377/` 已有初稿 |
| K5 | 上机：S0–S8 自测，GMU、CP、SMMU 在 12377 上跑通 | M | — | 寄存器逻辑不变，查的是中断、线程、内存 |
| U1 | Turnip `ios27` 目标 | S | `build-turnip-darwin.py` | 已试编 |
| **U2** | **AdrenoMetal 合到 `d6ca655`** | **L** | `AM`，`BU/a830-inputs/upstream/artifacts/metal/resolve2.py` | **最大一项。** 21 处冲突已有解法。17 上阶段 2 先做完，这里归零 |
| U3 | `d6ca655` 之后的 27 专项 | M | 2.1 节 | 约 7 个提交，含 BGRA 修补 |
| U4 | Rust arm64e：`rust-src`、改写、重命名 | M | `rust-pac-rewrite.py`、`RustPacBoundary.c` | 要用户同意下载 `rust-src` |
| U5 | 翻译器保持 `720ef1c`，补丁重排 | S | `d6ca655` | 随 U2 |
| U6 | 27 的预热列表和着色器包 | M | 4.4 节 | 可后置 |
| U7 | 客户端名单：`SBRendererService`、海报扩展 | S | DV `8277f558` | |
| U8 | **上机：render server 崩溃排查到锁屏** | L | — | 每个缺的选择子都是一次崩溃 |
| U9 | 颜色、alpha、GMEM 对照 | M | `scanout_opaque`、`sysmem,nocb` | 13T 的 DPU 是否按 alpha 混合未知 |
| U10 | 壁纸叠加和海报 | M | `SH/scripts/pack27-gpu.sh`、`make-wallpaper-overlay.py` | |
| U11 | 第三方 App 的 arm64 切片 | S–M | 自写 | 可后置 |

S2 合计：**17 上阶段 2 已完成时约 2 周，否则约 3 到 4 周**，都含上机，不含 P。

### 移植来源与许可

| 来源 | 许可 | 用什么 |
| --- | --- | --- |
| UniXNU、sheng、GopFramebuffer、XnuPrelink | BSD-2-Clause-Patent | 构建脚本、ABI 数据、BRINGUP、显示后端 |
| DroidVM（`UniXNU-droidvm`） | BSD-2-Clause-Patent（`LICENSE` 已读【实测】） | `seed-data-prefs.py`、思路 |
| Adreno740、AdrenoMetal、ArmSmmu4K | MIT | 前端、桥、PAC 边界 |
| Mesa | 按其文件 | 只用我们已有的补丁 |
| metal2vulkan | GPL-2.0-or-later 或 LGPL-3.0-or-later（`Cargo.toml:19`） | 静态链进插件。现状如此，对外发镜像前要留意 |

都可以带署名移植。

### 总判断

| 段 | 时间 | 把握 |
| --- | --- | --- |
| P（别的报告） | 约 3 周 | 中 |
| S1 | 约 1 周，大部分复用到 S2 | 编码高，出画面一半 |
| S2 | 2 到 4 周 | 能出锁屏高，流畅度低 |
| 合计到可演示的 Metal 桌面 | 约 6 到 8 周，一人全职 | 大量上机 |

这只是到“GPU 合成的桌面出来”，不含 iPhone 身份、键盘、壁纸等界面修复和外设恢复。

---

## 7. 风险

### 7.1 性能

上游数据全部是 sheng（A740，约 680 MHz，3048×2032）【读码】：

| 指标 | 数值 | 位置 |
| --- | --- | --- |
| 锁屏每命令缓冲 GPU 时间 | 系统内存 17.1 ms，p95 52.1 ms；GMEM 17.8、56.1 ms | `gpu.md:92-93` |
| render pass GPU 时间 | 中位 0.5 ms，全屏 1.1 ms | `gpu.md:110` |
| 驱动锁等待 | 都低于 7 ms | `gpu.md:110` |
| 海报扩展任务 | 约 77 ms 一个，环上不分优先级 | `gpu.md:111`、`SH/porting.toml:169` |
| 内存块锁定 | 94–112 MiB 要 281–340 ms | `gpu.md:113` |

粗算：13T 1216×2640（`工具/make-a830-layout.sh:200`）约 3.2 MP，是 sheng 的 52%；A830 到 900 MHz，是 1.3 倍。17 ms 大约缩到 7 ms 上下【推断】，60 fps 够，120 Hz 紧张。这只按像素和频率算，没算架构差异。

**排队比算力更要紧。** 77 ms 的海报任务压在环上，再快的 GPU 也要等。CP 抢占上游默认关，A8xx 寄存器也不同，不能指望。缓解办法是壁纸叠加（上游已这样做）和限制海报扩展。

用户现在对 17 的评价是“基本跟原生没区别”。27 大概率是回退，这点 `ipados27-parity.md:194` 已提，新数据没有推翻它。

计算函数从 15 涨到 54【实测，27.1 模拟器库】，背景捕获走 compute，A830 的 compute 路径过去没怎么被压过【推断】。

### 7.2 固定内存池

| 项 | 数值 | 位置 |
| --- | --- | --- |
| 我们的池 | 默认 512，整包 `pagani-gpu-pool=1024`，上限 2048 | `PaganiGpuBoard.cpp:73`、`工具/make-a830-images.sh:71`、`A8/Adreno830Start.cpp:987` |
| 17 上用量 | 831/1024，最高 921 | `A8/docs/PROGRESS.md:337`、`merge-plan.md:219` |
| sheng 27 | **1536 MiB 池填满**，404 个 BO 落到散页，96 次 96 MiB 分配失败 | `A7` `c8806db` |
| 上游解法 | 系统内存 BO，无固定池 | `A7` `c8806db` |
| 翻译器块 | 64 MiB 块时主屏 2 GiB（客户机） | 补丁 `0013-growing-memory-blocks` 注释 |

**判断：** 1024 MiB 在 27 上大概率不够，1536 也不够【推断】。溢出后走散页，散页受 VM 区段上限 262144 限制，全散时约 1 GiB（`A8/NativeAdrenoGpuContext.cpp:96-101`）。ARCHITECTURE 记录 12 个客户端时已有 `vmr 37`（`A8/docs/ARCHITECTURE.md:1202`）。

**做法：** 第一轮 `pagani-gpu-pool=2048`，内存窗口 6 GiB 不变，看 `MEM` 行。不够再移植系统内存 BO，那是 L，且要带 `pmap_sync_wimg`（已在基线）和 VM 索引改造。翻译器块增长（`0020`）在 U2 里，能减压。

### 7.3 地址窗口和连接数

**题目的“4 GiB、12 窗口”已经过时。** 现在默认：

| 表 | 总量 | 槽 0 | 大槽 | 小槽 |
| --- | --- | --- | --- | --- |
| 16 GiB（默认） | 32 个 | 1536 MiB | 3 个 512 MiB | 28 个 448 MiB |
| 4 GiB（回退） | 12 个 | 1536 MiB | 3 个 352 MiB | 8 个 124 MiB |

位置：`A8/NativeAdrenoPublish.h:46-67`、`A8/Adreno830Board.h:169`。

| 限制 | 27 上会更早撞吗 | 判断 |
| --- | --- | --- |
| 16 GiB 总空间 | 不会 | 上游本来就按需，A830 够用 |
| 槽 0 的 1536 MiB | 不急，要看数 | 17 上 backboardd 自有约 511 MiB、导入约 100 MiB（`A8/docs/PROGRESS.md:337`）。27 的增幅没有数据。RGhA 帧缓冲每像素 8 字节（`AM/NativeAdrenoMetalPlugin.mm:398`），BGRA 模式能省一半 |
| 小槽 448 MiB | 不太会 | 比 4 GiB 表的 124 MiB 宽很多 |
| 32 个连接 | **可能** | 上游“每个 Metal 进程都给设备”。17 上 11 个窗口时已拒过 Maps、Spotlight 等（`NativeAdrenoPublish.h:31`）。27 多了 `SBRendererService` 等，App 一多会到 `gpu noslot`。保留我们的 `ClientRule.h`，别抄上游的全给 |
| 连接对象数 16384/8192/4096 | 不太会 | 看 `vmr`、`ovf` 再说 |
| VM 区段 262144 | **会** | 见 7.2，共享一个 VM，所有客户端合计 |

如果 34 位输入回退到 4 GiB 表，App 窗口只有 124 MiB，27 上基本不够用【推断】。第一轮要确认日志里 `gpu aperture` 是 16。

### 7.4 其他

| 风险 | 说明 | 级别 |
| --- | --- | --- |
| Metal 失败直接中止 | QuartzCore 建管线失败就 abort（`MetalContext::get_compute_pipeline`）【实测】。任何一条 27 新管线在 gen8 编不过，backboardd 就崩，所以 U8 是 L | 高 |
| `framebuffer_pixel_format` 修补改共享缓存代码页 | 上游靠 `vm_protect` + `VM_PROT_COPY`。我们的 AMFI 策略若不同可能失败，失败时走 RGhA，就要半浮点转换 | 中 |
| 帧缓冲 alpha | 13T 若按 alpha 混合，液态玻璃周围有色块 | 中 |
| GMEM | 我们的流畅度靠它，27 上未验证 | 中 |
| 第三方 App | arm64 载不了 arm64e 插件 | 中，看用途 |
| `@2x` 强制 | 27 上超过 1080 像素高或虚拟设备一律 `@2x`（DV `34c42ef1`，`-[CADisplayMode preferredScale]`），直接影响 13T 的布局方案 | 高，但不属于 GPU |
| `IsVirtualDevice` | 不能对用户态改 0（`SH` `cc62bf4`），很多行为按虚拟设备走 | 中 |
| `--allow-missing` | XnuPrelink 配不上的函数跑到才 panic，IOGPUFamily 路径也可能有 | 中 |

---

## 8. 对已有文档的更正

| 文档 | 原说法 | 更正 |
| --- | --- | --- |
| `ipados27-parity.md:70`、`AM/NativeBridgeMetalFrontend.mm:1319` | `sys_dcache_flush` 只剩屏障是 27 的行为 | 21H461 也一样【实测】 |
| `adrenometal-delta.md:197` | 我们的表面维护更好 | 清洗部分成立，失效部分是空操作 |
| `merge-plan.md` a.6、`adrenometal-delta.md` 第 4 节 | 默认 12 个窗口 | 10-31 起默认 16 GiB、32 窗口 |
| `drivers-27.md` 第 8 节第 1 条 | 上游文档只记录到苹果标志 | 补充 DroidVM 的无 GPU 记录，以及 27.1 的反编译 |

---

## 9. 未核实

- 没有 24A446 的共享缓存和 kernelcache。QuartzCore 分析用的是 27.1 iPhone 版，`arm64e.x1` 切片里还有 `ADDPT`（FEAT_CPA）这类指令，不能据此判断 24A446 的指令可用性。sheng 跑得动说明 24A446 没问题【推断】。
- Adreno830 只编译没链接，配置宏是 10063 的。
- Rust 桥 arm64e 没编，缺 `rust-src`。
- 插件和前端只做了语法检查。
- 翻译器实测用模拟器库和 priming 路径，没有函数常量变体，也没有跑 Vulkan 管线之外的东西。
- 软件路径的根因全部是推断。
- 27 上各进程的 GPU 内存、连接数，没有 13T 数据，sheng 也只有零散数字。
- 13T 的 DPU 是否按 alpha 混合。
- fileset 给原生 `__DATA` 还剩多少。
- 第三方 arm64 App 载 arm64e 插件的行为没有实测。
- 工作量全部是估计。

---

## 附录 A：本次实测产物

都在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/`，重启会清空，结论都已写进正文。

| 目录 | 内容 |
| --- | --- |
| `cc27/` | Adreno830 试编脚本 `check.py`、`compile.py`、`board.py`、`disp.py`，41 个 arm64e 目标文件 |
| `tu27/` | arm64e Turnip 构建 |
| `sc27/` | 27.1 QuartzCore 试翻译 |
| `disp27/` | 24A446 显示 ABI 头 |
| `q27/` | 27.1 QuartzCore、Metal 符号表 |
| `gh/` | 上游新提交的差异文本 |

## 附录 B：操作说明

有两处超出了“只写两处”的约束，都没有留下影响，仍在此记录：

1. 准备 Turnip 试编时，我在一条命令里误写了 `rm -rf /dev/null`。这条命令没有任何用途，是笔误。它被系统拒绝（`Operation not permitted`），`/dev/null` 完好（`crw-rw-rw- root wheel`，已核对）。
2. 生成 24A446 显示 ABI 头时，我让 Python 从 `U/scripts` 导入 `kernel_target`，它在只读的参考目录里写了 `scripts/__pycache__/kernel_target.cpython-314.pyc`。发现后我只删了这个文件和它新建的空目录，`git status` 干净。`build-userspace` 和 `Adreno830` 下没有发现本次运行写入的文件（按修改时间核对）。
