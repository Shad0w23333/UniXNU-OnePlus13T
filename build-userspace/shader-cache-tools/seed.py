#!/usr/bin/env python3
"""The Data seed (DBRDISK1, packed RAM disk) seen from the host: unpack to a raw APFS image, attach it, and either inspect the plugin's
files (read-only) or add the shader-cache pack and repack. The base seed is never modified. Uses UniXNU/drivers/DarwinBoot/Test/packed_ramdisk.py
(imported, never written: PYTHONDONTWRITEBYTECODE)."""
import contextlib
import hashlib
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/DarwinBoot/Test')
import packed_ramdisk  # noqa: E402

ADDONS = 'ngpu/addons'
BUNDLE = ADDONS + '/NativeAdrenoMetal.bundle'
PACK_NAME = 'NativeAdrenoMetal.m2vpack'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def unpack(seed, raw):
    blob = Path(seed).read_bytes()
    logical, extents = packed_ramdisk.regions(blob)
    with open(raw, 'wb') as f:
        f.truncate(logical)
        for offset, data in extents:
            f.seek(offset)
            f.write(data)
    return logical


@contextlib.contextmanager
def attached(raw, readonly):
    mount = Path(tempfile.mkdtemp(prefix='seed-mount-', dir=str(Path(raw).parent)))
    command = ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-owners', 'off', '-mountpoint', str(mount),
               '-plist']
    if readonly:
        command.insert(2, '-readonly')
    info = plistlib.loads(subprocess.check_output(command + [str(raw)]))
    device = info['system-entities'][0]['dev-entry']
    try:
        yield mount
    finally:
        subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)
        mount.rmdir()


def tree_manifest(root):
    """Every file and directory below root: relative path -> (kind, mode, size, mtime_ns, sha256 for files). Symlinks by target."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            st = path.lstat()
            if path.is_symlink():
                out[rel] = ('link', st.st_mode, os.readlink(path), st.st_mtime_ns, None)
            elif path.is_dir():
                out[rel] = ('dir', st.st_mode, 0, None, None)
            else:
                out[rel] = ('file', st.st_mode, st.st_size, st.st_mtime_ns, sha256(path))
    return out


def inspect(seed, work):
    """The facts the cache depends on, from the seed itself: the Turnip file time (Mesa's darwin build identifier, so the tablet's
    pipelineCacheUUID), the plugin's files and hashes, the env file, whether a pack is already there, and the volume's free space."""
    with tempfile.TemporaryDirectory(prefix='seed-', dir=str(work)) as td:
        raw = Path(td) / 'seed.raw'
        logical = unpack(seed, raw)
        with attached(raw, True) as mount:
            turnip = mount / BUNDLE / 'libvulkan_freedreno.dylib'
            st = turnip.stat()
            env = mount / ADDONS / 'NativeAdrenoMetal.env'
            vfs = os.statvfs(mount)
            facts = dict(seed_sha256=sha256(seed), logical_bytes=logical,
                         turnip_mtime=int(st.st_mtime), turnip_mtime_ns=st.st_mtime_ns, turnip_sha256=sha256(turnip),
                         plugin_sha256=sha256(mount / BUNDLE / 'NativeAdrenoMetal'),
                         env=env.read_text() if env.exists() else None,
                         publish=(mount / ADDONS / 'NativeAdrenoMetal.publish').exists(),
                         primelist=(mount / ADDONS / 'NativeAdrenoMetal.primelist').read_text()
                         if (mount / ADDONS / 'NativeAdrenoMetal.primelist').exists() else None,
                         pack_present=(mount / ADDONS / PACK_NAME).exists(),
                         free_bytes=vfs.f_bavail * vfs.f_frsize,
                         addons=tree_manifest(mount / 'ngpu'))
    return facts


def stage(base, output, pack, work, env_text=None, extra=None, replace_pack=False):
    """A copy of base with ngpu/addons/NativeAdrenoMetal.m2vpack added (0644), NativeAdrenoMetal.env replaced by env_text when given, and
    the files of extra ({name beside the bundle: text}) added (0644); nothing else changed. replace_pack: the pack a layered seed carries
    (keyed to an older runtime build, so never read again) is overwritten. Returns (before, after) manifests of ngpu/ and the packing
    metadata."""
    output = Path(output)
    if output.exists():
        raise SystemExit('refuse to overwrite ' + str(output))
    with tempfile.TemporaryDirectory(prefix='seed-', dir=str(work)) as td:
        raw = Path(td) / 'seed.raw'
        unpack(base, raw)
        with attached(raw, False) as mount:
            before = tree_manifest(mount / 'ngpu')
            target = mount / ADDONS / PACK_NAME
            if target.exists() and not replace_pack:
                raise SystemExit('the base seed already carries a pack')
            if target.exists():
                # Zeros in place before it goes: the freed blocks of a pack replaced as it is would stay in the packed seed, which leaves
                # out only zero blocks, and a seed rebuilt from a seed would carry every earlier pack against the payload's budget.
                with open(target, 'r+b') as old:
                    old.write(bytes(target.stat().st_size))
                    old.flush()
                    os.fsync(old.fileno())
                target.unlink()
            with open(pack, 'rb') as src, open(target, 'wb') as dst:
                for chunk in iter(lambda: src.read(1 << 20), b''):
                    dst.write(chunk)
            os.chmod(target, 0o644)
            if env_text is not None:
                env = mount / ADDONS / 'NativeAdrenoMetal.env'
                env.write_text(env_text)
                os.chmod(env, 0o644)
            for name, text in (extra or {}).items():
                if '/' in name or (mount / ADDONS / name).exists():
                    raise SystemExit('refuse to add ' + name)
                (mount / ADDONS / name).write_text(text)
                os.chmod(mount / ADDONS / name, 0o644)
            after = tree_manifest(mount / 'ngpu')
        if raw.read_bytes()[32:36] != b'NXSB':
            raise SystemExit('the edited image is no APFS container any more')
        metadata = packed_ramdisk.pack(raw, output)
    return before, after, metadata


def extract(seed, relative, destination, work):
    """Copies one file of the seed (path relative to the volume root, e.g. ngpu/addons/NativeAdrenoMetal.m2vpack) out, read-only."""
    with tempfile.TemporaryDirectory(prefix='seed-', dir=str(work)) as td:
        raw = Path(td) / 'seed.raw'
        unpack(seed, raw)
        with attached(raw, True) as mount:
            source = mount / relative
            if not source.is_file():
                return False
            with open(source, 'rb') as src, open(destination, 'wb') as dst:
                for chunk in iter(lambda: src.read(1 << 20), b''):
                    dst.write(chunk)
    return True
