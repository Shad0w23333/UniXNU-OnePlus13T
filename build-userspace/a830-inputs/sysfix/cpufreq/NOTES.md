# cpufreq：SCMI 瞬态失败改为退避重试（系统修复工作流，键 `cpufreq`）

- 范围：只改本键独占的 `UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/` 下 `ScmiPerf.h`、`CpuFreqGovernor.h`、`ArmCpu.cpp`、`README.md`、`Test/scmi-perf.cpp`（原处修改）。没碰手机、没提交、没写 `UniXNU/out-gpu`、没动任何共享集成文件。
- 一句话：D2 在 41.849 s 收到一次 SCMI BUSY（-6）就把调速器整轮关掉；现在 BUSY 与"无可用应答"类失败按退避重试、欠下的档位设置事后补发、被打断的 5 秒复查从断点续做；硬失败与超出预算仍然关闭；新增回退开关 `pagani-cpufreq-retry=0`（默认不加，即启用重试）。

## 1. 证据

| 出处 | 原文 | 含义 |
| --- | --- | --- |
| D2 `merged.log` seq 3701 | `41.849 U cpufreq: off: status -6 lvl get` | 5 秒复查里的 `PERF_LEVEL_GET` 收到 SCMI 状态 -6（BUSY），旧代码"任何失败即关" |
| D2 seq 3702–3703 | `cpufreq: d0 back to boot lv8: ok`、`cpufreq: d1 back to boot lv0: ok` | 关闭时两域被设回开机档：d0 lv8 = 1996.8 MHz，d1 lv0 = 1017.6 MHz |
| D2 心跳 | 10/20/40 s `cpu 3513MHz`；50 s 起 `cpu 1986MHz`；110.841 s `cpu 1012MHz on 7` | 关闭后性能核锁在约 1986 MHz、超大核锁在约 1012 MHz，直到 171 s 日志结束 |
| D2 时序 | 16.780 s `d1 back at lv3` 之后每 5 s 一次复查，41.849 s 正是复查时刻 | 失败的是复查，不是调速窗口里的设置 |
| D seq 3792 | `46.860 cpufreq: d1 at lv8 2841MHz, asked lv9 3072MHz, limits status -3` | `PERF_LIMITS_GET` 被固件拒绝（DENIED）；旧逻辑已是"停查、计入 err"，保留；但旧逻辑把任何状态码（包括 BUSY）都当拒绝而永久停查，现已区分 |
| D 51.870 / 116.992 s 等 | `d0 at lv4 1152MHz, asked lv10 2400MHz` 等 | CPUCP 在 GPU 约 90 °C 时自主压频；调速器只记录（`thr`），本修复不涉及 |
| 全部 44 个运行目录 | 只有 D2 出现 `cpufreq: off:` | BUSY 罕见（10-30 14:31 起多轮共数小时只见 1 次），属偶发瞬态 |

设备树（`a830-inputs/dt/running.dts` 17052–17129 行）：`qcom,scmi` 没有 `max-rx-timeout-ms`，即用 Linux 默认 30 ms，与 `ScmiPerf.h` 的 `kTimeoutMs` 一致；SCMI 发送用 `<&cpucp 0>`、接收（平台通知）用 `<&cpucp 2>` 与共享内存 `0x17b4e200`，本客户端两者都不碰。

## 2. 根因

- 旧 `CpuFreqGovernor.h` 第 40 行写明 "Every failure switches the feature off for the boot"：`controlCheck`、`setLevel`、`controlStart` 里任何一个 SCMI 结果不是 `kOk` 就调 `controlFail`，`on=false`，线程退出，两域设回开机档。
- SCMI 状态 -6 是 BUSY（Arm DEN0056 状态码表），表示平台此刻忙、可以再问；它不是协议错误也不是拒绝。旧 `ScmiPerf.h` 注释只列了 -1/-2/-3，-6 被当成普通失败。
- Linux 侧行为（GPL，仅作事实参照，未抄代码；本机没有 Linux 源码，以下凭对主线代码的了解）：arm_scmi 把 SCMI_ERR_BUSY 换成 -EBUSY 交还调用方，SCMI 核心不在内部重试；scmi-cpufreq 的快速切换路径失败就放弃这一次，cpufreq 核心设置失败只记一行错误、保持原频率；没有任何一处因一次失败停用驱动。共享内存传输发送前等通道空闲的上限是两倍接收超时。

