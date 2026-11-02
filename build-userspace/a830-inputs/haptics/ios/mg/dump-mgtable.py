# Dump libMobileGestalt's key table: (C string pointer to an obfuscated key, handler pointer) pairs in __DATA_CONST.
import struct, re, sys
path = sys.argv[1]
data = open(path, 'rb').read()
TEXT_VM, TEXT_OFF, TEXT_SZ = 0x1a0206000, 0x0, 0x80000
DC_VM, DC_OFF, DC_SZ = 0x1e7c3b070, 0x80000, 0x70000
def cstr(va):
    if not (TEXT_VM <= va < TEXT_VM + TEXT_SZ): return None
    o = va - TEXT_VM + TEXT_OFF
    e = data.find(b'\0', o, o + 64)
    if e == -1: return None
    s = data[o:e]
    return s.decode('latin1') if re.fullmatch(rb'[A-Za-z0-9+/]{22}', s) else None
names = {}
try:
    for l in open(sys.argv[2]):
        if '\t' in l:
            h, n = l.rstrip('\n').split('\t')[:2]; names[h] = n
except Exception: pass
rows = []
for o in range(DC_OFF, DC_OFF + DC_SZ - 16, 8):
    a, b = struct.unpack_from('<QQ', data, o)
    k = cstr(a)
    if k and TEXT_VM <= b < TEXT_VM + TEXT_SZ:
        rows.append((DC_VM + o - DC_OFF, k, b))
for va, k, b in rows:
    print(f"{va:#x}\t{k}\t{b:#x}\t{names.get(k, '')}")
print(f"# {len(rows)} entries", file=sys.stderr)
