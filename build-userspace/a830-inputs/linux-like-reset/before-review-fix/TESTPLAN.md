# pagani-reset-seq：实现说明与真机测试计划（2026-10-31）

事实与出处见同目录 `FACTS.md`；源码补丁 `pagani-reset-seq.diff`（相对 `../snapshots/BootProgress-0636/`，
在 `UniXNU-Pagani-Drivers` 根目录 `patch -R -p1 < pagani-reset-seq.diff` 可撤回）。未提交、未上机、未做整核构建。
注意：`UniXNU/out-gpu/.../tree/drivers/BootProgress` 是指向驱动仓库的链接，主会话下一次构建内核就会带上这些改动（缺省 0 时行为不变）。

## 1. 位与顺序（RamoopsLog.h 文档为准）

`pagani-reset-seq=<mask>`（XNU 的 boot-arg 解析支持 0x 十六进制；缺省 0 = 一个额外调用都没有）。
只在第一次进入 `reset_now`（定时器、观察者兜底、备份定时器、panic、无屏 panic）时执行一次，位置在 "reset reason" T 记录之后、PSCI 之前，按 Linux 顺序：

| 位 | 动作 | 调用（快速调用，SMC #0） | Linux 对应 |
|---|---|---|---|
| 0x002 | 清下载模式：读、写 0、再读 | 0xC2000501(0x1FD9000)，0xC2000502(0x1FD9000, 0)，0xC2000501 | qcom_dload_reboot → qcom_scm_set_download_mode(NODUMP) |
| 0x001 | 关 SDI | 0xC2000109(1, 0) | qcom_scm_shutdown → qcom_scm_disable_sdi |
| 0x004 | 停 SPMI PMIC 仲裁器 | 0xC2000901(0) | qcom_scm_shutdown → qcom_scm_halt_spmi_pmic_arbiter |
| 0x008 | 停其余 CPU（XNU 立即型跨核调用：屏蔽 DAIF、wfe/wfi 死循环），最多等 1 秒 | 无 SCM | machine_restart → smp_send_stop |
| 0x010 | （超出要求的附加位）OEM 热重启；返回则照常走 PSCI | 0xC3000922() | qcom_scm_do_restart（reboot_mode=warm 时） |
| 0x100 | 只读探测：开机时 IS_CALL_AVAIL（0x42000601）问 6 个调用 + 读下载模式，屏幕显示一行；复位时再读一次下载模式 | 0x42000601、0xC2000501 | — |

- 每个 SCM 调用写一条 U 记录（功能号、参数、x0 低 32 位有符号；读操作附 x1），写完再做下一步；失败照样继续到 PSCI。
- 开始一条 T：`reset seq 0x.. (pagani-reset-seq) reason=..: <步骤>`；结束一条 T：`reset seq done in N ms; resetting through PSCI`。
- 执行期间本 CPU 屏蔽中断；后来的复位者（另一 CPU、panic）不再执行，正在执行时最多等 2 秒。
- **看门狗咬合路径（`pagani-reset-wdog=1` 且 pagani-wdog 已上狗）一步都不做**，只记一条 T：`reset seq 0x.. not applied: the watchdog bite resets (pagani-reset-wdog)`。所以测试镜像必须去掉 `pagani-reset-wdog=1`。
- 0x008 会让观察者的 5 s 补发（G3）和备份定时器（G4）失效（它们在别的 CPU 上）；PSCI 返回时本 CPU 的每秒重试 + 重新上狗（H2 修复）仍在。
- SPMI 竞争：PaganiInput 每 8 ms、pagani-battery-pmic 都只读 SPMI（观察者通道），没有先停；停仲裁器后在途或之后的读会失败（-5/-110），只算一次缺样本；仲裁器停在 PS_HOLD 拉低之前，这正是 Linux 要防的情形。

## 2. 主机验证（已做）

- `Test/ramoops.c` 新增：缺省/=0 零调用（每个旧场景都由 fresh() 自动断言没有 SCM、没有停核）、开机探测与屏幕行、每一位单独、全部位的顺序与记录、INTERRUPTED 续调（x0=1、带回 x6、x1..x3 不变）、失败与 64 次仍 INTERRUPTED 照常继续、CPU 晚到/不到（1 秒）、未知位、一次性（后续触发、观察者补发、嵌套复位者等满 2 秒、两线程竞争 ×64）、panic 与无屏 panic、咬合路径、日志关闭时照样执行。
- `Test/console.c` 新增：探测摘要经 BootProgress 变成阶段行（屏幕历史 + S 记录）。`Test/crash.c` 只补两个桩函数。
- 四个测试 -O2/-O0 + ASan/UBSan 全过；`Test/panic-entry.py` 过；`工具/gpu-test-loop/run-tests.sh` 全过。
- 改动前后 `ramoops --dump`、`crash --dump` 的 zone 镜像与 expected.tsv 逐字节相同（run id 来自计数器，说明旧场景的调用次数也一样）。
- 16 个变异全部被测出（`tools/mutate.py`）。
- 内核配方单独编译（`tools/kernel-compile-check.py`，只读构建树，输出在草稿目录）：三个源文件零告警、无浮点寄存器、无 memset/memcpy；新增未定义符号只有 `_cpu_datap`、`_cpu_immediate_xcall`、`_ml_get_max_cpu_number`（XNU 自带）。

