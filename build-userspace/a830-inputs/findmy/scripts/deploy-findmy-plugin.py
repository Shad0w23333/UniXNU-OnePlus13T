#!/usr/bin/env python3
"""Rebuild NativeAdrenoMetal.bundle (with the bridge's runtime patch 0018, pooled blocks that fit the heap) and put it into a copy of
the current A830 phone Data seed, with its CDHash added to a copy of the seed's owner trust bundle.

  deploy-findmy-plugin.py [--tree private|live] [--plugin DIR] [--seed SEED] [--trust BUNDLE] [--out DIR] [--name NAME]
                          [--drop-replaced] [--force]
  deploy-findmy-plugin.py --check SEED.dbrdisk TRUST.bundle

Build (skipped with --plugin DIR, a directory holding NativeAdrenoMetal.bundle and trust.json from build-adreno-metal-plugin.py):
  --tree private (default): a resolved tree of its own under findmy/tree, as build-userspace-a830.sh makes one: UniXNU's scripts
      copied, drivers/kits/patches linked to their sources, a local clone of the build tree's metal2vulkan checkout at the pin (the
      bridge build applies the recorded patches 0001-0018 there), and read-only links to the build tree's Mesa headers
      (.cache/mesa-work) and iOS Turnip (build/mesa-tu-ios, the Turnip the seed carries). Nothing of build-userspace/tree changes.
  --tree live: build-userspace-a830.sh steps 1, 5 and 6 in build-userspace/tree itself (scripts synced, plugin rebuilt, its owner trust
      bundle remade): what the build does there, no more.
Seed: the bundle replaces /private/var/ngpu/addons/NativeAdrenoMetal.bundle in a copy of SEED (default the 2026-10-31-pkgAB seed
  phone-island-apps-audio.dbrdisk); nothing else in it changes (clients, env, pipeline pack, prime list, publish marker, daemons, apps,
  audio plug-in); the volume UUID stays. Trust: TRUST (default the seed's phone-island-apps-audio-owner-trust.bundle) is copied with the
  new plugin CDHash (and Turnip's, should it differ from the seed's) added to its owner module in ascending order, checked as
  DarwinBoot's ValidTrustCaches checks it; the two original Apple modules stay byte for byte (checked against build-63's
  original-static-system.bundle). The replaced plugin's CDHash stays unless --drop-replaced (the bundle then trusts the old seed too).
Output (refused when present, unless --force): OUT/NAME.dbrdisk, OUT/NAME.json (what was done, with the image command) and
  OUT/NAME-owner-trust.bundle; default OUT findmy/out, NAME phone-island-apps-audio-findmy. Runs on macOS (hdiutil, codesign, cargo).
  Never touches the phone, never commits."""
import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
TOP = Path('/Users/xiaochoumao/unixnu-13t')
UNIXNU = TOP / 'UniXNU'
A830 = TOP / 'Adreno830'
LIVE = TOP / 'build-userspace/tree'
FINDMY = TOP / 'build-userspace/a830-inputs/findmy'
PRIVATE = FINDMY / 'tree'
PKG = TOP / 'build-userspace/a830-inputs/2026-10-31-pkgAB'
DEFAULT_SEED = PKG / 'phone-island-apps-audio.dbrdisk'
DEFAULT_TRUST = PKG / 'phone-island-apps-audio-owner-trust.bundle'
ORIGINAL_TRUST = TOP / '作者产物/build-63/original-static-system.bundle'
ORIGINAL_SHA256 = '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7'
ADDONS = 'ngpu/addons'
BUNDLE = 'NativeAdrenoMetal.bundle'
RUNTIME_PATCH = '0018-pooled-blocks-fit-the-heap.patch'
sys.path.insert(0, str(UNIXNU / 'drivers/DarwinBoot/Test'))
import packed_ramdisk  # noqa: E402


def fail(message):
    raise SystemExit('deploy-findmy-plugin: ' + message)


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs)


def cdhash_of(path):
    detail = run('codesign', '--display', '--verbose=4', path).stderr
    match = re.search(r'^CDHash=([0-9a-f]{40})$', detail, re.M)
    if not match or not re.search(r'^Page size=4096$', detail, re.M):
        fail(f'{path}: no 4 KiB-page signature with a CDHash')
    return match.group(1)


