# Pagani 作者的 A830 进度，与我们的 Adreno830 对比（2026-10-31）

范围：`IssacL04-UniXNU-Pagani_SM8750` 的 `cdd273d..origin/main`（`48b93fb`）。只读，没有碰手机，没有改任何源码树。

## 0. 结论摘要

1. 他在真机上跑通了 Metal。系统界面由 GPU 合成，经 GPU 上屏。最后一次真机是 Run 90：389 MHz 请求，28–40 FPS。
2. Run 91（525 MHz、120 Hz 软件节拍、iOS QoS）只做完构建和镜像回读，**没上过机**。提交标题里的 “verified” 只指主机回读。
3. 他的驱动是“上游 Adreno740 + A830 分支判断”，A830 专属代码约 1350 行，加约 560 行改动。我们是独立分叉，约 29000 行。
4. 我们多出来的：中断取栅栏、DCVS、温控、ACD、挂死恢复、GMEM。他都没有。
5. 他多出来的：每进程页表、系统内存 BO（来自上游 parity 分支），且已在同款手机上真机跑通。
6. 硬件事实基本一致：频率表、时钟序列、GDSC、branch HALT、NoC 状态判定、UBWC、保护表都对得上。
7. 唯一值得怀疑我们的一处：ACTLR。他写 `0x32b` 并要求原样读回，真机通过。我们的板级注释说“只有 0x303 留得住”，但本机日志里找不到这个实测。
8. 亮度：他也没做成。他的真机读数是 `DSI disabled r=e00002d8`，随后一直搁置。这条读数可能正好解释我们的亮度无效（推测，要加日志确认）。
9. 音频、WiFi、蓝牙、电池：他的记录全是 planned 或 unsupported，没有可吸收的东西。这些方面我们领先。
10. 抖动：他的固定频率帮不上忙。我们上限 900 MHz，高于他的 525；已有日志里大停顿时 GPU 只忙 14–47%，主因是合成器没交帧（`gap`）。
11. 最值得拿的是 iOS QoS 补丁 `0023`：我们的转译层在 iOS 上这三个 QoS 函数是空实现（读码确认）。
12. 他的 SYSMEM 配置不能抄。那是绕开他 GMEM 花色的手段，而 GMEM 是我们流畅度的来源。
13. 如果合并上游 parity，他的 Run 88 是警告：同一批补丁下 A830 的 GMEM 出现颜色跳变，根因未查明。

吸收清单在第 7 节。

---

## 1. 证据与记号

路径简写：

- `P:`：`/Users/xiaochoumao/unixnu-13t/参考资料/上游-20261031/IssacL04-UniXNU-Pagani_SM8750`
- `B:`：他的纯源码包 `P:.ci-inputs/a830-diagnostics-sources.tar.gz` 解开后的目录（SHA-256 `7cb59618…`，即 Run 91 的源码快照）。本次解在 `/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/pagani-a830/ci-sources`。草稿目录会被清空，重新解包即可。
- `B:new/<n>`、`B:changes/<n>.patch` 的 `n`：0 UniXNU，1 设备仓库，2 sm8750-common，3 Pagani-Drivers，4 Adreno740，5 AdrenoMetal，6 AdrenoFirmware。
- `A:`：`/Users/xiaochoumao/unixnu-13t/Adreno830`

设备仓库只有板级文件。他的 GMU、电源、SMMU、A830 hwinit 都只在这个源码包里，所以硬件对比以 `B:` 为准。`P:.ci-inputs/a830-metal.enc` 是加密的固件输入，未尝试解密。

证据等级：**【他真机】** 他记录的用户读数或观察；**【我方真机】** 我们 `runs/` 里的日志；**【读码】** 我读代码确认；**【主机】** 主机测试或构建检查；**【推测】** 我的推断，未证实。

提交去重：38 个提交里，`7312ba7` 是分支结果在 main 上的一次性副本，`1f0c4a4` 再把分支 33 个提交并进来。实质内容是分支 33 个提交，加 `95d9dad`、`08fff86`、`48b93fb` 三个记录提交。

