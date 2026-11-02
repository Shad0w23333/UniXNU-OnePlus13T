# 默认壁纸全黑：根因与证据（2026-10-31，wallpaper 键）

输入：21H461 SystemOS 共享缓存（`systemos-21H461/.../dyld_shared_cache_arm64*`，未滑动地址）、OS 卷 `osmnt-21H461`、
覆盖层 `工具/phone-wallpaper-overlay`（修复前的副本在本目录 `old-overlay/`）。反汇编：`re/*.s`（ipsw dyld disass + a2s 符号缓存）。

## 链路（iPhone16,1 身份，Data 每次开机全新）

1. PosterBoard `+[PBFInitialPosterHelper initialPosterConfigurationWithModelCoordinatorProvider:role:]`（0x1ca8f6d2c）
   → `-[PBFWallpaperKitBridge shouldInstallCollectionsPosterAsDefaultLockScreenWallpaper]`（0x1ca8d5948）：
   `PBUIWallpaperConfigurationManager lockScreenWallpaperConfiguration…` 没有任何旧壁纸数据时走
   `fallbackWallpaperConfigurationForVarient:`（0x1b7f02770，type 4 = 黑色纯色）→ 返回 YES →
   锁屏海报 = `com.apple.WallpaperKit.CollectionsPoster`，描述符 ID = `-[PBFWallpaperKitBridge defaultWallpaperIdentifier]`
   （`[NSString stringWithFormat:@"%lu", WKDefaultWallpaperManager.defaultWallpaperBundle.identifier]`）。
2. WallpaperKit `-[WKDefaultWallpaperManager _loadDefaultWallpaperInformation]`（0x1b971ec10）：没有
   `DefaultWallpapers~iphone.plist` → “Falling back to static system wallpaper” → `sf_isiPad` ? 081E/7570 : 0817/7565
   （0x1b971f4e4）→ `wallpaperCollectionWithIdentifier:` + `wallpaperRepresentingWithIdentifier:`，再按 ID 全集合找；
   都找不到 → “No compatible wallpaper found on disk …; falling back to synthesized wallpaper.” →
   用 `+[UIColor blackColor]` 合成 `400.Solid_Black.png`，建 “Solid Black” 壁纸包（0x1b971f5d8–0x1b971f810）。
   **这就是用户看到的黑：WallpaperKit 自己合成的纯黑壁纸，不是渲染失败**。
3. 为什么找不到：`+[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:]`（0x1b970f628）对每个 `.wallpaper` 包检查
   `logicalScreenClass`：`[WKLogicalScreenClassString() isEqualToString:x]` 或
   `[x isEqualToString:WKWallpaperRepresentingLogicalScreenClassAny]`（0x1b970fa9c/0x1b970fab8），否则记
   “Wallpaper Bundle logical screen class '%@' does not match …” 并返回 NO（0x1b970ff18）。
   常量 `_WKWallpaperRepresentingLogicalScreenClassAny`（0x1f290cab8）→ CFString 0x1f2914a38（.33.dylddata 文件偏移
   0xa5fca38，长度 3）→ 字节 0x1b979ccf4 = **"any"（小写）**（`dyld_shared_cache_arm64.17` 文件偏移 0x5f28cf4：
   `61 6e 79 00`）。系统自带的 Stripe/Bokeh 也写 "any"。旧覆盖层写的是 **"Any"**（大写 A）→ 7565、7570 两个包都被拒，
   集合 0817 空 → 走到第 2 步的纯黑合成壁纸。
4. 修了第 3 步之后的下一道坎（上游已实测）：7565/7570 的分层动画只有一层图像，引用 `assets/J120.HEIC`、
   `assets/J120-Dark.HEIC`（3416²，8 位 sRGB，HEVC 瓦片）。UniXNU 上游 910a19d（2026-10-22，同一 21H461 根卷、同一类无
   AppleAVD/VXD 的机器）：“two HEIC images ImageIO cannot decode without a hardware HEVC decoder, so the wallpaper stayed
   black”，改 PNG 后解决。21H461 里 VideoToolbox 虽注册了 VCPHEVC 软件解码器（`VCPHEVCRegisterDecoder`），CMPhoto 的瓦片解码
   （`VTTileDecompressionPluginClass_create` 0x1aaec29c8）还要经 IOSurface/缩放转换会话，上游实测仍是黑；PNG 走 ImageIO
   的纯 CPU 解码，完全绕开 HEVC/CMPhoto/硬件缩放。旧覆盖层还经一个绝对路径符号链接（`assets` → 封装卷）取图，也一并去掉。