def build_environment():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.setdefault('DEVELOPER_DIR', '/Applications/Xcode-26.6.app/Contents/Developer')
    env['PATH'] = '/opt/homebrew/opt/bison/bin:/opt/homebrew/opt/llvm/bin:/opt/homebrew/bin:' + env.get('PATH', '')
    # The owner's driver variables of a shell must not reach the build (build-turnip-darwin.py drops them as well).
    for name in [n for n in env if n.startswith(('TU_', 'IR3_', 'FD_MESA_', 'NIR_', 'MESA_SHADER_'))]:
        env.pop(name)
    return env


# ---- the build ------------------------------------------------------------------------------------------------------------------
def link(path, target):
    if path.is_symlink() and path.resolve() == Path(target).resolve():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        fail(f'{path} is a directory where a link to {target} belongs')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)


def prepare_private_tree(log):
    """The resolved tree of build-userspace-a830.sh step 1 under findmy/tree, with read-only links to what it only reads."""
    for directory in ('drivers', 'kits', 'patches', 'build', '.cache/gpu-reference'):
        (PRIVATE / directory).mkdir(parents=True, exist_ok=True)
    for name in ('AdrenoMetal', 'Adreno740', 'GopFramebuffer', 'DarwinBoot'):
        link(PRIVATE / 'drivers' / name, UNIXNU / 'drivers' / name)
    link(PRIVATE / 'drivers/Adreno830', A830)
    for name in ('AdrenoPM4', 'ArmSmmu4K'):
        link(PRIVATE / 'kits' / name, UNIXNU / 'kits' / name)
    link(PRIVATE / 'patches/mesa', UNIXNU / 'patches/mesa')
    run('rsync', '-a', '--delete', f'{UNIXNU}/scripts/', f'{PRIVATE}/scripts/')
    # Read only: the Vulkan headers of the pinned Mesa, and the iOS Turnip build the seed's bundle carries.
    link(PRIVATE / '.cache/mesa-work', LIVE / '.cache/mesa-work')
    link(PRIVATE / 'build/mesa-tu-ios', LIVE / 'build/mesa-tu-ios')
    shutil.copyfile(A830 / 'docs/inputs/userspace/prime-list-21H461.txt', PRIVATE / 'build/prime-list.txt')
    pin = json.loads((PRIVATE / 'scripts/data/gpu-metal-vulkan-inputs.json').read_text())['translator']['commit']
    m2v = PRIVATE / '.cache/gpu-reference/metal2vulkan'
    if not (m2v / '.git').exists():
        # A local clone of the build tree's checkout (objects hard-linked, every blob of the pin present there): the bridge build
        # applies the recorded patches to this one, never to the build tree's.
        run('git', 'clone', '-q', '--local', '--no-checkout', LIVE / '.cache/gpu-reference/metal2vulkan', m2v)
        run('git', '-C', m2v, 'checkout', '-q', '--detach', pin)
    head = run('git', '-C', m2v, 'rev-parse', 'HEAD').stdout.strip()
    if head != pin:
        fail(f'{m2v} is at {head}, not the pin {pin}')
    log.write(f'private tree {PRIVATE}: scripts synced, metal2vulkan at {pin}\n')
    return PRIVATE


def build_plugin(root, live, log):
    env = build_environment()
    if live:
        # build-userspace-a830.sh step 1 (UniXNU's scripts into the tree) and the prime list of its step 5.
        run('rsync', '-a', '--delete', f'{UNIXNU}/scripts/', f'{root}/scripts/')
        shutil.copyfile(A830 / 'docs/inputs/userspace/prime-list-21H461.txt', root / 'build/prime-list.txt')
    if RUNTIME_PATCH not in (root / 'scripts/build-adreno-metal-bridge.py').read_text():
        fail(f'{root}/scripts/build-adreno-metal-bridge.py does not apply {RUNTIME_PATCH}')
    turnip = root / 'build/mesa-tu-ios/src/freedreno/vulkan/libvulkan_freedreno.dylib'
    if not turnip.is_file():
        fail(f'no iOS Turnip at {turnip} (build-userspace-a830.sh step 4)')
    for script in ['build-adreno-metal-plugin.py'] + (['make-owner-trust-bundle.py'] if live else []):
        log.write(f'== {script} in {root}\n')
        log.flush()
        result = subprocess.run([sys.executable, '-B', str(root / 'scripts' / script)], env=env, cwd=root, stdout=log,
                                stderr=subprocess.STDOUT)
        if result.returncode:
            fail(f'{script} failed (exit {result.returncode}); see {log.name}')
    return root / 'build/adreno-metal-plugin'


