#!/usr/bin/env python3
"""Crash reports end to end, on the host: BootProgress/Test/crash.c --dump (CrashTrace.h through the actual RamoopsLog.c
writer: SpringBoard SIGSEGV every 5.4 s, PosterBoard SIGABRT, extensions SIGTERM) -> read-ramoops.py --from-dir (the
decoder, with DRAM decay injected into every copy) -> symbolize-crashes.py with a synthetic ipsw A2S cache, the same
symbols as a shuffled text table, and synthetic Mach-O files (thin and fat). Checks that every X record comes back verified,
the grouping by pid, the "same" records, the exit join, cache/main/image/file-offset naming (return addresses looked up at
address - 1), the ESR and exception decoding, and the first frame outside the abort machinery. With the real 21H461 cache
symbols (--real-a2s, or CRASH_A2S) it also names two known dyld addresses; the saved runs/ logs without X records give
exit status 3 and their fatal-exit table.

    python3 tests/test_crash.py [--crash-test PATH/TO/crash.c] [--real-a2s dsc.a2s] [--verbose]
"""
import argparse
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
import ramoops_format as rf  # noqa: E402

DEFAULT_TEST = TOOLS.parents[1] / 'UniXNU-Pagani-Drivers/drivers/BootProgress/Test/crash.c'
SYMBOLIZE = TOOLS / 'symbolize-crashes.py'

# Symbols of the synthetic shared cache (unslid addresses as crash.c's storm uses them).
CACHE_SYMBOLS = [
    (0x180000000, '_cache_start'),
    (0x1a9463e50, '__dyld_start'),
    (0x1a9463e60, '-[SBTestScene _crashingMethod:]'),
    (0x1b0000000, '-[UIView(Hierarchy) _testCaller]'),
    (0x1b0000100, '_after_caller'),
    (0x1d0000000, '___pthread_kill'),
    (0x1d0000ff0, '_pthread_kill'),
    (0x1d0001ff0, '_abort'),
    (0x1d0002ff0, '-[WKWallpaperRepresentingCollectionsManager _loadSystemWallpaperCollections]'),
    (0x1d0004000, '_end_marker'),
]


def a2s_binary(symbols):
    """ipsw's A2S v2 layout: header, sorted {u64 address, u32 string offset}, strings."""
    strings = bytearray()
    entries = []
    for address, name in sorted(symbols):
        entries.append(struct.pack('<QI', address, len(strings)))
        strings += name.encode() + b'\0'
    return b'A2S\x02' + struct.pack('<II', len(entries), len(strings)) + b''.join(entries) + bytes(strings)


def macho(symbols, base, size=0x100000, fat_offset=None):
    """A minimal arm64 Mach-O (__TEXT at base, defined symbols in section 1); with fat_offset a fat file with an arm64e
    slice first and this arm64 slice at fat_offset."""
    names = b'\0' + b''.join(name.encode() + b'\0' for _, name in symbols)
    sizeofcmds = 72 + 24
    symoff = 32 + sizeofcmds
    stroff = symoff + 16 * len(symbols)
    header = struct.pack('<IiiIIIII', 0xfeedfacf, 0x0100000c, 0, 6, 2, sizeofcmds, 0, 0)
    segment = struct.pack('<II16sQQQQiiII', 0x19, 72, b'__TEXT', base, size, 0, size, 5, 5, 0, 0)
    symtab = struct.pack('<IIIIII', 0x2, 24, symoff, len(symbols), stroff, len(names))
    table = bytearray()
    strx = 1
    for value, name in symbols:
        table += struct.pack('<IBBHQ', strx, 0x0f, 1, 0, value)
        strx += len(name) + 1
    thin = header + segment + symtab + bytes(table) + names
    if fat_offset is None:
        return thin
    other = bytes(64)
    fat = struct.pack('>II', 0xcafebabe, 2) + struct.pack('>iiIII', 0x0100000c, 2, 0x1000, len(other), 12) + \
        struct.pack('>iiIII', 0x0100000c, 0, fat_offset, len(thin), 12)
    blob = bytearray(fat_offset + len(thin))
    blob[:len(fat)] = fat
    blob[0x1000:0x1000 + len(other)] = other
    blob[fat_offset:] = thin
    return bytes(blob)


