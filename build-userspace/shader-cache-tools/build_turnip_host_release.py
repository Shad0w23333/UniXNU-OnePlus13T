#!/usr/bin/env python3
"""A host (macOS arm64) Turnip built from exactly the source and options of the tablet's (build/mesa-tu-ios), release with NDEBUG like it:
the shader compiler whose pipeline-cache records the tablet's Turnip will load must be the same code with the same struct layouts. Refuses
when the Mesa checkout is not the state the iOS build recorded (builtin-build-profile.json: source diff and untracked sources). Writes only
into build-userspace/shader-cache/mesa-tu-host-release."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

TREE = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/tree')
SOURCE = TREE / '.cache/mesa-work'
OUT = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/shader-cache/mesa-tu-host-release')
LOGS = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/shader-cache/logs')


def source_state():
    diff = subprocess.check_output(['git', '-C', str(SOURCE), 'diff', 'HEAD', '--binary'])
    untracked = subprocess.check_output(['git', '-C', str(SOURCE), 'ls-files', '--others', '--exclude-standard', '-z']).split(b'\0')
    extras = {n.decode(): hashlib.sha256((SOURCE / n.decode()).read_bytes()).hexdigest() for n in untracked if n and (SOURCE / n.decode()).is_file()}
    return hashlib.sha256(diff).hexdigest(), extras


def main():
    ios = json.loads((TREE / 'build/mesa-tu-ios/builtin-build-profile.json').read_text())
    if ios.get('builtin_pack_required') or ios.get('embedded_pack_sha256'):
        sys.exit('the iOS Turnip embeds a builtin pack; this host build does not model that')
    diff, extras = source_state()
    head = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    p = ios['profile']
    if head != p['mesa_revision'] or diff != p['source_diff_sha256'] or extras != p['extra_sources']:
        sys.exit('the Mesa checkout is not the source the iOS Turnip was built from (rebuild iOS Turnip first)')
    # The iOS options minus the cross file; the same release/NDEBUG and disabled optional dependencies.
    options = [o for o in p['options']]
    for required in ('-Dbuildtype=release', '-Db_ndebug=true', '-Dzstd=disabled', '-Dshader-cache=disabled'):
        if required not in options:
            sys.exit('unexpected iOS options: ' + required + ' missing')
    options += ['-Dtu-builtin-source-id=' + ios['source_id'], '-Dtu-builtin-pack-header=']
    env = {k: v for k, v in os.environ.items() if not k.startswith(('IR3_', 'FD_MESA_', 'NIR_', 'MESA_SHADER_', 'TU_'))}
    env['PATH'] = str(TREE / '.cache/mesa-tools/bin') + ':' + str(TREE / '.cache/mesa-venv/bin') + ':/opt/homebrew/opt/bison/bin:/opt/homebrew/bin:' + env['PATH']
    env.update(CC='/usr/bin/clang', CXX='/usr/bin/clang++', CFLAGS='', CXXFLAGS='', CPPFLAGS='', LDFLAGS='')
    meson = TREE / '.cache/mesa-venv/bin/meson'
    LOGS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if not (OUT / 'build.ninja').exists():
        command = [str(meson), 'setup', str(OUT), str(SOURCE), '--wrap-mode=nodownload'] + options
    else:
        command = [str(meson), 'configure', str(OUT)] + options
    with (LOGS / 'turnip-host-release-configure.log').open('w') as log:
        if subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT).returncode:
            sys.exit('configure failed; see ' + str(LOGS / 'turnip-host-release-configure.log'))
    with (LOGS / 'turnip-host-release-build.log').open('w') as log:
        if subprocess.run(['ninja', '-C', str(OUT), '-j12', 'src/freedreno/vulkan/libvulkan_freedreno.dylib'], env=env, stdout=log,
                          stderr=subprocess.STDOUT).returncode:
            sys.exit('build failed; see ' + str(LOGS / 'turnip-host-release-build.log'))
    library = OUT / 'src/freedreno/vulkan/libvulkan_freedreno.dylib'
    (OUT / 'host-release-profile.json').write_text(json.dumps(dict(mesa_revision=head, source_diff_sha256=diff, extra_sources=extras,
        options=options, ios_source_id=ios['source_id'], library_sha256=hashlib.sha256(library.read_bytes()).hexdigest()), indent=2) + '\n')
    print('PASS: host release Turnip from the iOS build source (%d bytes)' % library.stat().st_size)


if __name__ == '__main__':
    main()
