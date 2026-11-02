# 阶段 1.5 上机前审查：固态声明、面板探针、键盘脚本、启动命令行

结论先说：**四块都没有找到会崩溃、会挂死或会写到窗口以外的确定缺陷。** 有 5 条发现，都不阻塞上机：1 条是首测数据的预期不符（脚本不进备忘录），2 条是探针风险的处理（顺序、门闩），1 条是文档结论写错，1 条是存储残余风险。第 3、4 条是一两行的改动，内核正在重编，要不要顺带改由主线决定。

全程只读。没有运行 adb、fastboot，没有编译内核。除本文件外没有改任何文件。证据标注：【读码】【反汇编】【真机】【推测】。

镜像按 `测试镜像/boot-A830-阶段15-首测.payload.json` 的参数核对。`UniXNU/out-gpu/.../tree/drivers` 下 16 个相关文件与 `UniXNU-Pagani-Drivers/drivers` 逐字节相同。反汇编用的是原版 21H461 内核缓存的副本，APFS 版本 2236.140.6，与真机日志一致。

---

## 一、固态声明

### 结论

- **不会发 TRIM**：APFS 唯一的 `DKIOCUNMAP` 调用点被 `DK_FEATURE_UNMAP` 挡住，我们的设备这一位是 0。
- **不会多一条写路径**：`NativeUfsBlock` 没有任何接口能把写送到控制器，这个 `.o` 里连 Data 存储窗口的变量都被编译器删掉了。
- **真实效果**：XNU 的限流和读写节奏变化，加上 APFS 事务的一个阈值。后者旧结论写错了（发现 1）。
- **已有真机旁证**：崩溃那轮已经带着这个声明跑完了根卷挂载、Data 挂载和 2.7 s 的种子拷贝，没有错误。

### 1. 写路径逐层核对

| 层 | 核对结果 | 依据 |
|---|---|---|
| 声明只读 | `reportWriteProtection` 返回 true | `NativeUfs.cpp:1238` |
| IOMedia | 不可写时 `IOMedia::unmap` 返回 `kIOReturnLockedWrite` | `IOMedia.cpp:900-950` |
| 块设备入口 | 方向不是读就返回 `kIOReturnNotWritable`，不碰控制器 | `NativeUfs.cpp:1467-1469` |
| `doUnmap` | 没有覆盖，走基类，返回 `0xE00002C7` | `llvm-nm` 看对象；【反汇编】`0xfffffff00596f05c` |
| `doFormatMedia`、`doEjectMedia` | 只返回错误 | `NativeUfs.cpp:1224-1225` |
| `doSynchronize` | 只看 `online`，不发命令 | `NativeUfs.cpp:1239-1240` |
| `getWriteCacheState`、`setWriteCacheState` | 固定 false，设置返回不支持 | `NativeUfs.cpp:1241-1242` |
| `doGetProvisionStatus`、`doSetPriority`、`requestIdle` | 基类返回不支持 | `IOBlockStorageDevice.cpp` |
| Data 存储窗口 | `hostStoreOpen` 在只读构建里直接返回 false | `RootCommands.h:360-364` |
| 编译产物 | 构建传了 `NATIVE_UFS_READ_ONLY=1`、`NATIVE_UFS_LOG_WRITES=1`；05:45 的 `NativeUfs.o` 里 `gStoreOpen`、`gStoreFirst` 已被优化掉，只剩 `gLogOpen`、`gLogFirst` | `build-native-ufs.py:30`；`llvm-objdump -t` |
| SCSI 总闸 | 方向为写，或操作码不在 TUR、INQUIRY、READ CAPACITY、READ(10)、唤醒 SSU 之内，一律要过 `hostLogCommand` | `RootCommands.h:199-204` |
| 日志闸门 | 只放 LUN 0、CDB 10 字节、无标志位（FUA 也不行）的 WRITE(10)，从 CDB 重新解析 LBA 和长度比对窗口；再加全零的 SYNCHRONIZE CACHE(10) | `RootCommands.h:340-357` |
| 窗口范围 | 分区名必须是 `unixnu`，末尾 8192 块加其下 65536 块，开窗前核对内容 | `LogWindow.h:8-10,19-40,62-73` |

