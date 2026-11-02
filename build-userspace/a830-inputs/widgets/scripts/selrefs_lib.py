import struct
def sections(data):
    magic, cputype, cpusub, filetype, ncmds, sizeofcmds, flags, res = struct.unpack_from('<IiiIIIII', data, 0)
    off = 32
    out = {}
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == 0x19:
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
