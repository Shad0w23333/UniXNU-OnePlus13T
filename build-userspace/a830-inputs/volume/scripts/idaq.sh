#!/bin/zsh
# usage: idaq.sh DB.i64 job.json
DB="$1"; JOB="$2"
IDAQ_JOB="$JOB" "/Applications/IDA Professional 9.5.app/Contents/MacOS/idat" -A -S/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/volume/scripts/idaq.py -L"${JOB%.json}.idalog" "$DB" > /dev/null 2>&1
echo "exit $?"
