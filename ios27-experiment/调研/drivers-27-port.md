# L 里程碑驱动的 27 副本：移植记录

时间：2026-11-01。证据等级：**【实测】** 本次跑出来的；**【读码】** 读源码确认；**【推断】** 由前两者推出。全程没有碰手机，没有下载，没有提交，主线目录只读。

路径约定：`E=/Users/xiaochoumao/unixnu-13t/ios27-experiment`，`S=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/w`（草稿目录，会话重启可能清空）。

## 0. 结论

- **八个目录都移植完了，全部能按 arm64e 编译，能跑的主机测试全部通过。** 副本在 `$E/drivers/` 和 `$E/kits/`。
- **内核链接成功了。** 用补丁系列的完整 12377 树（114 个补丁）加 L 驱动，经 Xcode 26.6 的 `ld-classic` 链接出 `MH_EXECUTE`、arm64e、`KER00`、`NOUNDEFS PIE`、26,102,048 字节的内核。只用了桩：firehose 5 个、`corecrypto_kext_start`、`native_owner_cache_stage`（手工 make 没传开关）、全零的 `verified_root_key`。**这个内核不能启动**，它证明的是符号闭合。
- **`early_boot_progress_init` 在 rebase 之前是安全的，不用推迟入口。** 从目标文件走调用图，18 个可达函数、95 个全局量，全部 PC 相对寻址，没有一个走 GOT 或认证指针，没有间接跳转。在链接好的内核里走完 103,613 个修复位置，没有一个落在这 95 个符号范围内。第 4 节逐个列出。
- **`setCPUState` 覆盖风险是真的，但在 13T 上没有行为后果；我把它消除了，方法是把这一步挪到 `ml_processor_register` 之前。** 判断见 3.5。
- **有三处我没有照抄上游，都是有意的：** `PaganiArmCpu` 去掉了空操作的 `processor_start` 线程；`NativeKeyStoreEvents` 放宽后在持久 Data 上关掉易失钥匙包（直接放宽会丢钥匙串）；`SourceStorage.cpp` 去掉了 `boot_log_*`（我们的 `UfsLog.c` 自己启动和重试）。
- **没有新增 XNU 钩子的需求。** 缺的是构建脚本，见第 6 节。
- **所有运行时行为都没有上机。** 未验证项见第 7 节，最大的两个是 IOStorageFamily-337 的类布局和真实 fileset。

## 1. 环境

| 项 | 值 |
| --- | --- |
| XNU | `xnu-12377.121.6`。两份编译记录：`$S/x12377`（只有上游系列，上一轮用的），`$S/xnu-series`（`scripts/check-xnu-12377-series.sh` 打完 114 个补丁，含另一路的设备层） |
| 编译 | Xcode-beta，`-arch arm64e -mkernel`，记录来自 `IOPlatformExpert.cpo.json`、`startup.o.json`、`kern_exec.o.json`、`start.o.json` |
| 链接 | `DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer`，`-fuse-ld=.../ld-classic`，`-Wl,-threaded_starts_section` |
| 存储头文件 | `tree/build/storage-abi/include`，与 `UniXNU/out-gpu/feeds/IOStorageFamily-67f9003c` 的 `IOBlockStorageDevice.h` 完全相同。**没有 IOStorageFamily-337** |
| 上游钉住版本 | DarwinBoot `3c75734`，VerifiedRoot `974f043`，RamBlock `4be2d7e`，VirtioMmioBlock `4e1013e`，ArmCpu `93a5030`，sheng `6b11bb5` |

## 2. 总表

| 目录 | 差异（`ORIGIN.diff`） | arm64e 编译 | 主机测试 |
| --- | --- | --- | --- |
| `drivers/PaganiDarwinBoot` | +413 −20 | EDK2 CLANGPDB 通过（UEFI，不走 XNU 记录） | 全过，QEMU 三组全过 |
| `kits/PaganiVerifiedRoot` | +66 −10 | 2 个文件 0 错误 | 9 组全过 |
| `drivers/NativeUfs` | +983 −45（含新测试） | 6 个编译任务 0 错误，4 个旧告警 | 全过；`Sm8750.cpp` 基线就不过 |
| `drivers/NativeRamBlock` | +40 −21 | 0 错误 0 告警 | 6 个全过 |
| `drivers/PaganiArmCpu` | +18 −29 | 0 错误 0 告警 | 3 个全过 |
| `drivers/PaganiArmDTPlatform` | 无改动 | 0 错误 0 告警 | 全过，59 个变异全抓到 |
| `drivers/NativeKeyStoreEvents` | +140 −7 | 2 个文件 0 错误 | 2 组全过 |
| `drivers/BootProgress` | +13 −3 | 4 个 C、`WeakDefaults.c`、`Entry.S` 0 错误 | 9 组全过 |

