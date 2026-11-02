#!/bin/zsh
# ida-run.sh <script.py> <database.i64> <out> args...   (runs idat headless, then names the stub-island calls in <out>)
set -e
W=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/widgets
IDAT="/Applications/IDA Professional 9.5.app/Contents/MacOS/idat"
script=$1; db=$2; out=$3; shift 3
cd "$(dirname $db)"
TERM=xterm "$IDAT" -A -L"$out.log" -S"$script $* " "$(basename $db)" > /dev/null 2>&1 || true
