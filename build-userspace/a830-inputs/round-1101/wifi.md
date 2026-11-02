# 13T Wi‑Fi 推到能用：核实结果、修复清单与上机计划

> **2026-11-01 实现时的更正，以这里为准**（`build-userspace/a830-inputs/fullrun/make-wifi-rounds.sh`、`工具/gpu-test-loop/wifi-rounds.py` 是落地结果）：
> 1. 第 6 节 P0 第 1 项“`ath12k_hif_power_down` → `ath12k_hif_power_up`”照写走不通，主机模拟里三处都会卡住：`ath12k_pci_power_down` 在 `ATH12K_PCI_FLAG_INIT_DONE` 没置时直接返回（ath11k 没这个判断，ath12k 有，这个标志要等任务模式才置）；
>    shim 的 `flush_work` 其实是取消，`mhi_power_down` 的关机工作根本不跑；`nw_mhi_pre_load` 只做一次，第二次引导时引导程序拿不到 MHI 上下文。三处都已修，变异验证确认每一处都抓得住。
> 2. 模式 OFF 后的等待：固件的服务 2 s 内回来就再给 10 s 等 FW_READY，不算“没回来”；只重启一次。
> 3. 第 5.3 节表里的 C 轮参数改了：`pagani-wifi-log=15`（level 7，加上崩溃时整份转储进 UFS 导出区，比 `=7` 多 1 字节），C 轮都带 `pagani-ufslog-ms=1000`。字节数以 `wifi-rounds.py` 的输出为准：C1 1010、C2 992、最长 1015，所以 C 轮要上限放宽到 1020。C5 接在 C4-cbc0 之后时再删 `pagani-battery`。
> 4. 第 5.7 节“要用户手机上取”的 `/odm` 文件不用取：`fwchk/` 里那两份 sha256 和早晨 D5 记录的手机文件一致，已存到 `wifi/odm-firmware/peach/`（不在任何仓库里）。
> 5. 第 5.3 节“这条串接没有跑过”：`make-phone-seed.py` 串在 s6-final 后面，2026-11-01 在主机上试过，能出种子，只大 12 KB。

更新：2026-11-01。对象：kernel `75e75d4e`、`UniXNU-Pagani-Drivers/drivers/PaganiWifi`、`build-userspace/a830-inputs/wifi/`。
本报告只读，没有改任何源码、脚本、种子或镜像；没有重启手机，没有写手机。

证据等级：【实测】我跑了命令或读了真机日志；【读码】读源码或反汇编；【推断】由事实推出，可能错。

## 0. 结论

1. **wifid 现在就在 13T 上跑，不需要 `wlan` 节点。** 本轮日志里 `pid=43 wifid` 在 14.956 s 启动，120 s 时还活着，ADT 里没有 `wlan`。review 和 ios.md 的“没有节点就不启动”是错的。原因是 `gop-display` 这个 nub 挂在 IOResources 下，让 `_LimitLoadToDeviceTree` 对任何名字都成立（第 4 节）。sheng 那个矛盾也由此解开。
2. **内核 `75e75d4e` 含阶段 1–5 和 review 的 16 个修复，不含协议栈。** 阶段 1–5 不用重编内核，只要换启动参数。阶段 6 现在会立刻报 `step=601 err=-78`。
3. **两个“未应用”的差异其实已应用并编进了内核**，不要再打。
4. **固件就地读成立。** 我把真实 modem_a 卷拉到本机，用驱动自己的 `WifiFirmware.c` 读出 7 个文件，sha256 全部和手机一致。但 **Android 实际用的 BDF 和 regdb 在 `/odm`，不是 modem_a 那份**，驱动现在读的是另一份。
5. **`peach.py` 的冷启动校准流程很可能会卡死。** 它在模式 OFF 后等固件自己重启并发第二次 FW_READY，而 Android 实测是主机断电重上电（第 3.4 节）。这是阶段 6 前必须改的第一项。
6. **启动参数只剩 4 字节**（996/1000，不是 review 写的 42 字节），必须删参数才能加 `pagani-wifi=N`。
7. **最大的未知项是固件能否跟 ath12k 栈跑起来。** pagani-linux 在这台手机上留着“固件在射频初始化时 RDDM 崩溃”的调试分支，没有证据表明它跑通过。所以 RDDM 读取器要先移植，否则第一次失败什么也看不到。
8. **9 轮可以压到 3 轮加一个变体矩阵**：顺利时无人值守约 16 分钟到拿 IP，最坏约 45 分钟（不含构建）。

**真机中途换了设备：** 11:43 查到 adb 上已经是 OPD2513（SM8850P，序列 `SERIAL_OTHER_DEVICE`），不是 13T（`SERIAL_13T`）。那之后我两次只读的电源抽样数据已作废，本报告未使用；之前的 PKX110 证据都用 fdt、固件 sha256 或 cnss 日志核对过。**跑批必须带 `--serial SERIAL_13T`，否则 `gpu-loop.py` 会重启错的设备。**

## 1. 需要更正的旧说法

