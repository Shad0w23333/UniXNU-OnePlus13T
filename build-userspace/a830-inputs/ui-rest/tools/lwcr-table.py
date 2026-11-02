#!/usr/bin/env python3
"""打印 AMFI 约束类别表：类别 -> self/parent 的 LWCR（地址来自 sub_FFFFFFF005AACCD0 / sub_FFFFFFF005AACF1C 的反编译）。"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import importlib.util
p = sys.argv[1]
d = open(p, 'rb').read()
BASE = 0xfffffff00527c000 - 0xa50000
def rd(b, i):
    t = b[i]; i += 1
    l = b[i]; i += 1
    if l & 0x80:
        n = l & 0x7f
        l = int.from_bytes(b[i:i + n], 'big'); i += n
    return t, l, i
def show(b, i, end):
    out = []
    while i < end:
        t, l, j = rd(b, i)
        v = b[j:j + l]
        if t == 0x0c: out.append(repr(v.decode()))
        elif t == 0x01: out.append('true' if v != b'\0' else 'false')
        elif t == 0x02: out.append(str(int.from_bytes(v, 'big', signed=True)))
        elif t == 0x30:
            kv = show(b, j, j + l); out.append('(' + ' : '.join(kv) + ')')
        elif t == 0xb0:
            out.append('{' + ', '.join(show(b, j, j + l)) + '}')
        elif t == 0x70:
            out.append('<' + ' '.join(show(b, j, j + l)) + '>')
        else: out.append(f'T{t:02x}:{v.hex()}')
        i = j + l
    return out
def blob(va):
    o = va - BASE
    t, l, j = rd(d, o)
    return ' '.join(show(d, o, j + l))
self_t = {1: 0xFFFFFFF0052E4FF3, 2: 0xFFFFFFF0052E506A, 19: 0xFFFFFFF0052E506A, 3: 0xFFFFFFF0052E50B5, 4: 0xFFFFFFF0052E513B, 5: 0xFFFFFFF0052E51C1, 6: 0xFFFFFFF0052E51E2,
          7: 0xFFFFFFF0052E5297, 8: 0xFFFFFFF0052E52B8, 9: 0xFFFFFFF0052E532F, 10: 0xFFFFFFF0052E540A, 11: 0xFFFFFFF0052E5490, 12: 0xFFFFFFF0052E54F5, 13: 0xFFFFFFF0052E557F,
          14: 0xFFFFFFF0052E55C0, 15: 0xFFFFFFF0052E5637, 16: 0xFFFFFFF0052E56BD, 17: 0xFFFFFFF0052E56F0, 18: 0xFFFFFFF0052E57AA, 20: 0xFFFFFFF0052E5824}
par_t = {1: 0xFFFFFFF0052E58AE, 4: 0xFFFFFFF0052E58C8, 5: 0xFFFFFFF0052E5955, 6: 0xFFFFFFF0052E59E2, 8: 0xFFFFFFF0052E5A7F, 17: 0xFFFFFFF0052E5A7F, 11: 0xFFFFFFF0052E5B14,
         14: 0xFFFFFFF0052E5CD4, 15: 0xFFFFFFF0052E5CEE, 16: 0xFFFFFFF0052E5D08, 18: 0xFFFFFFF0052E5D22}
for c in sorted(self_t):
    print(f'cat {c} self  {blob(self_t[c])}')
    if c in par_t:
        print(f'cat {c} parent {blob(par_t[c])}')
