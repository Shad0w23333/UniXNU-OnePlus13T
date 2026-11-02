# 相机路线报告（2026-11-01，第 1101 轮）

证据等级：【实测】= 我跑了命令或读了日志；【读码】= 读反汇编、设备树、模块符号或上游补丁；【推断】= 由前两者推出，没有上机证实。全程只读：没有改源码、种子、镜像，手机没有重启，安卓下只做了只读查询；从手机和原厂备份取出的文件只在草稿目录，本报告不含任何寄存器初始化表。

---

## 0. 结论

1. **硬件能走通，而且不需要 Spectra 那一整套。** 走 CSID + TFE/IFE-Lite 的 RDI 直写 DDR，不需要 ICP 固件、不需要 CDM、不需要 IPE/OFE。上游 Linux 的 SM8750 CAMSS 补丁（v6，2026-07，未合入）就是这么做的，用的正是本机长焦同款 S5KJN5，采到 4096x3072 SGBRG10。【读码（上游）】
2. **没发现 TrustZone 或 Gunyah 挡路的证据。** camera.ko 的 SCM 调用只落在 ICP 加载、安全相机、QoS、勘误几处，全部可以跳过或不是取流必需。这点的最终证明要等第一次真机读写。【读码 + 推断】
3. **最大的硬件工作量在“电源时钟 + PHY”，不在 ISP。** 本机 Android 用 CRM 管 TFE/CSID 时钟，但上游 camcc-sm8750 完全不用 CRM，所以直接编程 RCG 这条路是通的。【实测 + 读码】
4. **iOS 侧不需要仿苹果 ISP 协议。** FigCaptureDevice/FigCaptureStream 是进程内对象，接口很小（设备 2 个方法 + 属性，流 3 个方法 + 属性），现有 H6ISP 插件的入口正好能换成“真导出 `H6ISPCaptureDeviceCreate`”。【读码】
5. **有两条 iOS 路线，要分清：** 外接相机（CMIO）路线便宜稳，但相机 App 不认；内建假设备路线能进相机 App，但要过内建相机的整条管线，不确定性最大。相机 App 只 import 内建设备类型。【实测】
6. **建议顺序：** 先抓事实 → iOS 测试图（先外接后内建）→ 上电 + CCI 读 ID → TPG 帧进内存 → 长焦 RAW 一帧 → 软件 ISP 预览 → 拍照。测试图和 TPG 这两步把“系统侧”和“硬件侧”解耦，任何一步都有独立判据。
7. **诚实的距离：** 走完后得到的是“网络摄像头级别的相机”：能取景、扫码、拍 JPEG；画质远低于安卓；录像大概率没有（系统里只有硬件编码器）；人像、夜景、HDR、深度都没有。常态估计 2–3 个月（每天 3–6 轮上机），最乐观 6 周，某一步卡住可能更久。**这不是“接近日用”的优先项**，WiFi 和打字都应该排在前面。
8. **更正既有文档：** 21H461 没有 `cameracaptured`，相机服务在 mediaserverd 里；“`still-camera` 决定相机 App 出不出现”这条站不住（见 3.7）。

---

## 1. 取流方案总览

```
传感器 (S5KJN5 / IMX906 / IMX471)
  │ CCI I2C：上电、下寄存器序列、曝光增益
  │ MIPI CSI-2
CSIPHY v2.3.0 ──► CSID-Lite980（或 CSID980） ──► IFE-Lite980 RDI（或 mc_tfe980 RDI）
                                                   │ 总线写客户端，SID 0x1c00，cam_hf_qtb
                                                   ▼
                                            DDR 环形 RAW 缓冲
                                                   │ 内核用户客户端（AppleH6CamIn 服务）
                                                   ▼
mediaserverd 内 H6ISP.mediacapture：软件 ISP（CPU NEON）→ CVPixelBuffer(IOSurface, 420f)
                                                   │ PixelBufferHandler
                                                   ▼
                     CMCapture / AVCaptureSession → 相机 App、扫码、其它 AVFoundation 客户端
```

TPG（`qcom,cam-tpg104`，3 个）可以替换最左边两级，不接传感器就出帧。【实测 DT】

---

## 2. 硬件取流可行性

### 2.1 CAMSS 结构（本机）

| 模块 | 事实 | 等级 |
| --- | --- | --- |
| 架构 | `cam-isp` `arch-compat = "mc_tfe"`，不是老 IFE；camera.ko 里是 `tfe980_*`、`cam_ife_csid_980_*`、CPAS v980 | 【实测 DT、符号】 |
| TFE | `qcom,mc_tfe980`：0xac86000 / 0xac96000 / 0xaca6000，各 0x10000；中断 SPI 0x1b1 / 0x1b4 / 0x1c9；各有独立 GDSC（`cam_cc_tfe_0_gdsc` 等） | 【实测 DT】 |
| CSID | `qcom,csid980`：0xad27000 / 0xad2a000 / 0xad2d000，各 0x2b00；中断 0x259 / 0x25b / 0x1af | 【实测 DT】 |
| Lite | `qcom,csid-lite980` + `qcom,vfe-lite980`：窗口 0xad6c000、0xad71000；中断 0x25d / 0x25e 等；没有独立 GDSC | 【实测 DT】 |
| CSIPHY | 0xada9000 起 6 个，每个 0x2000，`qcom,csiphy-v2.3.0`；CSIPHY1 是 `qcom,csiphy-paganimain`，CSIPHY4 是 `qcom,csiphy-paganifront` | 【实测 DT】 |
| TPG | `qcom,cam-tpg104`：0xad8b000 / 0xad8c000 / 0xad8d000 | 【实测 DT】 |
| CCI | CCI0/1/2：0xac7b000 / 0xac7c000 / 0xac7d000；时钟 37.5 MHz | 【实测 DT】 |
| CPAS | top 0xac04000，camnoc nrt 0xac62000，camnoc rt 0xad90000，cesta 0xadcb000 | 【实测 DT】 |
| ICP/IPE/OFE | ICP0 0xac06000（`CAMERA_ICP`，pas-id 0x21），ICP1 0xac16000（`CAMERA_ICP_1`，pas-id 0x32）；IPE、OFE 只挂在 ICP 后面 | 【实测 DT】 |

