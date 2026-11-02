# 一加 13T（UniXNU + iPadOS 21H461，iPhone16,1 身份）功能现状证据清单

- 键：evidence（盘点工作流 `waga00lun` 的“真机证据”一路）；撰写时间 2026-10-31 07:50 前后；只读，没碰手机、没改任何源码树、没提交。
- 输入：`Adreno830/docs/PROGRESS.md`（2026-10-29 起全部用户反馈、20 项完美化清单、待真机验证清单）、`工具/gpu-test-loop/runs/` 下 44 个运行目录（`merged.log`、`summary.txt`、`device.txt`、`decode.json`）、当前镜像的 `.payload.json`、种子 `.json`、设备树反编译 `build-userspace/a830-inputs/dt/running.dts`、OS 卷 `osmnt-21H461`（只读）、各并行工作流已写出的笔记（只引用，不重复）。
- 工具：
  - `inventory/tools/aggregate_runs.py`（本次新写，只读）：逐个运行目录解析启动参数、心跳、GPU 行、FPS/卡顿、U 行分主题、AppleKeyStore 失败选择子、exec 次数、全部非零退出，并做跨运行汇总。输出 `inventory/tools/runs.txt`（人读）与 `inventory/tools/runs.json`（机读）。重跑：`python3 -I inventory/tools/aggregate_runs.py 工具/gpu-test-loop/runs --json inventory/tools/runs.json > inventory/tools/runs.txt`。
  - `工具/gpu-test-loop/symbolize-crashes.py --a2s build-userspace/a830-inputs/dsc-21H461.a2s`：符号化 X 崩溃记录（只有 3 份：FindMy、routined、MobileSMS）。
  - `ipsw dyld disass`（AppleKeyStore 镜像，本次用来把失败的 AppleKeyStore 选择子对到 libaks 函数，见第 6 节）。
- 记号：运行目录简称——`D2` = `20261031-070836-pkgD2-widgets-wallpaper-1`，`D` = `20261031-070114-pkgD-longpress-1`，`C` = `20261031-065619-pkgC-system-apps-1`，`A` = `20261031-063106-pkgA-diag-cc-f2-1`，`island` = `20261030-213341-island-apps-1`，`bisectA` = `20261030-180230-bisect-A-old-d331p-1`，`pack` = `20261030-154643-phone-pack-1`，`m8full` = `20261030-144045-m8-full-1`，`acd` = `20261030-140451-m8-acd-1`，`m8` = `20261030-113746-m8-1`。证据写成“目录 seq 时间s 记录类型 原文”，seq 是 `merged.log` 第一列。
- 状态用语：**可用**（用户确认或日志确认正常）、**部分**（能用但有明确缺陷）、**坏**（有代码/资源但结果错误或崩溃）、**缺失**（没有驱动/没有链路）、**未知**（当前镜像没有可判读的证据）。

---

## 1. 当前镜像是什么（C / D / D2 三轮）

| 项 | 值 | 出处 |
| --- | --- | --- |
| C 包 | `测试镜像/boot-A830-C包-系统应用叠加.img`，sha256 `a0733dbc…`，900 s 定时复位 | `build-userspace/a830-inputs/pkgC-run.log` |
| D 包（D、D2 两轮同一张） | `测试镜像/boot-A830-D包-长按取日志.img`，sha256 `81b52cfc…`，1800 s 定时复位；比 C 多 `pagani-gpu-status=1`（覆盖前面的 2） | `pkgD-run.log`、`pkgD2-run.log`、两份 `.payload.json` 逐键比对 |
| 内核 | `95a534422841adae…`（C、D 相同） | `boot-A830-D包-长按取日志.payload.json` `kernel_sha256` |
| Data 种子 | `工具/seeds/phone-island-apps-audio.dbrdisk`，sha256 `fa26fbf2…`（= payload `data_ramdisk_sha256`），容器 2 GiB，开机可用约 2.0 GB | `phone-island-apps.json` `container_bytes 2147483648`、`available_kib 2046092` |
| 信任缓存 | `7c8ef1b7…` | payload |
| 身份 | 根 `model iPhone16,1`、`target-type J120`、`compatible J120AP`、产品名 iPhone 15 Pro、`artwork-device-idiom phone`、`artwork-device-subtype 2556`、`display-scale 3`、`artwork-scale-factor 3`；J120 的 `product-id` 保留（音频表按它查） | `工具/make-a830-layout.sh` 167–214 行；日志 `D2 2004 5.394 U identity: hw.product iPhone16,1 cpu type 0x100000c subtype 0x1` |
| 开着的驱动参数 | `pagani-buttons=1 pagani-touch=1 pagani-panel=1 pagani-gpu=1 sheng-gpu-addon=1 sheng-fb-gpu=1 pagani-gpu-profile=0 pagani-gpu-acd=1 pagani-gpu-pool=1024 pagani-cpufreq=2 pagani-wdog=30 pagani-overlay=0 pagani-reset-wdog=1 sheng-staged-apps=1 pagani-system-apps=1 pagani-audio=1 pagani-diag=0 sheng-aks=1` | `D2 6 0.000 A` |
| **没开**的硬件路径 | `pagani-battery`（电量计）、`pagani-adsp`（ADSP）、`pagani-speaker`（功放）、震动（驱动还在草稿）、`pagani-screenoff`、`pagani-idle`、`pagani-gpu-va=16`、`pagani-gpu-llcc`、`native-ufs-write` / 持久 Data | 同上（参数里没有）；`board.toml` `[kernel] archives` 里 PaganiAdsp/PaganiAudio/PaganiBattery 已链接但默认关；PaganiSpeaker 没进内核 |
| ADT 默认 | `[adt.defaults.u32] sleep-disabled = 1`（系统不走睡眠） | `UniXNU-Pagani_SM8750-gpu/board.toml` |
| 种子里禁用的 launchd 任务（9 个） | `com.apple.CommCenter`、`com.apple.PerfPowerServices`、`com.apple.bluetoothd`、`com.apple.fairplayd.A2`、`com.apple.identityservicesd`、`com.apple.iomfb_fdr_loader`、`com.apple.locationd`、`com.apple.routined`、`com.apple.watchdogd` | `工具/seeds/phone-island-apps.json` `disabled_jobs`；源头 `UniXNU-Pagani_SM8750-gpu/scripts/prepare-ram-data.py` 20–30 行 `DISABLED_JOBS`（注释：“Same-build hardware wait/crash loops documented by UniXNU-sheng and UniXNU-tb128fu”）；routined 是 10-30 21:3x 我们加的 |
| 种子里的 SpringBoard 偏好 | `SBDisableAutoDim`、`SBDontDimOrLockOnAC`、`SBDisableIdleSleep`、`BKNoWatchdogs` 都为真（不会自动变暗/自动锁屏/空闲睡眠，backboardd 看门狗关） | `board.toml` `skip_setup_args = ["--no-auto-dim"]`；`prepare-ram-data.py` 64–67 行；在种子原始字节里 `grep -a` 各命中 1 次 |
| 叠加层 | 壁纸集合、键盘布局、音频插件、控制中心、系统应用视图五层全部挂上 | `D2 2003 5.394 U overlays: wallpaper stage 5 status 0, keyboard stage 5 status 0, audio stage 5 status 0, controlcenter stage 5 status 0, system apps stage 5 status 0` |

