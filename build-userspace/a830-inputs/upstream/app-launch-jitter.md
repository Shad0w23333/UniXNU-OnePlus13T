# 打开应用时的小抖动：定位与优化方案（2026-10-31）

范围：一加 13T（PKX110，A830，iPadOS 17.7.11 21H461，iPhone16,1 身份）。本报告只读代码和历史日志，没有上机，没有改任何源码树。

证据标记：**[读码]** 读代码确认；**[日志]** 真机日志实测；**[上游]** 上游在 sheng（A740）上的实测，不等于我们的情况；**[推测]** 尚未验证。

---

## 0. 结论摘要

1. 用户这次测的完整包（`629cd45d`）**没有日志**：`runs/20261031-134632-a830-full-1-1` 两区全丢。下面的结论都来自更早几轮，只能指方向，定论要等长按日志。
2. 启动窗口里的卡顿**不在 GPU 队列**。灵动岛包（`20261030-213341-island-apps-1`）12 个慢秒全部以 `gap` 为主，`copy` 最大 13.6 ms，同一时段 `q` 最长 14 ms、`s0`。这和上游 sheng 的结论（多数慢帧排在别的进程后面）不同，所以**先不用移植 CP 抢占**。完整包有了壁纸和小组件，这一条要用新日志复核。
3. 嫌疑集中在**合成器出帧之前的 CPU 侧**：`gap` 是 render server 没有交帧。
4. 最强的线索是 CPU 频率。D 包（调速器活着）打开设置只有 62.9 ms 和 42.4 ms 两次卡顿；D2 包（调速器 41.8 s 死掉，停在 1997/1018 MHz）打开快捷指令和时钟时连续几秒 FPS 掉到 4–64，最长 674 ms。这不是单变量对照，但差距很大。D2 的调速器崩溃代码已修，完整包是否生效要看新日志里有没有 `cpufreq: off:`。
5. 读码确认四个能直接动手的问题：
   - 调速器的触摸提频只保持到松手后 100 ms，正好在应用启动开始干活时结束。
   - IOSurface 导入表是弱引用，上游实测 Core Animation 每帧重建纹理，导致每帧重新导入；我们没有上游的“保持一秒”修复。
   - `keep_room` 在纹理创建调用里同步分配 64 MiB 块；日志里 backboardd 的 BO 反复 ±64 MiB，与启动窗口吻合。
   - 转译层的调度、完成线程要设的用户交互 QoS 只在 macOS 生效，iOS 上是空函数。
6. 还有一条需要实测判定：QuartzCore 的管线在渲染线程上同步创建，而 Data 是内存盘，缓存只靠种子里的包。“偶有”小抖动很像首次用到某个着色器变体。
7. 现在的日志看不清小抖动：`J` 阈值 50 ms，低于它的只能看 F 行 `p99`；`gap` 分不清“卡住”和“本来就没东西画”；`pagani-gpu-status=3` 下 DCVS/MEM/IRQ 行被报告行替换；插件的管线耗时表不进 ramoops。第 8 节列了最小探针。
8. 建议顺序：下一包先做 CPU 启动提升、保持 IOSurface 导入、QoS 补丁、探针（第 7 节 P0）；`keep_room` 后台化、锁拆分排第二批；UFS 和抢占要等日志说话。

---

## 1. 数据来源与局限

| 日志（`工具/gpu-test-loop/runs/`） | 调速器 | 状态行 | 能看什么 | 局限 |
| --- | --- | --- | --- | --- |
| `20261031-134632-a830-full-1-1`（完整包） | 不明 | status=3 | **什么都没有** | 定时 PSCI 复位，两区全丢（`summary.txt`） |
| `20261031-070114-pkgD-longpress-1` | 活着（`U cpufreq` 行持续到 117 s） | status=1 | F/J/H/S | 没有 DCVS/MEM/IRQ 行；开机后 100 s 内，后台很忙 |
| `20261031-070836-pkgD2-widgets-wallpaper-1` | **41.849 s 死掉**（`cpufreq: off: status -6 lvl get`） | status=1 | F/J/H/S | 同上；心跳 1986 MHz |
| `20261030-213341-island-apps-1` | 不明，心跳全是 1985/1986 MHz（17 条） | status=2 | F/J/H/S + DCVS/MEM/IRQ | CPU 频率低；MobileSMS 有崩溃重启 |
| `20261030-180230-bisect-A`、`20261030-144045-m8-full-1` | 活着 | status=2 | 全部 | 当时 GPU 被旧温控压频（`q` 高到 91–122 ms），**不代表现状** |

通用局限：

- D、D2、灵动岛都比完整包旧。完整包多了调速器重试、着色器包重做、`KeyboardInlineCompletion=false` 等。
- 测试都在开机后几分钟内，后台守护进程大量启动，比稳态差。
- `J`、`SLOW` 的 `gap` 是相邻两帧到达显示驱动的间隔（`FrameRate.h:24-25`：50 ms 以上、1000 ms 以下算抖动）。**屏幕本来没东西要画时，下一帧到来也算一次 gap。** 所以孤立的大 gap 不一定是可见卡顿，见第 8 节。
- 静止时的 `FPS 10 ... gap 100` 成片出现（D 包 120–153 s），PROGRESS 已判为周期动画，不算卡顿。