本机传感器：

| 名字 | 类型 | 总线 | ID 读法 | 时钟/复位 | PHY | 模组 |
| --- | --- | --- | --- | --- | --- | --- |
| paganiMain | 索尼 0x0906 | CCI0 m1（gpio115/116） | R 0x0016 | MCLK1 gpio90；复位 PM8550VE gpio5 | 1（专用表） | sunny，OIS `dw9828_ois` |
| paganiTele | 三星 S5KJN5 | CCI0 m0（gpio113/114） | W 0xfcfc=0x4000，R 0x0000=0x38e5 | MCLK0 gpio89；复位 TLMM gpio178 | 5（标准表） | qtech |
| paganiFront | 0x0471/0x0615 | QUP SE9 I2C 0x20 | R 0x0016 | MCLK4 gpio93；复位 gpio7 | 4（专用表） | sunny，AON 相机 |

本机 `ro.vendor.oplus.camera.frontCamSize=16MP`，所以这台前摄是 16 MP 档；`backCamSize=50MP+50MP`。【实测】 设备树里没有 `cam-sensor1`，`paganiWide` 只是软件配置里的名字。【实测】

**选型：长焦先行。** 理由：上游同款已验证；CSIPHY5 是标准表；复位是 TLMM，不用碰 PMIC GPIO；没有 QSC 校准写入（主摄有 `enable_qsc_write_in_advance`、`qsc_data_size=3072`，前摄还有 AON 选通）。【读码 + 推断】

### 2.2 Gunyah 下寄存器能不能访问

- XNU 跑在 HLOS VM，Android 的 camera.ko 也跑在 HLOS VM。开机日志里 `cam_cpas_soc_enable_resources` 成功；`lsmod` 里 `gh_rm_drv` 的使用者有 `msm_drm`、`msm_eva`，没有 `camera`，说明相机不走 Gunyah 资源管理。所以 stage-2 上这些 MMIO 对 HLOS 是开的。【实测 + 推断】
- TLMM 保留脚只有 gpio 36–39、74，相机用的脚都不在里面。【实测 DT】
- 相机流 SID 0x1c00 走 `cam_hf_qtb`（0x17d2000，范围 0x1c00..0x1fff），非安全流。项目已有“直通 bank”写法（UFS、WiFi 的 `SmmuTakeover.h`、`PaganiWifi.cpp smmuStream()`）。【读码】
- 没上机证实的点：直通时写入是否 IO 一致（DT 标了 `dma-coherent`，但那是 Linux 的 CB 配置）。保守做法是用户态读前失效缓存。【推断】

### 2.3 要不要 ICP、CDM、SCM

camera.ko 未剥离，下面的调用点都按重定位逐个定位过。【实测】

| 调用 | 位置 | 对 RDI 取流 |
| --- | --- | --- |
| `qcom_scm_pas_auth_and_reset` | `cam_icp_v2_process_cmd` | 只为 ICP 固件，不需要 |
| `qcom_scm_set_remote_state` | `cam_icp_v2_core_control` | 只为 ICP，不需要 |
| `qcom_scm_camera_update_camnoc_qos` | `cam_update_camnoc_qos_settings` | 只是 QoS；DT 有 `enable-secure-qos-update`。上游不做 QoS 也能出帧，高码率可能溢出 |
| `qcom_scm_smmu_notify_secure_lut` | `cam_ife_notify_safe_lut_scm`（`cam_ife_mgr_start_hw/stop_hw`） | 上游没有这一步【读码（上游）+ 推断】 |
| `qcom_scm_io_readl/writel` | `cam_cpastop_scm_write`，只从 `cam_cpastop_poweron` 调 | 勘误写。SM8750 表 `cam980_cpas100_errata_wa_list` 大部分是 0，具体哪项生效我没核完 |
| `cam_csiphy_notify_secure_mode`、smcinvoke | CSIPHY 安全模式 | 只有安全相机用 |

- **ICP**：只服务 IPE/OFE（离线处理）。RDI 用不到。DT 里 `compat-hw-name` 把 ICP0 绑 IPE、ICP1 绑 OFE。【实测 DT】
- **CDM**：Android 用它在帧边界批量写寄存器。上游 CAMSS 直接 AHB 写，不用 CDM。【读码（上游）】
- **绕过 ICP 用 IFE 直写 DDR：能。** 上游 v6 封面信写明 “RDI only”，3 个 VFE 各 5 个 RDI、2 个 Lite 各 4 个 RDI；用 `media-ctl` + `yavta` 采 20 帧。【读码（上游）】 依赖的三个前置系列（x1e CSI2 PHY、x1e DTSI、Kaanapali CAMSS）都未批准合并，Krzysztof Kozlowski 7-22 为此批评过，所以这是可参考但不算稳定的代码。【读码（上游）】
- 上游记下的硬件坑：CAMNOC 需要 `qdss_debug_xo`；任何一个全功能 TFE 工作时要全部 `CAMNOC_RT_TFEx` 时钟；RT 和 NRT 的 camnoc axi 都要开才能在复位后空闲；gen4 要在 CSID 配完之后才发 REG_UPDATE 和 AUP_UPDATE。【读码（上游）】

