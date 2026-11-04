# UniXNU-OnePlus13T

把 XNU 内核和 iPadOS 17.7.11（21H461）跑在**一加 13T**（PKX110，骁龙 8 至尊版 SM8750，Adreno 830）上的全部源码，欢迎魔改。

基于 [UniXNU](https://github.com/UniXNU/UniXNU)，在 [IssacL04](https://github.com/IssacL04) 的 Pagani 移植上继续开发。系统身份是 iPhone 15 Pro（`iPhone16,1`），GPU 走 Metal → metal2vulkan → Turnip → 自写的 Adreno 830 驱动。

## 现状

| 状态 | 内容 |
| --- | --- |
| 真机能用 | 开机进桌面、触摸、Adreno 830 GPU 加速、系统应用、小组件、控制中心、电量显示、充电（硬件自己在充） |
| 已写好，未上真机 | 打字卡顿修复、声音和震动修复、WiFi、蓝牙、亮度、相机测试图、USB 探针、8 GiB 内存窗口、内存压缩器 |
| 做不到 | 蜂窝网络、GPS、Face ID、App Store |

数据卷是内存盘，重启后写进去的东西全部清空。详细进度看 [`docs/测试清单.md`](docs/测试清单.md) 和 [`Adreno830/docs/PROGRESS.md`](Adreno830/docs/PROGRESS.md)。

## 目录

| 目录 | 内容 |
| --- | --- |
| `Adreno830/` | Adreno 830 内核 GPU 驱动（设计见 `docs/ARCHITECTURE.md`） |
| `UniXNU-Pagani-Drivers/drivers/` | 外设驱动：显示、触摸、UFS、WiFi、蓝牙、相机、ADSP、声音、震动、电池、卡顿探针等，每个都有 README 和主机测试 |
| `UniXNU-Pagani_SM8750/` | 设备层：`board.toml`、XNU 补丁 `patches/xnu/` |
| `UniXNU-sm8750-common/` | SM8750 公共层 |
| `UniXNU/` | 构建器（`./unixnu`）和 Metal 插件源码 |
| `tools/` | 打包、种子、上机回路和日志判读脚本（`tools/gpu-test-loop/`） |
| `build-userspace/` | 用户态构建脚本和各项分析报告（`a830-inputs/round-1101/` 是最新一轮） |
| `ios27-experiment/` | iPadOS 27 实验路线，停在主机构建 |
| `patches/` | UEFI（Mu-Silicium）的改动 |
| `docs/` | 功能盘点、测试清单、路线图 |

## 各组件仓库（带完整提交历史，按 UniXNU 官方结构）

这里是方便浏览的合集。要改哪一块、要给上游提 PR，就去对应的组件仓库：

| 仓库 | 内容 |
| --- | --- |
| [Shad0w23333/UniXNU-Pagani_SM8750](https://github.com/Shad0w23333/UniXNU-Pagani_SM8750) | 设备仓库（`board.toml`、`porting.toml`、XNU 补丁），UniXNU 官网据此生成一加 13T 页面 |
| [Shad0w23333/Adreno830](https://github.com/Shad0w23333/Adreno830) | Adreno 830 GPU 驱动 |
| [Shad0w23333/UniXNU-Pagani-Drivers](https://github.com/Shad0w23333/UniXNU-Pagani-Drivers) | 外设驱动 |
| [Shad0w23333/UniXNU-sm8750-common](https://github.com/Shad0w23333/UniXNU-sm8750-common) | SM8750 公共层 |
| [Shad0w23333/UniXNU_Enc3l](https://github.com/Shad0w23333/UniXNU_Enc3l) | 构建器，组件目录已指向上面这些仓库 |

前四个衍生自 [IssacL04](https://github.com/IssacL04) 的 Pagani 系列，构建器衍生自 UniXNU。

## 怎么用（简版）

**准备**：Apple 芯片的 Mac，Xcode 命令行工具，Python 3，`brew install llvm`，[ipsw](https://github.com/blacktop/ipsw)，adb 和 fastboot；一加 13T 已解锁 BL。

1. **编内核**（约 5 分钟，产物在 `UniXNU/out-gpu/`）：

   ```bash
   cd UniXNU
   export PATH="$PATH:/opt/homebrew/opt/llvm/bin"
   PD=../UniXNU-Pagani-Drivers/drivers
   M=""; for d in Stage7Probe BootProgress NativeMobileDisplay PaganiArmCpu PaganiInput PaganiPanel NativeUfs PaganiAdsp PaganiAudio PaganiBattery PaganiHaptics PaganiSpeaker PaganiSensors PaganiWifi PaganiBt PaganiCamera PaganiTorch NativeKeyStoreEvents PaganiArmDTPlatform NativeRamBlock; do M="$M --local-module driver:$d=$PD/$d"; done
   ./unixnu kernel -D ../UniXNU-Pagani_SM8750 --out out-gpu --local-module driver:Adreno830=../Adreno830 $M
   ```

   构建器会从苹果官网下载 21H461 的 IPSW。构建器本身的用法看 `UniXNU/docs/`。

2. **做 Data 种子和镜像**：种子里有苹果的系统文件，所以不随仓库提供，要用 `tools/make-phone-seed.py`、`tools/make-a830-final-seed.sh` 从你自己的 IPSW 生成。之后用 `tools/make-a830-layout.sh` 或 `build-userspace/a830-inputs/fullrun/make-stage16.sh` 打镜像。

3. **上机，只用临时启动，不要刷分区**：

   ```bash
   python3 tools/gpu-test-loop/gpu-loop.py --image <镜像.img> --serial <你的手机序列号> --android-timeout 1200 --yes
   ```

   它会重启到 fastboot、临时启动镜像，等定时复位回到安卓后读回日志。日志判读用 `tools/gpu-test-loop/stall-report.py`、`wifi-rounds.py`、`adsp-rounds.py`。

**注意**

- 风险自担。这台手机的闪存 `bDataReliability` 是 0，写入途中掉电最坏会丢整个逻辑单元，包括安卓数据。只用 `fastboot boot` 临时启动，先备份安卓数据。
- 脚本里写死了作者机器的路径 `/Users/xiaochoumao/unixnu-13t`：设环境变量 `UNIXNU_TOP`，或者改脚本开头的路径。
- 代码里的设备序列号换成了占位符 `SERIAL_13T`，换成你自己的。
- 苹果文件、高通专有固件、种子、镜像、手机日志都不在仓库里。固件运行时从手机分区原地读取。

## 从哪里开始魔改

- GPU：`Adreno830/`，先读 `docs/ARCHITECTURE.md`。
- 外设：`UniXNU-Pagani-Drivers/drivers/<驱动>/README.md`，新功能都默认关闭，用启动参数分级打开。
- 设备配置和启动参数：`UniXNU-Pagani_SM8750/board.toml`。
- 下一步要上真机验证什么、按什么顺序：`docs/测试清单.md`。

## 许可证

各目录保留原作者的许可证文件和文件头声明。来源有 UniXNU、IssacL04 的 Pagani 系列、sheng（BSD-2-Clause-Patent）、Adreno740 和 AdrenoMetal（MIT）、Mu-Silicium。Linux、KGSL 等 GPL 代码只参考事实，没有复制。我们新写、没有单独声明许可证的部分按 MIT 授权，见 [`LICENSE`](LICENSE)。
