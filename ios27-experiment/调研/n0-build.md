# N0 主机构建记录

时间：2026-11-01。下载了 D0、D1，没有下载 D2、D3、D4。手机全程没动。

证据等级：**【实测】** 本次跑出来的；**【读码】** 读文件确认；**【推断】** 没有验证。

## 结果

| 产物 | 值 |
| --- | --- |
| `kernel.macho` | 95,928,320 字节，`MH_FILESET`，arm64e，SHA-256 `01032ef3` |
| 启动镜像 | `images/boot-27-N1.img`，36,626,432 字节，SHA-256 `013af34f`，Android 头 v4 |
| 载荷卷 | 192 MiB，已用约 96 MB。与主线相同，嵌在内核段里 |
| 启动参数 | 433 字节，`DarwinBoot` 上限 1023 |
| 固件 | `inputs/UEFI-Pagani_SM8750` 编译，带 27 版 `PaganiDarwinBoot` |

fileset 配对【实测】：

| 项 | 我们 | sheng 基线 |
| --- | --- | --- |
| 打桩引用 | 24 | 24，还原滑动量后逐地址一致 |
| 未解析闭包引用 | 2 | 2，逐地址一致 |
| 配上的函数 | 17039/21792 | 未记录 |
| kext 面向的目标 | 2799/3046 | 未记录 |

配对质量与 sheng 同一水平。这两组缺口是上游现状，配不上的函数运行到才 panic。

报告里 `loadable`、`runtime_qualified` 是 `false`，这是 `fileset_emit.py` 写死的，等真机证据才改，不代表不能启动。`mapping_complete` 是 `false`，因为有 2 个未解析，上游同样。

## 上机前静态检查【实测】

| 检查 | 结果 |
| --- | --- |
| kernelcache 哈希 | 等于上游 `38e6d9fa`，sheng 的 ABI 数据可以直接用 |
| `IOBlockStorageDevice.h`、`.cpp` | 337 与移植时用的版本零差异，这条风险解除 |
| 生产解析器解析真实 fileset | `test-kernel-parser.py` 通过，反例 11 个全部拒绝 |
| `/chosen/random-seed` | 256 字节 |
| 根卷匹配 | `2F8E6EED-…`，24A446 的 OS 卷 |
| `/arm-io/wlan`、八个 CPU 节点 | 都在 |
| `vmm-present`、`kern.maxvnodes` | 1、99328 |
| `/chosen/memory-map` | `PackedRAMDisk`、`RootPolicies`、`TrustCache` |
| 写保护 | `NATIVE_UFS_READ_ONLY=1`。写路径只有日志窗口（`NATIVE_UFS_LOG_WRITES=1`），要 `sheng-log=1` 才启动，启动参数没有它，也没有 `sheng-data-persist`。固件 UEFI 变量是内存模拟（`PcdEmuVariableNvModeEnable=TRUE`） |
| 补丁层 | 114 个，`git apply` 零模糊 |
| 根基址差分 | 基址 0 与主线一致，20 万个清单输入、200 万组判定 |

## 中间遇到的问题

都是构建阶段就暴露、已修掉的。

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| `git apply --3way` 缺 blob | `--filter=blob:none` 的部分克隆没有基线 | 改用 `git merge-file` 逐文件三方合并，7 处冲突逐处解决 |
| `flash_command` 打印 `fastboot flash unixnu` | 合并时发现，照做会覆盖 17 | 加 `[userdata] base_mib`，设了就只输出带 `seek` 的 `dd` |
| LTO 对照内核缺 `___ubsan_handle_*` | 我们的 `ArmDTPlatform` 有 RTC 代码，用 DEVELOPMENT 记录编译，RELEASE 没有 ubsan 运行时 | 对照内核不运行，按上游做法打桩 |
| `llvm-objdump` 找不到 | 这一步 PATH 没有 Homebrew LLVM | 脚本里回退到绝对路径 |
| `Chained fixup imports are not supported` | `adreno830_cpu_hint` 的弱默认是可见的，`BootProgress` 取了它的地址 | 改成隐藏可见性，只改 27 副本 |
| 回归策略多 6 处桩 | `thread_tid`、`thread_set_allocation_name` 没配上，共 6 个调用点 | 反汇编对照源码，逻辑逐句吻合，写入审定记录 |
| 回归策略要求 2 条跳转 | 属于显示和 HID 驱动，这个配置没带 | 设备层策略清空这一项，桩和缺失的上限不变。N3 要恢复 |
| `nm` 表头让探针脚本崩溃 | 我从 sheng 抄了 `panic_screen`，主线没开 | 删掉这一项，panic 走 BootProgress |
| 载荷卷 224 MiB | 我拍的，超过主线验证过的大小 | 改回 192 MiB |

## 用到的取舍

- **根策略用 sheng 的公钥和签名头。** 同一份 24A446 镜像，头是公开的，不用 D2 就能过构建。信任锚因此是上游的 owner 密钥。对实验够用，长期应换成自己的。
- **Data 用占位种子。** `scripts/make-n1-placeholder-seed.py` 生成的空 APFS，不含 Apple 内容。N1 走不到 Data 挂载。N2 必须换真种子并重编内核，卷 UUID 编进了内核。
- **信任缓存**由 D0 的三个模块组成，共 3711 条，`inputs/n1-ipsw-trust.bundle`。

## 复现

都在 `UniXNU-27/` 下运行，机器相关的输入在 `unixnu.local.toml`。

```bash
export DEVELOPER_DIR=/Applications/Xcode-26.6.app/Contents/Developer
```

```bash
python3 ./unixnu defconfig oneplus13t_ipados27_defconfig -D ../device/UniXNU-Pagani_SM8750-ios27
```

```bash
python3 ./unixnu build kernel -D ../device/UniXNU-Pagani_SM8750-ios27 --kernelcache out/ipsw/24A446/kernelcache.decompressed -j 10
```

```bash
python3 ./unixnu build package
```

```bash
python3 ./unixnu build firmware
```

```bash
python3 ./unixnu build payload
```

```bash
python3 ./unixnu bootimg
```

这一轮 `build kernel` 每次 4 到 6 分钟。从 D0 下完到出镜像共 28 分钟，含上表各轮失败重来。只重做 fileset 用 `build prelink`，参数与 `build kernel` 相同，不带 `-j`。上游差异见 `framework/`。

## 还没验证的

全部运行时行为。这个镜像没有上过机。
