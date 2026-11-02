说明：report.md 写入被工具策略拒绝（子代理不得写报告文件），我没有绕过，完整报告放在这里。输入是 21H461 根卷、SystemOS 共享缓存、J120 kernelcache、XNU 源和历次运行日志，地址都是未滑动地址。全程没有上机。

一、WebKit 辅助进程是谁杀的（根因确定，修复未上机）

不是 jetsam，也不是宿主 kill，是内核 exec 路径上 AMFI 的启动约束。

证据链：
1. 日志形态：23 条记录（island 14、bisectA 8、acd 1）都是 launchd exec load_machfile returned 后 1 ms 内 status=9 ns=0，没有 exec process signature enter。
2. ns=0 是指纹：psignal_vfork_with_reason 带 PSIG_VFORK，goto runlocked，不调 set_thread_exit_reason，reason 被释放。kill(2) 是 ns=2，jetsam 是 ns=1。全部运行里 status=9 ns=0 没有其他进程。这也推翻了盘点里“被宿主结束”和进度记录里“被 jetsam”的推断。
3. process_signature 在 signature enter 之前能发 SIGKILL 的只有 ip_cs_error 和启动约束；MAC 拒绝有单独日志行，没出现过。4 个二进制的 CDHash 都在信任包 Cryptex 模块里。
4. AMFI 类别表（sub_FFFFFFF005AACCD0）：WebKit 是类别 4，自身约束为 (on-authorized-authapfs-volume 或 on-system-volume) 且 launch-type∈{0,1} 且 validation-category==1，属强制类。
5. 两项卷事实都假：on-system-volume 取 vfs_flags&MNT_ROOTFS（sub_FFFFFFF005AB8D9C），SystemOS 用 mount(MNT_RDONLY|MNT_UNION) 挂上，没有这个标志。on-authorized-authapfs-volume 取 VNOP_IOCTL(FSIOC_AUTH_FS 0x80084118)==0（sub_FFFFFFF005AB8CF0），APFS 处理函数 sub_FFFFFFF0066E39D0 对普通 inode 要求 role==System、MNT_ROOTFS、已密封，否则返回 EAUTH(80)。
6. 别的能跑的原因：海报扩展同为类别 4 但在根卷；Safari.SearchHelper 在 SystemOS 上但是类别 5，只要 validation-category。

受影响主程序：WebKit 4 个扩展、adattributiond（类别 1）、Data 上的 HealthFollowUpExtension（类别 4）。

修复：启动参数 sheng-no-lc=1。开关早在内核里（zzyc-owner-skip-launch-constraints.patch），之前只在设 A830_DIAG 时才加。不用重编内核。
没采用：bindfs 转发 ioctl（无效，APFS 仍回 EAUTH）；给 SystemOS 置 MNT_ROOTFS（VFS 和 APFS 多处按它分支，副作用大）。

放开后的下一道关（未验证）：
- App cryptex 没挂载，8 个任务缺程序：com.apple.Safari.History、webbookmarksd、webpushd、webinspectord、safarifetcherd、passwordbreachd、SafariBookmarksSyncAgent、AuthenticationServicesAgent。这解释了 Safari 启动时的 xpcproxy status=4e00。
- 没有网络。
- 种子规则末尾的 !com.apple.WebKit.* 让 GPU 进程没有 Metal（当时为省 GPU 槽位加的）；普通页面应不需要【推断】，若 GPU 进程崩溃循环就去掉。
- WebContent 将是第一个带 JIT 授权的进程。

二、设置页

bisectA 398 s 与设置页无关：两轮时序一致，webprivacyd 启动后恰好 5.2 s 拉 WebKit，随后出现 Safari.SearchHelper。island 里能直接看到 MobileSafari 在 326.56 s 启动；bisectA 的前一个 pid 落在日志缺口，AppStore 是 406 s 才启动的。

设置页慢的根因没有定位：日志里没有卡死时的进程栈或超时信息。线索：
- pkgD 119 s 有 iconservicesagent status=9 ns=1 code=7，与图标重画一致，归 sysfix。
- 固定 5.2 s 像超时，可能与 App cryptex 缺失的服务有关【推断】。
- 设置里内嵌网页的页面修完 WebKit 会变好【推断】。

三、HEIC

调用链：ImageIO HEIFReadPlugin → CMPhoto → VTTileDecompressionPluginClass_create → VTTileDecompressionSessionCreate → 经 Mach 连 com.apple.coremedia.videocodecd.decompressionsession（SeparateCodecProcess 和 SeparateCodecProcess_MIGDecode 都开）→ videocodecd 选解码器 → VCPHEVC。

VCPHEVC 能服务瓦片：类版本 3，class[8]=0x1c8708c38，class[9]=0x1c870ad54。