## 3. 每个驱动改了什么

### 3.1 `PaganiDarwinBoot`

**改动：** `DarwinBoot.c` 以上游 `3c75734` 为底，恢复我们的 `DramOffsets`（`dram-base`、`dram-size` 占位符）和 `CNTFRQ_EL0` 调试行。带来 `ParseKernelHeader`/`ParseKernel` 的 fileset 解析、子类型 `& 0x00ffffff` 接受 0 和 2、`.vroot`、`.gpuaddon`。

**测试改动：** `make-fixtures.py` 加 `--fileset`、`--vroot`，`Kernel.S` 在 `FIXTURE_VROOT` 下校验 `RootPolicies` 的地址、大小、对齐和魔数，`Validation.c` 加 7 个 `.vroot` 拒绝用例（没有占位符、缺文件、占位符非零、魔数、版本、数量、大小）。

**验证【实测】：**

| 项 | 结果 |
| --- | --- |
| `test-adt.py` | 通过，含 `GpuAddon`、`RootPolicies` 占位符 |
| `test-kernel-parser.py` | 合成 fileset 接受，11 个拒绝 |
| `test-packed-ramdisk.py`、`window-size.c` | 通过 |
| 解析真实内核 | 这次链接出的 arm64e 内核通过 `ParseKernel`：base `0xfffffff007004000`，entry 等于 `__start` |
| QEMU（`Validation.c`） | 三组 `SMOKE PASS`：普通、`--fileset --vroot`、`--memory-size 0x20000000 --fileset --vroot` |
| QEMU 输出 | 33 个拒绝请求、7 个 `.vroot` 拒绝、8 个 GOP 拒绝，`ExitBootServices` 后夹具打印 PASS |

**限制：** `ReadFile` 内核上限 `SIZE_128MB`，`.vroot` 上限 `SIZE_128MB`，`MAX_SEGMENTS 64`。24A446 的 kernelcache 解压后是否低于 128 MiB 没有验证。

### 3.2 `PaganiVerifiedRoot`

取上游 `974f043` 的 `src/verified_root.c`、`include/verified_root.h`、`tests/check.py`、`tests/openssl_provider.c`，新增 `vr_open_header`、`check_header`。`tests/check.py` 输出 `PASS: 9 groups using real RSA signatures and actual C block verification`【实测】。

### 3.3 `NativeUfs`

**第 2.1 节的 5 个编译错误：** 按 `gh:ShengUfs/NativeUfs.cpp:11-14,1773` 改头文件，`vm_map_protect` 换成 `mach_vm_protect (kernel_map, verifiedStorage, verifiedStorageBytes, TRUE, VM_PROT_READ)`。原来的参数是（起点、终点、权限、`set_max`），现在是（起点、大小、`set_max`、权限），语义相同【读码】。

**清单 v2（`RootLayout.h`）：** 版本 2 最多 8 项，窗口和数据对象各最多 4 个。数据对象 `block_size` 4096、`flags` 3，大小不要求按扇区对齐。窗口和对象共用名字空间和重叠判定。v2 没有窗口时状态 7。

**有一处比上游严：** 上游把 `size & 511` 判定改成只对窗口生效，v1 里属性错又不对齐的项会从状态 4 变成 7。我写成 `(version == 1 || isWindow) && (size & 511)`，v1 的行为一个状态码都不变。

**根基址：**

- 启动参数 `pagani-root-base-mib`，默认 0，单位 MiB，内部换算成 4096 字节块（`rootBaseBlocks`）。
- `findRootLayout` 多一个 `rootBase` 参数，记入 `RootLayout.rootBase`。清单从 `userdataFirst + rootBase` 读。
- `imageBytes` 的上限从“整个分区”变成“基址以上的部分”。
- 窗口和数据对象的绝对位置统一走 `rootAbsolute`、`rootImageFirst`，`NativeUfs.cpp` 里三处手算都换掉了（探针 11、窗口准备、`gRootObjectBase`）。
- `ufsLogPlan`、`ufsLogOpenWindow` 多一个默认为 0 的 `rootBase`。`native_ufs_store_open` 的两个判定抽成 `ufsStorePlan`，放进 `LogWindow.h`，主机测试才跑得到同一份代码。
- 日志和 Data 的**位置**不随基址变，都是从分区末尾算；基址只影响**是否允许**。
- **基址出分区直接拒绝，基址处没有清单就不发布（状态 1），都不回退到分区开头**。回退等于用 27 内核挂 17 的根，这是必须防的。

