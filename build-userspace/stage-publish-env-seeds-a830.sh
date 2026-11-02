#!/bin/bash
# Extra --publish Data seeds for the A830 GPU desktop (M8) that differ from build/data-a830-publish.dbrdisk only in NativeAdrenoMetal.env.
# The env file is data: the plugin, Turnip and adrenoprobed are the same signed files, so build/adreno-metal-plugin/owner-trust.bundle and the
# kernel trust cache made from it stay valid; an image takes one of these seeds by --data-ramdisk alone (same --owner-trust-cache).
#
#   gmem   TU_DEBUG=nocb   system memory is no longer forced: Turnip's autotune (bandwidth) picks GMEM or system memory per render pass;
#                          passes with fewer than 5 draws stay in system memory. Concurrent binning stays off.
#   noenv  TU_DEBUG=       no TU_DEBUG flag at all. The empty value is needed: when TU_DEBUG is unset the plugin sets TU_DEBUG=nocb with
#                          overwrite off (NativeAdrenoMetalPlugin.mm deviceForPort, NativeTurnipSmoke.h), so an empty file or no file would be
#                          the gmem seed again. This Mesa keeps concurrent binning off by default too (tu_allow_concurrent_binning False).
#
# Nothing is rebuilt (build-userspace-a830.sh steps 4-6 made the inputs) and the existing seeds are not touched. A seed that exists is kept
# unless --force. check-env-seeds-a830.py then checks trust, the env semantics and the staged seeds on the host. Nothing touches the phone,
# commits or pushes.
#
#   stage-publish-env-seeds-a830.sh [--force] [NAME:ENV ...]      ENV as printf %b text (\n between lines); default: gmem and noenv
#   e.g.  stage-publish-env-seeds-a830.sh prefer-gmem:'TU_DEBUG=nocb\nTU_AUTOTUNE_ALGO=prefer_gmem'
set -euo pipefail

B=/Users/xiaochoumao/unixnu-13t/build-userspace
T=$B/tree
L=$B/logs
BASE_SEED="/Users/xiaochoumao/unixnu-13t/作者产物/build-63/ram-data/data.dbrdisk"   # as build-userspace-a830.sh: OwnerData 1726EAC0-...
# build-userspace-a830.sh's PUBLISH_CLIENTS (data-a830-publish's clients; check-env-seeds-a830.py CLIENTS checks both): the widgets, PosterBoard,
# the icon agent and the poster extensions that create a Metal device.
PUBLISH_CLIENTS=WidgetRenderer_Default,PosterBoard,iconservicesagent,AegirPoster,KaleidoscopePoster,PhotosPosterProvider,AmbientPhotoFramePosterProvider,MercuryPosterExtension,MonogramPosterExtension,HelloPosterExtension,PridePosterExtension,RhizomePoster,ExtragalacticPoster,EmojiPosterExtension,AvatarPosterExtension,WeatherPoster,LegacyPoster
# add-gpu-plugin-data.py imports UniXNU/drivers/DarwinBoot/Test/packed_ramdisk.py through the link: keep its __pycache__ out of that checkout.
export PYTHONDONTWRITEBYTECODE=1

force=0
if [ "${1:-}" = --force ]; then force=1; shift; fi
if [ $# -eq 0 ]; then
  set -- 'gmem:TU_DEBUG=nocb' \
         'noenv:# no TU_DEBUG flag: the empty value keeps out the plugin default TU_DEBUG=nocb, which is set only when unset\nTU_DEBUG='
fi
mkdir -p "$L"
cd "$T"
[ -f build/data-a830-publish.dbrdisk ] && [ -f build/prime-list.txt ] || { echo "run build-userspace-a830.sh first"; exit 1; }
python3 "$B/check-env-seeds-a830.py" --trust-only

names=()
for variant in "$@"; do
  name=${variant%%:*}
  env=${variant#*:}
  if [[ "$variant" != *:* || ! "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then echo "NAME:ENV with NAME in [a-z0-9-]: $variant"; exit 1; fi
  names+=("$name")
  out=build/data-a830-publish-$name.dbrdisk
  if [ -e "$out" ] && [ $force = 0 ]; then echo "kept $out (--force stages it again)"; continue; fi
  rm -f "$out" "${out%.dbrdisk}.json"
  printf '%b\n' "$env" > "build/NativeAdrenoMetal-$name.env"
  # The clients and launchd jobs of build-userspace-a830.sh's data-a830-publish (the icon agent enabled and listed): only the env differs.
  python3 scripts/add-gpu-plugin-data.py --publish --clients "$PUBLISH_CLIENTS" \
    --enable-jobs com.apple.iconservices.iconservicesagent --disable-jobs com.apple.fairplayd.A2 --env "build/NativeAdrenoMetal-$name.env" \
    --prime-list build/prime-list.txt "$BASE_SEED" "$out" 2>&1 | tee "$L/data-seed-publish-$name.log"
done

python3 "$B/check-env-seeds-a830.py" "${names[@]}" 2>&1 | tee "$L/check-env-seeds.log"
for name in "${names[@]}"; do shasum -a 256 "$T/build/data-a830-publish-$name.dbrdisk"; done
shasum -a 256 "$T/build/adreno-metal-plugin/owner-trust.bundle"
