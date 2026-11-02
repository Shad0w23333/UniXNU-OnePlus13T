亮度修复结论摘要（全程离线，未碰手机，未改任何源码树）：

1. **直接原因（最可能，未在我们的包上证实）**：`PaganiPanel` 没起来。上游 build 50 的现场是 `DSI disabled r=e00002d8`，对应 `PaganiPanel.cpp:141-145`：没有服务就没有 `backlight-control`，滑块的值无处下发。
2. **就算起来也不工作**：还有三个确定缺陷。`PeripheralIO.h:161` 字节序反了，面板要 `51 高 低`，设备树表、`bl-inverted-dbv`、厂商 UEFI 三处一致。没设 `NativeSandboxGrantProcess`，`FadePeriod` 会被沙盒拒（sheng int-276）。`FadePeriod` 被忽略，且发送用 LP，厂商 0x51 用 HS。
3. **用户态要求已查清**：走旧背光路径，服务需已注册、`backlight-control=true`、符合 `AppleARMBacklight`、带 `IODisplayParameters`，收 `{brightness, FadePeriod, backlight-level}`。不需要 ADT 节点、sysctl、种子改动或新启动参数。
4. **面板**：AA594 P 7 A0020，DCS 12 位，正常最大 `0xdbb`，硬件最大 `0xffe`，最小 1，默认 `0x599`（35%）。
5. **参照实现**：asteroids（SM7635，Gunyah，视频模式）系统滑块真机通过；zorn（SM8650，命令模式 DSC）DCS 写加读回真机通过。
6. **原型已完成**：新增主机测试 70 项、原有 314 项、`test-peripherals.py` 全过；用内核参数编译通过，虚表槽位与审计名单一致。完整内核链接未做。
7. **未解决**：为什么 `DSI_CTRL` bit 0 读到 0，离线解释不了。方案不再因此放弃，改为记全寄存器，再强制试发一条同值 DCS。成功就发布，失败就不发布（系统行为同现状），现场进日志。
8. **新风险**：13T 第一次发布 `backlight-control`，有 CA 卡死的可能（sheng int-236 同类）。回退包去掉 `pagani-panel` 即回到现状。

报告路径：**未写入**。Write 工具拒绝子代理写报告文件（试的是草稿目录的报告稿），我没有绕过，也没再试 `/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/upstream/brightness.md`。按任务约定，完整报告在下方。

---

# 屏幕亮度：根因与实现方案

日期 2026-10-31。全程离线。原型只在草稿目录，从 GitHub 只读抓取的参考代码也放在草稿目录。

证据标记：
- 【读码】读源码或反编译确认。
- 【13T 实测】一加 13T 上的现场记录。
- 【他机实测】别的机型的现场记录。
- 【推测】没有直接证据。

用户态地址都是 21H461 dyld 缓存的未滑动地址；内核地址来自 `作者产物/ida/kernel-68.macho.i64`（原版 kext 部分与当前内核同址，自写驱动部分不同）。

## 1. 上游怎么做

| 来源 | 亮度实现 | 状态 |
| --- | --- | --- |
| `UniXNU-GopFramebuffer` | 没有亮度代码；`feat/ipados27-gpu-parity`、`feat/static-refresh` 也没有 | 【读码】 |
| `IssacL04-UniXNU-Pagani_SM8750` | 即 `PaganiPanel` 前身。`porting.toml:200` 写命令路径 “has not passed hardware testing”；`porting.toml:195` 写 “low-byte-first”（错的，见第 4 节） | 从未工作 |
| `UniXNU-UniXNU-sheng` 的 `package/AppleARMBacklight.cpp`、`BacklightProbe.cpp` | 两颗 KTZ8866 走 GENI I2C，服务侧机制完整 | 默认开（`board.toml:146`） |
| `UniXNU-UniXNU` 的 `patches/xnu/zzwb-native-sandbox-grant-set-properties.patch` | `NativeSandboxGrantProcess` 同时放行 `setProperties`（`11c8182`，rebase 副本 `be4873d`、`3606368`） | **我们已带上**（`kernel-build-a830-integrate3.log:58`） |
| `asteroids.toml:109,267`（代码 `rootstockos/UniXNU-asteroids`） | DSI0 `0x0ae94000`，v2.8，视频模式，TPG 命令 FIFO，HS，`51 高 低`，服务沿用 sheng | 【他机实测】T37d：iOS 调亮度、熄屏亮屏都通 |
| `zorn.toml:136-139`（代码 `kerrinz/UniXNU-zorn` 的 `native/display/ZornDsiBrightness.h`） | 命令模式 DSC，读到 `DSI_CTRL=0x1f5`、`TRIG=0x80000004`、`DMA_CTRL=0x14000000`、STATUS=0；HS 写 `51 高 低`，0x52 读回，307→64 | 【他机实测】寄存器级通过，未做系统滑块 |
| `alioth.toml:235-236` | DCS 0x51 + TPG FIFO | 明写 “Not verified on hardware” |

sheng 的提交：`5553c9a`、`38540db`、`5d045d0`、`fb4839d`、`5b0e9c3`、`3521463`、`684e926`（另有两组 rebase 副本）。

sheng 踩过的坑（`AppleARMBacklight.cpp` 注释，他机实测）：
- int-236（行 303-307）：发布 `backlight-control` 却不满足 `AppleARMBacklight` 一致性，CoreBrightness 不起 libEDR 服务，QuartzCore 同步发送卡住，**屏幕停止更新**。
- int-275、int-276（行 271、311）：backboardd 沙盒拒绝 `FadePeriod`，而 CoreBrightness 每次都带它。
- int-277（行 360）：`setProperties` 里 `runAction` 会把 backboardd 亮度线程堵在工作循环门上。
- int-278（行 361）：用中断事件源转交请求写不进去，要用定时器。

sheng 不能用 `AppleARMBacklight` 这个类名，所以复制虚表冒充（行 266-293）。我们不需要：`PaganiPanel` 是原版基类的真子类（`OriginalBacklightABI.h`、`build-pagani-peripherals.py:180`）。

许可：sheng、GopFramebuffer、Pagani 设备仓库、Pagani-Drivers、asteroids、zorn 都是 BSD-2-Clause-Patent，可移植。一加开源 display-drivers（提交 `6a196f79`）是 GPL，这里只取寄存器偏移和语义，原型没有复制其代码。

## 2. iPadOS 17.7.11 用户态怎么下发亮度

全部【读码】，个别点另注。

### 2.1 从滑块到内核

1. 控制中心和设置 → backboardd `-[BKDisplayBrightnessController setBrightnessLevel:reason:options:]`（`0x100082604`）→ `BrightnessSystem` 的 `DisplayBrightness` 属性。iOS 上没有 `corebrightnessd`，CoreBrightness 在 backboardd 进程内；`com.apple.backlightd` 是 backboardd 的 MachService（`launchd.plist`）。
2. `-[BLControl start]`（块 `0x1b6266588`）→ `findDisplays`（`0x1b6267664`）。
3. `useSyncBrightnessTransactionForDisplay:`（`0x1b626c794`）对我们为假，走 CADisplay 路径：
   - `CBU_SyncBrightnessTransactionsExists` 按 `/product product-id` 比表。我们的 `e9fefef6…`（`profile.toml:90`）不在表内。`CBU_ShouldWaitForALS` 同理为假。
   - `-[CAWindowServerDisplay brightnessAvailable]`（`0x189573100`）看 `CADeviceSupportsBrightnessTransactions`（`0x189683a04`）：MG `DeviceSupportsFrameSynchronousBrightness` 取自 ADT `/backlight`，我们的配置里没有这个节点，偏好未设。
   - 再看 `IOMFBServer::brightness_capabilities()`（虚表 `+0x130`）。【推测】我们这里为空：`update_brightness_capabilities`（`0x1896e0680`）在标志字节为 0 时早退，这与 sheng、asteroids 走旧路径相符。
