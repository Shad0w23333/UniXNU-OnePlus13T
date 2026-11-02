#!/bin/bash
# A830 (OnePlus 13T PKX110) user-space GPU stack, built on this Mac: Turnip with the darwin kernel backend (libvulkan_freedreno.dylib for iOS 17),
# the metal2vulkan bridge (Rust, aarch64-apple-ios), NativeAdrenoMetal.bundle and adrenoprobed (4 KiB-page ad-hoc signatures), the owner trust
# bundle, and the Data seeds (M7 stage 1 without --publish, M8 with --publish) with TU_DEBUG=sysmem,nocb in NativeAdrenoMetal.env, plus the M8
# seeds that let Turnip use GMEM (stage-publish-env-seeds-a830.sh: data-a830-publish-gmem TU_DEBUG=nocb, data-a830-publish-noenv no flag).
#
# The build runs in $T, a resolved tree like the one ./unixnu tree makes: UniXNU's scripts are copied (they locate the tree from their own
# path), drivers, kits and patches/mesa are links to the sources, .cache and build are local. Sources stay where they are owned:
# UniXNU/scripts, UniXNU/patches/mesa, UniXNU/drivers/AdrenoMetal, Adreno830/NativeAdrenoABI.h (gpu_abi.py: drivers/Adreno830 first).
# Nothing here needs root; nothing touches the phone, commits or pushes.
#
#   build-userspace-a830.sh            build everything, stage both seeds, run the host checks
#   build-userspace-a830.sh --host     also build host Turnip and run the host simulation (check-turnip-darwin-host.py, A740 peer)
set -euo pipefail

B=/Users/xiaochoumao/unixnu-13t/build-userspace
U=/Users/xiaochoumao/unixnu-13t/UniXNU
A830=/Users/xiaochoumao/unixnu-13t/Adreno830
T=$B/tree
L=$B/logs
BASE_SEED="/Users/xiaochoumao/unixnu-13t/作者产物/build-63/ram-data/data.dbrdisk"   # OwnerData 1726EAC0-2168-4118-AFD6-19E12FE306E7
ORIGINAL_TRUST="/Users/xiaochoumao/unixnu-13t/作者产物/build-63/original-static-system.bundle"   # sha256 7416ca4f...
MESA_PIN=85fda3b36405bcaea8bffc64aa4f2651dc90770d
MESA_TREE=b72ff1eb2138026ed6aba524770899faabf7d7c8
M2V_PIN=720ef1ce9ae2a2068999a8675717418b9416283e
# The processes besides backboardd that get their own native Metal device in an M8 (--publish) seed (NativeAdrenoMetal.clients; the same list
# in stage-publish-env-seeds-a830.sh and check-env-seeds-a830.py CLIENTS, each name granted by the kernel's NativeAdrenoSandboxGrantList in
# Adreno830/NativeAdrenoPublish.h). Besides the widgets, PosterBoard and the icon agent: the poster extensions whose 21H461 code creates a Metal
# device (MTLCreateSystemDefaultDevice, Core Image, SpriteKit or SceneKit); without one they crash (AegirPoster status=6, KaleidoscopePoster
# status=b when the wallpaper gallery opens). The kernel has 12 connection slots for them (Adreno830 NativeAdrenoMaxClients).
PUBLISH_CLIENTS=WidgetRenderer_Default,PosterBoard,iconservicesagent,AegirPoster,KaleidoscopePoster,PhotosPosterProvider,AmbientPhotoFramePosterProvider,MercuryPosterExtension,MonogramPosterExtension,HelloPosterExtension,PridePosterExtension,RhizomePoster,ExtragalacticPoster,EmojiPosterExtension,AvatarPosterExtension,WeatherPoster,LegacyPoster

export DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode-26.6.app/Contents/Developer}
export PATH="/opt/homebrew/opt/bison/bin:/opt/homebrew/opt/llvm/bin:/opt/homebrew/bin:$PATH"
# add-gpu-plugin-data.py imports UniXNU/drivers/DarwinBoot/Test/packed_ramdisk.py through the link: keep its __pycache__ out of that checkout.
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$L"

