# 阶段 1.5 上机前审查：GPU 16 GiB / 32 窗口与调频参数

只读审查，没有改代码，没有碰手机，没有编译内核。实际启动参数以 `runs/20261101-053955-stage15-smoke-1/merged.log` 第 6 行为准，里面有 `pagani-gpu-profile=0`，所以真机走的是**设计配置**：CB0 的 TCR 是 T0SZ=30（`0xB59E351E`），不是 annibale 的 T0SZ=25；TLB 失效走 64 位 TLBIVAL，映射后不做 TLB 操作。

## 结论

- 没有找到 crash 或 hang 级的缺陷。沿真实调用顺序核对了从 S3 建表到 BoMap、提交、页故障归属的整条路，内核和用户态都没有把 GPU 地址存进 32 位变量，PM4 包的高 32 位都写了。
- 调频没有除零、溢出，恒定负载下没有振荡。
- 回退开关 `pagani-gpu-va=4` 有效，但命令行放不下，要先去掉一个参数。
- 有 4 条 minor。其中 G1、G2 是纯诊断缺口，不修的话，一旦 4 GiB 以上出问题，这次重启就白费了，建议上机前修。G3 是减少变量的建议，G4 是判据 5 大概率过不了的预警。

## 发现

### G1（minor）判据 1 的光圈注记在现有日志里看不到

- 位置：`/Users/xiaochoumao/unixnu-13t/Adreno830/Adreno830Start.cpp:1360`
- 路径：`stageSmmu` → `gpu_context.bringUp` 返回 → `traceNote("gpu aperture GiB, table pages",…)` → `boot_progress_trace` → 32 KiB 事件环。
- 证据：
  - 本次 smoke 的 `merged.log`：seq 829（1.537 s，S3 复位 CB0）之后直接跳到 seq 1038（1.959 s）。S3 后半段全在断档里，`missing seq` 为 830–1037、1042–1045、1050–1245、1250–1503。
  - 阶段 1 两轮断档位置相同（827–1034 等）。
  - 10-31 以来 7 份非空 `merged.log` 搜 `gpu aperture`，0 命中。这条注记从来没有留下来过。
  - S9 的 `g9 METAL begin`（seq 1965）留下来了，说明 S9 之后写的行能活。
- 影响：`stage15-gpu.md` 上机判据 1（`note gpu aperture GiB, table pages 10 2011`）无法判定。16 GiB 建表失败退回 4 GiB 时，日志里也没有直接证据。
- 主机测试抓不到的原因：`test_start`、`test_a830trace` 看的是写入接收端的行，不模拟 32 KiB 环回绕和 UFS 3 s 一轮的落盘时机。
- 最小修改：`stageMetal` 里 `native_adreno_publish` 成功后再写一次同一条 `traceNote`（第 1969 行之后），或把光圈 GiB 加进 `gpu meter` 行。
- 在修之前的替代证据：`gpu noslot … small x/4` 的 4 表示 16 GiB 表，`conn` 峰值超过 12，`gpu va past 4 GiB` 行。

### G2（minor）页故障的地址没有任何日志出口

- 位置：`Adreno830Start.cpp:842`（`smmuFaultSink`）、`NativeAdrenoPublish.cpp:1194-1212`、`A830Smmu.cpp:661-671`
- 路径：`contextIrq` 或 `poll` → `recordFault` 读全 64 位 FAR → `smmuFaultWork` → `drainFaults` → `smmuFaultSink` → `native_adreno_note_page_fault(far,…)`。这里只按窗口归属并计数，FAR 随后丢弃。
- 证据：
  - 未归属时只有 `native_adreno_fault_stats[5]=uint32_t(far)`，只存低 32 位，全仓库没有读者。
  - `gpu meter` 的 `pf` 只有四个计数。
  - 挂死快照的 `snapshot->smmuFar`（`Adreno830Start.cpp:2153`）也没有打印出口。
- 影响：判据 2 的 `pf` 一旦不为 0，日志答不出故障地址，也就分不清是高 32 位丢了（地址落在低 4 GiB）、越窗，还是页没映射，只能再重启一次。
- 主机测试抓不到的原因：测试只核对归属和计数；“真出故障时日志够不够用”要真出故障才暴露。
- 最小修改：在 `smmuFaultSink` 里加一行，受 `adreno830_status_diag_lines()` 门控，每次开机限 8 行，内容为 FAR、FSR、FSYNR0/1、SID、槽号。线程上下文，可以直接调 `boot_progress_trace`。

### G3（minor，未验证）槽 5、14、23 横跨 4 GiB 的整数倍，默认开机约 60 s 就可能碰到