4. `handleCADisplayArrival:`（`0x1b6269024`）→ `handleCBDisplayContainerStart` 的块（`0x1b62a2754`）→ `findBacklight`（`0x1b62a0ca0`）：`IOServiceGetMatchingServices`，条件 `{IOPropertyMatch: {backlight-control: true}}`。
5. 找到后 `handleDisplayArrival:` → `setupInternalModules`（`0x1b62a3d40`）：**必须** `IOObjectConformsTo(service, "AppleARMBacklight")` 才建 `CBDisplayModuleiOS`。
6. 找不到时打 “no backlight found”，容器不加入。**这就是现在的症状：滑块能动，值无处下发。**

### 2.2 `DisplayCreateWithID`（`0x1b62accc0`）的要求

- `IODisplayParameters` 必须存在，否则返回空。
- 从 `brightness` 读 `value`、`min`、`max`。`value` 为 0 时当作显示关。
- `new-backlight-architecture` 不存在或不为真时走旧路径，起 `brightnessThread`。**我们走这条。**
- `backlight-calibration-parameters`、`backlight-marketing-table` 可缺省，缺省表 5/140/400 尼特。

### 2.3 写入路径

`__DisplaySetBrightnessWithFade`（`0x1b62b8a9c`）→ `__DisplaySetLogicalBrightnessLegacy`（`0x1b62b9d98`）→ `__DisplaySetPhysicalBrightnessLegacy`（`0x1b62c6a5c`）→ `...OnThread`（`0x1b62c3964`）：

```
level = ceil(x * factor * (max - min)) + min
IORegistryEntrySetCFProperties(service, { "brightness": level,
                                          "FadePeriod": (int)(fade * 65536),
                                          "backlight-level": ... })   // __brightnessKeys @ 0x1ee884388
```

- 手动模式下 `x` 就是滑块值，线性（`0x1b62b9b34`，slope 为 0）。
- `DisplayBrightnessMin = (min+1)/max`，滑块到底不发 0。level 0 只在显示关的系数淡出时出现。
- `brightnessThread`（`0x1b62c363c`）写完 `usleep(fade)`：**渐变是驱动的活**，系统只发一次目标值。

### 2.4 发布 `backlight-control` 的连带效果（13T 从未走过）

- `CADeviceHasBacklight`（`0x189683834`）`dispatch_once` 查一次。为真后 `set_edr_enabled`（`0x189510b70`）、`register_libedr_callbacks`（`0x1895108d0`）、`EDRClient::client`（`0x1896ba828`）都会动起来。
- libEDR 服务由 CoreBrightness 打开显示时启动：`DisplayCreateWithID` → `__DisplayCheckEDRSupport`（`0x1b62b4a14`）→ `CBEDRServerStartServer`（`0x1b6256fd4`）。`com.apple.libEDR.Notification` 是 backboardd 自己的 MachService。
- 因此 **`IODisplayParameters` 必须先于 `backlight-control` 就绪**，否则 EDR 服务不起、CA 卡住。
- `once` 只查一次，服务必须在 backboardd 启动前发布。Stage7 约 2.5 s，首个显示客户端约 29 s（D2 日志 `29.008 R stage7`），时间宽裕。
- `CADeviceHasLCDPanel` 会变真（`/product` 没有 OLED 键）。读码范围内看到的消费者是 `AppleDisplay::update_display_limits`。

### 2.5 不会出问题的点

- `IOMFBBrightnessLevel`（`SetIOMFBBrightness` `0x1b61b2d68`）：`NativeLegacyDisplay` 虚表 44 槽是原版 `setProperties`（`0xfffffff005792404`），只认 4 个键，其余返回 `0xe00002c2`，无害。
- `CBSliderCommitTelemetry` 无条件创建，`getAPCE` 调 `IOMobileFramebufferGetBlock(73)` → 选择子 79（`0xfffffff0057799b4`）→ 虚表 224 槽（`0xfffffff00579fb98`）直接返回 `0xe00002c7`。不碰 `native_legacy_backend_guard` 的 panic 槽（211、245、246、306、307、338）。
- 不需要：ADT `/arm-io/backlight`（那是 `AppleARMBacklight` 个性匹配用的，我们手动实例化）、ADT `/backlight`（MG 三个键，缺省即可）、`/product` 改动、sysctl、新启动参数、种子偏好。

## 3. 现状

### 3.1 缺陷清单（全部【读码】）

| # | 位置 | 问题 | 后果 |
| --- | --- | --- | --- |
| 1 | `PaganiPanel.cpp:141-145` | `DSI_CTRL` bit 0 为 0 就 `start` 失败 | 无服务（上游 build 50 现场） |
| 2 | `PeripheralIO.h:161` | 线上是 `51 低 高` | 0x599 发成 DBV 0x9905，超过 `hbm-max` 的 0x10ed，亮度非单调。`peripherals.cpp:719`、`panel-power.cpp:107` 把错值钉成期望 |
| 3 | `PaganiPanel.cpp:114-118` | 启动第一拍用错序写 0x599 | DSI 一通，开机亮度就异常（面板怎么截断未知，【推测】） |
| 4 | `PaganiPanel.cpp:156-158` | 没设 `NativeSandboxGrantProcess` | `FadePeriod` 被拒，`setProperties` 进不来 |
| 5 | `PaganiPanel.cpp:169` | 忽略 `FadePeriod` | 亮度跳变 |
| 6 | `PeripheralIO.h:162` | 全部 LP | 与厂商不符（第 4 节）；zorn、asteroids 都用 HS |
| 7 | `PaganiPanel.cpp:124` | 16 ms 无条件轮询 | 62.5 次/秒唤醒，熄屏也不停 |
| 8 | `PaganiPanel.cpp:119-122` | -19、-16 也永久禁用 | 一次瞬时忙就失去亮度 |
| 9 | `boot_progress_peripheral` | 状态只进 `PANEL` 屏幕行，不进 ramoops，日常包也不显示 | 见 3.2 |

### 3.2 我们的包到底发生了什么：不知道

- 全部 `runs/*/merged.log` 没有 `AppleARMPlatform load`、`DSI panel control started`。事件环最早只保留到约 2.59 s（`20261031-070114-pkgD-longpress-1`，seq 11–1841 丢失）。
- 最新一次 `20261031-134632-a830-full-1-1` 读回 0 条记录。
- 完整包带 `pagani-diag=1`，守护进程 `r1@61 io ... pan:N` 能直接回答服务在不在（`DiagCore.cpp:394`），但还没读到过。
- `功能盘点.md:63`、`inventory/hardware.md` 2.9（“亮度已完成”）、`sensors/ios-sensors.md:105`（“手动亮度已通”）都不成立。
- 两种可能都解释症状：A. 服务没起来；B. 服务起来但被沙盒拒，这时开机亮度应不对，用户没报告过。A 更可能【推测】，方案同时覆盖两者。

### 3.3 `DSI_CTRL` bit 0 的谜团