| 旧说法（出处） | 现状 | 等级 |
| --- | --- | --- |
| wifid 要 `wlan` 节点才启动（review 第 6 节、ios.md 1.1、`PaganiWifiNet.cpp` 注释、`make-wifi-firmware-seed.md` 第 3 节） | 已在运行，节点不需要 | 【实测】 |
| “没有 wifid 时唯一入网路径是 join.txt” | 不成立，join.txt 只是免手输的办法 | 【实测】+【推断】 |
| 两个差异未应用（review 第 2、10 节，README，`PaganiWifi.cpp` 210 行注释） | 已应用，已编进内核 | 【实测】 |
| 启动参数余量 42 字节，981/1023（review 第 6、7 节） | 现在 996/1000，上限 1000（`payload.py:35`） | 【实测】 |
| 固件位置未知（facts M8/M9） | 全在 modem_a；BDF、regdb 另有 `/odm` 版本且 Android 优先用它 | 【实测】 |
| M3、TME 争议（facts 第 7 节） | Android 发 `phy_ucode20.elf`、`aux_ucode20.elf`；TME 请求**失败也继续** | 【实测】 |
| 模式 OFF 后固件自己重启（`peach.py`，来自 pagani-linux 注释） | Android 是主机下电再上电并重新加载固件 | 【实测】+【读码】 |
| M1 设备树不一致（SUMMARY） | 假警报，只差随机的 `resource-manager-rpc` 节点 | 【实测】 |
| 预制种子不能加文件（`make-wifi-firmware-seed.md` 第 4 节） | 可以用 `make-phone-seed.py` 串一步，`make-a830-final-seed.sh` 就是这么做的 | 【读码】 |

## 2. 任务 1：内核 75e75d4e 到底含什么

- **哈希**：`kernel.macho` sha256 是 `75e75d4e…faaa1`，等于 `测试镜像/boot-A830-阶段15-首测.payload.json` 的 `kernel_sha256`。【实测】
- **驱动版本**：`tree/build/pagani-wifi/build-report.json` 记录的 23 个源文件 sha256 与当前源码逐个相同，差异 0 个；`stack_objects` 是 0。所以 review 的 16 个修复都在内核里。目标文件的代码片段也能在内核里找到，`dma window, low memory`、`the Ath12kWifi stack is not linked`、`put back during the boot` 等修复专有字符串都在。【实测】
- **启动参数**：`pagani-wifi` 及 `-aj -bdf -chipreset -dma -dmapa -fwdir -fwpart -gen -m3 -msi -pdc -s7i -sleepclk -stop` 都在。`-tme -aux -qdss -cbc -features -boardkib` 由 `nw_board_option` 动态拼出，只有协议栈才用。【实测】+【读码】
- **`_nw_wifi_*`**：弱默认，不是协议栈。`_nw_wifi_start` 就是 `mov w0,#-78; ret`。nm 显示 `_nw_wifi_where` 是 D，是因为这份弱默认带数据：它的首字节读出来就是 `NO_STACK`，正是 `stackLinked()` 的判据。【实测】
- **`pagani_wifi_start`** 是强定义，在 PaganiWifi 文本段，Stage7Probe 第 340 行调用。【实测】+【读码】

**integrate 的两个差异**：

| 差异 | 状态 | 处理 |
| --- | --- | --- |
| `adreno830-shared-locks.diff` | 反向 dry-run 干净，正向失败。`Sm8750GpuPower.platform.o` 里有 `_gcc_gpll0_vote`，`Adreno830Start.o` 里有 `_sm8750_aoss_qmp_owner` | 不用再打 |
| `make-phone-seed-join.diff` | 反向 dry-run 干净，脚本已有 `0o600` 和不写摘要的逻辑 | 不用再打 |
| `board-adt-wlan.diff` | 没打，`board.toml` 里没有该节点 | 现在不需要，留作保险 |

其余四个差异（Stage7Probe、components、board、种子脚本）也都已在。`PaganiWifi.cpp` 210 行的“GPU 还不拿锁”注释已过时。【实测】

## 3. 任务 2：走到阶段 6 需要什么

### 3.1 构建

`UniXNU/components.toml` 第 968 行的 recipe 现在没有 `--with-ath12k`。要做的只有两件事：

1. recipe 加 `"--with-ath12k"`。
2. 准备依赖：`build_stack` 要 `PaganiWifi/Ath12kWifi/deps/linux/drivers/net/wireless/ath/ath12k`。现在驱动目录下**没有** `deps/`。本会话草稿区 `ext/Ath12kWifi/deps` 里有一份（整个 `ext/Ath12kWifi` 389 MB），但会话重启就会被清（MEMORY 已记）。要么复制过去（`deps` 被 git 忽略），要么用户同意联网 `--fetch-deps`。

`board.toml`、defconfig、XNU 补丁都不用动。【读码】

### 3.2 冲突、体积

