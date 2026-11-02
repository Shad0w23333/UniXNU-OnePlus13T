# UniXNU-Pagani_SM8750-ios27

一加 13T 的实验性 iPadOS 27 设备层。主线 `UniXNU-Pagani_SM8750-gpu` 不读这里的任何文件，这里也不改主线。

状态：**N0 已完成，内核和启动镜像都构建出来了，没有上过机。** 构建记录见 `../../调研/n0-build.md`，整体计划见 `../../PLAN.md`。

## 内容

| 路径 | 来源 | 与主线的差别 |
| --- | --- | --- |
| `board.toml` | 主线 `board.toml`（SHA-256 `1bf946a0`）加 sheng 27 配方 | 组件收缩到里程碑 N1、N2；ADT 用虚拟平台；根窗在分区内 10 GiB |
| `configs/oneplus13t_ipados27_defconfig` | sheng 27 配方 | 根卷走源码存储，保留 BootProgress |
| `dts/oneplus13t.dts` | 主线 `dts/oneplus13t.dts`（SHA-256 `2b2ea6ba`） | 加 `/arm-io/wlan` 节点 |
| `patches/xnu-12377/` | 移植，见 `../../调研/xnu-12377.md` | 12377 版设备补丁 |

## 构建状态

| 项 | 状态 |
| --- | --- |
| 框架 `../../UniXNU-27` | 上游 `dec111b` 加社区组件和构建脚本，差异在 `../../framework/` |
| `XnuPrelink`、`DarwinBoot` 等上游模块 | 已取。`XnuPrelink` 是 `b1888c1` 加两条审定记录 |
| 24A446 的 kernelcache、DeviceTree、信任缓存 | 已取，在 `../../UniXNU-27/out/ipsw/24A446/` |
| `build-pagani-source-storage.py` | 已写，在 `../../UniXNU-27/scripts/` |
| 根策略 `scripts/policies/ipados-27.0.1-24A446/` | 暂用 sheng 的公开签名头和公钥。长期换成自己的 owner 密钥，要先有 OS 镜像 |
| Data 种子 | N1 用占位种子。N2 换真种子，要改 `CONFIG_XNU_OWNER_DATA_VOLUME_UUID` 并重编内核 |
| `oneplus13t` 固件 | 沿用主线 UEFI，`PaganiDarwinBoot` 换 27 副本，已编译 |
| 驱动副本 | L 集合已在 `../../drivers/`、`../../kits/`。`NativeMobileDisplay` 等 S 集合还没做，接入时恢复 `prelink-regression.json` 的 `required_original_branches` |

构建内核时用带 `ld-classic` 的 Xcode：上游 `arm64e-threaded-starts.patch` 要它，Xcode 27 系列没有。

```bash
export DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer
```

## 不要做的事

- 不要 `fastboot flash unixnu`。那会从分区开头覆盖 17 的根卷。
- 不要在主线内核上用这里的启动参数。`pagani-root-base-mib` 只有 27 的 `NativeUfs` 副本认。
- 不要把 24A446 的固件、kernelcache、根镜像放进任何被跟踪的路径。
