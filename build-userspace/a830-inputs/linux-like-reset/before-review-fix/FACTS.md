# 13T 复位类型（WARM/HARD）相关事实核对（2026-10-31）

只记事实与出处；GPL 源码只读不抄。原始文件在 `src/`（`sources.txt` 有每个文件的 URL 与 sha256 前缀）。
简写：
- 13T = `OnePlusOSS/android_kernel_oneplus_sm8750` 分支 `oneplus/sm8750_b_16.0.0_oneplus_13t`（手机实测内核 6.6.89-android15，安卓 16）
- DT = `OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750` 同名分支
- UP = `torvalds/linux` master（上游，对照用）
- TFA = `ARM-software/arm-trusted-firmware` master（谷歌的 QTI 参考 EL3，不是手机上的 TZ）
- GH = `quic/gunyah-hypervisor` develop

## 1. SCM 调用（13T 的 drivers/firmware/qcom_scm.c / qcom_scm.h / qcom_scm-smc.c）

调用约定（qcom_scm-smc.c 210-229、38-55、174-184）：x0 = ARM_SMCCC_CALL_VAL(类型, SMC64, owner, svc<<8|cmd)，x1 = arginfo，x2..x5 = 参数；
“atomic”调用 = 快速调用（bit31=1），不取锁、不睡眠，x0 == 1（INTERRUPTED）时以 x0=1、x6=返回的 x6 重发；结果 x0 非 0 即错误（按 int 重映射），res.result[0] = x1。

| 用途 | 函数 | svc/cmd/owner | 完整功能号 | arginfo | 参数 | 出处 |
|---|---|---|---|---|---|---|
| 关 SDI | qcom_scm_disable_sdi | BOOT 0x01 / WDOG_DEBUG_PART 0x09 / SiP | 0xC2000109（快速） | ARGS(2)=0x2 | (1, 0) | qcom_scm.c 494-513；qcom_scm.h 99、105 |
| 停 SPMI PMIC 仲裁器 | qcom_scm_halt_spmi_pmic_arbiter | PWR 0x09 / IO_DISABLE_PMIC_ARBITER 0x01 / SiP | 0xC2000901（快速） | ARGS(1)=0x1 | (0) | qcom_scm.c 1087-1109（注释：PS_HOLD 在 SPMI 事务中途拉低可能锁死某些 PMIC）；qcom_scm.h 138-139 |
| 写下载模式（有 dload 地址时） | qcom_scm_set_download_mode → qcom_scm_io_writel | IO 0x05 / WRITE 0x02 / SiP | 0xC2000502（快速） | ARGS(2) | (地址, 值) | qcom_scm.c 563-582、1021-1035；qcom_scm.h 129-132 |
| 读下载模式 | qcom_scm_get_download_mode → qcom_scm_io_readl | IO 0x05 / READ 0x01 / SiP | 0xC2000501（快速） | ARGS(1) | (地址)，值在 x1 | qcom_scm.c 584-601、1001-1019 |
| 写下载模式（无 dload 地址时才用） | __qcom_scm_set_dload_mode | BOOT 0x01 / SET_DLOAD_MODE 0x10 / SiP | 0xC2000110（快速） | ARGS(2) | (mode, 0) | qcom_scm.c 548-561 |
| 查询调用是否存在 | __qcom_scm_is_call_available | INFO 0x06 / IS_CALL_AVAIL 0x01 / SiP | 0x42000601（**标准/可让出**调用） | ARGS(1) | (SiP<<24 \| svc<<8 \| cmd)，x1≠0 即有 | qcom_scm.c 326-353 |
| 热重启 | qcom_scm_reboot | OEM_POWER 0x09 / OEM_POWER_REBOOT 0x22 / **OEM（owner 3）** | 0xC3000922（快速） | 0 | 无 | qcom_scm.c 2149-2158；qcom_scm.h 205-207 |
| 定制关机（rtc/twm） | qcom_scm_custom_reboot | OEM_POWER 0x09 / 0x23 / OEM | 0xC3000923 | ARGS(1) | 0x80000005/0x80000006 | qcom_scm.c 2160-2173；include/.../qcom_scm.h 84-87 |

下载模式值（include/linux/firmware/qcom/qcom_scm.h 26-31）：NODUMP 0x00、EDL 0x01、FULLDUMP 0x10、MINIDUMP 0x20；13T 厂商实现整字写入（io_writel），不是上游的读改写。

下载模式寄存器地址：DT `sun.dtsi` 283-286 `qcom_scm { qcom,dload-mode = <&tcsr 0x19000>; }`，378-386 `tcsr: syscon@1fc0000 reg <0x1fc0000 0x30000>` → **0x1FD9000**（qcom_scm.c 2473-2497 按 phandle 基址 + 偏移计算）。
IMEM（sun.dtsi 1419-1440，基址 0x14680000）：`dload_type@1c`（下载目的地 QPST=0/EMMC=2，qcom-dload-mode.c 只在 sysfs emmc_dload 写，且是直接写 IMEM 不经 SCM）、`restart_reason@65c`。sun 上**没有**旧式 IMEM 下载魔数节点（`qcom,msm-imem-download_mode`），下载 cookie 只在 TCSR。