def decay(data, rng, p1=0.08, p0=0.005):
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


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--crash-test', type=Path, default=DEFAULT_TEST)
    parser.add_argument('--real-a2s', type=Path, default=Path(os.environ['CRASH_A2S']) if os.environ.get('CRASH_A2S') else None)
    parser.add_argument('--verbose', action='store_true', help='print the symbolized report')
    args = parser.parse_args()
    assert 'X' in rf.KINDS.decode() and rf.KIND_NAMES.get('X') == 'crash'
    with tempfile.TemporaryDirectory(prefix='crash-test-') as temporary:
        work = Path(temporary)
        binary = work / 'crash'
        build = run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', args.crash_test, '-o', binary])
        assert build.returncode == 0, build.stderr
        dump = work / 'dump'
        dump.mkdir()
        result = run([binary, '--dump', dump])
        assert result.returncode == 0, result.stdout + result.stderr
        expected = [line.split('\t', 1)[1] for line in (dump / 'expected.tsv').read_text().splitlines()]
        assert len(expected) >= 10

        # The decoder, with every copy decayed independently (the measured 8% loss of 1 bits).
        rng = random.Random(20261030)
        zones = {}
        for name in ('console', 'pmsg'):
            zone = (dump / (name + '.zone')).read_bytes()
            layout_bytes = 0xfffc
            body = bytearray(zone)
            for start in range(rf.ZONE_HEADER, len(zone) - layout_bytes + 1, layout_bytes):
                body[start:start + layout_bytes] = decay(zone[start:start + layout_bytes], rng)
            zones[name] = bytes(body)
        saved = work / 'saved'
        saved.mkdir()
        for name, data in zones.items():
            (saved / (name + '.zone')).write_bytes(data)
        decoded = run([sys.executable, TOOLS / 'read-ramoops.py', '--from-dir', saved, '--runs', work / 'runs'])
        assert decoded.returncode == 0, decoded.stdout + decoded.stderr
        run_dir = next((work / 'runs').iterdir())
        merged = (run_dir / 'merged.log').read_text()
        got = [line for line in merged.splitlines() if ' X  ' in line]
        texts = [line.split(' X  ', 1)[1] for line in got]
        assert texts == expected, (texts, expected)
        summary = (run_dir / 'summary.txt').read_text()
        assert 'CRASH REPORTS' in summary and 'FATAL EXITS' in summary, summary
        print('ok  %d X records decoded verified from decayed copies, in order; summary lists them' % len(texts))

        # Symbol sources: an A2S cache, the same symbols as a shuffled text table, Mach-O files.
        a2s = work / 'dsc.a2s'
        a2s.write_bytes(a2s_binary(CACHE_SYMBOLS))
        text = work / 'dsc.txt'
        rows = ['0x%x %s' % s for s in CACHE_SYMBOLS]
        random.Random(1).shuffle(rows)
        text.write_text('# address symbol\n' + '\n'.join(rows) + '\n')
        plugin = work / 'NativeAdrenoMetal'
        plugin.write_bytes(macho([(0x1000, '_adreno_submit'), (0x1220, '_adreno_wait_fence'), (0x2000, '_adreno_end')], 0))
        springboard = work / 'SpringBoard'
        springboard.write_bytes(macho([(0x100000000 + 0x5000, '_SBMain'), (0x100000000 + 0x6000, '_SBMainEnd')], 0x100000000,
                                      fat_offset=0x8000))
        outputs = {}
        for label, source in (('a2s', a2s), ('text', text)):
            out = run([sys.executable, SYMBOLIZE, run_dir, '--a2s', source, '--image', 'NativeAdrenoMetal=%s' % plugin,
                       '--image', 'SpringBoard=%s' % springboard, '--json', work / ('%s.json' % label)])
            assert out.returncode == 0, out.stdout + out.stderr
            outputs[label] = out.stdout
        a, t = (o.split('\n', 1)[1] for o in (outputs['a2s'], outputs['text']))
        assert a == t, 'the text table and the A2S cache must name the same'
        report = outputs['a2s']
        if args.verbose:
            print(report)
        must = [
            'SpringBoard pid 797 at',
            'SIGSEGV (11), EXC_BAD_ACCESS KERN_INVALID_ADDRESS at 0x28; exit status=b',
            'data abort from EL0, translation fault level 2, read',
            '-[SBTestScene _crashingMethod:] + 4',                  # pc c:1a9463e64: the symbol at 0x1a9463e60, + 4
            'NativeAdrenoMetal            _adreno_submit + 512',    # lr a:1200 (return address: looked up at 0x11ff)
            '_adreno_wait_fence + 20',                              # #1 a:1234
            '-[UIView(Hierarchy) _testCaller] + 16',                # #2 c:1b0000010
            'SpringBoard                  _SBMain + 1656',          # #3 m:5678 in the fat file's arm64 slice
            'same trace as pid 797',
            'PosterBoard pid 1800 at',
            'SIGABRT (6); exit status=6',
            'SVC (a system call',
            'first frame outside the abort/exception machinery: #2  -[WKWallpaperRepresentingCollectionsManager '
            '_loadSystemWallpaperCollections] + 16',
            'FATAL EXITS',
        ]
        for needle in must:
            assert needle in report, needle
        crashes = json.loads((work / 'a2s.json').read_text())['crashes']
        assert crashes[0]['pid'] == 797 and crashes[0]['frames'][0]['symbol'] == '-[SBTestScene _crashingMethod:] + 4'
        assert any(c.get('same_as') == 797 for c in crashes)
        print('ok  symbolize: A2S cache and text table agree; cache, main (fat), image, same, exits, ESR, machinery')

        # A file-offset token (a mapping that does not start with its header) and raw/unknown tokens, in a hand-made log.
        lines = [
            (1, 'X', 'crash pid=5 Viewer sig=10 exc=1 code=0x2 sub=0x1040 esr=0x9200004f far=0x1040 tid=0x9'),
            (2, 'X', 'regs pid=5 pc=b@8120 lr=?:900001234 fp=0x16f000200 sp=0x16f000100 x0=0x1 x1=0x2 cpsr=0x20000000'),
            (3, 'X', 'map pid=5 slide=0x0 main=0x100000000 hash=0badf00d skipped=2 async=1'),
            (4, 'X', 'img pid=5 m=Viewer b=libfile.dylib'),
            (5, 'X', 'bt pid=5 0 b@8104 c:1d0004010 end=read@0x16f000300'),
            (6, 'S', 'exit pid=5 Viewer status=a ns=2 code=a'),
        ]
        log = work / 'hand.log'
        log.write_text(''.join('%8d %10.3f %s  %s\n' % (seq, seq / 10.0, kind, text) for seq, kind, text in lines))
        library = work / 'libfile.dylib'
        library.write_bytes(macho([(0x100, '_file_first'), (0x110, '_file_second')], 0, fat_offset=0x8000))
        out = run([sys.executable, SYMBOLIZE, log, '--a2s', a2s, '--image', 'libfile.dylib=%s' % library])
        assert out.returncode == 0, out.stdout + out.stderr
        for needle in ('SIGBUS (10), EXC_BAD_ACCESS KERN_PROTECTION_FAILURE at 0x1040',
                       'permission fault level 3, write', 'libfile.dylib                _file_second + 16',
                       'libfile.dylib                _file_first + 4', '_end_marker + 16', 'stopped: read@0x16f000300',
                       'Swift async frames 1', '2 crash(es) of this name left out'):
            assert needle in out.stdout, (needle, out.stdout)
        print('ok  file offsets in a fat file, raw tokens, a stopped chain, async frames, SIGBUS/permission fault decoding')

        # The real 21H461 cache symbols, when given.
        if args.real_a2s and args.real_a2s.is_file():
            real = work / 'real.log'
            real.write_text(''.join('%8d %10.3f %s  %s\n' % (seq, seq / 10.0, 'X', text) for seq, text in [
                (1, 'crash pid=9 dyldtest sig=5 exc=6 code=0x1 sub=0x0 esr=0xf2000001 far=0x0 tid=0x1'),
                (2, 'regs pid=9 pc=c:1a9463e50 lr=c:1a9463e68 fp=0x0 sp=0x16f000000 x0=0x0 x1=0x0 cpsr=0x0'),
                (3, 'bt pid=9 0 end=end')]))
            out = run([sys.executable, SYMBOLIZE, real, '--a2s', args.real_a2s])
            assert out.returncode == 0 and '__dyld_start + 0' in out.stdout and \
                '__ZN5dyld422restartWithDyldInCacheEPKNS_10KernelArgsEPKN5dyld39MachOFileEPv + 4' in out.stdout, out.stdout
            assert 'BRK instruction #0x1 (Swift/compiler trap)' in out.stdout
            print('ok  real 21H461 cache symbols (%s): __dyld_start, dyld4::restartWithDyldInCache' % args.real_a2s)
        else:
            print('--  real cache symbols not given (--real-a2s or CRASH_A2S): skipped')

        # Saved evidence without X records: exit status 3 and the fatal exits.
        for run_dir in sorted((TOOLS / 'runs').glob('*/merged.log')):
            out = run([sys.executable, SYMBOLIZE, run_dir])
            assert out.returncode in (0, 3), out.stdout + out.stderr
            if 'phone-pack-1' in str(run_dir):
                assert out.returncode == 3 and 'SpringBoard' in out.stdout and 'status=b' in out.stdout, out.stdout
                assert 'PosterBoard' in out.stdout and 'status=6' in out.stdout
        print('ok  saved runs/ logs: no crash reports yet (exit 3), fatal exits listed')
    print('crash reports: all tests passed')


if __name__ == '__main__':
    main()
