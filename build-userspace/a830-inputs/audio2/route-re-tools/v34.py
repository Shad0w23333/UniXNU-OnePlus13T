exec(open("cc.py").read())
seen = set()
def collect(ea, depth=0, maxd=2):
    f = ida_funcs.get_func(ea)
    if not f or f.start_ea in seen or depth > maxd: return
    seen.add(f.start_ea)
    cs = consts_in(f.start_ea)
    sel = sorted(set(c.split("@")[0].split("/")[0] for h, c in cs))
    calls = [n for h, t, n in calls_in(f.start_ea, show=False)]
    hal = sorted(set(n for n in calls if "AudioObject" in n or "AudioDevice" in n))
    p("%s%s consts=%s hal=%s" % ("  " * depth, fn(f.start_ea), sel[:24], hal))
    for h, t, n in calls_in(f.start_ea, show=False):
        g = ida_funcs.get_func(t)
        if g and idc.get_segm_name(t) and "text" in idc.get_segm_name(t) and not any(x in (n or "") for x in ("sub_61B0C", "sub_96D24", "sub_B568C", "sub_70950", "sub_61F5C", "sub_2B0E4")):
            collect(t, depth + 1, maxd)
for a in [0x1BDCC8, 0x1C4810, 0x1C4B90, 0x1C4EF0, 0x1820FC, 0x1C3FDC, 0x183344, 0x1C1B60]:
    collect(a, 0, 1)