## 3. 修法

1. **失败分类**（`ScmiPerf.h` `scmi::classify`，新增完整状态码枚举 `scmi::Status`）
   - `kRetryBusy`：状态 -6 BUSY（平台在、只是忙）。
   - `kRetrySilent`（无可用应答）：超时 `kTimeout`、发送前通道一直被占 `kBusy`（"chan busy"）、通道错误位 `kChannelErr`、状态 -7 COMMS_ERROR。
   - `kFinal`（硬失败）：其余所有状态（-1/-2/-3/-4/-5/-8/-9/-10/-11 及未知值）、各种畸形应答（短、头不符、版本、域数、档位表不一致、未知档位、域号越界）、未映射、中断上下文。
2. **退避**（`CpuFreqGovernor.h` `controlError` / `controlReady` / `retryMs`）：一次瞬态失败后，本次调用不再发任何消息；在 `retry.at` 之前整个 SCMI 通道静默。间隔从 20 ms（下一个调速窗口）起，每连续失败一次翻倍，封顶 1280 ms（20/40/80/160/320/640/1280）。
3. **欠账补发**（`retry.pending`）：档位设置失败（或因退避被推迟）时记下该域"欠一次设置"，平台实际档位视为未知；退避过后即使调速器目标等于记录档位也重发一次，保证平台与记录一致（BUSY 时平台是否已执行未知，假 CPUCP 特意模拟"已执行但回 BUSY"）。
4. **复查续做**（`retry.checked`）：5 秒复查被打断时记下已完成的域，退避后从断点继续，不重复询问、不重复计数 `thr`；两域都完成才更新 `lastCheck`。复查只是信息性的：同一轮 4 次瞬态失败（`kCheckTries`）就放弃这一轮，调速照常，下一轮 5 秒后。
5. **启动重试**（`retry.waiting` + `controlProbe`）：探测或读开机档遇瞬态失败时，`controlStart` 返回 false 并置 `waiting`；`ArmCpu.cpp` 的线程按退避睡眠（`cpufreqSleepUntil`，不持锁）后调 `controlProbe` 重来；状态行显示 `CPU scmi retry: status -6 base ver`。一次尝试中若前面的消息已应答，则"无应答连串"清零（平台在）。
6. **预算**（超出即按硬失败关闭，原因带次数）：
   - 档位设置连续 48 次瞬态失败且其间没有一次设置成功（约 54 s 纯 BUSY）→ `CPU scmi off: status -6 lvl set x48`；
   - 启动连续 48 次失败 → `... x48`；
   - 连续 6 次无应答且其间没有任何应答（平台停止响应，约 0.62 s 退避 + 6 次超时 ≈ 1 s）→ `CPU scmi off: timeout lvl set x6`（以连串中的第一个失败命名）。
   - 设置的预算按"设置自己的连串"计，复查读取成功不会把它清零（否则"设置永远 BUSY、读取正常"永远不会触发预算——审查时发现并修正）。
