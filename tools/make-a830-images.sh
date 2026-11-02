#!/bin/bash
# Build the A830 GPU-line images from the current out-gpu kernel: the photo-free loop images (ramoops log from the board
# defaults pagani-ramoops=1 pagani-panic-reset=20, plus a timed warm reset) and the out-of-box GPU desktop image.
# Usage: make-a830-images.sh [name ...]   (no names: build all)
set -euo pipefail
TOP=/Users/xiaochoumao/unixnu-13t
LOGS=$TOP/build-userspace/logs/images
mkdir -p "$LOGS" "$TOP/测试镜像"
cd "$TOP/UniXNU"
export PATH="$PATH:/opt/homebrew/opt/llvm/bin"
DEV=../UniXNU-Pagani_SM8750-gpu
K=out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho
REC=out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/payload/payload.json
B=$TOP/build-userspace/tree/build
OWNER=$B/adreno-metal-plugin/owner-trust.bundle
WANT=" $* "

image() {   # image NAME CHINESE-NAME [unixnu payload arguments...]
    local name=$1 zh=$2; shift 2
    if [ -n "${WANT// /}" ] && [[ "$WANT" != *" $name "* ]]; then return; fi
    ./unixnu payload -D $DEV --out out-gpu --kernel $K "$@" > "$LOGS/$name-payload.log" 2>&1
    ./unixnu bootimg -D $DEV --out out-gpu --kernel $K "$@" -o "out-gpu/$name.img" > "$LOGS/$name-boot.log" 2>&1
    cp $REC "out-gpu/$name.payload.json"
    cp "out-gpu/$name.img" "$TOP/测试镜像/$zh.img"
    cp $REC "$TOP/测试镜像/$zh.payload.json"
    echo "$name -> 测试镜像/$zh.img $(shasum -a 256 "out-gpu/$name.img" | cut -c1-16)"
}

# Photo-free loop: stop after a stage, then warm-reset back to Android so the log can be read with adb.
image loop-s3 boot-A830-回路-停在S3 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=3 --kernel-arg pagani-reset-after=200
image loop-s5 boot-A830-回路-停在S5 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=5 --kernel-arg pagani-reset-after=200
image loop-s7 boot-A830-回路-停在S7 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=7 --kernel-arg pagani-reset-after=200
image loop-s9 boot-A830-回路-停在S9 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=9 --kernel-arg pagani-reset-after=200
image loop-full boot-A830-回路-完整 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=300
image loop-m7 boot-A830-回路-M7自检 --data-ramdisk $B/data-a830.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-status=3 --kernel-arg pagani-gpu-dcvs=0 --kernel-arg pagani-reset-after=300
image loop-m8 boot-A830-回路-M8桌面 --data-ramdisk $B/data-a830-publish.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=300
# Bisect a hang: S0 only validates inputs (no GPU register access at all), then stop after S1 (power) and S2 (SCM).
image loop-s0 boot-A830-回路-停在S0-不碰GPU --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=0 --kernel-arg pagani-reset-after=200
image loop-s1 boot-A830-回路-停在S1 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=1 --kernel-arg pagani-reset-after=200
image loop-s2 boot-A830-回路-停在S2 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-gpu-stop=2 --kernel-arg pagani-reset-after=200
# The design profile of the same stages, for an A/B when the annibale profile stops somewhere.
image loop-s9-design boot-A830-回路-停在S9-设计配置 --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-status=2 \
    --kernel-arg pagani-gpu-stop=9 --kernel-arg pagani-reset-after=200
# GPU desktop with the design profile (full DVFS table, DCVS up to the top level, interrupt fences).
image loop-m8-design boot-A830-回路-M8桌面-设计配置 --data-ramdisk $B/data-a830-publish.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=300
# The same desktop with GMEM rendering allowed (seed env TU_DEBUG=nocb instead of sysmem,nocb).
image loop-m8-design-gmem boot-A830-回路-M8桌面-设计配置-GMEM --data-ramdisk $B/data-a830-publish-gmem.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=300
# No pagani-dpu-probe=1 image: on the 13T its register snapshot at 150 s hung the system (2026-10-30 12:15, PROGRESS.md).
# The GMEM desktop with the frame-rate row (pagani-fps, on with the status rows): FPS, frame times and GPU/CPU frames once a
# second on screen and in the log, frame intervals above 50 ms as jitter records.
image loop-m8-fps boot-A830-回路-M8桌面-GMEM-帧率 --data-ramdisk $B/data-a830-publish-gmem.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=360
# Control for the freezes (FPS 0) seen with GMEM: the same kernel and rows with the sysmem seed (TU_DEBUG=sysmem,nocb, no GMEM).
image loop-m8-sysmem-fps boot-A830-回路-M8桌面-sysmem-帧率 --data-ramdisk $B/data-a830-publish.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=360
# GMEM without Turnip's autotune (TU_DEBUG=gmem,nocb: every render pass in GMEM): the freezes came with autotune's GMEM, and
# ReportMemoryException plus a display client re-entering ended the 64 s one (render server killed and restarted).
image loop-m8-gmemforced boot-A830-回路-M8桌面-强制GMEM-帧率 --data-ramdisk $B/data-a830-publish-gmem-forced.dbrdisk \
    --owner-trust-cache $OWNER --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=360
