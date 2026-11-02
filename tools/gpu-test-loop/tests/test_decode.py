#!/usr/bin/env python3
"""Decoder tests on real writer output: BootProgress/Test/ramoops.c (the kernel's RamoopsLog.c, layout 3: 4 + 32 copies of
0xfffc) dumps both zone images of a run; Linux's pstore view is applied (including what a decayed zone header does to it),
DRAM damage is injected independently into every copy, and the decoded log must come back with no wrong record among the
checked ones. Images of layout 2 (the first real runs: 1 + 8 copies of 0x3fff4) and of other zone sizes are rebuilt from
the same records with the reference encoder, so old dumps stay readable.

The damage model follows the measurement on the 13T (scratchpad fastloop/evidence-2026-10-29-after-F1.bin and the first
loop run: about 8% of the 1 bits lost, nearly always 1 -> 0); 0.5% of the 0 bits gained here.

    python3 tests/test_decode.py [--writer-test PATH/TO/ramoops.c] [--keep DIR]
"""
import argparse
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import ramoops_format as rf  # noqa: E402
import ramoops_device as rd  # noqa: E402

DEFAULT_WRITER = HERE.parents[2] / 'UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ramoops.c'
P1, P0 = 0.08, 0.005                        # the measured loss of 1 bits, and a pessimistic gain of 0 bits


def writer_run(source: Path, work: Path):
    """Compile and run the writer test with --dump; returns (console zone, pmsg zone, expected records by seq)."""
    binary = work / 'ramoops-test'
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
    out = work / 'dump'
    out.mkdir()
    subprocess.run([str(binary), '--dump', str(out)], check=True, stdout=subprocess.DEVNULL)
    expected = {}
    for line in (out / 'expected.tsv').read_text().splitlines():
        seq, ms, kind, text = line.split('\t', 3)
        expected[int(seq)] = (int(seq), int(ms), kind, text)
    return (out / 'console.zone').read_bytes(), (out / 'pmsg.zone').read_bytes(), expected


def decay(data: bytes, p1: float, p0: float, rng: random.Random) -> bytes:
    """Lose each 1 bit with p1, gain each 0 bit of a written (nonzero) byte with p0."""
    out = bytearray(data)
    for i, value in enumerate(out):
        if not value:
            continue
        for bit in range(8):
            if (value >> bit) & 1:
                if rng.random() < p1:
                    out[i] &= ~(1 << bit) & 0xff
            elif rng.random() < p0:
                out[i] |= 1 << bit
    return bytes(out)


def zone_header(zone: bytes, start: int = None, size: int = None) -> bytes:
    """The zone with its Linux header's start and/or size replaced (what decay of those fields does)."""
    out = bytearray(zone)
    if start is not None:
        struct.pack_into('<I', out, 4, start)
    if size is not None:
        struct.pack_into('<I', out, 8, size)
    return bytes(out)


def area_of(kind: str, text: str) -> str:
    """RamoopsLog.c area_of: the log header to the boot area, console lines and per-process stages to the console ring."""
    if kind in 'BA':
        return 'boot'
    if kind == 'C' or (kind == 'S' and (text[5:] if text.startswith('exit ') else text).startswith('pid=')):
        return 'console'
    return 'events'


