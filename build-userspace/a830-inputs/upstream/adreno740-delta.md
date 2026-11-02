# 上游 Adreno740 对 Adreno830 的价值评估（2026-10-31）

范围：上游 `UniXNU-Adreno740` 的 `5c8c145..origin/main`（`7157c16`），对照我们的 `Adreno830` 工作树。只读，没有碰手机，没有改我们的源码树和上游克隆，没有提交。试验只在草稿目录的副本里做（第 7 节）。

证据标记：

- **【读码】** 我读文件或提交确认。
- **【日志】** 我们 `工具/gpu-test-loop/runs/` 的真机日志。
- **【试验】** 本次在电脑上跑命令得到。
- **【上游自述】** 提交说明或注释这样写。数字都是 sheng（A740）上的，不是 13T，我没有复现。
- **【他人报告】** 同目录其他报告的结论，我只核对了引用到的部分。
- **【推测】** 我的判断，没有验证。

简写：A7 = `参考资料/上游-20261031/UniXNU-Adreno740`；A8 = `Adreno830`；不带前缀的 `Native*.cpp`、`A830*.cpp` 都指 A8 里的文件。

---

## 0. 结论摘要

1. **不能整体合并，只能按主题摘。** 37 个主题共 564 个 hunk，逐个试打到我们的树上，437 个失败【试验】。原因不是手误，是结构不同：我们从 5c8c145 派生后重写了锁、内存、恢复和启动。
2. **上游这批性能提交，大半是在修它那把全局设备锁。** 我们 M10 已拆成连接锁 + Ring + Memory + VM 四把，等围栏也已是无锁睡眠，所以 perf(wait)、perf(bo) 的主要收益我们本来就有【读码】。但拆锁后还留着几条串联路径，上游的思路能用上，第 4 节逐条列出。
3. **ABI 版本没变（仍是 2），现有结构没变。** 只加 2 个方法（17、18）、2 个标志位，删 2 个内部常量【读码】。
4. **接口层面用户态可以单独升级，内核不是前提，但没有上机验证。** 新 Turnip 忽略方法 17、18 的失败，两个新标志位都不是必需的【读码】。但有两个条件：构建要换新 ABI 头（已试验，改动 2 行）；上 BO 缓存之前要先做第 5 条的连续内存限制，否则固定池更容易把我们推进慢路径【推测】。
5. **第一批里有四项只要约 50 行，已在副本里跑通。** 导入别名只检查一次（`a087fca`）、连续内存搜索限制在 128 KiB 以内（`01ed692`）、池 BO 的清零移出 Memory 锁（`4c75ccb` 的思路）、ABI 头同步。主机测试通过，51 个内核对象编译通过【试验】。**没上过机，抖动能降多少不知道。**
6. **抖动最缺的是数据。** 我们只知道大帧间隔以 `gap` 为主，不知道里面有多少是内核调用。上游 `59171b2` 的调用记录思路应该最先做，精简成每方法最长耗时和等锁时间，放进现有的计量行。
7. **CP 抢占现在不移植。** 上游默认关，提交说明没有给真机切换成功的数字；默认模式下前台 App 根本不上第二环，帮不了“打开应用”；A8xx 的寄存器和记录大小都不同；我们的日志里超过一半的大间隔发生时 GPU 队列并不堵【读码】【日志】。
8. **系统内存 BO 是唯一能明显省内存的改动。** 现在 1024 MiB 池开机就钉死，日志里的用量是 324–921 MiB。Pagani 已在同款手机和 17.7.11 上跑通这条路【他人报告】。但它会让跨度数量暴涨，要连带改 VM 索引和客户端视图，是一整组改动。
9. **挂死恢复我们已有，重放不急。** 所有可解析的运行日志里都是 `h0`【日志】。值得先改的是归责策略：现在所有有在途作业的连接都记故障，backboardd 可能因此自退（第 4.7 节）。
10. **按需页表和每进程页表解决的是连接数和地址空间上限，不解决抖动。** 等 `nos` 或 `ovf` 出现非零再做。
11. **任务优先级没有第二环就没有作用**，而且 `task_get_gpu_role` 在我们的 xnu-10063 源码里搜不到【读码】。
12. **这 37 个主题都不修小组件不可用、键盘回馈、emoji 错位、亮度、声音、Wi‑Fi、蓝牙**【读码，逐条看过提交说明和差异】。这些问题不要指望这个仓库。差异里（不含测试）提到小组件的只有两处：`01ed692` 的注释讲渲染进程的分配耗时，`2c3df1e` 删掉了一段旧窗口注释。我们记录过的小组件全黑根因在 Metal 家族应答和私有像素格式（`docs/PROGRESS.md:492`），小组件渲染进程也已有 352 MiB 大窗口（`NativeAdrenoPublish.h:152`、`docs/PROGRESS.md:389`）。用户这次说的“用不了”是不是同一个原因，我没有查。

---

## 1. 范围与提交去重

【读码】`git log --no-merges 5c8c145..origin/main` 共 87 个提交，`git patch-id --stable` 去重后 50 个：

| 类别 | 数量 | 说明 |
| --- | --- | --- |
| 主线主题 | 37 | 每个两份（`git log --first-parent` 的一份，加 `feat/ipados27-gpu-parity` 上的 rebase 副本，如 `dd43abd`/`17171f4`），patch-id 全部相同 |
| 旧分支提交 | 13 | 10-22 到 10-26 的旧提交，合并时带进来的历史 |

13 个旧提交的去向【读码】：

| 提交 | 内容 | 去向 |
| --- | --- | --- |
| `b302469` `799f79b` `e1d4828` `23444fe` `9d9c068` `0bdc30f` `8ef8b84` `c5e955f` `6185989` `5629fbc` | 挂起快照、环上下文复位、128 IB、WPTR 围栏写、挂起计时 | patch-id 与 5c8c145 祖先相同（`ae93296` 等），**已在我们的基线里** |
| `62bfd8c` | 总线排空与重发 WPTR | 同标题的 `8ad15b6` 在基线里。patch-id 不同，差异只是另一个提交已带入的两处一致性改动 |
| `6e31dcd` | 128 IB 的另一版实现 | 被基线里的 `c232716` 取代 |
| `9d1f212` | 4 GiB GPU 光圈 | 被 `d834731`（2^ias 地址空间）取代；A8 自己有 4/16 GiB 光圈 |

### 分支相对 main 还有什么没合

**没有。** 【读码】

- `feat/ipados27-gpu-parity`、`perf/gpu-smoothness`、`feat/gpu-aperture-4g`、`feat/stall-snapshot`、`wip/submit-128-ibs` 都是 main 的祖先，`rev-list --left-right` 右侧全是 0。
- 这是用 `98396b9` 这个五父合并做到的，它只保留 main 的树（`git diff --quiet 149ef6d 7157c16` 成立）。所以“是祖先”本身不能证明内容进了 main，我逐条核对了，结果就是上表。
- `feat/ipados27-gpu-parity` 的树与 main 完全相同。
- `perf/gpu-smoothness` 是 10-26 的旧分支，内容全在 5c8c145 里，对我们没有新东西。

---

## 2. 两边的结构差异

后面所有“是否已有等价实现”的判断都基于这张表。

