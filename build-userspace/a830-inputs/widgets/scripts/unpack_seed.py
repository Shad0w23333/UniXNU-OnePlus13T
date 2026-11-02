#!/usr/bin/env python3
# Unpack a DBRDISK1 packed seed into a sparse raw image (read-only use on the host).
import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/xiaochoumao/unixnu-13t/UniXNU/drivers/DarwinBoot/Test')
import packed_ramdisk
src, dst = sys.argv[1], sys.argv[2]
blob = open(src, 'rb').read()
logical, extents = packed_ramdisk.regions(blob)
with open(dst, 'wb') as f:
    f.truncate(logical)
    for off, data in extents:
        f.seek(off)
        f.write(data)
print('logical', logical, 'extents', len(extents))