三轮结果：C 跑满 978 s 按时复位，但**两区日志都被 Linux 丢弃**（`C device.txt: ramoops: found existing invalid buffer, size 18807764`），只有用户口头反馈；D（XNU 日志到约 157 s）、D2（约 171 s）是用户在场长按复位，**36 份副本零翻转、几乎完整**，是今晚最可信的两份证据。

---

## 2. 日志通道本身的限制（决定哪些结论能下）

- 44 个运行目录里只有 22 个有记录，其中近乎完整的 7 个：`m8`（796/797 校验）、`m8full`（690/691）、`pack`（741/742）、`bisectA`（705/705）、`island`（707/707）、`D`（689/690）、`D2`（733/734）。其余要么两区被丢（`found existing invalid buffer, size 18807764`，pmsg 头 0x880040008 的固定弱单元，见 PROGRESS 20:15 审计），要么 20–60 % 的记录校验不过（看门狗咬合复位约 23 % 掉位，比如 `A` 只有 109/382）。
- 复位方式与保真度：用户长按（PMIC 复位）零翻转；PSCI / 看门狗咬合是硬复位、掉位（`C device.txt: pmic_info … Reset Type: HARD_RESET, Reset Trigger: PS_HOLD`）。所以**早上测试凡是要日志的项目，都应在用户在场时长按取回**。
- 32 KiB 事件环会被冲：D/D2 的 exec/exit 记录只覆盖约 56 s 之后（`D2` 缺 seq 11–1800、2006–2126 …），开机前一分钟里 WidgetRenderer、PosterBoard、CollectionsPoster、各守护进程的启动与早期退出**看不到**。下列“没有崩溃记录”的结论只对 56 s 之后成立。
- X 崩溃记录限流：同一进程名 10 s 内只记一次、30 s 内重复轨迹合并（`symbolize-crashes.py` 文档），所以 MobileSMS 退出 5 次只有 1 份回溯。
- SIGKILL、jetsam 不产生 X 记录，只有 S 记录里的 `status/ns/code`：`ns=1` 是 jetsam（code 7 = 单进程内存上限），`ns=2` 是信号，`ns=10 code=deadfa11` 是 SpringBoard 侧用户强退，`ns=0 status=9` 是被别的进程直接 `kill(SIGKILL)`（没有退出原因）。

---

## 3. 运行总表（新 → 旧，只列有记录或有结论的轮次）

| 运行 | 内容 | 记录 | 时长 | 关键证据 |
| --- | --- | --- | --- | --- |
| `D2` 07:08 | D 包，用户专测小组件、壁纸后长按 | 733/734 | 171 s | 无 X；唯一异常退出 `mount_hfs status=100`；`cpufreq: off: status -6 lvl get`（41.8 s，CPU 调速器自行关闭，见第 7 节）；音频路由 `ports=0`；最大卡顿 948 ms（gap 948） |
| `D` 07:01 | D 包，长按取日志 | 689/690 | 157 s | FindMy SIGABRT（VectorKit 空纹理）；iconservicesagent jetsam（`ns=1 code=7`）；duetexpertd 退出带走 3 个扩展（SIGKILL）；音频路由 `ports=0`；GPU 最高 900 MHz、92 °C、f0 h0 |
| `C` 06:56 | C 包（系统应用叠加） | 0（两区被丢） | 978 s 定时复位 | 只有用户反馈：预装应用全部出来并能打开 |
| `A` 06:31 | A 包（诊断、控制中心、键盘 F2、点屏脚本） | 109/382 | 约 660 s | 键盘按键层第一次收到触摸（`kbt Spotlight[321] … dn 18/18 dr 12 up 15/15 … in 12/12 cand 0/…`）；`FPS 9 … SLOW 497ms gap 500`；诊断守护进程没有任何 `diag:` 行 |
| `combined` 00:39 | 点屏 + 键盘探针 + 音量 | 205/506 | 约 480 s | `322.018 kbt SpringBoard[27] sum ev 206 set 11 dn 0/0 dr 0 up 0/0 …`（F2 之前按键层收不到触摸） |
| `force-install` 23:40 / `metal-diag` 23:07 / `wdog-reset` 22:04 | 预装应用诊断三轮 | 274/542、339/592、219/508 | 591 / 601 / 411 s | `app moves: D S>- com.apple.migrat 29`（迁移器删掉 29 个待装包）；`identity: hw.product iPhone16,1 cpu type 0x100000c subtype 0x1`；GPU 只有 backboardd 一个客户端 |
| `island` 21:33 | 灵动岛完整包，长按 | 707/707 | 491 s | MobileSMS SIGSEGV ×5（`CI::GLContext::init` pc=0）；routined SIGABRT（`getMOSettingsManagerClass`）；WebKit SIGKILL ×14；SensorKit 扩展 SIGKILL ×5；GPU `f45`（设计配置 45 次页故障） |
| `bisectA` 18:02 | 15:46 旧镜像重放（D331p） | 705/705 | 410 s | PosterBoard SIGABRT ×4、十来个海报扩展 SIGTERM；WebKit SIGKILL ×8（398 s，对应“设置里点选项卡死”）；powerlogHelperd exit 1 ×3 |
| `pack` 15:46 | 第一张 iPhone 布局合并包 | 741/742 | 310 s | PosterBoard SIGABRT ×6；**SpringBoard SIGSEGV ×5**（295 s 起每约 5.4 s）；海报扩展 SIGTERM ×45 |
| `m8full` 14:40 | iPad 身份 GPU 合并版 | 690/691 | 410 s | AegirPoster SIGABRT ×22、KaleidoscopePoster SIGSEGV ×17；fairplayd.A2 ×6；GPU 84–94 °C 被压频 |
| `acd` 14:04 | ACD 首测 | 234/522 | 350 s | mscamerad-xpc SIGKILL；App Store 用户强退（`ns=10 code=deadfa11`）；powerlogHelperd exit 1 |
| `m8` 11:37 | 第一次 GPU 合成桌面 | 796/797 | 180 s | fairplayd.A2 每 10.0 s 拉起一次（68.879、78.897 … 179.071 s，共 12 次） |

其余 22 个目录没有记录（`device.txt` 里是同一个 `found existing invalid buffer`）或全部校验失败（10-30 10:xx 的 S0–S3 回路），不单列。

---

## 4. 功能现状总表

