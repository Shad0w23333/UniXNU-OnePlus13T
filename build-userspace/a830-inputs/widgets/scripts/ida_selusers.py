# IDAPython (headless): decompile every function that uses one of the given selectors (through its selref or its objc_msgSend$ stub).
# idc.ARGV: selrefs_file out_file selector [selector ...]
import idc, idaapi, idautils, ida_name, ida_funcs, ida_hexrays, ida_auto, re
ida_auto.auto_wait()
args = idc.ARGV[1:]
selfile, outfile, sels = args[0], args[1], args[2:]
selea = {}
for line in open(selfile):
    parts = line.split()
    if len(parts) != 2: continue
    ea = int(parts[0], 16)
    selea.setdefault(parts[1], []).append(ea)
    ida_name.set_name(ea, 'selRef_' + re.sub(r'[^A-Za-z0-9_]', '_', parts[1]), ida_name.SN_NOCHECK | ida_name.SN_FORCE | ida_name.SN_NOWARN)
ida_hexrays.init_hexrays_plugin()
out = open(outfile, 'w')
done = set()
for sel in sels:
    users = set()
    for ea in selea.get(sel, []):
        for x in idautils.XrefsTo(ea):
            f = ida_funcs.get_func(x.frm)
            if f: users.add(f.start_ea)
    stub = idc.get_name_ea_simple('_objc_msgSend$' + sel)
    if stub != idc.BADADDR:
        for x in idautils.XrefsTo(stub):
            f = ida_funcs.get_func(x.frm)
            if f and f.start_ea != stub: users.add(f.start_ea)
    out.write('\n// ######## selector %s: %d users\n' % (sel, len(users)))
    for fea in sorted(users):
        nm = ida_funcs.get_func_name(fea)
        dn = idc.demangle_name(nm, idc.get_inf_attr(idc.INF_SHORT_DN)) or nm
        out.write('// user: %s @ %x\n' % (dn, fea))
    for fea in sorted(users):
        if fea in done: continue
        done.add(fea)
        nm = ida_funcs.get_func_name(fea)
        dn = idc.demangle_name(nm, idc.get_inf_attr(idc.INF_SHORT_DN)) or nm
        out.write('\n// ==== %s @ %x\n' % (dn, fea))
        try:
            out.write(str(ida_hexrays.decompile(fea)) + '\n')
        except Exception as e:
            out.write('// decompile failed: %s\n' % e)
out.close()
idc.qexit(0)
