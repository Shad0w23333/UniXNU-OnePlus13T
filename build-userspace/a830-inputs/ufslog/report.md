# UFS 日志通道报告（2026-10-31）

本文件第一部分是修复后的结论，第二部分是修复前的实现说明。两者冲突时以第一部分为准：绝对 LBA、看门狗兜底、主动重启补写、读超时这几处在第二部分已过期。

## 主线补充（2026-10-31 21:40，优先于下文）

- **上机前核对结果**：`data_reliability=0x00`（LUN 0–5 全是 0），`unixnu`=`sda16`，起始 859273216，`read-ufslog.py --header` 显示窗口空白。下文“为 0 就先不要开”成立，所以首次开 `sheng-log=1` 前要用户决定。`userdata`、`super`、`persist`、`metadata` 也在 LUN 0。
- **基线**：`baseline-20261031.txt`，复核脚本 `tools/check-baseline.sh`（只读）。
- **新增电源键暂停**：长按是测试里最常见的、软件看不见的复位。`PaganiInput` 发布 `pagani_input_power_held`（去抖后的物理电源键，脚本按键不算）；写线程按下时补一轮，之后不开新轮；`boot_log_export_put` 返回 -9，整份导出下周期重来；松开恢复；复位路径不等它。“代码看不到的复位”因此只剩看门狗咬合和固件复位。测试 `Test/ufslog.c` 的 `test_power_key`、`PaganiInput/Test/peripherals.cpp`、`InputScriptTests.h`，变异 U80–U85。
- **主线亲自核过**：闸门代码、`hostSubmit` 调用点、运行期命令都过 `gHost.lock` 和槽 0、UFS 流 SMMU 旁路、绝对 LBA、`ufs_log_start` 的调用时机（紧跟同样建线程的 `gunyah_wdog_start`）、`gunyah_wdog_bite_reset` 不依赖喂狗。
- **这一包不做**第 8 节的 os_log 和 CacheExport：CacheExport 的扫描开销会污染抖动判断，PaganiDiag 也还没证实在真机运行。等 UFS 首测通过后再加。

---

# UFS 日志两路审查的核实与修复结果

两路审查的 12 条问题和 1 组测试盲区全部成立，已全部修复。全部主机测试通过，157 个变异全部被抓到。真机没有验证过：我没有运行 adb 或 fastboot，没有构建镜像，也没有提交。

`report.md` 没有写：磁盘上本来没有这个文件，子代理规则也不允许写报告文件，所以结论全文就在这份回复里。

## 修了什么

| 编号 | 修法 |
| --- | --- |
| F1 | `reset_now` 最前面调用新函数 `ufs_log_quiesce`：屏蔽中断，停止喂狗，认领 `gHost.lock`（最多等 1 秒）且不再释放，写最后一次快照（头状态 6），flush，之后才走咬狗或 PSCI。全程最多 2 秒，第二个复位者最多等 2 秒。不带 `sheng-log=1` 时什么都不做 |
| F2 | 测试常量改成 512 GB 机的真实值（日志窗口 124178176，导出区 124112640），并与 `board.toml` 核对。读取工具校验 `first_lba`，不符时打印 `UFS LOG AT THE WRONG PLACE`，窗口只留在 `ufs/`，不读导出 |
| F3 | `hostWrite4kHeld` 在 `memcpy` 前检查隔离状态，`assertQuarantined` 加了写路径 |
| F4 | 两个永不写的末块只接受全零 |
| 1 | panic 时看门狗保持武装到刷写结束（`gunyah_wdog_panic_flushed`）。既没有武装的看门狗也没有定时复位时，最后一次快照默认关闭，显式 `pagani-ufslog-panic=1` 才开 |
| 2 | 只有 `current` 才进合并日志，`unknown` 留在 `ufs/` 并打印 WARNING。重启前的状态分为有头、确认空白、没读到三种 |
| 3 | 窗口打开时读超时用 10 秒，没开时仍是 1 秒 |
| 4 | 最后一次 flush 之后才统计，不含快照，头里的值滞后一轮；单条命令的最长时间另记，含等锁时间，所以是上界 |
| 5 | 一轮改为槽、msgbuf、头、一次 flush；只有新点名导出的那一轮在写头前多 flush 一次；panic 和复位也只 flush 一次 |
| 6 | 上一份导出未被盘上的头点名时，新导出首块返回 -8，`CacheExport.h` 现有逻辑下个周期会重试 |
| 7 | 首块不足 8 字节或没有 `SHEXR001` 时返回 -7 |
| 8 | `OSError` 转成 `FormatError`，`write_evidence` 也兜住，blob 保留在 `export.bin` |
| 测试盲区 | 见下 |

