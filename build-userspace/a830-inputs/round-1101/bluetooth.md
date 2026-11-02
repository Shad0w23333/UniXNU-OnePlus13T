# 13T 蓝牙路线报告（2026-11-01）

证据等级：【实测】本轮跑了命令或读了手机/镜像数据；【读码】读了反编译或源码（Linux 一律只取事实）；【推断】由事实推出，尚未验证。
本轮全程只读：没有重启手机，没有写分区，没有改任何现有源码、种子或镜像。

## 0. 结论

**推荐路线：真 `bluetoothd`（A 方案）+ 内核自带 GENI UART 与 H4 过滤层 + 两处 `text-fixups`。**

- 能走通的理由有三条，都是本轮新查到的：
  1. `bluetoothd` 的数据通道不是 `/dev/cu.bluetooth`，而是**内核控制套接字**。非 Skywalk 分支就是对套接字普通 `read/write`，内核注册一个 `kern_control` 就接上了【读码】。
  2. 芯片引导不在 `bluetoothd` 里，而在单独的 `BlueTool` 进程（XPC），修补一个函数入口就能跳过，`bluetoothd` 自身的 HCI 初始化全部是标准命令【读码】。
  3. 手机上有一份完整的 Android 串口跟踪，**这颗芯片的上电字节流已经完整拿到**（含 `0x31` 私有包、8 Mbps 切换、加载的 5 个固件文件及顺序）【实测】。
- 最大的新难点也是这份跟踪带来的：这颗芯片不是主线 `btqca` 那种流程。它是多子系统架构，要先用非 HCI 的 `0x31` 包加载外设固件和 TME-L 固件，之后 BT 子系统才响应 HCI【实测】。
- **最终能到的程度**：BLE/经典键鼠手柄、设置里的蓝牙开关与配对，把握中等；A2DP 听歌取决于 iOS 音频本身能否工作；HFP 通话基本不指望；隔空投送、接力、手表、AirPods 专属功能不做。详见第 5 节。
- **工作量**：到键鼠可用约 2600 行新代码、8–15 轮真机；A2DP 与功耗优化另加 4–8 轮。建议排在 Wi-Fi 的上电阶段之后，两者共用电源路径，Wi-Fi 第 2 阶段的电源测试等于替蓝牙踩一遍坑。

### 对既有材料和任务假设的更正

| 原说法 | 实际 | 证据 |
|---|---|---|
| `bluetoothd` 串口找 `/dev/cu.bluetooth`（`ios.md` 6.11） | 这两个路径只在 `start` 里做存在性检查（或者有 `bluetooth` 服务也行），数据走 `com.apple.uart.sk.bluetooth` 控制套接字 + Skywalk | 【读码】`sub_1000DDAA8`、`start` |
| `bluetoothd` 按博通芯片初始化 | 引导在 `BlueTool`；`bluetoothd` 的 HCI 初始化 26 步几乎都是标准命令；博通只体现在 VSC 层，由 `product-id` 查表选定 | 【读码】`0x1008D4640` 表 |
| 让 `bluetoothd` 接受非博通芯片是最难点 | 它**不探测芯片**：`J120` 直接查表得 `BCM_4355C0`（枚举 12），不读厂商号判断 | 【读码】`sub_10031AD30` |
| 固件在 `bluetooth_a` | 另有一个在 `modem_a`：`tmel_peach_20.elf` | 【实测】 |
| 第一阶段用 `HCI_Reset`、读版本验证握手 | Android 在固件下载完之前**从不发 HCI 命令**，首个握手是 IBS `0xFD→0xFC` 加 `0x31` 版本查询。`HCI_Reset` 要到第二阶段才有把握 | 【实测】跟踪；能否提前响应【推断】未知 |
| 高通 BT 音频经 SLIMbus 或 AOC | 这台手机是 **BT SoundWire**（`lpass_bt_swr@6CA0000`、`btfmswr_slave`）进 LPASS，iOS 用不上 | 【实测】dmesg |
| 控制器是 WCN7850 类 | 属性为 `ganges`、产品号 33，运行固件名 `BRAHMA`；主线 `qca_btsoc_type` 没有它 | 【实测】+【读码】 |

本轮还撤回过一个自己的中间判断：曾以为 `product-id` 不在 `bluetoothd` 表里、模型指针为空。反编译器把变参调用的后续项隐藏了，改为全文件搜 20 字节后确认它在表内，已按汇编重新定位（见 2.3）。

---

## 1. 硬件事实

### 1.1 控制器身份

| 项 | 值 | 等级 |
|---|---|---|
| 设备树 | `bt_peach`，`compatible = "qcom,peach-bt"`（`running.dts` 42477） | 【实测】 |
| 厂商属性 | `persist.vendor.qcom.bluetooth.soc = ganges`，`soc.productid = 33`（0x21） | 【实测】 |
| SoC 版本 | dmesg `bt_ioctl: unified Current SOC Version : 40210200` | 【实测】 |
| 运行固件 | `BTFW.BRAHMA.2.0.0-00262-PATCHZ-1.92902.28_DEF`（`.ver` 文件与控制器应答一致） | 【实测】 |
| `Read_Local_Version` | `04 0e 0c 01 01 10 00 0e 00 00 0e 1d 00 21 7e`：HCI 0x0e、LMP 0x0e、厂商 0x001D、子版本 0x7E21 | 【实测】 |
| 缓冲 | ACL 1024×8、SCO 240×4（`0x1005`）；LE 251×16、ISO 200×4（`0x2060`） | 【实测】 |
| 架构 | RISC-V 固件（ELF `e_machine=0xF3`），多子系统：Peri、TME-L、BT | 【实测】+【读码】 |
| 主线支持 | `btqca.h` 的枚举只到 `QCA_WCN7850`，没有 Ganges/peach；社区搜索也未见 `0x31` 外设补丁流程 | 【读码】 |

