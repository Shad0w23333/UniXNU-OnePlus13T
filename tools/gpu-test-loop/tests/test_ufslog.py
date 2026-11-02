#!/usr/bin/env python3
"""The UFS log's reader on windows the real writer made (BootProgress/Test/ufslog.c --dump: the kernel's UfsLog.c and RamoopsLog.c over a model of
NativeUfs) and on what becomes of them: a damaged header, a partial write, a slot of an old run, an old window as a whole, a window at the wrong
place, the export region, and read-ufslog.py, read-ramoops.py and gpu-loop.py --yes against the fake adb and fastboot of test_loop_mock.py. No
phone.

    python3 tests/test_ufslog.py [--writer-test PATH/TO/ufslog.c]
"""
import argparse
import dataclasses
import gzip
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import ramoops_format as rf  # noqa: E402
import ufslog_format as uf  # noqa: E402
import test_loop_mock as mock  # noqa: E402

DEFAULT_WRITER = HERE.parents[2] / 'UniXNU-Pagani-Drivers/drivers/BootProgress/Test/ufslog.c'
SLOT = 16 * uf.BLOCK
PARTITION_BYTES = 0x1000000000
PARTITION_BLOCKS = PARTITION_BYTES // uf.BLOCK
WINDOW_FIRST = PARTITION_BLOCKS - uf.WINDOW_BLOCKS
EXPORT_FIRST = WINDOW_FIRST - uf.EXPORT_BLOCKS
PARTITION_START = 107409152                 # the 512 GB PKX110: board.toml [userdata], checked in main()
WINDOW_LBA = PARTITION_START + WINDOW_FIRST
BOARD = HERE.parents[2] / 'UniXNU-Pagani_SM8750-gpu/board.toml'
ARGS = None                                 # main()'s: the zones' writer test lies beside --writer-test, in a copy too (mutate.py)


def writer_run(source: Path, work: Path):
    binary = work / 'ufslog-test'
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True)
    out = work / 'dump'
    subprocess.run([str(binary), '--dump', str(out)], check=True, stdout=subprocess.DEVNULL)
    runs = {}
    for name in ('normal', 'wrap', 'panic', 'reset'):
        info = dict(line.split('=') for line in (out / name / 'info.txt').read_text().split())
        runs[name] = ((out / name / 'window.bin').read_bytes(), int(info['sequence']), int(info['run']))
    return runs


def rewrite_header(window: bytes, **changes) -> bytes:
    h, why = uf.parse_header(window[:uf.BLOCK])
    assert h, why
    fields = dataclasses.asdict(dataclasses.replace(h, **changes))
    values = [uf.MAGIC] + [fields[name] for name in uf.FIELDS[1:]]
    block = bytearray(uf.HEADER.pack(*values))
    block[-4:] = struct.pack('<I', zlib.crc32(bytes(block[:-4])))
    return bytes(block) + window[len(block):]


def with_run(window: bytes, run: int) -> bytes:
    """The window of another ramoops run: the header and every slot's copy header say `run`."""
    h = uf.parse_header(window[:uf.BLOCK])[0]
    out = bytearray(rewrite_header(window, ramoops_run=run))
    for logical in uf.live_slots(h):
        at = slot_at(window, h, logical)
        struct.pack_into('<I', out, at + 20, run)
        struct.pack_into('<I', out, at + 60, zlib.crc32(bytes(out[at:at + 12]) + bytes(out[at + 16:at + 60])))
    return bytes(out)


def slot_at(window: bytes, h: uf.Header, logical: int) -> int:
    return (h.slot_first + uf.position(h, logical) * h.slot_blocks) * uf.BLOCK


def test_normal(window, sequence, run):
    log = uf.decode_window(window)
    d, h = log.decoded, log.header
    assert h and h.ramoops_run == run and log.run == run and h.state == 2 and h.slots_written >= 3, h
    assert h.first_lba == WINDOW_LBA == 124178176, h.first_lba
    assert [r.seq for r in d.records] == list(range(1, sequence + 1)), 'every record once, in order'
    assert all(r.source == 'ufs' and r.verified for r in d.records) and not d.missing and not d.notes, d.notes
    assert d.layout.copy_bytes == 0xfffc and d.layout.run_id == run
    assert log.msgbuf(window) == b'kernel message one\nkernel message two\n'
    kinds = {r.kind for r in d.records}
    assert {'B', 'A', 'T', 'U', 'C'} <= kinds, kinds
    assert 'sheng-log=1' in ' '.join(r.text for r in d.records if r.kind == 'A')
    assert any('ufslog open at LBA' in r.text for r in d.records)
    assert rf.summary(d) and rf.render(d.records)
    print('ok  normal: %d slots give all %d records, message buffer' % (h.slots_written, sequence))


