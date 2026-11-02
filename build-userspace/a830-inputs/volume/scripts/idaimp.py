import json, os
import idaapi, idautils, idc, ida_funcs, ida_name, ida_pro, ida_auto, ida_nalt
ida_auto.auto_wait()
job = json.load(open(os.environ["IDAQ_JOB"]))
out = open(job["out"], "w")
want = set(job["imports"])
def fname(ea):
    f = ida_funcs.get_func(ea)
    return "%s@%x" % (ida_name.get_name(f.start_ea), f.start_ea) if f else "nofunc@%x" % ea
res = {}
def cb(ea, name, ordinal):
    if name and (name in want or name.lstrip('_') in want):
        res[name] = ea
    return True
for i in range(ida_nalt.get_import_module_qty()):
    ida_nalt.enum_import_names(i, cb)
for name, ea in sorted(res.items()):
    out.write("IMPORT %s at %x\n" % (name, ea))
    callers = set()
    for x in idautils.XrefsTo(ea):
        f = ida_funcs.get_func(x.frm)
        if f:
            callers.add(fname(x.frm))
        else:
            # stub: look at xrefs to the stub
            for y in idautils.XrefsTo(x.frm):
                callers.add(fname(y.frm))
            fs = ida_funcs.get_func(x.frm)
    # also find functions named like the import (stubs)
    stub = idc.get_name_ea_simple("_" + name.lstrip('_'))
    for nm in (name, "_" + name.lstrip("_"), name.lstrip("_")):
        sea = idc.get_name_ea_simple(nm)
        if sea != idc.BADADDR and sea != ea:
            for y in idautils.CodeRefsTo(sea, 0):
                callers.add(fname(y))
    for c in sorted(callers):
        out.write("   caller %s\n" % c)
out.close()
ida_pro.qexit(0)
