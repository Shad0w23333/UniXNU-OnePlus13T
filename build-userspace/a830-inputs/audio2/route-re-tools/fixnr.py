# clear wrongly-set noreturn flags on callees of the given functions (call sites followed by more code)
import ida_funcs, ida_auto, ida_typeinf, ida_nalt
def clear_noret(ea):
    f = ida_funcs.get_func(ea)
    if f and (f.flags & ida_funcs.FUNC_NORET):
        f.flags &= ~ida_funcs.FUNC_NORET
        ida_funcs.update_func(f)
        tif = ida_typeinf.tinfo_t()
        if ida_nalt.get_tinfo(tif, f.start_ea):
            fi = ida_typeinf.func_type_data_t()
            if tif.get_func_details(fi):
                fi.flags &= ~ida_typeinf.FTI_NORET
                nt = ida_typeinf.tinfo_t()
                nt.create_func(fi)
                ida_typeinf.apply_tinfo(f.start_ea, nt, ida_typeinf.TINFO_DEFINITE)
        return True
    return False
def fix_callers_of_noret(x):
    ea = ea_of(x)
    f = ida_funcs.get_func(ea)
    if not f:
        return
    fixed = []
    for h in idautils.FuncItems(f.start_ea):
        if idc.print_insn_mnem(h) == "BL":
            t = idc.get_operand_value(h, 0)
            g = ida_funcs.get_func(t)
            nxt = idc.next_head(h, idc.BADADDR)
            if g and (g.flags & ida_funcs.FUNC_NORET) and ida_funcs.get_func(nxt) and ida_funcs.get_func(nxt).start_ea == f.start_ea:
                if clear_noret(t):
                    fixed.append(ida_name.get_name(t))
    if fixed:
        ida_funcs.reanalyze_function(f)
        ida_auto.auto_wait()
    return fixed

import ida_auto, ida_hexrays
def fix_func(x, depth=1):
    """clear noreturn on x itself and on callees followed by code; replan; mark dirty"""
    ea = ea_of(x)
    f = ida_funcs.get_func(ea)
    if not f:
        return []
    fixed = []
    if clear_noret(f.start_ea):
        fixed.append(ida_name.get_name(f.start_ea))
    for h in list(idautils.FuncItems(f.start_ea)):
        if idc.print_insn_mnem(h) in ("BL",):
            t = idc.get_operand_value(h, 0)
            g = ida_funcs.get_func(t)
            if g and (g.flags & ida_funcs.FUNC_NORET):
                if clear_noret(t):
                    fixed.append(ida_name.get_name(t))
    ida_auto.plan_range(f.start_ea, f.end_ea)
    ida_auto.auto_wait()
    ida_funcs.reanalyze_function(f)
    ida_auto.auto_wait()
    try:
        ida_hexrays.mark_cfunc_dirty(f.start_ea)
    except Exception:
        pass
    return fixed
