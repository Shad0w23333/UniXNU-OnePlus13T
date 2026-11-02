#!/bin/bash
# Host tests of the photo-free test loop; nothing touches a phone:
#   the kernel writer (BootProgress/Test/ramoops.c, the actual RamoopsLog.c), the decoder with injected bit flips on the
#   writer's zone images (layout 3) and on rebuilt layout-2 images, gpu-loop.py --yes / read-ramoops.py against fake adb
#   and fastboot, the crash reports (BootProgress/Test/crash.c: CrashTrace.h's frame walk, naming and rate limit on simulated
#   user memory; then its X records through the writer, the decoder and symbolize-crashes.py), and a re-decode of every saved
#   evidence directory in runs/ (output to a temporary directory only). The UFS log: its kernel writer (BootProgress/Test/ufslog.c, the
#   actual UfsLog.c on the actual RamoopsLog.c), the same with threads under TSan (ufslog-threads.c), the reader on that writer's windows and
#   what becomes of them (tests/test_ufslog.py). The stall probe: StallProbe.h (BootProgress/Test/stall.c under ASan/UBSan, stall-threads.c under TSan), its
#   glue in BootProgress.c over a fake of XNU's side (stall-glue.c), its records through the writer, the decoder, symbolize-crashes.py and stall-report.py, the
#   patch's copy of the structures against StallProbe.h, and the keyboard trace's version 3 with the probe's table of open pairs (tests/test_stall.py).
# RAMOOPS_WRITER_TEST=<path to ramoops.c>, CRASH_TRACE_TEST=<path to crash.c>, UFS_LOG_TEST=<path to ufslog.c> and STALL_TEST=<path to stall.c> override the test locations; CRASH_A2S=<ipsw
# A2S cache of the 21H461 dyld shared cache> adds a check against the real cache symbols.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
W=${RAMOOPS_WRITER_TEST:-$HERE/../../UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ramoops.c}
C=${CRASH_TRACE_TEST:-$HERE/../../UniXNU-Pagani-Drivers/drivers/BootProgress/Test/crash.c}
U=${UFS_LOG_TEST:-$HERE/../../UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ufslog.c}
Q=${STALL_TEST:-$HERE/../../UniXNU-Pagani-Drivers/drivers/BootProgress/Test/stall.c}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/gpu-test-loop.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
export TMPDIR=$WORK PYTHONDONTWRITEBYTECODE=1
cc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined "$W" -o "$WORK/ramoops"
ASAN_OPTIONS=detect_leaks=0 "$WORK/ramoops" | tail -1
python3 "$HERE/tests/test_decode.py" --writer-test "$W" --keep "$WORK/zones"
python3 "$HERE/tests/test_loop_mock.py" --zones "$WORK/zones"
cc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined "$C" -o "$WORK/crash"
ASAN_OPTIONS=detect_leaks=0 "$WORK/crash" | tail -1
python3 "$HERE/tests/test_crash.py" --crash-test "$C"
cc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined "$U" -o "$WORK/ufslog"
ASAN_OPTIONS=detect_leaks=0 "$WORK/ufslog" | tail -1
cc -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=thread "$(dirname "$U")/ufslog-threads.c" -o "$WORK/ufslog-threads"
TSAN_OPTIONS=halt_on_error=1 "$WORK/ufslog-threads" | tail -1
python3 "$HERE/tests/test_ufslog.py" --writer-test "$U"
cc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined "$Q" -o "$WORK/stall"
ASAN_OPTIONS=detect_leaks=0 "$WORK/stall" | tail -1
cc -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -I"$(dirname "$Q")/../../PaganiDarwinBoot/DarwinBootPkg/Driver" "$(dirname "$Q")/stall-glue.c" -o "$WORK/stall-glue"
ASAN_OPTIONS=detect_leaks=0 "$WORK/stall-glue" | tail -1
cc -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=thread "$(dirname "$Q")/stall-threads.c" -o "$WORK/stall-threads"
TSAN_OPTIONS=halt_on_error=1 "$WORK/stall-threads" | tail -1
python3 "$HERE/tests/test_stall.py" --stall-test "$Q"
for d in "$HERE"/runs/*/; do
  ls "$d" | grep -q -- '-ramoops-0' || continue
  status=0
  python3 "$HERE/read-ramoops.py" --from-dir "$d" --runs "$WORK/redecoded" > "$WORK/redecode.txt" || status=$?
  [ $status -eq 0 ] || [ $status -eq 3 ] || { cat "$WORK/redecode.txt"; exit 1; }
  echo "ok  re-decoded runs/$(basename "$d"): $(grep -m1 '^records ' "$WORK/redecode.txt"); $(grep -m1 '^  layout ' "$WORK/redecode.txt" | cut -c3- | cut -d, -f1)"
done
