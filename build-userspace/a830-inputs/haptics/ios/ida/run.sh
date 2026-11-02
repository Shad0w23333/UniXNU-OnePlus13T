#!/bin/bash
# usage: run.sh <binary name in ../bin> <targets file> <out file> [full]
# Creates/opens ida/<name>.i64 (database kept here, never next to the binary).
H=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/haptics/ios
IDAT="/Applications/IDA Professional 9.5.app/Contents/MacOS/idat"
name=$1; targets=$2; outf=$3; full=${4:-}
db=$H/ida/$name.i64
export DECOMP_TARGETS=$targets DECOMP_OUT=$outf
[ "$full" = full ] && export DECOMP_FULL=1
if [ -f "$db" ]; then
  "$IDAT" -A -S"$H/ida/decomp.py" -L"$H/ida/$name.log" "$db"
else
  "$IDAT" -A -S"$H/ida/decomp.py" -L"$H/ida/$name.log" -o"$db" "$H/bin/$name"
fi
