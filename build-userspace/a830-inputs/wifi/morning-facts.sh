#!/bin/sh
# 13T Wi-Fi（peach，PCIe 17cb:110e）早晨只读取证脚本：把 facts.md 里标【待核】的事实在安卓（root）下读回来。
#
# 注意：
#  - 本脚本今晚没有执行过（手机不在线）。只能在用户同意、手机开机进安卓、adb 已授权、KernelSU 可 su 时运行。
#  - 只读：手机上不写任何文件、不改属性、不开关 Wi-Fi、不挂载、不写 sysfs/debugfs、不推送任何文件。
#    命令经管道送进 `su -c sh`（base64 解码后在内存里执行），手机存储上不落任何东西；拉回的数据只写 Mac 的 OUT 目录。
#  - debugfs 默认不碰；DEBUGFS=1 时只读几个只读文件（clk_summary、interconnect_summary、regulator_summary、
#    pinctrl 的 pinconf-pins、gpio），不挂载 debugfs，不碰 pci-msm / cnss 的 debugfs 控制文件（那些是写触发的）。
#  - PULL_FW=1 时把找到的 WLAN 固件原样拉回 OUT/firmware/（高通/一加专有文件，只留本地，绝不进任何仓库）。
#  - MAC=1 时记录 wlan0 的 MAC 地址（个人数据，只留本地）。
#  - 最好在刚开机几分钟内运行：cnss 的开机日志（region id / chip_id、链路训练、固件加载）在内核环形缓冲里会被冲掉。
#    是否先重启由用户决定，本脚本不重启。
#
# 用法：sh morning-facts.sh [OUT]          （OUT 默认是本目录下的 phone/日期时间）
#       DEBUGFS=1 PULL_FW=1 sh morning-facts.sh
# 输出与要回答的问题（编号对应 facts.md 第 9 节“早晨核对清单”）：
#   A-*  身份、运行中设备树 sha256 是否仍是 13b8b74a…（M1）
#   B-*  PCIe 拓扑：端点 ID/修订号/子系统 ID、BAR0 大小、链路速率与宽度、配置空间（MSI/MSI-X/L1SS 能力）（M2–M5）
#   C-*  安卓实际用了几个 MSI、ITS/SPI、IOMMU 组、BAR 落在哪段地址（M6、M7）
#   D-*  固件在哪（modem_a 的 FAT image/ 还是 /vendor）、文件名、大小、sha256、版本串；搜索路径；qcacld INI（M8–M11）
#   E-*  内核/logcat 日志：chip_id/board_id/region、选中的 BDF、peach_v2、冷启动校准、TME/AUX/QDSS 是否下载（M12–M14）
#   F-*  七路 WLAN 供电在 Wi-Fi 开启时的电压/状态/用户；Wi-Fi 当前状态（M15）
#   G-*  （DEBUGFS=1）RF_CLK1/LN_BB 时钟、PCIe 互连投票、稳压器汇总（M16、M17）
#   H-*  （DEBUGFS=1）WLAN_EN/SW_CTRL/PERST/CLKREQ/WAKE/SOL 引脚与 PMK8550 gpio3 睡眠时钟脚状态（M18）
#   SUMMARY.txt  以上要点的机读汇总
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-"$HERE/phone/$(date +%Y%m%d-%H%M%S)"}
PULL_FW=${PULL_FW:-0}
DEBUGFS=${DEBUGFS:-0}
MAC=${MAC:-0}
mkdir -p "$OUT/files"
command -v adb >/dev/null 2>&1 || { echo "缺少 adb"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "缺少 python3"; exit 1; }
[ "$(adb get-state 2>/dev/null)" = "device" ] || { echo "没有已授权的 adb 设备（需要开机进安卓）"; exit 1; }

# S：以 root 在手机上执行一段 sh（只读命令）。脚本经 base64 管道送入 su，手机上不写文件。
# adb 的标准输入接 /dev/null：否则在 while read 循环里 adb 会吞掉循环要读的名单。
S() { b=$(printf '%s\n' "$1" | base64 | tr -d '\n'); adb shell "echo $b | base64 -d | su -c sh" 2>&1 </dev/null | tr -d '\r'; }
# P：以 root 读出一个文件的原始字节到 Mac（exec-out 不改二进制）。
P() { adb exec-out "su -c 'cat \"$1\"'" > "$2" 2>/dev/null </dev/null; }

[ "$(S 'id -u' | tail -n 1)" = "0" ] || { echo "su 不可用（需要 KernelSU 授权 shell）"; exit 1; }
echo "输出目录：$OUT"

