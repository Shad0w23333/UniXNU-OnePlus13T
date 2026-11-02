#!/usr/bin/env python3
"""Compile layout_probe.c with the exact flags each Turnip build used for ir3_disk_cache.c (compile_commands.json of build/mesa-tu-ios
and of the host release build) and compare the sizes it records. Prints JSON {ios: [...], host: [...], equal: bool}; exit 1 when unequal."""
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PROBE = TOOLS / 'layout_probe.c'


def sizes(build_dir, out):
    commands = json.loads((Path(build_dir) / 'compile_commands.json').read_text())
    entry = next(c for c in commands if c['file'].endswith('ir3/ir3_disk_cache.c'))
    args = shlex.split(entry['command'])
    # Drop the output, dependency and source arguments; keep the compiler, target, sysroot, defines, includes and options.
    kept, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ('-o', '-MF', '-MQ', '-MT'):
            skip = True
            continue
        if a in ('-c', '-MD') or a.endswith('ir3_disk_cache.c'):
            continue
        kept.append(a)
    subprocess.run(kept + ['-S', '-o', str(out), str(PROBE)], check=True, cwd=entry['directory'])
    text = Path(out).read_text()
    body = text[text.index('_tu_layout_probe:'):]
    values = [int(v, 0) for v in re.findall(r'\.quad\s+(\S+)', body)[:11]]
    if values[0] != 0x4c41594f55543031:
        raise SystemExit('probe marker not found')
    return values[1:]


def main():
    ios_dir, host_dir, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    ios, host = sizes(ios_dir, out / 'ios.s'), sizes(host_dir, out / 'host.s')
    names = ['ir3_shader_variant', 'VARIANT_CACHE_SIZE', 'offsetof(variant, info)', 'ir3_const_state', 'ir3_shader_key', 'ir3_info',
             'ir3_stream_output_info', 'ir3_shader_output', 'ir3_sampler_prefetch', 'alignof(variant)']
    print(json.dumps(dict(names=names, ios=ios, host=host, equal=ios == host)))
    sys.exit(0 if ios == host else 1)


if __name__ == '__main__':
    main()
