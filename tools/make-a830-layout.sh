#!/bin/bash
# Build the layout variants of the A830 GPU desktop with a temporary edit of the device repository's board.toml, restored afterwards
# whatever happens. The ADT merges arch -> SoC -> OS profile -> board.toml, so these keys override the iPadOS profile's iPad identity
# (UniXNU/docs/zh/guide/mobilegestalt.md). No new system image: the iPadOS 17.7.11 root stays as it is.
# Usage: make-a830-layout.sh scale3|phone|phone-plus|phone-apps|phone-island|phone-island-apps
#   scale3      iPad identity at display-scale/artwork-scale-factor 3 (405x880 points)   -> make-a830-images.sh loop-m8-scale3
#   phone       iPhone XS Max identity (iPhone11,6, phone idiom, subtype 2688)            -> make-a830-images.sh loop-m8-phone
#   phone-plus  phone with target-type J120 (hw.targettype: launchd's jetsam properties exist for J120, not for D331p) and the phone
#               Data seed (工具/make-phone-seed.py from the same base seed): the APFS container grown to 2 GiB (the volatile RAM disk
#               maximum), com.apple.fairplayd.A2 disabled (its KeepAlive restart every 10 s), and the wallpaper overlay that the kernel
#               shows over /Library/Wallpaper/Collections with 工具/phone-wallpaper-collections.patch
#   phone-apps  phone-plus + boot-arg sheng-staged-apps=1 (the kernel copies the 29 deletable system apps, 231 MB, from the OS volume's
#               /private/var/staged_system_apps into Data before launchd) and the DataMigrator sentinel, so SystemAppMigrator installs
#               them during the boot's data migration (every boot: Data is volatile). Experimental: a longer boot.
#   phone-island       phone-plus with the iPhone 15 Pro identity for the Dynamic Island (iPhone16,1, subtype 2556, fallback
#                      iPhone15,2 as in D83's /product): SpringBoard creates the island only for ArtworkDeviceSubType 2556 or 2796 and
#                      centres it in the screen bounds, 125x36.67 points at y 11.33 (420-796 x 34-144 pixels), around the 13T's
#                      centred punch hole (ro.oplus.display.screenhole.positon 569,40:647,118). The 405.33x880-point screen gets
#                      SpringBoard's screen type 21 (393x852) by size whatever the identity; its status-bar gap is moved to the
#                      centre with a text fix-up (below). The Plus key stays the ring/silent switch (function-button_ringerab).
#   phone-island-apps  phone-apps with the same identity.
# phone-plus and phone-apps (and their island forms) take the kernel arguments of make-a830-images.sh's loop-m8-phone entry (so they
# follow its changes) and build the image themselves. Environment: A830_PACKAGE_SEED (base seed, below build-userspace/tree/build or
# absolute, as in make-a830-images.sh); LAYOUT_OUT_OF_BOX=1 leaves out pagani-reset-after (no timed reset) and names the image
# 开箱即用; A830_TARGET_TYPE replaces the phone variants' target-type; A830_ISLAND_FIXUP=0 leaves the island's status-bar fix-up out;
# A830_CORNER_RADIUS=<points> adds /product display-corner-radius (MobileGestalt DeviceCornerRadius, absent by default).
# A830_CI_GL_FIXUP=0 leaves out the Core Image fix-up (on by default, every variant: below, before the board edit).
# A830_EXTRA_FIXUPS="<fix-up> ..." appends text fix-ups to the board's list (e.g. the decoder fix-ups of
# build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt); unset adds nothing.
# A830_OWNER_TRUST=<bundle> replaces build/adreno-metal-plugin/owner-trust.bundle, for the image and as the base of A830_AUDIO's bundle
# (a plugin built elsewhere, next2/metal-for-apps: its bundle trusts the old plugin and the new one). A830_EXTRA_ARGS="arg ..." appends
# kernel arguments (e.g. pagani-gpu-va=16) to the phone-plus/phone-apps forms.
# Every variant carries the time of this build as /chosen/pagani-build-time (Unix seconds, u32; A830_BUILD_TIME=<seconds> replaces it):
# PaganiArmDTPlatform's calendar starts there with boot-arg pagani-rtc=1 and no pagani-rtc-offset, and it is read only then.
# A830_DIAG=<dir> adds the PaganiDiag daemon (UniXNU-Pagani-Drivers drivers/PaganiDiag, scripts/build-paganidiag.py output) to the
# phone-plus/phone-apps seed (make-phone-seed.py --diag-daemon) and boot-arg pagani-diag=1, which turns it on; the owner trust bundle
# must carry its CDHash (A830_OWNER_TRUST=<bundle> from scripts/make-diag-trust.py; checked below). With A830_PREBUILT_SEED the seed
# already carries the daemon (scripts/replace-diag-in-seed.py): A830_DIAG=1 adds the boot-arg, the CDHash comes from the seed's .json.
# The kernel needs phone-diag-sysctl.patch, which starts the daemon in place of DumpPanic and lets the daemon's own image, and only
# that, past the launch constraints, so the image needs no sheng-no-lc=1 and differs from a daily one by the daemon alone.
# A830_BATTERY=1 adds boot-arg pagani-battery=1 to the phone-plus/phone-apps forms (17 bytes of the command line):
# PaganiBattery, a default-off kernel component, then shows the fuel gauge's charge without the ADSP (pagani-adsp stays unset).
# A830_SENSORS=N adds boot-arg pagani-sensors=N (17 or 18 bytes): PaganiSensors, a default-off kernel component; 1 publishes the iOS sensor
# services only (MobileGestalt's ambient-light-sensor; no samples), 2 and 3 also need the ADSP (A830_EXTRA_ARGS="pagani-adsp=1"):
# 2 looks the sensors up on its sensor hub (boot log only), 3 streams what iOS asks for and makes MobileGestalt's accel and gyro nodes;
# +4 the FastRPC file service of the sensor PD (5, 6, 7), +8 with 3 the orientation service and its 'ashd' user client (11, 15).
# A830_OWNED_MEMORY=<bytes> (hex or decimal) replaces board.toml's owned_memory_size for this image: the RAM window XNU gets, ADT
# /chosen/darwinboot-memory-size, nothing on the command line. 256 MiB to 8 GiB in 2 MiB steps: UniXNU's ADT generator and DarwinBoot
# (WindowSize.h) refuse anything else. dram_size and the DTS stay, they do not follow the window. Above 6 GiB the kernel needs
# phone-bootstrap-tables.patch: its bootstrap page table pool is read from kernel.macho below and an image it cannot boot is refused here.
# DarwinBoot takes the window from the first free run of UEFI's memory map that holds it; none: NO WINDOW on the screen, and the boot menu
# comes back without its countdown (Restart is in the menu).
# A830_VM_COMPRESSOR=2 adds "kern.vm_compressor" = 2 to the ADT's /defaults for this image (VM_PAGER_COMPRESSOR_NO_SWAP, the in-core compressor;
# vm_fault_init reads it when there is no vm_compressor boot-arg; it costs no byte of the command line). Unset or 0: no key, the kernel's
# default 8, no active compressor. It needs phone-compressor-thrashing.patch, or a jetsam for thrashing comes at a quarter of the rate.
# Host test: 工具/test-phone-island-layout.py.
set -euo pipefail
TOP=/Users/xiaochoumao/unixnu-13t
BOARD=$TOP/UniXNU-Pagani_SM8750-gpu/board.toml
VARIANT=${1:-}
case "$VARIANT" in
    scale3|phone|phone-plus|phone-apps|phone-island|phone-island-apps) ;;
    *) echo 'usage: make-a830-layout.sh scale3|phone|phone-plus|phone-apps|phone-island|phone-island-apps' >&2; exit 2 ;;