分析脚本在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/app-launch-jitter/`：`launch_corr.py`（按 exec 时刻对齐 F/J 行）、`jitter_q.py`（慢秒对应的队列延迟和内存跳变）、`irq_rates.py`（提交与等待速率）。

---

## 2. 上游分析要点

来源：`UniXNU-UniXNU` 提交 `359d77c0`（`docs/zh/porting/gpu.md` 第 96–113 行）、`36247a1b`。`origin/perf/gpu-smoothness` 是 10-26 的旧分支，已全部进入两个仓库的 main，没有独立内容；真正相关的提交在 `feat/ipados27-gpu-parity`。

上游把慢帧分成五段：提交到调度器、调度器到提交、提交到 GPU 开始、GPU 开始到结束、GPU 结束到回调。sheng 上 iPadOS 27 的结果 **[上游]**：

- 驱动锁等待都在 7 ms 以下，render pass 中位 0.5 ms。
- 多数慢帧排在别的进程后面：一个海报扩展的任务约 77 ms 一个，队列按序执行。
- 运行时把命令缓冲区送到回调的线程是默认 QoS，“最晚醒来 50 ms”（`bridge/patches/0023-quality-of-service-on-ios.patch`，说明在 `origin/feat/ipados27-gpu-parity` 的 `build-adreno-metal-bridge.py` 第 63–64 行）。
- 打开设置和 Safari 时，94–112 MiB 的内存块锁定要 281–340 ms。

**上游这些结论不能直接套到我们身上**，差异：

| 项 | 上游 sheng（当前） | 我们 |
| --- | --- | --- |
| BO 来源 | 系统内存，分配时才 wire（`882f4bf`） | 1 GiB 预留写合并池，分配不 wire（`NativeAdrenoMemory.cpp:170-194`） |
| 锁 | 一把全局设备锁 `g.lock`（`78e9343` 的 diff） | 每连接一把，加 Ring、Memory、VM 三把（`NativeAdrenoPublish.cpp:8-11`） |
| 内存语义 | 有备用块（`f42d44a`）、memoryless 不占内存（`159eb82`）、render server 帧缓冲保持 8 位（`11420f4`） | 这些提交都不在我们的基线 `ce5e19e` 里 |
| OS | 主要测 iPadOS 27 | iPadOS 17.7.11 |

基线：`Adreno830` 派生自 `5c8c145`，上游之后的 `8638683` 到 `10ac0ea` 都没有；`AdrenoMetal` 基线 `ce5e19e`，之后到 `a0a34fa` 的都没有。转译层钉在 `720ef1c`，补丁 18 个，和上游的 24 个不是一套，**不能整批打**。

---

## 3. 启动窗口实测

用 `exec process signature enter` 的时刻对齐前后的 F 行。时间相对 exec。

**D 包，调速器活着，设置（100.521 s）[日志]**

| 相对时间 | FPS | ft | p99 | max | 说明 |
| --- | --- | --- | --- | --- | --- |
| −0.69 | 120 | 8.3 | 12.7 | 15.7 | 基线 |
| +0.31 | 111 | 9.0 | 20.4 | **62.9** | `gap 61.4 copy 8.8`，100.704 s，exec 后 183 ms |
| +1.31 | 113 | 8.9 | 18.9 | 42.4 | |
| +2.31 | 117 | 8.5 | 11.8 | 12.1 | 恢复 |

**D2 包，调速器已死（1997/1018 MHz）[日志]**

| 应用 | 相对时间 | FPS | ft | p99 | max | 主要成分 |
| --- | --- | --- | --- | --- | --- | --- |
| 快捷指令 115.758 s | +1.09 | 29 | 29.0 | 364 | **468** | `gap 457 copy 12.7` |
| | +2.09 | 43 | 27.2 | 188 | 274 | `gap 268 copy 14.4` |
| | +3.09 | 64 | 15.5 | 80 | 96 | `gap 96 copy 7.1` |
| 时钟 139.758 s | +0.12 | 47 | 21.3 | 314 | 400 | `gap 400` |
| | +1.12 | 74 | 13.5 | 61 | 164 | `gap 159 copy 14.4` |
| | +2.12 | 34 | 23.8 | 160 | 190 | `gap 192` |
| | +3.12 | 4 | 229 | 658 | **674** | `gap 671` |

这是目前最强的线索，但不能当单变量对照：应用不同，都是开机后首次启动。

**灵动岛包，480.992 s 信息 [日志]**

| 时刻 | GPU | MEM | q | 帧 |
| --- | --- | --- | --- | --- |
| 479.9 | 900 MHz b51–61 | `used 487 imp 1982 backboardd:295+115` | 4–6/9–10 ms d3–5 s0 | |
| 480.6 | | | | `jitter 136ms gap 125 copy 13.6` |
| 480.9 | 900 MHz b51–62 | `used 552 imp 2017 backboardd:359+103` | 4–7/9–14 ms d3–5 s0 | |
| 482.9 | 660–832 MHz | `used 488 backboardd:295+102` | 5/9 ms d2 s0 | |

- 全程 12 个 `SLOW>=40ms` 的秒，12 个以 `gap` 为主，`copy` 最大 13.6 ms，邻近秒 `q` 最大 8–14 ms，全程 `s` 合计 0。
- backboardd 的自有 BO 在 470–486 s 反复 `295→359→295→359→295→360`，差值等于 `BLOCK_BYTES`，推断是池块（日志分不清是不是恰好同大小的独占块）。`used` 同步跳 64 MiB。
- 活动时约 230 次中断/s（与提交数接近）、600 次 `WaitFence`/s、其中 360 次超时/s（`IRQ` 行差分）。

`copy` 在启动窗口常到 12–14 ms。120 Hz 的预算是 8.3 ms，这会掉一帧，属于“小抖动”量级，但解释不了几十毫秒以上的 gap。

---

## 4. 帧路径现状（读码）

**出帧链路 [读码]**：CA 渲染线程 → `NativeBridgeMetalFrontend.mm` → metal2vulkan → Turnip Darwin 后端 → `Adreno830` → CP；完成后回调 → 交接线程 `serveScanout`（已是用户交互 QoS，`NativeMetalBridgePhase.h:95-96`）→ GPU 拷贝到扫描缓冲。

几个关键事实：

- 管线在 QuartzCore 渲染线程上同步创建（`NativeBridgeMetalFrontend.mm:1396` 的注释，int-153）。
- `commit` 时批次无依赖就在提交线程直接进 `VkQueue`（`scheduler/mod.rs` 第 232 行起的说明），所以调度线程只影响有依赖的批次，完成线程影响回调。
- `setGPUPriority:` 直接返回 NO（`NativeBridgeMetalFrontend.mm:1382-1386`），只有一条环。
- 准入控制：渲染服务器外的连接最多一个在途作业（`NativeAdrenoSubmit.cpp:41-48`）。

**内核锁的实际嵌套 [读码]**：

| 调用 | 连接锁 | Ring | Memory | VM | 里面的慢操作 |
| --- | --- | --- | --- | --- | --- |
| BoAlloc（池内） | 持 | | 持 | | `bzero` 整个 BO（`NativeAdrenoMemory.cpp:182`） |
| BoImport | 持（`Publish.cpp:782-788`） | | 持（`UserClient.cpp:351`） | | `prepare()` 锁页（`Memory.cpp:284`）、逐页取物理段 |
| BoMap | 持 | | | 持 | 写 PTE、`publishForDevice`（`Vm.cpp:448`）、`insert` 的 memmove（`Vm.cpp:381-387`） |
| BoFree | 持 | | 导入和池内 BO 持（`UserClient.cpp:435`） | unmap 时持 | TLB 失效、`complete()` 解锁页 |
| Submit | 持（`Publish.cpp:721`） | 持（`Submit.cpp:133`） | | 每个 buffer 短暂持（`:149`、`:178`、`:186`、`:223`） | |
| WaitFence 轮询 | 每次都持（`Publish.cpp:672`、`:684`） | 持 | | 回收时持 | |

所以有两条串联路径：

- **同进程**：backboardd 一个线程在 BoImport 或 BoMap，同连接的 Submit 和 fence 轮询都要等连接锁。
- **跨进程**：任一进程的 BoMap 持 VM 锁时，别的进程的 Submit 持着 Ring 锁等 VM 锁，于是所有提交都等。`NativeAdrenoSubmit.h:41-46` 写着 Ring 锁“held for microseconds”，但这建立在 VM 锁很短的前提上。

---

## 5. 候选原因总表

排序看证据强度和改动代价，不是确定的耗时占比。

| # | 候选 | 判断 | 证据 | 预期收益（推测） | 代价 |
| --- | --- | --- | --- | --- | --- |
| 1 | CPU 升频滞后、启动提升太短 | **主嫌疑** | D/D2 对比，读码 | 中到大 | 小，只改内核 |
| 2 | IOSurface 每帧重导入 | **主嫌疑** | 读码确认弱表；速率未测 | 中 | 小 |
| 3 | 64 MiB 块同步分配与震荡（BO 分配） | **主嫌疑** | 日志 + 读码 | 中，主要消单次尖峰 | 中 |
| 4 | 管线现场编译 | **主嫌疑，需判定** | 读码；耗时量级来自上游 | 对“首次”尖峰大 | 中 |
| 5 | 线程 QoS | 嫌疑 | 读码 + 上游实测 | 小到中 | 小，但要重做着色器包 |
| 6 | 驱动锁串联（含 fence 轮询持锁） | 嫌疑 | 读码，耗时未测 | 小到中 | 中 |
| 7 | 内存压力（free 页见底） | 嫌疑 | 日志，不够确定 | 未知 | 先探针 |
| 8 | UFS 单槽轮询（首读 App 与 dyld） | 嫌疑 | 读码，耗时未测 | 对首次启动可能大 | 大 |
| 9 | 页表更新与 TLB | 次要 | 读码 | 小 | 中 |
| 10 | 缓存维护 | 次要 | 读码 | 小 | 小 |
| 11 | GPU 排队、抢占 | 目前排除，完整包要复核 | 日志 | 现状下小 | 大 |
| 12 | GPU 升频滞后 | 次要 | 日志 + 读码 | 小 | 小 |
| 13 | CPU 核绑定 | 次要 | 读码 | 小 | 大 |
| 14 | backboardd、SpringBoard、render server 优先级 | 未验证 | 无 | 未知 | 先探针 |
| 15 | jetsam | 次要 | 日志 | 小 | |
| 16 | 页缓存 | 与 7、8 同源 | 读码 | 见 7、8 | |
| 17 | 显示节拍 | 低 | 读码 | 小 | |

已排除：

- **DDR 频率**：主机 DDR 票默认锁第 11 档，`ddr_floor` 跟随 `ddr_boot`（`Adreno830Board.h:156`、`:177`）。
- **温控**：启动窗口 58–72 °C，目标 95 °C。
- **池外连续内存搜索**（上游 `75e9b5f` 的问题）：709 条 MEM 行没有一条出现 `sc`。但这只代表当时没有发生，池用满时这条路还在（`UserClient.cpp:295-302`）。
- **GPU 睡眠唤醒**：完整包启动参数没有开 slumber、IFPC（`pagani-gpu-slumber`、`pagani-gpu-ifpc` 默认 0）。
- **CPU 深度空闲**：完整包没有 `pagani-idle`，默认不挂钩子（`PaganiArmCpu/README.md:114`）。

---

## 6. 各候选详述

### 6.1 CPU 升频滞后

**机制 [读码]**

- 调速器每 20 ms 采样一次（`ArmCpu.cpp:423`），需求是每核最近 160 ms 的平均（`CpuFreqGovernor.h:69`、`:275-282`）。
- 触摸只到 2227/2841 MHz，松手后保持 100 ms（`:77`、`:87`、`:397`）。
- GPU 出帧时保底 1785/2649 MHz（`:239`、`:398`）。
- 冲顶要**同一颗核**连续 3 个窗口饱和（`:74`、`:286`）。线程在 8 颗核之间迁移时，每颗核的饱和计数会断。XNU 的空闲时间以 10 ms 为单位，20 ms 窗口只能读出 0、50、100%（`:10-15`）。
- 应用启动的工作在松手之后才开始，100 ms 的提升基本覆盖不到。启动 zoom 动画约几百毫秒（常识，非本机实测）。

历史：10-30 15:00 用户要求“触摸保持最高档到松手后 1 s”；17:15 `cpu-gov` 为了发热和功耗改成现在这样（PROGRESS 第 391 行）。上游 sheng 的做法是触摸把 P 簇和 GPU 保持在顶档 1000 ms（`docs/devices/sheng.md:552`）。

**证据**：第 3 节的 D/D2 对比 **[日志]**。D 包的 U 行还显示，负载高时固件会把请求压下去：`d0 at lv12 2918MHz, asked lv15 3532MHz`、`d1 at lv13 4089MHz, asked lv15 4320MHz`，所以顶档不是随时拿得到。

**改法**：

1. 加一个“启动提升”，不要把所有触摸都拉满，以免回到 10-30 的发热问题。触发条件用 exec：`UniXNU/patches/xnu/zzz-boot-progress.patch` 第 235 行的 `boot_progress_process_stage` 已经在 exec 路径上，判断可执行文件在 `.app` 目录下时，写一个时间戳给 `pagani-cpufreq` 线程和 DCVS 线程。
2. 触发后 800–1200 ms 内：两簇至少到较高档（建议先试 d0 2918、d1 3513），GPU 至少到 734 MHz。
3. exec 比用户松手晚（设置那次 62.9 ms 的尖峰在 exec 后 183 ms），所以再把 `kTouchMs` 从 100 提到 200–300 ms，覆盖松手到 exec 之间。
4. 把这些做成启动参数（如 `pagani-cpufreq-launch-ms`、`pagani-cpufreq-touch-ms`），不重编就能 A/B。

**风险**：发热和功耗增加；温控上限（`g->capped`，`:412`）仍要优先，不要绕过。

### 6.2 IOSurface 导入

**机制 [读码]**：`surfaceBuffer` 用 `strongToWeakObjectsMapTable`（`NativeBridgeMetalFrontend.mm:407-437`），只有纹理或在途命令还拿着时才能复用。上游 `0f11e64` 的说明 **[上游]**：Core Animation 每帧为每个窗口表面建一次纹理，帧结束就丢，弱表保不住，每帧都重新分配、映射、释放；修法是保持最近 1 秒用过的导入，最多 64 个。

**每次重导入的代价 [读码]**：连接锁 + Memory 锁下 `prepare()` 锁页；VM 锁下写 PTE；释放时超过 512 页（2 MiB）用 `TLBIASID` 清掉 GPU 这个 ASID 的全部 TLB（`NativeAdrenoVm.cpp:582-583`），全屏表面（1216×2640×4）约 3100 页，每次都走这条。启动时屏幕上的表面变多，这个开销随之放大。

**未知**：我们这边实际的重导入速率没有测过。`imp` 是存活对象数，不是速率。`NativeBridgeMetalFrontend.mm:2130` 有 `surface made/released` 计数，但不进日志。

**改法**：只移植 `0f11e64` 里 `holdImport` 那约 15 行（MIT），不要带上其余改动。

**风险**：

- 多占 1 秒 wired 页。上游已限 64 个。
- 表面被回收重用时的身份判断：键含 ID、地址、大小，与现有键相同。
- 改插件后 CDHash 变，要重做信任包；不改运行时，`NMB_RUNTIME_ID` 不变（`build-adreno-metal-bridge.py:89`）。

### 6.3 BO 分配：64 MiB 块

**机制 [读码]**：

- 块大小 64 MiB（`memory.rs:53`），render server 的 1.5 GiB 窗口下保持这个值（0018 补丁）。
- `keep_room` 在纹理、缓冲、heap、管线创建时都会跑（`texture.rs:1413`、`buffer.rs:988`、`pipeline/render.rs:1500` 等），缺口就**同步** `vkAllocateMemory` 一个 64 MiB 块（`memory.rs:1453-1482`），也就是在 CA 渲染线程上。
- 每个池只保留一个空块，第二个空块立即释放（`memory.rs:397-416`），所以边界附近会反复分配释放。
- 内核里这 64 MiB 在**持 Memory 锁时**经写合并映射 `bzero`（`NativeAdrenoMemory.cpp:182`、`UserClient.cpp:286-288`），这段时间所有进程的 BoAlloc、BoImport、BoFree 都等。

**证据 [日志]**：灵动岛包 470–486 s 五次 ±64 MiB 跳变。480.6 s 的 136 ms 卡顿落在 `used 487→552` 的那一秒内（只能说时间吻合，MEM 行一秒一条，定位不到毫秒）。

**和上游的差别**：上游的 281–340 ms 主要是**锁页**，我们从预留池分配不锁页，所以量级应该小很多。单次耗时没有测过，**估计 10–40 ms（推测）**，够造成一两帧掉帧。

**改法**，由简到繁：

1. 内核：`takePooled` 先在 Memory 锁内划出区间，放锁后再 `bzero`。小改动，消除跨进程串联。
2. 转译层：`release` 多留一个空块（迟滞），或空块保留 10 s，抑制震荡。几行。
3. 转译层：移植 `0021` 的 room 线程和备用块。`0021` 依赖我们没有的 `0013-growing-memory-blocks`，要手工改写。

**风险**：第 2、3 项改运行时，要重做着色器包；空块多占 1 GiB 池的空间，`used` 峰值曾到 831 MiB，要防止池满后落到慢路径。第 3 项在 room 线程里分配要注意和 `Memory` 互斥。

### 6.4 着色器编译

**机制 [读码]**：管线同步创建在渲染线程上。Data 是内存盘，每次开机缓存都是种子里的内容。种子包 `0c5839b7`（166 文件）由 Mac 上重放生成，主机校验时 54 条管线命中 35（PROGRESS 第 473 行）。**包里没有来自本机实际运行的管线日志**：`make-shader-cache-a830.py --journal` 是可选项，我们没有把 `pipelines.log` 从手机取回的通道（`boot_log_export_put` 是弱空实现，`NativeAdrenoPublish.cpp:93-96`）。开机预编译已关（13T 上写 720 MiB、抢 CPU，PROGRESS 第 378 行）。

**耗时量级 [上游]**：冷启动 77 个管线约 43 s，103 个 Metal 管线共 40.1 s、单个最长 1.40 s（`docs/zh/drivers/adreno740.md`）。Oryon 单核更快，单个未命中估计几十到几百毫秒（推测），这和启动窗口的 gap 量级相符。

**这条最容易判定**：同一个应用连开几次，第一次抖、后面不抖，就主要是冷缓存（着色器、页缓存、UFS）；每次都抖就是分配和 CPU。见第 10 节。

**改法**：

1. 先做探针，拿到本机实际未命中的管线列表（第 8 节）。
2. 用这份列表扩充种子包。
3. 整库预编译（上游 `feat/prime-shaders`）在 13T 上已判有害，不要重新打开；如要用，只针对列表里的几十条，且在开机后后台低优先级跑。

**风险**：改运行时后包就失效，顺序要放在所有运行时改动之后；上游 `0023` 也会改运行时。

### 6.5 线程 QoS（含 CPU 核绑定的一部分）

**机制 [读码]**：`run_at_user_interactive` 只有 macOS 和 Linux 版，iOS 走空函数（`exit_guard.rs:783-795`、`:903-905`）。受影响的线程：`metal2vulkan-scheduler`（`scheduler/mod.rs:220`）、`metal2vulkan-completion` 和 `queue-wait`（`command_buffer/completion.rs:140`、`:148`）、`metal2vulkan-handlers`（`contexts/mod.rs:194`）。`at_least_user_initiated` 在 iOS 上恒真（`exit_guard.rs:924-928`），所以编译也不会提到 user-initiated。

**未知**：这些线程的实际 QoS。上游说是默认类；我们没读过。pthread 创建时可能继承创建者的类（推测）。

**为什么和启动有关**：启动时大量用户交互、用户发起类线程在抢 CPU，默认类的回调线程排在后面。回调晚了，CA 的缓冲回收和下一帧排布也晚。不过批次无依赖时提交走调用线程，所以影响主要在回调侧。

**改法**：按 `0023` 改 `#[cfg]`（我们的行号不同，手工改，内容 4 处）。

