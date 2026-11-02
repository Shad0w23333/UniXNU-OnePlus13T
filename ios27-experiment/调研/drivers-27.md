# 13T 内核驱动迁到 iPadOS 27（24A446，arm64e）的逐项调研

时间：2026-11-01。只读调研，没有碰手机，没有改任何驱动。

证据等级：**【实测】** 本次在电脑上跑出来的；**【读码】** 读了文件，给出 文件:行号；**【推断】** 由前两者推出，没有直接验证。

路径缩写：

| 缩写 | 路径 |
| --- | --- |
| `D/` | `UniXNU-Pagani-Drivers/drivers/` |
| `K/` | `UniXNU-Pagani-Drivers/kits/` |
| `TS/` | `UniXNU/scripts/`（我们本地的构建脚本） |
| `U/` | `参考资料/上游-20261031/UniXNU-UniXNU/` |
| `SH/` | `参考资料/上游-20261031/UniXNU-UniXNU-sheng/` |
| `GF/` | `参考资料/上游-20261031/UniXNU-GopFramebuffer/` |
| `AC/` | `参考资料/上游-20261031/UniXNU-ArmCpu/` |
| `XP/` | `参考资料/上游-20261031/UniXNU-XnuPrelink/` |
| `X12/` | xnu-12377 打完上游补丁的树（裸仓库 HEAD `9dee719`） |

`gh:` 开头的文件是用 `gh api` 读的上游文本，本地快照里是空子模块：`gh:ShengHidInput/NativeHidInput.cpp`、`gh:ShengUfs/NativeUfs.cpp`、`gh:DarwinBoot@3c75734`、`gh:RamBlock@4be2d7e`、`gh:VerifiedRoot@974f043`。

---

## 0. 结论

1. **绝大多数驱动重编就过。** 86 个源文件用 arm64e 的 12377 编译记录实测，只有 4 个文件报错；其中 2 个已在草稿目录验证了修法（第 2 节）。
2. **真正要动手的是 ABI 绑定，集中在 5 处：** `NativeMobileDisplay`、`PaganiInput`、`PaganiSensors`、`PaganiPanel`、`NativeUfs` 的存储基类。除 `PaganiSensors` 外，sheng 都给出了做法，数据文件针对同一份 kernelcache，可直接用。
3. **三个驱动可以直接丢掉：** `NativeAES`、`NativeVirtioBlock`、`ShengPlatformPower`。sheng 的 27 配方里都没有它们，27 上用 `vmm-present=1` 代替 PMGR。
4. **最大风险不在驱动代码：** 上游没有任何记录说明 27 不带 GPU 能显示 SpringBoard。文档里只记到“三次交换均完成，屏幕显示苹果标志”，桌面未验证（`U/docs/zh/porting/display.md:55-56`）。里程碑 S 可能必须带 Adreno830 和 Metal。
5. **日志通道要自己带：** sheng 的 27 配方不带 BootProgress，日志是 `BootLog.c` 写到 UFS，再启动一个 Linux 小镜像读出。13T 现有的日志和 panic 显示都在 BootProgress 里，它依赖的几个 XNU 补丁在 12377 上大半打不上。

---

## 1. 方法

- **导出头文件并试编 XNU。** 在草稿目录用 `make build ARCH_STRING_FOR_CURRENT_MACHINE_CONFIG=arm64e -miphoneos-version-min=27.0` 编译 `X12/`。1072 个目标文件全部生成，只有最后链接失败（Xcode 里没有 `ld-classic`）。由此拿到真实的 arm64e 编译记录，目标是 `arm64e-apple-ios27.0.0`，带 `XNU_BUILT_WITH_BTI`【实测】。
- **用同一份记录编译全部驱动。** 复用各构建脚本的 include 和宏，`-Wall -Wextra`，不开 `-Werror`。
- **符号核对。** 收集全部内核目标文件和 `all-alias.exp` 的 27840 个符号，与驱动目标文件的未定义符号比对。这不是真链接。
- **自声明原型单独核对。** 编译只能检查走头文件的接口，驱动自己 `extern` 的逐个对照 `X12/`。

局限：

- 本机没有 24A446 的 kernelcache。下文所有 24A446 地址、槽位、判别值都来自 sheng 的 JSON，我没有独立验证。
- 编译时用的是 21H461 的生成头文件和 17 时代的 IOStorageFamily 头文件，所以“编译通过”不代表 ABI 正确，只代表内核接口没问题。
- `Adreno830` 和 Ath12k 协议栈没有编译。`Adreno830` 只做了符号核对。
- 任务书里的分支 `base` 在裸仓库里不存在，实际是分离的 HEAD `9dee719`【实测】。

---

## 2. 编译与符号核对结果

### 2.1 编译【实测】

| 文件 | 结果 | 原因 | 修法 |
| --- | --- | --- | --- |
| `D/NativeUfs/NativeUfs.cpp` | 5 个错误 | `kma_flags_t`、`KMA_KOBJECT`、`KMA_DATA`（:1357），`vm_map_protect`（:1364），`kmem_free`（:1531）不再可见 | 加 `<vm/vm_kern_xnu.h>`、`<mach/mach_vm.h>`，改用 `mach_vm_protect`。**已验证通过** |
| `D/NativeVirtioBlock/NativeVirtioBlock.cpp` | 5 个错误 | 同上（:402、:406、:569） | 不修，27 上丢弃 |
| `D/PaganiWifi/PaganiWifiNet.cpp` | 4 个错误 | `<sys/mbuf.h>`（:38）在 C++ 下报 `m_add_hdr_crumb` 无匹配 | 改 `<sys/kpi_mbuf.h>`，sheng 也是这么做的（`SH/package/WifiNet.cpp:31`）。**已验证通过** |
| `D/PaganiSensors/PaganiSensorsHid.cpp` | 编译器后端致命错误 | `Call target is signed with its own value`，由 `bound<F>()`（:70）把数据符号转成函数指针再调用引起。最小样例能复现 | 见第 6.3 节 |

其余 82 个文件零错误。警告只有 `NativeUfs` 4 条和 `BootProgress` 4 个文件各 1 条。

两个不算问题的失败：`BootProgress.c` 缺 `kern/early_boot_progress.h`，这个头文件来自我们自己的 XNU 补丁，补上后通过；`PaganiTorchSysctl.c` 要用 `bsd_init` 的编译记录，换了就过（`D/PaganiTorch/scripts/build-pagani-torch.py:33`）。

`mach_vm_protect` 的参数顺序与旧 `vm_map_protect` 不同，是 `(map, start, size, set_max, prot)`。`VM_PROT_READ` 和 `TRUE` 都等于 1，写反了也能跑，移植时仍应按 `gh:ShengUfs/NativeUfs.cpp:1773` 写对。

### 2.2 符号核对【实测】

所有驱动的未解析符号只有以下几类，**没有一个真实内核接口消失**：

