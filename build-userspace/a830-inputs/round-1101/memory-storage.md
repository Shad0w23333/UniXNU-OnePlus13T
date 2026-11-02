# RAM、存储、装应用：查实结果与接近日用方案

日期：2026-11-01。对象：运行 `20261101-110201-stage15-smoke-1`，内核 `75e75d4e`，种子 `s6-final`。

证据等级：【实测】我跑了命令或读了真机日志；【读码】读源码或反汇编；【推断】由事实推出，可能错。

本报告只读：没有改源码、脚本、种子、镜像，没有重启手机，没有写分区，没有下载，没有提交。

---

## 0. 结论

### 0.1 用户的三个问题

| 问题 | 答案 |
| --- | --- |
| RAM 够不够 | 手机是 **16 GiB**，XNU 只用 **6 GiB**。这 6 GiB 里，GPU 池 1 GiB 和 Data 内存盘约 1.2 GiB 共占 36%，真正留给应用和文件缓存的约 3.4 GiB。系统能跑，但 `avail` 掉到 921.6 MiB 就开始杀进程，日用不够 |
| ROM 够不够 | 不够。可写空间只有一块 **2 GiB 的内存盘**，开机拷入后只剩约 1.5 GiB，用 7 分钟后剩 856 MiB，重启全丢。闪存本身没问题：512 GB，`unixnu` 分区里还有约 **53 GiB** 空闲 |
| 能不能装 App | App Store **做不到**（缺 FairPlay、激活、SEP）。自签 IPA 走 `installd` 现在也走不通。**现在能走的路**是把解密过的应用重签 ad-hoc，CDHash 写进 owner 信任包，预置进镜像，已有机制可复用 |

### 0.2 这轮查出的、改变判断的事

1. **压缩器确实没开，不是统计问题。** 【实测+读码】我们的 ADT 少了 `kern.vm_compressor`，内核回落到模式 8，`compressor_is_active` 是假。匿名页根本不能被回收，只能等 jetsam 杀进程。参考真机 D47 的值是 32。改一行 ADT 就能开。
2. **文档里的“12 GiB、2+4+6、连续 9 GiB”不属于这台手机。** 【实测】那是上游作者 256 GB 手机的数据。这台是 **2+6+8 共 16 GiB，`0x8c0000000` 起连续 13 GiB**。`功能盘点.md` 说的“要跨段、要改内存映射”也不成立：现在的窗口已经越过 `0xa00000000`，跑得好好的。
3. **直接把窗口改成 8 GiB 会黑屏死循环。** 【读码】`native-4k-bootstrap-capacity.patch` 把引导页表写死成 16 页，8 GiB 要 19 页。超了之后内核在开 MMU 前原地空转，没有日志，也没有看门狗。10-29 做过一批 8G 样板，我解包 `boot-R8` 核对过，它的内核也是 16 页。项目里找不到这批样板任何一次成功记录，很可能就是这个原因【推断】。
4. **启动参数只剩 4 字节。** 【实测+读码】996/1000（`payload.py:35`）。下一包的新设置必须走 ADT，或者先删测试参数。
5. **持久化的默认窗口会盖到不该碰的数据。** 【实测+读码】默认预留 1024 MiB，store 窗口顶在 64512 MiB，而非零的残留数据从约 64160 MiB 就开始了。必须把 `sheng-log-reserve` 加大。
6. **内存盘不能扩成 8 GiB 稀疏。** 【读码】它的页来自 `IOMallocZero`，算在 zone 上限里（6 GiB 窗口下约 2.2 GiB），而且没有 UNMAP，占用只涨不跌。Data 接近写满之前，系统会先撞 zone 耗尽。
7. **设置里两个容量读的不是同一个卷。** 【读码】“关于本机”读 `/private/var`（2 GB），“储存空间”页读 `/`（OS 容器 6.08 GB，会显示约“已用 5.7 GB / 共 6 GB”）。用户说的“4 G”两边都对不上。

### 0.3 下一包推荐做的四件事（不写闪存）

| 项 | 改什么 | 预期 | 风险 |
| --- | --- | --- | --- |
| 开压缩器 | `board.toml` 的 `[adt.defaults.u32]` 加 `"kern.vm_compressor" = 2` | 匿名页可回收，省几百 MiB，少杀进程 | 这条路径在本机从没跑过 |
| 窗口 8 GiB | 引导页表改 34 页 + `owned_memory_size = "0x200000000"` | 多 2 GiB，压力线只升 61 MiB | 忘改页表就黑屏 |
| 预装应用改只读视图 | 在挂 Data 之前把根卷的 `staged_system_apps` 绑到 `/Applications`，不再拷进内存盘 | 省约 450 MiB RAM 和 2.7 s | 改挂载顺序 |
| 加 `M mem` 记录 | 记 wired、匿名、压缩、zone、内存盘驻留、GPU 池峰值 | 下一轮能把账算到底 | 无 |

合起来估计空闲态 `avail` 从约 1.7 GiB 升到 4 GiB 以上【推断，上机验证】。持久化写闪存，要用户单独授权，放到后面（第 7 节）。

---

## A. RAM

### 1. 手机物理内存到底多少：16 GiB

| 证据 | 值 | 等级 |
| --- | --- | --- |
| `/proc/meminfo` `MemTotal` | 15470544 kB（14.75 GiB） | 【实测】adb，序列号 `SERIAL_13T` |
| 运行设备树 `ddr-regions` | region0 `0x80000000`+2 GiB，region1 `0x880000000`+6 GiB，region2 `0xa00000000`+8 GiB，共 16 GiB | 【实测】`dt/running.dts` |
| 设备树 `memory` 节点 `reg` 合计 | 15.13 GiB（已扣固件区） | 【实测】解析 |
| 同上，相邻区段合并后 | `0x8c0000000`–`0xc00000000` 连续 **13 GiB** | 【实测】解析 |
| `/proc/iomem` | `8c0000000-bffffefff : System RAM` 一整段 | 【实测】root 只读 |
| `/proc/cmdline` | `device_info.ddr_info=6\|8\|0\|6\|16` | 【实测】末位 16 是容量，含义是【推断】 |

**旧说法要改：**

