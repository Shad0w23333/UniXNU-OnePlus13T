#!/usr/bin/env python3
"""Shader/pipeline cache for the A830 render server, made on this Mac and shipped in the Data seed (no binary changes).

What the tablet's backboardd looks up, and why the Mac can produce the same entries:
  - The plugin opens the metal2vulkan runtime with a cache directory (backboardd: ~mobile/Library/Caches/com.apple.backboardd/m2v) and,
    before that, imports NativeAdrenoMetal.m2vpack from beside the bundle into it (CacheExport.h importPack, marker 0x6Cnn). The pack is
    data in the seed: the owner trust bundle, the plugin and Turnip stay byte-identical.
  - Translations live in v1/<build>/..: <build> = SHA-256 of the runtime version, the artifact versions and the two source digests
    (M2V_SOURCE_DIGEST, M2V_RUNTIME_SOURCE_ID) that are compiled into the plugin. Entry names hash the build, kind, key and the device
    profile fingerprint (Turnip's features, limits and formats for chip 0x44050001).
  - The driver's VkPipelineCache data lives in v1/<content>/.., one record per pipeline making, named by the making, the driver identity
    (vendor 0x5143, device 0x44050001, driverVersion, pipelineCacheUUID), the instance settings and the profile. Turnip's
    pipelineCacheUUID on Darwin is BLAKE3(LC_UUID of libvulkan_freedreno.dylib, chip id, TU_DEBUG cache flags, UCHE trap base, 3 drirc
    booleans): the host Turnip, built from the same Mesa source with the tablet's release options, carries the tablet Turnip's LC_UUID and
    so reports the tablet's UUID.
  - The requests are the device's own: the same frontend (NativeBridgeMetalFrontend.mm) and priming code (PrimeShaders.h) over the
    host build of the same runtime, the libraries read from the flashed root image (OS window) and the SystemOS cryptex. prime-done gets
    the device's ids (path, size, mtime, nmb_runtime_build()-LC_UUID), so the tablet's priming thread skips what the pack holds.
  - Optionally a pipeline journal (the plugin's pipelines.log from a device run: R and S lines) is replayed through the same frontend.

Steps: inputs and identities -> host tools -> libraries -> generate -> pack -> host checks -> stage the seed (and check it). Writes only
below build-userspace (shader-cache/, the output seed and its .json in tree/build). Never touches the phone, never commits.

  make-shader-cache-a830.py [--libraries quartzcore|all|none|FILE] [--journal pipelines.log] [--prime-off] [--replace-pack] [--base SEED] [--output SEED]
                            [--merge-export DIR] [--pack-budget-mib N] [--with-cacheexport] [--env-line KEY=VALUE] [--force] [--keep-mounts] [--skip-tools]
  make-shader-cache-a830.py --check-seed SEED [--journal pipelines.log]   (host checks of an existing variant only)

A device's own pipelines (jitter F2; shader-cache-tools/harvest-a830.py runs the rounds): --with-cacheexport adds the marker that starts the plugin's
export, an image with sheng-log=1 sends the cache directory to the UFS log, gpu-loop.py reads it back into <run>/ufs/export (with pipelines.log, the
journal), and --merge-export takes it into the next pack. --pack-budget-mib holds the pack to what the payload has room for.
"""
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
import time
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
B = Path('/Users/xiaochoumao/unixnu-13t/build-userspace')
T = B / 'tree'
TOOLS = B / 'shader-cache-tools'
SC = B / 'shader-cache'
GEN = SC / 'gen'
WORK = SC / 'work'
OUT = SC / 'out'
LOGS = SC / 'logs'
MNT = SC / 'mnt'
PLUGIN = T / 'build/adreno-metal-plugin'
BUNDLE = PLUGIN / 'NativeAdrenoMetal.bundle'
KIT = T / 'drivers/AdrenoMetal'
A830 = Path('/Users/xiaochoumao/unixnu-13t/Adreno830')
ROOT_IMAGE = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out/oneplus13t/ipados-17.7.11-21H461/oneplus13t/userdata/userdata-root.img')
SYSTEMOS_DMG = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out/ipsw/21H461/044-76034-038.dmg')
OS_DMG = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out/ipsw/21H461/044-76040-038.dmg')
DEFAULT_BASE = T / 'build/data-a830-publish-gmem.dbrdisk'
DEFAULT_OUTPUT = T / 'build/data-a830-publish-gmem-cache.dbrdisk'
DEVICE_CACHE_DIR = '/private/var/mobile/Library/Caches/com.apple.backboardd/m2v'
CHIP_ID = 0x44050001
# Files of the cache directory that are the process's own journals, not cache entries: the tablet writes its own.
JOURNALS = {'pipelines.log', 'shadow.log', 'prime-current', 'prime.log', 'prime-bad'}
sys.path.insert(0, str(TOOLS))
import seed as seedtool  # noqa: E402


def log(*parts):
    print('[shader-cache]', *parts, flush=True)


def require(condition, reason):
    if not condition:
        raise SystemExit('FAIL: ' + reason)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def run(command, **kwargs):
    return subprocess.run([str(c) for c in command], check=True, **kwargs)


# ---------------------------------------------------------------- identities (read-only)
def a830_constant(name):
    text = (A830 / 'A830Regs.h').read_text()
    m = re.search(r'#define\s+' + name + r'\s+(0x[0-9A-Fa-f]+)', text)
    require(m, name + ' not found in Adreno830/A830Regs.h')
    return int(m.group(1), 16)


def runtime_identity():
    out = subprocess.run([sys.executable, '-I', TOOLS / 'runtime_id.py'], capture_output=True, text=True)
    facts = json.loads(out.stdout)
    require(out.returncode == 0 and facts['at_final'], 'translator checkout is not its pin plus the recorded patches')
    return facts


def host_digests():
    found = {}
    for output in (SC / 'bridge-host/target/aarch64-apple-darwin/release/build').glob('*/output'):
        for line in output.read_text().splitlines():
            m = re.fullmatch(r'cargo:rustc-env=(M2V_SOURCE_DIGEST|M2V_RUNTIME_SOURCE_ID)=([0-9a-f]{64})', line)
            if m:
                found.setdefault(m.group(1), set()).add(m.group(2))
    require(all(len(found.get(k, ())) == 1 for k in ('M2V_SOURCE_DIGEST', 'M2V_RUNTIME_SOURCE_ID')), 'host bridge build digests')
    return {k: next(iter(v)) for k, v in found.items()}


