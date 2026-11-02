#!/usr/bin/env python3
"""Patch the A830 candidate kernel so pagani_gpu_start does not read protected SMEM pages.

pagani_gpu_start (author build 68) copies the whole 2 MiB SMEM window at 0x81D00000 byte by
byte. On the 512 GB PKX110 the page at offset 0x9E000 is protected and the read takes a
synchronous external abort (ESR 0x96000010). The parser that consumes the copy only needs the
SMEM header, the global partition and the partition table in the last 4 KiB, so the copy loop
is replaced by an 8-byte loop that jumps from HOLE_START to HOLE_END.

Usage: patch_smem_copy.py IN OUT [HOLE_START HOLE_END]   (hex, page aligned)
"""
import hashlib
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

FUNC = 0xfffffff00bfd9bd0          # _pagani_gpu_start in build 68
# Both SMEM copies of pagani_gpu_start (the second one re-reads the socinfo for a consistency
# check): address, original bytes, replacement. Each block is 11 instructions; the replacement
# keeps the registers the following code reads (x19/x24, and x0 = destination for the second).
SITES = [
    (0xfffffff00bfd9e44,
     '680240f9' '480100b4' '080080d2' '690641f9' '0a0340f9' '4a696838'
     '2a692838' '08050091' '0a0740f9' '1f010aeb' '43ffff54',
     """
    mov  x8, #0
    ldr  x9, [x19, #0x208]
    ldr  x10, [x24]
    mov  x12, #{end:#x}
1:  ldr  x11, [x10, x8]
    str  x11, [x9, x8]
    add  x8, x8, #8
    cmp  x8, #{start_page:#x}, lsl #12
    csel x8, x12, x8, eq
    cmp  x8, #0x200, lsl #12
    b.lo 1b
"""),
    (0xfffffff00bfd9f48,
     '600641f9' '680240f9' '280100b4' '080080d2' '090340f9' '29696838'
     '09682838' '08050091' '090740f9' '1f0109eb' '43ffff54',
     """
    ldr  x0, [x19, #0x208]
    mov  x8, #0
    ldr  x9, [x24]
    mov  x12, #{end:#x}
1:  ldr  x10, [x9, x8]
    str  x10, [x0, x8]
    add  x8, x8, #8
    cmp  x8, #{start_page:#x}, lsl #12
    csel x8, x12, x8, eq
    cmp  x8, #0x200, lsl #12
    b.lo 1b
"""),
]


def text_exec_offset(data, vmaddr):
    ncmds = struct.unpack_from('<I', data, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x19:  # LC_SEGMENT_64
            name = data[at + 8:at + 24].rstrip(b'\0')
            seg_vm, seg_vmsize, seg_off = struct.unpack_from('<QQQ', data, at + 24)
            if name == b'__TEXT_EXEC' and seg_vm <= vmaddr < seg_vm + seg_vmsize:
                return seg_off + (vmaddr - seg_vm)
        at += size
    raise SystemExit('address is not in __TEXT_EXEC')


def section_text(obj):
    """Bytes of __TEXT,__text in a Mach-O object file."""
    ncmds = struct.unpack_from('<I', obj, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', obj, at)
        if cmd == 0x19:
            nsects = struct.unpack_from('<I', obj, at + 64)[0]
            for index in range(nsects):
                sect = at + 72 + index * 80
                if obj[sect:sect + 16].rstrip(b'\0') == b'__text':
                    length, offset = struct.unpack_from('<QI', obj, sect + 40)
                    return obj[offset:offset + length]
        at += size
    raise SystemExit('no __text section in the assembled object')


def assemble(template, hole_start, hole_end, length):
    source = '    .text\n' + template.format(start_page=hole_start >> 12, end=hole_end)
    with tempfile.TemporaryDirectory() as tmp:
        asm, obj = Path(tmp, 'p.s'), Path(tmp, 'p.o')
        asm.write_text(source)
        subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-darwin', '-c', asm, '-o', obj], check=True)
        code = section_text(obj.read_bytes())
    if len(code) != length:
        raise SystemExit(f'replacement is {len(code)} bytes, need {length}')
    return code


def main(src, dst, hole_start='0x9e000', hole_end='0x1ff000'):
    start, end = int(hole_start, 16), int(hole_end, 16)
    if start % 0x1000 or end % 0x1000 or not 0 < start < end <= 0x1ff000:
        raise SystemExit('hole must be page aligned and end at or before the partition table')
    data = bytearray(Path(src).read_bytes())
    nm = subprocess.run(['xcrun', 'llvm-nm', '--defined-only', src], capture_output=True, text=True, check=True).stdout
    if not any(line.endswith(' _pagani_gpu_start') and int(line.split()[0], 16) == FUNC for line in nm.splitlines()):
        raise SystemExit('_pagani_gpu_start is not where build 68 has it')
    for address, original, template in SITES:
        original = bytes.fromhex(original)
        at = text_exec_offset(data, address)
        if bytes(data[at:at + len(original)]) != original:
            raise SystemExit(f'copy loop at {address:#x} differs from build 68')
        data[at:at + len(original)] = assemble(template, start, end, len(original))
        print(f'patched {address:#x} (file {at:#x})')
    Path(dst).write_bytes(data)
    print(f'both copies now read SMEM [0, {start:#x}) and [{end:#x}, 0x200000)')
    print('sha256', hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main(*sys.argv[1:])
