# 一加 13T 的 iPadOS 27 实验路线

时间：2026-11-01。这条路线和 17.7.11 主线并行，不替代主线，也没有动主线的任何文件。

证据等级：**【实测】** 本次跑出来的；**【读码】** 读文件确认；**【推断】** 没有直接验证。工作量全部是推断，按一人全职算。

上游版本：本地快照 `407db1a`，线上 main 已到 `dec111b`（2026-11-01），差异已用 `gh api` 读过。

---

## 1. 结论

1. **能做，硬件不卡。** 八核都满足 PAC，Gunyah 公开源码放行密钥和 PAC 指令，同系列 Oryon（SM8850）上第三方已经跑过 arm64e 到 SpringBoard。详见 `调研/pac-oryon.md`。
2. **27 能和 17 共存。** 不动分区表，不碰 17 的任何字节。27 根卷放在 `unixnu` 分区内 10 GiB 处。详见 `调研/storage-27.md`。
3. **卡点不在 CPU，在三件事：** 没有 Darwin 26 的 XNU 源码，kext 按 12377 接口推断配对，配错就是运行时 panic；27 不带 GPU 能否出画面没人证实过，所以 GPU 不是可选项，要按必须做来算；27 比 17 重，内存和排队的压力搬代码解决不了，流畅度大概率回退。
4. **两个新发现会改变计划：**
   - **虚拟机路线。** 官方文档收录的 DroidVM 能在手机开着安卓时，把 27 跑进 Gunyah 虚拟机，GPU 经 KGSL。它的应用支持表列了 SM8750，13T 的条件都对得上。这条路不用重启手机，不写分区。没有在 13T 上验证过。
   - **iPhone 根卷。** `iPhone16,1` 有 24A446，和上游固定的版本号完全相同。13T 本来就要当 iPhone，用它做根卷，主线那批身份和界面修复可能大半不用重做。
5. **主机构建 N0 已完成。** D0、D1 下载后，真实的 fileset 内核和启动镜像都做出来了（`images/boot-27-N1.img`），kext 配对质量和 sheng 相同：24 个桩、2 个未解析，地址完全一致。详见 `调研/n0-build.md`。
6. **下一步 N1 只用临时启动，不用 D2，不写手机存储。** 这个镜像用占位 Data 种子，走到等根设备就算通过，5 分钟后自动回安卓。现在停在这里等你同意上机。

---

## 2. 这一轮做到了哪

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 114 个 XNU 补丁在原版 `xnu-12377.121.6` 上零模糊打通 | 完成 | 【实测】我亲自重跑 `scripts/check-xnu-12377-series.sh`，退出码 0，结果树 `c47e1334` |
| **真实 fileset 内核** | `kernel.macho` 95.9 MB，上游全部真实依赖，配对与 sheng 同水平 | 【实测】`调研/n0-build.md` |
| **N1 启动镜像** | `images/boot-27-N1.img`，36.6 MB，校验 `013af34f` | 【实测】静态检查全过，没上过机 |
| 全部驱动试编 arm64e | 86 个文件 4 个报错，要走的都修了 | 【实测】 |
| L 驱动 27 副本 | 8 个目录全部移植，arm64e 编译通过，主机测试通过 | 【实测】`调研/drivers-27-port.md`。运行时全部没验证 |
| 根基址 | 基址 0 与主线差分 20 万个清单、200 万组判定一致；基址出界或无清单时拒绝，不回退到 17 | 【实测】我亲自重跑，退出码 0 |
| `Adreno830` 试编 arm64e | 41 个文件全过，源码零改动 | 【实测】只到目标文件，没链接 |
| 带 `turnip-a830-*` 的 Turnip 试编 arm64e | 700 步通过，`Adreno (TM) 830` 在 | 【实测】只编译没运行 |
| 翻译器 `720ef1c` 处理 27.1 QuartzCore 库 | 出 50 条管线 | 【实测】模拟器库，只是下限 |
| 27 设备层 | `device/`、`soc/`，10 个组件，114 个补丁，31 个 fileset 根 | 【实测】走完了真实构建 |
| 早期路径 | `early_boot_progress_init` 走到的 95 个全局量全是 PC 相对寻址，不受 rebase 影响，早期控制台已打开 | 【实测】调用图和 103,613 个修复位置 |
| PAC 探针 | EL0、EL1 各一个，都没运行 | 【实测】编译和反汇编通过 |