- 位置：`NativeAdrenoPublish.h:54`（槽 5 `0xFC000000–0x118000000`）、`:56`（槽 14 跨 8 GiB）、`:58`（槽 23 跨 12 GiB）
- 路径（按阶段 1 `merged.log` 的打开顺序）：
  - backboardd 29.7 s → 槽 0
  - FitnessWidget 48.6 s（第 3572 行）→ 名单外小类 → 槽 4
  - WidgetRenderer_Default 53.5 s（第 3918 行）→ 大类 → 槽 1
  - PhotosPosterProvider 60.0 s（第 4255 行）→ 名单内小类 → `first_small` → 槽 5
  - Turnip 从窗口低端分配（`tu_knl_darwin.cc:303-304`），小窗口的堆是 448 MiB，metal2vulkan 的块是 16 MiB，用到约 64 MiB 就会出现一个横跨 `0x100000000` 的 BO。
- 为什么是风险：
  - 地址超过 4 GiB 本身有上游证据：Linux msm 和上游 Adreno740 d834731（`GpuClientVaStart=0x100000000`）都从 4 GiB 起分配，Turnip 的 darwin 后端和 metal2vulkan 在 A740 上跑过这个范围。
  - **横跨 4 GiB 线的单个 BO 没有证据**：上面两者都从整数边界起，极少碰到这种情况。
  - 代码里没有找到依赖“同一 BO 高 32 位相同”的地方，内核 `test_vm` 也测过横跨，但硬件和固件的行为查不到依据。
- 主机测试抓不到的原因：`test_vm` 只验证页表和 TLB 写入，不执行 GPU。
- 最小修改（只改内核的窗口表，不动用户态）：
  - 槽 5–13 自 `0x100000000` 起，槽 14–22 自 `0x200000000` 起，槽 23–31 自 `0x300000000` 起，每个仍是 `0x1C000000`。
  - 槽 31 的长度改为 `0x1FFFE000`，仍止于 `0x3FFFFE000`。
  - 留下三段 64 MiB 空隙（`0xFC000000`、`0x1FC000000`、`0x2FC000000`）。
  - 这样第一次过线的地址和 Linux 的起点完全一样。同步改 `test_userclient.cpp:382-393`、`test_vm.cpp:458` 的说明和 ARCHITECTURE 6.2。
- 不改的话：`gpu va past 4 GiB` 行里 `iova` 小于 4 GiB、`end` 大于 4 GiB，就是横跨 BO；如果紧接着出页故障或挂死，先怀疑这一条。

### G4（minor，机制已读码，影响未验证）窗口变大后块变大，缓冲池和页表区段会更早用完

- 位置：`UniXNU/drivers/AdrenoMetal/bridge/patches/0018-pooled-blocks-fit-the-heap.patch`（`block_bytes`）、`tu_device.cc:2108-2109`、`NativeAdrenoGpuContext.cpp:98`、`NativeAdrenoVm.h:37`、`NativeAdrenoPublish.h:305`
- 路径：`NativeAdrenoUserClient::deviceInfo` 返回窗口 → Turnip 的堆取 `min(内存, va_size)` → `block_bytes` 取堆的 1/16 再向下取 2 的幂。

| 客户端 | 4 GiB 表窗口 / 块 | 16 GiB 表窗口 / 块 |
|---|---|---|
| backboardd | 1.5 GiB / 64 MiB | 不变 |
| 大类（槽 1–3） | 352 MiB / 16 MiB | 512 MiB / 32 MiB |
| 小类（槽 4–31） | 124 MiB / 4 MiB | 448 MiB / 16 MiB |

- 证据：
  - 0018 补丁自己写明“about eight blocks may stand while the program holds little”。同样 12 个客户端，最坏的空块从约 640 MiB 涨到约 1792 MiB。
  - 非渲染客户端能用的约为 768 MiB 池（1024 留 1/4）加 98304 个区段（384 MiB）。
  - 阶段 1 在 12 个客户端时已经碰顶：第 11243 行 `conn 0/12 … vmr 37 … cs 0/0/32`；第 8331 行 8 个客户端时还没有 `cs`。
  - 窗口数也从 12 变成 32，能进来的 App 更多。
- 影响：
  - 默认桌面不受影响，只有 4 个连接。
  - 开很多 App 时，`vmr` 和 `cs` 第三个数会比阶段 1 更早出现，判据 5 大概率过不了。
  - 被拒的映射在 Turnip 里变成 `VK_ERROR_OUT_OF_DEVICE_MEMORY`，受影响的是那个 App，不是 backboardd（渲染服务器保留额度还在）。
