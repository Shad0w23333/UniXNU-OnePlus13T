# 代码盘点：现有代码、接线状态、可移植代码（工作流 code 键）

- 时间：2026-10-31 07:40–08:20。只读盘点加主机测试：没碰手机，没改任何源码树，没提交。
- 范围：`UniXNU-Pagani-Drivers/drivers` 的全部驱动；`UniXNU/components.toml` 与 13T `board.toml`；GPU 线设备仓库的 XNU 补丁；`工具/` 下的种子与叠加层工具；当前镜像配方（C/D 包）；`参考资料/UniXNU-sheng` 里可移植的 Wi-Fi、音频、电池、ADSP 代码。
- 主机测试脚本（今晚修正后的版本，可直接重跑）：`bash build-userspace/a830-inputs/inventory/code-run-host-tests.sh [输出目录]`，结果见第 8 节。
- 简写：DRV = `UniXNU-Pagani-Drivers/drivers`；DEV = `UniXNU-Pagani_SM8750-gpu`；SHENG = `参考资料/UniXNU-sheng`（commit `08cde20`）；DT = `build-userspace/a830-inputs/dt/running.dts`；PROGRESS = `Adreno830/docs/PROGRESS.md`；RUNS = `工具/gpu-test-loop/runs`；TREE = `UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree`。

## 0. 结论速览

1. DRV 现有 22 个驱动目录（含 07:45 震动工作流刚放进来的 `PaganiHaptics`）。今晚实跑的主机测试只有一项失败：`NativeRamBlock/Test/sparse.cpp` 断言种子上限 2 GiB，而 `SparseDisk.h` 自导入提交 `0d0b413` 起就是 1 TiB 上限（`size>(1ULL<<40)`），是旧测试没跟上，不是今晚的改动，也不影响内核（内核另在 `NativeRamBlock.cpp:203/211` 按 `0x80000000` 分流）。另外两项“补丁与原始源码一致”检查（PaganiDiag `check-paganidiag-host.py patch`、`test-phone-keyboard-trace.py` 的补丁检查）因为 TREE 里的 `kern_exec.c`/`kdebug.c` 已经打过补丁而无法复跑，属环境原因。
2. 当前 C/D 包内核 `95a53442`（`kernel-build-a830-package.log`）链接了：PaganiArmCpu（含 PaganiArmDTPlatform）、NativeAES、NativeUfs、NativeVirtioBlock、NativeRamBlock、ShengPlatformPower、NativeMobileDisplay 归档（连同 Stage7Probe、BootProgress、PaganiInput、PaganiPanel、NativeKeyStoreEvents、QcomGeniSpi）、Adreno830、PaganiAdsp（默认关）、PaganiAudio、PaganiBattery（默认关）。**没链接**：PaganiSpeaker（没有构建脚本、没有 Stage7Probe 钩子、不在组件目录）、PaganiPersistentData（只有提案补丁）；PaganiDiag 只有内核补丁在 series 里，守护进程要靠种子。**下一次构建会自动带上**：`PaganiHaptics`（07:45–07:46 已进 `components.toml`、`board.toml`、Stage7Probe，默认关）和 BootProgress 里未提交的 `pagani-reset-seq`（默认 0，一个额外调用都没有）。
3. 真机验证过：显示（含 GPU 交接）、触摸与按键（含 Plus 键静音开关）、8 核与 `pagani-cpufreq=2`、UFS 只读根、RAM Data、A830 GPU、ramoops 日志回读、音频服务（静音后端，`audio: open 1 by audiomxd pid 92`）。**从没在 13T 上跑过**：PaganiAdsp、PaganiBattery、PaganiSpeaker、PaganiHaptics、`pagani-idle`、`pagani-screenoff`、面板睡眠、`sheng-data-persist`、PaganiPersistentData、`pagani-te`/`pagani-fb-flip`。所有 RUNS 里没有一条 `[battery]`、`[adsp]`、`[spk]` 行。
4. Wi-Fi：sheng 的 Wi-Fi 是三层。`Ath12kWifi`（协议栈）**本机没有源码**：SHENG 的子模块是空目录，`components.toml` 只记了 `UniXNU/Ath12kWifi` `f6bf561`，需经用户同意后拉取；`WifiProbe.cpp`（SM8550 + WCN7850 的上电、PCIe、MHI、DMA）大半要按 13T 重写；`WifiNet.cpp`（Apple80211 网络服务，同一 iPadOS 21H461）基本可直接复用。13T 是 peach（`17cb:110e`），Ath12kWifi 没有它的定义；13T 的 PCIe MSI 在安卓下走 GIC ITS（XNU 的 ArmCpu 没有 ITS），只能用 sheng 代码里现成的 1 ms 轮询退路。多周工作（C）。
5. 音频：sheng 的 GPR、AudioReach、音频服务、HAL 插件都已移植（PaganiAudio 上过机；PaganiSpeaker 把 CS35L43 换成 TFA9873，写完未接线）。麦克风没移植：13T 麦克风在 WCD939x、挂在 VA SoundWire（`0x7630000`），sheng 是 WCD938x 在 TX SoundWire（`0x6d30000`）。
6. 电池与 ADSP：都已移植、接进内核、默认关、从未上机。B 包（`pagani-battery=1`）用的是旧内核 `f638f5b0`，没有系统应用叠加，早上要按 D 包配方重打。
7. 今晚真正能做到“源码 + 主机测试 + 编进镜像”的（A 级）：PaganiSpeaker 接线（约 3–4 小时，含 GCC `0x52008` 共享投票锁）；按 D 包出单变量镜像（`+pagani-battery=1`、`+pagani-adsp=1 pagani-adsp-stop=N`、`+pagani-haptics=2` 干跑、`+pagani-idle=1`）。其余（Wi-Fi、麦克风、传感器、USB、蓝牙、相机、定位、大容量持久存储）都要真机多轮或多周；蜂窝、NFC 支付、指纹（Touch ID）离不开苹果的基带与安全隔区，这块硬件上做不到。

## 1. 驱动逐个盘点（DRV）

“GPU 线内核”指 `kernel-build-a830-package.log`（C/D 包内核 `95a53442`）里实际编进去的；“真机”只认 PROGRESS 与 RUNS 里的证据。