def this_build(digests):
    # metal2vulkan-runtime shader_cache/mod.rs this_build(): the runtime crate's version, the translator's ARTIFACT_FORMAT_VERSION and
    # SEMANTICS_VERSION (read from the checkout), then the two source digests.
    src = T / '.cache/gpu-reference/metal2vulkan/crates'
    version = re.search(r'^version\s*=\s*"([^"]+)"', (src / 'metal2vulkan-runtime/Cargo.toml').read_text(), re.M).group(1)
    cache_rs = (src / 'metal2vulkan/src/cache/mod.rs').read_text()
    artifact = int(re.search(r'pub const ARTIFACT_FORMAT_VERSION: u32 = (\d+);', cache_rs).group(1))
    semantics = int(re.search(r'pub const SEMANTICS_VERSION: u32 = (\d+);', cache_rs).group(1))
    h = hashlib.sha256(b'metal2vulkan shader cache build\0' + version.encode() + b'\0' + struct.pack('<I', artifact) + struct.pack('<I', semantics) +
                       digests['M2V_SOURCE_DIGEST'].encode() + digests['M2V_RUNTIME_SOURCE_ID'].encode())
    return h.hexdigest()


CONTENT_DIR = hashlib.sha256(b'metal2vulkan shader cache content\0').hexdigest()