**风险**：改运行时要重做着色器包。转译层是 `GPL-2.0-or-later OR LGPL-3.0-or-later`，补丁本身要沿用这个许可证，不能当 MIT。

### 6.6 驱动锁与 fence 轮询

**机制 [读码]**：见第 4 节。`WaitFence` 每次进出都要拿连接锁和 Ring 锁，活动时约 600 次/s，其中约 60% 超时返回（`IRQ` 行差分 **[日志]**）。上游 `17171f4` 让未完成时不拿锁读 fence。

**证据**：上游修之前单次轮询等锁最长 46 ms，那是全局锁 **[上游]**。我们是分锁，量级应该小，但**没有测过**。

**改法**：

1. BoImport：连接锁和 Memory 锁都放开后再 `prepare()`，只在登记时拿锁。`importTask` 目前把 `prepare` 和状态写在一起，要拆开。
2. 导入的 `complete()` 放到锁外（思路同上游 `a18a4fa`）。
3. WaitFence：`A830Fence::passed()` 本来就是无锁的，先无锁判断未完成再决定要不要进 core（思路同 `17171f4`）。
4. 把 `Submit` 里 `describe`、`holdJob`、`cleanForSubmission`、`noteUse` 的多次 VM 调用合并成一次持锁，减少 Ring 持锁时间。