### 2.4 时钟：CRM 不是障碍

从原厂 `vendor_boot_a.img` 解出 `camcc-sun.ko` 查重定位，Android 下这些走 CRM：【实测】

- PLL：`cam_cc_pll3/4/5`（`clk_alpha_pll_crm_lucid_evo_ops`）
- RCG（CRMC）：`cam_cc_tfe_0/1/2_clk_src`、`cam_cc_csid_clk_src`、`cam_cc_cphy_rx_clk_src`
- RCG（CRMB）：`cam_cc_camnoc_rt_axi_clk_src`
- 分支：TFE、CSID、camnoc 相关 15 个

这些**不走** CRM：`cam_cc_ife_lite_clk_src`、`cam_cc_ife_lite_csid_clk_src`、`cam_cc_cci_*`、`cam_cc_csi*phytimer_clk_src`、`cam_cc_slow_ahb`、`cam_cc_fast_ahb`、PLL0/1/2/6。【实测】

上游 `camcc-sm8750.c` 里 TFE、CSID、cphy_rx、camnoc 四个源全部用 `clk_rcg2_shared_ops`，PLL3/4/5 用 `clk_alpha_pll_taycan_elu_ops`，全文没有 CRM/CESTA。【读码（上游）】 所以**软件直接写 RCG 可行**。上游的“关键常开”是 `CAM_CC_DRV_AHB_CLK`、`CAM_CC_DRV_XO_CLK`、`CAM_CC_GDSC_CLK`、`CAM_CC_SLEEP_CLK`。【读码（上游）】

要写的：`cam_cc_titan_top_gdsc`（0xadf134c）→ GCC 相机 AHB/AXI → CPAS 一组 → CCI / CSIPHY / CSID / TFE。MCLK 在单独的 `cambistmclkcc`（0x1760000，19.2 MHz）。【实测 DT】 PLL 配置表在 `camcc-sun.ko` 的 `cam_cc_pll*_config` 里，事实可取。【实测】

**建议先走 IFE-Lite**：不需要 TFE GDSC，不需要“全部 TFE 的 RT 时钟”，核心时钟档只有 266/400/480 MHz。预览用 2048x1536 绰绰有余。全分辨率吃不吃得下要等实测。【读码 + 推断】

### 2.5 电源

全部是 RPMh VRM，项目已有 `Rpmh.h`（cmd-db 查地址 + VRM 写）和 WiFi 的 `WifiPmic`。【读码】

| 用途 | 主摄 | 长焦 | 前摄 |
| --- | --- | --- | --- |
| vio 1.8 V | ldom5 | ldom5 | ldom5 |
| vdig | ldom2 1.1 V | ldoi1 1.0 V | ldom1 1.05 V |
| vana | ldom3 2.8 V | ldom4 2.2 V | ldom3 2.8 V |
| vaf | ldom7 2.8 V | ldob14 2.8 V | — |
| 其它 | ldom6 1.8 V | — | — |

CSIPHY：1.2 V 是 `ldog3`；0.9 V 是 `ldof1`（PHY 0/1/3/5）或 `ldoi3`（PHY 2/4）。【实测 DT】

风险：**写错电压可能烧传感器**。VRM 写入必须按设备树 min/max 做白名单，照 PaganiTorch 的闸门写法。【推断】

还缺：MMCX/MXC 的 ARC 投票和相机 BCM 带宽投票。现有解析在 `Sm8750Rpmh`/`A830Votes`，但 GPU 的投票走 GMU，CPU 侧经 RSC 写 ARC/BCM 要新增。【读码】 不投 BCM 大概率也能出低码率帧，高码率可能溢出。【推断】

### 2.6 CCI 与脚

- CCI0 m0：gpio113 SDA、114 SCL；m1：gpio115、116。MCLK 复用功能 `cam_mclk`。【实测 DT】
- 协议是公开的（Linux `i2c-qcom-cci`），只取事实自己写。
- 主摄复位在 PM8550VE GPIO5（基址 0x8800），要新加 SPMI 写闸门白名单；长焦不用。【实测 DT】
- 前摄走 QUP SE9 I2C（0x884000）。设备树同一地址还有 SPI 节点，AON 相机的路由另有 `aon_cam_select_params_2_3_0_pagani_front`。放最后。【实测】

### 2.7 传感器寄存器表从哪来

