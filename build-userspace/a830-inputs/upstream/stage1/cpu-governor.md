# 第 1 阶段：CPU 调速器减少打开应用和按键时的升频滞后（2026-10-31）

全程没有上机，没有运行 adb、fastboot，没有跑整核，没有提交。Metal、Turnip、翻译层、`NativeAdrenoABI.h` 都没有动。`Adreno830`、XNU 补丁序列也都没有动。

证据标记：**【读码】** 读源码确认；**【日志】** `工具/gpu-test-loop/runs/` 的真机日志；**【主机实测】** 本次在本机跑出来的结果；**【推测】** 没有直接证据。

---

## 0. 结论

1. 触摸提频保持时间从 100 ms 提到 **250 ms**，参数 `pagani-cpufreq-touch-ms`。
2. 新增**应用启动提升**：一次进程启动发生在触摸帧之后 1000 ms 内，两个域都至少到 **2918.4 / 3513.6 MHz**，保持 **1500 ms**，参数 `pagani-cpufreq-launch-ms`，设 0 关闭。
3. **不用改 XNU，不用改 Adreno830。** 检测信号是 XNU 现成的全局计数 `nextpidversion`（每次 fork、每次 exec 各加一），调速线程每 20 ms 读一次，再用触摸做门控。`patches/xnu/series` 没有追加。
4. 温控不被绕过：启动提升的档位在 GPU 受热限时始终停在 2745.6 / 3072 MHz，触摸的解除窗口里也一样。**95 °C 紧急状态和熄屏时不提升**，进行中的提升立刻结束。触摸解除温控上限的时长仍是 100 ms，没有随保持时间变长。
5. SCMI 重试逻辑没有改。主机测试基线 2246649 项，现在 **2826011 项**，全过（审查后比初版 2862368 项少，是随机用例里紧急窗口不再提升，`if (g.launching)` 下的断言少跑；用例本身只增不减）。
6. 真机效果**没有验证**，第 6 节写了上机怎么判读。

---

## 1. 改动的文件

都在 `UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/`，源码注释沿用英文。

| 文件 | 改动 |
| --- | --- |
| `CpuFreqGovernor.h` | `kTouchMs` 改为 250（`:89`）；新增 `kTouchMaxMs`、`kTouchLiftMs`、`kLaunchMs`、`kLaunchMaxMs`、`kLaunchTouchMs`、`kLaunchLineMs`、`kLaunchKhz*`；新增 `LaunchInput`、`launchStep`（`:377`）、`launchLevel`（`:395`）、`noteLaunch`（`:1111`）；`governorStep`、`controlWindowCpus`、`controlWindow` 加一个带默认值的尾参；状态行末尾加 `app N`（放得下才显示） |
| `ArmCpu.cpp` | 读 `nextpidversion`、`pagani_screen_state`（`:452` 起）；解析两个启动参数（`:487-488`）；导出 `pagani_cpufreq_boost()`（`:507`） |
| `Test/scmi-perf.cpp` | 新增 5 组用例，扩展随机测试，调整既有断言（第 5 节列明） |
| `README.md` | 新行为和参数 |

没有改：`UniXNU/drivers/AdrenoMetal`、Turnip、翻译层、`NativeAdrenoABI.h`、`Adreno830/`、`BootProgress`、`NativeUfs`、`工具/gpu-test-loop`、`build-native-ufs.py`、`UniXNU-Pagani_SM8750-gpu/patches/xnu/series`。

---

## 2. 启动信号怎么选的

任务给的优先顺序是先用已有信号，确实不够才改 XNU。逐个核实后的结论：

| 信号 | 结论 | 依据 |
| --- | --- | --- |
| Adreno830 用户客户端新连接 | **不用** | App 的画面由 render server 合成，App 进程只有自己建 Metal 设备才开连接，比如 MobileSMS 是 Core Image 用到才要（`Adreno830/docs/ARCHITECTURE.md` 第 1187 行）【读码】。名单 `@apps @extensions !SpringBoard` 只决定谁可以开，不代表每个 App 都开。开连接时机比进程启动晚，每进程两次（`NativeAdrenoPublish.cpp:209`）。海报、小组件、图标代理也会开，与用户打开应用无关。全部日志里没有一行 `CLI o`，时序没有任何数据【日志】 |
| 触摸手势结束 | **只做门控** | 内核只看到触摸帧计数，点图标、按键、滑动抬手分不开。单独当启动信号，打字时每键都会触发 1.5 s 高档，回到 10-30 的发热问题 |
| XNU exec 钩子（新补丁） | **不需要** | 能判断路径在不在 `.app` 下，但系统里有不少后台 `.app`，仍要和触摸联用【推测】。联用后多出来的准确度不值得多一个补丁 |
| kauth `KAUTH_FILEOP_EXEC` | **不用** | 不改 XNU 也能收到 exec。但注册监听后每次 open、close 都要走 `vn_getpath`（`kern_authorization.c:536-544`，`vfs_vnops.c:199`）【读码】，这是全局开销，违背“别破坏流畅” |
| `nprocs` | **不用** | 有加有减，同一窗口一进一出会漏掉；还是 private extern |
| **`nextpidversion` + 触摸门控** | **采用** | 见下 |

