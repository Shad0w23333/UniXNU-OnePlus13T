# 亮度修复实现说明（初版）

本文件是审查前的说明。971、987 字节只在追加到现有参数时成立；“2000 次 1–3 次”不含线程抢占，不是上界；最终状态以 summary.md 为准。

亮度修复已落到源码，主机测试全过，内核配方编译零告警。**没有上机，也没做整核链接**。
**改动文件**：`/Users/xiaochoumao/unixnu-13t/UniXNU-sm8750-common/kits/Sm8750Platform/PeripheralIO.h`；`/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/` 下的 `PaganiPanel.cpp`、`PanelBrightness.h`（新）、`PanelPower.h`（仅注释）、`README.md`、`Test/panel-brightness.cpp`（新）、`Test/panel-power.cpp`；`/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/Stage7Probe/Stage7Probe.cpp`；`/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiInput/Test/peripherals.cpp`；`/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/porting.toml` 和 `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750/porting.toml`。
**主机结果**：`panel-brightness` 124903 项、`panel-power` 314 项（均开 ASan/UBSan）全过；`test-peripherals.py`（`-gpu` 仓库版本）全过。
**内核配方**：`PaganiPanel.cpp`、`Stage7Probe.cpp` 零告警；虚表自有槽位与审计名单一致，未定义符号在现有内核符号表中都能找到。
**已修**：线上字节序改为 `51 高 低`；亮度按设备树走 HS；支持 `FadePeriod` 渐变；曲线线性，范围 1..0xdbb，12 位，不越正常最大值。
**放行结论**：必须设 `NativeSandboxGrantProcess=backboardd`，已设。21H461 内核的沙盒配置里没有 `FadePeriod`，真子类也救不了，sheng 现场记录也吻合。
**并发**：定时器降到 user 组（优先级 31），避免轮询抢占界面线程；所有武装走一把锁，防止丢 `0x29`；计时器改事件驱动，空闲无唤醒；发命令前等帧间隙，不碰 DPU。
**新参数**：没有新名字，`pagani-panel` 改为位掩码，`=1` 含义不变；新增位 2 关强制发送、位 4 不发布背光、位 5 忽略渐变（另有原型的位 1、位 3）。
**CA 卡死开关**：`A830_DROP_ARGS="pagani-panel"` 完全关（971 字节）；`A830_EXTRA_ARGS="pagani-panel=17"` 保留 DCS 只不发布（987 字节）。两种命令行都按真实配置模拟过，未实际出包。
**Stage7 日志**：加载和类注册结果写入 U 记录；服务没起来时 45 秒后重打，之后每 10 分钟一次。
**未验证**：真机亮度、CoreBrightness 端到端、真 `0x28/0x29`、CA 是否卡死、`DSI_CTRL` 谜团、`pagani-te-ar-off=1` 组合、整核链接和预链接。
**与本次无关的既有失败**：`UniXNU-Pagani_SM8750-gpu/scripts/test-stage7.py` 的桩落后于显示驱动；`UniXNU-Pagani_SM8750/scripts/test-peripherals.py` 是旧副本，缺 `PaganiButtons.inc`。
**报告未落盘**：系统规则不允许子代理写报告文件，`stage1/brightness.md` 没写，全文在下方，请主线保存。

---

# 亮度修复：第 1 阶段落地报告

日期 2026-10-31。全程离线，没有运行 adb、fastboot，没有整核构建，没有提交。

## 1. 当前源码与原型的关系

三个目标文件（`PeripheralIO.h`、`PaganiPanel.cpp`、`Stage7Probe.cpp`）当前内容与原型的 `orig/` 基线一致，没有后续改动，所以融合不存在冲突。但原型有几处没考虑到的问题，下面第 3 节列出，落地时已修。

## 2. 按方案落地的内容

**`PeripheralIO.h`**
- `dsi_panel_command` 与 `dsi_panel_packet` 合并到 `dsi_panel_packet_mode`，只留一份 FIFO 代码。
- 字节序改为 `51 高 低`。
- 新增 `DSI_PANEL_LP`（默认 HS）和 `DSI_PANEL_FORCE`。
- 新增只读快照 `dsi_panel_snapshot`，不读 DSI1，不读 DPU。

**`PaganiPanel.cpp`**
- `start` 不再因 `DSI_CTRL` bit 0 放弃。先写寄存器日志，再用首条同值 DCS 判定，被检查拒绝时强制重试一次。
- 失败时服务留着但不注册，系统行为同今天，报告继续跑。
- 成功时先发布 `IODisplayParameters`，再设放行和 `backlight-control`，最后 `registerService()`。
- `setProperties` 只打包并武装定时器，不等工作循环。

**`Stage7Probe.cpp`**
- 改看 `pagani-panel` 的 bit 0。
- `AppleARMPlatform` 加载、类注册、服务启动三步结果写入 U 记录。

