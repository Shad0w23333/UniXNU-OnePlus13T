# 一加 13T（PKX110 / SM8750）传感器子系统（SSC / Qualcomm Sensing Hub）事实整理

- 键：`ssc-facts`；日期：2026-10-31；状态：**离线事实**（只用了本机文件、原厂备份与公开资料，没有碰手机，结论都要在明早上机核对，见附录 B）。
- 规则：GPL/LGPL 来源（Linux、libssc、libqmi、hexagonrpc）只取事实，不抄代码；高通官方 `qualcomm/sensinghub`（BSD-3-Clause）是字段号与消息号的主依据；附录 A 的测试向量由本次自写的编码器生成。

---

## 0. 结论速览

1. **所有运动/环境传感器都在 ADSP 的 sensor PD 里**（`msm/adsp/sensor_pd`）：加速度+陀螺（IMU）、磁力计、屏下环境光（RGB/色温，带高频 PWM 补偿）、接近，全部由 ADSP 上的 SSC（新名 QSH，Qualcomm Sensing Hub）驱动；总线（LPASS 岛内 QUP 的 I2C/I3C）、中断、供电都归 ADSP，AP 设备树里**没有**这些芯片的任何节点。AP 侧与"传感器"沾边的只有：Plus 键（设备树叫 `gpio-hall-sensor`，GPIO97，PaganiInput 已做成响铃/静音开关）、红外发射（SPI）、屏下光学指纹。气压计很可能没有。
2. **AP↔SSC 通道**：QMI 服务 `SNS_CLIENT_SVC` = **400（0x190）**，IDL 1.7，走 ADSP 的 `IPCRTR`（QRTR v1 over GLINK）。消息 0x20（请求/响应）、0x21（报告指示，载荷 ≤1000 B）、0x22（巨型报告指示，载荷 ≤10240 B）、0x24（版本）、0x1E（支持的消息）。载荷是 proto2 protobuf；全部字段号已与官方 proto 逐一核对（第 3 节、附录 A）。
3. **"实例 74"的更正**：74（0x4A）是 ADSP 各 PD 在 servreg 体系里的 QMI 实例号（定位器域表、servreg 通知服务 66 用），**不是** SNS 服务的实例号。SNS 服务的实例号由固件在 NEW_SERVER 里给出，安卓 HAL 取"第一个实例"；我们只按服务号 400 匹配即可。
4. **最重要的更正：sensor PD 读注册表/配置不走 QRTR 上的 TFTP，而是走 FastRPC 反向通道**。安卓上由 `/vendor/bin/sensors.qti sensorspd adsp`（QSH 版；旧名 `/vendor/bin/sscrpcd sensorspd`）通过 FastRPC 附着到 sensors 静态 PD，向 DSP 提供 `apps_std` 文件接口，DSP 借此读取 `/vendor/etc/sensors/sns_reg_config`、`/vendor/etc/sensors/config/*.json`、`/mnt/vendor/persist/sensors/registry/...`、`/sys/devices/soc0/*` 和动态库。**没有它，物理传感器驱动拿不到总线地址/中断脚/校准，SUID 查询就查不到 accel 等**。TFTP 只在 shikra 一类平台（soc_id 756/758/759）上被 QSH 用于传感器。→ XNU 需要一个新组件"FastRPC 反向文件服务"（GLINK 通道 `fastrpcglink-apps-dsp`，外加 apps_smmu 上 sensors PD 上下文库 SID 0x1004/0x1044 的 DMA 映射）。这是传感器能否工作的头号前置条件。【评审更正】已写成 `PaganiSensors` 的 `SensorsRpc.c`（`pagani-sensors` 的 +4 位），SMMU 用现成的直通规划器，不需要配页表；见 `build-report.md`。
5. **OPLUS 的额外依赖**：SMEM 全局分区条目 **130**（9016 B，OPLUS sensor devinfo；13T 只填了 `mag_fusion`）与 **128**（3488 B，SSC→AP 反馈）；屏下光感需要 AP 送来亮度/熄亮屏/DC/HBM/截屏亮度信息（内核 `/dev/ssc_interactive` 等，由用户态转发给 SSC，具体 QMI 消息待明早抓取）；OPLUS 自有 31 个虚拟 + 18 个物理传感器名（第 7 节）。

---

## 1. 13T 有哪些传感器、在哪里

| 功能 | 13T 实体 | 位置 | 证据 | 芯片型号 |
| --- | --- | --- | --- | --- |
| 加速度 + 陀螺（IMU） | 有 | SSC（ADSP sensor PD） | 公开规格；OPLUS 反馈模块物理名 `accel`/`gyro`；AP 设备树无 IMU 节点 | 未知（明早 `dumpsys sensorservice`） |
| 磁力计（罗盘） | 有 | SSC | 公开规格列 compass；设备树 `oplus_sensor { mag_fusion { sensor-type = <6>; is-virtual-sensor; fusion-type = <0>; fold-feature = <0>; drop_zero = <1>; } }`（OPLUS 磁融合虚拟传感器的参数） | 未知 |
| 环境光（屏下，RGB/色温） | 有 | SSC | `ro.display.underscreen.lightsensor.support=1`；`ro.lcd.display.screen.underlightsensor.region=716,89,788,150`（1216×2640 面板像素，顶部偏右，前摄右侧）；`ro.display.underscreen.lightsensor.screenshot.period=50`；`ro.vendor.oplus.sensor.high_pwm_rgb=true`；`ro.oplus.fusionlight=true`；`ro.vendor.oplus.sensor.brl_receive=true`；`ro.vendor.display.sensortype=2`；设备树 `ssc_interactive` 节点 | 未知 |
| 接近 | 有 | SSC | 公开规格；`persist.sys.sensor.disable.fusion_phone_prox=false`（OPLUS `phone_prox` 融合接近，通话用） | 未知 |
| 气压计 | 很可能没有 | — | 公开规格不列；属性里没有 pressure；OPLUS devinfo 模块里的气压校准 proc 是各机型通用代码 | 明早确认 |
| SSC 霍尔 | 没有 | — | 设备树无；`gpio-hall-sensor` 实为 Plus 键：`gpio-keys`，`gpios = <&tlmm 97 ACTIVE_LOW>`，`linux,code = 0x2df`，`gpio-key,wakeup`，PaganiInput 已接管 | — |
| 磁吸保护壳霍尔 | 没有 | — | vendor_boot 里有 `oplus_magcvr_ak09973/mkh100a/mxm1120.ko`、`oplus_magnetic_cover.ko`，但运行中设备树无 `magcvr` 节点（多机型共用模块） | — |
| 红外发射 | 有（非传感器） | AP SPI | `oplus,kookong_ir_spi`、`oplus,kookong_ir_core` | — |
| 屏下光学指纹 | 有（非本题） | AP | 触摸 DT `fingerprint_underscreen_support` | — |
| SAR、后置光/闪烁/光谱 | 不确定 | SSC | 只出现在 OPLUS 通用名表里（`sar`/`sars`/`ambient_light_rear`/`flicker_rear`/`spectral_rear`） | 明早 `/proc/sns_debug/sensor_list` 解码 |

平台标识（注册表按它挑 JSON，FastRPC 文件服务要原样提供）：设备树根 `model = "Qualcomm Technologies, Inc. Sun MTP,pagani"`，`compatible = "qcom,sun-mtp", "qcom,sun", "qcom,sunp-mtp", ...`，`qcom,msm-id` 含 0x26a（618，sun）与 0x27f（639，sunp），`qcom,board-id = <0x40008 0>`（硬件平台 8 = MTP）。高通参考 JSON 的 `config` 段正是按 `hw_platform`（MTP/Surf/RCM/QRD/HDK）和 `soc_id`（"618"、"639"）筛选的。

---

## 2. 传输层：QMI over QRTR over GLINK

### 2.1 拓扑与设备树事实

- AP（我们的 QRTR 节点号 1，见 `AdspQrtr.c`）↔ GLINK 通道 `IPCRTR`（lpass 边，remote-pid 2）↔ ADSP 的 QRTR 节点（常见为 5，以对方 HELLO 为准）↔ sensor PD 里的 SNS 服务端口（NEW_SERVER 给出）。
- 运行中设备树 `remoteproc-adsp/glink-edge/qcom,adsp_qrtr`：`qcom,glink-channels = "IPCRTR"`、`qcom,net-id = <2>`、`qcom,intents = <0x800 5 0x2000 3 0x4400 2>`（安卓为 ADSP 预备 5×2 KiB、3×8 KiB、2×17 KiB 接收 intent）、`qcom,no-wake-svc = <0x190>`（安卓的 qrtr 驱动对服务 400 的包不持唤醒锁——旁证 SNS 服务就是 400）。
- PaganiAdsp 当前 IPCRTR：8×2 KiB 接收 intent、单包发送上限 512 B、DSP 请求更大 intent 时拒绝（README"Limits"）。

