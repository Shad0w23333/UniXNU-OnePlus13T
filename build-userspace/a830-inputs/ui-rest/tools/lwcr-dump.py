#!/usr/bin/env python3
"""从 kernelcache 里定位 AMFI 内嵌的 LWCR（CoreEntitlements DER）约束块并打印。"""
import re, sys
p = sys.argv[1]
d = open(p, 'rb').read()
def rd(b, i):
    t = b[i]; i += 1
    l = b[i]; i += 1
    if l & 0x80:
        n = l & 0x7f
        l = int.from_bytes(b[i:i + n], 'big'); i += n
    return t, l, i
def show(b, i, end, ind=0):
    out = []
    while i < end:
        t, l, j = rd(b, i)
        if j + l > end:
            raise ValueError('overrun')
        v = b[j:j + l]
        if t == 0x0c:
            out.append(repr(v.decode()))
        elif t == 0x01:
            out.append('true' if v != b'\0' else 'false')
        elif t == 0x02:
            out.append(str(int.from_bytes(v, 'big', signed=True)))
        elif t == 0x04:
            out.append('hex:' + v.hex())
        elif t in (0x30,):
            kv = show(b, j, j + l, ind + 1)
            out.append('(' + ' : '.join(kv) + ')')
        elif t in (0xb0, 0x31, 0x70):
            kv = show(b, j, j + l, ind + 1)
            out.append(('{' if t == 0xb0 else '[' if t == 0x31 else 'P<') + ', '.join(kv) + ('}' if t == 0xb0 else ']' if t == 0x31 else '>'))
        else:
            out.append(f'T{t:02x}:{v.hex()}')
        i = j + l
    return out
seen = set()
for m in re.finditer(b'\x0c\x1don-authorized-authapfs-volume', d):
    # 往回找外层 0x70 或 0xb0
    for back in range(m.start() - 1, max(m.start() - 400, 0), -1):
        if d[back] in (0x70,):
            try:
                t, l, j = rd(d, back)
                if j + l > m.end() and l < 2000:
                    if back in seen:
                        break
                    r = show(d, j, j + l)
                    seen.add(back)
                    print(hex(back), l, ' '.join(r))
                    break
            except Exception as e:
                continue
