# 第 1 阶段非 GPU 修复总结（2026-10-31）

以下全部是主机结果，未上机。

**状态**：四路审查项已逐条核实，成立的都修了。全部只有主机结果：没上机、没跑整核、没提交。没动 Metal、Turnip、翻译层，`NativeAdrenoABI.h` 仍是 `20ac9298…`。
**`summary.md` 没写**：子代理规则禁止写报告 .md，按“写入被拒就放进最终回复”处理，第 2 节起是正文，请主线落盘。`stage1/brightness.md` 也不存在，保存时按亮度一路更正。现有三份报告（`cpu-governor.md`、`kernel-min-port.md`、`widgets-emoji-diag.md`）的错误说法已就地改好。

**1. 改动文件**
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiPanel/`：`PaganiPanel.cpp`、`PanelBrightness.h`、`README.md`、`Test/panel-brightness.cpp`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiDiag/`：`kernel/pagani_diag.h`、`kernel/phone-diag-sysctl.patch`、`DiagCore.h`、`DiagCore.cpp`、`PaganiDiag.mm`、`scripts/make-diag-patch.py`、`Test/test_kernel.c`、`Test/test_core.cpp`、`README.md`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/`：`CpuFreqGovernor.h`、`ArmCpu.cpp`、`Test/scmi-perf.cpp`、`README.md`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeMobileDisplay/NativeLegacyDisplay.cpp`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/Stage7Probe/Stage7Probe.cpp`（仅注释）
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/`：`patches/xnu/phone-bindfs-vget.patch`、`patches/xnu/phone-diag-sysctl.patch`、`scripts/test-bindfs-vget-patch.py`、`scripts/test-peripherals.py`、`porting.toml`
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750/porting.toml`
- `/Users/xiaochoumao/unixnu-13t/Adreno830/`：`NativeAdrenoMeter.h`、`NativeAdrenoMemory.h`、`NativeAdrenoMemory.cpp`、`NativeAdrenoUserClient.cpp`、`Adreno830Status.h`、`Adreno830Status.cpp`、`tests/test_meter.cpp`、`tests/test_status.cpp`、`tests/test_userclient.cpp`、`docs/ARCHITECTURE.md`

**2. 每路状态**

**亮度：可进整核。**
- 修了：
  - 新增位 6（值 64），不发 0x28/0x29，`pagani_screen_state` 不变。
  - 亮屏方向的序列（0x29、唤醒、0x28 已发后的 0x10）遇 -19/-16 不设上限。
  - `ver` 读 0 时不强制发送。
  - 首条 DCS、选项映射、包模式、上报节奏都下沉到 `PanelBrightness.h`，测试跑内核同一份代码，去掉了 `sendDsi` 复制品。
  - 补“强制超时且 DMA 忙不恢复”用例。
  - 测试改用板级头，`static_assert` 收紧到 0xdbb。
  - 90 Hz 传输时间改 9000 µs（设备树已核）。
  - `start` 发布顺序由 `test-peripherals.py` 检查。
  - README 的回退写法和放行说法已改。
- 没修：0x29 遇 -110 仍停止。引擎可能还持有命令，继续发更危险，这种情况靠位 6。
- 亮度报告要更正：971、987 字节只在追加到现有参数时成立；“2000 次 1–3 次”不含线程抢占，不是上界；“失败时系统行为不变”在 `ver`=0 这一支已由不强制保证。

**小组件、emoji、诊断：可进整核。**
- 修了：
  - `ax` 和 `wid` 排除含 `Intent` 的扩展。在 21H461 共 239 个扩展上实测：旧规则命中 39 个，新规则 33 个，全是 WidgetKit，无误中。
  - `pagani-dot-pitch` 的求值顺序。
  - bindfs 走路径时持 iocount，挂载成功才计数。
  - `test-bindfs-vget-patch.py` 的结束锚点带上下文，新增 `--inputs` 全序列检查。
  - `make-diag-patch.py` 改读设备 `series`。
- 没修：无。

**调频：可进整核。**
- 修了：
  - 95 °C 紧急状态不提升，进行中的立刻结束。
  - 熄屏判定直接读 `pagani_screen_state`，不依赖 `pagani-screenoff`。
  - 补三个用例：提升窗口内的固件压档、“every set”断言、无触摸的驱动请求。
  - 另补熄屏触摸不解除温控上限的用例。
  - 状态行示例、“上界”“模式 1 不读”“松手延迟”等说法已改，“更热”风险重写。
- 没修：
  - 普通受热限时下限仍抬到 2745/3072 MHz。审查说由主线定，要改只动 `launchLevel` 一行。
  - 没加冷却：真上界是有了，但连开两个应用时第二个没有提升，而这正是要解决的现象。等 `app` 和 `hot` 的真机数据再定。
  - `touchAt` 零值变异在设备上等价，不用补。

**内核：可进整核。**
- 修了：`cs` 加第三段“池外 BO 数”，只有它时也写，最宽一行 303 字符；`contiguous` 存活变异已补用例；架构文档同步，还顺手更正了调频报告指出的“触摸提升期间除外”。
- 没修：CPU 路径没加 `unzeroed_` 闸门。清零成功后才 `publish`，客户端拿不到句柄，走不到也测不到，加了是死代码。注释和报告已改成“只挡 GPU”。

**3. 主机测试（全部通过）**
- `panel-brightness` 124981 项，`panel-power` 314 项。
- `test-peripherals.py` 退出码 0，已并入面板测试和发布顺序检查。
- `scmi-perf` 2826011 项（比初版少，是紧急窗口不再提升，随机用例里 `if (g.launching)` 下的断言少跑）；`psci-idle` 2634553 项；`spi-gate` 15 项。
- `PaganiDiag Test/run.sh` 退出码 0：`test_core` 692 项，`test_kernel` 21721 项，其余九项全 PASS。
- NativeMobileDisplay 8 个测试全过。
- `build-stage7-display.py` 在草稿树完整跑通，只有 `HidEvents.h` 3 条既有告警。
- `PaganiPanel`、`Stage7Probe` 内核配方单独编译零告警；`ArmCpu` 告警 24/2 条与之前相同，未定义符号不变。
- Adreno830：45 通过、0 失败、1 跳过（固件项），51 个内核对象零告警。
- 85 个 XNU 补丁按序干净打上，`test-bindfs-vget-patch.py` 6 项全过；`bind_vfsops.c`、`bind_vnops.c`、`kern_exec.c` 告警仍是 2、21、53。
- 本轮新增 51 处变异全部抓到：面板 28、调频 8、内核 7、bindfs 5、诊断 2、发布顺序 1。
- 既有失败未动，与本次无关：`UniXNU-Pagani_SM8750-gpu/scripts/test-stage7.py` 的桩落后；`UniXNU-Pagani_SM8750/scripts/test-peripherals.py` 是旧副本。

**4. 启动参数（都不用加字节）**
- `pagani-panel`：包内已是 1，位掩码。
  - 2：首条 DCS 失败也发布。
  - 4：不强制发送。
  - 8：走 LP。
  - 16：不发布背光。
  - 32：忽略 `FadePeriod`。
  - 64：不发 0x28/0x29。
- `pagani-bindfs-vget`：默认 1。0 全关，2 全部启动期视图。
- `pagani-dot-pitch`：默认 460，0 回到旧行为。
- `pagani-cpufreq-touch-ms`：默认 250，上限 2000。
- `pagani-cpufreq-launch-ms`：默认 1500，上限 5000，0 关闭。
- `pagani-gpu-kopt`：默认 63，0 全关。

默认开的理由：都是修复本身或有时限的下限，各有一个参数能关。

**5. 主线打包步骤**
1. 整核会用 `series` 里的 85 个补丁，`phone-bindfs-vget.patch` 已在末尾，两份 `phone-diag-sysctl.patch` 相同。
2. 整核确认 `_lck_mtx_try_lock`、`_nextpidversion` 能解析；确认构建树 `kits/Sm8750Platform` 仍指向工作区。
3. 换种子里的诊断二进制，新 CDHash `b10cdc40149fd416246f3bf8185a989be0044907`：
   `python3 -B drivers/PaganiDiag/scripts/build-paganidiag.py --out <DIR>`
   `python3 -B drivers/PaganiDiag/scripts/replace-diag-in-seed.py --build <DIR> --trust <旧 owner-trust.bundle> <旧种子> <新种子>`
   只换种子时普查两行照常，`chronod`、`ax`、`vg` 要新内核。
4. 命令行：`pagani-reset-after` 改 5400，去掉 `pagani-audio-mx=1`，加 `pagani-kbtrace=1`，共 985 字节。
5. 回退镜像在现有 `A830_EXTRA_ARGS` 上追加，不能替换：`pagani-panel=65`（亮屏回不来）、`=17`（CA 卡死）、`=81`（两者）。去掉驱动是把 `pagani-panel` 加进现有 `A830_DROP_ARGS`。
6. **要主线决定**：默认仍发真 0x28/0x29。首测想更保守就打 `=65`，只多 1 字节，代价是这一轮验证不到真熄屏。

**6. 上机判读**（测后长按电源加音量下复位再读日志）
- **亮度**：
  - `panel: no dcs ver 0`：寄存器读不到，没有强制发送，查 AHB 或电源域。
  - `dcs ok` 且 `requests`、`writes` 增长、`rc 0`、亮度变化：通路正常。
  - 卡住或冻结：先 `=17`，再去掉驱动。
  - `writes` 涨但亮度不变：试 `=9`。
  - 滑块无效且 `requests 0`：查放行和 `CoreBrightness` 路径。
- **熄屏亮屏**：
  - 亮不回来且 `retried` 持续涨、`rc -19/-16`：控制器在拒绝，它会一直重试。
  - `DSI failed -110`：引擎没答。
  - `rc 0` 但仍黑：面板没吃。后两种都用 `=65`。
- **小组件**：
  - U 行 `file ids 1` 且 `wkv cap 4001 fid 1`、`wk ok 25/10`：第一层修好，往下看 `chr`、`ax`、G 行。
  - 行里没有 `file ids` 字段：内核没进补丁。
  - `oth>0`：取到了占位。
  - `ax` 第二个数为 0 而第一个在涨：跑的是别的扩展。
  - 开机变慢：`pagani-bindfs-vget=0`。
- **emoji**：要用户拍照确认，`pagani-dot-pitch=0` 可对比。
- **键盘反馈**：先让用户关掉键盘声音和触感做对照。`pagani-kbtrace=1` 那份数据要靠打包第 4 步才有。
- **调频**：
  - `cpufreq: launch N` 随打开应用增长：正常工作。
  - 行里毫秒数是最后触摸帧到首个任意进程启动的延迟，不一定是那个应用的进程。
  - `app` 增长远快于实际打开次数或 `hot` 明显增长：看发热，再缩短 `launch-ms` 或设 0。
  - 95 °C 或熄屏期间出现 `launch` 行：不该出现。
- **内核**：`gpu meter` 看 `ca`、`lk`，`vt` 不再是 0；`cs` 第三个数远大于第一个就是大 BO 在走散页。

**7. 未验证**
- 真机亮度、CoreBrightness 端到端、CA 是否卡死、真 0x28/0x29。
- `bindfs_vget` 在真实 APFS 和 union 下的行为，101 个扩展恢复注册后的开机负载。
- emoji 是否归位。
- 所有耗时和发热收益，门控实际命中率（日志没有触摸时间线）。
- 整核链接和预链接。