## 2. 安卓重启的完整顺序（13T 6.6）

1. `kernel_restart(cmd)` → `kernel_restart_prepare`：先跑 reboot 通知链，再 `device_shutdown()`（kernel/reboot.c 85-91、269-281）。
2. reboot 通知（优先级 255）：
   - `qcom_dload_reboot`（qcom-dload-mode.c 315-335）：置 in_reboot，`set_download_mode(NODUMP)` → SCM IO 写 0x1FD9000 ← 0；cmd 为 "edl" 设 EDL，为 "qcom_dload" 重新开转储；**只有当前下载模式非 NODUMP 时才把 reboot_mode 设成 REBOOT_WARM**。
   - `qcom_reboot_reason_reboot`（qcom-reboot-reason.c）：cmd 非空时把原因字节写进 PMIC SDAM 的 nvmem 单元 "restart_reason"（bootloader=0x02，recovery=0x01，OPLUS 加了 0x21-0x3E；未知 cmd 写 "other" 0x3E）。DT：sun-pmic-overlay.dtsi 引用 pmk8550.dtsi 的 `restart_reason: restart@48`。
3. `device_shutdown` 中 `qcom_scm_shutdown`（qcom_scm.c 2835-2843）：`qcom_scm_disable_sdi()`（0xC2000109(1,0)）→ `qcom_scm_halt_spmi_pmic_arbiter()`（0xC2000901(0)）→ 若 qcom_scm 自己的 download_mode 参数为真再清一次下载模式。
4. `machine_restart`（arch/arm64/kernel/process.c 128-149）：`local_irq_disable()` → `smp_send_stop()` → （有 EFI 运行时服务才走 efi_reboot）→ `do_kernel_restart`。
   - `smp_send_stop`（arch/arm64/kernel/smp.c 1008-1032）：IPI_CPU_STOP 给其余在线 CPU，最多等 1 秒；对方 `local_cpu_stop`（821-828）：下线、屏蔽 DAIF、`cpu_park_loop`（wfe/wfi 死循环，**不调 PSCI CPU_OFF**）。
5. restart 处理链（按优先级从高到低）：
   - 201 `qcom_dload_restart`（qcom-dload-mode.c 301-313）：只在“既非 panic 也非 reboot 通知路径”（紧急重启）时关 SDI + 清下载模式。
   - 130 `qcom_scm_do_restart`（qcom_scm.c 2509-2529，注册在 probe）：**reboot_mode == REBOOT_WARM 时调 OEM 热重启 0xC3000922**；cmd 为 "rtc"/"twm" 时走定制关机。
   - 129 `psci_sys_reset`（drivers/firmware/psci/psci.c 320-341）：WARM/SOFT 且支持 SYSTEM_RESET2 时调 SYSTEM_RESET2 类型 0，否则 **SYSTEM_RESET 0x84000009**。
6. reboot_mode 默认 REBOOT_COLD；只有 `reboot=` 命令行、`/sys/kernel/reboot/mode` 写 "warm"（kernel/reboot.c 1079-1141）或上面的 dload 驱动会改成 WARM。手机 /proc/cmdline 没有 reboot=。
   → `adb reboot bootloader`：dload 被清成 NODUMP，reboot_mode 仍是 COLD（除非 /sys/kernel/reboot/mode 被用户态写成 warm——**未在手机上核对**），最终调用的就是 **PSCI SYSTEM_RESET**，和 XNU 的最终调用相同。

## 3. 谁决定 PMIC 的 PS_HOLD 复位类型

- 13T 内核**不写** PS_HOLD 复位类型：
  - `drivers/input/misc/qpnp-power-on.c` 498-698（`qpnp_pon_reset_config`、`qpnp_resin_pon_reset_config`、`qpnp_pon_system_pwr_off`）与 784-798（`qpnp_pon_wd_config`）包在 `#if (LINUX_VERSION_CODE <= KERNEL_VERSION(6, 6, 0))` 里，6.6.89 不编译。gen3 寄存器：PS_HOLD_RST_CTL = 基址 + 0x52、CTL2 = +0x53（91-94）；13T 的 OPLUS 覆盖层把 qpnp-power-on 绑在 pmk8550 的 `pon_pbs@800`（DT oplus/sun_overlay_common.dtsi 53-66，`qcom,system-reset`），即 0x852/0x853。
  - `drivers/input/misc/pm8941-pwrkey.c` 的 reboot 通知（86-145，类型 WARM 1 / SHUTDOWN 4 / HARD 7，39-45）只在 `supports_ps_hold_poff_config` 时注册（399-402），gen3（pmk8350 兼容串）为 false（449、458、466-467）。
  - OPLUS 的 pmicwd 模块（dmesg 里的 `[PMICWD]`）源码不在公开仓库，**无法排除**它在重启时动 PON（缺口）。