def rebuild(expected, layout: rf.Layout, run_id: int = 0x5eed1234):
    """Zone images of `layout` holding the expected records the way its writer lays them out, each area keeping the
    newest records that fit it (as a ring does; the rebuild does not wrap); returns the images and the records kept."""
    by_area = {name: [] for name, _, _, _ in layout.areas}
    area = 'boot'
    for seq in sorted(expected):
        _, ms, kind, text = expected[seq]
        area = area if kind == '+' else area_of(kind, text)
        by_area[area].append(rf.encode(seq, ms, kind, text))
    copy = bytearray(layout.copy_bytes)
    kept = {}
    for name, offset, size, _ in layout.areas:
        records = by_area[name]
        while sum(map(len, records)) > size:
            records.pop(0)
        for record in records:
            copy[offset:offset + len(record)] = record
            offset += len(record)
            seq = int(record[1:9], 16)
            kept[seq] = expected[seq]
    zones = []
    for zone, size in enumerate(layout.zones):
        image = bytearray(size)
        struct.pack_into('<III', image, 0, rf.SIG, 0, size - rf.ZONE_HEADER)
        for k in range(layout.copies[zone]):
            at = rf.ZONE_HEADER + k * layout.copy_bytes
            image[at:at + layout.copy_bytes] = layout.header(layout.number(zone, k), run_id) + copy[rf.COPY_HEADER:]
        zones.append(bytes(image))
    return zones, kept


