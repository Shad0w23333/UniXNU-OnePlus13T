#!/usr/bin/env python3
"""lc-model.py：用 AMFI 的约束类别表（lwcr-table.py 打印的结果）和信任包，给出在 13T 的卷布局下哪些主程序会被启动约束杀掉。
卷事实（AMFI sub_FFFFFFF005AB8D9C / sub_FFFFFFF005AB8CF0 + APFS sub_FFFFFFF0066E39D0）：
  根卷：on-system-volume=真（MNT_ROOTFS）。SystemOS（联合上层，无 MNT_ROOTFS）与其 bindfs 视图、Data：两项都假。
类别 -> 是否要卷事实、是否强制（sub_FFFFFFF005AAD0A4 + sub_FFFFFFF005AAEEB0：掩码 1/2 强制，4/8 只记日志）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tc
NEEDS_VOLUME = {1, 2, 3, 4, 6, 8, 9, 10, 11, 12, 14, 15, 18, 19, 20}
MASK = {0: 1, 2: 1, 18: 1, 19: 1, 1: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 4, 9: 4, 10: 4, 11: 4, 12: 4, 13: 4, 17: 4, 14: 8, 15: 8, 16: 8, 20: 8}
def enforced(c):
    return (MASK.get(c, 1) & 3) != 0
d = open(sys.argv[1], 'rb').read()
t = {}
for mi, m in enumerate(tc.modules(d)):
    for h, ty, fl, c in tc.entries(m):
        t[h] = (mi, c)
rows = []
for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cdmap.tsv')):
    h, i, p = l.rstrip('\n').split('\t')
    if h not in t:
        continue
    mi, c = t[h]
    onsys = p.startswith('systemos-21H461')
    ondata = '/private/var/' in p
    rows.append((mi, c, onsys, ondata, i, p))
for name, cond in (('SystemOS 上的二进制', lambda r: r[2]), ('Data 上（staged 应用）', lambda r: r[3])):
    sel = [r for r in rows if cond(r)]
    print(f'== {name}: {len(sel)}')
    bad = [r for r in sel if r[1] in NEEDS_VOLUME and enforced(r[1])]
    log = [r for r in sel if r[1] in NEEDS_VOLUME and not enforced(r[1])]
    print(f'   要卷事实且强制（会被 SIGKILL）: {len(bad)}')
    for r in sorted(bad, key=lambda r: r[5])[:60]:
        print(f'     cat {r[1]:2d} {r[5].replace("systemos-21H461", "SYS").replace("osmnt-21H461", "")}')
    print(f'   要卷事实但只记日志: {len(log)}')
    for r in sorted(log, key=lambda r: r[5])[:12]:
        print(f'     cat {r[1]:2d} {r[5].replace("systemos-21H461", "SYS").replace("osmnt-21H461", "")}')
hist = {}
for r in rows:
    if r[3]:
        hist[r[1]] = hist.get(r[1], 0) + 1
print('Data 上类别分布', sorted(hist.items()))
want = ['ClockPosterExtension', 'CollectionsPoster', 'com.apple.Safari.SearchHelper', 'webprivacyd']
for r in rows:
    if os.path.basename(r[5]) in want:
        print(r[5], 'module', r[0], 'cat', r[1])
