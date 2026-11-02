# 免拍照上机回路（gpu-test-loop）

思路照搬 REDMI K90（annibale）“日志走回电脑、软件复位”的快回路，但不需要常驻 loader：XNU 把日志写进安卓自己的 ramoops 保留区，到点自己热复位回安卓，电脑用 adb 从 `/sys/fs/pstore` 读回、校验、合并。一轮只需要“临时启动镜像 → 等它自己回安卓 → 读日志”，全程不用拍照。

## 组成

| 位置 | 作用 |
| --- | --- |
| `UniXNU-Pagani-Drivers/drivers/BootProgress/RamoopsLog.{c,h}` | 内核端写入器：`pagani-ramoops=1` 时把同一份日志写成 36 份（布局 3：每份 `0xfffc` 字节，console 区 `0x880000000` 放 4 份，pmsg 区 `0x880040000` 放 32 份，偏移一致），任一区被 Linux 丢弃，另一区仍有足够的份投票；两个 Linux 区头只在启动时写一次（start 0、size 为整区）；`pagani-reset-after` / `pagani-panic-reset` 定时热复位 |
| `UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ramoops.c` | 写入器主机测试（编码、CRC、份大小与份数规则、份头、环形回绕、Linux 读取规则、复位路径） |
| `read-ramoops.py` | 读取器：adb + su 读出两个 pstore 文件，找出全部份（新布局 36 份，旧布局 9 份）、按位投票、用 CRC 校验与修复，写证据目录（只读，不重启手机） |
| `gpu-loop.py` | 一轮回路：安卓 → fastboot → `fastboot boot` → 等回安卓 → 读日志 → 打印 GPU 阶段与状态行；不加 `--yes` 只打印计划 |
| `ramoops_format.py`、`ramoops_device.py` | 记录格式、两种份布局（3 与旧的 2）、找份（份头比对、旋转还原）、按位投票、CRC 修复、Linux 的 pstore 读取模型；adb/fastboot 与证据目录 |
| `UniXNU-Pagani-Drivers/drivers/BootProgress/CrashTrace.h` + 设备仓库 `patches/xnu/crash-trace.patch` | 崩溃定位：进程死于致命信号时记录出错线程的寄存器和最多 16 层帧链（`X` 记录，见下文“崩溃定位”） |
| `symbolize-crashes.py` | 把 `X` 记录里的地址符号化成“函数 + 偏移”（共享缓存用 ipsw 的 a2s 表，其他映像用 Mach-O 文件） |
| `UniXNU-Pagani-Drivers/drivers/BootProgress/UfsLog.{c,h}` | UFS 日志写入器：`sheng-log=1` 时把 ramoops 副本的快照写进 unixnu 分区末尾的日志窗口，见下文“UFS 日志” |
| `read-ufslog.py`、`ufslog_format.py` | UFS 日志读取器（只读）：窗口头、槽、按序号合并、导出解包，并用分区表核对窗口位置；`gpu-loop.py` 和 `read-ramoops.py` 也会调用 |
| `UniXNU-Pagani-Drivers/drivers/BootProgress/StallProbe.h` + 设备仓库 `patches/xnu/phone-stall-probe.patch` | 卡顿探针：`pagani-diag` 非 0 时统计调度延迟、IPI、空闲核，卡顿和键盘慢时采样关键线程（`M`、`Q` 记录，见下文“卡顿探针”） |
| `stall_format.py`、`stall-report.py` | 解析 `M`、`Q` 记录；把一轮日志排成时间线，给出最严重的 20 次卡顿的结论表 |
| `tests/`、`run-tests.sh` | 主机测试（不碰手机） |

## 镜像要求（启动参数）

| 参数 | 含义 |
| --- | --- |
| `pagani-ramoops=1` | 开日志（`=3` 另记显示探针行，最多每 10 秒一组；`+4` 改用 Device 映射，作对照实验用） |
| `pagani-reset-after=<秒>` | 日志开始后这么多秒无条件热复位（CPU 定时器，1 Hz 观察线程兜底）。回路镜像必须带 |
| `pagani-panic-reset=<秒>` | panic 画面停留这么多秒后复位；不设时若有 `pagani-reset-after` 则等到它的期限（至少 5 秒），两者都没有则照旧停住 |
| `pagani-reset-cold=1` | 不用 SYSTEM_RESET2 热复位，改用 SYSTEM_RESET（热复位若进了 CrashDump 之类再用） |
| `pagani-ramoops-copy=<字节>` | 每份大小的上限（默认 `0x10000`，13T 上得到 4 + 32 份 `0xfffc`）。`0x8000` 得到 console 区 8 份、pmsg 区 56 份（每份 `0x7ffc`），事件环和控制台环各缩到约 14 KiB；低于 16 KiB 的份拒绝开启。解码器不需要额外参数 |
| `pagani-ramoops-pa/-console/-pmsg` | 安卓 ramoops 区以后若挪了位置，用它覆盖（默认 `0x880000000`/`0x40000`/`0x200000`） |
| `pagani-crash=<0/1/2>` | 崩溃记录（需要内核带 `crash-trace.patch`）：默认 1，同一进程名每 10 秒最多一次、30 秒内相同调用链只记一行 `same`；`0` 关闭；`2` 每次崩溃都完整记录 |

