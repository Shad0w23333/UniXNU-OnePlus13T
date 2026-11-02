#!/usr/bin/env python3
"""Stage the owner-built Adreno Metal plugin into a copy of a packed Data seed.

  add-gpu-plugin-data.py [--publish] [--diag] [--clients NAME,...] [--env FILE] [--prime-list FILE]
                         [--enable-jobs LABEL,...] [--disable-jobs LABEL,...] BASE.dbr OUTPUT.dbr

With --publish the seed also carries /private/var/ngpu/addons/NativeAdrenoMetal.publish: the plugin then returns the native Metal device to the original
discovery in backboardd (NativeAdrenoMetalPlugin.mm), so the render server composites through it. Without the file the plugin only runs its tests.
With --clients the seed also carries /private/var/ngpu/addons/NativeAdrenoMetal.clients: the processes besides backboardd that get their own native
Metal device ("*" = every process, "prefix*", @apps = every process in an .app bundle, @extensions = every one in an .appex, "!token" leaves out
what token names; drivers/AdrenoMetal/ClientRule.h); any other process keeps the software renderer.
--enable-jobs and --disable-jobs edit the seed's launchd policy (/private/var/db/com.apple.xpc.launchd/disabled.plist, written by the device
repository's prepare-ram-data.py; label -> true keeps a job from loading), in place, so the file keeps its owner and mode. A job to enable must be
disabled there. The icons need both: the A830 base seed disables com.apple.iconservices.iconservicesagent, the only process that renders app
icons (IconServices clients such as SpringBoard send it every image request and draw the generic placeholder when the request fails), and the
agent composites them with Core Image, which needs a native Metal device: without one it falls back to an OpenGL ES context that does not
exist here and the agent can abort (int-155). So a --publish seed enables the job and lists iconservicesagent in --clients (the kernel
already grants it the GPU user client: NativeSandboxGrantProcess).
With --env FILE the seed carries NativeAdrenoMetal.env: KEY=VALUE lines the plugin sets in every process that loads it, before Turnip starts
(only TU_, FD_, IR3_, NIR_, MESA_ and METAL2VULKAN_ names; the first A830 boots use TU_DEBUG=sysmem,nocb). It is data, so changing it needs no
rebuild and no new trust bundle. With --prime-list FILE the seed carries NativeAdrenoMetal.primelist, the OS image's .metallib paths that
shader priming walks instead of scanning directories. With --diag the seed carries NativeAdrenoMetal.diag, which turns on the render
server's telemetry thread and display-state probe (off otherwise: they cost the render server).

Adds /private/var/ngpu/addons/NativeAdrenoMetal.bundle, /private/var/ngpu/bin/adrenoprobed and /private/var/ngpu/daemons/*.plist (from build/adreno-metal-plugin, see build-adreno-metal-plugin.py). The
kernel's boot-time addon mount (sheng-gpu-addon=1) presents that directory as a read-only union view over
/System/Library/Extensions, which is where the original Metal discovery looks for MetalPluginName. BASE is not modified and the
volume UUID is unchanged. Nothing is written to Git or to the OS inputs."""
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'drivers/DarwinBoot/Test'))
import packed_ramdisk

BUNDLE = ROOT / 'build/adreno-metal-plugin/NativeAdrenoMetal.bundle'


def take(args, flag):
    # --flag VALUE, removed from args; None when absent.
    if flag not in args:
        return None
    at = args.index(flag)
    if at + 1 >= len(args):
        sys.exit(flag + ' takes a file')
    value = args[at + 1]
    del args[at:at + 2]
    if not Path(value).is_file():
        sys.exit(flag + ': no such file: ' + value)
    return value


ENV_PREFIXES = ('TU_', 'FD_', 'IR3_', 'NIR_', 'MESA_', 'METAL2VULKAN_')


def check_env(path):
    # The same rules as the plugin's reader (NativeAdrenoMetalPlugin.mm applyOwnerEnvironment): a line the plugin would ignore is refused
    # here, so a typo does not silently leave the setting out.
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line or line.startswith('#'):
            continue
        name, equals, _ = line.partition('=')
        if not equals or not name or not name.startswith(ENV_PREFIXES) or len(line) > 254:
            sys.exit('%s:%d: not a KEY=VALUE line the plugin accepts (%s prefixes): %s' % (path, number, '/'.join(ENV_PREFIXES), line))


# launchd's override database of the system domain, relative to the Data volume (/private/var).
LAUNCH_POLICY = 'db/com.apple.xpc.launchd/disabled.plist'
ICON_AGENT, ICON_AGENT_JOB = 'iconservicesagent', 'com.apple.iconservices.iconservicesagent'


def take_labels(args, flag):
    # --flag LABEL,... (launchd job labels), removed from args; [] when absent.
    if flag not in args:
        return []
    at = args.index(flag)
    labels = [label for label in args[at + 1].split(',') if label] if at + 1 < len(args) else []
    del args[at:at + 2]
    if not labels or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', label) for label in labels):
        sys.exit(flag + ' takes launchd job labels separated by commas')
    return labels


