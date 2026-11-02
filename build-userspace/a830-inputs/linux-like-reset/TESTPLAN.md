# pagani-reset-seq：实现说明与真机测试计划（2026-10-31，按审查意见修订）

事实与出处见同目录 `FACTS.md`（第 7 节是审查后补核的）；源码补丁 `pagani-reset-seq.diff`（相对 `../snapshots/BootProgress-0636/`，
在 `UniXNU-Pagani-Drivers` 根目录 `patch -R -p1 < pagani-reset-seq.diff` 可撤回）；审查修复这一步单独的差异在 `review-fix.diff`
（相对 `before-review-fix/`，那里存着修复前的源码与笔记）。未提交、未上机、未做整核构建。
**注意**：07:46 起构建树的 `UniXNU/out-gpu/.../tree/drivers/BootProgress` 指向 `snapshots/BootProgress-0636`（不含本功能），
主会话现在构建的内核**不带** pagani-reset-seq；要上机，得由主会话把链接改回驱动仓库再构建。

## 1. 位与顺序（RamoopsLog.h 文档为准）

`pagani-reset-seq=<mask>`，缺省 0 = 一个额外调用都没有，复位路径与没有这个参数时逐字节相同。
**掩码必须写成小写 `0x` 加 1 到 8 位十六进制数字**，例如 `pagani-reset-seq=0x101`。写成 `0X101`、`101`、`0101`、`0x0000000101`、
单词或只写参数名，都会被**整体拒绝**（什么都不执行，复位与不带参数时相同；开机写一条 T 记录
`reset seq refused: pagani-reset-seq=... read as 0x...: <原因>; none of it runs ...`，状态行带 `reset seq REFUSED 0x...`）。
原因：XNU 的解析器会把这些写法变成别的数（`0X10F` → 0x00315830，含 0x10 位；`010` → 八进制 8；只写参数名 → 1；
超过 8 位只留低 32 位），出现未知位或写法不对就拒绝。同一参数出现多次时以最后一次为准（与 XNU 一致）。

只在第一次进入 `reset_now`（定时器、观察者兜底、备份定时器、panic、无屏 panic）时执行一次，位置在 "reset reason" T 记录之后、PSCI 之前，按 Linux 顺序：

| 位 | 动作 | 调用（快速调用，SMC #0） | Linux 对应 |
|---|---|---|---|
| 0x002 | 清下载模式：读、写 0、再读 | 0xC2000501(0x1FD9000)，0xC2000502(0x1FD9000, 0)，0xC2000501 | qcom_dload_reboot → qcom_scm_set_download_mode(NODUMP) |
| 0x001 | 关 SDI | 0xC2000109(1, 0) | qcom_scm_shutdown → qcom_scm_disable_sdi |
| 0x004 | 停 SPMI PMIC 仲裁器 | 0xC2000901(0) | qcom_scm_shutdown → qcom_scm_halt_spmi_pmic_arbiter |
| 0x008 | 停其余 CPU（XNU 立即型跨核调用：屏蔽 DAIF、wfe/wfi 死循环），最多等 1 秒 | 无 SCM | machine_restart → smp_send_stop |
| 0x010 | （超出要求的附加位）OEM 热重启；返回则照常走 PSCI | 0xC3000922() | qcom_scm_do_restart（reboot_mode=warm 时） |
| 0x100 | 只读探测：开机时 IS_CALL_AVAIL（0x42000601）问 6 个调用 + 读下载模式，屏幕显示一行；复位时再读一次下载模式 | 0x42000601、0xC2000501 | — |

审查修复后新增的规则：
- **下载模式联锁（F1）**：0x001、0x004、0x008、0x010 中任一位，或 `pagani-reset-cold=1` 与任一位同时出现，都会**自动带上 0x002 的清除**
  （T 记录写 `dload-clear(implied)`，三条 U 记录带 `(implied)`）——安卓每次重启都先清成 NODUMP，这不算多一个变量（下载模式本来是 0 时写 0 不改变任何东西）。
  清完**回读必须是 0**（x0 = 0 且值为 0），这些步骤和 `pagani-reset-cold` 才生效；回读不是 0、读失败或 64 次仍 INTERRUPTED，就一条 T 记录
  `reset seq: the download mode reads back ..., not 0: <被跳过的步骤> skipped (a warm reset would enter the crash dump)`，跳过它们，
  `pagani-reset-cold` 也不生效（先 SYSTEM_RESET2 再 SYSTEM_RESET，即已知的 HARD 老路径）。显式带 0x002 时同样受这个回读约束。