| 类别 | 符号 | 处理 |
| --- | --- | --- |
| 我们自己的 XNU 补丁 | `boot_progress_stall_*`（15 个）、`pagani_stall_on`、`pagani_app_execs`、`early_boot_progress_vectors_done` | 补丁移植时带上 |
| 宏展开的旧堆名 | `KHEAP_DATA_BUFFERS` | 重编后自动变成新名字；但 `D/PaganiSensors/scripts/build-pagani-sensors.py:108`、`D/PaganiSpeaker/scripts/build-pagani-speaker.py:63` 的导入白名单要改 |
| kext 类 | `IOMobileFramebufferLegacy`（184）、`IOHIDDevice`（52）、`IOBlockStorageDevice`（11）、`IOAESAccelerator`（11）、`ApplePMGR`（5）、`AppleARMBacklight`（3）、`IOAcceleratorES`（3） | 就是要绑定的部分 |
| 驱动之间 | `Sm8750*`、`native_ufs_*`、`__GLOBAL__sub_I_*` | 正常 |
| 编译器运行库 | `___chkstk_darwin`（`NativeRamBlock`） | 17 上同样存在，由链接时解决【推断】 |

### 2.3 内核接口抽查

| 接口 | 用到的驱动 | 12377 上的情况 | 证据 |
| --- | --- | --- | --- |
| `processor_start` | `PaganiArmCpu` | **语义变了。** 别名到 `processor_start_from_kext`，什么都不做，返回 `KERN_FAILURE` | 【实测】`all-alias.exp:84`；【读码】`X12/config/Unsupported.exports:163`、`X12/osfmk/kern/processor.c:1268-1274` |
| `ml_processor_register` | `PaganiArmCpu` | **语义变了。** 非 AppleARMSMP 配置下内部直接调 `processor_boot` | 【读码】`X12/osfmk/arm64/machine_routines.c:1761-1770` |
| `vm_map_protect` | `NativeUfs` | 移到 `vm_map_internal.h`，参数顺序改成 `set_max` 在前，类型改成 `_ut` | 【读码】`X12/osfmk/vm/vm_map_internal.h:384` |
| `kmem_alloc`、`kmem_free` | `NativeUfs` | 带标志的版本移到 `vm_kern_xnu.h` | 【实测】编译错误 |
| `IOMallocData`、`kalloc_data` | 多个 | 改用 `KHEAP_DATA_PRIVATE`；`IOMallocAligned` 改用 `KHEAP_DATA_SHARED`；新增 `IOMallocDataShareable` | 【读码】`X12/iokit/IOKit/IOLib.h:219,299-300`、`X12/osfmk/kern/kalloc.h:136,153` |
| `IOUserClient`（含 `externalMethod`、`clientMemoryForType`） | 7 个驱动 | 头文件与 10063 **零差异** | 【实测】`diff` |
| `IOPlatformExpert`、`IOCPU`、`IOPMPowerSource`、`IOCommandGate`、`IOTimerEventSource`、`IOWorkLoop`、`IOInterruptEventSource` | 多个 | 头文件零差异 | 【实测】`diff` |
| `IOMemoryDescriptor` | 多个 | 只有新增；`IOMDDMAWalkSegmentState` 从 128 字节变 64，我们没用 | 【实测】`diff` |
| `pmap_set_cache_attributes`、`pmap_find_pa`、`pmap_valid_address`、`phystokv`、`get_task_pmap` | `PaganiAdsp`、`PaganiWifi`、`PaganiAudio` | 都在，声明兼容 | 【读码】`X12/osfmk/vm/pmap.h:399`、`X12/osfmk/arm/pmap/pmap.h:522,555,576` |
| `ml_io_map_wcomb`、`ml_vtophys`、`ml_static_vtop`、`ml_static_ptovirt`、`ml_install_interrupt_handler` | `BootProgress`、`NativeRamBlock`、`PaganiArmCpu` | 都在 | 【读码】`X12/osfmk/arm/machine_routines.h` |
| `vnode_lookup`、`vnode_open`、`vn_rdwr`、`vnode_getattr` | `PaganiWifi`、`PaganiSensors` | 编译通过 | 【实测】 |
| `mbuf_*`、`ifnet_*`、`ether_*`、`bpf_attach`、`kev_post_msg` | `PaganiWifi` | 换头文件后编译通过 | 【实测】 |
| `thread_call_*`、`timer_call_*`、`sysctl_register_oid`、`OSKext::loadKextWithIdentifier` | 多个 | 编译通过，头文件无差异 | 【实测】 |
| 驱动自声明的 `rtclock_intr`、`processor_info`、`cpu_to_processor`、`hz_tick_interval`、`nextpidversion`、`read_random`、`proc_selfname` | `PaganiArmCpu`、`NativeKeyStoreEvents` | 都在，原型一致 | 【读码】 |

### 2.4 PAC 风险点

**不用改的：** 普通 C 函数指针、`thread_call`、`IOTimerEventSource`、`IOWorkLoop::runAction`、`IOInterruptEventSource` 回调、驱动里的 C 回调表。编译器自动签名，全部驱动没有用 `OSMemberFunctionCast`【实测 grep】。`D/PaganiArmCpu/ArmCpu.cpp:1074` 的 `reinterpret_cast<IOInterruptHandler>` 是函数指针之间互转，判别值都是 0，安全【推断，依据 `gh:ShengHidInput/NativeHidInput.cpp:211` 的 0 判别值用法】。

**运行时一定出错的，全部列在这里【读码】：**

| 位置 | 问题 |
| --- | --- |
| `D/PaganiInput/HidEvents.h:65` | 裸读虚表再调用：虚表指针带 DA 签名，槽位带 IA 签名 |
| `D/PaganiInput/HidEvents.h:68`、`D/PaganiInput/PaganiInput.cpp:297` | 拿已签名的虚表指针和裸地址比较，永远不等 |
| `D/PaganiInput/HidEvents.h:100` | 读裸地址表再调用，调用未签名指针 |
| `D/PaganiInput/HidEvents.h:103-108` | `memcpy` 虚表再写回：槽位签名绑定地址，换地址后全部失效 |
| `D/PaganiSensors/PaganiSensorsHid.cpp:233,251,256,348,350,376-381` | 同上三种 |
| `D/PaganiSensors/PaganiSensorsHid.cpp:70` | 数据符号转函数指针，编译都过不了 |
| `D/NativeMobileDisplay/NativeLegacyDisplay.cpp:591-593` | 裸读虚表第 354 槽 |
| `D/PaganiAudio/hal/PaganiAudioPlugIn.c:150-151,164-166,795-796,1007,1072` | 用户态：把 `dlsym` 结果掩掉高位再调用。arm64e 下必须保留签名 |
| `D/PaganiAudio/hal/PaganiAudioPlugIn.c:690-758` | 改写符号指针。上游明确在 arm64e 下关掉（`SH/hal/ShengAudioPlugIn.c:526-528`） |

---

## 3. 总表

绑定分四类，后面反复用到：

