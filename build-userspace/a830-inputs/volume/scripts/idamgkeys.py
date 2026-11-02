import json, os, re
import idaapi, idautils, idc, ida_funcs, ida_name, ida_pro, ida_auto, ida_bytes
ida_auto.auto_wait()
job = json.load(open(os.environ["IDAQ_JOB"]))
out = open(job["out"], "w")
# For each call to an MG function, find the most recent ADRP/ADD/ADRL loading X0 before the call and resolve the CFString
targets = {}
for name in ("_MGGetBoolAnswer", "_MGGetSInt32Answer", "_MGCopyAnswer", "_MGGetStringAnswer", "_MGCopyAnswerWithError"):
    ea = idc.get_name_ea_simple(name)
    if ea != idc.BADADDR:
        targets[ea] = name
def fname(ea):
    f = ida_funcs.get_func(ea)
    return "%s@%x" % (ida_name.get_name(f.start_ea), f.start_ea) if f else "nofunc"
def cfstr(ea):
    # CFString struct: isa, flags, ptr, len
    p = ida_bytes.get_qword(ea + 16)
    s = idc.get_strlit_contents(p, -1, 0)
    return s.decode(errors='replace') if s else None
res = {}
for tea, tname in targets.items():
    for x in idautils.CodeRefsTo(tea, 0):
        # walk back up to 12 instructions for X0 load
        ea = x
        key = None
        for i in range(16):
            ea = idc.prev_head(ea)
            if ea == idc.BADADDR: break
            mnem = idc.print_insn_mnem(ea)
            if mnem in ("ADRL", "ADR", "ADRP", "ADD", "LDR") and idc.print_operand(ea, 0) == "X0":
                v = idc.get_operand_value(ea, 1)
                if mnem == "ADD":
                    v = idc.get_operand_value(ea, 2)
                    # combine with previous adrp
                    pe = idc.prev_head(ea)
                    if idc.print_insn_mnem(pe) == "ADRP":
                        v = idc.get_operand_value(pe, 1) + v
                s = cfstr(v)
                if s:
                    key = s
                    break
        res.setdefault(key, set()).add(fname(x))
for k in sorted(res, key=lambda z: str(z)):
    out.write("%s: %s\n" % (k, ", ".join(sorted(res[k]))))
out.close()
ida_pro.qexit(0)
