# libMobileGestalt: for each obfuscated key (env MG_KEYS, comma separated), find its CFString, everything that references the CFString,
# and dump the neighbourhood (qwords) of data references, resolving pointers to names.
import os
import idaapi, idc, idautils, ida_funcs, ida_bytes, ida_name, ida_segment, ida_hexrays

out = open(os.environ["DECOMP_OUT"], "a")
def log(s=""):
    out.write(s + "\n"); out.flush()

def name_of(v):
    n = ida_name.get_name(v)
    if n:
        return n
    f = ida_funcs.get_func(v)
    if f:
        return ida_funcs.get_func_name(f.start_ea) + "+0x%x" % (v - f.start_ea)
    s = ida_segment.getseg(v)
    if s:
        return "%s:0x%x" % (ida_segment.get_segm_name(s), v)
    return ""

keys = [k for k in os.environ.get("MG_KEYS", "").split(",") if k]
strs = {}
for s in idautils.Strings():
    v = str(s)
    if v in keys:
        strs[v] = s.ea
for k in keys:
    log("==== key %s" % k)
    sea = strs.get(k)
    if sea is None:
        log("  string not found"); continue
    for x in idautils.XrefsTo(sea):
        cf = x.frm
        seg = ida_segment.get_segm_name(ida_segment.getseg(cf))
        # CFString struct: cf points at the ptr field (+0x10) usually
        cfbase = cf - 0x10 if seg == "__cfstring" else cf
        log("  str ref from 0x%x (%s) cfstring base 0x%x" % (cf, seg, cfbase))
        for y in list(idautils.XrefsTo(cfbase)) + list(idautils.XrefsTo(cf)):
            fn = ida_funcs.get_func(y.frm)
            ys = ida_segment.get_segm_name(ida_segment.getseg(y.frm))
            log("    <- 0x%x seg %s func %s" % (y.frm, ys, ida_funcs.get_func_name(fn.start_ea) if fn else "-"))
            if fn is None:
                base = y.frm - 0x40
                for i in range(0, 0x80, 8):
                    q = ida_bytes.get_qword(base + i)
                    log("       %s0x%x: 0x%016x %s" % ("=>" if base + i == y.frm else "  ", base + i, q, name_of(q)))
out.close()
idc.qexit(0)