| 驱动 | 作用 | 启动参数（默认；镜像里的值） | 主机测试（今晚实跑） | GPU 线内核 | 真机 |
| --- | --- | --- | --- | --- | --- |
| BootProgress | 早期启动显示、panic 屏、ramoops 日志（A/T/U/S/X/G/F/J 记录，36 份副本）、定时复位、Gunyah 看门狗、FPS/CPU 行、崩溃回溯；未提交的 `pagani-reset-seq`（Linux 式复位前序列，复位工作流在做） | `pagani-ramoops`（镜像 =1）、`pagani-panic-reset`（=20）、`pagani-reset-after`（C=900、D=1800）、`pagani-wdog`（=30）、`pagani-reset-wdog`（=1）、`pagani-overlay`（=0）、`pagani-gpu-status`、`pagani-fps`、`pagani-crash`、`pagani-platform-status`（0）、`pagani-reset-seq`（0）、`pagani-reset-cold`、`pagani-ramoops-copy/console/pmsg/pa` | `Test/ramoops.c`、`crash.c`、`gunyah-wdog.c`、`console.c` 全过（console 需 `-I PaganiDarwinBoot/DarwinBootPkg/Driver -I TREE/build/boot-progress`） | 是 | 是：每轮读回，D2 包 734 条 733 条校验、零翻转 |
| NativeAES | 软件 AES-CBC，挂 IOAESAccelerator | 无 | 无测试 | 是 | 隐式（每次开机） |
| NativeKeyStoreEvents | 易失 AppleKeyStore 状态与选择子 | `sheng-aks=1` | `test_closed_boundary.py` 过 | 是（显示归档内） | 隐式 |
| NativeMobileDisplay | Legacy 帧缓冲 ABI 的 CPU 呈现、GPU 交接（`sheng-fb-gpu`）、120 Hz 节拍、DPU 探针、三缓冲翻页 | `sheng-vsync=120`、`sheng-vsync-fixed=1`、`offline-legacy-*`（镜像都开）、`sheng-fb-gpu=1`、`pagani-vsync`（120）、`pagani-fb-wc`（1）、`pagani-fb-async`（1）、`pagani-fb-scan`（0）、`pagani-te`（0）、`pagani-te-ar-off`、`pagani-fb-flip`（0）、`pagani-dpu-probe`（0；`=3` 曾整机卡死）、`sheng-fb-diff`、`sheng-early-retire` | 8 项全过（request-state、source-cache、display-timing 16、scanout-flip 10、dpu-safety 193、gop-handoff 7、frame-rate、frame-timing） | 是 | 是：桌面、GPU 交接、F1 提速（10-29 用户确认）；TE/翻页未测 |
| NativeRamBlock | RAM Data 盘：≤2 GiB 打包种子按页懒分配；带持久覆盖层 `PersistOverlay.h`（接 `native_ufs_store_*`） | `sheng-data-reset` | direct、persist、seeddisk 过；packed-hash/seed-identity 是工具（对 D 包种子输出 `aee73c27…`，与种子 `.json` 的 `output.sha256` 一致）；**sparse.cpp 失败**（见第 0 节第 1 条） | 是 | 是：每轮 Data 盘 |
| NativeUfs | SM8750 UFS、SMMU 接管、只读根窗口；给别的驱动的分区读 `native_ufs_part_find/read`；持久窗口 `native_ufs_store_*`（唯一会写盘的部分） | `native-ufs-root=1`、`native-ufs-diagnostics=1`、`native-ufs-probe`、`native-ufs-stats-delay`、`sheng-data-persist`（0；1 只读、2 开写窗，窗口在 `unixnu` 分区末尾、≤分区 1/4）、`sheng-log`、`sheng-log-reserve` | `check_root_path_host.py`（RootLayout、RootWindowRead、RootDiagnostics、RootPublication、PartitionTable 带手机 GPT）过；`check_legacy_descriptors.py` 14 项过 | 是 | 是：只读根；持久窗口从未开 |
| NativeVirtioBlock | 仅 ABI 依赖，13T 没有 virtio | 无 | 无 | 是 | 不适用 |
| PaganiAdsp | ADSP：modem_a 上的固件就地读、PAS 启动、SMEM/SMP2P/GLINK、IPC 路由与服务定位（音频 PD、充电 PD、传感器 PD、调制解调器 PD 的 `gps/gps_service`） | `pagani-adsp`（**0**）、`-stop=N`、`-part`（modem_a）、`-ready-ms`（5000）、`-rail`（384） | `Test/check-pagani-adsp-host.py --gpt-dir 一加13T原厂备份/分区表`：核心 4348、qrtr 84、FAT16 950、FAT32 1539、平台适配 10346、整体 26643 项、GPT 查找，全过 | 是（追加进 `libAdreno830.a`；Stage7Probe 在 GPU 之后调 `pagani_adsp_start`） | **从未上机**（待测队列第 11 项） |
| PaganiArmCpu | CPU/GIC/定时器、8 核 SMP、SCMI PERF 调频与调速器、PSCI 空闲、熄屏 CPU 封顶、驱动用 SPI 中断接口 | `cpus=8 sheng-smp=1 sheng-smp-gic=1 armcpu-self-ipi=1`（镜像开）、`pagani-cpufreq`（0；镜像 =2）、`-min`、`pagani-idle`（0）、`-c4-us`、`-cl-us`、`-log`、`pagani-screenoff` 位 0（0）、`armcpu-diagnostics`、`armcpu-skip-gic`、`armcpu-spi-ipi` | spi-gate 15、scmi-perf 2181373、psci-idle 2634553 项全过 | 是 | 8 核与调速器在跑（D 包 `cpufreq: d0 at lv12 2918MHz…`）；空闲与熄屏未上机 |
| PaganiArmDTPlatform | ADT 平台设备 | `sheng-platform-stage`、`sheng-platform-panic` | 无 | 是 | 隐式 |
| PaganiAudio | iOS 音频服务（共享环、时间线）+ HAL 插件 `PaganiAudio.driver`（audiomxd 加载）；静音后端 | `pagani-audio`（0；C/D=1）、`-hostclock`（1）、`-trace`（0）、`-probe`（1）、`-log`（1） | `scripts/check-pagani-audio-host.py --tree TREE`：abi 67、engine 140、plugin 205、glue 740、三处内存屏障、信任包合并、iOS 插件 CDHash `c88be410…`、内核归档，全过 | 是（`CONFIG_XNU_EXTRA_LD_ARCHIVES` force_load） | 是：`audio: up … backend silent`、`audio: open 1 by audiomxd pid 92`；但 `audio route #1 … ours=35 vold=1 run=0 vcsp=0/0 vc=0 … out=0 … ports=0` → 音量条不出（路由层问题，音量工作流在查） |
| PaganiBattery | `IOPMPowerSource`：NFG8011B 电量计（I2C hub SE1 `0x984000`、地址 0x55、gpio66/67）+ ADSP 电池管理器（`PMIC_RTR_ADSP_APPS`） | `pagani-battery`（缺省时等于 `pagani-adsp`，即关）、`-gauge`（1）、`-gauge-id`（1）、`-adsp`（1）、`-charge`（0）、`-fcc-ma`（2000）、`-sdp-ma`（900）、`-design-mah`（6260）、`-gauge-ms`、`-log`（1）、`-pmic`（1） | `Test/check-pagani-battery-host.py --tree TREE`：battmgr 3138、gauge 239、i2c 7739、spmi 2404、core 82235、参数文档核对、内核归档（7 个对象），全过 | 是（追加进 `libAdreno830.a`；Stage7Probe 无条件调用，自己读参数） | **从未上机**（RUNS 无 `[battery]`；B 包未测） |
| PaganiDarwinBoot | UEFI→XNU 负载与内存交接（固件侧） | — | `test-packed-ramdisk.py`、`test-adt.py` 过 | 固件 | 是 |
| PaganiDiag | iOS 诊断守护进程 `paganidiag`，经 `kern.pagani_diag_line` 写 U 行；顶替第一次 exec 的 DumpPanic | `pagani-diag`（≠0 才顶替；C/D=0）；另需 `sheng-no-lc=1` | `Test/run.sh`：core 814、kernel 511（-O0/-O2）过；`build` 过（CDHash `7301a805…`）；`patch` 检查环境原因失败；`trust` 缺草稿目录旧信任包跳过 | 内核补丁 `phone-diag-sysctl.patch` 在 series；守护进程靠种子 | A 包上机没有任何 `diag:` 行（没跑起来） |
| PaganiHaptics | 震动：PMIH010x HAP530（SPMI 从机 7，`0xf000/0xf100/0xf200`），DIRECT_PLAY、板上 FIFO 效果、PCM 流 | `pagani-haptics`（0；2 干跑）、`-vmax-mv`（3600）、`-play-ms` 等 6 个 | `Test/check-pagani-haptics-host.py`：spmi 9047、core 91832、参数核对，全过（07:5x 快照） | 07:46 起已进 `board.toml` 与 Stage7Probe，下次构建链接 | 未上机；iOS 触感链路在震动工作流（`a830-inputs/haptics/ios`） |
| PaganiInput | S3910 触摸（TouchComm、SE DMA、SMMU 直通 `0xa3`）、PMIC 键（电源、音量±）、Plus 键做静音开关、点屏脚本 | `pagani-buttons=1`、`pagani-touch=1`、`pagani-input-script`（0）、`-at`（240）、`pagani-peripheral-status`（0） | DEV `scripts/test-peripherals.py --tree TREE` 过（SPI、TouchComm v1/v2、HID、按键与静音开关、RPMh 锁、点屏脚本） | 是 | 是：触摸、电源键、音量键报告、静音开关 |
| PaganiPanel | DSI 亮度（DCS 0x51）、熄屏 0x28/0x29、`pagani_screen_state`、熄屏时面板睡眠 | `pagani-panel=1`、`pagani-screenoff` 位 2（0） | `Test/panel-power.cpp` 314 项过 | 是 | 电源键熄屏/亮屏自 build 50 起用户确认过（PaganiInput README）；DCS 亮度与熄屏命令在 DEV `porting.toml` display 一节仍记为“未通过硬件测试”，PROGRESS 也没有亮度滑块的结论；位 2 未上机 |
| PaganiPersistentData | 持久 Data：安卓 `/data` 里的 f2fs 锁定文件，XNU 直写其 UFS 块（128–256 GiB） | `pagani-data-file=0/1/2`、`-eager`、`-verify`（1）、`-selftest`、`-head`、`-revoke`；`pagani-data-volatile-mib`（1024） | `Test/run.sh` ALL PASS（6388、1823、441 项，补丁后的 RootCommands 两组，`git apply --check` 两份提案补丁） | **否**（`integration/*.patch` 只是提案） | 否；需用户在安卓下先建文件（`工具/persistent-data`） |
| PaganiSpeaker | 扬声器：两颗 TFA9873（I2C hub SE4 `0x990000`，0x34/0x35，复位 gpio2/164，L15B 1.8 V）、GPR/APM 建图（I2S sink、二路 MI2S、48 kHz×2×32 bit）、PRM 位时钟、周期缓冲（`0xd0000000`）、接 PaganiAudio 的 `adsp` 后端、开机测试音 | `pagani-speaker`（0）、`pagani-audio-tone`（0）、`-tone-hz`（440）、`-tone-ms`（500）、`-tone-atten`（30）、`pagani-speaker-stop/amps(3)/sd/bits(32)/prm-port/clock(1)/feedback(0)/i2c-pull(1)/rail(1)/sids(0xf)/dsp-ms(120000)/pa` | 无现成 runner（`scripts/` 空，README 里的 `check-pagani-speaker-host.py` 不存在），手工编译：tone 26675、protocol 200、amp 16594、hw 240、core 269405 项全过 | **否**：无构建脚本、不在组件目录与 `board.toml`、Stage7Probe 没有 `pagani_speaker_start` | 从未上机 |
| ShengPlatformPower | 受限 PMGR 兼容（保留原类名与 ABI） | `sheng-pmgr=2`、`sheng-platform-power` | 无 | 是 | 隐式 |
| Stage7Probe | 启动外设与驱动：输入、面板、显示、DPU 探针、`pagani_gpu_start`、`pagani_adsp_start`、`pagani_battery_start`、`pagani_audio_start`、（新）`pagani_haptics_start` | `pagani-buttons/touch/panel/gpu/adsp`、`offline-key-events`、`sheng-aks`、`sheng-fb-gpu`、`pagani-peripheral-status`、`pagani-platform-status` | 无单测（编进显示归档） | 是 | 是 |