- **符号**：草稿区 `pw-kbuild-final/libPaganiWifi.a`（10-31 带协议栈的构建）有 331 个未定义符号，我逐个对照 `75e75d4e` 的符号表，**全部能解析**。协议栈全局符号都改名为 `_nwk…`；`--append-to` 会拒绝重复强符号；FAT 读取器用 `pagani_wifi_fat_*`，不和 PaganiAdsp 撞。这是静态核对，不是真链接，且那份归档早于 review 修复，构建后要重跑。【实测】
- **类名**：内核缓存 38 个 kext 里没有 IONetworkingFamily，`IONetworkStack`、`IO80211Interface` 不重名。【实测】
- **PaganiAdsp**：本轮镜像没开 `pagani-adsp`。运行时只共用 QMP 和 DRV‑2 锁字，Wi‑Fi 的 PDC 默认关。review 第 8 节第 7 条的 SMMU 规划无锁仍在；Wi‑Fi 线程在 2 s 之后才开始，UFS 和触摸的规划应已结束，这一点没有直接证据。【读码】+【推断】
- **内核体积**：协议栈目标文件共 0.54 MiB（text 423 KB、cstring 74 KB、const 38 KB、bss 33 KB）。【实测】
- **载荷预算**：工具的预算线是 192 MiB 的 95%，即 191.26 MB；现有内容约 185.6 MB，余量约 5.7 MB（FAT 卷实际还空 14.3 MiB）。加协议栈和 join.txt 没问题；**把 amss（6.84 MB）放进种子会超过预算线**，所以就地读是首选，种子只放小文件。【实测】+【推断：种子重打包后大小基本不变】

### 3.3 启动参数字节

首测镜像 996 字节。下面三项与 Wi‑Fi 无关，可用 `A830_DROP_ARGS` 删掉：`pagani-input-script=1`（22）、`pagani-kbtrace=1`（17）、`pagani-gpu-status=3`（20）。删后 937 字节，余量 63。`pagani-reset-after` 从 420 改 150 或 240 不变长。【实测】

再不够时，矩阵后几轮可再删 `pagani-audio-srzl=1`（20）、`pagani-haptics-ios=2`（21）、`pagani-haptics=1`（17），它们与 Wi‑Fi 测试无关。`sheng-gpu-metal-opens=0` 不能删，默认值是 40，语义不同；`pagani-gpu-profile=0` 也不能删，默认值是 1。【读码】

### 3.4 固件：就地读成立，但有两处要改

**Android 这次开机的真实加载记录**（`dmesg`，PKX110，开机后 11 分钟读取）【实测】：

| 请求名 | 来源 | 大小 | 备注 |
| --- | --- | --- | --- |
| `peach/amss20.bin` | modem_a | 6,840,384 | 两次（校准后重新加载） |
| `tmel_peach_20.elf` | modem_a `image/` | 55,204 | QMI 请求失败 `result: 1, err: 83`，两次都失败，继续 |
| `cnss_softsku_peach.pfm` | 不存在 | — | 跳过，继续 |
| `peach/regdb.bin` | **`/odm/etc/wifi/peach/`** | 24,278 | modem_a 那份大小相同，内容差 1950 字节 |
| `peach/bdwlan.elf` | **`/odm/etc/wifi/peach/`** | 125,328 | modem_a 那份 125,420 字节，差 13275 字节 |
| `peach/phy_ucode20.elf` | modem_a | 290,816 | 只在第一次要 |
| `peach/aux_ucode20.elf` | modem_a | 61,440 | 只在第一次要 |
| `peach/qdss_trace_config_perf_v2.cfg` | 不存在 | — | 回退下一项 |
| `peach/qdss_trace_config_v2.cfg` | modem_a | 6,241 | 真的下发 |

其他事实：`region id: 1, nvid: 0x97, wcn chip_id: 0x02`；`Host BDF config: HW_XPA: 1 CalDB: 0`；`Calibration took 8400 ms`；固件版本 `WLAN.GNG.1.0-03342-QCAGNGSWPL_V1.0_V2.0_SILICONZ-1.81206.153`；`amss20.bin` 在 0 和 0x80000 处都是 ELF 头，与 `mhi_fw_layout` 的假设一致。【实测】

**就地读验证**：modem_a 是 FAT16、4 KiB 扇区、16 KiB 簇、起始块 4870、89600 块，与 facts 一致。我用驱动的 `WifiFirmware.c`＋`WifiFat.c`（ASan/UBSan）读真实卷，`amss.bin`、`board.bin`、`regdb.bin`、`m3.bin`、`aux_ucode.bin`、`tmel_peach_20.elf`、`qdss_trace_config_v2.cfg` 全部读到，sha256 和手机一致；`firmware-2.bin`、`board-2.bin`、`caldata.bin` 返回“没有”，正是协议栈要的回退信号。【实测】

**还没验证的**：`native_ufs_part_find/read` 从没在真机上读过 LUN4，所有历史日志都没有 `[adsp]` 行。阶段 5 是它的第一次真机运行。【实测】

**要改的第一处：BDF 和 regdb。** 查找顺序是 modem_a 优先，所以种子里放 `/odm` 版本也不会生效。`/odm` 在 erofs 上，内核读不了，只能走种子。`pagani-wifi-bdf=bdwlan.odm` 能绕开 BDF，但 regdb 没有覆盖开关；而且种子此时可能还没挂载（Data 在 4.87 s 才回，线程 2 s 就开始），`firmwareReady` 把 BDF 当可选，协议栈随后拿不到就失败。要让这两个文件“种子优先，并等 Data 挂载”。用错 BDF 大概率仍能起，影响的是发射功率和合规；是否影响校准不确定。【读码】+【推断】

