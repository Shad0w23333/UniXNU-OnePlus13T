# 早晨：把 peach Wi‑Fi 固件放进 RAM Data 种子

> **2026-11-01 更正，以这里为准**（依据 `../round-1101/wifi.md`）：
> - **固件位置已经确定**：全部文件都在 modem_a，就地读已用真实卷验证（7 个文件 sha256 和手机一致），不用拷进种子。`amss` 放进种子还会超过 95% 的载荷预算线，别放。
> - **但 BDF 和 regdb 要放**：安卓实际用的是 `/odm/etc/wifi/peach/{bdwlan.elf,regdb.bin}`，内容和 modem_a 不同，内核读不了 `/odm`（erofs）。PaganiWifi 现在对这两个文件**先读种子**，并等 Data 挂载（最多 60 s），种子里没有才回退 modem_a。
>   本地已有一份，sha256 和手机一致：`wifi/odm-firmware/peach/`（专有文件，这个目录不在任何仓库里）。
> - **wifid 不要 `wlan` 节点**，它已经在跑；`join.txt` 不是唯一入网办法，只是免手输。下面第 0、3、5 节里相反的说法作废。
> - **预制种子可以加文件**：第 4 节“已有的预制种子不能再加文件”不对，`make-phone-seed.py --disable-jobs "" --wifi-firmware DIR s6-final.dbrdisk s7-wifi.dbrdisk` 能串一步，2026-11-01 在主机上试过（种子只大 12 KB），owner trust 不变。
> - **第 4 节的集成差异都已打上**，不用再打。出包走 `fullrun/make-wifi-rounds.sh`。
>
> 现在的做法只有一步（密码在终端输入）：
>
> ```sh
> python3 build-userspace/a830-inputs/wifi/make-wifi-firmware-dir.py - build-userspace/a830-inputs/wifi/wifi-firmware-seed \
>     --join-ssid '<网络名>' --odm build-userspace/a830-inputs/wifi/odm-firmware
> bash build-userspace/a830-inputs/fullrun/make-wifi-rounds.sh seed
> ```

更新：2026-10-31（工作流步骤 wifi-port）。配套：`morning-facts.sh`（只读取证）、`make-wifi-firmware-dir.py`（整理成种子布局）、
`integrate/make-phone-seed.py.diff`（种子脚本的 `--wifi-firmware`）、`integrate/make-a830-layout.sh.diff`（`A830_WIFI_FW`）。

规则：手机上不写任何东西；固件是高通/一加的专有文件，只留在这台 Mac 的本地目录和本地种子里，**绝不进任何仓库**。

## 0. 先判断要不要种子

PaganiWifi 找固件的顺序是：**先 modem_a 分区的 FAT（就地读，`image/peach/` 再 `image/`），再 RAM Data 种子**
（`/private/var/ngpu/firmware/wlan/`）。所以：

- 如果早晨核对（M9）显示固件就在 modem_a（安卓挂在 `/vendor/firmware_mnt`，路径形如 `/vendor/firmware_mnt/image/peach/amss20.bin`），
  固件不用拷，镜像里也不放固件。`join.txt` 例外：它只从种子读，**没有 wifid 时它是唯一的入网办法**（见 `review.md` 第 6 节），
  只放它一个文件时用 `make-wifi-firmware-dir.py - 目录 --join-ssid 名字`。
- 如果固件在 `/vendor/firmware/...`（super→vendor，erofs，内核没法就地读），就按下面步骤把它放进种子。
- 只缺个别文件（例如 `tmel_peach_20.elf` 不在 modem_a），也可以只把缺的放进种子：分区里没有的才会去种子里找。

## 1. 早晨只读取证并拉回固件（需要用户同意、手机进安卓、adb 已授权、su 可用）

```sh
cd /Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/wifi
DEBUGFS=1 PULL_FW=1 sh morning-facts.sh            # 输出在 phone/<日期时间>/
```

- 最好开机后几分钟内跑（cnss 开机日志会被冲掉）。脚本全程只读（`su -c sh` 里只有 ls/cat/sha256sum/strings/getprop 一类）。
- 看 `phone/<时间>/SUMMARY.txt` 的 M8/M9：固件的完整路径、大小、sha256、所在挂载点。
- `phone/<时间>/firmware/` 是拉回的原样文件（文件名是手机路径把 `/` 换成 `_`），`D5-wlan-firmware-sha256.txt` 是手机上算的 sha256。

如果脚本的 su 调用方式在真机上不灵（今晚只在假 adb/su 上演练过），手工等价做法（同样只读）：

```sh
adb shell su -c 'ls -la /vendor/firmware_mnt/image/peach /vendor/firmware_mnt/image /vendor/firmware/peach 2>/dev/null'
adb exec-out "su -c 'cat /vendor/firmware_mnt/image/peach/amss20.bin'" > amss20.bin
adb shell su -c 'sha256sum /vendor/firmware_mnt/image/peach/amss20.bin'     # 对照本地 shasum -a 256 amss20.bin
```

## 2. 需要哪些文件

