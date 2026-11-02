#!/bin/bash
# Wi-Fi 跑批的镜像（round-1101/wifi.md 第 5.3 节的合并方案），写法照 make-stage15.sh。轮次、参数、删项的唯一来源是
# 工具/gpu-test-loop/wifi-rounds.py（--spec），这里只出包，出完用它的 --check-payload 核对。这个脚本不碰手机。
#   make-wifi-rounds.sh               A B C1 C2：头四轮
#   make-wifi-rounds.sh C             C 的全部变体（C3、C4 各两支，C5 六种），无人值守跑批前一次出齐，约 12 张、每张约 190 MB
#   make-wifi-rounds.sh C3-cbc0 B2 ...  指定的轮次，名字见 wifi-rounds.py 不带参数的输出
#   make-wifi-rounds.sh D '<开关>'    设置页那一轮，开关是矩阵里胜出的（wifi-rounds.py 跑完会打印这条命令）。要手输密码、人用 900 s：
#                                     种子 s7-final，面板 65，删项跟胜出的那一轮（都在 --spec 里）
#   make-wifi-rounds.sh seed          只做 s7-wifi 种子
#   DRY=1 make-wifi-rounds.sh ...     只打印每轮的环境和参数
# 前提：
#   内核是带协议栈的新内核（UniXNU/components.toml 的 PaganiWifi 配方已有 --with-ath12k），./unixnu kernel 跑过，主线完成
#   C 轮命令行最长 1015 字节：要 memory-boot 把 UniXNU/scripts/unixnu/payload.py 的上限放宽到 1020，否则 C1 出包时被拒绝
#   C 轮的种子 s7-wifi：wifi/wifi-firmware-seed 里要有 join.txt，先在自己的终端跑（密码在终端输入，不进命令行）：
#     python3 build-userspace/a830-inputs/wifi/make-wifi-firmware-dir.py - build-userspace/a830-inputs/wifi/wifi-firmware-seed \
#         --join-ssid '<网络名>' --odm build-userspace/a830-inputs/wifi/odm-firmware
# join.txt 是明文密码：s7-wifi.dbrdisk、wifi-firmware-seed 和每一张 C 轮镜像都带着它。只留本机，跑完删掉（wifi.md 第 5.7 节）。
# 这几处之外还有：最后一次出 C 轮时的中间产物，$OUT、UniXNU/out-gpu 的 loop-m8-phone-island-apps.img 和 payload 目录。它们不归这个脚本，
# 删掉 C 轮镜像后再出一张不带 join.txt 的（make-wifi-rounds.sh A）把它们盖掉。
set -euo pipefail
cd /Users/xiaochoumao/unixnu-13t
F=$PWD/build-userspace/a830-inputs/final
WD=$PWD/build-userspace/a830-inputs/wifi
ROUNDS=工具/gpu-test-loop/wifi-rounds.py
OUT="测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用.img"
# make-stage15.sh 的 BASE：要删的项由 --spec 的 drop 走 A830_DROP_ARGS 删掉，这里不改，基线只有一份
BASE="pagani-gpu-status=3 sheng-no-lc=1 pagani-haptics=1 pagani-haptics-ios=2 pagani-audio-srzl=1 pagani-halt=2 pagani-rtc=1 pagani-diag=1 pagani-kbtrace=1"
FIX="$(tr '\n' ' ' < build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt)"
S7=$WD/s7-wifi.dbrdisk

seed() { # 在 s6-final 上加 wifi-firmware-seed（join.txt 和 /odm 的 BDF、regdb）。s6 的 owner trust 不变：这些只是数据
  local dir=$WD/wifi-firmware-seed
  [ -f "$dir/join.txt" ] || { echo "没有 $dir/join.txt：先跑本文件开头那条 make-wifi-firmware-dir.py" >&2; exit 1; }
  [ -f "$dir/peach/bdwlan.elf" ] && [ -f "$dir/peach/regdb.bin" ] || echo "注意：$dir 没有 /odm 的 BDF 或 regdb，驱动会回退 modem_a 那份（发射功率、合规数据不是安卓用的）" >&2
  rm -f "$S7" "${S7%.dbrdisk}.json"
  python3 -B 工具/make-phone-seed.py --disable-jobs "" --wifi-firmware "$dir" "$F/s6-final.dbrdisk" "$S7"
  chmod 600 "$S7"
  ls -la "$S7"
}

one() { # 轮名 [D 轮的开关]
  local name=$1 winner=${2:-} line image= reset= panel= seed_name= drop= args=
  while IFS= read -r line; do
    case $line in
      image=*) image=${line#image=} ;;
      reset=*) reset=${line#reset=} ;;
      panel=*) panel=${line#panel=} ;;
      seed=*) seed_name=${line#seed=} ;;
      drop=*) drop=${line#drop=} ;;
      args=*) args=${line#args=} ;;
    esac
  done < <(python3 -B "$ROUNDS" --spec "$name" --winner "$winner")
  [ -n "$image" ] || { echo "没有这一轮：$name" >&2; exit 1; }
  local prebuilt=$F/$seed_name.dbrdisk trust=$F/$seed_name-owner-trust.bundle
  if [ "$seed_name" = s7-wifi ]; then prebuilt=$S7; trust=$F/s6-final-owner-trust.bundle; fi
  echo "=== $name: $image reset=$reset panel=$panel seed=$seed_name drop=$drop args=$args"
  if [ -n "${DRY:-}" ]; then
    echo "A830_DROP_ARGS='$drop' A830_PREBUILT_SEED=$prebuilt A830_OWNER_TRUST=$trust A830_RESET_AFTER=$reset A830_PANEL=$panel"
    echo "A830_EXTRA_ARGS='$BASE sheng-log=1 $args'"
    return
  fi
  [ -f "$prebuilt" ] || { echo "没有种子 $prebuilt：先跑 make-wifi-rounds.sh seed" >&2; exit 1; }
  A830_DROP_ARGS="$drop" A830_PREBUILT_SEED=$prebuilt A830_OWNER_TRUST=$trust A830_AUDIO=1 A830_SYSTEM_APPS=1 A830_BATTERY=1 \
  A830_RESET_AFTER=$reset A830_PANEL=$panel A830_EXTRA_FIXUPS="$FIX" A830_EXTRA_ARGS="$BASE sheng-log=1 $args" 工具/make-a830-layout.sh phone-island-apps
  cp "$OUT" "测试镜像/$image"
  cp "${OUT%.img}.payload.json" "测试镜像/${image%.img}.payload.json"
  shasum -a 256 "测试镜像/$image" | cut -c1-16
  python3 -B "$ROUNDS" --check-payload "$name" --winner "$winner"
}

ALL_C="C1 C2 C3-cbc0 C4-cbc0 C3-cbc1 C4-cbc1 C5-C1 C5-C2 C5-C3-cbc0 C5-C4-cbc0 C5-C3-cbc1 C5-C4-cbc1"
case ${1:-} in
  '') for name in A B C1 C2; do one "$name"; done ;;
  seed) seed ;;
  C) for name in $ALL_C; do one "$name"; done ;;
  D) one D "${2:-}" ;;
  *) for name in "$@"; do one "$name"; done ;;
esac