型号名无法确认：Wi-Fi 文档写 “WCN7880 系列” 来自路线图，这里不作为事实。

### 1.2 UART 与引脚

- QUP2 SE6：`qcom,qup_uart@898000`，`compatible = "qcom,msm-geni-serial-hs"`，别名 `hsuart0`，`/dev/ttyHS0`【实测】。
- 中断 GIC SPI 461（`/proc/interrupts` 显示 `GICv3 493`），唤醒 TLMM gpio27【实测】。
- 引脚（`active` 状态）：gpio24 CTS、gpio25 RTS、gpio26 TX、gpio27 RX，功能名 `qup2_se6_l0..l3`；主线 pinctrl 里四脚的 `qup2_se6` 都是**功能号 1**【读码】。
- `pinctrl-0`（`default`）是 GPIO 功能，要切到 `pinctrl-1`（`active`）。驱动力 2 mA；上下拉：CTS 无、RTS 下拉、TX 上拉、RX 无【实测 DT】。
- TLMM 控制寄存器按项目表为 `0x0f100000 + 0x1000*n`（`PaganiWifiTables.h`），gpio24 = `0x0f118000`【读码】。
- `/proc/tty/driver` 实测这次开机 `mmio:0x00898000 tx:341400 rx:8398 RTS|CTS|DTR|DSR|CD`【实测】。

### 1.3 GENI 固件

- **SE 固件已由引导链加载，协议是 UART**【推断，证据强】：
  - sysfs `ver_info = FW ver=0x66, HW ver=4.0.0`，`xfer_mode = SE_DMA`，`hs_uart_version = 1.1`【实测】；
  - 有 `qupfw_a/b` 分区；QUP2 设备树节点没有 `firmware-name`【实测】；
  - XNU 下 QUP1 的 SPI 不加载固件就能用（`PaganiInput`）【读码】。
- 上机第一步仍要读 `GENI_FW_REVISION_RO`（`0x68`）确认协议域是 UART，这一步只读。
- QUP2 的 `iommus = <0x7e 0x423 0x00>`，SE DMA 要经 SMMU（SID 0x423）。**首版用 FIFO 轮询绕开**，FIFO 模式不碰 SMMU【推断】。

### 1.4 时钟

| 项 | 值 | 等级 |
|---|---|---|
| SE 时钟 | `clocks = <0x34 0x79>`；主线：RCG `0x1e77c`，CBCR `0x1e76c`，投票 `0x52010` bit10 | 【读码】 |
| 外壳时钟 | `0x52010` bit0 core、bit1 s_ahb、bit2 m_ahb、bit3 core_2x | 【读码】 |
| 频率表 | 含 7.3728 MHz（GPLL0_EVEN，M/N 384/15625）和 128 MHz（GPLL0_MAIN，M/N 16/75） | 【读码】 |
| 分频实测 | `ser_clk_cfg` 769/65/17 → 分频 48/4/1，对应 9600/115200/8000000 | 【实测】 |
| 反推 | 7.3728 MHz ÷4 ÷16 = 115200；128 MHz ÷1 ÷16 = 8 Mbps | 【推断】 |
| RF 参考时钟 | `bt_rf_clk1 = <0x35 0x06>` 即 `RPMH_RF_CLK1`，cmd-db `rfclka1`，地址 +4 写 1；**全设备树只有 BT 节点投它** | 【实测】+【读码】 |
| 睡眠时钟 | PMK8550 gpio3（PPID 0x0ba），`PaganiWifi` 已有 `pw_pmic_sleep_clock` | 【读码】 |

`0x52010` 目前没有任何 XNU 驱动使用（0x52008 才是共享的那个）；`rfclka1` 是否默认开着未知，即 Wi-Fi 的待核项 M16。

### 1.5 电源轨与使能

| 设备树名 | 轨道 | 范围 (mV) | 还有谁用 |
|---|---|---|---|
| `bt-vdd-rfa1p25` | **S7I** | 1312–1340 | **UFS `qcom,vccq-parent-supply`**（DT 10175，L1D 的上级）、WLAN rfa2 |
| `bt-vdd-rfa1p8` | S3G | 1860–2000 | WLAN rfa1 |
| `bt-vdd-rfa0p75`、`bt-vdd-aon` | S5F | 876–1000 | WLAN dig |
| `bt-vdd-dig`、`bt-vdd-wlan-aon` | S4D | 560/876–1036 | WLAN aon |
| `bt-vdd12-io` | L2F | 1200 | WLAN io12（常开） |
| `bt-vdd18-aon` | L3F | 1800 | WLAN io（常开） |

以上均为【实测 DT】。BT 用的轨道是 `PaganiWifi` 七路（`kRails`）的子集，多出来的 L8B 是天线共享轨，BT 是否必须未知，建议一起投【推断】。

上电时序【实测 dmesg】：

```
14.131880  Power ON 请求
14.136967  BTON:Turn Bt OFF asserting BT_EN to low
14.191572  BTON: WLAN ON Asserting BT_EN to high        （低 54 ms）
14.245848  bt_configure_gpios: on                       （高后等 54 ms）
14.2479    打开串口（2 ms 后）
14.2722    第一个 0xFD（约 26 ms 后，且已切 115200）
```

