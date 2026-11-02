# Deobfuscate MobileGestalt keys: obf = base64(md5("MGCopyAnswer" + name)) without padding.
# usage: python3 -I deobf.py OBFKEYS CANDIDATE_STRINGS... > out
import sys, hashlib, base64
obf = set(l.strip() for l in open(sys.argv[1]) if l.strip())
found = {}
seen = set()
for path in sys.argv[2:]:
    with open(path, 'rb') as f:
        for raw in f:
            s = raw.rstrip(b'\n')
            if not s or len(s) > 80 or s in seen:
                continue
            seen.add(s)
            # also try trimmed variants
            for cand in (s, s.strip()):
                h = base64.b64encode(hashlib.md5(b"MGCopyAnswer" + cand).digest()).decode().rstrip('=')
                if h in obf and h not in found:
                    found[h] = cand.decode('latin1')
for h, n in sorted(found.items(), key=lambda x: x[1]):
    print(f"{h}\t{n}")
print(f"# {len(found)} of {len(obf)} keys deobfuscated", file=sys.stderr)