# The same with ACD (M13 d): QMP val 1 to the AOP, the HFI ACD table, and the DCVS ceiling lifted from the ACD-off 734 MHz to
# the table top (900 MHz on the common table). Without the AOP's or the GMU's acknowledgement ACD stays off (734 MHz).
image loop-m8-acd boot-A830-回路-M8桌面-GMEM-ACD --data-ramdisk $B/data-a830-publish-gmem.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-acd=1 --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=360
# ACD with a 1 GiB BO pool (pagani-gpu-pool=1024: the 512 MiB pool was 89% used under load, and a full pool falls back to
# scattered pages that exhaust the VM's span capacity, int-137). The device repository's board.toml now gives XNU 6 GiB.
image loop-m8-acd-pool boot-A830-回路-M8桌面-GMEM-ACD-大池 --data-ramdisk $B/data-a830-publish-gmem.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-acd=1 --kernel-arg pagani-gpu-pool=1024 --kernel-arg pagani-gpu-status=2 \
    --kernel-arg pagani-reset-after=360
# Everything together for the freeze and stutter round: the user-space fixes are in the seeds (rebuilt 2026-10-30 14:14 with the
# new owner trust bundle), the kernel carries the BO-pool and hand-off fixes and the SCMI CPU frequency client (pagani-cpufreq=1:
# both clusters at their top level, the CPUCP firmware keeps its thermal limits), GPU at up to 900 MHz with ACD, 1 GiB BO pool.
image loop-m8-full boot-A830-回路-M8桌面-全开 --data-ramdisk $B/data-a830-publish-gmem.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-acd=1 --kernel-arg pagani-gpu-pool=1024 --kernel-arg pagani-cpufreq=1 \
    --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=420
# Bigger UI and phone layout on the same iPadOS root (no new system image): built with a temporary board.toml edit each
# (工具/make-a830-layout.sh). scale3 keeps the iPad identity at display-scale/artwork-scale-factor 3 (405x880 points); phone
# also claims iPhone11,6 with the phone idiom and subtype 2688, keeping J120AP and the J120 product-id for the audio tables.
image loop-m8-scale3 boot-A830-回路-M8桌面-放大界面 --data-ramdisk $B/${A830_PACKAGE_SEED:-data-a830-publish-gmem.dbrdisk} --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-acd=1 --kernel-arg pagani-gpu-pool=1024 --kernel-arg pagani-cpufreq=2 \
    --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=420
# The phone identity's keyboard layouts come from the KBLayouts_iPhone.dat overlay (phone-keyboard-layouts.patch, make-phone-seed.py
# --keyboard; make-a830-layout.sh phone-plus builds it with make-phone-keyboard-layouts.py, which adds a family drawn for 405.333
# points). The earlier text fix-up returning KBLayouts_iPad.dat read the same iPhone-* keyboards (that file holds them too): keys and
# keyboard view disagreed because UIKit sizes a phone keyboard by its family's design width (375) on a screen size it does not know.
# pagani-reset-wdog=1 ends the run with the watchdog's bite instead of PSCI SYSTEM_RESET, which flipped weak DRAM cells in the log's
# headers (a bite and a button reset kept the log intact, 2026-10-30).
# pagani-overlay=0 keeps the diagnostic text off the screen (it covered the top-left of the iOS UI); the ramoops log keeps it.
# Unattended runs: the Gunyah watchdog (pagani-wdog=30, BootProgress GunyahWdog.c) resets a kernel whose threads stopped running
# (2026-10-30 18:35: a run hung with no timed reset and needed a manual power + volume-down reset).
image loop-m8-phone boot-A830-回路-M8桌面-iPhone布局 --data-ramdisk $B/${A830_PACKAGE_SEED:-data-a830-publish-gmem.dbrdisk} --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-acd=1 --kernel-arg pagani-gpu-pool=1024 --kernel-arg pagani-cpufreq=2 \
    --kernel-arg pagani-gpu-status=2 --kernel-arg pagani-reset-after=420 \
    --kernel-arg pagani-wdog=30 --kernel-arg pagani-overlay=0 --kernel-arg pagani-reset-wdog=1
# Out of the box (CPU on the load governor, pagani-cpufreq=2: pinning both clusters at the top heated the SoC until the GPU
# throttled to 389 MHz), the fastest desktop so far: design profile, GMEM, ACD (900 MHz), 1 GiB BO pool, 6 GiB XNU window (board.toml),
# status and frame-rate rows, no timed reset.
image m8-gmem boot-A830-GPU桌面-GMEM-开箱即用 --data-ramdisk $B/data-a830-publish-gmem.dbrdisk --owner-trust-cache $OWNER \
    --kernel-arg pagani-gpu-profile=0 --kernel-arg pagani-gpu-acd=1 --kernel-arg pagani-gpu-pool=1024 --kernel-arg pagani-cpufreq=2 \
    --kernel-arg pagani-gpu-status=2
# Out of the box: GPU-composited desktop with the board defaults (no timed reset).
image m8 boot-A830-GPU桌面-开箱即用 --data-ramdisk $B/data-a830-publish.dbrdisk --owner-trust-cache $OWNER