**`readPolicyTable` 挪了位置：** 上游在 `NativeUfs.cpp`，我放进 `RootLayout.h`，这样主机测试能覆盖基址加对象偏移的那一步。

**源码存储：**

| 文件 | 处理 |
| --- | --- |
| `SourceRootBinding.cpp`、`SourceRootPolicies.cpp` | 带署名原样移植 |
| `SourceStorage.cpp` | 带署名移植，去掉 `boot_log_tick`、`boot_log_check`、`boot_log_*`。我们的 `ufs_log_start` 由 `boot_progress_start` 调用（`osfmk/kern/startup.c`），`native_ufs_log_open` 返回 `no root layout` 时每秒重试（`UfsLog.c:309,578`），不依赖这里。加了弱空实现 `native_keystore_probe`，见 3.7 |
| `make-source-base.py` | 从上游构建脚本 `:152-165` 抽出，生成元类改写后的 `IOBlockStorageDevice.cpp` |
| `VerifiedBlockPolicy.h` | 取自 VirtioMmioBlock `4e1013e`，放进本目录，不再依赖 `NativeVirtioBlock` |
| `NativeUfs.cpp` | `NATIVE_UFS_SOURCE_API` 三处，只含头部的策略 |

**新测试：**

- `tests/RootBase.cpp`：用 13T 的真实分区数字（first 107409152，last 124186367）建模型 LUN，17 镜像在开头，27 镜像在 10240 和 27648 MiB。覆盖清单、绝对 LBA（110030592、114487040）、镜像边界、v2 对象跨块读取、基址出界、不回退、日志和 Data 两侧的重叠边界（差一个块）、回绕。
- `tests/RootBaseMainline.cpp`：把**主线的** `RootLayout.h`、`LogWindow.h` 和副本编进同一个程序，同样输入逐字段、逐次读比较。`check_root_path_host.py --mainline DIR` 启用。

**验证【实测】：**

| 项 | 结果 |
| --- | --- |
| `check_root_path_host.py` | 14 项全过，含 `RootBase`、`RootBaseMainline`，ASan 加 UBSan |
| 基址 0 与主线比较 | 20 万个变异输入：60,305 个接受、98,291 个清单拒绝、41,414 个 GPT 拒绝，结果和读序列完全相同；200 万组日志和 Data 判定完全相同 |
| 现有测试未改语义 | `LogWindow.cpp`、`RootLayout.cpp` 一行未动。`LogInterface.cpp` 给结构体加字段，另加 2 个基址用例；`RootPathBridge.cpp` 改用 `rootAbsolute` |
| 变异检验 | 故意改坏 14 处生产代码，全部被测试抓到（比如去掉 `rootBase > room` 判定、清单读取忘加基址） |
| `check_qemu_root_commands.py` | 通过，用通用 QEMU UFS |
| `check_arm64_root_commands.py` | `PASS ALL`。脚本要 `git rev-parse`，在草稿目录建了一个临时仓库跑的 |
| `check_legacy_descriptors.py`、`LunDiscovery`、`TransferPoll`、`SmmuTakeover` | 通过 |
| `tests/Sm8750.cpp` | **失败**，2 个 `static_assert`（`GccUfs.h` 的常量）。基线目录同样失败，与本次无关，没有改 |

**“基址 0 与主线完全一致”的边界：** v1 清单完全一致，上面已证明。v2 清单是新能力，主线给状态 2，副本接受，基址 0 也一样，这是有意的。

**数字对得上【实测】：** 基址 10240 加 16 GiB 镜像，日志和 19 GiB 的 Data 都放得下；Data 窗口落在分区 44 GiB 到 63 GiB，与 `storage-27.md` 的规划一致。

### 3.4 `NativeRamBlock`

取上游 `4be2d7e` 的 `PersistOverlay.h` 和 `Open::Kept`（诊断状态 7），保留 `declareSolidState`。`Test/direct.cpp`、`persist.cpp`、`seeddisk.cpp` 取上游。

**行为变化要知道：** 窗口里不是这个种子的数据时，以前自动重建，现在保持原样、本次 Data 只用种子跑。**第一次开持久 Data 必须带 `sheng-data-reset=1`**。第一阶段 Data 在内存里，不受影响。

**对接源码存储：** 不用改代码。`SourceStorage.cpp` 用 `allocClassWithName("NativeRamBlock")` 启动，构建时要去掉它的 `__mod_init_func`（第 6 节）。

