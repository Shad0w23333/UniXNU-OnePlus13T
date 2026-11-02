#!/usr/bin/env python3
"""dscref.py ADDR...：在 dyld_shared_cache_arm64* 所有子缓存的可写/常量映射里，扫 8 字节对齐、低 36 位等于 ADDR 的指针（GOT 引用），
输出未滑动地址。再由调用方用 ipsw 把地址归到镜像。"""
import glob, mmap, os, struct, sys
import numpy as np
D = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/'
targets = [int(a, 16) for a in sys.argv[1:]]
for f in sorted(glob.glob(D + 'dyld_shared_cache_arm64*')):
    if f.endswith('.map') or f.endswith('.symbols') or f.endswith('.atlas'):
        continue
    with open(f, 'rb') as fh:
        h = fh.read(0x200)
        if h[:6] != b'dyld_v':
            continue
        mapOff, mapCnt = struct.unpack_from('<II', h, 0x10)
        fh.seek(mapOff)
        maps = [struct.unpack('<QQQII', fh.read(32)) for _ in range(mapCnt)]
        m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        for addr, size, off, maxp, initp in maps:
            if initp & 4:   # 跳过可执行映射，其余都扫（唯一化的 GOT 在只读映射里）
                continue
            a = np.frombuffer(m, dtype='<u8', count=size // 8, offset=off)
            low = a & ((1 << 36) - 1)
            for t in targets:
                for i in np.nonzero(low == t)[0][:4000]:
                    print(f'{t:#x} {addr + int(i) * 8:#x} {os.path.basename(f)}')
            del a, low
        m.close()