- build 50 读到 0，屏幕却在更新【13T 实测】。命令模式下帧必须经 DSI 控制器，控制器理应使能。
- zorn 在同样的 `fastboot boot` + SimpleFb 链路读到 `0x1f5`【他机实测】，但它读得更早（CPU0 启动阶段）。
- 厂商 `DisplayDxe` 确实用 `0xae94004`（`sub_15204` 写 `0x1f1`），退出启动服务走连续闪屏分支，不反初始化（`sub_222C4`）【读码】。
- 那一行的 `v=` 没有记录。
- 13T 上 XNU 没成功读过别的 MDSS 寄存器：全量探针读挂过整机（`PROGRESS.md:238`），`INTF_1` 没读过（`PROGRESS.md:282`）。

可能是 AHB 侧读到 0、瞬时状态或 SM8750 差异，都是【推测】。离线判定不了，只能让下一次启动把现场带回来。

## 4. 面板事实

来源：`dt/running.dts` 41120 行起、`Panel_AA594_P_7_A0020_dsc_cmd.xml`、`DisplayDxe.efi`、一加 display-drivers。

| 项 | 值 | 出处 |
| --- | --- | --- |
| 型号 | AA594 P 7 A0020，命令模式 DSC | dts:41120 |
| 控制 | `bl_ctrl_dcs`，12 位 | dts:41187；xml:206-208 |
| 正常最大 | `0xdbb` | dts:41180-41181 |
| 硬件最大 | `0xffe`；HBM 表另有 `51 10 ed` | dts:41182-41183、41312 |
| 最小 | 1 | dts:41184 |
| 默认 | `0x599` = `(35*4095+50)/100` | dts:41178；xml:207；`sub_FF3C` |
| 模式 | `hbm-on/off`、off 序列 HS，on 序列 LP；该节点无 `qcom,bl-dsc-cmd-state`，缺省 HS（`dsi_panel.c:3344-3346`） | dts |
| BCTRL | 上电序列 `15 53 20`，固件已写 | xml:83-88 |
| 阈值 | DC `0x7a8`；PWM 切换 `0x48a` | dts:41179 |

字节序的三份独立证据：
1. 面板命令表：`hbm-off = 51 0d bb`（dts:41329），`hbm-on = 51 0e e0`（41328），`exit-max = 51 0f fe`（41311）。
2. `qcom,mdss-dsi-bl-inverted-dbv`（dts:41185）。`dsi_panel.c:824-825` 通用分支的语义是把低字节优先改成高字节优先。一加分支走 `oplus_display_ops`，那份源码没读到，但 1 和 3 已足够。
3. 厂商 UEFI `sub_FF3C`（`Panel_Default_Brightness_Level`）拼 `[0x51, 值>>8, 值&0xff]`。

寄存器（`dsi_ctrl_reg.h`）：`0x3c` DMA_CTRL，`0x84` TRIG_CTRL，`0xa8` LANE_STATUS，`0x11c` CLK_CTRL，`0x15c` TPG_CTRL，`0x17c` TPG FIFO，`0x1ec` FIFO 复位，`0x2cc` SECURE_DISPLAY_STATUS，`0x330` SPLIT_LINK，`0x390` DMA_CTRL_1（bit 1 是 MULTI_DMA_BURST，现有 `dma1&~2` 正确）。发送流程与 `dsi_ctrl_hw_cmn_kickoff_fifo_command`（`dsi_ctrl_hw_cmn.c:957`）一致。

## 5. 实现方案

### 5.1 `UniXNU-sm8750-common/kits/Sm8750Platform/PeripheralIO.h`

- 修字节序。`dsi_panel_command` 改成 `dsi_panel_packet_mode` 的薄包装，合并两份 FIFO 代码。
- 新增模式位：`DSI_PANEL_LP`（默认 HS）；`DSI_PANEL_FORCE`（跳过使能位、TPG、MDP 忙检查，DMA 忙仍等）。
- `dsi_panel_packet` 保持 LP，唤醒序列按设备树走 LP；亮度、0x28、0x29 走 HS。
- 强制模式下超时且 DMA 未报忙时恢复寄存器；非强制保持“超时不复位”。
- 新增只读 `dsi_panel_snapshot`，偏移是 zorn 读过的那些。**不读 DSI1，不读 DPU**（13T 读未上电块会挂死）。

### 5.2 `UniXNU-Pagani-Drivers/drivers/PaganiPanel/`

新增 `PanelBrightness.h`：无内核依赖，含请求字打包、16.16 秒转毫秒、夹紧到 `0..0xdbb`、5 s 上限、线性渐变。

`PaganiPanel.cpp`：
1. `start` 不再因 `DSI_CTRL` 放弃。先 `logState("start")`，再 `qualify()`：把 `0x599` 原值写一遍，以 DMA done 判断。-19、-16 时用 `DSI_PANEL_FORCE` 再试，成功后一直用强制模式。
2. 失败：不注册，服务留着只为上报，系统行为同现状。
3. 成功：**先 `publish()` 写 `IODisplayParameters`**，再设 `NativeSandboxGrantProcess=backboardd`、`backlight-control`、`Built-In`，最后 `registerService()`。
4. `setProperties`：打包请求，`timer->setTimeoutUS(1)`，立即返回。不 `runAction`，不用中断事件源，超界夹紧。
5. 定时器改事件驱动：只在渐变、序列、重试时武装；`pagani_panel_set_power` 通过 `panelTimer` 唤醒。
6. 错误分级：-19、-16 什么都没写，100 ms 后重试，最多 600 次；-110 停用并留现场。
7. 显示关时不写亮度，开屏发 0x29 后补写目标。

未新增虚函数，`build-pagani-peripherals.py:180` 的名单 `[0,1,7,14,21,44,86,87,111,168]` 不用改。

### 5.3 `Stage7Probe.cpp`

`enabled("pagani-panel")` 改为看 bit 0，不增加命令行字节。

### 5.4 日志

- 启动时两行 `panel: start …`，`dcs ok` 或 `no dcs` 各一组。
- 45 s 再打一次全部寄存器，之后每分钟一行计数，共 30 次，未发布的服务也打（早期 U 记录会被事件环冲掉）。
- 出错打 `panel: DSI failed …`。

### 5.5 种子、ADT、参数

种子、ADT 都不改。完整包命令行 986 字节，上限 1000，所以不加新参数：

| `pagani-panel` | 含义 |
| --- | --- |
| 缺省或 0 | 不起服务；`pagani_screen_state` 仍工作，熄屏靠黑帧 |
| 1（默认） | 首条 DCS 成功才发布 |
| 3 | DCS 失败也发布，只用于单测 CoreBrightness 段 |
| 9 | 亮度用 LP，只用于排查 |

回退包：`A830_DROP_ARGS="pagani-panel"`（`make-a830-layout.sh` 支持）。

### 5.6 文档要跟着改

`PaganiPanel/README.md`、设备仓库 `porting.toml`、`功能盘点.md:63`、`inventory/hardware.md` 2.9、`sensors/ios-sensors.md:105`。

### 5.7 明确不做

- 亮度曲线：先线性，与 sheng、asteroids 一致。OLED 中段会偏亮，确认通路后再调。
- HBM：要温控配合。
- `pwm-switch-mode1/2` 表：不影响调光功能，低亮度画质差异未知。
- 自动亮度：等光感，将来复用同一出口。
- `0x52` 读回：BTA 在自动刷新下未验证，不进默认路径。