**要改的第二处：冷启动校准。** Android 的时间线是【实测】：

```
15.385 Calibration took 8400 ms
15.505 PERST 拉低，cnss_power_off_device
15.613 QMI WLFW service disconnected
15.616 重新上电 → 15.651 链路起来 → 15.655 重新加载 amss20.bin → 16.234 QMI 重连
```

`cnss2/main.c` 的 `cnss_cold_boot_cal_done_hdlr` 确实是模式 OFF 后调 `cnss_bus_dev_shutdown`。ath11k 对需要重启的芯片也是主机 `hif_power_down` 再 `hif_power_up`（`ath11k_qmi_fwreset_from_cold_boot`）。而 `peach.py` 发完模式 OFF 就 `break`，等第二次 FW_READY，没有超时。如果固件不会自己重启，协议栈就永远停住，en0 不出现。【读码】+【推断】

### 3.5 协议栈其余静态核对

- `0x110e` 走 QCC2072 分支时不读芯片版本，直接定 `hw_rev`，不会因 major=2 被拒。【读码】
- 协议栈上电必定做 LTSSM、热复位、清中断、QFPROM、清 warm entry、全局复位，等价于 `pagani-wifi-chipreset=0x7d`（热复位取 QCC2072 地址），比 README 写的 0x79 多一个热复位。cnss2 不做这些。pagani-linux 用同样的路径能走到 QMI，所以芯片大概率扛得住。【读码】+【推断】
- host_cap 差异：cnss2 的代码从不设 QDSS_CFG_MISS 和 PERST_NO_PULL，会按条件设 DRV、WLAN_EN、AUX_UC 等；QCC2072 条目是 QDSS_CFG_MISS、PERST_NO_PULL、AUX_UC。这是阶段 6 失败时的第一批变量。【读码】
- pagani-linux 把 RDDM 依次归因到 TME、M3、校准，但 Android 的 TME 请求本身就失败，所以至少这条归因是错的；它发空 M3 又和 Android 相反，可能正是崩溃原因之一。这是推测，不能当结论。【实测】+【推断】
- **`ath12k_dbg` 是 level 7，`pLog` 只把 level ≤ 6 写进启动日志。** 现在 QMI 细节只留在内存环 `gText` 里，复位后就没了。要加开关。【读码】

## 4. 任务 3：让设置里的 Wi‑Fi 页能用

### 4.1 wifid 为什么已经在跑

`launchd` 的 `_LimitLoadToDeviceTree` 确实生效（0x1000043cc，不满足返回 0x8a）。但内核这边的回答被一次错误的虚函数调用带偏了：

1. `Stage7Probe.cpp` 第 294–308 行建的 `gop-display` 是 `IOPlatformDevice`，IOService 父节点是 IOResources，却挂进了 `/arm-io` 的 IODeviceTree 平面。
2. `IOPlatformDevice::compareName` 把 `getProvider()` 强转成 `IOPlatformExpert`，调虚表 0x580 的 `compareNubName`。
3. IOResources 的虚表没那么长，这个槽位落到后面 MetaClass 虚表里的 `OSMetaClass::release() const`，函数体只有 `ret`，x0 还是 IOResources 指针。
4. `IODTFindMatchingEntries` 用 `cmp w26, w20, lsr #1` 判断，非零就算匹配。

所以任何名字都“存在”。【读码：反汇编 `75e75d4e`】旁证：门控为 `hilo/display-hilo/twodbl` 的 `IOMFB_bics_daemon` 也在 14.980 s 启动，ADT 里同样没有这些节点。sheng 的 `LegacyDisplayProbe.cpp` 第 534、549 行是同一写法，所以它的 ADT 没有 `wlan` 而 wifid 能跑。【实测】+【读码】

副作用：`_LimitLoadFromDeviceTree` 恒不成立，`CommCenter-noncellular`（`baseband`）不加载；与 Wi‑Fi 无关。**这是 bug 带来的便利**：如果以后有人修这个 nub，wifid 就不启动了，到时打上 `board-adt-wlan.diff` 即可。

review 担心的“wifid 面对没有 en0 的系统没测过”也不用担心了：最近三轮完整日志里 wifid 都是这么跑的，没有崩溃；`20261031-223924` 那轮它活了约 1165 s，到 1180 s 才以 `status=f00` 退出，紧接着就是 1181 s 的 panic 落盘，退出原因我没有查。【实测】

MobileGestalt 现在已是 `wifi1`，同样没有 `/chosen/mac-address-wifi0`，所以设置里应该已有 Wi‑Fi 行。【实测】+【推断】

### 4.2 wifid 要什么，现在各项的状态

