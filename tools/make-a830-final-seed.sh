#!/bin/bash
# Compose the Data seed of the full image from the seeds of 2026-10-31, whose bases are gone (each step works on a copy of the last):
#   s1-metal2-wallpaper  the Metal plug-in (bridge patch 0018: Find My; RenderBox family cap: widgets) and the wallpaper overlay
#   s1b-cache            + the shader cache pack generated for that runtime (make-shader-cache-a830.py --replace-pack --prime-off): the
#                        plug-in's runtime id changes with its sources and a stale pack means every pipeline compiles on the phone
#   s2-audio             + the PaganiAudio plug-in (route repair, Actuator device), its old CDHash out of the owner trust bundle
#   s3-full              + the haptics overlays (J120 tunings, MediaExperience tables), keyboard haptics, inline completion off, SOS off,
#                        powerlogHelperd off
#   s4-diag              + the morning feature check daemon (idle without pagani-diag=1)
#   s5-final             + the flashlight plug-in (idle without pagani-torch-ios=1)
#   s6-final             + ML tap typing off for the keyboard
#   s7-final             + what stops typing from slowing down exponentially without a network (edit-phone-seed-launchd.py --text-input)
#   s7b-final            + F4: the daemons of round-1101/f4-disable-list.md disabled; a variant to compare with s7-final, not its successor
#   s7bt-final           + com.apple.bluetoothd enabled, for the images with pagani-bt=40 or 48 and the two text fix-ups of PaganiBt; a variant
#   s7cam-final          + the camera's test pattern marker in H6ISP.mediacapture, for the images with pagani-torch-ios=1; a variant
#   s4-diag              + the PaganiDiag daemon, its CDHash in the owner trust bundle (inert without boot-arg pagani-diag=1)
# Usage: make-a830-final-seed.sh            rerun after a plug-in or overlay change; nothing outside build-userspace/a830-inputs/final
#        make-a830-final-seed.sh s7         only s7-final and s7b-final, from the s6-final that is there, which stays as it is
# Environment: AUDIO_HAL=<dir> (default audio2/out/hal), HAPTICS_OVERLAY=<dir> (default audio2/out/haptics-overlay), DIAG_BUILD=<dir>
# (default diag/out: UniXNU-Pagani-Drivers drivers/PaganiDiag/scripts/build-paganidiag.py --out), CAM_PLUGIN=<dir> (default camera/out/plugin:
# UniXNU-Pagani-Drivers drivers/PaganiCamera/scripts/build-pagani-camera-plugin.py --out).
# The trust bundle of s3-full is s2-audio's: overlays and preferences are data. The image takes s4-diag and its own bundle through
# A830_PREBUILT_SEED and A830_OWNER_TRUST (make-a830-layout.sh; A830_DIAG=1 turns the daemon on).
set -euo pipefail
TOP=/Users/xiaochoumao/unixnu-13t
IN=$TOP/build-userspace/a830-inputs
F=$IN/final
AUDIO_HAL=${AUDIO_HAL:-$IN/audio2/out/hal}
HAPTICS_OVERLAY=${HAPTICS_OVERLAY:-$IN/audio2/out/haptics-overlay}
DIAG_BUILD=${DIAG_BUILD:-$IN/diag/out}
CAM_PLUGIN=${CAM_PLUGIN:-$IN/camera/out/plugin}
PD=$TOP/UniXNU-Pagani-Drivers/drivers/PaganiDiag
S1=$F/s1b-cache
S1_TRUST=$F/s1-metal2-wallpaper-owner-trust.bundle
S2=$F/s2-audio
S3=$F/s3-full
S4=$F/s4-diag
# Jobs of jitter.md 4.3 tier 1 that passed the check of round-1101/f4-disable-list.md (which also says why the other 19 of that table stay):
# reporting; updates, ads and commerce; what has no use without a radio, an account or the hardware.
F4_DISABLE=com.apple.analyticsd,com.apple.aggregated,com.apple.aggregated.addaily,com.apple.wirelessinsightsd,com.apple.OTATaskingAgent
F4_DISABLE=$F4_DISABLE,com.apple.OTACrashCopier,com.apple.betaenrollmentd,com.apple.UsageTrackingAgent
F4_DISABLE=$F4_DISABLE,com.apple.softwareupdateservicesd,com.apple.mobile.softwareupdated,com.apple.ap.adprivacyd,com.apple.ap.promotedcontentd
F4_DISABLE=$F4_DISABLE,com.apple.attributionkitd,com.apple.amsengagementd
F4_DISABLE=$F4_DISABLE,com.apple.icloud.fmflocatord,com.apple.mediastream.mstreamd,com.apple.videosubscriptionsd,com.apple.sociallayerd
F4_DISABLE=$F4_DISABLE,com.apple.softposreaderd,com.apple.coreidvd,com.apple.financed,com.apple.announced,com.apple.dt.remotepairingdeviced
F4_DISABLE=$F4_DISABLE,com.apple.cmfsyncagent,com.apple.facetimemessagestored,com.apple.studentd,com.apple.familynotification
F4_DISABLE=$F4_DISABLE,com.apple.askpermissiond,com.apple.accessoryupdaterd,com.apple.mobilerepaird,com.apple.eyereliefd
F4_DISABLE=$F4_DISABLE,com.apple.voicebankingd,com.apple.biomesyncd
# Without a network and without Offline Dictation Status every keyboard client feeds UIKit's dictation availability notification back into
# itself (round-1101/keyboard.md, keyboard-verify.md). Preferences and the launch policy are data: the plug-ins' trust stays as it is.
s7_seeds() {
    local S6=$F/s6-final S7=$F/s7-final S7B=$F/s7b-final S7BT=$F/s7bt-final S7CAM=$F/s7cam-final
    rm -f "$S7.dbrdisk" "$S7.json" "$S7-owner-trust.bundle" "$S7B.dbrdisk" "$S7B.json" "$S7B-owner-trust.bundle" \
        "$S7BT.dbrdisk" "$S7BT.json" "$S7BT-owner-trust.bundle" "$S7CAM.dbrdisk" "$S7CAM.json" "$S7CAM-owner-trust.bundle"
    python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --text-input --trust "$S6-owner-trust.bundle" "$S6.dbrdisk" "$S7.dbrdisk"
    python3 -I -B "$TOP/工具/check-keyboard-seed.py" "$S7.dbrdisk"
    python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --disable "$F4_DISABLE" --trust "$S7-owner-trust.bundle" "$S7.dbrdisk" "$S7B.dbrdisk"
    python3 -I -B "$TOP/工具/check-keyboard-seed.py" "$S7B.dbrdisk"
    python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --enable com.apple.bluetoothd --trust "$S7-owner-trust.bundle" "$S7.dbrdisk" "$S7BT.dbrdisk"
    python3 -I -B "$TOP/工具/check-keyboard-seed.py" "$S7BT.dbrdisk"
    python3 -B "$TOP/UniXNU-Pagani-Drivers/drivers/PaganiCamera/scripts/add-pagani-camera-data.py" --plugin "$CAM_PLUGIN" --trust "$S7-owner-trust.bundle" \
        --replace --test-pattern "$S7.dbrdisk" "$S7CAM.dbrdisk"
    python3 -I -B "$TOP/工具/check-keyboard-seed.py" "$S7CAM.dbrdisk"
    ls -la "$S7.dbrdisk" "$S7-owner-trust.bundle" "$S7B.dbrdisk" "$S7B-owner-trust.bundle" "$S7BT.dbrdisk" "$S7CAM.dbrdisk"
}
if [ "${1:-}" = s7 ]; then
    s7_seeds
    exit 0