- `STAGE6.md:897`、`:899`、`:1057`，`porting.toml:153`：“12 GiB，2+4+6，连续 9 GiB”。这是上游作者那台 12 GB 手机。第 1057 行自己也写了“用户提供的 12 GB”。【读码】
- `功能盘点.md:187`：“`0xa00000000` 起 8 GiB 没用上……XNU 只认一段连续物理内存，要改内存映射”。不对：region1 和 region2 首尾相接。
- 本任务书的“0x8c0000000 起连续 9 GiB”同样来自上游。

**窗口早就跨过分界了。** 【实测】日志头 `xnu ram=0x8c007c000+0x180000000`，窗口结束于 `0xa4007c000`，已越过 `0xa00000000` 1 GiB。第 33 秒有一行 `audio clock ... pa=a05302908`，说明那段内存在被真实使用。这证明两段在 UEFI 里连成一片，且高段可用。

### 2. 6 GiB 窗口的账

总页数验证：`1499090 free + 73774 wired = 1572864`，正好 6 GiB/4 KiB，所以内核是 4 KiB 页。【实测】

三个时刻的账，单位 MiB，合计都是 6143：

| 项 | 第 15 秒 | 第 214 秒（压力峰） | 第 275 秒（空闲） | 来源 |
| --- | --- | --- | --- | --- |
| 启动占用 | 288 | 288 | 288 | 【实测】`73774 wired pages` |
| GPU 池 | 1024 | 1024 | 1024 | 【实测】`pagani-gpu-pool=1024`，日志 `MEM pool 1024` |
| Data 内存盘（已用，是驻留的下限） | 519 | 1178 | 1165 | 【实测】`2048 - staged apps watch free` |
| 其余常驻（内核、zone、页表、栈、匿名页、GPU 散页） | 280 | 2625 | 1946 | 倒算 |
| 文件缓存 + 可清除 | 1662 | 1028 | 966 | 【实测】`avail - free` |
| 空闲 | 2370 | 0 | 754 | 【实测】`M io free` |

能再细分的：

- **启动占用 288 MiB**：内核映像约 138 MiB【实测，段表】，打包种子 55 MiB【实测，`NativeRamBlock.cpp:379` 注释说不释放】，其余约 95 MiB 是页数组等 VM 表【推断】。
- **GPU 页表** 32 MiB：日志 `gpu aperture GiB, table pages 10 2011`，即 16 GiB 光圈、0x2011 页。【实测】
- **内核基线** 约 280 MiB：第 15 秒只有约 30 个进程时的“其余常驻”，已含上面的 GPU 页表。【推断】
- **内存盘页表** 4 MiB（2 GiB/4 KiB × 8 字节）。【读码】
- **Spotlight 一个进程** 949 MiB（第 181 秒被杀时）、725 MiB（第 216 秒）。【实测】`msgbuf.txt`

算不出的：zone 实际用量、各进程匿名页、进程页表、GPU 散页。日志没记这些，所以“其余常驻”只能给总数。`kt_fixed_zones=240` 只是类型隔离的 zone 个数，不是一笔内存预留。【读码】`kalloc.c:450`。“compressor 6G”“zones 24G”是虚拟地址，不占物理内存。

Data 占用的增长：种子 53 MiB → 开机拷预装应用 445 MiB（解压后，根卷上压缩存放只有 231 MiB）→ 第 15 秒 519 MiB → 第 395 秒 1192 MiB。这是 RAM，不可回收。而且 `NativeRamBlock` 不支持 UNMAP，APFS 是写时复制，所以真实驻留只会比“已用”更高，最终趋向 2 GiB【读码+推断】。

**省内存最便宜的一招：** 预装应用现在先拷进内存盘，再绑到 `/Applications`。当初拷贝是为了让迁移流程安装它们，后来安装没走通，改成了视图，拷贝已经没有必要。`native_owner_data_prepare` 里只要在 `mount` Data 之前，把根卷上还看得见的 `/private/var/staged_system_apps` 用现成的 `native_gpu_addon_mount` 绑到 `/Applications`，就能省掉这 445 MiB 和 2.7 s。【读码】移除迁移哨兵的动作仍要放在 Data 挂载之后。根卷的文件是压缩存放的，经 bindfs 读取是否一切正常，要上机确认。【推断】`jitter.md` 的 F6 也提了同一件事。

### “4 G”最可能是哪来的

| 候选 | 值 | 用户看得到吗 | 判断 |
| --- | --- | --- | --- |
| 关于本机 > 容量 | **2 GB** | 能 | 对不上。【读码】算法见第 6 节，且与用户 16:25 说的“容量 2 GB、可用 1 GB”一致 |
| iPhone 储存空间页 | **约 5.7 / 6 GB** | 能 | 对不上。【读码推算】没人截过图 |
| 第 3 秒 `free` 4139 MiB、`avail` 4466 MiB | 约 4 GiB | **看不到**，只在日志里，本轮 `pagani-overlay=0` | 除非有人转述 |
| 屏幕诊断行 `RAM MiB=... free=... avail=...` | 旧包是 `RAM MiB=4096` | 只有开了诊断文字的旧镜像能看到 | **最可能之一** |
| 10-29 到 10-30 的 4 GiB 窗口 | 4 GiB | 进过旧汇报、`board.toml` 注释 | **最可能之一** |

所以“4 G”大概率是旧窗口的记忆或转述，不是现在系统里的数。【推断】这一点我不确定。判断办法很简单：问用户在哪看到的，并截两张图：设置 > 通用 > 关于本机，设置 > 通用 > iPhone 储存空间。如果设置里真显示 4，就说明我的静态推算错了，要重查。

### 3. 压缩器为什么 cmp 0 dcmp 0：真没开

**统计是真的。** `M io` 的 `cmp`、`dcmp` 读 `vm_statistics_compressions`、`vm_statistics_decompressions`（`phone-stall-probe.patch:983-984`），是内核原始计数。全程 359 秒合计 cmp 1、dcmp 1，都在第 210.4 秒。【实测】

**链路：**