| 来源 | 内容 | 状态 |
| --- | --- | --- |
| `/odm/lib64/camera/com.qti.sensormodule.pagani{Main,Tele,Front,Wide}.bin` | 上电时序、init、各分辨率模式、曝光增益公式、EEPROM、OIS、VCM 驱动数据 | 【实测】文件在，头是 `QTI Chromatix Header / Parameter Parser V5.6.1`，有 `sensorDriverData`、`cameraModuleData`、`EEPROMDriverData`、`OISDriver`、`actuatorDriver`、`PDConfigData` 段。**不是明文寄存器表**：里面是带索引和数据池的压缩结构，要写解析器。不加密 |
| `/odm/lib64/camera/com.qti.sensor.pagani*.so` | 曝光增益换算代码 | 【实测】文件在 |
| `/odm/lib64/camera/com.qti.tuned.pagani*.bin`（6.8–17 MB） | Chromatix 调参：LSC、CCM、AWB、伽马 | 【实测】文件在，格式更复杂 |
| 运行时抓取 | 开 `/sys/module/camera/parameters/debug_mdl`、`debug_type`，安卓开一次相机，dmesg 里有 `CCI%d_I2C_M%d_Q%d_LOAD_DATA_ADDR:val 0x%x:0x%x`，还有 `3phase:%d, combo mode:%d` | 【读码】字符串在 camera.ko，参数文件在，当前都是 0。**最省事**，但要写 sysfs 并启动相机，属于本轮规则外，要用户做 |
| 上游 S5KJN5 驱动（v4，2026-09，约 2800 行） | 长焦的完整寄存器表 | 【读码（上游）】GPL，只能当核对参考，**不能抄进我方 BSD 驱动** |
| IMX906、前摄 | 公开驱动 | 没搜到 |

**版权处理：** 这些表和调参是 OPLUS/高通/传感器厂的专有数据。按 `proprietary-files.txt` 的做法：用户从自己手机本地导出，构建时放进本地种子（`.dbrdisk` 不入库），驱动运行时从 Data 卷读；开源仓库只放解析器和格式说明，不放任何表。

未知项：三颗传感器的 I2C 从地址、PHY 类型、数据率。`csiphy_3ph_*`、`datarate_230_2p8Gsps_*` 这些表说明主摄、前摄很可能是 C-PHY，但我没有证实；长焦上游测试是 D-PHY 系列。【推断】 上游 v6 的 CSIPHY 只做 D-PHY，**主摄若是 C-PHY 就没有上游可参考**，这是主摄排在长焦后面的另一个原因。

---

## 3. iOS 侧接入

### 3.1 进程结构（21H461）

- **没有 `cameracaptured`**。launchd 总表里只有 mediaserverd 提供 `com.apple.coremedia.capturesession`、`capturesource`、`flashlight`、`cameraviewfinder`。相机栈在 mediaserverd 进程内的 CMCapture 里。【实测】
- 崩溃影响面：mediaserverd 没有 `_PanicOnCrash`，`KeepAlive` + `ThrottleInterval 5`。音频已在 audiomxd（日志 pid 93），所以崩了只影响相机和部分媒体服务，不是开机循环。【实测】
- `dietapplecamerad` 限 `IsDarwinOS`，`realitycamerad` 按需，都无关。【实测】
- 内存：J120 的 jetsam 表里 mediaserverd `ActiveSoftMemoryLimit = -1`，不怕限额。【实测】
- 本轮日志 mediaserverd 在跑（pid 29），启动参数里没有 `pagani-torch`，所以 `AppleH6CamIn` 没发布，插件没加载。【实测】

### 3.2 设备发现

- `+[BWFigCaptureDeviceVendor sharedCaptureDeviceVendor]`（0x1a693ee00）同时传两个创建函数：`FigCaptureGetCaptureDeviceCreateFunction()`（默认设备）和 `_CMIOExtensionFigCaptureDeviceCreate`（外接设备）。只要其一存在 vendor 就成立。CMIO 分支除了类存在没有别的判断，不看机型。【读码 0x1a693ee3c 起】
- 默认设备：`FigCaptureGetCaptureDeviceCreateFunction`（0x1a693f9a0，表 0x1e8474310）查 `AppleH16/13/10/9/6CamIn`，`dlopen /System/Library/MediaCapture/<名字>.mediacapture`，`dlsym <名字>CaptureDeviceCreate`。【读码，torch 笔记】
- 创建约定：`int Create(CFAllocatorRef, id 第二参数, FigCaptureDeviceRef *out)`（`blr x24` at 0x1a68c1938）。第二参数含义没确认（CMIO 版把它当设备 ID 往下传，H9ISP 入口处没有保存它）。【读码】
- 捕获源生成：`csu_createSourceInfoDictionariesFromAVCaptureSessionPlistForCaptureDeviceIDs`（0x1a69a7328）。默认设备走 `FigCaptureCreateSourceInfoArrayFromDeviceAndModelSpecificPlist` + `CMCapture.framework/<hw.model 去 AP>/AVCaptureSession.plist`（J120 目录存在，6951 字节）；其它设备走 `...AndGenericPlist` + `iOS/AVCaptureSession.plist`。【读码】
- 通用路径把 `DeviceType` 固定成 15，`SourceType` 固定成 1；`BWDeviceTypeIsExternalDeviceType` 判 15、16。Position 由流的 PortType 定。CMIO 流的 PortType 写死 `FrontFacingCamera`。【实测内存 + 读码】

### 3.3 FigCaptureDevice / FigCaptureStream 最小接口

都是 `CMDerivedObjectCreate(allocator, &vtable, FigCapture{Device,Stream}GetClassID(), &obj)` 造的对象。vtable = `{0, &baseClass, &derivedClass}`。【读码 + 实测内存：`kCMIOExtensionFigCaptureStreamVTable` 0x1f4bd03c0，`kH9ISPCaptureStreamVTable` 0x22b8a9b98】

