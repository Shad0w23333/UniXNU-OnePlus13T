#!/usr/bin/env python3
"""Set the phone keyboard preferences in a copy of an existing phone Data seed, or show the ones a seed carries.

  apply-keyboard-prefs.py [--keep-inline] [--pref DOMAIN:KEY=VALUE ...] [--trust BUNDLE] [--scratch DIR] SEED.dbrdisk OUTPUT.dbrdisk
  apply-keyboard-prefs.py --check [--scratch DIR] SEED.dbrdisk

The seeds of 2026-10-31 are layered from seeds whose bases are gone (make-a830-layout.sh A830_PREBUILT_SEED, then the Metal plug-in,
wallpaper, audio and haptics steps), so make-phone-seed.py --keyboard-prefs cannot be rerun on them; this edits mobile's preference
plists in a copy of SEED (a DBRDISK1 packed APFS Data container) through 工具/phone-keyboard-trace/keyboard-prefs.py (keys merged in
place, owner/mode/format kept) and changes nothing else: same volume UUID, plug-ins, apps, overlays, sentinels. Preferences are
data, not code: the seed's owner trust bundle stays valid (--trust copies it next to OUTPUT as OUTPUT-owner-trust.bundle).

Default preferences (sysfix/keyboard/NOTES.md):
  com.apple.keyboard:KeyboardDisableHitTesting=true                    (already in the 2026-10-31 seeds: --keyboard-prefs)
  com.apple.keyboard.ContinuousPath:KeyboardContinuousPathEnabled=false   (likewise)
  com.apple.keyboard.preferences:KeyboardInlineCompletion=false       (new: Settings > General > Keyboard > "Show Predictions
      Inline" off. kbd runs the inline completion beam search, up to 32 forward passes of the English transformer language
      model, inside every autocorrection request, and UIKit's keyboard task queue waits for that request; on the iPhone the
      model runs on the Neural Engine, here on the CPU (unilm_joint_cpu, int8). Predictions and autocorrection stay; the user
      can turn it back on in Settings. --keep-inline leaves it alone.)
--pref adds more DOMAIN:KEY=VALUE (keyboard-prefs.py's domains: com.apple.keyboard, com.apple.keyboard.preferences,
com.apple.keyboard.ContinuousPath, .GlobalPreferences).

--check attaches SEED read-only and prints the keyboard-related preferences found in mobile's Library/Preferences.
Writes OUTPUT and OUTPUT with .json (what was done). Runs on macOS (hdiutil). Never touches the phone."""
import argparse
import hashlib
import importlib.util
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parents[3]                                           # /Users/xiaochoumao/unixnu-13t
sys.path.insert(0, str(TOP / 'UniXNU/drivers/DarwinBoot/Test'))
import packed_ramdisk  # noqa: E402

KEYBOARD_PREFS = TOP / '工具/phone-keyboard-trace/keyboard-prefs.py'
PREFS_DIR = 'mobile/Library/Preferences'
BASE_FIXES = ('com.apple.keyboard:KeyboardDisableHitTesting=true',
              'com.apple.keyboard.ContinuousPath:KeyboardContinuousPathEnabled=false')
INLINE_OFF = 'com.apple.keyboard.preferences:KeyboardInlineCompletion=false'
SHOWN = {
    'com.apple.keyboard': None, 'com.apple.keyboard.preferences': None, 'com.apple.keyboard.ContinuousPath': None,
    'com.apple.preferences.sounds': ('keyboard-audio', 'keyboard'),
    '.GlobalPreferences': ('AppleKeyboards', 'AppleLanguages', 'AppleLocale'),
}


def unpack(seed, raw):
    logical, extents = packed_ramdisk.regions(seed.read_bytes())
    with raw.open('wb') as handle:
        handle.truncate(logical)
        for offset, data in extents:
            handle.seek(offset)
            handle.write(data)
    return logical


def attach(raw, mount, readonly):
    command = ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-owners', 'on' if readonly else 'off',
               '-mountpoint', str(mount), '-plist', str(raw)]
    if readonly:
        command.insert(4, '-readonly')
    attached = plistlib.loads(subprocess.check_output(command))
    device = attached['system-entities'][0]['dev-entry']
    if not (Path(mount) / 'mobile').is_dir():
        detach(device)
        sys.exit(f'The seed did not mount at {mount} as a Data volume')
    return device


def detach(device):
    subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)