- **A 类**：只继承 XNU 自带的类或纯 C。重编即可。
- **B 类**：继承 kext 类，但有公开源码（`IOBlockStorageDevice`、`IOHIDDevice`）。改用公开头文件，判别值由方法名自动算对。
- **C 类**：继承私有 kext 类。需要 JSON 记录槽位、判别值和布局。
- **D 类**：直接调原版函数或改别人的虚表。需要跳板和重签。

| 驱动 | 类 | 21H461 的绑定 | 27 要做的事 | sheng 对应物 | L | S | 量 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `BootProgress` | A | 0 个地址，靠 XNU 补丁钩子 | 重写构建入口，移植钩子补丁 | `package/BootLog.c`，不带 BootProgress | 带 | 带 | M |
| `NativeAES` | C | 22 个绑定，0x120 字节 | **丢弃** | `SoftwareAES`，27 配方已去掉 | 不带 | 不带 | 无 |
| `NativeKeyStoreEvents` | A | 0 | 放宽易失存储判断 | `AppleKeyStoreState` + `KeyStoreProbe` | 带 | 带 | S |
| `NativeMobileDisplay` | C+D | 193 个绑定，358 槽，34 个脚本字面量 | 接 24A446 JSON，合并上游分支 | `GopFramebuffer` + `build-gop-legacy-display-27.py` | 不带 | 带 | M |
| `NativeRamBlock` | B | 11 个绑定 | 改走源码存储基类 | `RamBlock` | 带 | 带 | S |
| `NativeUfs` | B | 11 个绑定，0 个字面量，190 槽 | 改走 `SourceStorage`，补 5 个编译错误和清单 v2 | `ShengUfs` + `Source*.cpp` | 带 | 带 | M |
| `NativeVirtioBlock` | B | 11 个绑定 | **丢弃**，根绑定换成 `SourceRootBinding.cpp` | 27 配方已去掉 | 不带 | 不带 | 无 |
| `PaganiAdsp` | A | 0 | 重编 | `AdspProbe` | 不带 | 不带 | S |
| `PaganiArmCpu` | A | 0，但用私有符号 | 改注册次序、`bootInitialized`、起核路径 | `ArmCpu` | 带 | 带 | M |
| `PaganiArmDTPlatform` | A | 0 | 重编，构建脚本放开检查 | `ArmDTPlatform` | 带 | 带 | S |
| `PaganiAudio` | A | 0（内核部分） | 内核重编；HAL 改 arm64e | `ShengAudioService` + `AudioProbe` | 不带 | 不带 | 内核 S，HAL M |
| `PaganiBattery` | A | 0 | 重编 | `ShengBattery` + `BatteryProbe` | 不带 | 不带 | S |
| `PaganiDarwinBoot` | 无 | 0 | 移植 fileset 解析和 `.vroot` | `DarwinBoot@3c75734` | 带 | 带 | S |
| `PaganiDiag` | 用户态 | 0 | 不进配方，按需重编 | 无 | 不带 | 不带 | S |
| `PaganiHaptics` | A | 0 | 重编；叠加层路径另算 | 无 | 不带 | 不带 | S |
| `PaganiInput` | B+D | 54 个绑定，207+220+37 槽 | 换公开 `IOHIDDevice.h`，重写 `HidEvents.h` | `ShengHidInput` + `HidInputProbe` | 不带 | 建议带 | M |
| `PaganiPanel` | C | 3 个绑定，169 槽，0x370 字节 | 改继承 `IOService`，走 `getMetaClass` 伪装 | `AppleARMBacklight.cpp` | 不带 | 不带 | S |
| `PaganiPersistentData` | 未编入 | 无 | 等 Data 方案定了再说 | 无 | 不带 | 不带 | 未估 |
| `PaganiSensors` | B+D | 51+8 个绑定，10 个字面量 | 重写钩子，重新定位 8 个原版函数 | 无 | 不带 | 不带 | L |
| `PaganiSpeaker` | A | 0 | 重编 | `AudioProbe` 一部分 | 不带 | 不带 | S |
| `PaganiTorch` | A | 0（内核部分） | 内核重编；插件改 arm64e | 无 | 不带 | 不带 | 内核 S，插件 M |
| `PaganiWifi` | A | 0 | 换一个头文件，加 `/arm-io/wlan` | `WifiProbe` + `WifiNet` | 不带 | 不带 | S |
| `ShengPlatformPower` | C | 8 个绑定，340 槽，26 个字面量 | **丢弃**，ADT 设 `vmm-present=1` | 27 配方已去掉 | 不带 | 不带 | 无 |
| `Stage7Probe` | A | 0 | 对接 27 的显示和存储流程 | `LegacyDisplayProbe` | 不带 | 带 | S |
| `PaganiVerifiedRoot`（kit） | 纯 C | 0 | 加 `vr_open_header` | `VerifiedRoot@974f043` | 带 | 带 | S |
| `NativePrelink`（kit） | 工具 | `slide=-0x1000000` | 换 `XnuPrelink`（PR 3 之后） | `XnuPrelink` | 带 | 带 | S |
| `Adreno830`（仅绑定） | C | 3 个绑定 | 用 `ioaccelerator-base-abi-24A446.json`，换跳板 | `build-adreno740-accelerator.py` | 不带 | 视情况 | S |

绑定数量证据：`TS/../out-gpu/.../tree/build/*/{bindings,build-report,abi-audit}.json`【实测】；字面量是 `0xfffffff...` 在脚本里的去重计数【实测】。

---

## 4. 共用机制

### 4.1 sheng 的 27 绑定方法【读码】

- **虚表判别值。** arm64e 下每个虚表槽的签名用“方法修饰名的 SipHash”作判别值。原版调用方按原版名字算出的判别值去验签。私有类的方法名不可知，sheng 的办法是从 kernelcache 的链式修复读出每个槽的判别值，再暴力找一个能算出相同判别值的假名字（`SH/scripts/legacy-display-24A446.json` 的 `names`，211 项），用它声明虚函数。构建时校验每个槽（`SH/scripts/build-gop-legacy-display-27.py:46-69,89-93,222-224`）。
- **不能搜 16 位判别值。** PAC 只有约 24 位，65536 个判别值里常有第二个值产生相同签名，搜到错的就触摸失效。必须读 kernelcache 的真实值（`gh:ShengHidInput/NativeHidInput.cpp:157-163`）。
- **调用原版函数走跳板。** 绝对符号没有修复记录，指针既不签名也不搬移。每个原版函数生成一段 `bti c; b <name>.original`，`.original` 是集合内固定地址的 `N_ABS` 符号。数据符号走 GOT（`SH/scripts/build-gop-legacy-display-27.py:243-258`）。
- **XnuPrelink PR 3 后不再减 `native-slide`。** 构建脚本写固定地址，由 `fileset_layout.relocate_original_stubs` 在布局后重算跳转，并核验目标落在保留 kext 的可执行段内（`6dafef9` 对该脚本的改动，PR 3 的 `fileset_layout.py`）。旧做法在 sheng 上跳到了 `0xfffffff00dbb4c74`，比正确地址多 `0x392c000`。

### 4.2 11 月 1 日上游更新对 13T 的影响【读码，gh api】

