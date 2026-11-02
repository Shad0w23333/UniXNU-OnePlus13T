# usage: python3.13 q.py DB.i64 snippet.py [out.txt]
# Opens an IDA database with idalib (no save), defines helpers, runs the snippet.
import sys, os, io, re, json
import idapro
db, script = sys.argv[1], sys.argv[2]
outp = sys.argv[3] if len(sys.argv) > 3 else None
idapro.open_database(db, False)
import idaapi, idautils, idc, ida_funcs, ida_hexrays, ida_name, ida_bytes, ida_xref, ida_segment, ida_nalt, ida_typeinf

_out = open(outp, "w") if outp else sys.stdout
def p(*a):
    _out.write(" ".join(str(x) for x in a) + "\n")

def fn(ea):
    f = ida_funcs.get_func(ea)
    if not f:
        return "nofunc"
    return "%s@%x" % (ida_name.get_name(f.start_ea), f.start_ea)

def ea_of(x):
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        try:
            return int(x, 16)
        except ValueError:
            e = idc.get_name_ea_simple(x)
            if e == idc.BADADDR:
                # try with leading underscore
                e = idc.get_name_ea_simple("_" + x)
            return e
    return int(x)

_hx = [False]
def dec(x, maxlines=100000):
    ea = ea_of(x)
    if not _hx[0]:
        ida_hexrays.init_hexrays_plugin()
        _hx[0] = True
    f = ida_funcs.get_func(ea)
    if not f:
        p("NO FUNC at", hex(ea) if ea != idc.BADADDR else x)
        return
    p("=" * 100)
    p("FUNC %s size %x" % (fn(f.start_ea), f.end_ea - f.start_ea))
    try:
        hf = ida_hexrays.hexrays_failure_t()
        c = ida_hexrays.decompile(f.start_ea, hf)
        if c is None:
            p("decompile failed:", hf.str, hex(hf.errea))
        else:
            lines = symbolize(str(c)).split("\n")
            for l in lines[:maxlines]:
                p(l)
    except Exception as e:
        p("decompile failed:", e)

def xr(x, maxn=200):
    ea = ea_of(x)
    p("XREFS to %x (%s)" % (ea, ida_name.get_name(ea)))
    n = 0
    for r in idautils.XrefsTo(ea):
        p("   from %x type %d in %s" % (r.frm, r.type, fn(r.frm)))
        n += 1
        if n >= maxn:
            break

def callers(x):
    ea = ea_of(x)
    fs = sorted(set(fn(r) for r in idautils.CodeRefsTo(ea, 0)))
    p("CALLERS of %x (%s): %s" % (ea, ida_name.get_name(ea), fs))
    return fs

def dis(x, n=40):
    ea = ea_of(x)
    p("DISASM %x" % ea)
    for i in range(n):
        p("  %x: %s" % (ea, idc.GetDisasm(ea)))
        ea = idc.next_head(ea, idc.BADADDR)

_strs = [None]
def strs(pats, regex=False, show_xrefs=True, maxhits=400):
    if _strs[0] is None:
        sc = idautils.Strings()
        sc.setup(strtypes=[0, 1], minlen=3)
        _strs[0] = [(s.ea, str(s)) for s in sc]
    n = 0
    for ea, t in _strs[0]:
        if not any((re.search(x, t) if regex else (x in t)) for x in pats):
            continue
        p("STR %x %r" % (ea, t[:300]))
        n += 1
        if show_xrefs:
            for r in idautils.XrefsTo(ea):
                p("   xref from %x in %s" % (r.frm, fn(r.frm)))
                if not ida_funcs.get_func(r.frm):
                    for y in idautils.XrefsTo(r.frm):
                        p("      via %x: xref from %x in %s" % (r.frm, y.frm, fn(y.frm)))
        if n >= maxhits:
            break

def names(pat, regex=True, maxn=300):
    n = 0
    for ea, nm in idautils.Names():
        if (re.search(pat, nm) if regex else pat in nm):
            p("NAME %x %s" % (ea, nm))
            n += 1
            if n >= maxn:
                break

def callees(x):
    ea = ea_of(x)
    f = ida_funcs.get_func(ea)
    res = []
    for h in idautils.FuncItems(f.start_ea):
        for r in idautils.CodeRefsFrom(h, 0):
            res.append((h, r, ida_name.get_name(r)))
    return res


_stubs = [None]
def stubs():
    if _stubs[0] is None:
        d = {}
        for line in open("/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/haptics/ios/dsc-stubs.txt"):
            if line.startswith("0x") and ": " in line:
                a, n = line.rstrip("\n").split(": ", 1)
                d.setdefault(int(a, 16), n)
        _stubs[0] = d
    return _stubs[0]
def sym(a):
    d = stubs()
    n = ida_name.get_name(a)
    if n and not n.startswith("sub_") and not n.startswith("loc_"):
        return n
    return d.get(a) or d.get(a + 0x10) or n or hex(a)
def symbolize(t):
    d = stubs()
    def rep_mem(m):
        a = int(m.group(1), 16)
        return d.get(a) or m.group(0)
    def rep_jump(m):
        a = int(m.group(1), 16)
        n = d.get(a)
        return ("JUMPOUT(%s)" % n) if n else m.group(0)
    t = re.sub(r"MEMORY\[0x([0-9A-Fa-f]+)(?:LL)?\]", rep_mem, t)
    t = re.sub(r"JUMPOUT\(0x([0-9A-Fa-f]+)(?:LL)?\)", rep_jump, t)
    return t
def calls_in(x, show=True):
    ea = ea_of(x)
    f = ida_funcs.get_func(ea)
    res = []
    if not f:
        return res
    for h in idautils.FuncItems(f.start_ea):
        m = idc.print_insn_mnem(h)
        if m in ("BL", "B") :
            t = idc.get_operand_value(h, 0)
            if m == "B" and ida_funcs.get_func(t) and ida_funcs.get_func(t).start_ea == f.start_ea:
                continue
            res.append((h, t, sym(t)))
    if show:
        p("CALLS in %s" % fn(ea))
        for h, t, n in res:
            p("  %x: -> %s" % (h, n))
    return res

exec(open(script).read())
if outp:
    _out.close()
idapro.close_database(False)
