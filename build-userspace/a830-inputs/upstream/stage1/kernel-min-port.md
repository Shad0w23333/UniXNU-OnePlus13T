# 第 1 阶段：内核小优化与计量（Adreno830）

日期 2026-10-31。只改了 `Adreno830` 工作树（源码、主机测试、文档）。

全程没有上机，没有运行 adb、fastboot，没有跑整核 `./unixnu kernel`，没有碰 `UniXNU/out`，没有提交。Metal、Turnip、翻译层、`NativeAdrenoABI.h`、着色器包都没有动（ABI 头 SHA-256 仍是 `20ac9298…`，与 git 无差异）。

**这里的所有收益都是预期，没有一项在 13T 上测过。** 主机结果只说明逻辑对、能编译。

---

## 1. 结论

六项改动落到了源码，每项一个开关，集中在一个新参数 `pagani-gpu-kopt`（默认 63，全开，**命令行不用加任何东西**）。

| 位 | 行为 | 来源 | 目标 |
| --- | --- | --- | --- |
| 0（1） | 池 BO 放开 Memory 锁之后再清零 | 上游 `4c75ccb` 的思路，试验版 | 拆跨进程串联 |
| 1（2） | 池外 BO 只在 64–128 KiB 试连续内存，失败后 5 s 内同尺寸及更大的不再试 | 上游 `01ed692` | 防全系统页分配卡顿 |
| 2（4） | 表事务不重复 preflight 的别名检查 | 上游 `a087fca` | 缩短 VM 锁，提交在等它 |
| 3（8） | `NativeAdrenoMaintainOwnedPages` 每 64 页放开一次抢占 | 上游 `2c3df1e` 的一部分 | 缩短不可抢占窗口 |
| 4（16） | 调用计量：每类调用最长一次、每把锁最长等待 | 上游 `59171b2` 的思路，精简 | 给第 3 阶段定方向 |
| 5（32） | VM 时钟：发布时接 `native_adreno_vm_now_us` | 自写 | 修“VM 耗时计数恒 0” |

`finishPooled` 失败路径的清理不算新行为，是修试验版的漏洞，没有开关。

数字全部进现有 `gpu meter` 行，没有新的每秒行，热路径不打日志、不排版，遵守 `Adreno830/AGENTS.md`。

---

## 2. 参数与默认开的理由

`pagani-gpu-kopt=<位>`：默认 63。`0` 是这批改动之前的行为，计量行逐字节相同。超出 0–63 时 S0 停在步骤 0x49，原因码 1，reg 是旋钮号 30，expect 是 63。只有上机对比才需要写。

这是 `Adreno830Board::flags` 放得下的最后一个旋钮（位 31 是池标志），再加要换字段，`Adreno830Board.h` 里加了 `static_assert` 挡住。所以六项合用一个位掩码，不是六个参数，也省命令行。

| 位 | 默认开的理由 | 代价 |
| --- | --- | --- |
| 0 | 页只属于这个还没发布的对象，语义不变；没清零的 BO 过不了 `holdForDevice`，到不了 GPU；失败时页还回池 | 无。连接锁仍持有，所以只解开跨进程串联，渲染线程自己的 ±64 MiB 等待不变（merge-plan a.1 第 3 条） |
| 1 | 只影响池外回退，日志里 `sc` 从未出现，平时不走；走到时一次失败的搜索在页队列锁和空闲页锁下扫全部空闲页（`vm_page_find_contiguous`），拖慢全系统 | 放不进池的大 BO 一律散页，每页一个跨度，靠 `vmr` 观察。5 s 和 128 KiB 是上游 A740 的数，不是我们的 |
| 2 | 核过 kit：`alias_class`、`conflicting_alias` 与 preflight 的类别和重叠判定一致，`Exclusive` 只跳过与现有跨度的比较，其他检查都在。主机测试证明两种设置下的拒绝都来自 preflight（VM 不中毒），第二遍确实不跑了 | 无。命中率未知：只有存在第二种缓存类别的导入才走到 |
| 3 | 每段自带 pin、CTR、两个 `dsb sy`，段尾 DSB 在发出这一段的核上完成，段之间被抢占或迁移没有影响。物理页版本本来就是 64 页一段 | 开机写合并 1024 MiB 池变成 4096 段，多约 8000 个 DSB，耗时没量 |
| 4 | 不改行为，是第 1 阶段要的数据。五类调用各读两次时钟；锁先 `IOLockTryLock`，拿到就不读时钟，没有争用时零开销 | 每次调用约两次时钟读。`status=1` 时算了也不写 |
| 5 | 不改行为，只让已有计数有数 | 每次映射或解映射在 VM 锁内多 8 次时钟读，相对几百微秒的事务可以忽略（推测，没量） |