板级默认（补丁 `device-board-kernel-args.patch`）已带 `pagani-ramoops=1 pagani-panic-reset=20`；回路镜像再加 `--kernel-arg pagani-reset-after=<秒>`。屏幕上 S6 之后会多一行 `ramoops on 0x880000000 copies 36 x 0xfffc ...` 说明配置。

## 用法

前提：手机在安卓、USB 调试已授权、KernelSU 已给 shell 授予 root；电脑上有 `adb`、`fastboot`。

```sh
cd /Users/xiaochoumao/unixnu-13t/工具/gpu-test-loop
# 先看计划（不碰手机），会检查镜像旁 *.payload.json 里的启动参数
python3 gpu-loop.py --image ../../UniXNU/out-gpu/a830-s3-loop.img --label s3
# 真正跑一轮（会重启手机）
python3 gpu-loop.py --image ../../UniXNU/out-gpu/a830-s3-loop.img --label s3 --yes
# 只读日志（手机已在安卓，比如手动复位后）
python3 read-ramoops.py --label manual
# 离线重新解码保存过的文件（新旧布局都能解，自动识别；也接受整区原始镜像 console.zone / pmsg.zone）
python3 read-ramoops.py --from-dir runs/20261030-101203-s3-annibale-1 --runs /tmp/redecoded
```

每轮写一个证据目录 `runs/<时间>-<标签>/`：两个原始 pstore 文件 `*-ramoops-0.bin`（被 Linux 丢弃的区没有）、逐条记录 `records.jsonl`（含来源：投票即通过 / 修复方式 / 未核验）、合并日志 `merged.log`（未核验的行在类别后标 `?`）、摘要 `summary.txt`（布局与份数、最后的阶段、GPU 阶段与状态行、异常、panic 全文、复位路径、最后的控制台行）、统计 `decode.json`、手机信息 `device.txt`。

## 记录格式与份布局

每条一行：`#SSSSSSSS MMMMMMMM LL CCCCCCCC K 文本`。S 序号、M 日志开始后的毫秒、L 文本长度（≤200，更长的行拆成 `+` 续行）、C 为 CRC32（zlib，覆盖除 CRC 字段与换行外的全部字节）、K 类别：

`B` 日志头 · `A` 启动参数 · `S` 阶段历史 · `R` 根设备/显示/GPU 状态阶段 · `C` 控制台 · `+` 续行 · `G` GPU 状态行（`组号.行/行数 文本`）· `D` 显示探针行 · `E` 内核异常 · `P` panic 画面全文 · `H` 每 10 秒心跳 · `T` PSCI 与复位路径 · `U` 驱动的 `boot_progress_trace` · `W` 告警 · `F` 帧率 · `J` 卡顿 · `X` 进程崩溃（事件环）· `M` 卡顿探针的每秒汇总（控制台环）· `Q` 卡顿探针的采样（事件环）

**布局 3（现在）**：每份 `0xfffc` 字节，console 区 4 份、pmsg 区 32 份，36 份逐字节相同（份号除外）。一份之内：

| 范围 | 内容 |
| --- | --- |
| `[0, 0x40)` | 份头：魔数 `UXNULOG3`、版本 3、份号、总份数、运行号、份大小、事件环位置与大小、控制台环大小、两区大小、区域物理地址、两区各自份数（偏移 56：低 16 位 console、高 16 位 pmsg）、CRC32（偏移 60，覆盖 `[0,12)+[16,60)`，不含份号） |
| `[0x40, 0x1000)` | 引导区：日志头和启动参数，只写一次；写满后溢出到事件环 |
| `[0x1000, 0x9000)` | 事件环 32 KiB：阶段、状态行、`boot_progress_trace`、异常、panic、复位（量小，不会被控制台刷掉） |
| `[0x9000, 0xfffc)` | 控制台环 `0x6ffc` 字节：控制台行和每个进程的 exec/exit 阶段 |