| 维度 | 上游 main | Adreno830 | 证据 |
| --- | --- | --- | --- |
| 锁 | 一把全局 `g.lock`，几乎所有调用都过它 | 每连接 `lock_`，加 Ring、Memory、VM 各一把，`g.lock` 只管注册表 | A7 `NativeAdrenoPublish.cpp:lockTimed`；`NativeAdrenoPublish.cpp:8-11` |
| 等围栏 | 无锁读围栏字，配 100 µs 级暂停 | `A830Fence::wait` 在中断上睡眠，16 ms 保险时钟 | A7 `NativeAdrenoPublish.cpp:576,615`；`A830Fence.cpp:84-133` |
| 用户 BO | 系统内存，散页，无池 | 启动时钉死的写合并连续池，完整包 1024 MiB | `测试镜像/boot-A830-完整包.payload.json`；`NativeAdrenoMemory.cpp:119-145` |
| 地址空间 | 每进程一张页表，4 GiB 到 2^ias | 一张表，分客户端窗口。完整包默认 4 GiB 表，12 个窗口 | A7 `NativeAdrenoPublish.cpp:351`；`NativeAdrenoPublish.h:23-41` |
| 作业 | 只受环空间限制 | 44 槽，非渲染客户端最多 1 个在途 | A7 `1c18419`；`NativeAdrenoSubmit.h:15-22`、`NativeAdrenoSubmit.cpp:41-48` |
| 挂死恢复 | 软复位，GMU 不重启，重放其余作业 | GMU 冷启动，在途作业全部判失败 | A7 `NativeAdrenoSubmit.cpp:749`；`A830Recovery.cpp:224-278` |
| 提交路径 | 每次仍做写合并再断言、抽样等 | 去掉了这些 | A7 `NativeAdrenoSubmit.cpp:412-461`；`NativeAdrenoSubmit.cpp:124-230` |
| 目标内核 | 同时支持 17.7.11、26、27，部分代码已依赖新内核接口 | 只有 xnu-10063（iPadOS 17.7.11） | `UniXNU/feeds.toml:10` |

一个容易忽略的事实：`2c3df1e` 标题写的是“统计缓存维护”，实际塞进了每进程页表这个最大的改动（`NativeAdrenoProcessVm`、`createVm`、CP 切表）。`git log -S NativeAdrenoProcessVm` 只命中这一个提交【读码】。只看标题会漏掉它。

---

## 3. NativeAdrenoABI.h 与用户客户端接口的全部变化

### 3.1 头文件逐项

全部来自 `git diff 5c8c145 origin/main -- NativeAdrenoABI.h`【读码】。A8 的头与 5c8c145 逐字节相同【试验：diff 为空】。

| 项 | 变化 | 引入提交 | 线上格式受影响吗 |
| --- | --- | --- | --- |
| `NativeAdrenoAbiVersion` | 不变，仍是 2 | — | 否 |
| `NativeAdrenoAbiMaxBuffers` | 不变，仍是 128 | — | 否 |
| `NativeAdrenoAbiMaxBoBytesShift = 28` | **删除** | `1c18419` | 否，内核内部常量 |
| `NativeAdrenoAbiMaxQueues = 8` | **删除** | `1c18419` | 否，内核内部常量 |
| `NativeAdrenoMethodSetPriority = 17` | 新增 | `3f4cb94` | 新方法 |
| `NativeAdrenoMethodNote = 18` | 新增 | `e1c0a2d` | 新方法 |
| `NativeAdrenoParamsOwnedCoherent = 2` | 新增 Query 标志 | `865495f` | 否，老客户端不看 |
| `NativeAdrenoParamsMemoryPressure = 4` | 新增 Query 标志（`799158a` 里叫 PoolPressure，`c8806db` 改名） | `799158a` | 否 |
| `struct NativeAdrenoAbiPriority` | 新增：`foreground`、`background`，共 8 字节 | `3f4cb94` | 新结构 |
| `struct NativeAdrenoAbiNote` | 新增：`kind`、`count`、`words[30]`，共 128 字节 | `e1c0a2d` | 新结构 |
| `NativeAdrenoNoteWords/Pass/Pipeline` | 新增枚举 | `e1c0a2d` | — |
| 其余结构和 `static_assert` | 不变；新结构没有加 `static_assert` | — | 否 |

### 3.2 用户客户端的行为变化

头文件之外的变化，都在 `NativeAdrenoUserClient.*` 和 `NativeAdrenoPublish.cpp` 里【读码】。

| 方法 | 上游新行为 | 引入提交 | A8 现状 |
| --- | --- | --- | --- |
| 分发表 | 选择子 17（入 8 字节，出 0）、18（入 128 字节，出 0） | `3f4cb94`、`e1c0a2d` | 只到 16（`NativeAdrenoPublish.cpp:895-911`） |
| 1 Query | `max_bo_bytes` 由固定 256 MiB 改为客户端窗口大小；`flags` 多两位 | `1c18419` 等 | 固定 256 MiB（`NativeAdrenoUserClient.cpp:256`） |
| 2 BoAlloc | `contiguous=0` 走系统页，不再搜连续内存（除非启动参数）；上限等于窗口 | `c8806db`、`01ed692` | 池优先，失败后 ≥64 KiB 先搜连续（`:297`） |
| 3 BoMap | 失败可返回 `kIOReturnNoMemory` 且 VM 不中毒（表页不够） | `d834731` | 表预分配，无此情形 |
| 4 BoFree | 系统 BO 的页放到锁外释放 | `14e4a7d` | 锁内释放 |
| 5 Submit | 环满返回 Busy；`wait_fence` 仍做 CPU 等待；可返回 `kIOReturnNotPermitted`（VM 不可用，`d77091c` 之后实际不再置位） | `1c18419`、`1105b0e` | 环满时按有无在途作业分 Busy 或内核重试；`wait_fence` 只校验不等待（`:462`） |
| 6 WaitFence | 未完成时不取锁 | `dd43abd` | 入口和出口取连接锁（见 4.1） |
| 8 GetStatus | `faults` 为全局加本 VM | `1105b0e` | 每连接一份（`NativeAdrenoStatus`） |
| 11 CreateQueue | 无上限，失败返回 `kIOReturnNoMemory` | `1c18419` | 每连接 8 个（`:728`） |
| 12 CloseQueue | 按动态容量检查 | `1c18419` | 同上 |
| 13/14 CPU 映射 | 系统 BO 的视图直接映射其描述符；视图表随 token 增长 | `c8806db` | 物理区间视图（`NativeAdrenoPublish.cpp:618-641`） |
| 15 GetDeviceInfo | 每进程 VA 为 4 GiB 到 2^ias | `d834731` | 按窗口返回（`:502-510`） |
| 16 BoImport | 上限等于窗口 | `1c18419` | 256 MiB |
| 17 SetPriority | 记下前后台优先级，`foreground==3` 拒绝 | `3f4cb94` | 无 |
| 18 Note | 写入两个 512 行的环，供日志导出 | `e1c0a2d` | 无 |
| 隧道方法 0x7101 | `4f07dc9` 加、`a08cf65` 删，**净零**，main 里没有 | — | 无 |
| 隧道方法 0x7100 | 不变 | — | 相同（`NativeAdrenoPublish.cpp:100`） |
| 交接方法 | 上游仍到 0x7003 | — | A8 扩到 0x7005（M9、M12，`NativeAdrenoPublish.h:286`），**这是我们自己加的** |

内核内部接口（不是用户态契约）：`setScope` 的 `queue_base` 改 64 位；新增 `setStatsSlot`、`offerSystemPages`、`collectFreedPages`、`systemWriteCombined`、`boLimit`、`setPriority`、`submitPriority`；`NativeAdrenoSubmit::submit` 多 `vm`、`clock` 参数，并有 `nextOwner`、`nextPriority`、`nextRole`。

### 3.3 新用户态是否依赖这些变化

检索了上游 `UniXNU-UniXNU` `407db1a` 的 `patches/mesa/*` 和 `UniXNU-AdrenoMetal` `831a9dc`【读码】。下文“补丁第 N 行”都指上游 `patches/mesa/turnip-darwin-kmd.patch`。

| 变化 | 谁用 | 老内核（我们现在）上的结果 | 是否必需 |
| --- | --- | --- | --- |
| 方法 17 | Turnip `dw_open_connection` 用 `(void)dw_call(...)` 发，失败不看；`tu_darwin_set_priority` 失败返回 false，前端只少计一次 | 走隧道到 `dispatch`，`inputSize` 返回 `SIZE_MAX`，答 `kIOReturnUnsupported`（`NativeAdrenoUserClient.cpp:574-575`） | 否 |
| 方法 18 | `tu_darwin_note`，失败返回 false，只在诊断开时调用 | 同上 | 否 |
| `OwnedCoherent` | Turnip 选映射缓存属性（补丁第 1232 行） | 我们不置位，走写合并分支，与现在相同 | 否 |
| `MemoryPressure` | **没人读**。Turnip 改用 `DISPATCH_SOURCE_TYPE_MEMORYPRESSURE`（补丁第 925 行） | 无影响 | 否 |
| 删掉的两个常量 | 上游用户态不引用；我们的 AdrenoMetal 工作树也不引用（`grep` 为空） | 无影响 | 否 |
| `max_bo_bytes` | Turnip 不读 | 超过 256 MiB 的 BO 仍被拒，与现在相同 | 否 |
| `va_start`、`va_size` | Turnip 按内核给的范围建空洞树，不假定 4 GiB 以上 | 可用；小窗口时走“缓存冲刷后重试”（补丁第 1066 行起） | 否 |
| 隧道与连接流程 | 未变：`dw_probe_selector`、0x4147、0x7100 | 可用 | 否 |