def test_wrap(window, sequence, run):
    log = uf.decode_window(window)
    d, h = log.decoded, log.header
    assert h.slots_written > h.slot_count
    assert uf.live_slots(h) == list(range(32)) + list(range(h.slots_written - (h.slot_count - 32), h.slots_written))
    assert len(uf.live_slots(h)) == h.slot_count
    seqs = [r.seq for r in d.records]
    assert seqs[0] == 1 and seqs[-1] == sequence, (seqs[0], seqs[-1], sequence)
    assert seqs == sorted(set(seqs))
    # Pinned slots keep the boot; what the ring lost is one gap (and a record the ring kept in the middle of it), reported as such.
    assert 1 <= len(d.missing) <= 3 and any('turned over' in n for n in d.notes), (d.missing, d.notes)
    assert d.missing[-1][1] < sequence - 40000, d.missing
    print('ok  ring wrap: %d slots written, %d left, %d of %d records, gaps %s' % (h.slots_written, len(uf.live_slots(h)), len(seqs), sequence, d.missing))


def test_panic(window, sequence, run):
    log = uf.decode_window(window)
    d, h = log.decoded, log.header
    assert h.state == 5 and h.slots_written == 2 and any('panic flush' in n for n in d.notes)
    # Up to the snapshot: the reset's own records come after it and stay in DRAM.
    assert [r.seq for r in d.records] == list(range(1, len(d.records) + 1)) and d.records[-1].seq <= sequence
    panic = [r for r in rf.joined(d.records) if r.kind == 'P']
    assert panic and 'panic(cpu 0): the test' in panic[-1].text, panic
    assert any(r.kind == 'T' and r.text.startswith('panic: reset in') for r in d.records)
    assert 'PANIC' in rf.summary(d)
    print('ok  panic: state 5, the panic records in the last slot')


def test_reset(window, sequence, run):
    log = uf.decode_window(window)
    d, h = log.decoded, log.header
    assert h.state == 6 and any('last snapshot before a reset' in n for n in d.notes), (h.state, d.notes)
    assert [r.seq for r in d.records] == list(range(1, len(d.records) + 1)) and d.records[-1].seq <= sequence
    assert any(r.kind == 'T' and r.text == 'ufslog last snapshot before the reset: restart' for r in d.records)
    print('ok  reset: state 6, the reset\'s reason in the last slot')


def test_damaged_header(window, sequence, run):
    want = uf.decode_window(window).decoded.records
    for name, damaged in (('one bit', bytes([window[0] ^ 1]) + window[1:]),
                          ('a field', window[:76] + b'\xff' + window[77:]),          # rounds: the CRC fails
                          ('zeroed', bytes(uf.BLOCK) + window[uf.BLOCK:])):
        log = uf.decode_window(damaged)
        assert log.header is None and log.run == run, name
        assert any('no usable header' in n for n in log.decoded.notes), log.decoded.notes
        assert [r.seq for r in log.decoded.records] == [r.seq for r in want], name
    # With no header and no slot either: a blank window says so.
    log = uf.decode_window(bytes(len(window)))
    assert log.blank and not log.decoded.records and uf.judge(log, None, None).kind == 'none'
    # Garbage in the header and nothing behind it: no records, no crash.
    log = uf.decode_window(b'\x5a' * uf.BLOCK + bytes(len(window) - uf.BLOCK))
    assert log.header is None and not log.decoded.records and not log.blank
    # Geometry that points outside the window is refused like a bad CRC.
    assert uf.parse_header(rewrite_header(window, slot_count=100000)[:uf.BLOCK])[0] is None
    assert uf.parse_header(rewrite_header(window, slot_pinned=509)[:uf.BLOCK])[0] is None
    assert uf.parse_header(rewrite_header(window, version=2)[:uf.BLOCK])[0] is None
    print('ok  damaged header: the slots still give the run, by their own copy headers')


