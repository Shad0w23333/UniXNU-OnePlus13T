# For a Mach-O (thin or the first arm64 slice of a fat file): every call to the named imported functions, with the immediates and
# C strings loaded into x0..x3 in the instructions before it (same basic block, best effort).
# usage: python3 -I callargs.py <binary> <symbol>[,<symbol>...]   (symbols without the leading underscore)
import sys, struct, subprocess, re, collections
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG, ARM64_OP_MEM

path, syms = sys.argv[1], sys.argv[2].split(',')
raw = open(path, 'rb').read()
base = 0
if struct.unpack_from('>I', raw, 0)[0] in (0xcafebabe, 0xcafebabf):
    n = struct.unpack_from('>I', raw, 4)[0]
    for i in range(n):
        cpu, sub, off, size, align = struct.unpack_from('>iiIII', raw, 8 + 20 * i)
        if cpu == 0x0100000c:
            base = off
            break
data = raw[base:]
segs, sects = [], []
ncmds = struct.unpack_from('<I', data, 16)[0]
o = 32
for _ in range(ncmds):
    cmd, cs = struct.unpack_from('<II', data, o)
    if cmd == 0x19:
        name = data[o + 8:o + 24].rstrip(b'\0').decode()
        vm, vs, fo, fs = struct.unpack_from('<QQQQ', data, o + 24)
        nsect = struct.unpack_from('<I', data, o + 64)[0]
        segs.append((name, vm, vs, fo, fs))
        so = o + 72
        for j in range(nsect):
            sn = data[so:so + 16].rstrip(b'\0').decode()
            addr, size, off2 = struct.unpack_from('<QQI', data, so + 32)
            sects.append((name, sn, addr, size, off2))
            so += 80
    o += cs
def v2o(va):
    for name, vm, vs, fo, fs in segs:
        if vm <= va < vm + fs:
            return fo + va - vm
    return None
def cstr(va):
    off = v2o(va)
    if off is None:
        return None
    e = data.find(b'\0', off, off + 300)
    s = data[off:e] if e > 0 else b''
    try:
        t = s.decode()
    except UnicodeDecodeError:
        return None
    return t if t and all(31 < ord(c) < 127 for c in t) else None
def cfstr(va):
    off = v2o(va)
    if off is None:
        return None
    try:
        isa, fl, p, ln = struct.unpack_from('<QQQQ', data, off)
    except struct.error:
        return None
    p &= 0xffffffffff
    if 0 < ln < 300:
        s = cstr(p)
        if s and len(s) == ln:
            return s
    return None
# stub addresses from otool (first slice listed first)
out = subprocess.run(['otool', '-arch', 'arm64', '-Iv', path], capture_output=True, text=True).stdout
stubs = {}
for l in out.splitlines():
    m = re.match(r'\s*(0x[0-9a-f]+)\s+\d+\s+_(\S+)', l)
    if m and m.group(2) in syms:
        stubs[int(m.group(1), 16)] = m.group(2)
text = [s for s in sects if s[1] == '__text'][0]
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True
code = data[text[4]:text[4] + text[3]]
window = collections.deque(maxlen=24)
res = collections.Counter()
for ins in md.disasm(code, text[2]):
    if ins.mnemonic == 'bl' and ins.operands[0].imm in stubs:
        regs, adr = {}, {}
        for w in window:
            ops = w.operands
            if w.mnemonic in ('mov', 'movz', 'orr') and len(ops) >= 2 and ops[-1].type == ARM64_OP_IMM:
                regs[w.reg_name(ops[0].reg)] = ops[-1].imm
            elif w.mnemonic == 'movk' and len(ops) == 2:
                rn = w.reg_name(ops[0].reg)
                if rn in regs:
                    regs[rn] |= ops[1].imm << (ops[1].shift.value if ops[1].shift.type else 0)
            elif w.mnemonic == 'adrp':
                adr[w.reg_name(ops[0].reg)] = ops[1].imm
            elif w.mnemonic == 'adr':
                a = ops[1].imm
                s = cstr(a) or cfstr(a)
                regs[w.reg_name(ops[0].reg)] = repr(s) if s else hex(a)
            elif w.mnemonic == 'add' and len(ops) == 3 and ops[2].type == ARM64_OP_IMM and w.reg_name(ops[1].reg) in adr:
                a = adr[w.reg_name(ops[1].reg)] + ops[2].imm
                s = cstr(a) or cfstr(a)
                regs[w.reg_name(ops[0].reg)] = repr(s) if s else hex(a)
        args = []
        for r in ('w1', 'w2', 'x0', 'x1', 'x2'):
            if r in regs:
                v = regs[r]
                args.append(f'{r}={v:#x}' if isinstance(v, int) else f'{r}={v}')
        res[(stubs[ins.operands[0].imm], ' '.join(args))] += 1
    window.append(ins)
for (s, a), c in sorted(res.items()):
    print(f'{c:4d} {s} {a}')
