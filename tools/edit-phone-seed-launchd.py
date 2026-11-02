#!/usr/bin/env python3
"""Change the launch policy and a few preferences in a copy of an existing phone Data seed, or list them.

  edit-phone-seed-launchd.py [--enable LABEL[,LABEL]] [--disable LABEL[,LABEL]] [--auto-lock [SECONDS]] [--keep-idle-sleep-block]
                             [--no-auto-updates] [--keep-mobile-asset] [--sos-off] [--language zh-Hans] [--text-input] [--force]
                             [--kernel MACHO] [--trust BUNDLE] [--no-budget] [--scratch DIR] SEED.dbrdisk OUTPUT.dbrdisk
  edit-phone-seed-launchd.py --check [--json] [--scratch DIR] SEED.dbrdisk

The seeds of 2026-10-31 were made from base seeds that are gone (make-a830-layout.sh A830_PREBUILT_SEED), so make-phone-seed.py cannot
rerun them. This expands a copy of SEED (a DBRDISK1 packed APFS Data container), attaches it with hdiutil and changes
/private/var/db/com.apple.xpc.launchd/disabled.plist and plists under /private/var/mobile/Library/Preferences, nothing else: same volume
UUID, plug-ins, apps, sentinels. Everything is data, so the seed's owner trust bundle stays valid unchanged (--trust copies it next to
OUTPUT as OUTPUT-owner-trust.bundle, for A830_OWNER_TRUST). A digest of the whole volume taken before and after must differ in exactly the
files named here, and OUTPUT is attached read-only again and read back before the tool reports success.

--enable L   removes L from the policy (launchd loads it again). L must be disabled there, so a typo does not pass unnoticed.
             com.apple.watchdogd is refused without --force: this kernel has no IOWatchdog service, watchdogd then crashes on purpose
             (/usr/libexec/watchdogd 0x100006768 -> 0x10000ab10 brk) and its job asks for a kernel panic on consecutive crashes
             (_PanicOnCrash, seen on the phone in run 33, STAGE6.md).
--disable L  sets L to true. com.apple.DumpPanic is refused without --force: the kernel starts PaganiDiag in its place.
             A label that no /System/Library/LaunchDaemons plist of the OS volume names is refused without --force (a typo).
--auto-lock [S]  com.apple.springboard: removes SBDisableAutoDim, SBDontDimOrLockOnAC and SBDisableIdleSleep and sets SBAutoLockTime = S
             seconds (default 30; 30..3600). SBAutoLockTime is the legacy key that -[MCMigrator _migrateSettings] (profiled) moves to
             ManagedConfiguration's maxInactivity (MCFeatureAutoLockTime, what SpringBoard's idle timer reads) during the boot's data
             migration, so it needs the seed's migration sentinel (make-phone-seed.py --migrate); without one the key is ignored and
             SpringBoard falls back to 60 s on the iPhone class. System sleep stays refused by the kernel: ADT sleep-disabled = 1
             (IOPMrootDomain::checkSystemSleepAllowed).
--keep-idle-sleep-block  with --auto-lock: keep SBDisableIdleSleep (SpringBoard's idle sleep assertion), should powerd not cope with the
             kernel's refusal once the screen locks by itself.
--no-auto-updates  com.apple.softwareupdateservicesd (mobile, any host): SUDisableAutoDownload = true, SUAutomaticUpdateV2Enabled,
             SUAutoInstallSecurityResponse and SUAutoInstallSystemDataFiles = false. com.apple.MobileAsset (mobile): MobileAssetServerURL
             and PallasUrlOverrideV2 = BLACKHOLE, an address that refuses connections. mobileassetd has no switch for automatic downloads
             on a customer build (every AutoAsset preference sits behind _MAPreferencesIsInternalAllowed); these two overrides are the
             preferences it does honour there, and they stop on-demand downloads too (Siri voices, dictionaries, language packs).
--keep-mobile-asset  with --no-auto-updates: leave com.apple.MobileAsset alone (software update keys only).
--sos-off    com.apple.SOS (mobile, any host): SOSDeviceSupportedKey = false, which overrides MobileGestalt in +[SOSManager deviceSupportsSOS],
             so -[SBCombinationHardwareButton _configureSOSGestureBehaviors] leaves both the side + volume hold and the 5 presses off.
             ByHost/com.apple.SOS..plist (the keys of +[SOSUtilities _sosPrefsValueForKey:], CFPreferences user mobile, current host,
             whose ByHost identifier is the empty string on iOS): the calls with hold and with presses, the automatic countdown and its
             sound off, in case a client reaches the SOS flow some other way.
--language zh-Hans  .GlobalPreferences: AppleLanguages = zh-Hans-CN, en and AppleLocale = zh_CN. InternationalSupportMigrator keeps them.
--text-input  what stops typing from slowing down exponentially without a network (a830-inputs/round-1101/keyboard.md, keyboard-verify.md):
             com.apple.assistant.support (mobile): Offline Dictation Status = {}. Without a dictionary there, every call of
             -[AFDictationConnection forcedOfflineDictationIsAvailableForLanguage:synchronous:] (0x19fae6178) posts one more availability
             change, each of which calls it again M times from UIKit's observers. assistantd only writes the key on a device with offline
             dictation (AFOfflineDictationCapable, ADT /product offline-dictation, which this ADT lacks). .GlobalPreferences: the same key
             for a sandboxed reader of that domain (CFPreferencesCopyAppValue falls back to the global domain), with AppleKeyboards =
             KEYBOARDS and AppleKeyboardsExpanded = 1 (what -[TIPreferencesController updateInputModes:] writes; read with boolValue), the
             fast path of -enabledInputModeIdentifiers:, whose default path has no cache. com.apple.keyboard.preferences:
             DictationLanguagesEnabled = DICTATION_OFF, or -isDictationLanguageEnabled: recomputes the dictionary at each call and sends
             the result to kbd. The language of the system stays as it is. KeyboardMigrator, which runs at every boot with a migration
             sentinel, writes AppleKeyboards back unchanged (none of its renames match these identifiers) and adds only
             ApplePasscodeKeyboards = AppleKeyboards and AddingEmojiKeybordHandled to .GlobalPreferences. Its two writes of
             AppleKeyboardsExpanded = true are not reached on these seeds: one needs no AppleKeyboards at all and the language ja, the other
             a system update from before version 13 (LastSystemVersion is 21H461) with two preferred languages; even there boolValue reads
             the same.

--check attaches SEED read-only and lists the policy, the preferences above and the volume UUID; it changes nothing and exits 0.

Budget: as replace-phone-wallpaper.py, the new seed with --kernel (default the GPU line's kernel.macho), the trust bundle and the small
payload files must fit 95 % of board.toml [payload] size_mib; --no-budget skips it. Writes OUTPUT and OUTPUT with .json (what was done).
Runs on macOS (hdiutil, diskutil). Never touches the phone."""
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parent
sys.path.insert(0, str(TOP / 'UniXNU/drivers/DarwinBoot/Test'))
import packed_ramdisk  # noqa: E402