区大小被覆盖时，份大小取“不超过 64 KiB、又能铺满每个区”的最大值（例如 console 区 `0x20000` 时为 `0xfff8`，2 + 32 份），总数最多 64 份；份不足 16 KiB 时拒绝开启。

**布局 2（2026-10-30 首轮回路及更早的镜像）**：每份 `0x3fff4` 字节，console 区 1 份、pmsg 区 8 份，魔数 `UXNULOG2`，事件环 64 KiB，CRC 在偏移 56。解码器自动识别，旧证据照样能解。

改布局的原因：首轮回路里 pmsg 区头的 size 字段发生翻转，Linux 丢弃了整个 pmsg 区，只剩 console 区 1 份，8% 衰减下 298 条里 297 条无法核验。布局 3 下丢掉 pmsg 区还剩 4 份、丢掉 console 区还剩 32 份。

区头衰减对 Linux 的影响（写入器只能把区头写对，管不了复位时的衰减）：size 丢位 → 文件变短（末尾的份残缺或缺失）；start 多出一位且不超过 size → 文件被旋转；sig 损坏或 size 多出一位 → 整区被丢弃，没有文件。

丢位是固定的弱单元，不是每次随机：截至 2026-10-30 的 4 轮真实证据里，console 区第一份的份头每次丢的是同一组 8 个位（手动复位、断电更久的那轮多丢 1 个），console 文件每次都被截成同样的 262036 字节，有 dmesg 的 3 轮里 Linux 每次都以同一个值 `size 18283476` 丢弃 pmsg 区。所以 pmsg 区很可能每轮都会被丢弃，实际起作用的是 console 区的 4 份；各份位于不同地址，弱单元落在份内不同偏移，投票仍然有效。主机模拟（每份独立注入）：只有 console 区 4 份时，1→0 为 8%/10%/12%/15% 分别核验 99.9%/99.7%/98.8%/91.3%；6 份及以上到 15% 仍是 100%。若真机上 console 区单独解码明显不全，给镜像加 `pagani-ramoops-copy=0x8000`（console 区 8 份）。

## 解码

实测（`evidence-2026-10-29-after-F1.bin` 与首轮回路）：复位后约 8% 的“1”位丢失，几乎全是 1→0。解码器：

1. 找布局和份：分别按布局 3 和布局 2，在两个文件的每个份偏移处用衰减模型比对 64 字节份头（期望的“1”位对空白数据、期望的“0”位对随机数据各算一次似然比，两项都要达到正常衰减份头的一半）；文件被旋转时找回对齐；份头对不上的份不参加投票；取匹配最好的布局（区大小另按文件长度推测一次，以应对覆盖过区大小的镜像）；
2. 读份头：任一份头 CRC 通过即用；否则对全部份头逐位投票；再不行用 CRC 解出投票结果里最没把握的位；仍不行就用第 1 步匹配上的布局的默认值；
3. 逐位投票：先从数据估计 1→0 和 0→1 的概率，再按每个字节实际覆盖到的份数 n 取最大似然阈值（8%/0.5% 下：36 份 ≥12 票、32 份 ≥11 票、9 份 ≥3 票、4 份 ≥2 票为 1），所以少数几份保住的“1”能战胜多数丢失；文件变短时，末尾字节按剩下的份数投票；
4. 投票后各区里 CRC 通过的记录直接采用；
5. 不通过的先还原必然成立的部分（标记、空格、长度、由前后记录推出的序号、7 位 ASCII、十六进制数字的第 5 位），再依次尝试：另错 1 位；32 个最没把握的位中至多 3 位；16 个最没把握的位作为未知量解 GF(2) 方程；一个所有份都丢了的“1”加至多 2 个没把握的位；CRC 字段本身损坏；把 CRC 字段里有分歧的位翻转后重试。每个结果都必须 CRC 相符、类别已知、文本可打印、序号和时间落在前后完好记录之间；同一步得到两条不同结果时都不采用；
6. 仍不通过的按“未核验”照样输出，在 `merged.log` 里类别后面标 `?`，摘要单独计数，不混入已核验记录。

主机测试（写入器导出的 383 条记录，每份独立注入 8% 1→0 和 0.5% 0→1）：两区都在时全部恢复（15% 也全部恢复）；只剩 console 区 4 份时 3 次都是 383 条全部核验通过；只剩 pmsg 区 32 份时全部恢复；文件变短、被旋转、全部份头损坏、其他区大小、重建的布局 2 镜像也都通过。误判压力测试（1 到 3 份、5% 到 12% 衰减，约 5 万条难解记录，其中 2.3 万条被修复）没有出现一条错误的“已核验”记录。

