#!/usr/bin/env python3
"""Host checks of the extra --publish Data seeds made by stage-publish-env-seeds-a830.sh (no phone, no GPU, nothing written outside build/):

  1. trust  The plugin, Turnip and adrenoprobed in build/adreno-metal-plugin carry the CDHashes of trust.json, and those are exactly the own
            module of owner-trust.bundle (owner-trust.json, the bundle's sha256, each raw CDHash once in the bundle): the kernel trust cache
            inputs made from that bundle stay valid for every seed staged from these files.
  2. env    What Turnip's TU_DEBUG becomes for each env file, in the plugin's order: OwnerEnvironment.h applies the file (overwrite on), then the
            plugin's default setenv("TU_DEBUG", "nocb", 0) of NativeAdrenoMetalPlugin.mm and NativeTurnipSmoke.h runs (overwrite off), then
            Turnip parses getenv("TU_DEBUG") (tu_env_init_once). Built from the real sources: OwnerEnvironment.h, Mesa's parse_debug_string
            (src/util/u_debug.c) and Turnip's option table (tu_util.cc/tu_util.h) of the pinned tree. Expected: data-a830-publish sysmem,nocb;
            gmem nocb; noenv no flag; an empty env file or none at all nocb (why noenv carries "TU_DEBUG=").
  3. seeds  Each seed's json (publish, clients, launchd jobs, prime list, env, plugin CDHashes, base) and data-a830-publish's (clients, launchd
            jobs: the icon agent enabled and listed); mounted read-only it equals data-a830-publish.dbrdisk
            file for file (type, mode, content, symlink target; owners are not compared, -owners off) except NativeAdrenoMetal.env, which
            holds its env file; the staged plugin files are the build's files with the trusted CDHashes; same APFS volume UUID.
  0. clients (always first, no build needed) CLIENTS against the kernel (Adreno830/NativeAdrenoPublish.h): every name is granted by
            NativeAdrenoSandboxGrantList as the kernel compares it (zzw-native-adreno-sandbox-grant.patch: the first 16 characters of
            the process name), no name twice, names add-gpu-plugin-data.py accepts, the clients file within the 1023 bytes the plugin reads
            (NativeAdrenoMetalPlugin.mm listedClient); it reports the kernel's connection slots besides the render server's
            (NativeAdrenoMaxClients - 1): more listed clients than that cannot all hold a device at once; a process that opens while
            every slot is taken is refused (kIOReturnBusy), so it gets no device (MTLCreateSystemDefaultDevice nil) instead of waiting.

  check-env-seeds-a830.py [--trust-only] [NAME ...]      NAME: build/data-a830-publish-NAME.dbrdisk (default gmem noenv)
  check-env-seeds-a830.py --clients-only                  section 0 alone
"""
import sys
sys.dont_write_bytecode = True   # packed_ramdisk is imported from the UniXNU checkout through the tree's link
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import tempfile

B = Path('/Users/xiaochoumao/unixnu-13t/build-userspace')
T = B / 'tree'
BUILD = T / 'build'
PLUGIN = BUILD / 'adreno-metal-plugin'
KIT = T / 'drivers/AdrenoMetal'
MESA = T / '.cache/mesa-work/src'
REFERENCE = BUILD / 'data-a830-publish.dbrdisk'
VOLUME_UUID = '1726EAC0-2168-4118-AFD6-19E12FE306E7'   # the kernel's CONFIG_XNU_OWNER_DATA_VOLUME_UUID (build-userspace-a830.sh)
# build-userspace-a830.sh / stage-publish-env-seeds-a830.sh PUBLISH_CLIENTS: the widgets, PosterBoard, the icon agent and the poster extensions
# whose 21H461 code creates a Metal device (section 0 checks them against the kernel's grant list and slot count).
CLIENTS = ['WidgetRenderer_Default', 'PosterBoard', 'iconservicesagent', 'AegirPoster', 'KaleidoscopePoster', 'PhotosPosterProvider',
           'AmbientPhotoFramePosterProvider', 'MercuryPosterExtension', 'MonogramPosterExtension', 'HelloPosterExtension',
           'PridePosterExtension', 'RhizomePoster', 'ExtragalacticPoster', 'EmojiPosterExtension', 'AvatarPosterExtension', 'WeatherPoster',
           'LegacyPoster']