真机窗口 LBA 124178176，换算成分区内 16769024，与 2026-10-31 17:50 用户授权的范围一致。

**结论**：任何情况下，写命令都到不了窗口以外。固态声明没有增加任何写入口。

### 2. TRIM 到底会不会发

**APFS 这一侧**【反汇编】：

1. `dev_init`（`0xfffffff006650afc`）只在“可写且 `features & 0x10`”时分配 TRIM 列表。
2. `_vnode_dev_unmap`（`0xfffffff00664efb4`）开头检查 `features & 0x10` 和列表非空，不满足直接返回 0。
3. 整个 APFS 只有一处 `DKIOCUNMAP`（`0x8010641F`，`0xfffffff00664f320`），入口同样检查 `features & 0x10`。
4. 真机每一行 `dev_init:306` 都是 `features 0`（`ufs/msgbuf.txt:285,300,316,384,418`）。

所以 APFS 不会发 TRIM，与是否固态无关。这一条解决了 `stage15-small.md` 留的未验证项。

**XNU 这一侧**【读码】：`vnode_trim_list` 先查 `MNT_IOFLAGS_UNMAP_SUPPORTED`，这个标志也来自 `DK_FEATURE_UNMAP`（`vm_compressor_backing_file.c:303`、`vfs_subr.c:4665`）。`vnode_trim` 在树里没有调用者。`kern_symfile.c` 只用于休眠文件。

**假设真有人对 NativeRamBlock 发 DKIOCUNMAP**：

```
dkioctl -> IOMedia::unmap -> IOBlockStorageDriver::unmap -> IOBlockStorageDevice::doUnmap
```

- 返回 `kIOReturnUnsupported`，映射成 `ENOTSUP`（`IOService.cpp:8188`）。
- 不清零，不释放页，稀疏页表不动。
- 耗时是常数，不扫描。
- APFS 即使走到这里也只记一行日志，不 panic（`_vnode_dev_unmap_flush_and_unlock`）。

### 3. 声明固态实际改变的行为（修正版清单）

| 位置 | 变化 | 影响对象 |
|---|---|---|
| `spec_vnops.c:1417-1421` | 限流周期从 40/85/200 改为 5/15/25 ms | 根卷、SystemOS、Data |
| `vfs_cluster.c:394-408` | 限流窗口 I/O 上限翻倍，异步数从 0 到 `MIN(ioscale,4)` | 同上 |
| `vfs_cluster.c:1349-1353` | 异步写比例从 6 到 2 | 只有 Data，RAM 盘同步完成，实际不等待 |
| `vfs_cluster.c:5664` | 预读单次不超过 512 KiB | 读路径，`ufsReadWindow` 本来就按 `kDataMax` 分块 |
| `vm_phantom_cache.c:38-42` | 抖动阈值 500 到 1000 | 文件页 |
| `vm_compressor_backing_store.c:573` | 只有 `VM_PAGER_COMPRESSOR_WITH_SWAP` 才走到 | iOS 默认不走 |
| **APFS `tx_leave`** | **事务阈值 256 到 1024（发现 1）** | **只有可写容器，即 Data** |
| `IOBlockStorageDriver::executeRequest` | 给 `io_rate_update` 带一个标志，默认回调是空函数 | 无 |