1. 日志第 74-75 行：`"vm_compressor_mode" is 8`，`mapping deprecated VM_PAGER_FREEZER_DEFAULT to VM_PAGER_FREEZER_COMPRESSOR_NO_SWAP`。【实测】
2. 取值顺序：启动参数 `vm_compressor` → ADT `/defaults` 的 `kern.vm_compressor` → 编译默认值（`vm_fault.c:286-309`）。iOS 带 `CONFIG_FREEZE` 时默认值是 8（`vm_compressor.c:156`）。【读码】
3. 模式 8 映射到的分支只设 `compressor_is_present` 和 `swap_is_present`，**不设 `compressor_is_active`**（`vm_pageout.c:5046-5053`）。【读码】
4. 我们的启动参数没有 `vm_compressor`。ADT `/defaults` 里只有 `kern.maxvnodes=66560` 和 `sleep-disabled=1`。【实测】解析 `payload.img`
5. 参考真机 D47 的 ADT 有 `kern.vm_compressor=32`，即 `0x20`，压缩器加带交换的冻结器。【实测】解析 `DeviceTree.d47ap.bin`。J120 自己的 ADT 不在本机。

**后果比“少省点内存”严重。** 【读码】`VM_DYNAMIC_PAGING_ENABLED()` 就等于 `compressor_is_active`。为假时，脏的匿名页进 `vm_page_queue_throttled`（`vm_resident.c:5100-5107`、`vm_pageout.c:3649-3658`、`vm_fault.c:914`），pageout 根本不看它们。系统能回收的只有文件页，而回收文件页就要重新读根卷。这和 `jitter.md` 里“页入最能预测卡顿”对得上【推断】。

**`avail` 的含义**：`memorystatus_available_pages` = 文件页 + 空闲 + 超额隐藏页 +（没开动态换页时）可清除页（`vm_page.h:1645-1651`）。不含匿名页。【读码】

**开了的收益与风险：**

| | 内容 | 等级 |
| --- | --- | --- |
| 收益 | 不活跃的匿名页压缩后变成空闲页，省几百 MiB（按“其余常驻”近 2 GiB 估算，幅度没有实测） | 【推断】 |
| 收益 | 回收不再只能牺牲代码页，少重读根卷，少卡顿 | 【推断】 |
| 收益 | jetsam 少杀进程 | 【推断】 |
| 风险 | 内核路径在这台机器从未跑过。只跑过一次压缩（第二次杀 Spotlight 时 `compressor_size:4`） | 【实测】 |
| 风险 | 使用时解压带来新的缺页延迟。这是 iOS 常态，但 CPU 调频不同 | 【推断】 |
| 风险 | 新增 jetsam 原因：压缩空间不足、抖动杀进程 | 【读码】 |
| 风险 | 水位按页写死，在 4 KiB 内核上比真机小 4 倍，`AVAILABLE_NON_COMPRESSED_MIN` 也一样 | 【读码】 |
| 风险 | `avail` 读数会变低，因为可清除页不再计入 | 【读码】 |
| 无风险 | GPU 导入页由 `prepare()` 固定，不会被压走；内存盘页是 wired，也不受影响 | 【读码】 |

`WKdm_compress_4k`、`lz4raw_encode_buffer` 都已链接进内核。【实测】压缩子映射的 6 GiB 地址空间早已预留。

**用模式 2，不用 32。** 模式 2 是纯内存压缩。模式 32 会往 `/private/var/vm` 建交换文件，那在内存盘上没有意义。【推断】

**写在 ADT 里，不写启动参数。** `vm_compressor=2` 要 16 字节，命令行只剩 4 字节：

```toml
[adt.defaults.u32]
sleep-disabled = 1
"kern.vm_compressor" = 2
```

板级和 OS 配置的 `defaults` 表会合并（现有 ADT 里两边的键都在）。【实测】ADT 里还缺几项真机有的值，比如 `kern.max_task_pmem`（D47 为 3376 MB）、`kern.entitled_max_task_pmem`。现在单个进程没有系统级上限（`phone-gpu-client-memlimits.patch` 注释也这么写），可以后续再加。

### 4. free 接近 0 是否异常：大体正常，但这里缓冲太薄

水位（`vm_page_free_reserve`，`vm_pageout.c:3828` 起）：

| 水位 | 页数 | 这台（4 KiB） | 16 KiB 真机 |
| --- | --- | --- | --- |
| `vm_page_free_reserved` | 128（100+28） | 0.5 MiB | 2 MiB |
| `vm_page_free_min` | 1500（封顶） | 5.9 MiB | 24 MiB |
| `vm_page_free_target` | 2000（封顶） | 7.8 MiB | 32 MiB |

【读码】上限按页数写死，所以 4 KiB 内核的缓冲只有真机的四分之一。

- 全程 359 个 M 秒：`free<2000` 共 93 秒，`free<1500` 共 89 秒，`free<128` 共 4 秒，最低 127 页。【实测】
- 第 15 到 275 秒 `free` 和 `avail` 差很多：`free` 为 0 时 `avail` 还有 1.0–2.3 GB，那是文件缓存，不是真没内存。任务书的“free 经常接近 0”只占一部分时间，第 220 秒之后大多有 140–760 MiB。【实测】
- 但 `free` 碰到 127 说明普通线程已在 `vm_page_wait` 里等，pageout 那一刻没跟上。这部分主要由 Spotlight 跑飞造成（`jitter.md` 第 5 节也这么看）。
- 掉帧相关性：`free<2000` 的秒里 45% 单帧超过 200 ms，其余秒是 26%；超过 500 ms 时两边都是 13–14%。这些秒同时也是最忙的秒（中位缺页 11428 对 575），有混淆，只能说内存压力有贡献，不是主因。【实测+推断】

压力线才是真正的限制。第 6 节的公式算出 6 GiB 的 `pressure` 是 235927 页（921.6 MiB），日志两次触发时分别是 235824、235961。【实测】最低 `avail` 是 241221 页（942 MiB），只差 20 MiB。

可选改进：把 `VM_PAGE_FREE_MIN_LIMIT`、`VM_PAGE_FREE_TARGET_LIMIT` 按 4 KiB 放大 4 倍，让字节数和真机一致。这是内核一行改动，建议先看压缩器和 8 GiB 的效果再决定。

### 5. 扩内存

#### 推荐值：8 GiB，可以作为下一包的一项

| 项 | 6 GiB | 8 GiB | 12 GiB（`vm_compression_limit`=6.6 GiB 地址空间） |
| --- | --- | --- | --- |
| jetsam 档位 | 中档 | 大档 | 大档 |
| critical / idle / pressure | 246 / 553 / 922 MiB | 328 / 655 / 983 MiB | 492 / 983 / 1475 MiB |
| zone 物理上限 | 2231 MiB | 2998 MiB | 4534 MiB |
| kmem 指针 / 隔离 / 数据区 | 2644 / 2645 / 5546 MiB | 约 1571 / 1571 / 3141 MiB | 约 1112 / 1112 / 2224 MiB |
| 引导页表（页） | 15 | **19** | **27** |

