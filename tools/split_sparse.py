#!/usr/bin/env python3
"""Split a raw partition image into Android sparse files for sequential fastboot flashing.

Each output file describes the whole partition: its own range as RAW or FILL(0) chunks and
DONT_CARE elsewhere, so flashing all of them in order writes the image byte for byte.
Zero blocks inside the range are written as FILL 0, never skipped, because the root
policies hash every block of the OS and SystemOS windows.
"""
import struct
import sys
from pathlib import Path

BLK = 4096
MAGIC, RAW, FILL, DONT_CARE = 0xED26FF3A, 0xCAC1, 0xCAC2, 0xCAC3
ZERO = bytes(BLK)


def main(image, outdir, part_mib=600):
    image, outdir = Path(image), Path(outdir)
    size = image.stat().st_size
    if size % BLK:
        raise SystemExit('image is not a whole number of 4 KiB blocks')
    total = size // BLK
    per_part = part_mib * 1024 * 1024 // BLK
    outdir.mkdir(parents=True, exist_ok=True)
    parts = []
    with image.open('rb') as src:
        start = 0
        while start < total:
            end = min(total, start + per_part)
            chunks = []                       # (type, nblocks, payload bytes or None)
            if start:
                chunks.append((DONT_CARE, start, None))
            src.seek(start * BLK)
            run_type, run_blocks, run_data = None, 0, []
            for _ in range(end - start):
                block = src.read(BLK)
                kind = FILL if block == ZERO else RAW
                if kind != run_type:
                    if run_type is not None:
                        chunks.append((run_type, run_blocks, b''.join(run_data) if run_type == RAW else None))
                    run_type, run_blocks, run_data = kind, 0, []
                run_blocks += 1
                if kind == RAW:
                    run_data.append(block)
            chunks.append((run_type, run_blocks, b''.join(run_data) if run_type == RAW else None))
            if end < total:
                chunks.append((DONT_CARE, total - end, None))
            path = outdir / f'unixnu-root.{len(parts):02d}.simg'
            with path.open('wb') as out:
                out.write(struct.pack('<IHHHHIIII', MAGIC, 1, 0, 28, 12, BLK, total, len(chunks), 0))
                for kind, blocks, data in chunks:
                    if kind == RAW:
                        out.write(struct.pack('<HHII', RAW, 0, blocks, 12 + len(data)))
                        out.write(data)
                    elif kind == FILL:
                        out.write(struct.pack('<HHII', FILL, 0, blocks, 16))
                        out.write(struct.pack('<I', 0))
                    else:
                        out.write(struct.pack('<HHII', DONT_CARE, 0, blocks, 12))
            parts.append((path.name, start, end, path.stat().st_size))
            start = end
    for name, first, last, nbytes in parts:
        print(f'{name}  blocks {first}..{last - 1}  {nbytes} bytes')
    print(f'{len(parts)} parts for {total} blocks ({size} bytes)')


if __name__ == '__main__':
    main(*sys.argv[1:3], *(int(x) for x in sys.argv[3:4]))