`nextpidversion` 的事实【读码，`xnu-work` 工作树】：

- `bsd/kern/kern_fork.c:158` 定义，`int nextpidversion = 0;`，单调递增。
- `forkproc` 新进程时加一（`:984`），`exec_handle_sugid` 每次 exec 加一（`kern_exec.c:7384`，由 `exec_mach_imgact` 无条件调用，`:1631`）。所以一次 spawn 加二，`xpcproxy` 再 exec 成守护进程时再加一。
- 增加发生在 fork 那一刻，比日志里的 `exec process signature enter` 还早。
- 上一版内核 `nm` 确认是外部符号：`(__DATA,__common) external _nextpidversion`。`kern_fork.o` 是普通 Mach-O，不是 LTO 位码。`libArmCpu.a` 用 `-force_load` 链进内核，和 `hz_tick_interval` 同样的方式引用。

**门控的代价有数据**【日志，统计脚本 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/cpu-gov/spawn_rate2.py`】。16 份日志的 20 个时段，把 1.5 s 内的 exec 合成一组：

| 时段 | 总时长 | 组数 | 每分钟 | 每组 1.5 s 时覆盖的时间 |
| --- | --- | --- | --- | --- |
| 开机 90–300 s | 2326 s | 95 | 2.5 | 6.7% |
| 开机 300 s 之后 | 1216 s | 49 | 2.4 | 7.3% |

**这张表是平均值，不是上界（审查后更正）。** 20 个时段里混有无人操作的轮次，分母还含环形日志丢失的时段。只算序号连续、没被冲掉的时段，汇总是 2.7、2.8 组/分，覆盖 7.4%、8.6%；单时段最高 19.6 组/分。已知有人开应用的 pkgD、pkgD2 是 16.6%、27.5%，别的轮次最高 54.0%，但那一轮只留下 28 s（复算脚本 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/gov-review/blind2.py`）。这些日志没有触摸时间线，**门控能挡掉多少、用户操作时误触发占多少，都没有数据**。这些数字里也包含用户真开应用的那几次。

没有加“提升结束后 N 秒内不再触发”的冷却：它能给出真上界，但用户连着开两个应用时第二个就没有提升，而这正是要解决的现象。现在每次提升有时限，受热上限和紧急状态约束，`app` 计数和 `cpufreq: launch` 行能在第一轮真机日志里量出频率，有数据再定。

**覆盖不到的情况：** 已经在运行的 App 切回前台没有进程启动，只有 250 ms 的触摸保持和原有的 GPU 帧保底。内核里没有便宜且准确的信号能判断这种情况。

---

## 3. 策略和参数

| 参数 | 默认 | 上限 | 含义 |
| --- | --- | --- | --- |
| `pagani-cpufreq-touch-ms` | 250 | 2000 | 触摸档位保持到最后一帧之后多久。`100` 等于旧行为，`0` 不保持 |
| `pagani-cpufreq-launch-ms` | 1500 | 5000 | 启动提升时长。`0` 关闭 |

两个都**不需要写进命令行**，默认值就是推荐值，完整包的命令行不用增加字节。

**为什么选这些值：**

- 250 ms 在任务给的 200–300 ms 中间。打字一键约 200 ms 时，档位在两键之间不再掉下去。GPU 的触摸保持是 300 ms，两边接近。
- 1500 ms：D 包设置那次，exec 后 +1.31 s 的采样里还有 42.4 ms 的卡顿，+2.31 s 才恢复（`app-launch-jitter.md` 第 3 节）【日志】。1500 ms 落在任务给的 1–2 秒内，比该报告建议的 800–1200 ms 保守一点。
- 2918.4 / 3513.6 MHz 是 `app-launch-jitter.md` 6.1 节建议的档位。D 包日志里固件在高负载下也常把 d0 压到这一档（`d0 at lv12 2918MHz, asked lv15`）【日志】，再高的请求不一定拿得到。
- 两个域一起拉：Clutch 是单 pset，线程可能落在 0–5 号核，只拉 6、7 号核帮不到它们（`app-launch-jitter.md` 6.13）。
- 门控 1000 ms：松手到进程启动的实际延迟**没有测过**，这是留足的值。新日志行会给出实测（第 6 节）。

