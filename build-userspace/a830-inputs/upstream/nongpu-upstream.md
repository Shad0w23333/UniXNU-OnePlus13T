# 上游在非 GPU 方面能帮到 13T 什么（nongpu-upstream）

- 日期：2026-10-31。全程只读：没碰手机，没改任何源码树和上游克隆，只写了本文件。
- 上游版本（均在 `参考资料/上游-20261031/` 下，取 main）：`UniXNU-UniXNU` `407db1a`、`UniXNU-UniXNU-sheng` `6b11bb5`、`UniXNU-UniXNU-sm8550-common` `a9f45dc`、`UniXNU-Ath12kWifi` `f6bf561`、`UniXNU-AdrenoMetal` `831a9dc`、`IssacL04-UniXNU-Pagani_SM8750` `48b93fb`。
- 任务点名的分支 `feat/sheng-audio-kernel`、`docs/native-wifi-state`、`feat/hardware-buttons`、`feat/oslog-export` 都已合入主仓库 main，ahead 为 0。sheng 仓库的 `feat/wifi`、`feat/speaker`、`feat/battery`、`feat/brightness`、`feat/reset-watchdog` 等也都 ahead 为 0。
- 路径约定：不带仓库名的 `porting.toml`、`package/`、`hal/`、`patches/` 指 `UniXNU-UniXNU-sheng`；`alioth.toml` 这类设备记录都在 `UniXNU-UniXNU/docs/.vitepress/device-records/`；`dt/running.dts`、`wifi/`、`adsp-review.md` 在 `build-userspace/a830-inputs/` 下；`device.txt` 指 `工具/gpu-test-loop/runs/20261031-134632-a830-full-1-1/device.txt`。
- 标注：**【读码】** 我读了源码或文件确认；**【记录】** 上游 `porting.toml`、提交说明、设备记录写的实机结果，我无法复现，只是转述；**【推测】** 我的判断。

## 0. 结论

1. **音频、WiFi、电池的代码没有新东西可合。** 我们移植用的 sheng `08cde20e`（10-26）之后，这三块只有 iPadOS 27 和编译层的改动，`Ath12kWifi` 的 HEAD 就是我们拷贝的 `f6bf561`（第 1、2、6 节）。
2. **最值钱的是日志。** sheng 没有解决 RAM 掉位，它根本不靠 RAM：内核每 2 s 把日志写到 UFS 上。我们的 `NativeUfs.cpp` 已经带着这套写窗口，只是构建时用 `-DNATIVE_UFS_READ_ONLY=1` 关了；窗口算下来落在我们自己的 `unixnu` 分区尾部，不碰安卓。这一步要写 UFS，**必须先征得用户同意**（第 10 节）。
3. 用户刚测的完整包，日志一条都没读回来（`runs/20261031-134632-a830-full-1-1`：`records 0`，`ramoops: found existing invalid buffer`，`device.txt:55`）。键盘延迟和小组件都是用户态问题，没有日志只能猜。sheng 的 `BootDiagExport.c` 让内核直接读 `/private/var/db/diagnostics`，配合 UFS 窗口能拿到全系统 os_log（第 9 节）。
4. **亮度有一个很具体的嫌疑。** sheng 记录 backboardd 的沙箱拒绝 `FadePeriod`，要服务声明 `NativeSandboxGrantProcess=backboardd`，而 `PaganiPanel.cpp` 没声明；内核补丁我们已经有。另一个嫌疑是作者第 50 版看到的 `DSI disabled`，这时服务根本没发布。两个都没有 13T 日志证实（第 11.1 节）。
5. **音频有一条来自同款功放的教训。** Nothing Phone (3a)（asteroids）也是两颗 TFA9873，图、时钟、PLL 全通却不响，换 SD1 才出声；sheng 也用 SD1；我们默认 SD0。不必改默认，但测试音应该在一次启动里两条线都走（第 1.4 节）。
6. 蓝牙、传感器、相机、振动：sheng 没有任何实现，我们走得更远。USB：sheng 没有；别的设备有一个实机可用的 CDC-ACM 调试控制台，源码不在本地（第 3、4、5、7、8 节）。
7. 小改动可以直接拿：内置启动参数（缓解 1023 字节上限）、sheng 的精简守护进程清单（第 11.2、11.3 节）。

## 1. 音频

### 1.1 现状

- 【记录】sheng 真机出声：内核测试音 155 周期发出、151 周期返回，六颗功放 PLL 全锁（`porting.toml:241`，`9e2e6de`）；iOS 放音乐、语音备忘录录音都平滑，4843 次 IO 周期里 4822 次间隔 15–25 ms，迟到 0（`porting.toml:248`，`dea464e`）。
- 【记录】限制：有些地方声音刺耳，没有均衡和喇叭保护（`porting.toml:221`）。

### 1.2 实现路径（【读码】）