【读码+计算】档位在 `kern_memorystatus.c:231-263`。8 GiB 比 6 GiB 多 2048 MiB，压力线只升 61 MiB，这是最划算的一点。

#### 要改什么

| 项 | 8 GiB | 说明 |
| --- | --- | --- |
| `UniXNU/patches/xnu/native-4k-bootstrap-capacity.patch` | **必须改**：`ARM_PGBYTES * 16` → `* 34` | 见下 |
| `board.toml` `owned_memory_size` | **必须改**：`"0x200000000"` | 这一项决定窗口大小 |
| `board.toml` `dram_size` + `dts/oneplus13t.dts` | 不必须，可同步 | `devicetree.py:306` 只校验这两者相等，不校验 `owned`；R8 样板就是 6 GiB 配 8 GiB 窗口 |
| DarwinBoot `WindowSize.h` | 不用改 | 上限正好 `0x200000000`，含等号 |
| UEFI 空闲图 | 不用改 | 见下 |
| Adreno830 | 不用改 | 地址由 `gPhysBase/gPhysSize` 动态判断；SMMU 输出位宽按硬件探测、封顶 40 位，窗口顶 `0xac007c000` 只要 36 位 |
| PaganiWifi `0xc0000000`、PaganiSpeaker `0xd0000000`、PaganiSensors | 不用改 | 都在 4 GiB 以下、XNU 窗口之外，判断是动态的 |
| ramoops `0x880000000` | 不用改 | 在窗口之下 |
| NativeUfs | 预计不用改 | 第 33 秒已有内核页在 `0xa05...`，但 UFS 的 DMA 缓冲具体落在哪没查；控制器按 64 位寻址【推断】 |
| `pagani-gpu-pool` | 不用改 | 见下 |

**引导页表是最大的坑。** 【读码+计算】`start.s` 的 `create_bootstrap_mapping` 给 V=P 和 KVA 两份映射各分 L2 页，每页管 1 GiB：

| 窗口 | V=P | KVA | 加 2 个根，共 | 上限 16 页 |
| --- | --- | --- | --- | --- |
| 6 GiB | 6 | 7 | 15 | 够 |
| 6.93 GiB（7100 MiB） | 7 | 7 | 16 | 正好 |
| 6.94 GiB | 7 | 8 | 17 | **超** |
| 8 GiB | 8 | 9 | **19** | **超** |
| 12 GiB | 12 | 13 | **27** | **超** |

超了之后走到 `Lbootstrap_tables_exhausted`，原地死循环。此时 MMU 没开，ramoops 没起，看门狗没装，屏幕停在 DarwinBoot 的最后一屏，只能长按重启。`arm_vm_init.c` 里那条 `BOOTSTRAP_TABLE_SIZE too small` 的检查拿页数比字节数，永远不触发。

**10-29 的 8G 样板能佐证。** `工具/board-R8-iPhone-8G-CPU满频.toml` 等配的是 `owned_memory_size = "0x200000000"`。我解包 `boot-R8` 确认窗口值确实是 8 GiB，内核里 `add ..., x25, #16, lsl #12` 也是 16 页。【实测】项目里找不到这些镜像跑成功的记录，后来板子又回到 4 GiB，再到 10-30 才升 6 GiB。所以这些样板不能算 8 GiB 验证过，反而很可能就是撞了这里。【推断】

**UEFI 空闲图。** 【读码】`RamManagerDxe` 把 XBL 的 RAM 分区逐个加进 GCD，DarwinBoot 的 `RunEnd` 会把相邻描述符连起来。现在窗口能跨 `0xa00000000` 就证明至少这一段连续。8 GiB 结束于 `0xac007c000`，离顶部 `0xc00000000` 还有 5 GiB 余量。【推断】UEFI 真实空闲图没有导出过，申请不到时 DarwinBoot 会在屏幕打印 `NO WINDOW` 并停下，不会坏。

**其余风险：**

- `/proc/iomem` 里 `0xa4...`–`0xac...` 全是 System RAM，没有固定 no-map 区。这是 Android 的视角，不是 UEFI 的。【实测】
- 内核 kmem 地址空间变小（`kmem_add_extra_claims`），指针区 2644→1571 MiB。够不够没有测量，日志里有 `kmem_ptr_range` 可看。【读码】
- 跨入 jetsam 大档后，critical、idle 的比例改变，影响很小。【读码】

#### 用满 12 GiB 要什么

**不需要跨段**，`0x8c0000000` 起有连续 13 GiB。需要的是：

1. `WindowSize.h`、`Test/window-size.c`、`devicetree.py:320` 三处上限，以及重建 UEFI 固件。
2. 引导页表 ≥27 页（34 页足够到 13 GiB）。
3. **必须限制压缩器地址空间**：`vm_compression_limit`（只有启动参数，要占命令行）。不限时 12 GiB 的 claims 超过可用地址空间（负 2.3 GiB），开不了机；10 GiB 时也只剩 2 GiB，数据区 986 MiB，放不下 1 GiB 的 GPU 池。【读码+计算】
4. 窗口上沿 `0xb8007c000` 离 Android 动态预留的 `0xbeb808000` 还有约 1.8 GiB，UEFI 顶部的占用没有实测。

建议先用 8 GiB，账算清楚再决定要不要 10–12 GiB。

#### GPU 池 1024 不算大

本轮 `MEM pool` 只记到第 30 秒（`used 0`），之后状态行换了内容，所以**本轮峰值没有数据**。【实测】历次运行：

| 运行 | `used` 峰值 MiB |
| --- | --- |
| `20261030-144045-m8-full-1` | **921** |
| `20261030-180230-bisect-A-old-d331p-1` | 815 |
| `20261030-154643-phone-pack-1` | 661 |
| `20261031-063106-pkgA-diag-cc-f2-1` | 615 |
| `20261030-213341-island-apps-1` | 552 |
| `20261031-003923-combined-input-kbd-vol-1` | 461 |
| 几次开机后没怎么操作的 | 324 |