# The kernel's process lists and slot count (Adreno830, linked into the tree by build-userspace-a830.sh).
KERNEL_PUBLISH = T / 'drivers/Adreno830/NativeAdrenoPublish.h'
# The launchd jobs every --publish seed enables (add-gpu-plugin-data.py --enable-jobs; the base seed disables them): the icon agent.
ENABLED_JOBS = ['com.apple.iconservices.iconservicesagent']
# ... and disables (--disable-jobs): fairplayd.A2, relaunched every 10 s and exiting at once without a FairPlay backend.
DISABLED_JOBS = ['com.apple.fairplayd.A2']
ENV = 'ngpu/addons/NativeAdrenoMetal.env'
# Left by macOS on a volume mounted read-write (add-gpu-plugin-data.py stages on such a mount); differs between any two stagings.
MACOS_ONLY = {'.fseventsd', '.Spotlight-V100', '.Trashes', '.TemporaryItems', '.DocumentRevisions-V100'}
# Turnip's flags per env file (section 2); other names are only reported.
EXPECTED = {'data-a830-publish': 'sysmem,nocb', 'gmem': 'nocb', 'noenv': '', 'empty file': 'nocb', 'no file': 'nocb'}

sys.path.insert(0, str(T / 'drivers/DarwinBoot/Test'))
import packed_ramdisk  # noqa: E402


def fail(message):
    sys.exit('FAIL: ' + message)


def jobs_ok(report):
    # The seed's json records ENABLED_JOBS enabled and none of them left disabled.
    policy = report.get('launch_policy') or {}
    return policy.get('enabled') == ENABLED_JOBS and policy.get('disabled') == DISABLED_JOBS and policy.get('disabled_now') is not None \
        and not set(ENABLED_JOBS) & set(policy['disabled_now']) and set(DISABLED_JOBS) <= set(policy['disabled_now'])


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def cdhash(path):
    run = subprocess.run(['codesign', '--display', '--verbose=4', str(path)], capture_output=True, text=True)
    found = re.search(r'^CDHash=([0-9a-f]{40})$', run.stdout + run.stderr, re.M)
    return found.group(1) if found else None


# ---- 0. clients ----
def kernel_grant(names, name, capacity=17):
    """zzw-native-adreno-sandbox-grant.patch native_sandbox_grant: name as proc_selfname copies it into capacity bytes, against the entries."""
    name = name[:capacity - 1]
    for entry in names:
        compared = min(len(entry), capacity - 1)
        if entry and name[:compared] == entry[:compared] and (len(entry) >= capacity - 1 or len(name) == len(entry)):
            return True
    return False


def check_clients():
    header = KERNEL_PUBLISH.read_text()
    found = re.search(r'NativeAdrenoSandboxGrantList\[\]\s*=\s*((?:\s*"[^"]*")+)\s*;', header)
    slots = re.search(r'NativeAdrenoMaxClients\s*=\s*(\d+)', header)
    if not found or not slots:
        fail('NativeAdrenoSandboxGrantList or NativeAdrenoMaxClients not found in %s' % KERNEL_PUBLISH)
    granted = ''.join(re.findall(r'"([^"]*)"', found.group(1))).split(',')
    if len(set(CLIENTS)) != len(CLIENTS):
        fail('CLIENTS names a process twice: %s' % CLIENTS)
    if any(not name or not all(c.isalnum() or c in '._-' for c in name) for name in CLIENTS):
        fail('CLIENTS holds a name add-gpu-plugin-data.py --clients refuses (or a pattern the kernel grant cannot match): %s' % CLIENTS)
    refused = [name for name in CLIENTS if not kernel_grant(granted, name)]
    if refused:
        fail('the kernel does not grant %s (NativeAdrenoSandboxGrantList, %s): their own sandbox may refuse the GPU' % (refused, KERNEL_PUBLISH))
    rule = ['PosterBoard', 'WidgetRenderer_Default']   # the patch's rule on its own: whole short names, long ones over 16 characters
    if not (kernel_grant(rule, 'PosterBoard') and kernel_grant(rule, 'WidgetRenderer_Default') and kernel_grant(rule, 'WidgetRenderer_D')
            and not kernel_grant(rule, 'Poster') and not kernel_grant(rule, 'PosterBoardX') and not kernel_grant(rule, 'WidgetRenderer')):
        fail('kernel_grant does not follow the patch\'s rule')
    text = '\n'.join(CLIENTS) + '\n'   # add-gpu-plugin-data.py writes the file this way
    if len(text.encode()) > 1023:
        fail('NativeAdrenoMetal.clients would be %d bytes; the plugin reads 1023' % len(text.encode()))
    others = int(slots.group(1)) - 1
    print('PASS: clients: %d listed processes, every one granted by the kernel (%d entries, first 16 characters); clients file %d bytes; '
          '%d connection slots besides the render server%s' % (len(CLIENTS), len(granted), len(text.encode()), others,
                                                             '' if len(CLIENTS) <= others else
                                                             ' (more clients than slots: an open while every slot is taken gets no device)'))