_spec = importlib.util.spec_from_file_location('replace_phone_wallpaper', HERE / 'replace-phone-wallpaper.py')
wallpaper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wallpaper)     # unpack, attach, detach, payload_budget, DEFAULT_KERNEL

POLICY = 'db/com.apple.xpc.launchd/disabled.plist'
POLICY_MARKER = 'db/com.apple.xpc.launchd/disabled.migrated'
PREFS = 'mobile/Library/Preferences'
SENTINEL = 'datamigrator/migrate'
MOBILE = 501
OS_ROOT = Path(os.environ.get('SEED_OS_ROOT', TOP / 'build-userspace/a830-inputs/osmnt-21H461'))
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
# Names macOS itself may write on an attached volume.
SKIPPED = {'.fseventsd', '.Spotlight-V100', '.TemporaryItems', '.Trashes', '.DS_Store', '.DocumentRevisions-V100'}

RISKY_ENABLE = {
    'com.apple.watchdogd': 'no IOWatchdog service in this kernel: watchdogd crashes and _PanicOnCrash panics the phone (run 33, STAGE6.md)',
}
RISKY_DISABLE = {
    'com.apple.DumpPanic': 'the kernel starts PaganiDiag in place of this job',
}

AUTO_LOCK_REMOVE = ('SBDisableAutoDim', 'SBDontDimOrLockOnAC', 'SBDisableIdleSleep')
AUTO_LOCK_RANGE = (30, 3600)    # defaultSettings.plist maxInactivity rangeMinimum; SpringBoard treats more than 3600 s as never
AUTO_LOCK_DEFAULT = 30
BLACKHOLE = 'https://127.0.0.1:9/'
NO_SOFTWARE_UPDATE = dict(SUDisableAutoDownload=True, SUAutomaticUpdateV2Enabled=False, SUAutoInstallSecurityResponse=False,
                          SUAutoInstallSystemDataFiles=False)