DRV 之外、同样编进 GPU 线内核的：`UniXNU/drivers/QcomGeniSpi`（SPI 引擎）、`UniXNU-sm8750-common/kits/Sm8750Platform`（GCC/UFS、SMMU、RPMh、SPI 时序、SPMI 观察通道、DSI FIFO：`PeripheralIO.h`、`Rpmh.h`、`SmmuTakeover.h`、`GccUfs.h`）、`Adreno830`（GPU 内核驱动 + `platform/sm8750` 的 RPMh、SCM、AOSS QMP、SocInfo、Tsens、LLCC、GpuPower，ADSP/电池/震动都复用这层）。`Adreno830/tests/run-host-tests.py` 今晚 44 过、0 失败、1 跳过（`test_sha256` 没给 `--firmware`），内核对象 51 个。

### 1.1 几处接线细节（今晚要动 PaganiSpeaker 时必须知道）

- **GCC 投票寄存器 `0x52008` 有三个写者**：触摸（`Sm8750Platform/PeripheralIO.h:52-53`，无锁 `|= 0x043c0000`）、电池（位 7/8/11，持弱锁字 `sm8750_gcc_vote_owner`，每次传输前复查被冲掉的位）、扬声器（`PaganiSpeaker/SpeakerHw.c:105-108`，无锁置位 7/8/14）。PaganiBattery README 明说“PaganiSpeaker 未链接”是这个顺序安全的前提；接扬声器前要在 Sm8750Platform 加一个三方共用的加锁读改写助手。
- PaganiSpeaker 的入口 `pagani_speaker_start()`（`PaganiSpeaker.cpp:279`）注释写“Stage7Probe 在 `pagani_audio_start()` 之后调用”，但 Stage7Probe 里没有这个弱默认与调用。
- PaganiSpeaker 的 DSP 资源全部借 PaganiAdsp（`adsp_apps` 通道、`PaganiAdsp.h` 弱默认）；`pagani-speaker=1` 要配 `pagani-adsp=1` 与 `pagani-audio=1`，否则停在第 6 步（等 DSP 最多 120 s）。
- 震动工作流已经把 `PaganiHaptics` 接进 `components.toml`、`board.toml`（`component:PaganiHaptics` 追加进 `libAdreno830.a`）、Stage7Probe（第 47、297 行），`PaganiPeripherals.h` 的常量片段（`integration/PaganiPeripherals.h.snippet`）07:50 时还没合进 DEV。

