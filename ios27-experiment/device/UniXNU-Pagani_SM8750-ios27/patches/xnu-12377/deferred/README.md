# 推迟的补丁

这里的文件不在 `../series` 里，不参与验证脚本的应用。前 5 个是 10063 版原文，没有改动；`phone-27-asid16.patch` 已经移植到 12377，只差一个硬件数据。

共同原因：下面 5 个界面补丁都写死了 21H461 或 J120 的路径和文件名，内核部分只是一次联合挂载。挂错目录不会 panic，但也起不到作用，还会让 `overlays:` 日志行显示假状态。它们要等 24A446 根卷解开之后才能写对，现在没有根卷（IPSW 的 OS 镜像是 AEA 加密，本机还没解）。

| 补丁 | 假定了什么 | 为什么现在不移植 | 要等的数据 | 恢复时要做的事 |
| --- | --- | --- | --- | --- |
| `phone-wallpaper-collections.patch` | `/Library/Wallpaper/Collections/Bokeh.wallpaperCollection/WallpaperCollection.plist`、`Collections~iphone.plist`；WallpaperKit 用 `Collections~<GSDeviceName>.plist` 列集合 | sheng 在 24A446 上已把壁纸改成 `7570.iPadOS_17-1032w-1376h@2x~ipad.wallpaper`，并加了 floating 层，说明目录和图层都变了（BRINGUP 第 9 块） | 24A446 的 `/Library/Wallpaper/Collections` 目录树；WallpaperKit 的屏幕类选择逻辑；13T 用的身份（J820 还是 iPhone） | 改挂载点和哨兵；`native_gpu_addon4_*` 变量补回 `pagani-audio-addon-mount.patch` 之前；`native_staged_apps_facts` 的 `overlays:` 行加回 wallpaper 字段；`make-phone-wallpaper-overlay.py` 重写 |
| `phone-keyboard-layouts.patch` | `TextInputUI.framework/KBLayouts_iPad.dat`，叠加 21H16 的 `KBLayouts_iPhone.dat` | `.dat` 格式在 27 上没有核实；要 27 的 iPhone IPSW 才有对应文件；配套的 F2 修补也要重新逆向 | iOS 27 iPhone 根卷里的 `KBLayouts_iPhone.dat`；24A446 的 TextInputUI 路径和文件名；`.dat` 版本号 | 改哨兵；`native_gpu_addon5_*` 和第一个 `boot_progress_trace` 声明的关系要重排（本目录外已由音频补丁提供声明） |
| `phone-torch-overlay.patch` | `/System/Library/MediaCapture/H6ISP.mediacapture`，服务类 `AppleH6CamIn` | M5 iPad 的相机栈已不是 H6；插件和 `pagani_torch_ios_commit` 都要重做 | 24A446 上 CMCapture 加载哪个 mediacapture、匹配哪个 IOKit 类；PaganiTorch 的 arm64e 版本 | 改路径；`pagani_torch_ios_commit` 是弱导入，27 内核链接时不能悬空，要改成弱定义或确保组件进链接 |
| `phone-haptics-overlays.patch` | `/Library/Audio/Tunings/J120`、`MediaExperience.framework/J120/` | 根变成 J820，目录名要改；CoreHaptics 配置文件和 `hapticengineconfig.plist` 格式没核实 | 24A446 上两个 J820 目录的内容；`-[AVHapticServer loadSynthPreset]` 在 27 的路径；J820 有没有马达表 | 改两个挂载点；scratch 0x000、0x200 要按 27 的 launchd 环境重新确认；`pagani_haptics_ios_commit` 同上 |
| `phone-controlcenter-settings.patch` | `ControlCenterUI.framework/DefaultModuleSettings~ipad.plist`，叠加 `~iphone` | 27 控制中心重写过，文件是否还在、键是否一样都不知道 | 24A446 的 ControlCenterUI 包内容；`-[CCUIModuleSettingsManager _loadSettings]` 的 27 版本 | 改哨兵；scratch 0x800 与壁纸挂载的先后要重排；`overlays:` 行加回字段 |

通用恢复顺序：先有 24A446 根卷的目录清单，再按上表改路径，放到 `pagani-audio-addon-mount.patch` 之后重新生成 diff，最后跑 `ios27-experiment/scripts/check-xnu-12377-series.sh`。

| 已移植，待硬件数据 | 说明 |
| --- | --- |
| `phone-27-asid16.patch` | 16 位硬件 ASID。缺 Oryon 的 `ID_AA64MMFR0_EL1.ASIDBits`（bit 7:4）实测值。DarwinBoot 已经打印这个寄存器（`PaganiDarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:653`），下次看 UEFI 日志，等于 2 就加进 series 末尾。这个补丁在序列之上能打上，验证脚本会检查。 |