- 主机测试抓不到的原因：内核测试没有 Metal 运行时，桥接测试只算块大小，没有整机账本。
- 可选修改：
  - 把 `NativeAdrenoGpuContext.cpp:98` 的 131072 改成 262144（`NativeAdrenoVm.cpp:118` 允许的上限，别名暂存 `1<<19` 够用，多占约 5 MiB），只改内核。
  - 或把 `HEAP_SHARE` 调大，这要重做插件和种子。
  - 或者接受现状，放宽判据 5：连接数更多时 `vmr` 上升不算回退。

## 逐项核对结果

### 页表与 SMMU

- 层级：输入 34 位时 `(34-12+8)/9=3` 级，从 L1 开始，根 16 项，L1 索引 `VA[33:30]`（`ArmSmmu4K.c`、`ArmSmmu4KLive.c:23,216`）。T0SZ=30 按规范也从 L1 开始。
- 页数：`layout()` 算出 1+16+8192=8209，等于 `TablePages16G`，注记的 `10 2011` 是十六进制。
- 预分配：`NativeAdrenoGpuContext.cpp:56` 分配 `0x2011000` 字节，失败退 4 GiB，`aperture_`、`pages`、`inputBits()`、`info.va_size`、`g.windows` 全部一致。
- TCR：`0x8080351E` 经 `tcrWithUpper` 变成 `0xB59E351E`，字段逐位核对无误。回读检查按 `want` 比较，没有写死 `0xB5A03520`（该宏只在静态断言里）。
- IAS/OAS：编码器按 `IDR2.UBS=48` 检查输入，输出 36 位，`0x5511` 满足。
- `T0SZ` 被 CP 改写的担心可以排除：上游 `ArmSmmu4KContext.c:78` 记录 EPD1 只在 CP 执行 `CP_SMMU_TABLE_UPDATE` 时被清，其余 TCR 位严格比对；本驱动不发这个包（`NativeAdrenoRing.cpp:39-40`）。
- TLB：`ArmSmmu4KSyncContextTlbPages` 写 `VA[55:12]` 的 44 位，超过 512 页用 TLBIASID，高位没有截断；设计配置的 `tableWrite64` 直通。
- 32 位截断：`GpuApertureBytes16G` 在 `enum : uint64_t` 里，窗口表全是 `ull`，`1u<<width` 的 width 最大 9，没有 `1<<32`。
- `A8XX` 没有 `*_ADDR_MODE_CNTL`：Mesa `a6xx.xml` 里这组寄存器全是 `variants="A6XX"`，不需要打开 64 位寻址。UCHE 陷阱地址 `0x1FFFFFFFFF000` 在 34 位之外，静态断言在 `A830Regs.h:1566`。
- 已编内核核对：`kernel.macho`（05:46）含 `gpu va past 4 GiB`、`gpu sec rs`、`gpu aperture GiB`；`PaganiGpuBoard.o`（05:45）偏移 0x404 处是 `mov w8,#0x10`，默认值确实是 16。

### PM4 高 32 位

全部发包点都写了高位：`NativeAdrenoSubmit.cpp:155-158`（IB）、`NativeAdrenoRing.cpp:162,194,214-216`（等待、事件）、`A830Cp.cpp:570-571`（CP_ME_INIT）、`A830SelfTest.cpp:51,63,73,99`。`CP_RB_BASE`、`RPTR_ADDR`、微码基址都用 `write64`。

### 用户态

- `tu_knl_darwin.cc`：`darwin_allocate_iova`、`darwin_bo_init`、`NativeAdrenoAbiBoMap`、`darwin_submit_add_entries` 全程 `uint64_t`。
- Turnip 其余部分：按 `iova`、`addr`、`va` 搜 32 位写法，没有命中；描述符用 `BASE_HI`。上游 msm 本来就在 4 GiB 以上运行。
- 项目自己的 Mesa 补丁里没有地址运算。
- metal2vulkan：应用看到的 Metal 地址本来就在 `[2^32, 2^47)`，真实设备地址只进根表的 `host: u64`（`instantiate.rs:292`，`roots.rs` 全是 64 位运算）。`live_buffers.rs`、`residency.rs`、`binding.rs` 没有截断；唯一的 `as u32` 是 `instantiate.rs:443` 的绑定偏移余数，和地址无关。开发环境里设备地址本来就很高。
- 着色器缓存：Turnip 内建包和 pipeline cache 的标识不含 `va`；`DeviceProfile` 指纹不含堆大小（`description.rs:70-77`），窗口变化不会让缓存失效。
- 用户已确认的路径：backboardd、WidgetRenderer_Default、PosterBoard、iconservicesagent、FitnessWidget 都在 `0x20000000–0xFC000000`，仍低于 4 GiB。第一个过线的是 PhotosPosterProvider（G3），其后每个 App 都在 4 GiB 以上。