## 2. 组件目录与 13T 板级

- `UniXNU/components.toml` 现有 47 个组件（07:45 又加了 `PaganiHaptics`）：26 个官方（`UniXNU/*`），21 个社区（`IssacL04/UniXNU-Pagani-Drivers` 与 `UniXNU-sm8750-common`）。
- DEV `board.toml` 的 `[components] include` 现为 24 个：BootProgress、NativeAES、NativeKeyStoreEvents、NativeMobileDisplay、NativeRamBlock、NativeUfs、NativeVirtioBlock、PaganiArmCpu、PaganiArmDTPlatform、PaganiDarwinBoot、ShengPlatformPower、Stage7Probe、NativePrelink、PaganiVerifiedRoot、Sm8750Platform、PaganiInput、PaganiPanel、QcomGeniSpi、Adreno830、AdrenoMetal、PaganiAdsp、PaganiAudio、PaganiBattery、PaganiHaptics。
- 不在 13T 的官方组件：ArmCpu/ArmDTPlatform/DarwinBoot/SoftwareAES/AppleKeyStoreState/RamBlock/VirtioMmioBlock/VerifiedRoot/XnuPrelink（由社区同名改版替代）、Adreno740/Sm8550Platform/ShengUfs/ShengHidInput/ShengNovatekTouch/ShengPreflight/GopFramebuffer（sheng 或 SM8550 专用）、ArmVirtPlatform/VirtioMmioConsole（QEMU）、AdrenoFirmware/AirNir（13T 未引入）；AdrenoPM4、ArmSmmu4K 不在 include 里，但作为 Adreno830/AdrenoMetal 的依赖进了构建树（TREE `kits/` 有这两个链接）；**Ath12kWifi（Wi-Fi；13T 没选，本机也没有源码）**。
- 在 DRV 里但不在组件目录的：PaganiSpeaker、PaganiDiag、PaganiPersistentData。
- DEV 的移植记录 `porting.toml`：network、audio 仍是 `planned`，power 是 `host-verified`（“电池、充电、睡眠、完整稳压器/时钟控制不支持”），gpu `implemented`，其余 device-verified。

## 3. XNU 补丁（构建实际应用 81 个）

- arch 层 57 个（`UniXNU/profiles/arch/arm64/patches/xnu/series` → `UniXNU/patches/xnu/*`）：4 KiB 用户 ABI、文本修补（`native-text-fixup`，/chosen/text-fixups 走它）、联合挂载（`native-union-*`）、owner 根/缓存/SystemOS（`offline-owner-*`）、GPU 插件与沙箱授权（`zzw-*`、`zzwb-*`）、附加卷挂载（`zzy-native-gpu-addon-mount`）、预装应用复制（`zzy-staged-system-apps`）、跳过启动约束（`zzyc-owner-skip-launch-constraints`，`sheng-no-lc=1`）等。
- soc 层 2 个：`sheng-platform-power.patch`、`sm8750-smp.patch`。os 层 3 个（`pagani-series`）：`zzz-boot-progress`、`xnu-10063.141.1-security-init`、`xnu-10063.141.1-sparse-index`。
- 设备层 19 个（DEV `patches/xnu/series`，顺序即应用顺序）：

| 补丁 | 作用 | 启动参数 / 开关 |
| --- | --- | --- |
| `pagani-bootstrap.patch` | bsd_init/kern_exec 的 13T 引导钩子 | `native-root-audit` |
| `zzzz-boot-progress-panic-output`、`zzzz-early-boot-progress`、`zzzzz-root-panic-progress`、`zzzzzz-vm-bootstrap-progress`、`zzzzzzz-early-framebuffer-page-tables` | 早期启动进度、panic 输出、早期帧缓冲页表 | — |
| `ticket-lock-diagnostics.patch` | 票锁诊断 | — |
| `phone-wallpaper-collections.patch` | `/private/var/ngpu/wallpaper-collections` 只读叠到 `/Library/Wallpaper/Collections` | 种子里有目录才挂 |
| `phone-keyboard-layouts.patch` | `/private/var/ngpu/textinputui-layouts` 叠到 TextInputUI（iPhone 键盘布局） | 同上 |
| `crash-trace.patch` | 致命信号时记录用户态寄存器与帧链（X 记录） | — |
| `phone-staged-apps-migration.patch` | 只对 datamigrator 回答“非易失存储” | `sheng-staged-apps` |
| `phone-staged-apps-trace.patch` | 预装应用拷贝与迁移的 U 行 | `sheng-staged-apps` |
| `pagani-audio-addon-mount.patch` | `/private/var/ngpu/audio` 叠到 HAL 插件目录 | `pagani-audio` |
| `phone-boot-facts.patch` | 开机事实行（`identity:`、`overlays:`）、应用包移动统计 | — |
| `zzwc-native-adreno-sandbox-grant-all.patch` | 带 `NativeSandboxGrantAll` 的服务允许所有进程开用户客户端（Metal 插件自己按名单发设备） | — |
| `phone-keyboard-trace.patch` | 键盘 kdebug 计数（`kbt` 行） | `pagani-kbtrace`（C/D 未开） |
| `phone-diag-sysctl.patch` | `kern.pagani_diag_line` 与顶替 DumpPanic | `pagani-diag` |
| `phone-controlcenter-settings.patch` | `/private/var/ngpu/controlcenterui` 叠到 ControlCenterUI（`DefaultModuleSettings~iphone.plist`） | 种子里有目录才挂 |
| `phone-system-apps-view.patch` | 拷来的应用只读叠到 `/Applications`，删迁移哨兵 | `pagani-system-apps` |

- 目录里有、**不在 series**：`pagani-idle-flush.patch`（`pagani-idle-flush=0` 跳过 `cpu_idle` 每次空闲的 L1 按 set/way 刷新；配套 DEV `scripts/test-idle-flush-patch.py`），从未启用。

## 4. 种子与叠加层工具（`工具/`）