fi
[ -f "$S1.dbrdisk" ] && [ -f "$S1_TRUST" ] || { echo "no $S1" >&2; exit 1; }
rm -f "$S2.dbrdisk" "$S2.json" "$S2-owner-trust.bundle" "$S3.dbrdisk" "$S3.json" "$S3-owner-trust.bundle" \
    "$S4.dbrdisk" "$S4.json" "$S4-owner-trust.bundle"
python3 -B "$TOP/UniXNU-Pagani-Drivers/drivers/PaganiAudio/scripts/add-pagani-audio-data.py" --plugin "$AUDIO_HAL" \
    --trust "$S1_TRUST" --trust-out "$S2-owner-trust.bundle" --replace "$S1.dbrdisk" "$S2.dbrdisk"
python3 -B "$TOP/工具/make-phone-seed.py" --disable-jobs "" --haptics-overlay "$HAPTICS_OVERLAY" --keyboard-haptics --keyboard-prefs \
    "$S2.dbrdisk" "$F/s2b-haptics.dbrdisk"
# The long press that reads the logs (power + volume) is also the emergency SOS gesture: off, with a countdown alarm no one wants.
# powerlogHelperd only reads the database PerfPowerServices writes, and that job is disabled (prepare-ram-data.py): -[PLCoreStorage init] ends
# in exit(1), the first Mach message to it stays queued and launchd relaunches it every 10 s (run 20261031-223924: 921..1162 s, 25 times, each
# one a CPU launch boost near a touch). Nothing is lost: it never served a client here, the same PowerLog clients already live without plxpclogger.
rm -f "$S3.dbrdisk" "$S3.json"
python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --sos-off --disable com.apple.powerlogHelperd --trust "$S2-owner-trust.bundle" \
    "$F/s2b-haptics.dbrdisk" "$S3.dbrdisk"