测试盲区新增和加强的内容：
- 新增多线程测试 `ufslog-threads.c`（ThreadSanitizer）。
- 把日志接口抽到 `LogInterface.h`，新增 `LogInterface.cpp`：真实封装加真实写入器，一直跑到模型 LUN。
- 新增掉电模型，每条命令处都检查头点名的导出是否完整。
- 统计记录改为断言数值。
- `test_ufslog.py` 补了“pstore 无 run id”“读头失败”，“读失败不影响 pstore”的断言也加强了。

### 审查之外新发现并已修的问题

- **发布无序**：TSan 发现 `export_lock` 和 `export_region` 没有同步，已改为 release/acquire 顺序。
- **我第一版设计的错误**：认领后 `live` 还可能被写线程发布一次，下一轮还可能复写正在读的头。现在告别流程一开始，写线程就不再开新一轮。
- **旧 `mutate.log` 的 P 组不可信**：`test_ufslog.py` 在变异副本里走到 `test_loop` 必定失败，所以在此之前没被抓到的 P 变异都算成了“抓到”，85/85 不能当证据。现在基线也包含 P，修好后全部重跑。

## 没修或没照做的，及理由

- **窗口已永久关闭时不认领**（与 F1 建议不同）：`writer_stopped` 在导出锁下置位，之后没有日志命令在途，读命令不怕复位。
- **`live` 用 relaxed 发布**：审查的第 8 个并发变异现在是等价的，因为 `last_snapshot` 在认领之后才读 `live`，锁已保证可见性。单独跑 3 次 TSan 和确定性测试都没发现问题，所以没放进变异列表。
- **代码看不到的复位**（挂死后的看门狗咬、固件复位、长按电源）：仍可能落在写命令中途，软件无法解决。严重程度取决于 `bDataReliability`，目前未知。
- **一条命令超过 1 秒**：复位仍可能落在它上面。定时器正好打断持锁线程时，认领必然等满 1 秒，这个代价可以接受。
- **读线程连续输给写线程**：没改锁的公平性，先看上机的 `longest command` 再决定。
- **msgbuf 改成不轮转写环**：没做，等上机数据。
- **从 fastboot 开始且 pstore 无 run id**：UFS 日志不进合并日志，要手动看 `ufs/`，这是有意的。

前一轮报告不在磁盘上，改不了，更正写在这里：
- 第 4、11 节的绝对 LBA 应为上面的值。
- 第 6 节的“看门狗兜底”修复后才成立，“主动重启不补写”修复后已不成立：现在有看门狗或定时复位时会补写，两者都没有时默认不补写。
- 第 10 节列为风险的读超时已改掉。

## 测试结果（只有主机）

- **BootProgress**：ramoops、console、crash、gunyah-wdog、ufslog、ufslog-threads 在 `-O2`、`-O0` 的 ASan/UBSan 下全部通过；ufslog-threads 在 `-O1`、`-O2` 的 TSan 下无报告；`panic-entry.py` 通过。
- **NativeUfs**：`check_root_path_host.py` 全部通过，含新增的 `LogInterface`；RootCommands、LogWindow、LogInterface 的 `-O2` 通过；QEMU 上 4 个 PASS ALL、1 个 PASS LOG WRITE、22 项根路径通过。
- **`run-tests.sh`**：全部通过，含 22 个历史证据目录重解码。
- **内核配方单独编译**：我们的代码零告警，5 个目标都干净。`NativeUfs` 有 49 条基线告警，都不在日志代码里。4 个 C 目标（RamoopsLog、BootProgress、GunyahWdog、UfsLog）都没有运行时辅助函数和 FP/SIMD 依赖。
- **变异**：157 个全部抓到，0 个存活。基线先要求全部测试通过，包括 P 组。
- **审查者的复现重跑**：
  - 一轮实测 30 ms，指标 30000 us。
  - 第三代导出被 -8 拒绝，盘上数据不变。
  - 隔离后 DMA 字节保持 0x11。
  - 路径冲突不再抛未捕获异常。

