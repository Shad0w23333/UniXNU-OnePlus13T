# PaganiSensors 评审与收尾报告（键：sensors-review）

- 日期：2026-10-31。状态：**只有源码和主机测试，没有碰手机，没有提交**。下面凡是写“静态确认”的，都没有实机证据。
- 驱动：`UniXNU-Pagani-Drivers/drivers/PaganiSensors`（下称 DRV）。集成补丁：`integrate/`。

---

## 0. 结论

1. **线上格式没有问题**：逐项对照 `ssc-facts.md`，未发现缺陷，A.4 的 16 个向量全过。
2. **iOS 侧原有 3 个缺口，已修**：
   - MobileGestalt 的 `accel`、`gyro` 节点没人建，现在由驱动在 `pagani_sensors_start` 返回前同步创建，只在模式 3，且服务确实发布了才建。
   - 接近传感器在重复写 `DetectionMode` 时不补报状态。
   - 方向服务原来没有，所以没踩陷阱，但也没有旋转。现已做成默认关的一体件（方向服务 + `'ashd'` 用户客户端 + 沙盒放行 + 内核方向判定）。
3. **原方案里还有一个新陷阱**：`backboardd` 和 `locationd` 都有沙盒。只加 `'ashd'` 不够，沙盒拒绝打开时照样空指针崩溃。已用项目现成的 `NativeSandboxGrantAll` 解决（第 2.2 节）。
4. **FastRPC 反向文件服务今晚做完了**，判断是可控：SMMU 不用配页表，现成的直通规划器在 UFS、触摸上已实机验证过。产物：完整的可移植核心、内核胶水、打包工具，以及用公开布局独立写的模拟 DSP 测试。**它从没见过真 DSP**，第 7 节的第 4 步才是真正的考验。
5. **做不完的只有一项硬缺口**：屏下光感需要的显示信息，要等安卓 HAL 抓到才知道发什么（第 3.4 节）。
6. **盘点里的一条要更正**：旋转**不需要 locationd**，CoreMotion 就跑在 backboardd 进程里。要 locationd 的只有 App 取陀螺仪和磁力计（第 4 节）。

---

## 1. 线上格式（对照 ssc-facts.md）

逐项核对了 `SensorsPb.c`、`SensorsSsc.c`，未发现缺陷，没有改动。

| 项 | 结果 |
| --- | --- |
| QMI 0x20 请求、响应（TLV 0x02/0x10/0x11）、0x21/0x22 指示（TLV 0x01/0x02） | 一致 |
| `sns_client_request_msg` 的必选字段（`susp_config` 两个字段、空 `request` 写 `22 00`、空 `data_type` 写 `0a 00`） | 一致 |
| 事件、SUID、属性（`sint` 是 sfixed64、`RANGES` 是嵌套 subtype）、物理配置、接近、错误事件的字段号与线型 | 一致 |
| 打包与不打包的 repeated float、10 字节负数 varint、未知字段跳过 | 一致 |
| `use_jumbo_report=0`（PaganiAdsp 的 IPCRTR 只有 2 KiB intent） | 一致 |
| QRTR `confirm_rx` 的 RESUME_TX | `QcomQrtr.c` 第 166 行已处理 |

---

## 2. iOS 侧

### 2.1 本次修复

| 问题 | 后果 | 修复 |
| --- | --- | --- |
| 没人创建 `accel`、`gyro` 节点 | MG 答 false，声明需要这两项的 App 装不上 | `pagani_sensors_hid_node`：在 `pagani_sensors_start` 返回前同步创建 `/product/accel`、`/product/gyro`（`name` 属性和注册表名两份都有，与设备树自身节点一致）。规划函数 `sns_hid_nodes` 有测试：只有模式 3、服务已发布才建。`pagani-sensors-nodes=0` 可关 |
| 静态补丁 `ios-sensors-adt-nodes.diff` 还在 `integrate/` 里 | 日常包会在没有数据源时报“有加速度计” | 改名为 `.do-not-apply`，防止被 `integrate/*.diff` 通配到 |
| 重复写 `DetectionMode` 不补报掩码（backboardd 模式切换后 0.06 s 还会再写一次 3） | 模式切换后 backboardd 拿不到当前遮挡状态 | `sns_core_demand`：接近的重复请求把最后状态再送一次，不发给 hub |
| README 缺失（头文件注释一直指向它） | — | 已补 |