- `BT_EN` 在 PM8550VE-F gpio3，PPID `0x58a`，经 SPMI 写，配置 `normal`、推挽输出、无上下拉【实测 DT】。
- **不需要先拉 WLAN_EN**：`btpower.ko` 字符串同时有 `BTON: WLAN OFF Asserting BT_EN to high`、`BTON: WLAN OFF waiting for 100ms delay`、`BTON: WLAN OFF and BT ON are too close`，说明 BT 可单独上电，但和 WLAN 开关要隔至少 100 ms【读码】。
- Android 上 `btpower` 自己不投电：`bt_num_vregs =0`，`bt_regulators_pwr: not avilable to vote on`；此刻 HLOS 票只有 S4D/S5F 500 mV、S7I 1224 mV `fast`【实测】。电是 cnss 和 AOSS PDC 表（`wlan_pdc`）给的。**XNU 下没有 cnss，要由我们投票**，这是和 Android 的最大差异【推断】。
- `fmd-set/fmd-chg-pon/fmd-cnt2-stop`（SDAM）是关机寻找设备功能，不碰。

### 1.6 固件文件（手机实测）

**实际加载的就是下面 5 个**：用跟踪里每段 TX 的字节在 25 个候选固件文件里比对得出，总量 323464 字节，加协议开销和校准数据后与 `tx:341400` 对得上【实测】。

| 顺序 | 文件 | 所在分区 | 字节 | sha256 前 12 位 | 通道 |
|---|---|---|---|---|---|
| 1 | `image/brhperifw20.mbn` | `bluetooth_a`（sde7，FAT） | 25420 | `16d4e64cead8` | `0x31`，ss 0 |
| 2 | `image/brhperinv20.bin` | 同上 | 741 | `26a2d959227d` | `0x31`，ss 0 |
| 3 | `image/tmel_peach_20.elf` | `modem_a`（sde6，FAT） | 55204 | `4814d33de800` | `0x31`，ss 3 |
| 4 | `image/brhbtfw20.mbn` | `bluetooth_a` | 228872 | `07047a909a73` | HCI `0xFC00/0x1E` |
| 5 | `image/brhbtnv20.bin` | 同上 | 13227 | `f2754fde272b` | HCI `0xFC00/0x1E` |

- 分区里还有 `gng*`、`hmt*` 等其他芯片的文件，共约 210 个，其中 `brhbtnv20.b2e…b41` 等是按板号选的 NVM；本机比对到的是默认 `.bin`（独有片段在偏移 2183）【实测】。
- NVM 阶段实发 72 个整段，比 13227 字节多，因为 HAL 把 `/data/vendor/bluetooth/calibration.bin`（4380 字节）拼在后面【实测+读码】。这份校准在 Android 分区，XNU 读不到，缺失时首发也能工作（Android 首次开机同样没有它）【推断】。
- **BD 地址写在 NVM Tag 2**：`brhbtnv20.bin` 解析出 Tag 2 是占位值，HAL 在发送前改内存副本（来源是 Modem NV 或 `persist.vendor.service.bdroid.bdaddr`）【读码+实测】。我们照做：只改内存，不动分区。
- 两个分区都能由现成 `FatVolume.c` 读取，无需进镜像，载荷预算不受影响【读码】。

### 1.7 真实握手序列

来源是 Android 的 `/sys/kernel/tracing/instances/hsuart/trace`。局限：每个 DMA 缓冲只记录前 21 字节；CPU0 环形缓冲回绕，**15.11 s 之前的 RX 全部丢失**，所以 `0x34` 事件的内容尚未亲眼看到【实测】。

`0x31` 帧结构由 HAL 反编译还原【读码】：

```
发送：31 00 | opcode(LE) | len | subop | ss | ...     opcode: 0xFFF0 外设 EDL / 0xFFF1 外设通用
接收：34 | host id(0) | evt | len | params...          （与 HCI 事件同时可能出现 0xFC/FD/FE）
```

| 时间 | 发送 | 含义（HAL 函数） |
|---|---|---|
| 14.2711 | —— | 主机切 115200，硬件流控开 |
| 14.2722 | `fd` | `WakeUpSoC`：最多 10 次，每次等 10 ms，等 `0xFC` |
| 14.2748 | `31 00 f0 ff 02 05 00` | `HCI_PERI_EDL_PATCH_GETVER` |
| 14.2804 | `31 00 f1 ff 02 02 15` | 设波特率，码 `0x15` = 8 Mbps（备选 `17`=3.2M、`14`=3M） |
| 14.3009 | —— | 关流控、等 20 ms、`tcdrain`、改主机波特率、开流控、读应答 |
| 14.3046 | `31 00 f0 ff 03 08 00 00` | 外设子系统仲裁 |
| 14.3065– | `31 00 f0 ff f6 06 00 f3 <243字节>` … | `brhperifw20.mbn` |
| 14.3626 | `31 00 f0 ff 01 0a` | `GET_BOARD_ID` |
| 14.3633– | `… f6 06 00 f3 …`，末段 `… 0f 06 00 0c …` | `brhperinv20.bin` |
| 14.3723 | `31 00 f0 ff 02 09 00` | `GET_BUILD_INFO` |
| 14.3735 | `fd` | 再次唤醒 |
| 14.3739 | `31 00 f0 ff 03 08 03 00` | TME-L 仲裁（ss 3） |
| 14.3750 | `31 00 f0 ff 02 05 03` | TME-L 版本 |
| 14.3755– | `31 00 f0 ff f6 06 03 f3 …` | `tmel_peach_20.elf` |
| 14.5212 | `31 00 f1 ff 03 00 01 01` | `HCI_PERI_ACTIVATE_SS`，激活 BT 子系统 |
| 14.5448 | `01 00 fc 01 19` | **第一条 HCI 命令**：EDL 取版本 |
| 14.5482– | `01 00 fc f5 1e f3 <243字节>` … | `brhbtfw20.mbn` |
| 15.0376 | `01 00 fc 01 23` | 取板号 |
| 15.0628– | `01 00 fc f5 1e f3 …` | NVM + 校准，每段应答 `04 0e 05 01 00 fc 00 1e` |
| 15.3010 | `01 00 fc 01 20` | 读版本串，应答含 `BTFW.BRAHMA…` |
| 15.3025 | `01 1d fc 00` | 取附加特性（HAL 可选） |
| 15.3050 | `01 09 fc 02 00 0a` | 读 OTP（只读，可选） |
| 15.3070 | `01 17 fc 02 14 00` 等 | 开控制器日志（可选） |
| 15.3084 | `01 03 0c 00` | `HCI_Reset` → `04 0e 04 01 03 0c 00` |

