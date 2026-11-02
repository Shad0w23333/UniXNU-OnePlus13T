#!/bin/bash
# Packs the two images of stage 1.5 from the current out-gpu kernel and the final seed. Each named image takes its .payload.json with it:
# gpu-loop.py reads the boot arguments from there.
set -euo pipefail
cd /Users/xiaochoumao/unixnu-13t
F=$PWD/build-userspace/a830-inputs/final
OUT="测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用.img"
BASE="pagani-gpu-status=3 sheng-no-lc=1 pagani-haptics=1 pagani-haptics-ios=2 pagani-audio-srzl=1 pagani-halt=2 pagani-rtc=1 pagani-diag=1 pagani-kbtrace=1"
FIX="$(tr '\n' ' ' < build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt)"
one() { # name panel reset drop extra
  echo "=== $1: panel=$2 reset=$3 drop=$4 extra=$5"
  A830_DROP_ARGS="startup_debug $4" A830_PREBUILT_SEED=$F/s6-final.dbrdisk A830_OWNER_TRUST=$F/s6-final-owner-trust.bundle A830_AUDIO=1 A830_SYSTEM_APPS=1 A830_BATTERY=1 \
  A830_RESET_AFTER=$3 A830_PANEL=$2 A830_EXTRA_FIXUPS="$FIX" A830_EXTRA_ARGS="$BASE $5" 工具/make-a830-layout.sh phone-island-apps
  cp "$OUT" "测试镜像/$1"
  cp "${OUT%.img}.payload.json" "测试镜像/${1%.img}.payload.json"
  shasum -a 256 "测试镜像/$1" | cut -c1-16
}
one boot-A830-阶段15-首测.img 1217 420 native-ufs-diagnostics "sheng-log=1"
one boot-A830-阶段15-完整包.img 65 5400 "" "sheng-log=1"