| 工具 | 作用 | 今晚测试 |
| --- | --- | --- |
| `make-phone-seed.py` | 从 GPU 底种子派生 iPhone 布局种子：扩到 2 GiB（易失上限）、禁用任务（布局脚本传 `com.apple.fairplayd.A2,com.apple.routined`）、`--wallpaper`、`--keyboard`、`--keyboard-prefs`、`--controlcenter`、`--migrate`、`--force-install-apps`、`--diag-daemon` | （由布局测试覆盖） |
| `make-a830-layout.sh` | 临时改 DEV `board.toml`（退出时还原）出布局变体：`scale3`、`phone`、`phone-plus`、`phone-apps`、`phone-island`、`phone-island-apps`；身份 iPhone16,1（`artwork-device-subtype 2556`、`target-type J120`、回退 iPhone15,2）；文本修补（Core Image 不走 GL、灵动岛状态栏、键盘 F2）放 `/chosen/text-fixups`；环境变量 `A830_PREBUILT_SEED`、`A830_OWNER_TRUST`、`A830_AUDIO`、`A830_SYSTEM_APPS`、`A830_BATTERY`、`A830_DIAG`、`A830_RESET_AFTER`、`A830_EXTRA_ARGS`、`LAYOUT_OUT_OF_BOX` 等 | `test-phone-island-layout.py` 32 项 OK |
| `make-a830-images.sh` | 各镜像条目（`loop-m8-phone` 是布局脚本取参数的来源：`pagani-gpu-profile=0 pagani-gpu-acd=1 pagani-gpu-pool=1024 pagani-cpufreq=2 pagani-gpu-status=2 pagani-reset-after=420 pagani-wdog=30 pagani-overlay=0 pagani-reset-wdog=1`） | — |
| `make-a830-package.sh` | 重编 GPU 线内核（`--local-module` 列了 Adreno830、Stage7Probe、BootProgress、NativeMobileDisplay、PaganiArmCpu、PaganiInput、PaganiPanel、NativeUfs、PaganiAdsp、PaganiAudio、PaganiBattery）再出布局镜像；**没有列 PaganiHaptics**（`local.toml` 的 `[local] drivers` 本就指向 DRV，下次构建时核对一下 `component-PaganiHaptics` 行） | — |
| `make-phone-wallpaper-overlay.py` | `Collections~iphone/ipad.plist` + `UniXNU_Any.wallpaperCollection`（7565/7570，`logicalScreenClass Any`，HEIC 图层软链系统卷） | 壁纸工作流在改（`a830-inputs/wallpaper`） |
| `make-phone-controlcenter-overlay.py` | 由 `~ipad` 生成 `DefaultModuleSettings~iphone.plist` | 已上机生效（06:50 用户确认） |
| `make-phone-keyboard-layouts.py` | 用 21H16 的 `KBLayouts_iPhone.dat` 生成 405.333 点宽的键盘族 | `test-phone-keyboard-layouts.py` 15 项 OK（1 跳过） |
| `phone-keyboard-trace/`、`test-phone-keyboard-trace.py` | `kbt` 计数 | 单元检查过；补丁检查因 TREE 已打补丁失败（环境） |
| `gpu-test-loop/` | `gpu-loop.py`（fastboot boot + 定时复位 + 电量门槛 + 回读）、`read-ramoops.py`、`symbolize-crashes.py`、`ramoops_device.py`、`run-tests.sh` | 未重跑（BootProgress 的写入器测试已过） |
| `persistent-data/` | 安卓端 `unixnu-datafile.c`（建锁定文件）、NDK 构建、f2fs 解析 | 未跑（要 NDK） |
| `seeds/` | 当前种子：`phone-island-apps-audio.dbrdisk`（`fa26fbf2…`，C/D 包用，副本在 `a830-inputs/2026-10-31-pkgAB/`）及信任包 `…-owner-trust.bundle`（`7c8ef1b7…`） | — |

## 5. 当前镜像配方（C/D 包）

- 内核 `95a53442`（06:36 起编：BootProgress 用 `a830-inputs/snapshots/BootProgress-0636`，不带复位工作流的半成品）；种子 `工具/seeds/phone-island-apps-audio.dbrdisk`（`fa26fbf2…`，逻辑 2 GiB，内容哈希 `aee73c27…`，含 PaganiAudio 插件 `c88be410…`、壁纸/键盘/控制中心叠加、迁移哨兵、28 个强制安装、键盘偏好、诊断守护进程文件）；信任包 `7c8ef1b7…`。
- 变体 `phone-island-apps`，环境大致为 `A830_PREBUILT_SEED=工具/seeds/phone-island-apps-audio.dbrdisk A830_OWNER_TRUST=工具/seeds/phone-island-apps-audio-owner-trust.bundle A830_AUDIO=1 A830_SYSTEM_APPS=1`，C 包 `A830_RESET_AFTER=900 A830_EXTRA_ARGS=pagani-diag=0`，D 包 `A830_RESET_AFTER=1800 A830_EXTRA_ARGS="pagani-diag=0 pagani-gpu-status=1"`（`imgC-build.log`、`imgD-build.log`、`pkgC/D-run.log`）。
- D 包完整参数（888 字节，DarwinBoot 上限 1000）：

```
-v serial=0 debug=0x14e cpus=8 sheng-smp=1 sheng-smp-gic=1 armcpu-self-ipi=1 kt_fixed_zones=240 native-ufs-root=1
native-ufs-diagnostics=1 sheng-pmgr=2 offline-apfs-load=1 startup_debug=0 -no_corpses sheng-vsync=120 sheng-vsync-fixed=1
offline-mobile-graphics-load=1 offline-legacy-register=1 offline-legacy-phase=4 offline-legacy-gop=1 offline-legacy-pm=1
offline-legacy-validate=1 offline-legacy-transfer=1 offline-legacy-cpu-present=1 offline-key-events=1 sheng-aks=1 pagani-buttons=1
pagani-touch=1 pagani-panel=1 pagani-gpu=1 sheng-gpu-addon=1 sheng-gpu-metal-opens=0 sheng-fb-gpu=1 pagani-ramoops=1
pagani-panic-reset=20 pagani-gpu-profile=0 pagani-gpu-acd=1 pagani-gpu-pool=1024 pagani-cpufreq=2 pagani-gpu-status=2
pagani-reset-after=1800 pagani-wdog=30 pagani-overlay=0 pagani-reset-wdog=1 sheng-staged-apps=1 pagani-system-apps=1 pagani-audio=1
pagani-diag=0 pagani-gpu-status=1
```

- 板级 ADT（布局脚本临时写入）：`display-scale 3`、`artwork-scale-factor 3`、`artwork-device-subtype 2556`、`product-name "iPhone 15 Pro"`、`artwork-device-idiom phone`、根 `model iPhone16,1`、`target-type J120`、`/buttons function-button_ringerab`、`sleep-disabled 1`；文本修补 = OS 档案的 4 个 + Core Image（`dyld_shared_cache_arm64.07` `0x2e2cf98`）+ 灵动岛（`.25` `0x219d270`）+ 键盘 F2（`.03` `0x3503038`）。
- 参数余量：D 包还剩 112 字节；两次 `pagani-gpu-status` 可删掉一个省 20 字节。加 `pagani-battery=1`（17）、`pagani-adsp=1`（13）、`pagani-speaker=1`（17）、`pagani-haptics=1`（17）、`pagani-idle=1`（14）都放得下，但全加会超；新开关尽量走默认值或 `/chosen` 属性。

## 6. sheng 可移植代码

### 6.1 Wi-Fi