没发现问题的部分：用途对、`Built-In`、`Placement=1`、靠近掩码 `0x40`、事件构造函数的参数顺序（已对照钉死的字节）、`IOHIDEventDriver` 接受属性特征元素（`0xfffffff005af3d84` 要求 type 257、Sensor 页 `0x30E/0x31B`，描述符相符）。

### 2.2 方向服务的崩溃陷阱

现状确认：原驱动没有 `{0x20,0x8A}`，不会踩陷阱。但这也意味着没有旋转，所以做成了默认关的一体件（`pagani-sensors` 的 +8 位）。

逐层核对后，陷阱比原文说的多一层：

| 层 | 事实 | 处理 |
| --- | --- | --- |
| 服务单独出现 | `sendConfiguration` 不判空解引用，backboardd 每次开机崩 | 方向设备在 `IOHIDDevice::start` **之前**就带好全部条件，服务可见时不存在缺一项的窗口 |
| 插件加载 | `IOCFPlugInTypes` 指向 `AppleSPU.kext/PlugIns/AppleSPULib.plugin`；根卷上有该目录和 Info.plist，代码在共享缓存 | 在祖父对象（`PaganiSensorHid`）上设置 |
| **沙盒（新发现）** | `backboardd` 有 `seatbelt-profiles: backboardd`，`locationd` 同样有；配置里认的是苹果的类名。`IOServiceOpen` 被拒，`Start` 失败，一样落到空指针 | 设置 `NativeSandboxGrantAll=true`，走已在 `series` 里的 `zzwc-native-adreno-sandbox-grant-all.patch`，打开用户客户端时跳过 MACF 判定 |
| 用户客户端 | 反汇编确认：`Probe` 恒返回 0；`Start` 只调 `IOServiceOpen(service, 'ashd')`，**没有类型检查**；`Open` 是选择子 4 无参数；`SendCommand` 是选择子 6、1 个标量 | `PaganiSensorSpuClient`：选择子 4、5、6 成功，其余 unsupported |
| vtable | 要多覆盖一个槽 | 槽 140 `newUserClient`，构建脚本按原版 IOService 表核对过；只有方向设备接 `'ashd'`，其余走原版 |
| 事件 | 不新绑构造函数：描述符放 7 个 1 位输入（`0xFF0C/0x64..0x6A`），由原版 `handleDeviceOrientationReport` 在位 0→1 时发事件 | `handleReport`，与 PaganiInput 按键同路 |

`ios-sensors.md` 里“类名已被预链接的 AppleSPU kext 占用”不准确：AppleSPU 不在 13T 的预链接根里。已在原文加注更正。

**没有排除的残余风险**：沙盒的 `iokit-get-properties` 若不让 backboardd 读这个设备的 `IOCFPlugInTypes`，插件创建失败，还是崩。MACF 放行只覆盖打开，不覆盖读属性。这只能上机验证，所以第 7 步要带定时复位。

方向判定（`SensorsOrient.c`，纯整数）：只采信 0.6–1.4 g 的样本（甩动、失重不算）；z 占 0.85 判面朝上下；改变状态要主轴领先 1.3 倍（对角线两侧各约 7° 迟滞）；新状态持续 250 ms 才上报。打开方向后加速度常开，至少 25 Hz。

---

## 3. 依赖缺口

### 3.1 决定

| 依赖 | 决定 | 状态 |
| --- | --- | --- |
| FastRPC 内核层、监听循环、`apps_std`、`apps_mem` | 做 | 已完成，有主机测试，未见真 DSP |
| SMMU（SID 0x1004 等） | 用现成直通规划器，不配页表 | 已完成 |
| 文件来源 | **打包进种子**，不在内核里读 erofs/ext4 | 已完成，等上午导出 |
| SMEM 130/128 | 做，改动在 PaganiAdsp，交补丁 | 已完成，待应用 |
| 屏下光感的显示信息 | **做不了**，消息内容未知 | 见 3.4 |

### 3.2 FastRPC 文件服务

DRV 新增 `SensorsRpc.c/h`、`SensorsPack.c/h`、`PaganiSensorsRpc.cpp`、`scripts/make-sensor-pack.py`。