| # | 功能 | 状态 | 最关键的证据 | 已有代码 / 进行中的工作 |
| --- | --- | --- | --- | --- |
| 1 | 震动 / 触感（系统震动、键盘触感、CoreHaptics） | 缺失 | 没有任何驱动、没有任何日志行；用户 06:50：“没有震动感觉非常奇怪，必须修好，全部要原生完美” | 草稿 `build-userspace/a830-inputs/haptics/hw/PaganiHaptics`（PMIH010x HAP530，SPMI 从机 7 的 0xf000/0xf100/0xf200，主机检查通过、未接线）；iOS 侧逆向在 `haptics/ios`（`VirtualAudioDevice_Actuator`、`AVHapticServer`） |
| 2 | 扬声器 / 系统声音 / 媒体播放 | 缺失（只有静音后端） | `D2 1933 2.592 U audio: up mode 1 backend silent …`；`D2 3477 31.617 U audio svc st=c users=0/0 req=0 bad=0 out=0/0/0 in=0/0 taken=0 played=0 …`；`D2 3479 31.617 U audio plug loads=1 opens=1/0 maps=3 by audiomxd vol=1000 sets=0 writes=0 …` | `PaganiAudio`（静音后端，已在镜像里）、`PaganiAdsp`、`PaganiSpeaker`（两颗 TFA9873、GPR/AudioReach）——后两者**从没在真机跑过**；可行性 `haptics/sound/FEASIBILITY.md` |
| 3 | 麦克风 / 录音 / 通话语音 | 缺失 | 同上：`in=0/0`，静音后端录到全零 | 只有 sheng 的 WCD938x 录音参考；13T 是 `wcd939x-tx-slave`（VA 宏 SoundWire） |
| 4 | 音量键与音量条 | 坏 | `D 4277 52.643 U audio route #1 r1 dev=2 va=63 vad=72 ours=35 vold=1 run=0 vcsp=0/0 vc=0 cat=csav mode=imdf out=0 .... .... ports=0 vol=1000 q=13`（D2 4222 同样）；用户 16:25“音量键能用但不弹音量条”，21:24“音量条仍不出”，07:05“音量键不工作” | 路由层问题（VirtualAudio 默认设备没有输出端口，`vcsp=0`，SpringBoard `_isVolumeChangeAllowedForState` 拒绝）；进行中：`a830-inputs/volume` |
| 5 | 静音拨片 / 操作按钮（左侧 Plus 键） | 部分 | 用户 17:27：“Plus 键映射成功，能切换静音模式”；21:24“左侧 Plus 键正常”；想要灵动岛机型的“操作按钮”自定义（未做） | `PaganiInput` gpio97 → Telephony 页 0x0B/0x2E；`board.toml` `function-button_ringerab = 0` |
| 6 | 电源键、锁屏、亮屏 | 可用 | 用户 16:25：“电源键锁屏/亮屏完美” | `PaganiInput` PON、`PaganiPanel` DCS 0x28/0x29 |
| 7 | 触摸 | 可用 | 用户长期在用；点屏脚本 `A 308.114 U input script: kb spotlight 608,1320>608,1770 250ms` 被真手指打断 `abort kb tap shift: real touch` | `PaganiInput` S3910 |
| 8 | 显示、120 Hz、GPU 合成 | 部分（流畅但有大停顿） | `D 4778 90.820 F FPS 120 (0-120) \| ft 8.3 p99 9.6 max 10.9ms \| gpu 120 cpu 0`；GPU 全程 `f0 h0`，最高 900 MHz、92 °C（`D 5027 140.688 G GPU RUN 900M b46 g11/b10 s8118 f0 h0 p0 92C`）；但 `D2 4956 128.862 J jitter 948.5ms … gap 948 off 0.0 take 0.0 copy 3.9`（合成器没交帧） | Adreno830、NativeMobileDisplay；见第 7 节 |
| 9 | 亮度（手动）/ 自动亮度 / 原彩 | 手动：大概率可用（无投诉、无日志）；自动亮度、原彩：缺失 | 没有 `panel:` 日志行；用户从未投诉亮度；13T 光感在屏下、要屏幕内容补偿（安卓属性 `ro.oplus.fusionlight=true`、`ro.display.underscreen.lightsensor.support=1`、区域 `716,89,788,150`） | `PaganiPanel`（DCS 0x51，`backlight-control`）；光感见 #24 |
| 10 | 默认壁纸（桌面 + 锁屏） | 坏（纯黑） | 用户 15:46“壁纸仍然没有”、17:27“系统默认壁纸读不出来，桌面和锁屏仍是黑的”、07:05“壁纸没有”；D2 没有 PosterBoard/CollectionsPoster 崩溃记录（56 s 后），叠加层 `wallpaper stage 5 status 0` | 根因已由壁纸工作流找到：WallpaperKit 只认小写 `any`，我们的覆盖层写了 `Any` → 两个默认包被 `+[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:]` 拒掉 → 合成 `Solid Black`；其后还有 HEIC 无硬件解码一关（`a830-inputs/wallpaper/notes/root-cause.md`） |
| 11 | 小组件（桌面、负一屏、锁屏） | 坏（全黑，不崩） | 用户 16:25“小组件全黑”、07:05“小组件仍黑”；D2：无 X、无 WidgetRenderer 异常退出（56 s 后）、GPU `f0 h0` → “画成黑的，不是崩溃”（PROGRESS 07:20） | J120 jetsam：`com.apple.chrono.WidgetRenderer-Default` 活跃软上限 98 MB、非活跃 45 MB；进行中：`a830-inputs/widgets` |
| 12 | 控制中心 | 可用 | 用户 06:50：“基本上完美，不像以前错乱”；`controlcenter stage 5 status 0` | `phone-controlcenter-settings.patch` + `DefaultModuleSettings~iphone.plist` 叠加 |
| 13 | 灵动岛 | 可用 | 用户 21:24：“灵动岛出来了” | `phone-island` 布局 + 状态栏 text-fixup |
| 14 | 键盘（输入、卡顿、候选词） | 部分 | F2 之前：`combined 322.018 kbt SpringBoard[27] … dn 0/0 dr 0 up 0/0`；F2 之后：`A kbt Spotlight[321] sum ev 102 set 4 dn 18/18 dr 12 up 15/15 task 529/529 in 12/12 cand 0/…`；用户 06:31：“能工作了，但输入极其卡、可能不到一帧”；`A` 的 `FPS 9 … SLOW 497ms gap 500 … copy 0.7`；候选词 `cand 0/0` 一直为 0 | A 包开着 `pagani-kbtrace=3`（UIKit 通用信号点全局打开，首要嫌疑）；C/D 包已不开探针，**C/D 之后没有键盘卡顿的反馈，也没有日志**→ 现状未知 |
| 15 | 设置页慢 / 点选项卡 | 部分（已知原因修了一个，现状未复核） | IMG_8517：`SLOW - idle 10.3s`（fairplayd.A2 每 10 s 拉起，`m8` 68.879…179.071 s）→ 15:50 起种子禁用 fairplayd；用户 21:24 仍说“设置页仍很慢”；`bisectA` 398 s 8 个 WebKit SIGKILL 对应“设置里点选项卡死” | 设置里的网页视图 / WebKit；C/D 无反馈 |
| 16 | 界面卡死 / 大掉帧 | 部分 | D2 23 条 J 记录，7 条 ≥250 ms，全部 `gap` 主导（948/809/674/572/569 ms），`copy` 只有 1–15 ms；`D2 3701 41.849 U cpufreq: off: status -6 lvl get` 之后 CPU 停在开机档（心跳 `cpu 1986MHz`）；用户 21:24“有地方会卡死” | 见第 7 节 |
| 17 | 预装应用（29 个） | 可用 | 用户 06:45：“预装应用全部出来且能正常打开”（备忘录、提醒、时钟、新闻、TV、播客、iTunes Store、健康、天气、股票、家庭、图书、文件等）；`D2 2001 5.394 U system apps view stage 5 status 0, migration sentinel 0`；`D2 2002 … staged apps copy: stage 2 dirs 6574 files 4214 bytes 466987635 failed 0 error 0 ms 2657` | `phone-system-apps-view.patch`；照相亭是 iPad 专属（`UIDeviceFamily [2]`） |
| 18 | 查找（Find My），以及地图、天气地图等 VectorKit 页面 | 坏（打开即崩） | `D 4731 74.385 X crash pid=298 FindMy sig=6 … esr=0x56000080`，符号化：`ggl::MetalResourceManager::newTexture + 1144` ← `upload` ← `AlphaAtlas` ← `-[VKSharedResources _initWithDevice:standardLibrary:]` ← `md::MapEngine` ← `-[VKMapView initWithDescriptor:]` ← `-[MKMapView _initWithFrame:allowsAntialiasing:]`；用户 06:45“查找黑屏闪退” | 根因与修复已在 `a830-inputs/findmy/notes/NOTES.txt`：App 小槽位 124 MiB，metal2vulkan 64 MiB 固定块 → 第一个图像块分配失败 → R8Unorm 128×128 纹理返回 nil；桥补丁 0018 按堆缩块（待上机）。注意：即使不崩，查找本身还要定位、网络、iCloud（#35、#46） |
| 19 | 所有 App 都能用 Metal / Core Image | 部分 | 修前：`island 8498 481.362 X crash pid=427 MobileSMS sig=11 … pc=?:0 lr=c:1976faac4`（`CI::GLContext::init`，Core Image 退回不存在的 OpenGL ES）×5；22:40 起 text-fixup 让 `_wrapGLIsUsable` 返回 0 + 插件规则 `@apps @extensions !SpringBoard`，之后没再出现；FindMy 说明小槽位仍不够 | Adreno830 32 槽位、`zzwc-native-adreno-sandbox-grant-all.patch` |
| 20 | Safari / 网页视图（WebKit） | 未知（有被杀记录） | `island` 332.2–392.7 s WebKit 辅助进程 `status=9 ns=0 code=0` ×14；`bisectA` 398 s ×8；`ns=0` = 被宿主直接 SIGKILL（不是 jetsam）；用户 16:25“Safari 自定义背景也不显示” | 没有网络时 Safari 只能开本地页；J120 jetsam `com.apple.WebKit.GPU` 上限 300 MB |
| 21 | 照片 | 未知 | 用户 17:45：“照片反而黑了”；之后没有反馈；用户自选照片当壁纸“正常”（17:27） | — |
| 22 | 电量显示 | 坏 / 缺失 | 用户 16:25“电量和充电不工作”、21:24“电量显示不准”；当前镜像没有 `pagani-battery`，任何一轮日志都没有 `[battery]` 行 | `PaganiBattery`（NFG8011B 电量计 I2C `i2c@984000` 0x55 + ADSP battmgr）已链接进内核、默认关；**B 包 `boot-A830-B包-A加电池.img`（`66bb5f81`）做好了但从没上机** |
| 23 | 充电（状态、充电控制） | 未知 | 没有 iOS 侧电源源；C 轮 978 s 后安卓读到电量 100 % → 100 %（`decode.json timings.battery_before/after`），不排除 PMIC 硬件自主充电在维持，未证实；原作者限制里写“不能充电”（`路线图.md`） | `PaganiBattery` 充电策略 `pagani-battery-charge=0/1/2`（默认只读） |
| 24 | 光线传感器 / 自动亮度 | 缺失 | 无驱动、无日志；硬件在 ADSP 的传感器岛里（`ssc_interactive { need_lb_algo; receive_screenshot_info; }`，`ro.boot.vendor.qspa.sensors=enabled`），屏下光感要屏幕内容补偿 | 进行中：`a830-inputs/sensors`（`PaganiSensors`，依赖 `pagani-adsp=1`） |
| 25 | 距离传感器（通话熄屏） | 缺失 | 同上；安卓属性 `persist.sys.sensor.disable.fusion_phone_prox=false`（融合式距离） | 同上 |
| 26 | 加速度计、陀螺仪、自动旋转、抬起唤醒 | 缺失 | 无驱动、无日志；`island` SensorKit 扩展被杀 ×5（随 MobileSMS 一起） | 同上 |
| 27 | 指南针（磁力计） | 缺失 | `oplus_sensor { mag_fusion { sensor-type = <0x06>; is-virtual-sensor; } }` | 同上 |
| 28 | 气压计 | 缺失（有无硬件未证实） | 设备树里没有物理传感器节点（都在 SSC 里），要在安卓下 `dumpsys sensorservice` 只读核对 | 同上 |
| 29 | 计步 / 运动协处理器 | 缺失 | 无 | iOS 计步来自运动协处理器（AOP/SPU）；同上 |
| 30 | 相机 | 缺失 | 用户 21:24“没有相机”；`acd 7390 307.042 S exit pid=379 mscamerad-xpc status=9 ns=2 code=9` | 无。硬件：CAMSS（`qcom,cam-isp`、`cam-icp`、`cam-ipe680`、9 个 `camera_*` 模组）、CCI `i2c@884000` |
| 31 | 手电筒 / 闪光灯 | 缺失 | 用户 21:24“没有…手电筒” | 无。硬件：PM8550（SPMI 从机 1）`qcom,flash_led@ee00`（`qcom,qti-pm8350c-flash-led`），`qcom,camera-flash` ×3 |
| 32 | 蜂窝网络、信号、SIM、电话短信 | 缺失（CommCenter 被禁用） | 种子 `disabled_jobs` 含 `com.apple.CommCenter`；安卓 `gsm.sim.state ABSENT,ABSENT`、`persist.radio.multisim.config dsds`、基带 `Q_V1_P14` | 无。硬件：`remoteproc-mss@04080000`（`qcom,sun-modem-pas`）、IPA |
| 33 | Wi-Fi | 缺失 | 13T 的设备文档：“No verified Wi-Fi or cellular path is selected for PKX110”；D2 137 s 只看到 `wifip2pd` 被拉起 | 进行中：`a830-inputs/wifi`（`PaganiWifi`，移植 sheng 的 ath12k 栈到 peach `17cb:110e`） |
| 34 | 蓝牙 | 缺失（bluetoothd 被禁用） | `disabled_jobs` 含 `com.apple.bluetoothd`；OS profile 的兼容修补里还改了 `BTAudioHALPlugin`（`prepare-ram-data.py` `COMPAT_FIXUPS`） | 无。硬件：`bt_peach`（`qcom,peach-bt`），固件分区 `bluetooth_a`（sde7） |
| 35 | GPS / 定位服务 | 缺失（locationd、routined 被禁用） | `disabled_jobs` 含 `com.apple.locationd`、`com.apple.routined`；`island 8411 441.030 X crash pid=425 routined sig=6`，符号化 `___getMOSettingsManagerClass_block_invoke + 232` ← `-[RTPeopleDiscoveryProvider _setup]` | 无。GNSS 在 MPSS 里（PaganiAdsp 的服务表把 `gps/gps_service` 标在 modem 180 域） |
| 36 | NFC / Apple Pay | 缺失 | 无 | 无。硬件：NXP SN220T `nq@28` 在 `i2c@a80000`（`qcom,sn-nci`），eSE `st54spi_gpio` |
| 37 | 指纹 / 面容 ID | 缺失 | 无；当前身份 iPhone 15 Pro 本身是面容 ID 机型 | 无。硬件：屏下光学 `fingerprint_dev`（`oplus,fp_spi`），安卓 `fps_hal` 走 TEE |
| 38 | USB（连电脑、配件、iOS 下的数据） | 缺失 | 完美化清单 #19“iOS 下 USB 不工作” | 无。硬件：`dwc3@a600000`（`snps,dwc3`）、M31 eUSB2 PHY、QMP 组合 PHY、eUSB2 中继 |
| 39 | 休眠 / 省电（熄屏、自动锁屏、系统睡眠） | 部分 | 按键熄屏可用；种子关了自动变暗/自动锁屏/空闲睡眠（`SBDisableIdleSleep` 等）；ADT `sleep-disabled = 1`；用户 17:45：“休眠”指熄屏后整机低功耗——没做 | `pagani-screenoff`（位 0 CPU、1 GPU、2 面板、3 断电）、`pagani-idle=1/2` 已在内核、默认关、**从没上机** |
| 40 | CPU 调度 / 调频（6 性能核 + 2 超大核） | 部分 | D：`46.860 U cpufreq: d1 at lv8 2841MHz, asked lv9 3072MHz, limits status -3`、`51.870 U cpufreq: d0 at lv4 1152MHz, asked lv10 2400MHz`（固件压频）；**D2：`41.849 U cpufreq: off: status -6 lvl get` → 两域退回开机档，之后心跳一直 `cpu 1986MHz`**；调度仍是 8 核对称（Clutch 单 pset） | `PaganiArmCpu` SCMI PERF 调速器（`pagani-cpufreq=2`）；可移植：sheng `patches/xnu/zzyf-sheng-amp.patch`（128 行，`__ARM_AMP__` + ADT 的 E/P 簇交给 Edge 调度器）。风险证据：原作者 build 59 多簇时出过调度器 ticket-lock panic（MPIDR 0x200），之后才改成 Clutch 单 pset（`UniXNU/docs/zh/devices/oneplus13t.md`） |
| 41 | 持久存储（128–256 GB） | 缺失 | Data 是 2 GiB 内存盘，每次开机全新；用户 16:25“容量显示 2 GB、可用 1 GB”；D 里 Data 可用从 `free 1539M`（15 s）掉到 `free 977M`（155 s） | `PaganiPersistentData`（设计 + 主机原型，未进内核）；`NativeUfs` 只读（`NATIVE_UFS_READ_ONLY`） |
| 42 | 钥匙串 / 密码 / 数据保护 / 锁屏密码 | 坏（新发现，见第 6 节） | D2 每次开机 `stage7 value=0xa: key operation unavailable` 64 次（D 68 次）等；0xa = `aks_wrap_key` | `AppleKeyStoreState`/`NativeKeyStoreEvents` 只回答状态查询（“no keybag, no passcode, nothing locked”），其余选择子一律 `0xe00002c7` |
| 43 | 通知（本地、推送） | 未知 / 推送缺失 | 没有任何证据；推送要 apsd + 网络 | — |
| 44 | Siri / 听写 | 缺失 | 无网络、无麦克风、无声音；D、D2 里 `KonaSynthesizer`（语音合成）被拉起但无下文 | — |
| 45 | Spotlight 搜索 | 部分 | 输入可用（`A` 的 `kbt Spotlight` 计数）；候选 `cand 0/0` | — |
| 46 | iCloud / Apple ID / App Store / iMessage / FaceTime | 缺失 | 无网络；`com.apple.identityservicesd` 被禁用；`backupd` 每轮被拉起 1–5 次后正常退出 | — |
| 47 | 时间、时钟、闹钟 | 部分 / 未知 | 时钟 App 能打开（06:45）；闹钟要声音（#2）；没有校时证据 | — |
| 48 | 温控 | 部分 | GPU 自己闭环（目标 95 °C、105 °C 紧急）：D 最高 92 °C；CPU 交给 CPUCP 固件（D 里多次“asked … at 低档”）；iOS 侧 thermalmonitord 没有传感器输入 | Adreno830 DCVS、`Sm8750Tsens` |
| 49 | 系统守护进程稳定性 | 部分 | D/D2 56 s 之后没有崩溃循环；历史上的循环都已经靠修复或禁用止住（第 5 节） | — |