| 环节 | sheng 的做法 | 证据 |
| --- | --- | --- |
| 是否走 ADSP | 走。固件就地读 `modem_a` FAT 的 `/image` 拆分文件，PAS 起 ADSP | `package/AdspFirmware.c:3,64`，`package/AdspProbe.cpp:6-7` |
| 传输 | SMEM、SMP2P、GLINK | `package/AdspProbe.cpp`，`porting.toml:216` |
| APR 还是 GPR | **GPR**，走 GLINK 通道 `adsp_apps`，源码没有 APR | `package/ShengGpr.h:2-4`，`package/AudioProbe.cpp:69` |
| QRTR | 音频控制和数据**不走** QRTR。QRTR 只在 `IPCRTR` 通道上回答保护域的服务定位 | `package/ShengQrtr.c:2-4,23-26` |
| DSP 图 | 手写 AudioReach 图：I2S 设备图 + 播放流图，APM 就绪后才发 | `package/ShengAudioReach.c`，`package/AudioProbe.cpp:435` |
| 缓冲 | 4 GiB 以下 64 KiB 写合并内存，地址高 32 位放流索引，音频流 SMMU 直通 | `package/AudioProbe.cpp:283,302`，`b49ce39` |
| 功放 | 六颗 CS35L43，I2C 配置，旁路功放 DSP | `package/ShengCs35l43.c`，`porting.toml:219` |
| HAL 插件 | **有**：`hal/ShengAudioPlugIn.c`，联合挂载到 `/System/Library/Audio/Plug-Ins/HAL`，CDHash 进属主信任缓存 | `patches/xnu/zzyd-sheng-audio-addon-mount.patch`，`porting.toml:217` |
| iOS 认设备 | 传输类型报 Thunderbolt，内置类型要苹果端口描述 | `hal/ShengAudioPlugIn.c:289`，`porting.toml:219` |
| 时钟 | CoreAudio 假定 24 MHz，插件找到常量，服务改成 19.2 MHz | `hal/ShengAudioPlugIn.c:79`，`dea464e` |
| 麦克风 | WCD938x，TX SoundWire，AMIC3，能录 | `porting.toml:219,241` |

### 1.3 和我们对比

| 项 | 我们 | 差距 |
| --- | --- | --- |
| ADSP 核心 | `PaganiAdsp` 从 `08cde20e` 移植，又修了 A1–A8（`adsp-review.md` 第 2 节） | 上游没有新修复。A5（RESUME_TX 写错）sheng 现在还是错的 |
| GPR、AudioReach | `PaganiGpr.c`、`PaganiReach.c` 移植 | 无 |
| 功放 | `Tfa9873.c` 自写 | sheng 的 CS35L43 不适用 |
| 数据线 | 默认 SD0（`SpeakerCore.c:589`），`pagani-speaker-sd=2` 可切 | sheng 用 SD1（`ShengAudioReach.c:44,182`），见 1.4 |
| 位时钟 | 默认向 PRM 请求，失败不致命（`SpeakerCore.c:373-377`） | sheng 扬声器路径不发 PRM（`AudioProbe.cpp:596` 起只有图命令），PRM 只用于麦克风 |
| 限幅 | 有数字天花板 | 我们更保守 |
| HAL | `PaganiAudio` 移植并加了路由探针、`srzl` 修复、马达设备 | sheng 之后的改动只有 arm64e 适配（`git diff 08cde20e main -- hal/`），21H461 用不上 |
| iPhone 身份路由 | 我们要额外处理（`audio2/route-re.md`） | sheng 是 iPad 身份，没遇到 |

**不出声的直接原因**【读码】：完整包的启动参数里没有 `pagani-adsp`、`pagani-speaker`（`测试镜像/boot-A830-完整包.payload.json`），PaganiAudio 跑的是静音后端（`PaganiAudio/README.md`）。音量键能调说明路由通了，这和预期一致。

### 1.4 别的设备带来的新事实（比 sheng 更贴近 13T）

`docs/.vitepress/device-records/asteroids.toml`（Nothing Phone 3a，SM7635，仓库 `rootstockos/UniXNU-asteroids`，本地没有）：

- 【记录】同样是两颗 TFA9873：`0x34` 槽 0、`0x35` 槽 1，MI2S，两个 32 位槽，48 kHz，容器里没有功放固件（`asteroids.toml:203`）。这和 `Tfa9873.h` 写的 13T 事实一模一样。
- 【记录】**T40k**：DSP 图、服务、插件、48 kHz 流全通，两颗功放找到，时钟和 PLL 全锁，却**不响**，因为 SD0 上功放拿到时钟没拿到数据；切到 SD1（`asteroids-audio-i2s=0x200200`）后开机音和 iOS 声音都出来了（`asteroids.toml:203`，证据提交 `49ec1ccb`）。
- 【记录】TFA9873 的 STATUS0 第 1、2、4 位（DCOCPOK、OTDS、UVDS）是“正常”标志，不是告警（`asteroids.toml:203`）。我们的 `Tfa9873.h` 只把 NOCLK 当错误，其余原样记录，没踩这个坑【读码】，读日志时注意即可。
- 【记录】功放要在图启动后上电，因为要等位时钟；设备图 32 位、流 16 位，由转换器加宽（`asteroids.toml:203`）。我们一样【读码】（`PaganiSpeaker/README.md`）。
- 【记录】麦克风：v1.7 的 SoundWire master 要写 `SWRM_LINK_MANAGER_EE` 才起总线（`asteroids.toml:203` T46）。13T 的 WCD939x 走 VA SoundWire，将来做麦克风时值得查。
- `pong.toml:142-143`（Nothing Phone 2，SM8475）：也是 TFA9873，配 SD0；APM、功放、HAL 初始化成功，但 audiomxd 因 AirPlay 路由 RPC 超时退出，**没有出声证据**。这条是 iPadOS 18 上的，只作旁证。

