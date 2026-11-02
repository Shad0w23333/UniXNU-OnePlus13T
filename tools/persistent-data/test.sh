#!/bin/bash
# 主机侧检查（不碰手机）：
#   1. 驱动侧全部主机测试（drivers/PaganiPersistentData/Test/run.sh）；
#   2. 安卓端工具交叉编译（build-android.sh，-Werror）；
#   3. Python 解码器与 C 实现交叉核对：槽解码、两个金丝雀块与一个写自检块的 SHA-256；
#   4. 有原厂备份里的 f2fs 元数据区转储时，只读跑超级块解析和空闲段/锁定文件分配模拟。
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PDATA=$(cd "$HERE/../../UniXNU-Pagani-Drivers/drivers/PaganiPersistentData" && pwd)
RAMBLOCK=$(cd "$PDATA/../NativeRamBlock" && pwd)
OUT=$(mktemp -d "${TMPDIR:-/tmp}/pdata-tool.XXXXXX")
trap 'rm -rf "$OUT"' EXIT

echo '== 1. 驱动侧主机测试'
"$PDATA/Test/run.sh" | tail -3

echo '== 2. 安卓端工具交叉编译'
"$HERE/build-android.sh" "$OUT/unixnu-datafile"

echo '== 3. Python 解码器 vs C'
c++ -std=c++17 -O1 -I"$PDATA" -I"$RAMBLOCK" "$PDATA/Test/test_datafile.cpp" -o "$OUT/test_datafile"
"$OUT/test_datafile" --emit "$OUT/slot.bin" > "$OUT/vectors.txt"
python3 -I "$HERE/df_decode.py" "$OUT/slot.bin" > "$OUT/slot.json"
python3 -I - "$OUT/slot.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
assert d['header'] == 'ok' and d['table'] == 'ok', d
assert d['generation'] == 7 and d['segment_count'] == 7 and d['extent_count'] == 2 and d['data_blocks'] == 7 * 511, d
assert d['extents_first'][0] == [297984 + 321 * 512, 5], d
print('slot decode ok:', d['state_name'], d['extent_count'], 'extents,', d['segment_count'], 'segments')
EOF
while read -r kind a b sha; do
    py=$(python3 -I "$HERE/df_decode.py" "--$kind" "$OUT/slot.bin" "$a" "$b")
    [ "$py" = "$sha" ] || { echo "$kind $a: C $sha Python $py"; exit 1; }
    echo "$kind $a ok"
done < "$OUT/vectors.txt"

META="$HERE/../../一加13T原厂备份/fsck修复前/userdata-f2fs-元数据区-1164MiB.img"
if [ -f "$META" ]; then
    echo '== 4. 10-29 f2fs 元数据转储（只读）：几何与 128/192/240 GiB 锁定文件分配模拟'
    python3 -I -B "$HERE/f2fs_sb.py" "$META" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print({k: d[k] for k in ("block_count","main_blkaddr","segment_count_main","segs_per_sec","log_blocks_per_seg")})'
    python3 -I -B "$HERE/f2fs_free_runs.py" "$META" --gib 128 192 240 2>/dev/null | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
print("free segments", d["free_segments"], "(%s GiB), free runs %d, single-segment runs %d" % (d["free_gib"], d["free_runs"], d["runs_of_1"]))
for size, row in d["pinned_simulation"].items():
    print(size, " ".join("%s: %d extents%s" % (k, v["extents"], "" if v["enough"] else " NOT ENOUGH") for k, v in row.items()))'
fi
echo 'ALL PASS'
