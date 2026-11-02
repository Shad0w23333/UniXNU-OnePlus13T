#!/bin/bash
# All BootProgress host tests (ramoops, console, crash, gunyah-wdog at -O2 and -O0 with ASan/UBSan, panic-entry.py), then
# 工具/gpu-test-loop/run-tests.sh. Builds into a temporary directory; nothing touches a phone or the kernel build tree (the
# patched XNU source and the font are only read).
# usage: run-host-tests.sh [BootProgress dir]
set -euo pipefail
ROOT=/Users/xiaochoumao/unixnu-13t
BP=${1:-$ROOT/UniXNU-Pagani-Drivers/drivers/BootProgress}
FONT=$ROOT/UniXNU-Pagani-Drivers/drivers/PaganiDarwinBoot/DarwinBootPkg/Driver
XNU=$ROOT/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work
WORK=$(mktemp -d "${TMPDIR:-/tmp}/bp-host-tests.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
export ASAN_OPTIONS=detect_leaks=0
for opt in -O2 -O0; do
  for t in ramoops console crash gunyah-wdog; do
    cc -std=c11 $opt -Wall -Wextra -Werror -fsanitize=address,undefined -I "$FONT" "$BP/Test/$t.c" -o "$WORK/$t"
    printf '%s %s: ' "$opt" "$t"
    "$WORK/$t" | tail -1 | cut -c1-110
  done
done
python3 "$BP/Test/panic-entry.py" "$XNU" --font "$FONT"
bash "$ROOT/工具/gpu-test-loop/run-tests.sh"