### 2.2 QRTR 与 QMI 帧（已在 QcomQrtr.c 实现，此处只列与 SSC 相关的事实）

- QRTR v1 头 32 字节（全部小端 u32）：`version=1, type, src_node, src_port, confirm_rx, size, dst_node, dst_port`，载荷按 4 字节补齐（`size` 填未补齐长度）。type：DATA 1、HELLO 2、BYE 3、NEW_SERVER 4、DEL_SERVER 5、DEL_CLIENT 6、RESUME_TX 7。NEW_SERVER/DEL_SERVER 控制包四个字：`service, instance(= 版本 | 实例<<8), node, port`。收到 `confirm_rx=1` 的数据包必须回 RESUME_TX（SSC 指示流量大，必须及时回）。
- QMI 头 7 字节：`u8 type(0 请求 / 2 响应 / 4 指示), u16 txn, u16 msg_id, u16 len`，随后 TLV：`u8 type, u16 len, value`。

### 2.3 SNS_CLIENT_SVC 消息表（官方 IDL `sns_client_api_v01`，服务 400，IDL 主版本 1、次版本 7）

| msg_id | 方向 | TLV | 说明 |
| --- | --- | --- | --- |
| 0x001E | 请求/响应 | 标准 | QMI_SNS_CLIENT_GET_SUPPORTED_MSGS |
| 0x0020 | 请求 SNS_CLIENT_REQ | 0x01 必选：`u16 count + count 字节`（count ≤ 1000，protobuf 编码的 `sns_client_request_msg`）；0x10 可选：`u8 use_jumbo_report` | TLV 区最大 1009 B |
| 0x0020 | 响应 SNS_CLIENT_RESP | 0x02 必选：`u16 result, u16 error`（0/0 成功）；0x10 可选：`u64 client_id`；0x11 可选：`u32 result`（0 成功，按 `sns_std_error` 理解） | 最大 25 B；**每个请求都会立刻回一个响应**，只表示"已收到"，数据只在指示里来 |
| 0x0021 | 指示 SNS_CLIENT_REPORT_IND | 0x01：`u64 client_id`；0x02：`u16 count + ≤1000 字节`（`sns_client_event_msg`） | 最大 1016 B |
| 0x0022 | 指示 SNS_CLIENT_JUMBO_REPORT_IND | 同上，载荷 ≤ 10240 | 最大 10256 B |
| 0x0024 | 请求/响应 SNS_CLIENT_VERSION | 0x10 可选：`u16 count + 字节`（`sns_client_version_request_msg` / `_response_msg`） | 可选 |

- `client_id` 标识"一条客户端连接"（即我们的一个 QRTR 端口），由服务端生成，该连接上所有响应与指示里都相同。
- `use_jumbo_report`：缺省/假 → 只用 0x21；**单个事件超过 1000 B 会被直接丢弃**，批量事件会被拆开。真 → 0x21、0x22 都可能出现，载荷只保证 < 10240。官方 QSH 会话库（`qmiSession.cpp`）每个请求都置真；libssc 也用巨型报告。
- 线上大小：0x21 最大 32+7+1016 = **1055 B**（装得进 2 KiB intent）；0x22 最大 32+7+10256 = **10295 B**（要 ≥10.3 KiB 的 intent，安卓给了 2×17 KiB）。按 PaganiAdsp 现在的 IPCRTR（只有 2 KiB intent、拒绝更大请求），**第一阶段不要置 use_jumbo_report**；若发现属性事件被丢（属性多的传感器接近 1000 B），再把 IPCRTR intent 改成安卓那组并打开巨型报告。注意：ADSP 若因 intent 不够而卡在发送上，整个 IPCRTR（含 PMIC/定位器等其他服务）都可能被拖住。
- 服务出现时机：sensor PD 起来、QMI 服务注册后 ADSP 的名字服务才广播 NEW_SERVER(400)。官方会话库首次等 4×2 s、之后 2×0.5 s 重试；sensor PD 在 ADSP 启动后可能还要数秒（等注册表）。DEL_SERVER(400) 或 BYE 表示 PD 重启（PDR/SSR），要全部重来。
- 断开：向对端控制端口发 DEL_CLIENT(我们的 node, port)，SSC 会释放该连接上的全部请求。
- 连接与传感器：官方会话库是"一个 ISession 一条 QMI 连接"，安卓 HAL 与 libssc 都是每个传感器一条连接；协议本身允许一条连接上挂多个 SUID（事件里带 SUID）。同一连接上的 SUID 请求是**追加**而不是替换；同一连接对同一 SUID 再发 513/514 是**更新**该流。

---

## 3. Protobuf 结构（官方字段号，proto2）

记号：`f号 名称 类型 [线型]`，线型 0=varint、1=64 位、2=长度前缀、5=32 位。`required` 字段即使等于默认值也必须编码。

### 3.1 通用

- `sns_std_suid`：f1 `suid_low` fixed64 [1]；f2 `suid_high` fixed64 [1]。SUID 查询传感器本身的 SUID 固定为 low = high = `0xabababababababab`，其余 SUID 全部动态发现。
- `sns_client_request_msg`：f1 `suid` sns_std_suid [2] 必选；f2 `msg_id` fixed32 [5] 必选；f3 `susp_config` [2] 必选；f4 `request` sns_std_request [2] 必选；f5 `resampler_config` [2] 可选；f6 `threshold_config` [2] 可选；f7 `client_tech` enum [0] 可选。
  - `suspend_config`：f1 `client_proc_type` enum [0] 必选（默认 APSS=1）；f2 `delivery_type` enum [0] 必选（WAKEUP=0 / NO_WAKEUP=1）；f3 `nowakeup_msg_ids` repeated fixed32。
  - `sns_resampler_client_config`：f1 `rate_type`（FIXED 0 / MINIMUM 1）；f2 `filter` bool。
  - `sns_threshold_client_config`：f1 `threshold_type`（RELATIVE_VALUE 0 / RELATIVE_PERCENT 1 / ABSOLUTE 2 / ANGLE 3）；f2 `threshold_val` repeated float（FLT_MAX 表示该字段不设阈值）。
- `sns_std_request`：f1 `batching` batch_spec [2]；f2 `payload` bytes [2]（传感器自己的请求消息编码后放这里，无消息体则不出现）；f3 `is_passive` bool [0]（缺省主动）；f4 `permissions` [2]（f1 repeated sns_tech）。
  - `batch_spec`：f1 `batch_period` uint32 必选（**微秒**，0=不批处理）；f2 `flush_period` uint32（微秒，不填则等于 batch_period）；f3 `flush_only` bool；f4 `max_batch` bool。整个 `batching` 不填 = 不批处理。
- `sns_client_event_msg`：f1 `suid` [2]；f2 `events` repeated [2]，每个 `sns_client_event`：f1 `msg_id` fixed32 [5]；f2 `timestamp` fixed64 [1]（**QTimer 时钟刻度**，见第 4 节）；f3 `payload` bytes [2]（按该传感器 API 解码）。框架产生的事件（配置更新、错误）时间戳是事件生成时刻。
- `sns_client_version_response_msg`：f1..f6 `major/minor/patch/sensor_major/sensor_minor/sensor_patch` uint32；请求消息为空。

### 3.2 标准消息号（框架层，所有传感器通用）

| msg_id | 名称 | 方向 | 载荷 |
| --- | --- | --- | --- |
| 1 | SNS_STD_MSGID_SNS_STD_ATTR_REQ | 请求 | 空（QSH 定义为空；老 SEE 的 `sns_std_attr_req` 只有可选字段，发空完全兼容） |
| 2 | SNS_STD_MSGID_SNS_STD_FLUSH_REQ | 请求 | 空；传感器先送出未发事件，再回 129 |
| 3 | SNS_STD_MSGID_SNS_STD_DEBUG_REQ | 请求 | 空 |
| 10 | SNS_CLIENT_MSGID_SNS_CLIENT_DISABLE_REQ | 请求 | 空；停掉本连接对该 SUID 的流 |
| 128 | SNS_STD_MSGID_SNS_STD_ATTR_EVENT | 事件 | `sns_std_attr_event` |
| 129 | SNS_STD_MSGID_SNS_STD_FLUSH_EVENT | 事件 | 空 |
| 130 | SNS_STD_MSGID_SNS_STD_ERROR_EVENT | 事件 | `sns_std_error_event`：f1 `error` enum |
| 131 | SNS_STD_MSGID_SNS_STD_DEBUG_RESP | 事件 | `sns_std_debug_resp`：f1 partial_resp bool、f2 debug_string、f3 binary_data{f1 proto_filename、f2 msgid、f3 payload} |
| 133 | SNS_STD_MSGID_SNS_STD_RESAMPLER_CONFIG_EVENT | 事件 | f1 `resampled_rate` float |