# ---- trust bundles (make-owner-trust-bundle.py's format; DarwinBoot ValidTrustCaches) ------------------------------------------------
def modules_of(data):
    count = struct.unpack_from('<I', data)[0]
    if not 0 < count <= 128 or len(data) < 4 + 4 * count:
        fail('not a trust bundle')
    starts = list(struct.unpack_from(f'<{count}I', data, 4))
    return [data[s:e] for s, e in zip(starts, starts[1:] + [len(data)])]


def bundle_of(modules):
    cursor, starts = 4 + 4 * len(modules), []
    for module in modules:
        starts.append(cursor)
        cursor += len(module)
    return struct.pack(f'<{1 + len(modules)}I', len(modules), *starts) + b''.join(modules)


def valid_like_the_loader(data):
    count = struct.unpack_from('<I', data)[0]
    if not 0 < count <= 128:
        return False
    for index in range(count):
        start = struct.unpack_from('<I', data, 4 + 4 * index)[0]
        end = struct.unpack_from('<I', data, 8 + 4 * index)[0] if index + 1 < count else len(data)
        if start < 4 + 4 * count or start & 3 or end > len(data) or end - start < 24 or struct.unpack_from('<I', data, start)[0] != 2:
            return False
        entries = struct.unpack_from('<I', data, start + 20)[0]
        if end - start != 24 + entries * 24:
            return False
        for entry in range(entries):
            at = start + 24 + entry * 24
            if not 1 <= data[at + 20] <= 4 or (entry and data[at - 24:at - 4] >= data[at:at + 20]):
                return False
    return True


def owner_entries(data):
    """The owner module (the first that is not one of the original Apple modules) and its entries {hash: 4 trailing bytes}."""
    if not valid_like_the_loader(data):
        fail('the trust bundle is not one the boot loader accepts')
    modules = modules_of(data)
    original = ORIGINAL_TRUST.read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        fail(f'{ORIGINAL_TRUST} is not the unchanged original static/System trust bundle')
    apple = modules_of(original)
    if [m for m in modules if m in apple] != apple:
        fail('the trust bundle does not carry both original Apple modules unchanged')
    owner = next((i for i, m in enumerate(modules) if m not in apple), None)
    if owner is None:
        fail('the trust bundle has no owner module')
    module = modules[owner]
    count = struct.unpack_from('<I', module, 20)[0]
    entries = {module[24 + 24 * i:44 + 24 * i]: module[44 + 24 * i:48 + 24 * i] for i in range(count)}
    return modules, owner, apple, entries


def edit_trust(base, add, drop):
    data = base.read_bytes()
    modules, owner, apple, entries = owner_entries(data)
    before = sorted(entries)
    for cdhash in drop:
        entries.pop(bytes.fromhex(cdhash), None)
    for cdhash in add:
        entries.setdefault(bytes.fromhex(cdhash), bytes([2, 0, 0, 0]))   # hash type 2 (SHA-256 truncated), no flags or category
    ordered = sorted(entries)
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, 'org.unixnu.findmy.owner:' + ''.join(h.hex() for h in ordered))
    modules[owner] = struct.pack('<I16sI', 2, identifier.bytes, len(ordered)) + b''.join(h + entries[h] for h in ordered)
    result = bundle_of(modules)
    if not valid_like_the_loader(result) or [m for m in modules_of(result) if m in apple] != apple:
        fail('the new trust bundle would be refused by the boot loader')
    return result, dict(base=str(base), base_sha256=hashlib.sha256(data).hexdigest(),
                        owner_hashes_before=[h.hex() for h in before], owner_hashes=[h.hex() for h in ordered],
                        added=[h for h in add if bytes.fromhex(h) not in before], dropped=[h for h in drop if bytes.fromhex(h) in before],
                        apple_modules_unchanged=True, sha256=hashlib.sha256(result).hexdigest())


# ---- the seed -------------------------------------------------------------------------------------------------------------------
def attach(raw, mount, readonly):
    args = ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-mountpoint', str(mount), '-plist']
    args += ['-readonly', '-owners', 'on'] if readonly else ['-owners', 'off']
    attached = plistlib.loads(subprocess.check_output(args + [str(raw)]))
    return attached['system-entities'][0]['dev-entry']   # the whole image's device: detaching it detaches the volume too