**规则：**

- 启动提升只是下限，负载规则可以更高。
- 窗口内再有进程启动就续期，但门控只看最后一个触摸帧，所以由进程启动触发的提升最晚在最后一个触摸帧之后 2500 ms 结束。
- 到期后走原有的 3-of-4 回落规则，3 个窗口（约 60 ms）回到目标档。
- 没有触摸时 launchd 自己起的进程不触发。计数器第一次读数不算事件。
- `pagani_cpufreq_boost()`：别的驱动明确知道有应用启动时可以调用，不需要触摸门控。任意线程、任意上下文，只加计数，不发 SCMI。**目前没有调用方。** 调用方要自带空的弱默认实现，因为静态链接的内核不接受未定义弱引用。

**默认开启的理由：**

1. 只抬下限，只有触摸后的进程启动才生效，单次有时限。
2. 受热上限压在它上面，95 °C 紧急状态和熄屏时根本不提升，`pagani-cpufreq-launch-ms=0` 一个参数关掉。熄屏判定直接读 `pagani_screen_state`，不依赖 `pagani-screenoff`：所有包都没有这个参数，触摸轮询也不看屏幕，初版靠屏幕策略挡不住（审查发现，已改代码）。
3. 主机测试验证了关闭时和没有这个功能的调速器逐窗口一致（3000 窗口随机对照）。
4. 第 1 阶段命令行几乎没有余量（986/1000），默认关就没法在用户手里生效。

**和 sheng 的区别（参考，未照搬）：** sheng 在每次触摸后把 P 簇和 GPU 保持在顶档 1000 ms（`docs/devices/sheng.md:552`）。我们这边 10-30 已经因为发热改掉过类似做法（PROGRESS 的 `cpu-gov` 条目）。这里把“每次触摸”和“应用启动”分开：触摸只保持中档 250 ms，高档只给启动窗口，且不到顶档。

**对功耗的影响【主机实测，模型】：** 每 600 ms 一次轻点的轻负载场景，平均频率从 1588/2302 MHz 变为 1742/2400 MHz，没有碰顶档，没有迟到帧。这是模型数字，不是真机功耗。

---

## 4. 温控和 SCMI

**温控优先：**

- `launchLevel`（`:395`）：只要 GPU 受热限，启动档位不超过 `x.cap`，不看是否在触摸解除窗口内。
- `g->capped`（`:434`）用新的 `lifted`（最后一帧后 100 ms），不用 `touched`。保持时间变成 250 ms，但能越过温控上限的窗口仍是 100 ms。主机场景“GPU 受热下的重负载”修改前后都是 87 个受限窗口、10 个被触摸解除窗口，一个没多。
- 95 °C 紧急状态穿透触摸和负载规则，行为不变；**启动提升在紧急状态下不开始，进行中的立刻结束**。初版只保证不越上限，实际把下限抬到上限：5% 负载下高于地板的时间从 300 ms 变成 2340 ms，其中 2220 ms 停在 2745/3072 MHz，这和 CPU 该让出余量相反（审查发现，已改）。
- 普通受热限（未到 95 °C）仍按 `launchLevel` 把下限抬到上限 2745.6 / 3072 MHz。是否降到触摸档留给主线：要改只动 `launchLevel` 一行。这里保留，理由是这时用户正在开应用，热上限本身已生效，时长有限。
- 固件自己压档（CPUCP）只读取和报告，提升不会反复请求。
- 显示熄灭时不触发，进行中的提升立刻结束，不管有没有 `pagani-screenoff`（`LaunchInput::screen`，每窗口多一次原子读）。

**SCMI：**

- `pagani_cpufreq_boost` 不发消息，所有档位都走原来的 `setLevel`，BUSY 重试、退避、预算全部不变。
- 窗口内不增加消息。打字快于约 300 ms 一键时，档位不再上下来回，SCMI 消息反而变少。
- BUSY 退避超过提升时长时，这次提升就落空了，这是预期行为：提升不等待、不延长，也不多发消息。

---

## 5. 主机结果【主机实测】

都是本机构建运行，没有一项在手机上跑过。