**风险**：导入路径的生命周期复杂（int-233..235 的卡死就在这里），第 1、2 项必须带主机测试。

### 6.7 内存压力

**证据 [日志]**：D 包心跳 `free` 多次掉到很低（50 s 1289、70 s 1433、140 s **475**），同期 `avail` 45–80 万页（约 1.8–3 GiB）。`free` 是 `vm_page_free_count`，`avail` 是 `memorystatus_available_pages`（`BootProgress.c:1199`）。

**含义 [推测]**：可用页很多，但空闲链见底，缺页时要等 pageout 线程腾页，任何线程都可能在缺页里停一下，包括渲染线程。心跳 10 s 一次，无法和卡顿对齐。Data 内存盘读出的页还会在 UBC 里再存一份。

**改法**：先探针（`vm_page_wait` 次数、压缩与 pageout 次数）。如果确认，再考虑调 `vm_page_free_target` 或提前回收。

### 6.8 UFS 首读（含页缓存）

**机制 [读码]**：

- 根卷读是**单个 UTP 槽 + 轮询 + 中断屏蔽**（`NativeUfs.cpp:822`）。
- 在调用线程上同步完成，持窗口锁，读、校验、拷贝都在锁内（`RootWindowRead.h`、`NativeUfs.cpp:1555`），单次最多 128 KiB（`RootTransportLayout.h:7`）。
- 每个 4 KiB 块都走完整性校验（`RootWindowRead.h` 的 `ops.verify`、`vr_verify_block`）。这条分支确实编进了内核：`out-gpu/.../kernel.macho` 含 `read block integrity` 字符串；构建开关见 `build-native-ufs.py:34`，且带 `-mgeneral-regs-only`，校验走通用寄存器（哈希实现的速度未核对）。
- 所以根卷 IO 全系统串行。应用缺页读 dyld 共享缓存时，backboardd 或 SpringBoard 碰到冷页也要排队。