【实测】921/1024 已经用到 90%，缩小会退回散页，而散页是之前 500 ms 停顿的来源。建议保持 1024，加日志看 `native_adreno_pool_stats[4]`（峰值）后再定。

---

## B. 存储

### 6. 设置里显示什么

**关于本机**（`-[PSGAboutDataSource _loadValues]`）【读码，反汇编】：

- 容量：`/private/var` 的 `NSURLVolumeTotalCapacityKey`，除以 1e9，四舍五入取整，再乘 1e9，用十进制格式显示。
- 可用容量：`NSURLVolumeAvailableCapacityForImportantUsageKey`。
- 2 GiB 容器：2147483648 / 1e9 = 2.147 → **“2 GB”**。这和用户当时看到的完全一致，反过来印证了这段解读。【实测+读码】

**iPhone 储存空间**（`StorageSettings.bundle` 的 `STStorageController`，用 `STStorageDiskMonitor`）【读码，反汇编】：

- `_GetDeviceSpace` 用 `getattrlist` 读 **`/`** 的 `ATTR_VOL_SIZE`、`ATTR_VOL_SPACEAVAIL`。
- 容量条：`setCapacity: deviceSize`，`deviceSize` 同样四舍五入到整数 GB；`setBytesUsed: usedBytes`，`used = total - (free + purgeable)`。
- `/` 是 OS 卷，容器 6077546496 字节，可用 359149568 字节。【实测】本机挂载 `osmnt-21H461`
- 所以预计显示：**共 6 GB，已用约 5.7 GB**（`purgeable` 越大，已用越小）。`isLowSpace` 也会是真（≤16 GB 的卷阈值 1 GB），可能出现存储不足提示。【推断，没有截图】

真机上 `/` 和 `/private/var` 在同一容器，所以 Apple 不区分。这里是两个容器，两页就对不上。这只影响显示，不影响安装和写入。要修可以在内核里让根卷的 `ATTR_VOL_SIZE` 报 Data 的值，优先级低。

**容量显示对照**（持久化后，关于本机页）：

| Data 大小 | 显示 |
| --- | --- |
| 2 GiB | 2 GB |
| 8 GiB | 9 GB |
| 16 GiB | 17 GB |
| 30 GiB | 32 GB |
| 32 GiB | 34 GB |
| 48 GiB | 52 GB |

### 7. 持久化方案

#### 现状

| 项 | 状态 | 等级 |
| --- | --- | --- |
| `PersistOverlay.h` | 已有：头、位图、页区，先写页再写位图，全程 FUA；种子一变就作废 | 【读码】 |
| `DirectDisk.h` | 已有：改过的页只在闪存，不进 RAM | 【读码】 |
| `SeedDisk.h` | 已有：大于 2 GiB 的盘只留种子的非零页，内存按内容算 | 【读码】 |
| `NativeUfs` store | 已有 `native_ufs_store_*`，`hostWrite4k` 带 FUA | 【读码】 |
| 写闸门 | 每次写都查窗口；**13T 内核编译时关着**：`build-native-ufs.py:30` 有 `-DNATIVE_UFS_READ_ONLY=1`，`hostStoreOpen` 直接返回 false，`hostLogCommand` 只放行无 FUA 的日志写 | 【读码】 |
| 上游验证 | sheng 板 `sheng-data-persist=2`、`persistent_data=true`；asteroids 记录 `Data persists across boots`，并写明没有 `sheng-aks` 时 SpringBoard 起不来 | 【读码】上游记录，不是本机 |
| 种子增长 | `UniXNU/scripts/seed-data-grow.py` 已有，保持卷 UUID | 【读码】 |
| GPU 插件脱离种子 | `GpuAddon` 机制已有（开机写进 Data，不改种子身份），当前没用（`gpu_addon_sha256: null`） | 【读码+实测】 |
| 本机写闪存实绩 | 只有 UFS 日志：本轮 156 轮、0 错误，最长命令 641 µs，WriteBooster 开 | 【实测】 |

`NativeUfs.cpp:1090` 注释说窗口“最多四分之一分区”，代码里其实没有这个检查，`功能盘点.md` 的“约 16 GiB”上限不准。【读码】

#### 空闲区实况

`unixnu` 是 64 GiB（`sda16`，起始 LBA 107409152）。【实测】只读采样：

| 区域（分区内） | 内容 |
| --- | --- |
| 0 – 8948 MiB | 根镜像（`userdata-root.json` 的 `image_bytes` 9382658048；任务书写的 9147 MiB 我没对上，哪个准不影响下面的结论） |
| 约 8948 – 64160 MiB | 采样全零（64 MiB 步长，64000 起 4 MiB 步长） |
| 约 64160 – 64512 MiB | **高熵残留数据，稀疏**，疑似旧的加密 userdata【推断】 |
| 64512 – 65248 MiB | 代码里的预留区，同样有残留 |
| 65248 – 65504 MiB | 导出区 |
| 65504 – 65536 MiB | 日志窗口 |

**坑：** 默认 `sheng-log-reserve=1024` 时，store 窗口的顶在 64512 MiB，正好压在 64160–64512 这段残留上。用户说过这片不能碰，所以必须加大预留。我建议 `sheng-log-reserve=2048`，窗口顶在 63488 MiB，离最低的非零采样还有 672 MiB。这只改 store 的位置，不改日志窗口。【读码】首次写之前，要在 Android 下对整个窗口做一次只读全扫描，采样不能代替。

#### 方案比较

| 方案 | 写入时机 | 崩溃后 | RAM | 改动 | 判断 |
| --- | --- | --- | --- | --- | --- |
| **A. 现有覆盖层直写** `sheng-data-persist=2` | 每次写 FUA | 已确认的写不丢 | 极低 | 小：开编译开关、长种子、改参数 | **推荐先做**，上游验证过 |
| B. 内存盘 + 关机落盘 | 只在关机 | 丢本次会话 | 高，仍受 2 GiB 限制 | 中 | 写入暴露最小，但落盘要做 A/B 提交，否则写一半时 APFS 新旧混杂会坏。现有格式是原地覆盖，不能直接用 |
| C. 真 APFS 裸窗口（`NativeUfsBlock` 可写，走 SYNC 屏障） | APFS 决定，带缓存 | APFS 崩溃一致 | 低 | 中大 | 性能最好，后续优化方向 |
| D. Android 锁定文件（`PaganiPersistentData`） | 同 A | 同 A | 低 | 中大，代码是提案 | 只有要超过 53 GiB 时才需要 |

