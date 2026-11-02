#!/usr/bin/env python3
"""adsp-rounds.py 的判据和走法，全在主机上：合成的运行目录（格式与 runs/ 里的一样，[adsp]、[spk]、[battery] 行是驱动主机仿真打印的原文，
PaganiAdsp.cpp 那几条内核侧的行照源码写），假的 gpu-loop 输出，再用真实运行目录做反例（它们没有一行 [adsp]）。
不碰手机：真跑的入口只在假 adb、fastboot 下调用过，看它只问了 devices。

    python3 tests/test_adsp_rounds.py            # 全部测试
    python3 tests/test_adsp_rounds.py --mutate   # 变异验证：把关键判据逐条改掉，每一条都要有测试失败
"""
import concurrent.futures
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import tomllib
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
TOP = TOOLS.parents[1]
MAKE = TOP / 'build-userspace/a830-inputs/fullrun/make-adsp-rounds.sh'
STAGE16 = TOP / 'build-userspace/a830-inputs/fullrun/make-stage16.sh'
DRIVERS = TOP / 'UniXNU-Pagani-Drivers/drivers'
BASELINE_SCRIPT = TOP / 'build-userspace/a830-inputs/ufslog/tools/check-baseline.sh'


def load():
    """按文件载入 adsp-rounds.py；ADSP_ROUNDS_MUTATION 是 [旧, 新]：变异验证用，文件本身不动。"""
    path = TOOLS / 'adsp-rounds.py'
    source = path.read_text()
    mutation = os.environ.get('ADSP_ROUNDS_MUTATION')
    if mutation:
        old, new = json.loads(mutation)
        if old not in source:
            sys.exit('变异点不存在：%s' % old)
        source = source.replace(old, new, 1)
    module = types.ModuleType('adsp_rounds')
    module.__file__ = str(path)
    sys.modules['adsp_rounds'] = module
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


ar = load()
UFS_OK = 'UFS log at LBA 124178176: boot_id 0x47bbc9eb, ramoops run 0x92b77965, state 6 (last snapshot before a reset), up %d s, 50 rounds, 20 slots, 0 write errors, gpt 0/0, period 3000 ms'
DEVICE_OK = '''battery: Charger voltage : 5123
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  level: 100
  voltage: 4428
pmic_info: PMIC|0|0x80|0x0084|Reset Type: HARD_RESET,Reset Trigger: PS_HOLD, (4)
'''
checks = 0


def check(condition, message=''):
    global checks
    assert condition, message
    checks += 1


class Log:
    """一份 merged.log：(时间, 种类, 文本)。"""

    def __init__(self, *lines):
        self.lines = list(lines)

    def write(self, directory: Path, summary: str, device: str = DEVICE_OK):
        directory.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self.lines, key=lambda item: item[0])
        text = ''.join('%8d %10.3f %s  %s\n' % (i + 1, t, kind, line) for i, (t, kind, line) in enumerate(ordered))
        (directory / 'merged.log').write_text(text)
        (directory / 'summary.txt').write_text(summary + '\n\nrecords 1\n')
        if device:
            (directory / 'device.txt').write_text(device)
        return directory

    def without(self, word):
        return Log(*[item for item in self.lines if word not in item[2]])

    def plus(self, *lines):
        return Log(*(self.lines + [(30.0, 'U', line) for line in lines]))

    def replaced(self, old, new):
        return Log(*[(t, k, line.replace(old, new)) for t, k, line in self.lines])

    def until(self, seconds):
        return Log(*[item for item in self.lines if item[0] < seconds])


GAUGE = ['[battery] i2c up st=5 err=0 vote=0x463c0b80 after=0x463c0b80 voted=0 cbcr=0x88000002 core=0x8004000 s1=0x8000000 rcg=0x100001 fw=0x304 clk=0x71 scl=0xa02c1a sda=0x2007 sclpin=0x2007',
         '[battery] gauge addr=0x55 rc=0 type=0x4231 NFG8011B nack=0 to=0',
         '[battery] gauge pass=1 mv=4457 ma=0 avg=57 t=28.10 soc=100 rm=5898 fcc=5898 cyc=31 soh=100 flags=0x20 tte=0 ttf=0 valid=0xfff retry=0 fail=0',
         '[battery] state published pct=100 ext=0 chg=0 full=0 crit=0 mv=4457 ma=57 t=28.10 rm=5898 fcc=5898 cyc=31 mcp=95 src=gauge+inferred']
BATTERY_DSP = ['[battery] dsp channel PMIC_RTR_ADSP_APPS registered (5 x 1024)', '[battery] dsp channel up',
               '[battery] dsp cyc=1 layout=oplus st=2 cap=8750 uv=3950000 ua=812000 t=- lim=3000000 en=- usb=1 type=0 adap=0 real=0 icl=1500000 vbus=0 ibus=0 ans=18 err=0 to=0 note=0 ign=0',
               '[battery] dsp cyc=2 layout=oplus st=2 cap=8750 uv=3950000 ua=812000 t=2950 lim=3000000 en=1 usb=1 type=0 adap=0 real=0 icl=1500000 vbus=0 ibus=0 ans=42 err=0 to=0 note=0 ign=0',
               '[battery] state changed pct=100 ext=1 chg=1 full=0 crit=0 mv=4450 ma=800 t=29.50 rm=5898 fcc=5898 cyc=31 mcp=95 adapter=0xe0004007 src=gauge+charger',
               '[battery] gauge pass=2 mv=4450 ma=800 avg=400 t=28.20 soc=100 rm=5898 fcc=5898 cyc=31 soh=100 flags=0x20 tte=0 ttf=0 valid=0xfff retry=0 fail=0']
ADSP = [(1, '[adsp] toc n=5 0xfffe/0xfffe@0x4000+0x98000 0x1/0x2@0x9c000+0x64000 0x0/0x2@0x100000+0x80000 0x0/0x1@0x180000+0x40000 0x5/0x2@0x1c0000+0x3c000'),
        (1, '[adsp] smem part adsp=0x100000+0x80000 cl=64 global=0x4000+0x98000'),
        (2, '[adsp] fw part=modem_a lun=4 first=4870 blocks=89600 fat=16 sector=4096 cluster=16384'),
        (2, '[adsp] fw adsp_dtb hdr=mdt phdrs=3 load=1 files=1 KiB=155 meta=852 reloc=1 span=0x27000 end=0x9eda7000 region=0x9ed80000+0x80000'),
        (2, '[adsp] fw adsp hdr=mdt phdrs=55 load=53 files=45 KiB=599 meta=2516 reloc=1 span=0xc2000 end=0x9eec2000 region=0x9ee00000+0x5080000'),
        (2, '[adsp] fw parsed ms=120 reads=100'), (3, '[adsp] smem touch adsp=0x100000 global=0x4000'),
        (3, '[adsp] survey items=0x0 slave=-1 qmp=LIAM qmp_magic=0x4d41494c qmp_ver=1 links=0xf drv2=0x30000 refused=0'),
        (4, '[adsp] smem oplus sensor items 130=0 128=0'), (4, '[adsp] smp2p out item=0x100030 size=340'),
        (5, '[adsp] ipcc intid=261 attach=0x0 readback=0xa003 router=0x0'), (6, '[adsp] qmp rc=0 reused=1 step=3 kicks=1 lock_waits=0'),
        (6, '[adsp] qmp load_state adsp on'),
        (7, '[adsp] rails on rc=0 lcx=0x30000 hlvl=3 vlvl=384 lmx=0x30010 hlvl=3 tcs=2 step=0 polls=4 lock_waits=0')]
SCM = '[adsp] scm %s pas=%s rc=0 fid=0x42000201 x0=0 x1=0x0 calls=1 intr=%d waitq=0 busy=0 us=%d lock_waits=0'
ADSP_RUN = [SCM % ('init_image', '0x24', 0, 1200), SCM % ('mem_setup', '0x24', 0, 800), '[adsp] pas loaded adsp_dtb pas=0x24 KiB=155 ms=9',
            SCM % ('auth_and_reset', '0x24', 3, 40000), '[adsp] pas auth_and_reset pas=0x24 rc=0 ms=40 x0=0 x1=0x0', SCM % ('init_image', '0x1', 0, 1500),
            SCM % ('mem_setup', '0x1', 0, 900), '[adsp] pas loaded adsp pas=0x1 KiB=59900 ms=800', SCM % ('auth_and_reset', '0x1', 12, 900000),
            '[adsp] pas auth_and_reset pas=0x1 rc=0 ms=900 x0=0 x1=0x0', '[adsp] slave-kernel value=0x2 changed=0x2',
            '[adsp] ready wait ms=430 slave=0x2 handover=0 irqs=2', '[adsp] glink start desc=0x100300 tx=0x100330+0x4000',
            '[adsp] glink rx fifo item=0x104340+0x4000', '[adsp] glink version agreed features=0x1', '[adsp] glink opens nobody serves: PMIC_LOGS_ADSP_APPS',
            '[adsp] channel IPCRTR up', '[adsp] slave-kernel value=0x6 changed=0x4', '[adsp] rails off rc=0 lcx=0x30000 hlvl=0 vlvl=0 lmx=0x30010 hlvl=0 tcs=2 step=0 polls=4 lock_waits=0',
            '[adsp] handover: proxy votes dropped rc=0', '[adsp] qrtr hello node=5', '[adsp] qrtr services n=1 0x777:0x1',
            '[adsp] locator ask=avs/audio domains=1 sent=1 from=5:32', '[adsp] locator ask=tms/servreg domains=6 sent=1 from=5:32']
SPEAKER = [(2, '[spk] rail ldob15 mv=1800 rc=0 detail=0x41e00'),
           (3, '[spk] bus clk=0 vote=0x4180 lock=1 waits=0 ahb=0x80000002 core=0x1 s4=0x1 rcg=0x0 rcg_fixed=0 fw=0x323 if=0x0 clkcfg=0x0 scl=0x0 set=1 abort=0 rc=0'),
           (4, '[spk] amp 0x34 rev=0xb73 type=tfa9873 table=10'), (4, '[spk] amp 0x35 rev=0xb73 type=tfa9873 table=10'),
           (5, '[spk] amp 0x34 config ok transfers=89 bits=32 slot=0 tdm=0x28037bf1 en=0x7923 slots=0xc107d21'),
           (5, '[spk] amp 0x35 config ok transfers=89 bits=32 slot=1 tdm=0x28037bf1 en=0x7923 slots=0x1c017d20'),
           (6, '[spk] dsp up waited_ms=2992'), (7, '[spk] apm state=ready queries=3 answers=3 errors=0 timeouts=0 last=1 ms=200')]
SMMU = ['[spk] smmu start sids=0xf', '[spk] smmu 0x1001=ok/0x7f 0x1081=ok/0x17f 0x1041=ok/0x27f 0x1061=ok/0x37f']
BUFFER = ['[spk] buffer pa=0xd0000000 dsp=0x1d0000000 bytes=65536', '[spk] graphs handle=0x77 bits=32 sd=sd0 prm_port=2 clock=3072000']


def tone_lines(run, channels):
    return ['[spk] play start run=%d amps=0x3 clks_wait_failed=0x0 prm=0 pins=0 queued=4 ms=2' % run,
            '[spk] amp 0x34 state=9 clks=1 plls=1 st=0x40 st2=0x40 st3=0x0 man=0x48 irq=0x0 bat=291 temp=33',
            '[spk] amp 0x35 state=9 clks=1 plls=1 st=0x40 st2=0x40 st3=0x0 man=0x48 irq=0x0 bat=291 temp=33',
            '[spk] play stop run=%d sent=62 played=58 werr=0 sendfail=0 stale=4 stalls=0 ms=559 lim=0 gmin=1000 in=1035 out=1035 clamp=0' % run,
            '[spk] tone rc=0 hz=440 ms=500 atten=30 gain=1036 periods=50 sent=62 played=58 amps=0x3 clks_failed=0 ch=%s out=1035 ceil=8231' % channels]


def stops(text, start=3.0):
    return [(start + i * 0.01, 'U', line) for i, line in enumerate(text)]


def good(name):
    """这一轮一次干净运行的日志。"""
    r = ar.ROUNDS[name]
    adsp_stop, speaker_stop = ar.argument(r, 'pagani-adsp-stop'), ar.argument(r, 'pagani-speaker-stop')
    tone = ar.argument(r, 'pagani-audio-tone')
    words = ar.command_line(r, ar.baseline_args())
    lines = [(0.0, 'B', 'UniXNU ramoops log v3 run=0x92b77965 mask=1'), (0.0, 'A', ' '.join(words)),
             (14.9, 'S', 'pid=27 SpringBoard exec process signature enter'), (14.9, 'S', 'pid=26 backboardd exec process signature enter'),
             (20.0, 'M', 'io rd 300 40 vfy 1.0 free 1 avail 1'), (48.7, 'U', 'diag: r1 sum @77 mg 6/14 pwr 100 c0 net 1 data 1300/2048M wk 35/35'),
             (61.2, 'F', 'FPS 35 (0-35) | ft 21.4 p99 168 max 198ms | gpu 35 cpu 0 | jit 3 | SLOW 198ms gap 198'),
             (r.reset - 0.1, 'T', 'ufslog last snapshot before the reset: timer')]
    battery, adsp, speaker = [], [], []
    if ar.turned_on(r, 'pagani-battery'):
        dsp = ar.turned_on(r, 'pagani-adsp')
        battery = ['[battery] start gauge=1 adsp=%d charge=0 design=6260 log=%d pmic=1 mode=1' % (dsp, 2 if dsp else 1)] + GAUGE + (BATTERY_DSP if dsp else [])
    if ar.turned_on(r, 'pagani-adsp'):
        adsp = ['[adsp] start stop=%d ready_ms=5000 part=modem_a' % adsp_stop] + [t for stage, t in ADSP if stage <= (adsp_stop or 99)]
        if adsp_stop == 7:
            adsp += ['[adsp] rails off rc=0 lcx=0x30000 hlvl=0 vlvl=0 lmx=0x30010 hlvl=0 tcs=2 step=0 polls=4 lock_waits=0',
                     '[adsp] qmp rc=0 reused=1 step=3 kicks=2 lock_waits=0', '[adsp] undo shutdown_adsp=0 shutdown_dtb=0 unvote=0 load_state_off=0']
        if adsp_stop:
            adsp.append('[adsp] stopped as asked (pagani-adsp-stop) st=%d' % adsp_stop)
        else:
            adsp += ADSP_RUN
    if ar.turned_on(r, 'pagani-speaker'):
        sids = ar.argument(r, 'pagani-speaker-sids') or 0xf
        speaker = ['[spk] start amps=0x3 bits=32 sd=sd0 prm_port=2 clock=1 feedback=0 rail=1 sids=%#x pa=0xd0000000 stop=%d ceil_db=-12 ceil=8231' % (sids, speaker_stop),
                   '[spk] pins sda=0x2007 scl=0x2007 sck=0x1 d0=0x1 ws=0x1 d1=0x1'] + [t for stage, t in SPEAKER if stage <= (speaker_stop or 99)]
        if not speaker_stop or speaker_stop >= 8:
            speaker += ['[spk] smmu start sids=%#x' % sids, '[spk] smmu 0x1001=ok/0x7f'] if sids == 1 else SMMU
        if not speaker_stop or speaker_stop >= 10:
            speaker += BUFFER
        if speaker_stop:
            speaker.append('[spk] stopped as asked (pagani-speaker-stop) st=%d' % speaker_stop)
        elif tone == 1:
            speaker += tone_lines(1, '0x3') + ['[spk] pagani-audio is not set: no iOS output, the speaker path stays idle (st=11 after a tone)']
        elif tone == 2:
            speaker += ['[spk] tone walk: 1 both speakers, 2 amplifier 0x34 alone (slot 0, the top one?), 3 amplifier 0x35 alone (slot 1)']
            speaker += tone_lines(1, '0x3') + tone_lines(2, '0x1') + tone_lines(3, '0x2')
            speaker += ['[spk] pagani-audio is not set: no iOS output, the speaker path stays idle (st=11 after a tone)']
        else:
            speaker += ['[spk] backend adsp handed to the PaganiAudio service (st=12)']
    return Log(*(lines + stops(battery, 2.0) + stops(adsp, 3.0) + stops(speaker, 6.0)))


