#!/usr/bin/env python3
"""compile-platform.py [OUTDIR [ARMDT_SOURCE]]: compile PaganiArmDTPlatform/ArmDTPlatform.cpp (or the copy ARMDT_SOURCE, its headers
beside it) and PaganiArmCpu/ArmCpu.cpp the two ways the GPU tree can: (1) "platform", exactly as scripts/build-arm-platform.py does for the 13T kernel (its compile_command on the IOKit record
IOPlatformExpert.cpo.json: the record's own -Weverything set, nothing added or removed), and (2) "strict", the recipe of the tree's
scripts/build-arm-driver.py (the record's -W flags dropped; -Wall -Wextra -Werror with its -Wno list; -I .cache/xnu-work/osfmk).
Objects go to OUTDIR (default: a fresh temporary directory); nothing is written into the out-gpu tree and no archive is built.

Pass rule (exit 0): both files compile in both recipes, and neither recipe gives a warning located in a driver's own files (its .cpp
and the headers beside it) except the baseline ones listed in BASELINE (warnings inside XNU's own headers, which every IOKit file of
this record gets, are counted and printed; they fail only the strict recipe). Exit 1 otherwise. The two files are linked into the same
kernel and built by the same script, so the check always covers both (PaganiArmCpu belongs to the sysfix workflow)."""
import json, os, re, runpy, struct, subprocess, sys, tempfile
TREE = '/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree'
RECIPE = TREE + '/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
STRICT = ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
          '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier', '-I', TREE + '/.cache/xnu-work/osfmk']
# Warnings in the driver sources that predate this workflow (text after "warning: "): both drivers' OSDefineMetaClassAndStructors line,
# and ArmCpu.cpp's extern "C" diagnostics without prototypes and two sign conversions of the strict recipe (lines 930 and 935 on
# 2026-10-31), which belong to PaganiArmCpu.
METACLASS = [r"nested designators are a C99 extension", r"ISO C\+\+ requires field designators"]
BASELINE = {
    'ArmCpu.cpp': METACLASS + [r"no previous prototype for function 'armcpu_(gic_diag|sgi_selfprobe|ipi_selftest|spi_attach|spi_count)'",
                               r"implicit conversion changes signedness"],
    'ArmDTPlatform.cpp': METACLASS,
}
compile_command = runpy.run_path(TREE + '/scripts/build-arm-platform.py')['compile_command']
record = json.loads(open(RECIPE).read().strip().rstrip(','))


def strict_command(source, obj):
    arguments, command, i = record['arguments'], [], 0
    while i < len(arguments):
        value = arguments[i]
        if value == record['file']:
            command.append(source)
        elif value == '-o':
            command += ['-o', obj]
            i += 1
        elif value in ('-MF', '-MJ', '-MT', '-MQ'):
            i += 1
        elif value == '-Xclang' and i + 1 < len(arguments) and arguments[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W') and value not in ('-MD', '-MMD', '-MP'):
            command.append(value)
        i += 1
    return command + STRICT


def run(name, command, obj):
    if os.path.exists(obj):
        os.unlink(obj)
    r = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    lines = r.stderr.splitlines()
    kind = '-'
    if r.returncode == 0 and os.path.exists(obj):
        with open(obj, 'rb') as f:
            kind = 'arm64 MH_OBJECT' if struct.unpack('<4I', f.read(16)) == (0xfeedfacf, 0x100000c, 0, 1) else 'UNEXPECTED'
    return r.returncode, lines, kind


outdir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='armplatform-')
os.makedirs(outdir, exist_ok=True)
status = 0
for driver in ('ArmDTPlatform', 'ArmCpu'):
    # The tree's drivers/Pagani* are symbolic links to the driver repository; compile through the tree path as the build does.
    source = os.path.join(TREE, 'drivers', 'Pagani' + driver, driver + '.cpp')
    if driver == 'ArmDTPlatform' and len(sys.argv) > 2:
        source = sys.argv[2]
    folder = os.path.dirname(source) + '/'
    for recipe in ('platform', 'strict'):
        obj = os.path.join(outdir, f'{driver}.{recipe}.o')
        command = compile_command(record, source, obj) if recipe == 'platform' else strict_command(source, obj)
        rc, lines, kind = run(driver, command, obj)
        diagnostics = [l for l in lines if ' warning: ' in l or ' error: ' in l]
        own = [l for l in diagnostics if l.startswith(folder)]
        headers = [l for l in diagnostics if l not in own]
        known = [l for l in own if any(re.search(p, l) for p in BASELINE[driver + '.cpp'])]
        new = [l for l in own if l not in known]
        print(f'{driver}.cpp [{recipe}]: rc {rc} object {kind}; diagnostics {len(diagnostics)} '
              f'(XNU headers {len(headers)}, own baseline {len(known)}, own new {len(new)})')
        for l in new:
            print('  NEW ' + l.split('/tree/drivers/')[-1])
        for l in known:
            print('  baseline ' + l.split('/tree/drivers/')[-1])
        if rc or kind != 'arm64 MH_OBJECT' or new or (recipe == 'strict' and headers):
            status = 1
            if recipe == 'strict':
                for l in headers:
                    print('  ' + l)
print('outdir', outdir, 'PASS' if status == 0 else 'FAIL')
sys.exit(status)