构建层面：Turnip 补丁用到 `NativeAdrenoAbiPriority`、`NativeAdrenoNoteWords`，所以**必须用新头编译**。我们的 `scripts/gpu_abi.py` 优先取 `drivers/Adreno830` 的头，因此要同步头文件。试验：直接换成上游的头，把两个删除的常量挪到 `NativeAdrenoSubmit.h`，全部编过【试验】。

### 3.4 结论：能否单独升级用户态

**能，新用户态不依赖内核的任何新能力。** 但这只是接口层面的结论，**没有上机**。下面三件事要先想清楚：

1. **BO 缓存和固定池的相互作用【推测】。** 新 Turnip 把释放的 BO 保留约 1–2 秒（`darwin_cache_trim`，补丁第 892 行起）。在上游这只占系统内存；在我们这里占池。非渲染客户端要给 backboardd 留池的四分之一（`NativeAdrenoPublish.cpp:433`，1024 MiB 时是 256 MiB），池用量一过约 768 MiB，它们就走回退路径。这条路现在有连续搜索。所以**升级用户态之前先做第 8 节第 3 项**。
2. **Run 88 的 GMEM 颜色跳变。** Pagani 并入同一批上游补丁后，A830 上 GMEM 出现颜色跳变，根因未查明，他靠 `sysmem` 绕开【他人报告：`pagani-a830.md` 第 0 节第 13 条】。GMEM 是我们流畅度的来源，这一项要单变量验证。
3. **我们的本地修改要保住。** 0x7004、0x7005 交接方法和 `NativeAdrenoCoherentImports`、AdrenoMetal 本地补丁都是上游没有的。这部分由用户态的报告负责，这里只提醒。

如果确实要在内核上接 17、18：SetPriority 存下数值即可（约 10 行，没有第二环时不起作用），Note 可以直接答成功或忽略。不接也能跑。

---

## 4. 重点主题

### 4.1 perf(wait)：无锁等围栏（`dd43abd`）

**上游做了什么【读码】：** 围栏未到时不取全局锁，只读环上的围栏字；到了或超时才取锁做回收。同一提交还加了等锁、持锁计时。

**上游为什么要做【上游自述】：** 多个进程在等时，单次轮询等全局锁最长 46 ms。

**A8 现状【读码】：** 等待主体已经比上游更好。`AGXDeviceUserClient::waitFence` 醒着时不持任何锁，`observeFence` 进 `A830Fence::wait` 睡在中断上（`NativeAdrenoPublish.cpp:662-699`、`A830Fence.cpp:84-133`）。上游还是 100 µs 级的轮询暂停。

**还剩的差距【读码】：** 入口和醒来后各有一次 `locked()` 零超时调用（`NativeAdrenoPublish.cpp:672,684`），要拿连接锁和 Ring 锁。如果同进程另一线程正在 BoAlloc 或 BoImport，等待的进出都要排队。

**移植：** 思路可用，代码不能用。约 10 行：入口先用 `completed()` 无锁判断未完成就直接睡；醒来确认完成后可以不取锁就返回，回收交给中断的 `retire_call`（`NativeAdrenoSubmit.h:127-129`）。`BoFree` 自己会先 `retireCompleted()`（`NativeAdrenoUserClient.cpp:423-425`），所以不影响释放。

**三症状：** 抖动可能有帮助，提交延迟无直接帮助，内存无关。

### 4.2 perf(bo)：锁外释放页（`14e4a7d`）

**上游做了什么【读码】：** `BoFree` 在锁内解除映射，把还 wired 的描述符交出去，出锁后再 `complete()` 和 `release()`。只针对系统 BO。

**上游为什么要做【上游自述】：** 一次 BoFree 持锁最长 38 ms。

**A8 现状【读码】：**

- 池 BO：释放只是 `poolGive`，很快，不需要这个改动。
- 池外自有 BO：释放不持 Memory 锁，只持连接锁（`NativeAdrenoUserClient.cpp:435`）。
- **导入 BO：`imported_->complete()` 和 `release()` 在连接锁加 Memory 锁下执行（`:435-436`、`NativeAdrenoMemory.cpp:366-373`）。** 上游的提交不覆盖这种情况。

**移植：** 上游代码用不上，要自己写“导入的解除固定放到锁外”。导入生命周期很敏感（int-233..235 的卡死就在这里），必须带主机测试。【他人报告】`app-launch-jitter.md` 提到 IOSurface 可能每帧重新导入，如果属实，这条的优先级要上调。

**三症状：** 抖动可能有，提交延迟可能有（backboardd 的导入释放堵住同连接提交），内存无关。

### 4.3 perf(vm)：映射索引（`36c4e44`）和别名检查（`a087fca`）

**`36c4e44` 做了什么【读码】：** 不再维护一张全部跨度的有序表，改为按 GPU 地址排序的映射对象索引。

**上游为什么要做【上游自述】：** 系统内存 BO 每页一个跨度，约一百万条，BoMap 持锁最长 70 ms。

**A8 现状【读码】：** 仍是全部跨度的有序表，每次 `insert` 做一次 `memmove`（`NativeAdrenoVm.cpp:375-389`）。容量 131072 条（`NativeAdrenoGpuContext.cpp:98`），每条 40 字节（`ArmSmmu4K.h:24-28`），最坏约 5.2 MiB。池 BO 只占一条，所以我们远没有上游那么多。**在保留池的前提下不需要移植；一旦上系统内存 BO 就必须带上。**

**`a087fca` 做了什么【读码】：** 预检已经查过别名后，表事务不再查第二遍，一律走 `ArmSmmu4KLiveMapContextBatchExclusive`。

**上游为什么要做【上游自述】：** 非独占导入的映射每次 3.56 ms，占全部映射时间 79%。

**A8 现状【读码】：** 同样的双重检查还在（`NativeAdrenoVm.cpp:460-464`）。这段在 VM 锁内，而别的进程的 Submit 持着 Ring 锁等 VM 锁（`NativeAdrenoSubmit.cpp:149,178,186`），所以它会拖慢所有进程的提交。

**命中率要说清楚【读码】：** 只有存在第二种缓存类别的导入时才走非独占分支。Turnip 导入一律请求读写（补丁第 1175 行），第二类只来自属主只读的内存（`NativeAdrenoVm.cpp:524`）。我们有多少这种导入没有测过，`native_adreno_vm_kind_us[2]` 记了但没导出。所以收益可能很小，但改动只有 6 行，kit 的语义也确认过（`ArmSmmu4KLive.c:434,437-438`，上游 `83969cd` 的文档说明了同一点）。

**三症状：** `a087fca` 对提交延迟可能有帮助。`36c4e44` 当前无帮助。

### 4.4 perf(memory)：系统内存 BO（`c8806db`）和锁外 wire（`4c75ccb`）

**上游做了什么【读码】：**

- `c8806db`：用户 BO 改成无任务、无内核映射的可分页 `IOBufferMemoryDescriptor`，`prepare()` 时 wire；VM 在 wire 时批量设写合并属性；客户端视图映射描述符本身；取消固定池；用页位图证明导入的独占性。
- `4c75ccb`：描述符的创建和 wire 放到锁外，锁内只收养。

**上游为什么要做【上游自述】：** 1536 MiB 池被运行时的块填满，404 个 BO 回退到散页，96 次 96 MiB 分配失败；另一轮 5454 次分配持锁 4.0 s。

**A8 现状：** 没有等价实现。三件事分开看：