**验证【实测】：** 6 个测试全过；`packed-hash`、`seed-identity` 的结果与 `packed_ramdisk.py`、整盘 `shasum` 相同。`sparse.cpp` 在基线就失败：用例还按 2 GiB 上限，代码已是 1 TiB，我把这一个用例改对了。

### 3.5 `PaganiArmCpu`

**启动核：** `setCPUNumber`、启动参数、`IOCPUInterruptController`、`registerInterrupt` 全部挪到 `ml_processor_register` 之前。12377 里 `ml_processor_register` 内部调 `processor_boot`（`machine_routines.c:1761-1770`），一路走到 `cpu_machine_init` → `initCPU(true)`，这时中断路径必须已经装好。之后 `if (!bootInitialized) { initCPU (true); }` 防止跑两次。`bootInitialized` 在 `initCPU` 的两个出口置位，包括 `skipGic`。

**次级核：** `setCPUNumber`、`setCPUState (kIOCPUStateUninitalized)`、`armcpu_smp_step = 2` 挪到 `ml_processor_register` 之前。

**去掉了 `startSecondary`、`startThread` 和 `processor_start` 声明，上游保留了。** 理由：12377 的 `processor_start` 是空操作，只返回 `KERN_FAILURE`（`processor.c`，`all-alias.exp:84`）。线程留着的唯一效果是写 `armcpu_smp_step = 3`，而此时新核可能已经写到 7，起核诊断会读错。**第 3 步从此不再出现**，步骤变成 1、2、4、5、6、7。

**没动的：** 单 pset（`ml_get_boot_cluster_type`），不带 Edge，Aff2 掩码 `0xff00ffffffULL`（四处），`info.processor_idle`，`idleStart` 仍在次级核启动之前。

**没有调 `ml_cpu_init_completed`，与 17 和上游默认一致。** 后果【读码】：`pcs_init_completed` 保持 false，`pcs_powerdown_suspend_count` 保持 1，簇下电一直挂起。`sched_rt_init_completed` 的内容只在 `CONFIG_SCHED_EDGE` 下才有，BCM2837 是 `config_clutch`，不受影响。

**`setCPUState` 覆盖风险的判断：**

- **风险是真的。** 12377 的次级核在 `ml_processor_register` 内启动且不等待（`processor.c:1349-1353`），上游的写法里 `setCPUState (kIOCPUStateUninitalized)` 在其后，新核的 `kIOCPUStateRunning` 可能被盖掉【读码】。
- **后果很小。** `getCPUState` 的使用方只有 `IOCPUSleepKernel`（`IOCPU.cpp:302,352,356`）和 `IOCPUState` 属性字符串（`:450,476`）。13T 走不到前者，有两层：`board.toml:148` 的 `sleep-disabled = 1`，以及不调 `ml_cpu_init_completed` 时 `sched_override_available_cores_for_sleep` 会直接 panic。所以唯一可见的后果是 ioreg 里显示错误状态【读码】。
- **已消除。** 把这一步挪到注册之前，改动一行，所以这个竞争不存在了。这比上游严，没有副作用：`setCPUState` 只写字段。

**验证【实测】：** 编译 0 错误 0 告警，`psci-idle`（2,634,553 项）、`scmi-perf`（2,851,641 项）、`spi-gate`（15 项）全过。但这三个测的是头文件里的逻辑，**注册次序没有主机测试**，只有读码和上机能验证。

### 3.6 `PaganiArmDTPlatform`

没有源码改动，`ORIGIN.diff` 是空的。两份记录都 0 错误 0 告警，`check-platform-host.py` 全过，59 个变异全抓到【实测】。第 5.5 节说的“改构建检查”在 `scripts/` 里，不在驱动目录，见第 6 节。

### 3.7 `NativeKeyStoreEvents`

**新增 `KeyStoreProbe.cpp`：** 移植 sheng 的 `package/KeyStoreProbe.cpp`，加上主线 `Stage7Probe.cpp` 的 `startKeyState`，提供 `native_keystore_probe` 和驱动要的五个钩子。L 不带显示，`XNU_NATIVE_LEGACY_DISPLAY` 关着，`bsd_init.c` 的 `native_legacy_display_late_probe` 不调用，所以入口放在 `SourceStorage.cpp` 末尾。**不需要新 XNU 钩子。**

**放宽判断，但没有照抄。** 上游只是把 `ephemeral != 1` 的拒绝放宽。我们多一个易失钥匙包（`pagani-keybag`），类密钥每次启动随机生成，README 明确写着不能和持久 Data 一起用：`securityd` 下次启动解不开旧条目，会删掉。所以：

