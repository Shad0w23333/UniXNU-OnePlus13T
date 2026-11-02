#!/usr/bin/env python3
"""Compile the sources of the UFS log with the kernel's recorded recipes, into objects OUTSIDE the build tree (read-only use of the
tree: no -MD/-MF files, -o goes to the given directory), the way UniXNU/scripts/build-boot-progress.py and build-native-ufs.py do.
Prints clang's diagnostics; fails on any diagnostic. Then runs the checks build-boot-progress.py makes (no memcpy & co., no FP/SIMD).
usage: kernel-compile-check.py <out dir> [--drivers DIR]"""
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TREE = ROOT / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree'


def plain(record, source, obj):
    args = iter(record['arguments'])
    command = []
    for value in args:
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            next(args)
            command += ['-o', str(obj)]
        elif value in ('-MF', '-MJ', '-MT', '-MQ'):
            next(args)
        elif value not in ('-MD', '-MMD', '-MP'):
            command.append(value)
    return command


def run(command, cwd, name, ours=None):
    """`ours`: the diagnostics that count, a function of (file, line, text); None: all of them (a file with no baseline of warnings)."""
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    text = result.stdout + result.stderr
    found = re.findall(r'^(/[^:\n]+):(\d+):\d+: (warning|error): (.*)$', text, re.M)
    mine = [f for f in found if f[2] == 'error' or ours is None or ours(f[0], int(f[1]), f[3])]
    if result.returncode or mine:
        print(text)
        print('\n'.join('OURS %s:%s %s' % (Path(f[0]).name, f[1], f[3]) for f in mine))
        sys.exit('%s: not clean' % name)
    print('clean: %s (%d diagnostics of the file\'s baseline, none of ours)' % (name, len(found)))


def boot_progress(name, drivers, out):
    record = json.loads((TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json').read_text().strip().rstrip(','))
    obj = out / (name + '.o')
    args = iter(plain(record, drivers / 'BootProgress' / (name + '.c'), obj))
    command = []
    for value in args:
        if value == '-D':
            define = next(args)
            if not define.startswith(('XNU_BOOT_PROGRESS=', 'XNU_EARLY_BOOT_PROGRESS=')):
                command += [value, define]
        elif not value.startswith(('-DXNU_BOOT_PROGRESS=', '-DXNU_EARLY_BOOT_PROGRESS=')):
            command.append(value)
    command += ['-DXNU_BOOT_PROGRESS=1', '-DXNU_EARLY_BOOT_PROGRESS=1', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
                '-mgeneral-regs-only', '-fno-jump-tables', '-I' + str(drivers / 'PaganiDarwinBoot/DarwinBootPkg/Driver'),
                '-I' + str(TREE / 'build/boot-progress')]
    run(command, record['directory'], name)
    undefined = subprocess.check_output(['xcrun', 'nm', '-u', obj], text=True)
    if any(helper in undefined for helper in ('_memset', '_memcpy', '_bzero', 'stack_chk', 'chkstk')):
        sys.exit('%s: depends on a compiler runtime helper:\n%s' % (name, undefined))
    assembly = subprocess.check_output(['llvm-objdump', '-d', '--no-show-raw-insn', '--no-leading-addr', obj], text=True,
                                       env={'PATH': '/opt/homebrew/opt/llvm/bin:/usr/bin:/bin'})
    if re.search(r'\b(?:[qds][0-9]+|v[0-9]+\.[0-9a-z]+)\b', assembly):
        sys.exit('%s: uses FP/SIMD registers' % name)
    print('checked: %s has no runtime helpers and no FP/SIMD; undefined: %s' % (name, ' '.join(undefined.split())))


def native_ufs(drivers, out):
    record = json.loads((TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
    obj = out / 'NativeUfs.o'
    command = plain(record, drivers / 'NativeUfs/NativeUfs.cpp', obj)
    command += ['-DNATIVE_UFS_SM8750=1', '-DUFS_ROOT_PARTITION="unixnu"', '-DNATIVE_UFS_READ_ONLY=1', '-DNATIVE_UFS_LOG_WRITES=1',
                '-Wno-unused-function', '-I', str(TREE / 'build/storage-abi/include'),
                '-I', str(TREE / 'kits/PaganiVerifiedRoot/include'), '-I', str(TREE / 'kits'), '-I', str(drivers / 'NativeVirtioBlock'),
                '-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1', '-mgeneral-regs-only']
    # NativeUfs.cpp has its baseline of -Weverything warnings. Ours: anything in LogWindow.h, LogInterface.h or RootCommands.h's log code (from
    # the read timeouts on, which come with the log).
    commands = (drivers / 'NativeUfs/RootCommands.h').read_text().splitlines()
    start = next(i for i, line in enumerate(commands, 1) if 'A command that outlives its timeout quarantines' in line)

    def ours(file, line, text):
        return file.endswith(('LogWindow.h', 'LogInterface.h')) or (file.endswith('RootCommands.h') and line >= start)
    run(command, record['directory'], 'NativeUfs', ours)
    defined = subprocess.check_output(['xcrun', 'nm', '-gU', '-j', obj], text=True).split()
    for name in ('native_ufs_log_open', 'native_ufs_log_write', 'native_ufs_log_sync', 'native_ufs_log_close', 'native_ufs_log_panic',
                 'native_ufs_log_panic_write', 'native_ufs_log_panic_sync', 'native_ufs_export_blocks', 'native_ufs_export_write',
                 'native_ufs_store_gpt_check'):
        if '_' + name not in defined:
            sys.exit('NativeUfs: %s not defined' % name)
    print('checked: NativeUfs defines the log interface')


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('out', type=Path)
parser.add_argument('--drivers', type=Path, default=ROOT / 'UniXNU-Pagani-Drivers/drivers')
parser.add_argument('--only', action='append')
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
names = args.only or ['NativeUfs', 'RamoopsLog', 'BootProgress', 'GunyahWdog', 'UfsLog']
for name in names:
    if name == 'NativeUfs':
        native_ufs(args.drivers.resolve(), args.out.resolve())
    else:
        boot_progress(name, args.drivers.resolve(), args.out.resolve())