---

## 3. 比试验版 `a830-min-port.diff` 多做和改掉的

1. **退避按时间。** 用连接的时钟（`dispatch` 的 `clock`，真机是 `g.clock`，微秒），5 s。试验版按分配次数。
2. **退避状态改成原子。** 试验版是普通 `static`，但我们拆了锁，池外分配之间没有共用的锁，上游能用普通变量是因为有全局锁。现在是 `native_adreno_contig_backoff[2]`，两个字之间的竞争最多多搜或少搜一次。
3. **`finishPooled` 失败路径。** `allocate(..., pooled=true)` 失败时 `release()` 把页还回池，答失败。`allocBoInner` 走已有的失败分支，在 Memory 锁下归还，槽位回到 FIFO。失败时不清 `unzeroed_`，所以这个对象之后也拿不到设备引用。
4. **加了闸门。** `holdForDevice` 在 `unzeroed_` 时拒绝。试验版里忘了调用 `finishPooled` 的 BO 会把上一个属主的内容经 GPU 交给下一个进程，这是信息泄漏，不只是显示问题。**闸门只挡 GPU 映射**：客户端的 CPU 视图（`cpuMap`、`memoryForToken`、`clientMemoryForType`）不看 `unzeroed_`，防的是 `allocBoInner` 清零成功后才 `publish`，客户端在这之前拿不到句柄。闸门没有加到 CPU 路径：那几处走不到未清零对象，加了也测不到，是一段死代码。
5. **不换 ABI 头。** 试验版换了上游头，这里没有，理由见 merge-plan a.2 第 3 条。
6. **`failed_bytes` 没有写成连接成员。** 空闲内存是所有连接的，上游也是全局。

调用计时放在发布外壳（`runCore`、`importBo`），按调用者看到的算，连接锁等待、环满睡眠、BoFree 拆 CPU 视图都算在内。一开始放在 `dispatch` 里便于主机测试，后来改了：抖动要看的是调用者被卡了多久，`dispatch` 看不到环满的 200 ms 睡眠和 `dropViews`。

---

## 4. `gpu meter` 行的新字段

放在 `pf` 和 `ap` 之间，每段全 0 时不写。时间是整毫秒向上取，所以小于 1 ms 也显示 1。窗口是一分钟，由 `adreno830_status_clients` 每秒并入一次。

```
... pf 0/0/0/0 ca 12/5/2/1/1 lk 3/0/7/1 im 122/3/6 vt 39/21 zf 411/62 cs 2/1/4 ap1
```

| 段 | 含义 | 看到什么就做什么 |
| --- | --- | --- |
| `ca <BoAlloc>/<BoMap>/<BoFree>/<BoImport>/<Submit>` | 窗口里每类调用最长一次 | 总览。超过一帧（120 Hz 约 8 ms）的调用是掉帧的直接嫌疑 |
| `lk <连接>/<Ring>/<Memory>/<VM>` | 每把锁最长一次等待 | 连接大：做等围栏免锁。Memory 大：导入 `prepare`/`complete` 出锁。VM 大：`mapLocked` 的维护出锁（merge-plan 阶段 3） |
| `im <导入映射>/<走别名检查的>/<其毫秒合计>` | 非独占导入的比例和代价 | 第二个数恒 0 说明位 2 没有收益。第一个数除以 60 就是 IOSurface 重新导入速率 |
| `vt <映射毫秒>/<解映射毫秒>` | 窗口里 VM 表事务合计 | 约等于 VM 锁占用 |
| `zf <MiB>/<毫秒>` | 池 BO 清零量和时间 | 相除就是写合并清零速度，这个数一直是推测（adreno740-delta 第 9 节第 6 项） |
| `cs <尝试>/<失败>/<池外 BO 数>` | 连续内存尝试，以及这一分钟落到池外的全部 BO | 没有这一段说明回退路径没走到。第三个数远大于第一个，就是大于 128 KiB 的 BO 在走散页，这是位 1 的代价（审查发现初版只数 64–128 KiB 的尝试，这种情况完全看不到，已加第三个数） |

