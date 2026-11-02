#!/bin/zsh
# Rebuilds phone-keyboard-trace.patch (version 2) from a pristine xnu-10063.141.1 bsd/kern/kdebug.c ($1) and the edited
# copy ($2, bsd/kern/kdebug.c of the work tree) plus pagani_kbtrace.h next to this script. Read-only on both inputs.
set -e
HERE=${0:A:h}
PRISTINE=$1; EDITED=$2
T=$(mktemp -d)
mkdir -p $T/a/bsd/kern $T/b/bsd/kern
cp $PRISTINE $T/a/bsd/kern/kdebug.c
cp $EDITED $T/b/bsd/kern/kdebug.c
cp $HERE/pagani_kbtrace.h $T/b/bsd/kern/pagani_kbtrace.h
cd $T
{ diff -u a/bsd/kern/kdebug.c b/bsd/kern/kdebug.c | sed -e '1s|^--- .*|--- a/bsd/kern/kdebug.c|' -e '2s|^+++ .*|+++ b/bsd/kern/kdebug.c|' || true
  diff -u /dev/null b/bsd/kern/pagani_kbtrace.h | sed -e '1s|^--- .*|--- /dev/null|' -e '2s|^+++ .*|+++ b/bsd/kern/pagani_kbtrace.h|' || true
} > $HERE/phone-keyboard-trace.patch
rm -rf $T
echo "wrote $HERE/phone-keyboard-trace.patch ($(wc -l < $HERE/phone-keyboard-trace.patch) lines)"
