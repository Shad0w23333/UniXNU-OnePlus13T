import re
ida_hexrays.init_hexrays_plugin()
out = []
for ea, nm in idautils.Names():
    if not nm.startswith("InitFunc_"):
        continue
    try:
        c = ida_hexrays.decompile(ea)
    except Exception:
        continue
    if c is None: continue
    t = str(c)
    cur = None
    for line in t.split("\n"):
        m = re.search(r'qmemcpy\(&(\w+), "(.{12})", 12\)', line)
        if m:
            s = m.group(2)
            cur = (m.group(1), s[0:4][::-1], s[4:8][::-1], s[8:12][::-1])
            continue
        m2 = re.search(r'(word_\w+) = \1 & 0xF800 \| (0x[0-9A-Fa-f]+|\d+);', line)
        if m2 and cur:
            out.append((nm, cur[0], cur[1], cur[2], cur[3], int(m2.group(2), 0)))
            cur = None
for nm, g, t, c, e, fl in out:
    p("%-14s %-14s %s conn=%s %s flags=0x%03x%s" % (nm, g, t, c, e, fl, "  <-- 0x8" if fl & 8 else ""))
p("total", len(out))
