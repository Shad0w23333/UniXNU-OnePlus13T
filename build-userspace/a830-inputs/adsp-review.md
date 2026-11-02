# ADSP、扬声器、电池：硬件安全与正确性评审（adsp-review）

- 日期：2026-10-31。全程只动源码和主机测试，**没有碰手机，没有提交**，没有改 Stage7Probe、components.toml、board.toml、patches/xnu/series、make-a830-layout.sh、make-phone-seed.py，也没有写 `UniXNU/out-gpu`。
- 范围：`drivers/PaganiAdsp`（含新的 QRTR 客户端接口）、`drivers/PaganiSpeaker`、`drivers/PaganiBattery`。
- 标注：【证实】= 源码或测试证实；【推断】= 判断；【未验证】= 只有上机才知道。
- **当前内核 `f38f9e4b` 不含这些修复。早上上机前要先按第 4 节合入并重编内核，第 1 节才成立。**

## 0. 结论

1. 最危险的一处是扬声器的 SMMU 阶段：原代码给每个音频流各占一个上下文银行，要写银行寄存器，而 sheng 正是在给这些流编程上下文时整机复位。已改成 Linux 和 sheng 的做法：共用存储流已经在跑的旁路银行，只写一个 S2CR、一个 SMR。
2. ADSP 启动失败时会把电压票从可能还在运行的 Q6 脚下撤掉，超时的安全调用之后还继续发 SCM。已改：关不掉就留着票，安全调用超时就不再发第二个。
3. 中断接上但不响时，链路永远不动（只有事件才服务）。已改：就绪后发现没响过就转轮询，空闲等待到期也看一次。
4. TZ 等待队列的唤醒会被 GPU 的 994 中断处理函数抢走，ADSP 的轮询永远等不到，最后 20 s 超时、TZ 里留下挂起的会话。这个要改 GPU 也在用的 `Sm8750Scm.c`，以 diff 交付（第 4 节）。
5. 默认数字天花板从 −6 dBFS 改成 −12 dBFS：没有喇叭保护时，−6 dBFS 的持续正弦约 2.5 W，不是安全值。
6. `pagani-adsp=1` 不再顺带打开电池。第 1 节每一步写的就是实际打开的东西。

## 1. 早上的分阶段启动参数（严格按这个顺序）

**基线**：日常包 E，`loop-m8-phone` 的参数已含 `pagani-ramoops=1 pagani-panic-reset=20 pagani-reset-after=420 pagani-wdog=30 pagani-reset-wdog=1`。下表的参数放进 `A830_EXTRA_ARGS`。启动参数余量约 132 字节（`功能盘点.md` 第 4 节），每一步都放得下。

**规矩**：一步一张镜像，一次只加一样。用户在场。任何一步出 `FAIL`、panic 或整机复位，就停在这一步，先读日志，不跳步。回到安卓后看一眼 `dumpsys battery` 和充电是否正常。

### A 段：不开 ADSP

| 步 | `A830_EXTRA_ARGS` | 字节 | 新碰的硬件 | 通过时看到 | 出问题 |
| --- | --- | --- | --- | --- | --- |
| A1 | `pagani-battery=1`（或 `A830_BATTERY=1`） | 17 | SPMI 表只读；I2C hub 时钟（`0x52008` 位 7、8、11）；SE1；电量计读与一次 DEVICE_TYPE | `[battery] start … adsp=0 charge=0`；`i2c up st=5 err=0`；`gauge addr=0x55 rc=0 type=0x4231 NFG8011B`；`state published pct=…`；状态栏电量与安卓一致 | 卡死或总线挂：I2C hub 没有 `qup-config` 互连票也许不通【未验证】，A2 起全部暂停，因为 SE4 走同一个 hub |
| A2 | `pagani-speaker=1 pagani-speaker-stop=4` | 39 | L15B 投票；SE4；gpio2、164 复位脉冲 | `[spk] rail ldob15 mv=1800 rc=0`；`bus … rc=0`；`amp 0x34 rev=0x..73 type=tfa9873`；`amp 0x35` 同样；`stopped as asked … st=4` | `absent`：先看 `rail` 行 |
| A3 | `pagani-speaker=1 pagani-speaker-stop=5` | 39 | 功放寄存器（掉电状态） | 两颗都是 `config ok`；`stopped as asked … st=5` | `config MISMATCH`：停，记下 `reg/got/want` |

### B 段：只开 ADSP，不接任何客户端

