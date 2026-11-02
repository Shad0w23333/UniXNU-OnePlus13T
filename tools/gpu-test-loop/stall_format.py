#!/usr/bin/env python3
"""The stall probe's records (BootProgress StallProbe.h, boot-arg pagani-diag) as data, for symbolize-crashes.py and stall-report.py.

parse(records) takes (seq, seconds, kind, verified, text) tuples, as symbolize-crashes.py's read_log gives them, and returns a Run:
  samples   Q samples in log order: the head record, the threads (th + bt records, by tag), the CPUs (cpu records), the tags with no thread
  seconds   M records, one Second per pass (lat, cpu, ipi, io, mem fold into the Second they came in), with the F record's tail added
  maps      pid -> the Q map record
  images    pid -> the Q img records: raw addresses of that process become file offsets
  frames    the F records (fps, max ms, gap/copy of the SLOW part)
  jitters   the J records
  kbt_long  kbt ... long records, with kbt_offset to put their mach times on the log's clock
A record the ring lost leaves a sample without that part: every field is optional, nothing here raises on a short record.
"""
import re

BLOCK_HINTS = {
    0: 'none', 1: 'kernel mutex', 2: 'port receive', 3: 'port set receive', 4: 'port send', 5: 'port send in transit', 6: 'semaphore',
    7: 'kernel rwlock read', 8: 'kernel rwlock write', 9: 'kernel rwlock upgrade', 10: 'user lock', 11: 'pthread mutex',
    12: 'pthread rwlock read', 13: 'pthread rwlock write', 14: 'pthread condvar', 15: 'parked workqueue', 16: 'workloop sync wait',
    17: 'on process', 18: 'sleep with inheritor', 19: 'eventlink', 20: 'compressor',
}
SYSCALLS = {
    -31: 'mach_msg', -32: 'mach_msg_overwrite', -47: 'mach_msg2', -36: 'semaphore_wait', -38: 'semaphore_timedwait',
    -39: 'semaphore_timedwait_signal', -61: 'thread_switch', -60: 'swtch', 3: 'read', 93: 'select', 230: 'poll', 301: 'psynch_mutexwait',
    303: 'psynch_cvbroad', 305: 'psynch_cvwait', 334: '__semwait_signal', 369: 'kevent64', 374: 'kevent_qos', 375: 'kevent_id', 396: 'read_nocancel',
    423: '__semwait_signal_nocancel', 515: 'ulock_wait', 544: 'ulock_wait2',
}
STATES = {'C': '在 CPU 上', 'R': '可运行未上 CPU', 'W': '等待', 'U': '不可中断等待', 'T': '退出中', '?': '未知'}
HEAD = re.compile(r'^s=(\d+) (.*)$')
LONG = re.compile(r'^kbt (\S+?)\[(\d+)\] long (\S+) b=(\d+) e=(\d+) d=(\d+)$')
FIRST = re.compile(r'^kbt \S+\[\d+\] first \S+ @(\d+)ms$')
F_TAIL = re.compile(r' \| tch (\d+) last (\S+) vl (\d+)')
F_MAX = re.compile(r'max ([0-9.]+)ms')
F_FPS = re.compile(r'^FPS (\d+) ')
F_SLOW = re.compile(r'SLOW ([0-9.]+)ms(?: gap (\S+))?(?: off \S+)?(?: take \S+)?(?: copy (\S+))?')
F_JIT = re.compile(r'\| jit (\d+)')
J_LINE = re.compile(r'jitter ([0-9.]+)ms \S+ at (\d+)ms(?: gap (\S+))?(?: off \S+)?(?: take \S+)?(?: copy (\S+))?')
# mach_msg2 options of a run loop's receive: a thread waiting with these has nothing to do, anything else is a synchronous wait for somebody.
RUN_LOOP_OPTION = '0x507000806'
PAGE_BYTES = 4096
# Timed animations on a still screen, as (shortest, longest interval in ms, most frames a second): SpringBoard's clock icon at 10 Hz
# (+[SBHClockApplicationIconImageView initialize]), 20 Hz, a cursor blinking every 500 ms. On a still screen their intervals are no stall.
PERIODS = ((95, 112, 12), (45, 56, 24), (480, 520, 12))


