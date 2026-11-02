#!/bin/bash
# Every host test the UFS log touches, in a temporary directory; nothing touches a phone or the kernel build tree (the patched XNU
# source and the font are only read):
#   BootProgress (ramoops, console, crash, gunyah-wdog, ufslog, ufslog-threads) at -O2 and -O0 with ASan/UBSan, ufslog-threads with TSan,
#   panic-entry.py;
#   NativeUfs (check_root_path_host.py: root path plus RootCommands, LogWindow and LogInterface with the kernel's flags, -O0; the same three at
#   -O2; the remaining unit tests; the legacy descriptor check; the production commands at EL1 on QEMU's UFS device, writes included);
#   工具/gpu-test-loop/run-tests.sh (writers, decoders, the fake phone).
# usage: run-host-tests.sh
set -euo pipefail
ROOT=/Users/xiaochoumao/unixnu-13t
BP=$ROOT/UniXNU-Pagani-Drivers/drivers/BootProgress
UFS=$ROOT/UniXNU-Pagani-Drivers/drivers/NativeUfs
FONT=$ROOT/UniXNU-Pagani-Drivers/drivers/PaganiDarwinBoot/DarwinBootPkg/Driver
XNU=$ROOT/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work
PLATFORM=$ROOT/UniXNU-sm8750-common/kits
WORK=$(mktemp -d "${TMPDIR:-/tmp}/ufslog-host-tests.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
export ASAN_OPTIONS=detect_leaks=0
for opt in -O2 -O0; do
  for t in ramoops console crash gunyah-wdog ufslog ufslog-threads; do
    cc -std=c11 $opt -Wall -Wextra -Werror -fsanitize=address,undefined -I "$FONT" "$BP/Test/$t.c" -o "$WORK/$t"
    printf '%s %s: ' "$opt" "$t"
    "$WORK/$t" | tail -1 | cut -c1-110
  done
done
for opt in -O1 -O2; do
  cc -std=c11 $opt -g -Wall -Wextra -Werror -fsanitize=thread "$BP/Test/ufslog-threads.c" -o "$WORK/ufslog-threads-tsan"
  printf '%s TSan ufslog-threads: ' "$opt"
  TSAN_OPTIONS=halt_on_error=1 "$WORK/ufslog-threads-tsan" | tail -1 | cut -c1-110
done
python3 "$BP/Test/panic-entry.py" "$XNU" --font "$FONT"
python3 "$UFS/tests/check_root_path_host.py" --output "$WORK/rootpath"
KERNEL="-DNATIVE_UFS_SM8750=1 -DNATIVE_UFS_READ_ONLY=1 -DNATIVE_UFS_LOG_WRITES=1"
for t in RootCommands LogWindow; do
  clang++ -std=c++17 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined $KERNEL "$UFS/tests/$t.cpp" -o "$WORK/$t-O2"
  printf -- '-O2 %s: ' "$t"
  "$WORK/$t-O2" | tail -1 | cut -c1-110
done
cc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined -c "$BP/Test/ufslog-e2e.c" -o "$WORK/ufslog-e2e.o"
clang++ -std=c++17 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined $KERNEL "$UFS/tests/LogInterface.cpp" "$WORK/ufslog-e2e.o" -o "$WORK/LogInterface-O2"
printf -- '-O2 LogInterface: '
"$WORK/LogInterface-O2" | tail -1 | cut -c1-110
for t in LunDiscovery TransferPoll SmmuTakeover Sm8750; do
  clang++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -DNATIVE_UFS_SM8750=1 '-DUFS_ROOT_PARTITION="unixnu"' -I "$PLATFORM" \
    "$UFS/tests/$t.cpp" -o "$WORK/$t"
  printf '%s: ' "$t"
  "$WORK/$t" | tail -1 | cut -c1-110
done
python3 "$UFS/tests/check_legacy_descriptors.py" --output "$WORK/legacy" | tail -1
# The production commands at EL1 on QEMU's UFS device, WRITE(10) and SYNCHRONIZE CACHE(10) included (case log-writes), and the root path.
PATH="/opt/homebrew/opt/llvm/bin:/opt/homebrew/bin:$PATH" python3 "$UFS/tests/check_arm64_root_commands.py" --output "$WORK/arm64" | grep -E '^(PASS LOG WRITE|PASS ALL|PASS rejected)' | sort | uniq -c
PATH="/opt/homebrew/opt/llvm/bin:/opt/homebrew/bin:$PATH" python3 "$UFS/tests/check_qemu_root_commands.py" --output "$WORK/qemu" | grep -c '"pass"' | sed 's/$/ passes in the QEMU root path report/'
bash "$ROOT/工具/gpu-test-loop/run-tests.sh"