运行期【实测+读码】：

- IBS：`0xFD` 唤醒、`0xFC` 应答、`0xFE` 睡眠；空闲约 250 ms 主机发 `0xFE`。
- 控制器主动发 ACL 句柄 `0x2EDC` 的日志包（`02 dc 2e …`），还有 `0xFF` 厂商事件。
- 从第一个 `0xFD` 到 `HCI_Reset` 全程约 1.04 s。

---

## 2. `bluetoothd` 与 `BlueTool` 静态分析

对象：`/usr/sbin/bluetoothd`（`sha256 3f1e4493…`，编译日期 Jul 11 2025）、`/usr/sbin/BlueTool`，根卷只读，IDA 在草稿目录副本上分析。本节除注明外均为【读码】。

### 2.1 启动门（`start`）

1. MAC 非零：`BT_DEVICE_ADDRESS` 环境变量，或 `marconi-bt`/`bluetooth` 服务的 `local-mac-address`。
2. 存在 `/dev/cu.bluetooth`、`/dev/uart.bluetooth`、`marconi-bt` 服务、`bluetooth` 服务四者之一。
3. 否则 `errx(0, "No bluetooth on this device.")`。退出码 0，launchd 配置 `KeepAlive.SuccessfulExit=false`，**不会重启**。

launchd 任务只有 `RunAtLoad`，没有 `_LimitLoadToDeviceTree`。种子里只是被禁用，工具 `edit-phone-seed-launchd.py --enable com.apple.bluetoothd` 现成。

### 2.2 HCI 传输

`sub_1001D104C`：先看环境变量 `BT_HCI_TRANSPORT=传输:驱动:选项`，否则读服务 `marconi-bt`、`bluetooth` 的 `transport-encoding`：

| 值 | 传输 | 驱动 |
|---|---|---|
| 0 | USB | USB |
| 1 | **H4** | POSIX |
| 2 | H4DS | POSIX |
| 3 | H4BC（**属性缺失时的默认值**） | POSIX |
| 4 | H5 | POSIX |
| 5 | BCSP → 落到 USB | USB |
| 6 | APPLEBT | APPLEBT |
| 7 | PCIESW | PCIESW |

另有 `VIRT`、`PCIE` 只能走环境变量。`transport-speed` 或 `BT_BAUDRATE` 给波特率，默认 2400000。

**POSIX 驱动（`sub_1000DDAA8`）**：

```
socket(PF_SYSTEM, SOCK_STREAM, SYSPROTO_CONTROL)
ioctl(CTLIOCGINFO, 名字)      名字默认 "com.apple.uart.sk.bluetooth"，或选项 "名字,波特率"
connect()
若名字 == 默认名：setsockopt(IOAOSSKYSETCHANNELSPEC) → getsockopt(UUID) → os_channel_create（Skywalk）
否则：不走 Skywalk
getsockopt(TIOCGETA)/setsockopt(TIOCSETA)     返回值都不检查
读写：无通道时就是 read()/write()
```

- 真实 J120 上这个控制名由 `AppleOnboardSerial` 提供，**没有预链进我们的内核**【实测】，名字空闲。
- 内核已有 `_ctl_register`、`_ctl_enqueuedata`、`_ctl_getenqueuespace`【实测】。
- 用 SOCK_STREAM 注册要带 `CTL_FLAG_REG_SOCK_STREAM`；`bluetoothd` 以 `mobile` 运行，不能设 `CTL_FLAG_PRIVILEGED`。
- 沙盒配置是 `BTServer`（`com.apple.private.sandbox.profile:embedded`），保持原控制名最稳，不去赌沙盒对新名字的放行【推断】。
- H4 接收只接受包类型 1–4，其他字节进重同步状态（`sub_1000DF6E4`）。所以 `0x34`、IBS 字节、ISO 都必须在内核层挡掉。

**`VIRT` 驱动**：打开 `<路径>-in`（写）和 `<路径>-out`（读），即一对 FIFO，H4 成帧。这是另一个现成接口（见路线 C1）。

### 2.3 芯片选择：不看控制器，只看 `product-id`

`sub_10031AD30`：

- `getenv("BT_CHIP") == "ACI BT"` → 枚举 2000；
- 否则 73 次变参 `MGIsDeviceOneOfType`，比较 20 字节的 `/product/product-id`；
- 本机 `product-id = e9fefef6…0ec3`（`profile.toml` 第 90 行，并已在 `payload.img` 里确认【实测】），是 J120 的，命中 `0x10031b230` 这一组，得 `MOV W1,#0xC`，即 **`BCM_4355C0`（枚举 12）**；
- 都不中时：虚拟机用枚举 1，否则记 “Unknown platform”，模型指针保持空。

枚举 12 决定 VSC 层走博通表 `off_1008D4EE8`（`sub_1001A5E88`），2000 以上走 ACI，枚举 0（`THIRD_PARTY_DONGLE`）被 `sub_1001E1DC8` 拒绝。

### 2.4 引导：`BlueTool`

- `sub_1002F05F4`（“simple and fast”）只给 PCIe：没有 `bluetooth-pcie` 服务且启动参数无 `wifibt-external=` 时返回 11，转 BlueTool。
- `sub_1002EF860` 发 XPC 到 `com.apple.BlueTool`（`/usr/sbin/BlueTool -R`）：
  - 请求：`RunBuiltinScript`，`script` 为 `boot`/`init`/`deepsleep`，可带 `btaddress`；
  - 回包：`result`、`firmware`、`manufacturer`；`result` 0 成功，10/13 算超时，其余算失败。