def read_prefs(prefs_dir):
    found = {}
    for domain, keys in SHOWN.items():
        path = prefs_dir / f'{domain}.plist'
        if not path.is_file():
            continue
        try:
            data = plistlib.loads(path.read_bytes())
        except Exception as error:          # a damaged plist is reported, not fatal
            found[domain] = f'unreadable: {error}'
            continue
        if not isinstance(data, dict):
            found[domain] = 'not a dictionary'
            continue
        found[domain] = {k: v for k, v in data.items() if keys is None or k in keys}
    return found


def jsonable(value):
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def check_seed(seed, scratch_parent):
    with tempfile.TemporaryDirectory(prefix='keyboard-seed-check-', dir=str(scratch_parent)) as scratch:
        raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
        unpack(seed, raw)
        mount.mkdir()
        device = attach(raw, mount, readonly=True)
        try:
            found = read_prefs(mount / PREFS_DIR)
        finally:
            detach(device)
    print(json.dumps({'seed': str(seed), 'preferences': jsonable(found)}, indent=2, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--keep-inline', action='store_true', help='do not turn off inline completions')
    parser.add_argument('--pref', action='append', default=[])
    parser.add_argument('--trust', type=Path)
    parser.add_argument('--scratch', type=Path, help='where the 2 GiB sparse raw copy goes (default beside OUTPUT, or SEED with --check)')
    parser.add_argument('seed', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    if not args.seed.is_file():
        sys.exit(f'No seed {args.seed}')
    if args.check:
        sys.exit(check_seed(args.seed, args.scratch or args.seed.parent))
    if not args.output:
        sys.exit('OUTPUT.dbrdisk is needed (or --check)')
    if args.output.exists():
        sys.exit('Refuse to overwrite ' + str(args.output))
    if args.trust and not args.trust.is_file():
        sys.exit(f'No trust bundle {args.trust}')
    if not KEYBOARD_PREFS.is_file():
        sys.exit(f'No {KEYBOARD_PREFS}')
    specs = list(BASE_FIXES) + ([] if args.keep_inline else [INLINE_OFF]) + list(args.pref)
    # keyboard-prefs.py's own parser validates every spec before the seed is unpacked
    spec = importlib.util.spec_from_file_location('keyboard_prefs', KEYBOARD_PREFS)
    prefs_tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prefs_tool)
    for one in specs:
        try:
            prefs_tool.parse(one)
        except ValueError as error:
            sys.exit(f'keyboard-prefs.py rejects {one!r}: {error}')
    report = dict(seed=str(args.seed), seed_sha256=packed_ramdisk.inspect(args.seed)[0]['sha256'], preferences=specs)
    lineage = args.seed.with_suffix('.json')
    if lineage.is_file():
        report['seed_report'] = json.loads(lineage.read_text())
        for key in ('plugin_cdhash', 'plugin'):           # what make-a830-layout.sh and the plug-in tools read
            if key in report['seed_report']:
                report[key] = report['seed_report'][key]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='keyboard-seed-', dir=str(args.scratch or args.output.parent)) as scratch:
        raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
        report['container_bytes'] = unpack(args.seed, raw)
        mount.mkdir()
        device = attach(raw, mount, readonly=False)
        try:
            prefs_dir = mount / PREFS_DIR
            if not prefs_dir.is_dir():
                sys.exit(f'The seed has no /private/var/{PREFS_DIR}')
            report['before'] = jsonable(read_prefs(prefs_dir))
            done = subprocess.run([sys.executable, '-I', '-B', str(KEYBOARD_PREFS), str(prefs_dir), *specs], capture_output=True,
                                  text=True)
            if done.returncode:
                sys.exit('keyboard-prefs.py failed: ' + done.stderr.strip())
            report['changed'] = json.loads(done.stdout)
            report['after'] = jsonable(read_prefs(prefs_dir))
            for spec in specs:
                domain, rest = spec.split(':', 1)
                key, raw_value = rest.split('=', 1)
                want = True if raw_value == 'true' else False if raw_value == 'false' else int(raw_value)
                got = report['after'].get(domain, {})
                if not isinstance(got, dict) or got.get(key) != want:
                    sys.exit(f'{spec} does not read back from the seed copy')
        finally:
            detach(device)
        if raw.open('rb').read(4096)[32:36] != b'NXSB':
            sys.exit('The edited image is no APFS container any more')
        report['output'] = packed_ramdisk.pack(raw, args.output)
    if args.trust:
        trust_copy = args.output.with_name(args.output.stem + '-owner-trust.bundle')
        shutil.copyfile(args.trust, trust_copy)
        report['owner_trust'] = dict(source=str(args.trust), copy=str(trust_copy),
                                     sha256=hashlib.sha256(trust_copy.read_bytes()).hexdigest(), changed=False)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'seed_report'}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