def judge(work, name, log, summary=None, device=DEVICE_OK):
    r = ar.ROUNDS[name]
    directory = work / ('run%d' % len(os.listdir(work)))
    log.write(directory, summary or UFS_OK % (r.reset - 2), device)
    return ar.judge(ar.Evidence.read(directory), r)


def reasons(verdict):
    return ' | '.join(verdict.reasons)


def expect(work, name, cases, status='HARD'):
    """cases：(日志, 判决里要出现的话)。"""
    for log, word in cases:
        v = judge(work, name, log)
        check(v.status == status and word in reasons(v), (name, word, v.status, reasons(v)))


def test_table():
    base = ar.baseline_args()
    sizes = {name: ar.size_of(r, base) for name, r in ar.ROUNDS.items()}
    check(list(ar.ROUNDS) == ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'B4', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'D1', 'D2'], list(ar.ROUNDS))
    check(ar.LIMIT == 1020 and max(sizes.values()) <= ar.LIMIT, sizes)
    check(sizes['C3'] == 1013 and sizes['A1'] == 954 and sizes['B4'] == 952, sizes)
    args = {name: ' '.join(r.args) for name, r in ar.ROUNDS.items()}
    for name, want in (('A1', 'pagani-battery=1'), ('A2', 'pagani-speaker=1 pagani-speaker-stop=4'), ('A3', 'pagani-speaker=1 pagani-speaker-stop=5'),
                       ('B1', 'pagani-adsp=1 pagani-adsp-stop=3'), ('B2', 'pagani-adsp=1 pagani-adsp-stop=5'), ('B3', 'pagani-adsp=1 pagani-adsp-stop=7'),
                       ('B4', 'pagani-adsp=1'), ('C2', 'pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=7'),
                       ('C3', 'pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=8 pagani-speaker-sids=1'), ('C4', 'pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=8'),
                       ('C5', 'pagani-adsp=1 pagani-speaker=1 pagani-speaker-stop=10'), ('C6', 'pagani-adsp=1 pagani-speaker=1 pagani-audio-tone=1'),
                       ('C7', 'pagani-adsp=1 pagani-speaker=1 pagani-audio-tone=2'), ('D1', 'pagani-adsp=1 pagani-speaker=1'),
                       ('D2', 'pagani-battery=1 pagani-adsp=1 pagani-speaker=1')):
        check(args[name] == want, (name, args[name]))
    check(args['C1'].startswith('pagani-battery=1 pagani-adsp=1 ') and 'pagani-battery-log=2' in args['C1'])   # 比文档多日志级别：见文件头
    for r in ar.ROUNDS.values():
        words = ar.command_line(r, base)
        check(all(a in words for a in r.args), r.name)
        check(len({w.split('=')[0] for w in words}) == len(words), '同一个参数出现两次：%s' % r.name)
        for keep in ('sheng-log=1', 'pagani-reset-after=%d' % r.reset, 'pagani-panel=65', 'pagani-ramoops=1', 'pagani-panic-reset=20', 'pagani-wdog=30', 'pagani-reset-wdog=1',
                     'pagani-diag=1', 'native-ufs-root=1', 'sheng-gpu-metal-opens=0', 'pagani-gpu-profile=0', 'pagani-gpu-status=3'):
            check(keep in words, (r.name, keep))
        check(not any(w.split('=')[0] in ar.DROP_ALWAYS for w in words), r.name)
        check(not ar.forbidden(words), (r.name, ar.forbidden(words)))
        check(r.seed == 's7-final' and r.panel == 65 and r.step == r.name[0], r.name)
        check(any(w.startswith('pagani-battery=') for w in words) == (r.name in ('A1', 'C1', 'D2')), r.name)     # A830_BATTERY 不开
        check(not any(w.startswith(('pagani-wifi', 'pagani-sensors')) for w in words), r.name)
        check(r.touches and r.image.name == 'boot-A830-ADSP-%s.img' % r.name and r.label == 'adsp-' + r.name, r.name)
        check(('pagani-audio=1' in words) != (r.name in ('C6', 'C7')), r.name)          # 提示音之后不把后端交给 iOS
    check(ar.names_of('unattended') == ['A1', 'A2', 'A3', 'B1', 'B2'])
    check(ar.names_of('attended') == ['B3', 'B4', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'D1', 'D2'])
    for r in ar.ROUNDS.values():
        adsp_stop, speaker_stop, tone = ar.argument(r, 'pagani-adsp-stop'), ar.argument(r, 'pagani-speaker-stop'), ar.argument(r, 'pagani-audio-tone')
        adsp, speaker = ar.turned_on(r, 'pagani-adsp'), ar.turned_on(r, 'pagani-speaker')
        # 阶段的依赖：扬声器第 6 阶段起要 DSP 全程起来，提示音要扬声器走完全程；battmgr 通道要 DSP 全程
        if speaker and (not speaker_stop or speaker_stop >= 6):
            check(adsp and not adsp_stop, r.name)
        if tone:
            check(speaker and not speaker_stop, r.name)
        if ar.turned_on(r, 'pagani-battery') and adsp:
            check(not adsp_stop, r.name)
        check(speaker_stop in (0, 4, 5, 7, 8, 10) and adsp_stop in (0, 3, 5, 7), r.name)        # 这些值 stop_after 都认：SpeakerCore.h、AdspCore.h 的阶段
        if not r.attended:
            # 无人值守：不写 ADSP 的电源（stage 6 起是 QMP 和投票），不到 SMMU（扬声器 stage 8），不起 Q6，不出声
            check(not adsp or 0 < adsp_stop < 6, r.name)
            check(not speaker or 0 < speaker_stop < 6, r.name)
            check(not tone and not r.listen, r.name)
    check([n for n, r in ar.ROUNDS.items() if r.listen] == ['C6', 'C7', 'D1', 'D2'])
    check(all(ar.ROUNDS[n].human for n in ar.names_of('attended')))
    check(ar.names_of('B') == ['B1', 'B2', 'B3', 'B4'] and ar.names_of('nosuch') == [] and ar.names_of('all') == list(ar.ROUNDS))
    resets = {name: r.reset for name, r in ar.ROUNDS.items()}
    check(resets['B4'] >= 49 + 180 and resets['A1'] < resets['B1'] < resets['C1'] < resets['B4'], resets)    # B4 要桌面保持 3 分钟
    check(resets['C2'] > 120, resets)       # 等 adsp_apps 的上限 120 s：超时的 FAIL 行要看得到
    # 走到第几个阶段该有哪一行：这两张表不从 ar 里读，抽掉一项才抓得到
    check(ar.ADSP_WALK == ((1, '[adsp] toc '), (1, '[adsp] smem part '), (2, '[adsp] fw part='), (2, '[adsp] fw parsed'), (3, '[adsp] smem touch'), (3, '[adsp] survey '),
                           (4, '[adsp] smp2p out'), (5, '[adsp] ipcc intid='), (6, '[adsp] qmp rc='), (6, '[adsp] qmp load_state adsp on'), (7, '[adsp] rails on '),
                           (8, '[adsp] pas loaded adsp_dtb '), (9, '[adsp] pas loaded adsp '), (10, '[adsp] pas auth_and_reset pas=0x1 '), (11, '[adsp] ready wait '),
                           (12, '[adsp] glink version agreed'), (13, '[adsp] channel IPCRTR up'), (13, '[adsp] qrtr hello ')), ar.ADSP_WALK)
    check(ar.SPEAKER_WALK == ((2, '[spk] rail ldob15 '), (3, '[spk] bus '), (4, '[spk] amp 0x34 rev='), (4, '[spk] amp 0x35 rev='), (5, '[spk] amp 0x34 config ok'),
                              (5, '[spk] amp 0x35 config ok'), (6, '[spk] dsp up '), (7, '[spk] apm state=ready '), (8, '[spk] smmu start '), (8, '[spk] smmu 0x1001='),
                              (9, '[spk] buffer '), (10, '[spk] graphs ')), ar.SPEAKER_WALK)
    print('通过  轮次表：每条命令行都放得下，最长 %d 字节' % max(sizes.values()))


def test_forbidden():
    bad = ('pagani-battery-charge=1', 'pagani-battery-charge=2', 'pagani-battery-pmic=2', 'pagani-speaker-ceiling=6', 'pagani-speaker-ceiling=11', 'pagani-speaker-ceiling=x',
           'pagani-speaker-ceiling=0', 'pagani-speaker-feedback=1', 'pagani-gpu-async=1', 'pagani-battery=7', 'pagani-battery=5', 'pagani-battery=15', 'pagani-audio-tone-atten=0',
           'pagani-audio-tone-hz=2000')
    for word in bad:
        check(ar.forbidden([word]) == [word], word)
    for word in ('pagani-battery=1', 'pagani-battery-pmic=1', 'pagani-speaker-ceiling=12', 'pagani-speaker-ceiling=20', 'pagani-speaker-ceiling=0x10', 'pagani-speaker-feedback=0',
                 'pagani-gpu-async=0', 'pagani-audio-tone=1', 'pagani-audio-tone=2', '-v', 'pagani-wifi=2', 'pagani-sensors=1'):
        check(not ar.forbidden([word]), word)
    # 传感器、Wi-Fi 不和 ADSP 同轮
    check(ar.forbidden(['pagani-adsp=1', 'pagani-wifi=2']) == ['pagani-wifi=2'] and ar.forbidden(['pagani-adsp=1', 'pagani-sensors=3']) == ['pagani-sensors=3'])
    check(not ar.forbidden(['pagani-adsp=1', 'pagani-wifi=0']))
    print('通过  今天不能开的参数')


def layout_line(env, extra_args):
    """make-a830-layout.sh 和 payload.py 的规则算出的命令行：board.toml、make-a830-images.sh 的 loop-m8-phone、固定加的几项、A830_EXTRA_ARGS。"""
    text = (TOP / 'UniXNU-Pagani_SM8750-gpu/board.toml').read_text().replace('"pagani-panel=%s"' % env.get('A830_PANEL', '1'), '"pagani-panel=1"')
    board = tomllib.loads(text)['payload']['kernel_args']
    images = (TOP / '工具/make-a830-images.sh').read_text().replace('\\\n', ' ')
    entry = [line for line in images.splitlines() if line.startswith('image loop-m8-phone ')]
    check(len(entry) == 1)
    words = shlex.split(entry[0], comments=True)
    kargs = [words[i + 1] for i, word in enumerate(words) if word == '--kernel-arg']
    kargs = ['pagani-reset-after=%s' % env['A830_RESET_AFTER'] if a.startswith('pagani-reset-after=') else a for a in kargs]
    kargs += ['sheng-staged-apps=1', 'pagani-system-apps=1', 'pagani-audio=1']
    if env.get('A830_BATTERY') == '1':
        kargs.append('pagani-battery=1')
    kargs += extra_args.split()
    unique = [a for i, a in enumerate(kargs) if a.split('=')[0] not in [b.split('=')[0] for b in kargs[i + 1:]]]
    drop = env['A830_DROP_ARGS'].split()
    board = [a for a in board if a.split('=')[0] not in drop]
    board = ['pagani-panel=%s' % env['A830_PANEL'] if a == 'pagani-panel=1' else a for a in board]
    unique = [a for a in unique if a.split('=')[0] not in drop]
    names = {a.split('=')[0] for a in unique}
    return [a for a in board if a.split('=')[0] not in names] + unique


def stage16_base():
    return re.search(r'^BASE="(.*)"$', STAGE16.read_text(), re.M).group(1)


def test_baseline():
    """基线和阶段 1.6 同一份：BASELINE 是它的完整包的记录，这里从 board.toml、make-a830-images.sh、make-stage16.sh 重新算一遍。"""
    record = json.loads(ar.BASELINE.read_text())['kernel_args'].split()
    check(ar.BASELINE.name == 'boot-A830-阶段16-完整包.payload.json' and len(' '.join(record)) == 998, '阶段 1.6 完整包的记录')
    env = dict(A830_RESET_AFTER='5400', A830_PANEL='65', A830_DROP_ARGS='startup_debug', A830_BATTERY='1')
    line = layout_line(env, stage16_base() + ' sheng-log=1')
    check(sorted(line) == sorted(record), sorted(set(line) ^ set(record)))
    check('boot-A830-阶段16-完整包.img 65 5400' in STAGE16.read_text(), 'make-stage16.sh 的完整包')
    check('SEED=$F/s7-final' in STAGE16.read_text(), 'make-stage16.sh 的种子')
    payload = (TOP / 'UniXNU/scripts/unixnu/payload.py').read_text()
    check(int(re.search(r'^KERNEL_ARGS_LIMIT = (\d+)$', payload, re.M).group(1)) == ar.LIMIT)
    check(not any(w.startswith('pagani-battery=') for w in ar.baseline_args()) and len(' '.join(ar.baseline_args())) == 981)
    print('通过  基线就是 make-stage16.sh 那一份')