esac
# The island variants build as phone-plus and phone-apps (same seed options, same kernel arguments) with the island identity.
LAYOUT=$VARIANT
ISLAND=0
case "$LAYOUT" in
    phone-island) VARIANT=phone-plus ISLAND=1 ;;
    phone-island-apps) VARIANT=phone-apps ISLAND=1 ;;
esac
B=$TOP/build-userspace/tree/build
SEED_NAME=${A830_PACKAGE_SEED:-data-a830-publish-gmem.dbrdisk}
case "$SEED_NAME" in /*) BASE_SEED=$SEED_NAME ;; *) BASE_SEED=$B/$SEED_NAME ;; esac
# A relative A830_OWNER_TRUST means the caller's directory: the payload and boot image steps below run in UniXNU/.
case "${A830_OWNER_TRUST:-}" in ''|/*) ;; *) A830_OWNER_TRUST=$PWD/$A830_OWNER_TRUST ;; esac
case "${A830_DIAG:-}" in ''|/*|1) ;; *) A830_DIAG=$PWD/$A830_DIAG ;; esac

# A830_PREBUILT_SEED=<seed .dbrdisk> takes a phone seed this script made before (with its .json beside it) instead of making one
# from the base seed, with A830_OWNER_TRUST=<the bundle made with it>; with A830_AUDIO=1 that seed already carries the audio plug-in
# and the bundle its CDHash. For a new kernel when the base seed is gone (2026-10-31: the scratchpad that held it was wiped).
case "${A830_PREBUILT_SEED:-}" in ''|/*) ;; *) A830_PREBUILT_SEED=$PWD/$A830_PREBUILT_SEED ;; esac
if [ -n "${A830_PREBUILT_SEED:-}" ]; then
    { [ "$VARIANT" = phone-plus ] || [ "$VARIANT" = phone-apps ]; } || { echo "make-a830-layout: A830_PREBUILT_SEED is for the phone forms" >&2; exit 1; }
    [ -f "$A830_PREBUILT_SEED" ] && [ -f "${A830_PREBUILT_SEED%.dbrdisk}.json" ] || { echo "make-a830-layout: no seed or .json at $A830_PREBUILT_SEED" >&2; exit 1; }
    [ -f "${A830_OWNER_TRUST:-}" ] || { echo "make-a830-layout: A830_PREBUILT_SEED needs A830_OWNER_TRUST" >&2; exit 1; }
    case "${A830_DIAG:-}" in ''|1) ;; *) echo "make-a830-layout: a prebuilt seed carries the daemon already (replace-diag-in-seed.py): A830_DIAG=1" >&2; exit 1 ;; esac
    SEED=$A830_PREBUILT_SEED
    [ "${A830_AUDIO:-0}" = 1 ] && AUDIO_TRUST=$A830_OWNER_TRUST
fi

# The phone variants' seed first, so a failure there leaves board.toml untouched.
if [ -z "${A830_PREBUILT_SEED:-}" ] && { [ "$VARIANT" = phone-plus ] || [ "$VARIANT" = phone-apps ]; }; then
    [ -f "$BASE_SEED" ] || { echo "make-a830-layout: no base seed $BASE_SEED" >&2; exit 1; }
    SEED=$TOP/工具/seeds/$LAYOUT.dbrdisk
    mkdir -p "$TOP/工具/seeds"
    rm -f "$SEED" "${SEED%.dbrdisk}.json"
    MIGRATE=()
    # The migration installs the staged system apps; --force-install-apps lists them for SystemAppMigrator's force install pass
    # (make-phone-seed.py: the 23:07 run's migration marked every app hidden and removed it). A830_FORCE_INSTALL=0 leaves it out.
    [ "$VARIANT" = phone-apps ] && MIGRATE=(--migrate)
    [ "$VARIANT" = phone-apps ] && [ "${A830_FORCE_INSTALL:-1}" != 0 ] && MIGRATE+=(--force-install-apps)
    # The iPhone keyboard layouts (phone-keyboard-layouts.patch): KBLayouts_iPhone.dat of the iOS 17.7 iPhone root plus a family
    # drawn for 405.333 points (工具/make-phone-keyboard-layouts.py). UIKit sizes a phone keyboard by its family's design width
    # unless the screen is one of its known sizes; 405.333 x 880 is not, so with the official file the keyboard view stays 375 wide
    # while the keys are stretched to 405.333.
    KEYBOARD=()
    KB_SRC=$TOP/build-userspace/iphone-17.7-21H16/KBLayouts_iPhone.dat
    KB=$TOP/工具/phone-keyboard/KBLayouts_iPhone.dat
    if [ -f "$KB_SRC" ]; then
        python3 -B "$TOP/工具/make-phone-keyboard-layouts.py" "$KB_SRC" "$KB" > /dev/null
        KEYBOARD=(--keyboard "$KB")
        # The keyboard's preference fixes (make-phone-seed.py --keyboard-prefs); A830_KEYBOARD_PREFS=0 leaves them out.
        [ "${A830_KEYBOARD_PREFS:-1}" != 0 ] && KEYBOARD+=(--keyboard-prefs)
    fi
    DIAG=()
    [ "${A830_DIAG:-}" = 1 ] && { echo "make-a830-layout: A830_DIAG=1 is for A830_PREBUILT_SEED; a seed made here takes A830_DIAG=<dir>" >&2; exit 1; }
    [ -n "${A830_DIAG:-}" ] && DIAG=(--diag-daemon "$A830_DIAG")
    # The Control Center module sizes of the iPhone identity (phone-controlcenter-settings.patch): DefaultModuleSettings~iphone.plist
    # that 工具/make-phone-controlcenter-overlay.py derives from the OS volume's ~ipad one; without it every module is drawn 1x1.
    # A830_CONTROLCENTER=0 leaves it out.
    # The peach Wi-Fi firmware pulled from the phone (A830_WIFI_FW=<dir>, build-userspace/a830-inputs/wifi/make-wifi-firmware-seed.md)
    # to /private/var/ngpu/firmware/wlan of the seed, for PaganiWifi when the files are not on modem_a. Absent: nothing added.
    WIFI=()
    [ -n "${A830_WIFI_FW:-}" ] && WIFI=(--wifi-firmware "$A830_WIFI_FW")
    CONTROLCENTER=()
    CC_DIR=$TOP/工具/phone-controlcenter-overlay
    [ "${A830_CONTROLCENTER:-1}" != 0 ] && [ -f "$CC_DIR/DefaultModuleSettings~iphone.plist" ] && CONTROLCENTER=(--controlcenter "$CC_DIR")
    python3 -B "$TOP/工具/make-phone-seed.py" --gib 2 --disable-jobs com.apple.fairplayd.A2,com.apple.routined \
        --wallpaper "$TOP/工具/phone-wallpaper-overlay" ${KEYBOARD[@]+"${KEYBOARD[@]}"} ${MIGRATE[@]+"${MIGRATE[@]}"} \
        ${DIAG[@]+"${DIAG[@]}"} ${CONTROLCENTER[@]+"${CONTROLCENTER[@]}"} ${WIFI[@]+"${WIFI[@]}"} "$BASE_SEED" "$SEED"
    # A830_AUDIO=1: the PaganiAudio audio server plug-in (silent output device until the ADSP path, so the volume HUD and audio
    # routes exist) added to this seed and its CDHash to a copy of the owner trust bundle (PaganiAudio/scripts/add-pagani-audio-data.py);
    # the image then takes that bundle and boot-arg pagani-audio=1 (pagani-audio-addon-mount.patch shows it over the HAL directory).
    if [ "${A830_AUDIO:-0}" = 1 ]; then
        AUDIO_SEED=${SEED%.dbrdisk}-audio.dbrdisk
        rm -f "$AUDIO_SEED" "${AUDIO_SEED%.dbrdisk}.json" "${AUDIO_SEED%.dbrdisk}-owner-trust.bundle"
        rm -rf "${AUDIO_SEED%.dbrdisk}.hal"     # the plug-in the script builds next to its output (it refuses to overwrite)
        python3 -B "$TOP/UniXNU-Pagani-Drivers/drivers/PaganiAudio/scripts/add-pagani-audio-data.py" \
            ${A830_OWNER_TRUST:+--trust "$A830_OWNER_TRUST"} "$SEED" "$AUDIO_SEED" > /dev/null
        SEED=$AUDIO_SEED
        AUDIO_TRUST=${AUDIO_SEED%.dbrdisk}-owner-trust.bundle
        [ -f "$AUDIO_TRUST" ] || { echo "make-a830-layout: no audio trust bundle $AUDIO_TRUST" >&2; exit 1; }
    fi
fi

# Core Image without OpenGL ES (2026-10-30, next2/metal-for-apps.md). A process that finds no Metal device (not named by the plugin's
# NativeAdrenoMetal.clients, or every GPU slot taken) makes -[CIContext initWithOptions:] fall back to CI::GLContext, whose init calls
# the GL dispatch table that no OpenGL ES driver filled here: MobileSMS jumped to address 0 five times (IconServices'
# _MI_sharedIconCompositorContext). CIGLIsUsable() returns _wrapGLIsUsable(), "mov w0, #1; ret" (VM 0x1976f4f98,
# dyld_shared_cache_arm64.07 file offset 0x2e2cf98, followed by _wrapGLIsAccelerated, the same two instructions); the fix-up makes it
# "mov w0, #0", so with neither Metal nor GL -[CIContext initWithOptions:] and internalContextWithEAGLContext: return nil, and
# IconServices draws the icon with Core Graphics. A process with a Metal device never reaches the GL path. Any identity (no OpenGL ES
# driver exists on this hardware). It travels in board.toml's [payload] text_fixups (/chosen/text-fixups), not on the command
# line (payload.py's KERNEL_ARGS_LIMIT), which the island's fix-up and loop-m8-phone's arguments already fill to 876 bytes. A board list replaces the OS
# profile's, so the board gets the profile's four fix-ups (shared cache .15/.13, BTAudioHALPlugin) followed by this one.
OS_PROFILE=$TOP/UniXNU/profiles/os/ipados-17.7.11-21H461/profile.toml
CI_FIXUP=
if [ "${A830_CI_GL_FIXUP:-1}" != 0 ]; then
    CI_FIXUP=dyld_shared_cache_arm64.07,2e2cf98,20008052c0035fd620008052c0035fd6,00008052c0035fd620008052c0035fd6
fi
# The island's status-bar gap. SpringBoard publishes screen type 21's exclusion area (+[UISDisplaySingleRectShape(SpringBoard)
# sb_displayShapeForScreenType:]) as x 402/3, y 34/3, 375x110/3 points, centred on a 393-point screen, and UIKit's DynamicSplit status
# bar lays out around it (sensorAreaCenterOffsetFromTrailing), while SBSystemApertureViewController centres the island itself in the
# screen bounds (UIRectCenteredXInRectScale): on 405.33 points the gap would sit 6.17 points left of the island. The fix-up turns the
# literal 402.0 (dyld_shared_cache_arm64.25 file offset 0x219d270, VM 0x1d0231270, read only by that rect) into 420.5. The kernel
# (native-text-fixup.patch) changes the page after its signature check and only when the bytes match; A830_ISLAND_FIXUP=0 omits it.
# It travels with the Core Image one in /chosen/text-fixups (it was a text-fixup0 boot argument until the command line grew to 996
# bytes with the input script and the keyboard trace, 2026-10-31).
ISLAND_FIXUP=
if [ "$ISLAND" = 1 ] && [ "${A830_ISLAND_FIXUP:-1}" != 0 ]; then
    ISLAND_FIXUP=dyld_shared_cache_arm64.25,219d270,0000000000207940,0000000000487a40
fi
# The phone keyboard's frame (next/kb/keyboard-405.md section 6, F2). _UIKeyboardGetDerivedKeyboardForSpecificOrientation (UIKitCore,
# VA 0x18a2af038, dyld_shared_cache_arm64.03 file offset 0x3503038) knows the keyboard of a few phone screen sizes only; for 405.333x880
# it derives none, so the keyboard view's frame does not match the 405-point keys our layout family draws, and touches on the keys never
# reach UIKeyboardLayoutStar (2026-10-31 00:30 run: SpringBoard took 206 synthetic touch events with the Spotlight keyboard up, "set 11",
# and 0 key downs). F2 turns its 360x780 test into "reference height == 880.0" (the 880.0 constant at 0x18b33c218) into the same flag
# (w21) and skips the rest of that test: our screen takes the 390-point phones' path (iPhone-PortraitChoco, keyboard width 405.333).
# Phone forms only (the code runs in the phone-idiom branch); A830_KEYBOARD_F2=0 leaves it out.
KEYBOARD_FIXUP=
if { [ "$VARIANT" = phone-plus ] || [ "$VARIANT" = phone-apps ]; } && [ "${A830_KEYBOARD_F2:-1}" != 0 ]; then
    KEYBOARD_FIXUP=dyld_shared_cache_arm64.03,3503038,02c941fd0020621ee8179f1a698400f0,020d41fd2020621ef5179f1a05000014
fi
KEEP=$(mktemp)
cp "$BOARD" "$KEEP"
trap 'cp "$KEEP" "$BOARD"; rm -f "$KEEP"' EXIT
python3 - "$BOARD" "$VARIANT" "${A830_TARGET_TYPE:-}" "$ISLAND" "${A830_CORNER_RADIUS:-}" "$CI_FIXUP" "$OS_PROFILE" "$ISLAND_FIXUP" "$KEYBOARD_FIXUP" "${A830_BUILD_TIME:-$(date +%s)}" "${A830_EXTRA_FIXUPS:-}" <<'EOF'
import sys, tomllib
path, variant, target_override, island, corner = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == '1', sys.argv[5]
ci_fixup, os_profile, island_fixup, keyboard_fixup = sys.argv[6], sys.argv[7], sys.argv[8], sys.argv[9]
build_time = int(sys.argv[10])
extra_fixups = sys.argv[11].split()
if not 0 < build_time < 1 << 32:
    sys.exit(f'make-a830-layout: A830_BUILD_TIME={build_time} is out of range')
phone = variant != 'scale3'
# SpringBoard (-[SpringBoard _completeStartupAfterMainSceneConnect:]), SpringBoardUIServices (SBUIIsSystemApertureEnabled) and
# SpringBoardServices (SBSIsSystemApertureAvailable) turn the Dynamic Island on when MobileGestalt's ArtworkTraits
# ArtworkDeviceSubType (/product artwork-device-subtype) is 2556 or 2796, and on nothing else: the island variants claim the iPhone 15
# Pro (iPhone16,1, D83: subtype 2556, compatible-device-fallback iPhone15,2), the others the iPhone XS Max.
name, model, fallback, subtype = (('iPhone 15 Pro', 'iPhone16,1', 'iPhone15,2', 2556) if island else
                                  ('iPhone XS Max', 'iPhone11,6', 'iPhone11,2', 2688))
s = open(path).read()
def sub(old, new):
    global s
    if s.count(old) != 1:
        sys.exit(f'make-a830-layout: expected one {old!r} in board.toml')
    s = s.replace(old, new, 1)
# @3x: 1216x2640 pixels become 405x880 points, the size class of a large iPhone.
sub('[adt.chosen.u32]\ndisplay-scale = 2\n', f'[adt.chosen.u32]\ndisplay-scale = 3\npagani-build-time = {build_time}\n')
sub('[adt.product.u32]\nthin-bezel = 1\n', '[adt.product.u32]\nthin-bezel = 1\nartwork-scale-factor = 3\n'
    + (f'artwork-device-subtype = {subtype}\n' if phone else ''))
if phone:
    # DeviceClass, GSDeviceName and UIKit's idiom all follow the root model's prefix (libMobileGestalt computes DeviceClass and
    # GSDeviceName from the same device-class function; UIKit maps DeviceClassNumber): iPhone11,6 (iPhone XS Max, A12) gives the phone
    # idiom. The root compatible (J120AP) and the product-id stay the iPad's, which the audio server and MediaExperience key their
    # tables on. Device family does not hide apps: every system app but Photo Booth declares UIDeviceFamily [1, 2].
    # target-type is hw.targettype, from which launchd loads /System/Library/LaunchDaemons/com.apple.jetsamproperties.<it>.plist: the
    # iPad root has J120, J121, J207 and J208 only, so phone (D331p) runs without any jetsam configuration; phone-plus and phone-apps
    # keep J120 (it does not enter DeviceClass or ProductType).
    target = 'D331p' if variant == 'phone' else 'J120'
    # A830_TARGET_TYPE overrides it (2026-10-30 bisect: the ramoops log survived the D331p runs and was lost in every J120 run).
    target = target_override or target
    sub('[adt.product.string]\nmodel = "PKX110"\n', f'[adt.product.string]\nmodel = "PKX110"\nproduct-name = "{name}"\n'
        f'product-description = "{name}"\nartwork-device-idiom = "phone"\ncompatible-device-fallback = "{fallback}"\n'
        f'\n[adt.root.string]\nmodel = "{model}"\ntarget-type = "{target}"\n')
extra = [f for f in (ci_fixup, island_fixup, keyboard_fixup) if f] + extra_fixups
if extra:
    # /chosen/text-fixups (UniXNU scripts/unixnu/payload.py, native-text-fixup.patch, at most 32): the OS profile's fix-ups, then the
    # Core Image and the island ones above (the board's list replaces the profile's in the merge).
    if 'text_fixups' in s:
        sys.exit('make-a830-layout: board.toml already has text_fixups; add the Core Image fix-up there by hand')
    try:
        inherited = tomllib.loads(open(os_profile).read()).get('payload', {}).get('text_fixups', [])
    except OSError as error:
        sys.exit(f'make-a830-layout: {error}: the OS profile\'s text fix-ups are needed beside the Core Image one')
    fixups = [f for f in inherited if f not in extra] + extra
    sub('[payload]\n', '[payload]\ntext_fixups = [' + ', '.join(f'"{f}"' for f in fixups) + ']\n')
if corner:
    # MobileGestalt's DeviceCornerRadius: /product display-corner-radius, a u32 numerator and denominator in points (D83: 55). Without
    # it libMobileGestalt falls back to a fixed value for a list of older models and to 0 otherwise.
    numerator = round(float(corner) * 3)
    if not 0 < numerator < 1 << 32:
        sys.exit(f'make-a830-layout: A830_CORNER_RADIUS={corner} is out of range')
    value = numerator.to_bytes(4, 'little').hex() + (3).to_bytes(4, 'little').hex()
    if '\n[adt.product.bytes]\n' in s:
        sub('\n[adt.product.bytes]\n', f'\n[adt.product.bytes]\ndisplay-corner-radius = "{value}"\n')
    else:
        s += f'\n[adt.product.bytes]\ndisplay-corner-radius = "{value}"\n'
# A830_DROP_ARGS also takes options of the board's own kernel_args (the loop below takes make-a830-images.sh's).
import os, re
for option in os.environ.get('A830_DROP_ARGS', '').split():
    s = re.sub(r'"%s(=[^"]*)?",\s*' % re.escape(option), '', s)
# A830_PANEL=<mask> replaces the board's pagani-panel=1 (drivers/PaganiPanel/README.md): appended it would cost the option twice.
panel = os.environ.get('A830_PANEL')
if panel:
    if not panel.isdigit() or s.count('"pagani-panel=1"') != 1:
        sys.exit('make-a830-layout: A830_PANEL takes a number, in a board with one pagani-panel=1')
    s = s.replace('"pagani-panel=1"', '"pagani-panel=%s"' % panel)
# A830_OWNED_MEMORY, A830_VM_COMPRESSOR: this image's window and compressor (header above).
owned = os.environ.get('A830_OWNED_MEMORY')
if owned:
    try:
        size = int(owned, 0)
    except ValueError:
        sys.exit(f'make-a830-layout: A830_OWNED_MEMORY={owned} is not a number')
    if not 0x10000000 <= size <= 0x200000000 or size % 0x200000:
        sys.exit('make-a830-layout: A830_OWNED_MEMORY takes 256 MiB to 8 GiB in 2 MiB steps')
    s, count = re.subn(r'^owned_memory_size = "[^"]*"$', f'owned_memory_size = "{size:#x}"', s, flags=re.M)
    if count != 1:
        sys.exit('make-a830-layout: expected one owned_memory_size in board.toml')
compressor = os.environ.get('A830_VM_COMPRESSOR', '')
if compressor not in ('', '0'):
    if compressor != '2':
        sys.exit('make-a830-layout: A830_VM_COMPRESSOR takes 2: mode 32 would swap to a file on the RAM disk')
    sub('[adt.defaults.u32]\nsleep-disabled = 1\n', '[adt.defaults.u32]\nsleep-disabled = 1\n"kern.vm_compressor" = 2\n')
open(path, 'w').write(s)
EOF

# A window above the 6 GiB every kernel so far was run with: the kernel's pool of bootstrap page tables has to hold it. Two roots, the screen's L2
# page (early_boot_progress_map), and for the V=P and the KVA mapping each one L2 page per GiB plus one, as either base may straddle a GiB: the
# worst case, whatever address UEFI gives the window. A kernel that lacks them resets at once, before it logs anything.
if [ -n "${A830_OWNED_MEMORY:-}" ]; then
    python3 - "$TOP/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho" "$A830_OWNED_MEMORY" <<'EOF4'
import os, subprocess, sys
kernel, size = sys.argv[1], int(sys.argv[2], 0)
if size > 0x180000000:
    nm = subprocess.run(['llvm-nm', '-n', kernel], capture_output=True, text=True,
                        env=dict(os.environ, PATH=os.environ['PATH'] + ':/opt/homebrew/opt/llvm/bin'))
    if nm.returncode:
        sys.exit(f'make-a830-layout: llvm-nm {kernel}: {nm.stderr.strip()}')
    lines = [line.split() for line in nm.stdout.splitlines()]
    at = [i for i, line in enumerate(lines) if line[-1:] == ['_bootstrap_pagetables']]
    if len(at) != 1 or at[0] + 1 == len(lines):
        sys.exit(f'make-a830-layout: no _bootstrap_pagetables in {kernel}')
    pool = (int(lines[at[0] + 1][0], 16) - int(lines[at[0]][0], 16)) // 4096
    need = 3 + 2 * ((size + (1 << 30) - 1) // (1 << 30) + 1)
    if pool < need:
        sys.exit(f'make-a830-layout: a window of {size:#x} needs {need} bootstrap table pages and this kernel has {pool} '
                 '(phone-bootstrap-tables.patch)')
EOF4
fi

if [ "$VARIANT" = scale3 ] || [ "$VARIANT" = phone ]; then
    "$TOP/工具/make-a830-images.sh" "loop-m8-$VARIANT"
    exit 0
fi

# The kernel arguments of make-a830-images.sh's loop-m8-phone entry.
ARGS=()
while IFS= read -r arg; do ARGS+=("$arg"); done < <(python3 - "$TOP/工具/make-a830-images.sh" <<'EOF'
import shlex, sys
text = open(sys.argv[1]).read().replace('\\\n', ' ')
lines = [line for line in text.splitlines() if line.startswith('image loop-m8-phone ')]
if len(lines) != 1:
    sys.exit('make-a830-layout: no single loop-m8-phone entry in make-a830-images.sh')
words = shlex.split(lines[0], comments=True)
values = [words[i + 1] for i, word in enumerate(words) if word == '--kernel-arg']
if not values:
    sys.exit('make-a830-layout: the loop-m8-phone entry has no --kernel-arg')
print('\n'.join(values))
EOF
)
[ ${#ARGS[@]} -gt 0 ] || { echo 'make-a830-layout: no kernel arguments' >&2; exit 1; }
KARGS=()
for arg in "${ARGS[@]}"; do
    if [ "${LAYOUT_OUT_OF_BOX:-0}" = 1 ] && [[ "$arg" == pagani-reset-after=* ]]; then continue; fi
    # A830_RESET_AFTER=<s> replaces the timed reset (phone-apps boots 1-3 minutes longer while it installs the system apps).
    if [ -n "${A830_RESET_AFTER:-}" ] && [[ "$arg" == pagani-reset-after=* ]]; then arg=pagani-reset-after=$A830_RESET_AFTER; fi
    KARGS+=(--kernel-arg "$arg")
done
[ "$VARIANT" = phone-apps ] && KARGS+=(--kernel-arg sheng-staged-apps=1)
# A830_SYSTEM_APPS=1: the copied apps shown in /Applications as built-in apps, the migration switched off (phone-system-apps-view.patch).
[ "$VARIANT" = phone-apps ] && [ "${A830_SYSTEM_APPS:-0}" = 1 ] && KARGS+=(--kernel-arg pagani-system-apps=1)
[ -n "${AUDIO_TRUST:-}" ] && KARGS+=(--kernel-arg pagani-audio=1)
[ "${A830_BATTERY:-0}" = 1 ] && KARGS+=(--kernel-arg pagani-battery=1)
case "${A830_SENSORS:-0}" in
    0) ;;
    1|2|3|5|6|7|11|15) KARGS+=(--kernel-arg "pagani-sensors=$A830_SENSORS") ;;
    *) echo 'make-a830-layout: A830_SENSORS takes 0, 1, 2, 3, 5, 6, 7, 11 or 15' >&2; exit 1 ;;
esac
# PaganiDiag is off without this argument, also in a seed that carries it (header above).
[ -n "${A830_DIAG:-}" ] && KARGS+=(--kernel-arg pagani-diag=1)
for arg in ${A830_EXTRA_ARGS:-}; do KARGS+=(--kernel-arg "$arg"); done
# XNU takes the last value of a repeated option, so the earlier ones only cost bytes of the command line.
UNIQUE_KARGS=()
for ((i = 1; i < ${#KARGS[@]}; i += 2)); do
    later=0
    for ((j = i + 2; j < ${#KARGS[@]}; j += 2)); do [ "${KARGS[j]%%=*}" = "${KARGS[i]%%=*}" ] && later=1; done
    [ $later = 1 ] || UNIQUE_KARGS+=(--kernel-arg "${KARGS[i]}")
done
KARGS=("${UNIQUE_KARGS[@]}")
# A830_DROP_ARGS="name ..." leaves options out of the command line (their defaults apply): for options that repeat a default.
if [ -n "${A830_DROP_ARGS:-}" ]; then
    KEPT_KARGS=()
    for ((i = 1; i < ${#KARGS[@]}; i += 2)); do
        drop=0
        for name in $A830_DROP_ARGS; do [ "${KARGS[i]%%=*}" = "$name" ] && drop=1; done
        [ $drop = 1 ] || KEPT_KARGS+=(--kernel-arg "${KARGS[i]}")
    done
    KARGS=("${KEPT_KARGS[@]}")
fi

if [ "${LAYOUT_OUT_OF_BOX:-0}" = 1 ]; then
    NAME=m8-$LAYOUT
    case "$LAYOUT" in
        phone-plus) ZH=boot-A830-GPU桌面-iPhone布局-增强-开箱即用 ;;
        phone-apps) ZH=boot-A830-GPU桌面-iPhone布局-增强-系统应用-开箱即用 ;;
        phone-island) ZH=boot-A830-GPU桌面-iPhone15Pro灵动岛-开箱即用 ;;
        phone-island-apps) ZH=boot-A830-GPU桌面-iPhone15Pro灵动岛-系统应用-开箱即用 ;;
    esac
else
    NAME=loop-m8-$LAYOUT
    case "$LAYOUT" in
        phone-plus) ZH=boot-A830-回路-M8桌面-iPhone布局-增强 ;;
        phone-apps) ZH=boot-A830-回路-M8桌面-iPhone布局-增强-系统应用 ;;
        phone-island) ZH=boot-A830-回路-M8桌面-iPhone15Pro灵动岛 ;;
        phone-island-apps) ZH=boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用 ;;
    esac
fi

# As make-a830-images.sh's image(): payload, boot image, copies in 测试镜像.
LOGS=$TOP/build-userspace/logs/images
mkdir -p "$LOGS" "$TOP/测试镜像"
cd "$TOP/UniXNU"
export PATH="$PATH:/opt/homebrew/opt/llvm/bin"
DEV=../UniXNU-Pagani_SM8750-gpu
K=out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho
REC=out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/payload/payload.json
OWNER=${AUDIO_TRUST:-${A830_OWNER_TRUST:-$B/adreno-metal-plugin/owner-trust.bundle}}
# A base seed with an owner-built Metal plugin (set-seed-plugin.py writes its plugin_cdhash beside the seed) needs that CDHash in the
# owner trust bundle, or the phone boots with an untrusted plugin and no GPU device for any client: refuse before building.
python3 - "${BASE_SEED%.dbrdisk}.json" "$OWNER" <<'EOF2'
import json, sys
try:
    cdhash = json.load(open(sys.argv[1])).get('plugin_cdhash')
except (OSError, ValueError):
    cdhash = None
if cdhash and bytes.fromhex(cdhash) not in open(sys.argv[2], 'rb').read():
    sys.exit(f'make-a830-layout: the seed\'s Metal plugin {cdhash} is not in {sys.argv[2]} (set A830_OWNER_TRUST)')
EOF2
# The PaganiDiag daemon in the seed runs only with its CDHash in the owner trust bundle.
if [ -n "${A830_DIAG:-}" ] && { [ "$VARIANT" = phone-plus ] || [ "$VARIANT" = phone-apps ]; }; then
    python3 - "$A830_DIAG" "${SEED%.dbrdisk}.json" "$OWNER" <<'EOF3'
import json, sys
c = json.load(open(sys.argv[2]))['diag_daemon']['cdhash'] if sys.argv[1] == '1' else json.load(open(sys.argv[1] + '/trust.json'))['cdhash']
if bytes.fromhex(c) not in open(sys.argv[3], 'rb').read():
    sys.exit(f'make-a830-layout: PaganiDiag {c} is not in {sys.argv[3]} (set A830_OWNER_TRUST)')
EOF3
fi
./unixnu payload -D $DEV --out out-gpu --kernel $K --data-ramdisk "$SEED" --owner-trust-cache "$OWNER" "${KARGS[@]}" \
    > "$LOGS/$NAME-payload.log" 2>&1
./unixnu bootimg -D $DEV --out out-gpu --kernel $K --data-ramdisk "$SEED" --owner-trust-cache "$OWNER" "${KARGS[@]}" \
    -o "out-gpu/$NAME.img" > "$LOGS/$NAME-boot.log" 2>&1
cp $REC "out-gpu/$NAME.payload.json"
cp "out-gpu/$NAME.img" "$TOP/测试镜像/$ZH.img"
cp $REC "$TOP/测试镜像/$ZH.payload.json"
echo "$NAME -> 测试镜像/$ZH.img $(shasum -a 256 "out-gpu/$NAME.img" | cut -c1-16)  seed $(basename "$SEED") from $(basename "$BASE_SEED")"
