#!/usr/bin/env python3
"""gpu-loop.py --yes and read-ramoops.py against fake adb/fastboot executables (no phone): Android -> reboot bootloader ->
fastboot boot -> "XNU" -> reset -> Android -> pstore read through su/base64 -> decode. A second scenario leaves the fake
phone in fastboot after the reset and expects the single `fastboot reboot` nudge; a third has Linux drop the pmsg zone
(as in the first real run): only console-ramoops-0 is listed, and its four copies must still give the log.

    python3 tests/test_loop_mock.py [--zones DIR]    # DIR with console.zone/pmsg.zone (tests/test_decode.py --keep DIR);
                                                     # without it the writer test is built and run for them
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
sys.path.insert(0, str(HERE.parent))
import ramoops_format as rf  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
import base64, json, os, sys, time
state_path = os.environ['FAKE_PHONE_STATE']
data_dir = os.environ['FAKE_PHONE_DATA']
tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if args[:1] == ['-s']:
    args = args[2:]
with open(state_path) as f:
    state = json.load(f)
def save():
    with open(state_path, 'w') as f:
        json.dump(state, f)
state['log'].append([tool] + args)
if state['mode'] == 'xnu' and time.time() - state['xnu_since'] > state['xnu_seconds']:
    state['mode'] = state['after_reset']
save()
mode = state['mode']
if tool == 'fastboot':
    if args == ['devices']:
        print('FAKE13T\tfastboot' if mode == 'fastboot' else '')
    elif args[:1] == ['boot'] and mode == 'fastboot':
        assert os.path.isfile(args[1])
        state['mode'] = 'xnu'; state['xnu_since'] = time.time(); state['booted'] = True; save()
        sys.stderr.write("Sending 'boot.img' OKAY\nBooting OKAY\n")
    elif args == ['reboot'] and mode == 'fastboot':
        state['mode'] = 'android'; save()
    else:
        sys.stderr.write('fake fastboot: bad request %r in %s\n' % (args, mode)); sys.exit(1)
    sys.exit(0)
if args == ['get-state']:
    if mode == 'android':
        print('device'); sys.exit(0)
    sys.stderr.write('error: no devices/emulators found\n'); sys.exit(1)
if mode != 'android':
    sys.stderr.write('error: no devices/emulators found\n'); sys.exit(1)
if args == ['reboot', 'bootloader']:
    state['mode'] = 'fastboot'; save(); sys.exit(0)
line = args[1] if len(args) > 1 else ''
def root(cmd):
    return line in ("su -c '%s'" % cmd, 'su -c %s' % cmd)
files = {'console-ramoops-0': 'console-ramoops-0.bin', 'pmsg-ramoops-0': 'pmsg-ramoops-0.bin'}
# The end of the unixnu partition: unixnu.json says its size and which block-range files it holds ([first block, file], the newest of the
# two window files once the phone went through "XNU"); blocks nothing holds read as zero. Asks for root and dd exactly as the reader does.
unixnu = os.path.join(data_dir, 'unixnu.json')
if args[0] == 'shell' and line == "su -c 'blockdev --getsize64 /dev/block/by-name/unixnu'" and os.path.exists(unixnu):
    with open(unixnu) as f:
        print(json.load(f)['bytes'])
    sys.exit(0)
if args[0] == 'shell' and line == "su -c 'readlink -f /dev/block/by-name/unixnu'" and os.path.exists(unixnu):
    print('/dev/block/sda16')
    sys.exit(0)
if args[0] == 'shell' and line == "su -c 'cat /sys/class/block/sda16/start'" and os.path.exists(unixnu):
    with open(unixnu) as f:
        print(json.load(f).get('start_sectors', ''))
    sys.exit(0)
if args[0] == 'exec-out' and line.startswith("su -c 'dd if=/dev/block/by-name/unixnu bs=4096 ") and os.path.exists(unixnu):
    assert line.endswith(" 2>/dev/null'"), line
    fields = dict(part.split('=') for part in line[len('su -c \'dd '):-len(" 2>/dev/null'")].split())
    skip, count = int(fields['skip']), int(fields['count'])
    with open(unixnu) as f:
        layout = json.load(f)
    out = bytearray(count * 4096)
    for first, name in layout['before' if state.get('booted') is None else 'after']:
        with open(os.path.join(data_dir, name), 'rb') as f:
            data = f.read()
        blocks = len(data) // 4096
        low, high = max(first, skip), min(first + blocks, skip + count)
        if low < high:
            out[(low - skip) * 4096:(high - skip) * 4096] = data[(low - first) * 4096:(high - first) * 4096]
    if os.path.exists(os.path.join(data_dir, 'short-dd')) and count > 1:
        out = out[:4096]                            # a read of the window that breaks off
    if os.path.exists(os.path.join(data_dir, 'no-header-dd')) and count == 1:
        out = b''                                   # the one before the run
    sys.stdout.buffer.write(bytes(out))
    sys.exit(0)
if args[0] == 'shell':
    if line == 'getprop sys.boot_completed':
        print('1')
    elif root('id'):
        print('uid=0(root) gid=0(root) context=u:r:su:s0')
    elif root('ls /sys/fs/pstore'):
        print(' '.join(n for n in files if os.path.exists(os.path.join(data_dir, files[n]))))
    elif line.startswith("su -c 'stat -c %s /sys/fs/pstore/"):
        name = line.split('/')[-1].rstrip("'")
        print(os.path.getsize(os.path.join(data_dir, files[name])))
    elif line.startswith("su -c 'dmesg"):
        print('[    1.994198] ramoops: found existing invalid buffer, size 18283476, start 0')
    elif line.startswith("su -c 'cat /sys/module/ramoops/parameters/"):
        print({'mem_address': '0x880000000', 'mem_size': '2359296'}.get(line.split('/')[-1].rstrip("'"), '0'))
    elif line.startswith('dumpsys battery'):
        levels = state['battery']                   # one reading per call; the last one stays
        level = levels.pop(0) if len(levels) > 1 else levels[0]
        save()
        print('  AC powered: false\n  USB powered: true\n  status: 2\n  level: %d\n  voltage: 4000\n  temperature: 300' % level)
    elif line.startswith('getprop') or line.startswith('cat ') or line.startswith('uname'):
        print('fake')
    else:
        sys.stderr.write('fake adb: unexpected shell %r\n' % line); sys.exit(1)
    sys.exit(0)
if args[0] == 'exec-out' and line.startswith("su -c 'base64 -w 0 /sys/fs/pstore/"):
    name = line.split('/')[-1].rstrip("'")
    with open(os.path.join(data_dir, files[name]), 'rb') as f:
        sys.stdout.write(base64.b64encode(f.read()).decode())
    sys.exit(0)
sys.stderr.write('fake adb: unexpected %r\n' % args); sys.exit(1)
'''


def zones_from_writer(directory: Path):
    return (directory / 'console.zone').read_bytes(), (directory / 'pmsg.zone').read_bytes()


def zones_from_writer_test(work: Path):
    import test_decode  # noqa: E402
    console, pmsg, _ = test_decode.writer_run(test_decode.DEFAULT_WRITER, work)
    return console, pmsg


def scenario(work: Path, console: bytes, pmsg: bytes, after_reset: str, kernel_args: str, dropped: str = None, battery=None,
             loop_args=()):
    data = work / 'data'
    data.mkdir()
    rng = random.Random(3)
    copies = {}
    for name, zone in (('console', console), ('pmsg', pmsg)):
        if name == dropped:
            copies[name] = None                     # Linux found the zone header invalid: no pstore file
            continue
        view = bytearray(rf.pstore_view(zone))
        for _ in range(len(view) // 200):            # the reset lost some bits (1 -> 0)
            bit = rng.randrange(len(view) * 8)
            view[bit >> 3] &= ~(1 << (bit & 7)) & 0xff
        (data / ('%s-ramoops-0.bin' % name)).write_bytes(bytes(view))
        copies[name] = bytes(view)
    bin_dir = work / 'bin'
    bin_dir.mkdir()
    for tool in ('adb', 'fastboot'):
        path = bin_dir / tool
        path.write_text(FAKE)
        path.chmod(0o755)
    state = work / 'state.json'
    state.write_text(json.dumps({'mode': 'android', 'xnu_since': 0, 'xnu_seconds': 2, 'after_reset': after_reset, 'log': [],
                                 'battery': battery or [80]}))
    image = work / 'a830-test.img'
    image.write_bytes(b'ANDROID!' + bytes(4096))
    (work / 'a830-test.payload.json').write_text(json.dumps({'kernel_args': kernel_args}))
    env = dict(os.environ, FAKE_PHONE_STATE=str(state), FAKE_PHONE_DATA=str(data))
    runs = work / 'runs'
    loop = HERE.parent / 'gpu-loop.py'
    dry = subprocess.run([sys.executable, str(loop), '--image', str(image), '--adb', str(bin_dir / 'adb'), '--fastboot',
                          str(bin_dir / 'fastboot'), '--runs', str(runs)], env=env, capture_output=True, text=True)
    assert dry.returncode == 0 and 'dry run' in dry.stdout, dry.stdout + dry.stderr
    assert json.loads(state.read_text())['log'] == [], 'a dry run must not call adb or fastboot'
    run = subprocess.run([sys.executable, str(loop), '--image', str(image), '--yes', '--label', 'mock', '--adb',
                          str(bin_dir / 'adb'), '--fastboot', str(bin_dir / 'fastboot'), '--runs', str(runs),
                          '--fastboot-timeout', '20', '--android-timeout', '60', '--fastboot-grace', '2', *loop_args],
                         env=env, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, run.stdout + run.stderr
    assert 'battery ' in run.stdout and ' before the run)' in run.stdout, run.stdout
    log = json.loads(state.read_text())['log']
    commands = [' '.join(entry) for entry in log]
    assert 'adb reboot bootloader' in commands and any(c.startswith('fastboot boot ') for c in commands), commands
    if after_reset == 'fastboot':
        assert 'fastboot reboot' in commands, commands
    else:
        assert 'fastboot reboot' not in commands, commands
    directory = next(runs.iterdir())
    decoded = rf.decode(copies)
    saved = [json.loads(line) for line in (directory / 'records.jsonl').read_text().splitlines()]
    assert len(saved) == len(decoded.records) and saved[-1]['seq'] == decoded.records[-1].seq
    for name in ('console', 'pmsg'):
        path = directory / ('%s-ramoops-0.bin' % name)
        assert path.read_bytes() == copies[name] if copies[name] is not None else not path.exists()
    if dropped:
        assert decoded.vote['copies'] == 4 and sum(r.verified for r in decoded.records) >= 0.98 * len(decoded.records)
        assert 'no %s-ramoops-0' % dropped in run.stdout and 'found existing invalid buffer' in run.stdout, run.stdout
    assert 'GPU status rows' in run.stdout and 'evidence:' in run.stdout, run.stdout
    # The reader alone, online (read-only commands only) and offline.
    before = len(log)
    read = subprocess.run([sys.executable, str(HERE.parent / 'read-ramoops.py'), '--adb', str(bin_dir / 'adb'), '--runs',
                           str(runs), '--label', 'again'], env=env, capture_output=True, text=True)
    assert read.returncode == 0, read.stdout + read.stderr
    later = [' '.join(entry) for entry in json.loads(state.read_text())['log'][before:]]
    assert not [c for c in later if 'reboot' in c or c.startswith('fastboot')], later
    offline = subprocess.run([sys.executable, str(HERE.parent / 'read-ramoops.py'), '--from-dir', str(directory), '--runs',
                              str(runs)], capture_output=True, text=True)
    assert offline.returncode == 0, offline.stdout + offline.stderr
    return decoded, commands, run.stdout


def no_charge_scenario(work: Path, kernel_args: str):
    """A phone whose battery stays at 20 % on a port that does not charge: the loop waits --charge-timeout, then stops
    with status 5 before any reboot."""
    bin_dir = work / 'bin'
    bin_dir.mkdir()
    for tool in ('adb', 'fastboot'):
        (bin_dir / tool).write_text(FAKE)
        (bin_dir / tool).chmod(0o755)
    state = work / 'state.json'
    state.write_text(json.dumps({'mode': 'android', 'xnu_since': 0, 'xnu_seconds': 2, 'after_reset': 'android', 'log': [],
                                 'battery': [20]}))
    image = work / 'a830-test.img'
    image.write_bytes(b'ANDROID!' + bytes(4096))
    (work / 'a830-test.payload.json').write_text(json.dumps({'kernel_args': kernel_args}))
    (work / 'data').mkdir()
    env = dict(os.environ, FAKE_PHONE_STATE=str(state), FAKE_PHONE_DATA=str(work / 'data'))
    run = subprocess.run([sys.executable, str(HERE.parent / 'gpu-loop.py'), '--image', str(image), '--yes', '--adb',
                          str(bin_dir / 'adb'), '--fastboot', str(bin_dir / 'fastboot'), '--runs', str(work / 'runs'),
                          '--charge-timeout', '1', '--battery-poll', '0.2'], env=env, capture_output=True, text=True, timeout=60)
    commands = [' '.join(entry) for entry in json.loads(state.read_text())['log']]
    assert run.returncode == 5, run.stdout + run.stderr
    assert not [c for c in commands if 'reboot' in c or c.startswith('fastboot')], commands
    assert '[0] battery 20 % < 40 %' in run.stdout and 'stopped' in run.stdout, run.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--zones', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(HERE))
    if args.zones:
        console, pmsg = zones_from_writer(args.zones)
    else:
        with tempfile.TemporaryDirectory(prefix='gpu-loop-writer-') as name:
            console, pmsg = zones_from_writer_test(Path(name))
    kernel_args = '-v pagani-gpu=1 pagani-ramoops=1 pagani-reset-after=60 pagani-panic-reset=15'
    for after_reset in ('android', 'fastboot'):
        with tempfile.TemporaryDirectory(prefix='gpu-loop-mock-') as name:
            decoded, commands, _ = scenario(Path(name), console, pmsg, after_reset, kernel_args)
            print('ok  reset into %-8s: %d records read and decoded from %d copies; %s' % (
                after_reset, len(decoded.records), decoded.vote['copies'], ' -> '.join(
                    c for c in commands if 'reboot' in c or c.startswith('fastboot boot'))))
    with tempfile.TemporaryDirectory(prefix='gpu-loop-mock-') as name:
        decoded, _, _ = scenario(Path(name), console, pmsg, 'android', kernel_args, dropped='pmsg')
        print('ok  pmsg zone dropped by Linux: %d of %d records checked from the console zone\'s %d copies' % (
            sum(r.verified for r in decoded.records), len(decoded.records), decoded.vote['copies']))
    with tempfile.TemporaryDirectory(prefix='gpu-loop-mock-') as name:
        _, commands, out = scenario(Path(name), console, pmsg, 'android', kernel_args, battery=[30, 35, 50, 65],
                                    loop_args=('--battery-poll', '0.1'))
        assert '[0] battery 30 % < 40 %' in out and 'battery 65 %: going on' in out, out
        print('ok  low battery: charged in Android from 30 %% to 65 %% before the run, then %s' % ' -> '.join(
            c for c in commands if 'reboot' in c or c.startswith('fastboot boot')))
    with tempfile.TemporaryDirectory(prefix='gpu-loop-mock-') as name:
        no_charge_scenario(Path(name), kernel_args)
        print('ok  battery that does not charge: stopped with status 5 before any reboot')
    print('PASS: gpu-loop --yes and read-ramoops against fake adb/fastboot (dry run touches nothing, fastboot nudge, '
          'read-only reader, a dropped zone, the battery gate)')


if __name__ == '__main__':
    main()
