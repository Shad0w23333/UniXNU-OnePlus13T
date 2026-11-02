import sys
sys.path.insert(0, 'ui-rest/tools')
import tc
d = open('../../作者产物/build-63/original-static-system.bundle', 'rb').read()
t = {}
for mi, m in enumerate(tc.modules(d)):
    for h, ty, fl, c in tc.entries(m):
        t[h] = (mi, c)
seen = {}
for l in open('ui-rest/tools/cdmap.tsv'):
    h, i, p = l.rstrip('\n').split('\t')
    if h in t:
        seen[h] = (t[h], i, p)
by = {}
for h, ((mi, c), i, p) in seen.items():
    by.setdefault((mi, c), []).append(p)
for k in sorted(by):
    print(k, len(by[k]))
    for p in sorted(by[k])[:int(sys.argv[1]) if len(sys.argv) > 1 else 14]:
        print('   ', p.replace('systemos-21H461', 'SYS').replace('osmnt-21H461', ''))
print('unmatched TC', len(set(t) - set(seen)))
