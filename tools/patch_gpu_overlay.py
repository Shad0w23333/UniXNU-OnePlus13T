#!/usr/bin/env python3
"""Show the A830 startup result in the status overlay of the build 68 kernel.

With pagani-platform-status=1 the overlay repaints two rows over the UI: CPU and RAM. The GPU
stages only reach the verbose console, which is gone once the UI owns the display. This patch
makes the RAM row print the last boot_progress_display_status entry instead, with the format
the driver already uses for its log ("Pagani GPU: <stage> <value>"): "GPU Metal registered"
means the whole startup passed; any other stage is where it stopped.

Usage: patch_gpu_overlay.py IN OUT
"""
import hashlib
import struct
import sys
from pathlib import Path

ROW = 0xfffffff00bffad5c           # RAM row of boot_progress_peripheral_frame, 20 instructions
ORIGINAL = [0xf0fd8c68, 0xd503201f, 0xf9409508, 0xd354fd08, 0x90000b09, 0xd503201f, 0xb946f929,
            0x53087d29, 0xd503201f, 0xd503201f, 0x1873750a, 0x53087d4a, 0xa900abe9, 0x9100c3e0,
            0xf90003e8, 0x52800a01, 0xf0fd8aa2, 0x91320c42, 0x97f5ad3e, 0x1400000c]
STATUS = 0xfffffff00c30da14        # detail text written by boot_progress_display_status
VALUE = 0xfffffff00c30da68         # its value
FORMAT = 0xfffffff00714fd9b        # "Pagani GPU: %s %llx\n"
SNPRINTF = 0xfffffff00bd6629c
DRAW = 0xfffffff00bffadd8          # common path that paints the formatted line
NOP = 0xd503201f


def adrp(pc, rd, target):
    delta = ((target & ~0xfff) - (pc & ~0xfff)) >> 12
    return 0x90000000 | ((delta & 3) << 29) | (((delta >> 2) & 0x7ffff) << 5) | rd


def add_imm(rd, rn, imm):
    return 0x91000000 | ((imm & 0xfff) << 10) | (rn << 5) | rd


def ldr_x(rt, rn, imm):
    return 0xf9400000 | ((imm // 8) << 10) | (rn << 5) | rt


def branch(pc, target, link):
    return (0x94000000 if link else 0x14000000) | (((target - pc) >> 2) & 0x3ffffff)


def code():
    pc = ROW
    out = []

    def emit(word):
        nonlocal pc
        out.append(word)
        pc += 4

    emit(adrp(pc, 8, STATUS)); emit(add_imm(8, 8, STATUS & 0xfff))      # x8 = text
    emit(adrp(pc, 9, VALUE)); emit(ldr_x(9, 9, VALUE & 0xfff))          # x9 = value
    emit(0xa90027e8)                                                     # stp x8, x9, [sp]
    emit(0x9100c3e0)                                                     # add x0, sp, #0x30
    emit(0x52800a01)                                                     # mov w1, #0x50
    emit(adrp(pc, 2, FORMAT)); emit(add_imm(2, 2, FORMAT & 0xfff))      # x2 = format
    emit(branch(pc, SNPRINTF, True))
    emit(branch(pc, DRAW, False))
    while len(out) < len(ORIGINAL):
        emit(NOP)
    return out


def text_exec_offset(data, vmaddr):
    ncmds = struct.unpack_from('<I', data, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x19:
            name = data[at + 8:at + 24].rstrip(b'\0')
            seg_vm, seg_vmsize, seg_off = struct.unpack_from('<QQQ', data, at + 24)
            if name == b'__TEXT_EXEC' and seg_vm <= vmaddr < seg_vm + seg_vmsize:
                return seg_off + (vmaddr - seg_vm)
        at += size
    raise SystemExit('address is not in __TEXT_EXEC')


def main(src, dst):
    data = bytearray(Path(src).read_bytes())
    at = text_exec_offset(data, ROW)
    if list(struct.unpack_from(f'<{len(ORIGINAL)}I', data, at)) != ORIGINAL:
        raise SystemExit('RAM row code differs from build 68')
    struct.pack_into(f'<{len(ORIGINAL)}I', data, at, *code())
    Path(dst).write_bytes(data)
    print(f'patched {ROW:#x} (file {at:#x}): overlay row 2 shows the GPU startup stage')
    print('sha256', hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main(*sys.argv[1:])