- **启用**：`pagani-sensors` 的 +4 位。单独跑是 `5`（不带 QMI 客户端，单变量）。
- **流程**：通道 `fastrpcglink-apps-dsp` 打开 → 等 Data 挂载、读包 → SMMU 直通 → 附着 sensors PD（handle 1，消息 pid 0，ctx 低位 2）→ 打开并注册 `adsp_default_listener` → `init2` → `next2` 循环。
- **内存**：`0xd0100000` 起 8 MiB（紧跟扬声器的 `0xd0000000`），XNU 窗口之外、4 GiB 以下，写合并映射。前 2 MiB 走调用，后 6 MiB 给 `apps_mem`。依据与扬声器相同；设备树 `memory` 的 `0xbe360000 + 0x16ac3000` 覆盖这段。
- **地址**：上下文库号放 bits 39:32，默认 4（`compute-cb@2`，`pd-type=3`）。`pagani-sensors-rpc-bank=N` 不用重编就能换。
- **SMMU**：`0x1004` 必需，`0x1084`、`0x1044`、`0x1064` 能配就配。写入推迟到首次附着前，因为规划器没有锁，开机时可能和触摸抢同一个 SMR 槽。
- **没有包也照跑**：每次打开都失败，并把 DSP 要的路径记进日志。这正是第 4 步想要的信息。

反向调用的封送以高通 BSD-3 的 `listener_buf.h`、IDL 和 `apps_std_skel.c` 为准，方法号与 hexagonrpc 的实测子集一致。比 hexagonrpcd 多做了 `fopen`、`fgets`、`ftell`、`flen`、`rewind`、`feof`、`getenv`、`fileExists`、`print_string`。写操作全部拒绝，并记一行。

**写代码时测试抓到的真错误**：等待 Data 的计时写反了（`now|1` 比 `now` 大，无符号相减下溢），会立刻判定“Data 永远不来”。已修。

### 3.3 文件进种子

- 工具：`DRV/scripts/make-sensor-pack.py`，只读 tar，不解包，拒绝不安全路径。
- 位置：`/private/var/ngpu/sensors/pack.bin`。种子工具的 `--sensor-pack` 在 `integrate/make-phone-seed.py.diff`。
- 含本机校准：只留本地，不进仓库。
- 载荷只剩几 MB：默认不打 DSP 动态库。按日志里的 `rpc missing` 用 `--add` 逐个加；`--libs` 全带会超。

### 3.4 屏下光感（明确未做）

`ssc-facts.md` 6.3 的链路是：内核 `/dev/ssc_interactive` → 安卓用户态 → QMI 发给 SSC 里 OPLUS 的算法。**哪个进程、发给哪个 SUID、什么 msg_id，离线都不知道。**

还差的步骤很具体：

1. 上午在安卓下跑 `ssc-facts.md` 附录 B.2 的“OPLUS 屏下光感链路”一组，找出打开这两个设备的进程。
2. `sensor-libs.tar` 带回它的库，离线逆向消息格式。
3. 在 NativeMobileDisplay 侧加亮度、熄亮屏、DC/HBM 状态的出口，由 PaganiSensors 转发。

在此之前，读数可能跟着屏幕自身发光走，亮度可能自激。**止损办法**：`pagani-sensors-als=-`（数据类型写成不存在的），不开流，服务仍在，MG 答案不变。

### 3.5 SMEM 130/128

`integrate/PaganiAdsp-oplus-smem.diff`：在第 4 阶段（第一次写 SMEM 的阶段）于全局分区分配 130 和 128，内容与安卓一致，`mag_fusion` 块的 `u32@8764=6`、`u32@8836=1`，其余为 0。已存在的条目不动，失败只记一行。在副本上打补丁后 PaganiAdsp 主机测试全过（1450918 项，含新增的内容核对）。

没有直接改 PaganiAdsp，因为 adsp-review 正在改这个目录（11:33 还有写入），请在它之后应用。

---

## 4. locationd 有无时，哪些传感器能到 iOS

**更正**：`功能盘点.md` 2.2 把“自动旋转”写成依赖放出 locationd，本次任务描述里也这么写。静态证据不支持：

- CoreMotion 的 `CLOrientationNotifier` 在 **backboardd 进程内**运行（`ios-sensors.md` 2.7）；
- backboardd 自己持有 `com.apple.aop.hid-device.user-client` 下 `orientation` 的 `send-command` 权限，说明真机上也是它直接开 SPU 设备。

`caps & 0x40` 这条分支不经过 locationd。CoreBrightness 同理：iOS 上没有 corebrightnessd，只有 `corebrightnessdiag`，代码在 backboardd 内。

