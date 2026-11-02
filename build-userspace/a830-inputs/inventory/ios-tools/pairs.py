# Find (HID usage page, usage) immediate pairs in a Mach-O's __text: a mov of 0xff00..0xff7f or of a standard page (1..0x20, 0x84,
# 0x85) and, within 4 instructions, a mov of a small usage; print each pair with the strings referenced within 40 instructions around it.
# usage: python3 -I pairs.py <binary> [--std]
import sys, struct, collections
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM

path = sys.argv[1]
std = '--std' in sys.argv
data = open(path, 'rb').read()
segs, sects = [], []
ncmds = struct.unpack_from('<I', data, 16)[0]
o = 32
for _ in range(ncmds):
    cmd, cs = struct.unpack_from('<II', data, o)
    if cmd == 0x19:
        vm, vs, fo, fs = struct.unpack_from('<QQQQ', data, o + 24)
        segs.append((vm, fs, fo))
        nsect = struct.unpack_from('<I', data, o + 64)[0]
        so = o + 72
        for j in range(nsect):
            sn = data[so:so + 16].rstrip(b'\0').decode()
            addr, size, off2 = struct.unpack_from('<QQI', data, so + 32)
            sects.append((sn, addr, size, off2))
            so += 80
    o += cs
def v2o(va):
    for vm, fs, fo in segs:
        if vm <= va < vm + fs:
            return fo + va - vm
def cstr(va):
    off = v2o(va)
    if off is None:
        return None
    e = data.find(b'\0', off, off + 200)
    s = data[off:e] if e > 0 else b''
    try:
        t = s.decode()
    except UnicodeDecodeError:
        return None
    return t if len(t) > 3 and all(31 < ord(c) < 127 for c in t) else None
def cfstr(va):
    off = v2o(va)
    if off is None:
        return None
    try:
        isa, fl, p, ln = struct.unpack_from('<QQQQ', data, off)
    except struct.error:
        return None
    p &= 0xfffffffff
    if 0 < ln < 200:
        s = cstr(p)
        if s and len(s) == ln:
            return s
text = [s for s in sects if s[0] == '__text'][0]
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True
code = data[text[3]:text[3] + text[2]]
insns = []
for ins in md.disasm(code, text[1]):
    ops = ins.operands
    imm = None
    if ins.mnemonic in ('mov', 'movz') and len(ops) == 2 and ops[1].type == ARM64_OP_IMM:
        imm = ops[1].imm
    s = None
    if ins.mnemonic == 'add' and len(ops) == 3 and ops[2].type == ARM64_OP_IMM:
        s = ('add', ins.reg_name(ops[1].reg), ops[2].imm)
    elif ins.mnemonic == 'adrp':
        s = ('adrp', ins.reg_name(ops[0].reg), ops[1].imm)
    insns.append((ins.address, ins.mnemonic, ins.reg_name(ops[0].reg) if ops and ops[0].type == 1 else None, imm, s))
pages = set(range(0xff00, 0xff80)) | ({1, 0xb, 0xc, 0xd, 0x20, 0x84, 0x85} if std else set())
found = collections.defaultdict(set)
for i, (a, mn, r, imm, s) in enumerate(insns):
    if imm in pages and imm >= 0xff00:
        for j in range(max(0, i - 4), min(len(insns), i + 5)):
            if j == i:
                continue
            b, mn2, r2, imm2, _ = insns[j]
            if imm2 is not None and 0 < imm2 < 0x100 and r2 != r:
                # strings around
                ctx = []
                adr = {}
                for k in range(max(0, i - 40), min(len(insns), i + 40)):
                    _, m3, r3, _, s3 = insns[k]
                    if s3 and s3[0] == 'adrp':
                        adr[s3[1]] = s3[2]
                    elif s3 and s3[0] == 'add' and s3[1] in adr:
                        t = cstr(adr[s3[1]] + s3[2]) or cfstr(adr[s3[1]] + s3[2])
                        if t and t not in ctx:
                            ctx.append(t)
                found[(imm, imm2)].add(' / '.join(c[:70] for c in ctx[:4]))
                break
for (p, u), ctxs in sorted(found.items()):
    print(f'page {p:#x} usage {u:#x} ({u})')
    for c in sorted(ctxs)[:6]:
        print('     ', c)