- `sub_100438790` 对结果：0 继续；2（超时）或 213（XPC 失败，日志 `Unknown platform/chipset/manufacturer combination`）→ 等 1 s 后 `_exit(1)`；其他无限重试。**这就是原作者记录的“崩溃循环”的来源**【推断】。
- `BlueTool` 内嵌全部博通固件（9.8 MB，所以根卷没有 `/usr/share/firmware/bluetooth`）【实测】，对高通芯片不可能成功。

### 2.5 虚拟机分支不能用

`kern.hv_vmm_present`（ADT `vmm-present`）为真时有 4 处分支：跳过可用性检查、用默认芯片、跳过电源循环、跳过芯片初始化，并换用 `CBStackControllerVMStack`。
但跳过初始化后 `sub_100438790` 返回 0，调用方把电源状态置 3 就返回，**协议栈不启动**。这是“没有蓝牙硬件时别崩”的桩路径。上游 `droidvm` 的 “bluetoothd 运行” 很可能就是这个【推断】，不算真蓝牙先例。

### 2.6 HCI 初始化与 VSC

- `0x1008D4640` 表 26 步：写地址、Reset、读版本/命令/地址/特性、事件掩码、SSP、LE 支持、缓冲、状态、接受列表、PA 列表、CIS/子速率、主机缓冲、流控、错误数据上报。除第 1 步外全是标准命令号。
- 第 1 步写 BD 地址是 VSC，但只有全局地址非零才发，非 PCIe 路径里该值被清零（`sub_100182BC8`），**所以不发**。`Reset` 失败也被忽略。
- `Done starting up stack` 之后的 VSC 走 `sub_100045F48`：调用方基本不看返回值，但**同步等完成**。`sub_100046308` 等 15 s，超时记 `VSC stall detected` 然后 `__break` 崩溃。
- 启动路径上静态可见 27 个 VSC 调用点，例如 `BD_VSC_HDR_CONTROL`、`BD_VSC_READ_PROP_LOCAL_FEATURES`、`BD_VSC_BT_ENA_FEATURES`、`BD_VSC_ENABLE_LMP_ROUTING`、`BD_VSC_SET_FW_TRACE_MODE`、`BD_VSC_OLYMPIC_SET_EXT_SCAN_LIMITS`，全是特性开关和读统计。实际发哪些受枚举 12 的条件限制，要上机记录。
- 二进制总共认识 250 多条 VSC。
- **关键安全约束**：博通 VSC 号和高通 VSC 号共用 `0xFCxx` 空间，例如高通 `0xFC00` 下载、`0xFC0B` NVM、`0xFC14` 写地址。**OGF 0x3F 一律不能透传**，要在内核就地回 `Command Complete`（状态 0x01）。

### 2.7 其他依赖

| 项 | 内容 |
|---|---|
| 偏好 | 域 `com.apple.MobileBluetooth.debug`，字典形式如 `LE = {SupportsMultipleAdvertising = NO}`，对客户版无条件生效，可由种子写入 |
| 配对数据 | `/var/mobile/Library/MobileBluetooth/com.apple.MobileBluetooth.ledevices.paired.db` 等，和 `com.apple.MobileBluetooth.devices` 偏好；Data 卷现在是内存盘，重启丢失，要靠持久 Data |
| Keychain | 用 `SecItem*`，有 `com.apple.bluetooth` 访问组；是否阻塞启动未知 |
| HID | 键鼠走 `IOHIDUserDevice`；内核有 `IOHIDResourceDeviceUserClient`【实测】 |
| MobileGestalt | `bluetooth` 要 `/chosen/mac-address-bluetooth0`；`bluetooth-le` 要 `/product/bluetooth-le`（`ios.md`） |
| 音频插件 | `BTAudioHALPlugin` 的 `_AudioServerPlugIn_Create`（0x38bec）现被 `text-fixups` 改成返回 NULL，上游记为“无蓝牙后端时的忙重试” |
| 启动阻塞风险 | 上游称部分任务禁用是为避开 XPC 同步等待链。`bluetoothd` 卡住时 SpringBoard 等客户端可能被拖住，未在 13T 上验证【推断】 |

---

## 3. 路线比较

| | **A 真 `bluetoothd`（推荐）** | C1 `VIRT` + 用户态翻译 | B 内核 HCI + 自写最小栈 | C2 整套 Apple 串口 kext |
|---|---|---|---|---|
| 内核 | GENI UART、上电、下载、IBS、H4 过滤、`kern_control`、`bluetooth` 服务 | 同左，只是输出字符设备 | 同左 | 还要把 GENI 包成 `AppleOnboardSerial` 总线接口并做 Skywalk |
| 用户态 | 不写 | 翻译守护进程（要穿过 launchd/沙盒门） | 整个协议栈 + HID 注入 | 不写 |
| 修补 | 2 条 `text-fixups`（下述） | 同左 + 环境变量注入 | 无 | 同 A 的 F2 |
| 依赖私有行为 | 控制套接字、XPC 跳过、VSC 容错 | `VIRT` 只有环境变量可达 | 几乎没有 | 最多 |
| 系统集成 | 设置、CoreBluetooth、配对界面、音频路由全部可用 | 同 A | **全无**：App 看不到，设置页不亮 | 同 A |
| 风险 | VSC 行为靠实测 | 多一层进程，延迟和启动顺序 | 工作量转到协议栈 | Skywalk 工程量大，收益为零 |
| 新代码 | 约 2600 行 | 约 3000 行 | 约 5000 行起 | 难估，明显更大 |