NO_MOBILE_ASSET = dict(MobileAssetServerURL=BLACKHOLE, PallasUrlOverrideV2=BLACKHOLE)
SOS_DOMAIN = dict(SOSDeviceSupportedKey=False)
SOS_BYHOST = dict(SOSCallWithVolumeLockHoldKey=False, SOSCallWithSideButtonPressesKey=False, SOSAutomaticCallCountdownEnabledKey=False,
                  SOSPlayAudioDuringCountdownKey=False)
SOS_BYHOST_FILE = 'ByHost/com.apple.SOS..plist'     # "%@.%@.plist" of the domain and _CFXPreferencesGetByHostIdentifierString() = ""
LANGUAGES = {'zh-Hans': (['zh-Hans-CN', 'en'], 'zh_CN')}
OFFLINE_DICTATION = {'Offline Dictation Status': {}}    # an empty dictionary reads back as non-nil and says no language is installed
# In the form UIKeyboardInputModeGetIdentifierWithKeyboardLayouts returns as it is (sw among the mode's SWLayouts, hw given or, for emoji, none
# to give), so that -enabledInputModeIdentifiers: finds them equal to enabledInputModes and stops there.
KEYBOARDS = ['en_US@sw=QWERTY;hw=Automatic', 'zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', 'emoji@sw=Emoji']
# Every language -[UIKeyboardInputMode dictationLanguage] asks about for KEYBOARDS: UIDefaultDictationLanguages of the keyboard's language
# (zh_Hans: zh_CN, yue_CN, wuu_CN), or that language itself without an entry (en_US, emoji: keyboard-verify.md, section 3).
DICTATION_OFF = dict(en_US=False, zh_CN=False, yue_CN=False, wuu_CN=False, emoji=False)

# What --check shows: file under PREFS, the keys to show (None: all).
VIEWS = (('com.apple.springboard.plist', None),
         ('.GlobalPreferences.plist', ('AppleLanguages', 'AppleLocale', 'AppleKeyboards', 'AppleKeyboardsExpanded', *OFFLINE_DICTATION)),
         ('com.apple.softwareupdateservicesd.plist', None), ('com.apple.MobileAsset.plist', None), ('com.apple.SOS.plist', None),
         (SOS_BYHOST_FILE, None), ('com.apple.assistant.support.plist', None),
         ('com.apple.keyboard.preferences.plist', ('DictationLanguagesEnabled',)))

Edit = namedtuple('Edit', 'file set remove')


class SeedError(Exception):
    """A refused request or a failed check; main() exits with its text."""


def labels(values):
    """The labels of repeated, comma separated options."""
    out = []
    for value in values or ():
        for label in value.split(','):
            if not LABEL.fullmatch(label):
                raise SeedError(f'{label!r} is no launchd label')
            if label not in out:
                out.append(label)
    return out


def known_labels(os_root):
    """Labels of the OS volume's LaunchDaemons, or None without the volume."""
    daemons = Path(os_root) / 'System/Library/LaunchDaemons'
    if not daemons.is_dir():
        return None
    found = set()
    for path in daemons.glob('*.plist'):
        try:
            label = plistlib.loads(path.read_bytes()).get('Label')
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        if label:
            found.add(label)
    return found