---

## 3. 两条路线

| | **原生路线 N** | **虚拟机路线 V** |
| --- | --- | --- |
| 做法 | 我们的驱动直接跑 27，走 `fastboot boot` | 手机开着安卓，27 跑在 crosvm 加 Gunyah 里 |
| 要重启手机 | 每次测试一次，到 Metal 桌面估计 25 到 65 次 | 正常不用。虚拟机崩可能带崩手机，作者遇到过看门狗复位 |
| 写手机存储 | 根卷写 `unixnu` 分区，约 15 GB | 装应用、放镜像到 `/data`，加载内核模块，不写分区 |
| 驱动 | 全部要迁到 arm64e | 用 DroidVM 现成的 virtio 驱动，不用我们的 |
| GPU | 自己的 `Adreno830` | Turnip 在客户机，经 virtio 到安卓 KGSL |
| 能验证 | 全部 | 24A446 根卷、种子、Metal 栈、iPhone 根卷、27 在这台手机上的观感 |
| 验证不了 | | 我们的驱动、我们的 XNU 补丁、原生显示和 120 Hz |
| 最终形态 | 真正的原生 27 | 安卓里的一个窗口 |
| 到第一次看见 27 | 约 2 周加调试，出画面要 Metal，约 5 到 7 周 | 约几天，把握低于 N 的代码部分，见第 5 节 |

**建议：先走 V 看一眼，再决定投不投 N。** 理由：N 的代价是几十次重启，而 27 大概率比 17 更卡。V 能在不重启、不写分区的情况下回答“27 在这台手机上值不值”，还能用 iPhone 根卷提前验证第 8 节的问题。两条路共用 24A446 的所有输入，V 不浪费 N 的工作。

---

## 4. 原生路线的里程碑

| 里程碑 | 通过判据 | 上机 | 写手机 | 工作量 |
| --- | --- | --- | --- | --- |
| **N0 主机构建** | 真实 fileset，出 `boot.img` | 否 | 否 | **已完成** |
| **N1 内核入口** | 临时启动，八核上线，PAC 密钥设置过，走到 `bsd_init`，根卷等待或 panic 都算通过 | 3 到 8 次 | 否 | 2 到 3 天，不要 D2 |
| **N2 launchd** | 挂上 OS 和 SystemCryptex，真 Data 种子在内存，`launchd` 跑起来，日志可读 | 5 到 15 次 | **写 `unixnu` 10 GiB 处** | 约 1 周，要 D2 |
| **N3 显示探针** | 限两轮。显示后端工作，出 Setup 或锁屏算成立；不出就直接进 N4 | 最多 2 轮 | 否 | 约 1 周，大部分 N4 也要 |
| **N4 Metal 桌面** | render server 经 A830 合成，到锁屏再到桌面 | 15 到 40 次 | 否 | 2 到 4 周 |
| **N5 iPhone 根卷** | 手机界面、键盘、灵动岛 | 待定 | 写根窗 B | 待定 |
| **N6 外设恢复** | 触摸、按键、亮度、Wi-Fi、音频、传感器 | 多 | 持久 Data 要另外同意 | 2 到 3 周起 |

合计：N0 之后到 N2 约 2 周，到 N4 约 5 到 7 周，含大量上机。这只到“GPU 合成的桌面出来”，不含 N5、N6。

### N1 怎么做

我这边不碰手机。你同意后由主线会话跑，命令带序列号，不会打到另一台设备：

```bash
python3 /Users/xiaochoumao/unixnu-13t/工具/gpu-test-loop/gpu-loop.py --serial SERIAL_13T --image /Users/xiaochoumao/unixnu-13t/ios27-experiment/images/boot-27-N1.img --label ios27-n1 --runs /Users/xiaochoumao/unixnu-13t/ios27-experiment/runs --yes
```

不加 `--yes` 只打印计划。镜像旁边放了 `boot-27-N1.payload.json`，工具能据此检查启动参数。证据写进 `ios27-experiment/runs/`，不进主线目录。