- **看门狗（S3）**：带掩码时复位开头不再关看门狗，而是 PET 一次再 SET_TIME(2000, 4000)、停止喂狗、保持开着，步骤卡住（TZ 之外）4 秒后咬合复位；
  执行步骤的 CPU 做完后、调 PSCI 之前才关掉（与不带掩码时 PSCI 时刻的状态相同）。T 记录：`wdog: <原因> reset: pets stopped, PET(...)=0 SET_TIME(... bark 2000, bite 4000)=0: armed through the reset steps (pagani-reset-seq)`，
  做完后 `wdog: <原因> reset steps done: CONTROL(0x86000005, 2)=0 disabled the watchdog before PSCI`。后来的复位者不再动看门狗。
  panic 路径例外：panic 时看门狗按原设计已关（RamoopsLog 之后会复位），步骤在这条路径上没有看门狗兜底。
- **中断（S4）**：0x008 停了其他 CPU 之后，本 CPU 一直屏蔽中断直到复位（和 PSCI 返回后的重试），同 Linux 的 machine_restart；不含 0x008（或被联锁跳过）时，步骤后恢复调用者原来的中断状态，同修复前。
- 每个 SCM 调用写一条 U 记录（功能号、参数、x0 低 32 位有符号；读操作附 x1），写完再做下一步；失败照样继续到 PSCI。
- 开始一条 T：`reset seq 0x.. (pagani-reset-seq) reason=..: <步骤>`；结束一条 T：`reset seq done in N ms; resetting through PSCI`。
- 后来的复位者（另一 CPU、panic）不再执行步骤，正在执行时最多等 2 秒。
- **看门狗咬合路径（`pagani-reset-wdog=1` 且 pagani-wdog 已上狗）一步都不做**，只记一条 T：`reset seq 0x.. not applied: the watchdog bite resets (pagani-reset-wdog)`。所以测试镜像必须去掉 `pagani-reset-wdog=1`。
- 0x008 会让观察者的 5 s 补发（G3）和备份定时器（G4）失效（它们在别的 CPU 上）；现在由步骤期间开着的看门狗（4 s 咬合）兜底。
- SPMI 竞争：PaganiInput 每 8 ms、pagani-battery-pmic 都只读 SPMI（观察者通道），没有先停；停仲裁器后在途或之后的读会失败（-5/-110），只算一次缺样本；仲裁器停在 PS_HOLD 拉低之前，这正是 Linux 要防的情形。

## 2. 主机验证（已做，2026-10-31 审查修复后重跑）

- `Test/ramoops.c`：原有用例按新行为更新；新增联锁（单步自动带清除、回读非 0/读失败/一直 INTERRUPTED 时跳过、显式 0x002 同样受约束、`pagani-reset-cold` 带清除且只调 SYSTEM_RESET、清除失败时 cold 不生效、多步与 cold 同时跳过、cold 不带掩码时与原来相同）、
  看门狗（每个复位者先 hold，只有执行步骤的 CPU 在 RESET2 之前 release；咬合路径不 hold 不 release；看门狗未上时照常执行）、中断（观察者线程路径：含 0x008 时 PSCI 调用处于屏蔽状态，其他掩码和无掩码时恢复）、
  拒绝（`0X10F`、单词、八进制、十进制、`0b`、只写名字、9 位与超长十六进制、`k` 后缀、未知位、最后一次出现、文本与解析值不符；接受大写十六进制数字、前导 0、制表符分隔；日志关闭时状态行）。`fresh()` 对每个不带掩码的旧场景断言：没有 SCM、没有停核、没有 hold/release。
