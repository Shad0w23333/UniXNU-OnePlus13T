def imm32_sites(val):
    lo, hi = val & 0xffff, (val >> 16) & 0xffff
    res = []
    for seg in idautils.Segments():
        if "text" not in (idc.get_segm_name(seg) or ""):
            continue
        for h in idautils.Heads(seg, idc.get_segm_end(seg)):
            m = idc.print_insn_mnem(h)
            if m == "MOVK" and idc.get_operand_value(h, 1) == hi and "LSL#16" in idc.GetDisasm(h).replace(" ", ""):
                reg = idc.print_operand(h, 0)
                e = h
                for i in range(10):
                    e = idc.prev_head(e, 0)
                    if idc.print_insn_mnem(e) == "MOV" and (idc.get_operand_value(e, 1) & 0xffff) == lo and idc.print_operand(e, 0) == reg:
                        res.append(h); break
            elif m == "MOV" and idc.get_operand_value(h, 1) == val:
                res.append(h)
    return res
def report_imm(val, label=None):
    s = imm32_sites(val)
    p("IMM %s (%x): %d sites" % (label or "", val, len(s)))
    seenf = {}
    for h in s:
        seenf.setdefault(fn(h), []).append(hex(h))
    for k, v in seenf.items():
        p("   %s %s" % (k, v[:8]))
