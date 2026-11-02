# 屏幕亮度修复方案

全程只读。没有运行 adb 或 fastboot，没有改源码、脚本、种子、镜像，也没有提交。文中概率都是【推测】。

## 0. 结论

1. **根因**：帧通路是活的，CPU 到 MDSS 寄存器口这一段不通，读被总线以 0 应答。最可能是接口时钟或接口域，把握约 55%。
2. **下一轮**：只加一个 `pagani-panel` 位，不加新参数，命令行只多 1 字节。探针分 5 段按风险从低到高执行，全部进 U 记录，约 20 行。同一轮还能顺带验证 CA 是否卡死。
3. **修复**：路线 A（真 DCS）视探针结果决定。路线 B（GPU 拷贝时乘系数）不依赖 DSI，只能调暗，上限是固件亮度 `0x599`。
4. **推荐**：下一包带探针，B 同时开工。探针若显示 AHB 关着，A 只要约 20 行，优先走 A，B 保留为自动兜底。

## 1. 根因判断

| 解释 | 把握 | 依据 |
|---|---|---|
| 接口时钟或接口域没通，含 CESTA 重初始化的副作用 | 约 55% | 【读码】安卓读寄存器前先投票 GDSC 和时钟，XNU 没投过。SM8750 独有 CESTA/CRM，第二阶段 ClockDxe 在屏幕运行时重写 CRMC。帧走 MDP/byte/pixel，AHB 只管寄存器口 |
| 访问控制或总线断连让读返回 0（XPU、NoC 断连） | 约 25% | 【真机】读 0 且不中止、不挂死。本机被拒访问的已知结果是同步外部中止，所以只是候选 |
| 只在帧内可读 | 约 10% 以下 | 【真机】两次开机至少 5 个时刻全 0 |
| XNU 前 1.4 s 自己弄坏 | 约 5% 以下 | 【读码】没有代码碰 dispcc。【真机】Build 50 没有 GPU 驱动也读到 bit 0 为 0 |
| 地址错、映射错、DSI0 没工作、S2 未映射 | 基本排除 | 反汇编、设备树和固件一致，常量版本号不该读 0 |

**还缺的证据**
1. dispcc 的时钟和 GDSC 实际状态。
2. 是整段 `qhs_display_cfg` 读 0，还是只有 DSI 读 0。
3. 高密度相位采样。
4. XNU 接管时刻的读数。

**对调研结果的更正（都影响方案）**
- **`pagani-dpu-probe=4` 在现有包里不进日志。** D 记录要 `pagani-ramoops` 位 1（`RamoopsLog.c:1449`），现在是 `=1`，屏幕覆盖层也关了，另外还要 19 字节。
- **12:15 挂死那轮 `mask=1`【真机】。** 不管快照读没读完，D 行都不可能进日志，“一行没留下”不是挂死证据。挂死与探针的关系只有时间吻合。
- **`NativeAdrenoPublish.cpp:977` 不是转发路径，是标记流。** 真正的隧道上限是 `NativeAdrenoHandoffLast=0x7005`（`NativeAdrenoPublish.h:286`），加方法必须改 Adreno830，共三处。
- **`disp_crm` 的时钟是 dispcc 索引 6，即 `DISP_CC_MDSS_AHB_CLK`。** 读 CRMC 的门槛是 `ahb` 和 `rscc_ahb` 都开。只看 `rscc_ahb` 不够。
- **翻译器版本不同。** 我们是 `720ef1ce`，上游是 `624bab39`。上游 `scanout_opaque` 在 13T 跑通的结论不能直接照搬，要先过主机检查。

**可以立刻用的发现**：现有内核 `f7f9908f` 下，`A830_PANEL=67` 不改代码就能验证 CA 是否卡死。`=67` 是 1+2+64，字节数不变。`pagani_panel_publishes` 会放行，`requests` 进日志，DCS 重试 60 s 后停止（`PanelBrightness.h:127`、`PaganiPanel.cpp:184`）。

## 2. 下一轮探针

### 2.1 开关与文件