- 持久 Data 上服务照常起，只答状态查询。
- 新钩子 `native_aks_storage_volatile`：`/chosen ephemeral-storage` 不是 1 时，`AppleKeyStore::start` 把钥匙包关掉，选择子 8、10、11、31、32 返回 `kIOReturnUnsupported`。无论 `pagani-keybag` 怎么设。
- 第一阶段 Data 在内存里，钥匙包照常工作。

**验证【实测】：** `test_closed_boundary.py`（新增持久存储的三种参数）、`test_volatile_keybag.py` 全过。`KeyStoreProbe.cpp` 依赖内核接口，只有编译和链接，没有主机测试。

**未确认：** 27 的 `libaks` 是否改了选择子 10、11 的语义，要在 dyld cache 里核对，本地没有 24A446。

### 3.8 `BootProgress` 第一阶段

**源码：** `BootProgress.c`、`RamoopsLog.c`、`UfsLog.c`、`GunyahWdog.c`、`Entry.S` 一行没改。

**新增 `WeakDefaults.c`：** `native_legacy_copy_stats` 的隐藏弱定义。L 不带显示时这是唯一找不到提供者的弱引用。链接实测证实未定义的弱导入确实报错（`boot_log_rounds` 就是这样被拦下的），与上游 `build-sheng-source-storage.py:27-34` 的说法一致。

**`Entry.S` 的坑【实测】：** 用 `-target arm64e-apple-ios27.0` 汇编出来是 `USR00`，内核对象是 `KER00`。必须用内核的 `start.o` 记录汇编。

**构建检查在 arm64e 上重跑【实测】：** 四个 C 文件没有 `_memset`、`_memcpy`、`_bzero`、`stack_chk`、`chkstk`，没有 FP 或 SIMD 寄存器。

**测试【实测】：** `console`、`ramoops`、`ufslog`、`crash`、`gunyah-wdog`、`stall`、`stall-threads`、`ufslog-threads`、`panic-entry` 全过。`panic-entry.py` 改了三处才能读 12377：`__mockable` 前缀、`panic_trap_to_debugger` 多的 `panic_initiator`、`panic_with_data` 多的选项参数。它确认 12377 的 `panic`、`panic_with_options`、`panic_with_data` 都走到 `boot_progress_panic`，并在 `iotrace_disable` 之前。

**没做的：** `StallProbe.h` 的 `crash_user_mask` 只处理用户地址，内核返回地址上的 PAC 没有专门处理。第一阶段 `pagani-stall` 不开，不影响。

## 4. `early_boot_progress_init` 在 rebase 之前用到的全局量

**方法【实测】：** 解析 arm64e 目标文件的节、符号、重定位，从五个入口走调用图：`early_boot_progress_init`、`early_boot_progress_map`、`early_boot_progress_mmu_on`、`early_boot_progress_arm_init`（在 `arm_slide_rebase_and_sign_image` 之前调用）、`early_boot_progress_fatal`（异常路径，可能发生在任何时刻）。再到链接好的内核里走 `__TEXT,__thread_starts` 的修复链。

**可达函数 18 个：** `early_boot_progress_init`、`boot_progress_bind`、`boot_progress_stage`、`putc_raw`、`glyph_pixels`、`ramoops_log_panic_putc`、`emit`、`status_band`、`ramoops_log_line`、`emit_text`、`hex_raw`、`ramoops_log_suspend`、`early_boot_progress_map`、`early_boot_progress_mmu_on`、`early_boot_progress_arm_init`、`early_boot_progress_fatal`、`panic_footer`、`panic_open`。

**总体结果：**

| 检查 | 结果 |
| --- | --- |
| 寻址方式 | 105 个符号（95 个全局量加 10 个字符串）全是 `PAGE21`/`PAGEOFF12`，没有 `GOT_LOAD`，没有认证指针 |
| 间接跳转 | 18 个函数里 0 处 |
| 被引用符号里含指针的 | 0 个。`BootProgress.o` 的 `__DATA,__const`（16 个指针）和 `__auth_ptr`（3 个）、`UfsLog.o` 的 `__auth_ptr`（10 个）都不在这条路径上 |
| 链接产物的修复链 | 24 条链，103,613 个位置（31,444 个认证），0 个落在这 95 个符号内 |

**逐个结论：**

