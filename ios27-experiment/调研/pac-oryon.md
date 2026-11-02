# Oryon 的 PAC 和 arm64e 特性核查

时间：2026-11-01。手机在安卓，只做只读查询，没有写任何文件。原始抓取在 `调研/raw/`。

证据等级：**【实测】** 本次在手机或电脑上跑出来的；**【读码】** 读源码确认，给出位置；**【推断】** 由前两者推出，没有直接测过。

## 结论

**PAC 这条线通过，27 没有被硬件卡死。** 官方 `docs/zh/porting/arm64e.md` 的五条要求里：

| 要求 | 结果 | 证据 |
| --- | --- | --- |
| 每个启用的 CPU 实现 FEAT_PAuth，含地址密钥和通用密钥 | **满足** | 【实测】八核在线，`Features` 有 `paca pacg`；【读码】Linux 要求副核 PAC 级别与启动核一致，否则停住该核 |
| XNU 在 EL1，五组密钥寄存器可写且不陷入 | **基本确定，未直接测** | 【读码】Gunyah 把 `HCR_EL2.APK`、`API` 置 1；【推断】SM8750 固件带这个宏编译 |
| PAC 指令在 EL1 和 EL0 不陷入，每核往返正确 | **基本确定，未直接测** | 同上，加【实测】安卓内核开着 `CONFIG_ARM64_PTR_AUTH_KERNEL=y` |
| 已有 SoC 支持 | 满足 | 【实测】17.7.11 已在这台手机上跑 |
| 原生 4 KiB 一级翻译 | 满足 | 【实测】同上 |

还没有在这台手机上直接测过的只有两件事：EL1 上真的做一次 PACIA/AUTIA 往返，和具体算法、FPAC 级别。算法不影响 arm64e，官方文档写明签名都在本机生成【读码，`arm64e.md:47-49`】。第一次启动 27 内核时密钥设置在极早期运行，失败会立刻显形，所以这不是必须先做的独立步骤。想提前确认，见 `probes/README.md`。

## 实测数据

设备：`OP60F5L1`，平台 `sun`，内核 `6.6.89-android15-8`，构建 `V.19749a3_aaad6b_a9b132`。

`/proc/cpuinfo` 八个核的 `Features` 行完全相同：

```
fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc
dcpop sha3 sm3 sm4 asimddp sha512 asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp flagm2
frint i8mm bf16 rng bti ecv afp rpres
```

| 核 | `midr_el1` | 备注 |
| --- | --- | --- |
| 0 到 5 | `0x514f0014` | implementer 0x51，variant 4，part 0x001，rev 4 |
| 6、7 | `0x513f0014` | 同上，variant 3 |

内核配置：`CONFIG_ARM64_PTR_AUTH=y`、`CONFIG_ARM64_PTR_AUTH_KERNEL=y`、`CONFIG_ARM64_BTI=y`、`CONFIG_GUNYAH=y`。内核命令行没有 `arm64.nopauth`。

**没查到的：** 启动早期的 `CPU features: detected` 行已经滚出日志缓冲区（`dmesg` 最早到 2.1 秒，开机 9 分钟）；`/proc/kcore` 没开；`kptr_restrict=2`，`/proc/kallsyms` 全是 0；debugfs 没挂。改这些要写手机，所以没动。

## 为什么“八核都有”成立

`/proc/cpuinfo` 的 `Features` 行来自全局能力，不是逐核读寄存器，不能直接当逐核证据【读码，Linux v6.6 `arch/arm64/kernel/cpuinfo.c` 的 `c_show`】。

真正的依据是 Linux 的能力判定【读码，Linux v6.6 `arch/arm64/kernel/cpufeature.c:2051-2076`】：

- `paca` 对应 `ARM64_HAS_ADDRESS_AUTH_*`，类型是 `ARM64_CPUCAP_BOOT_CPU_FEATURE`。
- 副核要满足 `sec_val >= min && sec_val == boot_val`，直接读本核寄存器比较。源码注释写明“a mismatched secondary CPU is parked”。

手机上八个核都在线【实测】，所以八个核的 PAC 级别与启动核相同【推断，强】。

## 为什么判断 EL1 不陷入

Gunyah 公开源码（`quic/gunyah-hypervisor`，`develop` 分支 `254727746fce`，2026-05-29）【读码】：

