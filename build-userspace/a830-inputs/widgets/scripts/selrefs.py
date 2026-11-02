#!/usr/bin/env python3
"""List the selector names an extracted (slid) dylib references through __objc_selrefs, resolved against the cache."""
import sys, struct
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/widgets/scripts')
from dsc import Cache

def sections(data):
    magic, cputype, cpusub, filetype, ncmds, sizeofcmds, flags, res = struct.unpack_from('<IiiIIIII', data, 0)
    off = 32
    out = {}
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == 0x19:  # LC_SEGMENT_64
            segname = data[off+8:off+24].rstrip(b'\0').decode()
            vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, sflags = struct.unpack_from('<QQQQiiII', data, off+24)
            so = off + 72
            for i in range(nsects):
                sectname = data[so:so+16].rstrip(b'\0').decode()
                seg = data[so+16:so+32].rstrip(b'\0').decode()
                addr, size2, offset = struct.unpack_from('<QQI', data, so+32)
                out[(seg, sectname)] = (addr, size2, offset)
                so += 80
        off += size
    return out

def main():
    dylib, cachepath = sys.argv[1], sys.argv[2]
    data = open(dylib, 'rb').read()
    secs = sections(data)
    cache = Cache(cachepath)
    key = [k for k in secs if k[1] == '__objc_selrefs']
    if not key:
        print('no selrefs'); return
    addr, size, offset = secs[key[0]]
    names = []
    for i in range(0, size, 8):
        ptr, = struct.unpack_from('<Q', data, offset + i)
        ptr &= 0x0000000fffffffff
        s = cache.cstr(ptr)
        names.append((addr + i, s))
    for a, s in names:
        print('%x %s' % (a, s))

main()
