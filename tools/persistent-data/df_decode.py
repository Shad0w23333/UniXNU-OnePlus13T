#!/usr/bin/env python3
"""只读解码 iOS Data 文件描述符（DataFileFormat.h 的独立 Python 实现，用于交叉核对和排查）。

  df_decode.py SLOT.bin                    解码一个槽（120 块：头 + 区段表），校验头哈希、表哈希、几何与重叠
  df_decode.py --unixnu-dump DUMP.bin      DUMP 是 unixnu 分区开头至少 256 块（dd if=/dev/block/by-name/unixnu bs=4096 count=256），解码 A/B 两槽并给出生效的一个
  df_decode.py --canary SLOT.bin INDEX REL 打印该段金丝雀块的 SHA-256（与 C 实现比对）
  df_decode.py --selftest SLOT.bin W REL   打印 XNU 写自检块（文件数据块 W，分区内块号 REL）的 SHA-256（与 C 实现比对）
不写任何东西。"""
import hashlib
import json
import struct
import sys

BLOCK, SEG, SEG_DATA, HASHED = 4096, 512, 511, 4064
SLOT_A, SLOT_B, SLOT_BLOCKS = 16, 136, 120
MAX_EXTENTS = (SLOT_BLOCKS - 1) * BLOCK // 16
STATES = {1: 'ACTIVE', 2: 'READONLY', 3: 'REVOKED'}


def decode_header(b):
    if b[:8] != b'UNXDFIL1':
        return None, 'magic'
    if struct.unpack_from('<I', b, 8)[0] != 1:
        return None, 'version'
    if hashlib.sha256(b[:HASHED]).digest() != b[HASHED:HASHED + 32]:
        return None, 'hash'
    if any(b[200:HASHED]) or struct.unpack_from('<I', b, 92)[0]:
        return None, 'padding'
    h = dict(zip(('state', 'generation', 'flags', 'extent_count'), struct.unpack_from('<IQII', b, 12)))
    h['file_id'] = b[32:48].hex()
    h['nonce'] = b[48:80].hex()
    h.update(zip(('lun', 'block_bytes', 'seg_blocks'), struct.unpack_from('<III', b, 80)))
    h.update(zip(('part_first', 'part_last', 'fs_main', 'fs_blocks', 'segment_count', 'data_blocks', 'file_bytes', 'file_ino', 'created'),
                 struct.unpack_from('<9Q', b, 96)))
    h['table_sha'] = b[168:200].hex()
    if h['state'] not in STATES:
        return h, 'state'
    if h['state'] == 3:
        return h, 'ok'
    if h['flags'] != 1 or h['lun'] != 0 or h['block_bytes'] != BLOCK or h['seg_blocks'] != SEG:
        return h, 'geometry'
    if (h['part_last'] < h['part_first'] or h['part_last'] - h['part_first'] + 1 < h['fs_blocks'] or not h['fs_main']
            or h['fs_main'] >= h['fs_blocks'] or h['fs_blocks'] - h['fs_main'] < SEG):
        return h, 'geometry'
    main_segs = (h['fs_blocks'] - h['fs_main']) // SEG
    if (not 1 <= h['extent_count'] <= MAX_EXTENTS or not 1 <= h['segment_count'] <= main_segs
            or h['data_blocks'] != h['segment_count'] * SEG_DATA or h['file_bytes'] != h['segment_count'] * SEG * BLOCK):
        return h, 'counts'
    return h, 'ok'


def decode_table(h, t):
    n = h['extent_count']
    raw = t[:n * 16]
    if hashlib.sha256(raw).hexdigest() != h['table_sha']:
        return None, 'table-hash'
    main_end = h['fs_main'] + (h['fs_blocks'] - h['fs_main']) // SEG * SEG
    ext, total = [], 0
    for i in range(n):
        first, segs, res = struct.unpack_from('<QII', raw, 16 * i)
        if res:
            return None, 'padding'
        if not segs or first < h['fs_main'] or first >= main_end or segs > (main_end - first) // SEG:
            return None, 'extent-range'
        if (first - h['fs_main']) % SEG:
            return None, 'extent-align'
        ext.append((first, segs))
        total += segs
    if total != h['segment_count']:
        return None, 'extent-sum'
    srt = sorted(ext)
    for a, b in zip(srt, srt[1:]):
        if a[0] + a[1] * SEG > b[0]:
            return None, 'overlap'
    return ext, 'ok'


def fill(h, tag, index, rel):
    out = bytearray(BLOCK)
    out[:8] = tag
    struct.pack_into('<I', out, 8, 1)
    out[16:32] = bytes.fromhex(h['file_id'])
    struct.pack_into('<QQ', out, 32, index, rel)
    mac = hashlib.sha256(bytes.fromhex(h['nonce']) + bytes(out[:48])).digest()
    out[48:80] = mac
    for i in range(80, BLOCK):
        out[i] = out[48 + (i - 80) % 32] ^ (((i - 80) // 32) & 0xff)
    return bytes(out)


def canary(h, index, rel):
    return fill(h, b'UNXCANRY', index, rel)


def selftest(h, w, rel):
    return fill(h, b'UNXSELFT', w, rel)


def summary(slot):
    h, why = decode_header(slot[:BLOCK])
    res = dict(header=why)
    if h:
        res.update(h, state_name=STATES.get(h['state'], '?'))
        if why == 'ok' and h['state'] != 3:
            ext, w2 = decode_table(h, slot[BLOCK:])
            res['table'] = w2
            if ext:
                res['extents_first'] = ext[:4]
                res['data_gib'] = round(h['data_blocks'] * BLOCK / 2**30, 3)
    return res


def main():
    a = sys.argv[1:]
    if a and a[0] == '--canary':
        slot = open(a[1], 'rb').read()
        h, why = decode_header(slot[:BLOCK])
        print(hashlib.sha256(canary(h, int(a[2]), int(a[3]))).hexdigest())
        return
    if a and a[0] == '--selftest':
        slot = open(a[1], 'rb').read()
        h, why = decode_header(slot[:BLOCK])
        print(hashlib.sha256(selftest(h, int(a[2]), int(a[3]))).hexdigest())
        return
    if a and a[0] == '--unixnu-dump':
        d = open(a[1], 'rb').read()
        slots = {name: d[off * BLOCK:(off + SLOT_BLOCKS) * BLOCK] for name, off in (('A', SLOT_A), ('B', SLOT_B))}
        out = {name: summary(s) for name, s in slots.items()}
        valid = [(v.get('generation', -1), k) for k, v in out.items() if v['header'] == 'ok']
        out['in_force'] = None
        if valid:
            gen, name = max(valid)
            if len(valid) == 2 and valid[0][0] == valid[1][0] and slots['A'][:BLOCK] != slots['B'][:BLOCK]:
                out['in_force'] = 'refused (ambiguous)'
            else:
                out['in_force'] = 'refused (revoked)' if out[name]['state'] == 3 else name
        print(json.dumps(out, indent=1, default=str))
        return
    print(json.dumps(summary(open(a[0], 'rb').read()), indent=1, default=str))


if __name__ == '__main__':
    main()
