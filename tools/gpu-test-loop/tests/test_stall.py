#!/usr/bin/env python3
"""The stall probe's records end to end, on the host: BootProgress/Test/stall.c --dump (StallProbe.h through the actual RamoopsLog.c writer) ->
read-ramoops.py --from-dir (the decoder) -> symbolize-crashes.py and stall-report.py. Then a hand-made log
for what one short run cannot show: a kbt pair with its long record, a thread runnable with idle CPUs, a page fault, on core, frames the Q img
records name, seconds of a timed animation, the App on screen, the IPI verdict, an old log. Also the keyboard trace's version 3 (tests/kbtrace-v3.c)
on the header the two patches give, and the patch's copy of the structures against StallProbe.h.

    python3 tests/test_stall.py [--stall-test PATH/TO/stall.c] [--patches DIR] [--verbose]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(HERE))
import ramoops_format as rf  # noqa: E402
import stall_format  # noqa: E402
import test_crash  # noqa: E402

DEFAULT_TEST = TOOLS.parents[1] / 'UniXNU-Pagani-Drivers/drivers/BootProgress/Test/stall.c'
DEFAULT_PATCHES = TOOLS.parents[1] / 'UniXNU-Pagani_SM8750-gpu/patches/xnu'
SYMBOLIZE = TOOLS / 'symbolize-crashes.py'
REPORT = TOOLS / 'stall-report.py'
SYMBOLS = [(0x180000000, '_cache_start'), (0x1a9463e50, '_mach_msg2_trap'), (0x1a9463e60, '_mach_msg2_internal'), (0x1b0000000, '-[UIView _testCaller]'),
           (0x1b0000100, '_after_caller'), (0x1d0004000, '_end_marker')]


def run(command, **options):
    return subprocess.run([str(c) for c in command], capture_output=True, text=True, **options)


def test_end_to_end(args, work):
    binary = work / 'stall'
    out = run(['cc', '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', args.stall_test, '-o', binary])
    assert out.returncode == 0, out.stderr
    dump = work / 'dump'
    dump.mkdir()
    out = run([binary, '--dump', dump])
    assert out.returncode == 0, out.stdout + out.stderr
    expected = [line.split('\t', 1) for line in (dump / 'expected.tsv').read_text().splitlines()]
    assert {kind for kind, _ in expected} == {'M', 'Q'}
    assert 'M' in rf.KINDS.decode() and 'Q' in rf.KINDS.decode() and rf.KIND_NAMES['M'] == 'stall-meter'
    target = work / 'run'
    out = run([sys.executable, TOOLS / 'read-ramoops.py', '--from-dir', dump, '--runs', target])
    assert out.returncode in (0, 3), out.stdout + out.stderr
    merged = next(target.glob('*/merged.log'))
    text = merged.read_text()
    for kind, line in expected:
        assert ' %s  %s\n' % (kind, line) in text, (kind, line, text[-2000:])
    print('ok  %d M and Q records through the writer and the decoder' % len(expected))
    return merged


def test_tools(merged, work, verbose):
    a2s = work / 'dsc.a2s'
    a2s.write_bytes(test_crash.a2s_binary(SYMBOLS))
    library = work / 'NativeAdrenoMetal'
    library.write_bytes(test_crash.macho([(0x1000, '_plugin_render'), (0x1200, '_plugin_wait')], 0))
    out = run([sys.executable, SYMBOLIZE, merged, '--a2s', a2s, '--image', 'NativeAdrenoMetal=%s' % library])
    if verbose:
        print(out.stdout)
    assert out.returncode == 0, out.stdout + out.stderr
    for needle in ('STALL SAMPLES', 'sample 1 at', 'gap110', '(122 ms since the newest frame, sign 2, 1 focal)', 'rs backboardd[38] tid 0x100: 等待', 'mach_msg2 port receive 超时3137ms',
                   'fg Preferences[448] tid 0x100: 等待', '_mach_msg2_internal + 4', '-[UIView _testCaller] + 16', 'NativeAdrenoMetal', '_plugin_wait + 52', 'k0   k:7123456',
                   'kbd: no such thread now'):
        assert needle in out.stdout, (needle, out.stdout)
    report = run([sys.executable, REPORT, merged, '--a2s', a2s, '--image', 'NativeAdrenoMetal=%s' % library, '--timeline', '--json', work / 'report.json'])
    if verbose:
        print(report.stdout)
    assert report.returncode == 0, report.stdout + report.stderr
    for needle in ('总览', '最严重的', '各列含义', '时间线', '等待：mach_msg2 port receive 超时3137ms', '没有观察到丢唤醒', '前台 App 主线程阻塞：mach_msg2 port receive 超时3137ms',
                   '内核线程调度延迟：最大 12.0 ms', 'vm_page_wait 睡眠 3 次', '只有帧前节奏的 gap 9/0/0', '内存账 MiB', '938 / 1406 / 977 / 0 / 352 / 5 / 430 / 1177 / 400 921',
                   'M mem wire=240000'):
        assert needle in report.stdout, (needle, report.stdout)
    rows = json.loads((work / 'report.json').read_text())['rows']
    gap = next(r for r in rows if r['类型'].startswith('gap'))
    assert gap['主线程'].startswith('rs W') and gap['前台'] == 'Preferences[448] W' and gap['调度'].endswith(' 12.0') and gap['VM'].split('/')[3] == '3'
    print('ok  symbolize-crashes.py and stall-report.py on those records')


def line(seq, seconds, kind, text):
    return '%8d %10.3f %s  %s\n' % (seq, seconds, kind, text)


def test_period_limits(work):
    """The limits of a timed animation's second: one with too many frames for a beat is no such second, and in one that is, a stall longer than the
    longest beat stays in the table."""
    rows = [
        (1, 10.0, 'F', 'FPS 10 (10-10) | ft 100 p99 100 max 100ms | gpu 10 cpu 0 | jit 1 | tch 0 last 9000 vl 0'),
        (2, 9.5, 'Q', 's=1 why=gap400 st=1 n=1 age=812 tch=- gpu=100 fb=3 since=812 ask=0'),
        (3, 9.5, 'Q', 's=1 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=95 hint=2 sc=-47'),
        (4, 20.0, 'F', 'FPS 60 (55-60) | ft 16 p99 99 max 100ms | gpu 60 cpu 0 | jit 2 | tch 0 last 9000 vl 0'),
    ]
    log = work / 'limits.log'
    log.write_text(''.join(line(*row) for row in rows))
    parsed = stall_format.parse([(r[0], r[1], r[2], True, r[3]) for r in rows])
    assert [frame['seconds'] for frame in parsed.frames if stall_format.periodic(frame)] == [10.0]
    report = run([sys.executable, REPORT, log, '--json', work / 'limits.json'])
    assert report.returncode == 0, report.stdout + report.stderr
    assert [r['时长'] for r in json.loads((work / 'limits.json').read_text())['rows']] == ['812'], report.stdout


def test_hand(work, verbose):
    log = work / 'hand.log'
    rows = [
        (2, 10.0, 'U', 'kbt MobileNotes[448] first dn @71398ms'),
        (3, 11.0, 'M', 'lat fg 100/210/4/2/1 MobileNotes[448]/2b5/47'),
        (4, 11.0, 'M', 'cpu idle 1,2,3,4,5,6,7,8 ipis 0,0,0,0,0,0,0,0 ipir 0,0,0,0,0,0,0,0 tmr 1,1,1,1,1,1,1,1'),
        (5, 11.0, 'M', 'ipi max 30.0,0.1,0.1,0.1,0.1,0.1,0.1,0.1 idleq 0,6,0,0,0,0,0,0 stuck 1,0,0,0,0,0,0,0 tick 41.0 img 1'),
        (6, 11.0, 'M', 'io rd 40 300 vfy 2.0 free 900 avail 200000 flt 5000 pin 20 cmp 1 dcmp 9 t1 0 t2 1 t3 0 q 100/2/3 kq 40/0/1'),
        (7, 11.0, 'F', 'FPS 30 (20-60) | ft 30.0 p99 300 max 320ms | gpu 30 cpu 0 | jit 2 | SLOW 320ms gap 310 off 0.0 take 0.0 copy 3.0 done 0.0 fb0 to0 | tch 12 last 8 vl 1'),
        # Runnable with an idle CPU beside it.
        (8, 12.0, 'Q', 's=1 why=gap200 st=1 n=1 age=200 tch=5 gpu=4 fb=3 since=205 ask=2'),
        (9, 12.0, 'Q', 's=1 cpu 0:-/500 1:Mail/2b5/31/100 2:Mail/2b6/31/100 3:Mail/2b7/31/100 rq=3 rt=0'),
        (10, 12.0, 'Q', 's=1 th rs pid=38 backboardd tid=0x100 st=R pri=54/54 cpu=- wt=190 hint=0'),
        (11, 12.0, 'Q', 's=1 bt rs pid=38 pc=?:104001234 lr=c:1b0000010 end=end'),
        # On core in the kernel, with a kernel pc.
        (12, 13.0, 'Q', 's=2 why=in st=7 n=1 age=300 tch=5 gpu=- fb=0 pid=448 MobileNotes open=1'),
        (13, 13.0, 'Q', 's=2 th app pid=448 MobileNotes tid=0x2b5 st=C pri=47/47 cpu=3 wt=280 x=ok hint=0 sc=301'),
        (14, 13.0, 'Q', 's=2 bt app pid=448 pc=c:1a9463e64 lr=c:1b0000010 kpc=k:7123456 c:1b0000010 end=end'),
        (15, 13.0, 'Q', 's=2 th kbd pid=90 kbd tid=0x300 st=W pri=31/31 cpu=- wt=9000 hint=14 ev=0x1 sc=305 to=12571'),
        (16, 13.5, 'Q', 's=3 why=in st=7 n=2 age=800 tch=5 gpu=- fb=0 pid=448 MobileNotes open=1'),
        (17, 13.5, 'Q', 's=3 th app pid=448 MobileNotes tid=0x2b5 st=C pri=47/47 cpu=3 wt=780 x=to hint=0'),
        (18, 14.0, 'U', 'kbt MobileNotes[448] long in b=73700 e=76837 d=3137'),
        # A page fault: the abort is in the PCB, the thread waits.
        (19, 15.0, 'Q', 's=4 why=gap100 st=2 n=1 age=100 tch=5 gpu=4 fb=3 since=103 ask=2'),
        (20, 15.0, 'Q', 's=4 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=95 hint=0 k=k:7123456,k:7234568'),
        (21, 15.0, 'Q', 's=4 bt rs pid=38 pc=c:1a9463e64 lr=c:1b0000010 esr=0x92000006 far=0x1000 end=end'),
        (22, 15.0, 'Q', 's=4 no sb'),
        # Sample lost: an F second of 500 ms and a long pair without any Q.
        (23, 20.0, 'F', 'FPS 2 (2-60) | ft 500 p99 500 max 500ms | gpu 2 cpu 0 | jit 1'),
        (24, 21.0, 'U', 'kbt Spotlight[419] long dn b=85000 e=87319 d=2319'),
        (25, 22.0, 'Q', 's=5 why=ipi st=1 n=1 age=0 tch=- gpu=- fb=0 cpu=4'),
        # Only the frames before tell, and it ended on its own: gap?, no F second says more.
        (26, 30.0, 'Q', 's=6 why=gap100 st=3 n=1 age=100 tch=- gpu=100 fb=3 since=100 ask=0'),
        (27, 30.0, 'Q', 's=6 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=95 hint=2 sc=-47'),
        # A tap on a screen idle for 40 s, 3 s without a frame: the F second's 40 s are the idle time, not this stall's.
        (28, 41.0, 'F', 'FPS 1 (1-1) | ft 40000 p99 40000 max 40000ms | gpu 1 cpu 0 | jit 0'),
        (29, 40.0, 'Q', 's=7 why=gap3000 st=4 n=5 age=3000 tch=2900 gpu=- fb=3 since=43000 ask=2'),
        (30, 40.0, 'Q', 's=7 th rs pid=38 backboardd tid=0x100 st=C pri=54/54 cpu=2 wt=2900 x=ok hint=0'),
    ]
    # The clock icon on a still screen: no stall, whatever the frames before say, also with a submission as its sign. The same 100 ms with a finger on the
    # glass stays, and so does 500 ms that is not a cursor's beat. A cursor's own second and a second of 20 Hz never become gap*.
    rows += [
        (31, 50.0, 'F', 'FPS 10 (10-10) | ft 100 p99 100 max 100ms | gpu 10 cpu 0 | jit 10 | tch 0 last 9000 vl 0'),
        (32, 49.5, 'Q', 's=8 why=gap100 st=5 n=1 age=100 tch=- gpu=100 fb=3 since=100 ask=0'),
        (33, 49.5, 'Q', 's=8 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=95 hint=2 sc=-47'),
        (34, 49.7, 'Q', 's=9 why=gap110 st=6 n=1 age=110 tch=- gpu=20 fb=3 since=111 ask=1 fg=0'),
        (35, 60.0, 'F', 'FPS 9 (9-9) | ft 111 p99 500 max 500ms | gpu 9 cpu 0 | jit 8 | tch 0 last 9000 vl 0'),
        (36, 61.0, 'F', 'FPS 20 (20-20) | ft 50 p99 51 max 51ms | gpu 20 cpu 0 | jit 11 | tch 0 last 9000 vl 0'),
        (37, 70.0, 'F', 'FPS 9 (9-9) | ft 111 p99 300 max 303ms | gpu 9 cpu 0 | jit 2 | tch 0 last 9000 vl 0'),
        (38, 71.0, 'F', 'FPS 10 (10-10) | ft 100 p99 510 max 510ms | gpu 10 cpu 0 | jit 3 | tch 4 last 8 vl 0'),
    ]
    # The App on screen, the render server and SpringBoard both waiting: idle in its run loop, blocked, on core; and nobody there.
    RUN_LOOP = 'hint=2 sc=-47 o=0x507000806'
    rows += [
        (39, 80.0, 'Q', 's=10 why=gap400 st=7 n=1 age=400 tch=380 gpu=- fb=0 since=5000 ask=2 fg=1'),
        (40, 80.0, 'Q', 's=10 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=3000 ' + RUN_LOOP),
        (41, 80.0, 'Q', 's=10 th sb pid=27 SpringBoard tid=0x200 st=W pri=47/47 cpu=- wt=2000 ' + RUN_LOOP),
        (42, 80.0, 'Q', 's=10 th fg pid=501 Preferences tid=0x900 st=W pri=47/47 cpu=- wt=390 ' + RUN_LOOP),
        (43, 82.0, 'Q', 's=11 why=gap1000 st=8 n=1 age=1000 tch=980 gpu=- fb=0 since=9000 ask=2 fg=1'),
        (44, 82.0, 'Q', 's=11 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=3000 ' + RUN_LOOP),
        (45, 82.0, 'Q', 's=11 th fg pid=501 Preferences tid=0x900 st=W pri=47/47 cpu=- wt=990 hint=6 sc=-36'),
        (46, 84.0, 'Q', 's=12 why=gap1000 st=9 n=1 age=1001 tch=980 gpu=- fb=0 since=9000 ask=2 fg=2'),
        (47, 84.0, 'Q', 's=12 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=3000 ' + RUN_LOOP),
        (48, 84.0, 'Q', 's=12 th fg pid=501 Preferences tid=0x900 st=C pri=47/47 cpu=2 wt=900 x=ok hint=0'),
        (49, 86.0, 'Q', 's=13 why=gap1000 st=10 n=1 age=1002 tch=980 gpu=- fb=0 since=9000 ask=2 fg=0'),
        (50, 86.0, 'Q', 's=13 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=3000 ' + RUN_LOOP),
        (51, 88.0, 'Q', 's=14 why=gap1000 st=11 n=1 age=1003 tch=980 gpu=- fb=0 since=9000 ask=2 fg=-1'),
        (52, 88.0, 'Q', 's=14 th rs pid=38 backboardd tid=0x100 st=W pri=54/54 cpu=- wt=3000 ' + RUN_LOOP),
        (53, 90.0, 'M', 'lat fg 10/1.0/0/0/0 k 40/3.5/0/0/0 ae/91'),
        (54, 90.0, 'M', 'io rd 4 3 vfy 0.1 free 7000 avail 300000 flt 9 pin 0 cmp 0 dcmp 0 pw 7 t1 0 t2 0 t3 0 q 200/0/0 kq 40/0/1 tq 18/1/2'),
        (55, 90.0, 'M', 'mem wire 240000 int 360000 ext 250000 cmp 256/512 thr 90000 pur 1200 zone 110000 ram 1048576000 gpu 400/921'),
        # A sure sign in the clock's second: somebody waits, whatever the beat.
        (56, 49.9, 'Q', 's=15 why=gap110 st=12 n=1 age=112 tch=3 gpu=- fb=3 since=113 ask=2 fg=0'),
    ]
    log.write_text(''.join(line(*row) for row in rows))
    parsed = stall_format.parse([(r[0], r[1], r[2], True, r[3]) for r in rows])
    assert len(parsed.samples) == 15 and len(parsed.seconds) == 2 and abs(parsed.kbt_offset - 61398) < 1
    assert parsed.samples[0].since == 205 and parsed.samples[0].asked == 2 and parsed.samples[5].asked == 0 and parsed.samples[1].since is None
    second = parsed.seconds[0]
    assert second.bands['fg']['over200'] == 1 and second.bands['fg']['who'] == 'MobileNotes[448]/2b5/47'
    assert second.io['read_ms'] == 300 and second.io['t2'] == 1 and second.io['q'] == '100/2/3' and second.io['kq'] == '40/0/1' and second.hungry[1] == 6
    assert second.fps == 30 and second.touches == 12 and second.vsync_late == 1 and second.touch_last == 8
    thread = parsed.samples[3].threads['rs']
    assert thread.fault and 'k:7234568' in thread.kernel and thread.wait_text() == '缺页'
    assert parsed.samples[3].missing == ['sb']
    assert parsed.samples[9].focal == 1 and parsed.samples[9].threads['fg'].run_loop and not parsed.samples[10].threads['fg'].run_loop
    assert parsed.samples[12].focal == 0 and parsed.samples[13].focal == -1 and parsed.samples[6].focal is None
    second = parsed.seconds[1]
    assert second.bands['k'] == {'n': 40, 'max': 3.5, 'over10': 0, 'over50': 0, 'over200': 0, 'who': 'ae/91'} and second.io['pw'] == 7 and second.io['tq'] == '18/1/2'
    assert second.mem == {'wire': 240000, 'int': 360000, 'ext': 250000, 'cmp': 256, 'held': 512, 'thr': 90000, 'pur': 1200, 'zone': 110000, 'ram': 1048576000,
                           'gpu': 400, 'gpu_peak': 921}
    second = parsed.seconds[0]
    assert [frame['jit'] for frame in parsed.frames][:2] == [2, 1] and second.mem == {}
    periodic = [frame['seconds'] for frame in parsed.frames if stall_format.periodic(frame)]
    assert periodic == [50.0, 60.0, 61.0], periodic
    report = run([sys.executable, REPORT, log, '--json', work / 'hand.json'])
    if verbose:
        print(report.stdout)
    assert report.returncode == 0, report.stdout + report.stderr
    rows = json.loads((work / 'hand.json').read_text())['rows']
    kinds = {}
    for r in rows:
        kinds.setdefault(r['类型'].split()[0], r)
    assert kinds['in']['判定'].startswith('在 CPU 上忙（内核态）') or kinds['in']['判定'].startswith('在 CPU 上忙'), kinds['in']
    assert kinds['in']['时长'] == '3137' and kinds['in']['副线程'].startswith('kbd W')
    assert kinds['dn*']['判定'] == '？没有采样' and kinds['dn*']['时长'] == '2319'
    assert sorted(r['时长'] for r in rows if r['类型'] == 'gap*') == ['303', '320', '500', '510'], rows
    # The clock's second is out, with a sign of ask=1 too; the cursor's and 20 Hz never came in. What it leaves out the overview says.
    assert not [r for r in rows if r['类型'] in ('gap?', 'gap') and r['时长'] in ('100', '110', '111') and float(r['时刻']) > 45], rows
    assert '周期动画秒 3 个' in report.stdout and '占 jit 29/' in report.stdout
    assert [r['类型'] for r in rows if r['时长'] == '112'] == ['gap'], rows
    # The App on screen tells a still screen from a stuck App, and its column what it saw.
    verdicts = {r['时长']: r for r in rows}
    assert '前台 App 主线程在运行循环空等：屏幕本来静止' in verdicts['400']['判定'] and verdicts['400']['前台'] == 'Preferences[501] W'
    assert '前台 App 主线程阻塞：semaphore_wait semaphore' in verdicts['1000']['判定']
    assert '前台 App 主线程在 CPU 上忙（用户态）' in verdicts['1001']['判定'] and verdicts['1001']['前台'] == 'Preferences[501] C'
    assert verdicts['1002']['前台'] == '主屏' and '前台' not in verdicts['1002']['判定'] and verdicts['1003']['前台'] == '-'
    assert any('卡在缺页' in r['判定'] for r in rows)
    assert any('空闲核' in r['判定'] and '根卷读 300 ms' in r['判定'] for r in rows), rows
    assert kinds['ipi']['判定'].startswith('调度看门狗触发：IPI')
    assert kinds['gap?']['时长'] == '100' and kinds['gap?']['主线程'] == 'rs W' and kinds['gap?']['前台'] == ''
    assert next(r for r in rows if r['主线程'] == 'rs C')['时长'] == '3000'
    assert '本轮有证据支持丢唤醒或 IPI 投递问题' in report.stdout
    assert '个 M 秒里醒着' in report.stdout
    assert '内核线程调度延迟：最大 3.5 ms' in report.stdout and 'vm_page_wait 睡眠 7 次，分布在 1 秒，最多一秒 7 次（90.0 s）' in report.stdout
    assert 'gap 等 200/0/0，键盘 40/0/1，只有帧前节奏的 gap 18/1/2' in report.stdout and '938 / 1406 / 977 / 1 / 352 / 5 / 430 / 1000 / 400 921' in report.stdout
    assert any(r['IPI'] == '30.0/6/1' for r in rows), rows
    # A second of M's tail, the sampler asleep: its zeros are nobody's look, and the table says so.
    tail = work / 'tail.log'
    tail.write_text(line(1, 20.0, 'M', 'ipi max 1.0,0.1,0.1,0.1,0.1,0.1,0.1,0.1 idleq 0,0,0,0,0,0,0,0 stuck 0,0,0,0,0,0,0,0 tick 0.0 img 0') +
                    line(2, 20.0, 'Q', 's=1 why=gap200 st=1 n=1 age=200 tch=5 gpu=4 fb=3 since=205 ask=2') +
                    line(3, 20.0, 'Q', 's=1 th rs pid=38 backboardd tid=0x100 st=R pri=54/54 cpu=- wt=190 hint=0'))
    report = run([sys.executable, REPORT, tail, '--json', work / 'tail.json'])
    assert report.returncode == 0, report.stdout + report.stderr
    assert json.loads((work / 'tail.json').read_text())['rows'][0]['IPI'] == '1.0/-/-'
    assert '1 个 M 秒里醒着 0 个' in report.stdout
    # One tick of the sampler in that second: no interval to show, a stuck IPI all the same.
    tail.write_text(tail.read_text().replace('stuck 0,0,0,0,0,0,0,0', 'stuck 1,0,0,0,0,0,0,0'))
    report = run([sys.executable, REPORT, tail, '--json', work / 'tail.json'])
    assert report.returncode == 0, report.stdout + report.stderr
    assert json.loads((work / 'tail.json').read_text())['rows'][0]['IPI'] == '1.0/0/1'
    # idleq alone is a point sample: no verdict from it, whatever the count. With latency past two ticks in that second, on any band, those seconds are named.
    idleq = 'ipi max 0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1 idleq 0,0,1,1,1,1,1,1 stuck 0,0,0,0,0,0,0,0 tick 11.0 img 0'
    sampled = work / 'idleq.log'
    sampled.write_text(line(1, 20.0, 'M', 'lat fg 900/9.0/0/0/0 k 90/19.0/1/0/0') + line(2, 20.0, 'M', idleq))
    report = run([sys.executable, REPORT, sampled])
    assert report.returncode == 0 and '本轮没有观察到丢唤醒：没有悬而未决的 IPI；idleq 所在的秒' in report.stdout and '有证据支持' not in report.stdout, report.stdout
    sampled.write_text(line(1, 20.0, 'M', 'lat fg 900/9.0/0/0/0 k 90/31.0/1/0/0') + line(2, 20.0, 'M', idleq) +
                       line(3, 21.0, 'M', 'lat fg 900/40.0/1/0/0') + line(4, 21.0, 'M', idleq.replace('idleq 0,0,1,1,1,1,1,1', 'idleq 0,0,0,0,0,0,0,0')))
    report = run([sys.executable, REPORT, sampled])
    assert report.returncode == 0 and '没有直接证据（没有悬而未决的 IPI）；1 秒同时有 idleq 和超过 20 ms 的调度延迟' in report.stdout and '全核都忙：20（' in report.stdout, report.stdout
    assert '有证据支持' not in report.stdout
    quiet = work / 'quiet.log'
    quiet.write_text(line(1, 20.0, 'M', 'lat fg 900/9.0/0/0/0') + line(2, 20.0, 'M', idleq.replace('idleq 0,0,1,1,1,1,1,1', 'idleq 0,0,0,0,0,0,0,0')))
    report = run([sys.executable, REPORT, quiet])
    assert '没有悬而未决的 IPI，也没有空闲核面对非空队列' in report.stdout, report.stdout
    old = work / 'old.log'
    old.write_text(line(1, 1.0, 'F', 'FPS 60 (60-60) | ft 16.6 p99 17.0 max 17.0ms | gpu 60 cpu 0 | jit 0'))
    assert run([sys.executable, REPORT, old]).returncode == 3
    assert run([sys.executable, SYMBOLIZE, old]).returncode == 3
    short = work / 'short.log'
    short.write_text(line(1, 1.0, 'Q', 's=9 th') + line(2, 1.0, 'Q', 's=9 bt') + line(3, 1.0, 'Q', 's=x') + line(4, 2.0, 'M', 'lat') +
                     line(5, 2.0, 'M', 'io') + line(6, 2.0, 'Q', 'img pid=1'))
    assert run([sys.executable, REPORT, short]).returncode == 0
    print('ok  runnable with an idle CPU, on core, a page fault, stalls without a sample, timed animations, the App on screen, M mem, the IPI verdict, old and short logs')


def test_abi(args, work):
    """The patch's copy of the structures against StallProbe.h: a mismatch leaves a phone without the probe, one boot later (boot_progress_stall_abi)."""
    added = '\n'.join(raw[1:] for raw in (args.patches / 'phone-stall-probe.patch').read_text().splitlines() if raw.startswith('+') and not raw.startswith('+++'))
    copy = added[added.index('#define BOOT_PROGRESS_STALL_VERSION'):added.index('extern char *proc_best_name')]
    show = '\nint main(void) { printf("%u\\n", BOOT_PROGRESS_STALL_ABI); return 0; }\n'
    values = []
    for name, text in (('patch', '#include <stdint.h>\n#include <stdio.h>\n' + copy + show), ('header', '#include <stdio.h>\n#include "StallProbe.h"\n' + show)):
        source = work / (name + '.c')
        source.write_text(text)
        binary = work / (name + '-abi')
        out = run(['cc', '-std=c11', '-Wno-unused-function', '-I', args.stall_test.resolve().parents[1], source, '-o', binary])
        assert out.returncode == 0, out.stderr
        values.append(run([binary]).stdout.strip())
    assert values[0] == values[1] and int(values[0]) >> 24 == 2, values
    # The kernel build's second pass only rebuilds the objects it lists, vm_resident.o is none of them: under XNU_BOOT_PROGRESS the count would stay 0.
    patch = (args.patches / 'phone-stall-probe.patch').read_text()
    resident = patch[patch.index('--- a/osfmk/vm/vm_resident.c'):]
    assert 'pagani_vm_page_waits++' in resident and '+#if' not in resident, resident
    print('ok  phone-stall-probe.patch and StallProbe.h: the same structures, ABI %#x' % int(values[0]))