1. **内存占用【日志】。** 池开机就占满：

   | 运行 | 池 MiB | 见过的最大 used | 
   | --- | --- | --- |
   | `20261030-144045-m8-full-1` | 1024 | 921 |
   | `20261030-180230-bisect-A` | 1024 | 815 |
   | `20261030-154643-phone-pack-1` | 1024 | 661 |
   | `20261031-063106-pkgA` | 1024 | 615 |
   | `20261030-213341-island-apps-1` | 1024 | 552 |
   | `20261031-003923-combined` | 1024 | 461 |
   | `20261030-230713-apps-metal-diag` | 1024 | 324 |

   XNU 窗口是 6 GiB（日志头 `xnu ram=0x8c007c000+0x180000000`），池占 17%。

2. **最坏路径【读码】。** 池放不下时，`allocate()` 走内核映射散页，然后 `makeWriteCombined()` 逐页改属性（`NativeAdrenoCpu.cpp:122-124`）。代码注释记录这要 90–130 ms 一个表面大小的 BO（`NativeAdrenoUserClient.cpp:291`）。上游走的批量设属性（`vm_pageout.c:9843` 调 `vm_object_set_pmap_cache_attr`，`:8886` 的 `PMAP_BATCH_SET_CACHE_ATTR`）不是逐页调用，预期更快，我没有测。

3. **`4c75ccb` 在我们这里的对应物【读码】。** 我们没有“锁内 wire”，但有**锁内清零**：`takePooled` 在 Memory 锁下用写合并映射 `bzero`（`NativeAdrenoMemory.cpp:182`、`NativeAdrenoUserClient.cpp:286-288`）。这段时间所有进程的 BoAlloc、BoImport、BoFree 都等 Memory 锁。写合并清零的速度没有测过，【他人报告】估计 64 MiB 需 10–40 ms（推测）。这是第 8 节第 2 项。

**移植可行性【读码】：**

- xnu-10063 有所需机制：`initWithPhysicalMask` 允许可分页且 `inTask` 为空（`IOBufferMemoryDescriptor.cpp:391-399`），命名入口带缓存属性（`IOMemoryDescriptor.cpp:381,587-591`），`vm_object_iopl_request` 批量设属性（`vm_pageout.c:9843,8886`）。
- Pagani 在 17.7.11 和 A830 上已跑通【他人报告：Run 88–90】，基线 `7fa13d4`，对内存层几乎没改（他的 `changes/4.patch` 对 `NativeAdrenoMemory.cpp` 只动了一个弱符号声明）【读码】。
- 但他用 `sysmem` 渲染、最高帧率低、没有长稳数据。这只证明能跑，不证明比池快。

**连带改动【读码】：** 散页会让跨度暴涨，所以这是一组，不能单摘：

| 必须一起动 | 原因 |
| --- | --- |
| VM 容量和索引（`36c4e44`，`28c652d`） | 131072 条不够。保留 32768 条给渲染服务器后，其他客户端约 98304 条，约 384 MiB 散页 |
| 客户端视图（`NativeAdrenoPublish.cpp:618-641`） | 改映射描述符。现在的物理视图有 65536 跨度上限 |
| `exclusivePages` | 要页位图，现在的成对比较会超预算（`NativeAdrenoVm.cpp:349`） |
| `peekWords` 等诊断 | 系统 BO 没有内核映射 |

**建议：** 分两步。先保留池，只把**池外回退**换成系统内存（解决最坏路径）；有了数据再缩小池。这和上游直接取消池不同，是我的取舍【推测】。

**三症状：** 内存有帮助（缩池之后）；抖动可能有帮助（避开最坏路径）；提交延迟无直接帮助。

### 4.5 连续内存分配限制（`b4ba1c1`、`9e21f6a`、`cb9b44f`、`01ed692`）

这四个提交是一条线，最终形态是 `01ed692`。

**上游做了什么【读码】：** 只有 BO 不超过 128 KiB 才尝试连续内存；一次失败后，5 秒内同尺寸及更大的请求都不再尝试。

**上游为什么要做【上游自述】：** 124 次搜索共 21.7 s，中位 163 ms，都在 render server 和小组件渲染器的分配里同步发生；成功的全是 64–128 KiB。改后小组件页、设置、Safari 启动窗口的 BoAlloc 最大值从 260/297/239 ms 降到 137/228/48 ms。

**A8 现状【读码】：** 同样的问题还在。`NativeAdrenoUserClient.cpp:297` 对 ≥64 KiB 的 BO 先做连续尝试，没有上限，也没有退避。这段不持 Memory 锁，但持连接锁。

**比“持连接锁”更严重的一点【读码】：** XNU 的连续搜索在 `vm_page_lock_queues` 和 `vm_free_page_lock` 下进行（调用链 `IOLib.cpp:775` → `vm_kern.c:1053` → `vm_resident.c:6677`，取锁在 `:6008-6009`），每考察 1000 页才可能让出一次，而且要当前连续段不超过 1 页才让（`:6158`）。所以一次失败的大搜索可能拖慢全系统的缺页和分配，不只是这个进程【推测，影响程度没有测】。

**什么时候会走到【读码】【日志】：**

- 非渲染客户端：池空闲小于“保留量 + 请求大小”时。1024 MiB 池的保留量是 256 MiB，所以用量约过 768 MiB 就开始走。
- 渲染服务器：池放不下时。
- `used` 到 917–921 的那一轮（`m8-full-1`），MEM 行带着客户端字段（`backboardd:596+306`）却没有 `sc`。这两个字段是同时加的（`docs/PROGRESS.md:306`），而 `sc` 排在前面（`Adreno830Status.cpp:340-341`），所以那些采样时刻没有池外自有 BO。我检查的全部日志都没有 `sc`，`app-launch-jitter.md` 的 709 条统计结论相同。
- `sc` 只显示存活的字节数。一次失败的搜索不会留下痕迹，这个结论只能说“没看到”。

**所以目前没有证据表明我们走到过这条路。这项是保险，不是已知病因。** 上 Turnip BO 缓存后更可能走到（3.4 节）。

**三症状：** 走到时对抖动帮助很大；平时无效果。内存无关。

### 4.6 CP 抢占两环（`3f4cb94`、`18d79d7`）

**上游做了什么【读码】：**

- 第二条环、两个 4192 KiB 抢占记录、两个 SMMU 信息页。
- 环尾加 `CP_CONTEXT_SWITCH_YIELD`，序言加 `CP_SET_PSEUDO_REG`。
- `driveSwitch` 在提交和回收时决定切换，`CP_SW` 中断完成切换。
- 只在两边 BO 句柄不相交时切换，低优先级作业老化 250 ms 后放行。
- `18d79d7` 加了放弃第二环的兜底：切换超时 30 ms、写寄存器失败、低环作业滞留 2 s，都永久放弃第二环，并在恢复时重放。

**状态【读码】：** 默认关（`sheng-gpu-preempt=1` 才开，sheng `package/GpuPowerProbe.cpp:1694`）。提交说明没有给真机切换成功的数字。sheng 的 `5d471bb` 说明修过“每次切换都被拒绝”的 keepalive 写错误，`18d79d7` 的注释提到真实事故（`untagged-cbea3217c`）。这是实验功能，不是已验证的优化。

**对“打开应用抖动”帮助不大，原因有四：**

1. **默认模式下前台 App 不上第二环【读码】。** `ringForPriority` 只把优先级 2、3 放到环 1（`A7 NativeAdrenoSubmit.cpp:178-186`）。新 Turnip 给普通进程的默认值是前台 1、后台 3。前台 App 是 1，和 backboardd 同在环 0。只有 `sheng-gpu-preempt=2` 才把优先级 1 也放过去。
2. **能否打断一个正在跑的长作业不明【未知】。** 上游写的是 `LEVEL1|SKIP_SAVE_RESTORE`，`YIELD` 在每次提交末尾。中途打断要看固件和 LEVEL 含义，代码和说明都没有写清。没有真机切换耗时数据。
3. **我们的日志里大间隔多数不是 GPU 排队【日志】。** 见第 6 节。
4. **BO 不相交检查跨进程无效【读码】。** `ringDisjoint` 比较 `job.handles`（`A7 NativeAdrenoSubmit.cpp:188-196`），这是 VM 映射句柄，每个 VM 从 1 编号（`A7 NativeAdrenoVm.h:73`）。同一 IOSurface 在两个进程的 VM 里句柄不同，查不出共享；不同 VM 的相同数字又会误判相交。是否造成实际问题未知。

