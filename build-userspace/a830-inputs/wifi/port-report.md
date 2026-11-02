# 13T Wi‑Fi 驱动移植报告（PaganiWifi）

> **2026-10-31 硬件审查后的更正（详见同目录 `review.md`）**：下文有四处已被审查推翻，以这里为准：
> ①第 3 节“DMA 掩码也已是 36 位，应更正为 36 位”不对：ath12k 流式 36 位、**一致性 32 位**，所以 DMA 窗口默认改到 4 GiB 以下的低端内存（`pagani-wifi-dmapa`，默认 0xc0000000）；
> ②第 2 节与第 6 节第 6 条“芯片侧写入默认 0x79”改成默认 0（与 cnss2 一致），0x79 留作第二轮；
> ③S7I 规则改了：读不到就什么电源都不投（第 202 步失败），读得到就总投高功率模式，电压和使能只在需要时才投；
> ④“系统存不住网络密码（没有钥匙包）”已过时：钥匙串现在同一次开机内可用，存不住的原因是 Data 是内存盘。
> **2026-11-01 再更正**：⑤wifid 不需要 `wlan` 节点，已经在跑；⑥校准后要主机重启，模式 OFF 后固件不会自己回来（`round-1101/wifi.md` 第 3.4 节）。

更新：2026-10-31（工作流步骤 wifi-port）。对象：一加 13T（PKX110 / SM8750，Gunyah HLOS），peach 芯片（PCIe `17cb:110e`，peach_v2，
WCN7880 家族），iPadOS 17.7.11（21H461）。依据：同目录 `facts.md`、`PaganiWifiTables.h`；移植来源：UniXNU-sheng `08cde20e`
（`package/WifiProbe.cpp`、`WifiPcieTables.h`、`MhiBoot.c`、`WifiNet.cpp`、`tests/mhi_boot_host.c`）与 Ath12kWifi `f6bf561`
（BSD-2-Clause-Patent，每个移植文件开头写明出处与提交）。

**一句话结论**：驱动、协议栈的 peach 条目、主机测试、内核参数构建和集成差异都已完成并通过本机验证；**没有任何一步在手机上跑过**，
每个阶段都要真机轮次。默认关闭：不给 `pagani-wifi=N` 时什么都不映射、不分配、不启动。

范围遵守：没有提交；没碰手机；没写 `UniXNU/out-gpu`（只读了编译记录，`libAdreno830.a` 是拷到草稿区的副本上做追加检查）；
共享集成文件一个没改（五个文件的 sha256 与开工时一致），改动全部以差异形式放在 `integrate/`；GPL 源码（Linux ath12k/MHI、cnss2、
pagani-linux）只取事实，没有抄代码；专有固件不进任何仓库。

## 1. 交付物

驱动目录：`/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiWifi/`（新目录，未提交）

