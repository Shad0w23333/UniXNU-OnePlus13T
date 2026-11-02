#!/usr/bin/env python3
"""Derive the Data seed of the iPhone-layout images from a GPU Data seed (a DBRDISK1 packed APFS container, add-gpu-plugin-data.py output).

  make-phone-seed.py [--gib N] [--disable-jobs LABEL,...] [--wallpaper DIR] [--keyboard FILE] [--keyboard-prefs] [--migrate]
                     [--keep-inline-completions] [--force-install-apps] [--diag-daemon DIR] [--controlcenter DIR]
                     [--haptics-overlay DIR] [--keyboard-haptics] [--wifi-firmware DIR] [--sensor-pack FILE]
                     BASE.dbrdisk OUTPUT.dbrdisk

--gib N            grow the APFS container to N GiB (default 2; at most 2). The 13T keeps Data in a volatile RAM disk: NativeRamBlock serves
                   a packed seed of at most 2 GiB from RAM, allocating a 4 KiB page only when it is first written (a 4 MiB page table for
                   2 GiB); a larger seed is "big" and needs the persistent UFS store, without which every request fails
                   (drivers/NativeRamBlock/NativeRamBlock.cpp, logical > 0x80000000). Settings shows the container size as capacity.
--disable-jobs L   launchd job labels the seed's /private/var/db/com.apple.xpc.launchd/disabled.plist keeps from loading, edited in place
                   (owner root and mode kept). Default com.apple.fairplayd.A2: KeepAlive, but it exits with status 0 right after the
                   launch (FairPlayIOKit is not in the 13T's prelinked kernel), so launchd restarts it every 10 s (its ThrottleInterval)
                   and a client that sends it a request waits for its own timeout. Disabled, the lookup fails at once. "" keeps the policy.
--wallpaper DIR    copy DIR (make-phone-wallpaper-overlay.py) to /private/var/ngpu/wallpaper-collections, hard links kept (its two
                   wallpapers share their PNG images; copied twice they would not fit the 192 MiB boot payload), after
                   check-phone-wallpaper-overlay.py passed on it. The kernel shows it over /Library/Wallpaper/Collections only with
                   phone-wallpaper-collections.patch; without it the files are unused. A seed made before: replace-phone-wallpaper.py.
--keyboard FILE    copy FILE (KBLayouts_iPhone.dat of the iOS 17.7 iPhone root, 21H16) to /private/var/ngpu/textinputui-layouts. The
                   kernel shows it over /System/Library/PrivateFrameworks/TextInputUI.framework only with phone-keyboard-layouts.patch.
--controlcenter DIR  copy DIR (make-phone-controlcenter-overlay.py: DefaultModuleSettings~iphone.plist) to
                   /private/var/ngpu/controlcenterui. The kernel shows it over /System/Library/PrivateFrameworks/ControlCenterUI.framework
                   only with phone-controlcenter-settings.patch: without the ~iphone module sizes every Control Center module of the
                   iPhone identity is drawn 1x1.
--haptics-overlay DIR  copy DIR/tunings-j120 and DIR/mediaexperience-j120 (make-phone-haptics-overlay.py: the J120 haptic server
                   preset with its waveforms, the vibe engine's configuration, and the iPhone 15 Pro's system sound vibration and
                   ringer tables of the same root, each with its sentinel UniXNU-haptics-overlay.txt) to /private/var/ngpu/tunings-j120
                   and /private/var/ngpu/mediaexperience-j120 (directories 0755, files 0644, read back). The kernel shows them over
                   /Library/Audio/Tunings/J120 and /System/Library/PrivateFrameworks/MediaExperience.framework/J120 only with
                   phone-haptics-overlays.patch and boot-arg pagani-haptics-ios>=1 (the switch that also creates /product/haptics).
--keyboard-haptics the keyboard's haptic feedback in mobile's preferences: com.apple.keyboard.preferences KeyboardVisceral = true
                   (Settings, Sounds & Haptics, Keyboard Feedback, Haptic), through keyboard-prefs.py as --keyboard-prefs. UIKit plays
                   it only with DeviceSupportsClosedLoopHaptics (pagani-haptics-ios=2); without it the key does nothing.
--keyboard-prefs  the phone keyboard's preference fixes (phone-keyboard-trace/keyboard-prefs.py, next2/keyboard-touch.md 5.3 B and D)
                   in mobile's preferences: com.apple.keyboard KeyboardDisableHitTesting = true (UIKeyboardLayoutStar takes the key
                   under the finger itself instead of waiting for kbd's typing-model hit test, which the iPhone identity's letter
                   keys queue behind) and com.apple.keyboard.ContinuousPath KeyboardContinuousPathEnabled = false (Settings' "Slide
                   to Type" off: no touch left in the continuous-path state that drops every later touch-down). The keyboard drew
                   right under the iPhone identity but no key typed (2026-10-30); slightly weaker autocorrection, no slide typing.
                   Also com.apple.keyboard.preferences KeyboardInlineCompletion = false (Settings > General > Keyboard > "Show
                   Predictions Inline" off, sysfix/keyboard/NOTES.md): kbd generates inline completions (a beam search of up to 32
                   forward passes of the English transformer language model) inside every autocorrection request, and UIKit's
                   keyboard task queue waits for that request (-[UIKeyboardAutocorrectionController setNeedsAutocorrection]
                   defers a task that holds the queue until kbd answers), so the next key waits too; the iPhone runs the model on
                   its Neural Engine, the 13T on the CPU (unilm_joint_cpu, int8; MobileGestalt has no AppleNeuralEngineSubtype
                   without an ane-type property). Predictions, autocorrection and the QuickType bar stay; the user can turn the
                   setting back on.
--keep-inline-completions  (with --keyboard-prefs) leave KeyboardInlineCompletion alone (inline completions on, the iOS default).
--migrate          create the DataMigrator sentinel /private/var/datamigrator/migrate (DMMigrationSentinelManager): with it
                   DMIsMigrationNeeded() is true although LastSystemVersion is 21H461, so the boot runs the data migration, whose
                   SystemAppMigrator (its third plugin) installs the deletable system apps that the kernel copied into
                   /private/var/staged_system_apps (boot-arg sheng-staged-apps=1). The seed is volatile: the migration runs on every boot.
                   The kernel also needs phone-staged-apps-migration.patch: the volatile Data sets /chosen ephemeral-storage, with which
                   datamigrator skips every migration ("Ephemeral environment. Skipping migration."), sentinel or not; the patch answers
                   datamigrator alone "not ephemeral". The edited image is checked on a read-only attach with ownership: the sentinel, no
                   copy marker or staged directory in the seed, no installd state that could mark the apps uninstalled or the system app
                   migration done, the migration preferences, and MIGRATE_FREE free bytes for the copy and the installs.

--force-install-apps  (with --migrate) the 28 deletable system apps of 21H461 that support the iPhone family (all 29 but the iPad-only
                   Photo Booth) in /private/var/preferences/com.apple.demo.SADForceInstall.plist, an array of bundle IDs that
                   -[MISystemAppMigrator init] (SystemAppMigrator.migrator, any device) reads and -[MISystemAppMigrator
                   handleForceInstalledApps] sets to system app state 1 (install) after the capability, device family, restriction
                   and Fitness/News passes; and com.apple.mobile.installation SkipDeviceFamilyCheck = true in mobile's preferences
                   (-[MIDaemonConfiguration init], InstalledContentLibrary). The 2026-10-30 23:07 run copied all 29 apps and ran the
                   migration, but every app reached -[MISystemAppMigrator attemptToInstallSystemAppWithBundleID:...] with desired
                   state 2/6 ("Skipping installation of %@ because it is already uninstalled") and datamigrator removed all 29 staged
                   apps: one of those passes marked every app hidden (markHiddenAppAsUninstalled:).
--diag-daemon DIR  the PaganiDiag daemon (UniXNU-Pagani-Drivers drivers/PaganiDiag, scripts/build-paganidiag.py output DIR):
                   DIR/paganidiag to /private/var/factory_mount/paganidiag (0755; its CDHash must be DIR/trust.json's, which the
                   image's owner trust bundle must carry: scripts/make-diag-trust.py) and DIR/paganidiag.conf beside it when
                   present. launchd of 21H461 takes every /System/Library job from its signed cache, so no job starts the daemon:
                   with boot-arg pagani-diag=1 the kernel (phone-diag-sysctl.patch) runs it in place of the first exec of
                   /usr/libexec/DumpPanic (job com.apple.DumpPanic) when the file is there, and the daemon writes its lines through
                   kern.pagani_diag_line. The directory is the one the Sandbox platform policy lets xpcproxy run a program from
                   (drivers/PaganiDiag/Test/check-exec-policy.py). The launch policy is not changed; the seed is refused when it
                   disables com.apple.DumpPanic. A seed that exists already: scripts/replace-diag-in-seed.py.

--wifi-firmware DIR  the peach Wi-Fi firmware pulled from the phone (build-userspace/a830-inputs/wifi/make-wifi-firmware-seed.md)
                   to /private/var/ngpu/firmware/wlan, its layout kept (peach/amss20.bin, tmel_peach_20.elf, join.txt ...): regular
                   files only, 0644 (join.txt, a password in the clear: 0600), at most 64 MiB in all. PaganiWifi (pagani-wifi=5 or 6)
                   reads it there after Data is mounted when the files are not on modem_a; the seed is volatile, nothing is written to
                   the phone. A sha256 of each file but join.txt goes to the report. Vendor files: the seed stays local, never committed.

--sensor-pack FILE  the file pack of the ADSP's sensor protection domain (DRV/PaganiSensors/scripts/make-sensor-pack.py, from the phone's
                   /vendor/etc/sensors and persist registry) to /private/var/ngpu/sensors/pack.bin, 0644, at most 16 MiB. PaganiSensors'
                   FastRPC file service (pagani-sensors with +4) serves it to the sensor PD after Data is mounted. It holds the phone's
                   calibration: the seed stays local, never committed.

BASE is not modified; the volume UUID is unchanged. Writes OUTPUT and OUTPUT with .json (what was done). Runs on macOS (hdiutil, diskutil)."""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
TOP = Path('/Users/xiaochoumao/unixnu-13t')
sys.path.insert(0, str(TOP / 'UniXNU/drivers/DarwinBoot/Test'))
import packed_ramdisk  # noqa: E402

