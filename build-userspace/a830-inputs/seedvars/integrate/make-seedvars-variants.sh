#!/bin/bash
# Single-variable Data seed variants from the final seed (功能盘点.md 3.2 seedvars step 4, for integrate). Each variant changes one thing on a
# copy of final/s3-full.dbrdisk with 工具/edit-phone-seed-launchd.py; the owner trust bundle is copied unchanged, nothing else is touched.
# Usage: make-seedvars-variants.sh [NAME...]       default every variant; refuses an OUTPUT that exists (delete it to rebuild)
# Image: A830_PREBUILT_SEED=$V/NAME.dbrdisk A830_OWNER_TRUST=$V/NAME-owner-trust.bundle 工具/make-a830-layout.sh ...
# Never run against the phone; writes only below build-userspace/a830-inputs/final/seedvars.
set -euo pipefail
TOP=/Users/xiaochoumao/unixnu-13t
F=$TOP/build-userspace/a830-inputs/final
V=$F/seedvars
S=${SEED:-$F/s3-full.dbrdisk}
T=${TRUST:-$F/s3-full-owner-trust.bundle}
TOOL=(python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --trust "$T")
mkdir -p "$V"
make() {    # name, then the tool's options
    local name=$1; shift
    "${TOOL[@]}" "$@" "$S" "$V/$name.dbrdisk" > "$V/$name.log" 2>&1 || { cat "$V/$name.log" >&2; exit 1; }
    echo "$name: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["output"]["encoded_sha256"][:16], "headroom", d["payload"]["headroom"])' "$V/$name.json")"
}
all="autolock prefs zh perfpower locationd commcenter"
for name in ${*:-$all}; do
    case $name in
    autolock)   make "$name" --auto-lock 30 ;;                                  # 30 s lock; on trouble rerun with --keep-idle-sleep-block
    prefs)      make "$name" --no-auto-updates --sos-off ;;                     # candidates for the daily seed once Wi-Fi and sound work
    zh)         make "$name" --language zh-Hans ;;
    perfpower)  make "$name" --enable com.apple.PerfPowerServices ;;            # report.md: lowest risk of the jobs, useful with pagani-battery=1
    locationd)  make "$name" --enable com.apple.locationd ;;                    # keep identityservicesd disabled (report.md)
    commcenter) make "$name" --enable com.apple.CommCenter ;;                   # the noncellular job; only to compare the Settings pages
    *) echo "unknown variant $name" >&2; exit 2 ;;
    esac
done