- **没有变化**：`PREFETCH`、`WRITE_BEHIND`、`vm_object_cluster_size`（macOS 分支）、UPL 写限流（iOS 恒为 SSD）。
- **APFS 块分配和预读**：设备句柄上的固态字段全 APFS 只有 `tx_leave` 一个读者（`0xfffffff00675d398`），其余都是日志。我扫了 `+0x504` 的所有访问和 `+0x500` 的 64 位访问，后者属于另一个结构。
- **`vnode_isonssd`**：内核缓存里没有任何 kext 导入它（没有 `j__vnode_isonssd` 和对应指针）。
- **`_solidState`**：原版 IOStorageFamily 里只有 `handleStart` 写、`executeRequest` 读（反编译全部 57 个方法核对）。

### 4. 字典现在存在，但只有一个键

以前字典不存在，相关分支从没走过。现在存在且只有 `Medium Type`，所以我把读者逐个查了一遍：

| 读者 | 处理 | 判定 |
|---|---|---|
| `IOBlockStorageDriver::handleStart` | `safeMetaCast` 后判空 | 安全，真机已过 |
| `IOMediaBSDClient`（`DKIOCISSOLIDSTATE`） | 同上 | 安全，真机已过 |
| APFS `0xfffffff00666ed38` | 读 Target Disk Mode，判空 | 安全 |
| APFS `0xfffffff00671b7bc` | 6 个键逐个 `safeMetaCast` 后判空 | 安全 |
| APFS `0xfffffff00671bb0c` | 用这些缓存字符串，逐项判空 | 安全，新增 `Main_MediumType` |

`stage15-small.md` 说函数开头的判空没有核对，这次核对了：`safeMetaCast` 的结果判空后才用。

**用户态读者只查到名单，没有逐个反汇编**：`Security`（`FileDesc::mediumType`）、`perfdata`（`config_emit`）、`libpartition2`（`-[LPMedia storageMedium]`）、`DiskImages2`（`-[DIBlockDevice mediumType]`）、`diskimagesiod`，以及读字典的 `PowerlogLiteOperators`、`PowerlogHelperdOperators`、`APFS.framework`、`livefiles_apfs`。它们都是 CF 字典读取，就算有问题也只影响用户态服务，这是【推测】。

### 5. 真机旁证（崩溃那轮）

- `spec_open : ... isssd is (1)` 出现 4 次（`ufs/msgbuf.txt:380,382,415,416`），属性确实从 APFS 媒体层走到了块设备。
- Data 挂载和种子拷贝完成：4214 个文件，467 MB，失败 0，2700 ms。阶段 1 是 2654、2660 ms，没有退步。
- 这是 launchd 之前的量。launchd 之后的重写入还没跑过，但走的是同一路径。

### 6. 没解释清的一处

`disk0`、`disk2s1`、`disk4` 这几行 `dev_init` 没有 `solidstate`，`disk1`、`disk3` 有（`msgbuf.txt:285,300,316` 对 `384,418`）。前者是物理层，后者是 APFS 媒体层，两边读的是同一个 ioctl。我没查清这个差异。

它不影响上面的结论：XNU 的 `MNTK_SSD` 走 `vfs_init_io_attributes`，`spec_open` 已证实为 1；APFS 这个字段只影响 Data 的事务阈值。本轮 msgbuf 截止在 63.47 s，Data 容器的那一行没抓到，下一轮可以看。

---

## 二、面板探针（`pagani-panel=1217`）

### 1. 位语义和线程

`1217` = 位 0、6、7、10：运行驱动、不发 0x28/0x29、P0/P1、P2/P3。没有位 1，不强制发布；没有位 11，不写 GCC。

- **线程**：`PaganiPanel::start` 用 `kernel_thread_start` 起一条内核线程（`PaganiPanel.cpp:188-192`），优先级是内核任务默认值，高于所有用户线程，不绑核。
- **窗口**：10 个窗口都带 `kIOMapReadOnly`（`PaganiPanel.cpp:66-71`）。
- **只读映射路径**【读码】：`IODeviceMemory` 的方向是 None，所以内存引用仍带写权限，`MAP_MEM_ONLY` 改缓存模式不受影响（`IOMemoryDescriptor.cpp:568-574,963-971`），映射的当前和最大权限都是只读，内核映射由 `vm_fault(VM_PROT_READ)` 预先建好（`:5269-5273`）。失败时打 `panel: probe map failed`，不 panic。
- **崩溃那轮**：探针线程已经起来过，到 4.8 s 没出事。但相关日志在缺口里，不算强证据。