- **只临时启动，不刷写。** 内核是 `NATIVE_UFS_READ_ONLY`。编进去的写路径只有分区末尾 32 MiB 的日志窗口（`NATIVE_UFS_LOG_WRITES=1`），要 `sheng-log=1` 才启动，这个镜像的命令行没有。固件的 UEFI 变量是内存模拟。
- **要人在场。** `pagani-reset-after=300` 要跑到 `boot_progress_start` 才开始计时，这之前卡住要手动长按电源加音量下，而这次的新代码有一部分就在这之前。日志在 DRAM 里，长按后一般还读得回。
- **回安卓后可能认不到 USB。** XNU 复位后有时要插拔数据线，这不等于卡死，用 `cat /proc/uptime` 反推真实开机时刻。
- **通过：** 屏幕或日志显示过了 `arm_init`，八核上线，走到存储启动，停在等根设备。这时 PAC 已在这台手机的 EL1 上跑通。
- **第一次就要确认的：** ABL 是否接受这个镜像；`pagani-27-early-progress-t0sz` 这段新代码，S3 之后黑屏先查它；kext 配对错误通常表现为 `unixnu_fileset_unmapped` panic。

N3 的处理是这次调查改出来的。17 上“先 CPU 后 GPU”的顺序在 27 上没有优势：QuartzCore 的软件回退还在【实测，27.1 反编译】，但两次 24A446 无 GPU 启动都没出桌面【读码】，而 GPU 路径有两个独立实证。

### N0 到 N2 要做什么

| 层 | 内容 | 状态 | 来源 |
| --- | --- | --- | --- |
| 框架 | 上游 `dec111b` 加我们的社区组件和构建脚本，在 `UniXNU-27/`，差异在 `framework/` | **完成** | `random-seed` 取上游的 256 字节，我们的 64 字节会在控制台出现前 panic |
| XNU | arch 64、soc 3、os 4、device 43 | **完成** | 带 `phone-27-*` 的是 sheng 移植，BSD-2-Clause-Patent，已带署名 |
| fileset | `XnuPrelink` `b1888c1` 加两条审定记录，`--lto` 对照内核 | **完成** | 配不上的函数运行到才 panic |
| 构建脚本 | L 集合用到的已改完。S 集合的 `build-stage7-*`、`build-pagani-peripherals.py` 等还是 arm64 | 部分 | `调研/drivers-27.md` 第 4.3 节 |
| 链接器 | 上游补丁要 `ld-classic`，Xcode 27 系列没有。本机 Xcode 26.6 有 | **已验证** | `DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer` |
| 驱动 | 见下表 | L 集合已移植 | `drivers/`、`kits/` |
| `libPaganiSourceStorage.a` | `scripts/build-pagani-source-storage.py` | **完成** | 11 个对象 |
| 根卷 | 清单 v2，暂用 sheng 的公开签名头和公钥 | 驱动侧完成，镜像要 D2 | `调研/storage-27.md`。`userdata.py` 的 `--object` 和根基址写入在 N2 用 |
| Data | N1 用占位种子。N2 从 IPSW 的 `private/var` 生成，写 `CAEnableMedina=false` 等偏好，并重编内核 | N2 要 OS 镜像 | 上游 `make-data-seed.py`，DroidVM `seed-data-prefs.py` |

### 驱动

完整表格见 `调研/drivers-27.md`。A 类重编即可，B 类改用公开头文件，C、D 类要 24A446 的 ABI 数据，sheng 都有现成 JSON，针对同一份 kernelcache。