GIB = 1024**3
VOLATILE_MAX = 2 * GIB          # NativeRamBlock: logical > 0x80000000 needs the persistent store
LAUNCH_POLICY = 'db/com.apple.xpc.launchd/disabled.plist'
WALLPAPER = 'ngpu/wallpaper-collections'
KEYBOARD = 'ngpu/textinputui-layouts'
CONTROLCENTER = 'ngpu/controlcenterui'
WIFI_FIRMWARE = 'ngpu/firmware/wlan'
WIFI_FIRMWARE_MAX = 64 << 20
SENSOR_PACK = 'ngpu/sensors/pack.bin'
SENSOR_PACK_MAX = 16 << 20
CONTROLCENTER_FILE = 'DefaultModuleSettings~iphone.plist'
HAPTICS_OVERLAYS = ('tunings-j120', 'mediaexperience-j120')   # under /private/var/ngpu, each a make-phone-haptics-overlay.py directory
HAPTICS_SENTINEL = 'UniXNU-haptics-overlay.txt'
KEYBOARD_HAPTICS = ('com.apple.keyboard.preferences:KeyboardVisceral=true',)
SENTINEL = 'datamigrator/migrate'
MOBILE = 501
# The kernel writes the staged apps uncompressed: 445.4 MiB (231.3 MiB compressed on the OS volume) in 4214 files and 6575 directories
# of 21H461, plus APFS metadata; the installs move them (same volume) and add containers and LaunchServices records.
MIGRATE_FREE = 640 * 1024**2
INSTALLD_STATE = 'installd/Library/MobileInstallation'
KEYBOARD_PREFS = TOP / '工具/phone-keyboard-trace/keyboard-prefs.py'
FORCE_INSTALL = 'preferences/com.apple.demo.SADForceInstall.plist'
INSTALLATION_PREFS = 'mobile/Library/Preferences/com.apple.mobile.installation.plist'
# /System/Library/AppPlaceholders of 21H461 (29 deletable system apps), less com.apple.Photo-Booth (UIDeviceFamily [2]).
FORCE_INSTALL_APPS = (
    'com.apple.tv', 'com.apple.iBooks', 'com.apple.MobileAddressBook', 'com.apple.facetime', 'com.apple.DocumentsApp',
    'com.apple.findmy', 'com.apple.Fitness', 'com.apple.freeform', 'com.apple.Health', 'com.apple.Home', 'com.apple.Magnifier',
    'com.apple.Maps', 'com.apple.measure', 'com.apple.mobilecal', 'com.apple.mobilemail', 'com.apple.mobilenotes',
    'com.apple.MobileStore', 'com.apple.mobiletimer', 'com.apple.Music', 'com.apple.news', 'com.apple.podcasts',
    'com.apple.reminders', 'com.apple.Translate', 'com.apple.shortcuts', 'com.apple.stocks', 'com.apple.tips',
    'com.apple.VoiceMemos', 'com.apple.weather')