许可证：设备仓库 `P:LICENSE` 是 BSD-2-Clause-Patent。文件头各不相同：`PaganiGpu.cpp`、`PaganiGpuVotes.c`、`Sm8750GpuSmmu.c`、`Sm8750GpuZap.c`、`Sm8750SocInfo.c`、`NativeAdrenoA830HwInit.cpp` 是 MIT；`Sm8750Gmu.c`、`Sm8750GpuPower.c`、`Sm8750GmuCommandDb.c`、AdrenoFirmware 是 BSD-2-Clause-Patent。他的 `patches/xnu/gpu-user-mappings.patch` 改编自 Droid-VM，其许可证本次没有查。metal2vulkan 是 `GPL-2.0-or-later OR LGPL-3.0-or-later`（我们的副本 `Cargo.toml:19`），QoS 补丁要沿用这个许可证。

---

## 2. 问题 1：他跑通了吗，那些名词是什么

**跑通了。** Run 78 起 GPU 命令和 GPU 上屏都在工作。但他的 `porting.toml` 里 `gpu` 区仍是 `host-verified`，文档状态比实际落后。

帧率全部是用户目测或屏幕计数，没有帧时间分布。着色器正确性、长时间稳定性、发热都没测。

| 版本 | 内容 | 真机结果 | 证据 |
| --- | --- | --- | --- |
| Run 78 | 首次完整链路 | 【他真机】`GPU Metal registered rc=00000000`，`CMD s=1039 c-1039 bad=0 P=518/0`，约十几帧 | `P:STAGE6.md:1269` |
| Run 79 | 诊断版 | 【他真机】`CLK req=222MHz`，最高约 15 FPS | `P:STAGE6.md:1313` |
| Run 88 | 并入上游 parity，389 MHz | 【他真机】约 60 帧，但颜色跳变 | `P:STAGE6.md:1384` |
| Run 88 SYSMEM 对照 | 只加启动参数 | 【他真机】颜色恢复，30–60 FPS，应用开关动画约 20 FPS | `P:STAGE6.md:1390-1392` |
| Run 90 | SYSMEM + 日志修复 | 【他真机】389 MHz，`busy=1`，28–40 FPS，G 耗时 20–50 ms，CPU 拷贝 0，`M map=3/5129` | `P:STAGE6.md:1405`、`P:README.md:9` |
| Run 91 | 性能候选 | **无真机**。构建、签名、回读通过 | `P:STAGE6.md:1421-1429` |

作为对照，我们的 D2 包（`runs/20261031-070836-pkgD2-widgets-wallpaper-1/merged.log`）有 `FPS 118 | ft 8.5 p99 15.6`，GPU 在 607–900 MHz。【我方真机】

逐项名词：

| 名词 | 是什么 | 真机证据 | 只是主机或读码 |
| --- | --- | --- | --- |
| 389 MHz stock 策略 | 从原机 OPP 表选不超过 389 MHz 的最高档，用同档电压和带宽票（level 60，bw 4），开机投一次，之后不变。无调频 | 【他真机】`CLK req=389MHz`。这是 GMU 应答后保存的请求值，不是测频 | 选档代码见 `B:new/1/package/PaganiGpuVotes.c`（Run 90 的包里阈值是 389000000） |
| 525 MHz 性能候选 | 同一段代码，阈值改 525 MHz（level 80，bw 4） | **无** | 【读码】`P:package/PaganiGpuVotes.c:39-44`；【主机】内核里有常量 525000001（`P:STAGE6.md:1425`） |
| SYSMEM profile | 启动参数 `sheng-tu-debug=sysmem,nocb`，强制 Turnip 走系统内存渲染，绕开 GMEM 花色 | 【他真机】颜色恢复（Run 88 对照、Run 90） | 根因未查明，注释写明等 GMEM 验证后恢复（`B:new/1/scripts/prepare-metal-profile.py:56`） |
| 120 Hz software pacing | `sheng-vsync=60` 换成 120，只改软件 vsync 通知节奏，不改面板时序 | **无** | 【主机】`P:scripts/test-stage7.py` 查 120 Hz 定时器和通知；`P:STAGE6.md:1411` 明说不代表 120 FPS 或面板 120 Hz |
| iOS QoS | metal2vulkan 的线程 QoS 函数原本只在 macOS 生效，补丁放开到 iOS | **无** | 【主机】真实 macOS 回归确认完成回调走 user-interactive；【读码】生产插件有 QoS 导入（`P:STAGE6.md:1423-1425`） |
| translator pin divergence | metal2vulkan `development` 分支与固定提交 `624bab39` 分叉，`check-metal2vulkan-pin.py` 第 1 步的祖先断言失败。他保留固定提交，单独验证 24 个补丁精确应用和还原 | 无关真机 | 【主机】`P:STAGE6.md:1415`，提交 `08fff86` |
| `dd148ec` Reuse GPU buffers and bound shared submission backpressure | 提交本身只换源码包。内容有三块：显示侧保留最多 6 个 surface 映射并给稳定编号；Metal 堆 alias 转发；Turnip 提交忙时共用 5 秒截止并让出 100 µs | 映射复用：【他真机】`map=3/5129`，命中涨到约 6000。其余两块无真机证据 | 【主机】堆复用（Run 89–91）、背压回归 |