**移植到 A830 的硬件差异【读码】：**

| 项 | A740（上游） | A830 |
| --- | --- | --- |
| `CP_CONTEXT_SWITCH_CNTL` | 0x08a0 | 0x08c0（`docs/inputs/regs/a8xx_regs.txt:360`） |
| `CP_CONTEXT_SWITCH_SMMU_INFO` | 0x08a1 | 0x08c1（`:361`） |
| 恢复地址 | 0x08a3 | 0x08c3（`:362`） |
| `RB_CONTEXT_SWITCH_GMEM_SAVE_RESTORE_ENABLE` | 0x8e50 | 0x8E50（`A830Regs.h:376`） |
| 保活寄存器 | 0x1f8c4 | 0x1F7E5（`A830Regs.h:451`） |
| 每环记录大小 | 4192 KiB | 13536 KiB（`docs/ARCHITECTURE.md:1042`，出自 Linux `a8xx_catalog.c` 的事实） |
| 记录布局、CP 包语义 | a6xx 系 | 需核对 A8xx，本次没有 Linux 源码副本 |

两环记录约 26 MiB 钉死内存。我们的单环假设遍布 `A830Fence`、`NativeAdrenoSubmit::state`、`rangesInFlight`、`NativeAdrenoVm::fence_`、`A830Cp`、`A830Recovery`。上游两个提交共 +1021 行，算上 A8 的改造和测试，我估计 1500 行以上、多轮上机【推测】。

**结论：** 不移植。按 `docs/ARCHITECTURE.md:1042` 已定的门槛（第 0 阶段 `fw` 数据）决定。目前日志里 `fw` 没有有效样本（唯一一条计量行 `ext 0`）。

### 4.7 hangcheck 与重放（`1105b0e`、`3713f8a`、`d77091c`）

**上游做了什么【读码】：**

- 500 ms 检查一次，围栏不动且 CP 无进展（IB1、IB2 基址和剩余量，含 ROQ，最多重试 3 次）判挂死。
- 恢复时：挂住的作业丢弃，其余作业按原围栏号重放，客户端不判设备丢失（`d77091c`）。
- 板级钩子做软复位，GMU 不重启（sheng `package/GpuPowerProbe.cpp:1601`）。

**上游为什么改成不判丢失【上游自述】：** render server 被判丢失后 backboardd 自退，SpringBoard 重启卡住，桌面回不来。

**A8 现状【读码】：**

| 能力 | A8 |
| --- | --- |
| 硬件挂死检测 | 有，`RBBM_INT_0` bit23 |
| 软件看门狗 | 有，500 ms 一查，2 s 无进展（`A830Fence.cpp:206-244`）。进展判据只有 BR IB1，**没有 IB2 和 ROQ** |
| 恢复 | 有且更彻底：GMU 冷启动，S4–S7 重来（`A830Recovery.cpp:224-278`） |
| 重放 | **无**，在途作业判失败（`A830Recovery.cpp:239-240`） |
| 终止卡住的 SMMU 事务（`3713f8a`） | **不需要**。`A830_SMMU_SCTLR_ENABLE=0x1167`，没有 CFCFG，是终止模式（`A830Regs.h:824`） |
| FSR 格式位（`d77091c` 的掩码） | 已处理（`docs/ARCHITECTURE.md:893`） |
| 只丢挂死的作业 | **无**，见下 |

**一个值得先改的策略问题【读码】：** `native_adreno_recovery_begin` 给**所有有在途作业的连接**记 `faults+1`（`NativeAdrenoPublish.cpp:1204-1205`），注释写的是“whatever the cause”。Turnip 看到 faults 变化就判设备丢失，用户态配合补丁让 backboardd 0.5 s 后自退（`docs/ARCHITECTURE.md` 4.13.4）。所以**一个 App 的作业挂死时，只要 backboardd 恰好有作业在途，整个界面就重启**。这正是 `d77091c` 修的那类问题。

**日志【日志】：** 我检查的所有 `merged.log` 里 `GPU RUN` 行都是 `h0`，没有 `GPU LOST`。所以这是低频路径。

**移植：**

- 通用部分：`Job` 保留环字、围栏按原号重放、`hangCheck` 的进展判据。
- 要重写的部分：`NativeAdrenoRing::recover` 现在把围栏推到最后一个号（`NativeAdrenoRing.cpp:418-438`），重放要改成推到挂死号再逐个重发；`A830Fence::forceComplete`、队列计量的 `queueSkip`、`A830Recovery` 的顺序都要跟着改。
- A7xx 专属：`gpuRestart` 的软复位不能抄。我们走 GMU 冷启动。

**建议：** 先做便宜的那一半：只给 CP 停留作业所属的连接记故障（`oldest` 已经算好，`:1186-1187`），其他连接的作业丢弃但不判丢失。重放放到后面。代价是这些连接丢一帧的工作，可能有画面错误【推测】。

**三症状：** 都无帮助，这是稳定性项。

### 4.8 按需页表（`d834731`）和每进程页表（`2c3df1e` 的一部分）

**上游做了什么【读码】：**

- `d834731`：表页按需逐页分配（`NativeAdrenoTablePages`），依赖 ArmSmmu4K `48dbd6c`；地址空间到 2^ias；GMU 保持 0–2 GiB。
- `2c3df1e`：每个连接一个 `NativeAdrenoProcessVm`，自己的页表和 ASID；内核缓冲映射到每张表的相同地址；模式 1 由 CPU 在 GPU 空闲时写 TTBR0，模式 2 由 CP 在环里发 `CP_SMMU_TABLE_UPDATE`；发布时用 `checkCpSwitch` 验证，失败退回模式 1。

**A8 现状【读码】：** 都没有。一张表，预分配 2053 页（8 MiB，`NativeAdrenoGpuContext.h`），16 GiB 时 8209 页。ArmSmmu4K 在 3ba9245，比上游落后 4 个提交。

**能解决什么：**

| 问题 | 现状 | 上了之后 |
| --- | --- | --- |
| 连接数 | 完整包 12 个窗口，普通 App 同时最多约 5 个（8 个小槽留 3 个，`NativeAdrenoPublish.h:184-199`） | 不限 |
| 窗口大小 | 小窗口 124 MiB | 2^ias |
| 隔离 | 只靠驱动检查，SMMU 不隔离 | SMMU 隔离 |
| 页表内存 | 固定 8 MiB | 按需 |

**不能解决的：** 抖动和提交延迟。换客户端时的上下文入口我们已经在发（`NativeAdrenoRing.cpp:160-176`），加切表只会多几个字。模式 1 反而更差：每次换进程都要等 GPU 空闲。

**A830 上的实测情况：**

- Pagani 跑通了每进程页表，但用的是**模式 1**，他的板级没调 `CP_SMMU_APERTURE`【他人报告】。Gen8 差异已摸清：`CP_SMMU_TABLE_UPDATE` 载荷 3 个字（他的 `changes/4.patch` 第 602 行 `a830?3:4`）【读码】。
- 模式 2 在 A830 上没有人验证过。我们有 `CP_SMMU_APERTURE` 调用，结果记在 `A830Smmu`。日志里只有一条计量行显示 `ap1`（状态 1 为授予），但那条记录没过校验、带位翻转，**不能算证据**【日志】。

**成本【读码】：** ArmSmmu4K 是破坏性接口变更（`ArmSmmu4KLiveArena` 换成 `ArmSmmu4KTablePages` 回调），GMU 和 GPU 两个上下文都要跟着改；故障归属要从 FAR 窗口改成 ASID；`A830Smmu` 的验证和 `A830SmmuModel` 测试都要改。`docs/PROGRESS.md:552` 估算 2–3 周加 4–5 轮上机，我认为合理。

**建议：** 不现在做。触发条件是计量行的 `nos`（无空槽拒绝）或 `ovf`（窗口溢出）出现非零。

### 4.9 任务优先级（`3f4cb94` 的一部分、`149ef6d`）

**上游做了什么【读码】：** 用户态用方法 17 告知前后台优先级；每次提交用 `task_get_gpu_role(current_task())` 判断取哪个值；只用来选环。

**A8 现状：** 没有。