[ -f "$S3-owner-trust.bundle" ] || cp "$S2-owner-trust.bundle" "$S3-owner-trust.bundle"
rm -f "$F/s2b-haptics.dbrdisk" "$F/s2b-haptics.json"
python3 -B "$TOP/工具/replace-phone-wallpaper.py" --check "$S3.dbrdisk"
python3 -I "$IN/sysfix/keyboard/apply-keyboard-prefs.py" --check "$S3.dbrdisk"
python3 -B "$PD/scripts/replace-diag-in-seed.py" --build "$DIAG_BUILD" --trust "$S3-owner-trust.bundle" "$S3.dbrdisk" "$S4.dbrdisk"
python3 -B "$PD/Test/check-seed.py" "$S4.dbrdisk" "$DIAG_BUILD"
# The flashlight plug-in for mediaserverd (data until pagani-torch-ios=1).
S5=$F/s5-final
rm -f "$S5.dbrdisk" "$S5.json" "$S5-owner-trust.bundle"
# Built by PaganiCamera, not add-pagani-torch-data.py: the capture device vendor is nil on this phone without the correction it carries, so the
# system flashlight never reaches the plug-in (PaganiCamera/README.md). No test pattern here: without a camera it only adds that correction.
python3 -B "$TOP/UniXNU-Pagani-Drivers/drivers/PaganiCamera/scripts/add-pagani-camera-data.py" --plugin "$CAM_PLUGIN" --trust "$S4-owner-trust.bundle" "$S4.dbrdisk" "$S5.dbrdisk"
# ML tap typing off: kbd runs it on the CPU for every key of an iPhone idiom (upstream/widgets-keyboard.md 2.3, 2.5); an iPad takes the classic
# path anyway. Preferences are data: the plug-ins' trust stays as it is.
S6=$F/s6-final
rm -f "$S6.dbrdisk" "$S6.json" "$S6-owner-trust.bundle"
python3 -I "$IN/sysfix/keyboard/apply-keyboard-prefs.py" --pref com.apple.keyboard:EnableMLTapTyping=false --trust "$S5-owner-trust.bundle" \
    "$S5.dbrdisk" "$S6.dbrdisk"
python3 -I "$IN/sysfix/keyboard/apply-keyboard-prefs.py" --check "$S6.dbrdisk"
python3 -B "$TOP/工具/edit-phone-seed-launchd.py" --check --json "$S6.dbrdisk" 2>/dev/null | grep '"com.apple.powerlogHelperd"' >/dev/null
ls -la "$S6.dbrdisk" "$S6-owner-trust.bundle"
s7_seeds