| 驱动 | N2 | N3 | 量 | 怎么做 | sheng 对应物，可移植 |
| --- | --- | --- | --- | --- | --- |
| `PaganiDarwinBoot` | 要 | 要 | S | fileset、`.vroot` | `DarwinBoot@3c75734` |
| `PaganiArmCpu` | 要 | 要 | M | 注册次序、`bootInitialized`；`ml_processor_register` 语义变了 | `ArmCpu`，保留 Aff2 和单 pset |
| `PaganiArmDTPlatform` | 要 | 要 | S | 只改构建检查 | |
| `NativeUfs` + `Source*.cpp` | 要 | 要 | M | 5 个编译错误、清单 v2、根基址 | `ShengUfs` + `SourceStorage` 等 340 行。SM8550 的显示、Wi-Fi SMMU 代码不要 |
| `NativeRamBlock` | 要 | 要 | S | 取 `Open::Kept` | `RamBlock@4be2d7e` |
| `PaganiVerifiedRoot` | 要 | 要 | S | `vr_open_header` | `VerifiedRoot@974f043` |
| `BootProgress` | 要 | 要 | M | 13T 的日志都在它里面，sheng 的配方没有它 | 不用 sheng 的 `BootLog.c` |
| `NativeKeyStoreEvents` | 建议 | 要 | S | 放宽易失存储判断 | `AppleKeyStoreState` |
| `NativeMobileDisplay` | | 要 | M | 358 槽变 379 槽，接 24A446 JSON | `GopFramebuffer`、`build-gop-legacy-display-27.py` |
| `Stage7Probe` | | 要 | S | | `LegacyDisplayProbe` |
| `PaganiInput` | | 建议 | M | 换公开 `IOHIDDevice.h`，重写 `HidEvents.h`。27 走 `AppleUserHIDEventService` | `ShengHidInput` |
| `PaganiPanel` | | | S | 改继承 `IOService`，`getMetaClass` 伪装，不重签 | `AppleARMBacklight.cpp` |
| `PaganiAdsp`、`PaganiBattery`、`PaganiHaptics`、`PaganiSpeaker`、`PaganiTorch`、`PaganiAudio`（内核） | | | S | 重编 | |
| `PaganiWifi` | | | S | 换头文件，加 `/arm-io/wlan`（芯片是 peach，不是 sheng 的 WCN7850） | `WifiNet` |
| `PaganiAudio` HAL、`PaganiTorch` 插件 | | | M | arm64e，`dlsym` 结果带签名 | `ShengAudioPlugIn.c` |
| `PaganiSensors` | | | L | 编不过，8 个原版函数要重新逆向 | 没有参考 |
| `NativeAES`、`NativeVirtioBlock`、`ShengPlatformPower` | | | 无 | **丢弃。** 27 用 `vmm-present=1` 代替 PMGR | |

没有一个内核接口消失【实测】。语义变化集中在 `processor_start`、`ml_processor_register`。

### N4 要做什么

完整表格见 `调研/gpu-27.md`。

| 层 | 内容 | 量 | 来源 |
| --- | --- | --- | --- |
| `Adreno830` 内核 | `build-adreno830.py` arm64e 化，24A446 基类 ABI，跳板 | S | `build-adreno740-accelerator.py`，取 `474cfee` 之后的版本。A830 专有部分与 OS 无关 |
| Turnip | 加 `ios27` 目标，`turnip-a830-*` 和 `turnip-darwin-kmd.patch` 原样保留 | S | `dsb sy`、Busy 处理都留着，与 OS 无关 |
| 翻译器 | 留在 `720ef1c`，不升 `624bab39` | S | 上游 10-28 在 27 上渲染时还是 `720ef1c`【读码】 |
| **AdrenoMetal 前端** | 合到 `d6ca655`，再摘约 7 项 27 专有提交 | **L** | 与主线阶段 2 是同一件事，**先在 17 上做完，这里能省一大半** |
| Rust 桥 | `rust-pac-rewrite.py`、`RustPacBoundary.c` 原样拿，要 `-Z build-std` | M | 要 `rust-src`。默认的 stable 1.99.1 已带，1.98.0 和 nightly 没带 |
| 上机 | render server 崩溃排查到锁屏。QuartzCore 建管线失败就 abort，每条都是一次崩溃 | **L** | |
| 着色器包 | 重做。不是启动前提，可后置 | M | |

---

## 5. 虚拟机路线的里程碑

全部没有在 13T 上验证过。依据是 `Droid-VM/UniXNU-droidvm`、`Droid-VM/DroidVM` 的文档和提交。

| 里程碑 | 内容 | 判据 | 工作量 |
| --- | --- | --- | --- |
| **V0** | 手机回安卓时只读查：`adb -s SERIAL_13T shell su -c 'ls -l /dev/gunyah'` | 节点存在 | 几分钟 |
| **V1** | 装 DroidVM，加载 GKI 6.6 模块，跑一个现成的 Linux 客户机 | 虚拟机能起，手机不重启 | 半天 |
| **V2** | 按 `ipados27/` 构建镜像，iPad17,3 根，不带 GPU | 到 launchd、SpringBoard | 2 到 4 天 |
| **V3** | GPU 经 KGSL，A830 | 锁屏 | 约 1 周 |
| **V4** | 换 iPhone16,1 根卷 | 手机界面的取舍有结论 | 几天 |

