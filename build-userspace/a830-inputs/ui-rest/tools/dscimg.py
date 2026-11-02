#!/usr/bin/env python3
"""dscimg.py ADDR...：解析 DSC 里各镜像的 Mach-O 段，把地址归属到镜像（读主缓存 images 表与子缓存映射）。"""
import glob, mmap, os, struct, sys
D = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/'
maps = []
main = None
for f in sorted(glob.glob(D + 'dyld_shared_cache_arm64*')):
    if f.endswith(('.map', '.atlas', '.symbols')): continue
    fh = open(f, 'rb')
    h = fh.read(0x200)
    if h[:6] != b'dyld_v': continue
    mapOff, mapCnt = struct.unpack_from('<II', h, 0x10)
    fh.seek(mapOff)
    m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    for i in range(mapCnt):
        addr, size, off, maxp, initp = struct.unpack_from('<QQQII', m, mapOff + i * 32)
        maps.append((addr, size, off, m))
    if f.endswith('arm64'):
        main = (m, h)
def rd(a, n):
    for addr, size, off, m in maps:
        if addr <= a < addr + size:
            o = off + a - addr
            return m[o:o + n]
    return None
m, h = main
imgOff, imgCnt = struct.unpack_from('<II', h, 0x1c0)
if imgCnt == 0:
    imgOff, imgCnt = struct.unpack_from('<II', h, 0x18)
segs = []
for i in range(imgCnt):
    addr, modt, ino, pathOff, pad = struct.unpack_from('<QQQII', m, imgOff + i * 32)
    p = m[pathOff:m.find(b'\0', pathOff)].decode()
    hdr = rd(addr, 0x8000)
    if not hdr: continue
    n = struct.unpack_from('<I', hdr, 16)[0]
    o = 32
    for _ in range(n):
        c, s = struct.unpack_from('<II', hdr, o)
        if c == 0x19:
            seg = hdr[o + 8:o + 24].rstrip(b'\0').decode()
            vm, vs = struct.unpack_from('<QQ', hdr, o + 24)
            segs.append((vm, vm + vs, seg, p))
        o += s
for a in sys.argv[1:]:
    a = int(a, 16)
    hit = [(g, p) for lo, hi, g, p in segs if lo <= a < hi]
    print(hex(a), hit[:2])