# 判据用到的原文都在驱动源码里：文档写于 10-31，之后驱动改过
SOURCES = {
    'PaganiAdsp/AdspCore.c': ('"start"', '"toc"', '"smem part"', '"fw part="', '"fw parsed"', '"fw mount failed"', '"smem touch"', '"survey"', '"smem oplus sensor items"', '"smp2p out"',
                              '"ipcc interrupt unavailable, polling"', '"qmp load_state adsp on"', '"pas loaded "', '"pas auth_and_reset"', '"ready wait"',
                              '"ipcc interrupt attached but silent, polling"', '"undo"', '" scm_parked=1"', '"kept_votes"', '"glink version agreed"', '"channel "',
                              '"handover: proxy votes dropped"', '"stopped as asked (pagani-adsp-stop)"', '"FAIL"', '"DSP fatal error while running; channels closed (no restart)"',
                              '"glink stream broken; link stopped"', '"glink: no version answer after 5 s"', '"glink opens nobody serves:"', 'pagani_text_kd(&t, "stop", stop)',
                              'pagani_text_kx(&t, "items", items)', 'pagani_text_kd(&t, "slave", c->status.slave)', 'pagani_text_ks(&t, "qmp"', 'pagani_text_kd(&t, "refused", c->refused)',
                              'pagani_text_kd(&t, "lun"', 'pagani_text_kd(&t, "first"', 'pagani_text_kd(&t, "irqs"', 'pagani_text_kd(&t, "shutdown_adsp"', 'pagani_text_kd(&t, "shutdown_dtb"',
                              'pagani_text_kd(&t, "unvote"', 'pagani_text_kd(&t, "load_state_off"', 'pagani_text_kx(&t, "slave", c->slave)', '"no ready"', 'kEntrySlave[] = "slave-kernel"'),
    'PaganiAdsp/AdspQrtr.c': ('"qrtr hello"', '"locator ask="', '"domains"', '"sent"', '"pd crash report "', '{"IPCRTR", kIntentSize'),
    'PaganiAdsp/PaganiAdsp.cpp': ('"[adsp] ipcc intid="', '"readback"', '"[adsp] qmp"', '"reused"', '"[adsp] rails on"', '"[adsp] rails off"', '"vlvl"', '"[adsp] scm "', '"rc", rc', '"intr"',
                                  '"us"', '"pagani-adsp"', '"pagani-adsp-stop"'),
    'PaganiSpeaker/SpeakerCore.c': ('"start"', '"feedback"', '"sids"', '"ceil_db"', '"ceil"', '"stop"', '"rail " SPEAKER_RAIL', '"mv"', '"bus"', '"clk"', '"amp"', '"rev"', '"type"', '"tfa9873"',
                                    '" config MISMATCH"', '" config ok"', '"absent"', '"init failed"', '"verify read failed"', '"dsp up"', '"apm"', 'pagani_ar_parse', '"smmu start"', '"smmu"',
                                    '"buffer"', '"pa"', '"dsp"', '"bytes"', '"graphs"', '"handle"', '"play start"', '"run"', '"amps"', '"state"', '"clks"', '"plls"', '"tone"', '"hz"', '"ms"',
                                    '"atten"', '"clks_failed"', '"ch"', '"play stop"', '"lim"', '"clamp"', '"werr"', '"sendfail"', '"stalls"', '"stopped as asked (pagani-speaker-stop)"', '"FAIL"',
                                    '"tone walk:', '"stall: the DSP stopped answering"', '"play lost: the DSP channel closed"', '"play refused"', '"the DSP never opened adsp_apps"',
                                    '"no TFA9873 answered"', '"stream 0x1001 not in pass-through"', '"period buffer not mapped (pagani-speaker-pa?)"'),
    'PaganiSpeaker/SpeakerBoard.h': ('#define SPEAKER_RAIL "ldob15"', '#define SPEAKER_RAIL_MV 1800u', '#define SPEAKER_BUFFER_PA 0xd0000000ull', '#define SPEAKER_BUFFER_BYTES 0x10000u',
                                     '#define SPEAKER_SIDS {0x1001u, 0x1081u, 0x1041u, 0x1061u}'),
    'PaganiSpeaker/Tfa9873.h': ('VDDS DCOCPOK OTDS OCDS UVDS MANALARM CLKS MTPB NOCLK', '#define TFA9873_CLKS (1u << 6)'),
    'PaganiSpeaker/Tfa9873.c': ('int tfa9873_known(uint16_t rev) {return (rev & 0xffu) == TFA9873_FAMILY;}', 'if (rev == 0x0a73)', 'if (rev == 0x0b73)'),
    'PaganiSpeaker/SpeakerLimit.h': ('#define SPEAKER_LIMIT_DEFAULT_DB 12u', 'the ceiling: 12 dB below full scale (8231)'),
    'PaganiSpeaker/PaganiSpeaker.cpp': ('"[spk] pagani-audio is not set:', '"[spk] backend adsp handed to the PaganiAudio service (st=12)"', '"pagani-speaker"', '"pagani-speaker-stop"',
                                        '"pagani-speaker-sids"', '"pagani-audio-tone"', '"pagani-audio-tone-atten"', '"pagani-audio-tone-hz"', '"pagani-audio"', 'gToneHz = 440', 'gToneMs = 500',
                                        'gToneAtten = 30', '"pagani-speaker-ceiling"', '"pagani-speaker-feedback"'),
    'PaganiBattery/PaganiBattery.cpp': ('"[battery] start"', '"adsp", gConfig.use_adsp', '"charge", gCore->cfg.charge', '"[battery] FAIL i2c"', '"[battery] i2c up"', '"st", gReport.stage',
                                        '"[battery] gauge"', '"addr", PGAUGE_ADDRESS', '"type", type', '"[battery] dsp channel PMIC_RTR_ADSP_APPS registered (5 x 1024)"', '"pagani-battery"',
                                        '"pagani-battery-log"', '"pagani-battery-charge"', '"pagani-battery-pmic"', '"[battery] i2c clock votes dropped by another driver, not back"'),
    'PaganiBattery/BatteryCore.c': ('"[battery] dsp channel up"', '"[battery] dsp set "', '"[battery] dsp"', '"cyc", b->cycles_done', '" layout="', '"[battery] state "', '"published"', '"pct"',
                                    '"mv"', '" src="', '"gauge"', '"+charger"', '"[battery] gauge stopped answering (3 passes)', 'pbat_text_kd(&t, "pass", g->passes)', '"ma"'),
    'PaganiBattery/BatteryGauge.h': ('PGAUGE_ADDRESS 0x55',),
    'PaganiBattery/PaganiBattery.h': (),
    'PaganiBattery/BatteryCore.h': ('PBAT_MODE_ON = 1u', 'PBAT_MODE_USB_PROBE = 2u', 'PBAT_MODE_PMIC_LOG = 4u', 'PBAT_MODE_PMIC_USE = 8u'),
    'Stage7Probe/Stage7Probe.cpp': ('if (enabled("pagani-adsp")) pagani_adsp_start();',),
}


def test_sources():
    """启动参数名和日志原文逐条对驱动源码。"""
    for name, literals in SOURCES.items():
        text = (DRIVERS / name).read_text()
        for literal in literals:
            check(literal in text, (name, literal))
    gpu = (TOP / 'Adreno830/Adreno830Board.h').read_text()
    check('"pagani-gpu-status"' in gpu and 'pagani-gpu-async' in gpu)
    wanted = {a.split('=')[0] for r in ar.ROUNDS.values() for a in r.args}
    text = ''.join((DRIVERS / name).read_text() for name in ('PaganiAdsp/PaganiAdsp.cpp', 'PaganiSpeaker/PaganiSpeaker.cpp', 'PaganiBattery/PaganiBattery.cpp'))
    for name in wanted:
        check('"%s"' % name in text, name)
    # IPCC 的中断号和读回：32 + SPI 229 = 261，0xa003 = 优先级 0xa0、使能、组 1
    check('#define PAGANI_ADSP_IPCC_SPI 229u' in (DRIVERS / 'PaganiAdsp/AdspCore.h').read_text())
    arm = (DRIVERS / 'PaganiArmCpu/ArmCpu.cpp').read_text()
    check('= 0xa0;' in arm and '(read32 (gDist, 0x80 + word) & bit) ? 1U : 0U' in arm and '(read32 (gDist, 0x100 + word) & bit) ? 2U : 0U' in arm)
    # 复位原因：timer 才是定时复位；observer 是定时器的 CPU 卡死
    ramoops, ufs = (DRIVERS / 'BootProgress/RamoopsLog.c').read_text(), (DRIVERS / 'BootProgress/UfsLog.c').read_text()
    check('reset_now("timer")' in ramoops and 'reset_now("observer")' in ramoops and '"%s"' % ar.LAST_SNAPSHOT in ufs)
    # gpu-loop.py 的默认等待和判定：改了这里要跟着看
    loop = (TOOLS / 'gpu-loop.py').read_text()
    check("'--android-timeout', type=int, default=900" in loop and r"the log\'s boot-args differ from the image\'s" in loop)
    print('通过  启动参数名和日志原文都在驱动源码里')


def test_round_a(work):
    for name in ('A1', 'A2', 'A3'):
        v = judge(work, name, good(name))
        check(v.status == 'PASS', (name, reasons(v)))
    a1 = good('A1')
    check(any('电量 100 %' in f for f in judge(work, 'A1', a1).facts))
    check(any('要人看的' in f and '状态栏' in f for f in judge(work, 'A1', a1).facts))       # 日志看不出的，判决里要写明没验证
    expect(work, 'A1', ((a1.replaced('i2c up st=5 err=0', 'FAIL i2c st=3 err=-110'), 'FAIL i2c'),
                        (a1.without('i2c up'), 'i2c up'),
                        (a1.replaced('type=0x4231', 'type=0x1111'), '电量计不对'),
                        (a1.replaced('gauge addr=0x55 rc=0', 'gauge addr=0x55 rc=-5'), '电量计不对'),
                        (a1.without('gauge addr'), '电量计不对'),
                        (a1.without('state published'), 'state published'),
                        (a1.plus('[battery] dsp set charge-limit op=0x10 prop=10 value=0x1e8480 rc=0 try=1'), 'dsp set'),
                        (a1.replaced('charge=0', 'charge=1'), 'charge=1'),
                        (a1.replaced('adsp=0', 'adsp=1'), 'adsp=1'),
                        (a1.without('[battery] start'), '[battery] start'),
                        (a1.plus('[spk] start amps=0x3'), '[spk]'),
                        (a1.plus('[adsp] start stop=0'), '[adsp]')))
    v = judge(work, 'A1', a1.replaced('i2c up st=5 err=0', 'FAIL i2c st=3 err=-110'))
    check(any('A2 起全部暂停' in a for a in v.next_step), v.next_step)
    expect(work, 'A1', ((a1.plus('[battery] gauge stopped answering (3 passes): the DSP\'s values or the last ones stay lost=1'), '电量计半路断了'),
                        (a1.plus('[battery] i2c clock votes dropped by another driver, not back n=1 vote=0x1 rc=-16'), '电量计半路断了'),
                        (a1.replaced('pct=100', 'pct=80'), '差得太多')), 'ASK')
    # 差 5 % 以内：UI 电量滞后，不算
    check(judge(work, 'A1', a1.replaced('pct=100', 'pct=95')).status == 'PASS')
    # 回到安卓：dumpsys battery
    v = judge(work, 'A1', a1, device=DEVICE_OK.replace('status: 2', 'status: 4'))
    check(v.status == 'ASK' and '没在充电' in reasons(v), reasons(v))
    check(judge(work, 'A1', a1, device=DEVICE_OK.replace('status: 2', 'status: 5')).status == 'PASS')
    # adb 走着 USB，安卓却说没有任何供电：充电被弄坏了
    v = judge(work, 'A1', a1, device=DEVICE_OK.replace('USB powered: true', 'USB powered: false').replace('status: 2', 'status: 3'))
    check(v.status == 'ASK' and '没有任何供电' in reasons(v), reasons(v))
    check(judge(work, 'A1', a1, device=DEVICE_OK.replace('USB powered: true', 'USB powered: false').replace('AC powered: false', 'AC powered: true')).status == 'PASS')
    v = judge(work, 'A1', a1, device='')
    check(v.status == 'PASS' and any('device.txt' in f for f in v.facts), v.facts)
    check(any('HARD_RESET/PS_HOLD' in f for f in judge(work, 'A1', a1).facts))
    for reset in ('Reset Trigger: PMIC_WD', 'Reset Trigger: KPDPWR_N'):
        v = judge(work, 'A1', a1, device=DEVICE_OK.replace('Reset Trigger: PS_HOLD', reset))
        check(v.status == 'ASK' and '不是定时复位' in reasons(v), reasons(v))
    check(judge(work, 'A1', a1, device=DEVICE_OK.replace('Reset Type: HARD_RESET', 'Reset Type: SHUTDOWN')).status == 'ASK')

    a2 = good('A2')
    expect(work, 'A2', ((a2.replaced('[spk] amp 0x35 rev=0xb73 type=tfa9873 table=10', '[spk] amp 0x35 absent rc=-5 step=0 geni=2 irq=0x401'), 'absent'),
                        (a2.replaced('type=tfa9873 table=10', 'type=unknown table=0'), 'tfa9873'),
                        (a2.replaced('rev=0xb73', 'rev=0xb72'), '0x..73'),
                        (a2.replaced('[spk] amp 0x35 rev=0xb73 type=tfa9873 table=10', '[spk] amp 0x35 rev=0xc73 type=tfa9873 table=0'), 'table=0'),
                        (a2.without('amp 0x35 rev'), '0x35 rev='),
                        (a2.without('rail ldob15'), 'rail ldob15'),
                        (a2.plus('[spk] FAIL st=-3 err=-110 i2c hub clocks'), 'FAIL st=-3'),
                        (a2.replaced('bus clk=0', 'bus clk=-110'), '总线'),
                        (a2.replaced('set=1 abort=0 rc=0', 'set=1 abort=0 rc=-5'), '总线'),
                        (a2.without('stopped as asked'), 'stopped as asked'),
                        (a2.replaced('stopped as asked (pagani-speaker-stop) st=4', 'stopped as asked (pagani-speaker-stop) st=3'), 'st=4'),
                        (a2.replaced('stop=4 ceil_db', 'stop=5 ceil_db'), 'stop=5'),
                        (a2.without('[spk] start'), '[spk] start'),
                        (a2.plus('[battery] start gauge=1'), '[battery]')))
    v = judge(work, 'A2', a2.replaced('[spk] amp 0x35 rev=0xb73 type=tfa9873 table=10', '[spk] amp 0x35 absent rc=-5 step=0 geni=2 irq=0x401'))
    check(any('rail' in a for a in v.next_step), v.next_step)
    # 电源投票失败不致命：功放应答才是判据，要人看
    expect(work, 'A2', ((a2.replaced('mv=1800 rc=0', 'mv=1800 rc=-16'), 'L15B'),), 'ASK')
    # 安全参数
    expect(work, 'A2', ((a2.replaced('feedback=0', 'feedback=1'), 'feedback=1'), (a2.replaced('ceil_db=-12', 'ceil_db=-6'), '天花板'), (a2.replaced('ceil=8231', 'ceil=16422'), '天花板')))
    a3 = good('A3')
    mismatch = '[spk] amp 0x34 config MISMATCH transfers=89 bits=32 slot=0 tdm=0x28037bf1 en=0x7923 slots=0x7c267d21 reg=0x26 got=0x7c26 want=0x10'
    expect(work, 'A3', ((a3.replaced('[spk] amp 0x34 config ok transfers=89 bits=32 slot=0 tdm=0x28037bf1 en=0x7923 slots=0xc107d21', mismatch), 'reg=0x26 got=0x7c26 want=0x10'),
                        (a3.without('amp 0x35 config ok'), '0x35 config ok'),   # 驱动一颗失败也走得下去，判据要两颗
                        (a3.plus('[spk] amp 0x35 init failed rc=-5 step=7 geni=2 irq=0x401'), 'init failed'),
                        (a3.plus('[spk] amp 0x35 verify read failed rc=-5 step=7 geni=2 irq=0x401'), 'verify read failed')))
    print('通过  A1 到 A3')