**文档**
- `porting.toml` 的 `low-byte-first` 错误事实已改。
- `功能盘点.md:63`、`inventory/hardware.md` 2.9、`sensors/ios-sensors.md:105` 等上机后再改（按原报告第 10 节第 6 步）。

## 3. 与原型的差异及理由

| 差异 | 理由 |
| --- | --- |
| 定时器从默认 `kernel-high`（优先级 92）改到 `kIOTimerEventSourceOptionsPriorityUser`（31） | 本机 `cpu_idle_latency` 为 0（PaganiArmCpu 没设 `powergate_latency`），`ml_delay_should_spin` 返回假，`IODelay` 在可抢占线程里阻塞而不是自旋。原报告“工作循环自旋约占一核 17%”的前提不成立。真问题是高优先级工作线程反复醒来抢占界面线程。代价是 `0x29` 也要等空闲 CPU，属【推测】可接受，见第 8 节 |
| 所有武装走 `panelTimerLock` | `wakeAtTime` 的世代号是非原子自增，再单独读取。三个线程武装同一定时器时，被抢占的线程会留下旧世代，`timeoutAndRelease` 丢弃这次回调。丢的可能是 `0x29`，屏幕就一直黑 |
| 帧间隙轮询从 10 µs 改为 100 µs（总时限不变） | 间隙约 1.5 ms，10 µs 没有必要，每次轮询都是一次定时器唤醒。模型里 120 Hz 最坏 69 次 |
| 默认路径并入 `PanelPower.h` 序列引擎 | 去掉两套分支。副作用：`0x29` 要等 `0x28` 后设备树规定的 20 ms |
| 整个 tick 下沉到 `PanelBrightness.h` | 原型的 tick 主机测不到。现在电源与亮度的交互用内核同一份代码在虚拟时钟上跑 |
| 序列包带上 `FORCE`，关屏序列走 HS | 原型的序列包不带强制位，强制模式下会全被 -19 拒；设备树 off 序列是 HS |
| 上报改为“有变化每分钟一行，无变化每 10 分钟一行，不设上限” | 原型 30 次后停止，第 1 阶段定时复位要延到 90 分钟。寄存器在显示熄灭时不读 |
| 服务不存在时 Stage7 周期重打一行 | 事件环只有 32 KiB，早期记录会被冲掉，写一次留不住；服务不存在时也没有别的上报者 |
| 插值改用无符号运算 | 有符号版在内核配方下引入 3 个 `__ubsan` 引用，它们在现有内核符号表里是本地符号，原来的 `PaganiPanel.o` 没有这种依赖 |
| 32 位负值按 0 处理 | 与 sheng 一致，避免异常值变成最亮 |

## 4. 放行结论：需要

**证据**
- 【逆向】`作者产物/ida/kernel-68.macho` 全文件 `FadePeriod` 只出现一次（偏移 `0x5b2b303`），在 AppleARMPlatform 自身的字符串里。
- 【逆向】沙盒配置集合里搜不到 `Fade`、`FadeP`、`adeP`、`Period`。`eriod` 片段只有 3 处，分别属于 `Timestamp Sync`、`kperf`、`kdebug`。
- 【读码】`CoreBrightness` 三个键放在同一字典；`is_io_registry_entry_set_properties` 被拒时直接返回 `kIOReturnNotPermitted`，`setProperties` 根本不运行。
- 【读码】`zzwb-native-sandbox-grant-set-properties.patch` 已在构建树的 `IOUserClient.cpp` 里。
- 【他机实测，仅读提交和注释】sheng 先冒充 `AppleARMBacklight`（`5d045d0`），之后仍要加放行（`fb4839d`，int-276）。它的元类链和我们的真子类一样含 `AppleARMBacklight`，所以真子类不能免除这一步。

**局限**：字符串搜索排除不了正则规则，这一点靠 sheng 的现场记录补足。

## 5. 面板写入的并发处理

- **串行**：所有 DSI 访问在同一工作循环上一次一个 tick；`start` 里的首条 DCS 在定时器武装之前。
- **自刷新**：MDP 自动刷新不停，只动 DSI 命令 DMA，不碰 CTL、INTF。
- **总线**：等 `DSI_STATUS` bit 1、2 清零，最多 20 ms，超时不写。
- **检测与触发之间**：约 20 次寄存器访问，极少数会碰上下一帧开始，由硬件仲裁，与安卓一致。模型里 2000 次中 1–3 次。
- **显示运行时**：`NativeLegacyDisplay` 的 `pagani_panel_dcs_write` 仍是弱默认，没有提供，`panelTo120` 不会发命令。完整包没开 `pagani-te`、`pagani-te-ar-off`、`pagani-fb-flip`、`pagani-screenoff`。
- **熄屏**：除 `0x28` 本身（及其重试）外不读写 DSI，这期间的目标亮度等亮屏后补写。
- **已知未解决**：`pagani-te-ar-off=1` 时 `0x28/0x29` 与自动刷新的写入没有定序，和以前一样。这个选项不在任何包里，README 已写明。

## 6. 启动参数