| 更新 | 13T 要做的事 |
| --- | --- |
| `build-fileset-release-reference.py --lto` | 对照内核必须用 LTO。非 LTO 时 corecrypto 的 `kprintf` 匹配丢失，运行到就 panic。**快照里的脚本写死 sheng 的路径**（`U/scripts/build-fileset-release-reference.py:16`，PR 155 已重写），要确认新版能接受我们的树 |
| `BTI c; B` 跳板 | 见 4.1。第 5、6 节所有绑定都走这条 |
| 13 个 vnode 操作描述符 | 由 `XnuPrelink` 的 `fixtures/fileset-24A446-symbols.json` 提供，取 PR 3 之后的版本即可。缺了 APFS 在 `vfs_fsadd` 崩 |
| `payload.runtime` | 准备信任模块、GPU 和音频 addon、Data seed。`audio_tool` 由板级配置指定，但 `build-boot-runtime.py` 写死了 `audio/ShengAudio.driver`，13T 要改 |
| `/arm-io/wlan` 节点 | `dts/oneplus13t.dts` 要加 `unixnu,adt-name = "wlan"` 的节点，否则 `com.apple.wifid` 不启动，设置里 Wi-Fi 页是空的 |
| `prelink-regression.json` | 记录 2 个未解析数据引用和 24 个陷阱引用。这是 sheng 现状，我们的内核不同，基线要重新生成 |

### 4.3 构建脚本里写死的 arm64 假设【读码】

这些检查在 arm64e 下全部失败，改起来机械但数量不少。

| 问题 | 位置 |
| --- | --- |
| 子类型必须为 0 | `TS/build-arm-platform.py:41`、`TS/build-native-ufs.py:41`、`TS/build-native-aes.py:18`、`TS/build-stage7-driver.py:59`、`TS/build-boot-progress.py:59` |
| 部署版本必须是 iOS 17 | `TS/build-stage7-driver.py:66` |
| `-target arm64-apple-ios17.0` | `TS/build-stage7-display.py:196`、`TS/build-pagani-peripherals.py:210`、`TS/build-boot-progress.py:57`、`TS/build-pagani-verified-root-probe.py:146`、`D/PaganiSensors/scripts/build-pagani-sensors.py:400` |
| 虚表重定位类型必须是 0（arm64e 是 11） | `TS/build-stage7-display.py:110`、`TS/build-pagani-peripherals.py:42`、`D/PaganiSensors/scripts/build-pagani-sensors.py:318` |
| `OriginalImage` 写死 21H461 的 SHA-256 | `TS/original_stage7.py:17` |
| BootProgress 要求 `XNU_NATIVE_UFS=1` | `U/scripts/build-xnu-4k-baseline.sh:84`，27 用 `XNU_SOURCE_STORAGE` |

上游的通用替代是 `U/scripts/kernel_target.py`。

---

## 5. L 和 S 必需的驱动

### 5.1 `NativeUfs`（含 `NativeRamBlock`、`NativeVirtioBlock`）

**21H461 绑定【读码】**

- `TS/build-ufs-storage-bindings.py` 没有硬编码地址：编译公开 `IOBlockStorageDevice.cpp` 取 190 个槽（:114），在 `IOStorageFamily` 的 `__DATA_CONST` 里按指针比对找虚表（:133-142），按指令模式找构造函数（:145-157）。绑定 11 个符号（`bindings.json`）【实测】。
- `NativeRamBlock`、`NativeVirtioBlock` 复用这份绑定（`TS/build-native-ufs.py:49-50`）。

**27 怎么变**

上游放弃了绑定，改为**把公开的 `IOBlockStorageDevice.cpp` 编进内核**，因此没有地址、没有槽位【读码】：

1. `U/scripts/build-sheng-source-storage.py:152-165` 把元类宏改写，新元类名字是 `UniXNUBlockStorageDevice`，父类是原版 kext 的 `IOBlockStorageDevice`。C++ 类名不变，判别值由编译器算对。
2. `SH/package/SourceStorage.cpp` 在 `bsd_init` 里（`SH/patches/xnu-12377/source-storage-runtime.patch`）依次加载 `IOStorageFamily`、校验类大小、认证策略、加载 APFS、注册类、启动 UFS 和 Data 盘。
3. `SH/package/SourceRootPolicies.cpp` 从 `/chosen/memory-map/RootPolicies` 读 `.vroot` 边车，复制到内核内存、设只读、用内置公钥验签。
4. `SH/package/SourceRootBinding.cpp` 提供 `native_verified_block_root_authorized` 等三个查询，取代 `NativeVirtioBlock` 里的同名实现。
5. 哈希表不进启动镜像。`CONFIG_XNU_SOURCE_STORAGE_TABLES="userdata"` 时边车只带 544 字节签名头，表作为根镜像里的数据对象 `<window>.vroot`，`NativeUfs` 自己读（`gh:ShengUfs/NativeUfs.cpp:1671-1695`）。

**我们要改的**

| 项 | 内容 |
| --- | --- |
| 编译错误 | 第 2.1 节，已验证 |
| `NATIVE_UFS_SOURCE_API` 三处条件 | `boot_progress_poll` 改强引用，`native_ufs_source_create`（`gh:ShengUfs/NativeUfs.cpp:49,1597,1893`） |
| 清单 v2 | `RootLayout.h` 差 55 行：数据对象、`gRootObject`、`readPolicyTable`。清单不在分区第 0 块的改动见 `调研/storage-27.md` |
| `VerifiedBlockPolicy.h` | 加 `NATIVE_VERIFIED_POLICY_HANDOFF` 分支，差 5 行【实测 diff】 |
| 三个 `Source*.cpp` | 带署名移植，共 340 行 |
| 改名 | `SourceStorage.cpp` 里的 `sheng-log`、`boot_log_*` 接到我们的 `UfsLog.c` |
| 策略 | 用自己的 owner 密钥为 24A446 的 OS 和 SystemOS 签名（`SH/scripts/policies/` 是 sheng 的） |

**不要合并的：** `gh:ShengUfs/NativeUfs.cpp` 里 `native_ufs_dpu_*`、`native_ufs_extra_*` 这批 SM8550 的显示和 Wi-Fi SMMU 代码。我们的 `SmmuTakeover.h`（差 129 行）、`RootQueue.h`（差 91 行）、`RootCommands.h`（差 148 行）带 SM8750 的改动，保留我们的。

**`NativeRamBlock`：** 编译通过【实测】。与 `gh:RamBlock@4be2d7e` 只差 29 行，建议取上游的 `Open::Kept` 状态，保留我们的 `declareSolidState`。必须带：Data 挂载失败时 `load_init_program` 直接停（`6dafef9` 的 `porting.toml` 证据）。

**`NativeVirtioBlock`：** 17 上只为根绑定查询保留，13T 没有 Virtio 硬件（`UniXNU-Pagani-Drivers/README.md`）。丢弃。

**移植许可：** `Source*.cpp` 是 BSD-2-Clause-Patent，可带署名移植【读码】。