def test_round_b(work):
    for name in ('B1', 'B2', 'B3', 'B4'):
        v = judge(work, name, good(name))
        check(v.status == 'PASS', (name, reasons(v)))
    # 走到哪一步该有哪一行：抽掉一行就是断在这里
    for name, stage in (('B1', 3), ('B2', 5), ('B3', 7), ('B4', 13)):
        for s, prefix in ar.ADSP_WALK:
            if s <= stage:
                v = judge(work, name, good(name).without(prefix.split('] ')[1].rstrip()) if prefix != '[adsp] pas loaded adsp ' else good(name).without('pas loaded adsp pas'))
                check(v.status == 'HARD', (name, prefix, v.status))
    b1 = good('B1')
    expect(work, 'B1', ((b1.plus('[adsp] FAIL st=-1 err=-1 no SMEM partition table'), 'no SMEM partition table'),
                        (b1.plus('[adsp] FAIL st=-3 err=-114 DSP already running'), 'DSP already running'),
                        (b1.without('stopped as asked'), 'stopped as asked'),
                        (b1.replaced('(pagani-adsp-stop) st=3', '(pagani-adsp-stop) st=2'), 'st=3'),
                        (b1.replaced('start stop=3', 'start stop=0'), 'stop=0'),
                        (b1.without('[adsp] start'), '[adsp] start')))
    expect(work, 'B1', ((b1.replaced('part=modem_a lun=4', 'part=modem_b lun=4'), 'modem_a'), (b1.replaced('first=4870', 'first=9999'), 'first=4870'),
                        (b1.replaced('lun=4', 'lun=3'), 'lun=4'), (b1.replaced('items=0x0', 'items=0x1'), 'items=0x0'), (b1.replaced('slave=-1', 'slave=0'), 'slave=-1'),
                        (b1.replaced('qmp=LIAM', 'qmp=?'), 'LIAM'), (b1.replaced('refused=0', 'refused=2'), 'refused=0')), 'ASK')
    v = judge(work, 'B1', b1.plus('[adsp] fw mount failed part=modem_a rc=-3 fat=0', '[adsp] FAIL st=-2 err=-3 firmware partition'))
    check(v.status == 'HARD' and any('modem_b' in a for a in v.next_step), v.next_step)
    # 最后一行是 smem touch 后 panic：ADSP 路线到此为止
    panic = Log(*([item for item in b1.lines if 'survey' not in item[2] and 'stopped' not in item[2] and item[0] < 5] + [(3.5, 'P', 'OnePlus 13T / XNU PANIC')]))
    v = judge(work, 'B1', panic, 'UFS log at LBA 124178176: state 5 (panic flush), up 3 s, 4 rounds, 4 slots, 0 write errors')
    check(v.status == 'HARD' and 'panic' in reasons(v) and any('smem touch' in a for a in v.next_step), v)

    b2 = good('B2')
    v = judge(work, 'B2', b2)
    check(v.status == 'PASS' and any('130=0 128=0' in f for f in v.facts), v.facts)      # 文档没写的 SMEM 全局分区写入，判决里要留下
    # 中断接不上能继续，记下来
    v = judge(work, 'B2', b2.replaced('attach=0x0', 'attach=0xffffffffe00002d5').plus('[adsp] ipcc interrupt unavailable, polling rc=-19'))
    check(v.status == 'PASS' and any('记下' in f and 'polling' in f for f in v.facts), v.facts)
    expect(work, 'B2', ((b2.replaced('readback=0xa003', 'readback=0xa002'), '0xa003'),), 'ASK')

    b3 = good('B3')
    check(any('要人看的' in f and '摸一下屏幕' in f for f in judge(work, 'B3', b3).facts))       # 要用户在场的轮次也写明日志判不了什么
    expect(work, 'B3', ((b3.plus('[adsp] FAIL st=-6 err=-16 AOSS load_state on'), 'AOSS load_state on'),
                        (b3.plus('[adsp] FAIL st=-7 err=-16 lcx/lmx proxy votes'), 'lcx/lmx'),
                        (b3.replaced('[adsp] rails off rc=0', '[adsp] rails off rc=-5'), 'rails off'),
                        (b3.without('rails off'), 'rails off'),
                        (b3.without('[adsp] undo'), 'undo'),
                        (b3.replaced('unvote=0', 'unvote=-5'), 'undo'),
                        (b3.replaced('load_state_off=0', 'load_state_off=-110'), 'undo'),
                        (b3.replaced('load_state_off=0', 'load_state_off=0 kept_votes=1'), 'undo'),
                        (b3.replaced('wk 35/35', 'wk 20/35'), '20/35')))
    v = judge(work, 'B3', b3.plus('[adsp] FAIL st=-7 err=-16 lcx/lmx proxy votes'))
    check(any('锁字被占' in a for a in v.next_step), v.next_step)
    expect(work, 'B3', ((b3.replaced('reused=1', 'reused=0'), 'reused=1'), (b3.replaced('vlvl=384', 'vlvl=256'), 'vlvl=384'),
                        (b3.replaced('gpu 35 cpu 0', 'gpu 0 cpu 35'), 'GPU 出帧'), (b3.without('FPS 35'), 'GPU 出帧')), 'ASK')
    check(judge(work, 'B2', b2.without('FPS 35')).status == 'PASS')         # B2 还没投票
    # 帧全在投票之前：证明不了投票之后 GPU 照常（固件分区重试时投票可以很晚）
    late = Log(*[(70.0 if 'rails on' in line else t, k, line) for t, k, line in b3.lines])
    v = judge(work, 'B3', late)
    check(v.status == 'ASK' and '投票（70 s）之后' in reasons(v), reasons(v))

    b4 = good('B4')
    v = judge(work, 'B4', b4)
    check(any('auth_and_reset 900000 us' in f for f in v.facts) and any('nobody serves' in f for f in v.facts), v.facts)
    # B4 走的是全程（stop=0）：stop 轮次看的值它一样要看
    expect(work, 'B4', ((b4.replaced('reused=1', 'reused=0'), 'reused=1'), (b4.replaced('vlvl=384', 'vlvl=256'), 'vlvl=384'),
                        (b4.replaced('gpu 35 cpu 0', 'gpu 0 cpu 35'), 'GPU 出帧'), (b4.replaced('readback=0xa003', 'readback=0xa002'), '0xa003')), 'ASK')
    v = judge(work, 'B4', b4.replaced('attach=0x0', 'attach=0xffffffffe00002d5').plus('[adsp] ipcc interrupt unavailable, polling rc=-19'))
    check(v.status == 'PASS' and any('记下' in f and 'polling' in f for f in v.facts), v.facts)
    # B4 怎么读
    for line, word in (('[adsp] FAIL st=-8 err=-1002 init_image', 'PAS 被拒'), ('[adsp] FAIL st=-9 err=-1003 mem_setup', 'PAS 被拒'), ('[adsp] FAIL st=-10 err=-1006 adsp auth_and_reset', 'PAS 被拒'),
                       ('[adsp] FAIL st=-11 err=-60 no ready', 'SoCCP')):
        v = judge(work, 'B4', b4.plus(line, '[adsp] undo shutdown_adsp=0 shutdown_dtb=0 unvote=0 load_state_off=0'))
        check(v.status == 'HARD' and line in reasons(v) and any(word in a for a in v.next_step) and any('undo' in f for f in v.facts), (line, v))
    parked = '[adsp] undo shutdown_adsp=0 shutdown_dtb=0 unvote=0 load_state_off=0 kept_votes=1 scm_parked=1'
    v = judge(work, 'B4', b4.plus(parked))
    check(v.status == 'HARD' and 'scm_parked' in reasons(v) and any('不要再试任何安全调用' in a for a in v.next_step), v)
    v = judge(work, 'B4', b4.plus('[adsp] FAIL st=-10 err=-1005 adsp auth_and_reset', parked))
    check(v.status == 'HARD' and any('不要再试任何安全调用' in a for a in v.next_step), v)
    expect(work, 'B4', ((b4.replaced(SCM % ('auth_and_reset', '0x1', 12, 900000), SCM.replace('rc=0', 'rc=-1006') % ('auth_and_reset', '0x1', 12, 900000)), '安全调用失败'),
                        (b4.without('scm auth_and_reset pas=0x24'), 'pas=0x24'),
                        (b4.without('scm auth_and_reset pas=0x1'), 'pas=0x1'),
                        (b4.replaced('ready wait ms=430 slave=0x2', 'ready wait ms=430 slave=0x4'), 'ready 位'),
                        (b4.plus('[adsp] DSP fatal error while running; channels closed (no restart)'), '崩溃'),
                        (b4.plus('[adsp] glink stream broken; link stopped in=7'), 'GLINK'),
                        (b4.plus('[adsp] glink: no version answer after 5 s rx_fifo=-1 in=0 out=1'), 'GLINK'),
                        (b4.plus('[battery] start gauge=1 adsp=1 charge=0'), '[battery]'),
                        (b4.plus('[spk] start amps=0x3'), '[spk]'),
                        (Log(*(b4.lines + [(90.0, 'S', 'pid=99 SpringBoard exec process signature enter')])), '启动了 2 次'),
                        (Log(*(b4.lines + [(90.0, 'S', 'pid=98 backboardd exec process signature enter')])), '启动了 2 次')))
    # exec 走控制台环，开机时第一次的记录会丢：只剩一条，但它在桌面起来之后，这就是重启
    v = judge(work, 'B4', Log(*(b4.without('SpringBoard exec').lines + [(90.0, 'S', 'pid=99 SpringBoard exec process signature enter')])))
    check(v.status == 'HARD' and '启动了 1 次' in reasons(v), reasons(v))
    check(judge(work, 'B4', b4.without('SpringBoard exec')).status == 'PASS')       # 第一次的记录丢了不算事
    v = judge(work, 'B4', Log(*(b4.lines + [(20.0, 'S', 'pid=31 SpringBoard exec process signature enter')])))     # 桌面起来之前就重启过
    check(v.status == 'HARD' and '启动了 2 次' in reasons(v), reasons(v))
    # 被杀的进不了 FATAL EXITS，但有 exit 行
    v = judge(work, 'B4', Log(*(b4.lines + [(120.0, 'S', 'exit pid=27 SpringBoard status=9 ns=2 code=9')])))
    check(v.status == 'HARD' and '退出了' in reasons(v), reasons(v))
    v = judge(work, 'B4', Log(*(b4.lines + [(120.0, 'S', 'exit pid=26 backboardd status=9 ns=2 code=9')])))
    check(v.status == 'HARD' and 'backboardd 退出了' in reasons(v), reasons(v))
    check(judge(work, 'B4', Log(*(b4.lines + [(120.0, 'S', 'exit pid=375 Maps status=6 ns=2 code=6')]))).status == 'PASS')
    v = judge(work, 'B4', b4, UFS_OK % 238 + '\n\nFATAL EXITS (S exit records)\n  SpringBoard      status=11  SIGSEGV  x1   113.0 .. 113.0 s\n')
    check(v.status == 'HARD' and '致命退出' in reasons(v), reasons(v))
    check(judge(work, 'B4', b4, UFS_OK % 238 + '\n\nFATAL EXITS\n  Maps             status=6   SIGABRT  x1\n').status == 'PASS')
    expect(work, 'B4', ((b4.without('handover: proxy votes dropped'), 'handover'),
                        (b4.replaced('proxy votes dropped rc=0', 'proxy votes dropped rc=-5'), 'handover'),
                        (b4.without('locator ask=avs/audio'), 'locator'),
                        (b4.replaced('ask=avs/audio domains=1 sent=1', 'ask=avs/audio domains=1 sent=0'), 'locator'),
                        (b4.plus('[adsp] pd crash report msm/adsp/sensor_pd reason=fatal sent=1 from=5:32'), 'pd crash report')), 'ASK')
    # 记下来，不停
    v = judge(work, 'B4', b4.plus('[adsp] ipcc interrupt attached but silent, polling'))
    check(v.status == 'PASS' and any('中断线不通' in f for f in v.facts), v.facts)
    v = judge(work, 'B4', b4.replaced(SCM % ('auth_and_reset', '0x1', 12, 900000), SCM % ('auth_and_reset', '0x1', 0, 900000)))
    check(v.status == 'PASS' and any('不让出 CPU' in f for f in v.facts), v.facts)
    check(not any('不让出 CPU' in f for f in judge(work, 'B4', b4).facts))
    print('通过  B1 到 B4')


def test_round_c(work):
    for name in ('C1', 'C2', 'C3', 'C4', 'C5'):
        v = judge(work, name, good(name))
        check(v.status == 'PASS', (name, reasons(v)))
    c1 = good('C1')
    v = judge(work, 'C1', c1)
    check(any('layout=oplus usb=1' in f for f in v.facts) and any('0 mA -> 800 mA' in f for f in v.facts), v.facts)
    expect(work, 'C1', ((c1.plus('[battery] dsp set charge-limit op=0x10 prop=10 value=0x1e8480 rc=0 try=1'), '违反只读'),
                        (c1.without('registered'), 'registered'),
                        (c1.replaced('[battery] dsp channel PMIC_RTR_ADSP_APPS registered (5 x 1024)', '[battery] dsp channel not registered (PaganiAdsp absent or off): the gauge only').without('dsp channel up'), 'not registered'),
                        (c1.without('[battery] dsp channel up'), 'channel up'),
                        (c1.without('dsp cyc=2'), 'cyc=2'),
                        (c1.replaced('[battery] start gauge=1 adsp=1', '[battery] start gauge=1 adsp=0'), 'adsp=0'),
                        (c1.without('[adsp] glink version'), 'glink version')))     # B4 的判据 C 段都还在
    expect(work, 'C1', ((c1.replaced('layout=oplus', 'layout=oplus?'), 'layout=oplus?'), (c1.replaced('layout=oplus', 'layout=qti'), 'layout=qti'),
                        (c1.replaced('src=gauge+charger', 'src=gauge+inferred'), 'src=gauge+charger')), 'ASK')

    c2 = good('C2')
    expect(work, 'C2', ((c2.without('[spk] dsp up').without('apm state').without('stopped as asked').plus('[spk] FAIL st=-6 err=-60 the DSP never opened adsp_apps op=0x0 dsp=0x0'), 'never opened'),
                        (c2.plus('[spk] FAIL st=-6 err=-32 PaganiAdsp failed (see its [adsp] lines) op=0x0 dsp=0xfffffff8'), 'PaganiAdsp failed'),
                        (c2.plus('[spk] FAIL st=-7 err=-60 the audio framework never became ready op=0x1001021 dsp=0x0'), 'never became ready'),
                        (c2.without('apm state=ready'), 'apm state=ready'),
                        (c2.replaced('apm state=ready', 'apm state=not-ready'), 'apm state=ready'),
                        (c2.without('[spk] dsp up'), 'dsp up')))
    v = judge(work, 'C2', c2.plus('[spk] FAIL st=-6 err=-60 the DSP never opened adsp_apps op=0x0 dsp=0x0'))
    check(any('audio PD' in a for a in v.next_step), v.next_step)
    for s, prefix in ar.SPEAKER_WALK:
        if s <= 7:
            check(judge(work, 'C2', good('C2').without(prefix.split('] ')[1].rstrip())).status == 'HARD', prefix)

    c3 = good('C3')
    check(judge(work, 'C3', c3).status == 'PASS')
    expect(work, 'C3', ((c3.replaced('sids=0x1 ', 'sids=0xf ').replaced('smmu start sids=0x1', 'smmu start sids=0xf'), 'sids=0xf'),
                        (c3.replaced('smmu 0x1001=ok/0x7f', 'smmu 0x1001=ok/0x7f 0x1081=ok/0x17f'), '0x1001'),
                        (c3.replaced('smmu 0x1001=ok/0x7f', 'smmu 0x1001=-1/0x7f'), '没进直通'),
                        (c3.without('smmu start'), 'smmu start'),
                        (c3.without('[spk] smmu 0x1001'), 'smmu 0x1001'),
                        (c3.replaced('(pagani-speaker-stop) st=8', '(pagani-speaker-stop) st=7'), 'st=8')))
    v = judge(work, 'C3', c3.replaced('smmu 0x1001=ok/0x7f', 'smmu 0x1001=-1/0xff000000').without('stopped as asked').plus('[spk] FAIL st=-8 err=-1 stream 0x1001 not in pass-through op=0x1001021 dsp=0x1'))
    check(v.status == 'HARD' and 'not in pass-through' in reasons(v) and any('没有写任何东西' in a for a in v.next_step), v)
    # 整机复位：smmu start 那一行落不了盘时最后一行更早，结论一样
    summary = 'UFS log at LBA 124178176: boot_id 0x1, ramoops run 0x2, state 2 (writing), up 9 s, 3 rounds, 3 slots, 0 write errors'
    for cut in ('[spk] smmu start sids=0x1', '[spk] apm state=ready', '[spk] dsp up', '[spk] amp 0x35 config ok'):
        v = judge(work, 'C3', c3.until(next(t for t, k, line in c3.lines if line.startswith(cut)) + 0.001), summary)
        check(v.status == 'HARD' and '整机复位或挂死' in reasons(v) and any('不要再试别的银行' in a for a in v.next_step), (cut, v))
        check(any('最后一条 [spk] 行' in f for f in v.facts), v.facts)
    # 过了 SMMU 的复位不怪它
    v = judge(work, 'C3', c3.until(next(t for t, k, line in c3.lines if line.startswith('[spk] stopped')) + 0.001), summary)
    check(v.status == 'HARD' and not any('银行' in a for a in v.next_step), v.next_step)
    # panic 不算整机复位
    v = judge(work, 'C3', Log(*(c3.until(6.05).lines + [(6.2, 'P', 'XNU PANIC')])), summary)
    check(v.status == 'HARD' and not any('银行' in a for a in v.next_step), v.next_step)

    c4 = good('C4')
    v = judge(work, 'C4', c4.replaced('0x1081=ok/0x17f', '0x1081=-16/0x17f'))
    check(v.status == 'PASS' and any('记下' in f and '0x1081=-16' in f for f in v.facts), v.facts)
    v = judge(work, 'C4', c4.replaced('0x1001=ok/0x7f', '0x1001=-16/0x7f').without('stopped as asked').plus('[spk] FAIL st=-8 err=-16 stream 0x1001 not in pass-through op=0x1001021 dsp=0x1'))
    check(v.status == 'HARD', v)
    expect(work, 'C4', ((c4.replaced(' 0x1061=ok/0x37f', ''), '0x1061'),))

    c5 = good('C5')
    expect(work, 'C5', ((c5.without('[spk] buffer'), 'buffer'), (c5.without('[spk] graphs'), 'graphs'),
                        (c5.replaced('handle=0x77', 'handle=0x0'), 'handle'),
                        (c5.plus('[spk] FAIL st=-10 err=-71 map buffer op=0x10001001 dsp=0x3'), 'map buffer'),
                        (c5.plus('[spk] FAIL st=-9 err=-12 period buffer not mapped (pagani-speaker-pa?) op=0x1001021 dsp=0x1'), 'period buffer')))
    expect(work, 'C5', ((c5.replaced('pa=0xd0000000 dsp=0x1d0000000', 'pa=0xd0010000 dsp=0x1d0010000'), 'buffer'),), 'ASK')
    v = judge(work, 'C5', c5.plus('[spk] FAIL st=-9 err=-12 period buffer not mapped (pagani-speaker-pa?) op=0x1001021 dsp=0x1'))
    check(any('pagani-speaker-pa=' in a for a in v.next_step), v.next_step)
    # 清缓冲区时同步异常：panic，最后一行是 smmu
    crash = Log(*([item for item in c5.lines if item[0] < 6.13 and 'buffer' not in item[2] and 'graphs' not in item[2]] + [(6.2, 'P', 'XNU PANIC: synchronous external abort')]))
    v = judge(work, 'C5', crash, summary)
    check(v.status == 'HARD' and any('pagani-speaker-pa=' in a for a in v.next_step), v)
    print('通过  C1 到 C5')