KEYBOARD_FIXES = ('com.apple.keyboard:KeyboardDisableHitTesting=true', 'com.apple.keyboard.ContinuousPath:KeyboardContinuousPathEnabled=false')
KEYBOARD_INLINE_OFF = 'com.apple.keyboard.preferences:KeyboardInlineCompletion=false'   # --keyboard-prefs unless --keep-inline-completions
# PaganiDiag (--diag-daemon): the daemon and its optional configuration.
DIAG_FILES = (('paganidiag', 'factory_mount/paganidiag', 0o755), ('paganidiag.conf', 'factory_mount/paganidiag.conf', 0o644))
# Its former place, from which xpcproxy may not run it: a base seed's files there are left behind by nothing.
DIAG_FORMER = ('ngpu/bin/paganidiag', 'ngpu/bin/paganidiag.conf', 'ngpu/daemons/org.unixnu.pagani.diag.plist')
DIAG_HOST_JOB = 'com.apple.DumpPanic'      # the kernel starts the daemon in place of this job's program


def diag_daemon_files(directory):
    """The files of a build-paganidiag.py output directory, after checking that the daemon carries the CDHash of its trust.json."""
    trust = json.loads((directory / 'trust.json').read_text()) if (directory / 'trust.json').is_file() else {}
    cdhash = trust.get('cdhash', '')
    detail = subprocess.run(['codesign', '--display', '--verbose=4', str(directory / 'paganidiag')], capture_output=True,
                            text=True).stderr
    actual = re.search(r'^CDHash=([0-9a-f]{40})$', detail, re.M)
    if not re.fullmatch(r'[0-9a-f]{40}', cdhash) or not actual or actual.group(1) != cdhash:
        sys.exit(f'--diag-daemon: {directory}/paganidiag does not carry the CDHash of {directory}/trust.json')
    files = [(directory / name, relative, mode) for name, relative, mode in DIAG_FILES if (directory / name).is_file()]
    if {name for name, _, _ in DIAG_FILES} - {source.name for source, _, _ in files} - {'paganidiag.conf'}:
        sys.exit(f'--diag-daemon: {directory} lacks a file of build-paganidiag.py')
    return files, cdhash


