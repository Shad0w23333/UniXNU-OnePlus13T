#!/usr/bin/env python3
"""One photo-free test run of a UniXNU image: Android -> fastboot boot <image> -> XNU logs into the ramoops region and
warm-resets by itself -> Android -> read and decode the log.

  gpu-loop.py --image ../../UniXNU/out-gpu/a830-s3.img --label s3            # prints the plan only
  gpu-loop.py --image ../../UniXNU/out-gpu/a830-s3.img --label s3 --yes      # runs it

With --yes, for each of --count runs:
  0. battery gate: in Android, below --min-battery % (default 40) the phone stays in Android to charge up to --charge-to %
     (default 60) for at most --charge-timeout s, else the loop stops (XNU does not charge the battery like Android and
     drains it: a night of unattended runs can end with a phone that shut itself down)
  1. adb reboot bootloader          (skipped when the phone already sits in fastboot)
  2. wait for fastboot              (--fastboot-timeout)
  3. fastboot boot <image>          (the image is not flashed; the next reset boots Android)
  4. wait for Android to finish booting (sys.boot_completed=1, --android-timeout). The image has to reset by itself:
     pagani-reset-after=<s> (and pagani-panic-reset=<s>) in its kernel arguments. Should the bootloader stay in
     fastboot after XNU's reset, one `fastboot reboot` continues to Android.
  5. read the two pstore files through adb + su and decode them (as read-ramoops.py) into runs/<time>-<label>-<n>/; an image with
     sheng-log=1 also has its UFS log read (as read-ufslog.py) into <that directory>/ufs/: when it belongs to this run it comes first, the
     directory's log files become the merged log and the pstore ones move to pstore/; one left by an earlier run is reported as old and
     stays in ufs/, and so does one that nothing tells from an old one (the loop started in fastboot, and pstore names no run). The header is
     read before the reboot too, which is how an old log is told from this run's.
  6. print the last GPU stage and the GPU status rows

Nothing touches the phone without --yes. Ctrl-C stops waiting at any step; if XNU hangs without resetting, hold
power + volume down to reset by hand and rerun read-ramoops.py once Android is up (DRAM usually keeps the log).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ramoops_device as rd  # noqa: E402
import ramoops_format as rf  # noqa: E402
import ufslog_format as uf  # noqa: E402


def check_image(image: Path):
    facts = rd.image_facts(image)
    notes, problems = [], []
    line = facts.get('kernel_args')
    if line is None:
        notes.append('no payload record beside the image (%s.payload.json): kernel arguments not checked' % image.stem)
    else:
        args = rd.boot_args_of(line)
        ramoops = args.get('pagani-ramoops', '')
        if not ramoops.isdigit() or not int(ramoops) & 1:
            problems.append('pagani-ramoops=1 is missing: the run leaves no log')
        reset = args.get('pagani-reset-after', '')
        if not reset.isdigit() or not int(reset):
            problems.append('pagani-reset-after is missing: XNU will not reset by itself (reset by hand: power + volume down)')
        else:
            notes.append('XNU resets %s s after its log starts' % reset)
        copy = args.get('pagani-ramoops-copy')
        if copy:
            notes.append('copies of at most %s bytes (pagani-ramoops-copy; the decoder finds them by itself)' % copy)
        panic = args.get('pagani-panic-reset', '')
        notes.append('after a panic: reset in %s s' % panic if panic.isdigit() and int(panic) else
                     'after a panic: waits for the pagani-reset-after deadline' if reset.isdigit() and int(reset) else
                     'after a panic: halts')
        if args.get('sheng-log') == '1':
            notes.append('sheng-log=1: the run keeps its log on the unixnu partition; it is read back too (read-ufslog.py)')
            wdog = args.get('pagani-wdog', '')
            if not (wdog.isdigit() and int(wdog)) and not (reset.isdigit() and int(reset)) and args.get('pagani-ufslog-panic') != '1':
                notes.append('no pagani-wdog and no pagani-reset-after: no last UFS snapshot in a panic or before a reset')
    return facts, notes, problems


def uses_ufs_log(facts: dict) -> bool:
    return rd.boot_args_of(facts.get('kernel_args')).get('sheng-log') == '1'


def ufs_header_before(phone: rd.Phone, facts: dict):
    """What the UFS log window holds before the reboot: its header, uf.BLANK for a window nothing wrote, None when the image has no UFS log or
    the read fails (then nothing tells an old log from this run's but pstore's run id)."""
    if not uses_ufs_log(facts):
        return None
    try:
        reader = uf.PhoneRead(phone)
        return reader.before() if reader.size() is not None else None
    except (rd.ToolError, uf.FormatError) as error:
        phone.log('    UFS log header not read before the run: %s' % error)
        return None


def plan(args, image: Path, facts, notes, problems):
    print('image: %s' % image)
    print('  %d bytes, sha256 %s' % (facts['bytes'], facts['sha256']))
    if facts.get('kernel_args'):
        print('  kernel args: %s' % facts['kernel_args'])
    for note in notes:
        print('  note: %s' % note)
    for problem in problems:
        print('  WARNING: %s' % problem)
    serial = ['-s', args.serial] if args.serial else []
    print('plan (%d run%s):' % (args.count, '' if args.count == 1 else 's'))
    if args.min_battery > 0:
        print('  0. in Android: battery below %d %% -> charge to %d %% first (up to %d s, else stop)' % (
            args.min_battery, args.charge_to, args.charge_timeout))
    print('  1. %s reboot bootloader   (only when the phone is in Android)' % ' '.join(['adb'] + serial))
    print('  2. wait up to %d s for fastboot' % args.fastboot_timeout)
    print('  3. %s boot %s' % (' '.join(['fastboot'] + serial), image))
    print('  4. wait up to %d s for Android (sys.boot_completed=1); if the phone stays in fastboot after the reset: '
          'fastboot reboot (once)' % args.android_timeout)
    print('  5. read /sys/fs/pstore/console-ramoops-0 and pmsg-ramoops-0 (adb + su), decode, write %s/<time>-%s-<n>/' %
          (args.runs, args.label or image.stem))
    if uses_ufs_log(facts):
        print('     and the UFS log (dd of the end of %s, read only), which comes first when it is this run\'s' % uf.PARTITION)
    print('  6. print the last GPU stage and status rows')


def wait_fastboot(phone: rd.Phone, timeout: float) -> bool:
    return rd.wait_until(phone.in_fastboot, timeout, 1.0)


def wait_android(phone: rd.Phone, timeout: float, fastboot_grace: float, reset_after: float = 0) -> str:
    """'android', 'timeout'; nudges a phone that stays in fastboot once. From the timed reset + 90 s on, adb is restarted and the
    USB bus listed once a minute: twice a phone back in Android on time stayed invisible to adb for minutes (2026-10-30)."""
    start = time.monotonic()
    nudged = False
    fastboot_since = None
    last_report = 0.0
    last_restart = 0.0
    while time.monotonic() - start < timeout:
        if phone.boot_completed():
            return 'android'
        if phone.in_fastboot():
            fastboot_since = fastboot_since or time.monotonic()
            if not nudged and time.monotonic() - fastboot_since > fastboot_grace:
                phone.log('  the phone stays in fastboot after the reset: fastboot reboot')
                phone.fastboot('reboot', timeout=60, check=False)
                nudged = True
                fastboot_since = None
        else:
            fastboot_since = None
        elapsed = time.monotonic() - start
        if elapsed - last_report >= 30:
            state = phone.adb_state() or ('fastboot' if phone.in_fastboot() else 'not on adb or fastboot')
            phone.log('  %3.0f s: %s' % (elapsed, state))
            last_report = elapsed
        if reset_after and elapsed >= reset_after + 90 and elapsed - last_restart >= 60:
            phone.restart_adb()
            phone.log('  %3.0f s: adb restarted; USB: %s' % (elapsed, phone.usb_phones()))
            last_restart = elapsed
        time.sleep(3)
    return 'timeout'


def battery_level(facts: dict):
    try:
        return int(facts.get('level', ''))
    except ValueError:
        return None


def charge_gate(args, phone: rd.Phone):
    """Step 0, with the phone in Android: (level before the run or None, whether to go on). A level below --min-battery
    waits in Android until --charge-to or --charge-timeout; an unknown level (no answer) does not stop the run."""
    level = battery_level(phone.battery())
    if level is None or level >= args.min_battery:
        return level, True
    phone.log('[0] battery %d %% < %d %%: charging in Android to %d %% first (up to %d s)' % (
        level, args.min_battery, args.charge_to, args.charge_timeout))
    deadline = time.monotonic() + args.charge_timeout
    shown = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(args.battery_poll)
        battery = phone.battery()
        level = battery_level(battery) if battery_level(battery) is not None else level
        if level >= args.charge_to:
            phone.log('    battery %d %%: going on' % level)
            return level, True
        if time.monotonic() - shown >= 300:
            shown = time.monotonic()
            phone.log('    battery %d %% (status %s, USB powered %s)' % (level, battery.get('status', '?'),
                                                                     battery.get('USB powered', '?')))
    phone.log('battery %d %% after %d s, below %d %%: stopped (is the cable on a port that charges?)' % (
        level, args.charge_timeout, args.charge_to))
    return level, False


def one_run(args, phone: rd.Phone, image: Path, facts: dict, index: int) -> int:
    label = '%s-%d' % (args.label or image.stem, index)
    timings = {}
    t0 = time.monotonic()
    state = phone.adb_state()
    ufs_before = None
    if state == 'device':
        timings['battery_before'], go_on = charge_gate(args, phone)
        if not go_on:
            return 5
        ufs_before = ufs_header_before(phone, facts)
        t0 = time.monotonic()
        phone.log('[1] adb reboot bootloader')
        phone._adb('reboot', 'bootloader', timeout=30)
    elif phone.in_fastboot():
        phone.log('[1] already in fastboot' + (' (the UFS log window cannot be read from here: only pstore can tell this run\'s from an old one)'
                                               if uses_ufs_log(facts) else ''))
    else:
        phone.log('[1] waiting for the phone in Android or fastboot (adb state %r)' % state)
        if not rd.wait_until(lambda: phone.adb_state() == 'device' or phone.in_fastboot(), 60, 2.0):
            phone.log('no phone on adb or fastboot')
            return 2
        if phone.adb_state() == 'device':
            timings['battery_before'], go_on = charge_gate(args, phone)
            if not go_on:
                return 5
            ufs_before = ufs_header_before(phone, facts)
            t0 = time.monotonic()
            phone.log('    adb reboot bootloader')
            phone._adb('reboot', 'bootloader', timeout=30)
    phone.log('[2] waiting for fastboot (up to %d s)' % args.fastboot_timeout)
    if not wait_fastboot(phone, args.fastboot_timeout):
        phone.log('fastboot did not appear')
        return 4
    timings['to_fastboot_s'] = round(time.monotonic() - t0, 1)
    phone.log('[3] fastboot boot %s' % image)
    t_boot = time.monotonic()
    result = phone.fastboot('boot', str(image), timeout=600)
    phone.log('    ' + (result.stderr or result.stdout).decode(errors='replace').strip().replace('\n', '\n    '))
    phone.log('[4] waiting for Android (up to %d s)' % args.android_timeout)
    reset_after = float(rd.boot_args_of(facts.get('kernel_args')).get('pagani-reset-after', 0) or 0)
    outcome = wait_android(phone, args.android_timeout, args.fastboot_grace, reset_after)
    timings['boot_to_android_s'] = round(time.monotonic() - t_boot, 1)
    if outcome != 'android':
        phone.log('Android not seen within %d s (USB: %s). Before any forced reset: 1) look at the screen (Android lock '
                  'screen, the XNU status rows still counting, black, or charging); 2) replug the cable and run adb kill-server; '
                  'adb devices; 3) in Android, read the log at once: read-ramoops.py --label %s; 4) only a screen stuck in XNU '
                  'needs power + volume down.' % (args.android_timeout, phone.usb_phones(), label))
        return 4
    phone.log('[5] Android is up after %.0f s; reading the log' % timings['boot_to_android_s'])
    phone.require_root()
    pulled = phone.pull_pstore()
    copies = {name: entry['data'] for name, entry in pulled.items()}
    phone_facts = phone.facts()
    # When Linux started, from fastboot boot: the time to Android less Android's uptime. A timed reset puts it near
    # pagani-reset-after + 58.5 s (2026-10-30 runs); later means a late reset, earlier an unplanned one (panic, watchdog, power).
    try:
        uptime = float(phone_facts.get('uptime', '').split()[0])
        started = timings['boot_to_android_s'] - uptime
        timings['android_started_s'] = round(started, 1)
        verdict = ''
        if reset_after:
            expected = reset_after + 58.5
            verdict = (' (on time: the timed reset)' if abs(started - expected) <= 20 else
                       ' (early: an unplanned reset)' if started < expected else ' (late: the timed reset did not land on time)')
        phone.log('    Linux started %.0f s after fastboot boot%s' % (started, verdict))
    except (ValueError, IndexError):
        pass
    timings['battery_after'] = battery_level(phone.battery())
    if timings['battery_after'] is not None:
        before = timings.get('battery_before')
        phone.log('    battery %d %%%s' % (timings['battery_after'], '' if before is None else ' (%d %% before the run)' % before))
    directory = rd.evidence_dir(Path(args.runs), label)
    (directory / 'device.txt').write_text(''.join('%s: %s\n' % kv for kv in sorted(phone_facts.items())))
    extra = {'source': 'gpu-loop', 'image': facts, 'timings': timings, 'phone': phone_facts,
             'files': {name: entry['file'] for name, entry in pulled.items()}}
    decoded = rd.write_evidence(directory, copies, extra)
    if uses_ufs_log(facts):
        decoded = uf.read_beside_pstore(phone, directory, decoded, ufs_before, extra, lambda text: print('    ' + text))
    phone.log('[6] result')
    print_result(decoded, facts)
    if any(data is None for data in copies.values()) and phone_facts.get('dmesg.ramoops'):
        print('  ramoops messages of this Android boot (a zone Linux found invalid is dropped):')
        print('\n'.join('    ' + line for line in phone_facts['dmesg.ramoops'].splitlines()))
    print('evidence: %s' % directory)
    return 0 if decoded.records else 3


def print_result(decoded: rf.Decoded, facts: dict):
    records = decoded.records
    for note in decoded.notes:
        print('  note: %s' % note)
    if not records:
        print('  no UniXNU ramoops records (pagani-ramoops missing, the region was overwritten, Linux dropped both zones, '
              'or the reset did not keep DRAM)')
        return
    full = rf.joined(records)
    args_seen = ' '.join(r.text for r in full if r.kind == 'A' and r.verified)
    if facts.get('kernel_args') and args_seen and set(args_seen.split()) != set(facts['kernel_args'].split()):
        print('  WARNING: the log\'s boot-args differ from the image\'s: this log may come from another run')
    stats = decoded.summary_stats()
    if decoded.vote.get('source') == 'ufs':
        print('  records %d (seq %s..%s) from %s UFS log slots, each with its CRC' % (stats['records'], stats['first_seq'],
                                                                                     stats['last_seq'], decoded.vote.get('copies')))
    else:
        print('  records %d (seq %s..%s): %d verified, %d unverified (?); %d repaired; vote over %s copies, 1->0 %.1f%%, '
              '0->1 %.2f%%' % (stats['records'], stats['first_seq'], stats['last_seq'], stats['verified'],
                               stats['unverified'], sum(decoded.repaired.values()), decoded.vote.get('copies'),
                               100 * decoded.vote.get('p1', 0), 100 * decoded.vote.get('p0', 0)))

    def line(r):
        return ('' if r.verified else '? ') + r.text

    gpu_stage = [r for r in full if r.kind == 'R' and 'GPU' in r.text.upper()]
    roots = [r for r in full if r.kind == 'R']
    stages = [r for r in full if r.kind == 'S' and not r.text.startswith(('pid=', 'exit pid='))]
    print('  last GPU stage : %s' % (line(gpu_stage[-1]) if gpu_stage else '(none)'))
    print('  last status    : %s' % (line(roots[-1]) if roots else '(none)'))
    print('  last stage     : %s' % (line(stages[-1]) if stages else '(none)'))
    number, rows = rf.gpu_rows(records)
    print('  GPU status rows%s:' % ('' if number is None else ' (set %d)' % number))
    for row in rows or ['(none logged)']:
        print('    ' + row)
    panic = [r for r in full if r.kind == 'P']
    if panic:
        print('  PANIC:')
        for r in panic[:40]:
            print('    ' + line(r))
    for r in [r for r in full if r.kind == 'T'][-3:]:
        print('  reset: %s' % line(r))
    print('  last record %d at %.3f s: %s %s' % (records[-1].seq, records[-1].ms / 1000.0, records[-1].kind,
                                                 line(records[-1])))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', required=True, type=Path, help='boot image built with pagani-ramoops=1 pagani-reset-after=<s>')
    parser.add_argument('--yes', action='store_true', help='actually reboot the phone and run the plan')
    parser.add_argument('--label', help='evidence directory label (default: the image name)')
    parser.add_argument('--count', type=int, default=1, help='runs in a row (default 1)')
    parser.add_argument('--serial', help='adb/fastboot serial when several phones are connected')
    parser.add_argument('--runs', default=str(rd.default_runs()), help='where evidence directories go (default: %(default)s)')
    parser.add_argument('--fastboot-timeout', type=int, default=90)
    parser.add_argument('--android-timeout', type=int, default=900, help='from fastboot boot until Android booted (s)')
    parser.add_argument('--fastboot-grace', type=int, default=45, help='fastboot seconds after the reset before a nudge')
    parser.add_argument('--min-battery', type=int, default=40, help='below this level (%%) charge in Android first; 0: never')
    parser.add_argument('--charge-to', type=int, default=60, help='level (%%) the charge wait goes on at')
    parser.add_argument('--charge-timeout', type=int, default=10800, help='longest charge wait (s) before the loop stops')
    parser.add_argument('--battery-poll', type=float, default=60, help='seconds between battery readings while charging')
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--fastboot', default='fastboot')
    args = parser.parse_args()
    image = args.image.resolve()
    if not image.is_file():
        print('gpu-loop: no image at %s' % image)
        sys.exit(2)
    facts, notes, problems = check_image(image)
    plan(args, image, facts, notes, problems)
    if not args.yes:
        print('dry run: nothing was done. Add --yes to reboot the phone and run the plan.')
        return
    phone = rd.Phone(args.serial, args.adb, args.fastboot)
    status = 0
    try:
        for index in range(1, args.count + 1):
            print('=== run %d of %d ===' % (index, args.count))
            status = one_run(args, phone, image, facts, index)
            if status:
                break
    except rd.ToolError as error:
        print('gpu-loop: %s' % error, file=sys.stderr)
        status = 1
    except KeyboardInterrupt:
        print('gpu-loop: stopped')
        status = 130
    sys.exit(status)


if __name__ == '__main__':
    main()