## 6. 主机验证（已做，均在最终原型上复跑）

| 检查 | 结果 |
| --- | --- |
| `Test/panel-brightness.cpp`（新，70 项）：线上字节对照面板表、HS/LP、-19/-16 不写寄存器、强制发送、超时处理、请求字、渐变 | ASan/UBSan 全过 |
| `Test/panel-power.cpp`（改期望） | 314 项全过 |
| `scripts/test-peripherals.py`（期望改为 `0xff990551`） | 全过 |
| 用 `IOPlatformExpert.cpo.json` 的内核参数编 `PaganiPanel.cpp` | 通过（本机 Xcode beta 需加 `-Wno-unnecessary-virtual-specifier`，是 XNU 头的告警） |
| 虚表：169 槽，自有槽与名单一致，`AppleARMBacklight` 未定义符号仅 3 个 | 通过 |

没做：完整内核链接和预链接；CoreBrightness 端到端（主机模拟不了）。

## 7. 上机验证

测后**长按电源加音量下复位**再读日志。

步骤：
1. 进桌面，看有无变慢或卡死。
2. 控制中心拖亮度，最低、中间、最高各停 3 秒。
3. 设置里拖一次。
4. 电源键熄屏亮屏 5 次。
5. 等自动变暗再触摸恢复。
6. 长按复位读日志。

| 现象 | 日志 | 下一步 |
| --- | --- | --- |
| 亮度可调 | `requests`、`writes` 增长，`rc 0`，`pan:1` | 完成，调曲线 |
| 开机卡住或桌面冻结 | `pan:1`，无 `requests` | EDR 路径问题，换回退包，查 `DisplayCreateWithID` |
| 滑块无效，`pan:0` | `panel: no dcs` | 按第 9 节分支 |
| 滑块无效，`pan:1`，`requests 0` | 无请求 | 查沙盒 grant 与 CoreBrightness 路径判断 |
| `requests` 涨，`writes` 不涨 | `rc -19` 或 `-16` | 看 `retry` 那组寄存器 |
| `writes` 涨但亮度不变 | `rc 0` | 面板没吃，开 0x52 读回 |

## 8. 风险

| 风险 | 分析 | 处理 |
| --- | --- | --- |
| **CA 卡死** | 13T 首次走这条路。我们是真子类且先发布参数；asteroids 同 OS 通过，但身份不同，`CBU_*` 表已逐项核对 | 回退包 |
| **DCS 与帧竞争** | 硬件仲裁；厂商命令模式默认不做 DMA 调度（`dsi_display.c` 约 845-872 行）。我们再等 `DSI_STATUS` bit 1、2 清零，落在帧间隙（120 Hz 下约 1.5 ms），最多 20 ms，超时不写 | 已实现 |
| **自动刷新** | DCS 只动 DSI 命令 DMA，不碰 CTL、INTF。不用停自动刷新。zorn：写后 1 ms 读回旧值，10 ms 新值 | 不碰 DPU |
| **DSI 寄存器** | `0x15c`、`0x3c`、`0x390`、`0x4c` 只在发送窗口改，完成恢复；串行在自己工作循环；`INT_CTRL` 只清 bit 0 | 将来的 `pagani_panel_dcs_write` 必须走同一循环 |
| **读寄存器挂死** | 13T 有前科。只读 DSI0 窗口内 zorn 读过的偏移，0x4 在 13T 读过不挂 | 不读 DSI1、INTF、CTL、SMMU、GDSC |
| **强制写副作用** | 只在启动试一次，50 ms 上限，写同值 | 引擎未忙则恢复 |
| **真 0x28/0x29 首次生效** | 以前熄屏靠黑帧。asteroids T37d 通过 | 电源键循环测试；`pagani-screenoff` 位 2 保持关 |
| **工作循环自旋** | 每条 DCS 最坏约 7 ms，平均约 2.8 ms（估算）；渐变中约占一个核 17%，只在渐变期 | 可放宽到 33 ms 步长 |
| **低亮度看不清** | 滑块最低约 DBV 1-2，与安卓最小值一致 | 触摸不受影响，需要时加下限 |

## 9. 现场不通时的分支

看 `panel: start` 那行：
1. `ver` 是 `0x2009xxxx`，`ctrl` 像 `0x1f5`：上游读数不复现，正常走。
2. `ctrl` bit 0 为 0，强制 DMA 成功：读数是假象，保持强制模式。
3. 强制 DMA 超时：看 `clk`、`lanes`、`trig`。`trig` 低 4 位不是 4 就按 asteroids 写软件触发；可试厂商 `ctrl_en` 的 `DSI_CTRL_DYNAMIC_FORCE_ON`。
4. `ver` 为 0：寄存器读不到，多半是 AHB 或电源域状态，先查 GCC 的显示 AHB 分支。
5. 兜底：GPU 拷贝到扫描缓冲时乘亮度系数。损失灰阶、不省电，**不建议在 DSI 路径定论前做**。

## 10. 实施清单

1. 应用附录 A。
2. 跑三组主机测试。
3. `make-a830-package.sh` 重编，完整链接。`local.toml` 的 `sm8750` 指向本地 `UniXNU-sm8750-common`，`PeripheralIO.h` 改在那里。
4. 出完整包（参数不变）和回退包（去掉 `pagani-panel`）。
5. 按第 7 节上机。可与其他修复同包：DCS 不通时系统行为不变。
6. 按结果更新 5.6 的文档。

## 附录 A：原型

草稿目录的完整文件（会话重启会清空，下面贴了核心代码）：
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/patches/PeripheralIO.diff`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/patches/PaganiPanel.diff`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/patches/Stage7Probe.diff`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/patches/peripherals-test.diff`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/patches/panel-power-test.diff`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/new/drivers/PaganiPanel/PanelBrightness.h`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/new/drivers/PaganiPanel/Test/panel-brightness.cpp`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/new/drivers/PaganiPanel/PaganiPanel.cpp`
- `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/brightness/proto/new/kits/Sm8750Platform/PeripheralIO.h`

### A.1 `PeripheralIO.h`：替换原 146 行到文件末尾