数据线的统计：出过声的两块板子（sheng、asteroids）都是 SD1，pong 配 SD0 但没出声证据。13T 哪根线仍然未知。13T 设备树里 `i2s1_sd1_active` 同时写了 `bias-disable` 和 `bias-pull-down`（`dt/running.dts` 1673 行起），可能是输入线，这也是我们默认 SD0 的依据之一【推测】。

**建议**：不改默认，但把 `pagani-audio-tone` 的走查扩成一次启动里 SD0、SD1 各走一遍，sense 输出保持关闭。这样不多占一轮测试，也符合“修完再测”。

### 1.5 能否搬到 SM8750

代码已经搬完，没有新代码可搬。有用的是 1.4 的事实。`asteroids` 的 `AsteroidsTfa9873.c` 和 `AsteroidsAudioProbe.cpp` 可能有参考价值，但不在本地，许可证未知，要下载须用户同意。

## 2. WiFi

### 2.1 现状

- 【记录】sheng 的 WCN7850：加入 WPA2-Personal，DHCP 拿到地址，Safari 能开网页；5 GHz、80 MHz、-73 dBm 下约下行 12、上行 24 Mbit/s，开机 31 s 内拿到地址（`porting.toml:209`，`a57341b`；`docs/zh/drivers/ath12k-wifi.md:22,42`）。
- 【记录】限制：吞吐低；系统存不住密码，钥匙串报 -25291；不扫 6 GHz（`docs/zh/drivers/ath12k-wifi.md:36,43`）。

### 2.2 ath12k 协议栈怎么处理（【读码】）

- **不是重写**。`tools/fetch-sources.sh` 在构建时拉 Linux v7.0.14 的 ath12k、MHI、QMI、QRTR，以及 FreeBSD releng/15.1 的 linuxkpi 头；`shim/` 是 Linux 兼容层；`tools/prepare.sh` 只改生成副本（收包环从 4096 缩到 512、`wmi.c` 等待只求值一次、关 rfkill 脚、QMI 总发 MLO 字段、声明按需主机内存等）。
- 自写部分：`src/nw_mac80211.c`（精简 mac80211）、`src/nw_sta.c`（扫描、加入、WPA2、数据路径）、`src/nw_qrtr.c` 和 `nw_qrtr_mhi.c`（WLAN 固件自己的 QRTR，跑在 MHI 上，和 ADSP 的 QRTR 无关）、`src/nw_crypto.c`。
- 板级接口 `include/nw_abi.h`。sheng 侧 `package/WifiProbe.cpp` 负责电源、PCIe0、固件、DMA、MSI，`package/WifiNet.cpp` 负责 `en0`、Apple80211 请求和假的 `IO80211Controller`、`IO80211Interface` 注册项（`WifiNet.cpp:444`）。

### 2.3 和我们对比

- 【读码】`PaganiWifi` 是同一版本的移植：`Ath12kWifi/ORIGIN.md` 写明来自 `f6bf561`，上游 HEAD 还是它。
- 【读码】sheng 自 `08cde20e` 起对 `WifiProbe.cpp`、`WifiNet.cpp` 的改动只有两类：`sys/mbuf.h` 换 `sys/kpi_mbuf.h`、大静态数组改成堆分配（为 iPadOS 27 的内核 `__DATA`）。**没有功能改动，没有要合并的东西。**
- 已知差异，都不是上游能解决的：芯片是 peach 不是 WCN7850（`tools/peach.py`）；DMA 窗口 sheng 是 32 位 stage 1 翻译窗口，我们是低端内存直通（`PaganiWifi.cpp:8`，`review.md` 第 2 节第 4 条），也就是说**我们这条路 sheng 没验证过**，如果 DMA 出问题，stage 1 窗口是后备思路；阶段 6 要先下载 Linux 源码，需要用户同意（`测试清单.md`）。
- 【记录】sheng 踩过的坑我们都已带上或已知：六路电源要模式票；固件 c2-00259 卡在按需分页，c7-00108 可用；MAC 全零要板级生成；关联要用接入点自己的 HT、VHT 能力；MSI 走 SPI 141（`ath12k-wifi.md:31-41`）。

### 2.4 一处矛盾要更正

`wifi/review.md` 第 6 节说 sheng 的 ADT 没有 `wlan` 节点，“所以这条路不需要 wifid”。这个推理不牢：

