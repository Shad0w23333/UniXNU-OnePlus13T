# Static summary of libMobileGestalt key handlers (21H461 cache image, extracted with plain pointers).
# usage: python3 -I mga.py <libMobileGestalt.dylib> <mgtable.tsv> <mgkeys-deobf.tsv> <names.txt> [name-regex]
# For each handler: C strings and CFStrings it references (obfuscated 22-char keys resolved against known MobileGestalt key names
# and device-tree names via base64(md5("MGCopyAnswer"+name))), small immediates (first-char hints) and the calls it makes.
import sys, struct, re, hashlib, base64
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG

lib, table, keys, namesf = sys.argv[1:5]
want = re.compile(sys.argv[5]) if len(sys.argv) > 5 else None
data = open(lib, 'rb').read()

# segments from the Mach-O header
segs = []
ncmds = struct.unpack_from('<I', data, 16)[0]
off = 32
for _ in range(ncmds):
    cmd, cs = struct.unpack_from('<II', data, off)
    if cmd == 0x19:
        name = data[off + 8:off + 24].rstrip(b'\0').decode()
        vm, vs, fo, fs = struct.unpack_from('<QQQQ', data, off + 24)
        segs.append((name, vm, vs, fo, fs))
    off += cs
def v2o(va):
    for name, vm, vs, fo, fs in segs:
        if vm <= va < vm + min(vs, fs):
            return fo + va - vm, name
    return None, None
def u64(va):
    o, _ = v2o(va)
    return struct.unpack_from('<Q', data, o)[0] if o is not None else None
def cstr(va, maxlen=200):
    o, seg = v2o(va)
    if o is None or seg != '__TEXT':
        return None
    e = data.find(b'\0', o, o + maxlen)
    if e < 0:
        return None
    s = data[o:e]
    try:
        t = s.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return t if t and all(32 <= ord(c) < 127 for c in t) else None
def cfstring(va):
    o, seg = v2o(va)
    if o is None or seg == '__TEXT':
        return None
    try:
        isa, flags, p, ln = struct.unpack_from('<QQQQ', data, o)
    except struct.error:
        return None
    if not (0 < ln < 300) or (flags & 0xffffff00) != 0 and flags > 0xffff:
        return None
    s = cstr(p, ln + 1)
    return s if s is not None and len(s) == ln else None

def h(k):
    return base64.b64encode(hashlib.md5(b'MGCopyAnswer' + k.encode()).digest()).decode().rstrip('=')
known = {}
for l in open(keys):
    p = l.rstrip('\n').split('\t')
    if len(p) >= 2 and p[1]:
        known[p[0]] = p[1]
dt = {}
for l in open(namesf):
    n = l.strip()
    if n:
        dt.setdefault(h(n), n)
        dt.setdefault(h(n.lower()), n.lower())
def resolve(s):
    if s and re.fullmatch(r'[A-Za-z0-9+/]{22}', s):
        if s in known:
            return f'{s}=MG:{known[s]}'
        if s in dt:
            return f'{s}=name:{dt[s]}'
        return f'{s}=?'
    return s

handlers = []
for l in open(table):
    p = l.rstrip('\n').split('\t')
    if len(p) >= 4 and p[2].startswith('0x'):
        handlers.append((p[3] or known.get(p[1], ''), p[1], int(p[2], 16)))
addr2name = {va: (n or k) for n, k, va in handlers}

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True
def summarize(va, depth=0, seen=None):
    seen = seen if seen is not None else set()
    if va in seen or depth > 1:
        return []
    seen.add(va)
    o, seg = v2o(va)
    if o is None:
        return ['(outside image)']
    regs = {}
    out = []
    code = data[o:o + 4 * 160]
    n = 0
    for ins in md.disasm(code, va):
        n += 1
        mn = ins.mnemonic
        ops = ins.operands
        if mn == 'adrp':
            regs[ops[0].reg] = ops[1].imm
        elif mn == 'add' and len(ops) == 3 and ops[2].type == ARM64_OP_IMM and ops[1].reg in regs:
            a = regs[ops[1].reg] + ops[2].imm
            regs[ops[0].reg] = a
            c = cstr(a)
            if c is not None:
                out.append(f'"{c}"')
            else:
                cf = cfstring(a)
                if cf is not None:
                    out.append(f'@"{resolve(cf)}"')
        elif mn == 'ldr' and len(ops) == 2 and ops[1].type == 3 and ops[1].mem.base in regs:   # ldr xN, [xM, #imm]
            a = regs[ops[1].mem.base] + ops[1].mem.disp
            v = u64(a)
            if v is not None:
                cf = cfstring(v)
                if cf is not None:
                    out.append(f'@"{resolve(cf)}"')
        elif mn in ('mov', 'movz') and len(ops) == 2 and ops[1].type == ARM64_OP_IMM:
            imm = ops[1].imm
            rn = ins.reg_name(ops[0].reg)
            if rn in ('w1', 'w2', 'w3', 'x2', 'x3') and 0 <= imm < 0x10000:
                out.append(f'{rn}={imm:#x}' + (f"('{chr(imm)}')" if 0x20 < imm < 0x7f else ''))
        elif mn in ('bl', 'b') and ops and ops[0].type == ARM64_OP_IMM:
            t = ops[0].imm
            if mn == 'bl' or not (va <= t < va + 4 * 160):
                lab = addr2name.get(t, f'{t:#x}')
                out.append(f'{mn} {lab}')
                if mn == 'b' and depth == 0 and t not in addr2name:
                    pass
        if mn == 'ret' or (mn == 'b' and ops and ops[0].type == ARM64_OP_IMM and not (va <= ops[0].imm < va + 4 * 160)):
            break
        if n > 150:
            break
    return out

for name, k, va in sorted(handlers, key=lambda x: x[0]):
    if want and not want.search(name or ''):
        continue
    items = summarize(va)
    print(f'{name or "?"}\t{k}\t{va:#x}\t' + ' | '.join(items))
