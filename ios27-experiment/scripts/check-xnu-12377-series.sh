#!/bin/bash
# 验证 13T 的 xnu-12377 补丁系列：从原版 xnu-12377.121.6 的干净检出开始，
# 按 arch（上游 arm64e 64 个）、soc（本项目）、os（上游 24A446 4 个）、device（本项目）顺序用 git apply 打完。
# 任何一个补丁失败立即非零退出。
#
# 只读使用 ios27-experiment/inputs 和参考资料，所有写入都在 mktemp 建的临时目录里，结束时删除。
# 环境变量：
#   TMPDIR        临时目录的父目录（默认系统临时目录）
#   KEEP=1        保留打完补丁的工作树，退出时打印路径
#   OPTIONAL=0    不检查 deferred/phone-27-asid16.patch（默认检查，失败也算失败）
#   XNU_REPO      覆盖 xnu 裸仓库路径
set -u -o pipefail
export LC_ALL=en_US.UTF-8

TOP=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
XNU_REPO=${XNU_REPO:-$TOP/ios27-experiment/inputs/xnu-12377.git}
XNU_TAG=xnu-12377.121.6
XNU_COMMIT=ac9718fb1af618d5ce8678d0dc6e8a58f252216f
UP=$TOP/参考资料/上游-20261031/UniXNU-UniXNU
ARCH_PROFILE=$UP/profiles/arch/arm64e
OS_PROFILE=$UP/profiles/os/ipados-27.0.1-24A446
SOC_DIR=$TOP/ios27-experiment/soc/UniXNU-sm8750-common-12377/patches/xnu-12377
DEV_DIR=$TOP/ios27-experiment/device/UniXNU-Pagani_SM8750-ios27/patches/xnu-12377

die() { echo "失败：$*" >&2; exit 1; }
for path in "$XNU_REPO" "$ARCH_PROFILE/patches/xnu/series" "$OS_PROFILE/patches/xnu/series" "$SOC_DIR/series" "$DEV_DIR/series"; do
  [ -e "$path" ] || die "缺输入 $path"
done
command -v git >/dev/null || die "缺 git"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/check-xnu-12377.XXXXXX") || die "建不了临时目录"
cleanup() {
  if [ "${KEEP:-0}" = 1 ]; then
    echo "保留工作树：$WORK/xnu"
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

echo "== 检出 $XNU_TAG"
# --no-hardlinks 保证不和 inputs 共享文件，克隆只写临时目录。
git clone --quiet --no-hardlinks --no-checkout "$XNU_REPO" "$WORK/xnu" 2>/dev/null || die "克隆失败"
git -C "$WORK/xnu" -c advice.detachedHead=false checkout --quiet --detach "$XNU_TAG" || die "检出失败"
got=$(git -C "$WORK/xnu" rev-parse HEAD)
[ "$got" = "$XNU_COMMIT" ] || die "提交不对：$got"
[ -z "$(git -C "$WORK/xnu" status --porcelain)" ] || die "检出不干净"
echo "   提交 $got"

# 解析一份 series，把补丁绝对路径写到标准输出。规则与 UniXNU/scripts/unixnu/patches.py 相同：
# 空行和 # 开头的行跳过；arch: 前缀相对 arch 配置目录；其余相对 series 所在目录。
resolve_series() {
  local series=$1 line entry path
  while IFS= read -r line || [ -n "$line" ]; do
    entry=${line#"${line%%[![:space:]]*}"}
    entry=${entry%"${entry##*[![:space:]]}"}
    case "$entry" in ''|'#'*) continue ;; esac
    case "$entry" in
      arch:*) path=$ARCH_PROFILE/${entry#arch:} ;;
      *) path=$(dirname "$series")/$entry ;;
    esac
    [ -f "$path" ] || die "$series 引用的补丁不存在：$entry"
    (cd "$(dirname "$path")" && echo "$PWD/$(basename "$path")")
  done < "$series"
}

SEEN=$WORK/seen.list
: > "$SEEN"
total=0; total_hunks=0

apply_layer() {
  local name=$1 series=$2 count=0 hunks=0 list p n
  list=$WORK/$name.list
  resolve_series "$series" > "$list" || exit 1
  while IFS= read -r p; do
    if grep -qxF "$p" "$SEEN"; then die "同一补丁在 series 里出现两次：$p"; fi
    echo "$p" >> "$SEEN"
    if ! out=$(git -C "$WORK/xnu" apply "$p" 2>&1); then
      echo "$out" >&2
      die "$name 层打不上：$(basename "$p")"
    fi
    n=$(grep -c '^@@' "$p" || true)
    hunks=$((hunks + n))
    count=$((count + 1))
  done < "$list"
  [ "$count" -gt 0 ] || die "$name 层是空的"
  printf '   %-7s %3d 个补丁  %4d 块  通过\n' "$name" "$count" "$hunks"
  total=$((total + count))
  total_hunks=$((total_hunks + hunks))
}