- 【记录】sheng 的 wifid 确实跑过：`3e679fb` 说守护进程用 PMK（cipher 6）加入，`67d709a` 说它查国家码、接管加入，文档也写设置能加入（`ath12k-wifi.md:27,43`）。
- 【读码】21H461 的 `com.apple.wifid.plist` 有 `_LimitLoadToDeviceTree = [wlan, marconi-wifi]`；但 sheng 仓库、主仓库、SoC 仓库的源码和 git 历史里都找不到这两个名字，内核的 `is_io_device_tree_entry_exists_with_name` 也没被改过。
- **结论**：sheng 的 wifid 怎么过这道门，我没查明。内核 `autoJoin` 不依赖 wifid 这点还成立（`WifiNet.cpp:539`），但“设备树没节点 wifid 就不起”这件事，要等我们下一次读到 `diag: k… ps` 行里的 `wifid:` 才能定。

### 2.5 能否搬到 SM8750

已经搬了，协议栈层面没有新东西。待上机。

## 3. 蓝牙

- 【读码】sheng 没有任何蓝牙代码；`com.apple.bluetoothd` 在 `sheng-lite` 里被禁用（`package/BootWatchdogPolicy.c:25`）。
- 【读码】41 份设备记录里没有一份实现蓝牙；Ath12kWifi 只做 WLAN。
- 不适用，没有可搬的。13T 的路线仍是 `功能盘点.md` 第 136 行的判断。

## 4. USB

- 【读码】sheng 没有 USB 设备或主机驱动。唯一相关的是经 ADSP 设置计算机端口输入限流（`ShengBattmgr.h:6-8`）和 Linux 读取器里的 USB gadget（`package/linux-log/init`），后者跑在 Linux 里，不是 XNU。
- 【记录】`docs/zh/drivers/community-native-dwc3.md`：`NativeDwc3` 面向 nabu（SM8150），“未记录真机结果”。
- 【记录】`QcomXnuUsbDebug`（alioth、munch、sirius 用，在 FFT-infra 的分支里，本地没有）：自写的 CDC-ACM 控制台，占用 DWC3，枚举成 `1209:316d`，电脑上实时读内核日志（`alioth.toml:496`，diagnostics 为 device-verified）。限制：链路死后 `dwc3_poll` 永久返回 -1（alioth）；sirius 上 220 s 后只重放片段。
- 【记录】iOS USB 栈：离线预链接 `IOUSBDeviceFamily`、`AppleEmbeddedUSB`、`AppleUSBDeviceMux`、`AppleSynopsysOTGDevice` 能链过，但从没上机；`[adt.*]` 加不了 usb 节点，要 DTS 节点带 `unixnu,adt-name`（`alioth.toml:513`）。另外 `usbmuxd` 是电脑侧的，设备侧是 lockdownd（`alioth.toml` 同段）。这几条能直接省我们 `research/usb` 的功夫，这个目录现在是空的。
- 能否搬：CDC 控制台思路可以，要拿到源码并适配 13T 的 eUSB2 和 SMMU 流，工作量不小，只算“值得参考”。

## 5. 传感器

- 【读码】sheng 没有传感器驱动，只在定位表里列了 `msm/adsp/sensor_pd`（`ShengQrtr.c:25`）。41 份设备记录也没有。
- `PaganiSensors`（QMI 400、FastRPC 文件服务、HID 发布）已经远超上游。不适用。

## 6. 电池

- 【记录】sheng 只走 ADSP：`PMIC_RTR_ADSP_APPS` 上的 battmgr，发布 `IOPMPowerSource`；充电和 PD/PPS 由 ADSP 固件自己跑（`porting.toml:258`）。实测 PPS 8.72 V、电池 +1.99 A（`porting.toml:280`）。
- 【读码】自 `08cde20e` 起 `ShengBattery.cpp`、`ShengBattmgr.c` 没有改动。
- 我们：`PaganiBattery` 已移植它的周期和映射，还多了不依赖 ADSP 的 I2C 电量计路径和 OPLUS 属性布局探测。用户说电量显示能用，这条路比上游好。
- 值得记住的【记录】：低电量关机在卸载时 panic（`vnode_rele_ext`，`porting.toml:260`），13T 做自动关机时要留意；不开 ADSP 就没人跑充电器（`功能盘点.md` 第 116 行），上游这条也印证了要靠 ADSP。
- 能否搬：不用搬。

## 7. 相机

- 【读码】上游没有相机工作。sheng 甚至把 `com.apple.cameracaptured` 关了：没有相机时它占着 GPU 客户端，每 5 s 写被拒绝的 trace 文件（`package/BootWatchdogPolicy.c:38-44`）。
- 这个标签在 21H461 的 `LaunchDaemons` 里没有【读码】，所以对我们这版系统不用照抄。不适用。

## 8. 振动

- 【读码】上游没有振动驱动。41 份记录里只有 `cas.toml:140`、`nx769j.toml:301` 把马达列为待办输入，都没实现。
- `PaganiHaptics` 已经走在前面。不适用。

## 9. OSLog 导出

上游有两代做法，只有第二代对我们有用。

