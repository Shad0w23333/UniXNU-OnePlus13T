#!/usr/bin/env python3
"""Host check of the textures system frameworks make first, in the GPU windows the Adreno 830 kernel gives its clients
(drivers/AdrenoMetal/bridge/texture_atlas_host.mm).

Find My aborted in VectorKit's ggl::MetalResourceManager::newTexture ("VectorKit received null texture from Metal") on its first texture,
ggl::AlphaAtlas's 128x128 R8Unorm atlas with 8 levels (Shared, ShaderRead): an app's connection has a 124 MiB window, which Turnip
reports as its memory heap, and the runtime's 64 MiB pooled blocks left no room for the first block of images (bridge patch 0018).

The test runs the native frontend over the host bridge (build-adreno-metal-bridge.py --target host), the host Turnip
(build-turnip-darwin.py --target host) and the host copy of the Adreno830 kernel stack (drivers/Adreno830 tests/hostsim, built here with
ADRENO830_HOST_TEST), with HOSTSIM_GPU=a830 and HOSTSIM_SLOT 4 (an app's 124 MiB window), 1 (a large client's 352 MiB) and 0 (the render
server's 1.5 GiB). The software CP executes nothing: texel contents are checked through the host's mapping (Shared textures), never through
GPU work. Output stays under build/texture-atlas-host."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'drivers/AdrenoMetal'
A830 = ROOT / 'drivers/Adreno830'
OUT = ROOT / 'build/texture-atlas-host'
TURNIP = ROOT / 'build/mesa-tu/src/freedreno/vulkan/libvulkan_freedreno.dylib'
if not TURNIP.is_file():
    sys.exit('Build the host Turnip first: python3 scripts/build-turnip-darwin.py --target host')
if not (A830 / 'tests/hostsim/host_gpu_service.cpp').is_file() or 'HOSTSIM_SLOT' not in (A830 / 'tests/hostsim/host_gpu_service.cpp').read_text():
    sys.exit('drivers/Adreno830 has no host kernel stack with HOSTSIM_SLOT (tests/hostsim/host_gpu_service.cpp)')
subprocess.run([sys.executable, ROOT / 'scripts/build-adreno-metal-bridge.py', '--target', 'host'], check=True)
BRIDGE = ROOT / 'build/adreno-metal-bridge/host/libadreno_metal_bridge.a'
OUT.mkdir(parents=True, exist_ok=True)

# The host kernel stack of the Adreno 830 driver, as its tests/run-host-tests.py builds the driver for the host.
includes = OUT / 'host-include'
for name in ['IOKit/IOTypes.h', 'IOKit/IOReturn.h', 'IOKit/IOBufferMemoryDescriptor.h', 'IOKit/IOLib.h', 'kern/task.h']:
    path = includes / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#include "XnuMemoryFixture.h"\n')
(includes / 'kern/clock.h').write_text('#include "Adreno830HostClock.h"\n')
smmu, pm4 = ROOT / 'kits/ArmSmmu4K', ROOT / 'kits/AdrenoPM4'
objects = []
for stem in ['ArmSmmu4KContext', 'ArmSmmu4KProgram', 'ArmSmmu4KLive', 'ArmSmmu4KStreams', 'ArmSmmu4KMmio']:
    objects.append(OUT / (stem + '.o'))
    subprocess.run(['xcrun', 'clang', '-std=c11', '-O1', '-fPIC', '-c', smmu / (stem + '.c'), '-o', objects[-1]], check=True)
objects.append(OUT / 'AdrenoPM4.o')
subprocess.run(['xcrun', 'clang', '-std=c11', '-O1', '-fPIC', '-c', pm4 / 'AdrenoPM4.c', '-o', objects[-1]], check=True)
cxx = ['xcrun', 'clang++', '-std=c++23', '-O1', '-fPIC', '-DADRENO830_HOST_TEST', '-I', includes, '-I', A830 / 'tests', '-I', A830,
       '-I', smmu, '-I', pm4]
for name in ['tests/hostsim/host_gpu_service', 'NativeAdrenoMemory', 'NativeAdrenoSmmuMemory', 'NativeAdrenoVm', 'NativeAdrenoRing',
             'NativeAdrenoSubmit', 'NativeAdrenoUserClient', 'NativeAdrenoTimer', 'NativeAdrenoSmmuContext', 'NativeAdrenoGmuContext',
             'NativeAdrenoGpuContext']:
    objects.append(OUT / (Path(name).name + '.o'))
    subprocess.run([*cxx, '-c', A830 / (name + '.cpp'), '-o', objects[-1]], check=True)
objects.append(OUT / 'host_iokit_shim.o')
subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-fPIC', '-c', A830 / 'tests/hostsim/host_iokit_shim.cpp', '-o', objects[-1]],
               check=True)
shim = OUT / 'libhostiokit.dylib'
subprocess.run(['xcrun', 'clang++', '-dynamiclib', '-o', shim, *objects, '-framework', 'IOKit', '-framework', 'CoreFoundation'], check=True)

test = OUT / 'texture_atlas_host'
subprocess.run(['xcrun', 'clang++', '-std=c++17', '-fobjc-arc', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                '-Wno-deprecated-declarations', '-I', KIT, KIT / 'NativeBridgeMetalFrontend.mm', KIT / 'bridge/texture_atlas_host.mm', BRIDGE,
                '-framework', 'Foundation', '-framework', 'Metal', '-framework', 'IOSurface', '-framework', 'CoreFoundation', '-o', test],
               check=True)
failed = False
for slot, what in (('4', "an app's 124 MiB window"), ('1', "a large client's 352 MiB window"), ('0', "the render server's window")):
    cache = OUT / ('cache-slot' + slot)
    cache.mkdir(exist_ok=True)
    env = dict(os.environ, DYLD_INSERT_LIBRARIES=str(shim), HOSTSIM_GPU='a830', HOSTSIM_SLOT=slot, MESA_LOG='')
    for name in [n for n in env if n.startswith(('TU_', 'METAL2VULKAN_'))]:
        env.pop(name)
    run = subprocess.run([str(test), str(TURNIP), str(cache)], env=env, capture_output=True, text=True, timeout=900)
    log = OUT / ('slot%s.log' % slot)
    log.write_text(run.stdout + run.stderr)
    verdict = [l for l in run.stdout.splitlines() if l.startswith(('PASS', 'FAIL', '  capacity'))]
    print('slot %s (%s): %s' % (slot, what, '; '.join(verdict[-2:]) if verdict else 'no verdict'))
    if run.returncode or not any(l.startswith('PASS') for l in verdict):
        print('  see %s' % log)
        failed = True
if failed:
    sys.exit('the frameworks\' first textures were refused in a client window; see %s' % OUT)
print('PASS: VectorKit\'s 128x128 R8Unorm atlases (8 levels, Shared, ShaderRead) and the A8/R8/RG8/BGRA8 sRGB/RGBA16F/depth/stencil sweep '
      'are made in an app\'s 124 MiB window, a large client\'s and the render server\'s (host kernel stack; no GPU)')
