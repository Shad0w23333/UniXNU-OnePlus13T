# Decode Apple "pbze" (pbzx-style chunked LZFSE) into raw bytes. usage: pbze.py IN OUT
import sys, struct, ctypes
lib = ctypes.CDLL('/usr/lib/libcompression.dylib')
lib.compression_decode_buffer.restype = ctypes.c_size_t
COMPRESSION_LZFSE = 0x801
d = open(sys.argv[1], 'rb').read()
assert d[:4] in (b'pbze', b'pbzx'), d[:4]
chunk = struct.unpack('>Q', d[4:12])[0]
off = 12
out = bytearray()
while off < len(d):
    usz, csz = struct.unpack('>QQ', d[off:off+16]); off += 16
    blob = d[off:off+csz]; off += csz
    if usz == csz:
        out += blob; continue
    dst = ctypes.create_string_buffer(usz + 64)
    n = lib.compression_decode_buffer(dst, usz + 64, blob, csz, None, COMPRESSION_LZFSE)
    if n == 0:
        raise SystemExit('decode failed at %d' % off)
    out += dst.raw[:n]
open(sys.argv[2], 'wb').write(out)
print(len(out))
