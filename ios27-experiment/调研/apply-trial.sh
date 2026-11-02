#!/bin/bash
# 只在草稿目录的 xnu-12377 工作树里试打补丁，统计我们的补丁能否落到 12377 上。
set -u
W=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/ipados27-parity/xnu-12377
TOP=/Users/xiaochoumao/unixnu-13t
U=$TOP/参考资料/上游-20261031/UniXNU-UniXNU
SOC=$TOP/参考资料/上游-20261031/UniXNU-UniXNU-sm8550-common
SH=$TOP/参考资料/上游-20261031/UniXNU-UniXNU-sheng
cd "$W"
git checkout -q -f FETCH_HEAD 2>/dev/null || git checkout -q -f ac9718fb1af618d5ce8678d0dc6e8a58f252216f
git clean -qfd
apply_list() {  # 标题 目录 文件名...
  local title=$1 dir=$2; shift 2
  local ok=0 bad=0
  for p in "$@"; do
    case "$p" in '#'*|'') continue;; esac
    if git apply --whitespace=nowarn "$dir/$p" 2>/tmp/ap.err; then ok=$((ok+1)); else bad=$((bad+1)); echo "  FAIL $title: $p: $(head -2 /tmp/ap.err | tr '\n' ' ' | cut -c1-140)"; fi
  done
  echo "== $title: 成功 $ok 失败 $bad"
}
# 上游 arm64e 系列
apply_list 'arch arm64e' "$U/profiles/arch/arm64e/patches/xnu" $(grep -v '^#' "$U/profiles/arch/arm64e/patches/xnu/series")
apply_list 'soc sm8550' "$SOC/patches/xnu-12377" $(grep -v '^#' "$SOC/patches/xnu-12377/series")
apply_list 'os 27' "$U/profiles/arch/arm64e" zzz-placeholder 2>/dev/null | grep -v placeholder | grep -v '成功 0 失败 1' 
for p in zzz-boot-progress darwin26-syscalls darwin26-kpi darwin26-shared-region; do apply_list "os27" "$U/patches/xnu-12377" "$p.patch"; done
apply_list 'device sheng' "$SH/patches/xnu-12377" $(grep -v '^#' "$SH/patches/xnu-12377/series")
git add -A >/dev/null 2>&1
git -c user.email=a@b -c user.name=x commit -qm base >/dev/null 2>&1
echo "基线树已打完，开始试我们的补丁（--check，每个单独相对基线）"
try() {
  local title=$1 p=$2
  if git apply --check --whitespace=nowarn "$p" 2>/tmp/ap.err; then echo "OK    $title $(basename $p)"; else
    # 再用 patch --dry-run 看有多少 hunk 失败
    local out; out=$(patch -p1 --dry-run -f --fuzz=3 < "$p" 2>&1)
    local fail=$(echo "$out" | grep -c 'FAILED at')
    local total=$(grep -c '^@@' "$p")
    local fuzz=$(echo "$out" | grep -c 'with fuzz')
    local missing=$(echo "$out" | grep -c "can't find file\|No file to patch")
    echo "CONF  $title $(basename $p): 共 $total 块, 失败 $fail 块, 模糊 $fuzz, 缺文件 $missing"
  fi
}
DEV=$TOP/UniXNU-Pagani_SM8750-gpu/patches/xnu
for p in $(grep -v '^#' $DEV/series); do try device $DEV/$p; done
try soc $TOP/UniXNU-sm8750-common/patches/xnu/sm8750-smp.patch
try arch $TOP/UniXNU/patches/xnu/native-arch-timer-frequency.patch