逐层核对：
- CMPhotoDecompressionSupportedForContainerAndCodec 对 hvc1 静态返回真。
- ___CMPhotoIsTileDecoderAvailable_block_invoke_2 对 HEVC 无条件置 1，总走瓦片插件。
- VT 规格默认只有 EnableHardware=true；过滤函数只在 Require 时拒绝软件解码器。
- hvc1 只有 VCPHEVC：H264H8 查不到 AppleD5500 就不注册，这份缓存没有 AVD。
- IOSurfaceAccelerator 不是硬依赖：VCPHEVC、像素传输、旋转都有软件回退（旋转是 Celeste→Metal→软件）。
- 13T 没有 iommu-present 和 /vram，VT 会加 PurpleGfxMem 属性，但 IOSurface 找不到内存区时走默认分配，无害。
- hw.cpufamily 未知只是不用压缩格式。
- ImageIO/decode_images_oop 未开，ImageIOXPCService 不参与。

模拟器实证（iOS 26.5）：VTIsHardwareDecodeSupported(hvc1)=0、没有 IOSurfaceAccelerator，J120.HEIC 解出 3416×3416，平均 RGB 126/85/80，已加载 VCPHEVC.videocodec，且为进程内解码。这一运行时比 21H461 新，只能证明软件路径本身可行。

到底哪里失败：
- 已证实的硬门控：kCGImageSourceUseHardwareAcceleration=true → AccelerationMode=2 → RequireHardwareCodec → VT 过滤掉 VCPHEVC。读这个键的只有 ChatKit、IMSharedUtilities、BlastDoor、MediaAnalysis、PhotoAnalysis、PodcastsUI、PaperKit、QuickLook。已核对 QuickLook 传 true、PVFaceCropUtils 传 false，其余未逐一核对。QuartzCore、UIKit、PhotosUI、WallpaperKit 都不用它，所以它解释不了壁纸和照片。
- 未证实的唯一单点：普通路径依赖远程 videocodecd。ImageIO 对这一步失败的报错就是“media server is NOT running”“no way to decode a HEIC”。静态核对启动链没发现问题，但所有日志窗口都没出现过 videocodecd，成败都没有证据。

设备侧现象：
- 16:25 用户说“Safari 自定义背景不显示”，背景图是 8 张 HEIC 加 1 张 PNG，这是 HEIC 在普通 App 失败的最直接线索，但那轮日志全丢。
- 17:45 “照片反而黑了”很可能与 HEIC 无关【推断】：照片网格是 PhotosUICore 的 PXGMetalRenderer（Metal），当时种子的 Metal 名单没有照片，@apps 是 22:40 才加的，现已覆盖；17:27 与 17:45 种子相同；那轮日志也全丢。
- 默认壁纸全黑已查明是 logicalScreenClass。上游 910a19d 只是一句提交说明，没有错误码，“没硬解就解不了”这个说法逆向不支持。

修复（两条文本修补，dyld_shared_cache_arm64.07）：
1. _VTShouldRunVideoDecodersInProcess，VA 0x197ae6900，偏移 0x321e900：fd7bbfa9fd030091 → 20008052c0035fd6（mov w0,#1; ret）。两种解码会话都改为进程内，绕开 videocodecd，就是模拟器验证过的路径。
2. _VTSelectAndCreateVideoDecoderInstanceInternal+0x360，VA 0x19789ef58，偏移 0x2fd6f58：f4630139 → ff630139（strb wzr,[sp,#0x58]）。“强制硬件”不再排除软件解码器。

没选其他方案的原因：偏好 IIO_use_hw_decoder=0（域 com.apple.ImageIO）只影响 ImageIO，沙箱里能否读到不确定，也解决不了 videocodecd；MG 答案对 13T 无实际效果；VT 属性没有全局入口。

修补 1 是预防性的，不保证治好。若 HEIC 仍失败，下一步要抓 ImageIO 和 CMPhoto 的统一日志（PaganiDiag 能读，需要 A830_DIAG）。

四、视频播放

不是同一堵墙：
- RequireHardwareAcceleratedVideoDecoder 的 GOT 槽全缓存只有 CMPhoto 读；MediaToolbox 读的是 Enable（6 处）。
- mediaplaybackd、mediaserverd 有 decode-in-process 授权，本来就在进程内解码，不走 videocodecd。
- H264SW、VCPHEVC、VCPMP4V、VCH263、AV1SW 都在。
- 缩放和色彩转换有软件回退。
- 显示走 YCbCr IOSurface 经 Metal 采样，桥里已实现 420v/420f，但这一环未上机验证，是视频最可能出问题的地方【推断】。

分场景：Safari 视频要先修好 WebKit，且没有网络；Apple TV 的 DRM 内容受 fairplayd 禁用影响【推断】；4K HEVC 全靠 CPU，可能掉帧。

五、建议主会话更新的记录

- 功能盘点.md 第 72、189 行：改成“启动约束，Safari 触发”，修复为 sheng-no-lc=1。
- 第 78、80、81 行：HEIC 与视频按上文更新，IOSurfaceAccelerator 不是硬依赖。
- 工具/check-phone-wallpaper-overlay.py 规则 I1 的说法待修补验证后放宽，在此之前保留 PNG 壁纸。
- 新增一行：App cryptex 未挂载，8 个任务缺程序。