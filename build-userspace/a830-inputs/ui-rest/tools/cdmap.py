#!/usr/bin/env python3
"""cdmap.py ROOT... > tsv: 扫描 Mach-O，算 CodeDirectory 的 CDHash（sha256 取 20 字节 / sha1），输出 cdhash\tidentifier\tpath。"""
import hashlib, os, struct, sys
LC_CODE_SIGNATURE = 0x1d
def slices(d):
    m = struct.unpack_from('>I', d)[0]
    if m == 0xcafebabe and len(d) > 8:
        n = struct.unpack_from('>I', d, 4)[0]
        if n > 16:
            return
        for i in range(n):
            ct, st, off, size, al = struct.unpack_from('>5I', d, 8 + i * 20)
            yield d[off:off + size]
    else:
        yield d
def cd_of(s):
    if len(s) < 32 or struct.unpack_from('<I', s)[0] != 0xfeedfacf:
        return []
    ncmds = struct.unpack_from('<I', s, 16)[0]
    p = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', s, p)
        if cmd == LC_CODE_SIGNATURE:
            off, sz = struct.unpack_from('<II', s, p + 8)
            b = s[off:off + sz]
            magic, length, cnt = struct.unpack_from('>III', b)
            if magic != 0xfade0cc0:
                return []
            out = []
            for i in range(cnt):
                ty, o = struct.unpack_from('>II', b, 12 + i * 8)
                if ty == 0 or 0x1000 <= ty < 0x1005:
                    mg, l = struct.unpack_from('>II', b, o)
                    if mg != 0xfade0c02:
                        continue
                    cd = b[o:o + l]
                    ht = cd[37]
                    ver = struct.unpack_from('>I', cd, 8)[0]
                    ident_off = struct.unpack_from('>I', cd, 20)[0]
                    ident = cd[ident_off:cd.index(b'\0', ident_off)].decode('utf-8', 'replace')
                    h = hashlib.sha256(cd).digest()[:20] if ht == 2 else hashlib.sha1(cd).digest()
                    out.append((h.hex(), ident, ht))
            return out
        p += size
    return []
for root in sys.argv[1:]:
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                if os.path.islink(p) or not os.path.isfile(p) or os.path.getsize(p) < 4096:
                    continue
                with open(p, 'rb') as fh:
                    h = fh.read(4)
                    if h not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
                        continue
                    d = h + fh.read()
                for s in slices(d):
                    for c, i, ht in cd_of(s):
                        print(f'{c}\t{i}\t{p}')
            except Exception as e:
                print(f'ERR {p} {e}', file=sys.stderr)
