#!/usr/bin/env python3
"""只读分析 f2fs 元数据区镜像（[0, main_blkaddr) 的转储）：选出有效检查点，读 SIT（含检查点里的 SIT 日志），
统计主区空闲段的连续段落，并模拟 pinned 文件按段顺序分配时会落成多少个物理区段。不写任何东西。

用法: f2fs_free_runs.py META.img [--gib 128 192 256] [--hint SEG]"""
import argparse, struct, sys, zlib, json
sys.path.insert(0, __import__('os').path.dirname(__file__))
from f2fs_sb import parse_sb

BLK = 4096
F2FS_SUPER_MAGIC = 0xF2F52010

def f2fs_crc32(data):
    # f2fs 的 crc32：以超级块 magic 为初值的 crc32（与 zlib 的初值/取反约定不同）
    crc = F2FS_SUPER_MAGIC
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1))
    return crc & 0xffffffff

def read_blk(f, blk, n=1):
    f.seek(blk * BLK)
    d = f.read(n * BLK)
    if len(d) != n * BLK:
        raise SystemExit(f'镜像太短：块 {blk}')
    return d

def load_cp(f, sb):
    best = None
    for pack in (0, 1):
        start = sb['cp_blkaddr'] + pack * (1 << sb['log_blocks_per_seg'])
        b = read_blk(f, start)
        ver, = struct.unpack_from('<Q', b, 0)
        cks_off, = struct.unpack_from('<I', b, 164)
        if cks_off < 4 or cks_off > 4092:
            continue
        crc_ok = f2fs_crc32(b[:cks_off]) == struct.unpack_from('<I', b, cks_off)[0]
        total, = struct.unpack_from('<I', b, 136)
        tail = read_blk(f, start + total - 1)
        tail_ver, = struct.unpack_from('<Q', tail, 0)
        ok = crc_ok and tail_ver == ver
        print(f'检查点包 {pack}: 版本 {ver:#x} crc={"对" if crc_ok else "错"} 尾块版本一致={tail_ver == ver}', file=sys.stderr)
        if ok and (best is None or ver > best[0]):
            best = (ver, start, b)
    if not best:
        raise SystemExit('没有有效检查点')
    return best

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--gib', type=int, nargs='*', default=[64, 128, 192, 256])
    ap.add_argument('--hint', type=int, default=None, help='pinned 分配起点段号（默认：从 0 开始；另给出若干起点的统计）')
    ap.add_argument('--export', metavar='FILE', help='把 --gib 第一个大小、--hint（缺省 0）的模拟结果写成 "分区内块号 段数" 文本，供主机测试用')
    a = ap.parse_args()
    f = open(a.image, 'rb')
    sb = parse_sb(read_blk(f, 0, 2))
    assert sb['magic'] == hex(F2FS_SUPER_MAGIC) and sb['segs_per_sec'] == 1 and sb['log_blocks_per_seg'] == 9
    ver, cp_start, cp = load_cp(f, sb)
    flags, = struct.unpack_from('<I', cp, 132)
    start_sum, = struct.unpack_from('<I', cp, 140)
    sit_bm_size, nat_bm_size = struct.unpack_from('<II', cp, 156)
    free_seg_cp, = struct.unpack_from('<I', cp, 32)
    cur_node = struct.unpack_from('<8I', cp, 36)[:3]
    cur_data = struct.unpack_from('<8I', cp, 84)[:3]
    if flags & 0x400 or sb['cp_payload']:
        raise SystemExit('本脚本未处理大 NAT 位图/cp_payload 布局')
    sit_bitmap = cp[192:192 + sit_bm_size]
    nseg = sb['segment_count_main']
    sit_blocks_per_copy = sb['segment_count_sit'] // 2 * 512
    vblocks = [0] * nseg
    for sblk in range((nseg + 54) // 55):
        use_second = sit_bitmap[sblk >> 3] & (1 << (7 - (sblk & 7)))
        addr = sb['sit_blkaddr'] + sblk + (sit_blocks_per_copy if use_second else 0)
        b = read_blk(f, addr)
        for i in range(55):
            seg = sblk * 55 + i
            if seg >= nseg:
                break
            v, = struct.unpack_from('<H', b, i * 74)
            vblocks[seg] = v & 0x3ff
    # 紧凑摘要：第一个块里依次是 NAT 日志（507 字节）和 SIT 日志（507 字节）
    journal_n = 0
    if flags & 0x4:
        sumb = read_blk(f, cp_start + start_sum)
        sitj = sumb[507:507 * 2]
        n_sits, = struct.unpack_from('<H', sitj, 0)
        for k in range(min(n_sits, 6)):
            segno, = struct.unpack_from('<I', sitj, 2 + k * 78)
            v, = struct.unpack_from('<H', sitj, 2 + k * 78 + 4)
            if segno < nseg:
                vblocks[segno] = v & 0x3ff
                journal_n += 1
    cur = set(x for x in cur_node + cur_data if x < nseg)
    free = [vblocks[s] == 0 and s not in cur for s in range(nseg)]
    nfree = sum(free)
    full = sum(1 for s in range(nseg) if vblocks[s] == 512)
    # 空闲段的连续段落
    runs = []
    s = 0
    while s < nseg:
        if free[s]:
            e = s
            while e < nseg and free[e]:
                e += 1
            runs.append((s, e - s))
            s = e
        else:
            s += 1
    runs_sorted = sorted(r[1] for r in runs)
    def simulate(need, hint):
        # 按 find_next_zero_bit(free_secmap, hint) 顺序（到尾回绕）逐段分配
        extents = []
        got = 0
        order = list(range(hint, nseg)) + list(range(0, hint))
        last = None
        for seg in order:
            if got >= need:
                break
            if not free[seg]:
                continue
            if last is not None and seg == last + 1:
                extents[-1][1] += 1
            else:
                extents.append([seg, 1])
            last = seg
            got += 1
        return got, extents
    out = dict(checkpoint_version=hex(ver), ckpt_flags=hex(flags), sit_journal_entries=journal_n,
               main_segments=nseg, free_segments=nfree, free_segments_in_cp=free_seg_cp,
               free_gib=round(nfree * 2 / 1024, 2), full_segments=full,
               current_segments=sorted(cur), free_runs=len(runs),
               largest_runs_segments=runs_sorted[-5:], runs_of_1=sum(1 for r in runs_sorted if r == 1),
               runs_le_8=sum(1 for r in runs_sorted if r <= 8))
    sims = {}
    hints = [a.hint] if a.hint is not None else [0, nseg // 4, nseg // 2, 3 * nseg // 4]
    for g in a.gib:
        need = g * 1024 // 2
        row = {}
        for h in hints:
            got, ext = simulate(need, h)
            row[f'hint{h}'] = dict(segments=got, extents=len(ext), enough=got >= need)
        sims[f'{g}GiB'] = row
    out['pinned_simulation'] = sims
    if a.export:
        got, ext = simulate(a.gib[0] * 1024 // 2, a.hint or 0)
        with open(a.export, 'w') as fo:
            fo.write(f'# fs_main={sb["main_blkaddr"]} fs_blocks={sb["block_count"]} segments={got}\n')
            for seg, cnt in ext:
                fo.write(f'{sb["main_blkaddr"] + seg * 512} {cnt}\n')
        out['exported'] = dict(file=a.export, extents=len(ext), segments=got)
    print(json.dumps(out, indent=1, ensure_ascii=False))

if __name__ == '__main__':
    main()