def stage_diag_daemon(mount, files, cdhash):
    """Copies the PaganiDiag files into the mounted seed; the seed's launch policy must leave com.apple.DumpPanic enabled."""
    policy = mount / LAUNCH_POLICY
    if policy.is_file() and plistlib.loads(policy.read_bytes()).get(DIAG_HOST_JOB) is True:
        sys.exit(f'--diag-daemon: the seed disables {DIAG_HOST_JOB}, in whose place the kernel starts the daemon')
    for relative in DIAG_FORMER:
        (mount / relative).unlink(missing_ok=True)
    staged = {}
    for source, relative, mode in files:
        target = mount / relative
        for parent in reversed(target.relative_to(mount).parents[:-1]):
            if not (mount / parent).is_dir():
                (mount / parent).mkdir()
                (mount / parent).chmod(0o755)
        shutil.copyfile(source, target)
        target.chmod(mode)
        if target.read_bytes() != source.read_bytes():
            sys.exit(f'--diag-daemon: /private/var/{relative} reads back different')
        staged['/private/var/' + relative] = f'{mode:o}'
    return dict(files=staged, cdhash=cdhash, host_job=DIAG_HOST_JOB)


def copy_tree_with_links(source, destination):
    """shutil.copytree that keeps hard links as hard links (and symlinks as symlinks); modes copied, owner left to the mount."""
    seen = {}

    def copy(src, dst):
        info = os.lstat(src)
        key = (info.st_dev, info.st_ino)
        if info.st_nlink > 1 and key in seen:
            os.link(seen[key], dst)
            return dst
        shutil.copy2(src, dst)
        seen[key] = dst
        return dst
    shutil.copytree(source, destination, symlinks=True, copy_function=copy)