`P:STAGE6.md:1330` 还写明这些机制是参考依据，不是达到 60 FPS 的证据。

---

## 3. 问题 2：驱动架构

| 方面 | Pagani | 我们 |
| --- | --- | --- |
| 基线 | 上游 Adreno740 `7fa13d4` + `B:changes/4.patch`（30 个文件，约 560 行增删）+ 6 个新文件。A830 是 `chip_id` 分支 | 从 `5c8c145` 分叉（MIT），退役 A740 专属文件，重写为独立组件 |
| 规模 | A830 专属约 1350 行 | 核心与平台约 29000 行（`A:*.cpp *.h`、`A:platform/sm8750`） |
| 代码放置 | GMU、电源、SMMU、ZAP 在 SoC 仓库 `Sm8750Platform`；板级在 `package/PaganiGpu.cpp` | 全在 `A:`，板级 `PaganiGpuBoard.cpp` 只填结构体 |
| 启动 | 一个同步 `start()`，走完一次（`P:package/PaganiGpu.cpp:300-362`），失败即停 | S0–S10 编排，阶段体可重入，供唤醒和恢复复用 |
| HFI | 上游 AdrenoHfi，补了 A8xx TABLE 消息（`B:changes/6.patch`） | 自写 `A:A830Hfi.cpp` |
| 栅栏 | 轮询，中断全屏蔽（`PaganiGpu.cpp:327-328`、`:339`） | 中断 332 |
| 调频 | 无。`native_gpu_dcvs[0]` 恒 0（`PaganiGpu.cpp:44-45`） | `A:A830Dcvs.cpp` |
| 温控 | 无（`P:README.md:106`） | 闭环，目标 95 °C |
| ACD、IFPC | 都关（`P:package/PaganiGpuVotes.c:39`） | 完整包开 ACD，IFPC 有开关默认关 |
| 挂死恢复 | 无。故障后停提交，保留内存到重启（`P:README.md:106`） | `A:A830Recovery.cpp` |
| 用户 BO | 系统内存 wire，无预留池（`P:STAGE6.md:1351`） | 1 GiB 写合并池 |
| 地址空间 | 每进程页表。A830 的 SQE、AQE 映射进每张表 | 单表分客户端窗口，4 GiB 下 12 个槽（`A:tests/run-host-tests.py` 的 `test_userclient` 说明） |
| 页表切换 | 模式 1：GPU 空闲时 CPU 写 TTBR0。他的板级不调 `CP_SMMU_APERTURE`，所以 `tableMode()` 取 1【读码】 | 无切换 |
| 渲染模式 | SYSMEM | GMEM（`TU_DEBUG=nocb`，自动调优） |

他的每进程页表值得记一笔：这是同款手机上已真机运行的路径（Run 88–90）。Gen8 差异已摸清：`CP_SMMU_TABLE_UPDATE` 载荷 3 个字（`B:changes/4.patch` 的 `a830?3:4`），`tableWords()` 6，`resetWords()` 28，SQE/AQE 大小 `0x1c000`/`0x7000`。这能解决我们待办第 18 项的客户端数量上限。但我们的 M14 估算要 2–3 周加 4–5 轮上机（`A:docs/PROGRESS.md:552`），不是小改动。

---

## 4. 问题 3：硬件事实逐项对比

结论先说：**绝大多数一致。** 表里“相同”都是我逐项读码比对过的。