def test_partial_write(window, sequence, run):
    h = uf.parse_header(window[:uf.BLOCK])[0]
    whole = {r.seq: r for r in uf.decode_window(window).decoded.records}
    last = h.slots_written - 1
    at = slot_at(window, h, last)
    # A write that stopped in the middle of the current slot: some blocks are the older snapshot (here: zero), a tail is garbage.
    torn = bytearray(window)
    torn[at + 5 * uf.BLOCK:at + 8 * uf.BLOCK] = bytes(3 * uf.BLOCK)
    torn[at + 12 * uf.BLOCK:at + 13 * uf.BLOCK] = bytes(range(256)) * 16
    log = uf.decode_window(bytes(torn))
    records = log.decoded.records
    assert log.header and len(records) < len(whole) and len(records) > len(whole) / 2, (len(records), len(whole))
    for r in records:
        assert (r.ms, r.kind, r.text) == (whole[r.seq].ms, whole[r.seq].kind, whole[r.seq].text), 'a wrong record'
    assert log.decoded.missing, 'the loss is reported'
    # The slot's first block gone: it is read with the layout of the others.
    gone = bytearray(window)
    gone[at:at + 64] = bytes(64)
    log = uf.decode_window(bytes(gone))
    assert any('without an intact copy header' in n for n in log.decoded.notes), log.decoded.notes
    assert len(log.decoded.records) == len(whole)
    # A slot the header does not count is not read, whatever it holds: the round that wrote it did not finish.
    fewer = rewrite_header(window, slots_written=h.slots_written - 1)
    log = uf.decode_window(fewer)
    assert log.decoded.records[-1].seq < sequence
    # A header that went out and no slot since: nothing to read, and it says so.
    none = rewrite_header(window, slots_written=0, rounds=0)
    log = uf.decode_window(none)
    assert not log.decoded.records and any('finished no round' in n for n in log.decoded.notes)
    print('ok  partial write: nothing wrong comes out, the loss shows as missing records')


def test_old_run(window, sequence, run, other_window, other_run):
    h = uf.parse_header(window[:uf.BLOCK])[0]
    assert run != other_run
    # Slots of an older run behind the ones this run wrote: not counted, so not read.
    mixed = bytearray(other_window)
    mixed[:uf.BLOCK] = window[:uf.BLOCK]
    end = slot_at(window, h, h.slots_written)
    mixed[uf.BLOCK:end] = window[uf.BLOCK:end]
    log = uf.decode_window(bytes(mixed))
    assert [r.seq for r in log.decoded.records] == list(range(1, sequence + 1))
    # An older run's slot that the header counts (it should not happen) is left out by its run id.
    at = slot_at(window, h, 1)
    planted = bytearray(window)
    planted[at:at + SLOT] = other_window[at:at + SLOT]
    log = uf.decode_window(bytes(planted))
    assert any('another ramoops run' in n for n in log.decoded.notes)
    assert all(r.source == 'ufs' for r in log.decoded.records)
    # Without a header the newest run is the one at position 0.
    log = uf.decode_window(bytes(uf.BLOCK) + bytes(mixed[uf.BLOCK:]))
    assert log.run == run and [r.seq for r in log.decoded.records] == list(range(1, sequence + 1))
    assert any('others left out' in n and 'left out: 0' not in n for n in log.decoded.notes), log.decoded.notes
    # judge(): the header before the run, pstore's run, neither.
    log = uf.decode_window(window)
    same = dataclasses.replace(log.header)
    verdict = uf.judge(log, same, None)
    assert verdict.kind == 'old' and 'OLD UFS LOG' in verdict.text
    assert uf.judge(log, dataclasses.replace(log.header, boot_id=1), None).kind == 'current'
    assert uf.judge(log, None, run).kind == 'current'
    verdict = uf.judge(log, None, run ^ 1)
    assert verdict.kind == 'old' and 'OLD UFS LOG' in verdict.text
    assert uf.judge(log, None, None).kind == 'unknown'
    # Same boot_id wins over a matching run: the header did not move.
    assert uf.judge(log, same, run).kind == 'old'
    # A window that was blank before the run is this run's, unless pstore shows another run.
    assert uf.judge(log, uf.BLANK, None).kind == 'current'
    assert uf.judge(log, uf.BLANK, run ^ 1).kind == 'old'
    assert uf.judge(uf.decode_window(bytes(len(window))), uf.BLANK, None).kind == 'none'
    # The kernel's place against the partition table's, before anything else.
    verdict = uf.judge(log, uf.BLANK, run, WINDOW_LBA + 1)
    assert verdict.kind == 'old' and 'WRONG PLACE' in verdict.text and str(WINDOW_LBA) in verdict.text
    assert uf.judge(log, uf.BLANK, run, WINDOW_LBA).kind == 'current' and uf.judge(log, uf.BLANK, run, None).kind == 'current'
    print('ok  old run: uncounted and foreign slots left out, old logs called old, a blank window before, the wrong place')


def export_blob(files):
    root = b'/private/var/mobile/Library/Caches/com.apple.backboardd/m2v'
    stream = bytearray(b'M2VCACH1' + struct.pack('<I', len(root)) + root + struct.pack('<I', len(files)))
    for path, data in files:
        name = path.encode()
        stream += struct.pack('<IIIIQ', len(name), 0o100644, 501, 501, len(data)) + name + data
    return b'SHEXR001' + struct.pack('<II', len(stream), 0) + bytes(stream)