```c
// One DCS write packet through the controller's command FIFO: type 0x05 (short, no parameter: length 1), 0x15 (short, one parameter:
// length 2) or 0x39 (long, 1 to DSI_PANEL_PACKET_MAX bytes). The FIFO words follow dsi_ctrl.c/dsi_ctrl_hw_cmn.c: the header word holds the
// last-command flag (bit 31), the long-packet flag (bit 30), the data type and the word count (long) or the two data bytes (short); the
// payload follows little-endian, padded with 0xff to a word; an odd word count gets a zero word. The firmware's scanout setup stays.
// mode: high speed unless DSI_PANEL_LP (the DT's dsi_hs_mode/dsi_lp_mode for that command set: brightness and display off are high speed,
// the on sequence low power); DSI_PANEL_FORCE skips the checks of the enable bit, the test-pattern FIFO and the MDP stream (for a handoff
// that reads otherwise than the sibling boards': whether the engine answers then decides).
// Returns 0, -22 bad argument, -19 not sent (enable bit clear, or the test-pattern FIFO in use), -16 the engine stayed busy for 20 ms
// (nothing written), -110 no DMA done in 50 ms: the command stays the engine's and nothing is restored, except with DSI_PANEL_FORCE when
// the DMA engine does not report busy (it never took the command).
#define DSI_PANEL_PACKET_MAX 16
#define DSI_PANEL_LP 1u
#define DSI_PANEL_FORCE 2u
static inline void dsi_panel_restore(const struct peripheral_io *io,uint32_t enables,uint32_t dma,uint32_t dma1,uint32_t length) {
    peripheral_write(io,0x110,enables|1);
    peripheral_write(io,0x15c,0);
    peripheral_write(io,0x1ec,1);io->delay(io->context,1);peripheral_write(io,0x1ec,0);
    peripheral_write(io,0x3c,dma);peripheral_write(io,0x390,dma1);peripheral_write(io,0x4c,length);
}
static inline int dsi_panel_packet_mode(const struct peripheral_io *io,uint8_t type,const uint8_t *payload,unsigned length,unsigned mode) {
    if (!payload || (type==0x05 && length!=1) || (type==0x15 && length!=2) ||
        (type==0x39 && (!length || length>DSI_PANEL_PACKET_MAX)) || (type!=0x05 && type!=0x15 && type!=0x39)) return -22;
    if (!(mode&DSI_PANEL_FORCE) && (!(peripheral_read(io,4)&1) || peripheral_read(io,0x15c))) return -19;
    // Busy: the command DMA, and the MDP stream too (the gap between two frames) unless forced: the hardware arbitrates them, as on Android.
    const uint32_t busy=(mode&DSI_PANEL_FORCE)?2:6;
    unsigned n;
    for (n=0;n<2000 && (peripheral_read(io,8)&busy);++n) io->delay(io->context,10);
    if (n==2000) return -16;
    uint32_t words[2+DSI_PANEL_PACKET_MAX/4];
    unsigned count=0;
    if (type==0x39) {
        words[count++]=0xc0000000u|((uint32_t)type<<16)|length;
        for (unsigned i=0;i<length;i+=4) {
            uint32_t word=0;
            for (unsigned b=0;b<4;++b) word|=(uint32_t)(i+b<length?payload[i+b]:0xff)<<(8*b);
            words[count++]=word;
        }
    } else {
        words[count++]=0x80000000u|((uint32_t)type<<16)|(length>1?(uint32_t)payload[1]<<8:0)|payload[0];
    }
    const uint32_t bytes=4*count;
    if (count&1) words[count++]=0;
    uint32_t dma=peripheral_read(io,0x3c),dma1=peripheral_read(io,0x390),saved=peripheral_read(io,0x4c);
    // Status bits are W1C; keep interrupt enables without acknowledging other clients.
    const uint32_t enable_mask=0xa222aa02;
    uint32_t enables=peripheral_read(io,0x110)&enable_mask;
    peripheral_write(io,0x110,enables|1);
    peripheral_write(io,0x15c,0x30006);
    for (unsigned i=0;i<count;++i) peripheral_write(io,0x17c,words[i]);
    peripheral_write(io,0x3c,(dma&~0xc4000000u)|0x10000000u|((mode&DSI_PANEL_LP)?0x04000000u:0));
    peripheral_write(io,0x390,dma1&~2u);
    peripheral_write(io,0x4c,bytes);
    peripheral_write(io,0x90,1);
    for (n=0;n<5000 && !(peripheral_read(io,0x110)&1);++n) io->delay(io->context,10);
    // Do not reset a command still owned by the engine on timeout. Caller disables further requests.
    if (n==5000) {
        if ((mode&DSI_PANEL_FORCE) && !(peripheral_read(io,8)&2)) dsi_panel_restore(io,enables,dma,dma1,saved);
        return -110;
    }
    dsi_panel_restore(io,enables,dma,dma1,saved);
    return 0;
}
// The packet writer as the panel's power sequences use it: low power, the DT's state of the on sequence.
static inline int dsi_panel_packet(const struct peripheral_io *io,uint8_t type,const uint8_t *payload,unsigned length) {
    return dsi_panel_packet_mode(io,type,payload,length,DSI_PANEL_LP);
}
// DCS display on/off and 16-bit brightness, high speed. Brightness goes out high byte first (0x51, high, low): the panel's DT
// (qcom,mdss-dsi-bl-inverted-dbv, its 51 0d bb hbm-off table) and the vendor's UEFI Panel_Default_Brightness_Level say so.
static inline int dsi_panel_command_mode(const struct peripheral_io *io,uint8_t command,unsigned level,unsigned mode) {
    if ((command!=0x51 && command!=0x28 && command!=0x29) || level>0xffff) return -22;
    if (command==0x51) {
        const uint8_t payload[3]={0x51,(uint8_t)(level>>8),(uint8_t)level};
        return dsi_panel_packet_mode(io,0x39,payload,3,mode);
    }
    return dsi_panel_packet_mode(io,0x05,&command,1,mode);
}
static inline int dsi_panel_command(const struct peripheral_io *io,uint8_t command,unsigned level) {
    return dsi_panel_command_mode(io,command,level,0);
}
// What the controller was left in, read only (the zorn port read the same offsets on SM8650 under Gunyah).
struct dsi_panel_state {uint32_t version,ctrl,status,fifo,dma,trigger,lanes,ints,clocks,tpg,ack,timeout,secure,split;};
static inline void dsi_panel_snapshot(const struct peripheral_io *io,struct dsi_panel_state *s) {
    s->version=peripheral_read(io,0x000);s->ctrl=peripheral_read(io,0x004);s->status=peripheral_read(io,0x008);
    s->fifo=peripheral_read(io,0x00c);s->dma=peripheral_read(io,0x03c);s->trigger=peripheral_read(io,0x084);
    s->lanes=peripheral_read(io,0x0a8);s->ints=peripheral_read(io,0x110);s->clocks=peripheral_read(io,0x11c);
    s->tpg=peripheral_read(io,0x15c);s->ack=peripheral_read(io,0x068);s->timeout=peripheral_read(io,0x0c0);
    s->secure=peripheral_read(io,0x2cc);s->split=peripheral_read(io,0x330);
}
```

### A.2 `drivers/PaganiPanel/PanelBrightness.h`（新增）

