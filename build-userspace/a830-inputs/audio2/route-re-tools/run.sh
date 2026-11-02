#!/bin/zsh
# usage: run.sh DB snippet out
export PYTHONPATH="/Applications/IDA Professional 9.5.app/Contents/MacOS/idalib/python"
exec python3.13 $(dirname "$0")/q.py "$@"