def detach(device):
    subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)


def unpack(seed, raw):
    logical, extents = packed_ramdisk.regions(seed.read_bytes())
    with raw.open('wb') as handle:
        handle.truncate(logical)
        for offset, data in extents:
            handle.seek(offset)
            handle.write(data)
    return logical


def seed_plugin(seed, scratch):
    """The plugin bundle's and its Turnip's CDHashes in a packed seed (read-only attach)."""
    raw = scratch / 'check.raw'
    unpack(seed, raw)
    mount = scratch / 'check-mount'
    mount.mkdir()
    device = attach(raw, mount, readonly=True)
    try:
        bundle = mount / ADDONS / BUNDLE
        run('codesign', '--verify', '--strict', bundle)
        return dict(cdhash=cdhash_of(bundle), turnip_cdhash=cdhash_of(bundle / 'libvulkan_freedreno.dylib'),
                    executable_sha256=hashlib.sha256((bundle / 'NativeAdrenoMetal').read_bytes()).hexdigest(),
                    files=sorted(str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file()),
                    neighbours=sorted(p.name for p in (mount / ADDONS).iterdir() if p.name != BUNDLE))
    finally:
        detach(device)
        raw.unlink()
        mount.rmdir()


def stage(mount, bundle):
    """The bundle in place of the seed's: a file with the same bytes stays (Turnip and Info.plist, normally), every other one is
    zeroed in place before it goes, so its freed blocks hold zeros, which the packed seed leaves out (else the old 21 MiB executable
    stayed in the seed beside the new one). Returns the files kept and the files written."""
    target = mount / ADDONS / BUNDLE
    if not target.is_dir() or not (mount / 'mobile').is_dir():
        fail('the seed has no /private/var/ngpu/addons/NativeAdrenoMetal.bundle or no /mobile: wrong base image?')
    if any(p.is_symlink() for p in [*bundle.rglob('*'), *target.rglob('*')]):
        fail('a bundle with symbolic links')
    new = {str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file()}
    old = {str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()}
    kept = sorted(name for name in old & new if (target / name).read_bytes() == (bundle / name).read_bytes())
    for name in sorted(old - set(kept)):
        path = target / name
        with path.open('r+b') as handle:
            handle.write(bytes(path.stat().st_size))
            handle.flush()
            os.fsync(handle.fileno())
        path.unlink()
    written = sorted(new - set(kept))
    for name in written:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle / name, target / name)
    for path in [target, *target.rglob('*')]:
        os.chmod(path, 0o755 if path.is_dir() or path.name in ('NativeAdrenoMetal', 'libvulkan_freedreno.dylib') else 0o644)
    staged = {str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()}
    if staged != new or any((target / n).read_bytes() != (bundle / n).read_bytes() for n in new):
        fail('the staged bundle differs from the built one')
    run('codesign', '--verify', '--strict', target)
    return dict(kept=kept, written=written)