系统应用在 Data 内存盘（`staged_system_apps`），读得快；共享缓存和框架在根卷，走 UFS。

**未知**：耗时。`native_ufs_read_ticks`、`native_ufs_verify_ticks` 这两个计数已有，但没有进日志（`NativeUfs.cpp:1398`）。

**改法**：先把两个计数加进 F 行或心跳。如果校验占比高，优先考虑硬件 SHA 或缩短锁范围；中断加多槽是大工程，排后。短期可在开机后空闲时预读常用框架段（注意别和开机预编译一样伤 CPU）。

### 6.9 页表更新、TLB 维护

**机制 [读码]**：

- `insert`、`erase` 对整张 span 表 memmove（`NativeAdrenoVm.cpp:381-395`），导入的散页每页一个 span。上游 `92aa70f` 改成按映射索引，理由是 BoMap 持锁最长 70 ms **[上游]**，但那是系统内存 BO，每页一个 span；我们自有 BO 来自池，一个 span，散页只有导入。
- `exclusivePages` 对每个导入遍历全部映射（`:345-352`），池内 BO 跳过，所以多数情况快。
- 解除映射超过 512 页走 ASID 失效，之后 GPU 要重新走页表（代价在 GPU 侧，未测）。
- GPU SMMU 全局空间由 Gunyah 仿真（`ARCHITECTURE.md:556`），上下文银行的写是否陷入**不清楚**，这决定逐页 `TLBIVAL` 的代价。