**结论**：选 A。B 不作产品路线，但其中一小块（内核里发 LE 扫描命令数广播）要做，作为第 3 阶段的射频自检。

### A 的修补清单

已对根卷原文件逐字节核对【实测】。`native-text-fixup.patch` 在签名校验之后改页，原字节不匹配就不改，上限 32 条，现有 7 条左右，不需重签。

```
F2 跳过 BlueTool：  bluetoothd,2ef860,ff8304d1e9230c6d,00008052c0035fd6      sub_1002EF860 → return 0
F3 跳过 Skywalk：   bluetoothd,ddd20,a0050034,1f2003d5                       strcmp 后的 CBZ → NOP
```

F1 已取消（见 2.3）。可选的 F4 是在 A2DP 就绪后**删除**现有 `BTAudioHALPlugin` 那条。
不用环境变量：launchd 从签名缓存取任务，改 plist 无效；`kern_exec` 重定向 DumpPanic 的先例（`phone-diag-sysctl.patch`）可用但没必要。

### 内核 H4 层要做的事

- **发送方向**：解 H4 帧；`opcode` 高 6 位是 0x3F 时不下发，立刻回 `04 0e 04 01 lo hi 01`，并记录 `opcode` 备查。其余进发送环；`HCI_Reset` 透传。
- **接收方向**：吃掉 `0xFC/0xFD/0xFE`（`0xFD` 回 `0xFC`）；丢弃 ACL 句柄 `0x2EDC`、`0xFF` 事件、`0x34` 包；其余进 `ctl_enqueuedata`。
- **IBS**：发送前若对端睡眠，先 `0xFD` 等 `0xFC`。空闲发 `0xFE` 放到功耗阶段，先常醒。
- **开关**：`ctl_connect` 时确保固件已下载，失败则断开。不接 BlueTool 后 `boot/init/deepsleep` 全空操作，蓝牙开关由协议栈发 `HCI_Reset` 处理，控制器保持加载。
- **可选开关**：若实测 `bluetoothd` 看厂商号走分支，在 `Read_Local_Version` 应答里改写。默认关。

`bluetooth` 服务用驱动动态创建：`IOService` + `setName("bluetooth")` + 属性 `local-mac-address`（6 字节）、`transport-encoding=1`、`transport-speed=8000000`，再 `registerService()`，要在 launchd 启动前同步完成，做法同 Wi-Fi 的 `/chosen/mac-address-wifi0`。这里不能只放 ADT 名字节点，因为 `bluetoothd` 用的是 `IOServiceNameMatching`。

### 音频走哪条路

| 场景 | Android | iOS（A 方案下） |
|---|---|---|
| A2DP | 卸载经 SoundWire 进 LPASS；也支持主机编码【实测】 | `BTAudioHALPlugin` → `com.apple.BTAudioHALPlugin.xpc` → `bluetoothd` → L2CAP → **HCI ACL**，纯数据，和芯片音频口无关【推断，由服务名和协议分层得出，未逐函数跟踪】 |
| 带宽 | —— | 约 40 KB/s，8 Mbps UART 绰绰有余【推断】 |
| HFP | SCO 经 SoundWire【实测】 | 枚举 12 下 `sub_1002D612C` 为假【读码】；它控制“调整 sco hci 缓冲”，芯片号 ≥22 才走 HCI SCO，所以 `bluetoothd` 此时多半期望 PCM 总线，13T 没有这条线【推断】 |
| HCI SCO | 控制器报告 SCO 缓冲 240×4【实测】，但是否真能走 HCI 未知 | 要改走 HCI 需要更多修补和音频侧支持【推断】 |

所以 **A2DP 技术上看不到蓝牙侧障碍，卡在 iOS 音频本身**（本轮“音量键能调但不出声”）。HFP 判 D。

### 先例

| 项目 | 情况 | 等级 |
|---|---|---|
| UniXNU 系列（`devices.toml` 及设备记录） | 没有一台实现真实蓝牙；13T 上游文档写未实现 | 【实测】 |
| `droidvm` | “bluetoothd 运行”，无控制器描述 | 【实测】文档 +【推断】VM 分支 |
| Project Sandcastle | 公开资料列为不工作 | 网页 |
| Corellium iOS | 支持文档列为不支持 | 网页 |
| PongoOS | 引导前环境，与此无关，未查到相关内容 | 未核实 |
| BrcmPatchRAM / `BlueToolFixup` | macOS 第三方蓝牙思路相近，但搜索未查到具体实现 | 未核实 |
| 主线 `btqca`/`hci_qca` | 只取事实：EDL 子命令、TLV 分段 243、Tag 2 地址、Tag 17 HCI；不覆盖 `0x31` | 【读码】 |

**这条路线没有可抄的现成实现，这是诚实的判断。** 好在 sheng 的 Wi-Fi 上电和本项目的 GENI、SPMI、FAT 代码都能复用（BSD-2-Clause-Patent）。

---

## 4. 分阶段计划

约定：新驱动 `PaganiBt`，`Stage7Probe` 里 weak `pagani_bt_start()`，默认全关，只记 `[bt]` 行进 BootProgress（U 记录）。

**启动参数已用 996/1000 字节**【实测】，`pagani-bt=N` 放不下。要么去掉 `pagani-kbtrace=1`、`pagani-input-script=1` 这类测试项，要么走 `/chosen` 属性（板级 ADT）。

### 阶段 0：无真机可完成（先做完再上机）

| 内容 | 规模 |
|---|---|
| `0x31` 帧与 TLV 分段的纯函数 + **用 `hsuart.trace` 回放校验**：拿真固件生成全部 TX，与跟踪前 21 字节逐条比对 | 约 400 行 C + 200 行测试 |
| GENI UART 寄存器层（FIFO、流控、波特率）+ 主机模型 | 约 400 行 + 测试 |
| IBS 状态机 | 约 150 行 + 测试 |
| H4 过滤层、`kern_control` 外壳；喂入模拟 `bluetoothd` 序列 | 约 500 行 + 测试 |
| `text-fixups` 校验脚本、种子改动清单 | 小 |