| 文件 | 行数 | 层次 | 13T 能否复用 |
| --- | --- | --- | --- |
| `package/WifiProbe.cpp` | 1504 | 板级：PMIC 与 RPMh 上电（六路 PMU 电源，模式/电压/使能三次请求）、GPIO（WLAN_EN 80、PERST 94、CLKREQ、SW_CTRL）、PCIe0 GDSC/时钟/复位、QMP PHY 表、PARF、LTSSM、iATU 配置空间、BAR、SMMU 流 `0x1400/0x1401` 的 RID 哈希表、DMA 区（借 NativeUfs 的 `native_ufs_extra_*`）、固件从 `oops` 分区的 `WFW1` 容器读、MSI（SPI 141）或 1 ms 轮询、MHI 状态快照、`lx_platform`/`nw_board` 回调、工作线程 | **估计（推断）四成左右可直接用**：回调骨架、`lx_platform` 实现、轮询循环、RDDM 扫描、事件环应答、iATU/BAR/配置空间、RID 哈希（13T 的 `iommu-map` 也是 `0x1400`/`0x1401`）。**要按 13T 改**：电源轨表、GPIO 号、PHY 表、GCC 偏移、PCIe 窗口、固件来源、DMA 区来源（13T 的 NativeUfs 没有 `native_ufs_extra_*`） |
| `package/WifiPcieTables.h` | 37 | SM8550 PCIe0 QMP PHY 值 | 13T 的 DT `qcom,phy-sequence`（`pcie@1c00000`，PHY 版本 `0x5e`）开头的 serdes 值与之相同（`0xc0=01, 0xcc=62, 0xd0=02, 0x60=f8, 0x64=01, 0x00=93 …`），整表应从 DT 的三元组（偏移、值、延时）生成 |
| `package/MhiBoot.c/.h` | 241 | MHI BHI/BHIe 固件下载状态机，纯 C、无等待 | 可直接用（peach 同为 MHI；镜像格式与执行环境要核对） |
| `package/WifiNet.cpp` | 597 | iOS 侧：以太网接口、`IO80211Interface` 式注册项、Apple80211 ioctl（`SIOCGA80211/SIOCSA80211`：power、scan、associate、state、ssid、bssid、channel、rssi、country…） | **可直接用**（同一 iPadOS 21H461）；`kHardwareVersion` 字符串写死 `WCN7850` 要改；iPhone 身份下 wifid 行为未知 |
| `drivers/Ath12kWifi`（子模块） | 本机为空 | ath12k 协议栈（PCI、MHI、QMI、WMI、HTT、`nw_wifi_start/step/scan/connect/tx`），含 QCC2072、QCN9274、WCN7850 定义 | **源码不在本机**；peach 不在支持列表 |

13T 事实（DT）：
- 端点：`qcom,cnss-peach@b0000000`（DT 42409–42461），`supported-ids = <0x110e>`，`use-nv-mac`；PCIe0 `pcie@1c00000`（DT 26542），`compatible "qcom,pci-msm"`，parf `0x1c00000`、phy `0x1c06000`、dm_core `0x40000000`、elbi `0x40000f20`、iatu `0x40001000`、conf `0x40100000`、`pcie_sm 0x1d07000`；MEM 窗口 `0x40300000`+`0x3d00000`（sheng 是 `0x60300000`）；`perst-gpio` 102、`wake-gpio` 104、`qcom,pcie-clkreq-pin` 103；`qcom,drv-name "cesta"` 与 `qcom,pcie-sm-seq`（SM8750 新的 PCIe 电源状态机，sheng 没有）。
- GDSC：`gcc_pcie_0_gdsc` `0x16b004`、`gcc_pcie_0_phy_gdsc` `0x16c000`（与 SM8550 的 GCC 内偏移 `0x6b004`/`0x6c000` 相同；其余时钟偏移要按 gcc-sm8750 的事实核对）。
- 引脚：WLAN_EN gpio16（`cnss_wlan_en_active`：上拉、输出高、16 mA）、SW_CTRL gpio18/19、`wlan-host-sol` gpio202、`wlan-dev-sol` gpio203；BT_EN 在 PMIC GPIO（`qcom,bt-en-gpio = <0x707 3 0>`，pm8550ve 的 gpio）。
- 电源：PMU 映射 `VDDD_AON_0P9/VDDA_RFA_0P9/VDDD_WLCX/BTCX/BTCMX → s5f`、`VDDA_RFA_1P9 → s3g`、`VDDA_RFA_1P3/VDDA_PCIE_1P2/0P9 → s7i`、`VDDD_WLMX_0P9 → s4d`；另有 `vdd-wlan-io`（ldof3）、`vdd-wlan-io12`（ldof2）、`vdd-wlan-ant-share`（ldob8）；PDC 设置靠 AOSS QMP 消息 `{class: wlan_pdc, ss: rf, res: s5f.m, enable: 1}` 等四条（`qcom,qmp`），可用 Adreno830 的 `Sm8750AossQmp` 发。
- SMMU：`iommu-map` RID 0 → `0x1400`、RID `0x100` → `0x1401`（apps SMMU `0x15000000`）；13T 上 HLOS 能改 apps SMMU 的 S2CR（触摸的 `0xa3`、扬声器设计的 `0x1001` 直通同一做法），所以 DMA 可行。
- MSI：`msi-map = <0 0x85 0x1400 1 0x100 0x85 0x1401 1>`，`0x85` 是 `gic_its: msi-controller@0x16040000`；PCIe 自带的 `qcom,pci-msi`（`0x17110040`，SPI 0x300 起）是 `disabled`。ArmCpu 没有 ITS/LPI → 先用 1 ms 轮询（sheng 现成），中断要另做。

peach 相对 WCN7850 还缺：Ath12kWifi 的硬件定义与 QMI 差异（`路线图.md` 第 158 行：renhiyama/pagani-linux 约 600 行 QMI 改动加 pwrseq 上电时序，GPL，只能当事实）；固件与 board data（`amss`/`m3`/`bdwlan`/`regdb` 一类，在安卓 vendor 分区，本机备份没有，要早上 adb 只读取，私有文件不进仓库）；固件载体（sheng 写进 `oops` 分区；13T 不能写分区，可改放 Data 种子或启动镜像负载）；MAC（`use-nv-mac`）；SM8750 的 PCIe CESTA。

结论：今晚能写的是只读的第 1 阶段探针（读 UEFI/ABL 留下的 PCIe0、GDSC、时钟、引脚、PMU 电源状态，不上电），B 级；能扫描、能连网是多周（C）。拉 Ath12kWifi 源码要用户同意。

### 6.2 音频

