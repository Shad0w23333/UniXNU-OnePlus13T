#!/usr/bin/env python3
"""Minimal dyld shared cache reader (21H461): VM address -> bytes across the main cache and its subcaches."""
import struct, os, glob, mmap

class Cache:
    def __init__(self, path):
        self.maps = []   # (vm, size, fileoff, mm)
        self.files = []
        base = path
        paths = [base] + sorted(p for p in glob.glob(base + '.*') if p[len(base)+1:].split('.')[0].isdigit())
        for p in paths:
            f = open(p, 'rb')
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            self.files.append((f, mm))
            magic = mm[:16]
            if not magic.startswith(b'dyld_v1'):
                continue
            mappingOffset, mappingCount = struct.unpack_from('<II', mm, 0x10)
            for i in range(mappingCount):
                addr, size, fileoff, maxp, initp = struct.unpack_from('<QQQII', mm, mappingOffset + i * 32)
                self.maps.append((addr, size, fileoff, mm, p))
        self.maps.sort(key=lambda m: m[0])

    def read(self, vm, n):
        for addr, size, off, mm, p in self.maps:
            if addr <= vm < addr + size:
                o = off + (vm - addr)
                return mm[o:o + n]
        return None

    def cstr(self, vm, maxlen=512):
        b = self.read(vm, maxlen)
        if b is None:
            return None
        i = b.find(b'\0')
        return b[:i if i >= 0 else len(b)].decode('utf-8', 'replace')

    def where(self, vm):
        for addr, size, off, mm, p in self.maps:
            if addr <= vm < addr + size:
                return p, off + (vm - addr)
        return None