| 表 | 布局 |
| --- | --- |
| CMBaseClass（版本 1） | +0x08 存储大小，+0x18 Invalidate，+0x20 Finalize，+0x30 CopyProperty，+0x38 SetProperty |
| 设备类（版本 1） | +0x08 CreateProcessingSession（H9 有），+0x10 RequestControlOfStreams，+0x18 RelinquishControlOfStreams（CMIO 有）；都是可选 |
| 流类（H9 版本 2，CMIO 版本 3） | +0x08 Start，+0x10 Stop，+0x18 AddAttachmentsToSampleBuffer，+0x20 EnqueueReactionEffect（仅 CMIO） |

属性规模：H9ISP 引用 123 个流属性、5 个设备属性（`Clock`、`FirmwareVersion`、`MemoryPoolID`、`StreamArray`、`SupportedSynchronizedStreamsGroups`）；CMCapture 一共定义 213 个流属性、13 个设备属性。【实测 nm】

CMIO 适配层（`CMIOExtensionFigCaptureStreamCopyProperty/SetProperty`）给出了能跑通 AVCaptureSession 的最小集：【读码】

- 读：`SupportedPropertiesDictionary`、`PortType`、`Manufacturer`、`ModelID`、`UniqueID`、`LocalizedName`、`SupportedFormatsArray`、`VideoOutputConfigurations`、`VideoOutputsEnabled`、`FormatIndex`、`PixelSize`、`MinimumFrameRate`、`MaximumFrameRate`、`DeferAdditionOfAttachments`
- 写：`VideoOutputConfigurations`（输出 `PrimaryScaler`、`SecondaryScaler`、`IntermediateTap`、`StillImage`，键 `CropRect`、`Width`、`Height`）、`VideoOutputHandlers`（`EventHandler`、`PixelBufferHandler`、`BlockBufferHandler`）、`VideoOutputsEnabled`、`FormatIndex`、`MinimumFrameRate`、`MaximumFrameRate`
- 帧：调 `PixelBufferHandler`。通知：`StreamStarted`、`StreamStopped`、`PixelBufferPoolReady`、`FrameReceiveTimeout` 等

`BWFigCaptureStream` 有 `getPropertyIfSupported:`，靠 `SupportedPropertiesDictionary` 跳过不支持的键。`-[BWFigVideoCaptureDevice _initWithCaptureDevice:...]`（0x1a685d314）里强制读的只有设备 `Clock` 和（外接时）流的 `Manufacturer`、`NonLocalizedName`、`LocalizedName`；`BWFigVideoCaptureStream` 初始化强制读 `SupportedFormatsArray`、`PixelSize`、`CameraViewMatrix` 等少数几个。【读码】 **所以初始化门槛不高，真正的未知在启动流和处理帧的路径上。**

### 3.4 现有 H6ISP 插件走到哪一步

- 机制：内核 `load_init_program` 里挂叠加层 `/private/var/ngpu/mediacapture` → `/System/Library/MediaCapture`，发布 `AppleH6CamIn` 和用户客户端，加 `/product/camera flash`。条件是 `pagani-torch=1 pagani-torch-ios=1`。【读码】
- 插件不导出 `H6ISPCaptureDeviceCreate`，`dlsym` 失败，vendor 仍无默认设备；构造函数替换 vendor 6 个方法，只给名叫 `FigFlashlight` 的客户占位设备。【读码】
- **从未上过机**。README 写 “not yet run on the device”；50 次运行日志里没有一行 `[torch]`。所以 “插件能在 mediaserverd 里 dlopen” 这件事本身未验证。【实测】
- 风险点沿用 torch 报告：dlopen 信任链在 mediaserverd 是第一次（机制同 audiomxd，音频插件已验证）。【读码】

### 3.5 路线 A：内建假设备（进相机 App 的唯一正路）

做法：插件真导出 `H6ISPCaptureDeviceCreate`，返回自己的设备和流；`PortType` 用 `BackFacingCamera` / `FrontFacingCamera`；`SupportedFormatsArray` 对齐 `J120/AVCaptureSession.plist` 的 `FigCaptureStreamFormats`，或用叠加层换一份精简 plist（叠加机制现成）。`ModuleInfo` 返回 J120 默认的 0x0303、0x0314，让 `CameraSetup.plist` 的调参能查到。【读码 + 推断】

优点：相机 App、控制中心相机、锁屏相机，以及其它只找内建类型的系统功能都能看到它。

未知（都要上机迭代）：

1. 内建分支的帧元数据。`_addStateAttachmentsToVideoSampleBuffer:`、AF 处理器、`FigCaptureCameraParameters` 对缺字段的容忍度不明。H9ISP 附带 `DriverMetadataDictionary`、`MetadataDictionary` 等。
2. 拍照走 `StillImageCaptureNow` + `StillImage` 输出。
3. 预览尺寸是否强制要求缩放器。没有 `AppleM2ScalerCSCDriver`，`IOSurfaceAccelerator` 缺失（功能盘点已记），但 CMPhoto 有 `JPEGSoftwareEncode`、`JPEGSoftwareFallback`、`PreferSoftwareEncode` 键。【实测字符串】
4. 预览图层用 420f：Metal 前端有 `newTextureWithDescriptor:iosurface:plane:`，但视频类 YUV 显示从没在这台手机上验证过。【读码】

### 3.6 路线 B：CMIO 外接相机