对得上的条件【实测 + 读码】：SoC 在支持表（`gunyah.yaml` 列了 SM8750、SM8750AB、SM8750AC），有 root（KernelSU），`CONFIG_GUNYAH=y`，GKI 6.6 模块存在。作者的工具提到 OPLUS 的频率上限，很可能在同厂手机上跑过【推断】。

没核实的【推断】：

- `/dev/gunyah` 是否存在。手机离线，没查到。
- `--droidvm-gpu`、`--virtio-mmio-*` 不在 crosvm 默认分支的代码索引里，也不在预编译包清单里。很可能要自己编 crosvm 分叉。
- GPU 记录来自 A750 和 A840，A830 没人跑过。`turnip-a830-*` 要带进客户机。
- 官方页面写的 SM8650 上状态最全，SM8850 的 27 记录最新，SM8750 夹在中间。
- 作者记录过 14 个 GPU 客户端把手机内存打到换页，几分钟没响应。

---

## 6. 分区布局

偏移相对 `unixnu` 起点，详见 `调研/storage-27.md`。

| 区间 | 用途 | 状态 |
| --- | --- | --- |
| 0 到 8948 MiB | 17 的清单和根卷 | 不动 |
| 8948 MiB 到 10 GiB | 保护带 | 空 |
| 10 到 27 GiB | **27 根窗 A**，iPad17,3 | 写入要同意 |
| 27 到 44 GiB | 27 根窗 B，iPhone16,1 | 以后 |
| 44 到 63 GiB | 27 持久 Data | 以后 |
| 63 到 64 GiB | 日志保留区 | 共用 |

27 根镜像约 14.7 GiB【读码，sheng 校验记录】：OS 9168 MiB，SystemOS 5738 MiB，哈希表约 117 MiB。第一阶段 Data 放内存，和 17 一样重启即丢。

**不能 `fastboot flash unixnu`，那会从分区开头覆盖 17。** 用安卓下 `dd` 加 `seek=2560`（块大小 4 MiB）。

---

## 7. 下载清单

**D0、D1 已下载，其余没有下载。** 实际占用：D0 解压后 77 MB（`UniXNU-27/out/ipsw/`），D1 的 Apple 源码 22 MB（`UniXNU-27/out/feeds/`），上游框架和模块 30 MB（`UniXNU-27/`），sheng 仓库 5 MB。

本机已有，不用下载：`xnu-12377.121.6`（`inputs/xnu-12377.git`）、corecrypto、Mesa `85fda3b`、metal2vulkan `720ef1c`、Xcode 26.6（`ld-classic`）、Xcode 27（iOS 27 SDK）、UEFI 工作树、`aea`、`ipsw`、TrustCache 头。另外 `~/Library/Developer/Xcode/iOS DeviceSupport/iPhone19,4 27.1 (24A94425)` 有 6.5 GB 带符号的 iOS 27.1 系统库，可以离线做静态分析，但它不是 24A446。

### D0：kernelcache 等小件，约 24 MB（已完成）

有了它就能算 fileset，验证 sheng 的全部 ABI 数据。按范围取，不用下整个 IPSW。来源是下面 D2 的 URL。

| 文件 | 大小（字节） |
| --- | --- |
| `kernelcache.release.ipad17` | 24,014,290 |
| `Firmware/all_flash/DeviceTree.j820ap.im4p` | 65,439 |
| `Firmware/043-68950-668.dmg.aea.trustcache` | 85,177 |
| `Firmware/043-69086-713.dmg.aea.trustcache` | 3,167 |
| `Firmware/043-69297-793.dmg.trustcache` | 863 |

### D1：源码，约 60 MB（已完成）

大小是 GitHub 报告的仓库大小，按钉住提交浅取会更小。

