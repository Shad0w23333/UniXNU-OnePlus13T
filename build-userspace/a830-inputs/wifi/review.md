# 13T Wi‑Fi 硬件安全审查（PaganiWifi 阶段 1–5）

> **2026-11-01 更正（依据 `../round-1101/wifi.md`，已用真机日志和反汇编核对）**：下文这几处已过时，以这里为准：
> ①第 1、6 节“wifid 要 `wlan` 节点才启动、没有 wifid 时 `join.txt` 是唯一入网路径”不对：wifid 现在就在跑（本轮日志 pid 43、14.956 s），ADT 里没有 `wlan`。
> 原因是 `gop-display` 这个 nub 让 `_LimitLoadToDeviceTree` 对任何名字都成立，详见 README。`join.txt` 只是免手输的办法，`board-adt-wlan.diff` 留作那个 bug 被修之后的保险；
> ②第 2、10 节两个差异“未应用”不对：`adreno830-shared-locks.diff`、`make-phone-seed-join.diff` 都已应用并编进内核 `75e75d4e`；
> ③第 6、7 节“启动参数余量 42 字节（981/1023）”过时：当时是 996/1000，只剩 4 字节，上限在 `UniXNU/scripts/unixnu/payload.py`。上机方案改用 `fullrun/make-wifi-rounds.sh`，先删无关项，C 轮要上限放宽到 1020；
> ④第 7 节的 9 轮已合并成 A、B、C（`round-1101/wifi.md` 第 5.3 节），跑批用 `工具/gpu-test-loop/wifi-rounds.py`。

更新：2026-10-31（工作流 `wyfwvokjv` 的 wifi-review）。对象：`UniXNU-Pagani-Drivers/drivers/PaganiWifi`、本目录的 `facts.md`、
`port-report.md`、`morning-facts.sh`。依据：运行中设备树 `a830-inputs/dt/running.dts`、sheng `08cde20e`、草稿区 `ext/` 下的 Linux 主线、
一加 OSS（pci-msm、gcc-sun、cnss2）、pagani-linux（GPL 只取事实）。

**没有任何一步在手机上跑过。** 下面的“通过”都只是主机模型和内核参数编译的结果。没有提交，没碰手机，没写 `UniXNU/out-gpu`，
共享集成文件一个没改（新差异在 `integrate/`）。

## 1. 结论

- 默认关闭的路径是干净的：不给 `pagani-wifi=N` 时只解析一次启动参数。构建树里现有的内核含的是修复前的代码，**这版修复要重新编内核才生效**。
- 确认并修复 16 个问题。最要紧的四个：
  1. **第 4 阶段在端点不应答时会 panic**：全 1 的 BHIOFF 经 32 位回绕变成对 Device 内存的未对齐读。
  2. **DMA 地址超出任何已跑通的范围**：窗口在 0x8c… 以上，而安卓、pagani-linux、sheng 给芯片的一致性地址都在 4 GiB 以下。现在默认放到低端内存。
  3. **S7I 的模式从来没被保证**：已在 1312 mV 时什么都不投，读不到时仍继续拉起芯片，而 S7I 同时带着 UFS。
  4. **两处假失败**：RCG 判定和配置空间单次读，都比 sheng 或厂商更严，会白白浪费上机轮次。
- 链接：当前内核不带协议栈时阶段 1–5 正常，`nw_wifi_*` 落在 PaganiWifi 的弱默认上，与 PaganiAdsp 无强符号冲突。阶段 6 改为线程开头立刻报错，什么都不上电。
- 凭据：没有 wifid 时唯一的入网路径是内核 `autoJoin` 读种子里的 `join.txt`（第 6 节）。
- 最大的未知项仍是：低端窗口、SPI 141、PHY 真实供电、CX/MX 档位。都能用启动参数或日志定位，见第 8 节。

## 2. 确认并修复的问题