| 需要 | 13T 现状 | 证据 |
| --- | --- | --- |
| launchd 启动 | 已运行 | 【实测】 |
| `IONetworkStack`（configd 的 InterfaceNamer 打开后才发布 IOKit 空闲；缺它 wifid 会卡在 `__wait_for_IOKit_to_quiesce`） | `PaganiWifiNet.cpp` 有，en0 建好时注册；**当前镜像没有**，所以 wifid 现在多半卡在这里 | 【读码】+【推断】，依据 sheng `f3aefd7` |
| `IO80211Controller`、`IO80211Interface` 注册项 | 有，属性照 sheng | 【读码】 |
| BSD `en0`：`IFNET_SUBFAMILY_WIFI`、`NX_NOAUTO`、bpf、`IFM_IEEE80211` | 有 | 【读码】 |
| `SIOCGA80211/SIOCSA80211` | 22 种读、12 种写；其余 `EOPNOTSUPP`，前 300 条全打日志 | 【读码】 |
| `/chosen/mac-address-wifi0` | 阶段 6 且链接了协议栈时同步写，早于 launchd | 【读码】 |
| 国家码、cipher 6 的 PMK | 已处理，来自 sheng `67d709a`、`3e679fb` | 【读码】 |
| 种子未禁用 wifid，无陈旧的 MobileGestalt 缓存 | 我只读挂载 `s6-final` 核对过 | 【实测】 |

### 4.3 能走到哪一步

上游提交说明写明：wifid 用自己算的 PMK 加入（cipher 6），`sheng-wifi-aj=0` 时由设置 App 负责加入，能拿 DHCP、开网页。Ath12kWifi 的 `f6bf561` 就是 sheng 跑通的版本，`PaganiWifiNet.cpp` 是它的移植。**扫描列表、输入密码、加入这条路在 sheng 上走通过，在 13T 上没跑过。** 代价：不需要新框架代码，预计只是按日志补 1–3 轮未处理的请求。【读码】+【推断】

`known[]` 信道表没有 DFS 信道（5260–5320 等）。国内路由器若在 52–64 信道，wifid 可能不显示。这是【推断】，上机看到再加两行。

**限制**（【读码】`nw_sta.c`、README）：

- 只支持 WPA2‑PSK/CCMP 或开放网络。纯 WPA3‑SAE、强制 PMF 不行；WPA2/WPA3 过渡模式可以。
- 不扫 6 GHz，隐藏网络扫不到。
- 密码记不住：Data 是 RAM 盘，已知网络和钥匙串重启就没了。所以设置里输一次只管这次开机。
- 速度：sheng 约 12 Mbit/s 下行、24 Mbit/s 上行。够浏览，不算快。
- 没有下电和省电路径，开 Wi‑Fi 后七路电源和芯片一直开着，耗电会增加；幅度没有数据。

**DHCP、DNS**：走 configd（IPConfiguration）和 mDNSResponder，两者都已在运行（`configd` 14.972 s、`mDNSResponder` 26.902 s）。sheng 实测开机 31 s 拿到地址。【实测】+【读码】

### 4.4 建议路线

1. **join.txt 先行**：无人值守就能验证到 IP，省人。和 wifid 并存在 sheng 上跑过。
2. **再测设置页**：`pagani-wifi-aj=0`，不带 join.txt，手动操作。
3. **日用形态**：每次开机靠 join.txt 自动连。后续可让内核把设置里成功加入的 SSID 和 PMK 存一块 UFS 保留块，下次开机自动连，这样镜像里不用放明文（第 6 节 P2）。

## 5. 任务 4：上机清单

### 5.1 是否关 sheng-log：不关

**判断：所有轮都保留 `sheng-log=1`。**

- 日志几乎只有 UFS 这一份。XNU 的复位变成 HARD_RESET，DRAM 掉位，Linux 丢弃 ramoops：最近两轮 pstore 都是 0 条；`20261031-174545-a830-full-2` 没有 UFS 窗口数据，全程 0 条记录。关掉等于这几轮盲跑。【实测】
- 写入量极小：只写 unixnu 末尾 32 MiB 窗口，3 s 一轮，最长 1.04 ms，占空比约 0.035%；0 写错误。【实测】
- 如果 S7I 真把 VCCQ 拉垮，根卷读取一样失败，关日志救不了系统。【推断】
- 把 S7I 抬到 1312 mV 以上是 Android 给芯片上电时就要做的动作（平时读数 1224 mV，设备树 `vdd-wlan-rfa2-config` 下限 1312 mV），UFS 当时也在工作。【实测】+【读码】+【推断】

**残余风险要说明**：LUN0–5 的 `bDataReliability` 都是 0x00（今天实测），README 警告这种 LUN 掉电时可能丢整个单元的数据，LUN0 上有安卓 userdata。概率极低但后果大，**跑 A 轮前请确认安卓数据已备份**。

A 轮不放 WLAN_EN，只有电压和模式变化，没有负载阶跃，这是主要的保护。

### 5.2 通用设置

