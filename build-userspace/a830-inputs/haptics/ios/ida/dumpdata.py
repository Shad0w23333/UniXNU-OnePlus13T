import os, idc, ida_bytes, ida_name, ida_funcs, ida_segment, idautils
out = open(os.environ["DECOMP_OUT"], "a")
def nm(v):
    n = ida_name.get_name(v)
    if n: return n
    f = ida_funcs.get_func(v)
    if f: return ida_funcs.get_func_name(f.start_ea) + "+0x%x" % (v - f.start_ea)
    s = idc.get_strlit_contents(v)
    if s: return '"%s"' % s.decode('latin1')[:60]
    return ""
for spec in os.environ["DUMP_RANGES"].split(","):
    a, n = spec.split(":")
    a = int(a, 16); n = int(n)
    out.write("==== data at 0x%x\n" % a)
    for i in range(n):
        ea = a + 8 * i
        q = ida_bytes.get_qword(ea)
        refs = [hex(x.frm) for x in idautils.XrefsTo(ea)][:3]
        out.write("0x%x: 0x%016x %s %s\n" % (ea, q, nm(q), ("<- " + ",".join(refs)) if refs else ""))
out.close()
idc.qexit(0)