- `hyp/vm/vcpu/aarch64/src/aarch64_init.c:179-184`：定义 `ARCH_ARM_FEAT_PAuth` 时 `HCR_EL2_set_APK(true)`、`HCR_EL2_set_API(true)`。这正是官方文档说的两个位。
- `hyp/vm/vcpu/aarch64/src/aarch64_init.c:142`：`TID3` 置 1，客户机读 ID 寄存器都陷入 EL2。
- `hyp/vm/vcpu/aarch64/src/sysreg_traps.c:289-333`：`ID_AA64ISAR1_EL1` 从全 0 开始白名单填写，`APA`、`API`、`GPA`、`GPI` 只有在同一个 `ARCH_ARM_FEAT_PAuth` 下才从硬件拷过来。`ID_AA64ISAR2_EL1` 的 `APA3`、`GPA3` 同理（`:340-344`）。
- `hyp/vm/vcpu/armv8-64/src/pauth.c`：逐 vCPU 保存恢复 DA、DB、IB、GA 四组密钥；注释说 EL2 只用 APIA，在 EL2 进出时切换。密钥由虚拟机自己管。

推理链：安卓看到 `paca pacg` → 这些字段没被 Gunyah 清掉 → 固件定义了 `ARCH_ARM_FEAT_PAuth` → 同一个宏把 `APK`、`API` 置 1 → EL1 写密钥和执行 PAC 指令都不陷入【推断】。

唯一的折扣是公开源码不等于 SM8750 上的固件。旁证有两条：

- sheng 已经在同族 Gunyah 下跑通 27【读码，上游 `sheng boots iPadOS 27 24A446`，`e9865b0`】。
- **更接近的一条：** DroidVM 的 arm64e 内核在骁龙 8 Elite Gen 5（SM8850，同样是 Oryon）的 Gunyah 客户机里起到 AppleImage4，再到 launchd 和 SpringBoard【读码，第三方自述，`Droid-VM/UniXNU-droidvm` 的 `cd71f02c`、`b56ce8ce`】。这是 Oryon 系列加 Gunyah 加 arm64e 的直接运行记录，只是 SoC 差一代，而且它是 crosvm 客户机，13T 上是 HLOS 虚拟机。两者走的是 Gunyah 同一段 vCPU 初始化代码【读码，`aarch64_init.c`】。

XNU 会走到的其他陷入都不是新问题：`TACR` 陷 `ACTLR_EL1`，`TIDCP` 陷实现定义寄存器，`TSC` 陷 SMC，`TWI` 陷 WFI，这些 17.7.11 上已经面对过【读码，`aarch64_init.c:137-146`】。

## 与 sheng 的差异

| 项 | sheng（SM8550） | 13T（SM8750） | 影响 |
| --- | --- | --- | --- |
| PAC | 有 | 有 | 无 |
| SME | 无（官方记录） | 无【实测】 | 同 sheng。26.5.2 的 dyld 缓存里有 SME 代码（官方记录 2495 条 SMSTART），sheng 没有 SME 也进了桌面，说明运行时按能力选择【推断】 |
| SVE | 有（Cortex-X3 系）【推断】 | 无【实测】 | Apple 不用普通 SVE【推断】 |
| 拓扑 | E + P 两簇，Edge 调度 | 2+6 全 Oryon，Clutch 单 pset | 见 `调研/xnu-12377.md` |
| MIDR | Arm 核 | Qualcomm 0x51 part 0x001 | 12377 里按 MIDR 分支的代码要查，BCM2837 配置下多半不走【推断】 |

13T 的 ISA 特性覆盖了 Apple A12 基线要用到的东西：`atomics`、`lrcpc`、`ilrcpc`、`jscvt`、`fcma`、`paca`、`dcpop`、`sha512`、`sha3`、`asimddp`、`asimdfhm`、`bf16`、`i8mm`、`flagm2`、`frint`、`sb`、`ssbs`、`dcpodp`、`bti` 都在【实测】。

## 残留风险

1. 这颗 Oryon 的 PAC 算法和 VA 位配合没人验证过。XNU 的 PAC 位宽由 TCR 决定，上游只在 Cortex 系上跑过。
2. 12377 的 PAC 路径在 SM8750 上没运行过。第三方在 SM8850 的 Oryon 上跑过（见上）。`native-pac-keys.patch` 每次启动生成一组随机密钥，写入每个核，这一步在 13T 上第一次执行就是里程碑 N1。
3. 官方文档说全部进程共用一套密钥，PAC 不做进程隔离【读码，`arm64e.md:54-56`】。这是实验路线，可以接受。
