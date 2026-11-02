#!/usr/bin/env python3
"""把一轮日志里的调度延迟、Q 采样、M、F、kbt 排成时间线，给出最严重的若干次卡顿各自的结论表。

    stall-report.py runs/<目录> [--a2s dsc.a2s] [--dsc 缓存] [--root 目录] [--image 名=路径] [--kernel kernel.macho]
                    [--top 20] [--timeline] [--json 文件]

记录含义见 UniXNU-Pagani-Drivers/drivers/BootProgress/StallProbe.h。一次“卡顿”按下面来源汇总：
  - gap：同一 st= 的 Q 采样（why=gap110/200/400/1000/3000/8000，旧日志首阈值是 gap100），时长取采样 age；卡顿从最新一帧算起时（since 与 age 相近），再和
    F 行这一秒的最大帧间隔取较大者。全部采样都是 ask=0 时类型写成 gap?：只有帧前节奏，可能只是动画正常结束（ask=1 只有 GPU 提交，ask=2 触摸或键盘）；
  - dn/up/in：同一 st= 的 Q 采样，时长取 kbt long 记录（有的话）和最后一次采样的 age；
  - idleq/ipi：看门狗发现 CPU 空闲却有排队线程，或 IPI 悬而未决；
  - 未采样：F 行里 max>=200 ms、kbt long 里有、但没有对应 Q 采样（被限流或判为不活跃）。
周期动画秒（无触摸，节奏是桌面时钟 10 Hz、20 Hz 或光标 500 ms，见 stall_format.PERIODS）不算卡顿：其中的 gap? 和 gap* 不进表，总览里单列秒数和 jit 占比。
idleq 只是点采样：一个在途线程会让每个空闲核同时计数，单次几十微秒的正常排队被约 11 ms 周期的采样撞上就算数。所以“丢唤醒”的结论只看 IPI 悬而未决；
idleq 和超过两拍的调度延迟落在同一秒时只列出这些秒待查。
退出码 0；日志里没有 M/Q 记录时为 3。
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stall_format  # noqa: E402

spec = importlib.util.spec_from_file_location('symbolize_crashes', HERE / 'symbolize-crashes.py')
symbolize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(symbolize)

COLUMNS = (
    ('序号', '按时长从大到小'),
    ('时刻', '卡顿开始的日志秒（第一次采样减 age）'),
    ('类型', 'gap=有东西在等新帧而帧没来；gap?=只有帧前节奏，可能只是动画结束；dn/up/in=键盘配对未结束；idleq=CPU 空闲却有排队线程（点采样）；ipi=IPI 悬而未决；后缀 * 表示没有 Q 采样'),
    ('时长', '毫秒'),
    ('样本', '这次卡顿的 Q 采样数'),
    ('主线程', 'rs（backboardd 渲染线程）或 app（App 主线程）每次采样的状态：C 在 CPU 上，R 可运行未上 CPU，W 等待，U 不可中断等待，T 退出中，- 没读到'),
    ('副线程', 'sb（SpringBoard 主线程）或 kbd（kbd 最忙线程）的状态，同上'),
    ('前台', 'gap 采样里屏幕上的 App（focal 角色）和它主线程每次采样的状态；“主屏”=没有这样的 App，- 这次没读到；旧日志为空'),
    ('等待', '主线程最长一次等待的内容：系统调用、阻塞类型、超时参数（ms），“缺页”表示卡在缺页'),
    ('栈', '主线程用户栈第一个可命名的帧，没有符号时是地址记号'),
    ('调度', '这一秒前台带（优先级>=47）最大调度延迟 ms / 超过 50 ms 的次数，全部用户带超过 50 ms 的次数，内核线程最大调度延迟 ms（旧日志是 -）'),
    ('CPU', '采样时：忙核数/空闲核数/运行队列长度'),
    ('IPI', '这一秒：最慢 IPI ms / 空闲却有排队的 tick 数 / 悬而未决的 IPI 数；后两项是 - 表示这一秒采样器睡着，没人看过'),
    ('IO', '这一秒根卷读：次数/耗时 ms'),
    ('VM', '这一秒：缺页/换入/解压/vm_page_wait 睡眠次数/最小 free（采样器睡着的秒 free 是观察那一刻的值，不是最小值）'),
    ('判定', '由上面各列推出的直接原因，“？”表示数据不足'),
)


USER_BANDS = ('fg', 'def', 'bg')
IDLEQ_LATE_MS = 20      # 两拍采样：idleq 至少连续两拍看见队列非空，同一个线程真被晾着时调度延迟不会比这短
PERIOD_MS = max(period[1] for period in stall_format.PERIODS)


def state_of(sample, tag):
    thread = sample.threads.get(tag)
    return thread.state if thread and thread.state else '-'


def primary_tags(samples):
    tags = [tag for sample in samples for tag in sample.threads]
    if 'app' in tags or any(sample.why in ('dn', 'up', 'in') for sample in samples):
        return 'app', 'kbd'
    return 'rs', 'sb'


class Stall:
    def __init__(self, kind, start, ms, samples=()):
        self.kind = kind
        self.start = start
        self.ms = ms
        self.samples = list(samples)
        self.who = None
        self.tail = False


def in_periodic(run, seconds):
    """`seconds` 落在周期动画秒里：F 行记的是它之前的一秒。"""
    return any(stall_format.periodic(frame) and 0 <= frame['seconds'] - seconds <= 1.1 for frame in run.frames)


def gather(run):
    stalls = {}
    for sample in run.samples:
        stalls.setdefault(sample.key, []).append(sample)
    out = []
    for key, samples in stalls.items():
        first, last = samples[0], samples[-1]
        start = first.seconds - (first.age or 0) / 1000.0
        stall = Stall(key[0], start, float(last.age or 0), samples)
        stall.who = '%s[%s]' % (first.name, first.pid) if first.pid else None
        if key[0] == 'gap' and all(sample.asked == 0 for sample in samples):
            stall.tail = True
        # A stall that began later than the frame (a tap on a screen long idle): F's largest interval is mostly that idle time.
        if key[0] == 'gap' and (first.since is None or first.since - (first.age or 0) <= 100):
            for frame in run.frames:
                if first.seconds - 0.5 <= frame['seconds'] <= last.seconds + 1.5 and frame.get('max', 0) > stall.ms:
                    stall.ms = frame['max']
        elif key[0] in ('dn', 'up', 'in') and run.kbt_offset is not None:
            for pair in run.kbt_long:
                begin = (pair['begin'] - run.kbt_offset) / 1000.0
                if pair['group'] == key[0] and pair['pid'] == first.pid and abs(begin - start) < 1.0:
                    stall.ms = max(stall.ms, float(pair['ms']))
                    start = begin
                    stall.start = begin
        # 静止屏幕上一拍动画的长度：没有触摸或键盘在等（ask=2），间隔也没有超过最长的周期，这不是卡顿。
        if key[0] == 'gap' and all((sample.asked or 0) < 2 for sample in samples) and stall.ms <= PERIOD_MS and in_periodic(run, first.seconds):
            continue
        out.append(stall)
    covered = [(s.start - 0.2, s.start + s.ms / 1000.0 + 1.2) for s in out if s.kind == 'gap']
    for frame in run.frames:
        if frame.get('max', 0) >= 200 and not stall_format.periodic(frame) and not any(a <= frame['seconds'] <= b for a, b in covered):
            out.append(Stall('gap*', frame['seconds'] - 1.0, frame['max']))
    for pair in run.kbt_long:
        begin = (pair['begin'] - run.kbt_offset) / 1000.0 if run.kbt_offset is not None else pair['seconds'] - pair['ms'] / 1000.0
        if not any(s.kind == pair['group'] and abs(s.start - begin) < 1.0 for s in out):
            stall = Stall(pair['group'] + '*', begin, float(pair['ms']))
            stall.who = '%s[%s]' % (pair['name'], pair['pid'])
            out.append(stall)
    out.sort(key=lambda s: -s.ms)
    return out


def first_named(thread, named, namer):
    tokens = ([named.regs['pc']] if named.regs.get('pc') else []) + list(named.frames)
    fallback = tokens[0] if tokens else ''
    for index, token in enumerate(tokens):
        image, symbol, _ = namer.name(named, token, index > 0)
        if symbol and image not in ('libsystem_kernel.dylib', 'libsystem_pthread.dylib'):
            return '%s %s' % (image, symbol)
    for index, token in enumerate(tokens):
        image, symbol, _ = namer.name(named, token, index > 0)
        if symbol:
            return symbol
    return fallback


def app_note(stall):
    """前台 App 主线程在这次卡顿里干什么：分清“屏幕本来静止”和“App 主线程堵住了”。"""
    threads = [sample.threads['fg'] for sample in stall.samples if 'fg' in sample.threads]
    if not threads:
        return None
    counts = {letter: sum(1 for t in threads if t.state == letter) for letter in 'CR'}
    if counts['C'] * 2 >= len(threads):
        return '前台 App 主线程在 CPU 上忙（%s）' % ('内核态' if sum(1 for t in threads if t.kpc) * 2 >= counts['C'] else '用户态')
    if counts['R'] * 2 >= len(threads):
        return '前台 App 主线程可运行未上 CPU'
    waiting = [t for t in threads if t.state in ('W', 'U')]
    if not waiting:
        return '前台 App 主线程状态不一'
    if all(t.run_loop for t in waiting):
        return '前台 App 主线程在运行循环空等：屏幕本来静止'
    blocked = max((t for t in waiting if not t.run_loop), key=lambda t: t.wait_ms or 0)
    return '前台 App 主线程阻塞：%s' % ('卡在缺页' if blocked.fault else blocked.wait_text() or '类型未知')


def judge(stall, primary, second):
    threads = [sample.threads[primary] for sample in stall.samples if primary in sample.threads]
    if not stall.samples:
        return '？没有采样'
    if stall.kind == 'ipi':
        return '调度看门狗触发：IPI 悬而未决'
    if stall.kind == 'idleq':
        # 空闲核刚被选中（~）或刚进 idle（0 ms）：线程正在路上，IPI 在途，不是没人叫它。
        moving = any(cpu.chosen or (cpu.idle and not cpu.ms) for sample in stall.samples for cpu in sample.cpus.values())
        return '调度看门狗触发：CPU 空闲但运行队列非空（%s）' % ('当场有核刚被选中或刚进 idle，是正常在途的点采样' if moving else '点采样，看同一秒的调度延迟')
    if not threads:
        return '？没读到目标线程'
    total = len(threads)
    counts = {letter: sum(1 for t in threads if t.state == letter) for letter in 'CRWU'}
    notes = []
    app = app_note(stall)
    if app:
        notes.append(app)
    if second and second.io.get('read_ms', 0) >= 100:
        notes.append('根卷读 %.0f ms' % second.io['read_ms'])
    if counts['R'] * 2 >= total:
        idle = [s for s in stall.samples if primary in s.threads and s.threads[primary].state == 'R' and
                any(c.idle for c in s.cpus.values())]
        head = '可运行却没上 CPU，同时有空闲核：调度或唤醒问题' if idle else '可运行但各核都忙：CPU 竞争'
    elif counts['C'] * 2 >= total:
        kernel = sum(1 for t in threads if t.kpc)
        head = '在 CPU 上忙（%s）' % ('内核态' if kernel * 2 >= counts['C'] else '用户态')
    else:
        waiting = [t for t in threads if t.state in ('W', 'U')]
        longest = max(waiting, key=lambda t: t.wait_ms or 0) if waiting else None
        if longest is None:
            head = '？状态不一'
        elif longest.fault:
            head = '卡在缺页'
        else:
            head = '等待：%s' % (longest.wait_text() or '类型未知')
    return '；'.join([head] + notes)


def foreground(stall):
    gaps = [sample for sample in stall.samples if sample.focal is not None]
    if not gaps:
        return ''
    named = next((sample.threads['fg'] for sample in gaps if 'fg' in sample.threads), None)
    if named is None:
        return '主屏' if all(sample.focal == 0 for sample in gaps) else '-'
    return '%s[%s] %s' % (named.name, named.pid, ' '.join(state_of(sample, 'fg') for sample in gaps))


def row(index, stall, run, threads, namer):
    primary, secondary = primary_tags(stall.samples)
    first = stall.samples[0] if stall.samples else None
    second = run.second_at(stall.start + stall.ms / 2000.0)
    middle = stall.samples[len(stall.samples) // 2] if stall.samples else None
    waits, stack = '', ''
    chosen = None
    for sample in stall.samples:
        thread = sample.threads.get(primary)
        if thread and (chosen is None or (thread.wait_ms or 0) > (chosen[1].wait_ms or 0)):
            chosen = (sample, thread)
    if chosen:
        waits = chosen[1].wait_text()
        stack = first_named(chosen[1], threads[(chosen[0].id, primary)], namer)
    cell = {
        '序号': str(index), '时刻': '%.3f' % stall.start, '类型': stall.kind + ('?' if stall.tail else '') + (' %s' % stall.who if stall.who else ''), '时长': '%.0f' % stall.ms,
        '样本': str(len(stall.samples)),
        '主线程': '%s %s' % (primary, ' '.join(state_of(s, primary) for s in stall.samples)) if stall.samples else '',
        '副线程': '%s %s' % (secondary, ' '.join(state_of(s, secondary) for s in stall.samples)) if stall.samples else '',
        '前台': foreground(stall),
        '等待': waits, '栈': stack, '调度': '', 'CPU': '', 'IPI': '', 'IO': '', 'VM': '',
        '判定': judge(stall, primary, second),
    }
    if middle and middle.cpus:
        busy = sum(1 for c in middle.cpus.values() if c.name)
        idle = sum(1 for c in middle.cpus.values() if c.idle)
        cell['CPU'] = '%d/%d/%s' % (busy, idle, middle.rq)
    if second:
        fg = second.bands.get('fg', {})
        over = sum(second.bands.get(band, {}).get('over50', 0) for band in USER_BANDS)
        cell['调度'] = '%s/%s %s %s' % (fg.get('max', '-'), fg.get('over50', 0), over, second.bands.get('k', {}).get('max', '-'))
        if second.ipi_max:
            # 采样器睡着的秒（M 记录的尾巴）idleq、stuck、tick 都是 0，那是没看，不是没有。
            # 只跑一拍的秒 tick 也是 0，但第一拍就可能记到 stuck，有数就照实显示。
            seen = second.tick or any(second.hungry or []) or any(second.stuck or [])
            watched = '%d/%d' % (sum(second.hungry or []), sum(second.stuck or [])) if seen else '-/-'
            cell['IPI'] = '%.1f/%s' % (max(second.ipi_max), watched)
        if second.io:
            cell['IO'] = '%s/%s' % (second.io.get('reads', '-'), second.io.get('read_ms', '-'))
            cell['VM'] = '%s/%s/%s/%s/%s' % (second.io.get('flt', '-'), second.io.get('pin', '-'), second.io.get('dcmp', '-'), second.io.get('pw', '-'),
                                             second.io.get('free', '-'))
    return cell


def wakeup_verdict(seconds, hungry, stuck):
    """“丢唤醒”只有 IPI 悬而未决才算证据；idleq 是点采样，和超过两拍的调度延迟落在同一秒才值得看，也只是待查。"""
    if stuck:
        return '本轮有证据支持丢唤醒或 IPI 投递问题：%d 个 IPI 悬而未决超过 20 ms' % stuck
    late = [s for s in seconds if sum(s.hungry or []) and max((b.get('max', 0.0) for b in s.bands.values()), default=0.0) > IDLEQ_LATE_MS]
    if late:
        return '没有直接证据（没有悬而未决的 IPI）；%d 秒同时有 idleq 和超过 %d ms 的调度延迟，要看这些秒是不是全核都忙：%s' % (
            len(late), IDLEQ_LATE_MS, ' '.join('%.0f' % s.seconds for s in late[:12]) + (' ...' if len(late) > 12 else ''))
    if hungry:
        return '本轮没有观察到丢唤醒：没有悬而未决的 IPI；idleq 所在的秒调度延迟都不超过 %d ms，是点采样撞上正常排队' % IDLEQ_LATE_MS
    return '本轮没有观察到丢唤醒：没有悬而未决的 IPI，也没有空闲核面对非空队列'


def memory_lines(seconds):
    """M mem 的账：开头、avail 最低、结尾各一行。"""
    rows = [s for s in seconds if s.mem]
    if not rows:
        return []

    def mib(second, key):
        return second.mem.get(key, 0) * stall_format.PAGE_BYTES / 1048576.0

    lowest = min(rows, key=lambda s: s.io.get('avail', 1 << 40))
    lines = ['  内存账 MiB（M mem，%d 行）：wire / 匿名 / 文件 / 压缩器驻留 / 被限流 / 可清除 / zone / 内存盘驻留 / GPU 池 峰值' % len(rows)]
    for label, second in (('开头', rows[0]), ('avail 最低', lowest), ('结尾', rows[-1])):
        lines.append('    %s %.1f s：%.0f / %.0f / %.0f / %.0f / %.0f / %.0f / %.0f / %.0f / %d %d' % (
            label, second.seconds, mib(second, 'wire'), mib(second, 'int'), mib(second, 'ext'), mib(second, 'cmp'), mib(second, 'thr'), mib(second, 'pur'),
            mib(second, 'zone'), second.mem.get('ram', 0) / 1048576.0, second.mem.get('gpu', 0), second.mem.get('gpu_peak', 0)))
    return lines


def overview(run, stalls):
    lines = ['总览']
    seconds = run.seconds
    lines.append('  M 秒数 %d，Q 采样 %d，kbt 长配对 %d，卡顿 %d 次' % (len(seconds), len(run.samples), len(run.kbt_long), len(stalls)))
    periodic = [frame for frame in run.frames if stall_format.periodic(frame)]
    if periodic:
        lines.append('  周期动画秒 %d 个（无触摸，10 Hz、20 Hz 或光标节奏），占 jit %d/%d，其中的 gap? 和 gap* 没有进表' % (
            len(periodic), sum(f['jit'] for f in periodic), sum(f['jit'] for f in run.frames)))
    if seconds:
        fg_max = max((s.bands.get('fg', {}).get('max', 0.0) for s in seconds), default=0.0)
        over = {k: sum(s.bands.get(band, {}).get(k, 0) for s in seconds for band in USER_BANDS) for k in ('over10', 'over50', 'over200')}
        lines.append('  调度延迟：前台带最大 %.1f ms；用户线程全部带超过 10/50/200 ms 共 %d/%d/%d 次' % (fg_max, over['over10'], over['over50'], over['over200']))
        kernel = [s.bands['k'] for s in seconds if 'k' in s.bands]
        if kernel:
            lines.append('  内核线程调度延迟：最大 %.1f ms；超过 10/50/200 ms 共 %d/%d/%d 次' % (
                max(b.get('max', 0.0) for b in kernel), sum(b.get('over10', 0) for b in kernel), sum(b.get('over50', 0) for b in kernel),
                sum(b.get('over200', 0) for b in kernel)))
        ipi_max = max((max(s.ipi_max) for s in seconds if s.ipi_max), default=0.0)
        hungry = sum(sum(s.hungry or []) for s in seconds)
        stuck = sum(sum(s.stuck or []) for s in seconds)
        tick = max((s.tick or 0.0 for s in seconds), default=0.0)
        lines.append('  IPI：最慢 %.1f ms，悬而未决 %d 次；空闲核面对非空队列 %d 个 tick（点采样）；采样器最长间隔 %.1f ms' % (ipi_max, stuck, hungry, tick))
        verdict = wakeup_verdict(seconds, hungry, stuck)
        watched = sum(1 for s in seconds if s.tick)
        lines.append('  “IPI 丢失或空闲核没被叫醒”的假设：%s（采样器只在屏幕更新、触摸或键盘配对未结束时运行，%d 个 M 秒里醒着 %d 个）' % (verdict, len(seconds), watched))
        limits = [s.io.get('q') for s in seconds if s.io.get('q')]
        keyboard = [s.io.get('kq') for s in seconds if s.io.get('kq')]
        tails = [s.io.get('tq') for s in seconds if s.io.get('tq')]
        if limits:
            lines.append('  Q 记录用量（行数/超速率/超预算）：gap 等 %s，键盘 %s，只有帧前节奏的 gap %s' % (
                limits[-1], keyboard[-1] if keyboard else '-', tails[-1] if tails else '-'))
        waits = [(s.io.get('pw', 0), s.seconds) for s in seconds if s.io.get('pw')]
        if waits:
            lines.append('  vm_page_wait 睡眠 %d 次，分布在 %d 秒，最多一秒 %d 次（%.1f s）' % (sum(w[0] for w in waits), len(waits), *max(waits)))
        lines.extend(memory_lines(seconds))
    return lines


def timeline(run):
    events = []
    for sample in run.samples:
        states = ' '.join('%s=%s' % (tag, t.state) for tag, t in sample.threads.items())
        events.append((sample.seconds, 'Q s=%d %s st=%s n=%s age=%s %s' % (sample.id, sample.why, sample.stall, sample.n, sample.age, states)))
    for second in run.seconds:
        fg = second.bands.get('fg', {})
        events.append((second.seconds, 'M fg max=%s over50=%s rd=%s fps=%s max=%s tch=%s vl=%s' % (
            fg.get('max', '-'), fg.get('over50', 0), second.io.get('read_ms', '-'), second.fps, second.max_ms, second.touches, second.vsync_late)))
        if second.mem:
            events.append((second.seconds, 'M mem %s' % ' '.join('%s=%s' % item for item in second.mem.items())))
    for jitter in run.jitters:
        events.append((jitter['seconds'], 'J %.1f ms gap %s copy %s' % (jitter['interval'], jitter['gap'], jitter['copy'])))
    for pair in run.kbt_long:
        events.append((pair['seconds'], 'kbt %s[%d] long %s %d ms' % (pair['name'], pair['pid'], pair['group'], pair['ms'])))
    events.sort(key=lambda e: e[0])
    return ['%10.3f  %s' % event for event in events]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('log', type=Path, help='merged.log 或包含它的运行目录')
    parser.add_argument('--a2s', type=Path)
    parser.add_argument('--dsc', type=Path)
    parser.add_argument('--root', type=Path, action='append', default=[])
    parser.add_argument('--image', action='append', default=[])
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--top', type=int, default=20)
    parser.add_argument('--timeline', action='store_true', help='再输出按时间排的全部事件')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    log = args.log / 'merged.log' if args.log.is_dir() else args.log
    run = stall_format.parse(symbolize.read_log(log))
    if not run.samples and not run.seconds:
        print('%s: 没有 M/Q 记录（这一轮不是带 pagani-diag 和 phone-stall-probe.patch 的内核）' % log)
        return 3
    threads = symbolize.stall_threads(run)
    a2s = symbolize.open_a2s(args.a2s) if args.a2s and args.a2s.is_file() else None
    images = symbolize.CacheImages(args.dsc) if args.dsc and args.dsc.is_file() else None
    files = symbolize.Files([image.split('=', 1) for image in args.image if '=' in image], args.root)
    namer = symbolize.Namer(a2s, args.dsc, images, None, files)
    namer.prepare({'stall-%d-%s' % key: thread for key, thread in threads.items()})
    stalls = gather(run)
    out = overview(run, stalls)
    out.append('')
    chosen = stalls[:args.top]
    out.append('最严重的 %d 次卡顿' % len(chosen))
    names = [name for name, _ in COLUMNS]
    rows = [row(i + 1, stall, run, threads, namer) for i, stall in enumerate(chosen)]
    out.append(' | '.join(names))
    out.extend(' | '.join(r[name] for name in names) for r in rows)
    out.append('')
    out.append('各列含义')
    out.extend('  %s：%s' % column for column in COLUMNS)
    if args.timeline:
        out.append('')
        out.append('时间线')
        out.extend(timeline(run))
    print('\n'.join(out))
    if args.json:
        args.json.write_text(json.dumps({'rows': rows, 'overview': overview(run, stalls)}, ensure_ascii=False, indent=1) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