| | 第一代 | 第二代 |
| --- | --- | --- |
| 位置 | `UniXNU-AdrenoMetal/NativeOsLogExport.mm`（`e11fe3f`），由 `adrenoprobed` 跑，主仓库 `0208ce6`、`eeb23d0` 接入 | `UniXNU-UniXNU-sheng/package/BootDiagExport.c`（`d92e799`） |
| 办法 | 守护进程调 `OSLogStore`，写成 gz 文件，随缓存导出 | **内核自己**经 VFS 读 `/private/var/db/diagnostics`、`uuidtext`、`CrashReporter`，写进 UFS 导出区 |
| 评价 | 上游自己放弃了：`BootDiagExport.c:2-4` 明说“沙箱和一个从不运行的 launchd 任务（adrenoprobed）都不再挡路” | 现行做法 |

对我们第一代**没用**，两个原因都已被我们查实【读码】：

- 21H461 的 `+[OSLogStore storeWithScope:]` 只答当前进程（`PaganiDiag.mm:544-549`，上游代码 `NativeOsLogExport.mm:43` 用的正是这个调用）。
- 21H461 的 launchd 从签名缓存取任务，叠加目录里的 plist 不生效（`PaganiDiag/README.md`）。

`PaganiDiag` 已经用 `LoggingSupport` 的本地存储读全系统日志，比上游第一代强。**缺的不是读取，是出口**：ramoops 的 U 行每条 200 字符、事件环 32 KiB，装不下。

第二代有帮助的前提是 UFS 通道（第 10 节）。有了通道，内核读文件的办法不依赖任何守护进程。`BootDiagExport.c:35-59` 的根目录表和预算排序可以照用，只换路径。它还能抓屏（`screen.bgrx` 和 24 张场景图）。

【推测】这对键盘和小组件最有用：`chronod`、`WidgetRenderer`、`InputUI`、`kbd` 的 os_log 都在里面。主机端要把 `tracev3` 组成 `.logarchive` 再用 `log show` 看，上游仓库里没有这一步的脚本，要自己试。

## 10. 日志抗复位

### 10.1 我们的问题

- 【读码】`RamoopsLog` 写 RAM，36 份投票。HARD_RESET 下 1 位丢失率 0.08–0.23（`linux-like-reset/TESTPLAN.md:89`），只有用户长按零翻转（`测试清单.md`）。
- 【读码】最新一轮 30 分钟的真机测试（`pagani-reset-wdog=1`）`records 0`：`pmic_info` 第 0 条 `HARD_RESET`（`device.txt:71`），两个区头都坏了。这一轮的键盘、小组件现场全丢。

### 10.2 上游怎么解决

**他们没有解决 RAM 掉位，而是不用 RAM。**【读码】

| 环节 | 做法 | 证据 |
| --- | --- | --- |
| 写入器 | `sheng-log=1`：内核线程每 2 s 写一轮：文本槽、内核消息缓冲，最后写头，再 `SYNCHRONIZE CACHE` | `package/BootLog.c:263,1701-1799` |
| 位置 | userdata 最后 32 MiB，下面 256 MiB 是导出区 | `BootLog.c:2-10` |
| 防护 | 头最后写，读者不信头没计入的内容；每 30 轮回读 GPT，变了就永久停写 | `BootLog.c:1773-1781` |
| panic | `boot_log_panic_flush` 在 panic 里写消息缓冲和 panic 文本 | `BootLog.c:1802-1824` |
| 读回 | 启一个精简 Linux 镜像 `dd` 窗口，经 USB 串口取走；另一条路是 recovery + adb `dd` | `package/linux-log/init:31-41`，`docs/zh/porting/log-capture.md:109-124` |
| 无人值守回来 | Gunyah 看门狗 60 s 咬合；先往 PMIC SDAM 重启原因单元写 bootloader，任何复位都回 fastboot | `package/BootReset.c:92,121,141`，`BootResetCore.h:21` |

【记录】别的设备验证过这条路：alioth 546 s 写 273 轮零错误（`alioth.toml:500`）；asteroids 把同一套接到 NativeUfs，从 recovery 用 adb 取（`asteroids.toml:267`）。

### 10.3 搬到 13T：大部分已经在我们树里

【读码】

- `NativeUfs.cpp:1196-1272` 已有 `native_ufs_log_open/write/sync`、`native_ufs_export_write`，由 `sheng-log=1` 打开。
- 窗口算在根分区上：`UFS_ROOT_PARTITION` 是 `unixnu`（`build-native-ufs.py:28`）。`unixnu` 是 LBA 107409152–124186367，共 16777216 块（`UniXNU-Pagani_SM8750-gpu/board.toml:130-133`，`UniXNU/out/dual_sys/applied2/plan.json`）。**日志窗口在分区相对块 16769024 起 8192 块，导出区 16703488 起 65536 块。**
- 根镜像 9382658048 字节（`userdata-root.img`），只占到约相对块 2290688，尾部 1 GiB 预留是空的。Android 的 `userdata` 完全不碰。
- `hostWrite4k` 每次写都查 `hostInWindow`（`RootCommands.h:330-333`），GPT 回读 `native_ufs_store_gpt_check` 也在（`NativeUfs.cpp:1275-1287`）。
- 读回比 sheng 简单：手机有 KernelSU，`su -c 'dd if=/dev/block/sda16 bs=4096 skip=16769024 count=8192'` 即可，`sda16` 来自 `plan.json` 的 `unixnu_index=16`。不需要 Linux 读取器。

挡路的和要写的：

