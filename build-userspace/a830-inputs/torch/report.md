【说明】这是 report.md 的完整内容，文件本身不存在（见 summary）。

一、硬件事实核对
- 寄存器逐项对照 Linux leds-qcom-flash.c（GPL，只取事实）：TYPE 0x18、SUBTYPE 0x07；STATUS1 0x06、STATUS2 0x07（位 6:0）、STATUS3 0x09、定时器 0x3e、目标电流 0x42（位 6:0）、模块使能 0x46 位 7、分辨率 0x49、选通 0x4a（位 6:0）、通道使能 0x4e；电流公式；故障位。TorchRegs.h 没有错。
- 设备树：SPMI 地址（DT 11197）、flash_led@ee00（11361）、camera-flash 的 torch-source 只有 torch_0/1（34747），所以通道掩码 0x3；led_switch_2 的 0x0f 不代表四路都接灯。
- 两点注意：DT 兼容串是下游 qcom,qti-pm8350c-flash-led，寄存器布局按上游 4 路模块取，SUBTYPE 不对时驱动拒绝。DT 的 qcom,ires-ua 是 12500，我们用上游 torch 的 5 mA 步进，公式跟着步进走。

二、SPMI 写闸门核对
HapticsSpmi.c 的 sha256 仍是 45e83a18…，复制后没有变过。按前缀替换后逐行比对，差异只有：白名单换成闪光灯模块、新增通道互锁、通道页只映射一次、急停写两下（CHAN_EN=0、EN_CTL=0）、写成功后记录互锁。发现、仲裁器读写、归属重读、失败关总线的代码相同。

三、接手后确认并修复的问题
1. 上限写错地方：核心只限到 500（设备树值），200 只靠板级头文件。现在 PGT_TORCH_POLICY_MA=200 在核心强制，闸门拒绝 200 mA 以上的编码。
2. 关灯没有回读：写确认了灯没灭时驱动会以为已关。现在回读，不符按故障处理（off-verify）。
3. 急停后总线没关，理论上还能写。现在急停即关总线。
4. EN_CTL 保留位：闸门只收 0x00/0x80，核心按读回值改写，保留位非零要到第一次点灯才失败。现在初始化时拒绝，干跑判定行也显示 en-ctl-reserved-bits。
5. 删掉 pgt_core_set 里的无用变量，改为 off 行记录点亮时长 on_ms。

四、内核接口
- pagani_torch_set(level)：0 关，1..1000；等驱动线程最多 200 ms，返回真实结果。
- kern.pagani_torch：只有 pagani-torch=1 时才存在（运行时注册；kern 是可扩展节点，注册时已过 lockdown，都读 XNU 源码确认过），读当前 level，root 可写。
- 5 分钟自动关，改 level 不续期；故障、总线失败、超时、拒绝、回读不符一律灭灯并保持关到重启；开灯后立即查状态，点亮期间每 100 ms 轮询；开机不等待。

五、iOS 入口结论（21H461 静态逆向）
链路：
- FlashlightModule 的 UIRequiredDeviceCapabilities 是 camera-flash、arm64；默认模块顺序按 camera-flash 放入（0x200d90950）。
- +[AVFlashlight hasFlashlight] 就是 MGGetBoolAnswer("camera-flash")（0x1a33cd370）；MG 只看 IODeviceTree:/product/camera 有没有 flash 属性（0x1a0219fac、0x1a020fa68）。
- FigStartCaptureServers 启动 FigFlashlightServerStart，不依赖相机（0x1dfea3168；StartRecorderServer 缺省为真，0x197d9f288）。
- flashlight_setupDevice（0x1a69e4ecc）向 BWFigCaptureDeviceVendor 注册客户、取设备、取流；设备只被保存、比较、释放，流上只用 supportedProperties 和 setProperty:value:（TorchLevel）。
- 今天没有灯，是因为 FigCaptureGetCaptureDeviceCreateFunction（0x1a693f9a0，表 0x1e8474310）找不到 AppleH16/13/10/9/6CamIn，厂商类没有设备。

突破口：那张表的逻辑是某类 IOService 存在就 dlopen /System/Library/MediaCapture/<名字>.mediacapture。根卷这个目录是空的，dyld 缓存里只有 H9ISP，所以属主叠加层可以提供文件。选 H6：表中最后一项，固定 kernelcache 里有 H9、H10、H13 的类而没有 H6，不撞类名。launchd 只有 mobilerepaird 提到 H13/H16。全 dyld 缓存搜索，H6 还出现在 libMobileGestalt 和 libZhuGeArmory，它们只读相机模块序列号，属性不存在时答案为空（0x1a022bd24），无害。

实现：
1. 内核在 launchd 前发布 AppleH6CamIn 和用户客户端（NativeSandboxGrantProcess=mediaserverd，只允许 mediaserverd 打开，进程退出时灭灯），再加 /product/camera flash。只有插件可见且驱动在跑才加，不会出现点了没反应的按钮。
2. 插件不导出 H6ISPCaptureDeviceCreate：dlsym 失败，厂商类状态与今天相同，没有相机出现在任何地方。这是和伪造相机设备方案的关键区别，后者会让 FigCaptureSource 按 J120 的 AVCaptureSession.plist 发布假相机。
3. 插件构造函数替换厂商类 6 个方法的进程内实现，全有或全无；其它客户全部透传，只有名为 FigFlashlight 的客户拿到占位设备和把 TorchLevel 送进用户客户端的流。
4. 不加 still-camera、camera-rear 一类属性，相机 App 仍不出现。

已知限制：5 分钟自动关不回报给 iOS，按钮继续显示开着，点一下才同步。

六、要上机才知道的
- mediaserverd 是否在跑：种子没有停用它，但现存日志只留开机 56 s 以后，查不到启动记录。
- dlopen 信任链：机制与 audiomxd 加载音频插件相同，但在 mediaserverd 上是第一次。
- flashlight_setupDevice 开头经 MXSystemController 问 Wombat 状态，依赖 audiomxd 响应；这一问失败整条路就失败，日志只表现为服务被打开过但没有 by=ios。

七、留给主线的文档修改
功能盘点.md 第 17、149 行与第 6 节“系统按钮 C–D”，inventory/ios.md 6.15 与“不加 /product/camera flash”都已过时：现在是带插件时才加，系统按钮可改评为 B（静态已通，待上机）。