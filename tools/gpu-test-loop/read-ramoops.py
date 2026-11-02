#!/usr/bin/env python3
"""Read the UniXNU ramoops log back from Android and write an evidence directory (read-only on the phone).

After an XNU run with pagani-ramoops=1 has reset into Android, Linux exposes the copies of the log (layout 3: 4 in the
console zone, 32 in pmsg) as /sys/fs/pstore/console-ramoops-0 and pmsg-ramoops-0. This pulls both with adb (root through
su), votes the copies bit by bit, checks and repairs every record with its CRC, and writes runs/<time>[-<label>]/:

  console-ramoops-0.bin, pmsg-ramoops-0.bin   the raw files as Linux exposed them (a dropped zone has none)
  records.jsonl                               every record (seq, ms, kind, text, source: vote/repaired:<how>/unverified)
  merged.log                                  the merged log, one line per record ('+' continuations joined)
  summary.txt                                 last stages, GPU stage and status rows, panic, reset path, last console lines
  decode.json                                 per-copy statistics, repairs, lost ranges, phone facts

A UFS log on the phone (sheng-log=1, read-ufslog.py) is read as well, into ufs/ of the directory, and when it shows the run pstore shows the four
log files above become the merged log (UFS first) and pstore's own move to pstore/. A UFS log of another run is reported as old and left in
ufs/; so is one with nothing to compare it with (pstore names no run), with a warning: read it there. --no-ufs leaves it out.

  read-ramoops.py [--serial S] [--label L] [--runs DIR]
  read-ramoops.py --from-dir DIR [--runs DIR]       decode saved files (*-ramoops-0[.bin]) or raw zone images (*.zone,
                                                    decoded whatever their Linux header says)

Only getprop, ls, stat and cat/base64 run on the phone; nothing reboots. Note: Linux shows the copies until the next
reboot only, and the next XNU run overwrites the region.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ramoops_device as rd  # noqa: E402
import ufslog_format as uf  # noqa: E402


def read_phone(args) -> int:
    phone = rd.Phone(args.serial, args.adb, args.fastboot)
    state = phone.adb_state()
    if state != 'device':
        print('read-ramoops: no Android device on adb (state %r); boot the phone into Android first' % state)
        return 2
    phone.require_root()
    pulled = phone.pull_pstore()
    copies = {name: entry['data'] for name, entry in pulled.items()}
    listing = next(iter(pulled.values()))['listing']
    facts = phone.facts()
    directory = rd.evidence_dir(Path(args.runs), args.label)
    (directory / 'device.txt').write_text(''.join('%s: %s\n' % kv for kv in sorted(facts.items())) +
                                          'pstore: %s\n' % ' '.join(listing))
    extra = {'source': 'adb', 'serial': args.serial, 'pstore_listing': listing, 'phone': facts,
             'files': {name: entry['file'] for name, entry in pulled.items()}}
    if args.extra:
        extra.update(args.extra)
    decoded = rd.write_evidence(directory, copies, extra)
    if not args.no_ufs:
        decoded = uf.read_beside_pstore(phone, directory, decoded, None, extra, print)
    report(directory, decoded, copies)
    if any(data is None for data in copies.values()) and facts.get('dmesg.ramoops'):
        print('ramoops messages of this Android boot (a zone Linux found invalid is dropped):')
        print('\n'.join('  ' + line for line in facts['dmesg.ramoops'].splitlines()))
    return 0 if decoded.records else 3


def read_saved(args) -> int:
    source = Path(args.from_dir)
    copies = rd.load_saved(source)
    directory = rd.evidence_dir(Path(args.runs), args.label or ('decoded-' + source.name))
    decoded = rd.write_evidence(directory, copies, {'source': str(source)})
    report(directory, decoded, copies)
    return 0 if decoded.records else 3


def report(directory, decoded, copies):
    sizes = ', '.join('%s %s' % (name, '%d bytes' % len(data) if data is not None else 'missing')
                      for name, data in copies.items())
    print('copies: %s' % sizes)
    for note in decoded.notes:
        print('note: %s' % note)
    if not decoded.records:
        print('no UniXNU ramoops records: the run did not log (pagani-ramoops=1 missing?), Linux dropped both zones, '
              'or this boot did not follow an XNU run')
    print((directory / 'summary.txt').read_text(), end='')
    print('evidence: %s' % directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--serial', help='adb/fastboot serial when several phones are connected')
    parser.add_argument('--label', help='suffix of the evidence directory name')
    parser.add_argument('--runs', default=str(rd.default_runs()), help='where evidence directories go (default: %(default)s)')
    parser.add_argument('--from-dir', help='decode saved copies instead of reading the phone')
    parser.add_argument('--no-ufs', action='store_true', help='leave out the UFS log (read-ufslog.py)')
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--fastboot', default='fastboot')
    parser.set_defaults(extra=None)
    args = parser.parse_args()
    try:
        sys.exit(read_saved(args) if args.from_dir else read_phone(args))
    except rd.ToolError as error:
        print('read-ramoops: %s' % error, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