**移植障碍【读码】：** `task_get_gpu_role` 和 `PRIO_DARWIN_GPU_BACKGROUND/UTILITY` 在我们的 xnu-10063 源码里搜不到（`osfmk`、`bsd` 都没有，`resource.h` 只有 `PRIO_DARWIN_GPU_ALLOW/DENY`）。Pagani 的基线 `7fa13d4` 在这个提交之前，所以他没遇到。

**建议：** 不移植。没有第二环时优先级不改变任何行为。如果只为兼容新用户态，方法 17 存值即可。

---

## 5. 全部主题总表

列说明：**性质**：通用 = 与 GPU 世代无关；混合 = 逻辑通用但绑了 A7xx 的寄存器或包；专属 = A7xx 或上游结构专有。**抖/延/内** 分别指打开应用抖动、GPU 提交延迟、内存压力：有 / 可能 / 无。“830 现状”都读过源码。

| 提交 | 做了什么 | 830 现状 | 性质 | 成本/风险 | 抖 | 延 | 内 | 建议 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2c3df1e` a | 缓存维护分阶段计数 | 只有总量（`NativeAdrenoCpu.cpp:14`） | 通用 | 小/低 | 无 | 无 | 无 | 并入计量 |
| `2c3df1e` b | 属主已不可缓存的导入跳过维护 | 无 | 通用 | 小/低 | 可能 | 可能 | 无 | 等计量。上游注释针对 iPadOS 27 的显示表面，17.7 上命中率未知 |
| `2c3df1e` c | 缓存维护分段关抢占 | `maintainOwnedPages` 整段关抢占（`NativeAdrenoCpu.cpp:52-64`）；物理页版已分段（`:80-102`） | 通用 | 极小/低 | 可能 | 无 | 无 | **移植** |
| `2c3df1e` d | 每进程页表 | 无 | 混合 | 大/高 | 无 | 无 | 可能 | 暂缓，见 4.8 |
| `2c3df1e` e | 表走读一致时不清缓存 | 已有（`setTableWalkCoherent`） | 通用 | — | — | — | — | 不需要 |
| `865495f` | 自有 BO 走 IO 一致 | 已有更细的等级 2 和按内存类型选择（`NativeAdrenoVm.h:43`，本地 Turnip 补丁） | 通用 | — | — | — | — | 不需要 |
| `bfc5841` | 取消每进程连接上限 | 已等价：完整包 `sheng-gpu-metal-opens=0`，另有崩溃循环计数（`NativeAdrenoPublish.cpp:200-204`） | 通用 | — | — | — | — | 不需要 |
| `28c652d` | 跨度表到 1048576 | 固定 131072 | 通用 | 小/低 | 无 | 无 | 无 | 随系统 BO |
| `799158a` | Query 报告压力位 | 无；上游用户态不读 | 通用 | — | — | — | — | 不需要 |
| `d834731` | 按需页表，2^ias | 无 | 混合 | 大/高 | 无 | 无 | 可能 | 暂缓，见 4.8 |
| `1105b0e` | hangcheck 与重放 | 有检测，无重放 | 混合 | 中/中 | 无 | 无 | 无 | 后做，见 4.7 |
| `bed3b66` | 池按块增长 | 无 | 通用 | — | — | — | — | 已被 `c8806db` 取代 |
| `1c18419` | 对象、队列、作业按需增长 | 定长：作业 44、队列 8、对象 16384/8192/4096、BO 256 MiB | 通用 | 中/中 | 无 | 可能 | 可能 | 部分。放开作业上限可能加重排队，要等数据 |
| `6a3c9a8` | 测试替身 | — | 测试 | — | — | — | — | 随对应代码 |
| `3713f8a` | 重启前终止卡住的 SMMU 事务 | 终止模式，不会卡住（`A830Regs.h:824`） | 专属 | — | — | — | — | 不需要 |
| `b4ba1c1` | 锁内不搜连续内存 | 已不在全局锁，但仍在连接锁内 | 通用 | — | — | — | — | 由 `01ed692` 覆盖 |
| `d77091c` | 重启后所有客户端保留设备 | 有在途作业的连接都记故障（`NativeAdrenoPublish.cpp:1204`） | 通用 | 小/中 | 无 | 无 | 无 | **移植策略部分** |
| `9e21f6a` | 有池时不搜连续内存 | 未修（`NativeAdrenoUserClient.cpp:297`） | 通用 | — | — | — | — | 由 `01ed692` 覆盖 |
| `4f07dc9` `0ca23f5` `a08cf65` | 加了又删的管线导入方法 | 无 | — | — | — | — | — | 净零，跳过 |
| `c8806db` | 系统内存 BO，取消池 | 无 | 通用 | 大/中 | 可能 | 无 | 有 | 分步，见 4.4 |
| `44e6319` | 主机替身只应答 GPU 服务 | 无 | 测试 | 极小/无 | 无 | 无 | 无 | 顺手。试打 0 冲突 |
| `1a9517b` | 每客户端 BO 字节数 | 已有（`account()`、MEM 行） | 通用 | — | — | — | — | 不需要 |
| `4c75ccb` | wire 放到锁外 | 无对应操作；对应物是锁内清零 | 通用 | 小/低 | 可能 | 可能 | 无 | **移植思路** |
| `a087fca` | 别名只查一次 | 未修（`NativeAdrenoVm.cpp:460-464`） | 通用 | 极小/低 | 可能 | 可能 | 无 | **移植** |
| `cb9b44f` | 连续块阈值、恢复步骤记录 | 恢复有 `A830Trace` | 通用 | — | — | — | — | 被 `01ed692` 修正 |
| `e0d159a` | 导出围栏给显示直扫 | `NativeAdrenoSubmit::completed/lastSubmitted` 已有；我们走 D1 交接 | 通用 | — | — | — | — | 不需要 |
| `90ba1e3` `58ac3af` `e8bee0e` `88b83dd` | 作业时间线，CP 时间戳 | 有队列计量，无 CP 时间戳，单作业时间是估算 | 混合 | 小/低 | 无 | 无 | 无 | **移植**，默认关，定位价值高。Gen8 寄存器 0x8E7，Pagani 已用 |
| `59171b2` | 记录每次调用 | 无；方法统计没有耗时 | 通用 | 小/低 | 无 | 无 | 无 | **移植精简版** |
| `01ed692` | 连续内存只到 128 KiB，失败退避 | 未修 | 通用 | 极小/低 | 有 | 无 | 无 | **移植**，走到时才生效 |
| `dd43abd` | 无锁等围栏 | 主体已有且更好 | 通用 | 小/低 | 可能 | 无 | 无 | 只取入口和出口 |
| `14e4a7d` | 锁外释放页 | 池 BO 不需要；导入未覆盖 | 通用 | 中/中 | 可能 | 可能 | 无 | 改写成导入版 |
| `36c4e44` | 映射索引 | 跨度表 | 通用 | 中/中 | 无 | 无 | 无 | 随系统 BO |
| `3f4cb94` `18d79d7` | CP 抢占两环 | 无 | 专属 | 很大/高 | 未知 | 未知 | 约 +26 MiB | 不移植，见 4.6 |
| `e1c0a2d` | Note 方法、重启记录 | 无；我们没有 UFS 导出通道 | 混合 | 小/低 | 无 | 无 | 无 | 只留重启记录思路 |
| `149ef6d` | 优先级调用记录 | 无 | 通用 | — | — | — | — | 不需要 |

其余 13 个旧提交见第 1 节，都不需要动作。

`noteStall` 卸死处理（基线就有，上游保留）：A8 按设计删除了（`docs/ARCHITECTURE.md` 4.13.4），不要加回来。

---

## 6. 对三个症状的判断

### 打开应用时的抖动

**这个仓库不太可能单独解决它。**

1. 大间隔以 `gap` 为主（合成器没交帧），拷贝本身很短【日志，`inventory/evidence.md` 第 7 节；`app-launch-jitter.md` 结论相同】。
2. 我统计了 6 轮同时有 `J` 记录和队列行的日志，把 `gap≥50 ms` 的记录与前后 1.2 s 内的 `q` 最大值对照【日志，脚本 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/adreno740/gapq.py`】：

   | 同时段 `q` 最大值 | gap≥50 ms | gap≥200 ms |
   | --- | --- | --- |
   | <20 ms | 14 | 5 |
   | 20–49 ms | 3 | 0 |
   | ≥50 ms | 7 | 4 |
   | 没有队列行 | 1 | 1 |

   样本只有 25 条，因为日志环会被冲掉。`q≥50` 的 gap≥200 记录全部来自 `m8-full-1` 一轮，当时 GPU 被压在 389 MHz，88–98 °C，不代表现状。这个统计说明不了什么是主因，只说明**不是全部来自 GPU 排队**。