| 功能 | 路径 | locationd 禁用 | locationd 放出 | 还需要 |
| --- | --- | --- | --- | --- |
| 自动亮度 | ALS HID → CoreBrightness（backboardd 内） | 能 | 能 | 屏下补偿（3.4） |
| 通话熄屏 | 接近 HID → backboardd | 能 | 能 | 通话场景本身 |
| 自动旋转 | 内核方向 → `{0x20,0x8A}` → CoreMotion（backboardd 内） | **能** | 能，locationd 也会开同一个用户客户端 | `pagani-sensors` +8 |
| BKS 加速度客户端 | HID → backboardd | 能 | 能 | — |
| `CMMotionManager` 原始加速度 | CoreMotion 进程内 `CLAccelerometer` | **未核实**：App 沙盒是否放行 HID 客户端没查 | 能 | 第 8 步看 |
| `CMMotionManager` 陀螺仪、磁力计 | locationd 的 `CLGyro`、`CLMagnetometer` | 不能 | 能 | — |
| `deviceMotion`、罗盘航向 | 要 SPU 融合报告 `{0xFF0C,1}` | 不能 | **也不能** | 第三阶段（未做） |
| 计步、抬起唤醒 | J120 表没有该能力位 | 不能 | 不能 | 不做 |

注意：身份是 iPhone，主屏不随方向旋转是正常的，要在支持横屏的 App 里看。

---

## 5. 交付物

**DRV 内已改（符号链接进构建树，下次内核构建自动带上）**

- 新增：`SensorsOrient.c/h`、`SensorsPack.c/h`、`SensorsRpc.c/h`、`PaganiSensorsRpc.cpp`、`README.md`、`scripts/make-sensor-pack.py`、`Test/orient.c`、`Test/rpc.c`。
- 修改：`PaganiSensors.cpp/h`、`PaganiSensorsHid.cpp`、`SensorsCore.c/h`、`SensorsHid.c/h`、`SensorsKernel.h`、`scripts/build-pagani-sensors.py`、`Test/{core,hid,stack}.c`、`Test/check-pagani-sensors-host.py`。

**启动参数改成位域**（字节紧，一个开关走到底）：

| 值 | 含义 |
| --- | --- |
| 1 / 2 / 3 | 不变：服务 / +SUID 查询 / +数据流与 MG 节点 |
| +4 | 文件服务：5、6、7 |
| +8 | 方向（要模式 3）：11、15 |

**`integrate/` 待应用**（`patch --dry-run -p1` 都通过）

| 文件 | 内容 | 必需吗 |
| --- | --- | --- |
| `make-phone-seed.py.diff` | `--sensor-pack` | 第 5 步起需要 |
| `make-a830-layout.sh.diff` | `A830_SENSORS` 接受 5、6、7、11、15 | 否则用 `A830_EXTRA_ARGS` |
| `components.toml.diff` | 源文件、依赖、说明 | 不影响编译（`source_files` 只用来找首个 .cpp） |
| `PaganiAdsp-oplus-smem.diff` | SMEM 130/128 | 建议，排在 adsp-review 之后 |
| `PaganiAdsp-clients.diff` | PaganiAdsp 的 README、测试脚本、REQUIRED 符号 | **上次其实没合入**（这三个文件里都查不到），不影响编译，建议补上 |

`integrate/already-merged/`：已确认合入的旧补丁，移出以免重复应用。`ios-sensors-adt-nodes.diff.do-not-apply`：作废。

`ios-sensors.md`、`ssc-facts.md` 已加【评审更正】。

---

## 6. 验证

| 项 | 结果 |
| --- | --- |
| 宿主测试 `Test/check-pagani-sensors-host.py`（ASan + UBSan） | 全过：wire 240009、fixed 1205161、orient 7591、core 1997241、hid 1145、rpc 26164464、stack 4747、qrtr 519、weak 6、PaganiAdsp 自带套件 |
| 内核对象 | 12 个对象零告警，资格检查全过，归档 `sensors/kbuild/libPaganiSensors.a`（sha256 `06d226e54837a779…`）。只编到 `--out`，没有写 `UniXNU/out-gpu` |
| 符号 | 新前缀 `ssr_`、`ssp_`、`sno_` 与现有 `libAdreno830.a` 无冲突；**没做整核链接**，以 integrate 的构建为准 |
| rpc 测试的做法 | 模拟 DSP 与驱动不共享代码，按公开布局独立解析每条消息和元数据；含附着消息的逐字节向量、6 万轮 DSP 数据变异、链路断开重连 |

