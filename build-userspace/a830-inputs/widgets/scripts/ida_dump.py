# IDAPython (headless): name the selrefs from a mapping file, then decompile the functions whose names match the given patterns.
# Arguments (idc.ARGV): selrefs_file out_file pattern [pattern ...]
import idc, idaapi, idautils, ida_name, ida_funcs, ida_hexrays, ida_auto, re
ida_auto.auto_wait()
args = idc.ARGV[1:]
selfile, outfile, pats = args[0], args[1], args[2:]
count = 0
for line in open(selfile):
    parts = line.split()
    if len(parts) != 2: continue
    ea = int(parts[0], 16)
    name = 'selRef_' + re.sub(r'[^A-Za-z0-9_]', '_', parts[1])
    ida_name.set_name(ea, name, ida_name.SN_NOCHECK | ida_name.SN_FORCE | ida_name.SN_NOWARN)
    count += 1
out = open(outfile, 'w')
out.write('// selrefs named: %d\n' % count)
ok = ida_hexrays.init_hexrays_plugin()
out.write('// hexrays: %s\n' % ok)
regs = [re.compile(p) for p in pats]
for fea in idautils.Functions():
    nm = ida_funcs.get_func_name(fea) or ''
    dn = idc.demangle_name(nm, idc.get_inf_attr(idc.INF_SHORT_DN)) or nm
    if not any(r.search(nm) or r.search(dn) for r in regs): continue
    out.write('\n// ==== %s (%s) @ %x\n' % (dn, nm, fea))
    try:
        c = ida_hexrays.decompile(fea)
        out.write(str(c) + '\n')
    except Exception as e:
        out.write('// decompile failed: %s\n' % e)
out.close()
idc.qexit(0)