结论：**能走通，但要绕过 launchd，且只有部分 App 认。**

- CMCapture 侧完整：`CMIOExtensionDiscoverySession`、`CMIOExtensionFigCaptureDevice/Stream`、`_updateCMIOExtensionDevices`（0x1a69af35c，只跳过“只有 sink 流”的设备）。【读码】
- iPadOS 的 UVC 助手**不在这个根卷**：jetsam 表提到 `com.apple.cmio.uvcassistantextension`、`samplecaptureextension`，但 launchd 总表、`AppExtensions`、共享缓存字符串都没有实物。J120 是 Lightning 机型。苹果文档也说 UVC 外接相机只支持 USB-C iPad。【实测 + 网页】
- 发现机制：mediaserverd 是事件发布者 `com.apple.cmio.registerassistantservice.system-extensions.matching`，订阅者描述里带 `CMIOExtensionBundleIdentifier`，发事件后对方回 `endpoint` + `mach-o-uuid`。扩展端用 `xpc_set_event_stream_handler`，主 bundle 要有 `CMIOExtension` 字典。【读码 0x21ccd8308、0x21cd0b65c、0x21cd0b0f4、0x21ccfcf7c】
- **障碍**：launchd 只认签名总表 `/System/Library/xpc/launchd.plist`（PaganiDiag README 已证实叠加无效），表里没有订阅这个事件的任务。
- **绕法**：插件在 mediaserverd 里直接调 `+[CMIOExtensionSessionProvider sessionProviderWithEndpoint:delegate:]`（0x21ccf661c），代理设为 `[CMIOExtensionDiscoverySession sharedInstance]`，再 `postDeviceChangeNotification`。扩展端可以同进程（`CMIOExtensionProvider` 在 macOS 是公开 API，有苹果示例，iOS 的 CoreMediaIO 里类和方法都在），也可以放到独立守护进程换崩溃隔离（要解决 endpoint 传递）。【读码 + 推断，未试】
- 这样出来的是 `AVCaptureDeviceTypeExternal`。

谁认：【实测 `llvm-nm -u`】

| App | 外接类型 | 备注 |
| --- | --- | --- |
| 相机（CameraUI） | **不认** | 只 import 内建类型 |
| 代码扫描器 BarcodeScanner | 认 | iPhone/iPad 都支持，**最好的验证目标** |
| FaceTime | 认 | 要登录才看得到自视图，价值低【推断】 |
| Photo Booth | 认（`ExternalUnknown`） | iPad 专属，种子没装，当前 iPhone 身份用不了 |
| 放大器、测距 | 未见 | — |

`BWDeviceIsiPad` 看 `DeviceClassNumber == 3`，本机身份是 iPhone 类，外接相机分支在 iPhone 身份下是否完全正常未验证。FaceTime 里的变量名就叫 `cachedIsUsingIPadExternalCamera`。【读码】

**把 CMIO 伪装成内建给相机 App 用**：可以试，比如在 mediaserverd 里替换 `-[FigCaptureSourceBackings sourceInfoDictionaries]` 改 DeviceType 和 Position。但 `isExternalVariant` 由同一个 DeviceType 算，一改后端就走内建分支，约 30 处判断同时翻转（我数了调用点）。所以它和路线 A 面对的是同一批未知，只能当便宜实验，不是捷径。【读码 + 推断】

### 3.7 相机 App 与 `still-camera`

- `Camera.app/Info.plist` 的 `UIRequiredDeviceCapabilities` 只有 `arm64`。`SBAppTags = ["still-camera"]`，这个标签看上去走 `SBApplicationRestrictionController` 的 visible/hidden tags（该类响应运营商配置和限制变化），不是 MG。【实测 plist；标签来源是读码 + 推断】
- SpringBoard 里字面量 `still-camera` 只有一处：`-[SBMainDisplayPolicyAggregator _allowsCapabilityLockScreenCameraSupportedWithExplanation:]`（0x1cf9fe7d8），管的是锁屏相机。【实测 xref】
- 所以**功能盘点“`still-camera` 决定相机 App 出现与否”没有依据**。图标很可能一直在。但 “没有相机” 那条反馈写于 “预装应用一个都没有” 的同一轮，现在主屏有没有图标我不知道，要问用户。现存运行日志里找不到 Camera 进程启动记录（日志有丢失，不能证明从没启动过）。【实测】
- 无设备时的行为：CameraUI 有 `Failed to get device from -[AVCaptureDevice defaultDeviceWithDeviceType:...` 和回退日志，我推断是打开后黑屏，不崩。没上机。【读码 + 推断】
- MG 字面量 `still-camera` 还在 CoreFoundation、UIKitCore、GraphicsServices、VisionKitCore、DocumentCamera、PhotosUICore、PhotosUIPrivate、PhotoLibraryServices、AVConference 里。影响是“相机入口按钮出现”。【实测】
- **崩溃循环的担心**：launchd 总表里没有按相机能力启动的任务；`mobilerepaird` 的事件只匹配 `AppleH13/H16CamIn` 且 `CmClValidationStatus=Invalid`，H6 不触发。真实风险只有一个：`H6ISPCaptureDeviceCreate` 崩溃时 mediaserverd 按 5 秒节流反复重启（SpringBoard 预热相机、任何客户端枚举都可能触发）。不会开机循环。【实测 + 推断】
- **规矩**：`still-camera`、`/arm-io/isp` 节点只在插件可见且驱动就绪时才加，和 torch 的 `pgt_ios_final` 同一闸门；默认关，`native_gestalt_cache_drop` 已能清 MG 缓存。