---

## 5. 崩溃、循环、被杀的进程（跨运行汇总）

数字来自 `inventory/tools/runs.txt` 的 “CROSS-RUN BAD EXITS” 与 “LAUNCH LOOPS”（名字被 XNU 截到 16 字符）。“现状”以 C/D/D2 为准。

| 进程 | 方式 | 次数（运行） | 典型证据 | 原因（已证/推断） | 现状 |
| --- | --- | --- | --- | --- | --- |
| FindMy | SIGABRT（`abort_with_payload`） | 1（`D`） | `D 4731 74.385 X crash pid=298 FindMy sig=6`，`ggl::MetalResourceManager::newTexture + 1144` | 已证：Metal 返回空纹理，VectorKit 主动中止（`os_log_fault "Metal returned null texture."`，`a830-inputs/findmy/notes/NOTES.txt`）；小槽位 124 MiB 装不下 64 MiB 块 | **仍在**（修复在 findmy 工作流，未上机） |
| iconservicesagent | jetsam，单进程上限（`ns=1 code=7`） | 1（`D`） | `D 4950 119.216 S exit pid=101 iconservicesagen status=9 ns=1 code=7`（设置 100.5 s 启动之后） | 推断：J120 jetsam 给它的活跃软上限只有 **20 MB**（`com.apple.jetsamproperties.J120.plist` `com.apple.iconservices.iconservicesagent ActiveSoftMemoryLimit 20`），而它现在是 Metal 客户端，映射进来的 GPU 内存计入 phys_footprint | **仍在**（新发现）；后果：图标可能短暂丢失、重画 |
| CategoriesService / HeuristicInterpreter / AppPredictionInternal | SIGKILL（`ns=2 code=9`） | 各 1（`D`） | `D 4755 84.593 S exit pid=207 duetexpertd status=0` 紧接 `4756–4758 84.598–84.599 … status=9 ns=2 code=9` | 推断：宿主 duetexpertd 正常退出，其扩展被一并结束（正常生命周期） | 无害 |
| mount_hfs | exit(1)（`status=100`） | 3（`D`、`D2`、`m8`） | `D2 5050 150.246 S pid=310 com.apple.MobileSoft exec load_machfile enter` → `5054 150.251 S exit pid=310 mount_hfs status=100` | 推断：软件更新相关任务试挂 HFS 映像失败 | 无害 |
| xpcproxy | exit(78)（`status=4e00`，EX_CONFIG） | 3（`island` ×2、`bisectA`） | `island 7955 326.865 S exit pid=392 xpcproxy status=4e00`（紧跟 MobileSafari 启动） | 某个 XPC 服务的启动配置失败，名字没留下 | 未知，影响小 |
| MobileSMS | SIGSEGV | 5（`island`） | `island 8498 481.362 X crash pid=427 MobileSMS sig=11 … pc=?:0`，`CI::GLContext::init + 220` ← `+[CIContext(Internal) internalContextWithEAGLContext:options:]` ← `-[CIContext initWithOptions:]` ← IconServices 图标缩放 | 已证：拿不到 Metal 的进程 Core Image 退回 OpenGL ES，跳空指针 | 22:40 起已修（`_wrapGLIsUsable` text-fixup + 全 App Metal），之后没再出现 |
| com.apple.SensorKit.*（扩展） | SIGKILL（`ns=2 code=9`） | 5（`island`） | 与 MobileSMS 同一时段（321.2–481.4 s） | 推断：信息 App 的扩展随宿主崩溃被结束 | 随 MobileSMS 修复消失 |
| routined | SIGABRT | 1（`island`） | `island 8411 441.030 X crash pid=425 routined sig=6`，`___getMOSettingsManagerClass_block_invoke + 232` | 已证：iPhone 专属类在 iPad 根卷里不存在 | 种子禁用 routined（21:3x），后果：重要地点/基于位置的建议没有 |
| com.apple.WebKit.*（辅助进程） | SIGKILL，无退出原因（`ns=0`） | 23（`island` ×14、`bisectA` ×8、`acd` ×1） | `island 332.216–332.753 S exit … com.apple.WebKit status=9 ns=0 code=0`（0.5 s 内 9 个） | 推断：被 WebKit 宿主（设置/Safari）直接结束，可能是辅助进程启动或响应超时；不是 jetsam（jetsam 是 `ns=1`） | C/D 窗口里没出现；**未复核** |
| PosterBoard | SIGABRT | 10（`pack` ×6、`bisectA` ×4） | `pack 9212 294.214 S exit pid=800 PosterBoard status=6 ns=2 code=6` | 已证（PROGRESS 15:46/17:15）：iPhone 身份下 WallpaperKit 找不到 `Collections~iphone.plist` 抛 NSInvalidArgumentException | 16:00 壁纸集合叠加后不再 abort；但默认壁纸改为合成纯黑（#10） |
| 各种海报扩展（Infograph、Collections、Hello、Unity、Gradient、Mercury、Pride、Clock、Emoji、Extragalactic、AmbientPhotoFrame、PhotosPosterProvider） | SIGTERM | 合计约 70（`pack`、`bisectA`） | 每次 PosterBoard abort 后成批 `status=f` | 随宿主被结束 | 同上 |
| SpringBoard | SIGSEGV | 5（`pack`） | `pack 9281 295.063 S exit pid=797 SpringBoard status=b ns=2 code=b`，之后约每 5.4 s 一次 | 推断：锁屏需要海报、PosterBoard 一直起不来时某个 5 s 超时后的错误分支（crash-trace 当时还没有） | 之后各轮未再出现 |
| AegirPoster | SIGABRT ×22，SIGTERM ×5 | 27（`m8full`、`pack`、`bisectA`） | `m8full 7633 355.111 S exit pid=394 AegirPoster status=6`，此后 1 s 一次 | 推断：iPad 身份时期海报扩展不是 Metal 客户端（17:15 才给 14 个海报扩展沙盒授权、GPU 槽位 4→12） | 之后未再出现 |
| KaleidoscopePoster | SIGSEGV | 17（`m8full`） | 同上时段 | 同上 | 之后未再出现 |
| fairplayd.A2 | 每 10.0 s 拉起、正常退出（循环） | 49 次启动（8 轮） | `m8` 68.879、78.897 … 179.071 s | 已证：等不到 FairPlay 硬件/身份，循环；连带设置页 10 s 停顿（IMG_8517 `SLOW - idle 10.3s`） | 15:50 起种子禁用；后果：FairPlay 加密内容（付费 App、Apple Music DRM）不可用 |
| powerlogHelperd | exit(1)（`status=100`） | 4（`acd`、`bisectA`），每轮拉起 3–4 次 | `bisectA 11692 394.847 S exit pid=1045 powerlogHelperd status=100` | 推断：它的服务 `com.apple.PerfPowerServices` 在种子里被禁用 | C/D 窗口里没出现 |
| mscamerad-xpc | SIGKILL | 1（`acd`） | `acd 7390 307.042 S exit pid=379 mscamerad-xpc status=9 ns=2 code=9` | 推断：相机相关服务随调用者结束（没有相机） | — |
| AppStore | 用户强退（`ns=10 code=deadfa11`） | 1（`acd`） | `acd 7400 308.013 S exit pid=366 AppStore status=9 ns=10 code=deadfa11` | 用户在多任务里划掉 | 无害 |
| backupd | 正常退出、反复拉起 | 20 次（9 轮） | `D 4899 109.109 S exit pid=137 backupd status=0` | 正常 | 无害 |
| 被种子禁用、从不启动的 | — | — | `CommCenter`、`PerfPowerServices`、`bluetoothd`、`fairplayd.A2`、`identityservicesd`、`iomfb_fdr_loader`、`locationd`、`routined`、`watchdogd` | 原作者与我们为止住“等硬件/崩溃循环”而禁用 | 这些是蜂窝、蓝牙、定位、iMessage/FaceTime、电源日志功能缺失的直接原因之一 |

