#!/usr/bin/env python3
"""Build the source ArmDTPlatform and ArmCpu with XNU's recorded IOKit flags."""
import json
import subprocess
from pathlib import Path

from kernel_target import check_object


def compile_command(record, source, output):
    args = iter(record['arguments'])
    command = []
    found_source = False
    for value in args:
        if value == record['file']:
            command.append(str(source))
            found_source = True
        elif value == '-o':
            next(args)
            command.extend(['-o', str(output)])
        elif value in ('-MF', '-MJ', '-MT', '-MQ'):
            next(args)
        elif value not in ('-MD', '-MMD', '-MP'):
            command.append(value)
    if not found_source or '-o' not in command:
        raise ValueError('IOKit compile record lacks its source or output argument')
    return command


def main():
    root = Path(__file__).resolve().parents[1]
    recipe = root / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record = json.loads(recipe.read_text().strip().rstrip(','))
    for driver, folder in [('ArmDTPlatform', 'armdt-platform'), ('ArmCpu', 'armcpu')]:
        output = root / 'build' / folder
        output.mkdir(parents=True, exist_ok=True)
        obj = output / f'{driver}.o'
        command = compile_command(record, root / 'drivers' / ('Pagani' + driver) / f'{driver}.cpp', obj)
        subprocess.run(command, cwd=record['directory'], check=True)
        check_object(obj.read_bytes(), driver)
        archive = output / f'lib{driver}.a'
        archive.unlink(missing_ok=True)
        subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
        print(archive)


if __name__ == '__main__':
    main()