```c
// SPDX-License-Identifier: BSD-2-Clause-Patent
// The brightness requests of PaganiPanel, free of kernel headers so Test/panel-brightness.cpp runs them on a virtual clock.
//
// CoreBrightness (backboardd) has no new-backlight-architecture property here, so it takes its legacy path: it reads min, max and value from
// IODisplayParameters brightness when the display opens and then sets {brightness, FadePeriod (16.16 seconds), backlight-level} with
// IORegistryEntrySetCFProperties from its brightness thread, which sleeps for the fade period afterwards: the fade itself is the driver's.
// setProperties only packs the newest request into one word and arms the timer (a request replaces one not yet taken, and the caller never
// waits for the work loop: sheng int-277); the work loop takes it and steps from the level shown to the goal.
#ifndef PAGANI_PANEL_BRIGHTNESS_H
#define PAGANI_PANEL_BRIGHTNESS_H
#include <stdint.h>

enum { PAGANI_BRIGHTNESS_STEP_MS = 16, PAGANI_BRIGHTNESS_FADE_MAX_MS = 5000 };

// The request word: bit 63 set, the level in bits 16..31 (clamped to max), the fade in milliseconds in bits 0..15.
static inline uint64_t pagani_brightness_request(uint64_t level, uint32_t fade_16_16, uint32_t max)
{
    uint64_t milliseconds = ((uint64_t)fade_16_16 * 1000) >> 16;
    if (milliseconds > PAGANI_BRIGHTNESS_FADE_MAX_MS) milliseconds = PAGANI_BRIGHTNESS_FADE_MAX_MS;
    if (level > max) level = max;
    return 1ull << 63 | level << 16 | milliseconds;
}
static inline int pagani_brightness_request_valid(uint64_t word) { return (int)(word >> 63); }
static inline uint32_t pagani_brightness_request_level(uint64_t word) { return (uint32_t)(word >> 16) & 0xffff; }
static inline uint32_t pagani_brightness_request_fade(uint64_t word) { return (uint32_t)word & 0xffff; }

struct pagani_brightness {
    uint32_t shown;           // the last level the panel took
    uint32_t from, to;        // the running fade: from the level shown when it began to the goal
    uint64_t start, period;   // ns; period 0: the goal at once
};
static inline void pagani_brightness_init(struct pagani_brightness *b, uint32_t level)
{
    struct pagani_brightness z = {level, level, level, 0, 0};
    *b = z;
}
// A request taken at `now`: a fade from what the panel shows now, so a newer goal bends a running fade instead of restarting it.
static inline void pagani_brightness_begin(struct pagani_brightness *b, uint32_t goal, uint32_t milliseconds, uint64_t now)
{
    b->from = b->shown;
    b->to = goal;
    b->start = now;
    b->period = (uint64_t)milliseconds * 1000000u;
}
// The level the fade asks for at `now`.
static inline uint32_t pagani_brightness_level(const struct pagani_brightness *b, uint64_t now)
{
    const uint64_t elapsed = now - b->start;
    if (!b->period || elapsed >= b->period) return b->to;
    return (uint32_t)((int64_t)b->from + ((int64_t)b->to - (int64_t)b->from) * (int64_t)elapsed / (int64_t)b->period);
}
static inline int pagani_brightness_done(const struct pagani_brightness *b) { return b->shown == b->to; }
#endif
```

### A.3 `drivers/PaganiPanel/PaganiPanel.cpp`（全文）