def macho_uuid(path):
    out = subprocess.run([sys.executable, '-I', TOOLS / 'macho_uuid.py', path], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def turnip_uuid(lc_uuid, trap):
    out = subprocess.run([GEN / 'turnip_uuid', lc_uuid, hex(CHIP_ID), hex(trap)], capture_output=True, text=True, check=True)
    return out.stdout.strip()


# ---------------------------------------------------------------- host tools
def build_tools(runtime):
    LOGS.mkdir(parents=True, exist_ok=True)
    GEN.mkdir(parents=True, exist_ok=True)
    # The bridge for the host, from the tree's translator checkout (never patched here) with the tablet's NMB_RUNTIME_ID.
    bridge = SC / 'bridge-host'
    bridge.mkdir(parents=True, exist_ok=True)
    src = T / '.cache/gpu-reference/metal2vulkan'
    for fixture, ll in (('runtime_examples.air', 'crates/metal2vulkan-validation/fixtures/public/examples/runtime_examples.ll'),
                        ('function_constant.air', 'crates/metal2vulkan-validation/fixtures/public/kernel_function_constant_gate_fold.ll')):
        run(['/opt/homebrew/opt/llvm/bin/llvm-as', '-o', bridge / fixture, src / ll])
    manifest = (T / 'build/adreno-metal-bridge/Cargo.toml').read_text()
    require('crate-type = ["staticlib"]' in manifest, 'the tree bridge manifest')
    (bridge / 'Cargo.toml').write_text(manifest)
    shutil.copyfile(T / 'build/adreno-metal-bridge/Cargo.lock', bridge / 'Cargo.lock')
    env = dict(os.environ, NMB_RUNTIME_ID=runtime['runtime_id'], NMB_FIXTURE_AIR=str(bridge / 'runtime_examples.air'),
               NMB_FC_AIR=str(bridge / 'function_constant.air'), MACOSX_DEPLOYMENT_TARGET='14.0', IPHONEOS_DEPLOYMENT_TARGET='17.0',
               PATH='/opt/homebrew/opt/llvm/bin:/opt/homebrew/bin:' + os.environ['PATH'])
    with (LOGS / 'bridge-host.log').open('w') as out:
        r = subprocess.run(['cargo', '+1.98.0', 'build', '--release', '--offline', '--locked', '--manifest-path', str(bridge / 'Cargo.toml'),
                            '--target', 'aarch64-apple-darwin', '--target-dir', str(bridge / 'target')], env=env, stdout=out, stderr=subprocess.STDOUT)
    require(r.returncode == 0, 'host bridge build; see ' + str(LOGS / 'bridge-host.log'))
    run([sys.executable, '-I', TOOLS / 'build_turnip_host_release.py'])
    run(['/bin/bash', TOOLS / 'build_hostsim.sh'])
    flags = ['-std=c++17', '-fobjc-arc', '-O1', '-g', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-deprecated-declarations',
             '-Wno-unused-function', '-Wno-unused-variable', '-I', KIT]
    run(['xcrun', 'clang++', *flags, KIT / 'NativeBridgeMetalFrontend.mm', TOOLS / 'shader_cache_gen.mm',
         bridge / 'target/aarch64-apple-darwin/release/libadreno_metal_bridge.a', '-framework', 'Foundation', '-framework', 'Metal',
         '-framework', 'IOSurface', '-framework', 'CoreFoundation', '-o', GEN / 'shader_cache_gen'])
    run(['xcrun', 'clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Wno-unused-function', '-I', KIT, TOOLS / 'pack_import.cpp',
         '-o', GEN / 'pack_import'])
    blake = T / '.cache/mesa-work/src/util/blake3'
    run(['xcrun', 'clang', '-O1', '-DBLAKE3_NO_SSE2', '-DBLAKE3_NO_SSE41', '-DBLAKE3_NO_AVX2', '-DBLAKE3_NO_AVX512', '-DBLAKE3_USE_NEON=0',
         '-I', blake, TOOLS / 'turnip_uuid.c', blake / 'blake3.c', blake / 'blake3_dispatch.c', blake / 'blake3_portable.c',
         '-o', GEN / 'turnip_uuid'])
    run(['xcrun', 'clang', '-O1', '-I', T / '.cache/mesa-work/include', TOOLS / 'turnip_props.c', '-o', GEN / 'turnip_props'])
    # The bridge's own offline loader that forbids every Turnip compile (VK_PIPELINE_CREATE_FAIL_ON_PIPELINE_COMPILE_REQUIRED_BIT on each
    # vkCreate*Pipelines, NMB_TEST_FORCE_PRIME_STRICT=1), forwarding to the real Turnip: the strict consumer below.
    run(['xcrun', 'clang++', '-O1', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-dynamiclib', '-I', T / '.cache/mesa-work/include',
         KIT / 'bridge/priming_cache_loader.cpp', '-o', GEN / 'libStrictCacheLoader.dylib'])


def host_turnip(lc_uuid):
    """The host release Turnip with the tablet Turnip's LC_UUID (re-signed ad hoc), and the untouched one for the negative control."""
    built = SC / 'mesa-tu-host-release/src/freedreno/vulkan/libvulkan_freedreno.dylib'
    d = WORK / 'turnip'
    d.mkdir(parents=True, exist_ok=True)
    tablet, own = d / 'libvulkan_freedreno.dylib', d / 'host-identity/libvulkan_freedreno.dylib'
    own.parent.mkdir(exist_ok=True)
    shutil.copyfile(built, own)
    shutil.copyfile(built, tablet)
    run([sys.executable, '-I', TOOLS / 'macho_uuid.py', tablet, lc_uuid], capture_output=True)
    return tablet, own


def generator_env(seed_env):
    env = {'HOME': os.environ['HOME'], 'PATH': '/usr/bin:/bin', 'TMPDIR': os.environ.get('TMPDIR', '/tmp')}
    # The plugin's defaults (set with overwrite off, NativeAdrenoMetalPlugin.mm deviceForPort), then the seed's NativeAdrenoMetal.env
    # lines with the reader's rules (OwnerEnvironment.h: allowed prefixes, overwrite).
    env.update(TU_DEBUG='nocb', METAL2VULKAN_LOG_PIPELINES='1')
    for line in (seed_env or '').splitlines():
        name, eq, value = line.rstrip('\r\n').partition('=')
        if line.startswith('#') or not eq or not name or not name.startswith(('TU_', 'FD_', 'IR3_', 'NIR_', 'MESA_', 'METAL2VULKAN_')):
            continue
        env[name] = value
    # Host only: the kernel peer, strict selector coverage, and no memory pacing of the priming loop (it decides when, not what).
    env.update(DYLD_INSERT_LIBRARIES=str(SC / 'hostsim/libhostiokit.dylib'), NMB_DEVICE_BASE_NSOBJECT='1',
               METAL2VULKAN_PRIME_MAX_FOOTPRINT_MIB='1048576', METAL2VULKAN_PRIME_HEADROOM_MIB='0')
    return env


def turnip_props(dylib, env):
    out = subprocess.run([GEN / 'turnip_props', dylib], env=env, capture_output=True, text=True, timeout=120)
    m = re.search(r'vendor=0x([0-9a-f]+) device=0x([0-9a-f]+) driver=0x([0-9a-f]+) uuid=([0-9a-f]{32})', out.stdout)
    require(out.returncode == 0 and m, 'turnip_props: ' + out.stdout + out.stderr[-500:])
    return dict(vendor=int(m.group(1), 16), device=int(m.group(2), 16), driver=int(m.group(3), 16), uuid=m.group(4))


# ---------------------------------------------------------------- libraries
def hdiutil_mounts():
    info = plistlib.loads(subprocess.check_output(['hdiutil', 'info', '-plist']))
    out = []
    for image in info.get('images', []):
        points = [e['mount-point'] for e in image.get('system-entities', []) if e.get('mount-point')]
        out.append((image.get('image-path'), points, image))
    return out


def mount_source(image, mountpoint, section=None):
    """A read-only mount of image (or of a sector range of it): an existing one is reused and left alone; returns (path, attached_here)."""
    for path, points, _ in hdiutil_mounts():
        # Other sessions attach the same file through other paths (UniXNU/out-gpu/ipsw is a link to out/ipsw).
        if path and os.path.realpath(path) == os.path.realpath(image) and points:
            if section is None or str(mountpoint) in points:
                return Path(points[0]), False
    mountpoint.mkdir(parents=True, exist_ok=True)
    command = ['hdiutil', 'attach', '-readonly', '-noverify', '-noautofsck', '-nobrowse', '-owners', 'off', '-mountpoint', str(mountpoint)]
    if section:
        command += ['-imagekey', 'diskimage-class=CRawDiskImage', '-section', section]
    r = subprocess.run(command + [str(image)], capture_output=True, text=True)
    if r.returncode:
        return None, False
    return mountpoint, True


def detach(mountpoint):
    subprocess.run(['hdiutil', 'detach', str(mountpoint)], capture_output=True)


def resolve_sources():
    """The OS volume as the tablet mounts it (the flashed root image's OS window) and the SystemOS cryptex (its window is the IPSW's
    SystemOS dmg byte for byte, userdata-root.json); the IPSW OS dmg only when the root image cannot be attached."""
    attached = []
    manifest = json.loads(ROOT_IMAGE.with_suffix('.json').read_text())
    windows = {w['name']: w for w in manifest['windows']}
    os_window = windows['OS']
    section = '%d,%d' % (os_window['offset'] // 512, os_window['size'] // 512)
    os_mount, mine = mount_source(ROOT_IMAGE, MNT / 'root-os', section)
    os_source = 'flashed root image OS window'
    if not os_mount:
        os_mount, mine = mount_source(OS_DMG, MNT / 'os')
        os_source = 'IPSW OS dmg'
    require(os_mount, 'no OS volume')
    if mine:
        attached.append(os_mount)
    dmg = json.loads(Path(str(SYSTEMOS_DMG) + '.json').read_text())
    require(windows['SystemOS']['sha256'] == dmg['sha256'], 'the root image SystemOS window is not the IPSW SystemOS dmg')
    sys_mount, mine = mount_source(SYSTEMOS_DMG, MNT / 'sysos')
    require(sys_mount, 'no SystemOS volume')
    if mine:
        attached.append(sys_mount)
    return os_mount, sys_mount, os_source, attached


def select_libraries(primelist, which):
    # The device reads the list into a std::set (PrimeShaders.h readLines) and walks it in that order: sorted, without duplicates.
    paths = sorted(set(l for l in primelist.splitlines() if l.startswith('/')))
    if which == 'all':
        return paths
    if which == 'none':
        return []
    if which == 'quartzcore':
        return [p for p in paths if '/QuartzCore.framework/' in p]
    wanted = sorted(set(l.strip() for l in Path(which).read_text().splitlines() if l.strip()))
    missing = [p for p in wanted if p not in paths]
    require(not missing, 'not in the seed prime list: %s' % missing[:3])
    return wanted


def copy_libraries(paths, os_mount, sys_mount):
    """Copies (with their times) under work/libs/<device path>; returns prime-list lines host<TAB>device<TAB>size<TAB>mtime."""
    lines = []
    seen = set()
    for device in paths:
        if device.startswith('/System/Cryptexes/OS/'):
            source = sys_mount / device[len('/System/Cryptexes/OS/'):]
        else:
            source = os_mount / device.lstrip('/')
        st = source.stat()
        require(source.is_file(), 'not a file: ' + str(source))
        target = WORK / 'libs' / device.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target.exists() and target.stat().st_size == st.st_size and int(target.stat().st_mtime) == int(st.st_mtime)):
            shutil.copy2(source, target)
        # The device takes a library once by its file identity (st_dev, st_ino); a second path of the same file is the same library.
        identity = (st.st_dev, st.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        lines.append('%s\t%s\t%d\t%d' % (target, device, st.st_size, int(st.st_mtime)))
    return lines


# ---------------------------------------------------------------- generation
STATS_KEYS = ('primed', 'journal_made', 'journal_skipped', 'journal_refused', 'hits', 'misses', 'rejected', 'read_failures', 'writes', 'write_failures',
              'evictions', 'driver_loads', 'driver_hits', 'driver_misses', 'driver_saves', 'driver_truncated', 'driver_mismatched',
              'driver_unchanged')


def parse_stats(text):
    m = re.search(r'^STATS (.*)$', text, re.M)
    if not m:
        return None
    return {k: int(v) for k, v in (kv.split('=') for kv in m.group(1).split())}


def generate(cache, work, turnip, env, prime_lines, device_build, journal=None, libraries=(), expect_all_hits=False, label='produce'):
    """Runs the generator until every library is done (a crash moves the function being compiled to prime-bad, as the device does)."""
    work.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    totals = {k: 0 for k in STATS_KEYS}
    primed_lines = []
    remaining = list(prime_lines)
    journal_pending = journal is not None
    for attempt in range(30):
        if not remaining and not journal_pending and attempt:
            break
        listing = work / ('prime-list-%d.txt' % attempt)
        listing.write_text(''.join(l + '\n' for l in remaining))
        command = [GEN / 'shader_cache_gen', '--loader', turnip, '--cache', cache, '--device-build', device_build, '--work', work,
                   '--bad', work / 'prime-bad']
        if remaining:
            command += ['--prime-list', listing]
        if journal_pending:
            command += ['--journal', journal] + [x for lib in libraries for x in ('--library', lib)]
        if expect_all_hits:
            command.append('--expect-all-hits')
        started = time.time()
        r = subprocess.run([str(c) for c in command], env=env, capture_output=True, text=True, timeout=6 * 3600)
        (LOGS / ('%s-%d.log' % (label, attempt))).write_text(r.stdout + r.stderr)
        stats = parse_stats(r.stdout)
        done_now = re.findall(r'^PRIMED ([^\t]+)\t(.*)$', r.stdout, re.M)
        primed_lines += ['%s\t%s' % d for d in done_now]
        finished = {d[0] for d in done_now}
        remaining = [l for l in remaining if l.split('\t')[1] not in finished]
        if stats:
            for k in STATS_KEYS:
                totals[k] += stats.get(k, 0)
        log('%s run %d: exit %d in %.0f s, %d libraries done, %d left' % (label, attempt, r.returncode, time.time() - started,
                                                                         len(finished), len(remaining)))
        if r.returncode == 0:
            journal_pending = False
            if not remaining:
                return totals, primed_lines, r.stdout
            continue
        require(not expect_all_hits, label + ' failed: ' + (r.stdout + r.stderr)[-1500:])
        current = work / 'prime-current'
        if current.exists():
            bad = current.read_text()
            with (work / 'prime-bad').open('a') as f:
                f.write(bad)
            current.unlink()
            log('function that ended the process moved to prime-bad: ' + bad.strip())
        else:
            require(finished, label + ' failed without progress: ' + (r.stdout + r.stderr)[-1500:])
    raise SystemExit('FAIL: ' + label + ' did not finish')


# ---------------------------------------------------------------- device exports
def merge_export(cache, export, build_dir, identity, room=None):
    """Adds a device export's files (route B): translations of this runtime build, driver records of this driver identity (vendor,
    device, driverVersion, pipelineCacheUUID), and prime-done lines; everything else (other builds, journals) is left out.
    room: what the pack may still grow by, in compressed bytes, as a one-item list that this call lowers; None: no limit. A file that does
    not fit stays out: the device compiles it again, which is the cost and the only cost. The largest files go first: a compile's time grows
    faster than its shader's size (the stalls this pack is for are the long ones), and what a file costs here grows with it. The earlier
    of several exports goes first, so the flows that matter most belong in the first capture. Returns the record for the report, with
    the files taken (added_files, latest last), for take_back."""
    added, kept, skipped, chosen, left_out, left_bytes = 0, 0, [], [], [], 0
    fresh = []
    for path in sorted(p for p in export.rglob('*') if p.is_file()):
        rel = path.relative_to(export).as_posix()
        target = cache / rel
        if rel == 'prime-done':
            lines = set(target.read_text().splitlines()) if target.exists() else set()
            new = [l for l in path.read_text().splitlines() if l and l not in lines]
            with target.open('a') as f:
                f.write(''.join(l + '\n' for l in new))
            added += len(new)
            continue
        parts = rel.split('/')
        if len(parts) != 4 or parts[0] != 'v1' or parts[1] not in (build_dir, CONTENT_DIR):
            skipped.append(rel)
            continue
        data = path.read_bytes()
        if parts[1] == CONTENT_DIR and (data[:8] != b'M2VPPLC\0' or data[8:36] != identity or hashlib.sha256(data[:-32]).digest() != data[-32:]):
            skipped.append(rel)
            continue
        if target.exists():
            kept += 1
            continue
        fresh.append((len(zlib.compress(data, 9)), rel, target, data))
    for cost, rel, target, data in sorted(fresh, key=lambda f: (-f[0], f[1])):
        if room is not None and cost > room[0]:
            left_out.append(rel)
            left_bytes += len(data)
            continue
        if room is not None:
            room[0] -= cost
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        chosen.append(rel)
        added += 1
    return dict(export=str(export), added=added, already=kept, skipped=len(skipped), skipped_examples=skipped[:5], left_out=len(left_out),
                left_out_bytes=left_bytes, added_files=chosen)


def fit_pack(cache, pack, budget, merged):
    """The pack, within budget (bytes; None: as it comes). merge_export's costs are each file's own compression, the pack's stream compresses
    across files, so they rarely fall short: when they do, the last files taken leave again. What the production made itself never leaves:
    a baseline over the budget is refused here (a smaller --journal, or more room in the payload)."""
    facts = build_pack(cache, pack)
    while budget is not None and facts['pack_bytes'] > budget:
        last = next((m for m in reversed(merged) if m['added_files']), None)
        require(last, 'the pack without any export takes %d bytes, over the budget of %d' % (facts['pack_bytes'], budget))
        rel = last['added_files'].pop()
        (cache / rel).unlink()
        last['added'] -= 1
        last['left_out'] += 1
        facts = build_pack(cache, pack)
    return facts


# ---------------------------------------------------------------- pack
def cache_files(cache):
    files = []
    for path in sorted(p for p in cache.rglob('*') if p.is_file()):
        rel = path.relative_to(cache).as_posix()
        if rel in JOURNALS or rel.endswith('.tmp') or '/.probe.' in '/' + rel:
            continue
        files.append((rel, path))
    return files


def build_pack(cache, pack):
    """CacheExport.h's image pack: "SHEXP001", u32 stream bytes, u32 compressed bytes, then the zlib-compressed stream "M2VCACH1", u32
    root length, root, u32 file count, per file u32 path length, u32 mode, u32 uid, u32 gid, u64 size, path, bytes."""
    files = cache_files(cache)
    raw = bytearray(b'M2VCACH1')
    raw += struct.pack('<I', len(DEVICE_CACHE_DIR)) + DEVICE_CACHE_DIR.encode()
    raw += struct.pack('<I', len(files))
    for rel, path in files:
        data = path.read_bytes()
        raw += struct.pack('<IIIIQ', len(rel.encode()), 0o100600, 501, 501, len(data)) + rel.encode() + data
    require(len(raw) < 1 << 32, 'stream too large for a pack')
    compressed = zlib.compress(bytes(raw), 9)
    out = b'SHEXP001' + struct.pack('<II', len(raw), len(compressed)) + compressed
    require(len(out) <= 1 << 30, 'pack larger than the plugin reads (1 GiB)')
    pack.write_bytes(out)
    return dict(files=len(files), stream_bytes=len(raw), pack_bytes=len(out), sha256=hashlib.sha256(out).hexdigest())


def tree_bytes(root):
    return {rel: hashlib.sha256(path.read_bytes()).hexdigest() for rel, path in cache_files(root)}


def records(cache):
    """Every driver record (M2VPPLC) in the content directory: its identity."""
    found = []
    for path in (cache / 'v1' / CONTENT_DIR).rglob('*'):
        if path.is_file():
            b = path.read_bytes()
            require(b[:8] == b'M2VPPLC\0', 'non-record in the content directory: ' + str(path))
            vendor, device, driver = struct.unpack_from('<III', b, 8)
            found.append(dict(vendor=vendor, device=device, driver=driver, uuid=b[20:36].hex(),
                              checksum=hashlib.sha256(b[:-32]).digest() == b[-32:]))
    return found


# ---------------------------------------------------------------- main
def identify(base, report, skip_tools):
    """Step 1 and 2: what the seed holds, what the tablet derives from it, and the host tools that derive the same."""
    log('1/7 inputs and identities')
    base_facts = seedtool.inspect(base, WORK)
    base_facts.pop('addons')
    require(base_facts['publish'], 'the seed does not publish the GPU device (M8)')
    require(base_facts['plugin_sha256'] == sha256(BUNDLE / 'NativeAdrenoMetal'), 'the seed plugin is not the tree build (rebuild seeds)')
    require(base_facts['turnip_sha256'] == sha256(BUNDLE / 'libvulkan_freedreno.dylib'), 'the seed Turnip is not the tree build')
    trust = sha256(PLUGIN / 'owner-trust.bundle')
    runtime = runtime_identity()
    plugin_bytes = (BUNDLE / 'NativeAdrenoMetal').read_bytes()
    require(plugin_bytes.count(runtime['runtime_id'].encode() + b'\0') == 1, 'the plugin was not built from this translator source')
    ios_turnip_uuid = macho_uuid(BUNDLE / 'libvulkan_freedreno.dylib')
    require(ios_turnip_uuid == macho_uuid(T / 'build/mesa-tu-ios/src/freedreno/vulkan/libvulkan_freedreno.dylib'),
            'the bundle Turnip is not the current iOS Turnip build')
    device_build = runtime['runtime_id'] + '-' + ios_turnip_uuid
    trap = a830_constant('A830_UCHE_TRAP_ADDR')
    report.update(base_facts=base_facts, runtime=runtime, ios_turnip_lc_uuid=ios_turnip_uuid, device_build_id=device_build,
                  trust_bundle_sha256=trust)

    log('2/7 host tools')
    if not skip_tools or not all((GEN / n).exists() for n in ('shader_cache_gen', 'pack_import', 'turnip_uuid', 'turnip_props',
                                                                'libStrictCacheLoader.dylib')):
        build_tools(runtime)
    digests = host_digests()
    for name, value in digests.items():
        require(plugin_bytes.count(value.encode()) == 1, name + ' of the host bridge is not in the tablet plugin')
    build_dir = this_build(digests)
    layout = subprocess.run([sys.executable, '-I', TOOLS / 'layout_check.py', T / 'build/mesa-tu-ios', SC / 'mesa-tu-host-release',
                             SC / 'layout'], capture_output=True, text=True)
    require(layout.returncode == 0, 'IR3 record layout differs between the iOS and the host Turnip: ' + layout.stdout)
    hostsim = (B / 'hostsim-a830-kit/tests/hostsim/host_gpu_service.cpp').read_text()
    m = re.search(r'NativeAdrenoAbiDeviceInfo info=\{0x([0-9a-f]+)u,[^,]+,[^,]+,0x([0-9a-f]+)ull', hostsim)
    require(m and int(m.group(1), 16) == CHIP_ID and int(m.group(2), 16) == trap, 'the host kernel peer is not the A830 (chip, UCHE trap)')
    tablet_turnip, own_turnip = host_turnip(ios_turnip_uuid)
    env = generator_env(base_facts['env'])
    props = turnip_props(tablet_turnip, env)
    own_props = turnip_props(own_turnip, env)
    device_uuid = turnip_uuid(ios_turnip_uuid, trap)
    require(turnip_uuid(macho_uuid(own_turnip), trap) == own_props['uuid'], 'the UUID formula does not reproduce the host Turnip')
    require(props['uuid'] == device_uuid and props['device'] == CHIP_ID and props['vendor'] == 0x5143,
            'the host Turnip copy does not report the tablet identity')
    report.update(host_digests=digests, build_dir=build_dir, content_dir=CONTENT_DIR, layout=json.loads(layout.stdout),
                  device_identity=dict(props, uuid_formula=device_uuid), host_own_uuid=own_props['uuid'], env=env)
    log('   device build %s, pipelineCacheUUID %s (host Turnip copy %s), runtime build dir %s' % (device_build, device_uuid, props['uuid'],
                                                                                              build_dir[:16]))
    identity = struct.pack('<III', 0x5143, CHIP_ID, props['driver']) + bytes.fromhex(device_uuid)
    return dict(base_facts=base_facts, trust=trust, device_build=device_build, build_dir=build_dir, device_uuid=device_uuid, env=env,
                tablet_turnip=tablet_turnip, own_turnip=own_turnip, identity=identity)


def gather(ctx, selected, journal, keep_mounts, report):
    """Step 3: the libraries as the tablet sees them, copied with their times; prime-list lines for the selected ones."""
    log('3/7 libraries')
    primelist = ctx['base_facts']['primelist'] or ''
    require(primelist, 'the seed carries no NativeAdrenoMetal.primelist')
    embedded = set(l for l in (T / 'build/prime-list.txt').read_text().splitlines() if l)
    require(set(l for l in primelist.splitlines() if l) == embedded, 'the seed prime list differs from the plugin build list')
    quartzcore_paths = sorted(set(p for p in primelist.splitlines() if '/QuartzCore.framework/' in p))
    os_mount, sys_mount, os_source, attached = resolve_sources()
    try:
        # The journal's functions are looked up in QuartzCore's library first (the render server's), then in the primed ones.
        copied = copy_libraries(sorted(set(selected) | (set(quartzcore_paths) if journal else set())), os_mount, sys_mount)
    finally:
        if not keep_mounts:
            for mount in attached:
                detach(mount)
    prime_lines = [l for l in copied if l.split('\t')[1] in set(selected)]
    libraries = [str(WORK / 'libs' / p.lstrip('/')) for p in selected]
    quartzcore = [str(WORK / 'libs' / p.lstrip('/')) for p in quartzcore_paths]
    journal_libs = list(dict.fromkeys(quartzcore + libraries)) if journal else []
    report.update(os_source=os_source, libraries=len(prime_lines))
    log('   %d libraries from the %s and the SystemOS cryptex' % (len(prime_lines), os_source))
    return prime_lines, libraries or quartzcore, journal_libs


def check_pack(ctx, pack, files, prime_lines, journal, journal_libs, report, reference=None):
    """The device's view of a pack on the host: the plugin's own importer, then a consumer with the tablet identity that must find every
    entry (no compile, no write, no file rewritten), then the same requests under another Turnip identity, which must find none."""
    imported = WORK / 'imported'
    if imported.exists():
        shutil.rmtree(imported)
    imported.mkdir()
    r = subprocess.run([GEN / 'pack_import', pack, imported], capture_output=True, text=True)
    require(r.returncode == 0 and ('IMPORTED %d ' % files) in r.stdout, 'the plugin import refused the pack: ' + r.stdout + r.stderr)
    if reference is not None:
        require(tree_bytes(imported) == tree_bytes(reference), 'the imported tree differs from the generated cache')
    negative_dir = WORK / 'imported-negative'
    if negative_dir.exists():
        shutil.rmtree(negative_dir)
    shutil.copytree(imported, negative_dir)

    def identities(root):
        return {rel: (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_size) for rel, path in cache_files(root)}
    untouched, contents = identities(imported), tree_bytes(imported)
    for stale in ('prime-bad', 'prime-current'):
        (WORK / 'gen-consume' / stale).unlink(missing_ok=True)
    consumed, consumer_primed, _ = generate(imported, WORK / 'gen-consume', ctx['tablet_turnip'], ctx['env'], prime_lines, ctx['device_build'],
                                            journal=journal, libraries=journal_libs, expect_all_hits=True, label='consume')
    # Not one file written again, the helpers' priming record included (a making that misses rewrites its record by rename).
    require(identities(imported) == untouched and tree_bytes(imported) == contents, 'the consumer changed the cache (it compiled or wrote)')
    # Strict consumer: the same requests with every Turnip compile forbidden. The device opens (its helpers come from their record) and
    # every library makes exactly what the ordinary consumer made; with an empty directory the same loader cannot even open the device.
    strict_env = dict(ctx['env'], NMB_TEST_REAL_VULKAN_LOADER=str(ctx['tablet_turnip']), NMB_TEST_FORCE_PRIME_STRICT='1')
    strict_loader = GEN / 'libStrictCacheLoader.dylib'
    for stale in ('prime-bad', 'prime-current'):
        (WORK / 'gen-strict' / stale).unlink(missing_ok=True)
    strict, strict_primed, _ = generate(imported, WORK / 'gen-strict', strict_loader, strict_env, prime_lines, ctx['device_build'],
                                        journal=journal, libraries=journal_libs, expect_all_hits=True, label='strict')

    def summary(lines):
        return sorted((l.split('\t')[0], re.sub(r' (ms|waited)=\d+', '', l.split('\t')[1])) for l in lines)
    require(summary(strict_primed) == summary(consumer_primed) and strict['journal_made'] == consumed['journal_made'],
            'with Turnip compiles forbidden the consumer made less: an entry was missing')
    empty = WORK / 'strict-empty'
    if empty.exists():
        shutil.rmtree(empty)
    empty.mkdir()
    r = subprocess.run([str(GEN / 'shader_cache_gen'), '--loader', str(strict_loader), '--cache', str(empty)], env=strict_env,
                       capture_output=True, text=True, timeout=600)
    require(r.returncode != 0 and 'VkResultCode(1000297000)' in r.stdout, 'the strict loader did not forbid compiles (empty-cache control)')
    shutil.rmtree(empty)
    report['strict_stats'] = strict
    log('   strict consumer (Turnip compiles forbidden): the same %d pipelines made, device helpers from their record; '
        'empty-cache control refused with VK_PIPELINE_COMPILE_REQUIRED' % sum(int(m) for m in re.findall(r' made=(\d+)', ' '.join(strict_primed))))
    probe = [l for l in prime_lines if '/QuartzCore.framework/' in l][:1] or prime_lines[:1]
    negative, _, _ = generate(negative_dir, WORK / 'gen-negative', ctx['own_turnip'], ctx['env'], probe, ctx['device_build'],
                              journal=None if probe else journal, libraries=[] if probe else journal_libs, label='negative')
    require(negative['driver_loads'] == 0 and negative['driver_saves'] > 0, 'the negative control loaded records of another identity')
    shutil.rmtree(negative_dir)
    report.update(consume_stats=consumed, negative_stats=negative)
    log('   consumer: hits %d, misses %d (requests the translator refuses, never cached), writes %d, driver loads %d, driver saves %d; '
        'negative control: driver loads %d, saves %d' % (consumed['hits'], consumed['misses'], consumed['writes'], consumed['driver_loads'],
                                                         consumed['driver_saves'], negative['driver_loads'], negative['driver_saves']))
    return consumed


def check_seed(args):
    """--check-seed: the host checks of an existing seed variant (its pack, against the current tree's build)."""
    report = dict(checked=str(args.check_seed))
    ctx = identify(args.check_seed, report, True)
    facts = ctx['base_facts']
    require(facts['pack_present'], 'the seed carries no ' + seedtool.PACK_NAME)
    pack = OUT / 'checked.m2vpack'
    require(seedtool.extract(args.check_seed, seedtool.ADDONS + '/' + seedtool.PACK_NAME, pack, WORK), 'pack not readable')
    raw = zlib.decompress(pack.read_bytes()[16:])
    files = struct.unpack_from('<I', raw, 8 + 4 + struct.unpack_from('<I', raw, 8)[0])[0]
    imported = WORK / 'imported'
    if imported.exists():
        shutil.rmtree(imported)
    imported.mkdir()
    r = subprocess.run([GEN / 'pack_import', pack, imported], capture_output=True, text=True)
    require(r.returncode == 0, 'the plugin import refused the pack')
    done = [l.split('\t') for l in (imported / 'prime-done').read_text().splitlines()] if (imported / 'prime-done').exists() else []
    require(all(len(d) == 4 and d[3] == ctx['device_build'] for d in done), 'prime-done ids are not this build\'s')
    prime_lines, libraries, journal_libs = gather(ctx, sorted(d[0] for d in done), args.journal, args.keep_mounts, report)
    by_path = {l.split('\t')[1]: l.split('\t') for l in prime_lines}
    require(all(by_path[d[0]][2:4] == d[1:3] for d in done), 'prime-done size/mtime differ from the tablet\'s files')
    recs = records(imported)
    require(recs and all(r['checksum'] and (r['vendor'], r['device'], r['uuid']) == (0x5143, CHIP_ID, ctx['device_uuid']) for r in recs),
            'a driver record does not carry the tablet identity')
    log('6/7 host checks of ' + str(args.check_seed))
    check_pack(ctx, pack, files, prime_lines, args.journal, journal_libs, report)
    (OUT / 'check-report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    log('PASS: %s: %d files, %d driver records with the tablet identity, every entry found by the same derivation' % (
        args.check_seed, files, len(recs)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--libraries', default='quartzcore',
                    help='quartzcore (default: the render server\'s own library), all (the whole seed prime list), none, or a file of device paths')
    ap.add_argument('--prime-off', action='store_true',
                    help='also set METAL2VULKAN_PRIME=0 in the seed\'s NativeAdrenoMetal.env: no priming thread in backboardd at all')
    ap.add_argument('--journal', type=Path, help="a device pipelines.log to replay (R/S lines); functions from the listed libraries")
    ap.add_argument('--merge-export', type=Path, action='append', default=[],
                    help='a device export, <run>/ufs/export from gpu-loop.py or read-ufslog.py: its entries of this build and identity join the pack; '
                         'give the most important capture first')
    ap.add_argument('--pack-budget-mib', type=float,
                    help='the largest pack, in MiB; the exports leave out what does not fit, largest files first in (see merge_export)')
    ap.add_argument('--base', type=Path, default=DEFAULT_BASE)
    ap.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument('--force', action='store_true', help='replace an existing output seed')
    ap.add_argument('--replace-pack', action='store_true', help='the base seed carries a pack of an older runtime build: overwrite it')
    ap.add_argument('--keep-mounts', action='store_true')
    ap.add_argument('--skip-tools', action='store_true', help='use the host tools already built')
    ap.add_argument('--check-seed', type=Path, help='only the host checks of an existing seed variant')
    ap.add_argument('--with-cacheexport', action='store_true',
                    help='also add NativeAdrenoMetal.cacheexport (capture run: the kernel takes the export with sheng-log=1, BootProgress UfsLog.c)')
    ap.add_argument('--env-line', action='append', default=[], metavar='KEY=VALUE',
                    help='set a line of the seed\'s NativeAdrenoMetal.env (one the plugin reads: OwnerEnvironment.h), such as METAL2VULKAN_SURFACE_CLEAN=seed')
    args = ap.parse_args()
    for d in (WORK, OUT, LOGS, MNT):
        d.mkdir(parents=True, exist_ok=True)
    if args.check_seed:
        return check_seed(args)
    report = dict(base=str(args.base), output=str(args.output))
    ctx = identify(args.base, report, args.skip_tools)
    base_facts = ctx['base_facts']
    require(args.replace_pack or not base_facts['pack_present'], 'the base seed already carries a pack (--replace-pack)')
    selected = select_libraries(base_facts['primelist'] or '', args.libraries)
    prime_lines, libraries, journal_libs = gather(ctx, selected, args.journal, args.keep_mounts, report)

    # 4. generate
    log('4/7 generate')
    cache = WORK / 'cache'
    if cache.exists():
        shutil.rmtree(cache)
    for stale in ('prime-bad', 'prime-current'):
        (WORK / 'gen-produce' / stale).unlink(missing_ok=True)
    started = time.time()
    produced, primed, _ = generate(cache, WORK / 'gen-produce', ctx['tablet_turnip'], ctx['env'], prime_lines, ctx['device_build'],
                                   journal=args.journal, libraries=journal_libs, label='produce')
    report['produce_seconds'] = round(time.time() - started, 1)
    require(produced['write_failures'] == 0 and produced['rejected'] == 0, 'cache write failures in production')
    recs = records(cache)
    require(recs and all(r['checksum'] and (r['vendor'], r['device'], r['uuid']) == (0x5143, CHIP_ID, ctx['device_uuid']) for r in recs),
            'a driver record does not carry the tablet identity')
    require((cache / 'v1' / ctx['build_dir']).is_dir(), 'the runtime made another build directory than the tablet plugin derives')
    others = [d.name for d in (cache / 'v1').iterdir() if d.name not in (ctx['build_dir'], CONTENT_DIR)]
    require(not others, 'unexpected cache directories: %s' % others)
    bad = (WORK / 'gen-produce/prime-bad').read_text() if (WORK / 'gen-produce/prime-bad').exists() else ''
    report.update(produce_stats=produced, records=len(recs), primed=primed, prime_bad=bad.splitlines())
    pack = OUT / 'NativeAdrenoMetal.m2vpack'
    budget = int(args.pack_budget_mib * 2**20) if args.pack_budget_mib is not None else None
    room = [budget - build_pack(cache, pack)['pack_bytes']] if budget is not None else None
    require(room is None or room[0] >= 0, 'what the production made takes more than the pack budget')
    report['merged_exports'] = [merge_export(cache, export, ctx['build_dir'], ctx['identity'], room) for export in args.merge_export]

    # 5. pack
    log('5/7 pack')
    pack_facts = fit_pack(cache, pack, budget, report['merged_exports'])
    for merged in report['merged_exports']:
        log('   merged %s: %d added, %d already present, %d of other builds or identities, %d (%.1f MiB) over the budget' % (
            merged['export'], merged['added'], merged['already'], merged['skipped'], merged['left_out'], merged['left_out_bytes'] / 2**20))
        del merged['added_files']
    report['pack'] = pack_facts
    log('   %d files, %.2f MiB stream, %.2f MiB pack' % (pack_facts['files'], pack_facts['stream_bytes'] / 2**20, pack_facts['pack_bytes'] / 2**20))

    # 6. host checks of the pack (before the seed is written)
    log('6/7 host checks')
    # Journal replay (the route for a device pipelines.log): the production's own journal, replayed into an empty directory, must name
    # only entries the production made. A replay that derived other keys would show up here as names the production never wrote.
    host_journal = WORK / 'host-journal.log'
    host_journal.write_text((cache / 'pipelines.log').read_text() if (cache / 'pipelines.log').exists() else '')
    replay_lines = [l for l in host_journal.read_text().splitlines() if l.startswith(('R ', 'S ')) and ' ok=1 ' in l]
    if replay_lines:
        replay = WORK / 'replay'
        if replay.exists():
            shutil.rmtree(replay)
        replayed, _, _ = generate(replay, WORK / 'gen-replay', ctx['tablet_turnip'], ctx['env'], [], ctx['device_build'], journal=host_journal,
                                  libraries=libraries, label='replay')
        made, original = tree_bytes(replay), tree_bytes(cache)
        foreign = sorted(set(made) - set(original))
        require(replayed['journal_made'] == len(replay_lines) and replayed['journal_skipped'] == 0, 'journal replay skipped lines')
        require(not foreign, 'journal replay derived names the production did not write: %s' % foreign[:3])
        same = sum(1 for k in made if made[k] == original[k])
        report['journal_replay'] = dict(lines=len(replay_lines), files=len(made), identical_bytes=same, stats=replayed)
        log('   journal replay: %d lines -> %d files, all names among the production\'s, %d byte-identical' % (len(replay_lines), len(made), same))
        shutil.rmtree(replay)
    consumed = check_pack(ctx, pack, pack_facts['files'], prime_lines, args.journal, journal_libs, report, reference=cache)

    # 7. stage the seed variant and check it
    log('7/7 stage ' + str(args.output))
    if args.output.exists():
        require(args.force, 'the output seed exists (use --force)')
        args.output.unlink()
        args.output.with_suffix('.json').unlink(missing_ok=True)
    env_text = None
    wanted = (['METAL2VULKAN_PRIME=0'] if args.prime_off else []) + args.env_line
    if wanted:
        for line in args.env_line:
            name, eq, _ = line.partition('=')
            require(eq and name.startswith(('TU_', 'FD_', 'IR3_', 'NIR_', 'MESA_', 'METAL2VULKAN_')) and len(line) <= 254, 'an env line the plugin ignores: ' + line)
        names = tuple(l.partition('=')[0] + '=' for l in wanted)
        lines = [l for l in (base_facts['env'] or '').splitlines() if not l.startswith(names)]
        env_text = ''.join(l + '\n' for l in lines + wanted)
    extra = {'NativeAdrenoMetal.cacheexport': '1\n'} if args.with_cacheexport else {}
    before, after, metadata = seedtool.stage(args.base, args.output, pack, WORK, env_text, extra, replace_pack=args.replace_pack)
    added = sorted(set(after) - set(before))
    replaced = args.replace_pack and base_facts['pack_present']
    require(added == sorted(([] if replaced else ['addons/' + seedtool.PACK_NAME]) + ['addons/' + n for n in extra]),
            'the staged seed adds more than the pack: %s' % added)
    changed = sorted(k for k in before if before[k] != after[k])
    allowed = {'addons/' + seedtool.PACK_NAME} if replaced else set()
    if env_text is not None and env_text != base_facts['env']:
        allowed.add('addons/NativeAdrenoMetal.env')
    require(set(changed) <= allowed and (replaced or changed == sorted(allowed)), 'the staging changed a file of the base seed: %s' % changed)
    out_facts = seedtool.inspect(args.output, WORK)
    out_addons = out_facts.pop('addons')
    require(out_addons['addons/' + seedtool.PACK_NAME][4] == pack_facts['sha256'], 'the pack in the seed differs from the built pack')
    for key in ('turnip_mtime_ns', 'turnip_sha256', 'plugin_sha256', 'primelist', 'publish'):
        require(out_facts[key] == base_facts[key], 'the staged seed changed ' + key)
    require(out_facts['env'] == (env_text if env_text is not None else base_facts['env']), 'the staged seed env file')
    require(sha256(PLUGIN / 'owner-trust.bundle') == ctx['trust'], 'the owner trust bundle changed')
    packed = args.output.stat().st_size
    require(packed <= 256 * 1024**2, 'the packed seed exceeds DarwinBoot DBRDISK1 input limit (256 MiB)')
    kernel = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho')
    payload_estimate = (kernel.stat().st_size if kernel.exists() else 0) + packed + (PLUGIN / 'owner-trust.bundle').stat().st_size
    report.update(output_facts=out_facts, output_sha256=sha256(args.output), output_packed_bytes=packed, seed_metadata=metadata,
                  payload_estimate_bytes=payload_estimate, payload_limit_bytes=int(192 * 1024**2 * 0.95))
    log('   packed seed %.2f MiB (base %.2f MiB); payload files about %.1f MiB of %.1f MiB allowed (board size_mib 192)' % (
        packed / 2**20, args.base.stat().st_size / 2**20, payload_estimate / 2**20, 192 * 0.95))
    args.output.with_suffix('.json').write_text(json.dumps(dict(
        base_sha256=base_facts['seed_sha256'], output=metadata, sha256=report['output_sha256'], pack=pack_facts,
        pack_path='/private/var/ngpu/addons/' + seedtool.PACK_NAME, device_cache_dir=DEVICE_CACHE_DIR, device_build_id=ctx['device_build'],
        pipeline_cache_uuid=ctx['device_uuid'], runtime_build_dir=ctx['build_dir'], libraries=len(prime_lines),
        journal=str(args.journal) if args.journal else None, merged_exports=report['merged_exports'], prime_off=args.prime_off,
        cacheexport=args.with_cacheexport,
        env=out_facts['env'], produce_stats=produced, consume_stats=consumed, owner_trust_bundle_sha256=ctx['trust'],
        plugin=json.loads((PLUGIN / 'trust.json').read_text())), indent=2) + '\n')
    (OUT / 'report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    log('PASS: %s (pack %s, %d files); report %s' % (args.output, pack_facts['sha256'][:16], pack_facts['files'], OUT / 'report.json'))


if __name__ == '__main__':
    main()