def edit_launch_policy(data, enable, disable):
    """The launchd policy (the bytes of LAUNCH_POLICY) with each label of enable removed and each label of disable set to true: its new bytes,
    in the same plist format, and the labels it leaves disabled. A label to enable must be disabled in it, so a typo does not pass unnoticed."""
    if set(enable) & set(disable):
        sys.exit('A job cannot be enabled and disabled at once: ' + ','.join(sorted(set(enable) & set(disable))))
    policy = plistlib.loads(data)
    if not isinstance(policy, dict):
        sys.exit(LAUNCH_POLICY + ' is not a dictionary')
    for label in enable:
        if policy.get(label) is not True:
            sys.exit('--enable-jobs: %s is not disabled in the seed\'s %s' % (label, LAUNCH_POLICY))
        del policy[label]
    for label in disable:
        policy[label] = True
    fmt = plistlib.FMT_BINARY if data.startswith(b'bplist') else plistlib.FMT_XML
    return plistlib.dumps(policy, fmt=fmt), sorted(label for label, value in policy.items() if value is True)


def client_token(token):
    # One token of NativeAdrenoMetal.clients as drivers/AdrenoMetal/ClientRule.h reads it: a process name or "prefix*" (letters, digits,
    # '.', '_', '-', '*'), one of the classes @apps and @extensions, or either of those after '!' (an exclusion).
    body = token[1:] if token.startswith('!') else token
    return body in ('@apps', '@extensions') or (bool(body) and all(c.isalnum() or c in '._-*' for c in body))


def icon_notes(publish, clients, disabled_jobs):
    # What the seed leaves the icons with (see the module description); empty when nothing is wrong.
    listed = bool(clients) and (ICON_AGENT in clients or '*' in clients) and '!' + ICON_AGENT not in clients
    if ICON_AGENT_JOB in disabled_jobs:
        return ['%s stays disabled: every app icon is the generic placeholder (--enable-jobs %s)' % (ICON_AGENT_JOB, ICON_AGENT_JOB)]
    if publish and not listed:
        return ['%s runs without a native Metal device: Core Image falls back to OpenGL ES, which does not exist here, and the agent can '
                'abort (--clients ...,%s)' % (ICON_AGENT, ICON_AGENT)]
    return []