```cpp
// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "OriginalBacklightABI.h"
#include "../PaganiInput/Mapping.h"
#include <PaganiPeripherals.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/IOTimerEventSource.h>
#include <libkern/c++/OSNumber.h>
#include <kern/clock.h>
#include <pexpert/pexpert.h>
#include "../BootProgress/PeripheralStatus.h"
#include "PanelPower.h"
#include "PanelBrightness.h"

// The display's power as the system set it last, for the screen-off policies of PaganiArmCpu (pagani-screenoff bit 0) and Adreno830
// (bits 1 and 3): bit 0 set while the display is off, the number of changes above it (a reader that missed an off and an on still
// sees the count move). Written here only; a weak zero word like PaganiInput's touch counter, so the kernel link keeps one word.
extern "C" {
__attribute__((weak)) volatile uint32_t pagani_screen_state;
}

// The display PM callback publishes a value, never a pointer into a detachable service; the timer only exists while the service
// runs, and a started service stays until reboot.
static unsigned requestedPower=1;
static IOTimerEventSource *volatile panelTimer;
extern "C" void pagani_panel_set_power(unsigned on) {
    __atomic_store_n(&requestedPower,on?1:0,__ATOMIC_RELEASE);
    const uint32_t state=__atomic_load_n(&pagani_screen_state,__ATOMIC_RELAXED);
    if ((state&1u)!=(on?0u:1u)) __atomic_store_n(&pagani_screen_state,((state|1u)+1u)|(on?0u:1u),__ATOMIC_RELEASE);
    if (IOTimerEventSource *timer=__atomic_load_n(&panelTimer,__ATOMIC_ACQUIRE)) timer->setTimeoutUS(1);
}

// boot-arg pagani-panel: bit 0 runs this driver (Stage7Probe), bit 1 publishes the backlight even when the first DCS failed, to test
// CoreBrightness's side alone; bit 3 sends the brightness in low-power mode instead of high-speed.
enum { PANEL_OPTION_PUBLISH_ALWAYS = 2, PANEL_OPTION_LP = 8 };
// A DCS that found the engine busy or disabled changed nothing and is sent again this much later, at most this many times in a row.
enum { PANEL_RETRY_MS = 100, PANEL_RETRIES = 600 };
enum { PANEL_REPORT_FIRST_MS = 45000, PANEL_REPORT_MS = 60000, PANEL_REPORTS = 30 };

class PaganiPanel : public AppleARMBacklight {
    OSDeclareDefaultStructors(PaganiPanel)
    PeripheralWindow dsi;
    IOWorkLoop *loop=nullptr;
    IOTimerEventSource *timer=nullptr,*reporter=nullptr;
    bool added=false,running=false,failed=false;
    unsigned appliedPower=1,sendMode=0,retries=0,reports=0;
    uint64_t commands=0;
    pagani_brightness bright{};
    // The newest brightness asked for (PanelBrightness.h), taken by the work loop.
    volatile uint64_t request=0;
    uint32_t requests=0,writes=0,retried=0;
    int lastError=0;
    // pagani-screenoff bit 2: the vendor's off and on sequences with sleep mode (PanelPower.h) instead of 0x28/0x29 alone.
    bool sleepMode=false,stale=false;
    pagani_panel_power sequence{};
    void observe(const char *stage,int rc,uint32_t value) {
        boot_progress_peripheral(PAGANI_PANEL,stage,rc,value,__atomic_load_n(&commands,__ATOMIC_RELAXED));
    }
    static uint64_t nowNs() {uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);return ns;}
    static int sendPacket(void *context,uint8_t type,const uint8_t *payload,unsigned length) {
        return dsi_panel_packet(static_cast<const peripheral_io *>(context),type,payload,length);
    }
    // The controller as it is now, in two U records: the first DSI read of this port is on no log (the events ring keeps the last 32 KiB).
    void logState(const char *when) {
        auto io=dsi.io();dsi_panel_state s;dsi_panel_snapshot(&io,&s);
        char line[120];
        snprintf(line,sizeof line,"panel: %s ver %x ctrl %x st %x fifo %x dma %x trig %x",when,s.version,s.ctrl,s.status,s.fifo,s.dma,s.trigger);
        boot_progress_trace(line);
        snprintf(line,sizeof line,"panel: %s lanes %x int %x clk %x tpg %x ack %x to %x sec %x split %x",when,s.lanes,s.ints,s.clocks,s.tpg,s.ack,s.timeout,s.secure,s.split);
        boot_progress_trace(line);
    }
    void logCounters() {
        char line[120];
        snprintf(line,sizeof line,"panel: requests %u writes %u retried %u shown %u goal %u rc %d mode %u power %u/%u%s",
                 __atomic_load_n(&requests,__ATOMIC_RELAXED),writes,retried,bright.shown,bright.to,lastError,sendMode,appliedPower,
                 __atomic_load_n(&requestedPower,__ATOMIC_RELAXED),__atomic_load_n(&failed,__ATOMIC_RELAXED)?" stopped":"");
        boot_progress_trace(line);
    }
    // The ramoops log (U records): each finished sequence once, and the failure that disabled the panel.
    void noteSequence() {
        char line[96];
        // The step sets refresh only when the wake from sleep mode ended (it is cleared after this).
        const char *what=sequence.last==PAGANI_PANEL_ASLEEP?"display off, sleep in":sequence.last==PAGANI_PANEL_BLANK?"display off":
                         sequence.refresh?"sleep out, vendor writes, display on":"display on";
        snprintf(line,sizeof line,"panel: %s in %llu ms (asleep %u, woken %u, packets %u)",what,
                 (unsigned long long)(sequence.lastNs/1000000),sequence.sleeps,sequence.wakes,sequence.packets);
        boot_progress_trace(line);
    }
    // A request taken here starts a fade from the level shown.
    void takeRequest(uint64_t now) {
        const uint64_t word=__atomic_exchange_n(&request,0ull,__ATOMIC_ACQUIRE);
        if (pagani_brightness_request_valid(word))
            pagani_brightness_begin(&bright,pagani_brightness_request_level(word),pagani_brightness_request_fade(word),now);
    }
    int sendLevel(const peripheral_io *io,uint32_t level) {
        const int rc=dsi_panel_command_mode(io,0x51,level,sendMode);
        if (!rc) {
            bright.shown=level;++writes;
            __atomic_add_fetch(&commands,1,__ATOMIC_RELAXED);
        }
        observe("DSI brightness",rc,level);
        return rc;
    }
    // After a tick's commands: a command that was not sent (-19 enable bit or test FIFO, -16 engine busy for 20 ms) is sent again a
    // little later, a bounded number of times; anything else stops the panel requests, as a command the engine may still own must not
    // be followed by another. True: rearm as usual.
    bool settle(int rc,const char *what) {
        if (!rc) {retries=0;return true;}
        lastError=rc;
        if ((rc==-19 || rc==-16) && ++retries<=PANEL_RETRIES) {
            ++retried;
            if (retries==1) {logState("retry");logCounters();}
            timer->setTimeoutMS(PANEL_RETRY_MS);
            return false;
        }
        __atomic_store_n(&failed,true,__ATOMIC_RELEASE);setProperty("PanelError",(uint32_t)rc,32);
        char line[96];
        snprintf(line,sizeof line,"panel: DSI failed %d at %s; requests disabled",rc,what);
        boot_progress_trace(line);
        logState("failed");logCounters();
        IOLog("Pagani panel command failed: %d; requests disabled\n",rc);
        return false;
    }
    // One tick with the sleep option: the running sequence (or the next one the request needs), then the brightness once the panel
    // shows again (rewritten after sleep mode). A failure disables requests as the default path does.
    void tickSequenced(uint64_t now) {
        const unsigned power=__atomic_load_n(&requestedPower,__ATOMIC_ACQUIRE);
        auto io=dsi.io();bool changed=false;
        uint64_t wait=0;unsigned finished=0;
        const uint32_t sent=sequence.packets;
        int rc=pagani_panel_power_step(&sequence,now,power,sendPacket,&io,&wait,&finished);
        if (sequence.packets!=sent) __atomic_add_fetch(&commands,sequence.packets-sent,__ATOMIC_RELAXED);
        if (sequence.packets!=sent || rc) observe(rc?"DSI sequence":"DSI power",rc,(uint32_t)sequence.state<<8|sequence.next);
        if (finished) noteSequence();
        if (!rc && sequence.refresh) {sequence.refresh=0;stale=true;}
        const unsigned shown=sequence.state==PAGANI_PANEL_ON && !sequence.seq?1:0;
        if (shown!=appliedPower) {appliedPower=shown;changed=true;}
        const uint32_t next=pagani_brightness_level(&bright,now);
        const bool stepped=!rc && shown && (stale || next!=bright.shown);
        if (stepped) {
            rc=sendLevel(&io,next);
            if (!rc) {stale=false;changed=pagani_brightness_done(&bright);}
        }
        if (!settle(rc,sequence.seq==pagani_panel_wake?"the wake":sequence.seq==pagani_panel_off?"the off sequence":"a brightness")) return;
        if (changed) publish();
        uint32_t ms=0;
        if (sequence.seq || wait) {const uint64_t w=(wait+999999)/1000000;ms=w && w<PAGANI_BRIGHTNESS_STEP_MS?(uint32_t)w:PAGANI_BRIGHTNESS_STEP_MS;}
        else if (shown && !pagani_brightness_done(&bright)) ms=PAGANI_BRIGHTNESS_STEP_MS;
        if (ms) timer->setTimeoutMS(ms);
    }
    void publish() {
        auto b=OSDictionary::withCapacity(3),p=OSDictionary::withCapacity(1);
        auto lo=OSNumber::withNumber(0ULL,32),hi=OSNumber::withNumber(PAGANI_BRIGHTNESS_MAX,32),v=OSNumber::withNumber(bright.shown,32);
        if (b && p && lo && hi && v) {
            b->setObject("min",lo);b->setObject("max",hi);b->setObject("value",v);
            p->setObject("brightness",b);setProperty("IODisplayParameters",p);
        }
        OSSafeReleaseNULL(lo);OSSafeReleaseNULL(hi);OSSafeReleaseNULL(v);OSSafeReleaseNULL(b);OSSafeReleaseNULL(p);
        setProperty("brightness",bright.shown,32);setProperty("PanelDisplayOn",appliedPower!=0);
        if (sleepMode) setProperty("PanelAsleep",sequence.state==PAGANI_PANEL_ASLEEP);
    }
    static void tick(OSObject *owner,IOTimerEventSource *) {
        auto self=static_cast<PaganiPanel *>(owner);
        if (!__atomic_load_n(&self->running,__ATOMIC_ACQUIRE) || __atomic_load_n(&self->failed,__ATOMIC_ACQUIRE)) return;
        const uint64_t now=nowNs();
        self->takeRequest(now);
        if (self->sleepMode) {self->tickSequenced(now);return;}
        const unsigned power=__atomic_load_n(&requestedPower,__ATOMIC_ACQUIRE);
        auto io=self->dsi.io();int rc=0;bool changed=false;
        if (power!=self->appliedPower) {
            rc=dsi_panel_command_mode(&io,power?0x29:0x28,0,self->sendMode);
            if (!rc) {self->appliedPower=power;changed=true;__atomic_add_fetch(&self->commands,1,__ATOMIC_RELAXED);}
            self->observe("DSI power",rc,power);
        }
        const uint32_t next=pagani_brightness_level(&self->bright,now);
        if (!rc && power && next!=self->bright.shown) {
            rc=self->sendLevel(&io,next);
            if (!rc) changed=changed || pagani_brightness_done(&self->bright);
        }
        if (!self->settle(rc,"a brightness or display power command")) return;
        if (changed) self->publish();
        if (power && !pagani_brightness_done(&self->bright)) self->timer->setTimeoutMS(PAGANI_BRIGHTNESS_STEP_MS);
    }
    // The early records are long out of the events ring by the time anyone reads the log: the controller once more and the counters,
    // once a minute for half an hour, also on a service that stayed unpublished.
    static void report(OSObject *owner,IOTimerEventSource *sender) {
        auto self=static_cast<PaganiPanel *>(owner);
        if (!self->reports) self->logState("report");
        self->logCounters();
        if (++self->reports<PANEL_REPORTS) sender->setTimeoutMS(PANEL_REPORT_MS);
    }
    void cleanup() {
        __atomic_store_n(&running,false,__ATOMIC_RELEASE);
        __atomic_store_n(&panelTimer,(IOTimerEventSource *)nullptr,__ATOMIC_RELEASE);
        if (timer) {timer->cancelTimeout();if (added) loop->removeEventSource(timer);}
        if (reporter) {reporter->cancelTimeout();if (added) loop->removeEventSource(reporter);}
        added=false;OSSafeReleaseNULL(timer);OSSafeReleaseNULL(reporter);OSSafeReleaseNULL(loop);dsi.release();
    }
    // The first DCS: the level the boot loader set again, so nothing changes on the panel, to learn whether the command path answers
    // before the system is told there is a backlight. Not sent as the checks stand (they read other than on the sibling boards, or the
    // engine stayed busy): once more without them, and the commands after it the same way when that worked.
    int qualify() {
        auto io=dsi.io();
        int rc=sendLevel(&io,bright.shown);
        if (rc==-19 || rc==-16) {
            logState("forced");
            sendMode|=DSI_PANEL_FORCE;
            rc=sendLevel(&io,bright.shown);
            if (rc) sendMode&=~DSI_PANEL_FORCE;
        }
        lastError=rc;
        return rc;
    }
public:
    // The original constructor supplies storage and ancestry; no Apple hardware lifecycle is called.
    bool init(OSDictionary *properties=nullptr) override {return IOService::init(properties);}
    IOWorkLoop *getWorkLoop() const override {return loop;}
    bool start(IOService *provider) override {
        observe("service start",0,0);
        if (!IOService::start(provider)) {observe("service start",kIOReturnError,0);return false;}
        if (!dsi.map(PAGANI_DSI_BASE,0x1000)) {
            observe("DSI map",kIOReturnNoMemory,0);cleanup();IOService::stop(provider);return false;
        }
        logState("start");
        pagani_brightness_init(&bright,PAGANI_BRIGHTNESS_DEFAULT);
        unsigned options=0;PE_parse_boot_argn("pagani-panel",&options,sizeof options);
        if (options&PANEL_OPTION_LP) sendMode|=DSI_PANEL_LP;
        loop=IOWorkLoop::workLoop();timer=IOTimerEventSource::timerEventSource(this,tick);
        reporter=IOTimerEventSource::timerEventSource(this,report);
        added=loop && timer && reporter && loop->addEventSource(timer)==kIOReturnSuccess && loop->addEventSource(reporter)==kIOReturnSuccess;
        if (!added) {observe("timer",kIOReturnNoResources,0);cleanup();IOService::stop(provider);return false;}
        // pagani-screenoff bit 2: display off also enters sleep mode, display on leaves it with the vendor's writes (PanelPower.h).
        unsigned screenoff=0;PE_parse_boot_argn("pagani-screenoff",&screenoff,sizeof screenoff);
        sleepMode=(screenoff&4u)!=0;pagani_panel_power_init(&sequence,sleepMode);
        if (sleepMode) setProperty("PanelSleepMode",true);
        const int rc=qualify();
        logState(rc?"no dcs":"dcs ok");logCounters();
        reporter->setTimeoutMS(PANEL_REPORT_FIRST_MS);
        if (rc && !(options&PANEL_OPTION_PUBLISH_ALWAYS)) {
            // The system runs without a backlight, as it does without this driver: the screen keeps the boot loader's level. The service
            // stays, never registered (no class match, nothing for CoreBrightness), for its reports: the first read of a controller
            // that refuses must reach the log.
            observe(rc==-19?"DSI disabled":"DSI no answer",rc,0);
            setProperty("PanelError",(uint32_t)rc,32);
            __atomic_store_n(&failed,true,__ATOMIC_RELEASE);
            return true;
        }
        // Before anything can look: CoreBrightness refuses a display without IODisplayParameters, and QuartzCore waits for the libEDR
        // server CoreBrightness starts while opening that display as soon as backlight-control makes CADeviceHasBacklight true.
        publish();
        // backboardd's profile refuses FadePeriod, which CoreBrightness sends with every brightness (sheng int-276).
        setProperty("NativeSandboxGrantProcess","backboardd");
        // Publish only with real AppleARMBacklight ancestry: CoreBrightness uses IOObjectConformsTo.
        setProperty("backlight-control",true);setProperty("Built-In",true);
        setProperty("PanelMaxBrightness",PAGANI_BRIGHTNESS_MAX,32);
        setProperty("PanelRefreshSource","firmware timing; software presentation at 60 Hz");
        __atomic_store_n(&running,true,__ATOMIC_RELEASE);
        __atomic_store_n(&panelTimer,timer,__ATOMIC_RELEASE);
        registerService();return true;
    }
    IOReturn setProperties(OSObject *properties) override {
        auto dict=OSDynamicCast(OSDictionary,properties);
        if (!dict) return kIOReturnBadArgument;
        auto value=OSDynamicCast(OSNumber,dict->getObject("brightness"));
        if (!value) return kIOReturnSuccess;
        auto fade=OSDynamicCast(OSNumber,dict->getObject("FadePeriod"));
        __atomic_add_fetch(&requests,1,__ATOMIC_RELAXED);
        // Handed to the work loop without waiting for it: the caller is CoreBrightness's brightness thread (sheng int-277). A later
        // request replaces one not yet taken. The timer carries it (sheng int-278: an interrupt source signalled here wrote nothing).
        __atomic_store_n(&request,pagani_brightness_request(value->unsigned64BitValue(),fade?fade->unsigned32BitValue():0,PAGANI_BRIGHTNESS_MAX),__ATOMIC_RELEASE);
        if (__atomic_load_n(&running,__ATOMIC_ACQUIRE) && !__atomic_load_n(&failed,__ATOMIC_ACQUIRE)) timer->setTimeoutUS(1);
        return kIOReturnSuccess;
    }
    void stop(IOService *provider) override {cleanup();IOService::stop(provider);}
    void free() override {cleanup();IOService::free();}
    void unqualifiedBacklightMethod() override {
        panic("PaganiPanel: unsupported AppleARMBacklight slot 168");
    }
};
OSDefineMetaClassAndStructors(PaganiPanel,AppleARMBacklight)
extern "C" IOService *pagani_panel_allocate(void) {return new PaganiPanel;}
```