| 项 | Pagani | 我们 | 判断 |
| --- | --- | --- | --- |
| GPU 频率表 | `P:package/PaganiGpuOpps.h` | `A:platform/sm8750/Sm8750GpuLevels.h` | 六组 hz/level/bus 脚本比对全部相同。他去重（`sorted(set())`），我们 bin1 保留 DT 里重复的 1050 MHz，追加时会去重（`A:docs/ARCHITECTURE.md:446`） |
| 他漏的信息 | 只取 `qcom,bus-freq` | 还有 bus-min、bus-max、ACD | 我们更全 |
| GMU 频率、DDR 表 | 500/650 MHz，12 级 | 相同 | 相同 |
| 主机 CX/MX 票 | 取 cmd-db 最大值并保持（`PaganiGpu.cpp:143-149`） | 默认相同（`pagani-gpu-rails=1`） | 相同 |
| 主机 DDR 票 | 取最高行（`PaganiGpu.cpp:319-321`），ACV 用 GMU 编码 BIT(2) | 第 11 档，ACV BIT(0) | 我们符合 Linux 的 HLOS 用法，不改 |
| 关键 CBCR | 0x93a4、0x9008、0x9064、0x93a8、0x9004、0x90cc，每个加 10 µs | 同一组，不加延时 | 都能启动 |
| 等待型分支 | 0x9010、0x90d4、0x90e4、0x93e8、0x90e8、0x7000 | 相同（`Sm8750GpuPower.c:263`） | 相同 |
| branch HALT 与 NoC 状态 | `CLK_OFF` 清零或 NoC FSM(30:28)==2（`B:new/2/kits/Sm8750Platform/Sm8750GpuPower.c:42-46`） | 相同，另接受 HW_CTL（`A:platform/sm8750/Sm8750GpuPower.c:134-139`） | 相同。他 Run 74–76 踩的坑我们没有 |
| CX GDSC | `0x9080` mask `0x00fff007` 值 `0x00228000`，等 `0x9094` bit31，再置 RETAIN_FF | 相同（`:297-299`） | 相同 |
| 复位位已置 | 拒绝（`-16`） | 解除后继续 | 我们更宽，无害 |
| GPLL0 | 投票 0x18000，查 L=31、alpha=0x4000 | 相同，投票有共用锁（`:87-105`） | 相同 |
| PLL0 | L 0x34，alpha 0x1555，配置值一致 | 相同，配置前先停 | 相同 |
| RCG 顺序 | 先 hub 再 GMU（`:115-116`） | 先 GMU 再 hub（`:316`） | 双方都实测通过 |
| GX 电源 | 只在 DTSI 加了 `0x3d68024` 节点，代码没用 | 交给 GMU | 无差别 |
| SMMU 流与 bank | SID 0/1/5；GPU bank 0；GMU 从 bank 2 起动态选；槽位取前三个无效项 | SID 相同；CB0、CB2；优先槽 0/3/1，占用顺延 | 依据相同（厂商 0/1 留给 GPU） |
| **SMMU ACTLR** | 写 `0x32b`，要求读回相等，否则 `-5`（`PaganiGpu.cpp:245`） | 写 `0x303`，回读只记录 | **见下** |
| SCM | `0x42002801` 参数 9，PAS id 13 三步 | 相同 | 相同 |
| SMEM | 只读全局分区，Run 68 在 `0x81D9E000` 同步中止 | 只读 `[0x1000,0x9E000)` 和目录页（`Sm8750SocInfo.h:17-19`） | 他的实测印证了我们的边界 |
| 固件路径 | 备份 super.img 的 vendor 基础段，未应用 OTA（`P:scripts/data/sm8750-gpu-inputs.json`） | 备份的 vendor firmware，`.cache/firmware/pkx110-gpu` | GMU、SQE、AQE 哈希相同 |
| ZAP | `926b145d…`，12088 字节 | `1aa7af12…`，12088 字节 | **内容不同，大小相同**，各自都通过了 PAS。不用动，但别混用 |
| DT 来源 | `d00767f2…` | `13b8b74a…`（ColorOS 16.0.8.300） | 不同抓取，GPU 表一致 |
| GMU 启动寄存器 | CM3_CFG 0x4052、GBIF 0x71e20/0x20023000、QoS 0x33、CHIP 0x08000100 | 相同（`A:A830Regs.h:424-426`、`:512`、`:521`） | 相同 |
| AHB 围栏下界 | 0x8C0（Linux） | 设计配置 0x8A0（KGSL） | IFPC 关时不生效 |
| 依赖票高位 | `0x0FFFC000`（`B:changes/6.patch`） | `0xFFFFC000`（KGSL），`pagani-gpu-hfi` 位 0 可切 | 两边真机都通 |
| HFI 性能表 | A8xx TABLE，消息 15，GX 三字段、CX 两字段 | 相同 | 相同 |
| BW 表 ACV | BIT(2)，10687500 kBps 起 | 相同（`Sm8750Rpmh.h:86-87`） | 相同 |
| hwinit 表 | Linux 变体（`NativeAdrenoA830Regs.h`） | KGSL 变体（`A:A830HwInit.h`） | 差四项：`UCHE_CACHE_WAYS`、`RB_CCU_DBG_ECO_CNTL`、`TPL1_DBG_ECO_CNTL`、`TPL1_DBG_ECO_CNTL1`。我们用的是本机安卓生产表，不改 |
| UBWC | HBB 16，RB_CMP 0x20006 | 相同（`A:A830Regs.h:404-408`） | 相同 |
| 保护与中断掩码 | 0xFFFF000B、0x07E83F7F、0x77F、0x007F007F | 相同 | 相同 |
| HWCG | 0x702、0x20000、0x10111、0x5555 | 相同 | 相同 |
| 时间戳 | CP 计数器 0x8e7 | GMU AO 计数器 | 都可用 |
| 忙闲读数 | GMUAO `0x23b0c` bit23，单次采样 | XO 计数器 | 我们能算百分比 |