7. **限值查询**（`PERF_LIMITS_GET`，可选）：BUSY 或无应答只让这条降档日志写成 `, limits busy` / `, limits no answer`，下次降档变化时照问；状态拒绝（如 D 的 -3）仍是停查并计入 `err`；畸形应答仍为硬失败。
8. **日志限量**：每个连串开头一行、结尾一行，只记前 8 个连串（`cpufreq: retry: status -6 lvl get d1 after lvl set d1, next in 20 ms` / `cpufreq: retry ok: lvl get d1 after 1 failure in 20 ms (busy 1, no answer 0)`），复查被放弃记一行（同一配额）；配额用完后每分钟至多一行汇总（`cpufreq: retries since boot: busy …, no answer …, … streaks, longest …, checks left out …`）。每行都远小于 ramoops 单条 200 字符上限。`after lvl set d1` 记录同一窗口里紧挨着的上一条成功消息，用来验证"设置后马上读会遇 BUSY"的猜测。
9. **状态行**：`err` = 被拒的可选消息 + 已重试的瞬态失败（BUSY 次数 + 无应答次数），封顶 999；关闭原因带 `xN`；启动等待时 `CPU scmi retry: …`。最宽组合仍 ≤ 65 字符（测试覆盖）。
10. **回退开关**：启动参数 `pagani-cpufreq-retry=0`（`Control::noRetry`）恢复旧行为（任何失败即关）；启用时开机行末尾带 `, no retries`。默认 1。
11. **CPUCP 邮箱/门铃**：RX 窗口只服务于完成中断，轮询模式从不读写它，因此映射失败改为只记一行 `cpufreq: rx window not mapped (not used: completion is polled)` 而不关闭；TX、共享内存映射失败仍关闭（必需）。
12. **不比以前阻塞更久**：`transfer()` 的时序一字未改（等通道 ≤ 超时、等应答 ≤ 超时）；每次调用发出的消息只会比以前少（瞬态失败后本次调用立即停手），关闭路径的回设与以前相同。主机测试实测"平台停止响应"时最长一个窗口 150 ms = 3 × 50 ms（一次失败 + 两次回设），与旧代码同一情形相同。

## 4. 逐个调用点审查

| 调用点 | 消息 | 旧行为 | 新行为 |
| --- | --- | --- | --- |
| `controlProbe`（启动） | BASE_VERSION、PERF_VERSION、PERF_ATTRIBUTES、DOMAIN_ATTRIBUTES×2、DESCRIBE_LEVELS×多轮、LEVEL_GET×2（开机档） | 任何失败即关 | 瞬态：线程退避后整轮重探（48 次 / 无应答 6 次预算）；硬失败即关 |
| `setLevel`（调速窗口、模式 1 定顶、复查中模式 1 重设） | LEVEL_SET | 任何失败即关并回开机档 | 瞬态：记欠账、退避后重发；48 次连续失败或 6 次无应答才关；硬失败即关 |
| `controlCheck`（每 5 s） | LEVEL_GET×2 | 任何失败即关 | 瞬态：退避后断点续做，4 次放弃本轮；硬失败（短、未知档位等）即关 |
| `controlCheck` 内 | LIMITS_GET | 任何状态码都永久停查；超时/畸形即关 | BUSY/无应答：本行注明、下次照问；拒绝：停查计 err；畸形：即关 |
| `controlFail`（关闭时回设） | LEVEL_SET×≤2 | 尽力一次 | 尽力一次，另含欠账的域（即使记录档位等于开机档） |
| 通知 | PERF_NOTIFY_LIMITS / PERF_NOTIFY_LEVEL | 不存在 | 不存在：本客户端不订阅通知，不读 P2A 通道（`<&cpucp 2>`、`0x17b4e200`） |
| 门铃 `cpufreqRing` | 写 `tx + 0x104` | 只写，无失败路径 | 不变（Linux qcom-cpucp-mbox 发送同样只写一个 32 位值） |
| 发送前等通道空闲 | — | 超时记 `chan busy` 即关 | 归入无应答类，按预算重试 |
| 映射 | TX / 共享内存 / RX | 任一失败即关 | TX、共享内存失败即关；RX 只记一行 |
| `IOLockAlloc`、`cntfrq`、`kernel_thread_start` | — | 失败即关 | 不变（不可恢复） |

## 5. 测试（`Test/scmi-perf.cpp`）