# ---- 1. trust ----
TRUSTED = {'cdhash': 'NativeAdrenoMetal.bundle', 'turnip_cdhash': 'NativeAdrenoMetal.bundle/libvulkan_freedreno.dylib',
           'daemon_cdhash': 'adrenoprobed'}


def check_trust():
    trust = json.loads((PLUGIN / 'trust.json').read_text())
    owner = json.loads((PLUGIN / 'owner-trust.json').read_text())
    bundle = (PLUGIN / 'owner-trust.bundle').read_bytes()
    if hashlib.sha256(bundle).hexdigest() != owner['bundle_sha256']:
        fail('owner-trust.bundle is not the bundle of owner-trust.json')
    for key, relative in TRUSTED.items():
        actual = cdhash(PLUGIN / relative)
        if actual != trust[key]:
            fail('%s has CDHash %s, trust.json says %s' % (relative, actual, trust[key]))
        if bundle.count(bytes.fromhex(trust[key])) != 1:
            fail('CDHash %s of %s is not once in owner-trust.bundle' % (trust[key], relative))
    if sorted(owner['own_cdhashes']) != sorted(trust[key] for key in TRUSTED):
        fail('the own module of owner-trust.bundle is not the three CDHashes of trust.json')
    print('PASS: trust: plugin %s, Turnip %s and adrenoprobed %s are the own module of owner-trust.bundle %s'
          % (trust['cdhash'], trust['turnip_cdhash'], trust['daemon_cdhash'], owner['bundle_sha256']))
    return trust, owner['bundle_sha256']


