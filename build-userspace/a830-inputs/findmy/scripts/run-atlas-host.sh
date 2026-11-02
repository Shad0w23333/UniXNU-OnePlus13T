#!/bin/bash
# Build bridge/texture_atlas_host.mm against a host bridge library and run it over the host Adreno830 kernel stack with the host Turnip,
# in the GPU windows of the render server (slot 0), a large client (slot 1) and an app (slot 4, 124 MiB).
#   run-atlas-host.sh <libadreno_metal_bridge.a for aarch64-apple-darwin> <label> [slots...]
# Needs findmy/build/hostsim/libhostiokit.dylib (build-hostsim.sh) and findmy/build/mesa-tu-host (host Turnip of the current Mesa tree).
set -uo pipefail
F=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/findmy
KIT=/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/AdrenoMetal
LIB=${1:?bridge library}
LABEL=${2:?label}
shift 2
SLOTS=("${@:-4 1 0}")
[ $# -eq 0 ] && SLOTS=(4 1 0)
OUT=$F/build/atlas-host-$LABEL
mkdir -p "$OUT"
TURNIP=$F/build/mesa-tu-host/src/freedreno/vulkan/libvulkan_freedreno.dylib
SHIM=$F/build/hostsim/libhostiokit.dylib
xcrun clang++ -std=c++17 -fobjc-arc -O1 -Wall -Wextra -Werror -Wno-unused-parameter -Wno-deprecated-declarations -I "$KIT" \
  "$KIT/NativeBridgeMetalFrontend.mm" "$KIT/bridge/texture_atlas_host.mm" "$LIB" \
  -framework Foundation -framework Metal -framework IOSurface -framework CoreFoundation -o "$OUT/texture_atlas_host" || exit 2
status=0
for slot in "${SLOTS[@]}"; do
  cache=$OUT/cache-slot$slot; rm -rf "$cache"; mkdir -p "$cache"
  DYLD_INSERT_LIBRARIES=$SHIM HOSTSIM_GPU=a830 HOSTSIM_SLOT=$slot MESA_LOG= \
    "$OUT/texture_atlas_host" "$TURNIP" "$cache" > "$OUT/slot$slot.log" 2>&1
  rc=$?
  echo "== $LABEL slot $slot: exit $rc"
  grep -E '^(PASS|FAIL|FAILED|device|  turnip|  capacity|host gpu: slot)' "$OUT/slot$slot.log" | head -40
  [ $rc -ne 0 ] && status=1
done
exit $status