## 主线还要做的步骤

1. **重建**：重建内核和镜像。`LogInterface.h` 经 tree 的符号链接自动生效，构建脚本不用改。
2. **在安卓下只读核对**：
   - `cat /sys/class/scsi_device/0:0:0:0/device/unit_descriptor/data_reliability`（路径以实际 LUN 0 为准）。为 0 就先不要开 `sheng-log=1`，把这个残余风险交给用户决定。
   - `readlink -f /dev/block/by-name/unixnu` 应为 `/dev/block/sda16`。
   - `cat /sys/class/block/sda16/start` 应为 859273216。
   - `read-ufslog.py --header` 应显示窗口空白。
3. **留基线**：记下 `dd if=/dev/block/by-name/unixnu bs=4096 skip=16515072 count=188416 | sha256sum` 和主 GPT 的哈希。
4. **运行**：从安卓状态开始跑 `gpu-loop.py --yes`，不要从 fastboot 开始，否则读不到重启前的头。
5. **上机判据**：
   - 出现 `ufslog open at LBA 124178176: 509 slots, export 65536 blocks, every 3000 ms`。
   - 定时复位后头状态为 6，日志里有 `ufslog last snapshot before the reset: timer`。
   - `write_errors` 为 0，没有 `WRONG PLACE`。
   - 记下 `longest round` 和 `longest command`。
   - 第 3 步的两个哈希不变。

## 下一张镜像要加的启动参数

只加 `sheng-log=1`。

保留 `pagani-ramoops=1 pagani-panic-reset=20 pagani-reset-after=<秒> pagani-wdog=30 pagani-reset-wdog=1`。不要加 `sheng-log-reserve`、`pagani-ufslog-panic`、`pagani-ufslog-ms`，用默认值即可。

上次镜像的命令行是 986 字节，加上后 998 字节，上限 1023。

## 文件

新增：
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeUfs/LogInterface.h`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeUfs/tests/LogInterface.cpp`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeUfs/tests/LogLun.h`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ufslog-threads.c`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ufslog-e2e.c`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ufslog-platform.h`

主要修改：
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/BootProgress/` 下的 `UfsLog.{c,h}`、`RamoopsLog.{c,h}`、`GunyahWdog.{c,h}`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeUfs/` 下的 `RootCommands.h`、`LogWindow.h`、`NativeUfs.cpp`
- `/Users/xiaochoumao/unixnu-13t/工具/gpu-test-loop/` 下的 `ufslog_format.py`、`read-ufslog.py`、`read-ramoops.py`、`gpu-loop.py`、`run-tests.sh`、`README.md`

日志：
- `/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/ufslog/host-tests.log`
- `/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/ufslog/mutate.log`
- `/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/ufslog/kernel-compile.log`

---

# 附：修复前的实现说明

UFS 日志通道已实现，但只在主机上验证过，没有上真机；`report.md` 写入被工具拒绝，报告全文放在摘要后面。

## 摘要

