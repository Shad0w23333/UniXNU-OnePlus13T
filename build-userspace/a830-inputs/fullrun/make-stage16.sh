#!/bin/bash
# Packs the images of stage 1.6 from the integrated out-gpu kernel and seed s7-final (round-1101/keyboard.md: make-a830-final-seed.sh's next
# step). One thing changes per image, so a failure has one cause (memory-storage-verify.md section 3):
#   阶段16-完整包   6 GiB, no compressor, panel mask 65, the 90-minute reset: the daily image of the round
#   阶段16-8G       only the window (needs phone-bootstrap-tables.patch; make-a830-layout.sh refuses a kernel without it), 240 s
#   阶段16-压缩器   only kern.vm_compressor = 2 in the ADT, no byte of the command line (phone-compressor-thrashing.patch), 420 s
#   阶段16-亮度A1   only the panel mask: 577 runs probe A1, which writes DISP_CC_MDSS_AHB_CLK (PaganiPanel README), 240 s
#   阶段16-USB探针  only pagani-battery=7: the read-only USB probe and the PMIC record (no byte more), 240 s
#   阶段16-完整包-保守  the complete package on the seed from before the plug-in was rebuilt, with pagani-audio-srzl=1: the way back from F3 and
#                       from the VirtualAudio repair, when that seed was kept
# make-stage16.sh rounds adds one image for each next step of the new hardware (round-1101/ 的报告，测试清单.md): 亮度DCS (needs A1), 声音路由,
# 蓝牙替身, 蓝牙, 相机1..4 and 相机测试图. The Wi-Fi and ADSP rounds have their own scripts (make-wifi-rounds.sh, make-adsp-rounds.sh).
# pagani-input-script is not in any of them: the synthetic touches are over. pagani-kbtrace=1 stays (17 bytes) until typing works, its kbt records
# are how the keyboard is read. Each named image takes its .payload.json with it: gpu-loop.py reads the boot arguments from there.
set -euo pipefail
cd /Users/xiaochoumao/unixnu-13t
F=$PWD/build-userspace/a830-inputs/final
SEED=$F/s7-final
BEFORE_F3=$F/pre-f3-20261101/s7-final
OUT="测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用.img"
BASE="pagani-gpu-status=3 sheng-no-lc=1 pagani-haptics=1 pagani-haptics-ios=2 pagani-audio-srzl=7 pagani-halt=2 pagani-rtc=1 pagani-diag=1 pagani-kbtrace=1"
FIX="$(tr '\n' ' ' < build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt)"
[ -f "$SEED.dbrdisk" ] && [ -f "$SEED-owner-trust.bundle" ] || { echo "make-stage16: no $SEED: the keyboard seed comes first" >&2; exit 1; }
one() { # name panel reset drop extra window compressor [seed]
  local seed=${8:-$SEED}
  echo "=== $1: panel=$2 reset=$3 drop=$4 extra=$5 window=$6 compressor=$7 seed=$seed"
  A830_OWNED_MEMORY=$6 A830_VM_COMPRESSOR=$7 \
  A830_DROP_ARGS="startup_debug $4" A830_PREBUILT_SEED=$seed.dbrdisk A830_OWNER_TRUST=$seed-owner-trust.bundle A830_AUDIO=1 A830_SYSTEM_APPS=1 A830_BATTERY=1 \
  A830_RESET_AFTER=$3 A830_PANEL=$2 A830_EXTRA_FIXUPS="$FIX" A830_EXTRA_ARGS="$BASE $5" 工具/make-a830-layout.sh phone-island-apps
  cp "$OUT" "测试镜像/$1"
  cp "${OUT%.img}.payload.json" "测试镜像/${1%.img}.payload.json"
  shasum -a 256 "测试镜像/$1" | cut -c1-16
}
one boot-A830-阶段16-完整包.img 65 5400 "" "sheng-log=1" "" ""
one boot-A830-阶段16-8G.img 65 240 "" "sheng-log=1" 0x200000000 ""
one boot-A830-阶段16-压缩器.img 65 420 "" "sheng-log=1" "" 2
one boot-A830-阶段16-亮度A1.img 577 240 "" "sheng-log=1" "" ""
one boot-A830-阶段16-USB探针.img 65 240 "" "sheng-log=1 pagani-battery=7" "" ""
if [ -f "$BEFORE_F3.dbrdisk" ]; then
  KEEP=$BASE; BASE=${BASE/pagani-audio-srzl=7/pagani-audio-srzl=1}
  one boot-A830-阶段16-完整包-保守.img 65 5400 "" "sheng-log=1" "" "" "$BEFORE_F3"
  BASE=$KEEP
fi
if [ "${1:-}" = rounds ]; then
  one boot-A830-阶段16-亮度DCS.img 321 240 "" "sheng-log=1" "" ""
  one boot-A830-阶段16-声音路由.img 65 180 "" "sheng-log=1 pagani-audio-trace=1" "" ""
  for level in 1 2 3 4; do
    one boot-A830-阶段16-相机$level.img 65 240 "" "sheng-log=1 pagani-cam=$level" "" ""
  done
  # pagani-torch=1 pagani-torch-ios=1 is 34 bytes more: pagani-kbtrace and native-ufs-diagnostics make the room.
  one boot-A830-阶段16-相机测试图.img 65 600 "native-ufs-diagnostics pagani-kbtrace" "sheng-log=1 pagani-torch=1 pagani-torch-ios=1" "" "" "$F/s7cam-final"
  # The two fix-ups of bluetoothd go with its seed and the argument, all three or none (PaganiBt/README.md).
  FIX="$FIX $(grep -v '^#' UniXNU-Pagani-Drivers/drivers/PaganiBt/integration/text-fixups.txt | tr '\n' ' ')"
  one boot-A830-阶段16-蓝牙替身.img 65 300 "" "sheng-log=1 pagani-bt=40" "" "" "$F/s7bt-final"
  one boot-A830-阶段16-蓝牙.img 65 300 "" "sheng-log=1 pagani-bt=48" "" "" "$F/s7bt-final"
fi