def test_export(work):
    text = b'# sheng oslog pass 3\n    0.000     1 launchd : default hello\n# 1 entries\n'
    files = [('tu/abc.cache', bytes(range(256)) * 100), ('zz-oslog/oslog.txt.gz', gzip.compress(text)), ('prime-done', b'one\ntwo\n')]
    blob = export_blob(files)
    out = work / 'export-evidence'
    out.mkdir()
    manifest = uf.unpack_export(blob, out)
    assert (out / 'export/tu/abc.cache').read_bytes() == files[0][1]
    assert (out / 'export/prime-done').read_bytes() == b'one\ntwo\n'
    assert (out / 'export/zz-oslog/oslog.txt').read_bytes() == text
    assert (out / 'export/zz-oslog/oslog.txt.gz').read_bytes() == files[1][1]
    stated = json.loads((out / 'export-manifest.json').read_text())
    assert stated == manifest and len(stated['files']) == 3 and stated['directory'].endswith('/m2v')
    assert not (out / 'export' / 'export-manifest.json').exists()
    # What must not unpack.
    for bad, why in ((blob[:30], 'cut'), (b'SHEXP001' + blob[8:], 'the image pack form'), (blob[:16] + b'XXXXXXXX' + blob[24:], 'stream magic')):
        target = work / 'bad'
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir()
        try:
            uf.unpack_export(bad, target)
        except uf.FormatError:
            continue
        raise AssertionError('accepted: ' + why)
    for evil in ('../escape', '/etc/passwd', 'a/../../b', '', 'nul\0byte'):
        target = work / 'bad'
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir()
        try:
            uf.unpack_export(export_blob([(evil, b'x')]), target)
        except uf.FormatError:
            assert not (work / 'escape').exists() and not list(target.rglob('b'))
            continue
        raise AssertionError('accepted the path %r' % evil)
    # Paths that are fine one by one and cannot be files together: refused, not an exception out of the loop; the blob stays in export.bin.
    for pair in ((('a', b'1'), ('a/b', b'2')), (('a/b', b'1'), ('a', b'2'))):
        target = work / 'bad'
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir()
        try:
            uf.unpack_export(export_blob(list(pair)), target)
        except uf.FormatError:
            pass
        else:
            raise AssertionError('accepted %r' % (pair,))
        shutil.rmtree(target, ignore_errors=True)
        window = bytes(uf.WINDOW_BLOCKS * uf.BLOCK)
        log = uf.decode_window(window)
        conflict = export_blob(list(pair))
        uf.write_evidence(target, window, log, uf.Verdict('current', 'test'), conflict, {})
        assert (target / 'export.bin').read_bytes() == conflict and any('did not unpack' in n for n in log.decoded.notes)
    long_name = export_blob([('x' * 5000, b'1')])
    target = work / 'bad'
    shutil.rmtree(target, ignore_errors=True)
    uf.write_evidence(target, window, uf.decode_window(window), uf.Verdict('current', 'test'), long_name, {})
    assert (target / 'export.bin').read_bytes() == long_name
    trailing = bytearray(blob)
    trailing[8:12] = struct.pack('<I', struct.unpack_from('<I', blob, 8)[0] + 4)
    target = work / 'bad'
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    try:
        uf.unpack_export(bytes(trailing) + b'\0\0\0\0', target)
    except uf.FormatError:
        pass
    else:
        raise AssertionError('accepted trailing bytes')
    print('ok  export: files, zz-oslog as text, damaged blobs and paths refused')


def fake_phone(work: Path, windows, export=None, start=PARTITION_START):
    """A fake adb and fastboot whose unixnu partition, at LBA `start`, holds `windows` (before, after) at its end."""
    data = work / 'data'
    data.mkdir()
    bin_dir = work / 'bin'
    bin_dir.mkdir()
    for tool in ('adb', 'fastboot'):
        (bin_dir / tool).write_text(mock.FAKE)
        (bin_dir / tool).chmod(0o755)
    (data / 'window-before.bin').write_bytes(windows[0])
    (data / 'window-after.bin').write_bytes(windows[1])
    layout = {'bytes': PARTITION_BYTES, 'start_sectors': start * 8, 'before': [[WINDOW_FIRST, 'window-before.bin']],
              'after': [[WINDOW_FIRST, 'window-after.bin']]}
    if export:
        (data / 'export.bin').write_bytes(export[1])
        layout['after'].append([EXPORT_FIRST + export[0], 'export.bin'])
    (data / 'unixnu.json').write_text(json.dumps(layout))
    state = work / 'state.json'
    state.write_text(json.dumps({'mode': 'android', 'xnu_since': 0, 'xnu_seconds': 1, 'after_reset': 'android', 'log': [], 'battery': [80]}))
    env = dict(os.environ, FAKE_PHONE_STATE=str(state), FAKE_PHONE_DATA=str(data))
    return data, bin_dir, state, env