# ---- 0. tools (install once: HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
#         brew install glslang shaderc spirv-tools bison; rustup target add aarch64-apple-ios --toolchain 1.98.0) ----
for tool in glslangValidator glslc spirv-val meson ninja cargo; do command -v $tool >/dev/null || { echo "missing $tool"; exit 1; }; done
/opt/homebrew/opt/bison/bin/bison --version | grep -q ' 3\.' || { echo "bison 3 missing (brew install bison)"; exit 1; }
rustup target list --installed --toolchain 1.98.0 | grep -qx aarch64-apple-ios || { echo "rustup target add aarch64-apple-ios --toolchain 1.98.0"; exit 1; }
xcrun --sdk iphoneos --show-sdk-path

# ---- 1. the resolved tree ----
mkdir -p "$T"/{drivers,kits,patches,build,.cache/gpu-reference,.cache/mesa-tools/bin}
for d in AdrenoMetal Adreno740 GopFramebuffer DarwinBoot; do ln -sfn "$U/drivers/$d" "$T/drivers/$d"; done
ln -sfn "$A830" "$T/drivers/Adreno830"
for k in AdrenoPM4 ArmSmmu4K; do ln -sfn "$U/kits/$k" "$T/kits/$k"; done
ln -sfn "$U/patches/mesa" "$T/patches/mesa"
rsync -a --delete "$U/scripts/" "$T/scripts/"
ln -sfn /opt/homebrew/opt/bison/bin/bison "$T/.cache/mesa-tools/bin/bison"
cd "$T"

# ---- 2. pinned Mesa (sparse) with the recorded patches in json order (the gen8 backports before the darwin backend) ----
if [ ! -d .cache/mesa-work/.git ]; then
  git init -q .cache/mesa-work
  git -C .cache/mesa-work remote add origin https://chromium.googlesource.com/external/gitlab.freedesktop.org/mesa/mesa
  git -C .cache/mesa-work fetch --depth=1 --filter=blob:none origin $MESA_PIN
  git -C .cache/mesa-work sparse-checkout init --cone
  git -C .cache/mesa-work sparse-checkout set $(python3 -c "import json;print(' '.join(json.load(open('scripts/data/adreno-compiler-inputs.json'))['mesa']['sparse_directories']))")
  git -C .cache/mesa-work checkout -q --detach FETCH_HEAD
  test "$(git -C .cache/mesa-work rev-parse 'HEAD^{tree}')" = $MESA_TREE
  for p in $(python3 -c "import json;print(' '.join(json.load(open('scripts/data/adreno-compiler-inputs.json'))['patches']))"); do
    git -C .cache/mesa-work apply "$T/$p"
  done
fi
# Editing the darwin backend: change .cache/mesa-work/src/freedreno/vulkan/tu_knl_darwin.cc, then python3 scripts/save-turnip-darwin-patch.py
# rewrites UniXNU/patches/mesa/turnip-darwin-kmd.patch for its place in the order and re-registers the identity.
[ -x .cache/mesa-venv/bin/meson ] || { python3 -m venv .cache/mesa-venv && .cache/mesa-venv/bin/pip install -q -r scripts/requirements-adreno-compiler.txt; }

# ---- 3. metal2vulkan (full checkout: the bridge build assembles validation fixtures) ----
if [ ! -d .cache/gpu-reference/metal2vulkan/.git ]; then
  git init -q .cache/gpu-reference/metal2vulkan
  git -C .cache/gpu-reference/metal2vulkan remote add origin https://github.com/steelbrain/metal2vulkan
  git -C .cache/gpu-reference/metal2vulkan fetch --depth=1 --filter=blob:none origin $M2V_PIN
  git -C .cache/gpu-reference/metal2vulkan checkout -q --detach FETCH_HEAD
fi

