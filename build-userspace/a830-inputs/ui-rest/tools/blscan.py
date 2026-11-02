#!/usr/bin/env python3
"""blscan.py BIN CACHE.json REGEX：扫描从 DSC 抽出的 Mach-O 的 __TEXT,__text，把跳出本镜像的 BL/B 目标用 ipsw a2s 解析成名字（缓存到 json），
打印名字匹配 REGEX 的调用点。"""
import json, os, re, struct, subprocess, sys
DSC = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'
A2S = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/dsc-21H461.a2s'
bin_path, cache_path, regex = sys.argv[1:4]
d = open(bin_path, 'rb').read()
ncmds = struct.unpack_from('<I', d, 16)[0]
o = 32
text = None
lo, hi = 1 << 62, 0
for _ in range(ncmds):
    c, s = struct.unpack_from('<II', d, o)
    if c == 0x19:
        seg = d[o + 8:o + 24].rstrip(b'\0').decode()
        vm, vs, fo, fs, _, _, ns, _ = struct.unpack_from('<4Q4I', d, o + 24)
        if seg in ('__TEXT', '__TEXT_EXEC'):
            lo = min(lo, vm); hi = max(hi, vm + vs)
        p = o + 72
        for i in range(ns):
            sect = d[p:p + 16].rstrip(b'\0').decode()
            addr, size, off = struct.unpack_from('<QQI', d, p + 32)
            if sect == '__text':
                text = (addr, size, off)
            p += 80
    o += s
addr, size, off = text
cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
calls = {}
for i in range(0, size, 4):
    w = struct.unpack_from('<I', d, off + i)[0]
    if (w & 0xfc000000) in (0x94000000, 0x14000000):
        imm = w & 0x3ffffff
        if imm & 0x2000000:
            imm -= 0x4000000
        t = addr + i + imm * 4
        if not (lo <= t < hi):
            calls.setdefault(t, []).append(addr + i)
todo = [t for t in calls if hex(t) not in cache]
print('外部目标', len(calls), '待解析', len(todo), file=sys.stderr)
for t in todo:
    r = subprocess.run(['ipsw', 'dyld', 'a2s', DSC, hex(t), '--cache', A2S], capture_output=True, text=True)
    line = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.startswith('0x')]
    cache[hex(t)] = line[-1].split(': ', 1)[-1].strip() if line else '?'
json.dump(cache, open(cache_path, 'w'), indent=0)
rx = re.compile(regex)
for t, sites in sorted(calls.items()):
    n = cache.get(hex(t), '?')
    if rx.search(n):
        print(f'{n} {t:#x}: ' + ' '.join(hex(x) for x in sites[:12]) + (' ...' if len(sites) > 12 else ''))