| 步 | `A830_EXTRA_ARGS` | 字节 | 新碰的硬件 | 通过时看到 | 出问题 |
| --- | --- | --- | --- | --- | --- |
| B1 | `pagani-adsp=1 pagani-adsp-stop=3` | 33 | 只读：SMEM 表与 apps↔ADSP 分区、modem_a、AOSS、RSC | `toc`、`smem part adsp=…`、`fw part=modem_a lun=4 first=4870`、`fw parsed`、`survey items=0x0 slave=-1 qmp=LIAM`、`stopped as asked … st=3` | 最后一行是 `smem touch` 后 panic：这页 XNU 读不了，**ADSP 路线到此为止**，先查分区偏移。`fw mount failed`：用 `pagani-adsp-part=modem_b` 再试 |
| B2 | `pagani-adsp=1 pagani-adsp-stop=5` | 33 | SMEM 写 item 429；TCSR 互斥 3；IPCC 中断与门铃 | `smp2p out`；`ipcc intid=261 attach=0x0 readback=0xa003`；`stopped … st=5` | `ipcc interrupt unavailable, polling`：能继续，记下来 |
| B3 | `pagani-adsp=1 pagani-adsp-stop=7` | 33 | AOSS QMP；lcx/lmx 投票（DRV-2） | `qmp rc=0 reused=1`；`rails on rc=0 … vlvl=384`；`rails off rc=0`；`undo … unvote=0 load_state_off=0`；触摸、GPU 照常 | `rc=-16`：锁字被占，查谁占 |
| B4 | `pagani-adsp=1` | 14 | 全程：PAS、Q6 起机、GLINK、QRTR | `scm init_image`、`pas loaded adsp`、`scm auth_and_reset … rc=0 … us= intr=`、`ready wait ms=…`、`handover: proxy votes dropped rc=0`、`glink version agreed`、`channel IPCRTR up`、`qrtr hello node=…`、`locator ask=avs/audio domains=1 sent=1`；**没有** `[battery]` 和 `[spk]` 行；桌面保持 3 分钟 | 见下方“B4 怎么读” |

**B4 怎么读**：

- `FAIL st=-8/-9/-10`：PAS 被拒。看 `x0`、`x1`。`undo` 行应全 0，桌面应不受影响。
- `FAIL st=-11 err=-60`：没有 ready，可能是 SoCCP 依赖【未验证】。`undo` 行里 `kept_votes=` 出现表示 PAS 关不掉，票故意留着，下次开机就清了。
- `scm_parked=1`：SCM 超出 20 s。要么 auth 真的慢，要么（没打第 4 节 diff 时）唤醒被 GPU 抢走。此后不要再试任何安全调用，重启。
- `ipcc interrupt attached but silent, polling`：中断线不通，功能可用但费电，记下来。
- `intr=0` 且 `us` 很大：TZ 这段时间不让出 CPU；所有 SPI 都路由到启动核，若线程正好在 0 号核，显示、UFS、GPU 中断会被压住同样长的时间。留意这一下卡顿【推断】。

### C 段：ADSP 全程 + 一个客户端

