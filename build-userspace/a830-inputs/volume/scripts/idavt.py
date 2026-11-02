import json, os
import idaapi, idautils, idc, ida_funcs, ida_name, ida_pro, ida_auto, ida_bytes
ida_auto.auto_wait()
job = json.load(open(os.environ["IDAQ_JOB"]))
out = open(job["out"], "w")
for vt, n in job["vtables"]:
    ea = int(vt, 16)
    out.write("VTABLE %x\n" % ea)
    for i in range(int(n)):
        v = ida_bytes.get_qword(ea + 8 * i)
        f = ida_funcs.get_func(v)
        nm = ida_name.get_name(v) or ""
        out.write("  +%3d (0x%x): %x %s\n" % (8 * i, 8 * i, v, nm))
out.close()
ida_pro.qexit(0)