#### 断电一致性

A 方案在逻辑上是够的：页写完才写位图，全程 FUA，一个 run 中途断电时位图不会指向没写完的页；APFS 自己写时复制，靠检查点恢复。【读码】`native_ufs_log_panic` 和 `ufs_log_quiesce` 在代码发起的复位前先拿 `gHost.lock`，store 写也走这把锁，所以**软件发起的复位不会打断写命令**。【读码】

软件管不到的只有三种：挂死后看门狗咬、长按电源、硬件复位。这时落到 `bDataReliability=0`。

#### `bDataReliability=0` 怎么看

- 全部 LUN 都是 0，型号 `KLUFG8RHHD-B0G1`，寿命估计 `0x01`（0–10%）。【实测】
- 这是规格层面的最坏说法：写入途中掉电，整个逻辑单元的数据都可能丢。LUN 0 同时有 `userdata`、`super`、`persist`。这个值是出厂配置，改不了。
- Android 平时也在同一个 LUN 上持续写，长按重启时也可能打断写入，所以这个风险在手机正常使用中一直存在。但 XNU 现在的异常复位比 Android 多得多，**暴露次数远高于日常**。实际丢失概率没有数据，我不能给数字。【推断】

能做的降险措施：

1. 窗口固定，闸门每次校验，这部分已有。
2. 只在“稳定包”开写，调试循环包（带 `pagani-reset-after` 的）一律不开。
3. 给 store 写加上和日志一样的电源键联动：`pagani_input_power_held` 按下时不开新写。这是**要新增的**，现有联动只管日志。
4. 写前核对 GPT，写后再核对（`native_ufs_store_gpt_check` 已有），不一致立刻停写。
5. 第一次失败即停写（`directFailed` 已有）。
6. 动手前备份。`一加13T原厂备份/分区镜像` 已有一部分，需要核对是否含 `persist`、`super` 等；`userdata` 用户自己备份重要资料。
7. 减少写量：预装应用改只读视图（少写 445 MiB），高频日志写限流。

#### 分阶段

| 阶段 | 做什么 | 写闪存 | 工作量 |
| --- | --- | --- | --- |
| P0 | 下一包不写：改只读视图；`M mem` 记录 `native_ram_block_resident_bytes`；用 PaganiDiag 列出 Data 里最大的目录，弄清那 673 MiB 写了什么 | 否 | 随下一包 |
| P1 | 只读探针 `sheng-data-persist=1` + `sheng-log-reserve=2048`：验证窗口位置、读头、GPT 核对 | 否 | 0.5 天，1 轮 |
| P2 | 冻结种子身份：把插件、壁纸等改走 `GpuAddon`。种子一变覆盖层就作废，用户数据全丢，这一步不做不能开写 | 否 | 1–2 天 |
| P3 | **需要用户授权**。8 GiB 窗口，开写：`-DNATIVE_UFS_READ_ONLY=0`（或新开只放行 store 的宏）、`persistent_data = true`、`seed-data-grow.py --gib 8 --reserve-mib 2048`、`sheng-data-persist=2`、电源键联动。测：连续 3 次开关机数据还在，装入文件、改设置、重启后还在；看写延迟对卡顿的影响 | 是 | 2–3 天，3–5 轮 |
| P4 | 稳定后扩到 32 GiB（换种子，数据重来），可用 30 GiB 让设置显示 32 GB | 是 | 0.5 天，2 轮 |
| P5 | 看性能再决定是否改方案 C | 是 | 1 周以上 |

P3 需要的启动参数要 44 字节，而命令行只剩 4 字节，要先删掉 `pagani-input-script=1`、`pagani-kbtrace=1` 这类测试参数，或者把 `payload.py` 的 1000 上限提到 DarwinBoot 实际的 1023。

`-apfs_shared_datavolume` 在 sheng 的启动参数里，我没查清它对我们的挂载路径是不是必须，P3 前要核对。写路径的性能也没有数据：`gate` 全程持锁，所有 Data I/O 串行，一次提交两条 FUA 命令，可能影响主线程。这要实测。

#### 给用户的授权写法

> 允许 XNU 写入 `unixnu` 分区内 LBA **121564863–123662079**（8 GiB 版，4 KiB 块）；扩到 32 GiB 时为 LBA **115273215–123662079**。
> 这段以外仍不写。所有写都经过内核闸门校验。
> 我知道：写入途中如果断电或长按重启，按规格最坏可能丢失整个 LUN 0（含 Android 的 userdata、super、persist）；实际概率未知。
> 我已备份 Android 重要资料。

现有授权只覆盖日志窗口和导出区，“不往手机存储写数据”的原则仍然有效（`PROGRESS.md:11`）。

#### 内存盘先扩到多大：不扩，保持 2 GiB

8 GiB 稀疏的想法不可行：【读码】

1. `logical > 0x80000000` 走 big 路径，没有读写 store 时写请求直接失败（`NativeRamBlock.cpp:276-301`）。`BigVolatile.h` 是提案，没合入。
2. 页来自 `IOMallocZero(4096)`，走 `KHEAP_DEFAULT`，算 zone。zone 物理上限是可用内存的 37.5%（6 GiB 约 2231 MiB，95% 时开始 jetsam），连内核自己的用量一起算。
3. 也就是说，**现在 2 GiB 都用不满**：Data 到约 1.6–1.8 GiB 就可能撞 zone 上限，杀进程也救不回来（内存盘页不属于任何进程），最后 `zone map exhausted`。【推断，没复现】
4. 没有 UNMAP，占用只涨不跌，容量声明大于 RAM 时迟早写失败，APFS 会出 I/O 错误。

可做的改进：给 `NativeRamBlock` 加 UNMAP（实现 `doUnmap` 并声明特性），让 APFS 释放的块还回 RAM。效果取决于 APFS 何时下发 TRIM，没有数据，可以和 P0 一起试。

---

## C. 装应用

### 8. 这系统算什么