### 3.8 下游依赖

| 功能 | 依赖 | 判断 |
| --- | --- | --- |
| 预览 | IOSurface、Metal YUV 平面 | 待验证 |
| 拍 JPEG | CMPhoto 软件编码 | 键存在，可行【实测字符串】 |
| 拍 HEIC | HEVC 编码器 | 系统只列出 `ave.hevc`、`H9.videoencoder` 等硬件项，不可用；要设“兼容性最好”【实测字符串 + 推断】 |
| 录像 | H.264/HEVC 编码 | VideoToolbox 只有 `ave.*`、`h264.gva`、测试编码器；**没有可用的软件编码器**，录像大概率不行，要自带编码器才有戏【实测 + 推断】 |
| 扫码 | Vision 软件路径 | 可行【推断】 |

---

## 4. 软件 ISP 量级

本机 M4 Max 跑了一个基准（草稿目录，C 自动向量化，未手写 NEON）：RAW10 → 白平衡 + CCM + 伽马 LUT → NV12。【实测】

| 处理 | 单线程 | 4 线程 |
| --- | --- | --- |
| 2x2 合并到 2048x1536 | 9.8 ms | 3.2 ms |
| 全分辨率 4096x3072 双线性去马赛克 | 20.6 ms | 6.1 ms |

换算到手机：Oryon 大约是 M4 大核的 0.7–0.8，再算上 XNU 下调频不一定跑满，取 1.5–2 倍。【推断】

- 2048x1536 预览：15–20 ms 单线程，4 线程约 5 ms，30 fps 占 15–40% 个核。**CPU 够用**。
- 全分辨率单张：30–40 ms，拍照无压力。更好的去马赛克（5x5）约 3 倍。
- 降噪、LSC、锐化、3A 统计再加 1–2 倍，仍可做到 1080p30。
- 码率：4096x3072 RAW10 30 fps 约 472 MB/s，CSI 和 DDR 不成问题；缓冲 4 帧约 63 MiB。内存够（本轮可用内存 1.5 GiB）。【实测 + 计算】

GPU：Metal 前端有 `dispatchThreads`，走 metal2vulkan → Turnip，但这条路的 CPU 开销和延迟没测过，第一版不用。先 CPU，后面看功耗再说。【读码 + 推断】

功耗与发热是未知数：CPU ISP 比硬件 ISP 费电得多，传感器本身也要 200–500 mW 量级。【推断】

3A 全要自己写：

- AE：看亮度直方图，经 CCI 写曝光增益（公式在 sensormodule）。
- AWB：灰世界起步。
- AF：长焦 VCM 对比度爬山；主摄有 `dw9828_ois`，OIS 不初始化时镜头可能晃，要专门确认。
- 校准：EEPROM 有模组 LSC 和 AWB（主摄 `eeprom_slave_addr=0xa0`）。

Chromatix 调参解不开时，色彩只能靠手调，质量和安卓差很远。

---

## 5. 分阶段计划

工作量按本项目节奏估（代码量 + 上机轮次），代码由工作流写，瓶颈是上机。全部默认关，新参数 `pagani-cam=N` 分段（13 字节，启动参数现余量要先核）。遵守“修完再测”：每段改完一起打包再上机。

### 阶段 0：事实采集（0.5 天，用户操作 1 次）

- 用户在安卓下：开调试参数，开一次相机切主摄、长焦、前摄，导出 dmesg。
- 本地导出 `/odm/lib64/camera/com.qti.{sensor,sensormodule,tuned}.pagani*`。
- **判据**：拿到三颗的 I2C 地址、PHY 类型与车道、数据率、完整上电时序、init 序列（本地文件，不入库）。
- **风险**：低。第 0 步决定主摄后面是否要做 C-PHY。

### 阶段 1：相机 App 有预览（测试图，不碰相机硬件）

**1a 外接路线**：插件 + 同进程 `CMIOExtensionProvider` 出带二维码的滚动测试图。约 600 行，3–6 轮。

- 判据：代码扫描器打开有画面并识别出二维码；mediaserverd 5 分钟不崩。
- 风险：中。同时验证了 dlopen 信任链、IOSurface、预览合成这三件事，后面都要用。

**1b 内建路线**：导出 `H6ISPCaptureDeviceCreate`，叠加 plist，加 `still-camera` 和 `/arm-io/isp`。增量 800–1500 行，8–20 轮。

- 判据：相机 App 打开有预览，切前后摄不崩，AVCaptureDevice 列出 BuiltInWideAngle。
- 风险：**高**。这是全程最不确定的一段。若 20 轮内进不去，退到 B 计划（见第 6 节自写小 App）。
- 省轮次：给 PaganiDiag 加相机探针（枚举设备、起会话、数帧、算均值，写 U 行），开机自动跑，不用人点。

### 阶段 2：上电 + CCI 读传感器 ID

新驱动 PaganiCamera：GDSC、camcc、cambistmclkcc、VRM 白名单、TLMM、CCI。约 1500–2500 行，含主机测试和变异测试。3–6 轮。

- 判据：`[cam] cci0 m0 id 0x38e5`；断电后 VRM 回到关；无 NoC 报错。
- 风险：中。未上电访问寄存器会挂总线（`pagani-dpu-probe=3` 有前例），靠 `pagani-reset-after` 恢复。顺序错了可能卡死，不至于坏硬件。**电压白名单写错才有硬件风险。**