def pad(data: bytes) -> bytes:
    return data + bytes(-len(data) % uf.BLOCK)


def test_reader_on_phone(work, window, sequence, run):
    blob = export_blob([('zz-oslog/oslog.txt.gz', gzip.compress(b'hello os_log\n')), ('tu/x', b'abc')])
    h = uf.parse_header(window[:uf.BLOCK])[0]
    first = 32768
    named = rewrite_header(window, export_first=first, export_bytes=len(blob), export_generation=7, export_blocks=uf.EXPORT_BLOCKS,
                           export_below=uf.EXPORT_BLOCKS)
    data, bin_dir, state, env = fake_phone(work, (bytes(uf.BLOCK), named), (first, pad(blob)))
    json_state = json.loads(state.read_text())
    json_state['booted'] = True
    state.write_text(json.dumps(json_state))
    runs = work / 'runs'
    run_reader = subprocess.run([sys.executable, str(HERE.parent / 'read-ufslog.py'), '--adb', str(bin_dir / 'adb'), '--runs', str(runs),
                                 '--label', 'phone'], env=env, capture_output=True, text=True, timeout=120)
    assert run_reader.returncode == 0, run_reader.stdout + run_reader.stderr
    directory = next(runs.iterdir())
    saved = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines()]
    assert [r['seq'] for r in saved] == list(range(1, sequence + 1))
    for name in ('merged.log', 'summary.txt', 'decode.json', 'msgbuf.txt', 'ufs-header.json', 'ufs-window.bin.gz', 'export-manifest.json'):
        assert (directory / name).is_file(), name
    assert gzip.decompress((directory / 'ufs-window.bin.gz').read_bytes()) == named
    assert (directory / 'export/zz-oslog/oslog.txt').read_bytes() == b'hello os_log\n' and (directory / 'export/tu/x').read_bytes() == b'abc'
    assert json.loads((directory / 'decode.json').read_text())['export']['files'] == 2
    commands = [' '.join(entry) for entry in json.loads(state.read_text())['log']]
    # Read only, block numbers from here: only id, blockdev, readlink, the partition's start and dd went to the phone, at the partition's end.
    assert not [c for c in commands if 'reboot' in c or c.startswith('fastboot')], commands
    skips = [c for c in commands if 'dd if=' in c]
    assert any('skip=%d count=%d' % (WINDOW_FIRST, uf.WINDOW_BLOCKS) in c for c in skips), skips
    assert any('skip=%d count=%d' % (EXPORT_FIRST + first, (len(blob) + 4095) // 4096) in c for c in skips), skips
    assert not any(' of=' in c or 'blockdev --set' in c for c in commands), commands
    assert any('blockdev --getsize64' in c for c in commands) and not any('$((' in c for c in commands)
    allowed = ('id', 'blockdev --getsize64 /dev/block/by-name/unixnu', 'readlink -f /dev/block/by-name/unixnu', 'cat /sys/class/block/sda16/start')
    for command in commands:
        inner = command.split('su -c ', 1)[-1].strip("'")
        assert command.startswith(('adb get-state', 'adb shell getprop')) or inner in allowed or inner.startswith('dd if=/dev/block/by-name/unixnu bs=4096 skip='), command
    # --header prints it and reads one block; --from-file gets the same log; --no-export leaves it out.
    head = subprocess.run([sys.executable, str(HERE.parent / 'read-ufslog.py'), '--adb', str(bin_dir / 'adb'), '--header'], env=env,
                          capture_output=True, text=True, timeout=60)
    assert head.returncode == 0 and 'export generation 7' in head.stdout, head.stdout + head.stderr
    offline = subprocess.run([sys.executable, str(HERE.parent / 'read-ufslog.py'), '--from-file', str(directory / 'ufs-window.bin.gz'),
                              '--runs', str(runs), '--label', 'offline'], capture_output=True, text=True, timeout=60)
    assert offline.returncode == 0, offline.stdout + offline.stderr
    plain = subprocess.run([sys.executable, str(HERE.parent / 'read-ufslog.py'), '--adb', str(bin_dir / 'adb'), '--runs', str(runs),
                            '--label', 'noexport', '--no-export'], env=env, capture_output=True, text=True, timeout=120)
    assert plain.returncode == 0 and not list(runs.glob('*-noexport/export')), plain.stdout
    # A header that says another place than the partition table: said out loud, no export read from where it points.
    wrong = work / 'wrong'
    wrong.mkdir()
    data, bin_dir, state, env = fake_phone(wrong, (bytes(uf.BLOCK), named), (first, pad(blob)), start=44928768)
    json_state = json.loads(state.read_text())
    json_state['booted'] = True
    state.write_text(json.dumps(json_state))
    moved = subprocess.run([sys.executable, str(HERE.parent / 'read-ufslog.py'), '--adb', str(bin_dir / 'adb'), '--runs', str(wrong / 'runs'),
                            '--label', 'moved'], env=env, capture_output=True, text=True, timeout=120)
    assert moved.returncode == 0 and 'WARNING: UFS LOG AT THE WRONG PLACE' in moved.stdout, moved.stdout + moved.stderr
    assert not any('count=%d' % ((len(blob) + 4095) // 4096) in ' '.join(c) for c in json.loads(state.read_text())['log'])
    head = subprocess.run([sys.executable, str(HERE.parent / 'read-ufslog.py'), '--adb', str(bin_dir / 'adb'), '--header'], env=env,
                          capture_output=True, text=True, timeout=60)
    assert 'WARNING: the partition table puts the window at LBA' in head.stdout, head.stdout
    print('ok  read-ufslog: only read-only commands at the numbers worked out here; records, message buffer, export; the wrong place')


def loop_run(work, windows, console=None, pmsg=None, kernel_args='-v pagani-ramoops=1 pagani-reset-after=60 sheng-log=1', start=PARTITION_START,
             short_read=False, before_fails=False):
    data, bin_dir, state, env = fake_phone(work, windows, start=start)
    if short_read:
        (data / 'short-dd').write_text('1')
    if before_fails:
        (data / 'no-header-dd').write_text('1')
    if console:
        (data / 'console-ramoops-0.bin').write_bytes(console)
    if pmsg:
        (data / 'pmsg-ramoops-0.bin').write_bytes(pmsg)
    image = work / 'a830-test.img'
    image.write_bytes(b'ANDROID!' + bytes(4096))
    (work / 'a830-test.payload.json').write_text(json.dumps({'kernel_args': kernel_args}))
    runs = work / 'runs'
    run = subprocess.run([sys.executable, str(HERE.parent / 'gpu-loop.py'), '--image', str(image), '--yes', '--label', 'mock', '--adb',
                          str(bin_dir / 'adb'), '--fastboot', str(bin_dir / 'fastboot'), '--runs', str(runs), '--fastboot-timeout', '20',
                          '--android-timeout', '60', '--fastboot-grace', '2'], env=env, capture_output=True, text=True, timeout=180)
    return run, next(runs.iterdir()), json.loads(state.read_text())['log']


def writer_zones(work):
    import test_decode
    return test_decode.writer_run(ARGS.writer_test.with_name('ramoops.c'), work)[:2]


def test_loop(work, window, sequence, run, other_window):
    # This run's log: pstore is gone (the case the UFS log exists for), the header moved during the run. The window comes first.
    target = work / 'gone'
    target.mkdir()
    done, directory, commands = loop_run(target, (other_window, window))
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'new boot_id' in done.stdout and 'UFS log slots' in done.stdout, done.stdout
    saved = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines()]
    assert [r['seq'] for r in saved] == list(range(1, sequence + 1))
    assert (directory / 'ufs/msgbuf.txt').is_file() and (directory / 'pstore/summary.txt').is_file()
    joined = [' '.join(entry) for entry in commands]
    reboot = next(i for i, c in enumerate(joined) if 'reboot bootloader' in c)
    assert any('count=1 ' in c for c in joined[:reboot]), 'the header is read before the reboot'
    # An old window: the run never reached it, pstore has this run's log. The warning is plain and pstore's log stays on top.
    target = work / 'old'
    target.mkdir()
    done, directory, _ = loop_run(target, (window, window))
    assert done.returncode == 3 or done.returncode == 0, done.stdout + done.stderr
    assert 'OLD UFS LOG' in done.stdout, done.stdout
    assert (directory / 'ufs/summary.txt').is_file() and not (directory / 'pstore').exists()
    assert 'OLD UFS LOG' in (directory / 'ufs/summary.txt').read_text()
    # With pstore of the same run: pstore's records fill in behind the UFS log's, and its own files are kept.
    console, pmsg = writer_zones(target)
    target = work / 'both'
    target.mkdir()
    pstore = rf.decode({'console': rf.pstore_view(console), 'pmsg': rf.pstore_view(pmsg)})
    assert pstore.records
    same_run = with_run(window, pstore.vote['run_id'])
    assert len(uf.decode_window(same_run).decoded.records) == sequence
    done, directory, _ = loop_run(target, (other_window, same_run), rf.pstore_view(console), rf.pstore_view(pmsg))
    assert done.returncode == 0 and 'same ramoops run' in done.stdout, done.stdout + done.stderr
    assert (directory / 'pstore/records.jsonl').is_file() and (directory / 'console-ramoops-0.bin').is_file()
    saved = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines()]
    sources = {r['source'].split(':')[0] for r in saved}
    assert 'ufs' in sources and len(saved) >= sequence, sources
    assert any('records added from pstore' in n for n in json.loads((directory / 'decode.json').read_text())['decode']['notes'])
    # An image without sheng-log: not a single command at the partition.
    target = work / 'plain'
    target.mkdir()
    done, directory, commands = loop_run(target, (other_window, window), kernel_args='-v pagani-ramoops=1 pagani-reset-after=60')
    assert done.returncode in (0, 3), done.stdout + done.stderr
    assert not [c for c in commands if uf.PARTITION in ' '.join(c)] and not (directory / 'ufs').exists()
    # A window that was blank before the run: this run's, though its ramoops run says nothing to pstore's absence.
    target = work / 'blank'
    target.mkdir()
    done, directory, _ = loop_run(target, (bytes(uf.BLOCK), window))
    assert done.returncode == 0 and 'window was blank before this run' in done.stdout, done.stdout + done.stderr
    assert (directory / 'pstore').exists() and len((directory / 'records.jsonl').read_text().splitlines()) == sequence
    # The header not read before the run, and pstore without a run id: nothing tells, so the window stays out of the run's log.
    target = work / 'no-before'
    target.mkdir()
    done, directory, _ = loop_run(target, (window, window), without_run_id(rf.pstore_view(console)), without_run_id(rf.pstore_view(pmsg)),
                                  before_fails=True)
    assert 'UFS log header not read before the run' in done.stdout and 'WARNING: UFS log of ramoops run' in done.stdout, done.stdout + done.stderr
    assert not (directory / 'pstore').exists() and (directory / 'ufs/records.jsonl').is_file()
    assert 'ufs' not in {json.loads(line)['source'] for line in (directory / 'records.jsonl').read_text().splitlines()}
    # The pstore evidence when the window lies at the wrong place: top of the directory unchanged, the warning plain.
    target = work / 'moved'
    target.mkdir()
    done, directory, _ = loop_run(target, (other_window, same_run), rf.pstore_view(console), rf.pstore_view(pmsg), start=44928768)
    assert done.returncode == 0 and 'WRONG PLACE' in done.stdout and not (directory / 'pstore').exists(), done.stdout + done.stderr
    assert 'WRONG PLACE' in (directory / 'ufs/summary.txt').read_text()
    # A read of the partition that fails leaves pstore's evidence alone: every file of it, and nothing of the window.
    target = work / 'plain-pstore'
    target.mkdir()
    done, expected, _ = loop_run(target, (other_window, window), rf.pstore_view(console), rf.pstore_view(pmsg),
                                 kernel_args='-v pagani-ramoops=1 pagani-reset-after=60')
    assert done.returncode == 0, done.stdout + done.stderr
    target = work / 'short'
    target.mkdir()
    done, directory, _ = loop_run(target, (other_window, window[:uf.BLOCK * 10]), rf.pstore_view(console), rf.pstore_view(pmsg), short_read=True)
    assert done.returncode == 0 and 'UFS log not read' in done.stdout, done.stdout + done.stderr
    assert not (directory / 'ufs').exists() and not (directory / 'pstore').exists()
    for name in ('records.jsonl', 'merged.log', 'console-ramoops-0.bin', 'pmsg-ramoops-0.bin'):
        assert (directory / name).read_bytes() == (expected / name).read_bytes(), name
    assert 'evidence:' in done.stdout
    print('ok  gpu-loop: UFS log first when it is this run\'s, an old one called old, the wrong place, pstore kept whole, no sheng-log no commands')


def without_run_id(view: bytes) -> bytes:
    """pstore whose copy headers did not come through: records pass their CRC, the run id is gone (15 of the 22 runs with records so far)."""
    out = bytearray(view)
    for at in range(0, len(out) - 0xfffc + 1, 0xfffc):
        out[at:at + rf.COPY_HEADER] = bytes(rf.COPY_HEADER)
    return bytes(out)


def test_read_ramoops(work, window, sequence):
    """read-ramoops.py beside a UFS log: first when it shows pstore's run, old for another run; left in ufs/ with a warning when nothing tells:
    no pstore, or a pstore without a run id, whose records must not get mixed with an old window's. --no-ufs leaves it out."""
    console, pmsg = writer_zones(work)
    plain = (rf.pstore_view(console), rf.pstore_view(pmsg))
    pstore = rf.decode({'console': plain[0], 'pmsg': plain[1]})
    same_run = with_run(window, pstore.vote['run_id'])
    no_run = (without_run_id(plain[0]), without_run_id(plain[1]))
    assert rf.decode({'console': no_run[0], 'pmsg': no_run[1]}).vote.get('run_id') is None
    cases = (('same', same_run, plain, [], True), ('other', with_run(window, pstore.vote['run_id'] ^ 1), plain, [], False),
             ('gone', window, None, [], False), ('norun', window, no_run, [], False), ('off', same_run, plain, ['--no-ufs'], False))
    for label, chosen, zones, options, first in cases:
        target = work / label
        target.mkdir()
        data, bin_dir, state, env = fake_phone(target, (chosen, chosen))
        if zones:
            (data / 'console-ramoops-0.bin').write_bytes(zones[0])
            (data / 'pmsg-ramoops-0.bin').write_bytes(zones[1])
        runs = target / 'runs'
        read = subprocess.run([sys.executable, str(HERE.parent / 'read-ramoops.py'), '--adb', str(bin_dir / 'adb'), '--runs', str(runs),
                               '--label', label, *options], env=env, capture_output=True, text=True, timeout=120)
        assert read.returncode == (3 if label == 'gone' else 0), read.stdout + read.stderr
        directory = next(runs.iterdir())
        assert (directory / 'pstore').exists() == first, (label, read.stdout)
        assert (directory / 'ufs').exists() == (label != 'off'), label
        assert ('OLD UFS LOG' in read.stdout) == (label == 'other'), read.stdout
        if label in ('gone', 'norun'):
            # The warning is plain, the window is all in ufs/, and the top of the directory is what pstore gave: nothing of the old run.
            assert 'WARNING: UFS log of ramoops run' in read.stdout and 'nothing to compare it with' in read.stdout, read.stdout
            assert len((directory / 'ufs/records.jsonl').read_text().splitlines()) == sequence
            assert 'ufs' not in {json.loads(line)['source'] for line in (directory / 'records.jsonl').read_text().splitlines()}
    print('ok  read-ramoops: the UFS log first for the same run, old for another run, left in ufs/ when nothing tells, --no-ufs off')


def test_combined(window):
    log = uf.decode_window(window)
    d = log.decoded
    later = rf.Record(d.records[-1].seq + 1, 999999, 'T', 'reset reason=timer', 'vote')
    unchecked = rf.Record(d.records[-1].seq + 2, 999999, 'T', 'unverified', 'unverified')
    conflict = rf.Record(d.records[0].seq, 0, 'C', 'pstore version of the first record', 'vote')
    pstore = rf.Decoded([conflict, later, unchecked], None, {}, {}, 1, [], {'copies': 36}, [])
    out = uf.combined(d, pstore)
    assert out.records[-1].text == 'reset reason=timer' and out.records[-1].source == 'pstore:vote'
    assert out.records[0].text == d.records[0].text and out.records[0].source == 'ufs'
    assert not any(r.text == 'unverified' for r in out.records)
    print('ok  combined: UFS first, pstore fills in with checked records only')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--writer-test', type=Path, default=DEFAULT_WRITER)
    global ARGS
    args = ARGS = parser.parse_args()
    if BOARD.is_file():                     # not in a copy of this directory alone
        board = BOARD.read_text()
        assert 'first_lba = %d\n' % PARTITION_START in board and 'last_lba = %d\n' % (PARTITION_START + PARTITION_BLOCKS - 1) in board
    with tempfile.TemporaryDirectory(prefix='ufslog-test-') as name:
        work = Path(name)
        runs = writer_run(args.writer_test, work)
        window, sequence, run = runs['normal']
        test_normal(window, sequence, run)
        test_wrap(*runs['wrap'])
        test_panic(*runs['panic'])
        test_reset(*runs['reset'])
        test_damaged_header(window, sequence, run)
        test_partial_write(window, sequence, run)
        test_old_run(window, sequence, run, runs['wrap'][0], runs['wrap'][2])
        test_export(work)
        test_combined(window)
        phone = work / 'phone'
        phone.mkdir()
        test_reader_on_phone(phone, window, sequence, run)
        loop = work / 'loop'
        loop.mkdir()
        test_loop(loop, window, sequence, run, runs['wrap'][0])
        reader = work / 'reader'
        reader.mkdir()
        test_read_ramoops(reader, window, sequence)
    print('PASS: UFS log reader on the real writer\'s windows: normal, ring wrap, panic, damaged header, partial write, old run, export, phone')


if __name__ == '__main__':
    main()