| 文件 | 作用 | 来源 |
| --- | --- | --- |
| `LICENSE` | BSD-2-Clause-Patent | 同 PaganiAdsp |
| `PaganiWifiTables.h` | 13T 全部事实表（取自事实步骤，启动参数一节按本驱动的 0–6 阶段改写） | 事实步骤 |
| `WifiBringup.h/.cpp` | 阶段 1–4：勘察、上电、链路、MEM 窗口与 BAR0，可移植 C++，经寄存器回调运行 | sheng `wifiThread`/`bringUp`/`endpointProbe` |
| `WifiPmic.h/.c` | SPMI 仲裁器 v7：七路电源回读（只读观察通道），PMK8550 gpio3 睡眠时钟脚（属主核对后 sheng 的 5 次写） | sheng `pmicScan`/`pmicPrepare` |
| `MhiBoot.h/.c` | MHI BHI/BHIe 引导状态机（原样）+ 新增 `mhi_fw_layout`：peach 的 amss20.bin＝512 KiB 引导程序＋第二个 ELF，BHIe 只传后者 | sheng `MhiBoot.c` |
| `WifiMhiLoad.h/.c` | 阶段 5 自带 MHI 引导：缓冲区、BHIe 表、上下文（引导前与加载器复位后各写一次）、事件环应答、各阶段超时 | sheng `mhiBring`/`programContexts`/`eventAck` |
| `WifiDma.h/.c` | 端点 DMA 窗口分配器（按总线地址对齐到大小的二次幂，首页留给 MSI 目标） | sheng `arenaAlloc` |
| `WifiFirmware.h/.c`、`WifiFat.h/.c`、`FatVolume.h/.c` | 固件来源：先 modem_a/modem_b 的 FAT 就地读（NativeUfs），再 RAM Data 种子；FAT 读取器以 `pagani_wifi_fat_*` 名字编译（与 PaganiAdsp 那份同在一个归档里不冲突） | PaganiAdsp/sheng |
| `WifiUtil.h` | 日志行拼接（不越界） | PaganiAdsp `AdspUtil.h` |
| `PaganiWifi.h/.cpp` | 内核侧：线程与阶段、窗口、RPMh（经 Adreno830 平台层与 DRV‑2 锁字）、GCC 投票锁字、AOSS QMP、SMMU 直通流、DMA 窗口、iMSI（SPI 141）、芯片窗口 0x3278、配置空间、协议栈的 `lx_platform`/`nw_board` 回调、固件（含 VFS 读种子）、MAC 发布、网络类的延迟注册 | sheng `WifiProbe.cpp` |
| `PaganiWifiNet.cpp`、`WifiApple80211.h` | iOS 网络服务：en0、IO80211Controller/IO80211Interface/IONetworkStack 注册项、Apple80211 请求（SIOCGA80211/SIOCSA80211）、自动加入；请求布局抽到头文件供测试 | sheng `WifiNet.cpp` |
| `Ath12kWifi/` | Ath12kWifi `f6bf561` 副本 + `tools/peach.py`（由 `tools/prepare.sh` 调用）+ `nw_board_option` 弱默认；`ORIGIN.md` 说明来源与许可 | UniXNU/Ath12kWifi |
| `scripts/build-pagani-wifi.py` | 组件构建：重放构建树的 XNU 编译记录、零警告、审计 Mach-O、可选 `--with-ath12k [--fetch-deps]`、`--append-to libAdreno830.a` | PaganiBattery/PaganiAdsp 的脚本 + sheng `build-ath12k-wifi.py` |
| `Test/*` | 9 个主机测试程序与运行器 `check-pagani-wifi-host.py` | 新写（mhi_boot 移植自 sheng） |
| `README.md` | 阶段、启动参数、事实、构建、测试、需要真机的事项（英文，与同仓库其他驱动一致） | — |

本目录（`build-userspace/a830-inputs/wifi/`）新增：`port-report.md`（本文件）、`make-wifi-firmware-seed.md`（早晨把固件放进种子的步骤）、
`make-wifi-firmware-dir.py`（把早晨拉回的固件整理成种子布局并核对 sha256，已用合成数据演练）、`integrate/`（集成差异，见第 5 节）。

## 2. 阶段与启动参数（全部默认关）

| `pagani-wifi=` | 阶段 | 要点 |
| --- | --- | --- |
| 1 | 只读勘察 | GCC 投票、两个 GDSC、12 个分支 halt、8 个 WLAN 引脚、七路电源（SPMI 观察通道）、睡眠时钟脚；控制器只在 GDSC 开且时钟在跑时才读 |
| 2 | 上电 | 睡眠时钟脚 → 七路 RPMh 电源（cnss2 顺序，每路模式 7、电压、使能三次请求）→ PCIe NoC `SN4` 带宽票 → （可选）AOSS PDC 消息 → PERST# 低、CLKREQ# 功能 1、WLAN_EN 低 150 ms 再高、等 60 ms → GDSC 折叠票 0x5214c bit0/2 清零 → 两个 RCG、管道选择先停 TCXO、分支投票（持锁字 `sm8750_gcc_vote_owner`）、TCSR 参考时钟 → 两个 BCR 复位 |
| 3 | 链路 | PARF（RC，DBI 0x40000000，iATU 0x40001000）→ 设备树的 128 条 PHY 序列（第 126 条后把管道时钟切到 PHY）→ PHYSTATUS → BDF→SID（端点哈希 0x15 → 流 0x1401）→ 根端口 → PERST# 至少低 100 ms 后释放 → LTSSM → 1 s 内 L0 → 配置窗口 → 端点 ID 必须是 17cb:110e |
| 4 | 端点 | DMA 窗口（物理连续、2^36 以下、页属性改写合并）→ apps_smmu 给流 0x1401 一个 VA64 直通 bank（SoC 套件的 `SmmuTakeover.h` 规划器）→ iATU 区 1 MEM 窗口 → BAR0（2 MiB 时落在 0x40400000）→ 映射 BAR0 → MHI 寄存器、BHI 执行环境、经 0x3278 窗口读 TCSR_SOC_HW_VERSION |
| 5 | 固件＋自带 MHI 引导 | 等 amss.bin（最多 300 s）→ 芯片侧上电写入（位掩码）→ iMSI 或轮询 → 引导到任务模式（诊断用，不启动协议栈） |
| 6 | 协议栈＋网络服务 | 发布 `/chosen/mac-address-wifi0` → 预读固件 → `nw_wifi_start`（协议栈自己做 MHI/QMI）→ 有站点接口后注册网络类并起 en0 |

