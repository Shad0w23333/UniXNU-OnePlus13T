#!/usr/bin/env python3
"""jetsam-limits.py: the per-process memory limits a 21H461 root gives the GPU clients, from its jetsam properties, and what
phone-gpu-client-memlimits.patch makes of them. Reads the Apple files on the local OS volume only; prints numbers.

Rules (reverse-engineered from 21H461, see NOTES.md):
- launchd (/sbin/launchd) loads /System/Library/LaunchDaemons/com.apple.jetsamproperties.<hw.targettype>.plist once at start
  (before the Data volume exists). A job's category is Angel (__Angel), DriverKit, App/SystemApp (UIKitApplication: labels) or Daemon;
  its name _JetsamPropertiesIdentifier or the label. Each key is taken from the first that has it: Override[name], Override[Global],
  the job's own JetsamProperties, Default[name], Default[Global]. A Soft and a Hard variant of one limit: the smaller wins; negative =
  absent. Soft = non-fatal, Hard = fatal (posix_spawnattr_setjetsam_ext flags 4/8).
- RunningBoard (RBJetsamPropertyManager) loads the same file, keeps each section's Override entries and its Global, and asks for
  "<Category><Soft|Hard>MemoryLimit" (category Active, Inactive or a client-chosen one such as PosterProvider_Rendering_Nominal), the
  identifier's entry first, then the section's Global, the other strength as fallback; it sets active = inactive = that value with
  MEMORYSTATUS_CMD_SET_MEMLIMIT_PROPERTIES, fatal for Hard.
Run: python3 -I jetsam-limits.py [--plist P] [--xpc launchd.plist] [--floor MB]"""
import argparse
import plistlib
from pathlib import Path

OS = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/osmnt-21H461')
DEFAULT_PLIST = OS / 'System/Library/LaunchDaemons/com.apple.jetsamproperties.J120.plist'
DEFAULT_XPC = OS / 'System/Library/xpc/launchd.plist'

# (process, how it is launched, jetsam category, identifier). The GPU clients of the A830 seed (build-userspace-a830.sh PUBLISH_CLIENTS,
# Adreno830 NativeAdrenoSandboxGrantList) plus the processes around them.
CLIENTS = [
    ('iconservicesagent', 'launchd', 'Daemon', 'com.apple.iconservices.iconservicesagent'),
    ('chronod', 'launchd', 'Daemon', 'com.apple.chronod'),
    ('SpringBoard', 'launchd', 'Daemon', 'com.apple.SpringBoard'),
    ('backboardd', 'launchd', 'Daemon', 'com.apple.backboardd'),
    ('PosterBoard', 'RunningBoard', 'Angel', 'com.apple.PosterBoard'),
    ('WidgetRenderer_Default', 'RunningBoard', 'SystemApp', 'com.apple.chrono.WidgetRenderer-Default'),
    ('poster extensions', 'RunningBoard', 'Extension', 'com.apple.posterkit.provider'),
    ('widget extensions', 'RunningBoard', 'Extension', 'com.apple.widgetkit-extension'),
    ('other extensions', 'RunningBoard', 'Extension', 'Global'),
]
LIMIT_KEYS = ['ActiveSoftMemoryLimit', 'ActiveHardMemoryLimit', 'InactiveSoftMemoryLimit', 'InactiveHardMemoryLimit']


def launchd_merge(section, name, job):
    default, override = section.get('Default', {}), section.get('Override', {})
    chain = [override.get(name), override.get('Global'), job, default.get(name), default.get('Global')]
    out = {}
    for key in LIMIT_KEYS + ['JetsamPriority']:
        for source in chain:
            if source and key in source:
                out[key] = source[key]
                break
    return out


def pick(props, which):
    """(MB, fatal) of the active or inactive limit: the smaller of Soft and Hard, negative = absent; (-1, None) = none."""
    soft, hard = props.get(which + 'SoftMemoryLimit', -1), props.get(which + 'HardMemoryLimit', -1)
    soft = soft if soft > 0 else None
    hard = hard if hard > 0 else None
    if soft and hard:
        return (soft, False) if soft <= hard else (hard, True)
    if soft:
        return soft, False
    if hard:
        return hard, True
    return -1, None


def runningboard(section, ident, which):
    entry, glob = section.get('Override', {}).get(ident, {}), section.get('Override', {}).get('Global', {})
    for strength, fatal in (('Soft', False), ('Hard', True)):
        for source in (entry, glob):
            value = source.get(which + strength + 'MemoryLimit')
            if value is not None:
                return (value, fatal) if value > 0 else (-1, None)
    return -1, None


def text(limit):
    mb, fatal = limit
    return 'none' if mb <= 0 else f'{mb} {"hard" if fatal else "soft"}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plist', default=str(DEFAULT_PLIST))
    parser.add_argument('--xpc', default=str(DEFAULT_XPC))
    parser.add_argument('--floor', type=int, default=512)
    args = parser.parse_args()
    props = plistlib.loads(Path(args.plist).read_bytes())['Version4']
    jobs = {}
    if Path(args.xpc).is_file():
        for job in plistlib.loads(Path(args.xpc).read_bytes()).get('LaunchDaemons', {}).values():
            jobs[job.get('_JetsamPropertiesIdentifier', job.get('Label'))] = job
    print(f'{"process":24} {"via":12} {"category/identifier":52} {"active":>10} {"inactive":>10}   once a GPU client (floor {args.floor})')
    for process, via, category, ident in CLIENTS:
        section = props.get(category, {})
        if via == 'launchd':
            merged = launchd_merge(section, ident, jobs.get(ident, {}).get('JetsamProperties'))
            active, inactive = pick(merged, 'Active'), pick(merged, 'Inactive')
        else:
            active, inactive = runningboard(section, ident, 'Active'), runningboard(section, ident, 'Inactive')
        floored = [((max(mb, args.floor), fatal) if mb > 0 else (mb, fatal)) for mb, fatal in (active, inactive)]
        extra = ''
        if ident == 'com.apple.posterkit.provider':
            entry = section.get('Override', {}).get(ident, {})
            extra = '  states: ' + ', '.join(f'{k[len("PosterProvider_"):-len("MemoryLimit")]}={v}' for k, v in sorted(entry.items())
                                             if k.startswith('PosterProvider_'))
        print(f'{process:24} {via:12} {category + "/" + ident:52} {text(active):>10} {text(inactive):>10}   '
              f'{text(floored[0]):>9} {text(floored[1]):>9}{extra}')


if __name__ == '__main__':
    main()
