# SPDX-License-Identifier: MIT
"""The kernel GPU driver whose NativeAdrenoABI.h the user-space stack is built against: Turnip's darwin backend, the Metal plugin,
adrenoprobed and the host simulation.

The A830 driver (drivers/Adreno830) keeps the A740 wire ABI byte for byte (ABI v2; only the DeviceInfo values differ), so either header
serves. The choice: an explicit directory (--abi-dir or UNIXNU_GPU_ABI_DIR), else drivers/Adreno830 when the tree has it, else
drivers/Adreno740. Every header that is present must be identical to the chosen one: a driver that changed the wire ABI must not be
built against silently."""
import os
from pathlib import Path

CANDIDATES = ('drivers/Adreno830', 'drivers/Adreno740')


def abi_dir(root, override=None):
    root = Path(root)
    chosen = override or os.environ.get('UNIXNU_GPU_ABI_DIR')
    if chosen:
        chosen = Path(chosen)
    else:
        chosen = next((root / c for c in CANDIDATES if (root / c / 'NativeAdrenoABI.h').is_file()), root / CANDIDATES[-1])
    header = chosen / 'NativeAdrenoABI.h'
    if not header.is_file():
        raise SystemExit('No NativeAdrenoABI.h in ' + str(chosen))
    reference = header.read_bytes()
    for candidate in CANDIDATES:
        other = root / candidate / 'NativeAdrenoABI.h'
        if other.is_file() and other.read_bytes() != reference:
            raise SystemExit('%s and %s differ: the kernel/user-client wire ABI must stay identical (ABI v2)' % (header, other))
    return chosen.resolve()