| sheng | 13T 对应 | 状态 |
| --- | --- | --- |
| `package/ShengGpr.c/.h` | `PaganiSpeaker/PaganiGpr.c/.h` | 已移植，protocol 200 项过 |
| `package/ShengAudioReach.c/.h` | `PaganiSpeaker/PaganiReach.c/.h`（32 bit 设备格式、SD 线可选、PRM 端口；去掉麦克风图） | 已移植 |
| `package/ShengCs35l43.c/.h`（六颗 CS35L43） | `PaganiSpeaker/Tfa9873.c/.h`（两颗 TFA9873，0x34/0x35） | 重写，amp 16594 项过 |
| `package/GeniI2c.c/.h` | `PaganiSpeaker/GeniI2c.c`（SE4 400 kHz）、`PaganiBattery/BatteryI2c.c`（SE1 100 kHz） | 已移植 |
| `package/AudioProbe.cpp` 扬声器部分 | `PaganiSpeaker/SpeakerCore.c`、`SpeakerHw.c`、`SpeakerBackend.c`、`SpeakerTone.c`、`PaganiSpeaker.cpp` | 已移植，未接线 |
| `package/AudioProbe.cpp` 麦克风部分（约第 760–1000 行：TX SoundWire 命令 FIFO、WCD938x、AMIC3/micbias3/ADC2、TX decimator 0、codec DMA TX3） | 无 | **未移植**；13T 是 WCD939x（`wcd939x-tx-slave`，DT 32308–32347）在 VA SoundWire 主控 `0x7630000` 上，路径与寄存器都不同 |
| `package/ShengAudioService.cpp`、`ShengAudioAbi.h`、`hal/ShengAudioPlugIn.c` | `PaganiAudio/PaganiAudioService.cpp`、`PaganiAudioAbi.h`、`hal/PaganiAudioPlugIn.c` | 已移植并上机（静音后端） |

扬声器真出声还要：ADSP 真机启动（从未跑过）、APM 就绪、建图、PRM 时钟，以及 iOS 路由层让我们的设备有输出端口（D 包 `out=0 ports=0`），见 `a830-inputs/haptics/sound/FEASIBILITY.md` 与音量工作流。

### 6.3 电池

`package/ShengBattery.cpp`、`ShengBattmgr.c/.h`、`BatteryProbe.cpp`、`GeniI2c.c` → `PaganiBattery`（属性映射、请求周期移植；OPLUS 属性编号比小米少一位，首轮用 12/13 号属性探测布局；新增 NFG8011B 直读，不要 DSP 也能出电量）。已接进内核、默认关；要上机验证 `[battery] start … pmic=1` → `i2c up` → `gauge addr=0x55 rc=0 type=0x4231 NFG8011B` → `state published pct=…`。不开 ADSP 时没人喂充电器看门狗，可能停充（README 限制一节）。

### 6.4 ADSP

`package/AdspProbe.cpp`、`AdspFirmware.c`、`FatVolume.c`、`QcomGlink/Mdt/Qrtr/Smem/Smp2p.c`、`ShengQrtr.c`、`ShengAdsp.h` → `PaganiAdsp`（13T 事实：SMEM `0x81d00000`、固件 `modem_a` LUN4 第 4870 块起的 FAT、PAS 0x24 dtb 与 1 主镜像、载入区 `0x9ed80000`/`0x9ee00000`、IPCC `0x406000` SPI 229、TCSR 互斥 3、AOSS QMP `load_state`、lcx/lmx 384 级）。SCM/QMP/RPMh 复用 Adreno830 平台层（`sm8750_scm_owner`、`sm8750_aoss_qmp_owner`、`sm8750_rpmh_drv2_owner` 三把弱锁字）。限制：无子系统重启、无看门狗中断、无互连投票（安卓投 `rproc_ddr`/`crypto_ddr`）。传感器（SSC 与 ADSP 合并在 `adspslpi_region@9ee00000`）、充电、扬声器都排在它后面。

### 6.5 不必再移植的

- `QcomRpmh.c`、`QcomQmp.c`、`QcomScm.h` → Adreno830 `platform/sm8750/Sm8750Rpmh/AossQmp/Scm`；`SmmuBypass.h` → `Sm8750Platform/SmmuTakeover.h` 与各驱动的直通；`CpuFreqProbe.cpp`（EPSS LUT）→ 13T 用 SCMI PERF（`PaganiArmCpu/ScmiPerf.h`）；`BootLog.c`/`BootDiagExport.c` → BootProgress ramoops；`TouchBoard.h`/`TouchProbe.cpp` → PaganiInput。

## 7. 今晚可做与工作量

| 项 | 内容 | 工作量 | 级别 | 备注 |
| --- | --- | --- | --- | --- |
| PaganiSpeaker 接线 | `scripts/build-pagani-speaker.py`（照 `build-pagani-battery.py`，`--append-to libAdreno830.a`）、`Test/check-pagani-speaker-host.py`（把第 8 节的手工命令收进去）、Stage7Probe 弱默认 + 在 `pagani_audio_start()` 后调用、`components.toml` 条目、`board.toml` include 与 archives、Sm8750Platform 的 `0x52008` 共享加锁助手（触摸、电池、扬声器三方都改用）、README | 3–4 小时 | A（代码与镜像）/B（出声要多轮上机） | 默认关；不影响现有镜像行为 |
| 电池镜像 | 用 D 包配方加 `A830_BATTERY=1`（`pagani-battery=1`）重打（B 包内核太旧） | 20 分钟（含构建） | A | 只读电量计 + 一次 0x3e←0x0001 写，硬件审查结论“可以上机” |
| ADSP 分段镜像 | D 包 + `pagani-adsp=1 pagani-adsp-stop=3`（只读三步）→ `=7` → 全程 | 20 分钟 | A/B | 第 6 步起会写 AOSS/RPMh/PAS；要求用户在场、带定时复位 |
| 震动干跑镜像 | D 包 + `pagani-haptics=2`（只读发现与寄存器转储） | 20 分钟 | A | 震动工作流负责代码；iOS 触感链路另算 |
| CPU 空闲镜像 | D 包 + `pagani-idle=1`（自测带看门狗） | 20 分钟 | A/B | 代码与主机测试已有，从未上机 |
| 持久窗口（小） | `sheng-data-persist=1` 只读核对 `unixnu` 分区末尾窗口；`=2` 才写 | 代码已有 | B | 写 UFS 需用户同意；容量 ≤ 分区 1/4（约 16 GiB），大容量走 PaganiPersistentData（C） |
| Wi-Fi 第 1 阶段只读探针 | 13T 版 WifiProbe 阶段 1（只读 PCIe0/GDSC/时钟/引脚/PMU） | 半天 | B | 能连网是多周（C）；Ath12kWifi 源码要先拉 |
| 麦克风 | WCD939x + VA SoundWire + TX/VA 宏 + 采集图 | 1–2 周 | C | 依赖 ADSP 与扬声器链路先通 |
| 测试修正 | `NativeRamBlock/Test/sparse.cpp` 的 2 GiB 断言改成按 1 TiB 上限 | 10 分钟 | A | 纯测试 |

## 8. 主机测试记录（2026-10-31 07:45–08:10，`code-run-host-tests.sh`）