- 所以类型由固件选：
  - TFA（谷歌 QTI 参考 EL3）`qti_system_reset` → `qti_pmic_prepare_reset` → 经 SPMI 把 PS_HOLD 类型设成 HARD(7)，再拉 PS_HOLD（plat/qti/common/src/pm_ps_hold.c 17-41、qti_pm.c 171-192）。手机上的 TZ 是闭源的，安卓重启却是 WARM，说明手机 TZ 与参考实现不同，或其行为取决于某些状态。
  - GH：HLOS（critical）VM 的 SYSTEM_RESET 先 `psci_vm_off` 再“直接交给固件”（hyp/vm/psci/src/psci_common.c 818-852）；SYSTEM_RESET2 平台处理失败时返回 NOT_SUPPORTED（855-894）——与我们实测“PSCI_FEATURES(SYSTEM_RESET2)=0 但调用返回 -1”一致；参考平台的 SYSTEM_RESET 是 SMC32 0x84000009（hyp/platform/psci_smc/src/psci_smc.c 160-184）。高通平台的处理函数不公开。`psci_vm_off` 只在有处理函数要求延后时才挡住其余 VCPU（psci_common.c 775-791），不能断定 TZ 复位时其余核已停。
- 公开佐证（SDI）：Brian Norris 2020 年 RFC“firmware: qcom_scm: disable SDI at boot”（IPQ4019）代码注释原话：“Some bootloaders leave this enabled by default, which prevents normal warm reboot.” 上游现行实现（UP qcom_scm.c 2849-2850）在 DT 标 `qcom,sdi-enabled` 或下载模式关闭时于 probe 关 SDI。
- 公开佐证（OEM 热重启）：搜索到 Elliot Berman 的提交“firmware: qcom_scm: Add SCM call to perform warm reboot”（Fairphone FP5 内核树，gitlab.e.foundation …/android_kernel_fairphone_fp5/-/commit/147254f0），与 13T 的 qcom_scm_reboot 一致：OEM_POWER 0x09 / 0x22、原子调用、无参数、reboot_mode 为 warm 时由 restart 通知调用。该页面返回 HTTP 429 未能打开，这一条只来自搜索摘要；事实以 13T 源码为准。
- Norris 的 RFC 在 lore.kernel.org 返回 403，内容取自镜像 lkml.rescloud.iu.edu/hypermail/linux/kernel/2007.2/06551.html。

## 4. 来源之间的分歧

- 关 SDI：13T 用快速调用 0xC2000109；UP 用同一 svc/cmd（`QCOM_SCM_BOOT_SDI_CONFIG` 0x09，参数注释“Disable watchdog debug / Disable SDI”）但是**标准调用** 0x42000109、取 res.result[0]（UP qcom_scm.c 499-519）。
- 下载模式：13T 整字写 0x00/0x01/0x10/0x20；UP 对 bits[5:4] 读改写，字段值 0..3（UP qcom_scm.c 141-145、552-575）。
- 停 SPMI 仲裁器、OEM 热重启：只在厂商树里有，UP 没有。
- UP 的 sm8750.dtsi 的 scm 节点没有 `qcom,dload-mode`（220-224）：上游内核在 SM8750 上根本不碰下载模式；手机跑的是厂商 DT（有）。

## 5. 手机实测（本项目 runs/）

- `runs/20261031-063106-pkgA-diag-cc-f2-1/device.txt`：`qcom_dload_probe: Current dump mode: 0x0`（安卓启动时 probe 读回 0）、之后 `dload: 1`、`DLOAD dump type: mini`；`bootreason: bootloader`；PMIC PON 日志多条 PS_HOLD + WARM_RESET 与 HARD_RESET 交替；poff_reason [0] HARD/PS_HOLD，[1] WARM（Warm Reset Count: 3）。
  - 推断（源码 + 这一行）：probe 时 enable_dump 为 0 → `if (!enable_dump) qcom_scm_disable_sdi()` 已执行 → **安卓运行期间 SDI 一直是关的**（Linux 没有重新打开 SDI 的调用）；XNU 从不关 SDI。未验证 XBL/TZ 自己会不会再开。
- 多轮日志：`psci=1.1 conduit=smc(/psci) SYSTEM_RESET2 features=0x0`，`SYSTEM_RESET2 returned -1; trying SYSTEM_RESET`。
- 解码摘要的 `vote ... p1` 是 1 位丢失率（pkgA 那轮 0.2339）；长按复位实测零翻转。

## 6. 结论（假设排序，待真机单变量验证）

H1（最可能）SDI 开着：TZ 把 SYSTEM_RESET 当成非正常复位走 HARD（Norris 注释 + 安卓 probe 即关 SDI）。
H2 SPMI 仲裁器：若手机 TZ 像 TFA 那样在 SYSTEM_RESET 里经 SPMI 写 PS_HOLD 类型，先停仲裁器会改变结果（推测）。
H3 下载 cookie：XNU 期间 TCSR 0x1FD9000 的值未知（0x100 探测读出来）；非 0 时热复位会进 CrashDump。
H4 先调失败的 SYSTEM_RESET2：安卓（COLD）从不调它；`pagani-reset-cold=1` 可单独排除，无需新代码。
H5 Linux 的显式热复位路径是 OEM 0xC3000922（只在 reboot_mode=WARM 时用）。