| 所在节 | 符号 | 结论 |
| --- | --- | --- |
| `__TEXT,__const`（3） | `font`、`boot_logo`、`crc_nibble` | 只读数据，没有指针，安全 |
| `__TEXT,__cstring`（10） | 字符串常量 | 安全 |
| `__DATA,__data`（1） | `row` | 初值是整数，没有修复，安全 |
| `__DATA,__common`（5） | `boot_progress_panic_text`、`early_boot_args_pa`、`early_boot_phys_base`、`early_boot_stage`、`early_boot_virt_base` | 零初始化，运行时写，安全 |
| `__DATA,__bss`：显示状态（24） | `framebuffer`、`framebuffer_pa`、`framebuffer_wc`、`framebuffer_wc_bytes`、`width`、`height`、`stride`、`column`、`busy`、`display_owned`、`display_value`、`display_stage`、`display_frames`、`display_events`、`display_seen.0`～`.4`、`logo_visible`、`overlay_hidden`、`status_owned`、`status_dirty`、`status_shown` | 同上。`framebuffer` 存的是运行时写入的物理地址，不是修复指针 |
| `__DATA,__bss`：状态行（35） | `cpu_status`、`cpu_row`、`cpu_seq`、`cpu_drawn_seq`、`gpu_count`、`gpu_seq`、`gpu_drawn_seq`、`gpu_status`、`dpu_count`、`dpu_seq`、`dpu_drawn_seq`、`dpu_status`、`fps_mode`、`fps_row`、`fps_owned`、`fps_seq`、`fps_drawn_seq`、`fps_scanned`、`fps_status`、`fps_shown`、`fps_next`、`fps_logged`、`fps_extra`、`fps_extra_logged`、`slow_owned`、`slow_status`、`slow_shown`、`slow_next`、`frame_stage_now`、`peripheral_overlay`、`peripheral_status`、`jitter_limit.0`～`.3` | 同上 |
| `__DATA,__bss`：panic 和控制台（16） | `panic_cpu`、`panic_state`、`panic_line`、`panic_limit`、`panic_clipped`、`panic_released`、`console_lock`、`console_smp`、`stages`、`stage_count`、`root_sequence`、`fault_valid`、`exit_stage`、`poll_count`、`platform_overlay`、`presented` | 同上 |
| `__DATA,__bss`：ramoops（11） | `areas`、`copies`、`copy_count`、`counter_hz`、`drops`、`lock_suspect`、`lock_word`、`log_enabled`、`record`、`sequence`、`start_counter` | 同上。`ramoops_log_suspend` 把 `log_enabled` 清零，早期不往内存区写 |

`Entry.S` 里的 `early_boot_args_pa`、`early_boot_stage`、`early_boot_progress_stack`、`early_boot_progress_vectors`、`bootstrap_pagetables` 都是 `@PAGE`/`@PAGEOFF` 取地址，也安全。`ExceptionVectorsBase` 只在 `vm_ready` 之后用。

**两个外部调用，都不会发生：**

| 调用 | 为什么不发生 |
| --- | --- |
| `boot_progress_bind` → `ml_vtophys` | 条件要求 `framebuffer_wc` 非零，`early_boot_progress_init` 在调用前刚把它清零 |
| `early_boot_progress_init` → `__ubsan_handle_sub_overflow_minimal` | 内核记录带 `-fsanitize=signed-integer-overflow`。只在 `render_boot_logo` 的减法溢出时调用，`geometry` 已限制宽高不超过 8192，溢出不可能 |

**PAC：** 这条路径有 `pacibsp`、`retab`、`autibsp`。这时 `SCTLR_EL1.EnIB` 的值取决于固件，`DarwinEnter` 只清 M、C、I 三位【读码】。为 0 时它们是空操作或普通返回；为 1 时成对的签名和验证用同一把钥匙、同一个 SP，也不会失败【推断】。这要求 CPU 实现 FEAT_PAuth，否则 `retab` 是未定义指令；`调研/pac-oryon.md` 已实测 `paca pacg`。

**结论：入口不用推迟。从驱动侧看，`CONFIG_XNU_EARLY_BOOT_PROGRESS` 可以打开。** 这次的内核就是带 `XNU_EARLY_BOOT_PROGRESS=1` 编译链接的。没验证的是真机，尤其是另一路新写的 `pagani-27-early-progress-t0sz.patch`。

## 5. 编译和链接

**逐文件编译【实测】：** 95 个任务，在 `$S/x12377` 和 `$S/xnu-series` 两份记录上结果相同，只有 3 个失败，都不在本次范围：