def main():
    args = [a for a in sys.argv[1:] if a not in ('--publish', '--diag')]
    publish = '--publish' in sys.argv[1:]
    diag = '--diag' in sys.argv[1:]
    # --env FILE: the owner's KEY=VALUE tuning beside the bundle (NativeAdrenoMetal.env, read by the plugin before Turnip starts).
    # --prime-list FILE: the OS image's .metallib paths (NativeAdrenoMetal.primelist), so priming never scans directories.
    env = take(args, '--env')
    prime = take(args, '--prime-list')
    if env:
        check_env(env)
    # --enable-jobs / --disable-jobs LABEL,...: launchd jobs the seed's policy (LAUNCH_POLICY) lets load or keeps from loading.
    enable_jobs = take_labels(args, '--enable-jobs')
    disable_jobs = take_labels(args, '--disable-jobs')
    if set(enable_jobs) & set(disable_jobs):
        sys.exit('A job cannot be enabled and disabled at once: ' + ','.join(sorted(set(enable_jobs) & set(disable_jobs))))
    clients = None
    if '--clients' in args:
        at = args.index('--clients')
        clients = [name for name in args[at + 1].split(',') if name]
        del args[at:at + 2]
        if not clients or any(not client_token(name) for name in clients):
            sys.exit('--clients takes process names (prefix*, @apps, @extensions, !token) separated by commas')
    if len(args) != 2:
        sys.exit(__doc__)
    base, output = Path(args[0]).resolve(), Path(args[1]).resolve()
    if output.exists():
        sys.exit('Refuse to overwrite ' + str(output))
    if not (BUNDLE / 'NativeAdrenoMetal').is_file():
        sys.exit('Build the plugin first: python3 scripts/build-adreno-metal-plugin.py')
    blob = base.read_bytes()
    logical, extents = packed_ramdisk.regions(blob)
    with tempfile.TemporaryDirectory(prefix='data-seed-', dir=str(ROOT / 'build')) as td:
        raw = Path(td) / 'seed.raw'
        with raw.open('wb') as f:
            f.truncate(logical)
            for offset, data in extents:
                f.seek(offset)
                f.write(data)
        mount = Path(td) / 'mount'
        mount.mkdir()
        attached = plistlib.loads(subprocess.check_output(
            ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse', '-owners', 'off',
             '-mountpoint', str(mount), '-plist', str(raw)]))
        device = attached['system-entities'][0]['dev-entry']
        try:
            if not (mount / 'mobile').is_dir():
                sys.exit('The Data seed has no /mobile; wrong base image?')
            policy = mount / LAUNCH_POLICY
            disabled_jobs = None   # what the seed's launchd policy keeps from loading; None: the seed has no policy file
            if policy.is_file():
                edited, disabled_jobs = edit_launch_policy(policy.read_bytes(), enable_jobs, disable_jobs)
                if enable_jobs or disable_jobs:
                    with policy.open('r+b') as f:   # in place: same inode, so the owner (root) and the mode stay
                        f.write(edited)
                        f.truncate()
            elif enable_jobs or disable_jobs:
                sys.exit('The Data seed has no %s (the device repository\'s prepare-ram-data.py writes it)' % LAUNCH_POLICY)
            print('launch policy: enabled %s, disabled %s; disabled now: %s' % (enable_jobs, disable_jobs, disabled_jobs))
            target = mount / 'ngpu/addons/NativeAdrenoMetal.bundle'
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True)
            for directory in (mount / 'ngpu', mount / 'ngpu/addons'):
                os.chmod(directory, 0o755)
            shutil.copytree(BUNDLE, target, symlinks=False)
            if clients:
                (mount / 'ngpu/addons/NativeAdrenoMetal.clients').write_text('\n'.join(clients) + '\n')
                os.chmod(mount / 'ngpu/addons/NativeAdrenoMetal.clients', 0o644)
            for name, source in (('NativeAdrenoMetal.env', env), ('NativeAdrenoMetal.primelist', prime)):
                extra = mount / 'ngpu/addons' / name
                if extra.exists():
                    extra.unlink()
                if source:
                    shutil.copyfile(source, extra)
                    os.chmod(extra, 0o644)
            diag_marker = mount / 'ngpu/addons/NativeAdrenoMetal.diag'
            if diag:
                diag_marker.write_text('1\n')
                os.chmod(diag_marker, 0o644)
            elif diag_marker.exists():
                diag_marker.unlink()
            marker = mount / 'ngpu/addons/NativeAdrenoMetal.publish'
            if publish:
                marker.write_text('1\n')
                os.chmod(marker, 0o644)
            daemon_dir, bin_dir = mount / 'ngpu/daemons', mount / 'ngpu/bin'
            for directory in (daemon_dir, bin_dir):
                if directory.exists():
                    shutil.rmtree(directory)
                directory.mkdir(parents=True)
                os.chmod(directory, 0o755)
            shutil.copyfile(BUNDLE.parent / 'adrenoprobed', bin_dir / 'adrenoprobed')
            os.chmod(bin_dir / 'adrenoprobed', 0o755)
            for plist in sorted(BUNDLE.parent.glob('*.plist')):
                if plist.name.endswith('-entitlements.plist'):
                    continue   # build input, not a launchd job
                shutil.copyfile(plist, daemon_dir / plist.name)
                os.chmod(daemon_dir / plist.name, 0o644)
            if publish:
                # The render server (backboardd) picks a 10-bit "deep" framebuffer unless told not to; the preference is read when the process
                # starts, before any plugin code can run, so the seed carries it for the process's user (mobile) and for root.
                # Both the process's own domain and the global one (the lookup falls back to it), in case the process name is not the domain.
                for home, name in [(h, n) for h in ('mobile', 'root') for n in ('backboardd.plist', '.GlobalPreferences.plist')]:
                    directory = mount / home / 'Library/Preferences'
                    directory.mkdir(parents=True, exist_ok=True)
                    file = directory / name
                    values = plistlib.loads(file.read_bytes()) if file.exists() else {}
                    values.update({'CAEnableDeepFramebuffer': False, 'CAEnableFramebufferCompression': False,
                                   'CAEnableIOSurfaceCompression': False})
                    file.write_bytes(plistlib.dumps(values, fmt=plistlib.FMT_BINARY))
                    os.chmod(file, 0o644)
            for path in [target, *target.rglob('*')]:
                os.chmod(path, 0o755 if path.is_dir() or path.name in ('NativeAdrenoMetal', 'libvulkan_freedreno.dylib') else 0o644)
            print('staged', target.relative_to(mount), sorted(p.name for p in target.iterdir()))
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True)
        if raw.read_bytes()[32:36] != b'NXSB':
            sys.exit('The edited image is no APFS container any more')
        metadata = packed_ramdisk.pack(raw, output)
    notes = icon_notes(publish, clients, disabled_jobs) if disabled_jobs is not None else []
    report = dict(base_sha256=packed_ramdisk.inspect(base)[0]['sha256'], output=metadata, publish=publish, clients=clients,
                  env=Path(env).read_text() if env else None, prime_list=bool(prime), diagnostics=diag,
                  launch_policy=dict(enabled=enable_jobs, disabled=disable_jobs, disabled_now=disabled_jobs), icon_notes=notes,
                  plugin=json.loads((ROOT / 'build/adreno-metal-plugin/trust.json').read_text()))
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    for note in notes:
        print('NOTE: ' + note, file=sys.stderr)


if __name__ == '__main__':
    main()