- **开关**：`pagani-panel` 位 7（128）。其余新位见第 4 节。
- **新文件 `drivers/PaganiPanel/PanelProbe.h`**：不含内核头，只用 `peripheral_io`，可以在主机测试。
- **`PanelBrightness.h:26-37`**：加 `PAGANI_PANEL_OPTION_PROBE=128`。
- **`PaganiPanel.cpp`**：在 `:183` 之后用 `kernel_thread_start` 起线程。不用 report 定时器，因为这里要睡眠，不能占面板工作循环。
- **`Mapping.h:9`**：`map` 加一个可选参数传 `kIOMapReadOnly`。新窗口全部只读映射，误写会直接报错。
- **`BootProgress.c:1240`**：`start_reset_paths` 末尾加 E 行。
- **新测试 `Test/panel-probe.cpp`**：
  - `write` 回调直接中止。
  - 校验门闩：GCC 关时不碰 dispcc，AHB 或 GDSC 关时不碰 MDSS。
  - 校验面包屑先于读取。
  - 校验最长一行小于 120 字节。

### 2.2 时序（总时长 420 s）

线程启动约在 1.5 s，依次睡 48500、176000、60000、60000 ms。第一次读一个新块之前，先写 `next` 行，再 `IOSleep(3500)`，保证 UFS 快照（周期 3 s）已经带走这一行。

| 段 | 时刻 | 读什么 | 风险 |
|---|---|---|---|
| E | 0.13 s | DSI0 `0,4,0x11c`，GCC `0x127004`（`ml_io_map`） | 无新增，都是已读过的块 |
| P0 | 50 s | DSI0、GCC、TE GPIO 86（`0xf156004`） | 几乎无。GPIO 86 不在 `qcom,gpios-reserved`（36–39、74）内 |
| P1 | 226 s | dispcc | 低。门闩见下 |
| P2 | 286 s | PHY、RSCC、SCC、CRMC | 低到中，带门闩 |
| P3 | 346 s | INTF_1、MDSS 顶层、DSI1 | 中。放在最后，带门闩 |

### 2.3 逐段内容与日志格式

全部是 `boot_progress_trace`，即 U 记录。约 20 行，约 2.5 KiB。

**E**
```
panel: e0 ver %x ctrl %x clk %x gcc %x
```

**P0**：先各连读 1024 次测延迟，再无延时循环 100 ms。每圈读 DSI `0x0,0x4,0x8` 和 TE。
```
panel: p0 lat gcc %u te %u dsi %u
panel: p0 dsi n %u nz %u or %x/%x/%x first %x@%u
panel: p0 te rise %u per %u-%u us high %u%%
panel: p0 gcc ahb %x axi %x
```
- `lat` 是 1024 次的总 tick 数（19.2 MHz）。DSI 明显高于 GCC 说明疑似陷入 EL2。
- `first` 是第一个非 0 值及其微秒数。
- `gcc` 读 `0x127004`、`0x127008`。

**P1 门闩**：`v=[0x127004]`，要求 `(v&1) && (!(v>>31) || (v&2))`。bit 1 是硬件门控，这时 CLK_OFF 可能读 1，【真机】`0x71010` 就读到过 `0x80000003`。不满足时打 `panel: p1 skipped gcc %x`，整段跳过。

| 行 | 地址 |
|---|---|
| `panel: p1 gdsc %x/%x int2 %x/%x misc %x` | `0xaf09000/04`、`0xaf0b000/04`、`0xaf0f000` |
| `panel: p1 ahb %x rcg %x/%x nongdsc %x rscc %x %x` | `0xaf080b0`、`0xaf08360/64`、`0xaf0c004`、`0xaf0c00c`、`0xaf0c008` |
| `panel: p1 mdp %x rcg %x/%x lut %x vsync %x pll %x %x` | `0xaf08010`、`0xaf08150/54`、`0xaf08020`、`0xaf08030`、`0xaf00000`、`0xaf01000` |
| `panel: p1 link byte %x intf %x pclk %x esc %x rcg %x %x %x` | `0xaf08034/38/04/44`、`0xaf08180`、`0xaf08108`、`0xaf081b8` |
| `panel: p1 phase n 64 off ahb %u mdp %u byte %u pclk %u dsi nz %u lat %u` | 每 250 µs 一组，共 64 组，覆盖约两帧 |