**这一步的回放校验是最值钱的**：不上机就能证明下载流程逐字节等于 Android。

阶段 0 约 1450 行，是下面各阶段的共用部分；以下行数都只算该阶段新增的板级和集成代码。

### 阶段 1：上电、UART、握手（1–3 轮）

新增：约 500 行（复用 `WifiBringup` 的轨道规则、`WifiPmic` 的 SPMI、`sm8750_gcc_vote_set` 的写法）。

| 步 | 动作 | 成功判据 |
|---|---|---|
| 101 | 只读：GCC 投票、轨道、TLMM、PMIC gpio3 | 日志完整 |
| 102 | 开 `0x52010` 外壳和 SE6 时钟，读 `GENI_FW_REVISION` | 协议域 = UART，版本 0x66 |
| 201 | 投轨道（规则见下）、`rfclka1`、睡眠时钟，不动 BT_EN | S7I 不降，UFS 不报错，能进桌面 |
| 202 | `BT_EN` 低 ≥54 ms，再高，等 54 ms | PMIC 读回；`gpio18` 状态有记录 |
| 301 | RCG 7.3728 MHz，TLMM 切功能 1，115200，硬件流控 | CTS 可读 |
| 302 | 发 `0xFD`，最多 10 次 | **收到 `0xFC`** |
| 303 | 发 `31 00 f0 ff 02 05 00` | **收到 `34 00 …`**，原样记录 |
| 304（可选） | 发 `01 03 0c 00` 看反应 | 只记录，不作判据 |

### 阶段 2：固件下载与 HCI（2–3 轮）

新增：约 300 行（`FatVolume` 读文件、下载流程、波特率切换）。

- 先在 115200 跑完全程验证逻辑：323 KB 约 28 s；通过后切 3 Mbps（码 14），再切 8 Mbps（码 0x15，RCG 128 MHz）。
- NVM 发送前改 Tag 2 为本机地址。
- 判据：`Read_Local_Version` 得 `0e 0000 0e 1d00 217e`；版本串含 `BTFW.BRAHMA`；`Read_BD_ADDR` 等于设定值。

### 阶段 3：射频自检（1 轮）

新增：约 150 行，默认关。内核发 `LE_Set_Scan_Parameters/Enable`，统计 60 s 广播数。
判据：周围有手机或手环时计数大于 0。这一步不依赖任何 iOS 组件，通过后才有理由碰 `bluetoothd`。

### 阶段 4：接上 `bluetoothd`（2–4 轮）

- 内核（新增约 200 行）：`kern_control` 接线、`bluetooth` 服务、`/chosen/mac-address-bluetooth0`、`/product/bluetooth-le`。
- 镜像：F2、F3；种子 `--enable com.apple.bluetoothd`。
- 先用 `paganidiag` 在用户态连控制套接字发 `HCI_Reset`，验证通路，再放开真任务。
- 判据：日志出现 `Done starting up stack`；开机不被拖慢；MG `bluetooth=1`；设置页开关可切；过滤层列出全部 VSC。
- 单变量：每轮只放开这一件，保留 `pagani-reset-after`。按记忆里的约定，这些变更要和其他修复一起打包上机，不发零碎测试包。

### 阶段 5：配对与 HID（2–4 轮）

- 判据：BLE 键盘配对、打字；鼠标或手柄可用；经典 HID 设备可用。
- 配对库要进持久 Data，否则重启丢失。
- 这里最可能暴露 Keychain 依赖。

### 阶段 6：A2DP（2–5 轮，依赖音频线）

- 前提：iOS 音频路由可用。
- 删掉 `BTAudioHALPlugin` 那条 fix-up，确认 `audiomxd` 不再忙重试。
- 判据：耳机出声，连续 30 分钟不断流。

### 阶段 7：中断与功耗（2–3 轮）

- RX 改中断（SPI 461，走 `armcpu_spi_*`，Wi-Fi 的 SPI 141 有先例），IBS 真正睡眠。
- 不做这一步会有常驻 1 ms 轮询，可能加重本轮用户反馈的 jitter，所以**阶段 4 正式打开前至少要把空闲轮询降下来**【推断】。
- SE DMA 不是必须：FIFO + 硬件流控时吞吐上限约 256 KB/s（按 256 字节 FIFO、1 ms 周期估算），够用【推断】。

### 风险

| 风险 | 对策 |
|---|---|
| **S7I 带 UFS**：低模式带载或误降压可能拖垮存储 | 完全沿用 Wi-Fi 规则：读不到不投、只升不降、总投高功率模式、绝不 `enable=0`、投后读回；任何异常停在 `BT_EN` 之前 |
| 和 Wi-Fi 抢轨道和时序 | 轨道投票做成共用、幂等；`BT_EN` 与 `WLAN_EN` 动作隔 ≥100 ms |
| 共享 GCC 投票 | `0x52010` 目前无人用，仍走中断关闭的读改写；RCG 用 GPLL0，要确认已运行 |
| 错写 PMIC | 只写 PPID `0x58a`，先查 owner 和子类型，与 `pw_pmic_sleep_clock` 同样的检查 |
| 芯片挂死 | 全在 RAM，拉低 `BT_EN` 恢复；不发 OTP 写命令，不碰 FMD SDAM |
| 永久损伤 | 本方案没有任何写闪存、写 OTP 的步骤 |
| `bluetoothd` 拖住系统 | 单变量放开，有 `reset-after` 兜底 |
| VSC 与高通号冲突 | 0x3F 一律不透传 |

