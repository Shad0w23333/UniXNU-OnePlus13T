#!/bin/sh
# 13T 声音可行性：在安卓（root）下只读取证的命令清单。
#
# 注意：
#  - 本脚本没有被执行过。只能在用户同意、手机空闲（没有上机测试在跑）、已开机进安卓并 adb 可用时运行。
#  - 只读：手机上不写任何文件、不改属性、不挂载、不改 debugfs/dynamic_debug；所有输出只落在 Mac 的 OUT 目录。
#  - 拉回来的文件是 OPLUS/高通的专有数据（ACDB、功放容器、配置），只放本地，绝不进任何仓库。
#  - 第 D 节（抓 GPR 包头）不是只读（要改运行时调试开关），默认不执行，需要用户单独明确同意。
#
# 用法：sh phone-readonly-checklist.sh [OUT]   （OUT 默认是本目录下的 phone/日期）
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-"$HERE/phone/$(date +%Y%m%d-%H%M%S)"}
mkdir -p "$OUT/files"
S() { adb shell "su -c '$1'" 2>&1; }                 # 文本命令
P() { adb exec-out "su -c 'cat $1'" > "$OUT/files/$(echo "$1" | tr '/' '_')" 2>/dev/null; }   # 二进制安全地拉一个文件

echo "== A. 静态文件（回答：ACDB 在哪、后端格式、麦克风走哪个 ADC/解码器、扬声器数据线、功放容器是否同一份）"
S 'find /vendor /odm /system_ext -xdev \( -name "*.acdb" -o -name "*.qwsp" -o -name "*.acdbdelta" -o -name "backend_conf*.xml" -o -name "resourcemanager*.xml" -o -name "mixer_paths*.xml" -o -name "usecaseKvManager*.xml" -o -name "card-defs*.xml" -o -name "kvh2xml*.xml" -o -name "audio_policy_configuration*.xml" -o -name "tfa98xx*.cnt" -o -name "*tfa*.cnt" \) 2>/dev/null' > "$OUT/A1-audio-config-files.txt"
for f in $(cat "$OUT/A1-audio-config-files.txt" | tr -d '\r'); do P "$f"; done
S 'ls -laR /vendor/etc/acdbdata /odm/etc/acdbdata 2>/dev/null' > "$OUT/A2-acdbdata-ls.txt"
S 'sha256sum /odm/firmware/tfa98xx.cnt 2>/dev/null; ls -la /odm/firmware /vendor/firmware 2>/dev/null | grep -i tfa' > "$OUT/A3-tfa-container.txt"   # 期望 5699 字节，sha256 8574cf32…0a4e
S 'ls -la /vendor/lib/rfsa/adsp /vendor/lib64/rfsa/adsp /odm/lib/rfsa/adsp /vendor/dsp /vendor/dsp/adsp 2>/dev/null' > "$OUT/A4-adsp-dynamic-modules.txt"   # 动态模块（扬声器保护、OPLUS 音效）是否存在
S 'ls -la /vendor/firmware_mnt/image 2>/dev/null | grep -i -E "adsp|jsn"; sha256sum /vendor/firmware_mnt/image/adsp* 2>/dev/null' > "$OUT/A5-adsp-firmware.txt"   # 与 PaganiAdsp 的期望（modem_a FAT 的 image/adsp*.mdt/bNN）核对
S 'cat /proc/asound/cards; cat /proc/asound/pcm' > "$OUT/A6-asound.txt"

echo "== B. 运行时状态（回答：TFA 版本号、ADSP 加载耗时、编解码器枚举、音频是否走转换型 SMMU、0xD0000000 是否 HLOS 内存）"
S 'dmesg' | grep -i -E "tfa98|adsp|remoteproc|q6|gpr|spf|apm|audio_prm|audio_pkt|lpass|swr|wcd939|msm_audio_ion|snd_soc|sun-asoc|haptic" > "$OUT/B1-dmesg-audio.txt"
S 'for r in /sys/class/remoteproc/*; do echo $r $(cat $r/name) $(cat $r/state) $(cat $r/firmware 2>/dev/null); done' > "$OUT/B2-remoteproc.txt"
S 'cat /proc/iomem' > "$OUT/B3-iomem.txt"
S 'for g in /sys/kernel/iommu_groups/*; do echo $g: $(ls $g/devices); done' > "$OUT/B4-iommu-groups.txt"
S 'getprop' | grep -i -E "audio|acdb|agm|pal|haptic|vibrat|spk|speaker" > "$OUT/B5-props.txt"
S 'ls /vendor/bin /system/bin 2>/dev/null | grep -i -E "tinymix|tinyplay|tinycap|agmmixer|agmplay|agmcap"' > "$OUT/B6-mixer-tools.txt"
S 'ls /sys/kernel/debug 2>/dev/null | head -100; ls /sys/kernel/debug/pinctrl 2>/dev/null' > "$OUT/B7-debugfs-ls.txt"   # 只看有没有，不挂载

echo "== C. 播放/录音时再读一次（请用户在安卓里放铃声或录音；回答：副 MI2S 引脚是否被切成 i2s 功能、TFA 实际 TDM 设置、录音时的 ADC/解码器路由）"
echo "   用户开始播放后按回车"; read _
S 'cat /sys/kernel/debug/pinctrl/*f100000*/pinmux-pins 2>/dev/null | grep -E "pin (2|101|121|122|123|124|164) "' > "$OUT/C1-pinmux-playing.txt"
S 'ls -la /sys/kernel/debug | grep -i tfa; for d in /sys/kernel/debug/*tfa*; do ls $d; done' > "$OUT/C2-tfa-debugfs.txt" 2>&1
S 'command -v tinymix >/dev/null && tinymix -D 100 2>/dev/null | head -400' > "$OUT/C3-tinymix-playing.txt"
echo "   用户改为录音（语音备忘录）后按回车"; read _
S 'command -v tinymix >/dev/null && tinymix -D 100 2>/dev/null | head -400' > "$OUT/C4-tinymix-recording.txt"
S 'dmesg' | tail -200 | grep -i -E "swr|wcd939|tx_macro|va_macro|cdc_dma|prm" > "$OUT/C5-dmesg-recording.txt"

echo "== D.（默认不执行，非只读）GPR 包头抓取：打开 gpr/audio_pkt 的 dynamic_debug 只能拿到 opcode/端口/长度，拿不到负载；更推荐离线解析 ACDB"
# S 'echo "module gpr_dlkm +p" > /sys/kernel/debug/dynamic_debug/control'   # 需用户明确同意；用完要关回去

echo "完成：$OUT"