### 阶段 3：TPG → RDI → DDR

CSID-Lite + IFE-Lite + SMMU 直通 + BCM 投票 + 用户客户端（dequeue/queue）。约 1500 行，5–12 轮。

- 判据：缓冲内容与 TPG 图样校验和一致，帧号递增，30 fps 无 overflow；能在阶段 1 的假设备里直接显示 TPG 彩条。
- 风险：中高。CAMNOC 时钟组合、REG_UPDATE 时机、一致性都可能卡。优势是不依赖传感器。

### 阶段 4：传感器 RAW 一帧

CSIPHY、序列加载器、stream-on。约 1000 行 + sensormodule 解析器（或用阶段 0 抓的序列）。5–15 轮。

- 判据：RAW 帧写到 Data 卷或经用户客户端导出，拜耳图案对；遮挡镜头均值掉、照亮均值升。
- 风险：高。CSIPHY settle count、车道映射、数据率对不上时现象只是“没帧”，难排查。上游长焦数据可对照。

### 阶段 5：连续预览

软件 ISP、AE/AWB、接到阶段 1。约 2000 行，5–10 轮。

- 判据：相机 App（或代码扫描器）1080p 级实时取景，5 分钟不断流，扫得出真实二维码；CPU 温度不触发限频。
- 风险：中。功耗发热未知。

### 阶段 6：可选增强（各自独立）

| 项 | 工作 | 判断 |
| --- | --- | --- |
| 拍照 JPEG 落相册 | `StillImageCaptureNow` 路径 | 依赖 1b，中风险 |
| 对焦 | 长焦 VCM、主摄 VCM + OIS | 中，主摄 OIS 高 |
| 主摄 | 复位 SPMI、QSC、可能的 C-PHY | 高 |
| 前摄 | QUP I2C、AON 路由 | 高 |
| 闪光灯配合 | 接 PaganiTorch | 低 |
| 录像 | 自带软件 H.264 编码器接 VideoToolbox | 很高，未调研 |

**总时长**：阶段 0–5 常态 2–3 个月，最乐观 6 周。假定每天 3–6 轮上机且用户在场。阶段 1b 和阶段 4 是两个可能无限拖延的点。

---

## 6. 和日用的距离

走完阶段 5：

- 有：取景、扫码、基础拍照（1b 成功的话）。
- 画质：相当于没调好的网络摄像头。没有 HDR、夜景、人像、连拍、深度、电影效果。色彩和 LSC 靠手调。
- 没有：录像（编码器）、前后摄同时、超分、Live Photo。
- 功耗：明显高于安卓。

**B 计划**：相机 App 进不去时，自写一个系统签名的小相机 App，直接连内核用户客户端，软件 ISP 显示，ImageIO 写 JPEG，存相册。阶段 4 完成后约 2–3 天。代价是其它 App 调不到相机，但因为 App Store 第三方应用本来就装不上，**实际少掉的只有系统 App 里的入口**。这条路线的下限很确定。

**优先级建议**：相机排在 WiFi、打字、卡顿之后。理由是用户目标是“接近日用”，相机投入最大、到手的功能最弱。如果只想要“能扫码”，路线 B + 阶段 0–5 就够。

---

## 7. 既有文档需要更正

1. `功能盘点.md` 第 160 行、`inventory/ios.md` 第 174 行：`still-camera` 不决定相机 App 显隐（3.7）。
2. 任务描述里的 `cameracaptured`：21H461 不存在（3.1）。
3. `inventory/ios.md` 6.14 的“找到 iPadOS 17 的外接相机提供者并伪装”：这个根卷没有提供者，要自己当提供者并绕过 launchd（3.6）。
4. `hardware.md` 2.7 CSID/IFE 表：真实寄存器窗口以 2.1 为准。

---

## 8. 来源

上游（网页，只取事实）：

- [SM8750 CAMSS v6 封面信](https://lkml.iu.edu/2607.2/10269.html)
- [v6 回复：依赖未批准](https://lkml.iu.edu/2607.2/12149.html)
- [VFE 980 补丁](https://lkml.iu.edu/2607.2/10274.html)
- [VFE gen4 补丁 v11](https://lkml.iu.edu/2601.1/05694.html)
- [camcc-sm8750 系列封面信](https://lkml.iu.edu/2512.0/02296.html)
- [camcc-sm8750.c（主线）](https://raw.githubusercontent.com/torvalds/linux/master/drivers/clk/qcom/camcc-sm8750.c)
- [S5KJN5 驱动 v2](https://lkml.iu.edu/2607.3/00545.html)
- [AVCaptureDevice.DeviceType.external 文档](https://developer.apple.com/documentation/avfoundation/avcapturedevice/devicetype-swift.struct/external.md)

本地：

- 设备树 `build-userspace/a830-inputs/dt/running.dts`（26761–28900 行）
- 根卷 `build-userspace/a830-inputs/osmnt-21H461`、共享缓存 `systemos-21H461`
- 运行日志 `工具/gpu-test-loop/runs/20261101-110201-stage15-smoke-1`
- 插件 `UniXNU-Pagani-Drivers/drivers/PaganiTorch`、`build-userspace/a830-inputs/torch`
- 原厂 `一加13T原厂备份/分区镜像/vendor_boot_a.img`（只解到草稿目录）