### 4.1 ACTLR：我们的注释可能错了

- 他的代码写 `0x32b` 到 GPU bank 和 GMU bank，读回不等就返回 `-5`。【读码】这段在 Run 78 和 Run 90 的源码包里都在，且都走到了 `GPU Metal registered`。【他真机】所以 PKX110 上 `0x32b` 能写入并读回。
- 我们的 `UniXNU-Pagani_SM8750-gpu/package/PaganiGpuBoard.cpp:72` 注释写“measured … only 0x303 sticks under the hypervisor”。但 `A:docs/ARCHITECTURE.md:93` 的依据是 annibale（K90）“0x303 可写回”，不是本机。我在 `runs/` 和 `PROGRESS.md` 里没找到本机写 `0x32B` 的记录。
- 我们自己的运行设备树也是 `qcom,actlr = <0x00 0x3ff 0x32b>`（`build-userspace/a830-inputs/dt/running.dts:24150`）。
- 差异位是 bit3 和 bit5。凭记忆这是 GFX 切换时的 TLB 预取设置，本地没有 Linux 源码可核对。【推测】
- 我们的 `pagani-gpu-actlr` 是现成开关。一轮 `pagani-gpu-actlr=0x32b` 就能看读回值和性能。不要先改默认值。

### 4.2 其他小差异

- `A:platform/sm8750/Sm8750GpuPower.h:16` 写 `GPU_CC_CXO_AON_CLK` 读到 `0x80000000`，他抄录的是 `0x08000000`（`P:STAGE6.md:1168-1173`，用户手抄 7 位十六进制，他也注明“按用户文本保留”）。两者不矛盾到影响行为：我们的 `critical()` 不轮询这个位。
- 他的 OPP 选择有个小缺陷：`{3u,0u}` 这组因 `sku[1]==0` 跳过 SKU 过滤（`P:package/PaganiGpuVotes.c:12`），未知速度档会落到 1200 MHz 表。他只用 525 MHz 以下，没影响。我们按 KGSL 规则匹配，不存在这个问题。
- UEFI：他的内存表必须是一整段 `0x03D00000/0x100000`，切成小段时 `AddMemorySpace` 冲突，ClockDxe 会在纯偏移地址上出错（`P:STAGE6.md:1086-1094`）。我们现在没改这张表。以后改时要守这条。
- 实机速度档两边都没有记录。我们默认只用各档共有的 11 档，所以不影响现状。

---

## 5. 问题 4：亮度、音频、WiFi、触控、电池