严重度：**A** 会丢根文件系统或 panic；**B** 让阶段走不通或走错；**C** 假失败、体验或文档。

| # | 级 | 问题与证据 | 修复 |
| --- | --- | --- | --- |
| 1 | A | `barRd` 用 32 位 `off + 4 <= size`。BAR 全 1 时 `bhi + 0x40` 回绕成 0x3f，`barRd(0xffffffff + 0x28)` 读偏移 0x27，对 Device 内存未对齐读，内核 panic。这正是第 4 阶段本该报告的场景。sheng 写的是 `bhi < 0x20000 - 0x40` | `pw_reg_ok`（64 位、对齐），BAR 和配置窗口的读写都走它；BAR 全 1 时第 406 步失败，不再往下写 |
| 2 | A | S7I 已≥1312 mV 且开着时一条票都不投，**模式留在原处**。设备树 `qcom,init-mode = 1`，门限 200 mA，只有 UFS 自己的 210 mA 票够得着；cnss2 负载为 0；本内核完全不投负载。peach 的 `VDDA_RFA_1P3`、`VDDA_PCIE_*` 都挂在 S7I 上，低模式下带载会把 UFS 的 vccq 上级一起拉低 | 只要读得到就总投模式 7（只升不降）；电压只在低于 1312 时投，使能只在关着时投 |
| 3 | A | S7I 读不到时只是“不投”，后面照样拉高 WLAN_EN，芯片的负载落到没人看过的 UFS 轨上 | 读不到就第 202 步失败（`kErrRailRead`），**任何一路都不投**；`pagani-wifi-s7i=1` 才放行。投票后读回比之前低或变成关闭，也停在 WLAN_EN 之前 |
| 4 | B | 一致性 DMA 超范围：ath12k `dma_set_coherent_mask(32)`（BHI 缓冲、MHI 上下文与环、CE/HAL 环都在其中）；cnss 在 S1 后面，`iommu-addresses` 把 IOVA 限在 0x18000000–0xb0000000；sheng 的窗口是 32 位 IOVA。`port-report` 把“流式 36 位”当成了全部 | `pagani-wifi-dmapa`（默认 0xc0000000，48 MiB）：UEFI 常规内存（DXE_Heap 0xbe360000–0xd8000000），XNU 窗口之外，写合并映射，直通流下总线地址＝物理地址，与 sheng、PaganiSpeaker 的低端缓冲同法。`=0` 退回原来的高端分配 |
| 5 | B | 没有协议栈时 `pagani-wifi=6` 要把全部硬件上电、最多等 300 s 固件，才报“未链接” | 线程开头判 `stackLinked()`，立刻 `[wifi] FAIL step=601 err=-78`，不映射不上电。判据是弱默认 `nw_wifi_where[0]` 的标记值（Mach-O 不支持 alias），已确认走 GOT、未被折叠 |
| 6 | B | `/chosen/mac-address-wifi0` 在固件就绪后才写，晚于 launchd 与 MobileGestalt 的第一次查询（盘点评审已指出） | `pagani_wifi_start` 内同步写（仅阶段 6 且有协议栈） |
| 7 | B | 固件在 modem_a、`join.txt` 在种子时，站点接口可能早于 Data 挂载，`autoJoin` 收到“还没挂载”就静默退出，永远不入网。sheng 的文件全在一个容器里，没有这个问题 | 对 `PaganiWifiFileNotYet` 最多等 300 s |
| 8 | B | sheng 的 `linkRecover` 没移植：端点 PCIe 核重启后 BAR0 被清，第 5 阶段只能超时 | `PwMhiIo.link_check`：MHI 状态全 1 时每 100 步查一次，配置空间应答后写回 BAR0、译码位与 MSI |
| 9 | B | `0x52000` 与 GPU 共用，而 `Sm8750GpuPower.c` 改写时不拿锁字（GPU 上下电时写 bit 15/16）。Wi‑Fi 线程在读与写之间被抢占就可能写回旧值，丢掉 GPU 的 GPLL0 票 | Wi‑Fi 侧：关中断做改写、已设好就不写。GPU 侧：`integrate/adreno830-shared-locks.diff`（**未应用**） |
| 10 | C | RCG 更新在分支投票**之前**触发，当时根时钟是关的。sheng 不看结果，主线这两个 RCG 用 shared ops，时钟关着时根本不触发 UPDATE。移植版却判成致命错误 | 写入顺序照 sheng，判定推迟到分支投票之后 |
| 11 | C | L0 之后配置空间只读一次。厂商 `msm_pcie_check_ep_access` 等最多 1 s | 全 1 或重试状态 0x0001 时最多轮询 1 s；读到别的设备立刻失败 |
| 12 | C | “绝不低于 PMIC 读数”有漏洞：读数高于 cnss 上限时会被投回 chosen | 高于上限时不发电压命令（既不压低，也不把错读当票发出去） |
| 13 | C | 32 位 MSI 能力在窗口高于 4 GiB 时写到截断地址，经直通流成为野写（低端窗口下不再成立，`dmapa=0` 时仍可能） | `msiReachable()`：够不着就不开 MSI；阶段 6 直接失败 |
| 14 | C | `pagani-wifi-stop` 只在 402 以前有效；停在 401/402 还打 `FAIL` | 403–406、501、502 都能停，且不再报失败 |
| 15 | C | 阶段 5 默认做 0x79 写入，cnss2 在这台手机上一个都不写（芯片刚被 WLAN_EN 冷复位） | 默认 0，`pagani-wifi-chipreset=0x79` 留作第二轮。这是策略调整，改回只需一个常量 |
| 16 | C | `join.txt` 进种子时 0644、摘要进 JSON；`--join-psk` 留在 shell 历史；CRLF 文件把 `\r` 算进密码；120 s 后不再重连；wifid 开射频就停掉自动加入；错误密码反复重试；文档说“省了就由设置 App 手动加入”（没有 wifid 时根本没有） | 全部修正，种子部分见 `integrate/make-phone-seed-join.diff`（**未应用**） |