def edit_policy(data, enable, disable, known=None, force=False):
    """The policy (bytes) with each label of enable removed and each of disable true: (new bytes, what changed)."""
    both = set(enable) & set(disable)
    if both:
        raise SeedError('A job cannot be enabled and disabled at once: ' + ','.join(sorted(both)))
    jobs = plistlib.loads(data)
    if not isinstance(jobs, dict):
        raise SeedError(POLICY + ' is not a dictionary')
    before = sorted(label for label, value in jobs.items() if value is True)
    for label in enable:
        if jobs.get(label) is not True:
            raise SeedError(f'--enable: {label} is not disabled in the seed\'s {POLICY}')
        if label in RISKY_ENABLE and not force:
            raise SeedError(f'--enable: {label}: {RISKY_ENABLE[label]} (--force overrides)')
        del jobs[label]
    for label in disable:
        if label in RISKY_DISABLE and not force:
            raise SeedError(f'--disable: {label}: {RISKY_DISABLE[label]} (--force overrides)')
        if known is not None and label not in known and label not in jobs and not force:
            raise SeedError(f'--disable: no LaunchDaemons plist of the OS volume has the label {label} (--force overrides)')
        jobs[label] = True
    fmt = plistlib.FMT_BINARY if data.startswith(b'bplist') else plistlib.FMT_XML
    after = sorted(label for label, value in jobs.items() if value is True)
    return plistlib.dumps(jobs, fmt=fmt), dict(enabled=list(enable), disabled=list(disable), before=before, after=after)


def recipe(auto_lock=None, no_auto_updates=False, keep_mobile_asset=False, sos_off=False, language=None, keep_idle_sleep_block=False,
           text_input=False):
    """The preference edits the options ask for."""
    edits = []
    if auto_lock is not None:
        if not AUTO_LOCK_RANGE[0] <= auto_lock <= AUTO_LOCK_RANGE[1]:
            raise SeedError(f'--auto-lock takes {AUTO_LOCK_RANGE[0]} to {AUTO_LOCK_RANGE[1]} seconds')
        remove = tuple(key for key in AUTO_LOCK_REMOVE if not (keep_idle_sleep_block and key == 'SBDisableIdleSleep'))
        edits.append(Edit('com.apple.springboard.plist', dict(SBAutoLockTime=auto_lock), remove))
    if no_auto_updates:
        edits.append(Edit('com.apple.softwareupdateservicesd.plist', dict(NO_SOFTWARE_UPDATE), ()))
        if not keep_mobile_asset:
            edits.append(Edit('com.apple.MobileAsset.plist', dict(NO_MOBILE_ASSET), ()))
    if sos_off:
        edits.append(Edit('com.apple.SOS.plist', dict(SOS_DOMAIN), ()))
        edits.append(Edit(SOS_BYHOST_FILE, dict(SOS_BYHOST), ()))
    if language is not None:
        if language not in LANGUAGES:
            raise SeedError('--language takes ' + ', '.join(sorted(LANGUAGES)))
        languages, locale = LANGUAGES[language]
        edits.append(Edit('.GlobalPreferences.plist', dict(AppleLanguages=list(languages), AppleLocale=locale), ()))
    if text_input:
        edits.append(Edit('com.apple.assistant.support.plist', dict(OFFLINE_DICTATION), ()))
        edits.append(Edit('.GlobalPreferences.plist', dict(OFFLINE_DICTATION, AppleKeyboards=list(KEYBOARDS), AppleKeyboardsExpanded=1), ()))
        edits.append(Edit('com.apple.keyboard.preferences.plist', dict(DictationLanguagesEnabled=dict(DICTATION_OFF)), ()))
    return edits