1. `build-native-ufs.py:28` 的 `-DNATIVE_UFS_READ_ONLY=1` 让 `hostLogOpen` 直接失败（`RootCommands.h:299`），`hostScsiDir` 也拒绝写命令（`RootCommands.h:195-199`）。要改成“只放行日志窗口”，别整个打开。
2. `BootLog.c` 不能整搬：135 KB，asteroids 为它写了 104 个弱引用替身（`asteroids.toml:267`）。我们只要它的节奏和文件头约定，内容直接写 `RamoopsLog` 现成的一份日志，这样 `ramoops_format.py` 不用大改。
3. `native_ufs_log_panic` 在 ShengUfs 子模块里，克隆里是空目录，我们的 `NativeUfs.cpp` 也没有。panic 路径要自己写：`hostWrite4k` 要拿 `gHost.lock`，panic 里不能等锁。
4. 【推测】性能：写和根卷读共用一个请求槽和 `gHost.lock`，每 2 s 写约 49 块再 `SYNCHRONIZE CACHE`，可能带来小抖动。所以只在诊断包开。

更省的变体（我的建议，上游没这么做）：不周期写，只在 `reset_now` 里 PSCI 之前落盘一次。这样定时复位、关机复位不再靠 RAM，平时零开销；卡死时没有这次落盘，还靠看门狗和 RAM；panic 走同一路径。缺点是要在复位路径里做轮询 I/O，要有超时。

**风险和前提**：

- 这是 XNU 第一次在这台手机上发 WRITE(10)。写路径只有主机测试，13T 上从没跑过。
- `测试清单.md` 写着“不往手机存储写数据”，所以**要用户同意**。
- 上机建议分两步：先只开窗口不写，看 `gStoreGeometry` 算出的 LBA；再写一块标记，回安卓 `dd` 核对，并确认 `userdata` 和根镜像校验不变。

### 10.4 其他可参考的

- 【读码】13T 设备树有和 sheng 一样的单元：`sdam@7100` 下 `restart@48`，bits 1..7（`dt/running.dts:12363-12373`），`BootResetCore.h` 的做法理论上可用。我们要回安卓，所以不急。值 2 代表 bootloader 是 sheng 的事实，13T 要核对。【推测】若想绕开“复位后安卓起来但 USB 不枚举”的问题，回 fastboot 也许更可控，但这要写 PMIC，别轻易试。
- `zorn`（SM8650）用 Gunyah 看门狗 + recovery BCB + 单个 4 KiB 日志块做有界恢复（`zorn.toml:286`），思路相近，本地没有源码。
- 【记录】USB CDC 控制台（第 4 节）是唯一不依赖持久化的实时通道。alioth 的记录说控制台死后，只有 UFS 日志还留着记录（`alioth.toml:500`），两者互补。
- `docs/zh/porting/log-capture.md` 的 UEFI 二维码日志（Advanced Logger）只管 `ExitBootServices` 之前，帮不到这个问题。

## 11. 任务清单之外，但对应用户反馈

### 11.1 亮度

sheng 为亮度连修了多次（`38540db`、`5d045d0`、`fb4839d`、`5b0e9c3`、`3521463`、`684e926`），结论写在 `package/AppleARMBacklight.cpp` 里：

| 关卡 | sheng 的结论 | `PaganiPanel.cpp` 现状【读码】 |
| --- | --- | --- |
| CoreBrightness 认服务 | 要 `backlight-control=true` 且 `IOObjectConformsTo` 成立（`AppleARMBacklight.cpp:303-310`） | 真子类，`:28`，`:156` 设了属性，没问题 |
| 沙箱类名 | 只允许 `AppleARMBacklight` 类（`:268-271`） | 继承链含它，应该过 |
| **沙箱 `FadePeriod`** | backboardd 每次都带 `FadePeriod`，沙箱拒绝，要 `NativeSandboxGrantProcess=backboardd`（`:311-314`） | **没有声明**。全驱动树只有 `PaganiTorch`、`PaganiAudio` 声明过 |
| 不阻塞调用者 | 只存最新请求（`:359` 起） | 已是异步 |
| 服务能否起来 | 不涉及 | `:142-143` DSI_CTRL bit0 为 0 就退出，**不发布服务** |

- 内核一侧的补丁 `zzwb-native-sandbox-grant-set-properties.patch` 我们已经有（`xnu-patches.list:52`，`IOUserClient.cpp` 已打上）。**只差 `PaganiPanel` 一行 `setProperty("NativeSandboxGrantProcess","backboardd")`。**
- 作者第 50 版记录过 `DSI disabled r=e00002d8`（`IssacL04.../STAGE6.md:677`），之后“亮度暂缓”，没人再查。如果现在还是这样，服务根本不存在，上面那一行也救不了。
- 我们现在**看不到**是哪种：`boot_progress_peripheral` 只写屏幕行（`BootProgress.c:490`），`pagani-peripheral-status` 没开，45 轮 `merged.log` 里一条 `DSI`、`panel:`、`PANEL`、`brightness request` 都没有。
- 【记录】asteroids 用 sheng 的服务 + DCS 写入做到了 iOS 调亮度（`asteroids.toml:109`，T37d），`zorn` 的 DCS 亮度能读回（`zorn.toml`）。说明路是通的。

