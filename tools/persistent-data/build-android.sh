#!/bin/bash
# 交叉编译安卓端工具 unixnu-datafile（NDK clang，静态 aarch64，-Wall -Wextra -Werror）。只编译，不推送、不碰手机。
# 共用头文件来自驱动仓库 drivers/PaganiPersistentData（格式、构建、SHA-256 与 XNU 侧完全同一份）。
# 用法：build-android.sh [输出路径]（默认 out/unixnu-datafile）
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
INC=$(cd "$HERE/../../UniXNU-Pagani-Drivers/drivers/PaganiPersistentData" && pwd)
OUT=${1:-$HERE/out/unixnu-datafile}
NDK_BIN=${NDK_BIN:-}
if [ -z "$NDK_BIN" ]; then
    for v in 28.2.13676358 27.1.12297006; do
        d="$HOME/Library/Android/sdk/ndk/$v/toolchains/llvm/prebuilt/darwin-x86_64/bin"
        if [ -x "$d/aarch64-linux-android31-clang" ]; then NDK_BIN=$d; break; fi
    done
fi
[ -n "$NDK_BIN" ] || { echo "找不到 NDK（设 NDK_BIN=.../toolchains/llvm/prebuilt/darwin-x86_64/bin）" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
"$NDK_BIN/aarch64-linux-android31-clang" -std=c11 -O2 -Wall -Wextra -Werror -Wno-unused-function -static \
    -I"$INC" -o "$OUT" "$HERE/unixnu-datafile.c"
file "$OUT" | sed 's/, with debug_info.*//'
shasum -a 256 "$OUT"
