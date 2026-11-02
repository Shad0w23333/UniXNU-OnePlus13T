#!/usr/bin/env python3
"""tc.py BUNDLE CDHASH...: 解析 original-static-system.bundle / owner 信任包（版本 2 模块），打印 CDHash 条目的模块序号、类型、标志、约束类别。"""
import struct, sys
def modules(data):
    n, *offs = struct.unpack_from('<%dI' % (1 + struct.unpack_from('<I', data)[0]), data)
    offs = list(offs) + [len(data)]
    return [data[offs[i]:offs[i + 1]] for i in range(n)]
def entries(m):
    ver, = struct.unpack_from('<I', m)
    count, = struct.unpack_from('<I', m, 20)
    size = {1: 22, 2: 24}.get(ver, 24)
    for i in range(count):
        e = m[24 + i * size:24 + (i + 1) * size]
        yield e[:20].hex(), e[20], e[21], (e[22] if ver == 2 else None)
if __name__ == '__main__':
    data = open(sys.argv[1], 'rb').read()
    table = {}
    hist = {}
    for mi, m in enumerate(modules(data)):
        for h, t, f, c in entries(m):
            table[h] = (mi, t, f, c)
            hist[(mi, c)] = hist.get((mi, c), 0) + 1
    print('modules', len(modules(data)), 'entries', len(table))
    print('hist (module, category):', sorted(hist.items(), key=lambda k: (k[0][0], k[0][1] if k[0][1] is not None else -1)))
    for a in sys.argv[2:]:
        print(a, table.get(a.lower()))