文档更正：`port-report.md` 顶部加了更正块，`facts.md` 的 DMA 掩码行改了，README 与 `make-wifi-firmware-seed.md` 已同步。

## 3. 逐阶段审查

**写入面（模型实测，阶段 1–4 成功路径）**：共 451 次寄存器写。GCC 13 次（0x52000、0x52008、0x5214c、两个 BCR、两个 RCG 的 CMD/CFG、管道选择）；
TLMM 13 次，只有 gpio16、102、103；PARF 271 次（BDF 表 256 次）；PHY 128 次，与运行设备树逐项相等（独立脚本解析 DTS 核对）；
DBI 4、iATU 16、配置空间 5、TCSR 1（0xf204008）。RPMh 19 条 VRM、1 条 BCM。第 1 阶段零写入（SPMI 观察通道的读命令除外，不改 PMIC）。

| 阶段 | 核对结果 |
| --- | --- |
| 1 | GCC/TLMM/PMIC 的读不依赖时钟；PARF/DBI 只在 GDSC 开且四个分支在跑时才读（同 sheng）。gpio202/203 不在 `gpios-reserved`（36–39、74）里。新增四路 PHY LDO 与模式的读回 |
| 2 | 七路资源名、PPID、电压范围都对过设备树（S4D 500–1036、S5F 500–1000、S3G 1856–2000、S7I 1224–1340 mV，chosen 都在范围内且不低于 init）。S4D/S5F/S3G 除 BT、WLAN 外没有设备树消费者；L8B 只和 `sdhci@8804000`（disabled）共用，抬到 2.8 V 安全。GCC 分支位、halt、RCG、管道选择、GDSC 投票、BCR 全部对过 gcc-sm8750.c 与 gcc-sun.c。全程没有 `enable=0` 路径 |
| 3 | PARF 顺序同 sheng 和主线；与 pci-msm 的差异（`SYS_CTRL` 写法、`INT_ALL_MASK`、`CFG_BITS_3`）都是 sheng 上不需要的项。PHY 序列、管道切换点、BDF 哈希 0x15、iATU 字段都对。LNKCTL2 写在 LTSSM 之前 |
| 4 | 端点 BME 在 SMMU 流配好之后才开；SMMU 或 DMA 失败时 BME 保持关闭。BAR 大小判定能挡住全 1。SMMU 规划器与 NativeUfs、触摸是同一份，真机上这两者都在用，后者正是在 UFS 带电时做的映射 |
| 5 | MHI 状态机、上下文布局、事件环应答与 sheng 一致；每阶段有超时，全 1 不会死等；失败后 DMA 窗口不释放，迟到的 DMA 不会打到被回收的内存 |

