#!/bin/bash
# After a run with sheng-log=1: reads again, read-only, what baseline-20261031.txt recorded outside the log window and export region and
# compares. The GPT, the blocks below the export region and the root image must not have changed. It also holds the backup GPT against the copy
# written with the table, and the blocks the writer never touches against zeros: the last 15 of the window, and the export region when
# no run has exported. Only dd and sha256sum run on the phone. Not covered, there being no hash from before the first write: the 251 blocks above
# the window and unixnu blocks 2341632..16515071. A read that fails is reported as such, not as a change.
# usage: check-baseline.sh
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TOP=$(cd "$HERE/../../.." && pwd)
BASE=$HERE/baseline-20261031.txt
ZERO_15=$(head -c $((15 * 4096)) /dev/zero | shasum -a 256 | cut -d' ' -f1)
ZERO_EXPORT=$(head -c $((65536 * 4096)) /dev/zero | shasum -a 256 | cut -d' ' -f1)
GPT_BACKUP=$(shasum -a 256 "$TOP/UniXNU/out/dual_sys/applied2/gpt-backup.after" | cut -d' ' -f1)
now() { # retries: another adb server on this Mac (i4Tools) can take the port away for a moment
    local got attempt
    for attempt in 1 2 3 4 5; do
        got=$(adb shell "su -c \"$1 2>/dev/null | sha256sum\"" 2>/dev/null | tr -d '\r' | cut -d' ' -f1)
        [ ${#got} = 64 ] && { echo "$got"; return; }
        sleep 3
    done
}
old() { grep -A1 -F -- "$1" "$BASE" | tail -1 | cut -d' ' -f1; }
status=0
check() { # name, expected hash, command
    local is
    is=$(now "$3")
    if [ -z "$is" ]; then
        echo "UNREAD   $1  (adb)"
        status=1
    elif [ -n "$2" ] && [ "$2" = "$is" ]; then
        echo "same     $1  ${is:0:16}"
    else
        echo "CHANGED  $1  was ${2:0:16} is ${is:0:16}"
        status=1
    fi
}
check '主 GPT' "$(old '主 GPT')" 'dd if=/dev/block/sda bs=4096 count=6'
check '备份 GPT' "$GPT_BACKUP" 'dd if=/dev/block/sda bs=4096 skip=124186619 count=5'
check 'unixnu 块 16515072' "$(old 'unixnu 块 16515072')" 'dd if=/dev/block/by-name/unixnu bs=4096 skip=16515072 count=188416'
check 'unixnu 前 9147 MiB' "$(old 'unixnu 前 0x22F500')" 'dd if=/dev/block/by-name/unixnu bs=1048576 count=9147'
check '窗口末 15 块全零' "$ZERO_15" 'dd if=/dev/block/by-name/unixnu bs=4096 skip=16777201 count=15'
check '导出区全零' "$ZERO_EXPORT" 'dd if=/dev/block/by-name/unixnu bs=4096 skip=16703488 count=65536'
exit $status