消息号分区（官方）：框架 0–127 请求、128–255 非周期事件、256–511 周期事件；传感器自有 512–767 请求、768–1023 非周期事件、1024–1535 周期事件。**传感器自有消息号只在同一数据类型内唯一**（例如 772 同时是 sig_motion、amd、device_mode 的事件号），解码要按"数据类型 + msg_id"。

枚举：`sns_std_error` 0 NO_ERROR、1 FAILED、2 NOT_SUPPORTED、3 INVALID_TYPE、4 INVALID_STATE、5 INVALID_VALUE、6 NOT_AVAILABLE、7 POLICY；`sns_std_client_processor` 0 SSC、1 APSS、2 ADSP、3 MDSP、4 CDSP；`sns_tech` 0 未初始化、1 SENSORS、2 AUDIO、3 CAMERA、4 LOCATION、5 MODEM、6 BLUETOOTH、7 WIFI、8 CONNECTIVITY。

### 3.3 SUID 发现（`sns_suid.proto`）

- 512 `SNS_SUID_MSGID_SNS_SUID_REQ`（请求，发给 SUID 0xabab…）：`sns_suid_req` f1 `data_type` string 必选；f2 `register_updates` bool（真 = 持续，传感器后来出现也会推送）；f3 `default_only` bool（默认真：只给默认传感器）。`data_type` 为空串 = 所有传感器（每个数据类型一个事件）。
- 768 `SNS_SUID_MSGID_SNS_SUID_EVENT`：`sns_suid_event` f1 `data_type` string；f2 `suid` repeated sns_std_suid。可能多次到达（列表变化时）。
- 769 `SNS_SUID_MSGID_SNS_SUID_DISCOVERY_DONE_EVENT`：空消息；只发一次（物理传感器初始化完成，或 SUID 传感器启动后超时兜底，或发现完成后才连上的客户端立即收到）。官方 HAL 用 APSS + WAKEUP、`register_updates=true` 发起查询。
- 默认传感器规则来自注册表 `default_sensors` 组（高通 sun 参考：accel/gyro/mag/gravity/rotv/game_rv/geomag_rv 要求 RIGID_BODY(19)=0（显示屏刚体）；proximity/ambient_light/sar 要求 STREAM_TYPE(16)=1（变化触发））。

### 3.4 属性（`sns_std_type.proto` + `sns_std_sensor.proto`）

- `sns_std_attr_event`：f1 `attributes` repeated `sns_std_attr`。
- `sns_std_attr`：f1 `attr_id` int32 [0]；f2 `value` `sns_std_attr_value` [2]。
- `sns_std_attr_value`：f1 `values` repeated `data` [2]；`data` 是 oneof：f1 `subtype` sns_std_attr_value [2]（嵌套，用于"成对"值）、f2 `str` string [2]、f3 `flt` float [5]、f4 `sint` **sfixed64 [1]**（不是 varint）、f5 `boolean` bool [0]。

| ID | 名称 | 值 |
| --- | --- | --- |
| 0 | NAME | str |
| 1 | VENDOR | str |
| 2 | TYPE | str（即 data_type） |
| 3 | AVAILABLE | bool |
| 4 | VERSION | sint（[31:16] 主、[15:8] 次、[7:0] 修订） |
| 5 | API | str 数组（.proto 文件名） |
| 6 | RATES | flt 数组（Hz） |
| 7 | RESOLUTIONS | flt 数组（与 RANGES 等长） |
| 8 | FIFO_SIZE | sint（样本数） |
| 9 | ACTIVE_CURRENT | sint 数组（µA，与 OP_MODES 等长） |
| 10 | SLEEP_CURRENT | sint（µA） |
| 11 | RANGES | subtype 数组，每个 {flt 最小, flt 最大} |
| 12 | OP_MODES | str 数组（如 "LPM"、"NORMAL"、"HIGH_PERF"、"OFF"） |
| 13 | DRI | bool |
| 14 | STREAM_SYNC | bool |
| 15 | EVENT_SIZE | sint |
| 16 | STREAM_TYPE | sint：0 STREAMING、1 ON_CHANGE、2 SINGLE_OUTPUT |
| 17 | DYNAMIC | bool |
| 18 | HW_ID | sint |
| 19 | RIGID_BODY | sint 数组：0 DISPLAY、1 KEYBOARD、2 EXTERNAL |
| 20 | （保留；老 SEE 为 PLACEMENT 12 个 flt） | — |
| 21 | PHYSICAL_SENSOR | bool |
| 22 | PHYSICAL_SENSOR_TESTS | sint 数组（0 SW、1 HW、2 FACTORY、3 COM） |
| 23 | SELECTED_RESOLUTION | flt |
| 24 | SELECTED_RANGE | flt 数组（2 个） |
| 25 | ADDITIONAL_LOW_LATENCY_RATES | flt 数组 |
| 26 | PASSIVE_REQUEST | bool |
| 29 | TRANSPORT_MTU_SIZE | sint |
| 30 | HLOS_INCOMPATIBLE | bool |
| 31 | SERIAL_NUM | — |
| 32 | TECH_USED | sint 数组（sns_tech） |

### 3.5 标准传感器（`sns_std_sensor.proto`）

- 513 `SNS_STD_SENSOR_MSGID_SNS_STD_SENSOR_CONFIG`（请求，流式开启/改频）：`sns_std_sensor_config` f1 `sample_rate` float 必选（Hz）。
- 514 `SNS_STD_SENSOR_MSGID_SNS_STD_ON_CHANGE_CONFIG`（请求，变化触发开启）：无载荷。
- 768 `SNS_STD_SENSOR_MSGID_SNS_STD_SENSOR_PHYSICAL_CONFIG_EVENT`：f1 `sample_rate` float、f2 `water_mark` uint32、f3 `range` repeated float（最多 2）、f4 `resolution` float、f5 `operation_mode` string、f6 `active_current` uint32、f7 `stream_is_synchronous` bool、f8 `dri_enabled` bool、f9 `DAE_watermark` uint32、f10 `sync_ts_anchor` uint64。硬件配置变化或有新请求时发一次。
- 1025 `SNS_STD_SENSOR_MSGID_SNS_STD_SENSOR_EVENT`：`sns_std_sensor_event` f1 `data` repeated float、f2 `status` enum 必选（0 UNRELIABLE、1 ACCURACY_LOW、2 MEDIUM、3 HIGH）。
- `sns_std_sensor_config_event`：f1 `sample_rate` float。
- 门控流（`sns_std_event_gated_sensor.proto`）：518 门控版 SENSOR_CONFIG；772 "门控请求已转为非门控"事件。

### 3.6 各数据类型专有消息

| 数据类型 | 消息号 | 结构 |
| --- | --- | --- |
| proximity | 769 `SNS_PROXIMITY_EVENT`（变化触发）/ 1031 `..._EVENT_RECURRENT`（流式） | f1 `proximity_event_type`（FAR 0 / NEAR 1）、f2 `raw_adc` uint32、f3 `status` |
| hall | 770 `SNS_HALL_EVENT` | f1 `hall_event_type`（FAR 0 / NEAR 1）、f2 `status` |
| sar | 769 / 1031 | f1 `sar_event_type`、f2 `additional_sar_data` bytes、f3 `status` |
| 校准（gyro_cal、mag_cal、物理传感器的出厂校准事件） | 512 `SNS_CAL_RESET`（请求，空）；1022 `SNS_CAL_EVENT` | f1 `bias` repeated float、f2 `scale_factor` repeated float、f3 `comp_matrix` repeated float、f4 `status`、f5 `cal_id` fixed32 |
| pedometer | 1028 `SNS_STEP_EVENT`；775 `SNS_STEP_EVENT_CONFIG` | 两者都是 f1 `step_count` uint32 |
| sig_motion | 772 | 空 |
| device_orient | 776 | f1 `state`：-1 UNKNOWN、0 DEFAULT（Y 轴竖直向上）、1、2、3 |
| amd | 772 | f1 `state`：0 UNKNOWN、1 STATIONARY、2 MOTION |
| tilt | 774 | 空 |
| persist_motion_detect / persist_stationary_detect | 769 / 770 | 空（持续 ≥5 s 运动/静止） |
| device_mode | 772 | `sns_device_mode_event`（模式 0–8、状态 0–2；由 AP 经 FastRPC `sns_device_mode_set` 设置） |
| device_context | 777 | f1 state、f2 data{f1 msg_id fixed32、f2 payload} |
| presence_detect | 928 | f1 state（0 未知、1 有人、2 无人） |
| 物理传感器自检 | 515 `..._TEST_CONFIG`（f1 test_type）；1026 `..._TEST_EVENT`（f1 test_passed、f2 test_type、f3 test_data） | — |

---

## 4. 数据类型、开启方式与单位

