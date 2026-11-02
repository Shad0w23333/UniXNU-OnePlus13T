#!/usr/bin/env python3
"""dscoff.py VA [LEN]：把 DSC 未滑动地址换算成 子缓存文件名 + 文件内偏移，并打印该处字节（文本修补 text-fixup 的三元组就用这个）。"""
import glob, mmap, os, struct, sys
D = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/'
va = int(sys.argv[1], 16); n = int(sys.argv[2], 0) if len(sys.argv) > 2 else 16
for f in sorted(glob.glob(D + 'dyld_shared_cache_arm64*')):
    if f.endswith(('.map', '.atlas', '.symbols')): continue
    fh = open(f, 'rb'); h = fh.read(0x200)
    if h[:6] != b'dyld_v': continue
    mo, mc = struct.unpack_from('<II', h, 0x10)
    fh.seek(mo); ms = [struct.unpack('<QQQII', fh.read(32)) for _ in range(mc)]
    for addr, size, off, mp, ip in ms:
        if addr <= va < addr + size:
            o = off + va - addr
            fh.seek(o)
            b = fh.read(n)
            print(f'{os.path.basename(f)},{o:x},{b.hex()}  (映射 {addr:#x}+{size:#x} 文件偏移 {off:#x} 权限 {ip})')