### 12 变 32 窗口与诊断行

- `NativeAdrenoClientSlotFor`、`setScope`（要 `input_bits=34`，`inputBits()` 已传）、`1u<<(job.queue/8)`（最大 31）、`jobs_[44]`、`native_adreno_client_stats[33]`：都不越界。
- `gpu noslot`：`NativeAdrenoSlotLine` 每次写入都查 720 字节上限，`holders` 在 `g.lock` 下取，`box->name` 的寿命由槽位保证。限流无误。
- `gpu va past 4 GiB`：`input` 在 `locked()` 返回后仍有效，长度已由 `dispatch` 校验；邮箱路径的 `input` 是本地 buffer；`g.past_4g_noted` 和 `g.slot_line` 都在 `g.lock` 下；调用时连接锁已放开，没有锁序反转。
- `takeCalls`、`quiet_s`：只在 `native_adreno_client_tick` 的 `g.lock` 下动，连接从槽摘除也在 `g.lock` 下。

### system_diag

- 解析：`pagani_gpu_start` 线程上下文 → `PE_parse_boot_argn`，`system_diag` 在结构体末尾，不影响旋钮表和静态断言。胶水 05:45 重编过。
- `adreno830_status_diag` 在 `adreno830_status_reset` 之后调用，复位不清开关。
- `boot_progress_trace` 关中断加自旋短临界区，长行拆成 200 字节一段，没有固定大小的栈缓冲，在 `g.lock` 下调用合法。
- `adreno830_status_diag_lines` 只读三个标志，放在状态自旋锁里也安全。`secondLine` 和 `meterLine` 都经 `Row` 限长，`Adreno830StatusMeterBytes=320`。

### 调频

- 除零：`targetBusyPermille` 在 `init` 里拒绝 0，`span` 有 `now>lastSample_` 保护，`abQuantize` 有 `topKbps` 保护。
- 溢出：`demand` 最大约 `1.47e6` kHz，乘 1250 后仍在 32 位内；`demand*1000` 是 64 位。
- 振荡：`/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/preflight-gpu/dcvs_model.py` 按 `decide()` 建模，工作量 20–1100 MHz、4 个起始档、每组 4000 窗，0 个极限环。上升要 D>f(p-1)，下降要 D≤0.85·f(p-1)，迟滞 15%；734 MHz 以上下降不留余量，但上升被 `limit` 卡住，也不会来回跳。
- 模型只验证恒定负载，突发和帧余量以 `test_a830dcvs` 为准。

### 回退开关

- 路径有效：`bootArgument` → `PE_parse_boot_argn` → `knobs.va=4` → `validateBoard` 接受 → `aperture_gib=4` → 4 GiB 表 → `info.va_size` 取 `END_4G-BASE` → `NativeAdrenoClientWindowsFor` 返回 4G 表 → 用户态自动跟随。
- 命令行：首测 996 字节，完整包 998 字节，加 `pagani-gpu-va=4` 要 16 字节，超过 1000，`payload.py:35` 会直接报错，不会悄悄截断。
- 用法：同时去掉一个至少 14 字节的参数，比如从 BASE 里删 `pagani-kbtrace=1`。

## 残余风险（没有证据，不算发现）

- 写合并撕裂不再被掩盖：`turnip-a830-fixes.patch` 记录过别的 gen8 机器上 CP 读到 IB 的低位和长度、读不到高位。以前高 32 位恒为 0，撕裂看不出来；现在槽 5 以上不为 0。`tu_EndCommandBuffer` 和 `darwin_submit_chunk` 的 `dsb sy` 都在产物里。编码线程和提交线程不同的客户端，以及编译线程写的管线状态，没有单独的屏障，未验证。
- 16 GiB 冒烟（10-31 00:00）只知道没有 panic，日志全丢，S3 在 T0SZ=30 下通过这件事没有真机证据。自动退回 4 GiB 只发生在页表分配失败时（`NativeAdrenoGpuContext.cpp:57`），S3 其他步骤失败不会退回，GPU 这次开机就起不来。
- 硬件层面（SQE、UCHE、SP 在这颗固件上访问 4 GiB 以上）只有 Linux 和上游 A740 的旁证，没有本机实测。

## 建议顺序

1. 修 G1、G2：只动内核日志，风险最低。
2. 决定是否做 G3 的窗口表调整，它能去掉“横跨 BO”这个变量。
3. 判据 5 按 G4 调整预期。
