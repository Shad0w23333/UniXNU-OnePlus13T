#!/usr/bin/env python3
"""dscfind.py STRING...：在 DSC 只读映射里找字节串，打印所在未滑动地址（最多 5 个）。"""
import glob, mmap, struct, sys
D = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/'
for s in sys.argv[1:]:
    b = s.encode(); out = []
    for f in sorted(glob.glob(D + 'dyld_shared_cache_arm64*')):
        if f.endswith(('.map', '.atlas', '.symbols', 'linkedit')): continue
        fh = open(f, 'rb'); h = fh.read(0x200)
        if h[:6] != b'dyld_v': continue
        mo, mc = struct.unpack_from('<II', h, 0x10)
        m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        for i in range(mc):
            addr, size, off, mp, ip = struct.unpack_from('<QQQII', m, mo + i * 32)
            p = off
            while len(out) < 5:
                q = m.find(b, p, off + size)
                if q < 0: break
                out.append(addr + q - off)
                p = q + 1
    print(s, [hex(x) for x in out])
