#!/usr/bin/env python3
"""Let Sm8750GpuPowerOn continue past GPU clock branches whose enable bit does not read back.

_branch (build 68) sets CBCR bit 0 through _update, which polls until the bit reads back as 1 and
otherwise fails the whole "GPU power" stage. On PKX110 GPU_CC_CXO_AON_CLK (GPUCC+0x9008, one of
the CBCRs Linux gpucc-sm8750.c only sets once as critical, without reading back) returns
0x80000000 and the startup stops there. After this patch a failed enable no longer returns
directly: it falls into _branch's own "wait for the clock" decision, so branches called without
a halt wait (the critical CBCRs and the AHB clock, as in Linux) continue, and branches with a
halt wait still poll CLK_OFF/enable and still fail if the clock never runs.

Usage: patch_gpu_branch.py IN OUT
"""
import hashlib
import struct
import sys
from pathlib import Path

from patch_gpu_diag_overlay import offset

SITES = [
    # _branch+0x30: cbnz w0, return  ->  cbnz w0, halt-wait decision (cbz w22: no wait -> return 0)
    (0xfffffff00bfcf644, 0x35000100, 0x350001a0),
    # halt wait: (CBCR & 0x80000001) == 1  ->  (CBCR & CLK_OFF) == 0, the condition Linux waits for
    (0xfffffff00bfcf6d4, 0x12010508, 0x12010108),
    (0xfffffff00bfcf6d8, 0x7100051f, 0x7100011f),
]


def main(src, dst):
    data = bytearray(Path(src).read_bytes())
    for site, old, new in SITES:
        at = offset(data, site)
        if struct.unpack_from('<I', data, at)[0] != old:
            raise SystemExit(f'_branch at {site:#x} differs from build 68')
        struct.pack_into('<I', data, at, new)
    Path(dst).write_bytes(data)
    print('patched _branch: clock branches are accepted once CLK_OFF clears, unchecked ones always continue')
    print('sha256', hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main(*sys.argv[1:])
