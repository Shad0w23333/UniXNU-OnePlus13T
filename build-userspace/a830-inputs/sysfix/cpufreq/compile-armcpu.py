#!/usr/bin/env python3
"""compile-armcpu.py [--strict] [SOURCE] [OUTDIR]: compile PaganiArmCpu/ArmCpu.cpp (or SOURCE) exactly as the GPU tree's
scripts/build-arm-platform.py does (the IOKit compile record IOPlatformExpert.cpo.json, its own -Weverything set, nothing added),
but to a throwaway object in OUTDIR (default: $TMPDIR). Nothing is written into the out-gpu tree. Prints the warning and error
counts and every diagnostic line; exit status 1 on an error or any warning.
--strict: the recipe of the tree's scripts/build-arm-driver.py instead (the record's -W flags dropped, -Wall -Wextra -Werror and
its -Wno list added, -I .cache/xnu-work/osfmk)."""
import json, os, subprocess, sys, tempfile
TREE = '/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree'
RECIPE = TREE + '/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
argv = [a for a in sys.argv[1:] if a != '--strict']
strict = len(argv) != len(sys.argv) - 1
source = argv[0] if len(argv) > 0 else '/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/drivers/PaganiArmCpu/ArmCpu.cpp'
outdir = argv[1] if len(argv) > 1 else tempfile.gettempdir()
record = json.loads(open(RECIPE).read().strip().rstrip(','))
obj = os.path.join(outdir, 'ArmCpu-check.o')
args, it = [], iter(record['arguments'])
for value in it:
    if value == record['file']:
        args.append(source)
    elif value == '-o':
        next(it)
        args += ['-o', obj]
    elif value in ('-MF', '-MJ', '-MT', '-MQ'):
        next(it)
    elif strict and value == '-Xclang':
        following = next(it)
        if not following.startswith('-W'):
            args += [value, following]
    elif strict and value.startswith('-W'):
        pass
    elif value not in ('-MD', '-MMD', '-MP'):
        args.append(value)
if strict:
    args += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
             '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier', '-I', TREE + '/.cache/xnu-work/osfmk']
r = subprocess.run(args, cwd=record['directory'], capture_output=True, text=True)
lines = r.stderr.splitlines()
warnings = [l for l in lines if ' warning: ' in l]
errors = [l for l in lines if ' error: ' in l]
print(f'{os.path.basename(source)}: rc {r.returncode} errors {len(errors)} warnings {len(warnings)} object {obj if r.returncode == 0 else "-"}')
for l in lines:
    if ' warning: ' in l or ' error: ' in l:
        print('  ' + l)
if r.returncode == 0:
    with open(obj, 'rb') as f:
        head = f.read(16)
    import struct
    print('  mach-o', 'arm64 MH_OBJECT' if struct.unpack('<4I', head) == (0xfeedfacf, 0x100000c, 0, 1) else 'UNEXPECTED ' + head.hex())
sys.exit(1 if r.returncode or warnings else 0)