echo "== A. 身份与设备树"
S 'getprop ro.build.fingerprint; getprop ro.build.display.id; getprop ro.product.model; getprop ro.boot.slot_suffix;
getprop ro.soc.model; getprop ro.board.platform; getprop ro.product.oplus.wlan.ko; uname -a; cat /proc/version; uptime' > "$OUT/A1-identity.txt"
S 'getprop' | grep -i -E 'wlan|wifi|cnss|peach|qca|bdf' > "$OUT/A2-props-wlan.txt"
S 'sha256sum /sys/firmware/fdt' > "$OUT/A3-live-dtb-sha256.txt"          # 期望 13b8b74a8dba343b96fd34910858f66501ce7ab4eb9ce2eec1787f55fc2ffe60

echo "== B. PCIe（sysfs 只读）"
S 'ls -l /sys/bus/pci/devices/
for d in /sys/bus/pci/devices/*; do
  echo "== $d"
  for f in vendor device subsystem_vendor subsystem_device revision class irq enable current_link_speed current_link_width \
           max_link_speed max_link_width d3cold_allowed power_state; do
    printf "%s=" "$f"; cat "$d/$f" 2>/dev/null || echo "-"
  done
  echo "resource:"; cat "$d/resource" 2>/dev/null
  printf "msi_irqs: "; ls "$d/msi_irqs" 2>/dev/null | tr "\n" " "; echo
  printf "runtime_status="; cat "$d/power/runtime_status" 2>/dev/null
  printf "driver="; readlink "$d/driver"; printf "iommu_group="; readlink "$d/iommu_group"
  for a in aer_dev_correctable aer_dev_fatal aer_dev_nonfatal; do [ -r "$d/$a" ] && { echo "$a:"; cat "$d/$a"; }; done
done
toybox lspci -ek 2>/dev/null; lspci -vvv 2>/dev/null | head -200' > "$OUT/B1-pci-sysfs.txt"
P /sys/bus/pci/devices/0000:01:00.0/config "$OUT/files/ep-01.00.0-config.bin"   # 端点配置空间（链路断开时读回全 ff，正常）
P /sys/bus/pci/devices/0000:00:00.0/config "$OUT/files/rp-00.00.0-config.bin"   # 根端口配置空间

echo "== C. 中断、IOMMU、地址"
S 'cat /proc/interrupts' | grep -i -E 'msi|pci|pcie|wlan|cnss|mhi|wake|its|ce[0-9]|dp_|sol' > "$OUT/C1-interrupts.txt"
S 'grep -i -E "pci|pcie|40[0-9a-f]{6}-" /proc/iomem' > "$OUT/C2-iomem.txt"
S 'for g in /sys/kernel/iommu_groups/*; do if ls "$g/devices" 2>/dev/null | grep -q -E "0000:0[01]:00.0|cnss|1c00000"; then
  echo "$g: $(ls "$g/devices" | tr "\n" " ") type=$(cat "$g/type" 2>/dev/null)"; fi; done
ls -l /sys/bus/platform/devices/ | grep -i -E "cnss|pcie|1c00000|wlan|b0000000"' > "$OUT/C3-iommu-platform.txt"

echo "== D. 固件位置与校验"
S 'grep -E "firmware_mnt|bt_firmware| /vendor | /odm |/vendor/dsp|persist" /proc/mounts
ls -l /dev/block/by-name/ 2>/dev/null | grep -E " (modem|bluetooth|dsp|persist)(_a|_b)? "
printf "firmware_class.path="; cat /sys/module/firmware_class/parameters/path 2>/dev/null
grep -h -E "firmware_directories|external_firmware_handler" /system/etc/ueventd.rc /vendor/etc/ueventd.rc /odm/etc/ueventd.rc /vendor/ueventd.rc 2>/dev/null' > "$OUT/D1-mounts-paths.txt"
S 'ls -la /vendor/firmware_mnt/image/ 2>/dev/null' > "$OUT/D2-modem-image-ls.txt"      # modem_a FAT 根下 image/ 的完整名单（就地读取器用）
S 'for d in /vendor/firmware_mnt/image/peach /vendor/firmware/peach /vendor/firmware/wlan /vendor/firmware /odm/firmware \
           /vendor/etc/wifi /vendor/etc/wifi/peach /vendor/etc/wifi/peach_v2; do
  [ -d "$d" ] && { echo "== $d"; ls -la "$d"; }; done' > "$OUT/D3-wlan-dirs-ls.txt"
S 'find /vendor/firmware_mnt /vendor/firmware /odm/firmware /vendor/etc /odm/etc /vendor/lib/firmware /system/etc/firmware \
     -maxdepth 4 \( -iname "*amss*" -o -iname "bdwlan*" -o -iname "regdb*" -o -iname "m3.bin" -o -iname "phy_ucode*" \
     -o -iname "aux_ucode*" -o -iname "tmel*" -o -iname "qdss_trace_config*" -o -iname "*softsku*" -o -iname "peach_*" \
     -o -iname "hds.bin" -o -iname "WCNSS_qcom_cfg.ini" -o -iname "wlan_mac.bin" -o -iname "Data.msc" \) 2>/dev/null | sort -u' \
  | grep '^/' > "$OUT/D4-wlan-firmware-paths.txt"
: > "$OUT/D5-wlan-firmware-sha256.txt"
: > "$OUT/D6-wlan-firmware-versions.txt"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  S "ls -la \"$f\"; sha256sum \"$f\"" >> "$OUT/D5-wlan-firmware-sha256.txt"
  case "$f" in
    *.bin|*.elf|*.mbn)
      echo "== $f" >> "$OUT/D6-wlan-firmware-versions.txt"
      S "strings -n 12 \"$f\" 2>/dev/null | grep -m 6 -E 'QC_IMAGE_VERSION_STRING|OEM_IMAGE_VERSION|WLAN\\.[A-Z]+\\.[0-9]|IMAGE_VARIANT_STRING'" \
        >> "$OUT/D6-wlan-firmware-versions.txt" ;;
  esac
  case "$f" in
    *WCNSS_qcom_cfg.ini) P "$f" "$OUT/files/$(echo "$f" | tr '/' '_')" ;;     # qcacld 配置（非固件），留作参考
  esac
  if [ "$PULL_FW" = "1" ]; then
    mkdir -p "$OUT/firmware"
    P "$f" "$OUT/firmware/$(echo "$f" | sed 's#^/##' | tr '/' '_')"
  fi
done < "$OUT/D4-wlan-firmware-paths.txt"
[ "$PULL_FW" = "1" ] && (cd "$OUT/firmware" && shasum -a 256 * > SHA256SUMS.local 2>/dev/null)

echo "== E. 日志（只读转储）"
S 'dmesg' > "$OUT/E0-dmesg-full.txt"
grep -i -E 'cnss|wlan|qcacld|peach|mhi|qrtr|pci-msm|msm_pcie|pcie|wlfw|bdf|regdb|amss|tme|aux|qdss|cold.?boot|region id|chip_id|board_id|wifi' \
  "$OUT/E0-dmesg-full.txt" > "$OUT/E1-dmesg-wlan.txt"
S 'logcat -b all -d 2>/dev/null' | grep -i -E 'cnss|wlan|peach|qcacld|wificond|supplicant|vendor\.wifi|wifi.*(fw|firmware|driver|chip|bdf)' \
  | tail -n 4000 > "$OUT/E2-logcat-wlan.txt"

echo "== F. 供电与 Wi-Fi 状态"
S 'for r in /sys/class/regulator/regulator.*; do
  n=$(cat "$r/name" 2>/dev/null)
  case "$n" in
    pm_v8f_l3|pm_v8f_l2|pm_humu_l8|pm_v8d_s4|pm_v8f_s5|pm_v8g_s3|pm_v8i_s7|pm_v8f_l1|pm_v8g_l3|pm_v8i_l2|pm_v8i_l3|*pcie*)
      echo "$r name=$n uV=$(cat $r/microvolts 2>/dev/null) min=$(cat $r/min_microvolts 2>/dev/null) max=$(cat $r/max_microvolts 2>/dev/null) state=$(cat $r/state 2>/dev/null) users=$(cat $r/num_users 2>/dev/null) opmode=$(cat $r/opmode 2>/dev/null)"
      echo "  consumers: $(ls $r | grep -E -- "-|:" | tr "\n" " ")" ;;
  esac
done
cmd wifi status 2>/dev/null | head -5
dumpsys wifi 2>/dev/null | grep -i -E "firmware|driver version|chipset|wlan0|Wi-Fi is" | head -20' > "$OUT/F1-rails-wifi-state.txt"

if [ "$DEBUGFS" = "1" ]; then
  echo "== G/H. debugfs 只读文件（DEBUGFS=1）"
  S 'D=/sys/kernel/debug; [ -d $D/clk ] || { echo "debugfs 未挂载：跳过（不挂载）"; exit 0; }
grep -i -E "pcie|rf_clk|rfclk|ln_bb|sleep_clk|bi_tcxo|tcsr" $D/clk/clk_summary 2>/dev/null' > "$OUT/G1-clk-summary.txt"
  S 'D=/sys/kernel/debug; grep -i -E "pcie|cnss|1c00000|b0000000|pcie_noc|gem_noc|ebi" $D/interconnect/interconnect_summary 2>/dev/null | head -200' \
    > "$OUT/G2-interconnect.txt"
  S 'D=/sys/kernel/debug; grep -i -E "pm_v8f_l[123]|pm_humu_l8|pm_v8d_s4|pm_v8f_s5|pm_v8g_s3|pm_v8i_s7|pm_v8g_l3|cnss|pcie" $D/regulator/regulator_summary 2>/dev/null' \
    > "$OUT/G3-regulator-summary.txt"
  S 'D=/sys/kernel/debug; for p in $D/pinctrl/*; do
  case "$p" in *f000000*|*tlmm*) echo "== $p"; grep -E "^pin (16|18|19|102|103|104|202|203) " $p/pinconf-pins $p/pinmux-pins 2>/dev/null ;;
                *pmk8550*|*b800*) echo "== $p"; grep -E "gpio3" $p/pinconf-pins $p/pinmux-pins 2>/dev/null ;;
                *pm8550ve*|*8800*) echo "== $p"; grep -E "gpio3|gpio6" $p/pinconf-pins 2>/dev/null ;; esac; done
grep -E "gpio-(16|18|19|102|103|104|202|203) |wlan|pcie|cnss|sol" $D/gpio 2>/dev/null' > "$OUT/H1-pins.txt"
fi

S 'ls -la /sys/kernel/cnss/ 2>/dev/null; ls -la /sys/kernel/ | grep -i cnss' > "$OUT/I1-cnss-sysfs-ls.txt"   # 只列目录，不读写触发文件

if [ "$MAC" = "1" ]; then
  S 'cat /sys/class/net/wlan0/address 2>/dev/null; ip link show wlan0 2>/dev/null; iw dev wlan0 info 2>/dev/null' > "$OUT/J1-wlan0-private.txt"
fi

echo "== 汇总"
python3 -I - "$OUT" <<'EOF' > "$OUT/SUMMARY.txt"
import os, re, struct, sys
out = sys.argv[1]
def read(name):
    try:
        return open(os.path.join(out, name), errors='replace').read()
    except OSError:
        return ''
print('M1 运行中设备树 sha256:', 'match' if '13b8b74a8dba343b96fd34910858f66501ce7ab4eb9ce2eec1787f55fc2ffe60' in read('A3-live-dtb-sha256.txt') else 'DIFFERENT/unknown')
pci = read('B1-pci-sysfs.txt')
for block in pci.split('== ')[1:]:
    if '0000:01:00.0' in block.split('\n')[0]:
        kv = dict(re.findall(r'^(\w+)=(.*)$', block, re.M))
        print('M2 端点:', kv.get('vendor'), kv.get('device'), 'rev', kv.get('revision'), 'subsys', kv.get('subsystem_vendor'), kv.get('subsystem_device'))
        print('M3 链路:', kv.get('current_link_speed'), 'x' + str(kv.get('current_link_width')), '/ 最大', kv.get('max_link_speed'), 'x' + str(kv.get('max_link_width')))
        res = block.split('resource:\n', 1)[1].split('\n') if 'resource:\n' in block else []
        if res and res[0].startswith('0x'):
            a, b, flags = (int(x, 16) for x in res[0].split()[:3])
            print('M4 BAR0: 0x%x-0x%x 大小 0x%x 标志 0x%x' % (a, b, b - a + 1 if b else 0, flags))
        m = re.search(r'msi_irqs: (.*)', block)
        print('M6 msi_irqs 个数:', len(m.group(1).split()) if m else '?')
cfg_path = os.path.join(out, 'files', 'ep-01.00.0-config.bin')
if os.path.exists(cfg_path) and os.path.getsize(cfg_path) >= 256:
    cfg = open(cfg_path, 'rb').read()
    d32 = lambda o: struct.unpack_from('<I', cfg, o)[0]
    d16 = lambda o: struct.unpack_from('<H', cfg, o)[0]
    if d32(0) == 0xffffffff:
        print('M5 配置空间: 读回全 ff（链路当时断开/挂起）')
    else:
        print('M5 配置空间: id %08x class %06x BAR0 %08x BAR1 %08x' % (d32(0), d32(8) >> 8, d32(0x10), d32(0x14)))
        cap, guard = cfg[0x34], 0
        while 0x40 <= cap < 0x100 and guard < 48:
            cid, nxt = cfg[cap], cfg[cap + 1]
            if cid == 0x05:
                ctl = d16(cap + 2)
                print('   MSI @%#x: 可请求 %d 个向量, 已允许 %d, 64位=%d, 每向量屏蔽=%d' % (cap, 1 << ((ctl >> 1) & 7), 1 << ((ctl >> 4) & 7), (ctl >> 7) & 1, (ctl >> 8) & 1))
            elif cid == 0x11:
                print('   MSI-X @%#x: 表大小 %d, 已使能 %d' % (cap, (d16(cap + 2) & 0x7ff) + 1, d16(cap + 2) >> 15))
            elif cid == 0x10:
                lcap, lsta = d32(cap + 0xc), d16(cap + 0x12)
                print('   PCIe cap @%#x: LnkCap Gen%d x%d ASPM=%d; LnkSta Gen%d x%d' % (cap, lcap & 0xf, (lcap >> 4) & 0x3f, (lcap >> 10) & 3, lsta & 0xf, (lsta >> 4) & 0x3f))
            elif cid == 0x01:
                print('   PM cap @%#x' % cap)
            cap, guard = nxt, guard + 1
        if len(cfg) >= 0x1000:
            ext, guard = 0x100, 0
            while ext and guard < 64:
                hdr = d32(ext)
                if hdr in (0, 0xffffffff):
                    break
                names = {0x1: 'AER', 0x1e: 'L1SS', 0x18: 'LTR', 0x19: 'SecPCIe', 0x0b: 'VSEC', 0x26: 'Gen4', 0x16: 'DPA'}
                print('   扩展能力 @%#x: %s (%#x)' % (ext, names.get(hdr & 0xffff, '?'), hdr & 0xffff))
                ext, guard = (hdr >> 20) & 0xffc, guard + 1
irq = read('C1-interrupts.txt')
print('M6 /proc/interrupts 里 WLAN/MSI 相关行数:', len([l for l in irq.splitlines() if l.strip()]))
paths = [l for l in read('D4-wlan-firmware-paths.txt').splitlines() if l.startswith('/')]
print('M8 找到的 WLAN 固件/配置文件 %d 个:' % len(paths))
for line in read('D5-wlan-firmware-sha256.txt').splitlines():
    if re.match(r'^[0-9a-f]{64}\s', line):
        print('   ', line)
print('M9 固件所在挂载:', 'modem_a FAT(/vendor/firmware_mnt)' if any(p.startswith('/vendor/firmware_mnt/') for p in paths) else '',
      '/vendor' if any(p.startswith('/vendor/firmware/') or p.startswith('/vendor/etc') for p in paths) else '',
      '/odm' if any(p.startswith('/odm/') for p in paths) else '')
vers = read('D6-wlan-firmware-versions.txt').strip()
print('M10 版本串:\n' + '\n'.join('    ' + l for l in vers.splitlines()[:24]))
dm = read('E1-dmesg-wlan.txt') + read('E2-logcat-wlan.txt')
for key, pat in [('M12 region/chip_id', r'region id[^\n]*'), ('M12 board_id', r'board_id[^\n]*'), ('M13 BDF', r'(bdwlan[\w.]*|BDF[^\n]{0,80})'),
                 ('M13 peach 版本', r'(peach[_ ]?v?2|PEACH_V2|soc_hw_version[^\n]*)'), ('M14 冷启动校准', r'(cold.?boot[^\n]{0,80}|CBC[^\n]{0,60})'),
                 ('M14 TME/AUX/QDSS', r'(TME[^\n]{0,80}|aux[^\n]{0,60}ucode[^\n]{0,40}|qdss[^\n]{0,60})'), ('M3 链路训练', r'(link[^\n]{0,40}(gen|speed|width)[^\n]{0,60})')]:
    hits = re.findall(pat, dm, re.I)
    flat = [h if isinstance(h, str) else h[0] for h in hits]
    uniq = list(dict.fromkeys(flat))[:6]
    print(key + ':', ' | '.join(uniq) if uniq else '（日志里没有：可能开机太久被冲掉）')
print('M15 供电:')
for l in read('F1-rails-wifi-state.txt').splitlines():
    if 'name=' in l or 'Wi-Fi' in l or 'enabled' in l.lower():
        print('   ', l.strip())
EOF
cat "$OUT/SUMMARY.txt"
echo "完成：$OUT（只在本地；firmware/ 与 J1-* 含专有或个人数据，不要提交）"