| 来源 | 版本 | 大小 |
| --- | --- | --- |
| `UniXNU/UniXNU` | `dec111b` | 7.7 MB |
| `UniXNU/UniXNU-sheng` | `6a3d35c` | 1.6 MB |
| `XnuPrelink`、`DarwinBoot`、`VerifiedRoot`、`RamBlock`、`ArmCpu`、`ArmDTPlatform`、`GopFramebuffer`、`AppleKeyStoreState`、`QcomGeniSpi`、`ShengUfs`、`ShengHidInput`、`AirNir`、`AdrenoPM4`、`AdrenoFirmware`、`Adreno740`、`AdrenoMetal`、`ArmSmmu4K` | 按上游 `components.toml` | 合计约 2 MB |
| `apple-oss-distributions/IOStorageFamily` | 337.100.1 | 23.8 MB |
| `apple-oss-distributions/IOHIDFamily` | 2238.120.5 | 5.4 MB |
| `apple-oss-distributions/libdispatch` | 1542.100.32 | 3.7 MB |
| `apple-oss-distributions/libpthread` | 539.100.4 | 0.8 MB |
| `apple-oss-distributions/libplatform` | 375.120.2 | 0.3 MB |
| `apple-oss-distributions/AvailabilityVersions` | 157.2 | 0.2 MB |
| `rust-src` | 默认的 stable 1.99.1 已带；1.98.0 和 nightly 没带，要用时 `rustup component add` | 几十 MB，N4 和 V3 才要 |

### D2：iPad 根卷，约 10.6 GB。N2、V2 要

`https://updates.cdn-apple.com/2026FallFCS/6074e110-d48d-40c2-b0e8-b611520941a3/iPad17,1,iPad17,2,iPad17,3,iPad17,4_27.0.1_24A446_Restore.ipsw`，整包 11,654,176,243 字节（HEAD 200，`Last-Modified` 2026-09-26）。

| 成员 | 大小（字节） |
| --- | --- |
| `043-68950-668.dmg.aea`（OS） | 8,254,390,272 |
| `043-69086-713.dmg.aea`（SystemOS） | 2,348,810,240 |
| `043-69297-793.dmg`（AppOS） | 16,777,216 |

AEA 解密要向 Apple 取密钥，`ipsw fw aea --key`。

### D3：iPhone 根卷，约 11.3 GB。N5、V4 要

`https://updates.cdn-apple.com/2026FallFCS/2397fb0d-078f-4671-a0ad-d589b3cf1288/iPhone16,1_27.0.1_24A446_Restore.ipsw`，整包 12,225,373,164 字节。

| 成员 | 大小（字节） |
| --- | --- |
| `043-69111-662.dmg.aea`（OS） | 8,992,587,776 |
| `043-70459-708.dmg.aea`（SystemOS） | 2,348,810,240 |
| `kernelcache.release.iphone16` | 22,511,975 |

### D4：虚拟机路线

| 来源 | 版本 | 大小 |
| --- | --- | --- |
| `Droid-VM/DroidVM` 发行包 | `DroidVM-0.0.6.r242.3348830-dev.apk`（2026-10-12） | 121,108,280 字节 |
| `Droid-VM/UniXNU` | 分支 `wip/droidvm` | 7.3 MB |
| `Droid-VM/UniXNU-droidvm` | 分支 `wip/hackintosh` | 1.3 MB |
| `Droid-VM/crosvm` | 分支待定，很可能要自己编 | 37 MB |
| `Droid-VM/virglrenderer` | `droidvm` | 11.6 MB |
| `Droid-VM/edk2-gunyah` | `droidvm` | 0.3 MB |

本机磁盘剩 275 GiB，够用。IPSW 和解开的镜像放 `ios27-experiment/inputs/`，那里不被跟踪，也不是临时目录。

### 不建议

- **iOS 27.2（24B119）。** iPad 11,871,016,695 字节，iPhone 12,571,354,631 字节。上游所有地址和判别值都绑 24A446 的 kernelcache 和共享缓存，换构建要全部重找。

---

## 8. iPhone 根卷

**事实：** `iPhone16,1` 的 24A446 存在，当前签名开放【实测，ipsw.me 接口】。OS 比 iPad 大 738 MB，AppOS 是同一个文件【实测，BuildManifest】。

**想法：** kernelcache 继续用 iPad17 的，上游的 fileset 配方、JSON 都不用动。根卷、信任缓存、Data 种子换 iPhone 的。ADT 改 `iPhone16,1`、`D83`、`D83AP`。

