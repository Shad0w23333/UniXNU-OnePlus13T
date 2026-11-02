#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Check a Data seed made with make-phone-seed.py --diag-daemon DIR (read-only attach with ownership; macOS).

  check-seed.py SEED.dbrdisk DIR

The seed must hold DIR's daemon (same bytes, mode 0755, the CDHash of DIR/trust.json), the logging preference and the launchd job (0644),
and a launch policy that leaves com.apple.DumpPanic enabled and still disables com.apple.bluetoothd and com.apple.iomfb_fdr_loader.
Prints what it found as JSON."""
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/DarwinBoot/Test')
import packed_ramdisk  # noqa: E402

FILES = {'ngpu/bin/paganidiag': ('paganidiag', 0o755), 'preferences/Logging/Subsystems/com.apple.migration.plist':
         ('com.apple.migration.plist', 0o644), 'ngpu/daemons/org.unixnu.pagani.diag.plist': ('org.unixnu.pagani.diag.plist', 0o644)}


def main():
    seed, build = Path(sys.argv[1]), Path(sys.argv[2])
    problems, found = [], {}
    blob = seed.read_bytes()
    logical, extents = packed_ramdisk.regions(blob)
    with tempfile.TemporaryDirectory(prefix='diag-seed-check-', dir=str(seed.parent)) as tmp:
        raw = Path(tmp) / 'seed.raw'
        with raw.open('wb') as handle:
            handle.truncate(logical)
            for offset, data in extents:
                handle.seek(offset)
                handle.write(data)
        mount = Path(tmp) / 'mount'
        mount.mkdir()
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-readonly', '-nobrowse', '-owners', 'on',
             '-mountpoint', str(mount), '-plist', str(raw)], stderr=subprocess.DEVNULL))
        device = attached['system-entities'][0]['dev-entry']
        try:
            for relative, (name, mode) in FILES.items():
                path = mount / relative
                if not path.is_file():
                    problems.append(f'/private/var/{relative} missing')
                    continue
                info = path.stat()
                found[relative] = f'{info.st_uid}:{info.st_gid} {stat.S_IMODE(info.st_mode):o} {info.st_size}'
                if stat.S_IMODE(info.st_mode) != mode:
                    problems.append(f'/private/var/{relative} mode {stat.S_IMODE(info.st_mode):o}, not {mode:o}')
                if path.read_bytes() != (build / name).read_bytes():
                    problems.append(f'/private/var/{relative} differs from {build / name}')
            detail = subprocess.run(['codesign', '--display', '--verbose=4', str(mount / 'ngpu/bin/paganidiag')], capture_output=True,
                                    text=True).stderr
            cdhash = re.search(r'^CDHash=([0-9a-f]{40})$', detail, re.M)
            found['cdhash'] = cdhash.group(1) if cdhash else None
            if found['cdhash'] != json.loads((build / 'trust.json').read_text())['cdhash']:
                problems.append('the staged daemon does not carry the CDHash of trust.json')
            policy = plistlib.loads((mount / 'db/com.apple.xpc.launchd/disabled.plist').read_bytes())
            found['disabled'] = sorted(label for label, value in policy.items() if value is True)
            if policy.get('com.apple.DumpPanic') is True:
                problems.append('com.apple.DumpPanic is disabled')
            for label in ('com.apple.bluetoothd', 'com.apple.iomfb_fdr_loader'):
                if policy.get(label) is not True:
                    problems.append(f'{label} is not disabled any more')
            parents = mount / 'ngpu/bin'
            found['bin_directory'] = f'{parents.stat().st_uid}:{parents.stat().st_gid} {stat.S_IMODE(parents.stat().st_mode):o}'
            found['seed_paganidiag_conf'] = os.path.exists(mount / 'ngpu/bin/paganidiag.conf')
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)
    print(json.dumps(dict(found=found, problems=problems), indent=2))
    if problems:
        sys.exit('check-seed: ' + '; '.join(problems))
    print('PASS: the seed carries PaganiDiag as built, launch policy kept')


if __name__ == '__main__':
    main()