| 步 | `A830_EXTRA_ARGS` | 字节 | 新碰的硬件 | 通过时看到 | 出问题 |
| --- | --- | --- | --- | --- | --- |
| C1 | `pagani-battery=1 pagani-adsp=1` | 31 | battmgr 通道，只读 | `dsp channel up`；`dsp cyc=2 layout=oplus`；`dsp set` 行**一条也不能有**；`state … src=gauge+charger`；插拔线状态变化 | 出现 `dsp set`：立刻停，这违反只读 |
| C2 | `pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=7` | 53 | `adsp_apps`、APM 查询 | `dsp up`；`apm state=ready`；`stopped … st=7` | `the DSP never opened adsp_apps`：audio PD 没起来，看 `locator` 与 `pd crash report` 行 |
| C3 | `pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=8 pagani-speaker-sids=1` | 75 | **全程最危险的一步**：apps SMMU 写一个 S2CR、一个 SMR（只有 0x1001） | `smmu start sids=0x1`；`smmu 0x1001=ok/…`；`stopped … st=8` | 整机复位且最后一行是 `smmu start`：这台机器 Gunyah 不接受音频流路由，**停，不要再试别的银行**。`0x1001=-1/…`：没找到 UFS 的旁路银行，没有写任何东西 |
| C4 | `pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=8` | 53 | 四个音频流 | `smmu 0x1001=ok/… 0x1081=ok/… 0x1041=ok/… 0x1061=ok/…` | 只有后三个失败不致命，记下 |
| C5 | `pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=10` | 54 | 映射 `0xd0000000`；建图；MEM_MAP | `buffer pa=0xd0000000 dsp=0x1d0000000 bytes=65536`；`graphs handle=…`；`stopped … st=10` | 同步异常：这 64 KiB 没映射给 HLOS，用 `pagani-speaker-pa=` 换到 `DXE_Heap` 里别的地址 |
| C6 | `pagani-adsp=1 pagani-speaker=1 pagani-audio-tone=1` | 51 | 位时钟、功放上电、声音（440 Hz、500 ms、−30 dBFS） | 听到小声的音；`play start run=1 amps=0x3`；`amp 0x34 state=9 clks=1 plls=1`；`tone rc=0 … clks_failed=0`；`lim=0 … clamp=0` | 没声但全通过：下一张加 `pagani-speaker-sd=2`（只在 `feedback=0` 时）。`clks=0`：加 `pagani-speaker-prm-port=7` 试 |
| C7 | `pagani-adsp=1 pagani-speaker=1 pagani-audio-tone=2` | 51 | 同上 | 依次：两边、0x34、0x35，分清上下喇叭 | 记下哪颗是哪边 |

### D 段：合起来（等 audio2 路由）

| 步 | `A830_EXTRA_ARGS` | 字节 | 说明 |
| --- | --- | --- | --- |
| D1 | `pagani-adsp=1 pagani-speaker=1`（`AUDIO_TRUST` 自动加 `pagani-audio=1`） | 31 | iOS 出声，天花板 −12 dBFS；看 `play stop … lim= gmin= clamp=0` |
| D2 | `pagani-battery=1 pagani-adsp=1 pagani-speaker=1` | 48 | 日常组合 |

### 这些今天不要开

- `pagani-battery-charge=1/2`：写充电器。
- `pagani-battery-pmic=2`：写观察通道，留给单独的调试轮。
- `pagani-speaker-ceiling` 小于 12：等听过音乐再说；也不要放扫频或满幅正弦。
- `pagani-speaker-feedback=1`：功放往数据线上驱动。
- `pagani-gpu-async=1`：GPU 的 SCM、QMP 会和 ADSP 重叠。
- 传感器、Wi‑Fi 和 ADSP 同一轮首测：`armcpu_spi_attach` 不是线程安全的，Wi‑Fi 与 ADSP 都在各自线程里挂中断（该文件归别的代理，只记录）。

## 2. 发现与处理

