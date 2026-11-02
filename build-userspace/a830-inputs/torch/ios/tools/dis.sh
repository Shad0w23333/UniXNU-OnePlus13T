#!/bin/bash
# usage: dis.sh 0xADDR [0xADDR...]  — symbolicated disassembly of whole functions from the 21H461 dyld shared cache (ipsw).
DSC=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64
A2S=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/dsc-21H461.a2s
for a in "$@"; do
  ipsw dyld disass "$DSC" --vaddr "$a" --cache "$A2S" --no-color 2>&1 | grep -v 'Loading symbol cache'
done