**死掉的计数**：`native_adreno_vm_phase_us`、`native_adreno_vm_kind_us` 都在，但 `native_adreno_vm_now_us` 除了测试外没有人赋值，所以真机上恒为 0 **[读码]**。

**判断**：次要。第 6.2 修好后调用次数会大减。

### 6.10 缓存维护

**[读码]** MEM 行 `coh 1`：GPU 只读的导入走一致映射，映射和提交时跳过维护（`NativeAdrenoVm.cpp:446`、`:416`）。GPU 写的导入在 `mapLocked` 里按物理页 clean+invalidate（`:448`，持 VM 锁），约 64 页一段关抢占（`NativeAdrenoCpu.cpp:80-101`）。写合并 BO 不用维护。

**判断**：CA 合成主要读窗口表面，影响小。GPU 写的导入才走这条，它同样随 6.2 的重导入减少而减少。

### 6.11 GPU 排队与抢占

**证据 [日志]**：第 3 节。旧的高 `q`（91–122 ms）出现在旧温控压频时，不代表现状。

**但完整包要复核**：完整包有默认壁纸和小组件后，海报和小组件进程可能成为 GPU 客户端，就是上游那种情况。`gpu meter` 行的 `fw`、`n4` 能回答。按 `ARCHITECTURE` 的门槛：同一秒以 `off+copy` 为主且 `n4>0` 的慢帧占 20% 以上，或用户指出掉帧的那几秒 `fw` 最大值超过 4 ms，才启动抢占。

上游 `b895643`、`7b6245f` 是 A740 寄存器和固件，A830 记录大小不同（每环 13536 KiB，`ARCHITECTURE.md:1042`），不是移植而是重写。**不建议现在做。**

### 6.12 GPU 升频滞后

**机制 [读码]**：空闲 64 ms 降到 160 MHz（`A830Dcvs.cpp:404-409`），首个提交唤醒线程提到 525 MHz 保持 50 ms（`:187-193`、`:417-426`），触摸 607 MHz 保持到松手后 300 ms（`A830Dcvs.h:91-92`）。

**证据 [日志]**：启动窗口 GPU 在 832–900 MHz，忙 50–60%，`q` 低，没有被 GPU 卡住。心跳里偶见 `160M b34` 只说明采样瞬间在低档。

**判断**：第一帧可能短暂在低频，影响很小。随 6.1 的启动提升一起给 GPU 一个下限即可。

### 6.13 CPU 核绑定

**[读码]** Clutch 单 pset，8 核对称（`UniXNU-sm8750-common/README.md:15`、提交 `37ccfbe`、`d967069`），调度器不知道 6、7 号是超大核。线程可能落在 0–5 号。上限差约 22%（3.53 vs 4.32 GHz），不是停顿的来源。上游 `71ccc68` 传了簇类型，但 Clutch 的非对称调度要 CLPC 配合，改动大、风险高。**不建议现在做。**