## 崩溃定位（`X` 记录）

内核带上设备仓库的 `patches/xnu/crash-trace.patch`（BootProgress 带 `CrashTrace.h`）后，进程因 SIGILL/SIGTRAP/SIGABRT/SIGEMT/SIGFPE/SIGBUS/SIGSEGV/SIGSYS 的默认动作退出时，`postsig_locked` 在出错线程上、退出开始之前，把它的用户态寄存器交给 BootProgress：沿帧指针链 copyin 最多 16 个返回地址（每步检查 8 字节对齐、用户地址范围、必须向高地址走且一步不超过 16 MiB，读失败即停；去掉 Swift async 位和指针认证位），把每个地址写成“映像 + 偏移”，在紧挨着 `S exit` 记录之前写几条 `X` 记录：

```
crash pid=797 SpringBoard sig=11 exc=1 code=0x1 sub=0x28 esr=0x92000006 far=0x28 tid=0x2b5
regs pid=797 pc=c:1a9463e64 lr=a:1200 fp=0x16f000200 sp=0x16f000100 x0=0x0 x1=0x0 cpsr=0x60000000
map pid=797 slide=0x2a7c000 main=0x100a4c000 hash=712d0d20 skipped=0
img pid=797 m=SpringBoard a=NativeAdrenoMetal
bt pid=797 0 a:1234 c:1b0000010 m:5678 end=end
same pid=799 SpringBoard sig=11 hash=712d0d20 as=797 skipped=1
```

地址写法：`c:` 共享缓存里的未滑动地址（已减去 slide，可直接查 21H461 的符号）；`m:` 相对主程序 Mach-O 头的偏移；`a:`～`d:` 相对 `img` 行里该映像头的偏移；`a@` 该映像的文件偏移（映射不是从 Mach-O 头开始时）；`?:` 原始地址（匿名内存、JIT 或垃圾值）。`exc/code/sub` 是线程最后一次 Mach 异常：对段错误等是本次故障；对 abort（`esr` 的 EC 为 0x15，即 `pthread_kill` 系统调用发来的信号）是陈旧值。`end=` 说明帧链为何结束：`end` 正常到底，`max` 满 16 层，`read/order/gap/align/range` 为异常，后面带停下时的帧指针。限流：同一进程名 10 秒内只记第一次（其余计入下次的 `skipped`），30 秒内调用链哈希相同只记一行 `same`（`as=` 指向那份完整记录）；`pagani-crash=2` 关掉限流。事件环实测只保留约 55～66 秒（GPU 状态行占大头，加上 X 记录还会更短），所以完整记录每 30 秒至少重写一次，保证复位时事件环里还留着一份；早于事件环跨度的崩溃（例如复位前一分多钟的一阵崩溃）在事件环里留不下，需要时缩短 `pagani-reset-after`，让复位紧跟在要看的操作之后（`pagani-crash=2` 只会写得更多、冲得更快）。

符号化：

```sh
python3 symbolize-crashes.py runs/<目录> --a2s <ipsw 的 a2s 表>
# 还可以：--dsc <dyld_shared_cache_arm64>（ipsw dyld a2f，给出函数起点和所在映像）、--systemos-dmg <Cryptex1,SystemOS 的 dmg>（只读挂载后用于 --dsc，结束时卸载）、
#        --root <目录>/--image 名字=路径（主程序和缓存外映像的 Mach-O，例如插件 NativeAdrenoMetal.bundle 的构建目录）、--json <文件>
```

a2s 表是 `ipsw dyld a2f <dsc> --in <地址文件> --cache dsc.a2s` 第一次运行时生成的符号缓存（21H461：约 1296 万个符号，0.9 GB，约 40 秒）；脚本对它就地二分查找，不整份读入；也接受“十六进制地址 符号”的文本表（流式读一遍）。返回地址按“地址 − 1”查找。输出每个崩溃的信号、Mach 异常、ESR 解码、命名后的各帧，以及越过 abort/异常处理机制后的第一帧；末尾按进程列出 S 记录里的致命退出（没有 X 记录的轮次也有用）。`summary.txt` 也多了“FATAL EXITS”和“CRASH REPORTS”两节。

## UFS 日志（`sheng-log=1`）

XNU 自己触发的复位会到 PMIC 变成 HARD_RESET，DRAM 掉位，Linux 把 ramoops 两个区都丢弃，这时 pstore 里什么都没有。带 `sheng-log=1` 的镜像会把日志同时存到 unixnu 分区末尾：

