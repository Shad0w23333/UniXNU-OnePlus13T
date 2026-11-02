#!/bin/bash
# 基线提交之上按我们的 series 顺序累计试打（只在草稿目录），统计每个补丁的成败块数。
set -u
W=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/ipados27-parity/xnu-12377
TOP=/Users/xiaochoumao/unixnu-13t
cd "$W"
git reset -q --hard HEAD; git clean -qfd
DEV=$TOP/UniXNU-Pagani_SM8750-gpu/patches/xnu
tot=0; totf=0
try() {
  local title=$1 p=$2
  local out; out=$(patch -p1 -f --fuzz=2 --no-backup-if-mismatch -r /dev/null < "$p" 2>&1 || true)
  local fail=$(echo "$out" | grep -ci 'failed at')
  local total=$(grep -c '^@@' "$p")
  local fuzz=$(echo "$out" | grep -ci 'with fuzz')
  local offs=$(echo "$out" | grep -ci 'offset')
  local missing=$(echo "$out" | grep -ci "can't find file\|No file to patch\|File to patch")
  printf '%-7s %-46s 块 %3d  失败 %3d  模糊 %2d  偏移 %2d  缺文件 %d\n' "$title" "$(basename $p)" "$total" "$fail" "$fuzz" "$offs" "$missing"
  tot=$((tot+total)); totf=$((totf+fail))
}
try soc $TOP/UniXNU-sm8750-common/patches/xnu/sm8750-smp.patch
try arch $TOP/UniXNU/patches/xnu/native-arch-timer-frequency.patch
for p in $(grep -v '^#' $DEV/series); do try device $DEV/$p; done
echo "合计 块 $tot 失败 $totf"
git status --short | grep -v '\.orig$' | grep -v '\.rej$' | wc -l