def apply_edit(prefs, edit):
    """Applies EDIT below the directory PREFS. An existing plist keeps its other keys, format, inode, owner and mode; a new one is binary,
    0600, in the directory's owner (like the other user preferences), with a new parent 0755. Returns what it did."""
    prefs = Path(prefs)
    if not prefs.is_dir():
        raise SeedError(f'{prefs} is no directory')
    path = prefs / edit.file
    existing = path.is_file()
    fmt, data = plistlib.FMT_BINARY, {}
    if existing:
        raw = path.read_bytes()
        fmt = plistlib.FMT_BINARY if raw.startswith(b'bplist') else plistlib.FMT_XML
        data = plistlib.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise SeedError(f'{path} does not hold a dictionary')
    before = {key: data.get(key) for key in (*edit.set, *edit.remove)}
    for key in edit.remove:
        data.pop(key, None)
    data.update(edit.set)
    encoded = plistlib.dumps(data, fmt=fmt, sort_keys=True)
    created = []
    if existing:
        with path.open('r+b') as handle:        # in place: same inode, so the owner and the mode stay
            handle.write(encoded)
            handle.truncate()
    else:
        owner = prefs.stat()
        if not path.parent.is_dir():
            path.parent.mkdir()
            path.parent.chmod(0o755)
            created.append(str(path.parent.relative_to(prefs)))
        path.write_bytes(encoded)
        path.chmod(0o600)
        for item in (path, *([path.parent] if created else [])):
            try:
                os.chown(item, owner.st_uid, owner.st_gid)
            except PermissionError:
                pass                            # a mount without ownership; checked on the read-only attach of OUTPUT
        created.append(edit.file)
    after = {key: data.get(key) for key in (*edit.set, *edit.remove)}
    return dict(file=edit.file, created=created, format='binary' if fmt == plistlib.FMT_BINARY else 'xml', before=before, after=after,
                bytes=len(encoded))


def digest(mount):
    """relative path -> (kind, mode, size, content or link digest) of the whole volume, without what macOS itself writes on it."""
    out = {}
    root = str(mount)
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in SKIPPED]
        for name in [*subdirectories, *files]:
            if name in SKIPPED:
                continue
            path = os.path.join(directory, name)
            info = os.lstat(path)
            relative = os.path.relpath(path, root)
            if stat.S_ISLNK(info.st_mode):
                out[relative] = ('link', 0, 0, os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                out[relative] = ('dir', stat.S_IMODE(info.st_mode), 0, '')
            elif stat.S_ISREG(info.st_mode):
                sha = hashlib.sha256()
                with open(path, 'rb') as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b''):
                        sha.update(chunk)
                out[relative] = ('file', stat.S_IMODE(info.st_mode), info.st_size, sha.hexdigest())
            else:
                out[relative] = ('other', stat.S_IMODE(info.st_mode), 0, '')
    return out


def changed_paths(before, after):
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def volume_uuid(mount):
    return plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(mount)])).get('VolumeUUID')