**建议**：`PaganiPanel` 加那一行，再把 `start` 的每个失败和第一次 `brightness request` 用 `boot_progress_trace` 写进日志。这是【推测】的修复，不保证这一轮就好；13T 是命令模式带 DSC，`dsi_panel_command` 自身也没验证过（作者记录：“has not passed hardware testing”）。

### 11.2 启动参数额度

【读码】`patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch:1950-1985` 给 `pexpert/gen/bootargs.c` 加了弱符号 `PE_builtin_boot_args`：命令行优先，查不到再查内核内置串。我们的命令行已经很挤（`wifi/review.md` 记 981/1023）。这段不到 10 行，要 rebase 到我们的 xnu 上。

顺带：sheng 内置了 `sched_preemption_disable_debug_mode=1 interrupt-masked-debug-mode=1`，因为开发版内核的调度卫生检查曾把关中断 786 us 判成 panic（`BootWatchdogPolicy.c:38-44`）。我们的 `merged.log` 里没见过这种 panic，不急，但 UFS 写路径上来后可能需要。

### 11.3 精简守护进程

【读码】`BootWatchdogPolicy.c:24-37` 的 `lite_jobs`。对照我们种子的 9 个禁用项和 21H461 的 `LaunchDaemons`，**系统里有、我们没禁**的有：`photoanalysisd`、`mediaanalysisd`、`mediastream.mstreamd`、`coreduetd`、`duetexpertd`、`siriinferenced`、`siriknowledged`、`siriactionsd`、`PerfPowerServicesExtended`、`biomesyncd`、`amsengagementd`、`adid`、`email.maild`、`ndoagent`、`familycircled`、`financed`、`eyereliefd`、`softwareupdateservicesd`、`mobile.obliteration`、`passd`、`icloud.searchpartyd`、`findmy.findmybeaconingd`。

- 【记录】`coreduetd` 一个线程占 50% 以上的核，整个开机不停；`PerfPowerServices` 占 1.8/7 核（`BootWatchdogPolicy.c` 注释）。
- 工具现成：`工具/edit-phone-seed-launchd.py --disable`。
- **别全抄**：`searchpartyd`、`findmybeaconingd` 可能影响刚修好的“查找”；`passd` 影响钱包；`siri*` 影响“键盘听写”之类的不清楚。`seedvars/report.md` 的原则是单变量验证。
- 【推测】这可能减轻打开应用时的抖动和键盘延迟，但没有证据说它就是原因。

### 11.4 键盘延迟和打开应用抖动

上游**没有**键盘延迟的结论。但 sheng 10-31 刚加了一批正好能查这个的工具（都写进 UFS 导出区，依赖第 10 节）：

| 提交 | 内容 |
| --- | --- |
| `b6204e9` | 每 4 ms 采样 SpringBoard、WidgetRenderer、**InputUI**、Preferences、MobileSafari、PosterBoard 的首线程：状态、优先级、等待事件、11 层用户栈、`mach_msg` 对端 |
| `f3b12de` | 每个触摸报告交给 HID 的时间（`kernel.touch.bin`），加发给 backboardd、SpringBoard 的消息时间线 |
| `3075216`、`e4c236e` | `ipc_kmsg_send` 钩子；主屏出现 50 帧后才装上，因为第一版卡在了 logo |
| `a45772f` | `IOUserClient::callExternalMethod` 钩子，记录 200 us 以上的调用和锁等待；每核优先级和空闲状态 |
| `eb95d52`、`0cf0c15` | 每 100 ms 每进程 CPU 时间，三个最忙进程的最忙线程的栈 |

都是 xnu-12377 的钩子，要 rebase；思路可以直接用。

其他线索，都是【推测】：

- 触摸提频：sheng 保持 1 s（`CpuFreqProbe.cpp:24-26`），我们 100 ms（`PaganiArmCpu/README.md`）。打开应用的前几百毫秒可能吃亏，可以单变量试。
- 完整包开了 `pagani-haptics-ios=2`，每次按键的触感都经 audiomxd。可以用稳妥回退包（不带马达）对比键盘手感，先分清是不是触感路径同步等待。上游没有马达路径，这条帮不上。

### 11.5 小组件

上游没有直接结论，sheng 只记过 WidgetRenderer 被拒一次命令缓冲（`porting.toml:184`），属 GPU 范围。非 GPU 这边能帮的只有第 9 节的日志，以及 `boot-ui-focus3=chronod` 这种进程栈采样（`porting.toml:313`）。

## 12. 许可证

