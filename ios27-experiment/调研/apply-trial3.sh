#!/bin/bash
# 基线提交之上按我们的 series 顺序累计试打（只在草稿目录），统计每个补丁的成败块数。GNU patch，fuzz 2。
set -u
W=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/ipados27-parity/xnu-12377
TOP=/Users/xiaochoumao/unixnu-13t
cd "$W"
git reset -q --hard HEAD; git clean -qfd
DEV=$TOP/UniXNU-Pagani_SM8750-gpu/patches/xnu
tot=0; totf=0
try() {
  local title=$1 p=$2
  local out; out=$(/opt/homebrew/bin/gpatch -p1 -f --fuzz=2 --no-backup-if-mismatch < "$p" 2>&1 || true)
  local fail=$(echo "$out" | grep -c 'FAILED at')
  local total=$(grep -c '^@@' "$p")
  local fuzz=$(echo "$out" | grep -c 'with fuzz')
  local missing=$(echo "$out" | grep -c "can't find file\|No file to patch\|File to patch")
  printf '%-7s %-46s 块 %3d  失败 %3d  模糊 %2d  缺文件 %d\n' "$title" "$(basename $p)" "$total" "$fail" "$fuzz" "$missing"
  [ "$fail" -gt 0 ] && echo "$out" | grep -E '^patching file|FAILED at' | grep -B1 'FAILED' | grep '^patching' | sed 's/^patching file /          -> /' | sort -u | head -6
  tot=$((tot+total)); totf=$((totf+fail))
}
try soc $TOP/UniXNU-sm8750-common/patches/xnu/sm8750-smp.patch
try arch $TOP/UniXNU/patches/xnu/native-arch-timer-frequency.patch
for p in $(grep -v '^#' $DEV/series); do try device $DEV/$p; done
echo "合计 块 $tot 失败 $totf"
