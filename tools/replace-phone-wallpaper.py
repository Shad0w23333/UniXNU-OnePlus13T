#!/usr/bin/env python3
"""Put a wallpaper overlay (make-phone-wallpaper-overlay.py) into a copy of an existing phone Data seed, or check the one a seed carries.

  replace-phone-wallpaper.py [--overlay DIR] [--kernel MACHO] [--trust BUNDLE] [--no-budget] [--scratch DIR] SEED.dbrdisk OUTPUT.dbrdisk
  replace-phone-wallpaper.py --check [--render] [--scratch DIR] SEED.dbrdisk

The seeds of 2026-10-31 were made from base seeds that are gone (make-a830-layout.sh A830_PREBUILT_SEED), so make-phone-seed.py
--wallpaper cannot be rerun; this replaces /private/var/ngpu/wallpaper-collections in a copy of SEED (a DBRDISK1 packed APFS Data
container) with DIR (default 工具/phone-wallpaper-overlay), hard links kept, and changes nothing else: same volume UUID, same
plug-ins, apps, preferences, sentinels. The overlay is data, not code: the seed's owner trust bundle stays valid unchanged (--trust
copies it next to OUTPUT as OUTPUT-owner-trust.bundle, for A830_OWNER_TRUST). Before writing, DIR must pass
check-phone-wallpaper-overlay.py (with Core Animation's own reader when xcrun is there), and after the copy the overlay read back from
the attached copy must pass it again and match DIR file for file.

Budget: unixnu payload puts the kernel, its owner trust cache, the ADT and the seed into a FAT volume of board.toml [payload] size_mib
(192) and refuses more than 95 % of it. With --kernel (default the GPU line's kernel.macho) the new seed is checked against that limit;
--no-budget skips it.

--check attaches SEED read-only and runs check-phone-wallpaper-overlay.py on the overlay inside it (exit 1 on a failed rule): the
2026-10-31 seeds fail it (logicalScreenClass "Any", HEIC through a symlink), which is why their default wallpaper is black.

Writes OUTPUT and OUTPUT with .json (what was done). Runs on macOS (hdiutil). Never touches the phone."""
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parent
sys.path.insert(0, str(TOP / 'UniXNU/drivers/DarwinBoot/Test'))
import packed_ramdisk  # noqa: E402

WALLPAPER = 'ngpu/wallpaper-collections'
DEFAULT_OVERLAY = HERE / 'phone-wallpaper-overlay'
DEFAULT_KERNEL = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho'
BOARD = TOP / 'UniXNU-Pagani_SM8750-gpu/board.toml'
SMALL_PAYLOAD_FILES = 64 * 1024   # kernel.macho.adt (14 KiB) and unixnu.toml (1.3 KiB) of the 2026-10-31 payload, rounded up


def load_checker():
    spec = importlib.util.spec_from_file_location('wallpaper_check', HERE / 'check-phone-wallpaper-overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(directory):
    """relative path -> (kind, sha256 or link target, mode, inode group) of everything below DIRECTORY."""
    out, inodes = {}, {}
    for path in sorted(Path(directory).rglob('*')):
        relative = str(path.relative_to(directory))
        info = path.lstat()
        if path.is_symlink():
            out[relative] = ('link', os.readlink(path), 0, None)
        elif path.is_dir():
            out[relative] = ('dir', '', info.st_mode & 0o777, None)
        else:
            group = inodes.setdefault((info.st_dev, info.st_ino), relative) if info.st_nlink > 1 else None
            out[relative] = ('file', hashlib.sha256(path.read_bytes()).hexdigest(), info.st_mode & 0o777, group)
    return out


def copy_with_links(source, destination):
    seen = {}

    def copy(src, dst):
        info = os.lstat(src)
        key = (info.st_dev, info.st_ino)
        if info.st_nlink > 1 and key in seen:
            os.link(seen[key], dst)
            return dst
        shutil.copyfile(src, dst)
        os.chmod(dst, info.st_mode & 0o777)
        seen[key] = dst
        return dst
    shutil.copytree(source, destination, symlinks=True, copy_function=copy)
    for path in [Path(destination), *Path(destination).rglob('*')]:
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)


def run_check(checker, overlay, render):
    problems, facts = checker.check(overlay, checker.DEFAULT_OS_ROOT if checker.DEFAULT_OS_ROOT.is_dir() else None)
    if render and facts.get('animations') and shutil.which('xcrun'):
        checker.render(facts['animations'], problems, facts)
    return problems, facts


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
    if not (Path(mount) / 'mobile').is_dir():
        detach(attached['system-entities'][0]['dev-entry'])
        sys.exit(f'The seed did not mount at {mount} as a Data volume')
    return attached['system-entities'][0]['dev-entry']   # the whole image; detaching it detaches the container and volume


def detach(device):
    subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)


def payload_budget(seed_bytes, kernel, trust):
    board = tomllib.loads(BOARD.read_text())
    size_mib = int(board.get('payload', {}).get('size_mib', 192))
    limit = int(size_mib * 1024 * 1024 * 0.95)
    content = seed_bytes + kernel.stat().st_size + (trust.stat().st_size if trust else 64 * 1024) + SMALL_PAYLOAD_FILES
    return dict(size_mib=size_mib, limit=limit, content=content, kernel=str(kernel), kernel_bytes=kernel.stat().st_size,
                headroom=limit - content)


