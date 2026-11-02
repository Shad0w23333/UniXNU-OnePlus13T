#!/usr/bin/env python3
"""Turn the status overlay of the build 68 kernel into a four-row GPU/Metal diagnostic.

Apply to the SMEM-fixed kernel (patch_smem_copy.py). With pagani-platform-status=1 the overlay
repaints over the UI; its rows become:

  1  G <GPU startup stage> o<Metal opens> c<driver calls> s<last IOReturn>
  2  the plugin's own result line           (native_adreno_metal_diag.report)
  3  the plugin's Turnip-phase line ("T ...") (native_adreno_metal_diag.report2)
  4  the first failure text of a Metal client (native_adreno_client_text[0].text)

The stage comes from the startup's own pointer (written by stage() in pagani_gpu_start), so
the display driver's frame messages cannot overwrite it. The CPU and RAM row format strings are
used only by this overlay and are rewritten for the new rows.

Usage: patch_gpu_diag_overlay.py IN OUT
"""
import hashlib
import struct
import sys
from pathlib import Path

STAGE_PTR = 0xfffffff00c0ecce0     # const char * of the current/last pagani_gpu_start stage
METAL_DIAG = 0xfffffff00c1a1074    # native_adreno_metal_diag
CLIENT_TEXT = 0xfffffff00c1a1550   # native_adreno_client_text[4] {char name[20]; char text[64];}
SNPRINTF = 0xfffffff00bd6629c
DRAW = 0xfffffff00bffadd8
NOP = 0xd503201f

CPU_FMT = (0xfffffff007151c60, b'CPU reg=%u online=%u T=%02x I=%02x', b'G %s o%u c%u s%x')
RAM_FMT = (0xfffffff007151c83, b'RAM MiB=%lu free=%u avail=%u', b'%s')
ROWS = (0xfffffff00bffabc8, 0x52800049, 0x52800089)          # mov w9, #2 -> #4 (platform rows)
CPU_ROW = (0xfffffff00bffac18, [0xb0000d2b, 0x912cc16b, 0xb9400168, 0x7100011f, 0x1a9f07e8, 0xb0000d2c,
                                0x912d418c, 0xb9400189, 0x7100013f, 0x1a9f07e9, 0xb940056a, 0x7100015f,
                                0x1a9f07ea, 0x2a0a0508, 0xb940058a])
RAM_ROW = (0xfffffff00bffad5c, [0xf0fd8c68, 0xd503201f, 0xf9409508, 0xd354fd08, 0x90000b09, 0xd503201f,
                                0xb946f929, 0x53087d29, 0xd503201f, 0xd503201f, 0x1873750a, 0x53087d4a,
                                0xa900abe9, 0x9100c3e0, 0xf90003e8, 0x52800a01, 0xf0fd8aa2, 0x91320c42,
                                0x97f5ad3e, 0x1400000c])


def adrp(pc, rd, target):
    delta = ((target & ~0xfff) - (pc & ~0xfff)) >> 12
    return 0x90000000 | ((delta & 3) << 29) | (((delta >> 2) & 0x7ffff) << 5) | rd


def add_imm(rd, rn, imm):
    return 0x91000000 | ((imm & 0xfff) << 10) | (rn << 5) | rd