**P2 门闩**：RSCC、SCC 要 `rscc` 在跑。CRMC 还要 `ahb` 在跑。PHY 与 DSI 同属一段地址，不设门闩。
```
panel: p2 phy %x %x %x %x          (0xae95000..c)
panel: p2 rsc pwr %x wrap %x %x %x scc %x %x %x %x
panel: p2 crmc %x %x %x %x %x
```
- `pwr` 是 `0xaf202d0`，bit 12 表示 GDSC 塌缩成功过。
- `wrap` 是 `0xaf30000` 的 `+0,+0x24,+0x40`。
- `scc` 是 `0xaf31000` 的 `+0,+4,+8,+0x10`。
- `crmc` 是 `0xaf27800` 的 `+0xc,+0x18,+0x40,+0x280,+0x2a8`。

**P3 门闩**：`ahb` 开、CORE GDSC 上电、显示为开。否则打 `panel: p3 skipped ...`。这条门闩是降低挂死风险的主要手段。
```
panel: p3 intf te %x fc %x->%x int %x->%x ar %x
panel: p3 mdss ver %x intr %x
panel: p3 dsi1 ver %x ctrl %x
panel: p3 done
```
- `intf` 读 `0xae36000` 的 `+0,+0xac,+0x298,+0x2b4`，`fc`、`int` 间隔 1 s 读两次。
- `mdss` 读 `0xae00000` 的 `+0,+0x10`。
- `dsi1` 读 `0xae96000` 的 `+0,+4`。

### 2.4 判读

| 现象 | 结论 | 下一步 |
|---|---|---|
| `e0 ver` 非 0，1.5 s 变 0 | XNU 自己弄坏 | 二分启动代码 |
| P0 `nz>0` | 帧相关 | A2 |
| `ahb` 使能为 0，或 CLK_OFF=1 且不是硬件门控 | iface 断 | A1 |
| dispcc 全 0，GCC 正常 | 整段配置空间读 0 | B |
| 全部时钟开，PHY 非 0，DSI 为 0 | 控制器专属，疑似访问控制 | B |
| 全部时钟开，PHY、MDSS、INTF 都是 0 | MDSS 整段读 0，CESTA 嫌疑 | B，再做固件对照 |
| INTF `fc` 增长，只有 DSI 为 0 | 子块问题 | B，可再评估混合器 alpha |
| 挂死或中止 | 面包屑定位分界 | B |

**风险说清楚**：E、P0 没有新增风险。P1、P2、P3 不是“不可能挂死”。如果触发 `E` 中止，会打印 ESR 和 FAR，20 s 后复位。如果读挂，靠看门狗冷复位。这种情况下安卓起来后 USB 可能要插拔一次，这是全程唯一可能要用户动手的地方。

## 3. 修复路线

### A. DSI 可用时

**A1：强开时钟**。新增位 9（512）。只置 enable，不动 RCG，不动 `DSI_CLK_CTRL`，不切换 `DSI_EN`，不软复位。

1. 映射可写的 dispcc 窗口，在 `PaganiPanel.cpp:162` 之前调用：
```c
uint32_t v=peripheral_read(disp,0x80b0);
if(!(v&1)) peripheral_write(disp,0x80b0,v|1);
for(n=0;n<1000 && (peripheral_read(disp,0x80b0)>>31);++n) disp->delay(disp->context,1);
```
2. 记日志，确认三项：`ver` 为 `0x2009xxxx`，`ctrl` 含 bit 0 和 bit 2（参考值 `0x1f5`），`trig` 低 4 位是 4。若 RCG `ROOT_OFF` 为 1，不继续，改查父 PLL。
3. 版本号非 0 后，现有路径不用改。`pagani_panel_first_dcs` 写 `51 05 99`，FIFO 是 `0xc0390003,0xff990551`。序列见 `PeripheralIO.h:194-212`：保存寄存器、`TPG_CTRL=0x30006`、写 `0x17c`、`DMA_CTRL`、`DMA_LEN=8`、`0x90=1`、等 `0x110` bit 0、恢复。
4. 成功后走 `PaganiPanel.cpp:192-212` 的发布顺序。
5. 第一包保留 bit 6，真 0x28/0x29 单独一轮再开。

**A2：帧相关**。`PeripheralIO.h` 加 `DSI_PANEL_GATED=4`：
- 入口每 100 µs 轮询版本号非 0，最多 200 次，否则返回 -19。
- 不等帧间隙，改用 `busy=2`。在关闭窗口里 STATUS 读 0，会被误判为空闲。
- 写完 FIFO 回读 `0x15c==0x30006`，不符就恢复并返回 -19。
- `start` 里改用轮询到的版本号。