**失败后的清理**：驱动没有下电路径，这是刻意的。任何失败都只是停在那一步，电源、GDSC、时钟保持原样，所以不存在“回滚时把 S7I 或共享 LDO 拉掉”的可能。
逐条查过所有 `return`：GCC 锁字两条路径都释放，QMP 锁在发送失败时也释放，DRV‑2 锁在 `Sm8750RpmhWriteActive` 内部配对。代价是失败后 S7I 留在 1312 mV 高功率模式直到重启，只是多费点电。
RPMh 超时会留下一个被占用的 TCS（平台层设计如此，还剩两个），阶段随即失败。

`morning-facts.sh`：逐条读过，手机上确实没有写操作（无重定向、无挂载、不碰 sysfs 控制文件），可以按原计划用。F1 已包含 S7I 的 `opmode` 和 `pm_v8i_l2/l3`，正好回答第 2、4 条的问题。

## 4. 共享资源

| 资源 | 共享方 | 结论 |
| --- | --- | --- |
| S7I（`smpi7`） | UFS `vccq-parent`；pagani-linux 的描述里还是 L3B、L11B 的输入 | 见问题 2、3。绝不发 `enable=0`，电压只升不降，模式只升不降，投后再验 |
| PCIe PHY 的 LDO | 主线 MTP 说 L1F/L3G，pagani-linux 说 L3I/L2I；四路都是 UFS 路径上的（refgen、pll、qref、vddp-ref-clk），L3G 还带显示 | **永不投票**（模型里有检查）。安卓默认都是 HPM。到底是哪两路没有实机证据，所以第 1 阶段四路全读，不做门控 |
| DRV‑2 | GPU、ADSP、触摸 | 每条命令都走 `Sm8750RpmhWriteActive`，同一个锁字 `sm8750_rpmh_drv2_owner`（内核里只有一份）。只写 ACTIVE 集 |
| GCC `0x52008` | 触摸、电池、扬声器 | 与 `sm8750_gcc_vote_set` 同一协议（0 空闲、CAS、10 µs、50 ms），标签 `WIFI`，内核里一份 |
| GCC `0x52000` | GPU | 见问题 9 |
| `0x5214c` | 只有 PCIe0 | 设备树只有这两个 `qcom,collapse-vote`，工程里无其他写者，不需要锁 |
| AOSS QMP | GPU、ADSP | Wi‑Fi 与 ADSP 用同一锁字；**GPU 的 S1 发送不拿锁**（只在 `pagani-gpu-qmp=1` 或 ACD 时发），差异里一并补了。Wi‑Fi 的 PDC 默认关，第二实例遇到已开通道直接复用，不会重新握手 |
| SCM | GPU、ADSP | **Wi‑Fi 不发任何 SCM**：DMA 在 HLOS 自己的内存里，无需 assign；SMMU 不经 SCM |
| apps SMMU | NativeUfs、触摸、扬声器 | 规划器碰到 UFS 占的顶层 bank 会往下找空 bank；只写自己的 bank 和空闲 SMR 槽，UFS 的条目不动。TLBIALLNSNH 与触摸路径相同。`DpuProbe` 挂死记录来自全量 MDSS 快照，规划器只读 SMR、S2CR 和候选 bank 的 SCTLR，与已验证路径一致 |