| 项 | 结果 |
| --- | --- |
| `Test/scmi-perf.cpp`（ASan + UBSan，`-Wall -Wextra -Werror`） | **PASS，2826011 项**，36 组。基线 2246649 项，29 组 |
| `Test/psci-idle.cpp` | PASS，2634553 项 |
| `Test/spi-gate.cpp` | PASS，15 项 |
| `ArmCpu.cpp` 按 `build-arm-platform.py` 的配方单独编译（用 `out-gpu` 树的 `IOPlatformExpert.cpo.json`，输出只进草稿目录） | 编译通过 |
| 告警 | 配方原样 24 条，`-Wall -Wextra` 组合 2 条，与改动前副本**完全一致**，新增 0 条。这些都是 XNU 头文件或既有函数（`armcpu_gic_diag` 等缺原型、`initCPUInterruptController` 的符号转换），没有落在新代码上 |
| 目标文件符号对比 | 只多 `_nextpidversion`（未定义）和 `_pagani_cpufreq_boost`（已定义） |
| 变异检查 | 逐条破坏 10 条规则，测试全部失败，说明用例真能抓到回归：启动提升不受温控约束、整个保持期解除温控、门控 `<=` 改 `<`、去掉门控、熄屏不取消、提升不到期、续期不刷新、时长仍用常量、首次读数当事件、参数不裁剪。审查后又加 8 条，全部抓到：紧急状态不拦、不读屏幕状态、读屏幕状态不取位 0、去掉策略的熄屏、`lifted` 去掉 `!dark`、驱动请求要求触摸、拦截时不清 `launchUntil`、`launchLevel` 不压上限 |

**新增用例：**

| 用例 | 覆盖 |
| --- | --- |
| `testTouchHold` | 100、200、300、0、60000（裁到 2000）ms 的保持与回落时刻；一键 220 ms 的打字节奏，250 ms 时 0 个窗口掉档，100 ms 时 289 个窗口里 104 个掉档 |
| `testLaunch` | 首次读数、无触摸的启动、进程启动 120 ms 后触发、续期、到期后 3 窗口回落且只多一条消息、门控边界（1000 ms 算、1020 ms 不算）、连续启动最长 2500 ms、负载规则高于提升、`pagani_cpufreq_boost`、日志限频、`launch-ms=0` 与无此功能逐窗口一致、裁到 5000 ms、`pagani-cpufreq-min` 高于提升档、模式 1 读计数但不使用 |
| `testLaunchCaps` | 受热限时全程 2745.6/3072 MHz（含触摸解除窗口，并逐条检查这一段发出的档位）、标志清除后回到提升档、标志到来立刻压下、紧急状态穿透触摸和重负载并结束提升、紧急状态下进程启动不提升（5% 负载只有触摸保持）、标志解除后恢复提升、5 s 检查落在提升窗口内时固件压档只读不重发、有无屏幕策略熄屏都不触发且进行中的结束、熄屏触摸不解除温控上限、没有过触摸时驱动请求也能提升、状态行示例（65 字符，再多一位就不显示 `app`） |
| `testLaunchBusy` | 提升开始时 BUSY 连续 1、3、5 次：退避期间无消息，结束后档位到位，提升结束时刻不变；提升结束时的 BUSY；提升中间 5 s 检查遇 BUSY（pkgD2 重放） |
| `testLaunchScene` | 工作负载模型（10 ms 空闲 tick、窗口抖动）上走 `controlWindowCpus`：模型里迟到帧 3/121 变 0/121。这个数字只说明时序走通，**不代表真机收益** |
| 随机测试扩展 | `testProperties`（1278 次提升，每次都来自门控内启动或驱动请求）、`testScreenProperties`、`testExtremes`、`testRetryRandom`（55 次提升穿插在 237 个故障里） |

**既有用例的调整（都是默认值变化带来的）：**

- 启动日志期望从 `for 100 ms` 改 `for 250 ms`，末尾加 launch 字段。
- `testGovernor` 的触摸时间线从 140 ms 回落改为 300 ms 回落。
- `testHeat` 在紧急状态前加 8 个窗口，并顺带断言触摸保持不解除温控上限。
- `testProperties` 的安静窗口从 12 个加到 18 个，判定条件改为两个保持都结束 4 个窗口以上。
- 没有放宽任何 BUSY 重试相关的断言。

---

## 6. 真机未验证，上机怎么判读

**以下都没有上机：** 升频效果、发热、`nextpidversion` 在真机的行为、完整内核链接。`nextpidversion` 能否链接是根据上一版内核的符号表判断的，主线跑整核时才算实证。