### 2. 时间线

以 `panel: report` 落在 46.5 s 反推，线程约 1.5 s 起。

| 日志时间 | 动作 | 首次读的块 |
|---|---|---|
| 约 53.5 s | P0 | GCC `0x127004`、`0x127008`，各 1024 次 |
| 约 57.0 s | P0 | TLMM `0xf156004` |
| 约 60.5 s | P0 | DSI `0xae94000`：连读 1024 次，再 100 ms 约 5000 组 |
| **约 229.5 s** | P1 | **dispcc `0xaf00000`，25 个寄存器；然后 64 组相位（约 16 ms）** |
| **约 241–260 s** | **键盘脚本** | |
| 约 289.5–303.5 s | P2 | RSC、wrapper、SCC、CRMC、PHY，每块前重读门闩 |
| 约 349.5–350.5 s | P3 | INTF_1，读前重做 64 组相位 |
| 420 s | 定时复位 | |

地址和偏移我用 `dt/running.dts` 逐个核对过，全部落在设备树的 `reg` 内，也没有超出映射窗口：

- TE 引脚是 86（`qcom,platform-te-gpio = <... 0x56 ...>`），不在 `qcom,gpios-reserved`（36–39、74）内。
- TLMM 节点写的是 `@f000000`，但 GPIO 从 `+0x100000` 开始，与 `PAGANI_TLMM_GPIO` 一致。
- 探针全程没有写操作，`PeripheralWindow::write` 在这条线程上没有调用者。

### 3. Gunyah 下读这些地址会怎样

分三种情况：

| 情况 | 结果 | 依据 |
|---|---|---|
| Stage-2 未映射 | 公开 Gunyah 注入同步外部中止，XNU 走 panic，UFS 留日志，20 s 后复位。不会静默挂死 | `hw.md` 第 4 节，公开代码；真机镜像可能有私有模块 |
| 块没电、总线卡住 | 该 CPU 卡在这次读上。这是 2026-10-30 12:15 的情况，当时约 10 s 后冷复位回安卓，没有 panic | `DpuProbe.cpp:7-10` |
| 只有时钟断 | 读 0，不挂死，DSI 现状就是这样 | 【真机】多轮 |

卡住之后：

- 不在启动 CPU 上：其余 CPU 继续跑，日志继续写，定时复位照常。
- 在启动 CPU 上：心跳停，3 s 后扣留喂狗，30 s 内咬合复位。
- 以上都不需要用户动手，但这一轮后面的数据就没了（发现 3），复位也不受控（发现 5）。

### 4. 第一次亮度变化

**首测里这条路径根本不存在。** DSI 版本读 0 时：

1. `pagani_panel_dcs_gate` 判 `HELD_SHAPE`（`PanelBrightness.h:178-183`）。
2. `pagani_panel_start_dcs` 返回 -19，不读不写 DSI。
3. `pagani_panel_publishes` 为 0（没有位 1）。
4. `start` 在 `PaganiPanel.cpp:237-244` 置 `failed` 后返回，不调 `registerService`，`panelTimer` 保持空。

所以 CoreBrightness 看不到背光，`setProperties` 不会被调用，`pagani_panel_set_power` 只更新状态字、不启动定时器。这与阶段 1 的 `=65` 相同，用户这一轮亮度依然不会动，这是预期。

如果版本意外读成 `0x2009xxxx` 且形状正确，门闩放行，走首条 DCS，这是 `=65` 也有的路径，见 `probe-fixed.md`。