| 号 | 级别 | 发现 | 怎么证实 | 处理 |
| --- | --- | --- | --- | --- |
| S1 | 高 | 音频 SID 用 `planUfsSmmu` 各占一个上下文银行（从顶往下找 8 个），要写 SCTLR、CBAR、CBA2R，还发全局 TLBI。HLOS 看到“空闲”的银行不等于真空闲，Gunyah 捕获这些寄存器；sheng 给这些流编程上下文时整机复位（`AudioProbe.cpp` 281–282 行）。sheng 跑通的是共用末尾旁路银行 | 读代码对比 `SmmuTakeover.h` 与 sheng `SmmuBypass.h`；Linux `qcom_smmu` 的 bypass 也是共用一个银行 | 新 `SpeakerSmmu.h`：在 SMR 表里找 UFS 流 0x60 所在银行，核对它确实是旁路，只写 S2CR、SMR（先 S2CR），不写银行、不发 TLBI、不碰全局寄存器；任何异常一个字节都不写。`Test/smmu.c` 3201 项 |
| A1 | 高 | `undo()` 不管 PAS 关没关掉都撤票、发 `load_state off` | 读代码；新增场景 | 关不掉就留票和 `load_state`（`kept_votes=`）。变异验证能抓到 |
| A2 | 高 | SCM 超预算后会话留在 TZ，`undo` 还继续发 SCM、撤票 | 读 `Sm8750Scm.c`：`Sm8750ScmTimeout` 不收尾 | 记 `scm_parked`，不再发安全调用，票和 `load_state` 留着 |
| A3 | 高 | 中断接上但不响：`wait_until` 最后一眼能看到 ready，之后服务循环事件为 0 什么都不做，GLINK 永远不动 | 新场景 `silent_interrupt` 没修时失败 | 就绪时门铃计数为 0 就转轮询；空闲等待到期看一次（`lost_doorbells`） |
| A4 | 高 | GET_WQ_CTX 只把唤醒给一个调用者。994 的处理函数在 GPU 的 `Sm8750Scm` 里抢走唤醒，ADSP 自己那份永远等不到，20 s 后超时。`Sm8750Scm.c` 的注释“两边都安全”只对同一个实例成立 | 新测试在现行源码上 **失败**，打 diff 后通过 | 共享 `all_wakes`，见第 4 节。GPU 单实例行为不变 |
| A5 | 中 | RESUME_TX 里写的是发送方地址，且走控制端口。Linux 按“发往的地址”找流，DSP 的流量窗口永远不恢复。音频量小看不出，传感器指示流约 10 条后就停【推断】 | 对照 Linux `qrtr_send_resume_tx`、`qrtr_tx_resume`（只取事实）；sheng 同样写错 | 改成从收数据的端口发到发送方端口，内容是接收方地址 |
| A6 | 低 | 通道说明写“任何时候都能注册”，但 DSP 先开的 OPEN 被丢掉，晚注册的通道永远起不来 | 读代码；今晚的启动顺序碰不到 | GLINK 记住未注册的 OPEN（8 个），注册时补 OPEN_ACK，CLOSE 时清掉 |
| A7 | 低 | IPCRTR 只给 2048 字节接收缓冲，注释却说“照设备树”（`0x800×5、0x2000×3、0x4400×2`）。DSP 发大包会一直等 | 读 DT 13084 行 | 统一用 `0x4400`，共 136 KiB |
| A8 | 低 | 启动失败或 stop 后 IPCC 接收信号和中断线一直开着 | 读代码 | `quietDoorbells()`：关信号、关线 |
| P1 | 中 | GENI 读之前不清 RX FIFO。超时中断的读留下的字会被下一次读拿走，而功放的 `Update` 是读改写，会把错的值写回寄存器 | PaganiBattery 已有这一步，Speaker 没有；新用例 | 读之前清空。加变异项 |
| P2 | 中 | 默认天花板 −6 dBFS 只限峰值。喇叭 6 Ω（DT `tfa_default_mohm`），容器升压约 11.4 V【推断】：−6 dBFS 持续正弦约 2.5 W，−12 dBFS 约 0.6 W | 算术写在 `SpeakerLimit.h` | 默认改 −12 dBFS（比安卓小声），仍可用 `pagani-speaker-ceiling` 调，不能关 |
| P3 | 低 | SMMU 阶段的日志在全部写完之后才出，整机复位时看不出死在哪 | 读代码 | 写之前先记 `smmu start` |
| B1 | 中 | `pagani-adsp=1` 顺带打开电池，第 6、7、9 轮不是单变量 | `PaganiBattery.cpp` | 只认 `pagani-battery=1`。写 `=0` 仍然有效，旧参数不受影响 |
| B2 | 低 | `BatteryProps.h` 引用的 `Test/props.c` 不存在，`BatteryProps.c` 没有任何测试 | 看目录 | 补 `Test/props.c`（282 项），检查脚本加 powerd 字符串核对 |
| B3 | 低 | 注释还写着触摸、扬声器不加锁 | 对照 `PeripheralIO.h` | 改注释，无代码变化 |

没有问题、不用改的，见第 3 节。

## 3. 逐项回答

**`0xd0000000` 空闲吗？** 【证实，按源码】在 13T 的 UEFI 内存图里是 `DXE_Heap`（`MemoryMapLib.c` 第 36 行：`0xBE360000 + 0x16AC3000`，`Conv`），也在安卓 memory 节点的同一范围内，所以 Gunyah 映射给 HLOS；离它最近的保留区是 `0xD4E23000` 的 `HW_Fence`。XNU 窗口是 DarwinBoot 在 `0x8c0000000` 起分配的。DarwinBoot 在 UEFI 里只用 pool，`ExitBootServices` 后都作废；UEFI 运行时服务 XNU 不调用。【未验证】UEFI 那次启动有没有什么东西留到之后。注意 sheng 注释里的 `0xCCF80000–0xD8000000` 是小米的内存图，不能当 13T 的依据，已在 `SpeakerBoard.h` 写明。C5 是上机验证。

**SMMU 直通**：见 S1。写的只有 S2CR 和 SMR，流量经 UFS 的旁路银行到物理地址，和 sheng 一样；13T 上是否被 Gunyah 接受【未验证】，所以 C3 单独一步。