### 6.14 backboardd、SpringBoard、render server 优先级

**未验证。** 真机 iOS 上这些依赖 CLPC、工作区间和 runningboard；我们没有 CLPC。`native-work-interval-instance.patch` 已在我们树里，只是让调用成功，没有性能效果 **[读码]**。没有证据表明这里有问题，也没有证据排除。探针见第 8 节 P5。

### 6.15 jetsam

**[日志]** D 包启动附近有 `iconservicesagent status=9 ns=1 code=7`（119.2 s）和 84.6 s 三个 `ns=2` 的 SIGKILL。`phone-gpu-client-memlimits.patch` 已在完整包里修过图标服务的上限。jetsam 杀进程本身不阻塞出帧，但被杀服务重启会抢 CPU。**次要。**

### 6.16 显示节拍

**[读码]** 启动参数没有 `pagani-te=1`，120 Hz 节拍是定时器（`NativeLegacyDisplay.cpp`，`sheng-vsync-fixed=1`），不锁面板 TE。两者频率有微小差异时会周期性多显示或少显示一帧。这是全程现象，不是启动特有。TE 探针已知碰硬件有风险（PROGRESS 第 239 行），**排最后**。

---

## 7. 修复路线

### P0：下一包（低风险、改动小）

| 项 | 改动位置 | 来源 | 动运行时？ | 验证方式 |
| --- | --- | --- | --- | --- |
| CPU、GPU 启动提升（6.1） | `CpuFreqGovernor.h`、`ArmCpu.cpp`、`A830Dcvs.cpp`、exec 钩子 | 自写，策略参考 sheng | 否 | `scmi-perf` 主机测试加用例 |
| 保持 IOSurface 导入（6.2） | `NativeBridgeMetalFrontend.mm` | `0f11e64` 部分（MIT） | 否（要重做信任包） | `surface_import_host.mm` |
| 池内 `bzero` 移到锁外（6.3 第 1 项） | `NativeAdrenoMemory.cpp`、`NativeAdrenoUserClient.cpp` | 自写 | 否 | `test_userclient`、`test_memory` |
| 探针（第 8 节 P1–P4） | 见第 8 节 | 自写 | 否 | |
| 启用 `native_adreno_vm_now_us`，配合 P2 输出 | `Adreno830Start.cpp` | 一行 | 否 | `test_vm` 已覆盖计数 |

### P1：看日志后的第二批

| 项 | 来源 | 动运行时？ |
| --- | --- | --- |
| QoS（6.5） | `0023`，手工改 | **是** |
| 空块迟滞或 room 线程（6.3） | `0021` 思路 | **是** |
| BoImport 锁拆分（6.6） | `78e9343`、`a18a4fa` 思路 | 否 |
| 无锁 fence 判断（6.6） | `17171f4` 思路 | 否 |
| 用本机管线列表扩包（6.4） | 自写 | 否，但必须在所有运行时改动之后做 |

把动运行时的改动打成一批，最后统一重做着色器包，否则包每次失效，界面每条管线都现场翻译（PROGRESS 第 473 行的回归）。

### P2：看数据再决定

UFS 校验或中断、`92aa70f` 的 VM 索引、上游帧路径追踪全套（依赖 UFS 导出通道，我们没有）。

### 不建议

- CP 抢占（6.11）。
- 换上游内存模型（`882f4bf` 系统内存 BO）：这是我们不需要的问题的修复，且会把 281–340 ms 的锁页带进来。
- 整批打上游 bridge 补丁（基线不同）。
- 重开开机预编译。

### 许可

- `Adreno740`、`AdrenoMetal` 的 C++ 代码：MIT，可移植，保留版权行。
- `bridge/patches/*` 改的是 metal2vulkan（`GPL-2.0-or-later OR LGPL-3.0-or-later`，`Cargo.toml:19`），补丁部分沿用其许可证。
- Linux、KGSL：本报告没有用到它们的代码。

---

## 8. 遥测缺口与最小探针

### 缺口

| 缺什么 | 后果 |
| --- | --- |
| `J` 阈值 50 ms，低于它的不记 | 用户说的“小抖动”多半在 16–50 ms，只能看 F 行 `p99` |
| `gap` 不分“没东西画”和“卡住” | 孤立大 gap 不能判定 |
| `pagani-gpu-status=3` 下第 1–3 行被报告行替换（`Adreno830Status.cpp:604-614`、`NativeAdrenoPublish.cpp:1355-1368`） | 完整包日志**没有** DCVS、MEM、IRQ 行 |
| 插件的 `T pipeline`、`V vkpipe`、`V cache`、`N surface` 表只在 `.diag` 文件存在时发送，且只存内核内存（`NativeAdrenoPublish.cpp:38`、`:953-957`），不写 ramoops | 不知道未命中和重导入次数 |
| 内核 BO、导入、映射、锁等待都没有耗时 | 6.3、6.6、6.9 只能推测 |
| CPU 档位只上屏不进日志 | 卡顿时的频率未知，H 行 10 s 一次 |
| UFS 读与校验耗时不进日志 | 6.8 未知 |
| 没有缺页等待计数 | 6.7 未知 |

### 最小探针（都要遵守 `AGENTS.md`：不加每秒新行，数字进已有行）

**P1 把小抖动和 gap 分清（`BootProgress/FrameRate.h`，F 行末尾加字段）**