**工作量 M：** 代码量小，但根卷路径没有回退手段，要上机确认 APFS 能挂上 OS 和 SystemCryptex。sheng 在这条路上踩过 `vfs_fsadd` 崩溃和 Data 缺失两个坑。

### 5.2 `PaganiArmCpu`

**21H461 绑定：** A 类，继承 `IOCPU`，没有 kext 绑定。私有符号 `rtclock_intr`、`LowResetVectorBase`、`ResetHandlerData`、`const_boot_args`、`hz_tick_interval`、`nextpidversion` 都还在【实测】。

**三处语义变化【读码】**

| 变化 | 我们的代码 | 上游的改法 |
| --- | --- | --- |
| `ml_processor_register` 内部调 `processor_boot`，启动核在里面走 `initCPU` 并打开 GIC 和定时器 | :946 先注册，:957-967 才建中断控制器、注册中断，:968 再 `initCPU(true)`。在 12377 上会在处理函数装好之前开中断，并且 `initCPU` 跑两次 | `AC/ArmCpu.cpp:455-462`：先建控制器和注册中断，再注册处理器；用 `bootInitialized` 防重入（:462、:587、:594） |
| `processor_start` 变空操作 | :1044 调了也没事，返回失败不检查。次级核实际由 `ml_processor_register` 里的 `processor_boot` 拉起，且不等待（`X12/osfmk/kern/processor.c:1349-1353`） | `AC/ArmCpu.cpp:535` 保留调用 |
| `info.cluster_type` | :943、:996 全部用 `ml_get_boot_cluster_type()`，配合 17 的单 pset 策略 | `AC/ArmCpu.cpp:132-139` 的 `clusterType()` 读 ADT，配 `zzyf-sheng-amp.patch` 跑 Edge |

**次序带来的新问题：** `startCPU` 现在在 `ml_processor_register` 内执行，而 :1001-1003 的 `setCPUNumber`、`setCPUState(kIOCPUStateUninitalized)` 在它之后，可能覆盖新核写的 `kIOCPUStateRunning`。上游写法相同，影响未知【推断】。

**Edge 决定：** `armcpu-edge` 默认关，不调 `ml_cpu_init_completed`，Edge 矩阵全不允许迁移（`AC/ArmCpu.cpp:538-542`）。第一阶段建议不带 `zzyf-sheng-amp.patch`，沿用单 pset，先把八核跑起来。

**SM8750 特有：** 复位向量里 MPIDR 掩码。SM8550 的 12377 补丁用 `0xFFFF`（`上游/UniXNU-UniXNU-sm8550-common/patches/xnu-12377/native-smp-secondary.patch`），13T 大核簇在 Aff2，要 `0xFFFFFF`（`UniXNU-sm8750-common/patches/xnu/sm8750-smp.patch:54`）。这个补丁在 12377 上 3/6 块失败。

**其余功能：** `CpuFreqGovernor.h`、`PsciIdle.h`、`ScmiPerf.h` 是硬件逻辑，编译通过，不用改。`pagani_app_execs` 来自我们的 `phone-app-exec-count.patch`，跟补丁走。

**工作量 M：** 代码改动不大，依据是上游差异只有约 40 行。但起核失败只能靠日志判断，要上机验证八核。

### 5.3 `PaganiDarwinBoot`

**21H461：** 无内核绑定。`DarwinBoot.c:126-127` 要求 `filetype==2`、子类型严格等于 0，所以 arm64e 和 fileset 都被拒绝。

**要移植的，全部来自 `gh:DarwinBoot@3c75734`【读码】：**

| 项 | 内容 |
| --- | --- |
| 子类型 | 接受 `& 0x00ffffff` 等于 0 或 2 |
| fileset | `ParseKernelHeader` 拆出，`ParseKernel` 遍历 `LC_FILESET_ENTRY`（`0x80000035`），校验 `com.apple.kernel` 的段被集合段覆盖 |
| `.vroot` | 读 `<kernel>.vroot`，校验 `VROOTB01`，写入 `/chosen/memory-map/RootPolicies` |
| `.gpuaddon` | 同样的机制。我们目前把插件放在 Data seed，第一阶段可以不带 |

保留我们的 `dram-base`、`dram-size` 占位符逻辑，上游没有。

**工作量 S：** 约 130 行直接移植，有现成的 `Test/Validation.c`。

### 5.4 `PaganiVerifiedRoot`

与 `gh:VerifiedRoot@974f043` 只差一个功能：`check_header` 拆出，新增 `vr_open_header`，`.c` 差约 25 行，`.h` 差 4 行【实测 diff】。工具 `manifest.py`、`verify_image.py` 没有变化要求。编译通过【实测】。

**工作量 S。**

### 5.5 `PaganiArmDTPlatform`

A 类，继承 `IODTPlatformExpert`。头文件零差异，编译通过【实测】。比上游多 252 行差异，都是 `PlatformHalt.h`、`PlatformRtc.h` 这些 13T 功能，与 OS 无关。

**工作量 S：** 只改构建检查。

### 5.6 `BootProgress`

**绑定：** 0 个 kext 绑定，生产代码 0 个硬编码地址【实测 grep】。但依赖我们的 XNU 补丁和内核全局量：`bootstrap_pagetables`、`start_first_cpu`、`ExceptionVectorsBase`、`msgbufp`、`gPhysBase`、`cpu_datap`、`cpu_immediate_xcall`，这些都还在【实测】。

**27 要变的**

- `Entry.S` 必须按 arm64e 汇编，否则 arm64 对象链接不进 arm64e 内核（`TS/build-boot-progress.py:57`）【推断】。
- 它依赖的补丁在 12377 上大半打不上：`zzzz-early-boot-progress` 5/17、`zzzzz-root-panic-progress` 2/16、`zzzzzz-vm-bootstrap-progress` 4/19、`zzzzzzz-early-framebuffer-page-tables` 1/1 块失败（`build-userspace/a830-inputs/upstream/ipados27-parity.md:157-158`）。
- `U/scripts/build-xnu-4k-baseline.sh:84` 要求 `XNU_NATIVE_UFS=1`，要放开。
- **早期路径的新约束。** 27 的内核带链式修复，`arm_slide_rebase_and_sign_image` 在 `arm_init` 里才跑（`X12/osfmk/arm/arm_init.c:366`）。`_early_boot_progress_entry` 在这之前执行，C 代码里的全局指针表此时还是修复链，不能用【推断】。需要逐个检查 `early_boot_progress_init` 用到的全局量，或者把入口推迟到 `arm_slide_rebase_and_sign_image` 之后。
- `CrashTrace.h`、`StallProbe.h` 处理用户态返回地址时已经掩高位（`CrashTrace.h:11`），内核返回地址要确认同样去掉签名。`boot_progress_stall_*` 第一阶段可以整个关掉。

**为什么要带：** 上游这几天的修复都来自读 UFS 上的 `msgbuf`（`SH/scripts/read-sheng-log.py`，`6dafef9` 的证据条目）。13T 的对应通道是 `RamoopsLog.c` 和 `UfsLog.c`。第一阶段可以只带这两个加 panic 路径，先不带早期控制台，这样 `zzzz-early-boot-progress` 和 `zzzzzzz-early-framebuffer-page-tables` 可以推迟，代价是虚拟内存建立之前的失败看不到任何东西。

