#!/usr/bin/env python3
"""dscread.py ADDR LEN：从 DSC 读未滑动地址的字节（十六进制 + 8 字节小端）。"""
import glob, mmap, struct, sys
D = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/'
maps = []
for f in sorted(glob.glob(D + 'dyld_shared_cache_arm64*')):
    if f.endswith(('.map', '.atlas', '.symbols')): continue
    fh = open(f, 'rb'); h = fh.read(0x200)
    if h[:6] != b'dyld_v': continue
    mo, mc = struct.unpack_from('<II', h, 0x10)
    m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    for i in range(mc):
        maps.append(struct.unpack_from('<QQQII', m, mo + i * 32) + (m, f))
a, n = int(sys.argv[1], 16), int(sys.argv[2], 0)
for addr, size, off, mp, ip, m, f in maps:
    if addr <= a < addr + size:
        o = off + a - addr
        b = m[o:o + n]
        print(f.rsplit('/', 1)[1], hex(o), b.hex())
        print([hex(struct.unpack_from('<Q', b, i)[0]) for i in range(0, n - 7, 8)])
