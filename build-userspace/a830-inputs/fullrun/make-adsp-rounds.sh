#!/bin/bash
# ADSP 跑批的镜像（build-userspace/a830-inputs/adsp-review.md 第 1 节），写法照 make-wifi-rounds.sh。轮次、参数、删项的唯一来源是
# 工具/gpu-test-loop/adsp-rounds.py（--spec、--names），这里只出包，出完用它的 --check-payload 核对。这个脚本不碰手机。
#   make-adsp-rounds.sh               无人值守的五轮：A1 A2 A3 B1 B2
#   make-adsp-rounds.sh A             只出一段：A、B（B3、B4 要用户在场）、C（C1 到 C7，都要用户在场）或 D（等 audio2 路由）
#   make-adsp-rounds.sh C3 C4 ...     指定的轮次，名字见 adsp-rounds.py 不带参数的输出
#   DRY=1 make-adsp-rounds.sh ...     只打印每轮的环境和参数
# 前提：out-gpu 的内核带着现在这份驱动（--check-kernel 核对，不对就停，DRY 只打印）。每张镜像约 113 MB。
# 基线是 make-stage16.sh 的 BASE，从那个文件现读，基线只有一份；种子 s7-final，面板 65。
# A830_BATTERY=0：make-stage16.sh 是开的，这里不开，只有 A1、C1、D2 的 args 自己带 pagani-battery=1。A830_DROP_ARGS 删不得它：
# 删项在 A830_EXTRA_ARGS 之后才做，会连 args 里的一起删掉。
set -euo pipefail
cd /Users/xiaochoumao/unixnu-13t
F=$PWD/build-userspace/a830-inputs/final
ROUNDS=工具/gpu-test-loop/adsp-rounds.py
STAGE16=build-userspace/a830-inputs/fullrun/make-stage16.sh
OUT="测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用.img"
BASE=$(sed -n 's/^BASE="\(.*\)"$/\1/p' "$STAGE16")
[ -n "$BASE" ] || { echo "$STAGE16 里没有 BASE" >&2; exit 1; }
FIX="$(tr '\n' ' ' < build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt)"

one() { # 轮名
  local name=$1 line image= reset= panel= seed_name= drop= args=
  while IFS= read -r line; do
    case $line in
      image=*) image=${line#image=} ;;
      reset=*) reset=${line#reset=} ;;
      panel=*) panel=${line#panel=} ;;
      seed=*) seed_name=${line#seed=} ;;
      drop=*) drop=${line#drop=} ;;
      args=*) args=${line#args=} ;;
    esac
  done < <(python3 -B "$ROUNDS" --spec "$name")
  [ -n "$image" ] || { echo "没有这一轮：$name" >&2; exit 1; }
  local prebuilt=$F/$seed_name.dbrdisk trust=$F/$seed_name-owner-trust.bundle
  echo "=== $name: $image reset=$reset panel=$panel seed=$seed_name drop=$drop args=$args"
  if [ -n "${DRY:-}" ]; then
    echo "A830_DROP_ARGS='$drop' A830_PREBUILT_SEED=$prebuilt A830_OWNER_TRUST=$trust A830_AUDIO=1 A830_SYSTEM_APPS=1 A830_BATTERY=0 A830_RESET_AFTER=$reset A830_PANEL=$panel"
    echo "A830_EXTRA_ARGS='$BASE sheng-log=1 $args'"
    return
  fi
  [ -f "$prebuilt" ] && [ -f "$trust" ] || { echo "没有种子 $prebuilt 或信任包 $trust" >&2; exit 1; }
  A830_DROP_ARGS="$drop" A830_PREBUILT_SEED=$prebuilt A830_OWNER_TRUST=$trust A830_AUDIO=1 A830_SYSTEM_APPS=1 A830_BATTERY=0 \
  A830_RESET_AFTER=$reset A830_PANEL=$panel A830_EXTRA_FIXUPS="$FIX" A830_EXTRA_ARGS="$BASE sheng-log=1 $args" 工具/make-a830-layout.sh phone-island-apps
  cp "$OUT" "测试镜像/$image"
  cp "${OUT%.img}.payload.json" "测试镜像/${image%.img}.payload.json"
  shasum -a 256 "测试镜像/$image" | cut -c1-16
  python3 -B "$ROUNDS" --check-payload "$name"
}

if [ -n "${DRY:-}" ]; then python3 -B "$ROUNDS" --check-kernel || true; else python3 -B "$ROUNDS" --check-kernel; fi
case ${1:-} in
  '') names=$(python3 -B "$ROUNDS" --names unattended) ;;
  A|B|C|D) names=$(python3 -B "$ROUNDS" --names "$1") ;;
  *) names=$* ;;
esac
for name in $names; do one "$name"; done