**`0x52008`**：扬声器、触摸走 `sm8750_gcc_vote_set`，电池和 PCIe 用同一协议，只置位不清位；持锁超 50 ms 才无锁写并计数；电池每次传输前再看一眼。没有问题。

**I2C 失败后的总线**：每次传输三级（10 ms 取消、再中止、再放弃），有界；功放程序失败立刻返回；放弃后状态回 idle。修了 P1。SE4 上只有两颗功放，SE1 上只有电量计（DT 25540、25633），坏了影响不到别的设备。

**功放**：寄存器配置全程掉电；有位时钟才上电；停止时先下电再停图；DSP 掉线、卡住、拒绝启动都下电；sense 输出默认关，所以就算数据线选错也不会对驱。TFA 上下电顺序对照了 13T 的 `tfa_dsp.c`（`TFA_STATE_INIT_CF`、`OPERATING`、`tfa98xx_powerdown`），一致。**没有喇叭保护意味着**：没有偏移和温度模型，天花板只管峰值，靠 −12 dBFS 留余量；功放自身的过流、过温、欠压保护还在。所有周期都过限幅器，无法绕过（变异 `core-skips-the-ceiling` 能抓到）。

**DRV-2、QMP、SCM 共享**：DRV-2 三方都上同一个锁字，ADSP 投的是 `lcx.lvl`、`lmx.lvl`，碰不到 GPU 的 `gfx/mx/cx`。QMP：GPU 只在同步启动 S1 发一次，ADSP 线程在它之后，Wi‑Fi 和 ADSP 用同一个锁字。SCM：GPU 的调用全在同步启动里，运行期默认不走 TZ（`A830ZapResumeSecureMode` 什么也不发）；GPU 不取 `sm8750_scm_owner`，所以 `pagani-gpu-async=1` 不能和 ADSP 同时用。这一条 README 已改成实话。

**调制解调器**：ADSP 路径不写任何调制解调器相关的东西；modem_a 只读；TCSR 互斥只为 apps↔ADSP 分区分配时短暂持有，10 ms 上限；modem 在 XNU 下本来就不运行。定位器表里有 `msm/modem/root_pd`，但没人起它，只影响 GPS 相关服务。

**充电器只读**：`charge=0` 时 `policy()` 第一行就返回，发出去的只有通知请求和属性读；`set_allowed` 再挡一道；主机测试 `sim.sets == 0`。ADSP 起来本身会让 charger PD 接管充电，这和安卓开机一样，C1 看电流前后变化。

**电量计唯一的写**：`pagani_gauge_transfer_allowed` 只放行“寄存器地址后读 1–4 字节”和 `{0x3e, 0x01, 0x00}` 这一次 DEVICE_TYPE；其余一律拒绝并计数。`pagani-battery-gauge-id=0` 可以连这次也不写。

**BatteryProps 的键**：21 个键全部是 21H461 powerd 里的完整字符串（检查脚本现在每次核对）。按 `powerd` `0x10002e790` 的 CFString 引用顺序逐个核对过：`CurrentCapacity`、`MaxCapacity` 必需，`ExternalConnected`、`ExternalChargeCapable`、`BatteryInstalled`、`IsCharging`、`AppleRawExternalConnected`、`FullyCharged`、`AtCriticalLevel`，以及数字键 `Voltage`、`DesignCapacity`、`TimeRemaining`、`InstantAmperage`、`Amperage`、`CycleCount`，与 `BatteryProps.h` 的说明一致。`NominalChargeCapacity`、`AppleRaw*`、`Temperature` 也有引用。没有发布 `Serial`、`BatteryData`、`AppleAuthCPRelay`。

## 4. 要合入的差异（`build-userspace/a830-inputs/adsp-review/integrate/`）

| 文件 | 内容 | 必须吗 |
| --- | --- | --- |
| `Sm8750Scm-shared-wakes.diff` | `Adreno830/platform/sm8750/Sm8750Scm.c` 加共享唤醒计数，`tests/test_sm8750_scm.c` 加双实例用例。在草稿副本上：不打补丁新用例失败，打了通过；Adreno830 全量 44 过、0 败、1 跳过，内核对象编过；PaganiAdsp 主机测试也通过 | 上 B4 之前要有 |
| `Stage7Probe-battery-comment.diff` | 只改一行注释 | 可选 |