- 直方图：` h<12–25ms 帧数>/<25–50ms 帧数>/<50ms 以上>`。
- 动画中间的掉帧：间隔超过 12 ms，且前后都有间隔小于 34 ms 的帧，算 ` mid<n>`，这才是可见卡顿。

**P2 内核延迟（`A830Dcvs` 的一秒周期，进 MEM 或 IRQ 行）**

每秒最大值，只有非零才显示，例如 ` LAT a<ms>/<MiB> i<ms> m<ms> f<ms> L<连接锁>/<Ring>/<Mem>/<VM>`：BoAlloc、BoImport、BoMap、BoFree 的最长耗时及对应大小，四把锁的最长等待。用 `mach_absolute_time` 和原子最大值，成本很低。同时让 `pagani-gpu-status=2` 成为测试默认，或让模式 3 保留 IRQ、MEM 行。

**P3 F 行加 CPU 和系统数字**

` c<d0 MHz>/<d1 MHz> ld<d0%>/<d1%> f<free 千页> u<UFS 读 ms>/<校验 ms> w<vm_page_wait 次数>`，都是本秒增量。

**P4 render server 慢调用（`NativeBridgeMetalFrontend.mm`）**

- 在 `newRenderPipelineState*`、`newTexture*`（含 iosurface）、`newBuffer*`、`commit`、`waitUntilScheduled` 包一层计时。
- 超过 4 ms 记入环，一秒最多发一行，例如 `SL pipe 83ms tex 12ms surf 6ms n3`（各类本秒最长耗时和次数）。管线再带 QuartzCore 的名字，便于扩包。
- 另外每 10 s 用现有报告行发一行精简表：`RS p<n>/<总ms>/<最大ms> v<n>/<总>/<最大> hit<n> miss<n> surf<made>/<rel>`，不需要 `.diag`（`.diag` 每 4–6 s 发约 2200 个 marker，代价高，见 `NativeAdrenoMetalPlugin.mm:71-72`）。

**P5 线程类别（一次性）**

打印 CA 渲染线程、`metal2vulkan-scheduler`、`completion`、交接线程的 `pthread_get_qos_class_np` 和调度优先级各一次。

---

## 9. 现在这份长按日志怎么读

先 `python3 read-ramoops.py --label <标签>`，看 `summary.txt` 里 `missing seq`。ring 只留最后一段（status=2 时大约两三分钟，经验值），所以看的是长按前的操作。

**第 1 步：找启动**

```sh
grep 'exec process signature enter' merged.log | grep -E 'Preferences|MobileSafari|MobileCal|MobileTimer|Shortcuts|MobileNotes|Maps|Weather|MobileSlideShow'
```

或者用 `launch_corr.py`（`APPS` 集合里可加名字）。同一 pid 后面的 `exit` 能看出有没有崩溃重启。

**第 2 步：看启动后 0–4 s 的 F 行**

| 现象 | 倾向 |
| --- | --- |
| `max` 小于 50 ms 但 `p99` 高 | 只有小抖动，正是用户说的 |
| `SLOW` 以 `gap` 为主 | CPU 侧，走第 3 步 |
| `SLOW` 以 `off` 或 `copy` 为主，`copy` 超过 20 ms | GPU 排队，看 `gpu meter` |
| `to` 大于 0 的大 gap | 多半是空闲后第一帧，不算卡顿 |
| `FPS 10` 且 `gap 100` 成片 | 周期动画，忽略 |

**第 3 步：看同一时段的其他行**

- `U cpufreq:`：不能有 `off:`。有 `retry` 说明 SCMI 忙；`at lvN, asked lvM` 说明固件压档。
- `H`：`cpu <MHz>`（10 s 一次，只能看大概）、`free`、`avail`。`free` 小于约 2000 同时卡顿，倾向 6.7。
- `gpu meter`（每分钟至多一条）：`rs` 服务时间；`fw`、`n4` 判定要不要管排队；`ext` 外部作业数和忙时；`ctx` 切换；`pf` 页故障。
- `G`：status=3 下只有第 0 行 `GPU RUN <MHz> b<忙> ... <温度>` 有用，其余是报告行（`<进程名> c<提交>/<拒绝> e<错误> x<问题> f<占用> r<余量> m<映射>`、`CLI o.. c.. NGP ..`）。关注 `e`、`x` 增长和 `r` 接近 0。
- `S exit`：`ns=1` 是 jetsam；`status=b`、`status=6` 是崩溃，崩溃重启本身会造成 gap（PosterBoard 那次就是）。
- `X`：崩溃回溯，用 `symbolize-crashes.py`。

**这份日志答不了的**：管线未命中、导入次数、BO 耗时、锁等待、CPU 逐秒档位。要等 P1–P4。

---

## 10. 下一次测试建议

每步之间等 3–5 s，便于在日志里分开。

1. 开机后**等 5 分钟**再测，避开开机后台。
2. 选一个没开过的应用，连开 4 次：打开、回桌面、打开……
3. 换第二个应用，重复。
4. 打开设置，连开 4 次。
5. 长按电源 + 音量下取日志，且**测完立刻长按**，免得 ring 被冲掉。

判读：

| 结果 | 结论 |
| --- | --- |
| 只有第 1 次抖 | 冷缓存：着色器（6.4）、UFS（6.8） |
| 每次都抖 | 分配、CPU（6.1、6.2、6.3） |
| 第 1 次特别重，之后仍有小抖 | 两类都有 |
| 开机很久后再测明显好转 | 后台干扰，不是驱动问题 |

有了 P0 的启动参数后，同一镜像就能做单变量对照：`launch-ms=0` 和 `launch-ms=1000` 各跑一轮。
