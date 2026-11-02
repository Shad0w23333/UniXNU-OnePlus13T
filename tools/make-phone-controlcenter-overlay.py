#!/usr/bin/env python3
"""Build the Control Center overlay that gives the iPhone identity on the iPad root its default module sizes.

  make-phone-controlcenter-overlay.py OS_ROOT OUTPUT_DIR

OS_ROOT is the mounted OS volume of the 21H461 IPSW (read only). OUTPUT_DIR gets, for /private/var/ngpu/controlcenterui in the Data
seed (make-phone-seed.py --controlcenter), which the kernel shows over /System/Library/PrivateFrameworks/ControlCenterUI.framework
when it carries phone-controlcenter-settings.patch (a read-only union, like the wallpaper and keyboard overlays):

  DefaultModuleSettings~iphone.plist
                              -[CCUIModuleSettingsManager _loadSettings] reads the framework's DefaultModuleSettings.plist through
                              -[NSBundle URLForResource:withExtension:], which picks the ~iphone or ~ipad variant by the device class.
                              The iPad root has only ~ipad, so under the iPhone identity the URL is nil, the settings dictionary is
                              empty and every module falls back to the 1x1 size of _CCUIMakeLayoutSizeFromDictionary: connectivity,
                              now playing, the brightness and volume sliders and Focus all drawn as small squares (the "erratic"
                              Control Center of the 2026-10-30 21:24 run). The file carries the iPad root's module sizes (the same
                              4-column grid an iPhone uses in portrait), so the modules get their designed shapes.
Nothing in OS_ROOT is modified."""
import plistlib
import sys
from pathlib import Path

FRAMEWORK = 'System/Library/PrivateFrameworks/ControlCenterUI.framework'
IPAD = 'DefaultModuleSettings~ipad.plist'
PHONE = 'DefaultModuleSettings~iphone.plist'


def settings(source):
    """The module sizes of SOURCE, checked for the shape _loadSettings reads: identifier -> {size | portrait/landscape: {size}}."""
    with source.open('rb') as handle:
        table = plistlib.load(handle)
    if not isinstance(table, dict) or not table:
        sys.exit(f'{source}: not a module settings dictionary')
    for identifier, entry in table.items():
        sizes = [entry.get('size')] + [entry.get(o, {}).get('size') for o in ('portrait', 'landscape') if isinstance(entry.get(o), dict)]
        if not isinstance(entry, dict) or not any(isinstance(s, dict) for s in sizes):
            sys.exit(f'{source}: {identifier} has no size')
        for size in filter(None, sizes):
            if not all(isinstance(size.get(k, 1), int) and 1 <= size.get(k, 1) <= 4 for k in ('width', 'height')):
                sys.exit(f'{source}: {identifier} size {size} is outside the 4-column grid')
    return table


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.split('\n\n')[1])
    root, output = Path(sys.argv[1]), Path(sys.argv[2])
    framework = root / FRAMEWORK
    if not (framework / 'Info.plist').is_file():
        sys.exit(f'{root}: no {FRAMEWORK} (not the OS volume)')
    if (framework / PHONE).exists():
        sys.exit(f'{framework / PHONE} exists: this root needs no overlay')
    table = settings(framework / IPAD)
    output.mkdir(parents=True, exist_ok=True)
    target = output / PHONE
    if target.exists():
        sys.exit(f'{target} exists (remove it to rebuild)')
    with target.open('wb') as handle:
        plistlib.dump(table, handle, fmt=plistlib.FMT_BINARY, sort_keys=True)
    target.chmod(0o644)
    print(f'{target}: {len(table)} module sizes from {IPAD}')


if __name__ == '__main__':
    main()