注意：

- 这些数字只在 `pagani-gpu-status=2/3` 写，完整包是 3，够用。
- 只有最大值，不知道是哪个进程。要定位进程得再加东西，第 1 阶段不做。
- `ca` 含等待，`lk` 是其中的等待，两者不要相加。
- `lk` 的 VM 等待和 `vt` 需要位 5。

**长度：** 典型一行 193 个字符，一条记录放得下。最宽 303 个字符，超过 200 的部分 BootProgress 放进 `+` 记录，`ramoops_format.py` 第 1080 行起会接回去。`Adreno830StatusMeterBytes` 从 200 提到 320。

**事件环：** 活跃时每条约 227 字节（之前 172），单靠计量行写满 32 KiB 从约 190 分钟缩短到约 144 分钟。90 分钟一轮约 20 KiB（之前约 15.5 KiB）。复位前最后的计量行总在，但开机一次性的 U 行在长测试里更容易被冲掉。实际日志里一轮十分钟的 U 行就有约 14 KiB，这个环本来就紧，所以多出的这点影响不大，但要知道。

---

## 5. 文件

新增：

- `Adreno830/NativeAdrenoKopt.h`：六个位和全局量。
- `Adreno830/NativeAdrenoMeter.h`：计量的全部逻辑，头文件内联，所以能在主机上测。
- `Adreno830/tests/test_meter.cpp`：新主机测试。

修改：