---

## 三、键盘脚本（`pagani-input-script=1`）

- **没有问题的部分**：
  - **电源键字顺序正确**。`pollButtons` 先算物理键报告，再写 `pagani_input_power_held`，最后才调 `pagani_script_keys`（`PaganiInput.cpp:204-207`）。脚本的 Power 不会让 UFS 日志暂停，`InputScriptTests.h:395` 断言了这一点。
  - **物理键中止规则不受脚本键影响**：`real` 取自合并前的报告（`InputScript.h:374`）。
  - **按键不可能卡住**：每个键带截止时间，超过计划 40 ms 就自动松开（`InputScript.h:380`）。

- **`mask=1` 实际按的键**：
  - Power 最多 1 次，只在 `display_off` 时按，120 ms。
  - Menu 共 3 次，每次 120 ms，间隔 2 s 以上。
  - **音量键一次都不按**。

- **副作用**：
  - 脚本键只进 HID 报告，不碰 PMIC，不会触发硬件复位。
  - 关机滑块、Siri 要长按；截屏要 Power 加音量同时按；SOS 要连按 5 次或长按组合。脚本同一时刻只持有一步的键，都构不成。
  - `Menu` 间隔大于三击窗口。
  - 键盘没弹出时，点击可能落到桌面图标上，只是开个应用。

- **时间**：`at` 从触摸驱动启动算起，约 241 s 开始，约 18 s 结束，加唤醒等待不超过 3 s，离 420 s 的复位很远。

- **两点提醒**：
  - 全程不要碰屏幕和按键。pkgA 那轮脚本在第一下点击后以 `real touch` 中止，同一时段日志里有用户手打的 `dn 18/18`，所以判定是人碰的，不是误报。
  - `PanelBrightness.h:35-37` 写位 6 下 `pagani_screen_state` 不变，代码其实会变（`PaganiPanel.cpp:36-41`）。脚本的唤醒判断依赖它，代码是对的，注释过时了。

- **内容与预期不符**：见发现 2。

---

## 四、启动命令行

| 项 | 结果 |
|---|---|
| 超长处理 | `payload.py:29-37` 超过 1000 字节直接报错，不截断；字符集正则保证字节数等于字符数 |
| 出错后是否继续 | `make-a830-layout.sh:48`、`make-a830-images.sh:5` 都是 `set -euo pipefail`，不会拿旧 payload 打包 |
| UEFI 配置 | `Config.c:1166` 从 TOML 取字符串再转宽字符；转换函数我没读到，靠下面的真机 998 字节兜底 |
| DarwinBoot | 1024 字节缓冲，超长返回 `EFI_BAD_BUFFER_SIZE`，不截断（`DarwinBoot.c:516-522`） |
| XNU | `BOOT_LINE_LENGTH` 1024（`pexpert/arm64/boot.h:25`） |
| 当前长度 | 首测 996，完整包 998；无重复参数 |
| 真机 | `A` 记录来自 `PE_boot_args()`：996 字节的最后一个参数 `pagani-input-script=1`，998 字节的最后一个参数 `sheng-log=1` 都在，且后者已经生效 |

**结论**：不会悄悄截断。首测只剩 4 字节，放不下 `pagani-input-script-at=...`（至少 26 字节）。

---

## 五、发现

### 发现 1（minor）：README 说 APFS 不发 `DKIOCISSOLIDSTATE`，这是错的

- **位置**：`UniXNU-Pagani-Drivers/drivers/NativeUfs/README.md:17-19`
- **路径**：
  1. `dev_init` 调操作表第 2 项 `0xfffffff00664eac8`。
  2. 它执行 `VNOP_IOCTL(vp, 0x4004644F, ...)`，这个值就是 `DKIOCISSOLIDSTATE`。
  3. 结果存到句柄 `+0x504`，日志打印为 `solidstate`。
  4. `tx_leave`（`0xfffffff00675d370-3a8`）用它选阈值：`solidstate ? 0x400 : 0x100`。
