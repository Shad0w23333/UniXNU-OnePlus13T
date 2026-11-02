#!/usr/bin/env python3
"""compile-keystore.py [SOURCE] [OUTDIR]: compile NativeKeyStoreEvents.cpp exactly as the GPU tree's scripts/build-stage7-driver.py
does for the component NativeKeyStoreEvents (the IOKit compile record IOPlatformExpert.cpo.json through build-arm-platform.py's
compile_command, every -W option of the record dropped, then the Stage 7 warning policy -mgeneral-regs-only -Wall -Wextra -Werror
-Wno-unused-parameter -Wno-c99-designator -Wno-reorder-init-list -Wno-deprecated-enum-enum-conversion), but to OUTDIR (default: a
temporary directory): nothing is written into the out-gpu tree. Then the same object checks as the build (ARM64 MH_OBJECT, iOS 17
deployment), build-stage7-display.py's late staging (llvm-objcopy removes __mod_init_func and globalizes the static initializer;
the instructions must stay byte-identical), and every undefined symbol of the object looked up in the last linked kernel
(kernel/kernel.macho next to the tree), so a missing kernel symbol shows here and not at link time.
The environment is the project's kernel build environment (工具/make-a830-package.sh, PROGRESS.md "编译前"):
CCC_OVERRIDE_OPTIONS='# +-Wno-unnecessary-virtual-specifier', which silences only XNU's own OSSymbol.h under this clang.
Exit status 1 on an error, a warning, or a symbol the kernel lacks."""
import json, os, runpy, struct, subprocess, sys, tempfile
from pathlib import Path

TREE = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree')
KERNEL = TREE.parent / 'kernel/kernel.macho'
source = Path(sys.argv[1] if len(sys.argv) > 1 else
              '/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/NativeKeyStoreEvents/NativeKeyStoreEvents.cpp')
outdir = Path(sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix='keystore-compile-'))
outdir.mkdir(parents=True, exist_ok=True)
record = json.loads((TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                    .read_text().strip().rstrip(','))
compile_command = runpy.run_path(str(TREE / 'scripts/build-arm-platform.py'))['compile_command']
obj = outdir / 'NativeKeyStoreEvents.o'
arguments = iter(compile_command(record, source, obj))
command = []
for value in arguments:          # build-stage7-driver.py: keep XNU's ABI, drop its -W set
    if value == '-Xclang':
        option = next(arguments)
        if not option.startswith('-W'):
            command += [value, option]
    elif not value.startswith('-W'):
        command.append(value)
command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-I', str(outdir),
            '-I', str(TREE / '.cache/xnu-work/osfmk')]
env = dict(os.environ, CCC_OVERRIDE_OPTIONS='# +-Wno-unnecessary-virtual-specifier')
r = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True, env=env)
lines = r.stderr.splitlines()
warnings = [l for l in lines if ' warning: ' in l]
errors = [l for l in lines if ' error: ' in l]
print(f'{source.name}: rc {r.returncode} errors {len(errors)} warnings {len(warnings)}')
for l in lines:
    if ' warning: ' in l or ' error: ' in l:
        print('  ' + l)
if r.returncode:
    print('\n'.join(lines[:60]))
    sys.exit(1)
data = obj.read_bytes()
assert struct.unpack_from('<4I', data) == (0xfeedfacf, 0x100000c, 0, 1), 'Require ARM64 Mach-O object'
offset, deployment = 32, None
for _ in range(struct.unpack_from('<I', data, 16)[0]):
    cmd, size = struct.unpack_from('<II', data, offset)
    if cmd == 0x32:
        deployment = struct.unpack_from('<II', data, offset + 8)
    offset += size
assert deployment == (2, 0x110000), 'Require iOS 17 target'
print(f'  object {obj} ({len(data)} bytes): arm64 MH_OBJECT, iOS 17.0')

# build-stage7-display.py's late staging of this object.
sys.path.insert(0, str(TREE / 'kits/NativePrelink'))
from native_prelink import macho  # noqa: E402
llvm = Path(subprocess.check_output(['brew', '--prefix', 'llvm'], text=True).strip()) / 'bin'
late = outdir / 'NativeKeyStoreEvents.late.o'
subprocess.run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
                '--globalize-symbol=__GLOBAL__sub_I_NativeKeyStoreEvents.cpp', obj, late], check=True)
def text(raw):
    info = macho(raw)
    sec = next(s for g in info['segments'] for s in g['sections'] if s['name'] == '__text')
    return raw[sec['fileoff']:sec['fileoff'] + sec['size']]
assert text(data) == text(late.read_bytes()), 'Keystore late registration leaves instructions unchanged'
print('  late staging: __mod_init_func removed, initializer globalized, instructions unchanged')

nm = subprocess.run(['xcrun', 'nm', '-u', str(late)], capture_output=True, text=True, check=True).stdout.split()
undefined = sorted(set(nm))
missing = []
if KERNEL.exists():
    kernel_syms = set(subprocess.run(['xcrun', 'nm', '-gU', str(KERNEL)], capture_output=True, text=True).stdout.split())
    # The hooks Stage7Probe defines live in the same archive (Stage7Probe.o); everything else must be a kernel symbol.
    own = {'_native_keyevents_open_refused', '_native_aks_state_enabled', '_native_aks_lock_state', '_native_aks_call',
           '_native_aks_opened'}
    missing = [s for s in undefined if s not in kernel_syms and s not in own]
    print(f'  undefined symbols {len(undefined)}; absent from {KERNEL.name}: {missing if missing else "none"}')
else:
    print(f'  undefined symbols {len(undefined)} (no {KERNEL} to check against)')
print('  ' + ' '.join(undefined))
sys.exit(1 if warnings or missing else 0)