## 5. 没有协议栈时的链接

- 构建树现有内核（`mach.development.bcm2837.unstripped`，10:59）的 `nm` 结果：`_pagani_wifi_start` 在 PaganiWifi 文本段（不是 Stage7Probe 的弱默认）；`_nw_wifi_*` 13 个全部解析到弱默认；`_fat_mount` 与 `_pagani_wifi_fat_mount` 并存。
- PaganiWifi 的 136 个强符号与 PaganiAdsp 及其余组件无交集（修复前后符号表完全相同）；构建脚本只编 `WifiFat.c`，不单独编 `FatVolume.c`。
- 修复后新增的未定义符号只有 `_gPhysBase`、`_gPhysSize`、`_ml_set_interrupts_enabled`，当前内核都有。
- 结论：阶段 1–5 不依赖协议栈；阶段 6 不会走到弱调用，线程开头就干净失败，U 行是 `[wifi] FAIL step=601 err=-78 the Ath12kWifi stack is not linked: nothing was powered`。

## 6. 凭据：没有 wifid 时怎么入网

**现状**：wifid 的 launchd 任务要设备树里有 `wlan` 节点，所以默认不启动，设置里没有可用的 Wi‑Fi 页。入网只有一条路：

1. `make-wifi-firmware-dir.py - 目录 --join-ssid 名字`（密码在终端输入）生成 `join.txt`，经 `A830_WIFI_FW` 进 Data 种子的 `/private/var/ngpu/firmware/wlan/`。
2. 阶段 6 站点接口起来后，内核 `autoJoin` 读它，扫描、加入，掉线自动重连，错误密码就停。
3. 地址由 configd 走 DHCP。sheng 的实机证据是“开机 31 s 拿到地址”，且它的 ADT 同样没有 `wlan` 节点，所以这条路**不需要 wifid**。

**限制**：协议栈只支持 WPA2‑PSK/CCMP 或开放网络（`nw_sta.c`：AKM 只认 PSK，强制 PMF 拒绝）。纯 WPA3‑SAE 连不上，WPA2/WPA3 过渡模式可以。隐藏网络扫不到。

**明文**：`join.txt` 是明文，会进种子和镜像，所以只留本机，建议用专门的测试网络。日志只打长度，不打名字和密码。

| 方案 | 评价 |
| --- | --- |
| `join.txt`（现在的做法） | 推荐。不占启动参数，不需要 wifid，每次开机都能入网 |
| 凭据进启动参数 | 不要。余量只剩 42 字节（981/1023），还会明文进镜像和日志 |
| wifid + 设置里手输 | 第二步才有。要 `wlan` 节点，wifid 把 PMK 经 `kAssociate` 交下来（cipher 6 已处理），密码存进钥匙串。**钥匙串现在同一次开机内能用，但重启就没了**：钥匙串和已知网络都在 RAM Data 上，软件钥匙包的类密钥每次开机随机，种子预置钥匙串条目也解不开 |
| 两者并用 | 修改后 wifid 只是“开射频”不再打断自动加入，它主动加入、断开或关射频时内核才退让。wifid 看到一个不认识的已连接网络会不会主动断开，只能上机看 |

**关于 `wlan` 节点**：`board-adt-wlan.diff` 是静态节点，所有阶段都会起 wifid，而“wifid 面对没有 en0 的系统”没测过。所以它只能和带协议栈的阶段 6 镜像一起上。更好的办法是按启动参数在 `pagani_wifi_start` 里运行时建节点（盘点评审的建议），但 `IODTFindMatchingEntries` 依赖 `name` 属性的匹配，主机上验证不了，这次没有做。