- **真机**：`dev_init:306: disk3 ... features 0 internal solidstate`。
- **含义**：可写容器的事务阈值从 256 变成 1024（【推测】计数项是对象缓存里的脏对象）。这是 Data 的行为变化，分析卡顿时要算进去，单个事务会变大。对只读容器没有影响。
- **为什么主机测试抓不到**：主机没有 APFS。原来的结论来自扫描常量，这个常量的两条指令顺序不固定，扫描会漏。
- **修法**：改 README，写明上面的路径，并把“TRIM、`vnode_isonssd` 未定”改成已定（见第一节）。

### 发现 2（wrong-data）：脚本不进备忘录，只在 Spotlight 里点 8 下

- **位置**：`InputScript.h:58-84`、`PaganiInput/README.md:45`
- **路径**：`mask=1` 的程序是 wake、home、unlock、home、下拉 Spotlight、Shift、a×3、123、ABC、地球键、上滑、home。全程不打开任何应用。
- **影响**：
  - 任务说明写“自动进备忘录打字”，代码做不到。
  - 真机的长停顿记录都在备忘录（3137、6281、12571 ms）。
  - `analysis.json` 把这轮定义为“上机对照 A”，并写明“无人值守脚本不覆盖备忘录”。如果把备忘录当首测判据，这一轮拿不到。
- **为什么主机测试抓不到**：测试核对的是坐标和时序，不判断目标应用。
- **修法**：把判据改成“Spotlight 里 `kbt` 的 dn/up 配对”。备忘录仍按“上机对照 B”由用户手打，或者另写一个 bit，那要重新定几何参数，这一包不建议加。

### 发现 3（minor，未验证）：没验证过的读排在脚本前面，挂死会连带丢掉键盘数据

- **位置**：`PanelProbe.h:38`、`InputScript.h:37`
- **路径**：P0 的 DSI 大量读在约 60.5 s，P1 的 dispcc 第一次读在约 229.5 s，脚本约 241 s 才开始。
- **影响**：现存运行目录的命令行里都没有 `pagani-dpu-probe`，dispcc 在真机上没有读过的记录。`hw.md` 把“GCC 关时读 dispcc”列为可能挂死，P1 有门闩，但门闩能否防挂主机回答不了。一旦挂死，这一轮的键盘数据就没了，也白花一次用户同意。这是把最不确定的读放在了最想要的数据前面。挂死概率未知，所以只算排序建议。
- **为什么主机测试抓不到**：探针和脚本在两个驱动、两套虚拟时钟里测，没有测试看它们在同一条时间线上的先后。
- **修法**：把探针整体挪到脚本之后，改 `PanelProbe.h:38` 四个常量，建议 `P0=264500`、`P1=284500`、`P2=304500`、`P3=344500`：
  - P0 读到约 276.6 s，P1 约 289.5 s，P2 到约 323.5 s，P3 不变，都早于 420 s。
  - 同步改 `Test/panel-probe.cpp` 的固定时间和文件头注释。
  - 命令行放不下 `-at` 参数，所以不能反过来改脚本时间，除非改 `PAGANI_SCRIPT_AT_DEFAULT`。

### 发现 4（minor，未验证）：P1 和 P3 的相位循环读 DSI 时没看门闩

- **位置**：`PanelProbe.h:230-232`
- **路径**：`panel_probe_sample_phase` 每组先读 `cfg`，这是 GDSC 状态，接着无条件读 DSI `0x0`。
- **矛盾**：
  - 文件头的原则是“门闩紧贴它打开的块”。
  - `hw.md` 记录 GDSC 断电时读寄存器会挂，Android 读 DSI 前也要先投 GDSC。
  - 文件头还说 HW_CTRL 可能让硬件塌缩这个域。
  - 所以这一组恰好读到“GDSC 关”时，下一次读就踩上去了。