| 项 | 结果 |
| --- | --- |
| BootProgress `ramoops.c`、`crash.c`、`gunyah-wdog.c`、`console.c` | 全过（含未提交的 `pagani-reset-seq`） |
| NativeMobileDisplay 8 项 | 全过 |
| NativeRamBlock direct、persist、seeddisk | 过；packed-hash/seed-identity 对 D 包种子输出 `aee73c27…`（与 `.json` 一致） |
| NativeRamBlock `sparse.cpp` | **失败**：`Assertion failed: (!ramPackedLayout(bad.data(),bad.size(),&logical))`，`sparse.cpp:30`（2 GiB 断言 vs 1 TiB 上限，导入时就不一致） |
| NativeUfs `check_root_path_host.py --gpt-dir`、`check_legacy_descriptors.py` | 过（5 组 + 14 项） |
| NativeKeyStoreEvents | 过 |
| PaganiArmCpu spi-gate / scmi-perf / psci-idle | 15 / 2181373 / 2634553 项全过 |
| PaganiPanel panel-power | 314 项过 |
| PaganiInput + QcomGeniSpi（DEV `test-peripherals.py --tree TREE`） | 过 |
| PaganiSpeaker tone/protocol/amp/hw/core（手工编译，hw 需链 `Tfa9873.c`） | 26675/200/16594/240/269405 项全过 |
| PaganiAdsp | 全过（含手机 GPT） |
| PaganiAudio | 全过（插件 CDHash `c88be410…`） |
| PaganiBattery | 全过（内核归档 7 个对象） |
| PaganiPersistentData `Test/run.sh` | ALL PASS |
| PaganiDiag `Test/run.sh build` | 过（CDHash `7301a805…`）；`patch` 环境原因失败；`trust` 跳过 |
| PaganiHaptics | 全过（07:5x 快照） |
| PaganiDarwinBoot `test-packed-ramdisk.py`、`test-adt.py` | 过 |
| Adreno830 `tests/run-host-tests.py` | 44 过、0 失败、1 跳过（`test_sha256` 无 `--firmware`），内核对象 51 个 |
| `工具/test-phone-island-layout.py`、`test-phone-keyboard-layouts.py` | 32 OK、15 OK（1 跳过） |
| `工具/test-phone-keyboard-trace.py` | 单元检查过；补丁检查环境原因失败 |

## 9. 按功能看代码现状（与结构化结果一致）

级别：A 今晚能出源码 + 主机测试 + 镜像；B 今晚能写代码但要真机多轮；C 多周；D 这块硬件上做不到（缺苹果基带、安全隔区或 ISP）。

| 功能 | 现有代码 | 真机现状 | 缺什么 | 级别 |
| --- | --- | --- | --- | --- |
| 震动 | PaganiHaptics（07:45 接进组件、板级、Stage7Probe，默认关；主机检查过） | 没震动；从未上机 | 合并 `PaganiPeripherals.h` 片段、重编；iOS 触感链路（震动工作流） | 内核 A（干跑镜像）/系统触感 B–C |
| 扬声器 | PaganiAudio（上机）、PaganiSpeaker（未接线）、PaganiAdsp（默认关） | 静音设备；`out=0 ports=0` | 扬声器接线与 `0x52008` 锁；ADSP→APM→建图→测试音的真机轮次；iOS 路由 | B |
| 麦克风 | 只有 sheng 参考（WCD938x/TX SWR） | 采集全零 | WCD939x、VA SoundWire、采集图 | C |
| 音量键/音量条 | PaganiInput（键已上报）、PaganiAudio 路由探针 | 键有、条不出（`vcsp=0/0 vc=0`） | 路由层（音量工作流），不改内核 | A–B |
| 默认壁纸 | 壁纸叠加层与补丁 | 叠加已挂、仍黑 | 数据修正（壁纸工作流：HEIC/清单） | A |
| 小组件 | Metal 插件、种子 | 画成黑的，无崩溃 | 小组件工作流 | B |
| 查找 | Metal 插件 | VectorKit 空纹理 SIGABRT | 插件纹理修复（查找工作流） | A–B |
| 键盘卡顿 | 键盘布局叠加、F2 修补、键盘偏好 | A 包极卡（疑为 `pagani-kbtrace=3`），C/D 未单独反馈 | 先确认 C/D 下是否还卡；候选词服务 | A–B |
| 电池/充电 | PaganiBattery（默认关）、PaganiAdsp | 电量不准；从未上机 | 当前内核的 `pagani-battery=1` 镜像；充电状态要 ADSP | A（电量）/B（充电） |
| 传感器 | 只有 PaganiAdsp 的 QRTR 与服务定位 | 无 | ADSP、SSC 客户端（QMI/SEE）、iOS 期望的 HID 传感器服务 | C |
| 相机 | 无 | 无 | CAMSS/ISP 与苹果相机栈 | D |
| 手电筒 | SPMI 写通道可借 PaganiHaptics（`HapticsSpmi.c` 白名单写） | 无 | PMIC SID 1 `0xee00` 闪光灯写；iOS 手电筒走相机栈 | 内核 B/系统入口 C |
| 蜂窝 | 无 | 无 | 基带启动与 CommCenter 兼容层 | D |
| Wi-Fi | sheng 三层（Ath12kWifi 源码本机缺）、WifiNet 可复用 | 无 | 见 6.1 | C（只读探针 B） |
| 蓝牙 | 无 | 无（`com.apple.bluetoothd` 在启动策略里禁用） | UART HCI、固件下载、bluetoothd 兼容 | C–D |
| 定位 | 无（routined 已禁用防崩） | 无 | 调制解调器 GNSS 与 locationd 兼容；Wi-Fi 定位要先有 Wi-Fi | D（GNSS）/C（Wi-Fi 定位） |
| NFC | 无 | 无 | Apple 的 NFC 控制器与安全元件 | D |
| 指纹 | 无 | 无 | Touch ID 需要安全隔区 | D |
| USB | 无 | iOS 下不工作 | DWC3 设备模式 + eUSB2 PHY + IOUSBDeviceFamily 兼容 | C |
| 睡眠/省电 | `pagani-idle`、`pagani-screenoff` 位 0–3、GPU slumber/IFPC、`pagani-idle-flush.patch` | 全部默认关、未上机；`sleep-disabled=1` | 逐项单变量上机；真正挂起未实现 | B（空闲、熄屏）/C（挂起） |
| CPU 6+2 调度 | PaganiArmCpu（对称 8 核、分域调频） | 对称调度 | 非 AMP 的 BCM2837 内核配置；要 AMP 调度器与簇拓扑 | C |
| 持久存储 | NativeUfs `sheng-data-persist` 窗口 + NativeRamBlock 覆盖层；PaganiPersistentData（提案） | 每次开机清空 | 写 UFS 要用户同意；大容量要安卓端建锁定文件 | B（≤16 GiB）/C（128–256 GiB） |
| 系统功能 | 控制中心、预装应用叠加已生效；诊断守护进程未跑起来 | 控制中心与预装应用已确认 | 16 GiB VA 光圈（`pagani-gpu-va=16`）等排队项 | A–B |