其他开关：`-stop`（停在某步）、`-gen`（链路速率上限，默认 2）、`-dma`（窗口 MiB，默认 48）、`-msi`（0 轮询）、`-sleepclk`、`-s7i`、
`-pdc`、`-fwpart`、`-fwdir`、`-bdf`、`-chipreset`（阶段 5，默认 0x79 不含热复位）、协议栈的 `-m3`/`-tme`/`-aux`/`-qdss`/`-cbc`/
`-features`/`-boardkib`、`-aj`。完整表见驱动 README。每一步失败都写 `pagani_wifi_status[0]`（负步号）、`[1]`（错误码）和一行
`[wifi] FAIL step=… err=…`（BootProgress ramoops 的 U 记录），线程随即停住，绝不 panic。

**S7I 规则**：S7I 同时是 UFS vccq 的上级。只在 PMIC 回读低于 1312 mV 时才投票（往上抬），已经≥1312 mV 且开着就不碰，读不到就完全不投
（`pagani-wifi-s7i=1` 才强制）。所有电源都不降压、不关闭。

## 3. Ath12kWifi 的 peach 条目（`Ath12kWifi/tools/peach.py`）

关键事实：Linux v7.0.14 的 ath12k 里 **QCC2072 条目已经具备 peach 需要的大部分东西**（窗口寄存器 0x3278、QCC2072 的 HAL 寄存器与紧凑 RX
描述符、WCN7850 的 CE/MHI/WMI、QMI 实例 1、内部睡眠时钟、AUX 微码），DMA 掩码也已是 36 位（`pci.c` 的 `ATH12K_PCI_DMA_MASK 36`；
facts.md 第 7 节表里写的“ath12k 32 位”应更正为 36 位，与 cnss2 一致）。所以做法是：

1. 在构建时生成的 `wifi7/pci.c` 副本里加 `17cb:110e`，探测时并入 QCC2072 分支（【猜测】该条目其余参数也适用）。
2. 只对 0x110e，在生成的 `qmi.c` 副本里加：M3 开关（cnss2 的 phy_ucode20.elf / pagani-linux 的空 M3）、目标能力之后的 TME‑Lite 补丁
   下载（QMI 0x5b，失败只记日志）、AUX 失败不致命、QDSS 配置下载（QMI 0x44，默认关）、特性位覆盖、BDF 大小上限、第一次 FW_READY 时的
   冷启动校准（CAL 报告 0x26 → 模式 7 → 等 CAL_DONE 0x3e，60 s → 模式 OFF → 等第二次 FW_READY 进任务模式；失败就直接试任务模式）。
   每一项都经 `nw_board_option` 由 `pagani-wifi-<名字>` 控制。
3. 仓库里只有修改标记和新写的代码（同原有 prepare.sh 的做法），没有 ath12k 代码；Linux 源码在构建时拉取到 `deps/`（被忽略）。

验证：注入代码在 `-Wall` 下零告警；WCN7850 路径的整栈模拟（组件自带 `tests/stack_sim.c`）在原版与修改版上输出逐行相同（只有代码地址不同）。
**peach 的 QMI 流程没有任何模拟或实机验证**（组件的模拟器只模拟 WCN7850 的寄存器面）。

## 4. 验证结果（本机）

主机测试（`python3 Test/check-pagani-wifi-host.py`，全部 `-Wall -Wextra -Werror` 加 ASan/UBSan）：