def test_tones(work):
    """C6、C7：日志全过也只是存疑：听到没有要人答。"""
    for name in ('C6', 'C7'):
        v = judge(work, name, good(name))
        check(v.status == 'ASK' and '声音要人答' in reasons(v) and '叫用户来听' in reasons(v), (name, v))
        check(any('pagani-speaker-sd=2' in a for a in v.next_step), v.next_step)
    c6, c7 = good('C6'), good('C7')
    expect(work, 'C6', ((c6.replaced('tone rc=0', 'tone rc=-60'), 'tone rc=-60'),
                        (c6.replaced('atten=30', 'atten=0'), '-30 dBFS'),
                        (c6.replaced('hz=440', 'hz=2000'), '-30 dBFS'),
                        (c6.replaced('tone rc=0 hz=440 ms=500', 'tone rc=0 hz=440 ms=9000'), '-30 dBFS'),
                        (c6.replaced('out=1035 ceil=8231', 'out=1035 ceil=16422'), '-30 dBFS'),
                        (c6.replaced('lim=0', 'lim=3'), 'lim=0 clamp=0'),
                        (c6.replaced('clamp=0', 'clamp=1'), 'lim=0 clamp=0'),
                        (c6.without('[spk] tone rc='), '没有 tone 行'),
                        (c6.plus('[spk] backend adsp handed to the PaganiAudio service (st=12)'), 'iOS'),
                        (c6.plus('[spk] stall: the DSP stopped answering run=1 sent=4 played=0 inflight=4 dropped=0'), 'stall'),
                        (c6.plus('[spk] play lost: the DSP channel closed run=1 played=61'), 'play lost'),
                        (c6.plus('[spk] play refused run=1 rc=-71 op=0x1001001 dsp=0x3'), 'play refused'),
                        (c6.without('graphs'), 'graphs')))
    for log, word in ((c6.replaced('clks_failed=0', 'clks_failed=1'), 'clks_failed'),
                      (c6.replaced('amps=0x3 clks_failed', 'amps=0x1 clks_failed'), 'amps=0x3'),
                      (c6.replaced('play start run=1 amps=0x3', 'play start run=1 amps=0x1'), 'play start'),
                      (c6.without('[spk] play start'), 'play start'),
                      (c6.without('[spk] play stop'), 'play stop'),
                      (c6.replaced('werr=0', 'werr=2'), 'werr=0'),
                      (c6.replaced('[spk] amp 0x34 state=9', '[spk] amp 0x34 state=4'), 'state=9'),
                      (c6.replaced('[spk] amp 0x35 state=9 clks=1', '[spk] amp 0x35 state=9 clks=0'), 'state=9'),
                      (c6.replaced('plls=1', 'plls=0'), 'state=9'),
                      (c6.without('[spk] amp 0x35 state'), '0x35 state='),
                      (c6.without('pagani-audio is not set'), 'pagani-audio is not set')):
        v = judge(work, 'C6', log)
        check(v.status == 'ASK' and word in reasons(v) and '声音要人答' not in reasons(v), (word, v))
    v = judge(work, 'C6', c6.replaced('[spk] amp 0x35 state=9 clks=1', '[spk] amp 0x35 state=9 clks=0'))
    check(any('pagani-speaker-prm-port=7' in a for a in v.next_step), v.next_step)
    # 功放自己报过流：日志其余全过也不能说“日志全过”
    for bit in (0x4, 0x8, 0x10, 0x20):
        v = judge(work, 'C6', c6.replaced('[spk] amp 0x35 state=9 clks=1 plls=1 st=0x40', '[spk] amp 0x35 state=9 clks=1 plls=1 st=%#x' % (0x40 | bit)))
        check(v.status == 'ASK' and '功放 0x35 报警' in reasons(v) and '声音要人答' not in reasons(v), v)
    v = judge(work, 'C6', c6.replaced('st=0x40', 'st=0x41'))        # VDDS 是上电的粘滞位，不是报警
    check(v.status == 'ASK' and '报警' not in reasons(v) and '声音要人答' in reasons(v), v)
    v = judge(work, 'D1', good('D1').plus('[spk] amp 0x34 state=9 clks=1 plls=1 st=0x48 st2=0x40 st3=0x0 man=0x48 irq=0x0 bat=291 temp=33'))
    check(v.status == 'ASK' and '功放 0x34 报警' in reasons(v), v)
    expect(work, 'C7', ((c7.without('tone walk'), 'tone walk'),
                        (c7.replaced('ch=0x2', 'ch=0x1'), '0x3 0x1 0x2'),
                        (Log(*[item for item in c7.lines if 'ch=0x2' not in item[2]]), '0x3 0x1'),
                        (c7.plus('[spk] tone rc=0 hz=440 ms=500 atten=30 gain=1036 periods=50 sent=1 played=1 amps=0x3 clks_failed=0 ch=0x3 out=1035 ceil=8231'), '声道')))
    # D 段：要 pagani-audio=1，后端交给 iOS，等人听
    for name in ('D1', 'D2'):
        v = judge(work, name, good(name))
        check(v.status == 'ASK' and 'audio2' in reasons(v), (name, v))
        expect(work, name, ((good(name).without('backend adsp handed'), 'PaganiAudio'),
                            (good(name).plus('[spk] play stop run=1 sent=1 played=1 werr=0 sendfail=0 stale=0 stalls=0 ms=1 lim=4 gmin=500 in=32767 out=8231 clamp=3'), 'clamp=0')))
    v = judge(work, 'D1', good('D1').plus('[spk] play stop run=1 sent=1 played=1 werr=0 sendfail=0 stale=0 stalls=0 ms=1 lim=4 gmin=500 in=32767 out=8231 clamp=0'))
    check(v.status == 'ASK' and any('lim=4' in f for f in v.facts), v)
    print('通过  C6、C7、D 段：声音要人答')


def test_health(work):
    a1 = good('A1')
    for summary, word in (((UFS_OK % 88).replace('0 write errors', '3 write errors'), '写错误'),
                          ((UFS_OK % 88).replace('state 6 (last snapshot before a reset)', 'state 5 (panic flush)'), '状态 5'),
                          ((UFS_OK % 88).replace('state 6 (last snapshot before a reset)', 'state 2 (writing)'), '状态 2'),
                          ('no such line', 'UFS 日志头')):
        v = judge(work, 'A1', a1, summary)
        check(v.status == 'HARD' and word in reasons(v), (word, reasons(v)))
    v = judge(work, 'A1', Log())
    check(v.status == 'HARD' and reasons(v) == '没有日志（merged.log 空或不存在）', reasons(v))
    v = judge(work, 'A1', Log(*(a1.lines + [(50.0, 'P', 'OnePlus 13T / XNU PANIC')])))
    check(v.status == 'HARD' and reasons(v).startswith('panic'), reasons(v))
    v = judge(work, 'A1', a1.until(60))
    check(v.status == 'HARD' and '早于 90 s' in reasons(v) and any('[battery] 行' in f for f in v.facts), v)
    check(judge(work, 'A1', Log(*(a1.until(50).lines + [(76.0, 'T', ar.LAST_SNAPSHOT + 'timer')]))).status == 'PASS')   # 差 15 s 以内算到了
    # 状态 6 只说写了快照：离定时复位不到 15 s 时，是谁要复位的只有这一行知道
    for reason in ('observer', 'backup timer', 'reboot', 'wdog'):
        v = judge(work, 'A1', a1.replaced(ar.LAST_SNAPSHOT + 'timer', ar.LAST_SNAPSHOT + reason))
        check(v.status == 'HARD' and '不是定时复位' in reasons(v) and reason in reasons(v), reasons(v))
    v = judge(work, 'A1', a1.without(ar.LAST_SNAPSHOT))
    check(v.status == 'HARD' and '没有记录' in reasons(v), reasons(v))
    expect(work, 'A1', ((a1.replaced('wk 35/35', 'wk 20/35'), '20/35'), (a1.without('diag: r1 sum'), 'diag'), (a1.replaced('io rd 300', 'io rd 0'), '根卷'),
                        (a1.replaced(' pagani-battery=1', ''), 'pagani-battery=1')))      # 日志的启动参数没有这一轮的参数：别的运行的日志
    expect(work, 'C3', ((good('C3').replaced(' pagani-speaker-sids=1', ''), 'pagani-speaker-sids=1'),))      # 每一个参数都要看，不只第一个
    v = judge(work, 'A1', Log(*[item for item in a1.lines if item[1] != 'A']))
    check(v.status == 'HARD' and '启动参数记录' in reasons(v), reasons(v))
    # 启动参数里混进今天不能开的：镜像出错了
    for word in ('pagani-battery-charge=1', 'pagani-speaker-feedback=1', 'pagani-speaker-ceiling=6', 'pagani-gpu-async=1'):
        v = judge(work, 'A1', a1.replaced('sheng-log=1', 'sheng-log=1 ' + word))
        check(v.status == 'HARD' and '不能开' in reasons(v) and word in reasons(v), reasons(v))
    print('通过  每轮都要满足的')


def test_real_runs():
    """真实目录：一个是干净的 420 s 运行（有 [battery] 行，没有一行 [adsp]），一个 4.8 s 就 panic，一个没有记录。"""
    runs = TOOLS / 'runs'
    good_run, crash, empty = runs / '20261101-110201-stage15-smoke-1', runs / '20261101-053955-stage15-smoke-1', runs / '20261031-174545-a830-full-2'
    if not good_run.is_dir() or not crash.is_dir():
        print('（没有保存的运行目录：真实日志没有回放）')
        return
    e = ar.Evidence.read(good_run)
    check(len(e.records) == 11396 and not [t for t in e.texts() if t.startswith('[adsp] ')], '这次运行没有一行 [adsp]')
    # 正例：这次运行的 [battery] 行就是 A1 要看的，是真机打印的
    v = ar.judge(e, ar.ROUNDS['A1'])
    check(v.status == 'PASS' and any('wk 35/35' in f for f in v.facts) and any('电量 100 %' in f for f in v.facts), (v.status, reasons(v)))
    check(any('HARD_RESET/PS_HOLD' in f for f in v.facts) and any('status 2' in f for f in v.facts), v.facts)
    # 反例：没有 [adsp] 行的日志过不了任何一轮 ADSP
    for name in ('B1', 'B2', 'B3', 'B4', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'D1', 'D2'):
        v = ar.judge(e, ar.ROUNDS[name])
        check(v.status == 'HARD' and '没有 [adsp] start' in reasons(v) and 'pagani-adsp=1' in reasons(v), (name, reasons(v)))
    v = ar.judge(e, ar.ROUNDS['B4'])
    check('[battery]' in reasons(v), reasons(v))                            # B4 不该有 [battery] 行
    for name in ('A2', 'A3'):
        v = ar.judge(e, ar.ROUNDS[name])
        check(v.status == 'HARD' and '没有 [spk] start' in reasons(v), reasons(v))
    v = ar.judge(ar.Evidence.read(crash), ar.ROUNDS['A1'])
    check(v.status == 'HARD' and reasons(v).startswith('panic') and '状态 5' in reasons(v) and '早于' in reasons(v), reasons(v))
    v = ar.judge(ar.Evidence.read(crash), ar.ROUNDS['B4'])
    check(v.status == 'HARD' and 'panic' in reasons(v), reasons(v))
    if empty.is_dir():
        v = ar.judge(ar.Evidence.read(empty), ar.ROUNDS['B1'])
        check(v.status == 'HARD' and '没有日志' in reasons(v), reasons(v))
    # 面板 65 的真实运行：第一次 exec 没留下，桌面判据不误报；另一次 1179 s panic 时 SpringBoard 被杀，抓得到（FATAL EXITS 里没有它）
    panel65, killed = runs / '20261031-221300-stage1-smoke-1', runs / '20261031-223924-stage1-full-1'
    if panel65.is_dir() and killed.is_dir():
        for directory, bad in ((panel65, False), (killed, True), (good_run, False)):
            sheet = ar.Sheet(ar.Evidence.read(directory), ar.ROUNDS['B4'])
            ar.desktop_stayed(sheet)
            check(bool(sheet.hard) == bad, (directory.name, sheet.hard))
        check('SpringBoard' not in ''.join(t for t in ar.Evidence.read(panel65).texts('S') if 'exec' in t))
    print('通过  真实日志回放：没有 [adsp] 行过不了任何一轮 ADSP')


class Fake:
    """假的 gpu-loop：每轮按脚本写一个运行目录，输出 evidence: 行。"""

    def __init__(self, work, scripts):
        self.work, self.scripts, self.commands = work, scripts, []

    def __call__(self, command):
        self.commands.append(command)
        name = command[command.index('--label') + 1][len('adsp-'):]
        check('--yes' in command and command[command.index('--serial') + 1] == 'SERIAL_13T', command)
        check(Path(command[1]).name == 'gpu-loop.py' and command[command.index('--image') + 1].endswith('boot-A830-ADSP-%s.img' % name))
        # gpu-loop.py 从 fastboot boot 起等安卓：定时复位 + 约 60 s 开机 + 留量
        check(int(command[command.index('--android-timeout') + 1]) >= max(900, ar.ROUNDS[name].reset + 180), command)
        log = self.scripts.get(name) or good(name)
        directory = log.write(self.work / ('run-%s-%d' % (name, len(self.commands))), UFS_OK % (ar.ROUNDS[name].reset - 2))
        return 0, 'lots of output\nevidence: %s\n' % directory

    def played(self):
        return [c[c.index('--label') + 1][len('adsp-'):] for c in self.commands]


def quiet(function, *args, **options):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        result = function(*args, **options)
    return result, out.getvalue()