**可能省掉的【推断】：** jetsam 表、音频、触感按 `target-type` 取，D83 的文件根卷自带；键盘 `KBLayouts_iPhone.dat`、控制中心、壁纸不用从 iPad 派生；灵动岛是原生的。主线 9 处 dyld 字节修补和 5 个界面补丁里大半可能不需要。

**不省的：**

- 屏幕是 1216×2640，不是 D83 的 1179×2556。405.33 点的布局问题还在。27 应该自带 402 点的机型布局，但在别的 iPhone 上【推断】。
- **@3x 要另外解决。** 27 的 `-[CADisplayMode preferredScale]` 对虚拟设备或高度超过 1080 像素的屏幕一律 `@2x`，不看 `display-scale`【读码，DroidVM `34c42ef1`】。`vmm-present=1` 又不能去掉。13T 两条都中，要修补这个方法。
- 上游的 BGRA 帧缓冲修补地址绑 iPad 的共享缓存，要重找。
- iPhone 根卷有基带、电话相关守护进程，会失败或空转。
- 没有人这样跑过。

**建议：** N2 之前用上游的 iPad 根卷，变量最少。上层验证交给 V4，那里换根卷只是换磁盘镜像。

---

## 9. 风险

| 风险 | 级别 | 说明 |
| --- | --- | --- |
| 流畅度回退 | 高 | sheng 上 27 每命令缓冲约 17 ms；海报任务约 77 ms，环上没有优先级。你现在评价 17“基本跟原生没区别”，27 大概率更卡 |
| GPU 内存池 | 高 | sheng 1536 MiB 的池在 27 上填满过，我们 17 上 1024 MiB 已到 81 到 92%。第一轮 `pagani-gpu-pool=2048`。不够要移植系统内存 BO，量 L |
| kext 配对 | 高 | 24A446 的真实内核是 `xnu-13432.2.10`，公开源码只到 12377。N0 的桩和未解析与上游基线逐地址一致，但这只约束回归，不代表运行时合格。上游 11 月还在修配对错误，配不上的函数运行到才 panic |
| 早期路径看不见 | 中 | `pagani-27-early-progress-t0sz` 是全新代码，没上过机。Oryon 上第一次开 MMU 就是 T0SZ=17 加 4 级表。S3 之后黑屏先查这里。这时定时复位还没开始计时，只能手动长按 |
| 启动镜像体积 | 低 | N1 镜像 36.6 MB，载荷解压后 192 MiB，与主线相同。N2 的真种子可能要加大，ABL 的上限没测过 |
| fileset `__DATA` | 中 | L 集合已放下。显示、HID、`Adreno830` 进来后，原生 `__DATA` 还要塞进原 kernelcache 的槽，可能要把大静态数组改成运行时分配 |
| 写手机 | 中 | 这颗闪存 `bDataReliability` 为 0，LUN 0 掉电不保证。写约 15 GB 时保持充电 |
| 第三方 App | 中 | arm64e 插件大概率载不进 arm64 进程，第三方 App 拿不到 Metal。要出 fat 包 |
| `Adreno830` 在 27 上第一次跑 | 中 | 只过编译。中断、线程、内存的时序都没验证 |

---

## 10. 需要你决定的事

| # | 决定 | 我的建议 |
| --- | --- | --- |
| 1 | **N1 上机一次：`gpu-loop.py` 临时启动，不写存储，要人在场** | **做。** 这是现在最便宜的判定：能不能在这台手机上起 27 内核。放在你本来就要上机的时候。13T 现在不在 adb 上，要等它回来 |
| 2 | 走不走虚拟机路线。要下 D4，在手机上装应用、加载内核模块、放镜像到 `/data` | 走，N1 之后先做 V0、V1 |
| 3 | 下 D2（10.6 GB）。N2、V2 都要 | N1 过了再下 |
| 4 | 下 D3（11.3 GB）。只有验证 iPhone 根卷时才要 | V2 跑通后再说 |
| 5 | 允许写 `unixnu` 分区 10 GiB 处 | N2 时单独问。走 V 路线就不需要 |
| 6 | 跑 PAC 探针：EL0 版要往 `/data/local/tmp` 放一个文件；EL1 版要编进主线下一包 | 不是必须。N1 或 V2 会直接验证。想早点定心就跑 EL0 版 |
| 7 | 上机授权方式：逐次问，还是给一批次数 | 第一轮单独问。内核入口跑通后再给一批，比如 8 次 |
| 8 | 主线先做 AdrenoMetal 合并（`merge-plan.md` 阶段 2） | 对 27 很值，能省 N4 一大半 |

