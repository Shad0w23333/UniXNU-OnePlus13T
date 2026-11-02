# 屏幕亮度调研：别人怎么解决与软件兜底

全程只读，没有运行 adb 或 fastboot，没有改源码，也没有提交。临时文件只在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/brightness-others`。

标记说明：
- 【真机】：13T 现场日志。
- 【他机】：别的机型的现场记录。
- 【读码】：读源码或反汇编确认。
- 【推测】：没有直接证据。

用户态地址是 21H461 dyld 缓存的未滑动地址，内核地址来自 `kernel-68.macho`。

## 结论先行

1. 没有人遇到过 DSI 寄存器全读 0。asteroids 和 zorn 走同样的映射方式，都读到了正常值，这个现象是 13T 独有的，也没有现成解法。
2. 这块屏只能走 DCS。设备树、PMIC 节点、供电表都没有第二条亮度通道。
3. 系统没有现成的软件调暗路径。Reduce White Point、色彩矩阵、亮度修正最后都交给显示管线，而我们的内核全部返回 `0xe00002c7`。好处是这些入口都能接管。
4. `IOMobileFramebufferSetGammaTable` 在 QuartzCore、CoreBrightness、backboardd 里都没有调用者，走这条路没有意义。
5. 改 `GpuScanoutPresenter.h` 只算重编插件，不会让 m2vpack 失效。任务里“改插件运行时就失效”的说法需要收窄到翻译器（metal2vulkan）。
6. 软件调暗只能在开机亮度 `0x599` 以下调，调不亮。它是保底方案，修 DSI 访问仍是主线。

## 1. 上游和工作区的亮度现状

| 设备 | SoC / 面板 | 模式 | 通道 | 结果 |
| --- | --- | --- | --- | --- |
| asteroids | SM7635，RM69220 AMOLED，DSI v2.8 | 视频模式 DSC | DSI TPG FIFO，`51 高 低` | 【他机】T37d：iOS 调亮度、熄屏亮屏都通，已是默认开启（`asteroids.toml:106,109`） |
| zorn | SM8650，O11，DSI v2.8 | 命令模式 DSC | 同上，加 0x52 读回 | 【他机】寄存器级通过，307→64 读回成功，没有系统滑块（`zorn.toml:136-147`） |
| sheng | SM8550，LCD，两颗 KTZ8866 | — | GENI I2C | 【他机】服务侧机制完整，提交 `5553c9a`、`38540db`、`5d045d0`、`fb4839d`、`5b0e9c3`、`3521463`、`684e926` |
| alioth | SM8250，k11a | 命令模式 DSC | 同 asteroids | 只有主机测试。唯一一次上机内核先挂了（`2026-10-30-brightness-buttons.md:3-14`） |
| ace6 | SM8750，AA605 | 命令模式 DSC | 抄的 Pagani 旧驱动 | 没有上机记录，还带着低字节在前的旧错误 |
| IssacL04 / oneplus13t | SM8750，AA594 | 命令模式 DSC | 同上 | 从未工作。Build 50 显示 `DSI disabled (e00002d8)`，之后一直写 “brightness remains deferred”（`oneplus13t.toml:189-194`） |

- 其他 SM8750 机型（hummer、dada、dada2、erhai、elden）没有查到 DSI 寄存器记录。piano 仓库取不到，只有 `elden.toml` 写明未验证过 XNU 显示。
- pudding 同样是 `bl_ctrl_dcs`，只查到面板事实，没有驱动。kebab、cas、luming 的记录只到命令模式面板和帧缓冲，没有亮度驱动记录。

**IssacL04 最新状态。** 本地克隆 `48b93fb` 就是 GitHub 当前 HEAD（2026-10-31 20:23 UTC）。近期提交全是 A830 Metal，亮度相关内容自 Build 50 起没有进展。它的 `UniXNU-Pagani-Drivers` 最后推送是 10-27，文档还写 low-byte-first。

**DSI 读 0 或 DSI disabled 的情况。**
- zorn 读到 `version=20080000 CTRL=1f5 STATUS=0 TRIG=80000004 LANES=1f1f`（`progress-2026-10-28-brightness.md:23`）。
- zorn 的 `err1` 不是读 0，而是自己把整个 `SPLIT_LINK` 当零检查，后来改成只看 bit 0（同文件第 34 行）。
- 13T 的 `DSI disabled` 就是读 0，没有人解决过。
- zorn 还踩过两个坑：写后 1 ms 读回旧值，10 ms 才是新值；早期 `IODelay` 可能阻塞，所以改用计数器等待。

**补充推断【读码加真机】。** 46.5 s 的 `rc -19` 和 `mode 0` 说明，1.5 s 启动时版本寄存器也已是 0。`PanelBrightness.h:145` 只有版本非 0 才强制发送，否则 rc 不可能保持 -19。所以从启动起就读不到，不是运行中变成 0 的。这只是读码推断，1.5 s 那几行日志（`seq 226-240` 或 `245-259`）已经丢了。

**一条线索，不是定论。** 13T 的 Mu UEFI 会第二次运行厂商 ClockDxe。
- 补丁把 `Clock_CESTADisableDependencies` 去掉（`Binaries/pagani/README.md:16-18`，文件偏移 `0xc6b0` 的 `bl sub_C494` 改成 `mov w0,wzr`）。
- 显示 CESTA 的依赖时钟表是 `gcc_disp_ahb_clk`、`disp_cc_mdss_non_gdsc_ahb_clk`、`disp_cc_mdss_rscc_ahb_clk`、`disp_cc_mdss_rscc_vsync_clk`（数据 `0x3e8a8`）。
- SM8650 的 bale_b 也有同样补丁，所以它不能单独解释 13T 和 zorn 的差异。

## 2. 命令模式加 DSC 的公开例子与绕过

- **主线 SM8750 MTP：** `panel-novatek-nt37801.c:201-216` 在高速模式下用 `mipi_dsi_dcs_set_display_brightness_large`。这是完整初始化路径，不是交接模式。v4 封面信还写着 DSI 面板 “still shows darkness”，v5 才修时钟和 PHY。
- **厂商交接路径：** `dsi_display_cont_splash_config` 第一句注释是 “Vote for gdsc required to read register address space”，随后投票 core 和 link 时钟（`dsi_display.c:5833-5868`）。这和我们的差别很关键：安卓读寄存器前先投票，我们什么都没投。
- **simplefb：** 只有像素内存，没有亮度接口。我没有找到“simplefb 加命令模式 DCS 调亮度”的公开实现。asteroids 和 zorn 就是最接近的公开例子。

**确认只能走 DCS【读码】：**
- `running.dts:41187` 是 `bl_ctrl_dcs`，全树 34 个面板节点是它，另有 2 个 `bl_ctrl_external`。
- `dsi_panel.c:3268-3269` 映射到 `DSI_BACKLIGHT_DCS`，`:918-919` 进入 `dsi_panel_update_backlight`。
- PMIC 的 `qcom-amoled`（ibb、elvdd、oledb）是 `status="disabled"`，而且 `qcom,swire-control` 由面板 DDIC 控制（`running.dts:11972-12010`）。
- 面板电源只有 vddio、vdd、vci、tvdd 和一根 gpio1，没有 I2C。
- ELVDD 不是亮度旋钮，动它只会关屏或损屏【推测】。

## 3. 软件兜底

### 3.1 CoreBrightness 需要什么，发布有什么风险

条件沿用 `brightness.md` 第 2 节：
- 服务已注册，`backlight-control=true`。
- `IOObjectConformsTo` 能匹配 `AppleARMBacklight`。
- `IODisplayParameters` 先于 `backlight-control` 就绪。
- `NativeSandboxGrantProcess=backboardd` 放行 `FadePeriod`。

这些 `PaganiPanel.cpp:192-209` 都已做到。`pagani-panel=3` 能不发 DCS 就发布，但当前 tick 会重试 -19 共 60 s 后停止，所以需要新增不碰 DSI 的软件模式。

风险：
- **int-236 型 CA 卡死：** 触发条件是发布了 `backlight-control` 却不满足一致性。我们是真子类，且顺序正确，把握较高，但 13T 从未走过。回退包是 `pagani-panel=81`。
- **身份差异【推测】：** 13T 现在是 iPhone 15 Pro 的 MG 身份，已知走通这条路径的机型身份不同，OLED 或 AOD 分支可能有新路径。`product-id` 仍是 iPad 的（`make-a830-layout.sh:207`）。
- **CB 新模块：** 发布后 CB 显示模块才启动，夜览等会开始调用矩阵接口，内核返回 Unsupported，读码看无害。

### 3.2 系统现成路径核实【读码】

| 功能 | 调用链 | 内核入口 | 现状 |
| --- | --- | --- | --- |
| 减弱白点、颜色滤镜 | `-[AXBDisplayFilterManager _applyMatrixFilter:whitePointReduction:]`（`0x21c140b58`）→ `setAccessibilityColorMatrix:scale:` → `IOMFBDisplay::set_framebuffer_ax_matrix`（`0x189505be0`） | `SwapSetColorMatrix` 选择子 69 → 虚表 227；成功后 `SetParameter(3)` → 槽 276 | 227 返回 Unsupported，函数提前退出 |
| 夜览、CB 数字调暗 | `-[CBCAManager updateDigitalDimmingBrightnessScaler:]`（`0x1b62256cc`）→ `setColorMatrix:scale:rampDuration:` → `update_color_matrix`（`0x18950acb4`） | `SetMatrix` 选择子 55 → 槽 202；`SetBrightnessCorrection` 选择子 50 → 槽 207 | 都返回 Unsupported |
| 最大亮度 | `_updateCoreSupport` 用 `setMaximumBrightness:`，滤镜开启时为 0.89 | `update_framebuffer_locked` 标志 0x100 → 槽 207 | 同上 |
| 伽马表 | 选择子 17 → 槽 200 | 框架有导出 | QuartzCore、CoreBrightness、backboardd、thermalmonitord、IOMFB_bics_daemon、IOMFB_FDR_Loader、mobile_obliterator 都没有调用 |
| `CADisplay` 亮度 | — | — | `-[CADisplay]` 没有亮度属性 |

- 这些槽位都不在 `native_legacy_backend_guard` 名单（211、245、246、306、307、338）里。在基类里只是返回错误，不会 panic，也都能覆写。
- 软件滤镜走不通：`_forceSoftwareFilters` 和 `_allowSoftwareFilters` 在 iOS 上写死返回 0（`0x21c14013c`、`0x21c140144`）。
- CB 有原生数字调暗，但在新背光架构路径 `SetBLDriverNitsValue`（`0x1b61aedac`）里。条件是 `HardwareAccessibleMinNits > MinNitsAccessibleWithDigitalDimming`，它还用 1.0 和 0.999999 来回抖动缩放值，用来强制出帧。
- `setAccessibilityColorMatrix` 也会让 CA 走一次失效更新，读码看能带来新帧；这点要上机确认。

### 3.3 系数放在哪一步

| 位置 | 流畅度 | 着色器包 | 判断 |
| --- | --- | --- | --- |
| 插件呈现器 `GpuScanoutPresenter.h:60` | k=1 时仍走 blit，零影响；调暗时换计算或渲染通道，耗时要实测 | 不失效 | **首选** |
| 内核 CPU 拷贝 `native_legacy_copy_rows` | 一帧 143–285 ms（`PROGRESS.md:235`），不可用 | 无关 | 只用于回退帧 |
| DPU 混合器常量 alpha | 零 GPU 开销，静止画面立即生效，10 位精度 | 无关 | 最优，但前提是 MDSS 能访问 |
| DPU PCC、GC | 同上 | 无关 | 固件路径可能不经过 DSPP【推测】，排在 LM 之后 |
| SSPP 逐像素 alpha | 仍要写每个像素，GPU 开销不省 | 不失效 | 只换精度。上游注释说 sheng 的固件开了 alpha（`SRC_FORMAT 0x237ff`），13T 未验证 |

**着色器包结论【读码】：**
- `PrimeShaders.h:195-198` 写明 “a plugin rebuild alone keeps what was finished”。
- `runtime_id` 只由翻译器 pin 和补丁状态决定（`build-adreno-metal-bridge.py:87-89`），Turnip 那边看 LC_UUID。
- 改呈现器只变插件 CDHash，要重做信任包和种子。
- 新增的计算核不在包里，首次使用会现编，所以要在 `open` 时预编译。

**实现要点：**
- 插件已有 `native_bridge_metal_library_with_air`。
- 上游 `scanout_opaque`（MIT，`f72c5d6`）可作模板。IssacL04 的 Run 88–90 已让它在 13T A830 上跑通，用户看到约 60 帧，后续读数 28–40 FPS。他写明 G 耗时含交接，不是纯 shader 时间。
- 如果 SSBO 太慢，改全屏渲染通道。
- 线协议加一个可选方法，`NativeAdrenoPublish.cpp:977` 对 `0x7000..0x8000` 直接转发，不用改 Adreno830 主体。

**静止画面问题。** 内核收到亮度变化时，CA 不一定出新帧。可以重新提交上一帧，提早退役带来的撕裂只会出现一次。不要在原地反复重算，多次放大缩小会累积误差。

**AOD 与关屏。** CB 发 level 0 时不要改系数，继续走现有黑帧。

### 3.4 OLED 观感与功耗【推测】

- 在伽马编码上乘 k，亮度约按 `k^2.2` 线性缩放，幅度正确。OLED 发光功耗也按电流下降，这点和 LCD 不同，但 DDIC 与电源损耗不会变。
- k=0.25 时只剩约 64 级，渐变会有色带，近黑细节会压缩。着色器里加抖动能缓解。
- 比如滑块映射成 `k = x^0.45`，下限取 0.15 左右，100% 时等于开机亮度。这些值都要目测调。
- 上限是硬约束：DBV 固定在 `0x599`，室外偏暗，HBM 不可用。

## 4. 方案排序

| 排名 | 方案 | 把握 | 风险 | 工作量 |
| --- | --- | --- | --- | --- |
| 1 | 修 MDSS 访问，走真 DCS | 未知，约五成【推测】 | 只读探针可能挂死（`pagani-dpu-probe=3` 曾整机卡死），写入路径要验证 | 查因 1–3 轮上机；修好后驱动基本现成 |
| 2 | 发布背光加插件呈现器调暗 | 约 80%【推测】 | CA 卡死、计算耗时、只能调暗 | 2–3 天，含信任包和种子 |
| 3 | DPU 混合器常量 alpha（`LM_BLEND0_CONST_ALPHA_V12`，LM_0 在 MDSS 偏移 `0x45000`） | MDSS 可访问时约 70%，否则 0【推测】 | 读写挂死、刷新闪烁、闸门要扩 | 探针之后 1–2 天 |
| 4 | 方案 2 附带：覆写槽 227 和 276，让减弱白点生效 | 高，读码支持【推测】 | 低，不用发布背光，避开 CA 风险，但不是滑块 | 半天到 1 天 |
| 5 | UEFI 阶段发一次 DCS 抬高基准亮度 | 取决于 UEFI 阶段 DSI 是否可读 | 要改固件链 | 中 |
| 6 | 盲写一条 DCS 试探 | 约一成【推测】 | 现有恢复逻辑会把读到的 0 写回，必须改成固定常量 | 小，只做一次性实验 |
| 7 | CB 新背光架构原生数字调暗 | 低 | 新路径没人走过，校准参数齐全度未知 | 大 |

## 5. 建议的下一步

1. 先跑一轮只读探针：`pagani-dpu-probe=4` 看 GDSC，再跑 `=1` 看 INTF_1 计数。结果决定方案 1 和 3 还有没有戏。
2. 如果 MDSS 整体读 0 或挂死，直接转方案 2，并把方案 4 一起做。
3. 方案 2 上机时先用 `pagani-panel` 分档发布，确认没有 CA 卡死再开调暗，回退包同时准备好。

来源：
- [SM8750 DRM 系列 v4 封面信](https://lkml.iu.edu/hypermail/linux/kernel/2503.1/04460.html)
- [SM8750 dispcc 讨论：CESTA 说明](https://lkml.iu.edu/hypermail/linux/kernel/2412.0/04372.html)
- [SM8750 dispcc 讨论：时钟可不依赖 CESTA](https://lkml.iu.edu/hypermail/linux/kernel/2412.0/05323.html)