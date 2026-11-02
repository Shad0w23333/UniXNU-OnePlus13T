#!/usr/bin/env python3
"""Read the UFS log back from Android and write an evidence directory (read-only on the phone).

A run with sheng-log=1 keeps snapshots of its ramoops log in the last 32 MiB of the unixnu partition (BootProgress UfsLog.c), so its log
survives a reset that loses the pstore zones. This reads the window with dd through adb + su, checks every record's CRC, merges the slots
by sequence number and writes runs/<time>[-<label>]/:

  records.jsonl, merged.log, summary.txt, decode.json   as read-ramoops.py writes them
  msgbuf.txt                                            the kernel's message buffer
  ufs-header.json, ufs-window.bin.gz                    the header; the window as read
  export/, export-manifest.json                         the newest complete export, when the header names one: the render server's cache
                                                        directory (ready for make-shader-cache-a830.py --merge-export), zz-oslog as text

  read-ufslog.py [--serial S] [--label L] [--runs DIR] [--no-export]
  read-ufslog.py --header                               only print the header
  read-ufslog.py --from-file WINDOW [--export-file F]   decode a saved window ([.gz]), F holding the export region

Only blockdev, readlink, cat of a partition's start in sysfs and dd (no of=) run on the phone; block numbers are worked out here, Android's shell
has 32-bit arithmetic. The header's first_lba is held against that start: a window the kernel put somewhere else is reported, not believed.
"""
import argparse
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ramoops_device as rd  # noqa: E402
import ramoops_format as rf  # noqa: E402
import ufslog_format as uf  # noqa: E402


def report(directory: Path, log: uf.UfsLog, verdict: uf.Verdict) -> None:
    print((directory / 'summary.txt').read_text(), end='')
    if verdict.kind == 'old':
        print('WARNING: ' + verdict.text)
    print('evidence: %s' % directory)


def read_phone(args) -> int:
    phone = rd.Phone(args.serial, args.adb, args.fastboot, verbose=False)
    state = phone.adb_state()
    if state != 'device':
        print('read-ufslog: no Android device on adb (state %r); boot the phone into Android first' % state)
        return 2
    phone.require_root()
    reader = uf.PhoneRead(phone)
    if reader.size() is None:
        print('read-ufslog: no %s of a size that holds the window' % uf.PARTITION)
        return 2
    place = reader.window_lba()
    if args.header:
        h, why = uf.parse_header(reader.read(reader.blocks - uf.WINDOW_BLOCKS, 1, 30))
        print(h.describe() if h else 'no usable header: %s' % why)
        if h and place is not None and h.first_lba != place:
            print('WARNING: the partition table puts the window at LBA %d' % place)
        return 0 if h else 3
    window = reader.window()
    log = uf.decode_window(window)
    verdict = uf.judge(log, None, None, place)
    export = None if args.no_export or verdict.kind == 'old' else uf.read_export(reader, log, print)
    directory = rd.evidence_dir(Path(args.runs), args.label)
    uf.write_evidence(directory, window, log, verdict, export, {'source': 'adb', 'serial': args.serial})
    report(directory, log, verdict)
    return 0 if log.decoded.records else 3


def read_saved(args) -> int:
    path = Path(args.from_file)
    window = path.read_bytes()
    if window[:2] == b'\x1f\x8b':
        window = gzip.decompress(window)
    log = uf.decode_window(window)
    export = None
    if args.export_file and log.header and log.header.export_bytes:
        h = log.header
        region = Path(args.export_file).read_bytes()
        export = region[h.export_first * uf.BLOCK:h.export_first * uf.BLOCK + h.export_bytes]
    verdict = uf.judge(log, None, None)
    directory = rd.evidence_dir(Path(args.runs), args.label or ('decoded-' + path.parent.name))
    uf.write_evidence(directory, window, log, verdict, export, {'source': str(path)})
    report(directory, log, verdict)
    return 0 if log.decoded.records else 3


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--serial', help='adb serial when several phones are connected')
    parser.add_argument('--label', help='suffix of the evidence directory name')
    parser.add_argument('--runs', default=str(rd.default_runs()), help='where evidence directories go (default: %(default)s)')
    parser.add_argument('--header', action='store_true', help='only print the header')
    parser.add_argument('--no-export', action='store_true', help='leave the export out')
    parser.add_argument('--from-file', help='decode a saved window instead of reading the phone')
    parser.add_argument('--export-file', help='with --from-file: the export region')
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--fastboot', default='fastboot')
    args = parser.parse_args()
    try:
        sys.exit(read_saved(args) if args.from_file else read_phone(args))
    except (rd.ToolError, uf.FormatError) as error:
        print('read-ufslog: %s' % error, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