1. 写入闸门、写入线程、panic 路径、读回工具、回路集成都已完成。没有运行 adb/fastboot，没有提交，没有整核构建，没有动 `UniXNU/out`。
2. 写入闸门：编译开关 `NATIVE_UFS_LOG_WRITES=1` 与 `NATIVE_UFS_READ_ONLY=1` 同时使用。Data 窗口永远打不开。`hostScsiDir` 从 CDB 重新解出 LBA 再查窗口，只放行不带标志位的 WRITE(10) 和全零 SYNCHRONIZE CACHE(10)。
3. 内容闸门已加；几何确认来自 GPT 的 `unixnu` 分区，在 `startRoot` 里赋值。
4. 写入线程在 `UfsLog.c`。比原设计多了：一轮两次 flush、只写变化的块、环写得快时提前一轮、普通优先级。理由见第 6 节。
5. panic 路径默认开，`pagani-ufslog-panic=0` 关闭。`hostSubmit` 不睡眠，用 try-lock 且只认领不释放；总预算 1 s，不超过到复位时间的 4/5，复位不会晚。
6. 主机测试全过，内容包括 -O2/-O0 + ASan/UBSan、QEMU EL1、假手机端到端。
7. 变异测试 85 项全部抓到。过程中发现的 4 处测试缺口已补。
8. 新增的 QEMU `log-writes` 用例让生产 WRITE/SYNC 跑在 QEMU 自带 UFS 设备上，运行后磁盘文件只有窗口内变化。
9. 内核配方单独编译：`UfsLog.c` 等新文件零告警。`NativeUfs.cpp` 有 49 条原有告警，日志相关代码零新增。
10. `boot_log_export_put`：用真实的 `NativeAdrenoPublish.o` 做 `ld -r`，强定义覆盖弱定义。
11. 打开方式：`A830_EXTRA_ARGS="sheng-log=1"`，完整包 986 + 12 = 998 字节，还剩 2 字节。
12. 系统日志现在不会跑：`adrenoprobed` 在 21H461 上从未启动，就算启动，`storeWithScope:0` 也返回 nil。
13. 缓存导出现在也不会跑：种子里没有 `NativeAdrenoMetal.cacheexport`。
14. 补齐这两项都不用重建插件，方法见第 8 节。
15. 未验证：一切真机行为。新风险是 `hostRead4k` 的 1 s 读超时；总线挂死时 panic 路径只能靠看门狗兜底。
16. 原始日志：`/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/ufslog/host-tests.log`、`/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/ufslog/mutate.log`。

---

# UFS 日志通道：实现说明（2026-10-31）

授权范围：用户 2026-10-31 明确授权，XNU 可以写 `unixnu` 分区末尾 32 MiB 日志窗口及其下方 256 MiB 导出区，别处一律不写。所有写路径都以此为界。

## 2. 改动文件

`D` 代表 `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers`，`T` 代表 `/Users/xiaochoumao/unixnu-13t/工具/gpu-test-loop`。

| 文件 | 改动 |
| --- | --- |
| `D/NativeUfs/RootCommands.h` | `hostLogCommand` 写闸门、`hostLogOpen` 放行条件、`hostLogClose`；`hostWrite4kHeld`、`hostSyncCacheHeld`（不取锁，panic 用）；写超时 10 s，flush 超时 30 s |
| `D/NativeUfs/LogWindow.h`（新） | 窗口规划、内容闸门、`ufsLogOpenWindow`、panic 限时认领 `ufsLogClaim`；不依赖内核，主机测试跑同一份代码 |
| `D/NativeUfs/NativeUfsLog.h`（新） | 日志接口声明 |
| `D/NativeUfs/NativeUfs.cpp` | `native_ufs_log_open` 改走 `ufsLogOpenWindow`；新增 `native_ufs_log_close`、`native_ufs_log_panic`、`native_ufs_log_panic_write`、`native_ufs_log_panic_sync` |
| `D/NativeUfs/README.md` | 说明 |
| `D/NativeUfs/tests/LogWindow.cpp`（新）、`RootCommands.cpp`、`RootCommandsBridge.cpp`、`RootCommandsArm64.cpp`、`check_root_path_host.py`、`check_arm64_root_commands.py` | 闸门测试；QEMU 写用例 `log-writes` |
| `D/BootProgress/UfsLog.c`、`UfsLog.h`（新） | 写入线程，MIT，移植自上游 `package/BootLog.c`（6b11bb5），出处在文件头 |
| `D/BootProgress/RamoopsLog.c`、`RamoopsLog.h` | `ramoops_log_snapshot`、`ramoops_log_reset_in_ms`、环的单调累计量、`ramoops_log_panic_reset` 里调用 `ufs_log_panic` |
| `D/BootProgress/BootProgress.c` | `start_reset_paths` 里调用 `ufs_log_start` |
| `D/BootProgress/Test/ufslog.c`（新） | 真实 `UfsLog.c` + 真实 `RamoopsLog.c` + NativeUfs 模型 |
| `/Users/xiaochoumao/unixnu-13t/UniXNU/scripts/build-native-ufs.py` | 加 `-DNATIVE_UFS_LOG_WRITES=1` |
| `/Users/xiaochoumao/unixnu-13t/UniXNU/scripts/build-boot-progress.py`、`/Users/xiaochoumao/unixnu-13t/UniXNU/components.toml` | `UfsLog.c` 进 BootProgress 配方 |
| `T/ufslog_format.py`、`T/read-ufslog.py`（新） | 解码与读回 |
| `T/gpu-loop.py`、`T/read-ramoops.py`、`T/run-tests.sh`、`T/README.md` | 集成 |
| `T/tests/test_ufslog.py`（新）、`T/tests/test_loop_mock.py` | 主机测试；假 adb 支持 unixnu 分区 |
| `/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/ufslog/tools/` | `run-host-tests.sh`、`kernel-compile-check.py`、`mutate.py` |