# ---- 4. Turnip for iOS 17 (release, NDEBUG), against Adreno830/NativeAdrenoABI.h ----
python3 scripts/build-turnip-darwin.py --target ios 2>&1 | tee "$L/turnip-ios.log"

# ---- 5. plugin, bridge and adrenoprobed; the 21H461 prime list goes into __DATA,__primelist ----
cp "$A830/docs/inputs/userspace/prime-list-21H461.txt" build/prime-list.txt
python3 scripts/build-adreno-metal-plugin.py 2>&1 | tee "$L/plugin-ios.log"
cp build/adreno-metal-bridge/Cargo.lock "$B/adreno-metal-bridge.Cargo.lock"

# ---- 6. owner trust bundle (own module with the three CDHashes + both original modules unchanged) ----
mkdir -p build/original-static-system-trust
cp "$ORIGINAL_TRUST" build/original-static-system-trust/original-static-system.bundle
python3 scripts/make-owner-trust-bundle.py 2>&1 | tee "$L/owner-trust.log"

# ---- 7. Data seeds (the 13T's PaganiDarwinBoot takes no GpuAddon sidecar; the volume UUID stays the kernel's
#         CONFIG_XNU_OWNER_DATA_VOLUME_UUID 1726EAC0-...). M7: stage-1 self-tests only; M8: the render server composites on the GPU. ----
printf 'TU_DEBUG=sysmem,nocb\n' > build/NativeAdrenoMetal.env
rm -f build/data-a830.dbrdisk build/data-a830.json build/data-a830-publish.dbrdisk build/data-a830-publish.json
python3 scripts/add-gpu-plugin-data.py --clients WidgetRenderer_Default,PosterBoard --env build/NativeAdrenoMetal.env \
  --prime-list build/prime-list.txt "$BASE_SEED" build/data-a830.dbrdisk 2>&1 | tee "$L/data-seed.log"
# M8 icons: iconservicesagent renders every app icon for SpringBoard (Core Image, so with a native Metal device). The base seed disables
# its job (prepare-ram-data.py), which left every icon the generic placeholder: enable it and list it as a client (kernel grant: already).
# The poster extensions of PUBLISH_CLIENTS follow it (kernel grant and 12 slots: Adreno830 NativeAdrenoPublish.h).
# fairplayd.A2 is KeepAlive and exits at once here (no FairPlay backend): relaunched every 10 s, and a FairPlay request that arrives in
# the throttle window waits for the next launch. Disabled, its Mach services are not registered and a lookup fails at once (sheng-lite).
python3 scripts/add-gpu-plugin-data.py --publish --clients "$PUBLISH_CLIENTS" \
  --enable-jobs com.apple.iconservices.iconservicesagent --disable-jobs com.apple.fairplayd.A2 --env build/NativeAdrenoMetal.env \
  --prime-list build/prime-list.txt "$BASE_SEED" build/data-a830-publish.dbrdisk 2>&1 | tee "$L/data-seed-publish.log"
# M8 with GMEM allowed: the same seed with only another NativeAdrenoMetal.env (gmem: TU_DEBUG=nocb; noenv: TU_DEBUG= , no flag at all),
# checked against data-a830-publish.dbrdisk by check-env-seeds-a830.py. Same owner trust bundle.
"$B/stage-publish-env-seeds-a830.sh" --force

# ---- 8. host checks and product facts ----
python3 scripts/check-adreno-userspace-host.py 2>&1 | tee "$L/check-userspace-host.log"
P=build/adreno-metal-plugin
for f in "$P/NativeAdrenoMetal.bundle" "$P/NativeAdrenoMetal.bundle/libvulkan_freedreno.dylib" "$P/adrenoprobed"; do
  codesign --verify --strict "$f"
  codesign --display --verbose=4 "$f" 2>&1 | grep -E '^(Identifier|CDHash|Page size)='
