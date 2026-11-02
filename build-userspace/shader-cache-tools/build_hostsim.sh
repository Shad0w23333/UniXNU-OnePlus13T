#!/bin/bash
# The host copy of the NativeAdreno kernel stack with the A830's device info (build-userspace/hostsim-a830-kit, made by
# build-userspace-a830.sh --host), built the way UniXNU/scripts/check-turnip-darwin-host.py builds it, into shader-cache/hostsim.
set -euo pipefail
B=/Users/xiaochoumao/unixnu-13t/build-userspace
T=$B/tree
K=$B/hostsim-a830-kit
O=$B/shader-cache/hostsim
SMMU=$T/kits/ArmSmmu4K
PM4=$T/kits/AdrenoPM4
[ -f "$K/tests/hostsim/host_gpu_service.cpp" ] || { echo "no hostsim-a830-kit: run build-userspace-a830.sh --host once"; exit 1; }
grep -q '0x44050001u,12u<<20,0x4000000ull,0x1fffffffff000ull' "$K/tests/hostsim/host_gpu_service.cpp" || { echo "hostsim kit is not the A830 identity"; exit 1; }
mkdir -p "$O/host-include/IOKit" "$O/host-include/kern"
for n in IOKit/IOTypes.h IOKit/IOReturn.h IOKit/IOBufferMemoryDescriptor.h IOKit/IOLib.h kern/task.h; do
  printf '#include "XnuMemoryFixture.h"\n' > "$O/host-include/$n"
done
objs=()
for s in ArmSmmu4KContext ArmSmmu4KProgram ArmSmmu4KLive ArmSmmu4KStreams ArmSmmu4KMmio; do
  xcrun clang -std=c11 -O1 -fPIC -c "$SMMU/$s.c" -o "$O/$s.o"; objs+=("$O/$s.o")
done
xcrun clang -std=c11 -O1 -fPIC -c "$PM4/AdrenoPM4.c" -o "$O/AdrenoPM4.o"; objs+=("$O/AdrenoPM4.o")
inc=(-I "$O/host-include" -I "$K/tests" -I "$K" -I "$SMMU" -I "$PM4")
xcrun clang++ -std=c++23 -O1 -fPIC "${inc[@]}" -c "$K/tests/hostsim/host_gpu_service.cpp" -o "$O/host_gpu_service.o"
core=()
for n in NativeAdrenoMemory NativeAdrenoSmmuMemory NativeAdrenoVm NativeAdrenoRing NativeAdrenoSubmit NativeAdrenoUserClient NativeAdrenoTimer \
         NativeAdrenoSmmuContext NativeAdrenoGpuContext; do
  xcrun clang++ -std=c++23 -O1 -fPIC "${inc[@]}" -c "$K/$n.cpp" -o "$O/$n.o"; core+=("$O/$n.o")
done
xcrun clang++ -std=c++17 -O1 -fPIC -c "$K/tests/hostsim/host_iokit_shim.cpp" -o "$O/host_iokit_shim.o"
printf '#include <unistd.h>\n__attribute__((weak)) int proc_selfpid(void) { return getpid(); }\n' > "$O/host_kpi.c"
xcrun clang -std=c11 -O1 -fPIC -c "$O/host_kpi.c" -o "$O/host_kpi.o"
xcrun clang++ -dynamiclib -o "$O/libhostiokit.dylib" "$O/host_gpu_service.o" "$O/host_iokit_shim.o" "$O/host_kpi.o" "${core[@]}" "${objs[@]}" \
  -framework IOKit -framework CoreFoundation
echo "PASS: host kernel peer with the A830 identity: $O/libhostiokit.dylib"