| 位 | 值 | 作用 | 默认 |
| --- | --- | --- | --- |
| 0 | 1 | 运行驱动 | 开：这是修复本身，所有包已带 `pagani-panel=1`，不加字节 |
| 1 | 2 | DCS 失败也发布，只用于单测 | 关 |
| 2 | 4 | 不做强制发送 | 关（即默认强制）：build 50 读到 bit 0 为 0，不试就永远没有亮度；失败时系统行为不变 |
| 3 | 8 | 亮度和 `0x28/0x29` 走 LP | 关（默认 HS）：与设备树一致 |
| 4 | 16 | 不发布背光，保留 DCS、电源和日志 | 关：用于拆分 CA 卡死 |
| 5 | 32 | 忽略 `FadePeriod` | 关（默认渐变）：系统本来就把渐变交给驱动 |

**关闭 CA 风险**
- 完全关：`A830_DROP_ARGS="pagani-panel"`。Stage7 不加载 `AppleARMPlatform`，也不注册类。`board.toml` 里只有一处，模拟后命令行 971 字节。
- 只不发布：`A830_EXTRA_ARGS="pagani-panel=17"`。`payload.py:96` 按名字替换，987 字节。

## 7. 验证结果

**主机**

| 项 | 结果 |
| --- | --- |
| `Test/panel-brightness.cpp` | 全过，124903 项：线上字节、交接、帧间隙模型、请求、曲线、渐变、完整 tick、经寄存器模型 |
| `Test/panel-power.cpp` | 全过，314 项 |
| `UniXNU-Pagani_SM8750-gpu/scripts/test-peripherals.py` | 全过，退出码 0 |
| `PaganiPanel.cpp` 内核配方（经构建树符号链接路径） | 0 告警 0 错误 |
| `Stage7Probe.cpp` 内核配方 | 0 告警 0 错误 |
| 虚表 | 169 槽，自有 `[0,1,7,14,21,44,86,87,111,168]` 与审计名单一致 |
| 未定义符号 | 两个目标文件全部能在现有内核符号表找到，无 `__ubsan` 引用 |
| 静态初始化 | `__mod_init_func` 仍是 1 项，与旧对象一致 |

**曲线**：滑块最低对应 `51 00 01`，最高对应 `51 0d bb`（面板自己的 hbm-off 表），单调，最高字节不超 `0x0d`，任意输入都不越 `0xdbb`。

**真机**：无。以上全部没有在 13T 上运行过。

**没做**：整核链接和预链接（主线统一跑）。

## 8. 风险与未验证项

| 风险 | 状态 |
| --- | --- |
| CA 卡死（sheng int-236），13T 第一次发布 `backlight-control` | 未验证，有两档回退 |
| 真 `0x28/0x29` 首次生效，以前熄屏靠黑帧 | 未验证，需电源键循环测试 |
| `0x29` 在优先级 31 上，满载时亮屏可能略迟 | 【推测】可接受，八核很少全被高优先级占满 |
| `DSI_CTRL` bit 0 读 0 的原因 | 离线解释不了，日志会带回现场 |
| 首次读 DSI 全部寄存器 | 只读 DSI0 窗口内 zorn 读过的偏移，SM8750 上只有 `0x4` 读过；读前先写 U 记录，挂住也能定位 |
| 线性曲线中段偏亮 | 已知，确认通路后再调 |
| 低亮度 | 滑块最低是 DBV 1，与安卓最小值一致，可能很暗 |

## 9. 上机步骤

测后长按电源加音量下复位，再读日志。

1. 进桌面，看有无变慢或卡死。
2. 控制中心拖亮度，最低、中间、最高各停 3 秒。
3. 设置里拖一次。
4. 电源键熄屏亮屏 5 次。
5. 等自动变暗再触摸恢复。

| 现象 | 日志 | 下一步 |
| --- | --- | --- |
| 亮度可调 | `requests`、`writes` 增长，`rc 0` | 完成，再调曲线 |
| 开机卡住或桌面冻结 | `panel: dcs ok`，无 `requests` | 先用 `pagani-panel=17` 拆分；仍卡再去掉 `pagani-panel` |
| 滑块无效，有 `panel: no dcs` | 看 `ctrl`、`clk`、`lanes`、`trig` | 按原报告第 9 节分支 |
| 滑块无效，`requests 0`，`dcs ok` | 无请求 | 查放行与 `CoreBrightness` 路径 |
| `requests` 涨，`writes` 不涨 | `rc -19` 或 `-16` | 看 `retry` 那组寄存器 |
| `writes` 涨但亮度不变 | `rc 0` | 面板没吃，试 `pagani-panel=9` |
| 没有任何 `panel:` 行 | 看 `panel: stage7 ...` | 加载或类注册失败 |
| 上屏变卡 | `pagani-panel=33` 关渐变对比 | 查渐变期 DCS |
阿
---

（说明：上一行的“阿”是笔误，请忽略，报告到第 9 节结束。）