3. 用户这次的完整包没有日志（`20261031-134632` 两区全丢），所以上面都是旧包的数据。

**内核能做的【读码，收益未测】：** 减少串联。现在的链路是：一个进程的 BoAlloc 持 Memory 锁清零 → backboardd 的 BoImport 等 Memory 锁，同时持自己的连接锁 → backboardd 的 Submit 等连接锁 → 这一帧晚了。第 8 节第 2、4、7、8 项都在减少这类串联等待，第 3 项是防止走到连续搜索这条慢路径。

**用户态可能收益更大【他人报告】：** Turnip BO 缓存、iOS QoS 补丁、IOSurface 导入保持。这些不在本仓库，见 `app-launch-jitter.md`、`ipados27-parity.md`。

### GPU 提交延迟

【读码】路径本身已经很短，没有缓存维护，没有 CPU 等待。关键风险是 Ring 锁等 VM 锁：`NativeAdrenoSubmit::submit` 持 Ring 锁调 `describe`、`holdJob`、`noteUse`，每次都取 VM 锁；别的进程的 BoMap 持 VM 锁时，所有提交都等。`NativeAdrenoSubmit.h:41-46` 说 Ring 锁只持微秒级，这依赖 VM 锁也短。所以对提交延迟最有用的是缩短 VM 锁：`a087fca`，以及把 `mapLocked` 里的缓存维护（`NativeAdrenoVm.cpp:448`）挪到取锁之前。后者不是上游改动。

### 内存压力

【读码】按收益排序：缩小固定池（要先上系统 BO）> 上游 Turnip BO 缓存的副作用要控制 > 每连接表按需增长（最坏约 30–40 MiB）> 按需页表（省几 MiB）。

---

## 7. 试验记录

全部在草稿目录的副本里，**没有动我们的源码树**。目录：`/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/adreno740/trial/`。草稿目录会被清空，所以关键差异抄在附录 A。

### 7.1 补丁可应用性

每个主题单独对我们的工作树做 `patch --dry-run`。结果在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/adreno740/trial/apply-table.txt`。

- 564 个 hunk 失败 437 个（77%）。
- 每个提交是单独试的，没有带前置提交，所以数字偏高，只说明不能机械合并。
- 能干净应用的几乎只有测试替身（`44e6319`，0 失败）。

### 7.2 最小移植

| 项 | 对应上游 | 结果 |
| --- | --- | --- |
| 表事务不再重查别名 | `a087fca` | 通过 |
| 连续尝试限制 64–128 KiB，失败退避 | `01ed692` | 通过。要改两个钉住旧行为的测试块（`test_userclient.cpp` 第 668、924 行附近） |
| 池 BO 清零移出 Memory 锁（新增 `finishPooled`） | `4c75ccb` 的思路 | 通过 |
| 换上游 ABI 头，删除的两个常量挪到 `NativeAdrenoSubmit.h` | 3.3 节 | 通过 |

源码改动约 50 行（增删合计，不含 ABI 头和测试），差异见附录 A。

**测试：** `tests/run-host-tests.py` 全量 43 通过（含 51 个内核对象用真实 XNU 编译记录编译）、1 失败、1 跳过。

- 失败的 `test_a830votes` 是副本里的路径问题（找不到 `../UniXNU/kits/...`）。**干净副本同样失败**，与改动无关。
- 跳过的是 `test_sha256` 的固件项。

**局限：** 只证明能编译、不破坏现有用例。没有上机，没有测出耗时变化，也没有写新的并发用例。退避用的是分配次数，正式移植应改成时间（上游 5 s）。`maintainOwnedPages` 分段没有试，那个文件只在内核里编。`finishPooled` 失败时不再就地 `release()`：`allocBoInner` 的失败分支会 `releaseEntry`，但 `allocate(..., pooled=true)` 这条路没有清理，定稿时要补上。这个失败只有非写合并池才走得到，我也没有搜到传 `pooled=true` 的调用者。

---

## 8. 移植清单（按收益/风险排序）

改动量和工期是我的估计【推测】。**所有项都要上机验证，这里的“收益”是预期，不是实测。**

### 第一批：下一包可带，低风险

| # | 项 | 来源 | 改动量 | 风险 | 抖/延/内 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 内核调用计量：每方法最长耗时、连接锁和 Ring、Memory、VM 锁的最长等待 | `59171b2`、`dd43abd` 的思路 | 约 60 行 | 低 | 定位用 | **最先做。** 其余所有判断都缺这份数据。按 `AGENTS.md`，数字进现有计量行，不加新的每秒日志行 |
| 2 | 池 BO 清零移出 Memory 锁 | `4c75ccb` 的思路 | 约 20 行，已试验 | 低 | 可能/可能/无 | 拆跨进程串联 |
| 3 | 连续内存限制 128 KiB，失败退避 | `01ed692` | 约 15 行，已试验 | 低 | 走到时有/无/无 | 上 Turnip BO 缓存之前必须有 |
| 4 | 别名只查一次 | `a087fca` | 6 行，已试验 | 低 | 可能/可能/无 | 命中率未知，但几乎无代价 |
| 5 | 缓存维护分段关抢占 | `2c3df1e` 的一部分 | 约 15 行 | 低 | 可能/无/无 | 影响池外 BO 和开机写合并池 |
| 6 | ABI 头同步，方法 17、18 应答 | 3.3 节 | 头 2 行，应答约 15 行 | 低 | 无 | 用户态升级的前提。遵守 `AGENTS.md` 头文件逐字节一致的规定 |

### 第二批：有第一批的数据后再定

| # | 项 | 来源 | 改动量 | 风险 | 抖/延/内 | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| 7 | WaitFence 进出不取连接锁 | `dd43abd` 的思路 | 约 10 行 | 低 | 可能/无/无 | 看计量里连接锁等待 |
| 8 | 导入的 `prepare`、`complete` 移出 Memory 锁 | `14e4a7d` 的思路，扩展到导入 | 约 80 行加测试 | 中 | 可能/可能/无 | 生命周期敏感，int-233..235 |
| 9 | `mapLocked` 的缓存维护移到 VM 锁外 | 我们自己的改动 | 约 20 行 | 低 | 可能/可能/无 | 看计量里 VM 锁等待 |
| 10 | 故障只记给 CP 停留作业的连接 | `d77091c` 的思路 | 约 20 行 | 中 | 无 | 稳定性。防止一个 App 挂死拖垮界面 |
| 11 | CP 时间戳时间线，默认关 | `90ba1e3`、`88b83dd` | 约 100 行 | 低 | 定位用 | 分清排队时间和 GPU 执行时间 |

### 第三批：大改，需要明确理由

| # | 项 | 来源 | 改动量 | 风险 | 抖/延/内 | 触发条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 12 | 系统内存作为池外回退 | `c8806db`、`4c75ccb`、`14e4a7d`、`36c4e44`、`28c652d` | 约 500 行，1–2 周 | 中 | 可能/无/无 | 计量或 `sc` 字段显示走到回退路径 |
| 13 | 缩小固定池 | 第 12 项之后 | 启动参数 | 中 | 无/无/有 | 第 12 项稳定后，用内存压力数据决定 |
| 14 | 重放，hangcheck 加 IB2 和 ROQ | `1105b0e` | 约 200 行加测试，1 周 | 中 | 无 | 日志里出现 `h` 非零 |
| 15 | 对象、队列、作业按需增长 | `1c18419` | 约 200 行 | 中 | 可能/可能/可能 | 出现容量拒绝；作业上限单独评估 |

### 暂不做

| # | 项 | 来源 | 理由 | 重新考虑的条件 |
| --- | --- | --- | --- | --- |
| 16 | 按需页表和每进程页表 | `d834731`、`2c3df1e`、ArmSmmu4K 4 个提交 | 2–3 周加 4–5 轮上机，不解决用户现在的问题 | `nos` 或 `ovf` 非零 |
| 17 | CP 抢占 | `3f4cb94`、`18d79d7` | 见 4.6 | `fw` 达到 `docs/ARCHITECTURE.md:1042` 的门槛，且上游或 Pagani 给出真机切换数据 |
| 18 | 任务优先级 | `3f4cb94`、`149ef6d` | 依赖第 17 项；接口在 xnu-10063 上没有 | 随第 17 项 |

### 不移植

`865495f`、`bfc5841`、`799158a`、`bed3b66`、`b4ba1c1`、`9e21f6a`、`3713f8a`、`cb9b44f`、`1a9517b`、`e0d159a`、`4f07dc9`、`0ca23f5`、`a08cf65`、`e1c0a2d` 的 Note 部分，以及基线的 `noteStall`。理由见第 5 节。

### 许可证

Adreno740、ArmSmmu4K、AdrenoMetal 是 MIT；sheng 和 Pagani 设备仓库是 BSD-2-Clause-Patent（`LICENSE` 文件已看，Pagani 的单文件头各异，见 `pagani-a830.md`）。都可以移植，保留版权声明。Linux 和 KGSL 只取寄存器和数值事实。

---

## 9. 还缺的数据

不用 adb，等用户下次测试时带上：

1. **长按取日志**，完整包这次两区全丢，定时复位读不回来。
2. **内核调用计量**（第 1 项）：判断第二批哪些值得做。
3. **`gpu meter` 行的 `fw`、`n4`、`nos`、`ovf`、`pf`、`ap`**：判断抢占和每进程页表。注意只有 `pagani-gpu-status=2/3` 才写。
4. **`gpu aperture` 开机注记**：每进程页表能否走 CP 切换。目前只有一条损坏的记录。
5. **非独占导入的比例**：`native_adreno_vm_kind_us[2]` 没有出口，加进计量行。
6. **写合并清零的实际速度**：判断第 2 项的真实收益。
7. **IOSurface 的重新导入速率**：判断第 8 项的优先级。

---

## 附录 A：试验差异（内核部分）

ABI 头换成上游版本，测试改动没有列出。这是用来估算成本的试验，不是定稿补丁。

```diff
--- a/NativeAdrenoMemory.cpp
+++ b/NativeAdrenoMemory.cpp
@@ takePooled
-            mapping_=pool.virt+offset;bytes_=bytes;pooled_=true;pool_offset_=offset;prepared_=true;
-            bzero(mapping_,bytes);
-            /* Through a write-combined pool's own mapping the zeros go to DRAM; the barrier completes them. */
-            IOReturn status=kIOReturnSuccess;
-            if(pool.write_combined)__asm__ volatile("dsb sy":::"memory");
-            else status=NativeAdrenoMaintainOwnedPages(mapping_,bytes,NativeAdrenoClean);
-            if(status!=kIOReturnSuccess) {(void)release();return status;}
+            mapping_=pool.virt+offset;bytes_=bytes;pooled_=true;pool_offset_=offset;prepared_=true;unzeroed_=true;
             native_adreno_pool_stats[1]=native_adreno_pool_stats[1]+1;
             return kIOReturnSuccess;