| 种子里的名字 | 来源（安卓 cnss2 的请求名） | 必需 |
| --- | --- | --- |
| `peach/amss20.bin` | `peach/amss20.bin`（回退 `amss20.bin`） | 是（第 5、6 阶段） |
| `peach/bdwlan.elf` 及变体 `bdwlan.b0e/.b0i/.b0a/.e<id>` | 板级数据；国行默认 `bdwlan.elf`，实际选中哪个看 M12/M13 | 是（第 6 阶段） |
| `peach/regdb.bin` | `peach/regdb.bin` | 是（第 6 阶段） |
| `tmel_peach_20.elf` | 无 `peach/` 前缀 | 第 6 阶段 `pagani-wifi-tme=1`（默认）要 |
| `peach/phy_ucode20.elf` | M3 | `pagani-wifi-m3=1`（默认）要 |
| `peach/aux_ucode20.elf` | AUX 微码 | `pagani-wifi-aux=1`（默认）要 |
| `peach/qdss_trace_config_perf_v2.cfg`（或 `…_v2.cfg`） | QDSS 配置 | 仅 `pagani-wifi-qdss=1` |
| `join.txt` | 本地生成：第一行网络名，第二行密码（空行＝开放网络） | 没有 wifid 时入网必需（`pagani-wifi-aj=1`，默认）；只支持 WPA2-PSK/CCMP 或开放网络 |

## 3. 整理成种子布局（本地）

```sh
cd /Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/wifi
python3 make-wifi-firmware-dir.py phone/<时间> wifi-firmware-seed --join-ssid '<家里的 Wi-Fi 名>'
# 密码在终端提示输入，不写进命令行（--join-open：开放网络）。不给 --join-ssid 就不生成 join.txt：
# 没有 wifid 的镜像里也就不会入网，设置 App 这时没有 Wi‑Fi 页（wifid 要 ADT 的 wlan 节点才启动）。
```

脚本逐个核对本地 sha256 与手机上 `sha256sum` 的结果，不一致就停；输出 `wifi-firmware-seed/peach/…`、`tmel_peach_20.elf`、
`join.txt`、`MANIFEST.json`。目录名 `wifi-firmware-seed` 只是本地目录（`build-userspace` 不是任何仓库），不要拷进仓库。
`join.txt` 是明文密码（0600）：带它的种子和镜像都只留本机，不要外传；建议用一个专门的测试网络。

## 4. 做带固件的种子与镜像

先把两个集成差异打上（只改本地工具，不动设备仓库）：

```sh
cd /Users/xiaochoumao/unixnu-13t
patch -p1 < build-userspace/a830-inputs/wifi/integrate/make-phone-seed.py.diff
patch -p1 < build-userspace/a830-inputs/wifi/integrate/make-a830-layout.sh.diff
patch -p1 < build-userspace/a830-inputs/wifi/integrate/make-phone-seed-join.diff     # join.txt 用 0600，摘要不进报告
```

然后照常出包，只多一个环境变量（其余变量照旧）：

```sh
A830_WIFI_FW=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/wifi/wifi-firmware-seed \
A830_EXTRA_ARGS='pagani-wifi=1' bash 工具/make-a830-layout.sh phone-apps     # 或 phone-plus
```

- 只有 `phone-plus`、`phone-apps`（及其 island 变体）这两类会新做 Data 种子，`A830_WIFI_FW` 只在它们上起作用；
  `A830_EXTRA_ARGS` 的每一项都变成 `--kernel-arg`（`make-a830-layout.sh` 第 263 行）。
- 内核里要有 PaganiWifi：先按 `integrate/` 的 `components.toml.diff`、`board.toml.diff`、`Stage7Probe.cpp.diff` 接好再编内核
  （PaganiWifi 默认关，接进去但不给 `pagani-wifi=N` 时行为与现在完全一样）。

- `make-phone-seed.py --wifi-firmware DIR` 把目录原样（保留 `peach/` 子目录）拷到种子的 `/private/var/ngpu/firmware/wlan/`，
  每个文件 0644（`join.txt` 0600），回读比对，sha256 写进种子旁边的 `.json` 报告（`join.txt` 除外）；总量超过 64 MiB 拒绝。
- **容量**：种子是 2 GiB 的易失 RAM 盘，但打包后要放进 192 MiB 的启动负载（`board.toml [payload] size_mib`）。peach 固件合计
  估计 10–20 MB（M8 给出准数），出包前看 `.json` 里的 `used_kib` 和打包大小；放不下就只放 modem_a 上没有的文件。
- 已有的预制种子（`A830_PREBUILT_SEED`）不能再加文件：要用 `A830_WIFI_FW` 就得从基础种子重新做。

## 5. 上机顺序（每次一个变量，`pagani-ramoops=1` 看 `[wifi]` 行）

以 `review.md` 第 7 节的单变量顺序为准，这里只是摘要：

1. `pagani-wifi=1`：只读勘察，确认引脚、七路电源、PHY 的四路 LDO、睡眠时钟脚与事实表一致（不需要固件）。
2. `pagani-wifi=2`、`=3`、`=4`：上电、链路、端点（不需要固件）。卡住时看 `pagani_wifi_status[0]` 的负步号和 `[wifi] FAIL` 行。
3. `pagani-wifi=5`：自带 MHI 引导到任务模式（需要 `amss20.bin`：modem_a 或种子）。默认不做芯片侧写入；失败再加 `pagani-wifi-chipreset=0x79`。
4. `pagani-wifi=6`：要用带协议栈（`--with-ath12k`）的内核，否则立刻报 `[wifi] FAIL step=601 err=-78`、什么都不上电。
   需要第 2 节的全部文件；必要时配 `pagani-wifi-m3=0`、`pagani-wifi-qdss=1`、`pagani-wifi-cbc=0` 逐个试（facts.md 第 7 节的争议点）。
5. en0 起来、扫描能看到网络之后，再考虑第二步：`integrate/board-adt-wlan.diff`（ADT 的 `wlan` 节点，让 launchd 启动 wifid）。

种子是 RAM 盘：每次开机都是同一份内容，什么都不会写回手机；拔掉 `A830_WIFI_FW` 重新出包，固件就不在镜像里了。