## 7. 上机顺序（单变量，每轮带定时复位与 `pagani-ramoops=1`）

启动参数余量 42 字节。每轮只变一项，下面都是在现有参数后追加。

| 轮 | 追加参数 | 看什么 | 不对就 |
| --- | --- | --- | --- |
| 1 | `pagani-wifi=1` | `[wifi] rail` 七路与 `ldof1/ldog3/ldoi3/ldoi2` 的 `en`、`mv`、`mode`；`pins`；睡眠时钟脚 | S7I `unread`：查 PPID，不进下一轮 |
| 2 | `pagani-wifi=2 pagani-wifi-stop=204` | **只上电不放 WLAN_EN**。S7I `after` 不低于之前；系统正常进桌面、UFS 读写不出错 | 这是 UFS 风险的主测试，出问题立刻回退 |
| 3 | `pagani-wifi=2 pagani-wifi-stop=205` | WLAN_EN 高，`wlan_en ctl/io` 读回对；系统继续稳定 | 看 S7I 是否被拉低 |
| 4 | `pagani-wifi=2` | GDSC、`clocks`、BCR | 步号 206/207 |
| 5 | `pagani-wifi=3` | `phy pcs_status1`、`link gen width`、`endpoint id=0x110e17cb` | 302：PHY 供电，对照第 1 轮；306：RF_CLK1（M16）、睡眠时钟 |
| 6 | `pagani-wifi=4 pagani-wifi-stop=405` | `dma window, low memory`、`smmu sid`、`bar0`。不经 BAR 读任何东西 | 窗口行之后崩溃：换 `pagani-wifi-dmapa=` |
| 7 | `pagani-wifi=4` | `mhi regs`（`ee=0` 期望）、`soc hw version`（主版本 2） | 全 1：第 406 步失败；停在 `mhi regs` 之后：版本读有问题，以后用 405 跳过 |
| 8 | `pagani-wifi=5` | `mhi phase`、`mission mode`、`msis` | BHI 错：先试 `pagani-wifi-chipreset=0x79`；MSI 不来：`pagani-wifi-msi=0` |
| 9 | 带协议栈的内核 + `pagani-wifi=6` | QMI 各步、`en0`、扫描、加入、DHCP | 见 `port-report.md` 第 6 节 |

## 8. 残留风险（都要真机才能定）

1. **低端窗口**：0xc0000000 在 UEFI 常规内存里，UEFI 就在同一个 VM 里用过这段，sheng 和扬声器用同样的办法，但 13T 上还没人实测过这块（扬声器 README 也标了未验证）。`pagani-wifi-dmapa` 可换地址。
2. **低端窗口之外的 DMA**：协议栈的缓冲都从窗口分配，`dma_map` 对窗口外地址返回 0。流是直通的，万一固件发出窗口外地址不会被拦下，这与 UFS、触摸的取舍相同。
3. **链路断开时读 BAR**：sheng 上读到全 1；13T 的 DWC 没验证过，若是总线错误就是 SError。默认不做全局复位已经避开了最可能的时机。
4. **CX/MX 档位**：Gen1/2 要 ≥ LOW_SVS，驱动没投，靠 UFS 与 GPU 的既有票。不要在没投票时用 `pagani-wifi-gen=3`（要 NOM）。这里不能直接投：DRV‑2 每个资源只有一票，会覆盖 GPU 的 `Sm8750RpmhHostRails`。
5. **RF_CLK1**（M16）：cnss 不投，pagani-linux 的 PMU 节点投。第 5 轮 306 失败时优先怀疑它。
6. **SPI 141**：Gunyah 是否转发未知；不来就轮询，不影响安全。
7. **SMMU 规划没有全局锁**：扬声器、触摸、Wi‑Fi 若在同一个几百微秒窗口内规划，可能抢到同一个空闲槽。后果是那条流丢映射（故障而不是错写），UFS 的条目不受影响。要彻底解决需要在 SoC 套件里加锁字，这次没动。
8. **CESTA**：GCC 投票在 `pcie_sm` 未启动时是否直接生效，第 4 轮看 `clocks` 行。
9. **`TCSR_SOC_HW_VERSION`**：pagani-linux 的注释说经错误窗口读到垃圾所以跳过，经 0x3278 应该没事，但现在排在日志行之后，出事能定位。
10. **阶段 6 的日志量**（不在这次范围）：`kStackTraceLines = 600` 和每 30 s 一条 U 行，32 KiB 事件环扛不住，开机阶段行会被冲掉。上阶段 6 之前 integrate 要定预算。

