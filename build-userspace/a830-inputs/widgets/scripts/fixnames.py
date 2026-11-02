#!/usr/bin/env python3
"""Replace MEMORY[0x...] calls in a Hex-Rays dump with the stub-island targets' names (ipsw dyld stubs output)."""
import sys, re
stubs = {}
for line in open('/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/widgets/re/stubs.txt', errors='replace'):
    m = re.match(r'0x([0-9a-f]+): (.*)', line.strip())
    if m: stubs[int(m.group(1), 16)] = m.group(2)
text = open(sys.argv[1]).read()
def sub(m):
    a = int(m.group(1), 16)
    return stubs.get(a, m.group(0))
text = re.sub(r'MEMORY\[0x([0-9A-Fa-f]+)\]', sub, text)
open(sys.argv[2] if len(sys.argv) > 2 else sys.argv[1], 'w').write(text)