**C/D/D2 的结论**：开机 56 s 之后没有任何崩溃循环；只有 FindMy（打开即崩）和 iconservicesagent（jetsam）两处真实问题。56 s 之前的情况（含 WidgetRenderer、PosterBoard、CollectionsPoster 的启动）日志看不到。

---

## 6. AppleKeyStore：钥匙串与数据保护（新发现）

- 机制：`Stage7Probe.cpp` 187 行 `native_aks_call(selector, result)` 在选择子失败时写 `progress("key operation unavailable", selector)`，即日志里的 R 记录 `stage7 value=0x<选择子>: key operation unavailable`。AppleKeyStore 用户客户端（`UniXNU/drivers/AppleKeyStoreState/NativeKeyStoreEvents.cpp`）只实现 0/4/5/9/12/13/26/27/55/101/102（握手、记账）、7（锁状态）、14（系统包）、17/35（设备状态），其余一律返回 `0xe00002c7`。
- 每次开机的失败次数：`D2` = `0x8`×3、`0xa`×64、`0x1f`×21、`0x20`×2、`0x39`×1、`0x87`×1；`D` = `0x8`×6、`0xa`×68、`0x1f`×21、`0x20`×2、`0x39`×1、`0x87`×1（`runs.txt` `aks_fail`）。
- 选择子对到 libaks（21H461 共享缓存 `AppleKeyStore` 镜像，`ipsw dyld disass`，找 `IOConnectCallMethod_stub` 前的 `w1`）：
  - `0xa` = `_aks_wrap_key`（0x202fd5130 `mov w1, #0xa`）——**最频繁**；securityd 写钥匙串条目、保存 Wi-Fi 密码、账号令牌都要它。
  - `0xb` = `_aks_unwrap_key`（没在失败里出现，因为没有东西能被包起来）。
  - `0x8` / `0x20` = `_aks_lock_device`（0x202fd80e0–0x202fd80e8 按参数选 8 或 0x20）——锁屏时调用，失败无害。
  - `0x1f` = `_aks_get_configuration` / `_aks_prederived_is_enabled`。
  - `0x39` = `_aks_remote_peer_get_state`；`0x87` = `_aks_bak_get_beacon_internal` / `_aks_bak_get_swizzler_internal`（备份钥匙包）。