**不用 sheng 的 `BootLog.c`：** 我们的 `UfsLog.c`、`RamoopsLog.c` 更完整，而且已在真机验证。

**工作量 M：** 依据是四个补丁要手工改到新的 `start.s`、`sleh.c`、`arm_init.c`，都是启动最早期的代码。

### 5.7 `NativeKeyStoreEvents`

A 类，继承 `IOUserClient`、`IOService`。编译通过【实测】。sheng 的构建脚本写明它与 iPadOS 17 完全相同（`SH/scripts/build-gop-legacy-display-27.py:368`），27 上的选择子表与 17 一样，已到 SpringBoard。

**要改一处：** `D/Stage7Probe/Stage7Probe.cpp` 的 `startKeyState` 要求 `ephemeral-storage==1`，否则不注册。上游已放宽：持久 Data 也用（`SH/package/KeyStoreProbe.cpp:91-92`）。

**未确认：** 我们多了选择子 10、11 的软件钥匙包（sheng 没有），27 的 `libaks` 是否改了这两个选择子的语义，要在 dyld cache 里核对。

**工作量 S。**

### 5.8 `NativeMobileDisplay`

**21H461 绑定【实测，`build-report.json`】**

- `IOMobileFramebufferLegacy`：358 槽，自有 24 槽，实例 `0x18a0` 字节。
- 193 个绑定：184 个类成员，9 个 C 符号（7 个 IOSurface getter、`vsync_notify`、`set_refresh_properties`）。
- `TS/build-stage7-display.py` 有 34 个去重地址字面量，其中 18 处做原版字节校验，全部 21H461 专用。

**24A446 的差异【读码，`SH/scripts/legacy-display-24A446.json`】**

| 项 | 21H461 | 24A446 |
| --- | --- | --- |
| 虚表 | 358 槽 | 379 槽 |
| 实例大小 | `0x18a0` | `0x1dd8` |
| 纯虚槽 | 9 个 | 9 个：214、216、257、258、324、325、358、359、366 |
| `isMainDisplay` | 184 | 186 |
| `getDotPitch` | 188 | 190 |
| `supportedFrameInfo` | 213 | 216 |
| `getDisplayDimensions` | 249 | 261 |
| `startHardware` | 288 | 305 |
| `kickSwapQueue` | 290 | 307 |
| `completeCurrentSwap` | 295 | 312 |
| `validateSwap` | 296 | 313 |
| `getRefreshAttributes` | 301 | 319 |
| `collectCompletionReports` | 337 | 358 |
| 我们叫 `isTimingEnabled` 的纯虚槽 | 345 | 366，上游改名 `cancelSwapsAfterTimeout`，语义是超时后取消，不是查询 |
| 状态设置函数 | 354 | 375 `applyStateRequest` |
| `create_default_fb_surface` | 无此记录 | 316，返回 void，由我们提供默认 surface |

字段偏移：`current` 5472→6720，`head` 5480→6728，`tail` 5488→6736，`vsync` 客户 5592→6840，宽 5808→7152，高 5812→7156。请求结构：`surfaces` 2624→2944，`masks` 792→984，`options` 804→996，`dims` 816→1008，`next` 2960→3384，`prev` 2968→3392。

**IOSurface：** 24A446 没有 allocation getter，改用描述符长度；新增 `create` 和 `allocate` 两个原版函数（`SH/scripts/build-gop-legacy-display-27.py:316-358`）。

**要改的【读码】**

1. **合并源码。** 我们的 `NativeLegacyDisplay.cpp` 1078 行，上游 `GF/` 869 行。上游已把请求偏移参数化成 `kRequest*`、`kLegacy*`，我们还是字面量（:199、:718、:965-1001，共 10 行）。上游没有我们的 TE 锁定和翻页（`NativeDisplayTiming.h`、`NativeScanoutFlip.h`），这两个要保留。
2. **修 :591-593** 的裸虚表调用，改用 `applyStateRequest`。
3. **加三段条件代码：** `NATIVE_LEGACY_DEFAULT_SURFACE`、`NATIVE_LEGACY_CANCEL_SWAPS`、`LEAF` 宏（`GF/NativeLegacyDisplay.cpp:739-751,854-864`）。
4. **构建脚本。** `SH/scripts/build-gop-legacy-display-27.py` 520 行，写死 sheng 的目录和板级驱动清单（:383-505）。JSON 可以原样用，脚本要拆：保留 ABI 和跳板部分，板级对象换成我们的。注意取 `6dafef9` 之后的版本。
5. **`Stage7Probe` 的 `native_legacy_display_probe`** 不用变，钩子在 `X12/bsd/kern/bsd_init.c:557-558,1337-1338`。

**`NativeGOPPresenter.cpp`、`DpuProbe.cpp`：** A 类，编译通过【实测】。只在 GPU 交接时有用，软件合成阶段可以不启动。`NativeMobileDisplay.cpp` 本身不在构建列表里（`TS/build-stage7-display.py:232-239`）。

**工作量 M：** 依据是 ABI 数据全部现成，代码差异是机械合并。不确定性在第 8 节第 1 条。

### 5.9 `PaganiInput`

**21H461 绑定【读码】**

- `IOHIDDevice`：207 槽，`0xd0` 字节，52 个绑定。`TS/build-pagani-peripherals.py:89-115` 生成头文件，大部分槽是 `opaqueN`。
- 另外两张表：`IOHIDEventDriver` 220 槽、`IOHIDEvent` 37 槽（:143-163）。
- 该脚本有 30 个去重地址字面量（含 `PaganiPanel`）。

**27 怎么变**

1. **基类换公开头文件。** sheng 用 IOHIDFamily-2238.120.5 的 `IOHIDDevice.h`，所以 `opaqueN` 全消失，判别值由真实方法名算出（`SH/scripts/build-gop-legacy-display-27.py:381-386`）。这份源码本地没有，实施时要按 `U/profiles/os/ipados-27.0.1-24A446/profile.toml` 的钉住提交取。
2. **`handleReport` 第二个参数** 从 `0` 改成 `kIOHIDReportTypeInput`（`D/PaganiInput/PaganiInput.cpp:316`）。
3. **绑定。** 51 个 `IOHIDDevice` 导入按槽或判别值从 `SH/scripts/hid-device-24A446.json` 解出，走跳板（`SH/scripts/build-gop-legacy-display-27.py:423-461`）。
4. **重写 `HidEvents.h`。** 直接移植 `gh:ShengHidInput/NativeHidInput.cpp:93-226`：`vtableOf` 用 DA 键和 `0xcda1` 验签，`call` 按判别值换签，`kSlotDiversity[220]` 逐槽校验再重签。槽号 18、23、24、25、198 不变，判别值已列出。
5. **`bindTouchEvents` 要放宽。** 27 上的事件驱动类是 `AppleUserHIDEventService`（DriverKit 的内核侧），不再是 `IOHIDEventDriver`。`D/PaganiInput/PaganiInput.cpp:297-302` 的虚表比较要换成类名判断（`gh:ShengHidInput/NativeHidInput.cpp:194-196`）。
6. **用途对。** 该服务启动后自己发布 (1,6)，要反复补回 (0x0d,4)，否则 backboardd 丢弃数字化事件（`SH/package/HidInputProbe.cpp:119-139`）。
7. **`installPersonality`（`HidEvents.h:21-44`）** 指定 `IOClass=IOHIDEventDriver`。27 上这条个性是否还会选中，sheng 没有这一步，只靠 `HIDDefaultBehavior`。**未确认**，第一次上机要看。