**A3：CESTA 引起**。走固件对照，把 ClockDxe 的 `disp_cesta` 初始化跳过。这是第二轮的事，失败可能黑屏，不进自动首测。

### B. 不依赖 DSI 的软件调暗

新增位 8（256）。语义是自适应：DCS 能用就走真 DCS，首条 DCS 失败才走软件调暗。

**1. 系数放在哪里**：GPU 拷贝这一步。`k` 用 Q8，256 表示原样走现有 blit，零额外开销。

**2. CoreBrightness 接法（`PaganiPanel.cpp`）**
- `pagani_panel_publishes` 加上位 8。
- `publish()` 的 `max` 和 `setProperties:224` 的夹紧都改成 `0x599`。滑块 100% 就是固件亮度，没有死区，也不可能超过固件亮度。
- 其余沿用现有发布顺序：先 `IODisplayParameters`，再放行和 `backlight-control`，最后 `registerService`。
- 渐变继续用 `pagani_panel_tick`，只在 `sendPacket:65` 分流，不碰 DSI：
```c
static inline uint32_t pagani_soft_dim_q8(uint32_t level,uint32_t max){
    enum{MIN=40};  // 约 1.7% 的光，上机目测再调
    return level>=max?256:MIN+(256-MIN)*level/max;
}
```
- 收到 `0x51` 就算系数，变化时调 `native_display_set_dim(q8)`。`0x28/0x29` 直接返回 0。level 0 取下限，不取 0。

**3. 线协议**
- `NativeGOPWire.h` 加 `NGP_METHOD_WAIT_SWAP3=6`，记录 `NGPSwap3{NGPSwap swap; uint64_t target_offset; uint32_t dim_q8,reserved;}`。
- `NativeGOPPresenter.cpp:369` 后加 case。
- `NativeAdrenoPublish.h:286` 改成 `0x7006`，`.cpp:105` 和 `:871` 加上新方法。
- `GpuScanoutClient.h:135` 先试 SWAP3，失败退回 SWAP2。

**4. 静止画面重绘（`NativeLegacyDisplay.cpp`）**：内核重发上一帧，不绑定任何 swap。
- `:780` 接受 offer 时记下 `shownMemory`、`shownSurface`（retain）及尺寸，并清 `dimDirty`。
- `:731` 空白帧路径清掉记录，否则会把旧帧画回来。
- 新增：
```cpp
void redrawDim() {           // 经 runAction 进入工作循环门
    dimDirty=true;
    if(stopping||transferred||!shownMemory||flip.on()||pendingSwapRequest())return;
    if(native_legacy_gpu_offer_frame(this,shownMemory,shownStride,shownW,shownH,shownSurface,0))
        {dimRedraw=true;dimDirty=false;transferred=true;}
}
```
- `:888` 开头：`if(dimRedraw){dimRedraw=false;transferred=false;kickSwapQueue();return;}`
- `kickSwapQueue` 末尾：`if(dimDirty&&!transferred)redrawDim();`

借用 `transferred` 后真实帧只排队，不会被拒绝后退到 CPU 拷贝。上一帧的表面在下一帧退役前不会被改动【读码】。

**5. 插件**
- `GpuScanoutPresenter.h:28` 的 `Frame` 加 `dimQ8=256`。
- `:74` 之后：`if(frame.dimQ8<256 && dim_) return presentDim(...)`。
- `Client::open` 里预建管线，首帧不卡。
- `presentDim` 第一次失败就 `dim_=nil`，退回 blit。宁可不调暗，不丢帧。

着色器按上游 MIT 的 `scanout_opaque` 改写，新建 `shaders/scanout_dim.ll`。`k` 放在 `p[3]` 高 16 位，不改元数据。核心几行：
```llvm
%ds2 = and i32 %ds, 65535
%k   = lshr i32 %ds, 16
%rb  = and i32 %v, 16711935
%g   = and i32 %v, 65280
%rbm = add i32 (mul %rb,%k), 8388736      ; 再 lshr 8，and 0xff00ff
%gm  = add i32 (mul %g,%k), 32768         ; 再 lshr 8，and 0xff00
%px  = or (or %rb2,%g2), -16777216
```
`build-adreno-metal-plugin.py:58` 前用 `llvm-as` 汇编成头文件嵌入，经 `native_bridge_metal_library_with_air` 加载。不动 Rust 桥。翻译器版本不同，要先过 `bridge/present_host.mm`。