def test_batch(work):
    every = lambda r: True
    unattended = ar.names_of('unattended')

    def run(scripts=None, **options):
        fake = Fake(work / str(len(os.listdir(work))), scripts or {})
        done, text = quiet(ar.batch, fake, images=every, **options)
        return fake.played(), done, text

    # 默认走完无人值守的五轮，停在 B3 之前，退出码 0，打印做法和要用户做什么
    played, done, _ = run()
    check(played == unattended and all(v.status == 'PASS' for v in done), played)
    status, text = quiet(ar.conclude, done)
    check(status == 0 and 'B2 通过，到这里按要求停' in text and '下一轮 B3 要用户在场' in text and '第一次写 ADSP 的电源票' in text, text)
    check('--attended --from B3' in text and 'make-adsp-rounds.sh B3' in text, text)
    check('ANDROID_SERIAL=SERIAL_13T' in text and 'check-baseline.sh' in text, text)     # 回安卓后的基线核对
    check('adb shell' in BASELINE_SCRIPT.read_text() and 'adb -s' not in BASELINE_SCRIPT.read_text() and str(ar.CHECK_BASELINE) == str(BASELINE_SCRIPT))   # 它用裸 adb：序列只能靠环境变量给
    played, done, _ = run(first='B3', last='C2', attended=True)
    check(played == ['B3', 'B4', 'C1', 'C2'], played)
    status, text = quiet(ar.conclude, done, 'B3')
    check(status == 0 and '下一轮 C3 要用户在场' in text and '不要再试别的银行' in text, text)
    # 失败即停，存疑也停
    played, done, _ = run({'B2': good('B2').plus('[adsp] FAIL st=-4 err=-16 smp2p item 429')})
    check(played == ['A1', 'A2', 'A3', 'B1', 'B2'] and done[-1].status == 'HARD', played)
    status, text = quiet(ar.conclude, done)
    check(status == 1 and '停在 B2' in text and 'C3' not in text and 'check-baseline.sh' in text, text)
    played, done, _ = run({'A3': good('A3').replaced('mv=1800 rc=0', 'mv=1800 rc=-16')})
    check(played == ['A1', 'A2', 'A3'] and done[-1].status == 'ASK', played)
    status, text = quiet(ar.conclude, done)
    check(status == 1 and 'A3 存疑，停下等人' in text and 'L15B' in text, text)
    # --from、--to
    played, done, _ = run(first='A2', last='B1')
    check(played == ['A2', 'A3', 'B1'], played)
    check(quiet(ar.conclude, done, 'A2')[0] == 0)
    played, done, _ = run(first='B2')
    check(played == ['B2'], played)
    played, done, _ = run(first='B4', last='B4', attended=True)
    check(played == ['B4'], played)
    # 要人的轮次：不带 attended 一轮都不跑，只打印做法；退出码 2（一轮都没跑）
    played, done, _ = run(first='C3')
    check(played == [] and done == [], played)
    status, text = quiet(ar.conclude, done, 'C3')
    check(status == 2 and '一轮都没跑' in text and '下一轮 C3 要用户在场' in text, text)
    played, done, _ = run(first='C6')
    check(played == [])
    status, text = quiet(ar.conclude, done, 'C6')
    check(status == 2 and '叫用户来听' in text, text)
    played, done, _ = run(first='B2', last='C5')
    check(played == ['B2'], played)                                     # 给了 --to 也停在 B3 之前
    # 带 attended：只跑这一轮，下一轮再说
    played, done, text = run(first='C3', attended=True)
    check(played == ['C3'] and '用户在场' in text, played)
    status, text = quiet(ar.conclude, done, 'C3')
    check(status == 0 and '下一轮 C4 要用户在场' in text, text)
    played, done, _ = run(first='C3', last='C5', attended=True)
    check(played == ['C3', 'C4', 'C5'], played)
    # C6：先叫人，日志全过也停下等人答；退出码不是 0
    played, done, text = run(first='C6', last='C7', attended=True)
    check(played == ['C6'] and '>>>> 叫用户来听' in text and done[-1].status == 'ASK', (played, text))
    status, text = quiet(ar.conclude, done, 'C6')
    check(status == 1 and '声音要人答' in text, text)
    played, done, _ = run(first='C7', attended=True)
    check(played == ['C7'] and done[-1].status == 'ASK', played)
    # 叫用户来听的轮次单独开跑：--to 跨不过去，C5 过了停在 C6 之前，打印做法
    played, done, _ = run(first='C5', last='C7', attended=True)
    check(played == ['C5'] and done[-1].status == 'PASS', played)
    status, text = quiet(ar.conclude, done, 'C5')
    check(status == 0 and '下一轮 C6 要用户在场' in text and '叫用户来听' in text, text)
    # C3 也单独开跑：全程最危险的一步不跟在 C2 后面自动开始
    played, done, _ = run(first='C2', last='C4', attended=True)
    check(played == ['C2'], played)
    status, text = quiet(ar.conclude, done, 'C2')
    check(status == 0 and '下一轮 C3 要用户在场' in text, text)
    # 整机复位停在 C3，下一轮不提
    reset = good('C3').until(6.1)
    played, done, _ = run({'C3': reset}, first='C3', last='C5', attended=True)
    check(played == ['C3'] and done[-1].status == 'HARD', played)
    status, text = quiet(ar.conclude, done, 'C3')
    check(status == 1 and 'C4' not in text, text)
    # 没有镜像：一轮都不跑，算失败，结论的退出码不是 0
    fake = Fake(work / 'none', {})
    done, _ = quiet(ar.batch, fake, images=lambda r: False)
    check([v.status for v in done] == ['HARD'] and not fake.commands and '没有可用的镜像' in reasons(done[0]))
    check(quiet(ar.conclude, done)[0] == 1)
    # 前面都过了，缺的是后面的镜像：跑到那里停，不算通过
    fake = Fake(work / 'late', {})
    done, _ = quiet(ar.batch, fake, images=lambda r: r.name != 'B1')
    check(fake.played() == ['A1', 'A2', 'A3'] and [v.status for v in done] == ['PASS', 'PASS', 'PASS', 'HARD'], fake.played())
    check(quiet(ar.conclude, done)[0] == 1)
    # 默认的 image_ready：真实的 测试镜像/ 里没有这些镜像
    saved = ar.IMAGES
    ar.IMAGES = work / 'empty'
    try:
        fake = Fake(work / 'real-check', {})
        done, _ = quiet(ar.batch, fake)
        check(fake.commands == [] and done[-1].status == 'HARD', done)
    finally:
        ar.IMAGES = saved
    # gpu-loop 没留下证据
    v, _ = quiet(ar.one_round, ar.ROUNDS['A1'], lambda c: (4, 'Android not seen'))
    check(v.status == 'HARD' and 'USB' in ' '.join(v.next_step) and '退出 4' in reasons(v), v)
    # gpu-loop 退出码不是 0 却有证据：不算通过
    (work / 'bad').mkdir()
    directory = good('A1').write(work / 'bad/run', UFS_OK % 88)
    v, _ = quiet(ar.one_round, ar.ROUNDS['A1'], lambda c: (3, 'evidence: %s\n' % directory))
    check(v.status == 'ASK' and '退出 3' in reasons(v), v)
    # 输出里有几个证据目录：最后一个才是这一轮的
    other = good('A1').plus('[battery] FAIL no source (gauge down, no DSP channel): no battery is shown').write(work / 'bad/other', UFS_OK % 88)
    v, _ = quiet(ar.one_round, ar.ROUNDS['A1'], lambda c: (0, 'evidence: %s\nevidence: %s\n' % (other, directory)))
    check(v.status == 'PASS', v)
    # gpu-loop 说日志的启动参数和镜像对不上：别的运行的日志，判据全过也不算
    warned = "  WARNING: the log's boot-args differ from the image's: this log may come from another run\nevidence: %s\n" % directory
    v, _ = quiet(ar.one_round, ar.ROUNDS['A1'], lambda c: (0, warned))
    check(v.status == 'HARD' and reasons(v).startswith('gpu-loop.py 说日志的启动参数'), v)
    # D 段：定时复位 900 s，gpu-loop.py 默认的 900 s 等不到安卓
    commands = []
    quiet(ar.one_round, ar.ROUNDS['D1'], lambda c: commands.append(c) or (4, ''))
    check(int(commands[0][commands[0].index('--android-timeout') + 1]) == 1200, commands)
    quiet(ar.one_round, ar.ROUNDS['A1'], lambda c: commands.append(c) or (4, ''))
    check(int(commands[1][commands[1].index('--android-timeout') + 1]) == 900, commands)
    # 结论的退出码
    check(quiet(ar.conclude, [])[0] == 2)
    check(quiet(ar.conclude, [ar.Verdict('A1', 'HARD', ['x'], [], [])])[0] == 1)
    check(quiet(ar.conclude, [ar.Verdict('A1', 'ASK', ['x'], [], [])])[0] == 1)
    check(quiet(ar.conclude, [ar.Verdict('A1', 'PASS', [], [], [])])[0] == 0)
    check(ar.up_next([ar.Verdict('D2', 'PASS', [], [], [])], 'A1') is None and ar.up_next([], 'C4') is ar.ROUNDS['C4'])
    print('通过  轮次的走法和停的位置')


def test_serial():
    def tool(answers):
        return lambda command: answers[command[0]]
    check(ar.SERIAL == 'SERIAL_13T')
    check(ar.wr.serial_attached(tool(dict(adb=(0, 'List of devices attached\nSERIAL_13T\tdevice\n'), fastboot=(0, '')))))
    check(ar.wr.serial_attached(tool(dict(adb=(0, 'List of devices attached\n'), fastboot=(0, 'SERIAL_13T\tfastboot\n')))))
    # USB 上是另一台：不算
    check(not ar.wr.serial_attached(tool(dict(adb=(0, 'List of devices attached\nSERIAL_OTHER_DEVICE\tdevice\n'), fastboot=(0, 'SERIAL_OTHER_DEVICE fastboot\n')))))
    check(not ar.wr.serial_attached(tool(dict(adb=(1, ''), fastboot=(1, '')))))
    check(not ar.wr.serial_attached(tool(dict(adb=(0, 'xSERIAL_13T\tdevice\n'), fastboot=(0, '')))))
    print('通过  只认 13T')


def tool(*args, env=None):
    """adsp-rounds.py 的命令行，经这个文件载入：变异验证时跑的也是变异过的代码。"""
    return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--as-tool', *args], capture_output=True, text=True, env=env, cwd=TOOLS)


def fake_tools(work, answer=''):
    """假的 adb、fastboot：记下每次调用。"""
    bin_dir, marker = work / 'bin', work / 'touched'
    bin_dir.mkdir(exist_ok=True)
    for name in ('adb', 'fastboot'):
        path = bin_dir / name
        path.write_text('#!/bin/sh\necho "$0 $*" >> %s\n%s' % (marker, 'echo "%s"\n' % answer if answer else ''))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH='%s:%s' % (bin_dir, os.environ['PATH']), PYTHONDONTWRITEBYTECODE='1'), marker


def test_dry_run(work):
    """不带 --yes：计划打印出来，adb、fastboot、gpu-loop.py 都没有被调用。"""
    env, marker = fake_tools(work)
    out = subprocess.run([sys.executable, str(TOOLS / 'adsp-rounds.py')], capture_output=True, text=True, env=env)
    check(out.returncode == 0 and 'SERIAL_13T' in out.stdout and '什么都没做' in out.stdout, out.stdout + out.stderr)
    check('C3  attended' in out.stdout and 'A1  unattended' in out.stdout, out.stdout)
    check(not marker.exists(), marker.read_text() if marker.exists() else '')
    out = tool(env=env)
    check(out.returncode == 0 and '什么都没做' in out.stdout, out.stdout + out.stderr)
    out = tool('--limit', '1000', env=env)
    check(out.returncode == 1 and '超过上限' in out.stderr, '1000 字节放不下，要说出来')
    out = tool('--spec', 'C6', env=env)
    lines = dict(line.split('=', 1) for line in out.stdout.splitlines())
    check(lines['image'] == 'boot-A830-ADSP-C6.img' and lines['reset'] == '150' and lines['seed'] == 's7-final' and lines['panel'] == '65', lines)
    check('native-ufs-diagnostics' in lines['drop'] and lines['drop'].endswith('pagani-audio') and lines['args'].startswith('pagani-adsp=1 '), lines)
    check(tool('--spec', 'Z9', env=env).returncode == 2)
    out = tool('--names', 'A', env=env)
    check(out.stdout.split() == ['A1', 'A2', 'A3'], out.stdout)
    check(tool('--names', 'Z', env=env).returncode == 2)
    directory = good('A1').write(work / 'replay', UFS_OK % 88)
    out = tool('--replay', str(directory), '--round', 'A1', env=env)
    check(out.returncode == 0 and 'A1：通过' in out.stdout, out.stdout + out.stderr)
    out = tool('--replay', str(directory), '--round', 'B4', env=env)
    check(out.returncode == 1 and '失败，立刻停' in out.stdout, out.stdout)
    directory = good('C6').write(work / 'replay-c6', UFS_OK % 148)
    out = tool('--replay', str(directory), '--round', 'C6', env=env)
    check(out.returncode == 1 and '存疑，停下等人' in out.stdout, out.stdout)
    check(tool('--replay', str(directory), env=env).returncode == 2)
    check(not marker.exists(), marker.read_text() if marker.exists() else '')
    # 真跑也要先认出 13T：USB 上是另一台时只问了 devices，什么都没动。这里的 adb、fastboot 是上面写的假脚本
    env, marker = fake_tools(work, 'SERIAL_OTHER_DEVICE device')
    out = tool('--yes', env=env)
    check(out.returncode == 2 and '都看不到 SERIAL_13T' in out.stderr, out.stdout + out.stderr)
    asked = marker.read_text().splitlines()
    check(len(asked) == 2 and all(line.endswith(' devices') for line in asked), asked)
    # --from、--to 写错的，还没问手机就停
    marker.unlink()
    check(tool('--yes', '--from', 'Z9', env=env).returncode == 2)
    out = tool('--yes', '--from', 'B1', '--to', 'A1', env=env)
    check(out.returncode == 2 and '--to 在 --from 前面' in out.stderr, out.stderr)
    # --attended 不能从无人值守的轮次开始：--yes --attended 不带 --from 会从 A1 一路走进 B3；跳步也不行（这里 runs/ 是空的）
    out = tool('--yes', '--attended', env=env)
    check(out.returncode == 2 and '--attended 只从要人的轮次开始' in out.stderr, out.stderr)
    out = tool('--yes', '--attended', '--to', 'C3', env=env)
    check(out.returncode == 2 and '--attended 只从要人的轮次开始' in out.stderr, out.stderr)
    out = tool('--yes', '--attended', '--from', 'C3', env=env)
    check(out.returncode == 2 and 'A1 没有判决' in out.stderr, out.stdout + out.stderr)
    check(not marker.exists())
    print('通过  不带 --yes 什么都不碰')