## 其他核对

- CollectionsPoster 授权：`com.apple.posterkit.enhanced-memory-limits`（J120 jetsam：渲染 200 MB、更新 30 MB），沙盒只读
  `/Library/Wallpaper/`；PNG 2732² 解码 29.9 MB/张，浅深色各一张，远低于 200 MB。
- CollectionsPoster 不建 Metal 设备（不在 17 个点名客户端里，靠 @extensions 也不需要）；图层由 backboardd 合成。
- 动画包结构（`WKPlatformPackageView`，0x1b9765f30 起）：`+[CAPackage packageWithContentsOfURL:type:options:error:]`、
  `rootLayer`、`contentsGravity = kCAGravityResizeAspect`、居中；状态值由 WallpaperKit 按背光/解锁进度插值。图层
  `contentsGravity` 默认 resize，所以 2732² 的图被拉伸到 3416 单位的边界，和上游一样。
- 日志：两轮长按日志都没有 PosterBoard/CollectionsPoster 崩溃或 jetsam 记录，与“合成纯黑壁纸、一切正常运行”一致。

## 修复（2026-10-31 08:00，未上机、未提交）

- `工具/make-phone-wallpaper-overlay.py` 重写：两个包 `logicalScreenClass` 写 "any"；Flat 动画的两张 HEIC 在 Mac 上用 sips 转成
  2732² 8 位 sRGB PNG，放进各自 `.ca/assets/`，main.caml 只改 `src`/`relativePath`（图层名 "J120.HEIC" 保留）；不再有符号链接；
  7570 的两张 PNG 是 7565 的硬链接（种子里只占一份，10.75 MB）。生成后自动跑校验器（含 Core Animation 渲染）。
- `工具/check-phone-wallpaper-overlay.py`：按 21H461 反汇编实现的规则 W1/W2/W3/C1/I1/I2 + 预算；`--render` 用
  `工具/phone-wallpaper-render.m`（macOS QuartzCore 的 CAPackage）加载每个动画、核对 24 个状态的图像、渲染锁屏/主屏亮暗四态。
- `工具/replace-phone-wallpaper.py`：把覆盖层换进现成种子副本（保留硬链接、卷 UUID 不变、其余文件不动）；`--check` 只读挂载检查
  任意种子；按 192 MiB 载荷 95% 上限核预算。`工具/make-phone-seed.py --wallpaper` 也改为保留硬链接并先过校验器。
- 测试 `工具/test-phone-wallpaper-overlay.py`：15 项（14 通过 + 1 项 `WALLPAPER_SIM=1` 才跑的模拟器真 WallpaperKit 测试，单独跑通过）。
- 模拟器（iOS 26.5 运行时自带的 WallpaperKit，`工具/phone-wallpaper-wkcheck.m`）实测：常量 'any'；旧 7565/7570 → REJECT，
  新 7565/7570 → LOADS，系统 Stripe → LOADS。
- 种子（本目录 `seed/`）：
  - `phone-island-apps-audio-wallpaper.dbrdisk`（D 包种子 + 新壁纸；信任包原样拷贝 7c8ef1b7…，载荷余量 8.36 MB）
  - `phone-island-apps-audio-findmy-wallpaper.dbrdisk`（findmy 代理 07:43 的种子 + 新壁纸；信任包 ade14456…，插件 dbcf2191…，余量 8.18 MB）
  两份都只改了 `/private/var/ngpu/wallpaper-collections`（另有 macOS 挂载留下的 `.fseventsd` 日志，make-phone-seed.py 一样会留）。
- 旧覆盖层与旧脚本备份：`old-overlay/`；渲染预览：`preview/`（锁屏/主屏 × 亮/暗）。
