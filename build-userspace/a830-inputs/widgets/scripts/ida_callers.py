# IDAPython: decompile the callers of the functions at the given addresses (hex) or named functions.
# idc.ARGV: selrefs_file out_file target [target ...]
import idc, idautils, ida_funcs, ida_hexrays, ida_auto, ida_name, re
ida_auto.auto_wait()
args = idc.ARGV[1:]
selfile, outfile, targets = args[0], args[1], args[2:]
for line in open(selfile):
    p = line.split()
    if len(p) == 2:
        ida_name.set_name(int(p[0], 16), 'selRef_' + re.sub(r'[^A-Za-z0-9_]', '_', p[1]), ida_name.SN_NOCHECK | ida_name.SN_FORCE | ida_name.SN_NOWARN)
ida_hexrays.init_hexrays_plugin()
out = open(outfile, 'w')
done = set()
for t in targets:
    ea = int(t, 16) if re.match(r'^[0-9a-fA-F]+$', t) else idc.get_name_ea_simple(t)
    out.write('\n// ######## callers of %s (%x)\n' % (t, ea))
    callers = set()
    for x in idautils.XrefsTo(ea):
        f = ida_funcs.get_func(x.frm)
        if f: callers.add(f.start_ea)
    for fea in sorted(callers):
        nm = ida_funcs.get_func_name(fea)
        out.write('// caller: %s @ %x\n' % (idc.demangle_name(nm, idc.get_inf_attr(idc.INF_SHORT_DN)) or nm, fea))
    for fea in sorted(callers):
        if fea in done: continue
        done.add(fea)
        nm = ida_funcs.get_func_name(fea)
        out.write('\n// ==== %s @ %x\n' % (idc.demangle_name(nm, idc.get_inf_attr(idc.INF_SHORT_DN)) or nm, fea))
        try: out.write(str(ida_hexrays.decompile(fea)) + '\n')
        except Exception as e: out.write('// fail %s\n' % e)
out.close()
idc.qexit(0)
