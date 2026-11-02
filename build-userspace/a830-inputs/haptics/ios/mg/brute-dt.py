# Brute-force hashed device-tree names used by libMobileGestalt: hash = base64(md5("MGCopyAnswer"+name)).
import sys, hashlib, base64, itertools
targets = dict(t.split(":") for t in sys.argv[1].split(","))   # hash:firstchar
words = sys.argv[2].split(",")
seps = ["-", "_", ""]
def h(k): return base64.b64encode(hashlib.md5(b"MGCopyAnswer" + k.encode()).digest()).decode().rstrip("=")
found = {}
for n in range(1, 5):
    for combo in itertools.product(words, repeat=n):
        for sep in (seps if n > 1 else [""]):
            name = sep.join(combo)
            for v in {name, name.lower()}:
                hv = h(v)
                if hv in targets and v[:1] == targets[hv]:
                    found.setdefault(hv, set()).add(v)
for k, v in found.items():
    print(k, sorted(v))
print("searched", file=sys.stderr)
