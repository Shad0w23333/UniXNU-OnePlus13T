#!/usr/bin/env python3
"""dsym.py IN > OUT：把反编译文本里的 MEMORY[0x...]、0x201E...、stru_1E... 这类地址，用 dsc-21H461.a2s 符号表（ipsw 的 gob 不便读，这里调 ipsw dyld a2s 批量查）和
DSC 内容解析成名字。第二个参数给缓存文件（json）。"""
import json, os, re, subprocess, sys, struct
DSC = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'
A2S = '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/dsc-21H461.a2s'
txt = open(sys.argv[1]).read()
cache_path = sys.argv[2] if len(sys.argv) > 2 else None
cache = json.load(open(cache_path)) if cache_path and os.path.exists(cache_path) else {}
addrs = set(int(a, 16) for a in re.findall(r'MEMORY\[0x([0-9A-Fa-f]+)\]', txt))
todo = [a for a in sorted(addrs) if hex(a) not in cache]
for a in todo:
    r = subprocess.run(['ipsw', 'dyld', 'a2s', DSC, hex(a), '--cache', A2S], capture_output=True, text=True)
    line = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.startswith('0x')]
    name = line[-1].split(': ', 1)[-1].strip() if line else '?'
    cache[hex(a)] = name.replace('_stub', '')
if cache_path:
    json.dump(cache, open(cache_path, 'w'), indent=0)
def sub(m):
    a = int(m.group(1), 16)
    return cache.get(hex(a), m.group(0))
sys.stdout.write(re.sub(r'MEMORY\[0x([0-9A-Fa-f]+)\]', sub, txt))