@@ 新函数，放在 allocatePooled 之前
+/* The zero fill of a BO the pool just gave: the pages are this object's alone, so no lock is needed. */
+IOReturn NativeAdrenoMemory::finishPooled()
+{
+    if(!pooled_ || !unzeroed_)return kIOReturnSuccess;
+    unzeroed_=false;
+    bzero(mapping_,bytes_);
+    /* Through a write-combined pool's own mapping the zeros go to DRAM; the barrier completes them. */
+    if(pool.write_combined) {__asm__ volatile("dsb sy":::"memory");return kIOReturnSuccess;}
+    return NativeAdrenoMaintainOwnedPages(mapping_,bytes_,NativeAdrenoClean);
+}
@@ allocate
         status=takePooled(bytes,limit);
+        if(status==kIOReturnSuccess)status=finishPooled();
         if(status!=kIOReturnNoResources)return status;
--- a/NativeAdrenoMemory.h
+++ b/NativeAdrenoMemory.h
-    bool prepared_=false,quarantined_=false,write_combined_=false,pooled_=false;
+    bool prepared_=false,quarantined_=false,write_combined_=false,pooled_=false,unzeroed_=false;
@@ allocatePooled 之后
+    /* allocatePooled's zero fill, after the Memory lock is let go; the BO is not published until it succeeded. */
+    IOReturn finishPooled();
--- a/NativeAdrenoSubmit.h
+++ b/NativeAdrenoSubmit.h
+/* What upstream's ABI header no longer states; this kernel's fixed limits (a client's queues, a BO's bytes). */
+enum : uint32_t {NativeAdrenoAbiMaxQueues=8,NativeAdrenoAbiMaxBoBytesShift=28};
--- a/NativeAdrenoUserClient.cpp
+++ b/NativeAdrenoUserClient.cpp
+static uint64_t contiguous_tick;
+extern "C" {uint64_t native_adreno_contig_failed_bytes;}
@@ allocBoInner，Memory 锁之后
+    if(status==kIOReturnSuccess)status=entry->memory.finishPooled();
@@ allocBoInner，池外回退
-        if(request->contiguous==0 && request->bytes>=NativeAdrenoClientContiguousFirstBytes) {
+        /* A contiguous search the free memory cannot serve scans every free page first, under the VM's page-queue and free-page locks:
+         * only small runs are asked for, and after a failure none of that size or larger for a while (allocations, not time: no clock here). */
+        static uint64_t failed_at;
+        uint64_t &failed_bytes=native_adreno_contig_failed_bytes;
+        const uint64_t tick=__atomic_add_fetch(&contiguous_tick,uint64_t(1),__ATOMIC_RELAXED);
+        if(failed_bytes && tick-failed_at>=NativeAdrenoClientContiguousBackoff)failed_bytes=0;
+        if(request->contiguous==0 && request->bytes>=NativeAdrenoClientContiguousFirstBytes && request->bytes<=NativeAdrenoClientContiguousMaxBytes &&
+            !(failed_bytes && request->bytes>=failed_bytes)) {
             status=entry->memory.allocate((size_t)request->bytes,output_bits_,true,(size_t)request->bytes);
+            if(status) {failed_bytes=request->bytes;failed_at=tick;}
             if(status && !entry->memory.quarantined() && entry->memory.release()==kIOReturnSuccess)status=kIOReturnUnsupported;
         }
--- a/NativeAdrenoUserClient.h
+++ b/NativeAdrenoUserClient.h
-enum : size_t {NativeAdrenoClientMaxObjects=16384,NativeAdrenoClientMinObjects=256,NativeAdrenoClientContiguousFirstBytes=65536};
+enum : size_t {NativeAdrenoClientMaxObjects=16384,NativeAdrenoClientMinObjects=256,NativeAdrenoClientContiguousFirstBytes=65536,
+    NativeAdrenoClientContiguousMaxBytes=131072,NativeAdrenoClientContiguousBackoff=4096};
--- a/NativeAdrenoVm.cpp
+++ b/NativeAdrenoVm.cpp
@@ mapLocked
-    const ArmSmmu4KStatus status=exclusive?
-        ArmSmmu4KLiveMapContextBatchExclusive(&io_,bank_,context_,lower.arena?&lower:nullptr,
-            high.arena?&high:nullptr,upper,&visibility_[upper],changes,spans,budget_,&update):
-        ArmSmmu4KLiveUpdateContextBatch(&io_,bank_,context_,lower.arena?&lower:nullptr,
-            high.arena?&high:nullptr,upper,&visibility_[upper],changes,spans,1,budget_,&update);
+    /* preflight cleared the pages of conflicting aliases (exclusive, or checked against every other-class range there). */
+    const ArmSmmu4KStatus status=ArmSmmu4KLiveMapContextBatchExclusive(&io_,bank_,context_,lower.arena?&lower:nullptr,
+        high.arena?&high:nullptr,upper,&visibility_[upper],changes,spans,budget_,&update);
```