| 功能 | 他的状态 | 证据 | 对我们 |
| --- | --- | --- | --- |
| 亮度 | 没做成。Build 50 真机显示 `DSI disabled r=e00002d8`，之后一直“暂缓” | `P:STAGE6.md:677`、`:816`；`P:porting.toml` display 区 limits | 见下 |
| 音频 | `planned`，无组件 | `P:porting.toml` audio 区 | 我们领先（PaganiAdsp、PaganiAudio、PaganiSpeaker） |
| WiFi、蓝牙 | `planned`，无组件 | network 区 | 我们领先（PaganiWifi） |
| 触控 | S3910 轮询，基本可用，本轮没改 | input 区 limits | 我们是在这份基础上继续改的 |
| 电池、充电、休眠 | 不支持 | power 区 limits：`Battery, charging, sleep ... are unsupported` | 我们领先（PaganiBattery） |
| CPU 调频 | 未验证 | power 区 findings | 我们有 `pagani-cpufreq=2` |
| 小组件 | 无专项。只把 `WidgetRenderer_Default` 列为 Metal 客户端 | `PaganiGpu.cpp:296`；读数里 `who=WidgetRendere_D`（`P:STAGE6.md:1269`） | 打开过 GPU，不等于画对了，没有可用结论 |

结论：这些方向他没有可吸收的东西。他的 `drivers/` 只有 14 个目录，没有上面这些驱动。

### 5.1 亮度：他留下了一条有用的线索

- 他的读数 `DSI disabled r=e00002d8` 就是 `kIOReturnNotReady`。这个错误来自 `PaganiPanel::start`：读 DSI `+4`，bit0 为 0 时上报 `DSI disabled`，然后 `cleanup()` 并返回 false，不发布 backlight 服务（`UniXNU-Pagani-Drivers/drivers/PaganiPanel/PaganiPanel.cpp` 的 `start()`）。
- 这段判断我们原样继承。【读码】
- 服务没发布时，亮度滑块没有接收方。这和用户说的“亮度调整不能用”对得上。【推测】
- 我们看不到这条读数：`observe()` 只写叠加层，完整包是 `pagani-overlay=0`；`build-userspace/a830-inputs/inventory/evidence.md:78` 也记着“没有 `panel:` 日志行”。
- DSI 基址没错：13T 面板 `qcom,dsi-ctrl-num = <0x00>`，控制器 `0xae94000`（`running.dts:41226`、`:41887`）。
- 但画面明明在刷新，bit0 却读到 0，原因未知。可能是读的时机或时钟门控。【推测】
- 第一步：把 `observe` 的阶段和读到的 `control` 值写进 ramoops。一包就能判定。这是自写的小改动，不来自他。

---

## 6. 问题 5：对抖动有没有帮助

### 6.1 频率上限比较

| | Pagani | 我们（完整包） |
| --- | --- | --- |
| 上限 | 固定 389（已测）或 525（未测） | 900 MHz，ACD 未确认时 734（`A:A830Dcvs.cpp:86-93`） |
| 下限 | 同固定值 | 160 MHz（`A:A830Dcvs.h:73`） |
| 策略 | 开机一票 | 窗口 16 ms 或两个 TE；忙 ≥90% 升两档；空闲 64 ms 回下限；首提交 525 MHz 保持 50 ms；触摸 607 MHz 保持到松手后 300 ms |
| 温控 | 无 | 目标 95 °C，地板 525 MHz，105 °C 紧急 389 MHz |
| 启动参数 | `sheng-vsync=120`、`sheng-tu-debug=sysmem,nocb`、`sheng-gpu-sample=0` | `pagani-gpu-profile=0 pagani-gpu-acd=1 pagani-gpu-pool=1024 pagani-cpufreq=2 sheng-vsync=120 sheng-vsync-fixed=1`（`测试镜像/boot-A830-完整包.payload.json`） |

**提高上限帮不上忙。** 我们的上限已经高于他。

【我方真机】D2 包里，FPS 掉到 29 的那一秒是 `SLOW 468ms gap 457 … copy 12.7`，同时 `GPU RUN 900M b14`（`merged.log` 序号 4885–4887）。下一秒 `SLOW 274ms gap 268`，`b23`。GPU 没忙满，帧也没卡在拷贝上。同目录 `app-launch-jitter.md` 用更多轮日志得出同样结论。注意 D2 的 CPU 调速器中途死过，这组数字不能代表完整包，而完整包的日志两区全丢。

### 6.2 他的方案里什么有用

**有用：iOS QoS（`B:new/5/bridge/patches/0023-quality-of-service-on-ios.patch`）。**