| 来源 | 许可 | 说明 |
| --- | --- | --- |
| sheng 仓库、sm8550-common、主仓库 | BSD-2-Clause-Patent（`LICENSE`） | 可移植，保留声明 |
| `BootLog.c`、`BootReset.c`、`BootResetCore.h`、`BootDiagExport.c`、`BootWatchdogPolicy.c` | 文件头 `SPDX: MIT` | 比仓库 LICENSE 优先，保留版权行 |
| `NativeOsLogExport.mm`、`CacheExport.h`（AdrenoMetal） | MIT | 不打算用第一代 |
| `AppleARMBacklight.cpp`、音频、WiFi 板级文件、`hal/` | 文件头 BSD-2-Clause-Patent | 已按此移植 |
| **`UniXNU-Ath12kWifi`** | **NOASSERTION**：仓库没有 LICENSE 文件，0 个文件有 SPDX 头 | `ORIGIN.md` 已记录。主仓库 `CONTRIBUTING.md:101` 说贡献按 BSD-2-Clause-Patent，但“各目录的 LICENSE 适用于该目录”，这个仓库没有，不能据此认定 |
| Ath12kWifi 构建时拉取的 Linux 源码 | ath12k 为 BSD-3-Clause-Clear，MHI、QMI、QRTR 为 GPL-2.0（`ORIGIN.md:8`） | 不进仓库，但会编进内核。自用没问题，对外发镜像前要单独评估 |
| `asteroids`、`pong`、`QcomXnuUsbDebug`、`NativeDwc3` | 未知，本地没有 | 本文只用了设备记录里的事实 |
| 主仓库设备记录 | BSD-2-Clause-Patent | 只取事实 |

## 13. 三类清单

### 可以直接搬

| 项 | 来源 | 动作 | 代价或前提 |
| --- | --- | --- | --- |
| 亮度沙箱授权 | `AppleARMBacklight.cpp:314` | `PaganiPanel` 加一行，并把状态写进日志 | 几行；内核补丁已有；要重编内核 |
| UFS 日志窗口 | 我们 `NativeUfs.cpp` 已有 + `BootLog.c` 的节奏和头约定 | 放开日志窗口写，内容用 `RamoopsLog` 现成日志 | **要用户同意写 UFS**；13T 首次写；panic 路径要自写 |
| 诊断文件导出 | `BootDiagExport.c`（MIT） | 内核读 `diagnostics`、`uuidtext`、`CrashReporter`，写导出区 | 依赖上一项；主机端要自己组 `.logarchive` |
| 内置启动参数 | `zzzz-sheng-ipados27-bringup.patch:1950-1985` | rebase 到我们的 xnu | 不到 10 行，要重编内核 |
| 精简守护进程清单 | `BootWatchdogPolicy.c:24-37` | 用 `edit-phone-seed-launchd.py --disable` 分批禁 | 只改种子；要挑，要单变量 |
| SD 线走查 | sheng、asteroids 的事实 | 测试音在一次启动里走 SD0、SD1 | 只改 `PaganiSpeaker`，且要等 ADSP 上机 |

### 值得参考

- `asteroids`：TFA9873 程序、DCS 亮度服务、`pull-asteroids-log.py`。要下载，需用户同意。
- sheng 10-31 的诊断钩子：4 ms 客户端采样、触摸时间线、IPC 和 IOUserClient 追踪（第 11.4 节）。
- `QcomXnuUsbDebug` 的 CDC-ACM 控制台，alioth 的 USB 栈分析（四个 USB kext 能离线链过）。
- `BootResetCore.h` 的 SDAM 重启原因写法，13T 的 `restart@48` 相同。
- 触摸提频 1 s 的策略；`ButtonProbe.cpp` 的 PMIC 按键中断（SPI 481 经 PDC），我们是 8 ms 轮询，能用，不急。
- sheng 麦克风路径和 asteroids 的 SoundWire v1.7 经验，将来做 WCD939x 时用。
- sheng 的 WiFi 踩坑列表和 stage 1 DMA 窗口，作上机时的后备。
- `pong` 的 audiomxd AirPlay 路由超时，如果我们后面遇到类似现象。

### 不适用

- `ShengCs35l43.*`（功放不同）、`AppleARMBacklight.cpp` 的 KTZ8866 I2C 部分（13T 是 OLED DCS）。
- `package/linux-log/` Linux 读取器（13T 有安卓 root，只在安卓起不来时才有意义）。
- `CpuFreqProbe.cpp` 的 EPSS 寄存器（13T 走 CPUCP SCMI）。
- `UniXNU-sm8550-common`：`Sm8550Platform` 全是 GPU 的 GMU 和 SMMU；两个 XNU 补丁我们有 SM8750 对应物。
- `NativeOsLogExport.mm`（21H461 上 `OSLogStore` 只答当前进程，adrenoprobed 也起不来）。
- iPadOS 27 系列改动：`hal/` 的 ptrauth、VirtualAudio 只在 arm64 上重绑、`IOMallocData` 替代静态数组、`xnu-12377` 的其余补丁。
- 蓝牙、传感器、相机、振动：上游没有东西。

## 14. 没查清的

1. sheng 的 wifid 怎么过 `_LimitLoadToDeviceTree`（第 2.4 节）。下次读到 `ps` 行就知道我们这边的情况。
2. 13T 现在 `PaganiPanel` 到底起没起来（第 11.1 节）。
3. `/dev/block/sda16` 的读法只是按 `plan.json` 推的，没在手机上试。
4. `tracev3` 导出后能否在 Mac 上用 `log show` 解开，没试。
5. 本文所有“能搬”都只是读代码的判断，没有任何一项在 13T 上运行过。