---

## 11. 顺带给主线的发现

都不属于 27，我没有动主线，请主线判断。

1. **`sys_dcache_flush` 在 21H461 上也只是 `dsb ish; ret`**【实测】。`SurfaceMaintenance.h` 完成时的失效一直是空操作，`bridge/NativeMetalBridge.rs:213,216` 同样。CPU 在 GPU 写的时候预取了旧行，读回会是旧数据。修法是自己写 `dc civac`，或确认没人读后删掉。详见 `调研/gpu-27.md` 第 2.3 节。
2. `Adreno830/docs/PROGRESS.md:470,504` 写的“根镜像 9147 MiB”和基线文件不符。基线实际是 `0x22F500` 块，8949 MiB。
3. 用 `adb` 时要写序列号。这台电脑现在还连着另一台设备 `SERIAL_OTHER_DEVICE`。

---

## 12. 过程中的差错

没有造成影响，记录在这里。

- **我的 `adb` 落到了别的设备。** 13T（`SERIAL_13T`）中途离开 adb，另一台设备（`SERIAL_OTHER_DEVICE`，`OPD2513`）成了默认目标。我有几轮查询打到了它上面：`uptime`、`ls`、`getprop`、`su -c id`，以及几十次 `dd`（路径不存在，失败）。全部只读。这几轮结果已作废，`调研/storage-27.md` 里相关的抽样标成未核实。文档里的命令都写死了序列号。
- **GPU 审计任务有两处越界。** 一条命令里误写了 `rm -rf /dev/null`，被系统拒绝，`/dev/null` 完好，我已核对。Python 在只读的参考目录留了 `__pycache__`，它已删除，我核对 `git status` 干净。
- **我误用了 macOS 没有的 `timeout` 命令。** 一次搜索没有真正运行，得出“搜不到 9147”的错误结论，已重查并更正。

---

## 13. 对已有文档的更正

| 文档 | 原说法 | 更正 |
| --- | --- | --- |
| 任务背景 | sheng 是小米 13 系列 | 小米平板 6S Pro 12.4，SM8550 |
| `ipados27-parity.md:148` | iPhone16,1 是否有 27 未核实 | 有，27.0.1 和 27.2 |
| `ipados27-parity.md:70` | `sys_dcache_flush` 只剩屏障是 27 的行为 | 21H461 也一样 |
| `ipados27-parity.md` 2.2 节 | 各项未实测 | 见 `调研/pac-oryon.md` |
| `merge-plan.md` a.6 | 默认 12 个窗口 | 10-31 起默认 16 GiB、32 窗口 |
| 任务背景 | 前 9147 MiB | 手机上清单是 8948 MiB |

---

## 14. 目录

| 路径 | 内容 |
| --- | --- |
| `PLAN.md` | 本文 |
| `调研/pac-oryon.md` | 问题 1 |
| `调研/storage-27.md` | 问题 3 |
| `调研/drivers-27.md`、`调研/drivers-27-port.md` | 驱动审计和移植 |
| `调研/gpu-27.md` | GPU 栈审计 |
| `调研/xnu-12377.md` | XNU 补丁移植 |
| `调研/n0-build.md` | N0 构建记录 |
| `调研/raw/` | 手机原始抓取 |
| `device/UniXNU-Pagani_SM8750-ios27/` | 27 设备层 |
| `soc/UniXNU-sm8750-common-12377/` | 27 SoC 层 |
| `drivers/`、`kits/` | 27 驱动副本 |
| `images/` | N1 启动镜像和载荷记录 |
| `runs/` | 上机证据，N1 跑过才有 |
| `framework/` | 相对上游的框架差异 |
| `UniXNU-27/` | 上游框架检出和构建输出，3.6 GB |
| `probes/` | PAC 探针 |
| `scripts/check-xnu-12377-series.sh` | 补丁系列验证 |
| `scripts/make-n1-placeholder-seed.py` | N1 占位 Data 种子 |
| `scripts/port-check/` | 编译、链接、早期路径的验证脚本 |
| `inputs/` | XNU、corecrypto、UEFI 副本，sheng 仓库，占位种子，信任缓存 |
