#!/usr/bin/env python3
"""Host test of the pack budget of make-shader-cache-a830.py (jitter F2): which of a device export's files join the pack, within what the
payload has room for. Synthetic exports in a temporary directory; no seed, no phone, no Turnip. The plugin's importer takes the result when the
host tool gen/pack_import is there.

  python3 -I build-userspace/shader-cache-tools/tests/test_merge_budget.py
"""
import hashlib
import importlib.util
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOOL = HERE.parents[1] / 'make-shader-cache-a830.py'
spec = importlib.util.spec_from_file_location('make_shader_cache', TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

BUILD = '9' * 64
IDENTITY = struct.pack('<III', 0x5143, 0x44050001, 7) + bytes(range(16))
failures = 0


def check(ok, what):
    global failures
    if not ok:
        print('FAIL: ' + what)
        failures += 1


def noise(rng, size):
    """Bytes that do not compress: a file's cost is its size."""
    return bytes(rng.getrandbits(8) for _ in range(size))


def record(rng, size, identity=IDENTITY):
    body = b'M2VPPLC\0' + identity + noise(rng, size)
    return body + hashlib.sha256(body).digest()


def put(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def name(prefix, index):
    return '%02x/%064x' % (prefix, index)


def main():
    rng = random.Random(1101)
    with tempfile.TemporaryDirectory(prefix='merge-budget-') as work:
        work = Path(work)
        first, second, cache = work / 'first', work / 'second', work / 'cache'
        # A first capture: three translations and a driver record of 4, 2, 1 and 8 KiB, and what must never join.
        put(first, 'v1/%s/%s' % (BUILD, name(1, 1)), noise(rng, 4096))
        put(first, 'v1/%s/%s' % (BUILD, name(1, 2)), noise(rng, 2048))
        put(first, 'v1/%s/%s' % (BUILD, name(1, 3)), noise(rng, 1024))
        put(first, 'v1/%s/%s' % (tool.CONTENT_DIR, name(2, 1)), record(rng, 8192))
        put(first, 'v1/%s/%s' % (tool.CONTENT_DIR, name(2, 2)), record(rng, 64, identity=bytes(len(IDENTITY))))   # another Turnip's
        broken = bytearray(record(rng, 64))
        broken[50] ^= 1   # past the identity
        put(first, 'v1/%s/%s' % (tool.CONTENT_DIR, name(2, 3)), bytes(broken))                                     # a bad checksum
        put(first, 'v1/%s/%s' % ('8' * 64, name(1, 4)), noise(rng, 64))                                            # another runtime's
        put(first, 'pipelines.log', b'R t=1 ok=1\n')
        put(first, 'zz-oslog/oslog.txt.gz', b'x')
        put(first, 'prime-done', b'a\t1\t2\tb\n')
        # A second capture: one more file, and one the first has.
        put(second, 'v1/%s/%s' % (BUILD, name(3, 1)), noise(rng, 512))
        put(second, 'v1/%s/%s' % (BUILD, name(1, 1)), (first / 'v1' / BUILD / name(1, 1)).read_bytes())

        # No budget: everything of this build and identity joins, as before there was a budget.
        cache.mkdir()
        all_in = tool.merge_export(cache, first, BUILD, IDENTITY)
        check(all_in['added'] == 4 + 1 and all_in['skipped'] == 5 and all_in['left_out'] == 0, 'no budget: every file of this identity joins')
        check((cache / 'v1' / BUILD / name(1, 3)).exists() and (cache / 'v1' / tool.CONTENT_DIR / name(2, 1)).exists(), 'no budget: they are there')
        check(not (cache / 'v1' / tool.CONTENT_DIR / name(2, 2)).exists(), 'another Turnip\'s record stays out')
        check(not (cache / 'v1' / tool.CONTENT_DIR / name(2, 3)).exists(), 'a record with a bad checksum stays out')
        check(not (cache / 'v1' / ('8' * 64)).exists() and not (cache / 'pipelines.log').exists(), 'other runtimes and journals stay out')
        check((cache / 'prime-done').read_text() == 'a\t1\t2\tb\n', 'prime-done lines join')
        again = tool.merge_export(cache, first, BUILD, IDENTITY)
        check(again['added'] == 0 and again['already'] == 4, 'a second merge adds nothing')

        # A budget: the largest first, what does not fit stays out, and smaller files still get the room left.
        tight = work / 'tight'
        tight.mkdir()
        room = [8192 + 64 + 3000]
        merged = tool.merge_export(tight, first, BUILD, IDENTITY, room)
        check((tight / 'v1' / tool.CONTENT_DIR / name(2, 1)).exists(), 'the largest file first')
        check(not (tight / 'v1' / BUILD / name(1, 1)).exists(), '4 KiB does not fit what is left')
        check((tight / 'v1' / BUILD / name(1, 2)).exists(), 'but 2 KiB does')
        check(not (tight / 'v1' / BUILD / name(1, 3)).exists(), 'and then 1 KiB does not')
        check(merged['left_out'] == 2 and merged['left_out_bytes'] == 4096 + 1024, 'what stayed out is reported')
        check(0 <= room[0] < 1024, 'the room went down by what was taken')
        check(merged['added_files'][0].endswith(name(2, 1)), 'the record of what was taken, largest first')

        # Exports in the order given: the first capture has the room, the file both carry counts once.
        order = work / 'order'
        order.mkdir()
        room = [4096 + 2048 + 300]
        one = tool.merge_export(order, first, BUILD, IDENTITY, room)
        two = tool.merge_export(order, second, BUILD, IDENTITY, room)
        check(two['added'] == 0 and two['left_out'] == 1 and two['already'] == 1, 'the later capture gets what is left, and what both carry counts once')
        check(one['left_out'] == 2 and (order / 'v1' / BUILD / name(1, 1)).exists() and (order / 'v1' / BUILD / name(1, 2)).exists(),
              'the earlier capture went first')

        # fit_pack: the pack itself, not the estimate, holds the budget. The baseline (what the production made) never leaves.
        pack_dir = work / 'fit'
        pack_dir.mkdir()
        put(pack_dir, 'v1/%s/%s' % (BUILD, name(9, 9)), noise(rng, 2048))
        pack = work / 'fit.m2vpack'
        baseline = tool.build_pack(pack_dir, pack)['pack_bytes']
        merged = [tool.merge_export(pack_dir, first, BUILD, IDENTITY, [10 ** 9])]
        full = tool.build_pack(pack_dir, pack)['pack_bytes']
        budget = full - 5000
        facts = tool.fit_pack(pack_dir, pack, budget, merged)
        check(facts['pack_bytes'] <= budget and pack.stat().st_size == facts['pack_bytes'], 'fit_pack: the pack is within the budget')
        check((pack_dir / 'v1' / BUILD / name(9, 9)).exists(), 'fit_pack: the baseline stays')
        check(merged[0]['left_out'] >= 1 and merged[0]['added_files'][0].endswith(name(2, 1)), 'fit_pack: the last taken leave first')
        same = tool.fit_pack(pack_dir, pack, None, merged)
        check(same['pack_bytes'] == facts['pack_bytes'], 'fit_pack: no budget, no change')
        try:
            tool.fit_pack(pack_dir, pack, baseline - 1, merged)
            check(False, 'fit_pack: a baseline over the budget must be refused')
        except SystemExit:
            pass

        # The plugin's own importer reads the trimmed pack.
        importer = tool.GEN / 'pack_import'
        if importer.is_file():
            out = work / 'imported'
            out.mkdir()
            tool.build_pack(pack_dir, pack)
            r = subprocess.run([str(importer), str(pack), str(out)], capture_output=True, text=True)
            check(r.returncode == 0 and 'IMPORTED' in r.stdout, 'the plugin\'s importer takes the pack: ' + r.stdout + r.stderr)
            check(tool.tree_bytes(out) == tool.tree_bytes(pack_dir), 'the imported tree is the pack\'s')
        else:
            print('note: no %s; the importer was not run' % importer)
    if failures:
        return 1
    print('PASS: pack budget: only this build and identity join, the largest first, what does not fit stays out while smaller files fill the room, '
          'exports in order, the pack itself within the budget, the baseline never leaves')
    return 0


if __name__ == '__main__':
    sys.exit(main())