没有改 `UniXNU-Pagani_SM8750-gpu/modules` 里的副本。GPU 线构建树的 `drivers/` 是指向 `UniXNU-Pagani-Drivers` 的符号链接，改动会被用到。日常线副本没有 `UfsLog.c`，配方用 `is_file()` 判断，不受影响；`-DNATIVE_UFS_LOG_WRITES=1` 对旧源码没有作用。

## 3. 启动参数

| 参数 | 含义 |
| --- | --- |
| `sheng-log=1` | 打开；没有它时不起线程、不分配内存、不写分区 |
| `pagani-ufslog-ms=<毫秒>` | 一轮周期，默认 3000，下限 1000 |
| `pagani-ufslog-panic=0` | 关闭 panic 路径，默认开 |

`工具/make-a830-layout.sh` 第 288 行把 `A830_EXTRA_ARGS` 追加进 `phone-plus`、`phone-apps` 及其灵动岛形态：

```sh
A830_EXTRA_ARGS="sheng-log=1" 工具/make-a830-layout.sh phone-island-apps
```

长度限制在 `UniXNU/scripts/unixnu/payload.py:35`。完整包实测 986 字节，加上后 998，还剩 2 字节；要加别的参数就得用 `A830_DROP_ARGS` 去掉一个。

## 4. 窗口格式

分区相对块号（每块 4096 字节）：日志窗口 16769024..16777215，导出区 16703488..16769023。窗口起点绝对 LBA 为 61697792（44928768 + 16769024）。

| 窗口内的块 | 内容 |
| --- | --- |
| 0 | 头（`struct ufs_log_header`，128 字节，魔数 `PGLOG001`，末尾 CRC32） |
| 1..32 | 内核 msgbuf，最旧在前 |
| 33..8176 | 509 个槽，每槽 16 块（64 KiB） |
| 8177..8191 | 永不写；最后一块留给内容闸门 |

头字段（小端）：`magic[8] version header_bytes boot_id(u64) first_lba(u64) ramoops_run window_blocks msg_first msg_blocks msg_bytes slot_first slot_blocks slot_count slot_pinned slots_written uptime_s rounds write_errors gpt_open gpt_last state export_first export_blocks export_bytes export_generation export_below period_ms max_round_us crc`。

- `state`：2 写入中，5 panic 写入。3（写失败）和 4（GPT 变化）只进 ramoops 记录，盘上留最后一次成功的头。
- 槽：ramoops 第 0 份副本的快照，`0xfffc` 字节，余下补零。逻辑槽 n 的位置：n < 32 时是 n，否则 32 + (n − 32) % 477。读者只读已计入的槽。
- 导出区：两半各 32768 块，新导出写进不含上一份完整导出的那一半。单份最大 `(32768 − 1) × 4096` 字节，每半最后一块不写。

## 5. 写入闸门

1. **编译开关**：`NATIVE_UFS_READ_ONLY=1` + `NATIVE_UFS_LOG_WRITES=1`。`hostStoreOpen` 永远拒绝。
2. **`native_ufs_log_open`**（`ufsLogOpenWindow`）：
   - 分区名必须是 `unixnu`；
   - 窗口避开根镜像加 4 MiB guard；
   - 顺带修了一个旧隐患：`partition - reserve` 在 reserve 大于分区时回绕而放行；
   - 内容闸门读日志窗口和导出区的首块、末块，只能全零或带魔数（窗口 `PGLOG001`，导出区 `SHEXR001`，各认各的），读失败也拒绝。
3. **`hostWrite4k`**：范围必须在已打开窗口内，LUN 0。
4. **`hostScsiDir`**（`hostLogCommand`）：从 CDB 重新解出 LBA 和块数再查窗口，绕过 `hostWrite4k` 也过不去。只放行无标志位（含 FUA）的 WRITE(10) 和全零 SYNCHRONIZE CACHE(10)。
5. **`native_ufs_log_close`**：写入线程永久停止时关窗，导出也写不了，窗口不能再开。