# ---- 2. env ----
HELPER = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "OwnerEnvironment.h"
#define MAX2(a, b) ((a) > (b) ? (a) : (b))
struct debug_control {const char *string; uint64_t flag;};
%(parse)s
static const struct debug_control kOptions[] = {
%(table)s
   {NULL, 0},
};
// argv[1]: the env file ("-": none), argv[2]: the plugin's default for TU_DEBUG.
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    unsetenv("TU_DEBUG");
    const int applied = ownerenv::apply(strcmp(argv[1], "-") ? argv[1] : nullptr);   // initWithAcceleratorPort / adrenoprobed main
    setenv("TU_DEBUG", argv[2], 0);                                                   // deviceForPort / NativeTurnipSmoke: overwrite off
    const char *value = getenv("TU_DEBUG");                                            // tu_env_init_once: os_get_option is getenv
    const uint64_t flags = parse_debug_string(value, kOptions);
    printf("applied=%%d value=%%s flags=", applied, value ? value : "(unset)");
    const char *separator = "";
    for (const debug_control *option = kOptions; option->string; ++option)
        if (flags & option->flag) {printf("%%s%%s", separator, option->string); separator = ",";}
    printf("\n");
    return 0;
}
'''


def plugin_default():
    defaults, forced = set(), []
    for source in (KIT / 'NativeAdrenoMetalPlugin.mm', KIT / 'NativeTurnipSmoke.h'):
        text = source.read_text()
        defaults |= set(re.findall(r'setenv\("TU_DEBUG",\s*"([^"]*)",\s*0\)', text))
        forced += re.findall(r'setenv\("TU_DEBUG",\s*"[^"]*",\s*1\)', text)
    if len(defaults) != 1 or forced:
        fail('expected one TU_DEBUG default set with overwrite off in the plugin, found %s (overwrite on: %s)' % (defaults, forced))
    return defaults.pop()


def check_env(names, work):
    source = (MESA / 'util/u_debug.c').read_text()
    parse = re.search(r'^uint64_t\nparse_debug_string\(.*?^}\n', source, re.M | re.S)
    header = (MESA / 'freedreno/vulkan/tu_util.h').read_text()
    bits = dict(re.findall(r'(TU_DEBUG_\w+)\s*=\s*BITFIELD64_BIT\((\d+)\)', header))
    options = (MESA / 'freedreno/vulkan/tu_util.cc').read_text()
    options = options[options.index('tu_debug_options[] = {'):]
    entries = re.findall(r'\{\s*"(\w+)",\s*(TU_DEBUG_\w+)\s*\}', options[:options.index('};')])
    if not parse or not entries or any(flag not in bits for _, flag in entries):
        fail('parse_debug_string or the TU_DEBUG option table not found in the pinned Mesa tree')
    table = '\n'.join('   {"%s", 1ull << %s},' % (name, bits[flag]) for name, flag in entries)
    (work / 'helper.cpp').write_text(HELPER % {'parse': parse.group(0), 'table': table})
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-I', str(KIT), str(work / 'helper.cpp'), '-o', str(work / 'helper')], check=True)
    default = plugin_default()
    (work / 'empty.env').write_text('')
    cases = [('data-a830-publish', BUILD / 'NativeAdrenoMetal.env'), ('empty file', work / 'empty.env'), ('no file', '-')]
    cases += [(name, BUILD / ('NativeAdrenoMetal-%s.env' % name)) for name in names]
    for label, path in cases:
        if path != '-' and not Path(path).is_file():
            fail('no env file %s' % path)
        run = subprocess.run([str(work / 'helper'), str(path), default], capture_output=True, text=True, timeout=60)
        found = re.fullmatch(r'applied=(-?\d+) value=(.*) flags=(.*)\n', run.stdout)
        if run.returncode or not found:
            fail('env helper on %s: exit %d %s%s' % (label, run.returncode, run.stdout, run.stderr))
        flags = found.group(3)
        if label in EXPECTED and flags != EXPECTED[label]:
            fail('%s: Turnip would run with TU_DEBUG flags "%s", expected "%s"' % (label, flags, EXPECTED[label]))
        print('  env %-18s TU_DEBUG=%-14s -> Turnip flags: %s' % (label, '"%s"' % found.group(2), flags or '(none)'))
    drirc = (MESA / 'freedreno/vulkan/tu_drirc_gen.py').read_text()
    cb = re.search(r'B\("tu_allow_concurrent_binning",\s*(True|False)', drirc)
    if not cb or cb.group(1) != 'False' or 'tu_allow_concurrent_binning' in (MESA / 'util/00-mesa-defaults.conf').read_text():
        fail('concurrent binning is no longer off by default in this Mesa: nocb and no flag differ, recheck the noenv seed')
    print('PASS: env: plugin default TU_DEBUG=%s applies only when the file leaves TU_DEBUG unset; gmem keeps nocb without sysmem, noenv has no '
          'flag; concurrent binning is off by default (tu_allow_concurrent_binning False, no driconf entry), so nocb and none behave alike'
          % default)


# ---- 3. seeds ----
def attach(seed, work):
    """Decode the packed seed into a sparse raw file and attach it read-only; returns (mount point, whole-disk device, volume device)."""
    logical, extents = packed_ramdisk.regions(Path(seed).read_bytes())
    raw = work / (Path(seed).stem + '.raw')
    with raw.open('wb') as f:
        f.truncate(logical)
        for offset, data in extents:
            f.seek(offset)
            f.write(data)
    point = work / (Path(seed).stem + '.mount')
    point.mkdir()
    attached = plistlib.loads(subprocess.check_output(
        ['hdiutil', 'attach', '-readonly', '-noverify', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-owners', 'off',
         '-mountpoint', str(point), '-plist', str(raw)]))
    entities = attached['system-entities']
    volume = [e['dev-entry'] for e in entities if e.get('mount-point') and os.path.realpath(e['mount-point']) == os.path.realpath(point)]
    return point, entities[0]['dev-entry'], (volume[0] if volume else None), raw


def detach(device, raw):
    if subprocess.run(['hdiutil', 'detach', device], capture_output=True).returncode:
        subprocess.run(['hdiutil', 'detach', '-force', device], check=True, capture_output=True)
    raw.unlink()


def manifest(point):
    """Relative path -> (kind, mode, content sha256 or link target) for everything on the volume but macOS's own directories."""
    out = {}
    for directory, dirs, files in os.walk(point):
        relative = Path(directory).relative_to(point)
        if relative == Path('.'):
            dirs[:] = [d for d in dirs if d not in MACOS_ONLY]
            files = [f for f in files if f not in MACOS_ONLY]
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            key = str(relative / name)
            if stat.S_ISLNK(info.st_mode):
                out[key] = ('link', 0, os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                out[key] = ('dir', stat.S_IMODE(info.st_mode), '')
            elif not stat.S_ISREG(info.st_mode):
                out[key] = ('other', stat.S_IMODE(info.st_mode), str(stat.S_IFMT(info.st_mode)))
            else:
                try:
                    out[key] = ('file', stat.S_IMODE(info.st_mode), sha256(path))
                except PermissionError:
                    out[key] = ('file', stat.S_IMODE(info.st_mode), 'unreadable %d bytes' % info.st_size)
    return out


def volume_uuid(device):
    if not device:
        return None
    return plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', device])).get('VolumeUUID')


def mounted(seed, work, inspect):
    point, device, volume, raw = attach(seed, work)
    try:
        return manifest(point), volume_uuid(volume), inspect(point)
    finally:
        detach(device, raw)


def check_seeds(names, trust, work):
    reference_report = json.loads(REFERENCE.with_suffix('.json').read_text())
    if reference_report.get('clients') != CLIENTS or not jobs_ok(reference_report):
        fail('data-a830-publish.json does not record the clients %s with %s enabled' % (CLIENTS, ENABLED_JOBS))
    reference, reference_uuid, _ = mounted(REFERENCE, work, lambda point: None)
    if reference_uuid != VOLUME_UUID:
        fail('data-a830-publish.dbrdisk has volume UUID %s, not %s' % (reference_uuid, VOLUME_UUID))
    built = {str(Path('ngpu/addons') / path.relative_to(PLUGIN)): sha256(path)
             for path in (PLUGIN / 'NativeAdrenoMetal.bundle').rglob('*') if path.is_file()}
    built['ngpu/bin/adrenoprobed'] = sha256(PLUGIN / 'adrenoprobed')
    for name in names:
        seed = BUILD / ('data-a830-publish-%s.dbrdisk' % name)
        env = (BUILD / ('NativeAdrenoMetal-%s.env' % name)).read_text()
        report = json.loads(seed.with_suffix('.json').read_text())
        if not (report['publish'] is True and report['clients'] == CLIENTS and jobs_ok(report) and report['prime_list'] is True
                and report['diagnostics'] is False
                and report['env'] == env and report['plugin'] == trust and report['base_sha256'] == reference_report['base_sha256']
                and report['output']['encoded_sha256'] == sha256(seed)):
            fail('%s: its json does not record a --publish seed of this build with %s' % (seed.name, env.strip()))

        def inspect(point):
            return {relative: cdhash(point / 'ngpu/addons' / relative if relative != 'adrenoprobed' else point / 'ngpu/bin/adrenoprobed')
                    for relative in TRUSTED.values()}

        files, uuid, cdhashes = mounted(seed, work, inspect)
        changed = sorted(k for k in set(files) | set(reference) if files.get(k) != reference.get(k))
        if [k for k in changed if k != ENV]:
            fail('%s differs from data-a830-publish.dbrdisk beyond %s: %s' % (seed.name, ENV, changed[:20]))
        if files.get(ENV, (None, None, None))[2] != hashlib.sha256(env.encode()).hexdigest() or files[ENV][1] != 0o644:
            fail('%s: %s is not NativeAdrenoMetal-%s.env (0644)' % (seed.name, ENV, name))
        if any(files.get(path, (None, None, None))[2] != digest for path, digest in built.items()):
            fail('%s: the staged plugin or adrenoprobed is not the build\'s' % seed.name)
        if 'ngpu/addons/NativeAdrenoMetal.publish' not in files or 'ngpu/addons/NativeAdrenoMetal.diag' in files:
            fail('%s: publish marker missing or diagnostics marker present' % seed.name)
        if cdhashes != {TRUSTED[key]: trust[key] for key in TRUSTED}:
            fail('%s: staged CDHashes %s are not the trusted ones' % (seed.name, cdhashes))
        if uuid != reference_uuid:
            fail('%s: volume UUID %s, not %s' % (seed.name, uuid, reference_uuid))
        print('PASS: seed %s: --publish, equals data-a830-publish.dbrdisk in all %d entries but %s (%s), trusted CDHashes, volume %s, '
              'sha256 %s' % (seed.name, len(files), ENV, env.strip().splitlines()[-1], uuid, report['output']['encoded_sha256']))


def main():
    args = sys.argv[1:]
    trust_only = '--trust-only' in args
    check_clients()
    if '--clients-only' in args:
        return
    names = [a for a in args if a != '--trust-only'] or ['gmem', 'noenv']
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name) for name in names):
        sys.exit(__doc__)
    trust, _ = check_trust()
    if trust_only:
        return
    with tempfile.TemporaryDirectory(prefix='env-seed-check-', dir=str(BUILD)) as work:
        check_env(names, Path(work))
        check_seeds(names, trust, Path(work))


if __name__ == '__main__':
    main()