**不用改的：** `S3910.c`、`GeniSpi.c`、`Buttons.h`、`Mapping.h`、`InputScript.h` 全是硬件和协议，编译通过【实测】。

**工作量 M：** 依据是有完整参考，但多了第 5 到 7 条的运行时行为，只能上机确认。

### 5.10 `Stage7Probe`

A 类，编译通过【实测】，本身是从 sheng 的 `LegacyDisplayProbe`、`KeyStoreProbe` 改来的。

- `registerClass`、`startPeripherals`、`startPresenter` 不变。
- `startPeripherals` 里 `PaganiPanel` 要改注册方式（6.2 节）。
- 改 `startKeyState`（5.7 节）。

**工作量 S。**

---

## 6. 里程碑之外的驱动

### 6.1 重编即可的硬件驱动

sheng 的结论：板级驱动的类都派生自 XNU 自己的类，用内核自身的编译标志就得到内核的 ABI（`SH/scripts/build-gop-legacy-display-27.py:472-474`）。我们的 `PaganiAdsp`、`PaganiBattery`、`PaganiHaptics`、`PaganiSpeaker`、`PaganiTorch`（内核）、`PaganiAudio`（内核）、`PaganiWifi` 都是这种，编译通过【实测，`PaganiWifiNet.cpp` 除外】。

依赖要注意：`PaganiAdsp`、`PaganiBattery`、`PaganiHaptics`、`PaganiSensors`、`PaganiSpeaker`、`PaganiWifi`、`PaganiTorch` 都追加进 `libAdreno830.a`（`UniXNU-Pagani_SM8750-gpu/board.toml`），`PaganiAdsp`、`PaganiWifi` 还用 `Sm8750*` 平台层。L 阶段不带 GPU 时，这个归档关系要解开。

### 6.2 `PaganiPanel`

**21H461：** 继承 `AppleARMBacklight`，169 槽，`0x370` 字节，3 个绑定（`TS/build-pagani-peripherals.py:131-138,190-192`）。24A446 没有这个类的 ABI 记录。

**不要走重签。** sheng 的做法更简单【读码，`SH/package/AppleARMBacklight.cpp:268-298,367-382`】：

- 类继承 `IOService`，类名 `ShengBacklight`。
- `getMetaClass()` 手写，打开标志后返回 `AppleARMBacklight` 的元类。
- `IOObjectConformsTo` 和沙盒都走它，CoreBrightness 和 backboardd 的策略都能通过。
- arm64e 下不复制虚表。

把 `PaganiPanel` 改成这样，所有 ABI 绑定消失。**`OSDynamicCast(PaganiPanel, ...)` 会失效**，事件源回调要用静态转换。

**工作量 S。** 依据是改动集中在类声明和 `OSDefineMetaClass` 宏。

### 6.3 `PaganiSensors`

**21H461：** 10 个地址字面量（`D/PaganiSensors/scripts/build-pagani-sensors.py:63-94`）：6 个 `IOHIDEvent` 构造函数、`setProperties`、`copyEvent`、驱动表、`dispatchEvent`。

**27 上的问题【读码，部分推断】**

- 这些函数在 24A446 里没有记录，kernelcache 去了符号，要自己逆向定位。
- 编译都过不了。`bound<F>()` 要改成函数声明加跳板。
- 表复制和钩子要按 `PaganiInput` 的方式重写，而且要覆盖 `AppleUserHIDEventService`。
- 依赖 DriverKit 路径下 `setProperties`、`copyEvent` 是否还经过这个对象，**未确认**。
- 方向服务的 `ashd` 用户客户端和 `make-sensor-pack.py` 是 21H461 的用户态契约，要重新核对。

**工作量 L。** 依据是没有 sheng 参考，且要逆向。

### 6.4 `PaganiWifi`

- `D/PaganiWifi/PaganiWifiNet.cpp:38` 改头文件。
- DTS 加 `wlan` 节点（4.2 节）。
- 伪类 `IO80211Controller`、`IONetworkStack` 在 sheng 的 27 上原样使用（`SH/package/WifiNet.cpp:388-433`）。
- Ath12k 协议栈含 Linux 派生代码（GPL-2.0、BSD-3-Clause-Clear，`D/PaganiWifi/Ath12kWifi/ORIGIN.md`），只按原许可证使用，不复制到其他驱动。

**工作量 S**（代码）。Wi-Fi 上层在 27 的可用性上游还没验证完（`6dafef9` 的 `limits`）。

### 6.5 `PaganiAudio`

内核部分重编即可。**HAL 工作量 M：**

- 目标改 `arm64e-apple-ios27.0`（`D/PaganiAudio/scripts/build-pagani-audio-hal.py:74,81`）。
- 第 2.4 节列的 `dlsym` 掩码全部改成 `ptrauth_strip` 读、原指针调（`SH/hal/ShengAudioPlugIn.c:81-85`）。
- `rebind` 在 arm64e 下关掉。
- `PaganiAudioMxLayout.h` 是 21H461 的 audiomxd 布局，要重新逆向。这部分工作量未计入。

### 6.6 三个丢弃的驱动

| 驱动 | 依据 |
| --- | --- |
| `NativeAES` | 22 个绑定（`TS/data/native-aes-original-bindings.S`），类继承私有 `IOAESAccelerator`。`SH/board.toml:20` 的 27 归档没有 `SoftwareAES`，17 和 26 有（:140、:160）。27 已到 SpringBoard，说明不需要【推断】 |
| `NativeVirtioBlock` | 见 5.1 |
| `ShengPlatformPower` | 340 槽、8 个绑定，`UniXNU-Pagani_SM8750-gpu/scripts/audit-original-pmgr.py` 19 个地址。`SH/board.toml:101-102` 写明：没有 Apple PMGR 和 SEP，所以报告为虚拟平台，AMFI 就不等 `ApplePMGRNub`。27 的启动参数里也没有 `sheng-pmgr`。注意上游试过去掉 `vmm-present` 导致 backboardd 连崩（提交 `cc62bf4`） |

`CONFIG_XNU_SHENG_PMGR`、`CONFIG_XNU_NATIVE_AES`、`CONFIG_XNU_NATIVE_VIRTIO_BLOCK` 要从 13T 的 27 defconfig 去掉。

### 6.7 `Adreno830`（只看绑定）