官方数据类型字符串（`SNS_STD_SENSOR_ATTRID_TYPE`）：`accel gyro mag pressure ambient_light proximity hall color ambient_temperature humidity sensor_temperature sar gyro_cal mag_cal accel_cal rotv game_rv geomag_rv gravity pedometer sig_motion device_orient amd tilt motion_detect persist_motion_detect persist_stationary_detect presence_detect human_presence_detect ontable_detect hinge_angle device_mode transport_ppe gyro_rot_matrix fmv suid`（sun 参考注册表另有 `tilt_to_wake`、`smd`、`rmd`、`cm`、`ccd_*` 等算法配置）。

| data_type | 开启 | 数据事件 | 内容与单位 | iOS 用途 |
| --- | --- | --- | --- | --- |
| accel | 513（Hz） | 1025 | data[0..2]，m/s²，已做出厂校准，SSC 坐标系 | 旋转、运动、抬起检测的原料 |
| gyro | 513 | 1025 | rad/s，右手定则；动态零偏另由 `gyro_cal`（514 开，1022 事件，bias 单位 rad/s）给出 | 运动/姿态 |
| mag | 513 | 1025 | µT；硬铁零偏由 `mag_cal`（514，1022，bias µT）给出 | 罗盘 |
| pressure | 513 | 1025 | data[0] hPa | 气压计（13T 很可能无） |
| ambient_light | 514（默认传感器是变化触发）或 513 | 1025 | data[0] lux、data[1] 原始 ADC（OEM 可追加字段）；变化触发约 ±10% 才报，**每个新请求会立即报一次当前值** | 自动亮度 |
| color（rgb） | 513/514 | 1025 | ≥7 个：lux、色温 K、CIE x、CIE y、三刺激 X、Y、Z | 原彩/色温 |
| proximity | 514（默认）→ 769；513 → 1031 | 见上 | NEAR/FAR + raw_adc；RANGES 单位 cm；只在跳变时报，**新请求会立即报一次** | 通话熄屏 |
| rotv / game_rv / geomag_rv | 513 | 1025 | data[0..3] = 单位四元数 x、y、z、w（rotv 以东-北-天为参考） | 姿态 |
| gravity | 513 | 1025 | 6 个：重力 x/y/z + 线性加速度 x/y/z，m/s² | 姿态/旋转 |
| pedometer | 514 | 1028（每步）/ 775（连上时的当前计数） | 自启用以来累计步数 | 计步 |
| sig_motion | 514 | 772 | 单次触发 | 运动唤醒 |
| device_orient | 514 | 776 | 方向状态 | 自动旋转（备选，iOS 一般自己算） |
| amd / tilt | 514 | 772 / 774 | 静止/运动、倾斜 | 抬起亮屏的备选 |

时间戳：`timestamp` 是 QTimer 刻度（19.2 MHz）。官方 `qshTimeUtil` 直接读 AP 的 `cntvct_el0` 与 `CNTFRQ` 来换算，所以 SSC 时间戳与 HLOS 虚拟机里的虚拟计数器同一时基：ns = ticks × 10⁹ / 19 200 000（≈52.083 ns/tick）。

坐标系：手机竖持、屏幕朝人时 x 向右、y 向上、z 垂直屏幕指向用户（与安卓相同）。附注（给 HID 一侧参考）：iOS 加速度单位是 g，且符号与安卓相反（平放屏幕朝上时安卓 z = +9.81 m/s²，iOS z = −1 g）；陀螺与磁力计轴向一致。

---

## 5. 推荐会话时序（客户端视角）

1. 等 NEW_SERVER(400) → 记下 node/port。新开一个本地 QRTR 端口作为客户端连接。
2. SUID 发现：对每个需要的 data_type 发 512（`register_updates=1, default_only=1`，APSS、WAKEUP）；或者发一个空 data_type 拿全表；收集 768 事件，直到 769 DISCOVERY_DONE（再加超时兜底）。建议清单：`accel gyro mag pressure ambient_light proximity gyro_cal mag_cal game_rv rotv gravity pedometer sig_motion device_orient`，以及 OPLUS 名（第 7 节，先用空 data_type 的结果确认真实字符串）。
3. 对每个 SUID 发 1（ATTR_REQ，空载荷）→ 128：取 NAME、VENDOR、TYPE、AVAILABLE、STREAM_TYPE、RATES、RANGES、RESOLUTIONS、RIGID_BODY、PHYSICAL_SENSOR、API。AVAILABLE=false 的不用。
4. 开启：STREAM_TYPE=0 → 513 + `sns_std_sensor_config{rate}`（选 RATES 里 ≥ 需要值的最小档）；=1 → 514 无载荷；=2 → 按其 API。不设 batching。我们不挂起 AP，delivery 用 WAKEUP 最简单；若以后 AP 会休眠，非关键流改 NO_WAKEUP（AP"睡着"期间 SSC 会缓存，见 6.4）。
5. 先到 768 物理配置事件（以及 1022 校准事件），然后是 1025（周期）或 769（变化）。
6. 改频：同一连接对同一 SUID 再发 513。停：发 10（DISABLE），收响应。刷新：2 → 129。错误：130。
7. 关闭驱动：发 DEL_CLIENT。PD 重启（DEL_SERVER/BYE）：丢掉全部 SUID，等新的 NEW_SERVER 后从第 2 步重来。

---

## 6. sensor PD 对 AP 的前置依赖

| # | 依赖 | 机制 | 13T 证据 | PaganiAdsp 现状 | 缺了会怎样 |
| --- | --- | --- | --- | --- | --- |
| 1 | ADSP 启动；sensor PD 是 ADSP 镜像里的静态 PD，由 root PD 自行拉起 | PAS | modem_a `adsps.jsn`（`msm/adsp/sensor_pd`，实例 74） | 有 | — |
| 2 | QRTR HELLO 与名字服务 | IPCRTR | 设备树 | 有 | 收不到 NEW_SERVER |
| 3 | 服务定位器（服务 0x40，NEW_SERVER 公告字 0x101，PaganiAdsp 已如此公告）应答 GET_DOMAIN_LIST(0x21) | QMI | Linux 内核 pd-mapper 对 `qcom,sm8750` 用的就是 sm8550 表（含 sensor_pd 74） | 有 | PD 查询服务失败 |
| 3b | 定位器上的 PFR 上报 `SERVREG_LOC_PFR_REQ` = 0x24：TLV 0x01 服务名（≤64）、TLV 0x02 崩溃原因（≤256），回 TLV 0x02 结果 | QMI | Linux pd-mapper 收到即打印"service crash" | **未处理** | 只是丢失 PD 崩溃原因；建议应答成功并记一行 `[adsp]`/`[sensors]` U 日志 |
| 4 | SMP2P `sleepstate` 出站 bit0：1=醒着、0=即将挂起；入站 `sleepstate_see` 是 SEE 叫醒 AP 用的 | SMP2P（ADSP 边） | 设备树 `sleepstate-out`/`qcom,sleepstate-in`，`qcom,smp2p_sleepstate` | 有（第 4 阶段置醒） | 置 0 时非唤醒事件被缓存 |
| 5 | **FastRPC 反向文件服务（附着 sensorspd）** | GLINK `fastrpcglink-apps-dsp` | 见 6.1 | **无** | 注册表不加载 → 没有物理传感器 |
| 6 | OPLUS SMEM 130 / 128 | 全局 SMEM | 见 6.2 | 无 | 未知（低成本补上） |
| 7 | OPLUS 屏下光感所需的显示信息 | 用户态转发 | 见 6.3 | 无 | 光感读数被屏幕自身发光污染 |
| 8 | servreg 通知（可选） | QMI 服务 66，实例 74 | — | 无 | 只影响 PD 重启的感知；看 DEL/NEW_SERVER 也够 |

不需要的：TFTP（13T 的传感器不用）、rmtfs、AP 侧传感器 LDO（OPLUS devinfo 只有设备树写了 `ldo_enable` 才会动 GPIO，13T 没写）、传感器供电（ADSP 自己通过 `/pmic/client/sensor_vdd(io)` 投票）。

### 6.1 FastRPC 反向文件服务（事实）

