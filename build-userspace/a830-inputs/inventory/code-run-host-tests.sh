#!/bin/bash
# Host test sweep for the 13T driver inventory (inventory/code.md). Read-only on the sources; binaries and logs go to OUT.
# Usage: bash code-run-host-tests.sh [OUT]   (default $TMPDIR/code-host-tests). Known non-code failure: nrb-sparse (stale 2 GiB
# assertion against SparseDisk.h's 1 TiB limit since the import commit 0d0b413).
set -u
D=/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers
SOC=/Users/xiaochoumao/unixnu-13t/UniXNU-sm8750-common/kits/Sm8750Platform
TREE=/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree
OUT=${1:-${TMPDIR:-/tmp}/code-host-tests}
B=$OUT/bin
LOG=$OUT/logs
mkdir -p "$B" "$LOG"
SAN="-fsanitize=address,undefined -fno-sanitize-recover=all"
t() { # name, command...
  local name=$1; shift
  local start=$(date +%s)
  if ( "$@" ) >"$LOG/$name.log" 2>&1; then
    echo "PASS $name $(( $(date +%s)-start ))s :: $(tail -1 "$LOG/$name.log" | cut -c1-200)"
  else
    echo "FAIL $name rc=$? $(( $(date +%s)-start ))s :: $(tail -3 "$LOG/$name.log" | tr '\n' ' ' | cut -c1-400)"
  fi
}
# BootProgress
t bp-ramoops  bash -c "cd $D/BootProgress/Test && xcrun clang -std=c11 -O2 -Wall -Wextra -Werror $SAN ramoops.c -o $B/ramoops && $B/ramoops"
t bp-crash    bash -c "cd $D/BootProgress/Test && xcrun clang -std=c11 -O2 -Wall -Wextra -Werror $SAN crash.c -o $B/crash && $B/crash"
t bp-gwdog    bash -c "cd $D/BootProgress && xcrun clang -std=c11 -O2 -Wall -Wextra -Werror $SAN Test/gunyah-wdog.c -o $B/gunyah-wdog && $B/gunyah-wdog"
t bp-console  bash -c "cd $D/BootProgress/Test && xcrun clang -std=c11 -O1 -g -Wall -Wextra $SAN -I $D/PaganiDarwinBoot/DarwinBootPkg/Driver -I $TREE/build/boot-progress console.c -o $B/console && $B/console"
# NativeMobileDisplay
t nmd-request-state bash -c "cd $D/NativeMobileDisplay/Test && xcrun clang -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined request-state.c -o $B/request-state && $B/request-state"
for x in source-cache display-timing scanout-flip dpu-safety gop-handoff frame-rate frame-timing; do
  t nmd-$x bash -c "cd $D/NativeMobileDisplay/Test && xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined $x.cpp -o $B/$x && $B/$x"
done
# NativeRamBlock
SEED=/Users/xiaochoumao/unixnu-13t/工具/seeds/phone-island-apps-audio.dbrdisk
for x in direct persist seeddisk sparse; do
  t nrb-$x bash -c "cd $D/NativeRamBlock/Test && xcrun clang++ -std=c++17 -O1 -g -Wall -Wextra -fsanitize=address,undefined $x.cpp -o $B/nrb-$x && $B/nrb-$x"
done
# packed-hash / seed-identity are tools: they print the logical sha256 of a packed seed (compare with the seed .json output.sha256)
for x in packed-hash seed-identity; do
  t nrb-$x bash -c "cd $D/NativeRamBlock/Test && xcrun clang++ -std=c++17 -O1 -g -Wall -Wextra -fsanitize=address,undefined $x.cpp -o $B/nrb-$x && $B/nrb-$x $SEED"
done
# PaganiArmCpu
for x in spi-gate scmi-perf psci-idle; do
  t armcpu-$x bash -c "cd $D/PaganiArmCpu && xcrun clang++ -std=c++17 -O1 -g -Wall -Wextra -Werror $SAN Test/$x.cpp -o $B/$x && $B/$x"
done
# PaganiPanel
t panel-power bash -c "cd $D/PaganiPanel && xcrun clang++ -std=c++17 -O1 -g -Wall -Wextra -Werror $SAN -I $SOC Test/panel-power.cpp -o $B/panel-power && $B/panel-power"
# PaganiInput (+ QcomGeniSpi) through the device repo's runner
t input-peripherals python3 -B /Users/xiaochoumao/unixnu-13t/UniXNU-Pagani_SM8750-gpu/scripts/test-peripherals.py --tree "$TREE"
# PaganiSpeaker (no runner in the tree: compiled by hand)
S=$D/PaganiSpeaker
t spk-tone     bash -c "xcrun clang -std=c11 -O1 -g -Wall -Wextra -Werror $SAN -I $S $S/Test/tone.c $S/SpeakerTone.c -lm -o $B/spk-tone && $B/spk-tone"
t spk-protocol bash -c "xcrun clang -std=c11 -O1 -g -Wall -Wextra -Werror $SAN -I $S $S/Test/protocol.c $S/PaganiGpr.c $S/PaganiReach.c -o $B/spk-protocol && $B/spk-protocol"
t spk-amp      bash -c "xcrun clang -std=c11 -O1 -g -Wall -Wextra -Werror $SAN -I $S $S/Test/amp.c $S/GeniI2c.c $S/Tfa9873.c -o $B/spk-amp && $B/spk-amp"
t spk-hw       bash -c "xcrun clang -std=c11 -O1 -g -Wall -Wextra -Werror $SAN -I $S -I $S/Test $S/Test/hw.c $S/SpeakerHw.c $S/GeniI2c.c $S/Tfa9873.c -o $B/spk-hw && $B/spk-hw"
t spk-core     bash -c "xcrun clang -std=c11 -O1 -g -Wall -Wextra -Werror $SAN -I $S -I $S/Test -I $D/PaganiAdsp -I $D/PaganiAudio $S/Test/core.c $S/SpeakerCore.c $S/SpeakerBackend.c $S/SpeakerHw.c $S/GeniI2c.c $S/Tfa9873.c $S/PaganiGpr.c $S/PaganiReach.c $S/SpeakerTone.c $D/PaganiAudio/PaganiAudioEngine.c -lm -o $B/spk-core && $B/spk-core"
# Component check scripts
t adsp-host    python3 -B $D/PaganiAdsp/Test/check-pagani-adsp-host.py --gpt-dir /Users/xiaochoumao/unixnu-13t/一加13T原厂备份/分区表
t audio-host   python3 -B $D/PaganiAudio/scripts/check-pagani-audio-host.py --tree "$TREE"
t battery-host python3 -B $D/PaganiBattery/Test/check-pagani-battery-host.py --tree "$TREE"
t persistent-data bash $D/PaganiPersistentData/Test/run.sh
# "patch" needs the pristine kern_exec.c (the built tree is already patched); "trust" needs the old scratchpad bundle
t diag-run     bash $D/PaganiDiag/Test/run.sh build
t haptics-host python3 -B $D/PaganiHaptics/Test/check-pagani-haptics-host.py
t darwinboot   bash -c "cd $D/PaganiDarwinBoot/Test && python3 -B test-packed-ramdisk.py && python3 -B test-adt.py"
t ufs-rootpath python3 -B $D/NativeUfs/tests/check_root_path_host.py --output $B/ufs --gpt-dir /Users/xiaochoumao/unixnu-13t/一加13T原厂备份/分区表
t ksevents     python3 -B $D/NativeKeyStoreEvents/test_closed_boundary.py
echo DONE