- 后果（与 sheng 的记录一致：sheng `porting.toml` network 区 limits“The system cannot store the network password, so the network is not remembered.”）：钥匙串写入失败 → Wi-Fi 密码、Safari 密码、Apple ID 令牌、App 的钥匙串数据都存不住；也不能设锁屏密码（没有钥匙包）。Wi-Fi 做出来之后这一条会马上变成可见问题。

---

## 7. 流畅度、卡顿、CPU/GPU 证据

- GPU 健康：C/D/D2 全程 `f0 h0`；D 最高 `GPU RUN 900M b46 g11/b10 … 92C`（`D 5027 140.688`）；D2 最高 73 °C。`island`（设计配置）有 `f45`，是唯一一轮有 GPU 页故障的长日志。
- 掉帧形态：D2 23 条 J（≥50 ms 帧间隔），7 条 ≥250 ms，**全部 `gap` 主导**（合成器没交帧）：`57.782 jitter 802.3ms … gap 809 … copy 126`（开机着色器）、`128.862 jitter 948.5ms … gap 948 … copy 3.9`、`142.878 jitter 673.8ms … gap 671`、F 行 `143.880 FPS 23 … SLOW 572ms gap 570`。D 有 24 条 J，最长 `89.819 jitter 819.5ms … gap 821`；120–156 s 连续 `FPS 10 … SLOW 100ms gap 100`（某个 10 Hz 的界面在动，不一定是卡顿）。拷贝本身 ≤15 ms（开机时 120 ms 级），所以停顿在应用/合成器/CPU 侧。
- **CPU 调速器自行关闭（新发现）**：`D2 3701 41.849 U cpufreq: off: status -6 lvl get`、`3702 cpufreq: d0 back to boot lv8: ok`、`3703 cpufreq: d1 back to boot lv0: ok`。SCMI 状态 -6 是 BUSY（`ScmiPerf.h` 只把 -1/-2/-3 写进注释，-6 按 SCMI 规范为 BUSY）；`CpuFreqGovernor.h` 40 行写明“Every failure switches the feature off for the boot”。于是 D2 从 41.8 s 起性能域停在 lv8（约 1996.8 MHz，D 里 `101.971 cpufreq: d0 at lv8 1996MHz` 印证档位）、超大核域停在 lv0（约 1017.6 MHz），之后心跳几乎都是 `cpu 1986MHz`；D2 留下的 948/674/572 ms 大停顿都在这之后（41.8 s 以前的 J 记录被事件环冲掉，无法前后对比）。只有 D2 这一轮出现，D 同一镜像没出现 → 偶发；修法是对 BUSY 重试而不是整轮关闭（A 级：纯软件、主机可测）。
- CPU 被固件压频：D `46.860 cpufreq: d1 at lv8 2841MHz, asked lv9 3072MHz, limits status -3`（`PERF_LIMITS_GET` 被拒）、`51.870 d0 at lv4 1152MHz, asked lv10 2400MHz`、`51.870 d1 at lv3 1689MHz, asked lv11 3513MHz`、`116.992 d1 at lv13 4089MHz, asked lv15 4320MHz`。说明 CPUCP 在 GPU 90 °C 左右时把 CPU 压下来。
- 内存：D2 心跳 `avail` 从 1059636（10 s）降到 571366（171 s）（按 4 KiB 一页计约 4.3 GB → 2.3 GB；页大小按 PROGRESS 14:16 条“多出约 1.3–1.4 GB”反推）；Data 内存盘可用 `free 1539M → 1002M`（D2）、`→ 977M`（D）。前 2 分钟用掉 0.5 GB Data，之后趋平。
- 看门狗：`D2 4918 120.837 U wdog ok: armed 120 s, pets thread 121 observer 120 failed 0, longest gap 1554 ms (bark 15000), cpu 0 beats 120 (gap <= 1592 ms), withheld 0`。
- 键盘卡顿：唯一的数据是 A 包（开着 `pagani-kbtrace=3`）：用户“输入极其卡、可能不到一帧”，屏幕/日志 `FPS 9 … SLOW 497ms gap 500 … copy 0.7`、GPU 忙 9 %。C/D 不开探针后**没有新数据**。