| 位置（分区相对块号，每块 4096 字节） | 内容 |
| --- | --- |
| 16769024..16777215（最后 32 MiB） | 日志窗口：块 0 头（魔数 `PGLOG001`，带 CRC32），块 1..32 内核 msgbuf，块 33 起每槽 16 块，共 509 槽 |
| 16703488..16769023（其下 256 MiB） | 导出区：两半交替，头里写明已完整落盘的那一份（`SHEXR001`/`M2VCACH1`，见 `CacheExport.h`） |

槽里就是 ramoops 第 0 份副本的快照（每条记录有自己的 CRC），所以解码不需要投票，按序号合并所有槽就得到比环更长的日志。前 32 个槽写满后不再覆盖（保住开机），其余做环。

| 启动参数 | 含义 |
| --- | --- |
| `sheng-log=1` | 打开；没有它时不起线程、不分配内存、不写分区。内核命令行要多占 12 字节 |
| `pagani-ufslog-ms=<毫秒>` | 一轮的周期，默认 3000，下限 1000。有新记录才写；环写得快时提前，定时复位前 2 秒每 250 毫秒看一次 |
| `pagani-ufslog-panic=<0 或 1>` | 最后一次快照的开关（panic 时和每次复位前）。默认：有已武装的看门狗或 `pagani-reset-after` 时开，两者都没有时关，因为这时卡在总线上的 CPU 没有东西能复位手机。关掉后复位前仍会认领传输，只是不再发命令 |

每轮的顺序是槽、msgbuf、头、一次 flush。记录有 CRC，所以头先落盘也不会读到错的记录；导出没有校验，所以新导出的那一轮在写头之前多一次 flush。

每次由代码发起的复位（定时、`pagani-reset-wdog` 的主动咬、平台重启、panic）最先做的是静默：屏蔽中断，有界地认领 UFS 传输并不再释放，写最后一次快照（头状态 6，panic 是 5）再 flush，之后才走咬狗或 PSCI。认领最多等 1 秒，全程最多 2 秒，超时照常复位。这样复位不会落在日志的写命令中途。静默前先停止喂狗，总线卡死时靠看门狗咬；panic 时看门狗要等刷写结束才关。代码看不到的复位（挂死后的看门狗咬、固件复位、长按电源）仍可能落在写命令中途。

新一份导出要等头点名上一份之后才开始，否则正在写的那一半就是盘上的头还指着的那一半，这时返回 -8，渲染服务下个周期重试；首块不以 `SHEXR001` 开头时返回 -7，因为这样的块会让下次开机的内容闸门拒绝窗口。

统计记录 `ufslog rounds ... longest round ... ever ...; longest command ... ever ...` 每 30 轮一条：一轮从第一条命令算到最后一次 flush，不含快照；单条命令的时间包含等传输锁的时间。头里的 `max_round_us` 比统计记录晚一轮。

**上机前先核对**：`bDataReliability` 为 0 的逻辑单元，掉电时可能损失整个单元的数据，而不只是正在写的块。开 `sheng-log=1` 之前先在安卓下只读确认 LUN 0 的值：`adb shell su -c 'cat /sys/class/scsi_device/0:0:0:0/device/unit_descriptor/data_reliability'`（设备路径以实际 LUN 0 为准）。

读回：

```sh
python3 read-ufslog.py --label s3          # 单独读：写证据目录（含 msgbuf.txt、export/）
python3 read-ufslog.py --header            # 只看头
python3 read-ufslog.py --from-file 证据目录/ufs-window.bin.gz   # 离线重解
```

`gpu-loop.py` 对带 `sheng-log=1` 的镜像会在重启前读一次头、回安卓后读窗口；`read-ramoops.py` 总会看一眼窗口（`--no-ufs` 关掉）。日志不会被擦除，窗口里可能是上一轮的东西，所以每次都要判断：

| 结论 | 依据 | 证据目录 |
| --- | --- | --- |
| 本次运行的 | `boot_id` 和重启前不同，或重启前窗口确认是空白，或 ramoops run 和 pstore 的相同 | 顶层四个日志文件换成合并日志（UFS 优先，同一 run 的 pstore 补缺），原来的放进 `pstore/`，UFS 自己的完整证据在 `ufs/` |
| 旧日志（`OLD UFS LOG`） | `boot_id` 没变，或 ramoops run 和 pstore 的不同 | 顶层仍是 pstore，旧窗口只存在 `ufs/` 里 |
| 位置不对（`UFS LOG AT THE WRONG PLACE`） | 头里的 `first_lba` 不等于分区表算出的值（`/sys/class/block/sda16/start` 除以 8，加分区块数，减 8192） | 同旧日志。这说明分区动过，或内核写到了别处，要先查清再跑下一轮 |
| 无从判断 | 没读到重启前的头（`gpu-loop.py` 从 fastboot 开始时读不到），pstore 也给不出 run id | 打印 `WARNING`，只留在 `ufs/`，不进合并日志：要看就读 `ufs/` |