- 安卓守护进程（QSH 官方 rc）：`service vendor.sensors /vendor/bin/sensors.qti sensorspd adsp`（class early_hal，user system，group system wakelock）；它 dlopen `libadsp_default_listener.so` 调 `adsp_default_listener_start`，即"附着到 ADSP 的 sensors PD 并跑反向监听"。旧平台同一职责叫 `/vendor/bin/sscrpcd sensorspd`。13T recovery 里的 `vendor_file_contexts` 同时列出 `/vendor/bin/sensors.qti`、`/vendor/bin/sscrpcd`、`/vendor/bin/pd-mapper`、`/vendor/bin/tftp_server`、`/vendor/bin/adsprpcd`、`/vendor/bin/audioadsprpcd`、`/vendor/bin/qsap_sensors`、`/vendor/bin/hw/android.hardware.sensors-service.multihal`、`/mnt/vendor/persist/sensors(/.*)?`、`/vendor/dsp(/.*)?`（哪个真在跑，明早看）。
- 高通官方框架文档的注册表信号里有 `SNS_REGISTRY_RPCD_UP_SIGNAL`、`SNS_REGISTRY_RPCD_DN_SIGNAL`、`SNS_FILE_SERVICE_READY_SIGNAL`、`SNS_REGISTRY_DL_LOAD_SIGNAL`：注册表等 AP 上的 RPCD（即上面的守护进程）起来、文件服务就绪后才读文件、加载动态库。
- 社区实测（postmarketOS：SDM845、SM7225、SDM670 等）：只要 hexagonrpcd 以 `-s`（附着 sensorspd）提供注册表文件，传感器就能工作；SDM670 的合并请求明确说"只需要注册表"。
- QSH 的 systemd 单元：soc_id 756/758/759（shikra）不跑这个守护进程，而是把 `/var/cache/sensors` 链接给 `tqftpserv`（TFTP）；674/675/656/676/667 完全不跑。SM8750（618/639）属于"跑 FastRPC 守护进程"的一类。
- 设备树（ADSP `qcom,fastrpc`）：`qcom,glink-channels = "fastrpcglink-apps-dsp"`、`qcom,intents = <0x181 0x40>`、`qcom,vmids = <0x16 0x25>`、`memory-region`（adsprpc 内存）；计算上下文 `compute-cb@2`：`pd-type = <3>`（下游枚举 SENSORS_STATICPD；1 ROOT_PD、2 AUDIO_STATICPD、5 OIS_STATICPD、7 USERPD）、`qcom,nsessions = <8>`、`iommus = <&apps_smmu 0x1004 0x80>, <&apps_smmu 0x1044 0x20>`、`dma-coherent`。设备树里**没有** `qcom,fastrpc-adsp-sensors-pdr`（安卓不为 sensor PD 注册 PDR）。
- 内核层消息（AP→DSP，经该 GLINK 通道）：`{ s32 client_id; s32 tid; u64 ctx; u32 handle; u32 sc; u64 addr; u64 size; }` 共 40 B；`ctx = 上下文号（Linux 主线放在 bits[15:8]）| pd`，pd：ROOT_PD 0、USER_PD 1、**SENSORS_PD 2**；`addr` 是参数缓冲区的 DMA 地址（apps_smmu 上该会话上下文库里的 IOVA），`size` 按页向上取整。回应（DSP→AP）：`{ u64 ctx; s32 retval; }`（新版有带标志的扩展形式）。`sc` = `attr<<29 | method<<24 | in<<16 | out<<8 | in_handles<<4 | out_handles`。
- 附着 sensors PD：对 handle 1（INIT）调用 method 0（INIT_ATTACH），1 个入参 = 4 字节 client_id；此后该会话所有消息 `ctx` 低位为 2。下游 ioctl 对应 `FASTRPC_INIT_ATTACH_SENSORS = 3`，Linux 主线为 `FASTRPC_IOCTL_INIT_ATTACH_SNS`。
- 用户态反向通道：先经 DSP 的 remotectl（固定 handle 0；open = 方法 0，入参为接口名、返回句柄与错误串；close = 方法 1）按名字打开 `adsp_default_listener`，对它调 register（方法 0）；然后对 DSP 的固定句柄 3（`adsp_listener`）调 `init2`（方法 3），再循环 `next2`（方法 4）：每次 next2 带回上一个反向调用的结果（rctx、返回值、出参缓冲），并取回 DSP 发起的下一个反向调用（rctx、本地接口 handle、sc、入参缓冲，hexagonrpcd 给 256 B）。DSP 先用反向的 remotectl（AP 侧本地接口 0：open 0 / close 1）按名字打开 AP 侧的 `apps_std` 等接口，之后按返回的本地 handle 调用。
- `apps_std` 方法号（hexagonrpcd 实现的子集，实测足够）：freopen 1、fflush 2、fclose 3、fread 4、fseek 9、**fopen_with_env 19**、opendir 26、closedir 27、readdir 28、mkdir 29、stat 31。`fopen_with_env` 的环境变量名为 `ADSP_LIBRARY_PATH`（动态库搜索路径）或 `ADSP_AVS_CFG_PATH`；绝对路径直接按根目录解析。hexagonrpcd 拒绝写打开（'w'/'a'），仍能让传感器工作。
- DSP 会访问的路径（hexagonrpcd 映射 + 高通 QSH 安装路径 + sun 参考配置）：
  - `/vendor/etc/sensors/sns_reg_config`（QSH sun 参考内容：`version=1`、`file=input=json.lst`、`file=output=/mnt/vendor/persist/sensors/registry/registry`、`property=persist.vendor.sensors.enable.property=/mnt/vendor/persist/sensors/registry/file1` 等）；
  - `/vendor/etc/sensors/config/json.lst` 及其列出的 `*.json`（sun 参考约 60 个：`pakala_*.json`、`sns_*.json`、`qsh_camera_*.json`；每个 JSON 的 `config` 段写明适用的 `hw_platform` 与 `soc_id`；物理传感器 `*_platform` 组里有 `bus_type`（0 I2C、3 I3C…）、`bus_instance`、`slave_config`（地址）、`i3c_address`、`dri_irq_num`、`irq_*`、`vdd(io)_rail`、`rigid_body_type`，以及 `fac_cal`（出厂校准）等）；
  - `/mnt/vendor/persist/sensors/registry/registry/`（解析结果；QSH 默认是单个 `sensors_registry` 文件，首次启动由 SSC 写出）与 `/mnt/vendor/persist/sensors/registry/sns_reg_version`（`version=0`）——**含本机出厂校准，必须从本机原样抓**；
  - `/sys/devices/soc0/`（`soc_id`、`hw_platform`、`platform_subtype(_id)`、`revision` 等，用于挑 JSON）；
  - `ADSP_LIBRARY_PATH` 下的动态库：注册表组 `sns_dynamic_libraries` 列出要加载的 .so 与注册函数（sun 参考：`sns_tppe.so`/`sns_tppe_register`）；安卓上一般在 `/vendor/lib/rfsa/adsp`、`/vendor/dsp/adsp` 等，OPLUS 可能放了自己的驱动/算法库；
  - 同一内容的别名路径：`/persist/sensors/registry/registry`、`/system/vendor/etc/sensors/...`（不同平台访问路径不同，hexagonrpcd 两处都给）；音频 PD 另需 `/vendor/etc/acdbdata`。
- 结论：要让 13T 的传感器在 XNU 下工作，需要（a）FastRPC 内核层（GLINK 通道、上下文号、DMA 缓冲在 apps_smmu SID 0x1004/0x1044 下可被 DSP 访问）、（b）adsp_listener 反向循环、（c）`apps_std` 只读子集、（d）明早抓下来的配置/注册表/动态库/socinfo 打包成只读文件树。SSC 的 QMI 客户端（第 2–5 节）本身工作量小，但没有（a）–（d）它查不到任何物理传感器。

### 6.2 OPLUS SMEM 条目（反汇编 vendor_boot 里的 `oplus_sensor_deviceinfo.ko`、`oplus_sensor_feedback.ko`）

- **130（0x82）**：`qcom_smem_alloc(QCOM_SMEM_HOST_ANY, 130, 9016)`（全局分区），清零后按设备树 `oplus_sensor` 填写。布局：12 个物理传感器块，每块 688 B（+4 处 u32 = 类型；+8 起 3 个 228 B 硬件槽：u8 sensor_name、u8 bus_number、u8 direction、u8 irq_number，随后是参数），从偏移 8256 起 9 个虚拟传感器块，每块 84 B（块首 = 8260 + 84×类型；+0 u32 类型，+4… 参数）。**13T 的实际内容：除虚拟块 6（mag_fusion）外全 0**：u32@8764 = 6（类型）、u32@8828 = 0（fusion-type）、u32@8832 = 0（fold-feature）、u32@8836 = 1（drop_zero）；track_trigger/absolute_trigger 未配置（@8768、@8772 为 0）。13T 的物理传感器参数不在这里，而在注册表 JSON 里。
- **128（0x80）**：`qcom_smem_alloc(ANY, 128, 3488)`；SSC 往里写 109 条 × 32 B 反馈记录（事件 id 1000 携带物理/虚拟传感器可用位图；800/801/804 与 810–844 另有专门处理，其余按 150 项事件表转成 OPLUS kevent），AP 内核线程读出生成 `/proc/sns_debug/sensor_list`（`phy = 0x.., virt = 0x..`）并上报 OPLUS 的 kevent。设备树 `sensor_fb { master-id = <3>; smem-id = <0x260>; }` 里的 0x260 代码中未使用。
- 是否为 SSC 工作的硬前提：离线无法判定。建议 XNU 在启动 ADSP 前（拿 TCSR 硬件互斥锁）按上面内容分配 130、分配并清零 128，成本很低。