- `AGXAccelerator` 继承 `IOAcceleratorES`：136 字节、168 项、无操作覆盖。3 个绑定来自 `TS/data/ioaccelerator-base-abi.json`，由 `Adreno830/scripts/build-adreno830.py:213-243` 处理。源码里 0 个硬编码地址【实测 grep】。
- 其余是 `IOService`、`IOUserClient` 子类，A 类。`mac_iokit_check_open`、`gIOUCFilterCallbacks` 都在【实测】。
- 24A446 数据现成：`U/scripts/data/ioaccelerator-base-abi-24A446.json`。构造函数和析构函数走跳板，元类走 GOT，`U/scripts/build-adreno740-accelerator.py` 的 `FILESET` 分支照抄（PR 155 版本）。
- 沙盒：需要 `zzw-native-adreno-sandbox-grant` 和 bring-up 补丁里的 `native_kobj_map_grant`（`SH/patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch:1305-1333`）。

---

## 7. 里程碑

### L：27 上 launchd 起来

| 必须 | 理由 |
| --- | --- |
| `PaganiDarwinBoot` | 加载 fileset |
| `PaganiArmDTPlatform`、`PaganiArmCpu` | 平台和 CPU |
| `NativeUfs` + 三个 `Source*.cpp` + `PaganiVerifiedRoot` | 根卷 |
| `NativeRamBlock` | Data 挂载失败时 `load_init_program` 直接停 |
| `BootProgress`（至少 `RamoopsLog`、`UfsLog`、panic 路径） | 13T 现有的日志通道 |
| `XnuPrelink`、`Sm8750Platform`、corecrypto、libpthread 归档 | 构建前提 |
| `NativeKeyStoreEvents`（建议） | 量很小。launchd 本身不依赖，但依赖 AppleKeyStore 的守护进程没有它可能循环崩【推断】 |

**可以不带：** 其余全部。`NativeKeyStoreEvents` 目前由 `Stage7Probe` 启动，不带显示时要把启动点挪出来，或者走 `SH/package/KeyStoreProbe.cpp` 的 `native_keystore_probe` 路径。

### S：27 上 SpringBoard 显示

L 的集合，加：

| 必须 | 理由 |
| --- | --- |
| `NativeMobileDisplay`（`NativeLegacyDisplay`） | 显示后端 |
| `Stage7Probe` | 串起显示、HID、IOSurface 加载 |
| `IOHIDFamily`、`IOHIDEventDriver`、`IOSurface`、`IOMobileGraphicsFamily` kext 在 fileset 根里 | 已在上游配置的 31 个根中 |

**建议带：** `PaganiInput`。不带也能到锁屏（17 上第 50 版就是有按键没触摸到的锁屏），但进不了桌面。

**可以不带：** `PaganiPanel`（亮度停在引导器设的值）、音频、Wi-Fi、电池、传感器、触感、手电、`PaganiAdsp`、`NativeGOPPresenter`、`DpuProbe`。

**前提未确认：** 见第 8 节第 1 条。如果 27 没有软件合成路径，`Adreno830`、Turnip、Metal 插件都要进 S。

---

## 8. 风险和未确认事项

| # | 事项 | 为什么重要 | 怎么确认 |
| --- | --- | --- | --- |
| 1 | **27 不带 GPU 能否显示 SpringBoard** | 决定 S 的范围。上游文档只记录到苹果标志（`U/docs/zh/porting/display.md:55-56`），`6dafef9` 记录的 SpringBoard 启动镜像带 GPU 参数 | L 之后先测。或者问上游 |
| 2 | 早期 C 代码在 rebase 前运行 | 第一次启动就可能黑屏，且没有日志 | 检查 `early_boot_progress_init`，或推迟入口 |
| 3 | `XnuPrelink` 匹配对我们内核的稳定性 | 我们有 sheng 没有的补丁，LTO 对照内核布局不同。PR 3 记录过重编后丢掉两个依赖顺序的匹配。缺失函数运行到才 panic | 比较 `missing`、`stubbed` 报告与 sheng 基线 |
| 4 | 24A446 数据来自 sheng，未独立验证 | 同一份 kernelcache（`38e6d9fa...`），大概率可用 | 下载后用 `check-legacy27-binds.py` 核对 |
| 5 | `AppleARMBacklight`、`IOHIDEvent` 构造函数在 24A446 的数据 | 没有现成记录 | `PaganiPanel` 绕开；`PaganiSensors` 要逆向 |
| 6 | 我们的 HID 个性在 DriverKit 路径下的行为 | 触摸可能绑不上 | 第一次带 `PaganiInput` 上机看 |
| 7 | 软件钥匙包选择子在 27 的语义 | 影响钥匙串 | 查 24A446 的 `libaks` |
| 8 | 启动镜像体积 | sheng 注释提到 192 MiB 上限（`U/scripts/build-sheng-source-storage.py:105-106`），13T 的上限没测过。我们现在的 `kernel.macho` 127 MB 含哈希表，27 上表移出后会变小【推断】 | 打包时看 |
| 9 | IOStorageFamily-337、IOHIDFamily-2238 本地没有 | 本次编译用的是 17 时代头文件 | 实施时按钉住提交取 |

---

## 9. 工作量

只算内核驱动的编码和调通，不含 XNU 补丁移植、根卷制作和用户态。全部是【推断】。

| 阶段 | 内容 | 估计 |
| --- | --- | --- |
| 公共 | 构建脚本 arm64e 化（4.3 节）、`XnuPrelink` 接入、defconfig | 2 到 3 天 |
| L | `DarwinBoot` 1 天，`ArmCpu` 2 到 3 天，`NativeUfs` + `Source*` 3 到 4 天，`RamBlock`、`VerifiedRoot`、`KeyStore`、`ArmDTPlatform` 2 天，`BootProgress` 3 天 | 11 到 13 天编码，上机调通另算 1 周 |
| S | `NativeMobileDisplay` 4 天，`Stage7Probe` 1 天，`PaganiInput` 3 到 4 天 | 8 到 9 天编码，上机调通另算 3 到 5 天 |
| 恢复外设 | `PaganiPanel` 1 天，`PaganiWifi` 1 天，重编类 2 天，`PaganiAudio` HAL 3 天起，`PaganiTorch` 插件 2 天，`PaganiSensors` 1 周以上 | 2 到 3 周 |

**合计：** 到 L 约 3 周，到 S 约 5 周，驱动层恢复到现有功能面约 7 到 8 周，都按一人全职算。比 `ipados27-parity.md:196` 的“乐观 3 到 5 周”保守，因为这里单独算了 BootProgress 和上机时间。上游两个人从 10-26 到 10-28 就起机，但他们已有成熟的 UFS 日志和读取链路，而且 sheng 的 27 配方比我们的驱动少。

**最值得先做的两件事：**

1. 用这次的方法在 CI 里固定“12377 arm64e 编译全部驱动”这一步，几分钟就能发现接口回归。
2. L 跑通后立刻测第 8 节第 1 条，再决定 S 的范围。