- 基线：`build-userspace/a830-inputs/fullrun/make-stage15.sh` 的 `one`，只改下面几项。
- `A830_DROP_ARGS="startup_debug native-ufs-diagnostics pagani-input-script pagani-kbtrace pagani-gpu-status"`
- `A830_EXTRA_ARGS` 保留 BASE，加 `sheng-log=1` 和本轮参数。
- 运行：`python3 工具/gpu-test-loop/gpu-loop.py --image 测试镜像/<名字>.img --label <标签> --serial SERIAL_13T --yes`
- `pagani-ramoops=1`、`pagani-panic-reset=20`、`pagani-wdog=30`、`pagani-reset-wdog=1` 都保留。

### 5.3 轮次

review 的 9 轮合并成下表。依据：每个阶段都包含前面的阶段，`powerOn` 先做勘察，失败时 `[wifi] FAIL step=` 已写明位置，所以逐级分轮只在“硬挂死没有日志”时才有价值，那时再二分。

| 轮 | 合并 | 在基线上追加 | 字节 | 复位 | 内核 | 种子 |
| --- | --- | --- | --- | --- | --- | --- |
| A | 原 1、2 | `pagani-wifi=2 pagani-wifi-stop=204` | 972 | 150 s | 现有即可 | s6-final |
| B | 原 3–8 | `pagani-wifi=5 pagani-ufslog-ms=1000` | 973 | 150 s | 现有即可 | s6-final |
| C1 | 原 9 | `pagani-wifi=6 pagani-wifi-cbc=0 pagani-wifi-log=7` | 987 | 240 s | 新内核 | s7-wifi |
| C2 | | `pagani-wifi=6 pagani-wifi-log=7`（校准＋主机重启） | 969 | 240 s | 新内核 | s7-wifi |
| C3 | | C1 或 C2 中走得远的＋`pagani-wifi-pdc=1 pagani-wifi-qdss=1`，再删 `pagani-audio-srzl`、`pagani-haptics-ios` | 最多 983 | 240 s | 新内核 | s7-wifi |
| C4 | | C3＋`pagani-wifi-features=0x46`，再删 `pagani-haptics` | 最多 992 | 240 s | 新内核 | s7-wifi |
| C5 | | 走得最远的一轮＋`pagani-wifi-m3=0` | ≤ 1000 | 240 s | 新内核 | s7-wifi |
| D | 设置页 | `pagani-wifi=6 pagani-wifi-aj=0`＋胜出开关，删项跟胜出的那一轮，面板 65 | ≤ 989 | 900 s | 新内核 | s7-final（要手输密码，s6 没有打字修复） |

`pagani-wifi-log` 是待加的新参数。C1 先于 C2，是因为它不依赖新写的重启路径，而且失败时能直接拿到 RDDM 文本。C 轮任何一轮看到 `en0 address` 带真实 IP 就结束矩阵。C3 的 `pdc=1` 对应 Android 开机发的四条 PDC 消息，`qdss=1` 对应 Android 真的下发 QDSS 配置。

种子只为 C 轮做一次：

```sh
python3 build-userspace/a830-inputs/wifi/make-wifi-firmware-dir.py - <本地目录> --join-ssid '<网络名>'   # 用户在终端输入密码
python3 -B 工具/make-phone-seed.py --disable-jobs "" --wifi-firmware <本地目录> \
    build-userspace/a830-inputs/final/s6-final.dbrdisk <本地>/s7-wifi.dbrdisk
```

owner trust 继续用 `s6-final-owner-trust.bundle`，这只是数据。这条串接的做法来自 `make-a830-final-seed.sh`，没有跑过，要在主机上试一次。

### 5.4 通过判据

都在 `runs/<目录>/merged.log` 和 `summary.txt`。

**A 轮**

- `[wifi] rail smpi7 now` 读得到；`after` 行 mv ≥ 1312、mode 变高。
- 七路 rail 都有 `after` 且 `rc` 为 0。`stopped as asked`，没有 `[wifi] FAIL`。
- `summary.txt`：state 6、0 write errors、没有 panic。
- `diag: r1 sum @77 … wk 35/35`，桌面正常，`M io` 里根卷读持续。

**B 轮**

- `wlan_en … io` 读回高；`clocks` 通过；`link`、`endpoint id=0x110e17cb`。
- `dma window, low memory`、`smmu sid`、`mhi regs`（`ee=0` 期望）、`soc hw version`（major 2）。
- `firmware amss.bin rc=0 bytes=6840384 from=modem_a:image/peach/amss20.bin`。
- `msi attach rc=0`、`mission mode … msis=N`。系统稳定到定时复位。

**C 轮**

- `stack started, MSI on SPI 141`，`[wifi] ath6` 行有 QMI 进展。`tme-lite patch refused: result 1 error 83` 属正常（Android 也这样）。
- `wifi-net: en0 is up`、`IOKit is quiet`、`scan result`、`join finished: module result 0`。
- `wifi-net: dhcp in type 5`、`en0 address <真实 IP>`；`diag: r1@77 net` 含 `en0+`。

**D 轮**（用户手动）：设置里看到列表，选网络，输密码，连上，Safari 打开网页。

### 5.5 失败时的下一步