def ldr_x(rt, rn, imm):
    return 0xf9400000 | ((imm // 8) << 10) | (rn << 5) | rt


def ldr_w(rt, rn, imm):
    return 0xb9400000 | ((imm // 4) << 10) | (rn << 5) | rt


def branch(pc, target, link=False):
    return (0x94000000 if link else 0x14000000) | (((target - pc) >> 2) & 0x3ffffff)


class Code:
    def __init__(self, pc):
        self.pc, self.words = pc, []

    def __call__(self, word):
        self.words.append(word)
        self.pc += 4


def cpu_row():
    c = Code(CPU_ROW[0])
    c(adrp(c.pc, 8, STAGE_PTR)); c(ldr_x(8, 8, STAGE_PTR & 0xfff))       # x8 = stage text
    c(adrp(c.pc, 9, METAL_DIAG)); c(add_imm(9, 9, METAL_DIAG & 0xfff))   # x9 = diag
    c(ldr_w(10, 9, 0x00))                                                # opens
    c(ldr_w(11, 9, 0x24))                                                # calls
    c(ldr_w(12, 9, 0x2c))                                                # last_status
    c(0xa9002be8)                                                        # stp x8, x10, [sp]
    c(0xa90133eb)                                                        # stp x11, x12, [sp, #0x10]
    c(0x9100c3e0)                                                        # add x0, sp, #0x30
    c(0x52800a01)                                                        # mov w1, #0x50
    c(adrp(c.pc, 2, CPU_FMT[0])); c(add_imm(2, 2, CPU_FMT[0] & 0xfff))
    c(branch(c.pc, SNPRINTF, True))
    c(branch(c.pc, DRAW))
    assert len(c.words) == len(CPU_ROW[1])
    return c.words


def ram_row():
    c = Code(RAM_ROW[0])
    c(adrp(c.pc, 8, METAL_DIAG)); c(add_imm(8, 8, (METAL_DIAG + 0x186) & 0xfff))   # report
    c(add_imm(9, 8, 0x40))                                               # report2
    c(0xf1000b5f)                                                        # cmp x26, #2
    c(0x9a880128)                                                        # csel x8, x9, x8, eq
    c(adrp(c.pc, 9, CLIENT_TEXT)); c(add_imm(9, 9, (CLIENT_TEXT + 20) & 0xfff))   # client_text[0].text
    c(0xf1000f5f)                                                        # cmp x26, #3
    c(0x9a880128)                                                        # csel x8, x9, x8, eq
    c(0xf90003e8)                                                        # str x8, [sp]
    c(0x9100c3e0)                                                        # add x0, sp, #0x30
    c(0x52800a01)                                                        # mov w1, #0x50
    c(adrp(c.pc, 2, RAM_FMT[0])); c(add_imm(2, 2, RAM_FMT[0] & 0xfff))
    c(branch(c.pc, SNPRINTF, True))
    c(branch(c.pc, DRAW))
    while len(c.words) < len(RAM_ROW[1]):
        c(NOP)
    return c.words


def offset(data, vmaddr):
    ncmds = struct.unpack_from('<I', data, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x19:
            seg_vm, seg_vmsize, seg_off, seg_size = struct.unpack_from('<QQQQ', data, at + 24)
            if seg_vm <= vmaddr < seg_vm + seg_size:
                return seg_off + (vmaddr - seg_vm)
        at += size
    raise SystemExit(f'{vmaddr:#x} is not in the file')


def main(src, dst):
    data = bytearray(Path(src).read_bytes())
    for address, old, new in (CPU_FMT, RAM_FMT):
        at = offset(data, address)
        if bytes(data[at:at + len(old) + 1]) != old + b'\0':
            raise SystemExit(f'format string at {address:#x} differs from build 68')
        data[at:at + len(old) + 1] = new + b'\0' * (len(old) + 1 - len(new))
    at = offset(data, ROWS[0])
    if struct.unpack_from('<I', data, at)[0] != ROWS[1]:
        raise SystemExit('row count instruction differs from build 68')
    struct.pack_into('<I', data, at, ROWS[2])
    for (address, old), new in ((CPU_ROW, cpu_row()), (RAM_ROW, ram_row())):
        at = offset(data, address)
        if list(struct.unpack_from(f'<{len(old)}I', data, at)) != old:
            raise SystemExit(f'overlay code at {address:#x} differs from build 68')
        struct.pack_into(f'<{len(new)}I', data, at, *new)
    Path(dst).write_bytes(data)
    print('overlay rows: GPU stage + Metal counters, plugin report, Turnip report, first client failure')
    print('sha256', hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main(*sys.argv[1:])