def number(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def integer(text, default=None):
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return default


def pairs(words):
    """key=value words, and the others in order."""
    out, rest = {}, []
    for word in words:
        key, sep, value = word.partition('=')
        if sep:
            out[key] = value
        else:
            rest.append(word)
    return out, rest


class Thread:
    def __init__(self, tag):
        self.tag = tag
        self.pid = None
        self.name = '?'
        self.tid = self.state = self.pri = self.base = self.cpu = self.wait_ms = self.xcall = self.hint = self.event = None
        self.syscall = self.timeout = self.option = self.continuation = None
        self.kernel = []
        self.pc = self.lr = self.kpc = self.esr = self.far = self.end = None
        self.frames = []

    @property
    def run_loop(self):
        """Waiting for its next message in a run loop: idle, not stuck."""
        return self.state in ('W', 'U') and self.option == RUN_LOOP_OPTION

    @property
    def fault(self):
        """The thread sits in a page fault: its last exception from user space was an abort, in a wait."""
        return self.esr is not None and (self.esr >> 26) & 0x3f in (0x20, 0x24) and self.state in ('W', 'U')

    def wait_text(self):
        parts = []
        if self.state in ('W', 'U'):
            if self.fault:
                parts.append('缺页')
            if self.syscall is not None:
                parts.append(SYSCALLS.get(self.syscall, 'sc%d' % self.syscall))
            if self.hint:
                parts.append(BLOCK_HINTS.get(self.hint, 'hint%d' % self.hint))
            if self.timeout is not None:
                parts.append('超时%dms' % self.timeout)
        return ' '.join(parts)


class Cpu:
    def __init__(self, index, text):
        self.index = index
        self.text = text
        self.idle = text.startswith(('-', '~'))
        self.chosen = text.startswith('~')
        self.off = text in ('x', '?')
        parts = text.split('/')
        self.name = None if self.idle or self.off else parts[0]
        self.tid = parts[1] if len(parts) > 2 else None
        self.pri = integer(parts[2]) if len(parts) > 3 else None
        self.ms = integer(parts[-1]) if len(parts) > 1 else None


class Sample:
    def __init__(self, sample_id, seconds):
        self.id = sample_id
        self.seconds = seconds
        self.why = '?'
        self.stall = self.n = self.age = self.touch = self.gpu = self.frames_before = self.pid = self.name = self.open = self.cpu = None
        self.since = self.asked = self.focal = None
        self.threads = {}
        self.missing = []
        self.cpus = {}
        self.rq = self.rt = None

    @property
    def key(self):
        """One stall's samples share this."""
        return ('gap' if self.why.startswith('gap') else self.why, self.stall)


class Second:
    def __init__(self, seconds):
        self.seconds = seconds
        self.bands = {}
        self.idle = self.sent = self.recv = self.timers = self.ipi_max = self.hungry = self.stuck = None
        self.tick = self.images = None
        self.io = {}
        self.mem = {}
        self.fps = self.max_ms = self.slow_gap = self.slow_copy = self.touches = self.vsync_late = None
        self.touch_last = None


class Run:
    def __init__(self):
        self.samples = []
        self.seconds = []
        self.maps = {}
        self.images = {}
        self.frames = []
        self.jitters = []
        self.kbt_long = []
        self.kbt_offset = None              # mach ms - log ms

    def second_at(self, seconds):
        """The Second closest to `seconds`, at most 1.5 s away."""
        best = None
        for second in self.seconds:
            if abs(second.seconds - seconds) <= 1.5 and (best is None or abs(second.seconds - seconds) < abs(best.seconds - seconds)):
                best = second
        return best


def periodic(frame):
    """An F record's second holds a timed animation and nothing else: a still screen, what its intervals say about stalls is nothing."""
    if frame.get('touches') != 0 or not frame.get('fps'):
        return False
    return any(low <= frame.get('max', 0) <= high and frame['fps'] <= most for low, high, most in PERIODS)


def parse_lat(second, words):
    """fg n/max/over10/over50/over200 [Name[pid]/tid/pri] def ... bg ... k n/max/... [tid/pri]"""
    band = None
    for word in words:
        if word in ('fg', 'def', 'bg', 'k'):
            band = second.bands.setdefault(word, {})
        elif band is None:
            continue
        elif 'n' not in band:
            values = word.split('/')
            if len(values) == 5:
                band['n'] = integer(values[0], 0)
                band['max'] = number(values[1], 0.0)
                band['over10'], band['over50'], band['over200'] = [integer(v, 0) for v in values[2:]]
        else:
            band['who'] = word


def int_list(text):
    return [integer(v, 0) for v in text.split(',')]


def float_list(text):
    return [number(v, 0.0) for v in text.split(',')]


def parse_meter(run, seconds, text):
    words = text.split()
    if not words:
        return
    tag = words[0]
    last = run.seconds[-1] if run.seconds else None
    if tag == 'lat' or last is None or seconds - last.seconds > 0.5:
        last = Second(seconds)
        run.seconds.append(last)
    if tag == 'lat':
        parse_lat(last, words[1:])
    elif tag == 'cpu':
        for key, value in zip(words[1::2], words[2::2]):
            if key == 'idle':
                last.idle = int_list(value)
            elif key == 'ipis':
                last.sent = int_list(value)
            elif key == 'ipir':
                last.recv = int_list(value)
            elif key == 'tmr':
                last.timers = int_list(value)
    elif tag == 'ipi':
        for key, value in zip(words[1::2], words[2::2]):
            if key == 'max':
                last.ipi_max = float_list(value)
            elif key == 'idleq':
                last.hungry = int_list(value)
            elif key == 'stuck':
                last.stuck = int_list(value)
            elif key == 'tick':
                last.tick = number(value)
            elif key == 'img':
                last.images = integer(value)
    elif tag == 'io' and len(words) >= 3:
        last.io['reads'] = integer(words[2], 0)
        last.io['read_ms'] = number(words[3], 0.0)
        for key, value in zip(words[4::2], words[5::2]):
            if key == 'vfy':
                last.io['verify_ms'] = number(value, 0.0)
            elif key in ('q', 'kq', 'tq'):
                last.io[key] = value
            else:
                last.io[key] = integer(value, 0)
    elif tag == 'mem':
        for key, value in zip(words[1::2], words[2::2]):
            if key == 'cmp':
                last.mem['cmp'], _, held = value.partition('/')
                last.mem['cmp'], last.mem['held'] = integer(last.mem['cmp'], 0), integer(held, 0)
            elif key == 'gpu':
                used, _, peak = value.partition('/')
                last.mem['gpu'], last.mem['gpu_peak'] = integer(used, 0), integer(peak, 0)
            else:
                last.mem[key] = integer(value, 0)


def parse_thread(sample, words):
    tag = words[0]
    thread = sample.threads.setdefault(tag, Thread(tag))
    f, rest = pairs(words[1:])
    thread.pid = integer(f.get('pid'))
    thread.name = rest[0] if rest else '?'
    thread.tid = f.get('tid')
    thread.state = f.get('st')
    pri = (f.get('pri') or '').split('/')
    thread.pri = integer(pri[0])
    thread.base = integer(pri[1]) if len(pri) > 1 else None
    thread.cpu = integer(f.get('cpu'))
    thread.wait_ms = number(f.get('wt'))
    thread.xcall = f.get('x')
    thread.hint = integer(f.get('hint'))
    thread.event = f.get('ev')
    thread.syscall = integer(f.get('sc'))
    thread.timeout = integer(f.get('to'))
    thread.option = f.get('o')
    thread.continuation = f.get('cont')
    thread.kernel = (f.get('k') or '').split(',') if f.get('k') else []


def parse_bt(sample, words):
    tag = words[0]
    thread = sample.threads.setdefault(tag, Thread(tag))
    f, rest = pairs(words[1:])
    if thread.pid is None:
        thread.pid = integer(f.get('pid'))
    thread.pc = f.get('pc')
    thread.lr = f.get('lr')
    thread.kpc = f.get('kpc')
    thread.esr = integer(f.get('esr'))
    thread.far = f.get('far')
    thread.end = f.get('end')
    thread.frames = rest


def parse_cpus(sample, words):
    for word in words:
        if word.startswith('rq='):
            sample.rq = max(sample.rq or 0, integer(word[3:], 0))
        elif word.startswith('rt='):
            sample.rt = max(sample.rt or 0, integer(word[3:], 0))
        else:
            index, sep, text = word.partition(':')
            if sep and index.isdigit():
                sample.cpus[int(index)] = Cpu(int(index), text)


def parse_sample(run, by_id, seconds, text):
    m = HEAD.match(text)
    if not m:
        return
    sample_id, words = int(m.group(1)), m.group(2).split()
    if not words:
        return
    sample = by_id.get(sample_id)
    if sample is None:
        sample = by_id[sample_id] = Sample(sample_id, seconds)
        run.samples.append(sample)
    first = words[0]
    if first.startswith('why='):
        f, rest = pairs(words)
        sample.why = f['why']
        sample.stall = integer(f.get('st'))
        sample.n = integer(f.get('n'))
        sample.age = integer(f.get('age'))
        sample.touch = integer(f.get('tch'))
        sample.gpu = integer(f.get('gpu'))
        sample.frames_before = integer(f.get('fb'))
        sample.since = integer(f.get('since'))
        sample.asked = integer(f.get('ask'))
        sample.focal = integer(f.get('fg'))
        sample.pid = integer(f.get('pid'))
        sample.name = rest[0] if rest else None
        sample.open = integer(f.get('open'))
        sample.cpu = integer(f.get('cpu'))
    elif first == 'th' and len(words) > 1:
        parse_thread(sample, words[1:])
    elif first == 'bt' and len(words) > 1:
        parse_bt(sample, words[1:])
    elif first == 'cpu':
        parse_cpus(sample, words[1:])
    elif first == 'no' and len(words) > 1:
        sample.missing.append(words[1])


def parse_frame(run, seconds, text):
    m = F_FPS.match(text)
    if not m:
        return
    frame = {'seconds': seconds, 'fps': int(m.group(1))}
    found = F_MAX.search(text)
    frame['max'] = number(found.group(1), 0.0) if found else 0.0
    found = F_JIT.search(text)
    frame['jit'] = int(found.group(1)) if found else 0
    slow = F_SLOW.search(text)
    if slow:
        frame['slow'] = number(slow.group(1))
        frame['gap'] = number(slow.group(2))
        frame['copy'] = number(slow.group(3))
    tail = F_TAIL.search(text)
    if tail:
        frame['touches'] = int(tail.group(1))
        frame['last'] = integer(tail.group(2))
        frame['vsync_late'] = int(tail.group(3))
    run.frames.append(frame)


def join_frames(run):
    for frame in run.frames:
        second = run.second_at(frame['seconds'])
        if second is None or abs(second.seconds - frame['seconds']) > 0.5:
            continue
        second.fps = frame['fps']
        second.max_ms = frame['max']
        second.slow_gap = frame.get('gap')
        second.slow_copy = frame.get('copy')
        second.touches = frame.get('touches')
        second.touch_last = frame.get('last')
        second.vsync_late = frame.get('vsync_late')


def parse(records):
    run = Run()
    by_id = {}
    offsets = []
    for seq, seconds, kind, verified, text in records:
        if kind == 'Q':
            if text.startswith('map pid='):
                f, rest = pairs(text.split()[1:])
                pid = integer(f.get('pid'))
                if pid is not None:
                    run.maps[pid] = {'name': rest[0] if rest else '?', 'slide': integer(f.get('slide'), 0), 'main': integer(f.get('main'), 0)}
            elif text.startswith('img pid='):
                f, rest = pairs(text.split()[1:])
                pid = integer(f.get('pid'))
                start, end = integer(f.get('start')), integer(f.get('end'))
                if pid is not None and start is not None and end is not None:
                    run.images.setdefault(pid, []).append({'start': start, 'end': end, 'offset': integer(f.get('off'), 0),
                                                           'name': rest[0] if rest else '?'})
            else:
                parse_sample(run, by_id, seconds, text)
        elif kind == 'M':
            parse_meter(run, seconds, text)
        elif kind == 'F':
            parse_frame(run, seconds, text)
        elif kind == 'J':
            m = J_LINE.search(text)
            if m:
                run.jitters.append({'seconds': int(m.group(2)) / 1000.0, 'interval': number(m.group(1)), 'gap': number(m.group(3)),
                                    'copy': number(m.group(4))})
        elif kind == 'U' and text.startswith('kbt '):
            m = LONG.match(text)
            if m:
                run.kbt_long.append({'name': m.group(1), 'pid': int(m.group(2)), 'group': m.group(3), 'begin': int(m.group(4)),
                                     'end': int(m.group(5)), 'ms': int(m.group(6)), 'seconds': seconds})
            first = FIRST.match(text)
            if first:
                offsets.append(int(first.group(1)) - seconds * 1000.0)
    if offsets:
        offsets.sort()
        run.kbt_offset = offsets[len(offsets) // 2]
    join_frames(run)
    return run
