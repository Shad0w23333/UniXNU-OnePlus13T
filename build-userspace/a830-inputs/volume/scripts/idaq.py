# IDAPython batch query: reads a JSON job (path in env IDAQ_JOB), writes text to the job's "out" path.
# Actions: {"strings": [substr...]} -> string hits with code xrefs and their functions
#          {"decompile": [addr...]} -> pseudocode of the function containing each address
#          {"xrefs": [addr...]} -> xrefs to each address
#          {"disasm": [[start, count]...]}
#          {"funcs_calling": [addr...]} -> functions that call addr
import json, os, re
import idaapi, idautils, idc, ida_funcs, ida_hexrays, ida_auto, ida_bytes, ida_name, ida_xref, ida_pro

ida_auto.auto_wait()
job = json.load(open(os.environ["IDAQ_JOB"]))
out = open(job["out"], "w")
def p(*a):
    out.write(" ".join(str(x) for x in a) + "\n")

def fname(ea):
    f = ida_funcs.get_func(ea)
    if not f:
        return "nofunc"
    n = ida_name.get_name(f.start_ea)
    return "%s@%x" % (n, f.start_ea)

def to_ea(v):
    return int(v, 16) if isinstance(v, str) else int(v)

if job.get("strings"):
    pats = job["strings"]
    regex = job.get("regex", False)
    sc = idautils.Strings()
    sc.setup(strtypes=[0], minlen=4)
    for s in sc:
        t = str(s)
        hit = any((re.search(x, t) if regex else (x in t)) for x in pats)
        if not hit:
            continue
        p("STR %x %r" % (s.ea, t[:300]))
        for x in idautils.XrefsTo(s.ea):
            p("   xref from %x in %s" % (x.frm, fname(x.frm)))
            # also look for pointers to the string (cfstrings) and their xrefs
        for x in idautils.XrefsTo(s.ea):
            if not ida_funcs.get_func(x.frm):
                for y in idautils.XrefsTo(x.frm):
                    p("      via %x: xref from %x in %s" % (x.frm, y.frm, fname(y.frm)))

if job.get("decompile"):
    try:
        ida_hexrays.init_hexrays_plugin()
        ida_hexrays.change_hexrays_config("MAX_FUNCSIZE = 4096")
    except Exception as e:
        p("hexrays init failed", e)
    seen = set()
    for a in job["decompile"]:
        ea = to_ea(a)
        f = ida_funcs.get_func(ea)
        if not f or f.start_ea in seen:
            if not f:
                p("NO FUNC at %x" % ea)
            continue
        seen.add(f.start_ea)
        p("=" * 100)
        p("FUNC %s size %x" % (fname(f.start_ea), f.end_ea - f.start_ea))
        try:
            hf = ida_hexrays.hexrays_failure_t()
            c = ida_hexrays.decompile(f.start_ea, hf)
            if c is None:
                p("decompile failed:", hf.str, hex(hf.errea))
            else:
                p(str(c))
        except Exception as e:
            p("decompile failed:", e)

if job.get("xrefs"):
    for a in job["xrefs"]:
        ea = to_ea(a)
        p("XREFS to %x (%s)" % (ea, ida_name.get_name(ea)))
        for x in idautils.XrefsTo(ea):
            p("   from %x type %d in %s" % (x.frm, x.type, fname(x.frm)))

if job.get("funcs_calling"):
    for a in job["funcs_calling"]:
        ea = to_ea(a)
        fs = set()
        for x in idautils.CodeRefsTo(ea, 0):
            fs.add(fname(x))
        p("CALLERS of %x: %s" % (ea, sorted(fs)))

if job.get("disasm"):
    for a, n in job["disasm"]:
        ea = to_ea(a)
        p("DISASM %x" % ea)
        for i in range(int(n)):
            p("  %x: %s" % (ea, idc.GetDisasm(ea)))
            ea = idc.next_head(ea, idc.BADADDR)

if job.get("names"):
    for a, n in job["names"].items():
        ida_name.set_name(to_ea(a), n, ida_name.SN_NOWARN | ida_name.SN_FORCE)
    idaapi.save_database(None, 0) if hasattr(idaapi, "save_database") else None

if job.get("bytes_search"):
    for pat in job["bytes_search"]:
        # pat: hex string like "63 73 61 76"
        ea = 0
        p("BYTES %s" % pat)
        cnt = 0
        start = idaapi.inf_get_min_ea()
        end = idaapi.inf_get_max_ea()
        res = ida_bytes.find_bytes(pat, start, end)
        while res != idc.BADADDR and cnt < 200:
            p("   at %x seg %s in %s" % (res, idc.get_segm_name(res), fname(res)))
            cnt += 1
            res = ida_bytes.find_bytes(pat, res + 1, end)

if job.get("imm_search"):
    # find instructions with an immediate operand equal to value (mov/movk pairs are not combined)
    vals = [to_ea(v) for v in job["imm_search"]]
    for v in vals:
        p("IMM %x" % v)
        cnt = 0
        for seg in idautils.Segments():
            if idc.get_segm_name(seg) not in ("__text", "__TEXT.__text", "__auth_stubs"):
                continue
            for head in idautils.Heads(seg, idc.get_segm_end(seg)):
                if not idc.is_code(idc.get_full_flags(head)):
                    continue
                for i in range(3):
                    t = idc.get_operand_type(head, i)
                    if t == idc.o_imm and (idc.get_operand_value(head, i) & 0xffffffff) == v:
                        p("   %x %s in %s" % (head, idc.GetDisasm(head), fname(head)))
                        cnt += 1
                if cnt > 300:
                    break
out.close()
ida_pro.qexit(0)