**6. 不影响着色器包和流畅度**
- `runtime_id` 只取翻译器 pin 和补丁状态（`build-adreno-metal-bridge.py:89`）。`buildId` 再加 Turnip 的 LC_UUID。两者都不变，所以包不失效【读码】。
- 代价是插件 CDHash 变化，要重做信任包和种子。
- `k=256` 路径与现在完全相同。调暗时才走计算核。
- 【真机】现有 blit 的 `copy` 约 0.7–1.7 ms，帧率 100 上下。验收：滚动时中位 `copy` 不超过 4 ms，帧率不低于 100。不达标改全屏渲染通道。
- 不做逐帧 CPU 乘法：CPU 拷贝一帧 143–285 ms。CPU 回退帧首版不调暗，属已知限制。
- 【推测】`k=0.16` 时只剩约 40 级，近黑可能有色带，以后可加抖动。

**7. 自动验收（不用人）**
- 日志有 `requests>0`、`writes` 增长，帧率不掉。
- 位 7 与位 8 同开时，60 s 处自检一次：把亮度设到一半，2 s 后恢复。前后各采样扫描缓冲 256 个像素求和，打 `panel: dim q8 %u sum %u->%u`，比值应接近 `k`。

**上限**：不能超过 `0x599` 对应的亮度。室外偏暗是硬限制。

## 4. 风险、回退、命令行字节

新功能全放在 `pagani-panel` 掩码里，不用腾字节。三位数只多 1 字节。上限检查是 `len(line) > 1000` 才报错（`payload.py:35`），1000 本身合法。万一以后真要腾，先看 `pagani-kbtrace=1`（17 字节），是否可删要先确认。

| 档 | 值 | 首测（997） | 完整包（998） |
|---|---|---|---|
| 现状 | 65 | 997 | 998 |
| 只验 CA，不改代码 | 67 | 997 | 998 |
| 探针 | 193 | 998 | 999 |
| 探针加发布（下一轮推荐） | 195 | 998 | 999 |
| 软调暗 | 321 | 998 | 999 |
| 探针加软调暗 | 449 | 998 | 999 |
| A1 | 577 | 998 | 999 |
| A1 失败自动转软调暗 | 833 | 998 | 999 |
| 不发布背光（回退） | 81 | 997 | 998 |

| 路线 | 风险 | 回退 |
|---|---|---|
| 探针 | P1–P3 可能中止或挂死 | 回 `=65`，改值不用改代码 |
| 发布背光 | 13T 第一次走，CA 可能卡死（sheng int-236 同类） | `=81`，或 `A830_DROP_ARGS=pagani-panel` |
| A | 第一次写 dispcc、第一次写 DSI，可能出现 -110 | 回 `=65`；用 `=833` 时自动转软调暗 |
| B | 计算核耗时、翻译器不兼容、GPU 失败、色带 | 回 `=65`，运行时失败自动退回 blit |

构建时沿用现有环境变量，只改 `A830_PANEL`（`make-a830-layout.sh:245-250`）。命名镜像要连 `.payload.json` 一起拷。

## 5. 推荐顺序

1. **探针进下一包，值用 `A830_PANEL=195`。** 约半天工作量，7 分钟出结果，同时答完根因和 CA 卡死两件事，符合“修完再测”。若担心 CA 卡死影响同包其他项，先用 `=193`，再补一轮 `=195`。
2. **B 同时开工，先做内核部分。** 不管根因如何，B 都是需要的保底，用户要的是能调。
3. **看结果决定主线。** 若判读落到 A1，这是最省的路，亮度上限也能恢复到 `0xdbb`，完整包用 `=833`，B 留作自动兜底。其他结果就以 `=321` 交付 B。

A 优先于 B 的理由：真 DCS 没有灰阶损失，不增加 GPU 负担，也不受 `0x599` 限制。但只有探针能告诉我们它走不走得通，所以探针排第一。

**交付时要同步改的文档**：`PaganiPanel/README.md` 位表、`PeripheralIO.h:231-232`（“读未上电块必挂死”未被证明）、`功能盘点.md:63`、`Adreno830/docs/PROGRESS.md`。