- `Test/gunyah-wdog.c` 新增 `test_hold_for_steps`：先 PET 再 SET_TIME(2000, 4000)；hold 后线程、心跳、观察者都不再喂狗；第二个复位者和 before_reset 不改变任何东西；release 只关一次；之后 PSCI 返回照常重新上狗；步骤卡住时在 hold 的 PET 后 4 s 咬合（即使此前喂狗已停 17 s）；看门狗不是我们的、panic 后已关、咬合路径已接管时什么都不做。
- `Test/console.c`：启动参数随掩码；`Test/crash.c`：补三个桩函数。
- 四个测试 -O2/-O0 + ASan/UBSan 全过；`Test/panic-entry.py` 过；`工具/gpu-test-loop/run-tests.sh` 全过（`tools/run-host-tests.sh` 一次跑完）。
- 修复前后 `ramoops --dump`、`crash --dump` 的 zone 镜像与 expected.tsv 逐字节相同（哈希在 `before-review-fix/dump-hashes.txt`）。
- 40 个变异全部被测出（`tools/mutate.py`：原 M1–M16 更新模式，新增 M17–M35 覆盖联锁（含 x0、x1 按低 32 位判定）、看门狗、中断、拒绝，W1–W5 覆盖 GunyahWdog 的 hold/release）。
- 内核配方单独编译（`tools/kernel-compile-check.py`，只读构建树，输出在草稿目录）：三个源文件除构建树缺 `EXPORT_HDRS/libsa` 目录的环境告警外零告警；无浮点/SIMD 寄存器、无 mem* 调用；RamoopsLog.o 新增未定义符号只有 `_gunyah_wdog_hold_for_steps`、`_gunyah_wdog_release_hold`（GunyahWdog.o 定义）。

## 3. 真机测试计划（用户在场，一次只改一个变量）

### 0. 上机前（必做，排在所有 XNU 轮次之前）
- 只读：`adb shell cat /sys/kernel/reboot/mode`（若是 `warm`，安卓的重启走 OEM 热重启 0xC3000922，下面第 6 轮提前）、
  `/sys/module/qcom_scm/parameters/download_mode`、`/sys/pmic_info/poff_reason`（留底）。
- **必做：一次普通 `adb reboot`（不带 bootloader 等参数；重启手机前照例先征得用户同意）**，回到安卓后读 `poff_reason` 第 0 条。目前唯一的 WARM 样本是带命令的 `reboot bootloader`，而 H1–H5 普通重启同样会做：
  - 是 `WARM_RESET`：普通重启也热，继续第 1 轮。注意这仍不能完全排除“SDAM 重启原因”的作用（安卓普通重启进内核时 cmd 是空串还是 NULL 未核对，空串时 OPLUS 版本照样写 0x3E）。
  - 是 `HARD_RESET`：热复位取决于重启命令（旧版高通 msm-poweroff 的策略正是如此，见 FACTS 第 7 节），第 1–6 轮大概率白做；**停下 XNU 轮次**，先研究“带重启原因的复位”（XNU 要写 PMIC SDAM 的 restart_reason，是新的 SPMI 写路径，需单独设计与评审）。

### 镜像
- 每轮同一镜像，只改 `pagani-reset-seq`；保留 `pagani-ramoops=1`、`pagani-wdog=30`（复位前卡死的保险；现在也护着步骤）、`pagani-panic-reset`。
- **去掉 `pagani-reset-wdog=1`**（咬合路径不执行任何步骤）。
- **去掉 `pagani-overlay=0`**（否则探测行和状态行不画在屏幕上；硬复位又可能丢日志，屏幕是唯一可靠的现场）。
- **每轮都加 `pagani-reset-cold=1`**（S1：最后的 PSCI 调用与安卓冷重启完全一致，只调 SYSTEM_RESET，不再先调失败的 SYSTEM_RESET2）。
  `pagani-reset-cold=1` 一定要和 `pagani-reset-seq`（至少 `0x100`）一起用：只有带掩码时才有下载模式的清除与回读联锁；单用 `pagani-reset-cold=1` 是旧行为，没有联锁。
- 掩码一律写小写 `0x`（见第 1 节）；建议 `pagani-reset-after=300`（省时间、DRAM 更凉）。0x100 每轮都带（只读，不算变量）。
- 开机后看屏幕：状态行应含 `reset seq 0x1..`（若是 `reset seq REFUSED` 说明参数写错，这一轮不算数）；探测行 `reset seq 0x1.. avail sdi . dl . rd . wr . spmi . oem . dload 0x..`。

