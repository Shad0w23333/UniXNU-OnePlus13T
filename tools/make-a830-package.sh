#!/bin/bash
# One combined test package: rebuild the GPU-line kernel from the current sources (Adreno830 + the Pagani drivers), then the
# loop and out-of-box images of the chosen layout (make-a830-layout.sh: scale3, phone or a newer variant) with the chosen
# Data seed. Usage: make-a830-package.sh <layout> [seed]   (seed: a file name under build-userspace/tree/build, default the
# GMEM publish seed). Prints the kernel, seed and image hashes; never touches the phone.
set -euo pipefail
TOP=/Users/xiaochoumao/unixnu-13t
LAYOUT=${1:?usage: make-a830-package.sh <layout> [seed]}
SEED=${2:-data-a830-publish-gmem.dbrdisk}
cd "$TOP/UniXNU"
export PATH="$PATH:/opt/homebrew/opt/llvm/bin" CCC_OVERRIDE_OPTIONS='# +-Wno-unnecessary-virtual-specifier'
PD=../UniXNU-Pagani-Drivers/drivers
./unixnu kernel -D ../UniXNU-Pagani_SM8750-gpu --out out-gpu --local-module driver:Adreno830=../Adreno830 \
    --local-module driver:Stage7Probe=$PD/Stage7Probe --local-module driver:BootProgress=$PD/BootProgress \
    --local-module driver:NativeMobileDisplay=$PD/NativeMobileDisplay --local-module driver:PaganiArmCpu=$PD/PaganiArmCpu \
    --local-module driver:PaganiInput=$PD/PaganiInput --local-module driver:PaganiPanel=$PD/PaganiPanel \
    --local-module driver:NativeUfs=$PD/NativeUfs --local-module driver:PaganiAdsp=$PD/PaganiAdsp \
    --local-module driver:PaganiAudio=$PD/PaganiAudio --local-module driver:PaganiBattery=$PD/PaganiBattery \
    > "$TOP/kernel-build-a830-package.log" 2>&1 \
    || { tail -20 "$TOP/kernel-build-a830-package.log"; exit 1; }
grep -i 'error:\|warning:' "$TOP/kernel-build-a830-package.log" && { echo 'make-a830-package: the kernel build printed warnings or errors'; exit 1; } || true
echo "kernel $(shasum -a 256 out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho | cut -c1-16)"
echo "seed   $(shasum -a 256 "$TOP/build-userspace/tree/build/$SEED" | cut -c1-16) $SEED"
echo "trust  $(shasum -a 256 "$TOP/build-userspace/tree/build/adreno-metal-plugin/owner-trust.bundle" | cut -c1-16)"
A830_PACKAGE_SEED=$SEED "$TOP/工具/make-a830-layout.sh" "$LAYOUT"
