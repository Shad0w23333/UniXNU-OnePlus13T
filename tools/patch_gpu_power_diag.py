#!/usr/bin/env python3
"""Status overlay for the "GPU power" stage of the build 68 kernel (apply to the SMEM-fixed kernel).

Rows (pagani-platform-status=1):
  1  G <stage> p<Sm8750GpuPowerOn step> r<last register offset> v<last value read>
  2  <last boot_progress_display_status text> <value>   (e.g. "GPU RPMh failed ...")
  3  the Metal plugin's own result line
  4  the first failure text of a Metal client

The display driver used to overwrite the status text with "system frame copied <n>" on every
frame; that call is removed, so row 2 keeps the last message of anything else. Sm8750GpuPowerOn
keeps its progress in a status block (qword_FFFFFFF00C208BE0): +8 step (1 rails, 2 GCC AHB,
3 GDSC, 4 GCC/PLL checks, 5 RCGs, 6 branch clocks, 7 done), +12 domain (0 GCC, 1 GPUCC),
+16 register offset, +20 value read.

Usage: patch_gpu_power_diag.py IN OUT
"""
import hashlib
import struct
import sys
from pathlib import Path

from patch_gpu_diag_overlay import (CLIENT_TEXT, CPU_FMT, CPU_ROW, DRAW, METAL_DIAG, NOP, RAM_FMT, RAM_ROW,
                                    ROWS, SNPRINTF, STAGE_PTR, Code, add_imm, adrp, branch, ldr_w, ldr_x, offset)

POWER = 0xfffffff00c208be0         # Sm8750GpuPowerOn status block
STATUS = 0xfffffff00c30da14        # boot_progress_display_status text (value at +0x54)
FRAME_STATUS = (0xfffffff00bfe1000, 0x940064be, NOP)   # bl boot_progress_display_status("system frame copied")
CPU_TEXT = b'G %s p%u r%x v%x'
RAM_TEXT = b'%s %llx'


def cpu_row():
    c = Code(CPU_ROW[0])
    c(adrp(c.pc, 8, STAGE_PTR)); c(ldr_x(8, 8, STAGE_PTR & 0xfff))
    c(adrp(c.pc, 9, POWER)); c(add_imm(9, 9, POWER & 0xfff))
    c(ldr_w(10, 9, 8))                                                   # step
    c(ldr_w(11, 9, 16))                                                  # register offset
    c(ldr_w(12, 9, 20))                                                  # value read
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
    c(adrp(c.pc, 8, STATUS)); c(add_imm(8, 8, STATUS & 0xfff))           # row 2: status text
    c(0xf8400000 | (0x54 << 12) | (8 << 5) | 9)                          # ldur x9, [x8, #0x54]: its value
    c(adrp(c.pc, 11, METAL_DIAG))
    c(add_imm(10, 11, (METAL_DIAG + 0x186) & 0xfff))                     # row 3: plugin report
    c(0xf1000b5f)                                                        # cmp x26, #2
    c(0x9a880148)                                                        # csel x8, x10, x8, eq
    c(0x9a8903e9)                                                        # csel x9, xzr, x9, eq
    c(add_imm(10, 11, (CLIENT_TEXT + 20) & 0xfff))                       # row 4: client_text[0].text
    c(0xf1000f5f)                                                        # cmp x26, #3
    c(0x9a880148)                                                        # csel x8, x10, x8, eq
    c(0x9a8903e9)                                                        # csel x9, xzr, x9, eq
    c(0xa90027e8)                                                        # stp x8, x9, [sp]
    c(0x9100c3e0)                                                        # add x0, sp, #0x30
    c(0x52800a01)                                                        # mov w1, #0x50
    c(adrp(c.pc, 2, RAM_FMT[0])); c(add_imm(2, 2, RAM_FMT[0] & 0xfff))
    c(branch(c.pc, SNPRINTF, True))
    c(branch(c.pc, DRAW))
    while len(c.words) < len(RAM_ROW[1]):
        c(NOP)
    assert len(c.words) == len(RAM_ROW[1])
    return c.words


def main(src, dst):
    data = bytearray(Path(src).read_bytes())
    for (address, old, _), new in ((CPU_FMT, CPU_TEXT), (RAM_FMT, RAM_TEXT)):
        at = offset(data, address)
        if bytes(data[at:at + len(old) + 1]) != old + b'\0':
            raise SystemExit(f'format string at {address:#x} differs from build 68')
        data[at:at + len(old) + 1] = new + b'\0' * (len(old) + 1 - len(new))
    for address, old, new in (ROWS, FRAME_STATUS):
        at = offset(data, address)
        if struct.unpack_from('<I', data, at)[0] != old:
            raise SystemExit(f'instruction at {address:#x} differs from build 68')
        struct.pack_into('<I', data, at, new)
    for (address, old), new in ((CPU_ROW, cpu_row()), (RAM_ROW, ram_row())):
        at = offset(data, address)
        if list(struct.unpack_from(f'<{len(old)}I', data, at)) != old:
            raise SystemExit(f'overlay code at {address:#x} differs from build 68')
        struct.pack_into(f'<{len(new)}I', data, at, *new)
    Path(dst).write_bytes(data)
    print('overlay: power step/register/value, last status text, plugin report, client failure')
    print('sha256', hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main(*sys.argv[1:])