def stage_haptics_overlay(mount, directory):
    """Copies the two haptics overlays into the seed's /private/var/ngpu, modes 0755/0644, and reads every file back."""
    staged = {}
    for name in HAPTICS_OVERLAYS:
        source, destination = directory / name, mount / 'ngpu' / name
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=False)
        files = 0
        for path in [destination, *destination.rglob('*')]:
            path.chmod(0o755 if path.is_dir() else 0o644)
            if path.is_file():
                if path.read_bytes() != (source / path.relative_to(destination)).read_bytes():
                    sys.exit(f'--haptics-overlay: /private/var/ngpu/{name}/{path.relative_to(destination)} reads back different')
                files += 1
        if not (destination / HAPTICS_SENTINEL).is_file():
            sys.exit(f'--haptics-overlay: {name} lost its sentinel')
        staged['/private/var/ngpu/' + name] = files
    return staged


def check_wallpaper(directory):
    """check-phone-wallpaper-overlay.py's rules (WallpaperKit, CAML image references, PNG) on a --wallpaper directory."""
    result = subprocess.run([sys.executable, '-B', str(TOP / '工具/check-phone-wallpaper-overlay.py'), str(directory)],
                            capture_output=True, text=True)
    if result.returncode:
        sys.exit('--wallpaper: ' + str(directory) + ' fails check-phone-wallpaper-overlay.py:\n' + result.stdout + result.stderr)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def grow(raw, target):
    """Grow the APFS container in the raw image to fill target bytes (the file is extended first)."""
    with raw.open('r+b') as handle:
        handle.truncate(target)
    attached = run('hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nomount', '-nobrowse', str(raw))
    store = re.match(r'/dev/(disk\d+)', attached).group(1)
    try:
        listing = plistlib.loads(subprocess.check_output(['diskutil', 'apfs', 'list', '-plist']))
        container = next((entry['ContainerReference'] for entry in listing['Containers']
                          if any(s['DeviceIdentifier'] == store for s in entry.get('PhysicalStores', []))), None)
        if not container:
            sys.exit('No APFS container on the attached seed')
        run('diskutil', 'apfs', 'resizeContainer', container, '0')
    finally:
        subprocess.run(['hdiutil', 'detach', store, '-force'], capture_output=True)
    header = raw.open('rb').read(4096)
    blocks = int.from_bytes(header[40:48], 'little')
    if header[32:36] != b'NXSB' or int.from_bytes(header[36:40], 'little') != 4096 or not target - 64 * 1024**2 <= blocks * 4096 <= target:
        sys.exit(f'The grown container is {blocks * 4096} bytes, not {target}')
    return blocks * 4096


def edit_policy(mount, disable):
    policy = mount / LAUNCH_POLICY
    if not policy.is_file():
        sys.exit('The seed has no ' + LAUNCH_POLICY)
    data = policy.read_bytes()
    jobs = plistlib.loads(data)
    for label in disable:
        jobs[label] = True
    edited = plistlib.dumps(jobs, fmt=plistlib.FMT_BINARY if data.startswith(b'bplist') else plistlib.FMT_XML)
    with policy.open('r+b') as handle:   # in place: same inode, so the owner (root) and the mode stay
        handle.write(edited)
        handle.truncate()
    return sorted(label for label, value in jobs.items() if value is True)


def absent(mount, relative, problems):
    """Whether /private/var/RELATIVE is missing from the seed mounted at MOUNT. A path the checking user cannot look up (a directory on
    the way it may not search) is a problem, not an absence: the check must not pass without having looked."""
    try:
        os.lstat(mount / relative)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError as error:
        problems.append(f'/private/var/{relative} cannot be checked here: {error.strerror}')
        return True
    return False


