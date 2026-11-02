# IDAPython batch helper (UniXNU haptics/ios research).
# Environment:
#   DECOMP_TARGETS  file with one target per line: a symbol name, a 0x address, or "xref:<name|0xaddr>" (list callers),
#                   or "str:<text>" (functions that reference a C string containing <text>), "dis:<name|0xaddr>" (disassembly).
#   DECOMP_OUT      output text file (appended)
#   DECOMP_FULL     "1": wait for full auto-analysis first (slow on big binaries); otherwise analyse on demand.
import os, sys, re
import idaapi, idc, idautils, ida_auto, ida_funcs, ida_name, ida_hexrays, ida_bytes, ida_segment, ida_xref, ida_nalt

out_path = os.environ.get("DECOMP_OUT", "/dev/stdout")
out = open(out_path, "a")

def log(s=""):
    out.write(s + "\n"); out.flush()

def resolve(t):
    t = t.strip()
    if t.startswith("0x"):
        return int(t, 16)
    ea = ida_name.get_name_ea(idaapi.BADADDR, t)
    if ea == idaapi.BADADDR:
        # try demangled/objc variants
        for n, e in [(n, e) for e, n in idautils.Names()]:
            if n == t:
                return e
    return ea

def ensure_func(ea):
    f = ida_funcs.get_func(ea)
    if f is None:
        ida_funcs.add_func(ea)
        ida_auto.plan_and_wait(ea, ea + 0x4000)
        f = ida_funcs.get_func(ea)
    return f

def decomp(ea):
    f = ensure_func(ea)
    if f is None:
        return "<no function at 0x%x>" % ea
    try:
        c = ida_hexrays.decompile(f.start_ea)
        return str(c) if c else "<decompile failed>"
    except Exception as e:
        return "<decompile error %s>" % e

def disasm(ea, maxn=400):
    f = ensure_func(ea)
    lines = []
    if f is None:
        cur = ea
        for _ in range(maxn):
            lines.append("%x  %s" % (cur, idc.GetDisasm(cur)))
            cur = idc.next_head(cur, cur + 16)
        return "\n".join(lines)
    for h in idautils.Heads(f.start_ea, f.end_ea):
        lines.append("%x  %s" % (h, idc.GetDisasm(h)))
    return "\n".join(lines)

def main():
    if os.environ.get("DECOMP_FULL") == "1":
        ida_auto.auto_wait()
    if not ida_hexrays.init_hexrays_plugin():
        log("!! no decompiler")
    tfile = os.environ.get("DECOMP_TARGETS")
    targets = [l.rstrip("\n") for l in open(tfile)] if tfile else []
    for t in targets:
        if not t.strip() or t.startswith("#"):
            continue
        if t.startswith("xref:"):
            ea = resolve(t[5:])
            log("==== XREFS to %s (0x%x)" % (t[5:], ea if ea != idaapi.BADADDR else 0))
            if ea == idaapi.BADADDR:
                continue
            for x in idautils.XrefsTo(ea):
                fn = ida_funcs.get_func(x.frm)
                log("  0x%x in %s" % (x.frm, ida_funcs.get_func_name(fn.start_ea) if fn else "?"))
            continue
        if t.startswith("dis:"):
            ea = resolve(t[4:])
            log("==== DISASM %s (0x%x)" % (t[4:], ea))
            if ea != idaapi.BADADDR:
                log(disasm(ea))
            continue
        if t.startswith("str:"):
            needle = t[4:]
            log("==== STRING refs '%s'" % needle)
            for s in idautils.Strings():
                v = str(s)
                if needle in v:
                    for x in idautils.XrefsTo(s.ea):
                        fn = ida_funcs.get_func(x.frm)
                        log("  str@0x%x '%s' <- 0x%x %s" % (s.ea, v[:80], x.frm, ida_funcs.get_func_name(fn.start_ea) if fn else "?"))
                        if fn is None:
                            seg = ida_segment.getseg(x.frm)
                            if seg and ida_segment.get_segm_name(seg) == "__cfstring":
                                for base in (x.frm - 0x10, x.frm):
                                    for y in idautils.XrefsTo(base):
                                        fy = ida_funcs.get_func(y.frm)
                                        log("      cfstr@0x%x <- 0x%x %s" % (base, y.frm, ida_funcs.get_func_name(fy.start_ea) if fy else "?"))
            continue
        ea = resolve(t)
        log("==== %s (0x%x)" % (t, ea if ea != idaapi.BADADDR else 0))
        if ea == idaapi.BADADDR:
            log("<not found>")
            continue
        log(decomp(ea))
    out.close()

main()
idc.qexit(0)