---

## 8. UI / 系统问题逐项

| 问题 | 现状 | 证据 | 备注 |
| --- | --- | --- | --- |
| 键盘卡 | 未知（A 包有，C/D 无数据） | 第 7 节 | 早上第一项要看：不开 kbtrace 时还卡不卡；候选词 `cand 0/0`（kbd 预测无回应）另算 |
| 设置慢 | 未知 | 10 s 停顿的 fairplayd 原因已除；21:24 仍说慢；D 里设置 100.5 s 启动、119.2 s iconservicesagent 被 jetsam | iconservicesagent 20 MB 上限可能让设置里图标反复重画 |
| 某些界面卡死 | 部分 | D2 最长 948 ms 无帧；`bisectA` 398 s WebKit ×8 被杀对应“设置里点选项卡死”；BKNoWatchdogs、watchdogd 被禁用 → 卡住的 App 不会被系统看门狗收掉 | CPU 调速器关掉（D2）会放大 |
| 查找 | 坏 | 第 5 节 | 修了纹理也还缺定位/网络/iCloud |
| 小组件 | 坏（黑，不崩） | D2 无崩溃 | widgets 工作流 |
| 默认壁纸 | 坏（合成纯黑） | 第 4 节 #10 | wallpaper 工作流已找到根因 |
| 控制中心 | 可用 | 06:50 用户确认 | 手电筒、蓝牙、蜂窝、Wi-Fi 模块的开关没有硬件可控 |
| 通知 | 未知 | 无证据 | 推送依赖网络 |
| 锁屏 | 部分 | 电源键锁/亮屏完美；壁纸黑；没有密码（AKS） | 锁屏小组件未知 |
| Siri | 缺失 | 无网络、无麦克风、无声音 | — |
| Spotlight | 部分 | 输入 OK（F2 后），候选词 0 | 搜索结果未知 |
| 图标 | 可用但有风险 | 15:46 用户“图标修好了”；D 里 iconservicesagent 被 jetsam | — |
| 灵动岛 | 可用 | 21:24 | — |
| 预装应用 | 可用 | 06:45 | 照相亭不在（iPad 专属） |

