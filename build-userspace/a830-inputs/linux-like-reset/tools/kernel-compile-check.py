#!/usr/bin/env python3
"""Compile one BootProgress source with the kernel's recorded recipe (osfmk startup.o), the way
UniXNU/scripts/build-boot-progress.py does, into an object OUTSIDE the build tree. Read-only use of the
build tree: no -MD/-MF dependency files, -o goes to the given output. Prints clang's diagnostics.
usage: kernel-compile-check.py <tree> <source.c> <out.o>"""
import json, subprocess, sys
from pathlib import Path

tree, source, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
recipe = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json'
record = json.loads(recipe.read_text().strip().rstrip(','))
args = iter(record['arguments'])
command = []
for value in args:
    if value == record['file']:
        command.append(str(source))
    elif value == '-o':
        next(args)
        command += ['-o', str(out)]
    elif value in ('-MF', '-MJ', '-MT', '-MQ'):
        next(args)
    elif value in ('-MD', '-MMD', '-MP'):
        continue
    elif value == '-D':
        define = next(args)
        if not define.startswith(('XNU_BOOT_PROGRESS=', 'XNU_EARLY_BOOT_PROGRESS=')):
            command += [value, define]
    elif value.startswith(('-DXNU_BOOT_PROGRESS=', '-DXNU_EARLY_BOOT_PROGRESS=')):
        continue
    else:
        command.append(value)
drivers = Path(__file__).resolve().parents[4] / 'UniXNU-Pagani-Drivers/drivers'
command += ['-DXNU_BOOT_PROGRESS=1', '-DXNU_EARLY_BOOT_PROGRESS=1', '-ffreestanding', '-fno-builtin',
            '-fno-stack-protector', '-mgeneral-regs-only', '-fno-jump-tables',
            '-I' + str(drivers / 'PaganiDarwinBoot/DarwinBootPkg/Driver'), '-I' + str(tree / 'build/boot-progress')]
r = subprocess.run(command, cwd=record['directory'])
sys.exit(r.returncode)
