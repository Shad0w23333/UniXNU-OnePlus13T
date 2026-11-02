# Find code that builds a 32-bit immediate (MOV/MOVK pairs or a single MOV) equal to any of IMMS (comma-separated, decimal or 0x).
import os, idc, idautils, ida_funcs, ida_ua, idaapi
out = open(os.environ["DECOMP_OUT"], "a")
imms = [int(x, 0) for x in os.environ["IMMS"].split(",")]
want = {v & 0xffffffff for v in imms}
lo_parts = {v & 0xffff: v for v in want}
for seg in idautils.Segments():
    if idc.get_segm_name(seg) not in ("__text",):
        continue
    ea = idc.get_segm_start(seg); end = idc.get_segm_end(seg)
    regs = {}
    while ea < end and ea != idaapi.BADADDR:
        m = idc.print_insn_mnem(ea)
        if m in ("MOV", "MOVZ", "MOVK", "ORR"):
            op0 = idc.print_operand(ea, 0)
            if m in ("MOV", "MOVZ") and idc.get_operand_type(ea, 1) == idc.o_imm:
                regs[op0[-2:]] = idc.get_operand_value(ea, 1) & 0xffffffff
                v = regs[op0[-2:]]
                if v in want:
                    f = ida_funcs.get_func(ea); out.write("0x%x %s %s\n" % (ea, ida_funcs.get_func_name(f.start_ea) if f else "?", hex(v)))
            elif m == "MOVK" and idc.get_operand_type(ea, 1) == idc.o_imm:
                r = op0[-2:]
                dis = idc.GetDisasm(ea)
                sh = 16 if "LSL#16" in dis.replace(" ", "") else 0
                if r in regs:
                    v = (regs[r] & ~(0xffff << sh)) | ((idc.get_operand_value(ea, 1) & 0xffff) << sh)
                    regs[r] = v & 0xffffffff
                    if regs[r] in want:
                        f = ida_funcs.get_func(ea); out.write("0x%x %s %s\n" % (ea, ida_funcs.get_func_name(f.start_ea) if f else "?", hex(regs[r])))
        ea = idc.next_head(ea, end)
out.close()
idc.qexit(0)