- 命令（与盘点脚本 `inventory/code-run-host-tests.sh` 相同）：`cd UniXNU-Pagani-Drivers/drivers/PaganiArmCpu && xcrun clang++ -std=c++17 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all Test/scmi-perf.cpp -o /tmp/scmi-perf && /tmp/scmi-perf`
- 结果：`PASS … 2246649 checks`（改前 2181373）；ASan+UBSan、TSan（0 告警）、`-O2 -Wall -Wextra -Werror -Wshadow -Wconversion` 均通过；同时并行 6 个实例（模拟机器繁忙下的实时超时）全部通过；单跑连续 6 次全部通过。日志在 `logs/`。
- 旧用例的期望随语义调整：只出现一次的错误位/错 token/通道一直被占现为"重试后通过或预算耗尽后关闭（`x6`）"；D2 式 BUSY 不再关闭；原"中途失败回开机档"用例改用最终状态 -8（GENERIC_ERROR）。
- 新增 12 组：
  1. 分类表与退避表（每个错误种类、-1…-12 与极值状态；20/40/…/1280；48 次共 53740 ms，6 次 620 ms）。
  2. **D2 复现**：复查的 LEVEL_GET 回 BUSY（d0、d1、以及同窗口刚设置完 d0 后），调速器不关、20 ms 内无消息、断点续做（另一域不重复问、`thr` 只计一次）、之后负载突发两域到顶、空闲回下限，平台档位与记录一致。
  3. **BUSY 连发**（1/3/7/20 次）：每个退避期间零消息；重试间隔精确为 20/40/80/…；连发结束时平台档位 = 调速器档位（"恢复"）；日志与 `err` 计数准确。
  4. **永久 BUSY**：第 48 次后关闭，耗时恰为 53.74 s，原因 `status -6 lvl set x48`，两域回设（也 BUSY，照实记录），此后零消息，整段只一行重试日志。
  5. **间歇超时**：每隔若干窗口一个迟到 70 ms 的应答（超时 50 ms），8 s 内 15 次无应答、连续最多 1 次，全程不关，最终平台档位与记录一致；**180 ms 卡顿**后恢复；**平台彻底不响应**：6 次后关闭 `timeout lvl set x6`，退避共 0.62 s，最长窗口 150 ms；欠账域即使记录档位等于开机档也回设。
  6. **启动重试**：BUSY×3 后 20/40/80 ms 三次重试成功（状态行 `CPU scmi retry: status -6 base ver`，提前调用不发消息）；永久 BUSY 48 次后关闭；一次性错误位/错 token/迟到应答可恢复；十次连续"有应答中夹一个无应答"不误判为平台失联。
  7. **限值查询** BUSY/无应答：本行注明、下次照问；拒绝仍停查。
  8. **模式 1**：启动时定顶遇 BUSY，下一窗口补发两域；复查遇 BUSY 下一窗口续做并重设被压的顶档。
  9. **日志限量**：100 个 BUSY 连串（502 s）只产生 16 行明细 + 8 行汇总；`err 100`。
  10. **复查持续 BUSY**：每轮 4 次后放弃，60 s 内 11 轮共 46 次读取，调速设置照常成功。
  11. **随机故障注入**：5858 个随机窗口、201 个故障（BUSY 连发、迟到、错误位、错 token，从不注入硬失败）：全程不关、退避期零消息、每窗口 ≤ 4 条消息、档位始终在下限与顶档之间、故障停止 1.4 s 内无欠账且平台档位与记录一致。
  12. **回退开关** `pagani-cpufreq-retry=0`：D2 式 BUSY 立即关闭并回开机档，启动 BUSY 立即关闭（旧行为）。

## 6. 内核编译（零新增告警）

- 工具：`compile-armcpu.py`（本目录）。用 GPU 树的 IOKit 编译记录 `build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json`，与 `scripts/build-arm-platform.py` 完全相同的参数（`-Weverything` 加记录自带的 `-Wno-*`，含 `-Wshorten-64-to-32`、`-Wimplicit-int-conversion`），目标文件只写到草稿目录，不写 `out-gpu`。`--strict` 为 `scripts/build-arm-driver.py` 的配方（`-Wall -Wextra -Werror` 加其 `-Wno-*`）。
- 记录配方：改后 24 条告警，与基线逐条相同（忽略行号）——16 条来自 XNU 头文件（`IOLib.h` 10、`OSValueObject.h` 4、`vm_kern.h` 1、`cs_blobs.h` 1）、2 条来自 `OSDefineMetaClassAndStructors` 宏、5 条是 GIC/SPI 导出函数原有的 `-Wmissing-prototypes`、1 条为编译器的 `EXPORT_HDRS/libsa` 目录不存在提示；`ScmiPerf.h`、`CpuFreqGovernor.h` 及 cpufreq 部分 0 条。
- 严格配方：改后 2 条，与基线相同（`ArmCpu::start` 里原有两处 `-Wsign-conversion`，来自 IOKit 接口的有符号参数），通过 `-Werror`。
- 输出：`logs/kernel-compile-record.txt`、`logs/kernel-compile-record-baseline.txt`、`logs/kernel-compile-strict.txt`、`logs/kernel-compile-strict-baseline.txt`；均为 arm64 `MH_OBJECT`。