def check_migration(raw):
    """Attach the edited image read-only with ownership and check what the boot's data migration needs (see --migrate)."""
    mount = raw.parent / 'check'
    mount.mkdir()
    attached = plistlib.loads(subprocess.check_output(
        ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-readonly', '-nobrowse', '-owners', 'on',
         '-mountpoint', str(mount), '-plist', str(raw)]))
    device = attached['system-entities'][0]['dev-entry']
    problems, result = [], {}
    try:
        sentinel = mount / SENTINEL
        info = sentinel.lstat() if os.path.lexists(sentinel) else None
        if not info or not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IRUSR:
            problems.append('/private/var/' + SENTINEL + ' is no readable regular file')
        else:
            result['sentinel'] = f'{info.st_uid}:{info.st_gid} {stat.S_IMODE(info.st_mode):o}'
        parent = sentinel.parent.lstat()
        result['sentinel_directory'] = f'{parent.st_uid}:{parent.st_gid} {stat.S_IMODE(parent.st_mode):o}'
        # datamigrator runs as mobile: it needs to search the directory and, to remove the sentinel when it is done, to write it.
        if parent.st_uid != MOBILE or stat.S_IMODE(parent.st_mode) & 0o700 != 0o700:
            problems.append('/private/var/datamigrator is not mobile-owned and writable by it: ' + result['sentinel_directory'])
        for relative in ('db/.sheng-staged-apps', 'staged_system_apps'):
            if not absent(mount, relative, problems):
                problems.append(f'/private/var/{relative} is in the seed (the kernel copies the apps only without it)')
        for name in ('SystemAppInstallState.plist', 'MigrationInfo.plist', 'LastBuildInfo.plist'):
            if not absent(mount, f'{INSTALLD_STATE}/{name}', problems):
                problems.append(f'/private/var/{INSTALLD_STATE}/{name} is in the seed')
        try:
            preferences = plistlib.loads((mount / 'mobile/Library/Preferences/com.apple.migration.plist').read_bytes())
            disposition = preferences.get('DMUserDataDisposition') or {}
            result['last_system_version'] = preferences.get('LastSystemVersion')
            result['disposition_build'] = disposition.get('buildVersion')
            if result['last_system_version'] != result['disposition_build']:
                problems.append('com.apple.migration LastSystemVersion and DMUserDataDisposition buildVersion differ')
            result['migration_preference_keys'] = sorted(preferences)
        except (OSError, plistlib.InvalidFileException) as error:
            result['migration_preferences'] = f'unreadable here: {error}'
        usage = os.statvfs(mount)
        result['available_bytes'] = usage.f_bavail * usage.f_frsize
        if result['available_bytes'] < MIGRATE_FREE:
            problems.append(f'{result["available_bytes"]} bytes free, the copy and the installs need {MIGRATE_FREE}')
    finally:
        subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)
        mount.rmdir()
    if problems:
        sys.exit('The migration seed check failed: ' + '; '.join(problems))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gib', type=int, default=2)
    parser.add_argument('--disable-jobs', default='com.apple.fairplayd.A2')
    parser.add_argument('--wallpaper', type=Path)
    parser.add_argument('--keyboard', type=Path)
    parser.add_argument('--keyboard-prefs', action='store_true')
    parser.add_argument('--keep-inline-completions', action='store_true')
    parser.add_argument('--migrate', action='store_true')
    parser.add_argument('--force-install-apps', action='store_true')
    parser.add_argument('--diag-daemon', type=Path)
    parser.add_argument('--controlcenter', type=Path)
    parser.add_argument('--haptics-overlay', type=Path)
    parser.add_argument('--keyboard-haptics', action='store_true')
    parser.add_argument('--wifi-firmware', type=Path)
    parser.add_argument('--sensor-pack', type=Path)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    disable = [label for label in args.disable_jobs.split(',') if label]
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', label) for label in disable):
        sys.exit('--disable-jobs takes launchd labels separated by commas')
    if args.output.exists():
        sys.exit('Refuse to overwrite ' + str(args.output))
    if args.wallpaper and not (args.wallpaper / 'Collections~iphone.plist').is_file():
        sys.exit('--wallpaper takes a make-phone-wallpaper-overlay.py directory')
    if args.wallpaper:
        check_wallpaper(args.wallpaper)
    if args.controlcenter and not (args.controlcenter / CONTROLCENTER_FILE).is_file():
        sys.exit('--controlcenter takes a make-phone-controlcenter-overlay.py directory')
    if args.haptics_overlay and not all((args.haptics_overlay / name / HAPTICS_SENTINEL).is_file() for name in HAPTICS_OVERLAYS):
        sys.exit('--haptics-overlay takes a make-phone-haptics-overlay.py directory')
    if args.keyboard and (args.keyboard.name != 'KBLayouts_iPhone.dat' or not args.keyboard.is_file()):
        sys.exit('--keyboard takes a KBLayouts_iPhone.dat file')
    diag = diag_daemon_files(args.diag_daemon) if args.diag_daemon else None
    wifi = None
    if args.wifi_firmware:
        if not args.wifi_firmware.is_dir():
            sys.exit('--wifi-firmware takes a directory')
        wifi = sorted(p for p in args.wifi_firmware.rglob('*') if not p.is_dir())
        if any(p.is_symlink() or not p.is_file() for p in wifi) or not wifi:
            sys.exit('--wifi-firmware: regular files only, at least one')
        if sum(p.stat().st_size for p in wifi) > WIFI_FIRMWARE_MAX:
            sys.exit('--wifi-firmware: more than 64 MiB')
    if args.sensor_pack:
        if not args.sensor_pack.is_file() or args.sensor_pack.read_bytes()[:8] != b'SSCPACK1':
            sys.exit('--sensor-pack takes a pack of make-sensor-pack.py')
        if args.sensor_pack.stat().st_size > SENSOR_PACK_MAX:
            sys.exit('--sensor-pack: more than 16 MiB')
    target = args.gib * GIB
    if not 0 < target <= VOLATILE_MAX:
        sys.exit(f'{args.gib} GiB: a volatile Data RAM disk holds at most 2 GiB (NativeRamBlock); larger needs the persistent store')
    blob = args.base.read_bytes()
    logical, extents = packed_ramdisk.regions(blob)
    if target < logical:
        sys.exit(f'The base seed already holds {logical / GIB:.2f} GiB')
    report = dict(base=str(args.base), base_bytes=logical, base_sha256=packed_ramdisk.inspect(args.base)[0]['sha256'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='phone-seed-', dir=str(args.output.parent)) as scratch:
        raw = Path(scratch) / 'seed.raw'
        with raw.open('wb') as handle:
            handle.truncate(logical)
            for offset, data in extents:
                handle.seek(offset)
                handle.write(data)
        report['container_bytes'] = grow(raw, target) if target > logical else logical
        mount = Path(scratch) / 'mount'
        mount.mkdir()
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-owners', 'off',
             '-mountpoint', str(mount), '-plist', str(raw)]))
        device = attached['system-entities'][0]['dev-entry']
        try:
            if not (mount / 'mobile').is_dir():
                sys.exit('The Data seed has no /mobile; wrong base image?')
            report['disabled_jobs'] = edit_policy(mount, disable) if disable else None
            if args.wallpaper:
                destination = mount / WALLPAPER
                if destination.exists():
                    shutil.rmtree(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_tree_with_links(args.wallpaper, destination)
                report['wallpaper'] = '/private/var/' + WALLPAPER
            if args.keyboard:
                destination = mount / KEYBOARD
                if destination.exists():
                    shutil.rmtree(destination)
                destination.mkdir(parents=True)
                shutil.copyfile(args.keyboard, destination / args.keyboard.name)
                (destination / args.keyboard.name).chmod(0o644)
                report['keyboard'] = '/private/var/' + KEYBOARD
            if args.controlcenter:
                destination = mount / CONTROLCENTER
                if destination.exists():
                    shutil.rmtree(destination)
                destination.mkdir(parents=True)
                shutil.copyfile(args.controlcenter / CONTROLCENTER_FILE, destination / CONTROLCENTER_FILE)
                (destination / CONTROLCENTER_FILE).chmod(0o644)
                if (destination / CONTROLCENTER_FILE).read_bytes() != (args.controlcenter / CONTROLCENTER_FILE).read_bytes():
                    sys.exit('--controlcenter: /private/var/%s/%s reads back different' % (CONTROLCENTER, CONTROLCENTER_FILE))
                report['controlcenter'] = '/private/var/' + CONTROLCENTER
            if args.haptics_overlay:
                report['haptics_overlay'] = stage_haptics_overlay(mount, args.haptics_overlay)
            if args.keyboard_haptics:
                prefs = subprocess.run([sys.executable, '-B', str(KEYBOARD_PREFS), str(mount / 'mobile/Library/Preferences'), *KEYBOARD_HAPTICS],
                                       capture_output=True, text=True)
                if prefs.returncode:
                    sys.exit('keyboard-prefs.py failed: ' + prefs.stderr.strip())
                report['keyboard_haptics'] = json.loads(prefs.stdout)
            if args.keyboard_prefs:
                fixes = KEYBOARD_FIXES + (() if args.keep_inline_completions else (KEYBOARD_INLINE_OFF,))
                prefs = subprocess.run([sys.executable, '-B', str(KEYBOARD_PREFS), str(mount / 'mobile/Library/Preferences'), *fixes],
                                       capture_output=True, text=True)
                if prefs.returncode:
                    sys.exit('keyboard-prefs.py failed: ' + prefs.stderr.strip())
                report['keyboard_prefs'] = json.loads(prefs.stdout)
            if args.migrate:
                sentinel = mount / SENTINEL
                if not sentinel.parent.is_dir():
                    sys.exit('The seed has no /private/var/datamigrator')
                sentinel.write_bytes(b'')
                sentinel.chmod(0o644)
                try:   # mobile like its directory; on a mount without ownership a non-root user may not be allowed (checked below)
                    os.chown(sentinel, MOBILE, MOBILE)
                except PermissionError:
                    pass
                report['migration_sentinel'] = '/private/var/' + SENTINEL
            if args.force_install_apps:
                if not args.migrate:
                    sys.exit('--force-install-apps needs --migrate')
                force = mount / FORCE_INSTALL
                if not force.parent.is_dir():
                    sys.exit('The seed has no /private/var/preferences')
                force.write_bytes(plistlib.dumps(list(FORCE_INSTALL_APPS), fmt=plistlib.FMT_XML))
                force.chmod(0o644)
                prefs = mount / INSTALLATION_PREFS
                data = plistlib.loads(prefs.read_bytes()) if prefs.exists() else {}
                data['SkipDeviceFamilyCheck'] = True
                prefs.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
                prefs.chmod(0o600)
                report['force_install'] = dict(file='/private/var/' + FORCE_INSTALL, apps=len(FORCE_INSTALL_APPS),
                                               installation_prefs='/private/var/' + INSTALLATION_PREFS)
            if diag:
                report['diag_daemon'] = stage_diag_daemon(mount, *diag)
            if wifi:
                destination = mount / WIFI_FIRMWARE
                if destination.exists():
                    shutil.rmtree(destination)
                destination.mkdir(parents=True)
                files = {}
                for source in wifi:
                    relative = source.relative_to(args.wifi_firmware)
                    target_file = destination / relative
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target_file)
                    secret = source.name == 'join.txt'      # a network password in the clear: the kernel reads it, nothing else needs to
                    target_file.chmod(0o600 if secret else 0o644)
                    data = target_file.read_bytes()
                    if data != source.read_bytes():
                        sys.exit(f'--wifi-firmware: {relative} reads back different')
                    # (the digest of a short text can be searched: join.txt gets none in the report)
                    files[str(relative)] = dict(bytes=len(data), sha256=None if secret else hashlib.sha256(data).hexdigest())
                report['wifi_firmware'] = dict(directory='/private/var/' + WIFI_FIRMWARE, files=files)
            if args.sensor_pack:
                destination = mount / SENSOR_PACK
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.sensor_pack, destination)
                destination.chmod(0o644)
                data = destination.read_bytes()
                if data != args.sensor_pack.read_bytes():
                    sys.exit('--sensor-pack reads back different')
                report['sensor_pack'] = dict(file='/private/var/' + SENSOR_PACK, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            usage = subprocess.run(['df', '-k', str(mount)], capture_output=True, text=True).stdout.splitlines()[-1].split()
            report['used_kib'], report['available_kib'] = int(usage[2]), int(usage[3])
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)
        if raw.open('rb').read(4096)[32:36] != b'NXSB':
            sys.exit('The edited image is no APFS container any more')
        if args.migrate:
            report['migration_check'] = check_migration(raw)
        report['output'] = packed_ramdisk.pack(raw, args.output)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