## 9. 验证记录

- 主机测试：`python3 Test/check-pagani-wifi-host.py`，9 个程序全过（`-Wall -Wextra -Werror`、ASan/UBSan）。bring-up 12025 项（原 8757）、MHI load 1290 项（原 620）、PMIC 302 项（原 296）、DMA 4160981、firmware 244、SMMU 620、MHI boot 424、apple80211 29、tables 通过。
- 变异：针对本次修复的 12 个变异全部被 `Test/bringup.cpp` 杀死（S7I 模式、未读门控、不降压、投后校验、投票不重写、RCG 判定两个方向、配置空间等待、PHY LDO 只读等）。
- 内核参数构建：`scripts/build-pagani-wifi.py --tree <out-gpu 树> --out <草稿区>`，9 个对象零告警，强/弱符号表与修复前相同。
- GPU 差异：在草稿副本上 `test_sm8750_power`、`test_a830trace`、`test_start`、`test_sm8750_aossqmp` 全过，26 个电源变异全部杀死，`build-adreno830.py --only` 零告警，`patch -p1 --dry-run` 干净。
- 种子差异：`patch --dry-run` 干净，打补丁后的脚本能编译；`make-wifi-firmware-dir.py` 的只生成 `join.txt` 模式用开放网络试跑过，交互输入密码的分支没跑过。
- **没验证的**：任何真机行为；`PaganiWifi.cpp` 本身没有主机测试（只有内核编译，`dmaLow`、`hwVoteLock`、`stackLinked`、`mhiLinkCheck` 靠代码审读和目标码核对）；整内核链接；`--with-ath12k` 构建；`PaganiWifiNet.cpp` 的 `autoJoin` 只编译过。

## 10. 交付

- 驱动（`UniXNU-Pagani-Drivers/drivers/PaganiWifi/`，未提交）：`PaganiWifi.cpp/.h`、`PaganiWifiNet.cpp`、`WifiBringup.cpp/.h`、`WifiPmic.c/.h`、`WifiMhiLoad.c/.h`、`WifiUtil.h`、`WifiDma.h`、`PaganiWifiTables.h`、`README.md`、`Test/{bringup.cpp,pmic.c,mhi_load.c,tables.cpp}`。
- 本目录：`review.md`、`facts.md`、`port-report.md`、`make-wifi-firmware-dir.py`、`make-wifi-firmware-seed.md`。
- 待 integrate 决定是否应用（都没应用）：
  - `integrate/adreno830-shared-locks.diff`：GPU 的 `0x52000` 与 QMP 拿锁字，改的是 `Adreno830/`。
  - `integrate/make-phone-seed-join.diff`：`join.txt` 用 0600，摘要不进报告，改的是 `工具/make-phone-seed.py`。
- 新增启动参数 `pagani-wifi-dmapa`；改默认值的有 `pagani-wifi-chipreset`（0x79→0）；语义变化的有 `pagani-wifi-s7i`（现在表示“读不到也继续”）、`pagani-wifi-stop`（范围扩大）、`pagani-wifi-aj`（持续重连）。