### 6.3 OPLUS 屏下光感的显示信息链路（`oplus_sensor_interact.ko`）

- 设备树：`ssc_interactive { sup-power-fb; sup-hbm-mode = <1>; need_lb_algo = <1>; receive_screenshot_info = <1>; report_blank_mode = <1>; primary_lb_brl_info { brl_thrd_num = <1>; brl_thrd = <1>; brl_l2h_thrd = <1>; }; }`。
- 内核注册面板事件通知，把变化写进 kfifo；misc 设备 `/dev/ssc_interactive` 读出 32 B 记录：`u8 type, u8 pad, u16 value, …`。已见 type：1 = DC 调光模式、2 = 亮度（按 brl 阈值量化后）、6 = HBM、7 = 脉冲模式、8/9 = PWM/频率相关、16 = 截屏信息（两个 s64 + 两个 s32），另有熄屏/亮屏（blank）记录。`/dev/ssc_screenshot_info` 接收形如 `"%lld,%lld,%d,%d"` 的文本（SurfaceFlinger 按 50 ms 周期对光感区域截屏统计）。sysfs 另有 `brightness`、`dc_mode`。依赖 `oplus_sensor_feedback`（`ssc_fb_set_screen_status`）。
- 这些记录由用户态（OPLUS 传感器 HAL/守护进程）读出后再经 QMI 送给 SSC 里 OPLUS 的光感算法；具体发给哪个 SUID、用什么 msg_id 离线无法得知 → 明早找出打开这两个设备的进程，抓它的二进制离线分析（附录 B.2"OPLUS 屏下光感链路"一组，库文件由 B.1 的 `sensor-libs.tar` 带回）。
- 意义：不送这些信息，屏下光感读数会被屏幕自身发光污染（亮屏越亮读数越高，形成正反馈）；iOS 自动亮度要么喂这些数据，要么在 AP 侧补偿/限幅。
- 另：OPLUS 的光感出厂校准走 `/proc/sensor/als_cali/{red,green,blue,white}_max_lux、cali_coe、row_coe、als_type`（只是内核里的存储，由用户态 HAL 读写），也是 HAL 侧逻辑。

### 6.4 servreg / PDR（可选）

- 定位器：服务 64（0x40）；GET_DOMAIN_LIST 0x21；PFR 0x24。
- 通知：服务 66（0x42），ADSP 各 PD 的实例 74（Linux 以版本 1、实例 74 查找，QRTR 公告字 = 1 | 74<<8 = 0x4A01）。REGISTER_LISTENER 0x20（TLV 0x01 enable u8、0x02 服务路径如 `msm/adsp/sensor_pd`）→ 响应带当前状态；STATE_UPDATED 指示 0x22（状态、路径、u16 事务号）；SET_ACK 0x23（路径、事务号）；RESTART_PD 0x24。状态值：DOWN 0x0FFFFFFF、UP 0x1FFFFFFF、EARLY_DOWN 0x2FFFFFFF、UNINIT 0x7FFFFFFF。
- SSC 的 `remote_proc_state` 平台传感器负责感知 AP 挂起/唤醒（事件里还可带 AP 与 QSH 的时间偏移，AP 经 FastRPC 设置）；最简单的做法就是保持 `sleepstate` = 醒着。

---

## 7. OPLUS 增加的传感器名（`oplus_sensor_feedback.ko` 的 `virtual_sensor_list[31]` 与 `physical_sensor_list[18]`）

`/proc/sns_debug/sensor_list` 输出 `phy = 0x.., virt = 0x..`，第 i 位对应下表第 i 项（明早据此解码 13T 实际装了哪些）：

- 虚拟（算法）0–30：0 cm、1 lux_aod、2 pick_up_motion、3 pedometer、4 pedometer_minute、5 free_fall、6 amd_oplus、7 oplus_activity_recognition、8 oplus_double_tap、9 oplus_measurement、10 cmc_oplus、11 device_orient、12 camera_protect、13 elevator_detect、14 ois_system、15 direct_channel、16 shaking、17 lay_detect、18 gesture_prox、19 phone_prox、20 explorer_gyro、21 hinge_detect、22 fold_state、23 oplus_camera_orient、24 trestle_hall、25 fold_fusion_light、26 pad_als、27 pad_wise_rgb、28 pocket、29 sub_device_orient、30 chop。
- 物理 0–17：0 proximity、1 ambient_light、2 wise_light、3 wise_rgb、4 rgb、5 flicker、6 ambient_light_rear、7 rgb_rear、8 flicker_rear、9 spectral_rear、10 accel、11 gyro、12 mag、13 hall、14 pressure、15 sar、16 sars、17 resampler。
- 与 iOS 功能相关的候选：抬起亮屏 → `pick_up_motion`（或高通 `tilt`/`tilt_to_wake`、`amd`）；通话接近 → `proximity` 或 `phone_prox`（OPLUS 融合）；自动亮度 → `ambient_light`/`wise_light`/`rgb`；计步 → `pedometer`（+ `pedometer_minute`）；口袋/防误触 → `pocket`；方向 → `device_orient`。
- 这些是 OPLUS 反馈/统计用的名字，是否等于 SSC 的 data_type 字符串需明早用空 data_type 的 SUID 查询或 `dumpsys sensorservice` 确认；安卓层的 OPLUS 传感器类型字符串也以 dumpsys 为准。
- OPLUS 设备树里的虚拟传感器类型号（devinfo 用）：13T 只有 `mag_fusion` = 6。

---

## 附录 A：线格式（精确）

### A.1 protobuf 编码要点

- tag = (字段号 << 3) | 线型；varint 小端 7 位一组、最高位为续位；fixed32/float 4 字节小端；fixed64/sfixed64 8 字节小端；长度前缀 = varint 长度 + 字节。
- `int32`/`enum` 的负值按 64 位补码编码成 **10 字节 varint**（例：device_orient UNKNOWN(−1) = `08 ff ff ff ff ff ff ff ff ff 01`）。`sint` 属性值是 sfixed64（线型 1），**不是** zigzag。
- proto2 的 repeated 标量默认**不打包**（每个 float 一个 `0d xx xx xx xx`）；解码器必须同时接受打包（线型 2，长度内连续 4 字节 float）与不打包两种写法。未知字段按线型跳过；字段顺序不作假设；同一非 repeated 字段出现多次取最后一个。
- 编码时 required 字段一律写出（`susp_config` 的两个字段、`request` 即使为空也写 `22 00`、`sns_std_sensor_config.sample_rate`、`sns_suid_req.data_type` 即使空串也写 `0a 00`），否则 SSC 端 nanopb 解码失败。

### A.2 tag 字节速查

| 消息 | tag |
| --- | --- |
| sns_std_suid | `09` low、`11` high |
| sns_client_request_msg | `0a` suid、`15` msg_id、`1a` susp_config、`22` request、`2a` resampler、`32` threshold、`38` client_tech |
| suspend_config | `08` proc、`10` delivery、`1d`（或打包 `1a`）nowakeup_msg_ids |
| sns_std_request | `0a` batching、`12` payload、`18` is_passive、`22` permissions |
| batch_spec | `08` batch_period、`10` flush_period、`18` flush_only、`20` max_batch |
| sns_client_event_msg | `0a` suid、`12` event |
| sns_client_event | `0d` msg_id、`11` timestamp、`1a` payload |
| sns_suid_req | `0a` data_type、`10` register_updates、`18` default_only |
| sns_suid_event | `0a` data_type、`12` suid |
| sns_std_attr_event | `0a` attribute |
| sns_std_attr | `08` attr_id、`12` value |
| sns_std_attr_value | `0a` data |
| data（oneof） | `0a` subtype、`12` str、`1d` flt、`21` sint、`28` boolean |
| sns_std_sensor_config | `0d` sample_rate |
| sns_std_sensor_event | `0d`（或打包 `0a`）data、`10` status |
| physical_config_event | `0d` rate、`10` water_mark、`1d`/`1a` range、`25` resolution、`2a` op_mode、`30` active_current、`38` sync、`40` dri、`48` DAE_wm、`50` sync_ts_anchor |
| sns_proximity_event / _recurrent | `08` type、`10` raw_adc、`18` status |
| sns_hall_event | `08` type、`10` status |
| sns_cal_event | `0d`/`0a` bias、`15`/`12` scale、`1d`/`1a` comp_matrix、`20` status、`2d` cal_id |
| sns_step_event(_config) | `08` step_count |
| sns_std_error_event | `08` error |
| device_orient / amd 事件 | `08` state |

### A.3 QMI 帧