这台 512 GB 机器上 unixnu 是 107409152..124186367，内核应打印 `ufslog open at LBA 124178176`，导出区从 124112640 开始。44928768 起的那组数字是 256 GB 机型的，落在这台手机的 userdata 里。

头损坏时读取器按槽自身的份头恢复（位置 0 的槽总是最新一次运行的第一个槽），但找不到导出。

## 卡顿探针（`M`、`Q` 记录，`pagani-diag`）

内核带上 `patches/xnu/phone-stall-probe.patch`、启动参数有 `pagani-diag=1`（不必加新参数）时开启，否则钩子只多一次读取、不分配也不起任何东西。要回答的问题：严重卡顿和键盘慢的时候，关键线程到底在干什么；线程能运行却没上 CPU 的情况有没有发生；IPI 和空闲核有没有问题。记录的完整格式和每一步的并发说明在 `BootProgress/StallProbe.h` 开头。

| 记录 | 内容 |
| --- | --- |
| `M lat` | 这一秒“可运行到上 CPU”的延迟。用户线程按优先级带分：`fg`（≥47）、`def`（≥20）、`bg`；内核线程单独一带 `k`。次数、最大值、超过 10/50/200 ms 的次数，1 ms 以上的最大值带进程、线程和优先级（`k` 只有线程和优先级）；四带都到最长时放不下全部线程，数字一定在，线程只在还有余量时写 |
| `M cpu` | 每个 CPU：空闲进入次数、收到的 IPI 发送数、实际处理数、定时器中断数 |
| `M ipi` | 每个 CPU：最慢的 IPI（发送到处理）、空闲却面对非空运行队列的 tick 数、悬而未决超过 20 ms 的 IPI 数；采样器最长间隔；已查到的文件映射数 |
| `M io` | 根卷读次数和耗时、校验耗时、最小 free（采样器睡着的秒是当时的值）、avail、缺页、换入、压缩、解压、`pw`（`vm_page_wait` 真睡下去的次数）、Tier1/2/3 被限流次数，以及 `q 已用行/超速率/超预算`（有信号的 gap、idleq、ipi）、`kq`（键盘）和 `tq`（`ask=0` 的 gap），各三个数 |
| `M mem` | 每 5 秒一行（有 M 的秒才算，约占 console 环 3%）：`wire`、`int`（可换出匿名页）、`ext`（可换出文件页）、`cmp 驻留页/装着的页`、`thr`、`pur`、`zone`（都是 4 KiB 页），`ram` 内存盘驻留字节，`gpu 当前/峰值` GPU 池 MiB |
| `F` 行尾 | ` \| tch <这一秒触摸帧数> last <最后触摸距今 ms> vl <软件 vsync 迟到次数>` |
| `Q s=N why=...` | 一次采样的头：`gap110/200/400/1000/3000/8000`（有东西在等新帧而帧没来：最新一帧之后又见到触摸或 GPU 提交，或键盘配对未结束；从第一个信号起计时，每个阈值一次采样，直到新帧；首阈值 110 ms 而不是 100：桌面时钟图标是 10 Hz，主屏上每 100 ms 一帧是常态；`ask=2` 触摸或键盘，`ask=1` 只有 GPU 提交（可能是不上屏的工作，只在提交新鲜时算，只用低档预算）；只有帧前节奏时最多一次采样，`ask=0`，可能只是动画结束；光标闪烁不算；`age` 从卡顿开始算，`since` 从最新一帧算；`fg=` 是除 SpringBoard、backboardd 外处于 focal 角色的任务数，0 即主屏，-1 任务表正忙）、`dn/up/in`（键盘配对开始 300 ms 后未结束，之后每 500 ms 一次，采样 4 次后放慢到 1 s，8 次后 2 s）、`idleq`（某核连续 5 个 tick 空闲但运行队列非空）、`ipi`（IPI 悬而未决超过 20 ms） |
| `Q s=N th/bt` | 目标线程：状态、优先级、所在 CPU、等待时长、阻塞类型、等待事件、系统调用号、解码出的超时参数、内核返回地址；`bt` 是用户 PC、LR、帧链前 8 层。运行中的线程用跨核调用在它的 CPU 上取中断现场。`rs`=backboardd 的 `com.apple.coreanimation.render-server` 线程，`sb`=SpringBoard 主线程，`fg`=屏幕上 App 的主线程（gap 采样；RunningBoard 把 focal 角色给它，`TASK_FOREGROUND_APPLICATION`，每次采样重新问一遍，有几个时取最新的任务；主屏上没有这条，一次采样 7 行，有 App 时 9 行），`app`=键盘配对所属 App 的主线程，`kbd`=kbd 最忙线程 |
| `Q s=N cpu` | 8 个 CPU 的快照：当前进程、线程、优先级、在核毫秒数，运行队列长度。`-` 空闲，`~` 空闲且已被选中（等 IPI），`x` 离线 |
| `Q map`、`Q img` | 目标进程的 slide 和主程序地址；共享缓存外地址所在的文件映射 |