def read_plist(path):
    """A plist's content, None without the file."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    return plistlib.loads(raw) if raw else {}


def survey(mount):
    """What --check lists, from a mounted seed."""
    policy = read_plist(mount / POLICY)
    info = (mount / POLICY).lstat() if policy is not None else None
    state = dict(volume_uuid=volume_uuid(mount),
                 policy=None if policy is None else dict(
                     disabled=sorted(label for label, value in policy.items() if value is True),
                     enabled_explicitly=sorted(label for label, value in policy.items() if value is False),
                     owner=f'{info.st_uid}:{info.st_gid}', mode=f'{stat.S_IMODE(info.st_mode):o}',
                     marker=(mount / POLICY_MARKER).is_file()),
                 migration_sentinel=(mount / SENTINEL).is_file(), preferences={})
    for name, keys in VIEWS:
        data = read_plist(mount / PREFS / name)
        if data is not None and keys:
            data = {key: data[key] for key in keys if key in data}
        state['preferences'][name] = data
    usage = subprocess.run(['df', '-k', str(mount)], capture_output=True, text=True).stdout.splitlines()[-1].split()
    state['used_kib'], state['available_kib'] = int(usage[2]), int(usage[3])
    return state


def show_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return repr(value)


def print_survey(seed, state):
    print(f'{seed}: volume {state["volume_uuid"]}, {state["used_kib"]} KiB used, {state["available_kib"]} KiB free')
    policy = state['policy']
    if policy is None:
        print(f'  no {POLICY}')
    else:
        print(f'  launch policy ({policy["owner"]} {policy["mode"]}, disabled.migrated {"yes" if policy["marker"] else "NO"}): '
              f'{len(policy["disabled"])} disabled')
        for label in policy['disabled']:
            print('    ' + label)
        for label in policy['enabled_explicitly']:
            print(f'    {label} (explicitly enabled)')
    print(f'  migration sentinel /private/var/{SENTINEL}: {"yes" if state["migration_sentinel"] else "no"}')
    for name, data in state['preferences'].items():
        if data is None:
            print(f'  {name}: no file')
            continue
        print(f'  {name}:' + ('' if data else ' empty'))
        for key in sorted(data):
            print(f'    {key} = {show_value(data[key])}')


def check_seed(seed, as_json, scratch_parent):
    with tempfile.TemporaryDirectory(prefix='seed-launchd-check-', dir=str(scratch_parent)) as scratch:
        raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
        wallpaper.unpack(seed, raw)
        mount.mkdir()
        device = wallpaper.attach(raw, mount, readonly=True)
        try:
            state = survey(mount)
        finally:
            wallpaper.detach(device)
    if as_json:
        print(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print_survey(seed, state)
    return 0


def verify_output(output, scratch_parent, policy, edits, uuid):
    """Attaches OUTPUT read-only, with its owners, and reads back what must be there. Returns the survey."""
    with tempfile.TemporaryDirectory(prefix='seed-launchd-verify-', dir=str(scratch_parent)) as scratch:
        raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
        wallpaper.unpack(output, raw)
        mount.mkdir()
        device = wallpaper.attach(raw, mount, readonly=True)
        try:
            state = survey(mount)
            if state['volume_uuid'] != uuid:
                raise SeedError(f'OUTPUT has the volume UUID {state["volume_uuid"]}, SEED {uuid}')
            if state['policy'] is not None and state['policy']['disabled'] != policy:
                raise SeedError(f'The policy reads back as {state["policy"]["disabled"]}, expected {policy}')
            for edit in edits:
                path = mount / PREFS / edit.file
                data = read_plist(path)
                if data is None or any(data.get(key) != value for key, value in edit.set.items()) or set(edit.remove) & set(data):
                    raise SeedError(f'/private/var/{PREFS}/{edit.file} reads back different: {data}')
                owner = path.lstat().st_uid
                if owner != MOBILE:
                    raise SeedError(f'/private/var/{PREFS}/{edit.file} belongs to uid {owner}, not mobile')
        finally:
            wallpaper.detach(device)
    return state


def make_seed(args, enable, disable, edits):
    if args.output.exists():
        raise SeedError(f'Refuse to overwrite {args.output}')
    if args.trust and not args.trust.is_file():
        raise SeedError(f'No trust bundle {args.trust}')
    if not args.no_budget and not args.kernel.is_file():
        raise SeedError(f'No kernel {args.kernel} for the payload budget (--kernel, or --no-budget)')
    report = dict(seed=str(args.seed), seed_sha256=packed_ramdisk.inspect(args.seed)[0]['sha256'])
    lineage = args.seed.with_suffix('.json')
    if lineage.is_file():
        report['seed_report'] = json.loads(lineage.read_text())
        for key in ('plugin_cdhash', 'plugin'):     # what make-a830-layout.sh and the plug-in tools read from a seed's .json
            if key in report['seed_report']:
                report[key] = report['seed_report'][key]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    known = known_labels(OS_ROOT)
    scratch_parent = args.scratch or args.output.parent
    try:
        with tempfile.TemporaryDirectory(prefix='seed-launchd-', dir=str(scratch_parent)) as scratch:
            raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
            report['container_bytes'] = wallpaper.unpack(args.seed, raw)
            mount.mkdir()
            device = wallpaper.attach(raw, mount, readonly=False)
            try:
                uuid = volume_uuid(mount)
                report['volume_uuid'] = uuid
                before = digest(mount)
                policy_path = mount / POLICY
                if not policy_path.is_file():
                    raise SeedError('The seed has no ' + POLICY)
                policy_data = policy_path.read_bytes()
                edited, report['policy'] = edit_policy(policy_data, enable, disable, known, args.force)
                allowed = set()
                if enable or disable:
                    with policy_path.open('r+b') as handle:     # in place: same inode, so the owner (root) and the mode stay
                        handle.write(edited)
                        handle.truncate()
                    allowed.add(POLICY)
                report['preferences'] = []
                for edit in edits:
                    done = apply_edit(mount / PREFS, edit)
                    report['preferences'].append(done)
                    allowed.add(f'{PREFS}/{edit.file}')
                    allowed.update(f'{PREFS}/{name}' for name in done['created'])
                report['migration_sentinel'] = (mount / SENTINEL).is_file()
                if args.auto_lock is not None and not report['migration_sentinel']:
                    report['warning'] = (f'no /private/var/{SENTINEL}: nothing reads SBAutoLockTime on this seed, SpringBoard locks after its '
                                         'default 60 s')
                    print('warning: ' + report['warning'], file=sys.stderr)
                changed = changed_paths(before, digest(mount))
                extra = sorted(set(changed) - allowed)
                if extra:
                    raise SeedError('Files changed that this tool must not touch: ' + ', '.join(extra))
                report['changed'] = changed
                if volume_uuid(mount) != uuid:
                    raise SeedError('The volume UUID changed')
                usage = subprocess.run(['df', '-k', str(mount)], capture_output=True, text=True).stdout.splitlines()[-1].split()
                report['used_kib'], report['available_kib'] = int(usage[2]), int(usage[3])
            finally:
                wallpaper.detach(device)
            if raw.open('rb').read(4096)[32:36] != b'NXSB':
                raise SeedError('The edited image is no APFS container any more')
            report['output'] = packed_ramdisk.pack(raw, args.output)
        state = verify_output(args.output, scratch_parent, report['policy']['after'], edits, uuid)
        report['policy']['owner'] = state['policy']['owner']
        report['policy']['mode'] = state['policy']['mode']
        if not args.no_budget:
            report['payload'] = wallpaper.payload_budget(args.output.stat().st_size, args.kernel, args.trust)
            if report['payload']['headroom'] < 0:
                raise SeedError(f'The seed no longer fits the boot payload: {report["payload"]}')
        if args.trust:
            trust_copy = args.output.with_name(args.output.stem + '-owner-trust.bundle')
            shutil.copyfile(args.trust, trust_copy)
            report['owner_trust'] = dict(source=str(args.trust), copy=str(trust_copy),
                                         sha256=hashlib.sha256(trust_copy.read_bytes()).hexdigest(), changed=False)
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'seed_report'}, indent=2, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--json', action='store_true', help='with --check: the listing as JSON')
    parser.add_argument('--enable', action='append', metavar='LABEL')
    parser.add_argument('--disable', action='append', metavar='LABEL')
    parser.add_argument('--auto-lock', nargs='?', type=int, const=AUTO_LOCK_DEFAULT, metavar='SECONDS')
    parser.add_argument('--keep-idle-sleep-block', action='store_true')
    parser.add_argument('--no-auto-updates', action='store_true')
    parser.add_argument('--keep-mobile-asset', action='store_true')
    parser.add_argument('--sos-off', action='store_true')
    parser.add_argument('--language')
    parser.add_argument('--text-input', action='store_true')
    parser.add_argument('--force', action='store_true', help='lets through the jobs this tool refuses')
    parser.add_argument('--kernel', type=Path, default=wallpaper.DEFAULT_KERNEL)
    parser.add_argument('--trust', type=Path)
    parser.add_argument('--no-budget', action='store_true')
    parser.add_argument('--scratch', type=Path, help='where the 2 GiB sparse raw copies go (default beside OUTPUT, or SEED with --check)')
    parser.add_argument('seed', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    try:
        if not args.seed.is_file():
            raise SeedError(f'No seed {args.seed}')
        if args.check:
            return check_seed(args.seed, args.json, args.scratch or args.seed.parent)
        if not args.output:
            raise SeedError('OUTPUT.dbrdisk is needed (or --check)')
        enable, disable = labels(args.enable), labels(args.disable)
        if args.keep_idle_sleep_block and args.auto_lock is None:
            raise SeedError('--keep-idle-sleep-block goes with --auto-lock')
        if args.keep_mobile_asset and not args.no_auto_updates:
            raise SeedError('--keep-mobile-asset goes with --no-auto-updates')
        edits = recipe(args.auto_lock, args.no_auto_updates, args.keep_mobile_asset, args.sos_off, args.language, args.keep_idle_sleep_block,
                       args.text_input)
        if not enable and not disable and not edits:
            raise SeedError('Nothing to change: name a job or a preference option')
        make_seed(args, enable, disable, edits)
    except SeedError as error:
        sys.exit(str(error))
    return 0


if __name__ == '__main__':
    sys.exit(main())