| 程序 | 检查数 | 内容 |
| --- | --- | --- |
| tables | 通过 | 事实表一致性（PHY 128 条、BDF 哈希 0x15、分支、电源范围、启动参数名），摘要 912fb297 与事实步骤一致 |
| mhi_boot | 424 | sheng 原测试 + peach 固件布局（ELF 在 512 KiB 处、错一字节、过短、空文件、拆分后的 BHIe 表） |
| mhi_load | 620 | 引导 ROM/加载器模型：peach 拆分镜像与 WCN7850 单镜像的 BHI/BHIe 内容逐字节核对、上下文先后、36 位地址范围、事件环应答、过大、窗口不足、无 READY、RDDM |
| dma | 4,160,981 | 对齐、保留页、清零、合并、耗尽、2 万轮随机分配对照影子表 |
| pmic | 296 | SPMI 仲裁器模型：电源回读、睡眠时钟脚 5 次写的顺序与值、非属主/错误子类型/非 v7/被拒都不写 |
| firmware | 244 | mtools 做的 FAT16（4 KiB 扇区）与 FAT32 卷＋种子目录：名字映射、目录回退、分区优先、join.txt 只认种子、未挂载“稍后再试”、modem_b 回退、非 FAT、读错误重试、超大文件 |
| bringup | 8,757 | 模拟的 PCIe0 一角：电源顺序与三次请求、S7I 四种情况、引脚时序、折叠票只动两位、投票寄存器保留他人位且在锁内、128 条 PHY 写入顺序与切换点、BDF 表、PERST ≥100 ms、窗口、BAR 落点；8 种失败路径各停在其步且后面什么都没做；勘察零写入 |
| apple80211 | 29 | 请求头、两个 ioctl 号、扫描结果/加入请求/信道表/链路事件的字段偏移、全部信道号、PMK 十六进制 |
| smmu | 620 | 用 SoC 套件真实的 `SmmuTakeover.h`：UFS 占顶层 bank 时 0x1401 拿下一个 bank、先 S2CR 后 SMR、重启复用不写、冲突拒绝不写、地址宽度检查 |

内核参数构建（`scripts/build-pagani-wifi.py --tree <out-gpu 的树，只读> --out <草稿区>`）：PaganiWifi 自己的 9 个对象零警告，均为
ARM64 MH_OBJECT、iOS 17.0；只有 `PaganiWifiNet.cpp` 带静态初始化（已移出 `__mod_init_func` 并导出）；加 `--with-ath12k` 再编 54 个协议栈
对象（Linux 代码按 sheng 的做法 `-w`）；追加到 `libAdreno830.a` **副本**：平台符号齐全、无重复强符号（`fat_mount` 与
`pagani_wifi_fat_mount` 并存、`nw_wifi_*` 强符号覆盖本驱动的弱默认、`nw_board_option` 本驱动强定义覆盖协议栈弱默认）。归档引用的外部
符号全是静态内核里现成的（ifnet/mbuf/bpf/kev_post_msg/vnode/IOKit 等）。

集成差异：6 个差异都能 `patch -p1 --dry-run` 干净应用到当前文件；打补丁后的 Stage7Probe 用内核参数编译零警告并导出弱的
`pagani_wifi_start`；两份 TOML 能解析；`make-phone-seed.py` 能编译、`make-a830-layout.sh` 过 `bash -n`。

没验证的：任何真机行为；`make-phone-seed.py --wifi-firmware` 的完整运行（要基础种子和 hdiutil）；整内核链接（按要求不做完整内核构建）；
peach 的 QMI 流程。

## 5. 集成差异（`integrate/`，都没有应用）

| 差异 | 改什么 |
| --- | --- |
| `components.toml.diff` | 主仓登记 PaganiWifi（配方：`build-pagani-wifi.py --with-ath12k --fetch-deps --append-to {tree}/build/adreno830/libAdreno830.a`） |
| `board.toml.diff` | 设备仓库 `[components] include` 加 PaganiWifi，`archives` 在 PaganiHaptics 后加 `component:PaganiWifi`；不加任何启动参数（默认关） |
| `Stage7Probe.cpp.diff` | 弱默认 `pagani_wifi_start` 与在 ADSP、电池、振动之后的调用 |
| `make-phone-seed.py.diff` | 种子脚本 `--wifi-firmware DIR` → `/private/var/ngpu/firmware/wlan`（0644、回读核对、sha256 进报告、≤64 MiB） |
| `make-a830-layout.sh.diff` | `A830_WIFI_FW=<目录>` 传给种子脚本（只在 phone-plus/phone-apps 做种子时） |
| `board-adt-wlan.diff` | **第二步才用**：`[adt.product.wlan]` 名字节点，让 launchd 启动 wifid；要等 en0 起来之后与 `pagani-wifi=6` 一起上。它和传感器的 `ios-sensors-adt-nodes.diff` 插在同一位置，两者都打时手工合并 |