| 文件 | 错误 | 处理 |
| --- | --- | --- |
| `NativeVirtioBlock.cpp` | 5 个，与 `NativeUfs` 修掉的相同 | 第 5.1 节决定丢弃 |
| `PaganiWifiNet.cpp` | 4 个，`sys/mbuf.h` | 里程碑之外 |
| `PaganiSensorsHid.cpp` | 编译器后端错误 `Call target is signed with its own value` | 里程碑之外 |

L 范围 18 个源文件全部通过。告警只有 `NativeUfs.cpp` 的 4 个旧隐式转换，和公开 `IOBlockStorageDevice.cpp` 的 1 个弃用提示。`NativeUfs.cpp` 带和不带 `NATIVE_UFS_SOURCE_API` 都编译通过。

**内核链接，分三步【实测】：**

| 步骤 | 内核对象 | 驱动 | 未定义 |
| --- | --- | --- | --- |
| 1 | `x12377` | 无 | 8：firehose 5、`corecrypto_kext_start`、`boot_log_rounds`、`native_source_storage_start` |
| 2 | `x12377` | L 全部 | 27，全部能解释：除上面 7 个，还有设备层补丁才提供的 `boot_progress_stall_*`、`pagani_stall_on`、`pagani_app_execs`，生成文件的 `verified_root_key`，弱导入 `native_legacy_copy_stats`、`native_owner_cache_stage` |
| 3 | `xnu-series`（1072 个目标文件全部编过，`XNU_BOOT_PROGRESS=1`、`XNU_EARLY_BOOT_PROGRESS=1`） | L 全部，加 `WeakDefaults.c` | 1：`native_owner_cache_stage` |

补上桩之后**链接成功**，`$S/l/mach.l`：

- `MH_EXECUTE`，arm64e，`KER00`，`NOUNDEFS PIE`，26,102,048 字节。
- `native_source_storage_start` 是驱动的强定义，覆盖了 `phone-27-source-storage-runtime.patch` 的弱空函数。
- `_native_source_storage_start` 的调用序列和 `SourceStorage.cpp` 一致：`IOStorageFamily`、策略、APFS、三个 `__GLOBAL__sub_I_*`、`native_ufs_source_create`、Data 盘、`native_keystore_probe`。
- 四个延迟注册的对象已确认没有 `__mod_init_func`，`__GLOBAL__sub_I_*` 是外部符号。
- 另一路第 8.2 节列的 37 个未定义里，`boot_progress_*` 24 个、`native_verified_block_*` 3 个、`native_ram_block_*` 2 个已由 L 驱动提供。

**这次链接和真实构建的差别：**

- firehose、corecrypto 是桩，调用即 `brk`。
- 手工 make 只传了部分开关，`XNU_OWNER_*` 等没开，所以内核里调用根验证查询的路径没编进来。符号本身确认都在。
- 没有 XnuPrelink，不是 fileset。

## 6. 需要别的路配合的事

**XNU 补丁：不需要新钩子。**

**构建脚本（还没人写，这是下一步最大的缺口）：**

1. `build/pagani-source-storage/libPaganiSourceStorage.a`，`board.toml` 和 defconfig 已经指向它：

   ```
   按 IOPlatformExpert.cpo.json 记录编译：
     NativeUfs.cpp、SourceStorage.cpp、SourceRootBinding.cpp、SourceRootPolicies.cpp、
     NativeRamBlock.cpp、NativeKeyStoreEvents.cpp、KeyStoreProbe.cpp、
     以及 make-source-base.py 生成的 IOBlockStorageDevice.cpp（-Wno-error）
   按 startup.o.json 记录编译：verified_root.c、xnu_crypto.c、RootAnchor.c（生成）
   宏：-DNATIVE_UFS_SM8750=1 -DUFS_ROOT_PARTITION="unixnu" -DNATIVE_UFS_READ_ONLY=1
       -DNATIVE_UFS_LOG_WRITES=1 -DNATIVE_VERIFIED_BLOCK=1 -DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1
       -DNATIVE_UFS_SOURCE_API=1 -DNATIVE_VERIFIED_POLICY_HANDOFF=1
   另加：-mgeneral-regs-only
   包含：-I storage-abi/include -I kits/PaganiVerifiedRoot/include -I kits -I drivers/NativeUfs -I <xnu>/osfmk
   四个对象做延迟注册：
     llvm-objcopy --remove-section=__DATA,__mod_init_func \
                  --globalize-symbol=__GLOBAL__sub_I_<文件名>.cpp in.o out.late.o
     适用：IOBlockStorageDevice、NativeUfs、NativeRamBlock、NativeKeyStoreEvents
   ```

