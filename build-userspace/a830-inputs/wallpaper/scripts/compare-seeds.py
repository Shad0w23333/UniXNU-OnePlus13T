#!/usr/bin/env python3
"""Attach two packed phone seeds read-only and compare every path (type, mode, owner, size, sha256), and the APFS volume UUID.
  compare-seeds.py OLD.dbrdisk NEW.dbrdisk SCRATCH_DIR"""
import hashlib, os, plistlib, stat, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/DarwinBoot/Test')
import packed_ramdisk

def snapshot(seed, scratch):
    raw, mount = Path(scratch) / (seed.stem + '.raw'), Path(scratch) / (seed.stem + '-mnt')
    logical, extents = packed_ramdisk.regions(seed.read_bytes())
    with raw.open('wb') as h:
        h.truncate(logical)
        for off, data in extents:
            h.seek(off); h.write(data)
    mount.mkdir()
    att = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-readonly',
        '-nobrowse', '-owners', os.environ.get('OWNERS', 'on'), '-mountpoint', str(mount), '-plist', str(raw)], stderr=subprocess.DEVNULL))
    dev = att['system-entities'][0]['dev-entry']
    try:
        vol = next(e['dev-entry'] for e in att['system-entities'] if e.get('mount-point'))
        uuid = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', vol])).get('VolumeUUID')
        out, unreadable = {}, 0
        for root, dirs, files in os.walk(mount):
            for name in dirs + files:
                p = Path(root) / name
                rel = str(p.relative_to(mount))
                st = p.lstat()
                digest = ''
                if stat.S_ISREG(st.st_mode):
                    try:
                        digest = hashlib.sha256(p.read_bytes()).hexdigest()
                    except OSError:
                        unreadable += 1
                        digest = '?'
                elif stat.S_ISLNK(st.st_mode):
                    digest = os.readlink(p)
                out[rel] = (stat.S_IFMT(st.st_mode), stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, st.st_size if not stat.S_ISDIR(st.st_mode) else 0, digest)
        return uuid, out, unreadable
    finally:
        subprocess.run(['hdiutil', 'detach', dev], capture_output=True)

old, new, scratch = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
with tempfile.TemporaryDirectory(dir=scratch) as s:
    u1, a, r1 = snapshot(old, s)
    u2, b, r2 = snapshot(new, s)
print('volume UUID', u1, u2, 'same' if u1 == u2 else 'DIFFERENT')
print('unreadable files (as this user)', r1, r2)
prefix = 'ngpu/wallpaper-collections'
diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
outside = [k for k in diff if not (k == prefix or k.startswith(prefix + '/'))]
print('paths', len(a), len(b), 'differing', len(diff), 'outside the overlay', len(outside))
for k in outside[:40]:
    print('  ', k, a.get(k), b.get(k))