- 请求：`00 | txn(u16) | 20 00 | len(u16) | 01 | (n+2)(u16) | n(u16) | n 字节 protobuf | [10 01 00 | use_jumbo(u8)]`。
- 响应：`02 | txn | 20 00 | len | 02 04 00 | result(u16) error(u16) | [10 08 00 client_id(u64)] | [11 04 00 result(u32)]`。
- 指示：`04 | txn(通常 0) | 21 00 或 22 00 | len | 01 08 00 client_id(u64) | 02 (n+2)(u16) n(u16) n 字节`。

### A.4 测试向量（十六进制；自写编码器生成；SUID 示例值 low = 0x0123456789abcdef、high = 0xfedcba9876543210）

1. `sns_suid_req{"accel", register_updates=1, default_only=1}`（11 B）：
   `0a 05 61 63 63 65 6c 10 01 18 01`
2. 发给 SUID 传感器的 `sns_client_request_msg`（msg 512，APSS，WAKEUP）（46 B）：
   `0a 12 09 ab ab ab ab ab ab ab ab 11 ab ab ab ab ab ab ab ab 15 00 02 00 00 1a 04 08 01 10 00 22 0d 12 0b 0a 05 61 63 63 65 6c 10 01 18 01`
3. 上面这条装进 QMI 请求（txn 1，带 `use_jumbo_report=0`）（62 B）：
   `00 01 00 20 00 37 00 01 30 00 2e 00` + 第 2 条 46 B + `10 01 00 00`
4. 再装进 QRTR DATA（示例：我们 1:0x4001 → ADSP 5:0x1a；62 B 载荷补 2 个 0 到 64 B，共 96 B）：
   `01 00 00 00 01 00 00 00 01 00 00 00 01 40 00 00 00 00 00 00 3e 00 00 00 05 00 00 00 1a 00 00 00` + 第 3 条 62 B + `00 00`
5. ATTR_REQ（msg 1，空 request）（33 B）：
   `0a 12 09 ef cd ab 89 67 45 23 01 11 10 32 54 76 98 ba dc fe 15 01 00 00 00 1a 04 08 01 10 00 22 00`
6. `sns_std_sensor_config{50.0}`（5 B）：`0d 00 00 48 42`；SENSOR_CONFIG（msg 513）（40 B）：
   `0a 12 09 ef cd ab 89 67 45 23 01 11 10 32 54 76 98 ba dc fe 15 01 02 00 00 1a 04 08 01 10 00 22 07 12 05 0d 00 00 48 42`
   同上但显式 `batch_period=0`（44 B）：`… 22 0b 0a 02 08 00 12 05 0d 00 00 48 42`
7. ON_CHANGE_CONFIG（msg 514，空 request）：`0a 12 09 ef cd ab 89 67 45 23 01 11 10 32 54 76 98 ba dc fe 15 02 02 00 00 1a 04 08 01 10 00 22 00`
8. DISABLE（msg 10）：`0a 12 09 ef cd ab 89 67 45 23 01 11 10 32 54 76 98 ba dc fe 15 0a 00 00 00 1a 04 08 01 10 00 22 00`
9. `sns_std_sensor_event{0.125, 9.75, -0.5; status=3}`（不打包，17 B）：`0d 00 00 00 3e 0d 00 00 1c 41 0d 00 00 00 bf 10 03`
10. 对应的 `sns_client_event_msg`（msg 1025，timestamp 0x1234567890）（55 B）：
    `0a 12 09 ef cd ab 89 67 45 23 01 11 10 32 54 76 98 ba dc fe 12 21 0d 01 04 00 00 11 90 78 56 34 12 00 00 00 1a 11 0d 00 00 00 3e 0d 00 00 1c 41 0d 00 00 00 bf 10 03`
11. 装进 0x21 指示（client_id 0x55）（78 B）：
    `04 00 00 21 00 47 00 01 08 00 55 00 00 00 00 00 00 00 02 39 00 37 00` + 第 10 条 55 B
12. 成功响应（client_id 0x55，result 0）（32 B）：
    `02 01 00 20 00 19 00 02 04 00 00 00 00 00 10 08 00 55 00 00 00 00 00 00 00 11 04 00 00 00 00 00`
13. `sns_proximity_event{NEAR, raw_adc=1234, HIGH}`：`08 01 10 d2 09 18 03`
14. `sns_suid_event{"accel", [示例 SUID]}`（27 B）：`0a 05 61 63 63 65 6c 12 12 09 ef cd ab 89 67 45 23 01 11 10 32 54 76 98 ba dc fe`
15. `sns_std_attr_event`：NAME="lsm6dsv"、RATES={25, 50}、STREAM_TYPE=0、RANGES={(−78.4532, 78.4532)}（78 B）：
    `0a 0f 08 00 12 0b 0a 09 12 07 6c 73 6d 36 64 73 76 0a 12 08 06 12 0e 0a 05 1d 00 00 c8 41 0a 05 1d 00 00 48 42 0a 0f 08 10 12 0b 0a 09 21 00 00 00 00 00 00 00 00 0a 16 08 0b 12 12 0a 10 0a 0e 0a 05 1d 0a e8 9c c2 0a 05 1d 0a e8 9c 42`
16. `sns_device_orient_event{UNKNOWN}`：`08 ff ff ff ff ff ff ff ff ff 01`

### A.5 FastRPC 内核消息（只列事实，供以后实现文件服务时参考）

- AP→DSP（40 B，小端）：`+0 s32 client_id（内核自身调用为 0）| +4 s32 tid | +8 u64 ctx | +16 u32 handle | +20 u32 sc | +24 u64 addr | +32 u64 size`。
- DSP→AP（12 B 起）：`+0 u64 ctx | +8 s32 retval`。
- 附着 sensors PD：`handle = 1`，`sc = (0<<24) | (1<<16)`（method 0，1 个入参），入参 4 字节 = client_id，`ctx` 的 pd 位 = 2。

---

## 附录 B：明早上机的只读命令（手机进安卓、adb 已授权、有 root）

只读原则：不 `setprop`、不 `start/stop`、不写 sysfs/proc、不挂载 debugfs、不在手机上建文件；落盘只发生在电脑上。为避免多层引号出错，分两部分：B.1 在电脑上执行（把结果存成文件），B.2 先 `adb shell`、再 `su` 进入 root shell 后逐条粘贴。

### B.1 电脑端（落盘）

```sh
D=~/unixnu-13t/build-userspace/a830-inputs/sensors/device-dump; mkdir -p "$D"
adb shell dumpsys sensorservice > "$D/dumpsys-sensorservice.txt"          # 传感器清单：名称/厂商(=芯片)/类型/频率/唤醒标志
adb logcat -d -b all > "$D/logcat-all.txt"
adb exec-out "su -c 'getprop'" > "$D/getprop-root.txt"                    # root 下才能看到 vendor.* 与 init.svc.vendor.*
adb exec-out "su -c 'dmesg'" > "$D/dmesg.txt"
adb exec-out "su -c 'tar -cf - -C / vendor/etc/sensors'" > "$D/vendor-etc-sensors.tar"          # sns_reg_config、config/*.json、json.lst、hals.conf、proto/
adb exec-out "su -c 'tar -cf - -C / mnt/vendor/persist/sensors'" > "$D/persist-sensors.tar"     # 注册表与本机出厂校准
adb exec-out "su -c 'cd / && tar -cf - vendor/lib/rfsa/adsp vendor/dsp 2>/dev/null'" > "$D/dsp-libs.tar"
adb exec-out "su -c 'cd / && tar -cf - vendor/firmware_mnt/image/adsp* vendor/firmware_mnt/image/*.jsn'" > "$D/adsp-fw.tar"
adb exec-out "su -c 'cd / && tar -cf - \$(find vendor/lib64 odm/lib64 vendor/bin odm/bin -maxdepth 2 -iname \"*sensor*\" -o -iname \"*sns*\" -o -iname \"*ssc*\" -o -iname \"*qsh*\" -o -iname \"libadsp*\" -o -iname \"libfastrpc*\" 2>/dev/null)'" > "$D/sensor-libs.tar"
for f in "$D"/*.tar; do echo "$f: $(tar -tf "$f" | wc -l) 项"; done
grep -i -E 'sensor|sns|ssc|qspa|lightsensor|underscreen|fusion' "$D/getprop-root.txt"
grep -E 'init\.svc\.(vendor\.)?(sensors|sscrpcd|pd_mapper|tftp_server|adsprpcd|audioadsprpcd|qsap|oplus_sensor)' "$D/getprop-root.txt"
grep -i -E 'adsp|sensor|ssc|sns|fastrpc|adsprpc|pdr|servreg|sleepstate|smp2p|qrtr|glink' "$D/dmesg.txt" > "$D/dmesg-sensors.txt"
grep -i -E 'sensors|ssc|sns_|qsh|sscrpcd|sensors\.qti|adsp_default_listener|apps_std|suid' "$D/logcat-all.txt" > "$D/logcat-sensors.txt"
```