2. `root-policies.bin`：用自己的 owner 密钥为 24A446 的 OS、SystemOS 签名。`tables_in_userdata` 时只放 544 字节头。

3. `scripts/build-boot-progress.py` 三处：`:57` 改用内核的 `start.o` 记录汇编 `Entry.S`；`:58-60` 的子类型检查按 arm64e 改；对象列表加 `WeakDefaults.c`。

4. 根镜像工具：`userdata.py` 要有 v2 对象，同时带上我们的 `[userdata] partition`，写入要用基址，不能 `fastboot flash unixnu`。

**配置：**

- `CONFIG_XNU_NATIVE_UFS` 必须保持未设置。否则 `KernelConfigTables.cpp:95` 会加个性，类注册后可能再启动一个 `NativeUfs`。现在的 defconfig 是对的。
- `CONFIG_XNU_NATIVE_UFS_ARCHIVE`、`CONFIG_XNU_NATIVE_RAM_BLOCK_ARCHIVE` 这两行：`build-xnu-4k-baseline.sh:718` 在源码存储下不链接后者，前者脚本里没有引用。sheng 的 defconfig 没有这两行，上游只是把同一个归档拷到目录里的固定位置。建议删掉或指向同一份，不要做成两份对象。
- `CONFIG_XNU_EARLY_BOOT_PROGRESS` 从驱动侧看可以打开，开不开由你决定。

**S 里程碑：** `KeyStoreProbe.cpp` 和 `Stage7Probe.cpp` 的 `startKeyState` 定义同样的钩子，不能同时链接。带上 `Stage7Probe` 时去掉 `KeyStoreProbe.cpp`，`SourceStorage.cpp` 的弱空函数自动生效。那时 `Stage7Probe` 要同样放宽并实现 `native_aks_storage_volatile`。

## 7. 未验证项

| 项 | 风险 |
| --- | --- |
| **全部运行时行为** | 没有上机。根卷路径没有回退手段 |
| **IOStorageFamily-337** | **本地没有。** 用的是 `67f9003c` 的头文件和 `IOBlockStorageDevice.cpp`。`SourceStorage.cpp` 在运行时比较类大小，不一致就 panic。但大小相同、虚表变了的情况查不出来。`NativeUfsBlock`、`NativeRamBlock` 的虚表全靠这份头文件 |
| 真实 fileset | XnuPrelink 没跑，DarwinBoot 的 fileset 只测过合成夹具，以及真实内核的 `MH_EXECUTE` 形式 |
| 真实策略 | 没有 24A446 镜像，`.vroot` 和只含头部策略的路径只测过主机，真实 `vr_open` 流程要等签名 |
| 注册次序 | `PaganiArmCpu` 没有主机测试 |
| `KeyStoreProbe.cpp` | 只有编译和链接 |
| 12377 的 `kmem_alloc` 加 `mach_vm_protect` | 只编译通过。`KMA_KOBJECT | KMA_DATA` 在 `vm_kern.c` 的断言里合法【读码】，真实行为要上机 |
| BootProgress 早期路径 | 全局量已证明安全，但 `pacibsp`、`retab` 在 MMU 关闭时的实际行为、T0SZ=17 的 V=P 表都没上过机 |
| firehose、corecrypto | 需要重编 arm64e，依赖要下载（`调研/xnu-12377.md` 第 8.3 节） |
| `pagani-stall` | arm64e 下内核返回地址未处理，第一阶段不开 |
| `tests/Sm8750.cpp` | 基线就失败，与 `Sm8750Platform/GccUfs.h` 的常量有关，没有查原因 |

## 8. 来源记录

- 每个目录有 `ORIGIN.md`（主线提交 `14f50fce9778a8bb19ec6b0b7dc199863526c996`、未提交改动、上游代码来源）、`ORIGIN.sha256`（每个文件的哈希和修改时间）、`ORIGIN.diff`（相对拷贝基线）。主线工作树拷贝时有未提交改动，所以提交号不足以复现，以哈希为准。
- sheng 的代码保留 SPDX 行，加了来源和版权说明。`NativeUfs`、`NativeKeyStoreEvents` 带 `LICENSE.UniXNU-sheng` 全文。

**验证脚本都在草稿目录，重启可能丢失：** `$S/cc27.py`（逐文件编译，`SERIES=1` 用补丁系列）、`$S/port/link-l.py`（打包并链接）、`$S/early/macho.py`、`$S/early/chain.py`（第 4 节）、`$S/port/final-tests.sh`（全部测试）。要保留的话告诉我放哪里，我这轮只写了指定目录。