done
file "$P/NativeAdrenoMetal.bundle/NativeAdrenoMetal" "$P/NativeAdrenoMetal.bundle/libvulkan_freedreno.dylib" "$P/adrenoprobed"
strings "$P/NativeAdrenoMetal.bundle/libvulkan_freedreno.dylib" | grep -x 'Adreno (TM) 830'
python3 -c "import sys;b=open(sys.argv[1],'rb').read();n=b.count((0x44050001).to_bytes(4,'little'));print('chip id 0x44050001 in Turnip:',n);sys.exit(n==0)" \
  "$P/NativeAdrenoMetal.bundle/libvulkan_freedreno.dylib"
shasum -a 256 "$P/owner-trust.bundle" build/data-a830.dbrdisk build/data-a830-publish.dbrdisk build/data-a830-publish-gmem.dbrdisk \
  build/data-a830-publish-noenv.dbrdisk

if [ "${1:-}" = --host ]; then
  python3 scripts/build-turnip-darwin.py --target host 2>&1 | tee "$L/turnip-host.log"
  # The A830 identity over the A740 host kernel stack: a copy of the A740 peer whose device info is the A830's
  # (Adreno830/docs/inputs/userspace/patches/adreno830-hostsim-a830.diff), so Turnip takes its gen8 paths with gpu_id 830.
  K=$B/hostsim-a830-kit
  rm -rf "$K" && mkdir -p "$K/tests/hostsim"
  for e in "$U/drivers/Adreno740"/*; do [ "$(basename "$e")" = tests ] || ln -s "$e" "$K/"; done
  for e in "$U/drivers/Adreno740/tests"/*; do [ "$(basename "$e")" = hostsim ] || ln -s "$e" "$K/tests/"; done
  for e in "$U/drivers/Adreno740/tests/hostsim"/*; do [ "$(basename "$e")" = host_gpu_service.cpp ] || ln -s "$e" "$K/tests/hostsim/"; done
  cp "$U/drivers/Adreno740/tests/hostsim/host_gpu_service.cpp" "$K/tests/hostsim/"
  (cd "$K" && patch -s -p1 < "$A830/docs/inputs/userspace/patches/adreno830-hostsim-a830.diff")
  # Known with the pinned A740 peer (5c8c145), with or without these changes: deferred BO "pressure" stops the script (BoFree count), and
  # check-quartzcore-turnip-cache-host.py is missing from UniXNU; the later steps are run one by one.
  for peer in "$T/drivers/Adreno740:a740" "$K:a830"; do
    UNIXNU_GPU_ABI_DIR="${peer%%:*}" python3 scripts/check-turnip-darwin-host.py > "$L/hostsim-${peer##*:}.log" 2>&1 || true
    grep -E '^(PASS|device:)|failed' "$L/hostsim-${peer##*:}.log" || true
    UNIXNU_GPU_ABI_DIR="${peer%%:*}" python3 scripts/check-turnip-pipeline-cache-host.py 2>&1 | grep '^PASS: separate'
    (cd build/turnip-darwin-host && DYLD_INSERT_LIBRARIES="$PWD/libhostiokit.dylib" MESA_LOG= ./plugin_smoke "$T/build/mesa-tu/src/freedreno/vulkan/libvulkan_freedreno.dylib" | tail -1)
  done
fi

# ---- 9. image (the integration step, from UniXNU; GPU line out-gpu, our kernel passed explicitly) ----
# cd /Users/xiaochoumao/unixnu-13t/UniXNU
# ./unixnu payload -D ../UniXNU-Pagani_SM8750-gpu --local-module driver:Adreno830=../Adreno830 --kernel <out-gpu kernel.macho> \
#   --data-ramdisk $T/build/data-a830.dbrdisk --owner-trust-cache $T/build/adreno-metal-plugin/owner-trust.bundle [--kernel-arg ...]
#   (M8 GPU desktop: --data-ramdisk data-a830-publish.dbrdisk, or -gmem / -noenv for GMEM; the --owner-trust-cache stays the same)
# ./unixnu bootimg -D ../UniXNU-Pagani_SM8750-gpu --local-module driver:Adreno830=../Adreno830 [--kernel-arg ...] -o out-gpu/a830-m7.img