合入：在 `/Users/xiaochoumao/unixnu-13t` 下 `patch -p1 < 文件`（都试过 `--dry-run`）。驱动源码已就地改好，构建树是符号链接，重编内核就带上了。`Speaker` 不再依赖 `SmmuTakeover.h`，新增的 `SpeakerSmmu.h` 已在构建脚本的头文件表里。

`功能盘点.md` 第 336 行“`pagani-adsp=1` 顺带打开电池”的评审补充已过时，第 5 节的 6–9 轮应换成本文第 1 节。

## 5. 改动的文件

- `PaganiAdsp/`：`AdspCore.c`、`AdspCore.h`、`AdspPlatform.h`、`AdspQrtr.c`、`PaganiAdsp.cpp`、`PaganiAdsp.h`、`QcomGlink.c`、`QcomGlink.h`、`QcomQrtr.c`、`README.md`、`Test/core.c`、`Test/cores.c`、`Test/platform.c`、`Test/qrtr.c`、`Test/qrtr_client.c`
- `PaganiSpeaker/`：新增 `SpeakerSmmu.h`、`Test/smmu.c`；改 `GeniI2c.c`、`PaganiSpeaker.cpp`、`SpeakerBoard.h`、`SpeakerCore.c`、`SpeakerCore.h`、`SpeakerLimit.h`、`README.md`、`scripts/build-pagani-speaker.py`、`Test/amp.c`、`Test/core.c`、`Test/limit.c`、`Test/check-pagani-speaker-host.py`、`Test/mutate-pagani-speaker.py`
- `PaganiBattery/`：新增 `Test/props.c`；改 `PaganiBattery.cpp`、`PaganiBattery.h`、`BatteryI2c.c`、`BatteryI2c.h`、`BatteryProps.h`、`README.md`、`Test/check-pagani-battery-host.py`

## 6. 验证

| 项 | 结果 |
| --- | --- |
| `PaganiAdsp/Test/check-pagani-adsp-host.py` | 全过；core 1446335 项，失败路径 26 个（新增 5 个）、`silent_interrupt`、`lost_doorbells` |
| 手工变异（ADSP） | 7 个里 6 个被抓到；幸存的 1 个是等价变异（第一处 `scm_parked` 判断在 `adsp_running` 为真时不可能成立，第二处才起作用） |
| `PaganiSpeaker/Test/check-pagani-speaker-host.py` | 全过；新增 smmu 3201 项 |
| `PaganiSpeaker/Test/mutate-pagani-speaker.py` | 18 个变异全抓到。默认天花板改动后 `core-keeps-last-run-held` 一度漏网，已补检查（下一轮从静音、单位增益开始） |
| `PaganiBattery/Test/check-pagani-battery-host.py` | 全过；新增 props 282 项、powerd 21 键 |
| `PaganiSensors/Test/check-pagani-sensors-host.py` | 全过（它自带一份 ADSP 测试，QRTR 改动不影响） |
| `scripts/build-pagani-{adsp,speaker,battery}.py --tree … --out 草稿目录` | 三个归档都 PASS，零告警；符号审计通过 |

主机测试不能证明任何东西在手机上真的工作。

## 7. 没做的、不确定的

1. **SoCCP**：`research/soccp` 是空的。DT 里 SoCCP 有 `pong` 中断，像是引导链已经拉起的处理器【推断】，ADSP 是否依赖它【未验证】，表现为 B4 `FAIL st=-11`。
2. **I2C hub 的互连票**：安卓给 hub 投 `qup-core`、`qup-config`，我们不投，只投时钟。A1 是第一次验证。
3. **电池健康页**：`BatteryProps.h` 说要配合 `IN/battery/integrate` 里的 MobileGestalt 修补，但这个目录不存在（工作流被切断）。没有它，设置里的电池健康页会转圈、显示 0 %。不影响电量和充电显示。
4. **认证时的中断**：所有 SPI 都在启动核上。如果 TZ 不让出，auth 期间中断会被压住，看 B4 的 `intr=`、`us=`。
5. **运行中 DSP 崩溃**：没有重启，也不做 PAS 关闭，声音和充电视图要等重启；功放会下电，通道会关。
6. **喇叭电平**：−12 dBFS 的瓦数基于升压步长的推断。真正的安全要 Re、F0 标定或 SpeakerBoost。
7. **`armcpu_spi_attach`** 没有加锁，并发挂接可能丢一个处理函数。文件归别的代理，没动。