| 现象 | 判定 | 下一步 |
| --- | --- | --- |
| A：`step=202` S7I 读不到 | PPID 或 SPMI 读有问题 | 停，不进 B；查 `0x8ad` |
| A：`S7I dropped`、UFS 读错、起不来 | 这条路危险 | **立刻停**，回退完整包，人工审查 |
| B：日志中断，没有 FAIL 行 | 硬挂死 | 依次跑 `=2 stop=205`、`=2`、`=3`、`=4 stop=405`、`=4` |
| B：`step=302` | PHY 没就绪 | 对照 rail 的 `phy` 行 |
| B：`step=306` | 没进 L0 | 先看睡眠时钟脚，再考虑 RF_CLK1 投票 |
| B：窗口行后崩溃 | 低端内存有问题 | `pagani-wifi-dmapa=0xc4000000`（48 MiB 到 0xc7000000，安卓上是普通 RAM，低于 0xc8400000 的保留区） |
| B：`step=406` 全 1 | BAR 或 iATU | 看 `bar0` 行，用 `stop=405` 对照 |
| B：`firmware … rc≠0` | `native_ufs_part_*` 第一次真机失败 | 看 `ufs=` 状态码，先修 NativeUfs；种子兜底会超过 95% 预算线 |
| B：BHI 错 | 引导 | 加 `pagani-wifi-chipreset=0x7d` |
| B：MSI 不来 | Gunyah 不转发 SPI 141 | `pagani-wifi-msi=0` |
| C：`step=601 err=-78` | 内核没带协议栈 | 查 `build-report.json` 的 `stack_objects` |
| C：BAR 全 1、MHI 超时 | 协议栈的全局复位把链路弄掉了 | 跑 `pagani-wifi=5 pagani-wifi-chipreset=0x7d` 预演；要在 stage 6 补链路恢复 |
| C：`rddm:` 行 | 固件崩溃 | 读 RDDM 文本，按它改 M3、QDSS、特性位、PDC |
| C：QMI 停在 BDF 或 FW_READY | 依赖 level 7 日志 | 走 C3、C4、C5 |
| C：`no calibration done` | 校准失败 | 落回任务模式，看后续 |
| C：扫不到网络 | 校准或 BDF | 换 `/odm` BDF，查 L8B 是否 2.8 V |
| C：`network security not supported` | 路由器设置 | 换 WPA2 |
| D：页面空或一直转 | 未实现的请求 | 看 `wifi-net: request` 的 `-> 45`，补实现 |

gpu-loop 报 “USB: nothing” 时先按 MEMORY 判断：安卓可能已起来但 USB 没枚举，请用户看屏幕、插拔线。

### 5.6 总时长

实测：到 fastboot 13.8 s；`boot_to_android` 比定时复位多 106.6 s；读 UFS 窗口按 10 s 估。**每轮 ≈ 定时复位 + 130 s。**【实测】

| 路径 | 时长 |
| --- | --- |
| A + B | 约 9.5 分钟 |
| A + B + C1 全部通过 | 约 16 分钟 |
| A、B、chipreset 预演、C1–C5 全部跑完 | 约 45 分钟 |
| 原 9 轮逐级跑（到阶段 5 共 8 轮） | 约 37 分钟 |

构建另算：内核约 2–3 分钟，镜像每个约 0.5–1 分钟（依据：`kernel.macho` 与两个阶段 15 镜像的时间戳）。【推断】

**可以合并的**：A 和 B 都不需要重编内核，今天就能跑，约 10 分钟。按 MEMORY“修完再测”，我建议先写完第 6 节 P0、P1，用同一个新内核把 A、B、C 一次跑完，这样用户只需在场开始和结束时各一次；完整包继续当安全回退。如果想先知道硬件这关过不过，A、B 单跑也不白费。

### 5.7 需要用户提供的

- 把 13T 接回，确认序列 `SERIAL_13T`。
- 网络名和密码，在自己的终端用 `make-wifi-firmware-dir.py` 输入。要求：WPA2‑PSK/CCMP 或 WPA2/WPA3 过渡；非隐藏；2.4 GHz 或 36–48、149–165 信道。**建议开一个专用访客网络。**
- 备份安卓数据。

**明文风险**：`join.txt` 是明文，会进三个地方：本地目录（0600）、`s7-wifi.dbrdisk`、每一个带它的 `测试镜像/*.img`。任何拿到镜像的人都能解出密码。不写手机闪存（`fastboot boot`，RAM 盘），日志只记长度。但 `scan result` 会把周围 SSID 记进 UFS 日志窗口，这部分在手机上留存。跑完删本地目录和 s7 种子，镜像不要外传。D 轮不带 join.txt，没有这个问题。

## 6. 任务 5：还要写的代码和测试

工作量按“一个人专注写加主机测试”估，不含上机迭代。

### P0：阶段 6 之前必须有

