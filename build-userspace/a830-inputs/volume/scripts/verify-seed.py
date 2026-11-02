#!/usr/bin/env python3
"""Verify a packed Data seed: mount it read-only and report the PaganiAudio plug-in's CDHash, the ngpu tree and the volume UUID.
usage: verify-seed.py SEED.dbrdisk [SEED2.dbrdisk]  (two seeds: also diff their /ngpu trees except ngpu/audio)"""
import os, plistlib, re, subprocess, sys, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/DarwinBoot/Test')
import packed_ramdisk

def facts(seed, scratch):
    blob = Path(seed).read_bytes()
    logical, extents = packed_ramdisk.regions(blob)
    raw = Path(scratch) / (Path(seed).stem + '.raw')
    with raw.open('wb') as h:
        h.truncate(logical)
        for off, data in extents:
            h.seek(off); h.write(data)
    mount = Path(scratch) / (Path(seed).stem + '.mnt'); mount.mkdir()
    att = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-readonly', '-imagekey', 'diskimage-class=CRawDiskImage',
                                                  '-nobrowse', '-owners', 'on', '-mountpoint', str(mount), '-plist', str(raw)]))
    dev = att['system-entities'][0]['dev-entry']
    try:
        info = subprocess.run(['diskutil', 'info', str(mount)], capture_output=True, text=True).stdout
        uuid = re.search(r'Volume UUID:\s+(\S+)', info).group(1)
        drv = mount / 'ngpu/audio/PaganiAudio.driver'
        cd = re.search(r'CDHash=([0-9a-f]{40})', subprocess.run(['codesign', '-dvvvv', str(drv)], capture_output=True, text=True).stderr)
        tree = {}
        for p in sorted((mount / 'ngpu').rglob('*')):
            rel = str(p.relative_to(mount))
            st = p.lstat()
            tree[rel] = (oct(st.st_mode), st.st_uid, st.st_gid, st.st_size if p.is_file() else 0)
        return dict(uuid=uuid, cdhash=cd.group(1) if cd else None, tree=tree)
    finally:
        subprocess.run(['hdiutil', 'detach', dev], capture_output=True)

with tempfile.TemporaryDirectory(dir='/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/volume/out') as scratch:
    results = [facts(s, scratch) for s in sys.argv[1:]]
for s, r in zip(sys.argv[1:], results):
    print(s, 'volume UUID', r['uuid'], 'PaganiAudio CDHash', r['cdhash'], 'ngpu entries', len(r['tree']))
    print('  audio:', {k: v for k, v in r['tree'].items() if k.startswith('ngpu/audio')})
if len(results) == 2:
    a, b = results
    keys = sorted(set(a['tree']) | set(b['tree']))
    diff = [k for k in keys if not k.startswith('ngpu/audio') and a['tree'].get(k) != b['tree'].get(k)]
    print('UUID same:', a['uuid'] == b['uuid'], '| differences outside ngpu/audio:', diff[:20], len(diff))