## 3. 真机测试计划（用户在场，一次只改一个变量）

### 0. 上机前（安卓里只读，零风险）
- `adb shell cat /sys/kernel/reboot/mode`：若是 `warm`，安卓的重启走 OEM 热重启 0xC3000922，第 6 步提前。
- `adb shell cat /sys/module/qcom_scm/parameters/download_mode`、`/sys/pmic_info/poff_reason`（留底）。
- 可选：一次普通 `adb reboot`（不带 bootloader），看 poff_reason[0] 是否也是 WARM_RESET——目前唯一的 WARM 样本是 `reboot bootloader`。

### 镜像
- 每轮同一镜像，只改 `pagani-reset-seq`；保留 `pagani-ramoops=1`、`pagani-wdog=30`（复位前卡死的保险）、`pagani-panic-reset`；**去掉 `pagani-reset-wdog=1`**。建议 `pagani-reset-after=300`（省时间、DRAM 更凉）。0x100 每轮都带（只读，不算变量）。

### 顺序（前一轮看完再做下一轮）
1. `0x100`：只探测。预期仍 HARD_RESET/PS_HOLD。看屏幕阶段行 `reset seq 0x100 avail sdi . dl . rd . wr . spmi . oem . dload 0x..`：各调用是否可用（e-1 = Gunyah 或 TZ 不认）；**dload 非 0 则以后每轮都加 0x002**（否则热复位后 XBL 可能进转储流程）。
2. `0x101`（dload 非 0 则 `0x103`）：关 SDI。头号嫌疑（Norris：SDI 开着会妨碍正常热重启；安卓 probe 时就关了 SDI）。
3. `0x104`：只停 SPMI 仲裁器（若 TZ 像 TFA 那样复位时经 SPMI 写 PS_HOLD 类型，结果会变；也可能卡在 TZ 里）。
4. `0x102`：只清下载模式（仅当第 1 步读到非 0 才有意义）。
5. `0x108`：只停其余 CPU。
6. `0x10f`：Linux 的完整冷路径。
7. 以上都不是 WARM：`0x11f`（在第 6 步上只加 OEM 热重启，等于 Linux 热重启路径的完整顺序）；若成立，再试 `0x112` 找最小集合。
8. 排除项（不需新代码）：`pagani-reset-cold=1` + `0x100`，排除“先调失败的 SYSTEM_RESET2”。
- 一出现 WARM：同一掩码再跑一次确认可复现；之后可按需减位找最小集合。成立后建议回路镜像用 `0x00f`（或最小集合 + 0x004 防 SPMI 锁死），并去掉 `pagani-reset-wdog`。

### 判定
- 主信号：`/sys/pmic_info/poff_reason` 第 0 条 `Reset Type: WARM_RESET`（且 Warm Reset Count 加 1）；HARD 为 `Reset Type: HARD_RESET,Reset Trigger: PS_HOLD`。dmesg 的 `PMIC PON log` 最后一对同理。`gpu-loop.py` 会存进 device.txt 的 `pmic_info`、`dmesg.pmic_pon`。
- ramoops 存活：`dmesg.ramoops` 没有 `found existing invalid buffer`，两个 pstore 文件都在；summary.txt 记录基本全部 verified、`vote` 行的 p1（1 位丢失率）接近 0（HARD 时 0.08–0.23；长按复位实测零翻转）；`grep "reset seq" merged.log` 能看到每步 x0 和 `reset seq done ... resetting through PSCI`（带 0x010 时最后是 `... 0xc3000922() next`）。
- 只有 PMIC 说 WARM 而日志仍大量掉位，要记下来（说明还有别的因素）。

### 风险与恢复
- CrashDump/转储画面：dload 非 0 且复位变热时很可能出现（满转储停在等 USB 抓取的画面；mini 转储可能由 XBL 写 rawdump 分区后重启——这是引导程序写手机存储，要避免，所以 dload 非 0 必须带 0x002）。dload 为 0 时概率低；0x010 的行为未知，概率中等。
- 卡在 XNU 画面不复位：0x004（TZ 若需要 SPMI）、0x010 可能出现；看门狗在 PSCI 前已关，不会自己咬；0x008 还会让 G3/G4 失效。
- 恢复：长按电源键 + 音量下键约 10 秒强制重启（10-30 18:35 用过）。这次长按在 poff_reason 里会是另一种触发源，记录时区分开。
- 每轮约 6–8 分钟；用户全程看着屏幕直到回到安卓。