---

## 9. 待真机验证清单（PROGRESS“当前队列”）逐项现状

| # | 项目 | 现状 |
| --- | --- | --- |
| 0 | 读 01:01 那轮日志 | 已做：`20261031-061636-kbd-f2-input-r2-1` 0 条记录，`pmic_info … HARD_RESET, PS_HOLD`，`uptime 18535 s`（01:07 就回了安卓，是 Mac 没认到 USB） |
| 1 | A 包 | 已跑（`A`，109/382）：键盘 F2 生效、控制中心叠加成功、诊断守护进程没有任何 `diag:` 行 |
| 2 | B 包（电量计） | **没跑**（A 之后直接上了 C、D） |
| 3 | 16 GiB VA 光圈 | 00:00 冒烟一轮两区全丢，结论不明，之后没再测 |
| 4 | LLCC | 没跑 |
| 5 | 速度档 | 没跑 |
| 6 | M13（IFPC、slumber、AHB 超时） | 没跑 |
| 7 | 电源休眠（`pagani-screenoff`、`pagani-idle`） | 没跑 |
| 8 | D2 翻页阶梯（DPU 探针） | 没跑（`=3` 会卡死整机，12:12 已证） |
| 9 | 复位前热复位 | 代码未合入；另有“类 Linux 复位前序列”在 `a830-inputs/linux-like-reset`（进行中） |
| 10 | UFS 日志窗口 | 要往手机存储写，需用户同意，没做 |
| 11 | ADSP 探针（`pagani-adsp=1`） | **没跑**——声音、电池管理器、传感器全都卡在这一步之前 |

---

## 10. 完美化清单 20 项（PROGRESS 第 35 行起）对照

| # | 目标 | 2026-10-31 现状 | 证据 |
| --- | --- | --- | --- |
| 1 | 音量条 | 坏 | `audio route … vcsp=0/0 … ports=0` |
| 2 | 预装应用 | **完成** | 06:45 用户确认 |
| 3 | 键盘 | 能输入；卡顿待复核 | A 包 `kbt` 计数；06:31 用户原话 |
| 4 | 设置不慢 | 待复核 | — |
| 5 | 不卡死 | 部分 | D2 J 记录、cpufreq off |
| 6 | 控制中心 | **完成** | 06:50 用户确认 |
| 7 | 壁纸 | 坏（根因已找到） | wallpaper 笔记 |
| 8 | 电量 | 坏（B 包没测） | — |
| 9 | 手电筒 | 缺失 | — |
| 10 | 操作按钮 | 部分（静音开关可用） | 17:27 |
| 11 | 声音 | 缺失 | 静音后端 |
| 12 | 6+2 调度 | 缺失（对称调度）；调速器有 BUSY 即关的缺陷 | D2 41.849 |
| 13 | GPU 完美 | 大部分（f0 h0，偶发大停顿） | D/D2 G、J |
| 14 | 电源休眠 | 缺失（代码默认关） | — |
| 15 | 128–256 GB 存储 | 缺失（设计） | — |
| 16 | 相机 | 缺失 | — |
| 17 | 小组件 | 坏（黑） | D2 |
| 18 | 所有 App 用 Metal | 部分（FindMy 仍崩） | D 4731 |
| 19 | USB | 缺失 | — |
| 20 | iPhone 专属守护进程不崩 | 靠禁用 routined 止住 | island 8411 |

---

## 11. 新发现（今晚以前的笔记里没有）

1. **CPU 调速器遇 SCMI BUSY 整轮关闭**（`D2 3701`）：`PaganiArmCpu/CpuFreqGovernor.h` 的“任何失败即关”策略对 BUSY（-6）也生效，CPU 从此停在开机档（性能核约 1997 MHz、超大核约 1018 MHz）。建议对 BUSY/超时重试几次再关；属 A 级（纯软件、主机测试可覆盖）。
2. **iconservicesagent 被 jetsam**（`D 4950`，`ns=1 code=7`）：J120 jetsam 给它的活跃软上限只有 20 MB，而它现在是 Metal 客户端。可选：在种子或叠加层里放宽这一项（只改数据），或让小客户端少占 GPU 映射。
3. **钥匙串写入全部失败**（第 6 节）：`aks_wrap_key`（选择子 0xa）每次开机失败 64–68 次。Wi-Fi 做出来以后“记不住密码”会立刻出现；一个不安全但可用的软件钥匙包（包/解包用固定或开机随机密钥）属 B 级。
4. **定位、蓝牙、蜂窝、iMessage/FaceTime、电源日志的守护进程是种子禁用的**（`disabled_jobs`），不是“启动后崩溃”。硬件做出来之后要同时把对应任务从 `disabled.plist` 里放出来，并预期新的等待/崩溃循环（原作者禁用它们正是因为循环）。
5. **自动锁屏 / 自动变暗 / 空闲睡眠是种子偏好关掉的**（`SBDisableAutoDim`、`SBDontDimOrLockOnAC`、`SBDisableIdleSleep`），加上 ADT `sleep-disabled = 1`：做“休眠/省电”时这三处都要动。
6. **WidgetRenderer 的 jetsam 上限很低**（J120：活跃 98 MB、非活跃 45 MB；PosterBoard 350 MB）：小组件变黑若与 GPU 内存计入 footprint 有关，这是第一个要排查的上限（只是推断，D2 没有它被 jetsam 的记录，但开机前 56 s 看不到）。
7. **C 包那 978 s 的日志全部丢失**：C 包的结论只有用户口头确认；D、D2 才是当前镜像的唯一日志证据。

---

## 12. 早上上机时，证据角度要看的行

- 每一轮都请用户在场长按取日志（零翻转），并在开机后 60 s 内就开始操作（否则事件环只剩 56 s 之后）。
- 声音/音量：`audio route #` 行的 `out=`、`ports=`、`vcsp=`、`run=`，`audio svc … played=`；音量键后应出现新的 `audio route` 行。
- 电池：`[battery] start … pmic=1` → `i2c up` → `gauge addr=0x55 rc=0` → `state published pct=…`。
- ADSP：`[adsp]` 各阶段（1 Mapped … 13 Channel），任何 `FAIL` 停下。
- 震动：PaganiHaptics 的 U 行（驱动接线后定）；SPMI 只允许从机 7 的 0xf0–0xf2。
- CPU：不应再出现 `cpufreq: off:`；心跳 `cpu …MHz` 应在 1–4 GHz 之间随负载变化。
- 崩溃：`X crash` 行与 `exit pid=… status=` 非 0 行；FindMy、iconservicesagent 两项是否消失。
- 钥匙串：`stage7 value=0xa: key operation unavailable` 的次数（软件钥匙包做出来后应为 0）。
- 壁纸/小组件：没有崩溃也不代表正常，以屏幕为准；需要时打开 `pagani-gpu-status=2` 看 MEM 行的占用最多进程。
