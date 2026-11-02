#!/usr/bin/env python3
"""RETIRED (2026-11-01): the kernel's export now goes through BootProgress UfsLog.c, whose header this file does not read; gpu-loop.py and
read-ufslog.py unpack it into <run>/ufs/export, and shader-cache-tools/harvest-a830.py takes it from there. Kept for the older images only.

Read the render server's shader-cache export back from the 13T's unixnu partition (route B; needs the kernel patch
shader-cache-tools/patches/pagani-nativeufs-cache-export.patch, boot arguments pagani-cache-export=1 sheng-log=1, and a seed with
ngpu/addons/NativeAdrenoMetal.cacheexport). The export region is the 256 MiB below the log window (the last 32 MiB of the partition);
block 0 of the log window holds a PGEXPRT1 header naming the newest complete blob (CacheExport.h: "SHEXR001", u32 stream bytes, u32 0,
then the M2VCACH1 stream of the cache directory's files). The files are written under --out, ready for
make-shader-cache-a830.py --merge-export.

  read-cache-export-a830.py --adb --out DIR        (phone in Android with root; reads two block ranges with dd, writes nothing there)
  read-cache-export-a830.py --tail FILE --out DIR  (offline: FILE holds the partition's last 73728 blocks, export region then window)
  read-cache-export-a830.py --make-test-tail PACK FILE   (a synthetic tail with PACK's files as an export, for the host test)
"""
import argparse
import binascii
import json
import shlex
import struct
import subprocess
import sys
import zlib
from pathlib import Path

BLOCK = 4096
LOG_BLOCKS = 8192          # NativeUfs kLogReserveBlocks
EXPORT_BLOCKS = 65536      # NativeUfs kExportBlocks
HEADER = struct.Struct('<8sIIQIIIIIIQ')   # magic, version, header_bytes, log_first_lba, log_blocks, export_blocks, first, bytes, generation, pieces, uptime_ns
HEADER_BYTES = 64                          # sizeof(CacheExportHeader): the fields above, the CRC-32 of them, 4 bytes of padding
PARTITION = '/dev/block/by-name/unixnu'