- 【读码】我们的 metal2vulkan（`720ef1c` 加补丁，`build-userspace/tree/.cache/gpu-reference/metal2vulkan`）里，`exit_guard.rs:767/778`、`:783/904`、`:909/925` 三个函数都按 `target_os = "macos"` 分支。iOS 上 `raise_to_user_initiated` 和 `run_at_user_interactive` 是空函数，`at_least_user_initiated` 恒真。
- 影响面：管线编译线程（`pipeline/pool.rs:445`）和把命令缓冲送到回调的线程。
- 【主机】他在 macOS 上验证了回调线程的 QoS，并确认生产 iOS 构建能链接。**没有 A830 真机数据。** 收益大小未知，不要预期很大。
- 代价：他的行号对不上，要手改 3 个函数 6 处 cfg；运行时变了要重做着色器包（`app-launch-jitter.md` 6.5）。

**有点用：固定频率的那一面，即“没有低频下限”。** 他没有从 160 MHz 爬坡的过程。我们不用改代码就能试：`pagani-gpu-dcvs-min=389` 或 `525`。代价是空闲发热，他没有任何功耗数据可参考。

**待验证：Turnip 惰性内存。** 他的 Turnip 补丁有 `device->has_lazy_bos = true` 和 `darwin_sparse_vma_init`（`B:changes/0.patch`）。上游注释称 12 张 RGBA16F 无内存纹理省下 758 MiB。我们是 `has_lazy_bos = false`（`UniXNU/patches/mesa/turnip-darwin-kmd.patch:1480`）。这能减少 render server 的 BO 分配。但 13T 上的实际节省和抖动改善都没测过，而且它和 Run 88 的 GMEM 花色在同一批改动里，要单独验证。

**没用的：**

- 上屏映射复用：我们已有 `NativeGOPMappingCache.h`。
- 提交背压：我们的 `darwin_wait_for_room` 已有 5 秒上限，还处理了旧栅栏（`turnip-darwin-kmd.patch:1188-1250`）。
- 关采样线程：我们的 `NativeBridgeMetalFrontend.mm:117-119` 默认就关。但如果升级 AdrenoMetal，上游中间版本默认每 2 ms 暂停渲染线程，要用到 `fc5ca0b` 以后。
- `0024` 诊断开关：我们的转译层版本没有 `stage_trace.rs`，不适用。
- 120 Hz 软件节拍：我们已经是。
- 4 Hz 差量绘制日志带：完整包关了叠加层。

### 6.3 顺带发现：DCVS 的一处策略瑕疵

这不是他的东西，是比较时读出来的。`A:A830Dcvs.cpp:392` 用 `demand>current` 判断升档，`current` 是被提频抬高后的频率。触摸保持期间，如果忙度不到 70%，`policy_` 不会跟上实际需求。保持一结束，频率跌回 `policy_`。

用真实的 `A830Dcvs.cpp` 做了主机模拟（程序在草稿目录 `.../upstream-merge/pagani-a830/dcvs-sim/dip.cpp`）。设定：设计配置、ACD 开、空闲后点按、恒定负载 800 ms。

| 负载（MHz 当量） | 现状 | 改成 `demand>khz(policy)` | `pagani-gpu-dcvs-min=525` |
| --- | --- | --- | --- |
| 250–300 | 只有首窗口欠算 | 同左 | 无欠算 |
| 350 | 提频结束时 1 个窗口欠算 2.2% | 无 | 无 |
| 400 | 提频结束时 1 个窗口欠算 14.4%（频率 607→342→660） | 无 | 无 |
| 450–600 | 只有首窗口欠算 | 同左 | 525 及以下无欠算；550、600 只有首窗口 |

如实评价：**影响很小。** 只有约 343–430 MHz 当量的负载才会触发，最多少算一个 16 ms 窗口。首窗口欠算（300 MHz 当量时 46.6%）在模拟里偏重，因为模拟每 16 ms 才看一次，真机上 `noteSubmit` 会马上唤醒服务线程。模拟不含投票延迟。这条排在 QoS 和 CPU 侧之后。

---

## 7. 值得吸收的清单