| 项 | 值 | 等级 |
| --- | --- | --- |
| 根卷 | iPadOS 17.7.11 的 IPSW（J120 系），`SystemVersion.plist`：`ProductName=iPhone OS`，`ProductVersion=17.7.11`，`ProductBuildVersion=21H461` | 【实测】 |
| 内核 | 自编 XNU 23.6.0 DEVELOPMENT（BCM2837 配置，4 KiB 页），Apple 的 AMFI、CoreTrust、Sandbox、APFS kext 原样预链接 | 【实测】 |
| 身份 | 根 `model=iPhone16,1`，`/product/product-name=iPhone 15 Pro`，`target-type=J120`，`compatible=J120AP`，`product-id` 是 J120 的 | 【实测】ADT、`make-a830-layout.sh` |
| 是分裂身份 | 外观像 iPhone 15 Pro，内部很多表按 iPad Pro 12.9 二代 | 【读码】`功能盘点.md:210` |
| 设置里 | 预计型号名称“iPhone 15 Pro”，软件版本 17.7.11 | 【推断】没有截图 |
| 序列号等 | ADT 里没有 `serial-number`、`mlb-serial-number`、`model-number`、`region-info`，这几行大概率空白或未知 | 【实测】ADT 无此属性；显示效果是【推断】 |

### 9. 第三方应用能不能装

| 路线 | 现在 | 结论 |
| --- | --- | --- |
| App Store | 不能 | 确实做不到 |
| Apple 证书自签 IPA（免费号/开发者号） | 不能 | 要网络、配对、UDID，以后也不值得走 |
| `installd` 正常安装 ad-hoc IPA | 不能 | 被签名校验拒绝，且这条路从没走通过 |
| 原版 TrollStore | 不能 | CoreTrust 漏洞已修 |
| **ad-hoc + owner 信任包 + 预置** | **能** | **现在就走这条** |
| 只读 Apps 卷放闪存 | 要一次刷写授权 | 解决大应用占 RAM |
| 自制本机安装器 + 动态信任 | 要 Wi-Fi、持久化 | 长期方案 |

#### App Store：确实做不到

- **FairPlay**：App Store 的二进制都加密，免费应用也一样。`FairPlayIOKit` 不在预链接内核里，`fairplayd.A2` 启动后立刻退出，已被种子禁用（`make-phone-seed.py:14-15` 的说明）。本轮 `itunesstored`、`atc` 都报 `deny mach-lookup com.apple.fairplayd`。【实测】把 kext 加回来也没用，它要设备密钥和 SEP。【推断】
- **激活**：`mobileactivationd` 在跑，但要过 Apple 服务器，要设备证书和 SEP 证明。假的 iPhone16,1 过不了。【推断】
- **SEP**：只有 `sheng-aks` 替身，没有真实 SEP。【实测】
- **网络**：现在没有。
- 即使 Wi-Fi 通了，这几项也不会变。

#### 自签 IPA：AMFI 和 installd 都卡

- **AMFI**：`amfi_get_out_of_my_way`、`amfi_allow_any_signature` 只有 XNU 自己解析。预链接 kext 里 `code_signing_configuration` 只有两处调用，一处取监控类型，一处只测第 31 位，**都不读这些豁免位**。【读码，反汇编 `0xfffffff004a8f32c`、`0xfffffff004ab02e0`】所以加这些启动参数没用。
- `cs_enforcement_disable=1`：AMFI 只打印一句并检查调试许可（`0xfffffff004ab4bac` 起），不记录状态，不放行。上游 sirius 的记录也显示，AMFI 对不在信任缓存的映像一律拒绝（namespace 9 code 8）。【读码+上游记录】
- **`installd`**：`MICodeSigningVerifier` 只在两种情况下允许 ad-hoc 且要求在信任缓存：内建应用和“可删系统应用”（`isRemovableSystemApp`，查 `systemAppPlaceholderBundleIDToInfoMap`，第三方不在列）。其余走 libmis 的完整校验，ad-hoc 失败。【读码，反汇编】
- `codeSigningEnforcementIsDisabled` 先要 `allowsInternalSecurityPolicy` 为真。这由 `os_variant` 决定，零售版系统应当是假。【读码+推断】
- **开发者模式**只影响带描述文件的开发签名，和 ad-hoc 无关。在易失 Data 上开关也留不住。【读码+推断】
- **`installd` 这条路本身没验证过。** 10-30/31 的迁移安装从未真正把应用装进 `/private/var/containers/Bundle/Application`，全部被迁移流程删掉（`app moves: D S>- com.apple.migrat 29`），所以后来改成了 `/Applications` 视图。原因是迁移前几步把应用判成隐藏，没走到 `installd`。**所以 `installd` 本身能不能工作，目前未知**，不是“已证实坏了”。【实测+读码】

#### TrollStore：原版不行

官方支持范围到 16.7 RC 和 17.0；依赖的 CoreTrust 漏洞（CVE-2023-41991）在 16.7 正式版和 17.0.1 修复，21H461 是 17.7.11，不受影响。【网页资料，版本判断是推断】它给应用签的是伪造证书，过不了这里的 CoreTrust。

把它的 CDHash 写进信任包，它自己能跑，但它装的应用还是过不了。**真正有用的是“我们控制内核和信任缓存”，不是 TrollStore 本身。**

#### 现在能走的路：预置

这和 `paganidiag`、`NativeAdrenoMetal` 插件是同一套机制，已经在跑。【实测】本轮启动参数含 `sheng-no-lc=1`，launch constraints 不挡。

步骤：

1. 准备**解密过的** IPA：自己编译的、开源的，或用户合法取得并已解密的。`cryptid=1` 的不能用。
2. 新写工具 `make-phone-app-pack.py`：解包 → 删 `SC_Info` → 对每个 Mach-O（主程序、Frameworks、PlugIns）用 `codesign -f -s -` 重签 → 收集 CDHash。
3. 加权限：`com.apple.private.security.container-required` = bundle id，否则作为系统应用运行时没有数据容器。【推断，要试】
4. CDHash 并进 owner 信任包（`final/*-owner-trust.bundle`），改包只要重打镜像。
5. 文件放进 `/Applications` 视图的来源目录，`lsd` 每次开机建库时自动认。【读码】`phone-system-apps-view.patch`

限制：

