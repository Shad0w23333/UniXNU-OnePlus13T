# List call sites (BL/B) in __text whose target is in TARGETS (comma-separated hex).
import os, idc, idautils, ida_funcs
out = open(os.environ["DECOMP_OUT"], "a")
targets = {int(x, 16) for x in os.environ["TARGETS"].split(",") if x}
for seg in idautils.Segments():
    if idc.get_segm_name(seg) != "__text":
        continue
    for f in idautils.Functions(idc.get_segm_start(seg), idc.get_segm_end(seg)):
        for h in idautils.FuncItems(f):
            m = idc.print_insn_mnem(h)
            if m in ("BL", "B"):
                t = idc.get_operand_value(h, 0)
                if t in targets:
                    out.write("0x%x %s -> 0x%x\n" % (h, ida_funcs.get_func_name(f), t))
out.close()
idc.qexit(0)