**主机测试不能证明的**：DSP 真的按这个布局工作，这是第 7 节第 4 步要回答的。

---

## 7. 上午分阶段顺序

规则：每步单变量；镜像带 `pagani-ramoops=1` 和 `pagani-reset-after=<秒>`；用户在场。

**启动参数预算**：命令行真实上限是 1000 字节（`UniXNU/scripts/unixnu/payload.py`，PROGRESS 11:50 条已更正，不是 1023），候选包已用到接近 1000（PROGRESS 记 981，去重后约省 20）。本文要用的开关：`pagani-adsp=1` 14 字节，`pagani-sensors=15` 18 字节，`pagani-sensors-log=2` 22 字节，`pagani-sensors-rpc-bank=3` 26 字节。**每张镜像出包时要核算**，超了就去掉这一轮用不到的开关。

### 第 0 步：安卓只读导出（不重启、不写手机）

跑 `ssc-facts.md` 附录 B.1、B.2，另存 soc0，再做包：

```sh
D=~/unixnu-13t/build-userspace/a830-inputs/sensors/device-dump
adb exec-out "su -c 'cd /sys/devices/soc0 && for f in *; do [ -f \"\$f\" ] && echo \"\$f=\$(cat \"\$f\" 2>/dev/null)\"; done'" > "$D/soc0.txt"
python3 ~/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiSensors/scripts/make-sensor-pack.py \
  --dump "$D" --out "$D/pack.bin" --library-path '<sensors 守护进程 environ 里的 ADSP_LIBRARY_PATH>' --list
```

这一步同时决定：芯片清单、磁力计和气压计是否存在、OPLUS 的 data_type 真名、谁在读 `/dev/ssc_interactive`。

### 第 1 步：`A830_SENSORS=1`（不开 ADSP）

- 看：`[sensors] start mode=1 rpc=0 orient=0`、`services published=0x1f`、5 行 `hid ... bound`。
- 通过标准：正常进桌面，MG `ambient-light-sensor` 为真，`accelerometer` 仍为假（没有节点，符合设计）。

### 第 2 步：ADSP 分段全程通过（adsp 工作流的顺序）

这是后面所有步骤的前提，ADSP 从没在这台手机上跑过。

### 第 3 步：`A830_SENSORS=2`，`pagani-adsp=1`

- 看：`service up node=.. port=..`、`list <类型>` 各行、`discovery ...`。
- 目的：在**没有注册表**时 sensor PD 能起到哪一步。
- 没有 `service up`：看 `[adsp] qrtr services` 和 `[adsp] pd crash report`。

### 第 4 步：`A830_SENSORS=5`，`pagani-adsp=1`（种子**不带**包）

这是最关键的一步，只测 FastRPC，不依赖导出是否完整。

期望顺序：`rpc channel up` → `no file pack (no such file)` → `rpc smmu sid=0x1004 rc=0 ...` → `attached to the sensors PD` → `listening` → `first call of the DSP` → `dsp opens apps_std` → 一串 `rpc missing <路径>` → `rpc quiet ...`。

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| 没有 `rpc channel up` | DSP 没开通道 | 看 PaganiAdsp 的 `channels_up`；核对 GLINK 通道名 |
| `rpc smmu ... rc=-16` | 流已被占，或没有空闲库 | 看 `at=` 的 s2cr；关掉扬声器再试 |
| `attach unanswered` | DSP 没回 | 先试 `pagani-sensors-rpc-bank=3` |
| `attach refused value=X` | DSP 回了错误 | 记下 X，核对 ctx/pid |
| ADSP 崩溃（`[adsp]` 崩溃原因）或 SMMU 故障 | 地址或上下文库不对 | 试 bank=3；看崩溃文本 |
| `remotectl open failed ... dlerr=` | 接口加载失败 | 文本已进日志 |
| 路径是乱码 | 缓存一致性：DSP 看到旧内容 | 这是未验证的假设之一，见第 8 节 |
| `dsp opens X: no such interface here` | 还要别的接口 | 按名字补 |

### 第 5 步：种子带包，`A830_SENSORS=6`，`pagani-adsp=1`

