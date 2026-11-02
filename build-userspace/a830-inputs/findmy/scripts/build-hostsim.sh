#!/bin/bash
# The host copy of the Adreno830 kernel stack (user client, VM, ring, submit over the authored memory doubles) as an IOKit shim library,
# built as scripts/check-turnip-darwin-host.py builds it, but from the Adreno830 checkout (ADRENO830_HOST_TEST, as its
# tests/run-host-tests.py) and from findmy/hostsim/host_gpu_service.cpp: HOSTSIM_SLOT=<n> mounts the connection in slot n of the 4 GiB
# table (objects, render-server reserve, GPU window) as NativeAdrenoPublish.cpp mounts a client.
# Output: findmy/build/hostsim/libhostiokit.dylib. Reads the Adreno830 checkout and the build tree's kits; writes nothing else.
set -euo pipefail
F=/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/findmy
O=$F/build/hostsim
K=/Users/xiaochoumao/unixnu-13t/Adreno830
T=/Users/xiaochoumao/unixnu-13t/build-userspace/tree
SMMU=$T/kits/ArmSmmu4K
INC=$O/host-include
mkdir -p "$O"
for n in IOKit/IOTypes.h IOKit/IOReturn.h IOKit/IOBufferMemoryDescriptor.h IOKit/IOLib.h kern/task.h; do
  mkdir -p "$INC/$(dirname "$n")"; echo '#include "XnuMemoryFixture.h"' > "$INC/$n"
done
echo '#include "Adreno830HostClock.h"' > "$INC/kern/clock.h"
objs=()
for s in ArmSmmu4KContext ArmSmmu4KProgram ArmSmmu4KLive ArmSmmu4KStreams ArmSmmu4KMmio; do
  xcrun clang -std=c11 -O1 -fPIC -c "$SMMU/$s.c" -o "$O/$s.o"; objs+=("$O/$s.o")
done
xcrun clang -std=c11 -O1 -fPIC -c "$T/kits/AdrenoPM4/AdrenoPM4.c" -o "$O/AdrenoPM4.o"; objs+=("$O/AdrenoPM4.o")
CXXF=(-std=c++23 -O1 -fPIC -DADRENO830_HOST_TEST -I "$INC" -I "$K/tests" -I "$K" -I "$SMMU" -I "$T/kits/AdrenoPM4")
xcrun clang++ "${CXXF[@]}" -c "$F/hostsim/host_gpu_service.cpp" -o "$O/host_gpu_service.o"
core=()
for n in NativeAdrenoMemory NativeAdrenoSmmuMemory NativeAdrenoVm NativeAdrenoRing NativeAdrenoSubmit NativeAdrenoUserClient NativeAdrenoTimer \
         NativeAdrenoSmmuContext NativeAdrenoGmuContext NativeAdrenoGpuContext; do
  xcrun clang++ "${CXXF[@]}" -c "$K/$n.cpp" -o "$O/$n.o"; core+=("$O/$n.o")
done
xcrun clang++ -std=c++17 -O1 -fPIC -c "$F/hostsim/host_iokit_shim.cpp" -o "$O/host_iokit_shim.o"
xcrun clang++ -dynamiclib -o "$O/libhostiokit.dylib" "$O/host_gpu_service.o" "$O/host_iokit_shim.o" "${core[@]}" "${objs[@]}" \
  -framework IOKit -framework CoreFoundation
echo "PASS: $O/libhostiokit.dylib"
