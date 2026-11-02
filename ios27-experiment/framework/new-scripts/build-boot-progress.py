#!/usr/bin/env python3
"""Compile the 13T early screen console with the actual XNU C ABI."""
import json
import os
from pathlib import Path
import runpy
import re
import shutil
import subprocess

from kernel_target import check_object


def main():
    if any(os.environ.get(name) != '1' for name in
           ('XNU_EARLY_BOOT_PROGRESS', 'XNU_ARM_DT_PLATFORM', 'XNU_ARMCPU')):
        raise SystemExit('13T boot progress requires early progress, ArmDTPlatform and ArmCpu')
    root = Path(__file__).resolve().parents[1]
    compile_command = runpy.run_path(str(root / 'scripts/build-arm-platform.py'))['compile_command']
    recipe = root / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json'
    record = json.loads(recipe.read_text().strip().rstrip(','))
    output = root / 'build/boot-progress'
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(['python3', root / 'scripts/prepare-boot-logo.py',
                    root / '../../../../ipsw/21H461/applelogo@2732~ipad.im4p', output / 'BootLogo.h'], check=True)
    source = root / 'drivers/BootProgress'
    # BootProgress.c, plus RamoopsLog.c (the persistent ramoops log and the timed warm reset: pagani-ramoops,
    # pagani-reset-after, pagani-panic-reset), GunyahWdog.c (pagani-wdog: the hypervisor watchdog that resets a hung
    # kernel; it logs through RamoopsLog) and UfsLog.c (sheng-log: RamoopsLog's snapshots on UFS, the strong boot_log_export_put)
    # when the driver tree has them; all run on the early, IRQ and panic paths.
    names = ['BootProgress']
    if (source / 'RamoopsLog.c').is_file():
        names.append('RamoopsLog')
        for extra in ('GunyahWdog', 'UfsLog', 'WeakDefaults'):
            if (source / f'{extra}.c').is_file():
                names.append(extra)
    for name in ('RamoopsLog', 'GunyahWdog', 'UfsLog', 'WeakDefaults'):
        (output / f'{name}.o').unlink(missing_ok=True)
    objects = []
    for name in names:
        obj = output / f'{name}.o'
        command = compile_command(record, source / f'{name}.c', obj)
        # Bootstrap records disable the hooks; this object supplies their enabled implementation.
        args = iter(command)
        command = []
        for value in args:
            if value == '-D':
                define = next(args)
                if not define.startswith(('XNU_BOOT_PROGRESS=', 'XNU_EARLY_BOOT_PROGRESS=')):
                    command += [value, define]
            elif not value.startswith(('-DXNU_BOOT_PROGRESS=', '-DXNU_EARLY_BOOT_PROGRESS=')):
                command.append(value)
        command += ['-DXNU_BOOT_PROGRESS=1', '-DXNU_EARLY_BOOT_PROGRESS=1',
                    '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-mgeneral-regs-only', '-fno-jump-tables',
                    '-I' + str(root / 'drivers/PaganiDarwinBoot/DarwinBootPkg/Driver'), '-I' + str(output)]
        subprocess.run(command, cwd=record['directory'], check=True)
        objects.append(obj)
    entry = output / 'Entry.o'
    # With the kernel's own record: a -target of arm64e-apple-ios assembles a user object (USR00), which the kernel link refuses.
    assembly_record = json.loads((root / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/start.o.json').read_text().strip().rstrip(','))
    subprocess.run(compile_command(assembly_record, source / 'Entry.S', entry), cwd=assembly_record['directory'], check=True)
    for path in (*objects, entry):
        check_object(path.read_bytes(), path.name)
    for obj in objects:
        undefined = subprocess.check_output(['xcrun', 'nm', '-u', obj], text=True)
        if any(name in undefined for name in ('_memset', '_memcpy', '_bzero', 'stack_chk', 'chkstk')):
            raise SystemExit(f'{obj.name}: early console unexpectedly depends on a compiler runtime helper: ' + undefined)
        assembly = subprocess.check_output([shutil.which('llvm-objdump') or '/opt/homebrew/opt/llvm/bin/llvm-objdump', '-d', '--no-show-raw-insn', '--no-leading-addr', obj], text=True)
        if re.search(r'\b(?:[qds][0-9]+|v[0-9]+\.[0-9a-z]+)\b', assembly):
            raise SystemExit(f'{obj.name}: early/IRQ console unexpectedly uses FP/SIMD registers')
    archive = output / 'libBootProgress.a'
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects, entry], check=True)
    print(archive)


if __name__ == '__main__':
    main()