def check(decoded, expected, label, complete=True, least=None):
    """No wrong record among the checked ones; every record checked (complete) or at least `least` of them."""
    got = {r.seq: r for r in decoded.records}
    wrong = [s for s, r in got.items() if r.verified and (s not in expected or (r.seq, r.ms, r.kind, r.text) != expected[s])]
    assert not wrong, (label, 'wrong checked records', wrong[:5])
    verified = [s for s in expected if s in got and got[s].verified]
    if complete:
        missing = [s for s in expected if s not in verified]
        assert not missing, (label, 'not recovered', missing[:10], len(missing))
    if least is not None:
        assert len(verified) >= least, (label, len(verified), 'of', len(expected), decoded.notes)
    return len(verified)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--writer-test', type=Path, default=DEFAULT_WRITER)
    parser.add_argument('--keep', type=Path, help='keep the zone images here')
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='ramoops-decode-'))
    try:
        console_zone, pmsg_zone, expected = writer_run(args.writer_test, work)
        if args.keep:
            args.keep.mkdir(parents=True, exist_ok=True)
            (args.keep / 'console.zone').write_bytes(console_zone)
            (args.keep / 'pmsg.zone').write_bytes(pmsg_zone)
        run(console_zone, pmsg_zone, expected)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(console_zone, pmsg_zone, expected):
    passed = []
    rng = random.Random(2026)
    n = len(expected)
    console = rf.pstore_view(console_zone)
    pmsg = rf.pstore_view(pmsg_zone)
    assert console and pmsg and len(console) == 0x40000 - 12 and len(pmsg) == 0x200000 - 12, 'start 0, whole zone'

    # 1. Linux's checks on zone headers (persistent_ram_post_init / save_old), and what a decayed header shows.
    bad = bytearray(console_zone); bad[0] ^= 1
    assert rf.pstore_view(bytes(bad)) is None                         # sig: zapped
    assert rf.pstore_view(zone_header(console_zone, size=len(console_zone) - 11)) is None    # size beyond the zone
    assert rf.pstore_view(zone_header(pmsg_zone, start=1, size=0)) is None                   # start beyond size
    assert rf.pstore_view(struct.pack('<III', rf.SIG, 0, 0) + bytes(100)) == b''
    assert rf.pstore_view(zone_header(console_zone, size=0x3ff94)) == console[:0x3ff94]        # size lost bits: shorter
    assert rf.pstore_view(zone_header(pmsg_zone, start=0x800)) == pmsg[0x800:] + pmsg[:0x800]  # start gained a bit
    d = rf.decode({'console': None, 'pmsg': None})                    # Linux dropped both zones
    assert not d.records and d.layout is None and any('neither file' in note for note in d.notes)
    assert 'layout' not in rf.summary(d).splitlines()[1]
    passed.append('Linux zone checks (dropped, shortened, rotated; both dropped: no layout claimed)')

    # 1b. Raw zone images (read from the reserved memory) decode whatever their Linux header says: here both headers are
    #     ones Linux drops (pmsg's size as on the 13T, the console sig damaged), and every record comes back.
    with tempfile.TemporaryDirectory(prefix='ramoops-zones-') as name:
        bad_console = bytearray(console_zone); bad_console[1] ^= 0x40
        Path(name, 'console.zone').write_bytes(bytes(bad_console))
        Path(name, 'pmsg.zone').write_bytes(zone_header(pmsg_zone, size=18283476))
        assert rf.pstore_view(bytes(bad_console)) is None and rf.pstore_view(Path(name, 'pmsg.zone').read_bytes()) is None
        d = rf.decode(rd.load_saved(Path(name)))
        check(d, expected, 'raw zones')
        assert d.vote['copies'] == 36
    passed.append('raw zone images whose Linux headers are both invalid: decoded from their data, all recovered')

    # 2. Clean copies: every record, layout 3 from the copy headers; the Python layout rules match the writer's.
    d = rf.decode({'console': console, 'pmsg': pmsg})
    check(d, expected, 'clean')
    layout = d.layout
    assert layout.from_header and layout.version == 3 and layout.copies == (4, 32) and layout.copy_bytes == 0xfffc
    assert layout.events == (0x1000, 0x8000) and layout.console == (0x9000, 0x6ffc) and layout.pa == rf.DEFAULT_PA
    assert len(d.records) == n and not d.unverified and d.vote['copies'] == 36 and not d.notes
    assert rf.default_layout().geometry() == layout.geometry()
    assert rf.default_layout((0x20000, 0x200000)).copy_bytes == 0xfff8 and rf.default_layout((0x20000, 0x200000)).copies == (2, 32)
    small = rf.default_layout((0x8000, 0x200000))
    assert small.copy_bytes == 0x7ff4 and small.copies == (1, 63) and small.events == (0x1000, 0x37f8)
    assert rf.default_layout((0x400000, 0x400000)).copies == (32, 32) and rf.default_layout((0x2000, 0x2000)) is None
    old = rf.default_layout(version=2)
    assert old.copy_bytes == 0x3fff4 and old.copies == (1, 8) and old.events == (0x1000, 0x10000)
    passed.append('clean: %d records (seq %d..%d), 36 copies (4 + 32 of 0xfffc), layout 3 from the copy headers' %
                  (n, min(expected), max(expected)))

    # 3a. Both zones, the measured damage and worse in every copy: everything recovered, nothing wrong.
    for p1, p0 in ((P1, P0), (0.15, 0.006)):
        d = rf.decode({'console': decay(console, p1, p0, rng), 'pmsg': decay(pmsg, p1, p0, rng)})
        check(d, expected, 'both p1 %.2f' % p1)
        assert abs(d.vote['p1'] - p1) < 0.02 and d.vote['copies'] == 36, d.vote
        passed.append('(a) both zones, 1->0 %.0f%% / 0->1 %.1f%% in every copy: all %d recovered, 36 copies, vote '
                      'threshold %s' % (p1 * 100, p0 * 100, n, d.vote['thresholds']))

    # 3b. Linux dropped the pmsg zone (as in the first loop run): the console zone's 4 copies recover nearly everything.
    worst = n
    for seed in range(3):
        d = rf.decode({'console': decay(console, P1, P0, random.Random(100 + seed)), 'pmsg': None})
        worst = min(worst, check(d, expected, 'console only %d' % seed, complete=False, least=n * 98 // 100))
        assert d.vote['copies'] == 4 and d.vote['thresholds'] == {'4': 2} and d.layout.version == 3
        assert any('no pmsg-ramoops-0' in note for note in d.notes)
    passed.append('(b) console zone only (pmsg dropped), 4 copies at 8%%/0.5%%: at least %d of %d checked in 3 runs, none '
                  'wrong' % (worst, n))

    # 3c. Linux dropped the console zone: the 32 pmsg copies recover everything.
    d = rf.decode({'console': None, 'pmsg': decay(pmsg, P1, P0, rng)})
    check(d, expected, 'pmsg only')
    assert d.vote['copies'] == 32 and any('no console-ramoops-0' in note for note in d.notes)
    passed.append('(c) pmsg zone only (console dropped), 32 copies at 8%/0.5%: all recovered')

    # 4. Size fields that lost bits shorten the files: the copies vote where they still reach.
    short_pmsg = decay(rf.pstore_view(zone_header(pmsg_zone, size=0x1ffff4 & ~(1 << 20) & ~(1 << 5))), P1, P0, rng)
    d = rf.decode({'console': decay(console, P1, P0, rng), 'pmsg': short_pmsg})
    check(d, expected, 'short pmsg')
    assert d.copies['pmsg']['copies'] == 16 and any('pmsg-ramoops-0 has' in note for note in d.notes)
    short_console = decay(rf.pstore_view(zone_header(console_zone, size=0x3ff94)), P1, P0, rng)
    d = rf.decode({'console': short_console, 'pmsg': None})
    check(d, expected, 'short console', complete=False, least=n * 98 // 100)
    passed.append('shortened files (pmsg size lost bits 20 and 5: 16 copies left; console size 0x3ff94 as in the first '
                  'run, pmsg dropped): recovered')

    # 5. A start field that gained a bit rotates the file: it is realigned.
    rotated = decay(rf.pstore_view(zone_header(pmsg_zone, start=0x10000)), P1, P0, rng)
    d = rf.decode({'console': decay(console, P1, P0, rng), 'pmsg': rotated})
    check(d, expected, 'rotated pmsg')
    assert d.copies['pmsg']['rotation'] == 0x10000 and d.vote['copies'] == 36
    rotated = decay(rf.pstore_view(zone_header(console_zone, start=0x1000 | 0x40)), P1, P0, rng)
    d = rf.decode({'console': rotated, 'pmsg': None})
    check(d, expected, 'rotated console', complete=False, least=n * 98 // 100)
    assert d.copies['console']['rotation'] == 0x1040 and d.vote['copies'] == 4
    passed.append('rotated files (pmsg start 0x10000, console start 0x1040): realigned and recovered')

    # 6. Every copy header damaged (a different magic bit in each, the version cleared): the layout comes from the voted
    #    headers repaired with their CRC; with every header wiped, layout 3's defaults still read the copies.
    c, p = bytearray(decay(console, P1, P0, rng)), bytearray(decay(pmsg, P1, P0, rng))
    for k, (buf, start) in enumerate([(c, i * 0xfffc) for i in range(4)] + [(p, i * 0xfffc) for i in range(32)]):
        buf[start + k % 8] &= ~(1 << (k % 6)) & 0xff
        buf[start + 8] = 0
    d = rf.decode({'console': bytes(c), 'pmsg': bytes(p)})
    check(d, expected, 'damaged headers')
    assert d.layout.from_header and d.layout.copies == (4, 32), d.notes
    c = bytearray(decay(console, P1, P0, rng))
    for i in range(4):
        c[i * 0xfffc:i * 0xfffc + rf.COPY_HEADER] = bytes(rf.COPY_HEADER)
    d = rf.decode({'console': bytes(c), 'pmsg': None})
    check(d, expected, 'wiped headers', complete=False, least=n * 98 // 100)
    assert not d.layout.from_header and d.layout.version == 3 and any('no header matched' in note for note in d.notes)
    passed.append('damaged copy headers: layout from the repaired header vote; wiped headers: layout 3 defaults')

    # 7. Other zone sizes (pagani-ramoops-console=0x20000): copies of 0xfff8 (2 + 32), found and read from the headers.
    other = rf.default_layout((0x20000, 0x200000))
    zones, kept = rebuild(expected, other)
    assert kept == expected
    d = rf.decode({'console': decay(rf.pstore_view(zones[0]), P1, P0, rng), 'pmsg': decay(rf.pstore_view(zones[1]), P1, P0, rng)})
    check(d, expected, 'other zones')
    assert d.layout.from_header and d.layout.copies == (2, 32) and d.layout.copy_bytes == 0xfff8 and d.vote['copies'] == 34
    passed.append('other zone sizes (console 0x20000): 2 + 32 copies of 0xfff8 from the copy headers, all recovered')

    # 7b. pagani-ramoops-copy=0x8000 (for when Linux keeps dropping pmsg): 8 console copies of 0x7ffc, found without and
    #     read from the headers; the console zone alone at 15% still gives every record its shorter rings keep.
    eight = rf.default_layout(limit=0x8000)
    assert eight.copy_bytes == 0x7ffc and eight.copies == (8, 56) and eight.events == (0x1000, 0x37fc)
    zones, kept = rebuild(expected, eight)
    d = rf.decode({'console': decay(rf.pstore_view(zones[0]), 0.15, P0, rng), 'pmsg': None})
    check(d, kept, 'copy limit 0x8000')
    assert d.layout.from_header and d.layout.copies == (8, 56) and d.vote['copies'] == 8
    passed.append('pagani-ramoops-copy=0x8000: console zone alone (8 copies of 0x7ffc) at 15%%: all %d records its rings '
                  'keep recovered' % len(kept))

    # 8. Layout 2 (the first loop runs) still decodes: clean, decayed in all nine copies, and (as in the first run) the
    #    console copy alone, where whatever passes its CRC is right and the rest is shown unverified.
    zones, kept = rebuild(expected, rf.default_layout(version=2))
    assert kept == expected
    v2 = [rf.pstore_view(z) for z in zones]
    d = rf.decode({'console': v2[0], 'pmsg': v2[1]})
    check(d, expected, 'layout 2 clean')
    assert d.layout.version == 2 and d.layout.from_header and d.layout.copies == (1, 8) and d.layout.run_id == 0x5eed1234
    d = rf.decode({'console': decay(v2[0], P1, P0, rng), 'pmsg': decay(v2[1], P1, P0, rng)})
    check(d, expected, 'layout 2 decayed')
    assert d.layout.version == 2 and d.vote['copies'] == 9
    d = rf.decode({'console': decay(v2[0], P1, P0, rng), 'pmsg': None})
    verified = check(d, expected, 'layout 2 console only', complete=False)
    assert d.layout.version == 2 and d.vote['copies'] == 1 and d.unverified > 0
    assert all(not r.verified for r in d.records if r.source == 'unverified') and '? ' in rf.render(d.records)
    passed.append('layout 2 (1 + 8 of 0x3fff4): clean and 8%%-decayed recovered; console copy alone: %d checked (all '
                  'right), %d unverified shown with ?' % (verified, d.unverified))

    # 9. The summary: the newest complete GPU row set, the panic and the reset path.
    d = rf.decode({'console': console, 'pmsg': pmsg})
    number, rows = rf.gpu_rows(d.records)
    gpu = [r for s, r in sorted(expected.items()) if r[2] == 'G']
    assert number is not None and rows and gpu[-1][3].endswith(rows[-1]), (number, rows, gpu[-1])
    summary = rf.summary(d)
    for title in ('layout 3: 36 copies of 0xfffc', 'GPU STATUS ROWS', 'LAST STAGES', 'PANIC', 'RESET', 'LAST CONSOLE LINES'):
        assert title in summary, title
    assert rf.render(d.records).count('\n') == len(rf.joined(d.records))
    passed.append('summary: GPU rows set %d, panic, reset path, %d lines' % (number, summary.count('\n')))

    for line in passed:
        print('ok  ' + line)
    print('PASS: decoder on kernel writer output: %d records in 36 copies (layout 3) and rebuilt layout-2 images, DRAM '
          'decay injected into every copy, dropped / shortened / rotated zones' % n)


if __name__ == '__main__':
    main()