### A.4 其余小改动

`drivers/Stage7Probe/Stage7Probe.cpp`：

```diff
-    if (enabled("pagani-panel")) {
+    // pagani-panel is a bit mask since the brightness work: bit 0 runs the panel service, the others are PaganiPanel's own (PaganiPanel.cpp).
+    unsigned panel=0;PE_parse_boot_argn("pagani-panel",&panel,sizeof panel);
+    if (panel&1) {
```

`drivers/PaganiInput/Test/peripherals.cpp:719`：

```diff
-    assert(fifo==std::vector<uint32_t>({0xc0390003,0xff059951}));
+    assert(fifo==std::vector<uint32_t>({0xc0390003,0xff990551}));  // 51 05 99: high byte first, as the panel's own 51 0d bb table
```

`drivers/PaganiPanel/Test/panel-power.cpp`：

```diff
-        const uint8_t level[3] = {0x51, 0x99, 0x05};
+        const uint8_t level[3] = {0x51, 0x05, 0x99};
-        CHECK(!(k == 2 ? dsi_panel_packet(&ib, 0x39, level, 3) : dsi_panel_packet(&ib, 0x05, &command, 1)));
+        CHECK(!(k == 2 ? dsi_panel_packet_mode(&ib, 0x39, level, 3, 0) : dsi_panel_packet_mode(&ib, 0x05, &command, 1, 0)));
```

`Test/panel-brightness.cpp` 共 147 行，只在草稿目录（路径见上），编译方式同 `panel-power.cpp`。