- **局限**：
  - 是否真有塌缩未知。`=65` 的 5 个时刻没挂，但那只是 5 个时刻，P1 是第一次跨帧采样。
  - P0 的 DSI 读更多，也没有门闩。它排在读 dispcc 之前，没法加。
  - 所以这条修法只保护 P1、P3，P0 的风险靠发现 3 的调序控制。
- **为什么主机测试抓不到**：模型里 GDSC 关时读 DSI 不会挂，只返回一个值。
- **修法**：一行，`version` 改成只在 `panel_probe_gdsc_on(cfg, group)` 时读，否则取 0。需要把 `group` 的计算提前，模型测试的读序断言要跟着改。

### 发现 5（minor，未验证，存储残余风险）：探针挂死导致的复位不经过 UFS 静默

- **位置**：`BootProgress/UfsLog.c:556`、`UfsLog.h:45-48`、`GunyahWdog.c:357-369`
- **路径**：
  1. 探针读挂住一个 CPU，其余 CPU 上的 `pagani-ufslog` 线程继续每 3 s 写一轮。
  2. 随后的冷复位或看门狗咬合不经过 `ufs_log_quiesce`，可能落在写命令中途。
  3. 看门狗扣留喂狗时会打一行日志，但写线程不看这个状态，只看电源键和 `reset_coming`。
- **影响**：LUN 0 的 `bDataReliability=0`，写入中掉电，理论上影响整个逻辑单元，`userdata`、`super`、`persist` 都在上面。
  - 概率【推测】：一轮约 0.2–7 ms、周期 3 s，命中率约千分之几以内；还要先发生挂死。
  - 这不是新写路径，`UfsLog.h` 已写明不覆盖这种复位，用户也知道这个残余风险。新情况是：首测主动引入了几次可能挂死的读。
- **为什么主机测试抓不到**：真实复位时序在主机上不存在。
- **修法**：至少在问用户同意时说明这一点。如果想彻底堵住：
  - 让 `ufs_log_platform_power_held()` 同时看“喂狗被扣留”。这能覆盖启动 CPU 卡住的情况，改动很小。
  - 探针在每块首读前也置这个字：写线程立刻补一轮把面包屑落盘，然后停写，读完再放开。这样挂死时线路上没有命令，面包屑也不再靠 3.5 s 这个紧余量。

---

## 六、上机时顺带看

| 看什么 | 期望 |
|---|---|
| `spec_open ... isssd is (1)` | 根卷、SystemOS、Data 都是 1 |
| `dev_init:306` | `features 0`。Data 容器那行如果抓到，应带 `solidstate` |
| msgbuf | 没有 `trim'ing` 字样 |
| 复测基线 | 窗口外哈希不变 |
| `panel: probe map failed` | 不应出现；出现说明只读映射失败，探针这一轮白跑 |
| `panel: p0 gcc ahb` 之后每块的 `next` 行 | 最后一条 `next` 就是挂死现场 |
| `input script: armed 1 at 30xs` | 约 241 s 有 `start`，结束是 `done`，不是 `abort` |

---

## 七、未验证项

- 探针的所有 MMIO 读都没上过机。设备树只能证明地址存在，不能证明读得安全。
- 用户态读者没有逐个反汇编。
- `disk0` 等物理层 `dev_init` 不带 `solidstate` 的原因没查清。
- Data 容器在 launchd 之后的写入量下，1024 阈值的实际影响没有数据。
- Gunyah 真机镜像可能有私有模块，第二节第 3 点只依据公开代码和 12:15 的一次记录。
- 脚本的 Spotlight 点击坐标在当前身份下是否命中按键，要看 `kbt`。
- 反汇编基于 21H461 原版内核缓存。自建内核内嵌的是同一份，字符串偏移一致，但我没有逐字节比对全部 APFS 代码。