def check_seed(seed, render, scratch_parent):
    checker = load_checker()
    with tempfile.TemporaryDirectory(prefix='wallpaper-seed-check-', dir=str(scratch_parent)) as scratch:
        raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
        unpack(seed, raw)
        mount.mkdir()
        device = attach(raw, mount, readonly=True)
        try:
            overlay = mount / WALLPAPER
            if not overlay.is_dir():
                print(f'FAIL {seed}: no /private/var/{WALLPAPER}')
                return 1
            problems, facts = run_check(checker, overlay, render)
        finally:
            detach(device)
    for line in problems:
        print('FAIL ' + line)
    print(('PASS' if not problems else 'FAIL') + f' {seed}: /private/var/{WALLPAPER} ({facts.get("unique_bytes", 0)} bytes)')
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--render', action='store_true', help='with --check: also Core Animation\'s reader')
    parser.add_argument('--overlay', type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument('--kernel', type=Path, default=DEFAULT_KERNEL)
    parser.add_argument('--trust', type=Path)
    parser.add_argument('--no-budget', action='store_true')
    parser.add_argument('--scratch', type=Path, help='where the 2 GiB sparse raw copy goes (default beside OUTPUT, or SEED with --check)')
    parser.add_argument('seed', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    args = parser.parse_args()
    if not args.seed.is_file():
        sys.exit(f'No seed {args.seed}')
    if args.check:
        sys.exit(check_seed(args.seed, args.render, args.scratch or args.seed.parent))
    if not args.output:
        sys.exit('OUTPUT.dbrdisk is needed (or --check)')
    if args.output.exists():
        sys.exit('Refuse to overwrite ' + str(args.output))
    if args.trust and not args.trust.is_file():
        sys.exit(f'No trust bundle {args.trust}')
    if not args.no_budget and not args.kernel.is_file():
        sys.exit(f'No kernel {args.kernel} for the payload budget (--kernel, or --no-budget)')
    checker = load_checker()
    problems, facts = run_check(checker, args.overlay, render=True)
    if problems:
        sys.exit(f'{args.overlay} fails check-phone-wallpaper-overlay.py:\n  ' + '\n  '.join(problems))
    wanted = tree(args.overlay)
    report = dict(seed=str(args.seed), seed_sha256=packed_ramdisk.inspect(args.seed)[0]['sha256'],
                  overlay=str(args.overlay), overlay_bytes=facts['unique_bytes'],
                  overlay_images={str(Path(i['path']).relative_to(args.overlay)): f'{i["width"]}x{i["height"]} {i["bytes"]}'
                                  for i in facts.get('images', [])})
    lineage = args.seed.with_suffix('.json')
    if lineage.is_file():
        report['seed_report'] = json.loads(lineage.read_text())
        # what make-a830-layout.sh and the plug-in tools read from a seed's .json stays at the top level
        for key in ('plugin_cdhash', 'plugin'):
            if key in report['seed_report']:
                report[key] = report['seed_report'][key]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wallpaper-seed-', dir=str(args.scratch or args.output.parent)) as scratch:
        raw, mount = Path(scratch) / 'seed.raw', Path(scratch) / 'mount'
        report['container_bytes'] = unpack(args.seed, raw)
        mount.mkdir()
        device = attach(raw, mount, readonly=False)
        try:
            if not (mount / 'mobile').is_dir() or not (mount / 'ngpu').is_dir():
                sys.exit('The seed has no /mobile or /ngpu; not a phone Data seed?')
            destination = mount / WALLPAPER
            if destination.exists():
                before, _ = run_check(checker, destination, render=False)
                report['replaced'] = dict(problems=before, files=len(tree(destination)))
                shutil.rmtree(destination)
            copy_with_links(args.overlay, destination)
            if tree(destination) != wanted:
                sys.exit(f'/private/var/{WALLPAPER} reads back different from {args.overlay}')
            after, after_facts = run_check(checker, destination, render=True)
            if after:
                sys.exit('The overlay in the seed copy fails its check:\n  ' + '\n  '.join(after))
            report['default'] = {k: [Path(b).name for b in v] for k, v in after_facts.get('default', {}).items()}
            report['render'] = {Path(ca).name: {s['state']: round(s.get('dark', 1), 4) for s in r.get('renders', [])}
                                for ca, r in after_facts.get('render', {}).items()}
            usage = subprocess.run(['df', '-k', str(mount)], capture_output=True, text=True).stdout.splitlines()[-1].split()
            report['used_kib'], report['available_kib'] = int(usage[2]), int(usage[3])
        finally:
            detach(device)
        if raw.open('rb').read(4096)[32:36] != b'NXSB':
            sys.exit('The edited image is no APFS container any more')
        report['output'] = packed_ramdisk.pack(raw, args.output)
    if not args.no_budget:
        report['payload'] = payload_budget(args.output.stat().st_size, args.kernel, args.trust)
        if report['payload']['headroom'] < 0:
            args.output.unlink()
            sys.exit(f'The seed no longer fits the boot payload: {report["payload"]}')
    if args.trust:
        trust_copy = args.output.with_name(args.output.stem + '-owner-trust.bundle')
        shutil.copyfile(args.trust, trust_copy)
        report['owner_trust'] = dict(source=str(args.trust), copy=str(trust_copy),
                                     sha256=hashlib.sha256(trust_copy.read_bytes()).hexdigest(), changed=False)
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'seed_report'}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
