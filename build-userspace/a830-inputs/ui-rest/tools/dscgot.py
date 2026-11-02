#!/usr/bin/env python3
"""dscgot.py SLOT...：在 DSC 全部可执行映射里找 adrp+ldr(后 8 条指令内) 读取 GOT 槽 SLOT 的代码，按镜像汇总。"""
import glob, mmap, os, struct, sys
import numpy as np
D = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/'
slots = [int(a, 16) for a in sys.argv[1:]]
pages = {s & ~0xfff: [] for s in slots}
files, maps = [], []
for f in sorted(glob.glob(D + 'dyld_shared_cache_arm64*')):
    if f.endswith(('.map', '.atlas', '.symbols')): continue
    fh = open(f, 'rb'); h = fh.read(0x200)
    if h[:6] != b'dyld_v': continue
    mo, mc = struct.unpack_from('<II', h, 0x10)
    m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    files.append((fh, m))
    for i in range(mc):
        addr, size, off, maxp, initp = struct.unpack_from('<QQQII', m, mo + i * 32)
        maps.append((addr, size, off, initp, m))
    if f.endswith('arm64'):
        main = (m, h)
def rd(a, n):
    for addr, size, off, ip, m in maps:
        if addr <= a < addr + size:
            o = off + a - addr
            return m[o:o + n]
m0, h0 = main
imgOff, imgCnt = struct.unpack_from('<II', h0, 0x1c0)
if imgCnt == 0: imgOff, imgCnt = struct.unpack_from('<II', h0, 0x18)
segs = []
for i in range(imgCnt):
    addr, modt, ino, pathOff, pad = struct.unpack_from('<QQQII', m0, imgOff + i * 32)
    p = m0[pathOff:m0.find(b'\0', pathOff)].decode()
    hdr = rd(addr, 0x8000)
    if not hdr: continue
    n = struct.unpack_from('<I', hdr, 16)[0]; o = 32
    for _ in range(n):
        c, s = struct.unpack_from('<II', hdr, o)
        if c == 0x19:
            g = hdr[o + 8:o + 24].rstrip(b'\0').decode()
            vm, vs = struct.unpack_from('<QQ', hdr, o + 24)
            if g.startswith('__TEXT'):
                segs.append((vm, vm + vs, p))
        o += s
segs.sort()
lo = np.array([s[0] for s in segs], dtype=np.uint64)
res = {s: {} for s in slots}
for addr, size, off, ip, m in maps:
    if not ip & 4: continue
    w = np.frombuffer(m, dtype='<u4', count=size // 4, offset=off)
    isadrp = (w & 0x9f000000) == 0x90000000
    idx = np.nonzero(isadrp)[0]
    ww = w[idx].astype(np.int64)
    immlo = (ww >> 29) & 3; immhi = (ww >> 5) & 0x7ffff
    imm = (immhi << 2) | immlo
    imm = np.where(imm & (1 << 20), imm - (1 << 21), imm)
    pc = addr + idx.astype(np.int64) * 4
    tgt = (pc & ~0xfff) + (imm << 12)
    for page in pages:
        for i in idx[tgt == page]:
            rd_ = int(w[i]) & 31
            for j in range(i + 1, min(i + 10, len(w))):
                x = int(w[j])
                if (x & 0xffc00000) == 0xf9400000 and ((x >> 5) & 31) == rd_:
                    t = page + (((x >> 10) & 0xfff) << 3)
                    if t in res:
                        pcv = addr + int(i) * 4
                        k = int(np.searchsorted(lo, pcv, side='right')) - 1
                        p = segs[k][2] if k >= 0 and pcv < segs[k][1] else '?'
                        res[t].setdefault(p, []).append(pcv)
                    break
    del w
for s in slots:
    print(f'== 槽 {s:#x}')
    for p, v in sorted(res[s].items(), key=lambda kv: -len(kv[1])):
        print(f'   {len(v):3d} {p}  {hex(v[0])}')