def check(seed, trust):
    with tempfile.TemporaryDirectory(prefix='findmy-check-', dir=str(seed.parent)) as scratch:
        plugin = seed_plugin(seed, Path(scratch))
    _, _, _, entries = owner_entries(trust.read_bytes())
    missing = [name for name in ('cdhash', 'turnip_cdhash') if bytes.fromhex(plugin[name]) not in entries]
    print(json.dumps(dict(seed=str(seed), trust=str(trust), plugin=plugin, trusted=not missing), indent=2))
    if missing:
        fail(f'{trust} does not trust the seed\'s plugin ({", ".join(missing)})')
    print('PASS: the seed\'s NativeAdrenoMetal.bundle and its Turnip are in the owner trust bundle')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tree', choices=('private', 'live'), default='private')
    parser.add_argument('--plugin', type=Path)
    parser.add_argument('--seed', type=Path, default=DEFAULT_SEED)
    parser.add_argument('--trust', type=Path, default=DEFAULT_TRUST)
    parser.add_argument('--out', type=Path, default=FINDMY / 'out')
    parser.add_argument('--name', default='phone-island-apps-audio-findmy')
    parser.add_argument('--drop-replaced', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--check', nargs=2, type=Path, metavar=('SEED', 'TRUST'))
    args = parser.parse_args()
    if args.check:
        return check(args.check[0].resolve(), args.check[1].resolve())
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    seed_out, json_out, trust_out = out / (args.name + '.dbrdisk'), out / (args.name + '.json'), out / (args.name + '-owner-trust.bundle')
    for path in (seed_out, json_out, trust_out):
        if path.exists():
            if not args.force:
                fail(f'refuse to overwrite {path} (--force)')
            path.unlink()
    base_seed, base_trust = args.seed.resolve(), args.trust.resolve()
    for path in (base_seed, base_trust, ORIGINAL_TRUST):
        if not path.is_file():
            fail(f'missing input {path}')
    logs = out / 'logs'
    logs.mkdir(exist_ok=True)
    with (logs / (args.name + '-build.log')).open('w') as log:
        if args.plugin:
            plugin_dir = args.plugin.resolve()
        else:
            root = LIVE if args.tree == 'live' else prepare_private_tree(log)
            plugin_dir = build_plugin(root, args.tree == 'live', log)
    bundle = plugin_dir / BUNDLE
    built = json.loads((plugin_dir / 'trust.json').read_text())
    run('codesign', '--verify', '--strict', bundle)
    cdhash, turnip = cdhash_of(bundle), cdhash_of(bundle / 'libvulkan_freedreno.dylib')
    if cdhash != built.get('cdhash') or turnip != built.get('turnip_cdhash'):
        fail(f'{plugin_dir}/trust.json does not hold the bundle\'s CDHashes; rebuild the plugin')
    with tempfile.TemporaryDirectory(prefix='findmy-seed-', dir=str(out)) as scratch:
        scratch = Path(scratch)
        before = seed_plugin(base_seed, scratch)
        report = dict(base=str(base_seed), base_sha256=packed_ramdisk.inspect(base_seed)[0]['sha256'],
                      plugin=dict(path='/private/var/' + ADDONS + '/' + BUNDLE, cdhash=cdhash, turnip_cdhash=turnip,
                                  executable_sha256=hashlib.sha256((bundle / 'NativeAdrenoMetal').read_bytes()).hexdigest(),
                                  source=str(plugin_dir), runtime_patch=RUNTIME_PATCH, replaced=before),
                      plugin_cdhash=cdhash)
        raw = scratch / 'seed.raw'
        unpack(base_seed, raw)
        mount = scratch / 'mount'
        mount.mkdir()
        device = attach(raw, mount, readonly=False)
        try:
            report['files'] = stage(mount, bundle)
            usage = subprocess.run(['df', '-k', str(mount)], capture_output=True, text=True).stdout.splitlines()[-1].split()
            report['used_kib'], report['available_kib'] = int(usage[2]), int(usage[3])
        finally:
            detach(device)
        if raw.open('rb').read(4096)[32:36] != b'NXSB':
            fail('the edited image is no APFS container any more')
        report['output'] = packed_ramdisk.pack(raw, seed_out)
        raw.unlink()
        after = seed_plugin(seed_out, scratch)
    if (after['cdhash'], after['turnip_cdhash']) != (cdhash, turnip) or after['neighbours'] != before['neighbours']:
        fail('the packed seed does not hold the staged bundle beside the same files')
    add = [cdhash] + ([turnip] if turnip != before['turnip_cdhash'] else [])
    drop = [before['cdhash']] if args.drop_replaced and before['cdhash'] != cdhash else []
    trust_bytes, report['trust'] = edit_trust(base_trust, add, drop)
    trust_out.write_bytes(trust_bytes)
    report['trust']['output'] = str(trust_out)
    if turnip != before['turnip_cdhash']:
        report['note'] = 'the bundle carries another Turnip than the seed did; both of its CDHashes are trusted'
    report['image_arguments'] = ['--data-ramdisk', str(seed_out), '--owner-trust-cache', str(trust_out), '--kernel-arg', 'pagani-audio=1']
    report['image_command'] = (f'cd {TOP} && A830_PREBUILT_SEED={seed_out} A830_OWNER_TRUST={trust_out} A830_AUDIO=1 '
                               'A830_SYSTEM_APPS=1 A830_RESET_AFTER=1800 A830_EXTRA_ARGS="pagani-diag=0 pagani-gpu-status=1" '
                               '工具/make-a830-layout.sh phone-island-apps')
    json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'PASS: {seed_out.name} carries NativeAdrenoMetal.bundle {cdhash} (was {before["cdhash"]}), Turnip {turnip}; '
          f'{trust_out.name} trusts it')


if __name__ == '__main__':
    main()