- 准备：`make-phone-seed.py --sensor-pack "$D/pack.bin"`。
- 看：`rpc pack entries=..`、`rpc open /vendor/etc/sensors/sns_reg_config`、`rpc quiet ... missing=N`，然后 `suid accel ...`、`attr accel name=.. vendor=..`、`discovery done found=...`。
- `missing` 不为 0：按日志用 `--add` 补进包，重做这一步。
- 找不到传感器但 `list` 里有 OPLUS 名字：用 `pagani-sensors-als=TYPE` 等指定类型。

### 第 6 步：`A830_SENSORS=7`，`pagani-adsp=1`

- 看：`nodes accel=0 gyro=0`、`stream light on change`、`first light`。
- MG `accelerometer`、`gyroscope` 变真。
- 自动亮度：手电照、遮挡，看 lux 和屏幕亮度。**同时测自激**：暗处调高手动亮度，lux 跟着涨就是屏幕漏光。严重时加 `pagani-sensors-als=-`，等 3.4。
- 轴向：加 `pagani-sensors-motion-hz=25 pagani-sensors-log=2`，看 `first accel`，平放应为 z≈+9.8（hub 单位）。
- 接近：没有通话时 iOS 不会开这个流，加 `pagani-sensors-prox-on=1` 强制开，遮挡传感器看 `pagani-sensors-log=2` 下的 `prox` 行。

### 第 7 步：`A830_SENSORS=15`，`pagani-adsp=1`（方向，**带定时复位**）

- 看：`hid orientation ... bound`、`spu selector=4`、`spu selector=6 command=0x20`，转动时 `orientation state=N`（1 竖 2 倒 3 左 4 右 5 朝上 6 朝下）。
- 在支持横屏的 App 里验证，主屏不转是正常的。
- **backboardd 反复崩溃**：看 X 回溯是否在 `CLOrientationNotifier::sendConfiguration`。若是，说明插件没打开（最可能是 2.2 的读属性沙盒），退回 `7`，这一步之前的成果不受影响。

### 第 8 步：locationd 变体种子 + `15`

验证 `CMMotionManager` 的陀螺仪、磁力计。App 进程内原始加速度在这一步一起看（第 4 节未核实项）。第 7 步之前不需要这个种子。

---

## 8. 未验证的假设与剩余工作

| # | 事项 | 影响 | 何时揭晓 |
| --- | --- | --- | --- |
| 1 | SMMU 直通下 DSP 地址走法：高位库号 + 32 位物理地址，LPASS 取低 32 位 | 错了就 attach 失败或 ADSP 崩 | 第 4 步 |
| 2 | 缓存一致性：设备树的 `dma-coherent` 依赖翻译态页属性，直通下没有；这边用写合并映射，但 DSP 侧是否自行刷新未知 | 路径或数据乱码 | 第 4 步 |
| 3 | sensor PD 只读能否起来（首启要写注册表，`ssc-facts.md` 附录 D.2） | 查不到物理传感器 | 第 5 步 |
| 4 | DSP 动态库体积：载荷只剩几 MB | 包放不下 | 第 5 步 |
| 5 | SMMU 规划器无锁；同时开扬声器时两边都在 ADSP 起来前后写 | 小概率丢映射，配置后回读能发现大部分 | 同时开时留意 |
| 6 | 规划器一流一库、最多试 8 个：UFS、触摸、扬声器 4 个、传感器 4 个 = 10 个，超了 | 后两个可选流配不上，只记一行；只有 DSP 真用到才有影响 | 同时开时 |
| 7 | backboardd 读 `IOCFPlugInTypes` 的沙盒（2.2） | 第 7 步崩溃 | 第 7 步 |
| 8 | 陀螺单位 rad/s、磁力单位 µT 是推断（`ios-sensors.md` 第 7 节） | App 数值不对 | 第 8 步 |
| 9 | sensor PD 重启：PaganiAdsp 不支持子系统重启 | 这次开机内不恢复 | — |

**未做的功能**：

- 屏下光感的显示信息（3.4）；
- `deviceMotion` 与罗盘航向（`{0xFF0C,1}` 的报告布局和命令集还没逆完）；
- 摇一摇（要内核在加速度事件里发 type=1，未做）；
- 抬起亮屏的内核替代（`ios-sensors.md` 2.10，需用户拍板）。

**参考资料**：协议事实取自高通 BSD-3 的 `qualcomm/fastrpc`、`qualcomm/sensinghub`；Linux `fastrpc.c` 和 GPL-3 的 hexagonrpc 只读事实，没有抄代码。下载内容只在本次草稿目录。