几何确认：`gStoreGeometry` 在 `NativeUfs::startRoot` 里赋值（`native-ufs-root=1` 走 `rootMode && !probe` 分支），位于 `manifestStatus == 0` 之后。几何来自 `findRootLayout`，GPT 头和分区表都过 CRC，名字精确匹配，`UFS_ROOT_PARTITION` 由构建脚本定为 `"unixnu"`。

## 6. 写入线程

一轮：快照 → 写当前槽 → msgbuf 有变化就写 → flush → 写头 → flush。

| 比原设计多做或改动的地方 | 理由 |
| --- | --- |
| 一轮两次 flush | UFS 有易失缓存，只 flush 一次时设备可能先落头、后落数据 |
| 槽内只写有变化的块 | 一轮通常只动几块，IO 少很多；中途掉电时旧块不受影响 |
| 换槽基准改为“上一个槽最后一次快照的累计量” | 若用本槽第一次快照做基准，突发后再写 24 KiB 会丢记录；连续性测试和变异 U5 守着这点 |
| 换槽阈值取 24 KiB 与较小环 7/8 的较小值 | `pagani-ramoops-copy=0x8000` 时环只有约 14 KiB |
| 每 500 ms 看一眼，任一环累计 12 KiB 就提前一轮 | 控制台环 28 KiB，3 秒周期只能扛约 9 KiB/s；估算可提到约 50 KiB/s（未实测） |
| 定时复位前 2 秒内每 250 ms 看一次 | 多留最后的记录，只在有新记录时写 |
| 优先级显式设为 `BASEPRI_DEFAULT`（31） | `kernel_thread_start` 默认取内核任务优先级，高于所有用户线程；一轮里会自旋等传输 |
| 开窗后立刻写一次头 | 新一轮第一轮前就死掉时，不会被当成上一轮 |
| 写超时 2→10 s，flush 10→30 s | 超时会隔离整个传输，根卷从此读不了；等久一点只是阻塞读 |
| msgbuf 超过 128 KiB 时保留最新部分 | 上游保留最旧部分 |
| 导出每半少用最后一块 | 否则下次开机内容闸门会把自己写的数据当外来数据 |

**panic 路径**（由 `ramoops_log_panic_reset` 调用，屏蔽中断）：

- `hostSubmit` 可在屏蔽中断、不可睡眠时运行：SM8750 的等待是计数器自旋，`clock_interval_to_deadline`、`mach_absolute_time` 都是纯计算。
- 唯一会阻塞的是 `gHost.lock`。`lck_mtx_try_lock` 只是一次比较交换，没有中断检查，所以用 try-lock 循环，最多 100 ms。
- 拿到后不释放：panic 之后就是复位，解锁可能进入互斥锁的唤醒路径。
- 总预算 1 s，且不超过到复位时间的 4/5；每个命令的超时取步长上限和剩余预算的较小值，任何一步失败就放弃。
- `now` 在调用前取，刷写时间算在 `pagani-panic-reset` 的等待里。
- 只执行一次；写线程已停止时不写；写入下一个逻辑槽，不碰当前槽；头 `state=5`。
- panic 文本已在快照的 `P` 记录里，不再单独写 msgbuf。

默认打开的理由：panic 文本是最需要留下的东西，而所有等待都有界。剩余风险：panic 由总线挂死引起时，访问 UFS 寄存器的 MMIO 读可能卡住，这种挂起没有超时。完整包带 `pagani-wdog=30`，看门狗兜底；没有看门狗的镜像只能靠 `pagani-reset-after` 的定时器。

没做的：iOS 主动重启（`ramoops_log_restart`、`ramoops_log_power_off`）不补写，最后最多 3 秒会丢。

强弱定义：两个归档在内核链接里都是 `-force_load`。小程序三种链接顺序都解析到强定义；用真实的 `NativeAdrenoPublish.o` 与 `UfsLog.o` 做 `ld -r`，结果也只剩强定义。

## 7. 读回工具