def test_kbtrace(args, work):
    header = work / 'kbt' / 'bsd/kern/pagani_kbtrace.h'
    header.parent.mkdir(parents=True)
    for name in ('phone-keyboard-trace.patch', 'phone-stall-probe.patch'):
        text = (args.patches / name).read_text()
        chunks = []
        current = []
        for raw in text.splitlines(keepends=True):
            if raw.startswith(('--- a/', '--- /dev/null')) and current:
                chunks.append(''.join(current))
                current = []
            current.append(raw)
        chunks.append(''.join(current))
        wanted = [c for c in chunks if 'bsd/kern/pagani_kbtrace.h' in c.split('\n', 2)[0] + c.split('\n', 2)[1]]
        assert wanted, name
        patch = work / 'kbt' / (name + '.h.patch')
        patch.write_text(''.join(wanted))
        out = run(['patch', '-p1', '--batch', '-i', patch], cwd=work / 'kbt')
        assert out.returncode == 0, out.stdout + out.stderr
    binary = work / 'kbtrace-v3'
    flags = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wshorten-64-to-32', '-Wsign-compare', '-Wconversion', '-Wno-sign-conversion',
             '-Wimplicit-fallthrough', '-fsanitize=address,undefined', '-I', header.parent, '-isystem', args.stall_test.resolve().parents[1]]
    out = run(['cc', '-O1', '-g'] + flags + [HERE / 'kbtrace-v3.c', '-o', binary])
    assert out.returncode == 0, out.stderr
    out = run([binary], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
    assert out.returncode == 0, out.stdout + out.stderr
    print(out.stdout.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stall-test', type=Path, default=DEFAULT_TEST)
    parser.add_argument('--patches', type=Path, default=DEFAULT_PATCHES)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='stall-') as temporary:
        work = Path(temporary)
        merged = test_end_to_end(args, work)
        test_tools(merged, work, args.verbose)
        test_hand(work, args.verbose)
        test_period_limits(work)
        test_abi(args, work)
        test_kbtrace(args, work)
    print('stall records: all tests passed')


if __name__ == '__main__':
    main()
