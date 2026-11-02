#!/bin/bash
# 整合后的全量主机回归。每项退出码一行，输出在 regress/ 下，一项失败不影响后面。
cd /Users/xiaochoumao/unixnu-13t
OUT=build-userspace/a830-inputs/round-1101/regress
mkdir -p $OUT
TREE=$PWD/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree
INPUTS=$PWD/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/xnu-inputs.json
XNU=$PWD/UniXNU/out-gpu/feeds/xnu-b7545ced
run() { # 名字 目录 命令...
  local name=$1 dir=$2; shift 2
  ( cd "$dir" && "$@" ) > "$OUT/$name.txt" 2>&1
  printf '%-22s 退出码 %s\n' "$name" "$?"
}
run adreno830 Adreno830 python3 tests/run-host-tests.py
run ufs . bash build-userspace/a830-inputs/ufslog/tools/run-host-tests.sh
run peripherals . python3 UniXNU-Pagani_SM8750-gpu/scripts/test-peripherals.py --tree "$TREE"
run diag UniXNU-Pagani-Drivers/drivers/PaganiDiag/Test bash run.sh
run gpu-loop . bash 工具/gpu-test-loop/run-tests.sh
run wifi-rounds 工具/gpu-test-loop python3 -B tests/test_wifi_rounds.py
run adsp-rounds 工具/gpu-test-loop python3 -B tests/test_adsp_rounds.py
run wifi-host . python3 UniXNU-Pagani-Drivers/drivers/PaganiWifi/Test/check-pagani-wifi-host.py
run battery UniXNU-Pagani-Drivers/drivers/PaganiBattery python3 -B Test/check-pagani-battery-host.py --tree "$TREE"
run bt . python3 UniXNU-Pagani-Drivers/drivers/PaganiBt/Test/check-pagani-bt-host.py
run camera . python3 -B UniXNU-Pagani-Drivers/drivers/PaganiCamera/Test/check-pagani-camera-host.py --tree "$TREE"
run torch . python3 -B UniXNU-Pagani-Drivers/drivers/PaganiTorch/Test/check-pagani-torch-host.py
run audio . python3 -B UniXNU-Pagani-Drivers/drivers/PaganiAudio/scripts/check-pagani-audio-host.py
run haptics . python3 -B UniXNU-Pagani-Drivers/drivers/PaganiHaptics/Test/check-pagani-haptics-host.py
run speaker . python3 -B UniXNU-Pagani-Drivers/drivers/PaganiSpeaker/Test/check-pagani-speaker-host.py
run adsp . python3 -B UniXNU-Pagani-Drivers/drivers/PaganiAdsp/Test/check-pagani-adsp-host.py
run layout . python3 -B 工具/test-phone-island-layout.py
run payload UniXNU python3 -B scripts/unixnu/test_payload.py
run seed-edit . python3 -B 工具/test-edit-phone-seed-launchd.py
run seed-check . python3 -B 工具/test-check-keyboard-seed.py
run userspace build-userspace/tree python3 -B scripts/check-adreno-userspace-host.py
run cache-budget . python3 -I build-userspace/shader-cache-tools/tests/test_merge_budget.py
run cache-harvest . python3 -I build-userspace/shader-cache-tools/tests/test_harvest.py
for p in UniXNU-Pagani_SM8750-gpu/scripts/test-*-patch.py; do
  n=$(basename "$p" .py)
  case "$n" in
    test-idle-flush-patch) run "$n" . python3 -B "$p" ;;
    test-vm-page-limits-patch) run "$n" . python3 -B "$p" --inputs "$INPUTS" --tree "$TREE" ;;
    test-app-exec-count-patch) run "$n" . python3 -B "$p" --inputs "$INPUTS" --xnu "$XNU" ;;
    *) run "$n" . python3 -B "$p" --inputs "$INPUTS" ;;
  esac
done
echo 回归结束