def test_main(work):
    """--yes 的整条路径：序列门和 gpu-loop 用假的，看参数传到位、判决写进报告、退出码。"""
    saved = ar.HERE, ar.wr.serial_attached, ar.wr.run_streaming, ar.image_ready, sys.argv
    (work / 'runs').mkdir()
    fake = Fake(work / 'fake', {})
    ar.HERE, ar.wr.serial_attached, ar.wr.run_streaming, ar.image_ready = work, lambda run: True, fake, lambda r, limit=0: True
    def history(stamp, **verdicts):
        (work / 'runs' / ('adsp-rounds-%s.json' % stamp)).write_text(json.dumps([dict(name=n, status=s) for n, s in verdicts.items()]))

    def main(*args):
        fake.commands.clear()
        sys.argv = ['adsp-rounds.py', *args]
        return quiet(ar.main)

    try:
        # 跳步：前面的轮次没有判决，一个调用都不发，序列门也不问
        asked = []
        ar.wr.serial_attached = lambda run: asked.append(1) or True
        status, text = main('--yes', '--from', 'B1')
        check(status == 2 and not fake.commands and not asked and 'A1 没有判决' in text, text)
        history('00000000-000000', A1='PASS', A2='PASS', A3='ASK', B1='PASS', B2='PASS', B3='PASS')     # ASK 是用户看过才往下走的
        status, text = main('--yes', '--attended', '--from', 'B4', '--to', 'C1')
        check(status == 0 and fake.played() == ['B4', 'C1'] and 'C1 通过，到这里按要求停' in text and len(asked) == 1, text)
        reports = [path for path in (work / 'runs').glob('adsp-rounds-*.json') if '00000000' not in path.name]
        check(len(reports) == 1 and [v['status'] for v in json.loads(reports[0].read_text())] == ['PASS', 'PASS'], reports)
        history('00000000-000001', B4='PASS', C1='PASS', C2='PASS')     # 同一秒的报告会互相盖掉（真跑时一轮就要几分钟）
        status, text = main('--yes', '--from', 'C3')
        check(status == 2 and not fake.commands and '下一轮 C3 要用户在场' in text, text)      # 没有 --attended：一轮都不跑
        status, text = main('--yes', '--attended', '--from', 'C3')
        check(status == 0 and fake.played() == ['C3'], text)                                    # 有 --attended：只跑这一轮
        # C3 整机复位以后不能越过去试 C4、C5：文档说停，不要再试别的银行
        history('99999999-000000', C3='HARD')
        for first in ('C4', 'C5', 'C6', 'D1'):
            status, text = main('--yes', '--attended', '--from', first)
            check(status == 2 and not fake.commands and 'C3 最近一次判 HARD' in text, text)
        status, text = main('--yes', '--attended', '--from', 'C3')                              # 重跑 C3 本身可以
        check(status == 0 and fake.played() == ['C3'], text)
        history('99999999-000001', C3='PASS')
        status, text = main('--yes', '--attended', '--from', 'C4')
        check(status == 0 and fake.played() == ['C4'], text)
        # 报告坏了当没跑过
        for path in (work / 'runs').glob('adsp-rounds-*.json'):
            path.write_text('[{"name": "A1"')
        status, text = main('--yes', '--attended', '--from', 'C4')
        check(status == 2 and not fake.commands and 'A1 没有判决' in text and '跳过读不了的报告' in text, text)
    finally:
        ar.HERE, ar.wr.serial_attached, ar.wr.run_streaming, ar.image_ready, sys.argv = saved
    print('通过  --yes 的整条路径')


def test_make_script(work):
    """make-adsp-rounds.sh：bash -n；DRY 打印的环境按 make-a830-layout.sh 的规则算出来，就是这一轮的命令行。"""
    check(subprocess.run(['bash', '-n', str(MAKE)]).returncode == 0)
    env, marker = fake_tools(work)
    env['DRY'] = '1'

    def dry(*args):
        out = subprocess.run(['bash', str(MAKE), *args], capture_output=True, text=True, env=env)
        check(out.returncode == 0, out.stdout + out.stderr)
        return out.stdout

    def rounds(text):
        return re.findall(r'^=== (\w+):', text, re.M)

    check(rounds(dry()) == ar.names_of('unattended'))
    for segment in 'ABCD':
        check(rounds(dry(segment)) == ar.names_of(segment), segment)
    check(rounds(dry('C3', 'B1')) == ['C3', 'B1'])
    out = subprocess.run(['bash', str(MAKE), 'Z9'], capture_output=True, text=True, env=env)
    check(out.returncode != 0 and '没有这一轮' in out.stderr, out.stderr)
    text = dry(*ar.ROUNDS)
    check(not marker.exists(), '出包计划不问 adb')
    base = ar.baseline_args()
    sections = re.findall(r"^=== (\w+): (\S+) .*\n(A830_DROP_ARGS=.*)\nA830_EXTRA_ARGS='(.*)'$", text, re.M)
    check(len(sections) == len(ar.ROUNDS), len(sections))
    for name, image, environment, extra in sections:
        r = ar.ROUNDS[name]
        check(image == r.image.name and extra.startswith(stage16_base() + ' sheng-log=1'), name)
        values = dict(re.findall(r"(A830_\w+)=('[^']*'|\S+)", environment))
        values = {k: v.strip("'") for k, v in values.items()}
        check(values['A830_BATTERY'] == '0' and values['A830_AUDIO'] == '1' and values['A830_SYSTEM_APPS'] == '1', values)
        check(values['A830_PREBUILT_SEED'].endswith('final/s7-final.dbrdisk') and values['A830_OWNER_TRUST'].endswith('final/s7-final-owner-trust.bundle'), values)
        line = layout_line(values, extra)
        want = ar.command_line(r, base)
        check(sorted(line) == sorted(want), (name, sorted(set(line) ^ set(want))))
        check(len(' '.join(line)) == ar.size_of(r, base) <= ar.LIMIT, name)
    # 基线只有一份，从 make-stage16.sh 现读
    check('BASE=$(sed' in MAKE.read_text() and 'pagani-gpu-status=3' not in MAKE.read_text())
    # 上面看的是 DRY 打印的那一行：真出包的那一行没跑过，至少它的环境要和打印的一样
    script = MAKE.read_text()
    printed = re.search(r"""echo "A830_DROP_ARGS='\$drop' (.*)"$""", script, re.M).group(1).split()
    real = re.search(r'^  A830_DROP_ARGS="\$drop" ((?:.|\n)*?) 工具/make-a830-layout\.sh phone-island-apps$', script, re.M).group(1)
    check(len(printed) >= 7 and all(word in real.split() for word in printed), (printed, real))
    check('A830_EXTRA_ARGS="$BASE sheng-log=1 $args"' in real and "A830_EXTRA_ARGS='$BASE sheng-log=1 $args'" in script and 'A830_EXTRA_FIXUPS="$FIX"' in real, real)
    print('通过  make-adsp-rounds.sh 打印的环境算出来就是计划的命令行')


def test_check_payload(work):
    saved = ar.IMAGES, ar.KERNEL
    r = ar.ROUNDS['C3']
    good_words = ar.command_line(r, ar.baseline_args())
    baseline = ar.BASELINE
    ar.IMAGES, ar.KERNEL = work, work / 'kernel.macho'
    ar.KERNEL.write_bytes(b'this kernel')
    kernel = hashlib.sha256(b'this kernel').hexdigest()
    try:
        record = r.image.with_suffix('.payload.json')

        def verdict(words, built=kernel):
            record.write_text(json.dumps(dict(kernel_args=' '.join(words), kernel_sha256=built)))
            return quiet(ar.check_payload, 'C3', 2000)[0]      # C3 是最长的一轮，多一项就超长：这里只看逐项核对
        check(verdict(good_words) == 0)
        check(verdict([w for w in good_words if not w.startswith('pagani-speaker-sids=')]) == 1)
        check(verdict(good_words + ['pagani-kbtrace=1']) == 1)
        check(verdict(good_words + ['pagani-battery=1']) == 1)              # 没开 A830_BATTERY 才对
        check(verdict(good_words + ['pagani-speaker-feedback=1']) == 1)
        check(verdict([w.replace('pagani-reset-after=150', 'pagani-reset-after=420') for w in good_words]) == 1)
        check(verdict([w for w in good_words if w != 'sheng-log=1']) == 1)
        check(verdict([w.replace('pagani-panel=65', 'pagani-panel=1217') for w in good_words]) == 1)
        record.write_text('{"kernel_args": "x"')
        check(quiet(ar.check_payload, 'C3', ar.LIMIT)[0] == 1)
        # 超长：记录和计划一样，只是上限收紧
        check(verdict(good_words) == 0)
        check(quiet(ar.check_payload, 'C3', 1000)[0] == 1 and quiet(ar.check_payload, 'C3', ar.LIMIT)[0] == 0)
        # 出包之后内核重编过：镜像带的还是旧驱动
        check(verdict(good_words, 'an older kernel') == 1 and verdict(good_words, None) == 1)
        check(verdict(good_words) == 0)
        # 开跑前的同一套核对：记录不对或没有记录，这一轮不跑
        r.image.write_bytes(b'')
        check(ar.image_ready(r))
        ar.KERNEL.write_bytes(b'rebuilt since')
        check(not ar.image_ready(r))
        ar.KERNEL.write_bytes(b'this kernel')
        check(ar.image_ready(r))
        record.write_text(json.dumps(dict(kernel_args=' '.join(good_words + ['pagani-kbtrace=1']), kernel_sha256=kernel)))
        check(not ar.image_ready(r))
        record.unlink()
        check(not ar.image_ready(r))
        record.write_text(json.dumps(dict(kernel_args=' '.join(good_words), kernel_sha256=kernel)))
        r.image.unlink()
        check(not ar.image_ready(r))
    finally:
        ar.IMAGES, ar.KERNEL = saved
        ar.BASELINE = baseline
    print('通过  payload 记录逐项核对')


def test_check_kernel(work):
    """内核核对：假的构建树，改一个字节就要重编。"""
    names = 'TREE', 'KERNEL', 'DRIVERS', 'SCM'
    saved = {name: getattr(ar, name) for name in names}
    tree = work / 'tree'
    ar.TREE, ar.KERNEL, ar.DRIVERS, ar.SCM = tree, work / 'kernel.macho', work / 'drivers', work / 'Sm8750Scm.c'
    try:
        sources = {}
        for build, driver in (('pagani-adsp', 'PaganiAdsp'), ('pagani-speaker', 'PaganiSpeaker'), ('pagani-battery', 'PaganiBattery')):
            (ar.DRIVERS / driver).mkdir(parents=True)
            (ar.DRIVERS / driver / 'a.c').write_text(driver)
            (tree / 'build' / build).mkdir(parents=True)
            report = dict(source_sha256={'a.c': hashlib.sha256(driver.encode()).hexdigest(), 'PeripheralIO.h': 'elsewhere'})
            (tree / 'build' / build / 'build-report.json').write_text(json.dumps(report))
            sources[driver] = report
        ar.SCM.write_text('static uint32_t all_wakes[4];\n')
        (tree / 'build/adreno830').mkdir(parents=True)
        (tree / 'build/adreno830/build-report.json').write_text(json.dumps(dict(source_sha256={str(ar.SCM): hashlib.sha256(ar.SCM.read_bytes()).hexdigest()})))
        check(ar.kernel_problems() == ['没有内核 %s' % ar.KERNEL])
        ar.KERNEL.write_bytes(b'')
        check(ar.kernel_problems() == [], ar.kernel_problems())
        check(quiet(ar.check_kernel)[0] == 0)
        (ar.DRIVERS / 'PaganiSpeaker/a.c').write_text('changed')
        check(['改过' in p and 'PaganiSpeaker/a.c' in p for p in ar.kernel_problems()] == [True], ar.kernel_problems())
        check(quiet(ar.check_kernel)[0] == 1)
        (ar.DRIVERS / 'PaganiSpeaker/a.c').write_text('PaganiSpeaker')
        ar.SCM.write_text('no shared wakes here\n')
        check(['共享唤醒' in p for p in ar.kernel_problems()] == [True], ar.kernel_problems())
        ar.SCM.write_text('static uint32_t all_wakes[8];\n')
        check(['Sm8750Scm.c 改过' in p for p in ar.kernel_problems()] == [True], ar.kernel_problems())
        ar.SCM.write_text('static uint32_t all_wakes[4];\n')
        os.utime(ar.KERNEL, (1000, 1000))
        check(any('内核比 pagani-adsp 的构建报告旧' in p for p in ar.kernel_problems()), ar.kernel_problems())
        ar.KERNEL.touch()
        (tree / 'build/pagani-battery/build-report.json').unlink()
        check(any('读不了' in p for p in ar.kernel_problems()), ar.kernel_problems())
    finally:
        for name, value in saved.items():
            setattr(ar, name, value)
    print('通过  内核对照工作副本核对')


def run_all():
    with tempfile.TemporaryDirectory(prefix='adsp-rounds-') as scratch:
        work = Path(scratch)
        for name in ('a', 'b', 'c', 't', 'h', 'm', 'd', 'y', 'k', 'p', 'r'):
            (work / name).mkdir()
        test_table()
        test_forbidden()
        test_baseline()
        test_sources()
        test_round_a(work / 'a')
        test_round_b(work / 'b')
        test_round_c(work / 'c')
        test_tones(work / 't')
        test_health(work / 'h')
        test_real_runs()
        test_batch(work / 'm')
        test_serial()
        test_dry_run(work / 'd')
        test_main(work / 'y')
        test_make_script(work / 'k')
        test_check_payload(work / 'p')
        test_check_kernel(work / 'r')
    print('全部通过 adsp-rounds：%d 项检查' % checks)


