#!/usr/bin/env python3
# 列出 AudioReach ACDB 文件（acdb_cal.acdb）的顶层分块，并统计若干模块号/参数号/键值的出现次数。
# 只读；用法：python3 -I acdb_chunks.py <acdb_cal.acdb> [十六进制ID ...]
# 只做分块层面的粗看。真正取图（GET_GRAPH / GET_SUBGRAPH_DATA / 标定数据）要用开源 ACDB 库
# （AudioReach/audioreach-graphservices 的 acdb/，BSD-3-Clause）。
import struct
import sys

DEFAULT_IDS = {
    'MODULE_ID_WR_SHARED_MEM_EP': 0x07001000,
    'MODULE_ID_RD_SHARED_MEM_EP': 0x07001001,
    'MODULE_ID_PCM_CNV': 0x07001003,
    'MODULE_ID_I2S_SINK': 0x0700100A,
    'MODULE_ID_I2S_SOURCE': 0x0700100B,
    'MODULE_ID_CODEC_DMA_SINK': 0x07001023,
    'MODULE_ID_CODEC_DMA_SOURCE': 0x07001024,
    'PARAM_ID_I2S_INTF_CFG': 0x08001019,
    'PARAM_ID_CODEC_DMA_INTF_CFG': 0x08001063,
    'PARAM_ID_HW_EP_MF_CFG': 0x08001017,
    'KV_DEVICERX_SPEAKER': 0xA2000001,
    'KV_DEVICERX_SPEAKER_I2S': 0xA200000E,
    'KV_DEVICETX_HANDSETMIC': 0xA3000004,
    'KV_DEVICETX_SPEAKER_MIC': 0xA3000001,
}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__ or 'usage: acdb_chunks.py FILE [HEXID ...]')
        return 2
    data = open(sys.argv[1], 'rb').read()
    if data[:4] != b'ACDB':
        print('不是 ACDB 文件（缺少 "ACDB" 魔数）')
        return 1
    off, chunks = 12, []
    while off + 8 <= len(data):
        cid = data[off:off + 4]
        size = struct.unpack_from('<I', data, off + 4)[0]
        if not all(32 <= b < 127 for b in cid):
            print(f'在偏移 {off} 遇到非分块数据，停止')
            break
        chunks.append((cid.decode(), size, off))
        off += 8 + size
    print(f'{len(chunks)} 个分块，解析到 {off} / {len(data)} 字节')
    print(' '.join(f'{c}:{s}' for c, s, _ in chunks))
    ids = dict(DEFAULT_IDS)
    for extra in sys.argv[2:]:
        ids[extra] = int(extra, 16)
    for name, value in ids.items():
        print(f'{name:32s} 0x{value:08x} 出现 {data.count(struct.pack("<I", value))} 次')
    return 0


if __name__ == '__main__':
    sys.exit(main())
