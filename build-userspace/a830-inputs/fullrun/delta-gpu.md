# 新改动审查：GPU 窗口表、区段容量、光圈注记、页故障日志

审查对象是内核 `aa94decf`（`kernel.macho` 的 sha256 与 `测试镜像/boot-A830-阶段15-首测.payload.json`、`boot-A830-阶段15-完整包.payload.json` 里的 `kernel_sha256` 一致）。

全程只读，除本文件外没有改项目文件，没有碰手机，没有编译内核。临时文件在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/delta`。改动范围已用 `scratchpad/gpu-kdrv-final/before` 的快照比对，与任务列出的四项一致，`NativeAdrenoGpuContext.h` 只改了注释。

## 结论

- **没有发现 crash、hang、storage 级问题。** 四项改动都沿真实调用顺序走过，内核二进制也逐项核对过。
- **1 条 minor**：`vmr` 把两种拒绝加在一起，日志里也没有区段总数，第 2 项改动上机后没法判断是否对症。不影响上机，可以不修。
- 这批改动不需要因为本审查再重打镜像。

## 发现

### D1（minor）`vmr` 分不清对象上限和区段上限，区段总数没有日志出口

- 位置：`Adreno830/NativeAdrenoPublish.cpp:1369`
- 路径：`mapBo` → `mapClient` → 两个判定分别计入 `native_adreno_vm_reserve_refused[0]`（对象，`NativeAdrenoVm.cpp:544-547`）和 `[1]`（区段，`:548-551`）→ `native_adreno_client_tick` 把两者相加成 `reserve_refusals` → `gpu meter` 的 `vmr`。
- 证据：
  - `native_adreno_vm_reserve_refused` 全仓库只有这一个读者，读的时候就加在一起了。
  - `freeSpans()`、`freeObjects()` 没有状态路径的调用者，`spanCount()` 也不进任何日志行。
  - `runs/20261031-223924-stage1-full-1/merged.log` 第 11243 行的 `vmr 37` 分不出来。对象数 `imp` 所在的 MEM 行在该轮 28.97 s 之后就不再出现（第 2626 行是最后一条）。
  - `NativeAdrenoGpuContext.cpp:98-99` 的注释把 `vmr 37` 归到区段，这一点日志证明不了。
  - 非渲染客户端的对象上限仍是 24576（32768 减 8192 保留），这次没动，而连接数上限从 12 变成 32。
- 影响：判据 5 的 `vmr` 这轮要是还不为 0，答不出“是区段还没够，还是对象到顶了”，要再开一次机才知道。
- 主机测试抓不到的原因：测试分别断言两个计数器，不看日志行能不能分辨。
- 最小修改：`Adreno830StatusClients` 分成两个字段，`meterLine` 输出 `vmr <对象>/<区段>`。最宽的行多 6 个字符，约 309，仍在 `Adreno830StatusMeterBytes=320` 之内。要看总量再加一个无锁读的 `freeSpans()`。需要同步 `test_status` 并重编内核。
- 不修的话：`vmr` 只当“有没有拒绝”看，不用它判断 262144 够不够。

## 逐项核对

### 1. 窗口表

【二进制】直接从 `kernel.macho` 读出 `_NativeAdrenoClientWindows16G`（`0xfffffff00703f8d0`）：32 项与源码一致，全部 4 KiB 对齐（也都是 16 KiB 对齐，只有槽 31 的长度不是），没有重叠，没有一项跨 4 GiB 整数倍；空隙在 `0xFC000000`、`0x1FC000000`、`0x2FC000000`，各 `0x4000000`。总和 `0x3D3FFE000`，客户端范围 `0x3E0000000`。内核里这张表只有一份，`NativeAdrenoListedSmallKeep` 的指针比较成立。`_NativeAdrenoClientWindows4G`、`2G` 没变。

| 使用者 | 位置 | 怎么用 | 假设连续或总和吗 |
|---|---|---|---|
| `NativeAdrenoClientWindowsValid` | `NativeAdrenoPublish.h:70` | 只比较 `start<at`，允许空隙 | 否 |
| `NativeAdrenoClientWindowsFor` | `:92` | 只按 `va_end` 阈值选表 | 否 |
| `NativeAdrenoClientWindowsEnd` | `:98` | 取最大终点 | 否 |
| `NativeAdrenoClientSlotAt` | `:107` | 逐窗比较，空隙返回 `NativeAdrenoMaxClients` | 否 |
| `NativeAdrenoFaultNote`、`NativeAdrenoFaultVerdictFor` | `:124-144` | 空隙等同“没有窗口”，判定为 apart 或 undecided | 否 |
| `native_adreno_note_page_fault` | `NativeAdrenoPublish.cpp:1194` | 空隙不归责，只记 `fault_stats[1]`、`[5]`，不碰 `g.lock` | 否 |
| `NativeAdrenoClientSlotFor`、`NativeAdrenoClientSmallFree` | `.h:196`、`:227` | 只看 `bytes` 是否为 0 | 否 |
| `mount` | `.cpp:483` | 取 `g.windows[slot]` | 否 |
| `setScope` | `NativeAdrenoUserClient.cpp:209` | 终点不超过 `1<<34`，槽 31 为 `0x3FFFFE000` | 否 |
| `mapBo` | `:433` | 只按本窗口判界，空隙里映射不了 | 否 |
| `deviceInfo` | `:558` | 用本窗口替换 `va_start`、`va_size` | 否 |
| 提交范围 | `.cpp:483`、`setScope` | `slot*NativeAdrenoAbiMaxQueues`，与地址无关 | 否 |
| `native_adreno_publish` | `.cpp:1392-1393` | 用终点和 `input_bits` 比较 | 否 |
| `info.va_size` | `Adreno830Start.cpp:1957` | `0x3E0000000`，含空隙。只用来选表，以及没有窗口的内核自用客户端 | 否 |

`工具/gpu-test-loop/*.py` 里没有写死窗口表。

沿真实顺序走第一个过线的客户端（PhotosPosterProvider，约 60 s）：

1. `mount` 在 `g.lock` 下取到槽 5。
2. `core.open` 收到 `input_bits=34`（【二进制】`stageMetal` 里 `mov w10,#0x22` 加 `csel`）。
3. `setScope(5*队列数, 0x100000000, 0x1C000000)` 通过，这时连接还没有对象。
4. Turnip 的 `GetDeviceInfo` 拿到本窗口，`util_vma_heap_init` 建 448 MiB 的堆。
5. 第一个 BO 是 `global_bo`，iova 为 `0x100000000`。
6. `mapBo` 判界通过 → `mapClient` → `mapLocked` → `preflight`（`forbidden`、`rangeAt` 全是 64 位）→ 表事务 → `insert`。
7. `runCore` 在连接锁放开后调 `notePast4G`。

每一步用到的指针都在：`g.windows` 指向静态常量，连接核心由 `box_` 持有，`input` 在 `locked()` 返回后仍有效。

### 2. `va_start` 低 32 位为 0

- `tu_knl_darwin.cc`：`darwin_allocate_iova` 的 `*iova ? ...` 是 64 位判断。`device->va_start`、`NativeAdrenoAbiBoMap.iova`、`NativeAdrenoAbiSubmitBuffer.iova` 全是 `uint64_t`。
- `tu_device.cc:2901`：`util_vma_heap_init(&device->vma, va_start, ...)`。`vma.c` 的 0 判断是整个 64 位偏移。
- Turnip 其余部分：搜 `(uint32_t)...iova`、`iova & 0x`、`if (...iova)`，都没有把低 32 位当“未设置”。写入都是 `tu_cs_emit_qw` 或高低两半。
- metal2vulkan：`device_address` 是 `Option<u64>`，“没有地址”靠 `None` 表示，不靠数值。着色器里的判空也是 64 位（`function_table.rs` 的 `k64(fb, 0)` 配 `INotEqual`）。`bind/heap.rs:1744` 拆成低、高两半写入目录，`entry_address` 再拼回 64 位。
- AdrenoMetal 插件：只有 `NativeAdrenoUserTest.h:132` 用 `va_start`，用 `uint64_t` 保存，PM4 写了高低两半。这段只在未发布模式和 `adrenoprobed` 里跑；发布模式在 `publish::requested()` 处就返回了，这轮镜像走发布模式。
- 内核：`entry->iova`、`Mapping.iova`、`ArmSmmu4KRange.iova`、`NativeAdrenoSubmitBuffer.iova` 都是 64 位，没有 `!iova` 判断。

硬件语义：`global_bo` 偏移 0 是 `tu6_global.shaders`，所以 blit 着色器的基址寄存器低 32 位确实会是 0。代码里找不到依赖这一点的地方。Linux msm 和上游 Adreno740（`GpuClientVaStart=0x100000000`）的起点相同，这是常态路径，但只是旁证，见“未验证”。

读日志时可以用到的一点：槽 5 的窗口去掉高位后全部落在 `0x20000000` 以下，也就是落不到任何客户端窗口。所以高位丢失在槽 5 上几乎都会变成页故障。槽 6 以上丢高位可能落在低窗口的已映射页上，不一定有 `gpu fault`，`pf 0` 证明不了这些槽。

### 3. 槽 31 缩小、空隙、4 GiB 回退

| 窗口 | 堆（`min(内存, va_size)`） | `block_bytes` |
|---|---|---|
| 槽 0 | 1536 MiB | 64 MiB |
| 槽 1–3 | 512 MiB | 32 MiB |
| 槽 4–30 | 448 MiB | 16 MiB |
| 槽 31，改前 `0x2BFFE000` | 704 MiB | 32 MiB |
| 槽 31，改后 `0x1FFFE000` | 约 512 MiB（少 8 KiB） | **16 MiB** |

- 槽 31 的块变小了，因为 `0x1FFFE000/16` 不到 32 MiB。这是往保守方向变，而且只有其余小窗都占满才轮到它。
- `ROUND_DOWN_TO(va_size, os_page_size)` 只影响槽 31 的末尾，窗口起点都满足 16 KiB 对齐。
- 空隙不进任何客户端的堆，用户态只看到自己的窗口。
- 4 GiB 回退：`aperture_=0x100000000`，`aperture>END_4G` 为假（`END_4G` 正是 `0x100000000`），`va_size=0xE0000000`，`va_end=0x100000000`，`NativeAdrenoClientWindowsFor` 走第二个分支，返回 4G 表。`inputBits()=32`，`NativeAdrenoClientWindowsEnd(4G)=0xFFFFE000` 通过。**16G 表不会被用到。**

### 4. 容量 262144

- `ArmSmmu4KRange` 真实大小 **40 字节**。【二进制】`insert` 里 `mov w10,#0x28`，`attachCommon` 里 `add x8,x5,x5,lsl #2` 后再左移 3 位。
- 内存：`ranges_` 从 5 MiB 变成 10 MiB，**多 5,242,880 字节，都是常驻内核内存**。别名暂存（`1<<19` 项，2 MiB）和 `mappings_`（`0x280000`）都没变。
- 【二进制】`bringUp` 里 `mov w4,#0x40000`、`mov w5,#0x40000`。`attachCommon` 是 `(capacity-1)>>18` 非零才拒绝，262144 正好通过。
- `IONewData`：`IOMallocArraySize` 只防乘法溢出 → `IOMalloc_internal(KHEAP_DATA_BUFFERS)` → `kalloc_large`。除了 `Z_NOFAIL`、`Z_NOWAIT`、地址位数这几个判断没有大小上限，这次也不带 `Z_KALLOC_ARRAY`。
- S3 能否分配到：是不连续的虚拟连续分配，排在 1024 MiB 连续池和 32 MiB 页表之后。该路径以前分配 5 MiB 成功。
- 失败路径：`attachCommon` 返回 `kIOReturnNoMemory`，已分配的都释放，`bringUp` 带错返回，S3 失败。这里没有“退回较小容量”的路，但也没有证据会走到。
- 尾部临时区：`preflight` 保证 `spanCount()+n<=capacity_`，`changes=ranges_+spanCount()` 不出界。`insert` 要 `capacity_-count-n>=n` 才走 memcpy 分支，否则走三次翻转，都不出界。
- 别名暂存：比较的是较小一侧，最多 262144，小于 524288。`preflight` 和 `alias_pass` 都先比较再用。
- 32 位截断：`order[i]=(uint32_t)i`、`h[k++]` 都在范围内。`report->spans` 是 `uint32_t`，只记 S3 的 6 个缓冲。
- 别处写死：`NativeAdrenoClientContiguousMaxBytes=131072` 是 128 KiB 的 BO 大小，无关。`NativeAdrenoGmuContext.cpp:53` 的 262144 是轮询预算。`工具/gpu-test-loop`、板级代码没有引用这个容量。`clientMemoryForType` 的 `MaxSpans=65536` 早就小于旧容量，这次没有变化。
- memmove 成本：上界是 10 MiB。槽 0 的地址最低，它的映射和释放要搬动后面所有窗口的区段，所以它付得最多。这个代价要到区段真的超过 131072 才出现，而那以前是直接拒绝。

### 5. `stageMetal` 的第二次注记

- 作用域：`adreno830_start` → `traceOpen`（`traceDepth` 加 1）→ `runStages` → `stageMetal` → `traceNote` → 回来后才 `traceClose`。`traceLive()` 为真。`pagani-gpu-trace` 默认 1。启动线程不在中断上下文，也没有 `TraceQuiet`，`traceHere()` 为真。
- `d` 就是 `&g830`。`gpu_report` 只在 S3 的 `bringUp` 开头清零，`table_pages` 在退回 4 GiB 之后才写，所以是实际值。`stageMetal` 每次开机只跑一次。
- 位置在 `d->published=true` 之后、`metal_commit` 之前。这时加速器还没注册，没有用户连接。写一行走 `TraceLock` 关中断，和 S9 前后的 trace 行同路径。
- `pagani-gpu-trace=0` 或没有 ramoops 时直接返回，没有副作用。
- 这行能不能留下：三轮日志（`20261031-221300`、`20261031-223924`、`20261101-053955`）的 `g9 METAL begin/end` 都在（seq 约 1962–1966）。这一段前面的缺失到 1517 为止，后面的缺失从 2151 才开始。注记就夹在这两行之间。

### 6. `smmuFaultSink`

- `drainFaults` **只有一个调用者**：`smmuFaultWork`（`Adreno830Start.cpp:851`）。
- `smmuFaultWork` 是 `kCallAllocate` 建的 thread call，`THREAD_CALL_PRIORITY_KERNEL_HIGH` 加 `THREAD_CALL_OPTIONS_ONCE`，不会并发，`fault_lines` 没有竞争。
- 入队路径：
  - 中断：`contextIrq` → `recordFault` → `fault_notify` → `smmuFaultNotify` → `thread_call_enter`。中断上下文只入队，**不会调到 sink**。
  - 轮询：`poll()`（`dcvsPollFaults` 的 DCVS 线程，以及 S6 的 `cpRecordSticky`）→ `recordFault` → 同一个 notify。
- `boot_progress_trace` 在这里合法：`lock_log(0)` 关中断、有界自旋，失败就丢行，不阻塞。
- 行长最多 90 字符，缓冲 92 字节，`put` 按 `at+1<92` 判断，末尾写 0 不越界（【二进制】比较值是 `#0x5b`）。
- 写日志在 `native_adreno_note_page_fault` 返回之后。那里拿的 Ring 锁和 `g.lock` 是先后拿，不嵌套，等待有界。
- 门控：两张阶段 1.5 镜像都带 `pagani-gpu-status=3`、`pagani-diag=1`、`pagani-ramoops=1`，这行会写。阶段 1 全量轮 `pf 0/0/0/0`，CB0 开机没有良性故障吃掉 8 行额度。CB2 的故障也占这 8 行，它的计数只在 S3 诊断页上，运行日志里没有，这一点没有核对。

### 7. 反汇编确认

| 项 | 地址 | 内容 |
|---|---|---|
| `smmuFaultSink` 调日志 | `0xfffffff00c02426c` | `bl _boot_progress_trace`（强定义，不是弱空函数），之后 `fault_lines` 加 1（`[x20,#0x150]`） |
| 额度与门控 | `0xfffffff00c023d6c`、`0xfffffff00c023d74` | `cmp w8,#0x7`、`bl adreno830_status_diag_lines` |
| 先归责 | `0xfffffff00c023d60` | `bl _native_adreno_note_page_fault` |
| 下半部 | `0xfffffff00c023150` | `smmuFaultWork` 调 `drainFaults` |
| S9 注记 | `0xfffffff00c020fe4` | `native_adreno_publish`（`...f68`）之后 `bl traceNote`，`x0` 指向 `gpu aperture GiB, table pages`，`lsr x2,x8,#30`，`w1=2` |
| S3 注记 | `0xfffffff00c01fabc` | 同一字符串，保留 |

## 未验证

- A830 的 SQE、UCHE、SP 实际访问 `0x100000000` 以上，包括基址低 32 位为 0 的情况：代码里找不到问题，只有 Linux 和上游 A740 同一起点的旁证，本机没有实测。
- GMEM 范围的硬件比较是否用了全部地址位：`A8XX_UCHE_CCHE_GC_GMEM_RANGE_MIN`、`A8XX_SP_HLSQ_GC_GMEM_RANGE_MIN` 都按 64 位写，槽 5 里的 `0x104000000–0x104C00000` 低 32 位与 GMEM 窗口相同。上游没有因此出问题，但硬件行为没有依据。
- `vmr 37` 到底来自对象还是区段（见 D1）。
- memmove 和多一行日志的实际耗时没有测量。
- 用户态结论来自 `build-userspace/tree/.cache` 的源码，没有逐字节对照种子里的二进制。

## 顺带（不影响上机，不算发现）

- `docs/ARCHITECTURE.md:383` 的旋钮表还写着 `27×448 MiB + 约 704 MiB（0x2BFFE000）`，第 1199 行已是新表。
- `NativeAdrenoPublish.h:23-25` 的注释还说窗口合起来就是客户端范围，现在有三段空隙。
