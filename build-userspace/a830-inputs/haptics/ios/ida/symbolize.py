# Replace MEMORY[0x..], JUMPOUT(0x..), sub_XXXX and bare 0x1XXXXXXXX call targets in IDA output with dyld cache stub names.
# usage: python3 -I symbolize.py STUBS_TXT < in > out
import sys, re
names = {}
for line in open(sys.argv[1]):
    if line.startswith("0x") and ": " in line:
        a, n = line.rstrip("\n").split(": ", 1)
        names.setdefault(int(a, 16), n)
def rep_mem(m):
    a = int(m.group(1), 16)
    n = names.get(a)
    return f"{n}" if n else m.group(0)
def rep_jump(m):
    a = int(m.group(1), 16)
    n = names.get(a)
    return f"JUMPOUT({n})" if n else m.group(0)
def rep_sub(m):
    a = int(m.group(1), 16)
    n = names.get(a)
    return n if n else m.group(0)
t = sys.stdin.read()
t = re.sub(r"MEMORY\[0x([0-9A-Fa-f]+)(?:LL)?\]", rep_mem, t)
t = re.sub(r"JUMPOUT\(0x([0-9A-Fa-f]+)(?:LL)?\)", rep_jump, t)
t = re.sub(r"\bsub_([0-9A-F]{9,})\b", rep_sub, t)
sys.stdout.write(t)