M 记录在第一帧之前每秒都写（开机的长等待既没有帧也没有触摸），之后只在采样器醒着和最后一帧或触摸之后 10 秒内写。

限流：键盘、`ask=0` 的 gap、其余采样各有一份额度，互不挤占，各自每秒 20 行，最多存下两秒的量（一次卡顿前三个阈值 290 ms 内要写 27 行）。全程键盘 2000 行，`ask=0` 800 行，其余 2000 行；其余这份里，首阈值的只能用前 1200 行，200 ms 的只能用前 1600 行。超限只计数（`M io` 的 `q`、`kq`、`tq`）。算下来 Q 平均 130 字节一行：三份全速是 8 KiB/s，事件环 32 KiB 4 秒转一圈，UFS 日志 500 ms 看一次、新内容够 12 KiB 就写，转不过去；三份用尽 4800 行约 620 KiB，窗口能留 11 MiB。M 约 0.6 KiB/s，加上 `G`、`F` 约 1.2 KiB/s，console 环 28 KiB 没有 UFS 日志时只留最后 25 秒。

`stall-report.py` 不把周期动画秒算作卡顿：无触摸且节奏是 10 Hz、20 Hz 或光标 500 ms 的秒（`stall_format.PERIODS`），其中 `ask<2` 且没超过一拍的 gap 和 `gap*` 不进表，总览单列秒数和 `jit` 占比。`idleq` 是点采样，不再据它下结论：一个在途线程会让每个空闲核同时计数，单次约 28 µs 的正常排队被约 11 ms 周期的采样撞上就算数（run 20261101-110201 的 3011 个 tick，按秒取单核最大值只有 708）。“丢唤醒”只看 IPI 悬而未决；`idleq` 和超过两拍（20 ms）的调度延迟落在同一秒时只列出这些秒待查。`判定` 一栏用 `fg` 分清屏幕本来静止（App 主线程在运行循环空等，`o=0x507000806`）和 App 主线程堵住了。

```sh
python3 stall-report.py runs/<目录> --a2s ../../build-userspace/a830-inputs/dsc-21H461.a2s --kernel <kernel.macho>   # 加 --timeline 看时间线
python3 symbolize-crashes.py runs/<目录> --a2s ../../build-userspace/a830-inputs/dsc-21H461.a2s --kernel <kernel.macho>   # 每次采样的完整符号化
```

`phone-stall-probe.patch` 同时把键盘追踪升到版本 3：超过 1 s 的配对逐条打 `kbt X[pid] long in b=.. e=.. d=..`（mach 毫秒），FIFO 环溢出时不再配错（end 按编号对应自己的 begin），`touchUp:` 按触摸时间戳（arg2）认出重复调用（`sum` 行末的 `dup`）：重复调用也有自己的任务和 `up-done`，所以它占编号、不占队列，它的 `up-done` 不配任何人，也不告诉卡顿探针，探针的未结束配对始终等于队列里的配对；汇总 1 s 一次，槽位回收时打 `final` 和 `final-lat`。

## 排查

