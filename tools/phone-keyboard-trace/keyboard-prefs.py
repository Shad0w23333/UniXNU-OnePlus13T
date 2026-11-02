#!/usr/bin/env python3
"""Set TextInput keyboard preferences in a mounted Data seed (the follow-up fixes of the keyboard trace).

  keyboard-prefs.py PREFS_DIR DOMAIN:KEY=VALUE ...      (PREFS_DIR = <mounted seed>/mobile/Library/Preferences)

VALUE is true, false or an integer. TIPreferencesController (TextInput, 21H461) reads each key from one CFPreferences
domain, i.e. PREFS_DIR/<domain>.plist of user mobile:
  com.apple.keyboard:KeyboardDisableHitTesting=true
      -[TIKeyboardInputManager(ZephyrEngineSpecializations) canHandleKeyHitTest] (TextInputCore 0x1d9312084) answers NO,
      so -[UIKeyboardLayoutStar performHitTestForTouchInfo:touchStage:executionContextPassingUIKBTree:] (UIKitCore
      0x18ab5eae8) takes the key under the finger itself instead of waiting for kbd's typing-model hit test.
  com.apple.keyboard.ContinuousPath:KeyboardContinuousPathEnabled=false
      the Settings switch "Slide to Type": no QuickPath on the phone keyboard (-[UIKeyboardLayoutStar
      _allowContinuousPathUI] 0x18a5350f4), so no touch can be left in the continuous-path state that makes
      -[UIKeyboardLayoutStar touchDown:executionContext:] (0x18ab6669c) drop every later touch-down.
An existing plist keeps its other keys and its format (binary or XML); a new one is written binary. Owner and mode follow an
existing file, else the directory (mobile 501, 0600 like the other user preferences); on a mount without ownership the
owner change is skipped and reported. Prints what it changed as JSON."""
import json
import os
import plistlib
import re
import stat
import sys
from pathlib import Path

DOMAINS = {'com.apple.keyboard', 'com.apple.keyboard.preferences', 'com.apple.keyboard.ContinuousPath', '.GlobalPreferences'}
SPEC = re.compile(r'^([A-Za-z0-9.]+):([A-Za-z][A-Za-z0-9]*)=(true|false|-?[0-9]+)$')


def parse(spec):
    m = SPEC.match(spec)
    if not m or m.group(1) not in DOMAINS:
        raise ValueError(f'{spec!r}: want DOMAIN:KEY=true|false|INT with DOMAIN one of {sorted(DOMAINS)}')
    raw = m.group(3)
    value = True if raw == 'true' else False if raw == 'false' else int(raw)
    return m.group(1), m.group(2), value


def apply(prefs_dir, specs):
    prefs_dir = Path(prefs_dir)
    if not prefs_dir.is_dir():
        raise ValueError(f'{prefs_dir} is not a directory')
    wanted = {}
    for spec in specs:
        domain, key, value = parse(spec)
        wanted.setdefault(domain, {})[key] = value
    report = []
    for domain, keys in sorted(wanted.items()):
        path = prefs_dir / f'{domain}.plist'
        fmt, data, old_stat = plistlib.FMT_BINARY, {}, None
        if path.exists():
            old_stat = path.stat()
            raw = path.read_bytes()
            fmt = plistlib.FMT_BINARY if raw.startswith(b'bplist') else plistlib.FMT_XML
            data = plistlib.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise ValueError(f'{path} does not hold a dictionary')
        before = {k: data.get(k) for k in keys}
        data.update(keys)
        encoded = plistlib.dumps(data, fmt=fmt, sort_keys=True)
        if old_stat:
            with path.open('r+b') as handle:        # in place: same inode, owner and mode
                handle.write(encoded)
                handle.truncate()
            owner = None
        else:
            path.write_bytes(encoded)
            dir_stat = prefs_dir.stat()
            path.chmod(0o600)
            owner = 'kept'
            try:
                os.chown(path, dir_stat.st_uid, dir_stat.st_gid)
            except PermissionError:
                owner = 'not changed (mount without ownership or not root)'
        st = path.stat()
        report.append(dict(file=str(path), created=old_stat is None, format='binary' if fmt == plistlib.FMT_BINARY else 'xml',
                           before=before, after=keys, mode=f'{stat.S_IMODE(st.st_mode):o}', owner=owner or 'unchanged'))
    return report


def main(argv):
    if len(argv) < 3 or argv[1] in ('-h', '--help'):
        print(__doc__)
        return 0 if len(argv) > 1 and argv[1] in ('-h', '--help') else 2
    try:
        print(json.dumps(apply(argv[1], argv[2:]), indent=2))
    except (ValueError, plistlib.InvalidFileException) as error:
        print(f'keyboard-prefs.py: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