- `read-ufslog.py`：手机上只跑 `blockdev --getsize64` 和 `dd`，块号在电脑上算。证据目录有 `records.jsonl`、`merged.log`、`summary.txt`、`decode.json`、`msgbuf.txt`、`ufs-header.json`、`ufs-window.bin.gz`、`export/`、`export-manifest.json`。
- `export/` 可以直接给 `make-shader-cache-a830.py --merge-export`；`zz-oslog/*.gz` 另解压成文本。路径穿越、截断、尾部多余字节都拒绝。
- 头损坏时按槽自身的份头恢复：位置 0 的槽总是最新一次运行的第一个槽，只取这个 run。此时找不到导出。
- `gpu-loop.py`：只对带 `sheng-log=1` 的镜像动作，重启前读一次头，回安卓后读窗口。
  - 是本次运行的：顶层四个日志文件换成合并日志（UFS 优先，同一 run 的 pstore 补缺），原文件移到 `pstore/`。
  - 旧日志：明确打印 `OLD UFS LOG`，顶层保持 pstore。
  - UFS 证据都在 `ufs/`。
- `read-ramoops.py`：总会看一眼窗口，`--no-ufs` 关闭。没有重启前的头时只能和 pstore 的 run 比；pstore 也没了就提示无从判断。

`build-userspace/read-cache-export-a830.py` 读路线 B 的 `PGEXPRT1` 头，和这里不兼容。路线 B 的补丁 `shader-cache-tools/patches/pagani-nativeufs-cache-export.patch` 不能再打：它也有 `boot_log_export_put` 强定义，还会写块 0。

## 8. 系统日志和缓存导出现状（未改动）

| 环节 | 现状 | 依据 |
| --- | --- | --- |
| 种子里的任务 | 有：`/private/var/ngpu/bin/adrenoprobed`、`/private/var/ngpu/daemons/*.plist`，`sheng-gpu-addon=1` 挂到 `/System/Library/LaunchDaemons` | `add-gpu-plugin-data.py`、`zzy-native-gpu-addon-mount.patch` |
| 是否启动 | 不启动：21H461 的 launchd 从签名缓存读 `/System/Library` 下的任务 | `phone-diag-sysctl.patch` 注释、PaganiDiag README；`runs/` 全部 `merged.log` 里 `adrenoprobed` 零次，同类记录列出 93 种进程名（历史真机日志） |
| 就算启动 | xpcproxy 不能运行 `/private/var/ngpu/bin` 下的程序；`storeWithScope:0` 在 21H461 返回 nil，只写一行 `# no store` | PaganiDiag README 和 `PaganiDiag.mm:544` 的逆向结论，未在真机验证 |
| CacheExport 线程 | 只有 bundle 旁有 `NativeAdrenoMetal.cacheexport` 才启动（`NativeAdrenoMetalPlugin.mm:449`）；当前种子链没有（`s1b-cache.json` 的 `cacheexport` 为 false） | 源码、种子元数据 |
| 内核一侧 | `boot_log_export_put` 现在是真实实现；调用者必须是 `backboardd`（`NativeAdrenoPublish.cpp:873`） | 源码，未在真机跑过 |

还差两件事，都不用重建插件，着色器缓存不失效：

1. **种子加标记文件**：`/private/var/ngpu/addons/NativeAdrenoMetal.cacheexport`，内容 `1\n`。运行时才读，插件 CDHash 不变。可以给 `make-phone-seed.py` 加小选项，或在 `make-a830-final-seed.sh` 末尾加一步。
2. **把系统日志写入放进 PaganiDiag**：它在 `pagani-diag=1` 时由内核替换 DumpPanic 启动（完整包已带），有日志权限，`system_log_store()` 已解决 21H461 的问题。
   - 在 `PaganiDiag.mm` 加一个线程，按 `NativeOsLogExport.mm` 的格式写 `/private/var/mobile/Library/Caches/com.apple.backboardd/m2v/zz-oslog/oslog.txt.gz`；
   - `build-paganidiag.py` 加 `-lz`；
   - 用 `replace-diag-in-seed.py` 换进种子并更新信任包。

代价：CacheExport 每 15 秒扫一次目录。上游记录过（int-245）目录填满后扫描线程占 45% CPU、发送线程 48%、帧慢一倍，所以标记只该用于诊断镜像。另外 PaganiDiag README 写着尚未在设备上运行，第 2 步要先确认守护进程真的起来了。