echo "== 按顺序打补丁（git apply，零模糊）"
apply_layer arch "$ARCH_PROFILE/patches/xnu/series"
apply_layer soc "$SOC_DIR/series"
apply_layer os "$OS_PROFILE/patches/xnu/series"
apply_layer device "$DEV_DIR/series"
echo "   合计    $total 个补丁  $total_hunks 块"

[ "$(wc -l < "$WORK/arch.list" | tr -d ' ')" = 64 ] || die "arch 层不是 64 个"
[ "$(wc -l < "$WORK/os.list" | tr -d ' ')" = 4 ] || die "os 层不是 4 个"

echo "== 工作树检查"
if find "$WORK/xnu" -name '*.rej' -o -name '*.orig' | grep -q .; then die "工作树有 .rej 或 .orig"; fi
if grep -rnE '^(<<<<<<<|>>>>>>>) ' "$WORK/xnu/bsd/kern/kern_exec.c" "$WORK/xnu/osfmk/arm64/start.s" >/dev/null; then die "有冲突标记"; fi
git -C "$WORK/xnu" add -A
tree=$(git -C "$WORK/xnu" write-tree)
git -C "$WORK/xnu" diff --cached --stat "$XNU_COMMIT" | tail -1
echo "   结果树 $tree"

# 关键改动抽查：这几条缺了，补丁打上了也不对。
check() { # 文件 期望出现的固定文字 说明
  grep -qF -- "$2" "$WORK/xnu/$1" || die "抽查失败（$3）：$1 里没有 $2"
}
absent() { # 文件 不应出现的固定文字 说明
  if grep -qF -- "$2" "$WORK/xnu/$1"; then die "抽查失败（$3）：$1 里不该有 $2"; fi
}
echo "== 关键改动抽查"
check pexpert/pexpert/arm64/BCM2837.h '#define HAS_CLUSTER 1' 'SM8750 分簇'
check pexpert/pexpert/arm64/BCM2837.h '#define NO_CPU_OVRD 1' 'SM8750 无 CPU_OVRD'
check osfmk/arm64/start.s '#0xFFFFFF' 'reset_vector 的 Aff2 掩码'
check osfmk/arm64/machine_routines.c 'MPIDR_AFF2_MASK) >> MPIDR_AFF2_SHIFT' '簇号取 Aff2'
check osfmk/arm64/machine_routines_asm.s '#elif defined(APPLE_ARM64_ARCH_FAMILY)' '休眠路径挡苹果寄存器'
check osfmk/arm64/start.s 'b EXT(early_boot_progress_entry)' '早期进度入口'
check osfmk/arm64/start.s 'MOV64	x1, TCR_EL1_BOOT_V_EQ_P' 'TCR 从开 MMU 起用 T0SZ_USER'
absent osfmk/arm/arm_init.c 'TCR_EL1_KVA_ENTRY' '不能在 arm_init 关 TTBR0 走表'
absent osfmk/arm64/start.s 'TCR_EL1_KVA_ENTRY' '不能在 arm_init_tramp 关 TTBR0 走表'
check bsd/kern/kern_exec.c 'psa_from_darwin26' 'Darwin 26 spawn 属性'
check bsd/kern/lockdown_mode.c 'lockdown_mode_state_public' 'Darwin 26 lockdown sysctl'
check osfmk/vm/vm_dyld_pager.c 'dyld_pager_peek' '16K 修复页'
absent osfmk/vm/vm_dyld_pager.c 'sheng' '诊断打印已去掉'
absent pexpert/pexpert/arm64/BCM2837.h '__ARM_AMP__' 'bring-up 保持单 pset'
echo "   全部通过"

if [ "${OPTIONAL:-1}" != 0 ]; then
  echo "== 推迟补丁中已移植到 12377 的部分（只检查能否打上，不计入结果树）"
  git -C "$WORK/xnu" apply --check "$DEV_DIR/deferred/phone-27-asid16.patch" || die "deferred/phone-27-asid16.patch 打不上"
  echo "   phone-27-asid16.patch 可打上"
fi

echo "== 通过：全部 $total 个补丁干净打在 $XNU_TAG 上"