| 优先级 | 项 | 来源 | 改动 | 证据等级 | 怎么验证 |
| --- | --- | --- | --- | --- | --- |
| P0 | iOS QoS 补丁 | `0023` | metal2vulkan `exit_guard.rs` 手改；重做用户态和着色器包；不动内核 | 读码确认我们缺；他主机通过；无真机 | 下一包与现有版本比 F/J 行，加 QoS 探针 |
| P0 | 亮度诊断 | 他的 `DSI disabled` 读数 | `PaganiPanel` 把 `observe` 写进 ramoops，带 `control` 值 | 他真机一次；我们未知 | 看日志是否出现 `DSI disabled` |
| P1 | ACTLR `0x32b` 单变量 | `PaganiGpu.cpp:245` | 只加启动参数 `pagani-gpu-actlr=0x32b` | 他真机通过；性能收益未知 | 看读回值、页故障、帧时间。稳定后再改默认，并修正 `PaganiGpuBoard.cpp:72` 的注释 |
| P1 | GPU 下限单变量 | 他的固定频率思路 | 只加启动参数 `pagani-gpu-dcvs-min=389` | 模拟有效；真机未知 | 看开应用手感、温度 |
| P1 | `gpuCoreCount` | 上游 `0f11e64`；他设为 6（`B:changes/5.patch`） | `NBDeviceMethods` 加一个方法；A830 返回 6 | 上游注释称没有它就没有 MPS 设备；是否影响小组件未知（推测） | 看 MPS 相关崩溃、小组件 |
| P2 | Turnip 惰性内存 | `159eb82`，他的 `turnip-darwin-kmd.patch` | Mesa 补丁，单独一轮 | 上游称省内存；A830 无单独验证 | 看 MEM 行 backboardd 用量和颜色 |
| P2 | DCVS `demand` 判断 | 比较时发现 | `A830Dcvs.cpp:392` 一行，改测试 | 模拟确认，影响小 | `test_a830dcvs` 加用例 |
| P2 | Metal 堆 alias 转发 | `B:changes/5.patch`，MIT | 前端和 bridge 约 100 行 | 仅主机 | 真机效果未知，排后 |
| P3 | 每进程页表、系统 BO | 上游 parity，他的 A830 适配 | 大改，见第 3 节 | 他真机 Run 88–90 | 客户端不够用时再做 |

**不要吸收：**

- SYSMEM 配置。这是他绕开花色的手段。我们开 GMEM 时用户评价“质变”（`A:docs/PROGRESS.md` 10-30 12:06 条）。
- 固定 OPP、无 DCVS、无温控、栅栏轮询。这些都是他还没做到的部分，比我们现状差。
- 他的 hwinit Linux 变体。我们用本机安卓生产表，没有理由换。

**如果合并上游 parity，他的踩坑记录可以直接用：**

1. 内核要有 `native_pmap_user_mappings` 和 `native_gpu_dcvs`，否则最终链接失败（`P:STAGE6.md:1366`）。
2. 上游构建脚本引用的 `0023`、`0024` 在 `fc5ca0b` 里缺失，他是本地补的（`P:STAGE6.md:1407`）。
3. 提交背压补丁要排到 Mesa 序列末尾，否则和 GMEM 补丁的计时改动冲突（`P:STAGE6.md:1368`）。
4. `patchFramebufferBranch` 要加 `__arm64e__` 条件，否则 iOS 17 编译因未使用函数失败（`P:STAGE6.md:1370`）。
5. metal2vulkan 按提交号取，不要按分支；`624bab39` 已不在 `development` 历史上（`P:STAGE6.md:1415`）。
6. `gpuCoreCount` 上游固定 8，A830 应为 6（`P:STAGE6.md:1353`）。
7. **Run 88 的 GMEM 颜色跳变根因没查明。** 上游 `turnip-gmem-mutable-swap.patch` 改了 GMEM 的 load/store 交换逻辑，它是嫌疑之一（推测）。我们现在 GMEM 没这个问题。合并时这一项必须单变量验证，否则可能丢掉最大的性能来源。

---

## 8. 还不知道的

- 525 MHz 下他的实际帧率、发热：要等他测。
- 他的 Run 88 花色根因。
- 我们的亮度是否真走 `DSI disabled`，以及为什么 bit0 读到 0。
- QoS 在 A830 真机上的收益。
- 本机写 `0x32b` 后的读回值，和它对帧时间的影响。
- 实机速度档。两边都没记录。
- 我们的 `CP_SMMU_APERTURE` 结果：`runs/` 里没有找到 `gpu aperture` 行。这决定每进程页表能否走 CP 切换。