`CacheExport.h` 的 `kLimit` 是 128 MiB，内核上限少 4 KiB。落在这 4 KiB 里的导出会被拒绝，下一周期重试，无害。

## 9. 主机验证结果

全部在本机运行，未碰手机。

| 项目 | 结果 |
| --- | --- |
| BootProgress `ramoops`、`console`、`crash`、`gunyah-wdog`、`ufslog`，-O2 和 -O0，ASan + UBSan | 全过 |
| `panic-entry.py` | 过 |
| NativeUfs `check_root_path_host.py`（含 RootCommands 内核标志/纯只读，LogWindow 内核标志/纯只读/无标志） | 全过 |
| RootCommands、LogWindow 另跑 -O2；LunDiscovery、TransferPoll、SmmuTakeover、Sm8750；`check_legacy_descriptors.py` | 全过 |
| `check_arm64_root_commands.py`，新增 `log-writes`：4K 和 128K 写、flush、读回一致，窗口外写入未到达设备，磁盘文件只有窗口内变化 | 过 |
| `check_qemu_root_commands.py` | 过 |
| `run-tests.sh`：写入器、解码、假手机端到端、`test_ufslog.py`（真实写入器产生的窗口：正常、环回绕、panic、头损坏、部分写、旧 run、导出解包、假 adb 下三个工具），历史证据重解码 | 全过 |
| 内核配方单独编译：`UfsLog.c`、`RamoopsLog.c`、`BootProgress.c` 零告警，无运行时辅助函数，无 FP/SIMD；`NativeUfs.cpp` 日志相关代码和 `LogWindow.h` 零新增 | 过 |
| 变异测试：G 系 30 项（闸门）、U 系 45 项（写入线程）、P 系 10 项（读回），共 85 项 | 全部抓到 |

变异测试过程中发现 4 处测试缺口，都已补上：

- 内容闸门读失败时块里是旧值；
- 窗口检查被第二道闸门遮住（加了不带闸门的编译配置）；
- staging 分配失败未覆盖；
- 导出在 flush 期间完成时头不该提前写明。

## 10. 未验证项（都需要真机）

- 真实 UFS 上 WRITE(10) 是否被接受（写保护、UNIT ATTENTION）、SM8750 控制器上的数据流向、真实写延迟。
- 整核链接；只做了单文件编译和目标文件级链接验证。
- `boot_progress_start` 处起线程、`IOMallocAligned_external`、`IOLockAlloc` 的实际运行。
- 每轮耗时对根卷读的影响，看 `ufslog rounds ... longest round ... us` 记录。
- `hostRead4k` 的 1 s 超时没有改。写入后设备做垃圾回收时读超时会隔离传输。写入量很小、概率低，但以前 XNU 从不写，这是新风险。
- panic 路径从未真实触发。
- PS_HOLD 复位落在写入中途的后果：记录有 CRC，但没有实测。
- 真实 adb 读取速度和 `su -c` 行为，目前只用假 adb 测过。
- 第 8 节全部。

## 11. 主线后续步骤

1. 构建 GPU 线内核。`tree/scripts` 是构建时复制的，新配方会进去。确认 `libBootProgress.a` 含 `UfsLog.o`，`_boot_log_export_put` 落在它里面。
2. 出镜像：`A830_EXTRA_ARGS="sheng-log=1"` 加原有环境变量。
3. **上机前**在安卓下记录窗口以外的基准：分区块 16515072..16703487 的 sha256 和分区首块。第一次运行后再算一次，必须不变，这是对“绝对不写别处”的真机确认。
4. 第一轮用 `gpu-loop.py --yes`，看有没有 `ufslog open at LBA 61697792: 509 slots`，以及 `ufslog refused`、`ufslog stopped`。`read-ufslog.py --header` 看 `rounds`、`write_errors`、`gpt`。
5. 看 `longest round` 判断对根卷读的影响，影响大就调大 `pagani-ufslog-ms`（要先腾命令行字节）。
6. 故意触发一次 panic，验证 `state 5` 和 `pagani-panic-reset` 按时复位。
7. 需要 os_log 时做第 8 节两步。
8. `make-shader-cache-a830.py --with-cacheexport` 的帮助文字还写着 `pagani-cache-export=1`，应改为 `sheng-log=1`；`read-cache-export-a830.py` 可以退役。