### 顺序（前一轮看完再做下一轮；每轮都带 `pagani-reset-cold=1`）
1. `0x100`（单独验证 H4）：只是不再先调失败的 SYSTEM_RESET2；联锁自动带清除（下载模式本来是 0 时等于没做）。若 WARM：H4 成立，同样参数再跑一次确认，回路镜像用 `pagani-reset-cold=1 pagani-reset-seq=0x100` 即可，后面各轮不用做。
2. `0x101`：关 SDI（H1，头号嫌疑：Norris 注释说 SDI 开着会妨碍正常热重启；安卓 probe 时就关了 SDI）。
3. `0x104`：只停 SPMI 仲裁器（H2；也可能卡在 TZ 里，见风险）。
4. `0x108`：只停其余 CPU。
5. `0x10f` + `pagani-reset-cold=1`：Linux 冷重启路径的全部步骤（清下载模式、关 SDI、停仲裁器、停核，最后只调 SYSTEM_RESET）。
6. 以上都不是 WARM：`0x11f`（第 5 轮再加 OEM 热重启，等于 Linux 热重启路径的完整顺序）；若成立，再试 `0x112` 找最小集合。
- 第 2–5 轮都是在第 1 轮（cold + 自动清除）之上只加一步。0x002 不单独成轮：每轮都已带上。
- 一出现 WARM：同一参数再跑一次确认可复现；之后可按需减位找最小集合。成立后回路镜像用那组参数（或最小集合 + 0x004 防 SPMI 锁死），并去掉 `pagani-reset-wdog`。

### 判定
- 主信号：`/sys/pmic_info/poff_reason` 第 0 条 `Reset Type: WARM_RESET`（且 Warm Reset Count 加 1）；HARD 为 `Reset Type: HARD_RESET,Reset Trigger: PS_HOLD`。dmesg 的 `PMIC PON log` 最后一对同理。`gpu-loop.py` 会存进 device.txt 的 `pmic_info`、`dmesg.pmic_pon`。
- ramoops 存活：`dmesg.ramoops` 没有 `found existing invalid buffer`，两个 pstore 文件都在；summary.txt 记录基本全部 verified、`vote` 行的 p1（1 位丢失率）接近 0（HARD 时 0.08–0.23；长按复位实测零翻转）；`grep "reset seq" merged.log` 能看到每步 x0 和 `reset seq done ... resetting through PSCI`（带 0x010 时最后是 `... 0xc3000922() next`）。
- 日志里若有 `reset seq: the download mode reads back ..., not 0: ... skipped`：这一轮**没有测到**它的步骤（结果应是 HARD），先查清下载模式为何清不掉，再重做。
- 只有 PMIC 说 WARM 而日志仍大量掉位，要记下来（说明还有别的因素）。

### 风险与恢复
- CrashDump/转储画面：代码联锁保证步骤和 `pagani-reset-cold` 只在下载模式回读为 0 时生效，热复位不会带着非 0 的下载模式发生。剩余途径：步骤卡住后的看门狗咬合（卡在清除之后的步骤时下载模式已清；若卡在清除调用本身，咬合时可能还没清）；panic 路径同样先清再做。0x010 的行为未知。
- 卡在 XNU 画面不复位：0x004（TZ 若需要 SPMI）、0x010 可能出现。TZ 之外的卡死由看门狗在 4 秒后咬合复位；卡在 TZ 里面（SCM 调用不返回且占着核或锁）XNU 无能为力；panic 路径上看门狗已关。
- 恢复：长按电源键 + 音量下键约 10 秒强制重启（10-30 18:35 用过）。这次长按在 poff_reason 里会是另一种触发源，记录时区分开。
- 每轮约 6–8 分钟；用户全程看着屏幕直到回到安卓。

### 建议但本轮未实现（待主会话决定）
- 审查 S2 建议的只读诊断：在开机和复位前经 SPMI 观察者通道读 pmk8550 PON_PBS 的 PS_HOLD_RST_CTL/CTL2（SID 0、外设 0x08、偏移 0x52/0x53；类型 1 热、4 关机、7 硬，bit7 使能），
  直接看到 PSCI 之前 PMIC 里配的复位类型，分清是 TZ 在复位时改写还是开机后就已是 HARD。设计：单独一位（如 0x200），只在开机探测时（线程上下文）映射仲裁器窗口（核心 0xc400000、配置 0xc42d000、观察者 0xc440000，EE 0），
  读法照 Sm8750Platform PeripheralIO.h 的 spmi_find/spmi_read8；复位时只在已映射时读，有上限（每次至多 1 ms）。未做的原因：这是 BootProgress 里新的硬件访问路径（含屏蔽中断的复位路径），不属于对已审代码的修复，应单独评审。
- 若第 0 步普通重启是 HARD：研究 XNU 写 SDAM restart_reason（pmk8550 `restart@48`）的可行性，同样是新硬件写路径。