- 没有任何记录：镜像没带 `pagani-ramoops=1`；或 Linux 因区头损坏丢弃了两个区（看 `device.txt` 的 pstore 列表和 `dmesg.ramoops`）；或这次开机之前并没有跑 XNU。2026-10-30 11:02 那轮（`loop-s3-c1-1`）就是两区都被丢：pmsg 照例以 `size 18283476` 被拒，console 区没有任何提示却也没有文件（sig 坏了时 Linux 只在调试级别记一句，`init` 复制 `console-ramoops-0` 失败可以佐证）。两个文件都没有时，份布局帮不上忙，数据只可能还留在保留区的物理内存里：Linux 丢弃一个区时只清区头，pmsg 区平时很少被写，32 份大概率还在。若能用 root 读出整个保留区（例如经 `/proc/kcore`；尚未在真机上试过），把前 `0x40000` 字节存为 `console.zone`、其后 `0x200000` 字节存为 `pmsg.zone`，`read-ramoops.py --from-dir` 会不管 Linux 区头，直接按数据区解码。
- 只有一个 pstore 文件：另一个区的区头坏了被 Linux 丢弃。布局 3 下剩 pmsg 区（32 份）结果完整，剩 console 区（4 份）在实测衰减下也几乎完整；布局 2 只剩 console 区时多数记录只能给出未核验文本。
- `summary.txt` 第二行写明布局版本、份数，以及布局是读自份头还是推定的；`note` 行说明文件变短、被旋转、哪些份因份头对不上被剔除。
- XNU 不回安卓：镜像没带 `pagani-reset-after`，或在 `boot_progress_start` 之前就卡死（早于定时器）。长按电源 + 音量下手动复位，回安卓后跑 `read-ramoops.py`，DRAM 一般仍保留日志。另一种可能是电池耗尽后欠压关机：XNU 下没有 DSP 的充电管理、又亮屏满负荷运行，整夜连续跑会把电耗光（2026-10-31 01:01 那轮之后手机在 USB 上完全消失）。所以 `gpu-loop.py` 每轮重启前先在安卓下读 `dumpsys battery`（只读）：低于 `--min-battery`（默认 40 %）就留在安卓充到 `--charge-to`（默认 60 %），超过 `--charge-timeout`（默认 3 小时）仍不够就停下、不重启；每轮结束打印并记下前后电量（`decode.json` 的 `timings.battery_before/after`，`device.txt` 的 `battery`）。
- 回到了 fastboot：`gpu-loop.py` 会自动 `fastboot reboot` 一次。
- 热复位后进了 CrashDump/9008：下次镜像加 `pagani-reset-cold=1`。
- 损坏程度：`summary.txt` 的 `vote` 一行给出本次测得的 1→0 / 0→1 比例、份数和阈值；可比较 `pagani-ramoops=5`（Device 映射）与默认写合并映射、热复位与 `pagani-reset-cold=1`。

## 主机测试

```sh
./run-tests.sh
```

依次运行：内核写入器测试（真实 `RamoopsLog.c`，含份大小与份数规则）；解码测试（写入器导出的 36 份上按实测模型注入损坏：两区都在 / 只剩 console 区 / 只剩 pmsg 区，文件变短、被旋转、份头损坏、其他区大小、重建的布局 2 镜像）；用假 adb/fastboot 跑 `gpu-loop.py --yes` 与 `read-ramoops.py` 的端到端测试（含 pmsg 区被 Linux 丢弃）；崩溃记录测试（`BootProgress/Test/crash.c`：在模拟的用户内存上测帧链遍历的各种边界、地址命名、限流，再经真实写入器写出；`tests/test_crash.py`：注入衰减后解码、`symbolize-crashes.py` 用合成的 a2s 表和 Mach-O 文件符号化，设 `CRASH_A2S=<a2s 表>` 时另核对真实缓存符号）；UFS 日志测试（`BootProgress/Test/ufslog.c`：真实 `UfsLog.c` 加真实 `RamoopsLog.c`，NativeUfs 用模型，含任一命令处掉电；`ufslog-threads.c`：同样的代码加写线程、导出、记录流和复位并发，用 ThreadSanitizer 跑；`tests/test_ufslog.py`：用它产生的窗口测正常、环回绕、panic、复位、头损坏、部分写、旧 run、位置不对、导出解包，以及假 adb 下的 `read-ufslog.py`、`read-ramoops.py`、`gpu-loop.py`）；卡顿探针测试（`BootProgress/Test/stall.c`：`StallProbe.h` 的全部纯逻辑加真实写入器，ASan/UBSan；`stall-glue.c`：`BootProgress.c` 的胶水（第一帧之前的 M、采样器、目标与引用、屏幕上的 App、跨核调用、文件映射、三份额度、`M mem`）跑在 XNU 一侧的假实现上；`stall-threads.c`：每 CPU 计数、在核记录、配对表的并发，TSan；`tests/test_stall.py`：解码、符号化、`stall-report.py`、补丁与头文件的结构体一致、键盘追踪版本 3 及其与探针配对表的一致性）；最后把 `runs/` 下每个已保存的证据目录离线重解码一遍（输出到临时目录，不改动 `runs/`）。

BootProgress 另有 `Test/console.c`（在 `drivers/BootProgress` 下用 `-I ../PaganiDarwinBoot/DarwinBootPkg/Driver -fsanitize=address,undefined` 编译），其中核对了屏幕状态行 `ramoops on 0x880000000 copies 36 x 0xfffc`。