**确认在工作（`U` 记录）：**

```text
cpufreq: governor floor 960/1689 MHz, touch 2227/2841 MHz for 250 ms, GPU frames 1785/2649 MHz, heat caps 2745/3072 MHz, launch 2918/3513 MHz for 1500 ms
cpufreq: launch 3: a process start 180 ms after a touch, 2918/3513 MHz for 1500 ms
```

第二行每 5 s 至多一条，编号跳变说明中间有没记的。里面的毫秒数精度是 20 ms 窗口，含义是**最后一个触摸帧到第一个任意进程启动的延迟**，只记门控内、提升开始时的值，不一定是用户点的那个应用的进程。这个数我们以前没有。

| 看到 | 结论 |
| --- | --- |
| 打开 App 时有 `S ... exec process signature enter`，附近有 `cpufreq: launch` | 正常工作 |
| 有 exec，附近没有 `cpufreq: launch`，5 s 限频也不能解释 | 门控没命中。看同一次操作的时间，考虑加大 `kLaunchTouchMs` |
| `launch` 行的毫秒数常接近 1000 | 门控偏紧，同上 |
| `launch` 编号增长明显快于实际打开次数 | 误触发多，看是否发热，再考虑收紧门控 |
| 启动窗口 `U cpufreq: d0 at lv.., asked lv12` | 固件压档，提升拿不到 |
| `hot` 增长明显快于以前 | 提升带来发热，缩短 `launch-ms` |
| 紧急状态（95 °C）期间出现 `cpufreq: launch` | 不该出现 |
| 熄屏期间出现 `cpufreq: launch` | 不该出现，先查 `pagani_screen_state` 有没有发布 |
| `off:` 或 `err` 持续增长 | SCMI 有问题，先看 `retry` 行，和提升无关的可能性大 |

**单变量对照：** `app-launch-jitter.md` 第 10 节的步骤，同一镜像分别加 `pagani-cpufreq-launch-ms=0`、`pagani-cpufreq-touch-ms=100` 跑一轮，看启动后 0–4 s 的 F 行 `p99`、`max`。注意命令行只剩 15 字节（986 去掉 `pagani-audio-mx=1` 再加 `pagani-kbtrace=1` 是 985），这两个参数连空格分别要 27、28 字节，对照时要临时去掉别的。

**键盘：** 这次改动不解决 kbd 自身的计算量。`merge-plan.md` a.5 已指出“反馈慢”可能在触感路径，请用户先做关闭键盘反馈的对照。

---

## 7. 风险和回退

| 风险 | 程度 | 回退 |
| --- | --- | --- |
| 更热 | 低到中，**没有真机数据** | 单次有时限，受热上限保留，95 °C 紧急状态和熄屏不提升。但门控命中率没量过：日志里进程启动覆盖 7–9%，有人操作的轮次到 17–28%，这些里有多少落在触摸后 1 s 内不知道。普通受热限时下限仍抬到上限。看 `app` 和 `hot`。回退 `pagani-cpufreq-launch-ms=0` |
| 触摸保持变长的功耗 | 低 | `pagani-cpufreq-touch-ms=100` 回到旧值 |
| 守护进程在触摸后启动造成误触发 | 低 | 只多一次有时限的下限 |
| 门控错过慢启动 | 低 | 只是没有提升，等于旧行为 |
| 链接失败 | 很低 | 符号表确认存在，真失败时主线整核会直接报错 |

---

## 8. 给其他路和主线的话

- **adreno830 那一路：** 不需要改。`pagani_cpufreq_boost()` 已备好，将来日志证明有用时，可在 `native_adreno_new_user_client` 加一处弱调用；现在不建议加，理由见第 2 节。`app-launch-jitter.md` 建议的启动时 GPU 下限（734 MHz）这次没做，它属于 `A830Dcvs`，不在本任务范围。
- **主线整核：** `libArmCpu.a` 新增一个对 XNU 符号 `_nextpidversion` 的引用，新增导出 `_pagani_cpufreq_boost`。
- **`Adreno830/docs/ARCHITECTURE.md` 第 1030 行** 写“触摸提升期间除外”，现在准确说法是“最后一个触摸帧后 100 ms 内除外”。那是另一路的文件，这次没改。
- **没做的：** 触摸事件立刻唤醒调速线程。现在触摸最晚 20 ms 后才生效，想再缩短要让 `PaganiInput` 主动通知调速线程，等有延迟数据再说。
