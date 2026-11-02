#!/bin/sh
# PaganiWifiTables.h 自检：
#  1. 主机编译（C++17 与 C++2b，-Wall -Wextra -Werror）并运行 tables-selftest（静态断言 + 运行期交叉核对）；
#  2. 用内核树里真实的 XNU 编译记录（只读）把同一个文件编成 ARM64 内核目标文件，证明头文件能用内核参数编译。
# 只写临时目录（默认 $TMPDIR 下新建），不写 UniXNU/out-gpu，不动任何仓库。
# 用法：sh check-tables.sh [内核树目录]   （默认 UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree）
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../.." && pwd)                        # unixnu-13t
TREE=${1:-"$ROOT/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree"}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/pagani-wifi-tables.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

for std in c++17 c++2b; do
  xcrun clang++ -std=$std -Wall -Wextra -Werror -Wshadow -Wconversion -Wno-sign-conversion -O1 \
    -o "$WORK/selftest-$std" "$HERE/tables-selftest.cpp"
  "$WORK/selftest-$std"
done

RECORD="$TREE/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json"
if [ ! -f "$RECORD" ]; then
  echo "没有内核编译记录：$RECORD（跳过内核目标文件检查）" >&2
  exit 2
fi
python3 -I - "$RECORD" "$HERE/tables-selftest.cpp" "$WORK/tables-selftest.o" <<'EOF'
import json, subprocess, sys
record_path, source, obj = sys.argv[1:4]
record = json.loads(open(record_path).read().strip().rstrip(','))
args, out, i = record['arguments'], [], 0
while i < len(args):
    a = args[i]
    if a == record['file']:
        out.append(source)
    elif a == '-o':
        out += ['-o', obj]; i += 1
    elif a in ('-MF', '-MJ', '-MT', '-MQ'):
        i += 1
    elif a == '-Xclang' and i + 1 < len(args) and args[i + 1].startswith('-W'):
        i += 1
    elif not a.startswith('-W') and a not in ('-MD', '-MP', '-MMD'):
        out.append(a)
    i += 1
out += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter']
subprocess.run(out, check=True, cwd=record.get('directory'))
EOF
file "$WORK/tables-selftest.o"
xcrun nm -m "$WORK/tables-selftest.o" | grep pagani_wifi_tables_digest
echo "PaganiWifiTables.h：主机自检与内核参数编译均通过"