| # | 内容 | 文件 | 主机测试 | 量 |
| --- | --- | --- | --- | --- |
| 1 | 校准后主机重启：模式 OFF 后最多等 2 s；不来 FW_READY 就在 `nw_wifi_step` 里（不在 QMI 工作项内）做 `ath12k_hif_power_down` → 等 ≥100 ms → `ath12k_hif_power_up`，`cal_done` 保持 1 | `tools/peach.py`、`src/nw_wifi.c` | 扩 `tests/stack_sim.c`：模拟第二次会话、QRTR 重建、内存重申请 | 0.5–1 天 |
| 2 | 移植 RDDM 读取器（sheng `rddmScan`，BSD-2-Clause-Patent，带署名）和根端口 AER、SMMU 故障日志 | `PaganiWifi.cpp` | 新 `Test/rddm.c`：合成 BHIe 表 | 0.5 天 |
| 3 | `pagani-wifi-log=7`：level 7 进启动日志，限行数 | `PaganiWifi.cpp` | 编译 + 过滤单测 | 1 小时 |
| 4 | recipe 加 `--with-ath12k`，把 deps 落到持久位置 | `UniXNU/components.toml` | 重跑符号核对和 `check-pagani-wifi-host.py` | 30 分钟 + 构建 |

### P1：同一批做完

| # | 内容 | 文件 | 主机测试 | 量 |
| --- | --- | --- | --- | --- |
| 5 | BDF、regdb 种子优先，并等 Data 挂载 | `WifiFirmware.c/.h`、`PaganiWifi.cpp` | `Test/firmware.c` 加用例 | 2–3 小时 |
| 6 | 批量出包脚本（参照 `make-stage15.sh`） | 新脚本 | 比对 payload.json 的参数和字节数 | 1 小时 |
| 7 | 跑批脚本：逐轮调 gpu-loop、按第 5.4 节判据决定继续或停、强制 `--serial` | 新脚本 | 用现有 runs 目录回放判据 | 0.5 天 |
| 8 | 修正第 1 节列的过时文档和注释 | README、review、seed.md、`PaganiWifiNet.cpp`、`PaganiWifi.cpp` | 无 | 1 小时 |

### P2：en0 通了之后

| # | 内容 | 量 |
| --- | --- | --- |
| 9 | 信道表加 DFS 信道；`kRate`、`kPhyMode` 报真实值 | 2 小时 |
| 10 | stage 6 的链路恢复（如果全局复位真的清 BAR） | 0.5 天 |
| 11 | 记住网络：加入成功后存 SSID 和 PMK 到 UFS 保留块，开机自动连 | 1–2 天 |
| 12 | 真正的省电和关闭路径 | 1 周以上 |
| 13 | WPA3‑SAE | 1–2 周 |
| 14 | 吞吐优化 | 不确定 |

P0＋P1 合计约 2–3 天。

**诚实的把握**（【推断】，主观估计，只作排期参考）：A 过的把握很高；B 约六成，未知项是低端 DMA 窗口、MSI、`native_ufs_part_*` 的第一次真机运行；C 第一批就到 en0 约三四成，主要看固件。需要 RDDM 文本再迭代 1–3 批是常态。从第一批上机到“能上网”我估计几天到一两周；到“接近日用”还要记住网络和省电，再加一周以上。

## 7. 仍不确定的事

1. 固件在 ath12k 栈下能否进任务模式、过射频初始化。pagani-linux 没有证明这条路通。
2. 主机重启路径在 XNU 协作式 shim 里能否重走 MHI、QRTR、QMI。ath11k 有先例，这个 shim 没跑过。
3. peach 不校准能否工作。pagani-linux 注释说不行，但它的其他归因已被证伪。
4. 链路速率和 MSI 能力：配置空间在 Android 上只读到全 1（链路挂起），要在 B 轮看。
5. wifid 现在是否真的卡在 `__wait_for_IOKit_to_quiesce`。日志只证明它活着。
6. `IOMFB_bics_daemon` 等被误放行的任务有没有额外开销，这和卡顿的关系我没有查。
7. review 第 8 节的残留风险（低端窗口、SPI 141、CX/MX 档位、CESTA）都还在。

## 8. 证据位置

- 本轮日志：`工具/gpu-test-loop/runs/20261101-110201-stage15-smoke-1/merged.log`，行 1937–1939（wifid）、2020（IOMFB_bics_daemon）、4262（`mg … wifi1`）、6182（`wifid:43`）。
- 早晨数据：`build-userspace/a830-inputs/wifi/phone/20261031-115842/`。
- 反汇编：`__ZNK16IOPlatformDevice11compareNameEP8OSStringPS1_`、`__Z23IODTFindMatchingEntriesP15IORegistryEntryjPKc`；launchd 0x1000043cc、0x100007254、0x10003d9f0。
- 上游：`参考资料/上游-20261031/UniXNU-UniXNU-sheng`（`3e679fb`、`67d709a`、`f3aefd7`、`4041063`、`82755e6`）、`docs/zh/porting/network.md`。
- 草稿区（会话重启会清）：`ext/Ath12kWifi`（Linux v7.0.14 依赖）、`ext/op-modules`（cnss2，GPL，只取事实）、`ext/pagani-linux`、`pw-kbuild-final/`；`fwchk/t.c`（读真实 FAT 的主机程序）、`fwchk/odm_bdwlan.elf`、`fwchk/odm_regdb.bin`（专有文件，只在草稿区）。拉取的 modem_a 镜像和解出的固件已删除。
- 联网检索没有找到 peach、WCN7880 或 pagani-linux 的公开状态，上述判断不依赖网页。