- `Adreno830/NativeAdrenoMemory.h`、`.cpp`：`finishPooled`、`unzeroed_`、`setKopt`、`NativeAdrenoMaintainRuns`，计量全局量的定义（放这里是因为不想多一个内核对象，51 个不变）。
- `Adreno830/NativeAdrenoCpu.cpp`：分段。
- `Adreno830/NativeAdrenoVm.cpp`：别名只查一次，VM 锁等待进计量。
- `Adreno830/NativeAdrenoUserClient.h`、`.cpp`：清零出锁、连续内存限制和退避。`allocBo` 多一个可选 `clock` 参数。
- `Adreno830/NativeAdrenoPublish.cpp`：`lockMetered`、调用计时、接 VM 时钟、每秒取数。
- `Adreno830/Adreno830Status.h`、`.cpp`：`Adreno830StatusCalls`、计量窗口、行排版。
- `Adreno830/Adreno830Board.h`、`Adreno830/Adreno830Start.cpp`：新旋钮，S0 检查并存值。
- 测试：`tests/test_memory.cpp`、`test_vm.cpp`、`test_userclient.cpp`、`test_status.cpp`、`test_start.cpp`、`test_a830headers.cpp`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`（3.6、5.4、8.8）、`docs/PROGRESS.md`。

没动 `tests/run-host-tests.py` 的内容，钉住的 PASS 行都没变，新结果走各自的新 PASS 行。

---

## 6. 主机结果

**全部在电脑上跑，不是硬件时序。**

命令：`cd Adreno830 && python3 tests/run-host-tests.py`

| | 改动前 | 改动后 |
| --- | --- | --- |
| 主机测试 | 44 通过，0 失败，1 跳过 | 45 通过，0 失败，1 跳过（多出的 1 个是新的 `test_meter`） |
| 内核对象 | 51 个，零告警 | 51 个，零告警（有告警构建就失败） |
| 符号检查 | 30 必需、23 弱定义 | 30 必需、23 弱定义 |
| 新未定义符号 | — | 只有 `_lck_mtx_try_lock`（XNU 自带，静态链接时解析，主线整核要确认） |

跳过的是 `test_sha256` 的固件项，改动前后一样。构建树是 `UniXNU/out-gpu/.../tree`，`UniXNU/out` 没碰。

另外在草稿目录用内核配方单独编了 `PaganiGpuBoard.cpp` 的副本，改过的 `Adreno830Board.h` 能编过。

新用例：

| 测试 | 证明什么 |
| --- | --- |
| `test_memory` | `finishPooled` 只清一次；未清零时 `holdForDevice` 拒绝；失败后页全部回池、对象可重用；写合并池只有屏障没有清理；4 线程共 16000 个 BO 在锁外清零，互不碰对方的页；分段循环 11 项（段数、每段 pin 和两个屏障、覆盖整段恰好一次、行大小超页时解 pin 拒绝） |
| `test_userclient` | 14 块：64–128 KiB 才试；失败后 5 s 精确到微秒；更小尺寸仍试并降门槛；跨连接生效；`contiguous=1` 不受限且不算尝试；无时钟不记；关掉后行为同以前；池外 BO 每个都计数。用“解锁那一刻看页内容”证明清零在锁外，关掉后在锁内。失败路径两种设置下都还回池 |
| `test_vm` | 用时钟钩子看 scratch 被谁改过，证明位 2 开时只有 preflight 排序，关时表事务再排一遍；两种设置下别名拒绝都不中毒；VM 时钟有无时计数行为；线程握手下 VM 锁等待进计量（确定性，不靠 sleep） |
| `test_meter` | 哪些方法计时；计时器作用域；关掉或无时钟时不读时钟；无争用不读时钟；8 线程取最大；取走一次；VM 计数取差值 |
| `test_status` | 计量行逐字节：折叠一次、取最大、相加、整毫秒向上取、全 0 不写、只有池外 BO 时也写 `cs`、最宽 303、复位忘掉窗口；无调用时行不变 |
| `test_start` | 缺省全开、S0 原样存值、64 被拒 |

变异测试（草稿目录副本，逐处改坏再跑对应测试）：27 处全部被发现，包括退避差 1 µs、别名开关两个方向、失败不归还池、闸门、分段少一页、窗口折叠两次、S0 不存值。其中“失败不记退避”实际是 `-Wunused-function` 编译失败，换成能编译的等价变异后 `test_userclient` 同样抓到。审查另加 13 处，存活的是去掉 `request->contiguous==0`，已补用例。第三个数和这条用例又加 7 处，全部抓到。`NativeAdrenoPublish.cpp` 的接线主机上不运行，不在这些变异之内。

主机上的一个诊断（8192×8192 不同类别跨度，**不是硬件时序**）：表事务 780 µs（一遍）对 2124 µs（两遍），preflight 约 2 ms。

反汇编核对：`NativeAdrenoCpu.o` 里每段顺序是 `_disable_preemption`、`mrs CTR_EL0`、`dsb sy`、逐行维护、`dsb sy`、`_enable_preemption`，段长 `0x40000`，开关读 `native_adreno_kopt` 的位 3。

---

## 7. 真机观察

无。这批改动没有上过机。

---

## 8. 未验证项

1. **所有耗时收益都没测**，包括抖动是否减轻。
2. **整核链接和预链接没做**，留给主线。
3. **`NativeAdrenoPublish.cpp` 的接线只编译过，主机上不运行**：`runCore` 和 `importBo` 的计时、`lockMetered`、接 VM 时钟、`native_adreno_client_tick` 取数。逻辑都在 `NativeAdrenoMeter.h` 里测过，这几行本身没有。
4. **`NativeAdrenoCpu.cpp` 的汇编只编译过**，循环结构由主机模型测，真实缓存指令没跑。
5. **5 s、128 KiB、64 页都是上游 A740 上的数**。
6. **`pagani-gpu-kopt` 在真机解析没测**，走的是现有 `Adreno830ParseKnobs`，主机上覆盖。
7. **开机 S3 分段写合并 1024 MiB 池的耗时没量。** 上游 1.5 GiB 约 41 ms，是他的数。
8. **散页增加 VM 跨度的风险没量。** 回退路径走到时看 `cs` 第三个数。`sc` 不进日志，完整包的报告页还会盖住 MEM 行；`vmr` 只数带保留量的客户端。
9. **位 2 的命中率未知**，看 `im` 第二个数。

---

## 9. 给主线的交接

- **命令行不用加参数。** 需要回退某项时才写，例如 `pagani-gpu-kopt=47` 只关位 4，其余全开。`0` 全关。
- **整核时**确认 `_lck_mtx_try_lock` 能解析，确认 `UniXNU-Pagani_SM8750-gpu/package/PaganiGpuBoard.cpp` 随新头重编。
- **着色器包不受影响**：没碰 Turnip、Metal、ABI 头，用户态不包含这次改过的头。
- **上机看**：`gpu meter` 行有没有 `ca`、`lk`；`vt` 不再是 0（说明 VM 时钟接上了）；`cs` 三个数，第三个远大于第一个就是大 BO 在走散页；`GPU RUN` 的 `h` 和 `f` 不应变多。
- **小事**：`工具/analyze-a830-run.sh` 第 14 行 `cut -c1-260`，最宽的计量行会被截断，典型行不受影响。没改，它不在我的范围。