- **放得下多少**：载荷 192 MiB，已用将近满（内核 128 MiB + 种子 55 MiB），打包种子上限 256 MiB（`ramPackedLayout`）。小应用可以，大的要加大载荷，而且解开后全占 RAM。
- **数据不保留**：持久化之前，应用数据重启就没。
- **兼容性未验证**：Metal 走 metal2vulkan，OpenGL ES 大概率不行，没有 ANE。没试过任何第三方应用。身份是 iPhone 布局 405×880。

#### 大应用：只读 Apps 卷

在 Mac 上做一个 APFS 只读镜像，放在 `unixnu` 空闲区，内核启动时挂载，绑到 `/Applications`，CDHash 进信任包。

- 优点：不占 RAM，不进种子，XNU 这边只读，零写入风险。
- 代价：要新增窗口和挂载（类似 SystemOS），要用户授权从 fastboot 或 Android 写这块区域一次。
- 工作量：2–3 天。

#### 没有 Wi-Fi 和 USB 时怎么传文件

| 方式 | 说明 | 状态 |
| --- | --- | --- |
| 进种子 | 重打镜像 | 现在可用，空间小 |
| 进 Apps 只读卷 | 从电脑刷写 | 要授权 |
| 交换区 | Android 把文件写进 `unixnu` 的一段空闲区（或导出区旁），XNU 用 `native_ufs_part_read` 只读取回 | 要写工具，要授权 Android 写分区 |
| `PaganiPersistentData` | Android 建锁定文件，XNU 按块读 | 代码是提案 |
| Wi-Fi | Safari 下载、文件 App | 等 `wifi.md` 的路线，最大未知是固件能否跑起来 |
| USB NCM/usbmux | 连电脑 | `功能盘点.md`：DWC3 设备模式没做 |

13T 没有存储卡槽。

#### 长期：本机安装器

Data 持久化且 Wi-Fi 通了以后：

1. 内核加一个运行期信任表：`query_trust_cache` 同时查它，开机从 Data 读回，只有固定 helper 能添加。
2. 写一个小应用加 root helper：选 IPA → 解包 → 重签 → 建容器 → 注册 LaunchServices → 把 CDHash 加进信任表。
3. IPA 来自 Safari 或文件 App。

这能达到 TrollStore 的体验，不靠漏洞。代价是系统代码签名基本失效，只适合自用。工作量约 1–2 周。

---

## D. 接近日用的路线

| 顺序 | 内容 | 写闪存 | 轮数 |
| --- | --- | --- | --- |
| 1 | 下一包：压缩器、8 GiB、只读视图、`M mem`（0.3 节） | 否 | 2–3 |
| 2 | 修 Spotlight 跑飞（`jitter.md` F8），它是当前最大的单个内存消耗 | 否 | 另一路 |
| 3 | 第一批预置应用：挑 2–3 个小应用验证第三方能跑 | 否 | 2 |
| 4 | P1 只读探针，P2 冻结种子 | 否 | 1–2 |
| 5 | **用户授权后** P3 持久化 8 GiB | 是 | 3–5 |
| 6 | Wi-Fi 通 + P4 扩到 32 GiB | 是 | 看 Wi-Fi |
| 7 | 本机安装器 | 是 | 多轮 |

命令行预算要贯穿始终：现在 996/1000。建议把 `payload.py` 的检查放宽到 1020，并分清“测试包参数”和“日用包参数”。

---

## E. 需要用户决定或提供

1. **“4 G”在哪看到的？** 请截“关于本机”和“iPhone 储存空间”两张图。
2. **下一包同意上 8 GiB 和压缩器吗？** 两项都不写闪存，失败就黑屏或卡开机，长按回安卓。
3. **持久化授权**（第 7 节的授权写法），以及 Data 要多大，建议先 8 GiB。
4. **想装哪些 App？** 有没有解密过的 IPA 或源码？App Store 下载的加密包不能用。
5. **确认 13T 的状态。** 见下节，`adb` 上现在不是这台手机。

---

## F. 没查清的事

- UEFI 真实空闲图没导出过，13 GiB 是 Android 视角加“窗口能跨界”推出的。
- `-apfs_shared_datavolume` 对我们是否必须。
- zone、匿名页、页表各占多少。下一包的 `M mem` 补。
- 本轮 GPU 池峰值（`MEM` 行第 30 秒后就没了）。
- 压缩器开启后的真实收益和卡顿影响。
- FUA 写路径的真实延迟。
- 设置页的实际显示，全部是静态推算，没有截图。
- 预置第三方应用能否启动、容器是否正常，没有试过。
- Data 后期那 673 MiB 到底写了什么。
- 约 64160 MiB 以上的残留数据是什么。高熵，疑似加密 userdata 残留，没有证实。

---

## G. 本次操作记录

**手机只读查询（序列号 `SERIAL_13T`）：** `/proc/meminfo`、`/proc/iomem`、`/proc/cmdline`、`getprop`、设备树节点、UFS 的 sysfs 描述符；`dd` 读 `sda16` 采样（64 MiB 和 4 MiB 步长，每点 1 块；5 处各读 64 块看内容）。没有写任何分区，没有改设置。

**两处要说明的操作：**

1. 中途有一次 `adb push ... /dev/null`，是我想把脚本传过去时多写的一步。目标是空设备，没有留下任何文件，后来改用管道。
2. **后期 `adb` 上的设备换了。** 我最后一次查询时发现连着的是 `SERIAL_OTHER_DEVICE`（型号 OPD2513，从屏幕尺寸看是平板），不是 13T。在发现之前，我在这台平板上跑过一次只读查询：`getprop` 几项、`cat /proc/meminfo`、`su -c` 下的 `wm size`，另有确认身份的 `adb devices`、`getprop`、`/proc/meminfo`。全部只读，没有改任何东西，但这不在任务范围内。发现后我没有再执行任何 `adb` 命令。`wifi.md` 记录这次换机发生在 11:43 或更早。

**13T 的结论不受影响。** 所有引用的手机数据都能和 PKX110 的已知值核对：LUN 0 块数 `0x766F000`=124186624、`sda16` 起始 859273216、UFS 日志窗口块 16769024、设备树 `oplusboot.serialno=SERIAL_13T`。平板上的数值没有进入本报告。

**网页资料：**

- [opa334/TrollStore](https://github.com/opa334/TrollStore)：支持版本范围
- [iOS17/TrollStore README](https://github.com/iOS17/TrollStore/blob/main/README.md)：16.7.x、17.0.1+ 不支持的说明（第三方镜像，只作佐证）