def parse_header(block):
    fields = HEADER.unpack_from(block)
    (crc,) = struct.unpack_from('<I', block, HEADER.size)
    magic, version, header_bytes, log_first, log_blocks, export_blocks, first, size, generation, pieces, uptime = fields
    if magic != b'PGEXPRT1' or version != 1 or header_bytes != HEADER_BYTES:
        raise SystemExit('no export header in block 0 of the log window (no export yet, or the kernel lacks the patch)')
    if binascii.crc32(block[:HEADER.size]) & 0xffffffff != crc:
        raise SystemExit('export header CRC mismatch')
    if log_blocks != LOG_BLOCKS or export_blocks != EXPORT_BLOCKS or first not in (0, EXPORT_BLOCKS // 2) or not 16 < size <= EXPORT_BLOCKS // 2 * BLOCK:
        raise SystemExit('export header out of bounds')
    return dict(log_first_lba=log_first, first=first, bytes=size, generation=generation, pieces=pieces, uptime_s=uptime / 1e9)


def stream_of(blob):
    if blob[:8] == b'SHEXR001':
        raw_bytes, zero = struct.unpack_from('<II', blob, 8)
        raw = blob[16:16 + raw_bytes]
        if zero or len(raw) != raw_bytes:
            raise SystemExit('export blob truncated')
    elif blob[:8] == b'SHEXP001':
        raw_bytes, zbytes = struct.unpack_from('<II', blob, 8)
        raw = zlib.decompress(blob[16:16 + zbytes])
        if len(raw) != raw_bytes:
            raise SystemExit('pack stream length')
    else:
        raise SystemExit('not a cache export blob')
    return raw


def unpack(raw, out):
    if raw[:8] != b'M2VCACH1':
        raise SystemExit('not an M2VCACH1 stream')
    at = 8
    (root_len,) = struct.unpack_from('<I', raw, at)
    root = raw[at + 4:at + 4 + root_len].decode()
    at += 4 + root_len
    (count,) = struct.unpack_from('<I', raw, at)
    at += 4
    files = []
    for _ in range(count):
        plen, mode, uid, gid, size = struct.unpack_from('<IIIIQ', raw, at)
        at += 24
        rel = raw[at:at + plen].decode()
        at += plen
        if not rel or rel.startswith('/') or '..' in rel.split('/') or '\0' in rel:
            raise SystemExit('unsafe path in the export: ' + rel)
        data = raw[at:at + size]
        at += size
        if len(data) != size:
            raise SystemExit('export stream truncated in ' + rel)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(dict(path=rel, bytes=size))
    if at != len(raw):
        raise SystemExit('trailing bytes after the export stream')
    return root, files


def adb(command, binary=False):
    full = ['adb', 'exec-out', 'su', '-c', command]
    return subprocess.run(full, check=True, capture_output=True, text=not binary).stdout


def read_adb():
    total = int(adb('blockdev --getsize64 ' + PARTITION).strip())
    blocks = total // BLOCK
    window = blocks - LOG_BLOCKS
    region = window - EXPORT_BLOCKS
    header = adb('dd if=%s bs=%d skip=%d count=1 2>/dev/null' % (PARTITION, BLOCK, window), binary=True)
    facts = parse_header(header)
    count = (facts['bytes'] + BLOCK - 1) // BLOCK
    blob = adb('dd if=%s bs=%d skip=%d count=%d 2>/dev/null' % (PARTITION, BLOCK, region + facts['first'], count), binary=True)
    return facts, blob[:facts['bytes']]


def read_tail(path):
    data = Path(path).read_bytes()
    if len(data) != (EXPORT_BLOCKS + LOG_BLOCKS) * BLOCK:
        raise SystemExit('a tail file holds exactly the export region and the log window')
    facts = parse_header(data[EXPORT_BLOCKS * BLOCK:EXPORT_BLOCKS * BLOCK + BLOCK])
    start = facts['first'] * BLOCK
    return facts, data[start:start + facts['bytes']]


def make_test_tail(pack, path, generation=7, first=EXPORT_BLOCKS // 2):
    """What the patched kernel leaves after one export of PACK's files: the SHEXR001 blob in the upper half, the header in the window."""
    raw = stream_of(Path(pack).read_bytes())
    blob = b'SHEXR001' + struct.pack('<II', len(raw), 0) + raw
    tail = bytearray((EXPORT_BLOCKS + LOG_BLOCKS) * BLOCK)
    tail[first * BLOCK:first * BLOCK + len(blob)] = blob
    head = HEADER.pack(b'PGEXPRT1', 1, HEADER_BYTES, 123456789, LOG_BLOCKS, EXPORT_BLOCKS, first, len(blob), generation, 1, 42 * 10**9)
    head += struct.pack('<I', binascii.crc32(head) & 0xffffffff)
    tail[EXPORT_BLOCKS * BLOCK:EXPORT_BLOCKS * BLOCK + len(head)] = head
    Path(path).write_bytes(tail)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--adb', action='store_true')
    ap.add_argument('--tail', type=Path)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--make-test-tail', nargs=2, metavar=('PACK', 'FILE'))
    args = ap.parse_args()
    if args.make_test_tail:
        make_test_tail(*args.make_test_tail)
        return
    if not args.out or args.adb == bool(args.tail):
        raise SystemExit(__doc__)
    facts, blob = read_adb() if args.adb else read_tail(args.tail)
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit('the output directory is not empty')
    args.out.mkdir(parents=True, exist_ok=True)
    root, files = unpack(stream_of(blob), args.out)
    (args.out.parent / (args.out.name + '.export.json')).write_text(json.dumps(dict(facts, device_root=root, files=len(files),
        file_bytes=sum(f['bytes'] for f in files)), indent=2) + '\n')
    print('PASS: export generation %d (%d bytes, %.0f s after boot): %d files from %s into %s' % (
        facts['generation'], facts['bytes'], facts['uptime_s'], len(files), root, args.out))


if __name__ == '__main__':
    main()
