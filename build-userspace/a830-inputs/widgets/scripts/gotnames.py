#!/usr/bin/env python3
"""Name the GOT slots (and other pointer sections) of an extracted, slid dylib by the shared-cache symbol each points to (ipsw A2S cache)."""
import sys, struct, mmap
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/widgets/scripts')
from selrefs_lib import sections

class A2S:
    def __init__(self, path):
        self.f = open(path, 'rb'); self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        self.count, self.sb = struct.unpack_from('<II', self.m, 4); self.strings = 12 + 12 * self.count
    def entry(self, i): return struct.unpack_from('<QI', self.m, 12 + 12 * i)
    def name(self, off):
        s = self.strings + off; e = self.m.find(b'\0', s, s + 4096); return self.m[s:e].decode('utf-8', 'replace')
    def lookup(self, a):
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            if self.entry(mid)[0] <= a: lo = mid + 1
            else: hi = mid
        if lo == 0: return None
        st, off = self.entry(lo - 1)
        return st, self.name(off)

def main():
    dylib, a2s = sys.argv[1], A2S(sys.argv[2])
    data = open(dylib, 'rb').read()
    secs = sections(data)
    for (seg, sect), (addr, size, offset) in secs.items():
        if sect not in ('__got', '__auth_got', '__auth_ptr'): continue
        for i in range(0, size, 8):
            p, = struct.unpack_from('<Q', data, offset + i)
            p &= 0x0000000fffffffff
            r = a2s.lookup(p)
            if r and r[0] == p:
                print('%x %s' % (addr + i, r[1]))
main()
