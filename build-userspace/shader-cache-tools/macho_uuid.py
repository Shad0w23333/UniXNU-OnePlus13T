#!/usr/bin/env python3
"""Read or set the LC_UUID of a thin 64-bit Mach-O. Mesa's build identifier on Apple is the dylib's LC_UUID (util/build_id.c), which
Turnip hashes into pipelineCacheUUID; the host Turnip copy that makes the tablet's records carries the tablet's Turnip's LC_UUID.
  macho_uuid.py FILE            print the UUID (32 lowercase hex digits)
  macho_uuid.py FILE HEX32      set it, then re-sign ad hoc (the host loads only validly signed code)"""
import struct
import subprocess
import sys

LC_UUID = 0x1b


def find(data):
    magic, cputype, subtype, filetype, ncmds, sizeofcmds = struct.unpack_from('<6I', data)
    if magic != 0xfeedfacf:
        raise SystemExit('not a thin 64-bit Mach-O')
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<2I', data, at)
        if cmd == LC_UUID:
            return at + 8
        at += size
    raise SystemExit('no LC_UUID')


def read(path):
    data = open(path, 'rb').read()
    at = find(data)
    return data[at:at + 16].hex()


def write(path, value):
    data = bytearray(open(path, 'rb').read())
    at = find(data)
    data[at:at + 16] = bytes.fromhex(value)
    open(path, 'wb').write(data)
    subprocess.run(['codesign', '--force', '--sign', '-', path], check=True, capture_output=True)
    subprocess.run(['codesign', '--verify', '--strict', path], check=True, capture_output=True)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        print(read(sys.argv[1]))
    elif len(sys.argv) == 3 and len(sys.argv[2]) == 32:
        write(sys.argv[1], sys.argv[2].lower())
        print(read(sys.argv[1]))
    else:
        raise SystemExit(__doc__)
