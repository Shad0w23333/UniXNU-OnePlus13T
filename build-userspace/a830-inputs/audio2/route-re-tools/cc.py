# reconstruct MOV/MOVK constants in a function, report printable four-char codes (selector, scope) in order
import re, struct
_pr = re.compile(rb"^[ -~]{4}$")
def is4cc(v):
    b = struct.pack(">I", v & 0xffffffff)
    return bool(_pr.match(b)) and any(c.isalpha() for c in b.decode('latin1'))
def fourcc(v):
    return struct.pack(">I", v & 0xffffffff).decode('latin1')
def regkey(r):
    r = r.upper()
    if r.startswith("W") or r.startswith("X"):
        return r[1:]
    return r
def consts_in(x):
    ea = ea_of(x)
    f = ida_funcs.get_func(ea)
    res = []
    if not f:
        return res
    regs = {}
    for h in idautils.FuncItems(f.start_ea):
        m = idc.print_insn_mnem(h)
        if m in ("MOV", "MOVK", "MOVZ", "MOVN"):
            if idc.get_operand_type(h, 1) != idc.o_imm:
                regs.pop(regkey(idc.print_operand(h, 0)), None)
                continue
            r = regkey(idc.print_operand(h, 0))
            imm = idc.get_operand_value(h, 1) & 0xffffffffffffffff
            dis = idc.GetDisasm(h).replace(" ", "")
            if m == "MOVK":
                sh = 0
                mm = re.search(r"LSL#(\d+)", dis)
                if mm:
                    sh = int(mm.group(1))
                v = regs.get(r, 0)
                v = (v & ~(0xffff << sh)) | ((imm & 0xffff) << sh)
                regs[r] = v
                if sh == 16 and is4cc(v):
                    res.append((h, fourcc(v)))
                if sh == 48 and is4cc(v >> 32):
                    res.append((h, fourcc(v & 0xffffffff) + "/" + fourcc(v >> 32)))
            else:
                regs[r] = imm
                if is4cc(imm) and imm > 0xffff:
                    res.append((h, fourcc(imm)))
        elif m == "ADRP":
            regs["P" + regkey(idc.print_operand(h, 0))] = idc.get_operand_value(h, 1)
        elif m == "ADD" and idc.get_operand_type(h, 2) == idc.o_imm and ("P" + regkey(idc.print_operand(h, 1))) in regs:
            regs["P" + regkey(idc.print_operand(h, 0))] = regs["P" + regkey(idc.print_operand(h, 1))] + idc.get_operand_value(h, 2)
        elif m in ("LDR", "LDP", "LDUR") and idc.get_operand_type(h, 1) in (idc.o_mem, idc.o_displ):
            a = None
            if idc.get_operand_type(h, 1) == idc.o_mem:
                a = idc.get_operand_value(h, 1)
            else:
                dis = idc.print_operand(h, 1)
                mm = re.match(r"\[(X\d+|SP),#(-?0x[0-9A-Fa-f]+|-?\d+)\]", dis.replace(" ", ""))
                mm2 = re.match(r"\[(X\d+)\]", dis.replace(" ", ""))
                base = None; off = 0
                mm3 = re.match(r"\[(X\d+|SP),", dis.replace(" ", ""))
                if mm:
                    base = mm.group(1); off = int(mm.group(2), 0)
                elif mm2:
                    base = mm2.group(1)
                elif mm3:
                    base = mm3.group(1); off = idc.get_operand_value(h, 1)
                    if base != "SP" and ("P" + regkey(base)) in regs and off >= 0x1000:
                        off = off & 0xfff
                if base and ("P" + regkey(base)) in regs:
                    a = regs["P" + regkey(base)] + off
                if not a:
                    # displacement may be a symbol offset
                    v1 = idc.get_operand_value(h, 1)
                    if v1 and idc.get_segm_name(v1) and ("const" in idc.get_segm_name(v1) or "data" in idc.get_segm_name(v1)):
                        a = v1
            if a:
                for k in range(0, 16, 4):
                    v = ida_bytes.get_dword(a + k)
                    if v and is4cc(v) and v > 0xffff:
                        res.append((h, fourcc(v) + "@lit+%d" % k))
    return res