## 7. 集成

- 主会话无需套用任何东西：五个文件已在原处修改；`out-gpu/.../tree/drivers/PaganiArmCpu` 是指向 `UniXNU-Pagani-Drivers/drivers/PaganiArmCpu` 的符号链接，下次内核构建（`scripts/build-arm-platform.py` → `build/armcpu/libArmCpu.a`）自动带上。
- 共享集成文件（Stage7Probe、components.toml、board.toml、defconfig、patches/xnu/series、make-a830-layout.sh、make-phone-seed.py）均未改，也不需要改；`pagani-cpufreq=2` 等现有启动参数不变。
- `cpufreq-retry.diff`：相对 `baseline/`（改前快照，sha256 见下）的统一 diff，仅供审阅；已验证在基线副本上 `patch -p1` 干净套用且结果与现文件逐字节相同。`final-sha256.txt` 为改后文件哈希。
- 基线快照 sha256：`ArmCpu.cpp dd8c7164…`、`CpuFreqGovernor.h d9948697…`、`ScmiPerf.h d05cebfa…`、`README.md e980a8d4…`、`Test/scmi-perf.cpp 6c386c3b…`。注意 `ArmCpu.cpp`、`README.md` 在 git 里本就有大量未提交的旧改动，基线是今晚改动前的工作区文件，不是 git HEAD。
- 可选：若早上需要回到旧行为，启动参数加 `pagani-cpufreq-retry=0`（无需重编）。

## 8. 早上上机要看的行

- 正常：开机行 `cpufreq: scmi base … mode 2`（末尾没有 `, no retries`），之后不应再出现 `cpufreq: off:`。
- 若遇 BUSY：应看到 `cpufreq: retry: status -6 lvl get dX[ after lvl set dX], next in 20 ms`，紧接着（约 20 ms 后）`cpufreq: retry ok: … after 1 failure in 20 ms (busy 1, no answer 0)`；状态行 `err` 加 1；心跳 `cpu …MHz` 继续随负载变化，不再锁死在 1986 / 1012 MHz。
- 若 `after lvl set` 经常出现：支持"刚设置完马上读会撞 BUSY"的猜测，可考虑让复查避开刚设置过的窗口（未实施，先看数据）。
- 只有出现 `cpufreq: off: … x48` / `… x6` 或硬失败原因时才是真故障；`x6` 说明 CPUCP 停止响应，应保留日志。
- 需要回退时：`pagani-cpufreq-retry=0`。

## 9. 风险与未决

1. 预算数值（设置 48 次 ≈ 54 s、无应答 6 次 ≈ 1 s、复查 4 次、退避 20→1280 ms）是按机理推定的，没有真机统计；全部日志里 BUSY 只出现过 1 次。
2. 硬失败后仍"回开机档"：D2 中 d1 的开机档是 lv0（1017.6 MHz），所以真正的硬失败后超大核仍会停在约 1 GHz。本次未改（任务未要求；硬失败时通道多半已不可用，回设也未必成功）。后续可选：回到调速器下限而非开机档。
3. BUSY 时平台是否已执行设置不可知：已一律当"未知"处理并补发（有收敛测试）。
4. 状态行 `err` 含义扩大（拒绝 + 重试过的失败），与旧照片/旧日志对比时注意。
5. 模式 1 的 5 秒复查从线程循环移入 `controlWindowCpus`（行为等价；模式 1 只作对照）。
6. `PERF_LIMITS_GET` 被固件拒绝（D：-3）仍是"停查 + 计数"，没有其它来源可解释降档原因。
7. 固件约 90 °C 自主压频属 CPUCP 行为，调速器只记录，本修复不改变。
8. 以上全部为源码 + 主机测试结论；未上真机。主机测试的传输超时用实时 50 ms，已在并行负载下验证稳定。