defconfig：不需要（追加进已被强制加载的 `libAdreno830.a`）。XNU 补丁：不需要（sheng 同一套网络代码在未改动的 21H461 XNU 上跑过，
本驱动只引用静态内核已有的符号），`patches/xnu/series` 不动。

## 6. 标为【猜测】的值（都能用启动参数或表项改）

1. peach 按 QCC2072 条目探测，其余硬件参数通用（facts 第 7 节已逐项核对过 HAL/窗口/CE/MHI，未核对的是整体）。
2. M3 默认按 cnss2 发 phy_ucode20.elf（`-m3=0` 改发空 M3）；QDSS 默认不下载（`-qdss=1`）；冷启动校准默认做（`-cbc=0`），超时 60 s；
   校准后“模式 OFF → 第二次 FW_READY”的流程来自 pagani-linux 的说法，未证实。
3. BDF 上限 256 KiB（`-boardkib`）；BDF 名默认 `bdwlan.elf`（`-bdf`）。
4. DMA：端点流 0x1401 在 Gunyah 下能像 UFS 的 0x60 一样设成直通 bank；PBL 和固件接受 4 GiB 以上的地址（cnss2 对 peach 用 36 位掩码，
   ath12k 也是 36 位）；窗口 48 MiB 够用（`-dma`）；窗口用写合并属性映射。
5. SPI 141（iMSI）在 Gunyah 下能送到 HLOS（不行就 `-msi=0` 轮询）。
6. 阶段 5 的芯片侧上电写入默认 0x79（sheng/ath12k 的做法，cnss2 一个都不写；热复位地址未知，默认不写）。
7. 链路默认 Gen2（`-gen`）；只投 `SN4` 一个 BCM（sheng 只投 SN7），档位取 cnss 的 high；RF_CLK1 不投（M16 待核）。
8. 时序沿用 sheng：WLAN_EN 低 150 ms、高后等 60 ms（≥ pwrseq 的 50 ms）。
9. 固件位置未知，所以分区和种子两条路都做了（M8/M9 定）。

## 7. 早晨要做的（按顺序；每次只变一个参数，`pagani-ramoops=1` 看 `[wifi]` 行）

1. 跑 `morning-facts.sh`（`DEBUGFS=1 PULL_FW=1`），按 `make-wifi-firmware-seed.md` 判断固件在 modem_a 还是要放种子。
2. 应用 `integrate/` 里的前三个差异，编内核；固件要放种子时再应用种子脚本的两个差异、设 `A830_WIFI_FW`。
3. `pagani-wifi=1`：核对引脚/电源/睡眠时钟与事实表（M15、M18）。
4. `=2`：电源是否都上来、S7I 是否保持、GCC 票在 CESTA 存在时是否生效。
5. `=3`：PHY 就绪、L0、读到 17cb:110e、速率宽度（M3）。
6. `=4`：SMMU bank、BAR0 大小（M4）、芯片版本（期望主版本 2）。
7. `=5`：PBL 是否接受 4 GiB 以上的引导程序地址、拆分镜像是否被接受、是否进 RDDM。
8. `=6`：QMI 各步（TME/M3/AUX/QDSS/校准）按日志逐个调开关（M12–M14），然后 en0、扫描、加入、DHCP。
9. 最后才考虑 `board-adt-wlan.diff`（wifid）。

早晨事实 → 代码里的位置：固件位置 M8/M9 → `-fwpart`/种子；BDF 名 M12/M13 → `-bdf`；链路 M3 → `-gen`；BAR0 M4 → 自动按探测；
MSI M5 → `-msi`；电源 M15 → `kRails`；RF_CLK1 M16 → 需要时加一个投票表项；引脚 M18 → `kPins`。

## 8. 已知限制

没有下电路径（电源与链路保持到重启）、没有挂起；不扫 6 GHz（协议栈沿用 sheng 的设置）；系统存不住网络密码（没有钥匙包，sheng 同样
限制）；wifid 只有加了 ADT 节点才会启动；不读 DIAG/QDSS 追踪。Ath12kWifi 的 Linux 依赖（约 400 MB）没有留在仓库目录里：第一次带
`--with-ath12k` 构建时用 `--fetch-deps` 联网拉取（今晚拉在会话草稿区，会话重启会被清掉）。