### B.2 root shell（`adb shell` → `su`）

```sh
# 守护进程：sensors.qti 还是 sscrpcd、命令行、连的 fastrpc 设备、ADSP_LIBRARY_PATH
ps -A -o PID,USER,NAME,ARGS | grep -i -E 'sensor|sscrpcd|pd-mapper|tftp|adsprpcd|qrtr'
for p in $(pidof sensors.qti sscrpcd); do echo "== $p"; tr '\0' ' ' < /proc/$p/cmdline; echo; ls -l /proc/$p/fd; tr '\0' '\n' < /proc/$p/environ | grep -E 'ADSP|DSP|LD_'; done
grep -rn -E 'sensors|sscrpcd|pd-mapper|tftp_server|adsprpcd' /vendor/etc/init /odm/etc/init 2>/dev/null
cat /vendor/bin/init.qcom.sensors.sh 2>/dev/null
dumpsys android.hardware.sensors.ISensors/default 2>&1 | head -200

# OPLUS 的可用传感器位图（按第 7 节解码）与光感校准存储
cat /proc/sns_debug/sensor_list
ls -la /proc/sensor /proc/sensor/als_cali 2>/dev/null; for f in /proc/sensor/als_cali/*; do echo "$f=$(cat $f)"; done

# 配置与注册表
ls -laR /vendor/etc/sensors
cat /vendor/etc/sensors/sns_reg_config; echo ---; cat /vendor/etc/sensors/hals.conf; echo ---; cat /vendor/etc/sensors/config/json.lst
ls /vendor/etc/sensors/config/*sensing_hub_info.json 2>/dev/null      # 有它才可能不是 QMI 传输
ls -laR /mnt/vendor/persist/sensors
find /vendor /odm /vendor_dlkm /odm_dlkm /my_product -xdev \( -iname '*sensor*' -o -iname '*sns_*' -o -iname '*ssc*' -o -iname '*qsh*' \) 2>/dev/null

# socinfo：注册表按它挑 JSON，FastRPC 文件服务要原样提供
cd /sys/devices/soc0 && for f in *; do [ -f "$f" ] && echo "$f=$(cat "$f" 2>/dev/null)"; done; cd /

# DSP 动态库、ADSP 固件、pd-mapper 文件
ls -la /vendor/lib/rfsa/adsp /vendor/dsp /vendor/dsp/adsp /odm/lib/rfsa/adsp 2>/dev/null
ls -la /vendor/firmware_mnt/image | grep -i -E 'adsp|jsn'

# FastRPC / IPC 设备节点与中断
ls -la /dev | grep -i -E 'fastrpc|adsprpc|ssc|sensors|glink|qrtr'
ls -la /sys/kernel/fastrpc 2>/dev/null
grep -i -E 'smp2p|sleepstate|glink|ipcc|adsp|lpass' /proc/interrupts

# OPLUS 屏下光感链路：谁在读 ssc_interactive / 写 ssc_screenshot_info
ls -la /dev/ssc_interactive /dev/ssc_screenshot_info; ls -la /sys/class/misc/ssc_interactive/ 2>/dev/null
cat /sys/class/misc/ssc_interactive/brightness /sys/class/misc/ssc_interactive/dc_mode 2>/dev/null
for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q -E 'ssc_interactive|ssc_screenshot_info|sns_debug' && echo "$p $(tr '\0' ' ' < $p/cmdline)"; done
find /sys/devices/platform -maxdepth 3 -iname '*sensor_fb*' -exec ls -la {} \;

# QRTR 服务清单（多半没有工具；debugfs 没挂就跳过，不要自己挂）
which qrtr-lookup 2>/dev/null; ls /vendor/bin | grep -i -E 'qrtr|qmi'
ls /sys/kernel/debug/ipc_logging 2>/dev/null | grep -i -E 'qrtr|glink|adsp|fastrpc'
```

看什么：（1）`sensors.qti` 还是 `sscrpcd` 在跑、连的哪个 `/dev/fastrpc-*` 或 `/dev/adsprpc-*`、`ADSP_LIBRARY_PATH` 的值；（2）`dumpsys` 里每个传感器的名称/厂商（即芯片型号）、是否唤醒型、OPLUS 自定义类型；（3）`sns_reg_config` 的输入/输出路径与 `json.lst`；（4）`persist/sensors` 的完整内容（校准）；（5）`soc0` 的 `soc_id`/`hw_platform`/`platform_subtype`；（6）谁在读 `/dev/ssc_interactive`；（7）`/proc/sns_debug/sensor_list` 的位图。

---

## 附录 C：来源

本机：
- `/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/dt/running.dts`（ADSP remoteproc/GLINK/FastRPC/SMP2P、`oplus_sensor`、`sensor_fb`、`ssc_interactive`、`gpio-hall-sensor`、根节点 msm-id/board-id）。
- `/Users/xiaochoumao/unixnu-13t/一加13T原厂备份/设备信息/系统属性.txt`、`内核命令行.txt`。
- `/Users/xiaochoumao/unixnu-13t/一加13T原厂备份/分区镜像/vendor_boot_a.img`（解出 ramdisk：`oplus_sensor_deviceinfo.ko`、`oplus_sensor_feedback.ko`、`oplus_sensor_interact.ko`、`oplus_magcvr_*.ko` 等；用 IDA 9.5 反编译取事实）、`recovery_a.img`（`vendor_file_contexts`、sensors-multihal 清单）。
- `/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiAdsp/`（README、`AdspQrtr.c` 域表、`QcomQrtr.c`、`PaganiAdsp.h`）。

公开资料：
- 高通官方 Qualcomm Sensing Hub 仓库 `github.com/qualcomm/sensinghub`（BSD-3-Clause，提交 `8f23fc7fae0a93f0b938c5ed08ef3f4ba952f765`，2026-10-28）：`apis/proto/*.proto`、`core/qshQmiIDL/sns_client_api_v01.{c,h}`、`core/sessionImpl/src/qmiSession.cpp`、`utils/inc/qshTimeUtil.h`、`utils/src/suidLookUp.cpp`、`services/sensorsdaemon/*`、`config/registry/default/sensors/sun/*`、`config/Android.mk`。
- 高通 Sensing Hub API Guide（docs.qualcomm.com，文档号 80-P9361-100：client、driver、platform_sensors、fwutils、skel 各页）。
- libssc 0.4.4（GPL-3.0，Debian 源码）、libqmi 1.38（`qmi-service-ssc.json`，LGPL）——只核对事实（QMI_SERVICE_SSC = 0x190、0x20/0x21/0x22、TLV 号、消息号 512/513/514/768/769/1025/10/1/128）。
- hexagonrpc / hexagonrpcd（GPL-3.0，`linux-msm/hexagonrpc`）：手册页、`rpcd_builder.c`、`interface/*.c`——只取路径映射与方法号事实。
- Linux 主线 `drivers/misc/fastrpc.c`、`drivers/soc/qcom/qcom_pd_mapper.c`、`pdr_internal.h`、`include/linux/soc/qcom/pdr.h`；OnePlusOSS `android_kernel_oneplus_sm8550` 的 `drivers/char/adsprpc*.{c,h}`（GPL-2.0）——只取常量。
- postmarketOS 合并请求（SDM670 只需注册表、SDM845 用 hexagonrpcd 等）。

---

## 附录 D：待确认 / 风险

1. FastRPC 反向文件服务是硬前提，工作量最大：GLINK `fastrpcglink-apps-dsp` + 上下文号 + DMA 缓冲在 apps_smmu（SID 0x1004/0x1044）下对 DSP 可见。【评审更正】不用配一级页表：这些流走旁路上下文库（S2CR 指向 SCTLR=0 的库），`SmmuTakeover.h` 已在 UFS 和触摸上实机验证过，扬声器同法用于音频流；FastRPC 地址高位放上下文库号 4。
2. SSC 首次启动会尝试写注册表（`file=output=…/registry`）。只读服务按 hexagonrpcd 经验可工作（前提是带上安卓已生成的注册表）；能否完全不写，需上机验证。
3. OPLUS 的光感补偿链路（谁、发什么 QMI 消息给哪个 SUID）只能在明早抓到 HAL 二进制后确定。
4. SMEM 130/128 是否必需不明；建议照安卓内容预先分配（第 6.2 节）。
5. 芯片型号、是否有气压计/SAR/后置光感、OPLUS data_type 的真实字符串、`soc0` 的值，全部以明早 `dumpsys` 与 `/proc/sns_debug/sensor_list` 为准。
6. SNS 服务的 QRTR 实例号、ADSP 的 QRTR 节点号以 PaganiAdsp 的 `qrtr hello`/`qrtr services` U 行为准；`use_jumbo_report` 与 IPCRTR intent 尺寸要一起决定（第 2.3 节）。