# 变异验证：每一条是 adsp-rounds.py 里的一处关键判据，改掉它，上面的测试必须失败
MUTATIONS = (
    # 走法、序列门、退出码
    ("if r.attended and not attended:\n            break", "if False:\n            break"),
    ("or (r.attended and last is None)", "or False"),
    ("'--serial', SERIAL, '--yes',", "'--yes',"),
    ("str(max(ANDROID_WAIT, r.reset + ANDROID_AFTER_RESET))", "str(ANDROID_WAIT)"),
    ("if \"the log's boot-args differ from the image's\" in output:", "if False:"),
    ("        if (r.listen or r.solo) and done:\n            break\n", ""),
    ("if (r.listen or r.solo) and done:", "if r.listen and done:"),
    ("if (r.listen or r.solo) and done:", "if r.solo and done:"),
    ("不要再试别的银行', attended=True, solo=True)", "不要再试别的银行', attended=True)"),
    ("    if args.attended and not ROUNDS[args.first].attended:\n", "    if False:\n"),
    ("    blocked = out_of_order(args.first)\n", "    blocked = None\n"),
    ("        if name not in latest:\n", "        if False:\n"),
    ("        if latest[name] == 'HARD':\n", "        if False:\n"),
    ("    for path in sorted((HERE / 'runs').glob('adsp-rounds-*.json')):", "    for path in reversed(sorted((HERE / 'runs').glob('adsp-rounds-*.json'))):"),
    ("for name in names[names.index(first):]:", "for name in names:"),
    ("found = re.findall(r'^evidence: (.+)$', output, re.M)\n    if not found:", "found = re.findall(r'^evidence: (.+)$', output, re.M)[:1]\n    if not found:"),
    ("    if last.status == 'HARD':\n        print('  停在 %s：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))\n        return 1\n",
     "    if last.status == 'HARD':\n        print('  停在 %s：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))\n        return 0\n"),
    ("        if not images(r):\n", "        if False:\n"),
    ("if verdict.status != 'PASS' or name == last", "if verdict.status == 'HARD' or name == last"),
    ("if verdict.status != 'PASS' or name == last", "if verdict.status == 'ASK' or name == last"),
    ("if not wr.serial_attached(wr.run_streaming):", "if False:"),
    ("    if not args.yes:\n        return plan(args.limit)\n", "    if False:\n        return plan(args.limit)\n"),
    ("    if last.status == 'ASK':\n        print('  %s 存疑，停下等人：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))\n        return 1\n",
     "    if last.status == 'ASK':\n        print('  %s 存疑，停下等人：%s' % (last.name, last.reasons[0] if last.reasons else '见上'))\n        return 0\n"),
    ("        print('  一轮都没跑')\n        if coming and coming.attended:\n            brief(coming)\n        return 2\n",
     "        print('  一轮都没跑')\n        if coming and coming.attended:\n            brief(coming)\n        return 0\n"),
    ("print('  回到安卓后跑基线核对（只读）：ANDROID_SERIAL=%s bash %s' % (SERIAL, shlex.quote(str(CHECK_BASELINE))))", "pass"),
    ("    if coming and coming.attended:\n        brief(coming)\n    return 0\n", "    return 0\n"),
    ("if verdict.status == 'PASS':\n            verdict.status = 'ASK'", "if False:\n            verdict.status = 'ASK'"),
    ("done = batch(wr.run_streaming, args.first, args.last, args.attended, ", "done = batch(wr.run_streaming, args.first, args.last, False, "),
    ("done = batch(wr.run_streaming, args.first, args.last, args.attended, ", "done = batch(wr.run_streaming, args.first, None, args.attended, "),
    ("    if args.last and names.index(args.last) < names.index(args.first):\n", "    if False:\n"),
    # 轮次表
    ("'全程最危险的一步：apps SMMU 写一个 S2CR、一个 SMR（只有 0x1001）',\n              '用户在场看着屏幕。可能整机复位：复位了就停，这台机器 Gunyah 不接受音频流路由，不要再试别的银行', attended=True, solo=True)",
     "'全程最危险的一步：apps SMMU 写一个 S2CR、一个 SMR（只有 0x1001）',\n              '用户在场看着屏幕。可能整机复位：复位了就停，这台机器 Gunyah 不接受音频流路由，不要再试别的银行')"),
    ("摸一下屏幕：日志只看得出桌面起来、投票之后 GPU 在出帧', attended=True)", "摸一下屏幕：日志只看得出桌面起来、投票之后 GPU 在出帧')"),
    ("日志只看 SpringBoard 没有重启', attended=True)", "日志只看 SpringBoard 没有重启')"),
    ("回安卓后看充电和电流前后变化', attended=True)", "回安卓后看充电和电流前后变化')"),
    ("功放配过寄存器但掉电，不该出声', attended=True)", "功放配过寄存器但掉电，不该出声')"),
    ("BASELINE = IMAGES / 'boot-A830-阶段16-完整包.payload.json'", "BASELINE = IMAGES / 'boot-A830-阶段15-完整包.payload.json'"),
    ("(13, '[adsp] channel IPCRTR up'), (13, '[adsp] qrtr hello '),", "(13, '[adsp] channel IPCRTR up'),"),
    ("(5, '[spk] amp 0x34 config ok'),\n", "\n"),
    ("'pagani-speaker-stop=8', 'pagani-speaker-sids=1'),", "'pagani-speaker-stop=8'),"),
    ("drop=AUDIO_OFF, attended=True, listen=True),\n        Round('C7'", "attended=True, listen=True),\n        Round('C7'"),
    ("tone_note, drop=AUDIO_OFF, attended=True, listen=True),\n        Round('C7'", "tone_note, drop=AUDIO_OFF, attended=True),\n        Round('C7'"),
    ("Round('B4', 'B', RESET_DESK,", "Round('B4', 'B', RESET_STOP,"),
    ("('pagani-battery=1', 'pagani-adsp=1', 'pagani-battery-log=2')", "('pagani-battery=1', 'pagani-adsp=1')"),
    ("gone = set(DROP_ALWAYS) | set(r.drop)", "gone = set(DROP_ALWAYS)"),
    ("if not w.startswith('pagani-battery=')]", "]"),
    ("    'pagani-kbtrace',           # 17 字节：键盘追踪\n", ""),
    ("PANEL, SEED = 65, 's7-final'", "PANEL, SEED = 1217, 's7-final'"),
    ("PANEL, SEED = 65, 's7-final'", "PANEL, SEED = 65, 's6-final'"),
    ("LIMIT = 1020 ", "LIMIT = 1000 "),
    # 今天不能开的
    ("if (name == 'pagani-battery-charge' ", "if (False "),
    ("or (name == 'pagani-battery' and amount != 1) ", "or False "),
    ("or (name == 'pagani-battery-pmic' and amount not in (0, 1)) ", "or False "),
    ("amount < 12", "amount < 6"),
    ("or (name == 'pagani-speaker-feedback' and amount != 0) ", "or False "),
    ("or (name == 'pagani-gpu-async' and amount != 0) ", "or False "),
    ("or name.startswith('pagani-audio-tone-') ", "or False "),
    ("or (adsp and name in ('pagani-wifi', 'pagani-sensors') and amount != 0)):", "or False):"),
    # 每轮都要的
    ("    if panic:\n        s.hard.append(", "    if False:\n        s.hard.append("),
    ("if u['write_errors']:", "if False:"),
    ("if u['state'] != 6:", "if False:"),
    ("    if e.last_time() < r.reset - 15:\n        s.hard.append(", "    if False:\n        s.hard.append("),
    ("        if word not in seen:\n", "        if False:\n"),
    ("    for word in s.r.args:\n", "    for word in s.r.args[:1]:\n"),
    ("if why[-1:] != ['timer']:", "if False:"),
    ("    if e.last_time() < r.reset - 15:\n        s.hard.append(", "    if e.last_time() < r.reset - 200:\n        s.hard.append("),
    ("    if forbidden(seen):\n", "    if False:\n"),
    ("if found and not turned_on(s.r, name):", "if False:"),
    ("wr.apps_up(e, s.hard, s.facts)", "pass"),
    ("if len(starts) > 1 or late:", "if False:"),
    ("if len(starts) > 1 or late:", "if late:"),
    ("        late = [t for t in starts if up is not None and t > up]\n", "        late = []\n"),
    ("if re.match(r'exit pid=\\d+ %s ' % name, text):", "if False:"),
    ("if powered and 'true' not in powered:", "if False:"),
    ("if reset.groups() != ('HARD_RESET', 'PS_HOLD'):", "if False:"),
    ("if re.search(r'^\\s+%s\\s+status=' % name, s.e.summary, re.M):", "if False:"),
    ("if usb and usb.group(1) == 'true' and status and status.group(1) not in ('2', '5'):", "if False:"),
    # 电池
    ("if s.lines('[battery] dsp set'):", "if False:"),
    ("if start.get('charge') != '0':", "if False:"),
    ("if start.get('adsp') != ('1' if dsp else '0'):", "if False:"),
    ("if failed or not s.lines('[battery] i2c up st=5 err=0'):", "if False:"),
    ("gauge.get('type') != '0x4231'", "False"),
    ("abs((number(published.get('pct')) or 0) - s.level) > 5", "abs((number(published.get('pct')) or 0) - s.level) > 500"),
    ("if cycle[0].get('layout') != 'oplus':", "if False:"),
    ("if not [t for t in s.lines('[battery] state ') if fields(t).get('src') == 'gauge+charger']:", "if False:"),
    # ADSP
    ("if stage <= (stop or 13) and not s.lines(prefix):", "if False:"),
    ("parked = [t for t in s.lines('[adsp] ') if 'scm_parked=1' in t]", "parked = []"),
    ("        if fields(line).get('rc') != '0':\n            s.hard.append('安全调用失败：%s' % line)", "        if False:\n            s.hard.append('安全调用失败：%s' % line)"),
    ("if not (number(ready.get('slave')) or 0) & 2:", "if False:"),
    ("if stopped.get('st') != str(stop):", "if False:"),
    ("if off.get('rc') != '0':", "if False:"),
    ("if not undo or any(undo.get(k) != '0' for k in ('shutdown_adsp', 'shutdown_dtb', 'unvote', 'load_state_off')) or 'kept_votes' in undo:", "if False:"),
    ("if not s.lines('[adsp] handover: proxy votes dropped rc=0'):", "if False:"),
    ("if not [text for t, kind, text in s.e.records if kind == 'F' and t > voted and re.search(r'\\| gpu [1-9]', text)]:", "if False:"),
    ("kind == 'F' and t > voted and re.search", "kind == 'F' and re.search"),
    ("if stop == 0 or stop >= 7:\n        if s.first('[adsp] rails on ')", "if stop >= 7:\n        if s.first('[adsp] rails on ')"),
    ("if stop == 0 or stop >= 6:\n        if s.first('[adsp] qmp rc=')", "if stop >= 6:\n        if s.first('[adsp] qmp rc=')"),
    ("if stop == 0 or stop >= 5:\n        if s.lines('[adsp] ipcc interrupt unavailable, polling'):", "if stop >= 5:\n        if s.lines('[adsp] ipcc interrupt unavailable, polling'):"),
    ("if not s.lines('[adsp] locator ask=avs/audio domains=1 sent=1'):", "if False:"),
    ("for line in s.lines('[adsp] pd crash report'):", "for line in []:"),
    ("('[adsp] DSP fatal error while running', 'DSP 运行中崩溃'), ", ""),
    ("if s.first('[adsp] qmp rc=').get('reused') != '1':", "if False:"),
    ("if s.first('[adsp] rails on ').get('vlvl') != '384':", "if False:"),
    ("elif s.first('[adsp] ipcc intid=').get('readback') != '0xa003':", "elif False:"),
    ("if (part.get('part'), part.get('lun'), part.get('first')) != ('modem_a', '4', '4870'):", "if False:"),
    ("if (survey.get('items'), survey.get('slave'), survey.get('qmp'), survey.get('refused')) != ('0x0', '-1', 'LIAM', '0'):", "if False:"),
    ("if s.e.texts('P') and 'smem touch' in s.last('[adsp] '):", "if False:"),
    ("if auth.get('intr') == '0' and (number(auth.get('us')) or 0) >= BUSY_US:", "if False:"),
    # 扬声器
    ("if start.get('feedback') != '0':", "if False:"),
    ("if start.get('ceil_db') != CEILING_DB or start.get('ceil') != CEILING:", "if False:"),
    ("if start.get('stop') != str(stop):\n        s.hard.append('[spk] start", "if False:\n        s.hard.append('[spk] start"),
    ("if number(start.get('sids')) != (argument(r, 'pagani-speaker-sids') or 0xf):", "if False:"),
    ("if stage <= (stop or 10) and not s.lines(prefix):", "if False:"),
    ("s.hard.extend(absent + mismatch + broken)", "pass"),
    ("if rail.get('mv') != '1800' or rail.get('rc') != '0':", "if False:"),
    ("if bus.get('clk') != '0' or bus.get('rc') != '0':", "if False:"),
    ("if identity.get('type') != 'tfa9873' or (number(identity.get('rev')) or 0) & 0xff != 0x73:", "if False:"),
    ("if identity.get('table') == '0':", "if False:"),
    ("if [sid for sid, _ in streams] != wanted:", "if False:"),
    ("            if sid == '0x1001':\n", "            if False:\n"),
    ("if any(buffer.get(k) != v for k, v in BUFFER.items()):", "if False:"),
    ("if not (number(s.first('[spk] graphs ').get('handle')) or 0):", "if False:"),
    ("if s.early() and (stop == 0 or stop >= 8) and not s.lines('[spk] smmu 0x1001='):", "if False:"),
    ("if s.e.texts('P') and s.lines('[spk] smmu 0x1001=') and not s.lines('[spk] buffer '):", "if False:"),
    ("return not self.e.texts('P') and self.e.last_time() < self.r.reset - 15", "return self.e.last_time() < self.r.reset - 15"),
    ("if fields(s.last('[spk] stopped as asked (pagani-speaker-stop)')).get('st') != str(stop):", "if False:"),
    ("if [t.get('ch') for t in played] != wanted:", "if False:"),
    ("        if tone.get('rc') != '0':\n", "        if False:\n"),
    ("if (tone.get('hz'), tone.get('ms'), tone.get('atten'), tone.get('ceil')) != (TONE_HZ, TONE_MS, TONE_ATTEN, CEILING):", "if False:"),
    ("if stop.get('lim') != '0' or stop.get('clamp') != '0':", "if False:"),
    ("if tone.get('amps') != '0x3' or tone.get('clks_failed') != '0':", "if False:"),
    ("if any(stop.get(k) != '0' for k in ('werr', 'sendfail', 'stalls')):", "if False:"),
    ("elif any((t.get('state'), t.get('clks'), t.get('plls')) != ('9', '1', '1') for t in states):", "elif False:"),
    ("    if s.lines('[spk] backend adsp handed'):\n        s.hard.append('后端交给 iOS 了", "    if False:\n        s.hard.append('后端交给 iOS 了"),
    ("if not s.lines('[spk] backend adsp handed to the PaganiAudio service'):", "if False:"),
    ("if (number(fields(line).get('st')) or 0) & AMP_ALARMS:", "if False:"),
    ("AMP_ALARMS = 0x3c", "AMP_ALARMS = 0x38"),
    ("AMP_ALARMS = 0x3c", "AMP_ALARMS = 0x1c"),
    ("AMP_ALARMS = 0x3c", "AMP_ALARMS = 0x3d"),
    ("    if len(stops) != len(wanted):\n", "    if False:\n"),
    ("    if mode == 2 and not s.lines('[spk] tone walk:'):\n", "    if False:\n"),
    ("    for prefix in ('[spk] stall: the DSP stopped answering', '[spk] play lost: the DSP channel closed', '[spk] play refused'):", "    for prefix in ():"),
    ("if fields(line).get('clamp') != '0':\n                s.hard.append('天花板之上", "if False:\n                s.hard.append('天花板之上"),
    ("if r.listen and not s.hard and not s.ask:", "if False:"),
    ("if r.human and not r.listen:", "if False:"),
    ("if r.human and not r.listen:", "if r.human and not r.attended:"),
    # 镜像记录、内核
    ("problems = ['缺 %s' % w for w in want if w not in words]", "problems = []"),
    ("problems += ['多 %s' % w for w in words if w not in want]", "pass"),
    ("    if size > limit:\n        problems.append('%d 字节，超过 %d' % (size, limit))\n", ""),
    ("return r.image.is_file() and r.image.with_suffix('.payload.json').is_file() and not payload_problems(r, limit)[0]",
     "return r.image.with_suffix('.payload.json').is_file() and not payload_problems(r, limit)[0]"),
    ("return r.image.is_file() and r.image.with_suffix('.payload.json').is_file() and not payload_problems(r, limit)[0]", "return r.image.is_file()"),
    ("if b'all_wakes' not in source:", "if False:"),
    ("elif built != hashlib.sha256(source).hexdigest():", "elif False:"),
    ("if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != value:", "if False:"),
    ("if KERNEL.stat().st_mtime < report.stat().st_mtime:", "if False:"),
    ("if record.get('kernel_sha256') != built:", "if False:"),
)


def mutate():
    """每个变异跑一遍全部测试，要有一项失败。任何一个活下来，退出码就不是 0。一遍 7 s，并行跑。"""
    source = (TOOLS / 'adsp-rounds.py').read_text()
    survived, stale = [], []

    def one(item):
        index, (old, new) = item
        if old not in source:
            stale.append(old)
            return
        env = dict(os.environ, ADSP_ROUNDS_MUTATION=json.dumps([old, new]), PYTHONDONTWRITEBYTECODE='1')
        # 变异过的代码要载入得了：载入就崩的不算抓到
        loads = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--as-tool', '--names', 'A'], capture_output=True, text=True, env=env)
        if loads.returncode:
            stale.append(old)
            return
        run = subprocess.run([sys.executable, str(Path(__file__).resolve())], capture_output=True, text=True, env=env)
        if run.returncode == 0:
            survived.append(old)
        print('%3d %s  %s' % (index + 1, '活下来' if run.returncode == 0 else '抓到  ', old.splitlines()[0][:90]), flush=True)

    with concurrent.futures.ThreadPoolExecutor(max(2, (os.cpu_count() or 4) // 2)) as pool:
        list(pool.map(one, enumerate(MUTATIONS)))
    print('变异 %d 个，抓到 %d 个' % (len(MUTATIONS), len(MUTATIONS) - len(survived) - len(stale)))
    for old in stale:
        print('变异点不在源码里，或者改完载入不了：%s' % old)
    for old in survived:
        print('活下来：%s' % old)
    return 1 if survived or stale else 0


if __name__ == '__main__':
    if sys.argv[1:2] == ['--as-tool']:
        sys.argv = ['adsp-rounds.py'] + sys.argv[2:]
        with tempfile.TemporaryDirectory(prefix='adsp-rounds-') as scratch:     # 变异掉序列门时也走得到写报告，别写进真的 runs/
            ar.HERE = Path(scratch)
            (ar.HERE / 'runs').mkdir()
            sys.exit(ar.main())
    if sys.argv[1:2] == ['--mutate']:
        sys.exit(mutate())
    run_all()
