#!/usr/bin/env python3
"""只读解析 f2fs 超级块（镜像文件或其前 4 KiB），打印几何参数。"""
import struct, sys, json

def parse_sb(buf):
    s = buf[1024:1024 + 3072]
    magic, major, minor, log_ss, log_spb, log_bs, log_bps, segs_per_sec, secs_per_zone, cksum_off = struct.unpack_from('<IHHIIIIIII', s, 0)
    block_count, = struct.unpack_from('<Q', s, 36)
    (section_count, segment_count, seg_ckpt, seg_sit, seg_nat, seg_ssa, seg_main,
     segment0_blkaddr, cp_blkaddr, sit_blkaddr, nat_blkaddr, ssa_blkaddr, main_blkaddr,
     root_ino, node_ino, meta_ino) = struct.unpack_from('<16I', s, 44)
    uuid = s[108:124].hex()
    # volume_name 512 * u16 at 124 → 1148; extension_count u32 1148; extension_list 64*8 → 1152..1664
    cp_payload, = struct.unpack_from('<I', s, 1664)
    version = s[1668:1668 + 256].split(b'\0')[0].decode(errors='replace')
    init_version = s[1924:1924 + 256].split(b'\0')[0].decode(errors='replace')
    feature, = struct.unpack_from('<I', s, 2180)
    return dict(magic=hex(magic), major=major, minor=minor, log_blocksize=log_bs, log_blocks_per_seg=log_bps,
                segs_per_sec=segs_per_sec, secs_per_zone=secs_per_zone, block_count=block_count,
                section_count=section_count, segment_count=segment_count, segment_count_ckpt=seg_ckpt,
                segment_count_sit=seg_sit, segment_count_nat=seg_nat, segment_count_ssa=seg_ssa,
                segment_count_main=seg_main, segment0_blkaddr=segment0_blkaddr, cp_blkaddr=cp_blkaddr,
                sit_blkaddr=sit_blkaddr, nat_blkaddr=nat_blkaddr, ssa_blkaddr=ssa_blkaddr,
                main_blkaddr=main_blkaddr, root_ino=root_ino, uuid=uuid, cp_payload=cp_payload,
                version=version, init_version=init_version, feature=hex(feature))

if __name__ == '__main__':
    with open(sys.argv[1], 'rb') as f:
        buf = f.read(8192)
    print(json.dumps(parse_sb(buf), indent=1))