---

## 5. 最终能到什么程度

本节全部是判断【推断】，没有一项在 XNU 上跑过。

| 功能 | 把握 | 说明 |
|---|---|---|
| 控制器上线，HCI 通 | 较高 | 字节流已知，固件现成 |
| 设置里蓝牙可开关、可搜索 | 中等 | 靠 `bluetoothd` 对 VSC 失败的容忍，静态看有利，要实测 |
| BLE 键鼠、手柄、心率带 | 中等 | 依赖 GATT/HID 路径和 Keychain |
| 经典蓝牙键鼠 | 中等 | 同上 |
| App 用 CoreBluetooth | 中等 | 随 `bluetoothd` 一起得到 |
| A2DP 耳机/音箱 | 中等偏低 | 蓝牙侧没障碍，卡在 iOS 音频 |
| LDAC/aptX | 不可能 | iOS 无编码器 |
| HFP 通话、耳机麦克风 | 低 | 见第 3 节 |
| 隔空投送、接力、个人热点 | 不做 | 要 AWDL、账号与苹果私有能力 |
| AirPods 专属功能、手表配对 | 不做 | 基础 A2DP 或许可用 |
| 查找网络、低功耗广播（Find My） | 不做 | |
| 蓝牙长时间待机省电 | 要到阶段 7 | |

对“接近日用”的判断：蓝牙键盘和耳机是有意义的增量，但**优先级低于 Wi-Fi、输入卡顿和存储**。蓝牙把握最高的前半段（阶段 0–3）和 Wi-Fi 共用电源工作，可以并行低成本推进；阶段 4 以后再看音频进展决定。

可行性等级：UART+HCI **B–C**，`bluetoothd`+HID **C**，A2DP **C–D**，HFP **D**。

---

## 6. 需要用户决定或提供

1. 是否接受用 `text-fixups` 修补 `bluetoothd` 两处（不改文件、不重签，只改内存页）。
2. 蓝牙地址：用 Android 真实地址（要从手机读出后写进镜像，涉及隐私）还是生成固定地址。不同地址意味着已配对设备要重配。
3. 是否同意**一次性**在 Android 上放大跟踪缓冲并开关一次蓝牙，抓全 RX：`echo 65536 > /sys/kernel/tracing/instances/hsuart/buffer_size_kb`，然后关开蓝牙，立刻读 `trace`。这会写 tracefs（重启即失效），本轮规则不允许所以没做。价值很高：能提前看到 `0x34` 事件的真实内容和应答时序，阶段 1 可少走一轮。
4. 启动参数只剩 4 字节，测试包要去掉哪些项，或者是否接受改用 `/chosen` 属性。
5. 蓝牙与 Wi-Fi 的先后：建议先跑 Wi-Fi 的 `pagani-wifi=2 pagani-wifi-stop=204`，它同时验证蓝牙要用的轨道。

---

## 7. 证据索引与复现

本轮所有手机操作都是只读，`su` 仅用于读文件。

**设备变更说明**【实测】：本轮前段 adb 连的是 13T（`PKX110`，序列号 `SERIAL_13T`，当次 11:02 开机）。收尾时发现 USB 上已换成另一台设备（`OPD2513`，序列号 `SERIAL_OTHER_DEVICE`，已运行 3 天），13T 不在线；不是本轮操作造成的，本轮没有执行任何重启命令。

- 最后一次确认到 13T 是 `ro.boot.prjname = 24821`，它在 `btpower.ko` 拷出之后，所以报告里的跟踪、固件、日志、属性、轨道数据全部来自 13T。
- 只有一条 `sha256sum` 落在了新设备上，结果与 13T 副本不符，已弃用；1.6 节的校验值是用 11:16 从 13T 拷出的副本重算的。另外对新设备只做过 `getprop`、`uptime`、`ls` 这些身份确认。
- 两台设备固件文件名相同但内容不同：**上机前必须按序列号或 `prjname` 确认目标**，不要只看 adb 有设备。`hsuart` 跟踪在 13T 下次重启前仍有效，前提是它当前还在原来那次开机上（未核实）。

| 目的 | 命令或位置 |
|---|---|
| 串口跟踪（**到下次重启前有效**） | `adb exec-out 'su -c "cat /sys/kernel/tracing/instances/hsuart/trace"'` |
| 固件列表 | `adb shell 'su -c "ls -la /vendor/bt_firmware/image /vendor/firmware_mnt/image"'` |
| 串口统计与版本 | `/proc/tty/driver/*`；`/sys/devices/platform/soc/8c0000.qcom,qupv3_2_geni_se/898000.qcom,qup_uart/{ver_info,xfer_mode}` |
| 上电日志 | `dmesg \| grep -E 'bt_power\|BTON\|SOC Version'` |
| 设备树 | `build-userspace/a830-inputs/dt/running.dts` 第 25429、42409、42477、10175、8249–8536、11569 行 |
| 反编译锚点 | `bluetoothd`：`start`、`sub_1001D104C`、`sub_1000DDAA8`、`sub_10031AD30`（`0x10031b230`）、`sub_1002EF860`、`sub_1002F05F4`、`sub_100438790`、`sub_100046308`、`sub_1002D612C`、表 `0x1008D4640`；高通 HAL：`PeriPatchDLManager::{PeriSocInit,WakeUpSoC,FramePeriHciPkt,SetPeriBaudRateReq,ReadHciPeriEvent}`、`PatchDLManager::SocInit` |
| 主线事实（只取事实） | `gcc-sm8750.c`、`pinctrl-sm8750.c`、`clk-rpmh.c`、`btqca.h` |

草稿在系统临时目录，含拷出的固件和 HAL，未进任何项目路径；重启会话后会清空，需要时按上表重取。报告里未写入本机 BD 地址。
