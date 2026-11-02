#!/usr/bin/env python3
"""Read-only aggregation of every gpu-test-loop run (merged.log, summary.txt, device.txt).

Prints, per run (newest first): key boot args, identity/overlay lines, last GPU rows, FPS/jitter stats, heartbeat CPU MHz,
audio / battery / cpufreq / keyboard / staged-apps / diag lines, AKS selector failures, launches per process and every
non-zero exit; then a cross-run table of process exits (signal / jetsam / other) and of launch loops.

usage: aggregate_runs.py RUNS_DIR [--json OUT.json]
"""
import collections
import json
import re
import sys
from pathlib import Path

LINE = re.compile(r'^\s*(\d+)\s+(\d+\.\d+)\s(\S)([ ?]) ?(.*)$')
EXIT = re.compile(r'^exit pid=(-?\d+) (.*?) status=([0-9a-f]+) ns=(\d+) code=([0-9a-f]+)$')
EXEC = re.compile(r'^pid=(-?\d+) (.+?) exec process signature accepted$')
EXEC_ENTER = re.compile(r'^pid=(-?\d+) (.+?) exec load_machfile enter$')
AKS = re.compile(r'^stage7 value=0x([0-9a-f]+): key operation unavailable$')
SIG = {1: 'SIGHUP', 2: 'SIGINT', 3: 'SIGQUIT', 4: 'SIGILL', 5: 'SIGTRAP', 6: 'SIGABRT', 7: 'SIGEMT', 8: 'SIGFPE',
       9: 'SIGKILL', 10: 'SIGBUS', 11: 'SIGSEGV', 12: 'SIGSYS', 13: 'SIGPIPE', 14: 'SIGALRM', 15: 'SIGTERM'}
NS = {0: 'none', 1: 'JETSAM', 2: 'SIGNAL', 3: 'CODESIGNING', 4: 'HANGTRACER', 5: 'TEST', 6: 'DYLD', 7: 'LIBXPC', 8: 'OBJC',
      9: 'EXEC', 10: 'SPRINGBOARD', 11: 'TCC', 12: 'REPORTCRASH', 13: 'COREANIMATION', 14: 'AGGREGATED', 15: 'RUNNINGBOARD',
      16: 'SKYWALK', 17: 'SETTINGS', 18: 'LIBSYSTEM', 19: 'FOUNDATION', 20: 'WATCHDOG', 21: 'METAL', 22: 'WATCHKIT', 23: 'GUARD',
      24: 'ANALYTICS', 25: 'SANDBOX', 26: 'SECURITY'}
JETSAM = {1: 'generic', 2: 'highwater', 3: 'vnode', 4: 'vm-page-shortage', 5: 'proc-thrashing', 6: 'fc-thrashing',
          7: 'per-process-limit', 8: 'disk-space', 9: 'idle-exit', 10: 'zone-map', 11: 'compressor-thrashing',
          12: 'compressor-space', 13: 'lowswap', 14: 'sustained-pressure', 15: 'vm-pageout-starvation'}
KEYS = ('pagani-', 'sheng-staged', 'sheng-no-lc', 'sheng-fb-gpu')
TOPICS = {
    'audio': re.compile(r'audio'),
    'battery': re.compile(r'battery|gauge|battmgr|charger'),
    'cpufreq': re.compile(r'cpufreq'),
    'keyboard': re.compile(r'^kbt |kbd|keyboard'),
    'input': re.compile(r'input script|touch|button|key '),
    'apps': re.compile(r'staged apps|app moves|app move |app state|system apps view|migrat'),
    'diag': re.compile(r'^diag: '),
    'wdog': re.compile(r'wdog'),
    'gpu-meter': re.compile(r'gpu meter|gpu aperture'),
    'overlay': re.compile(r'overlay|identity:'),
    'adsp': re.compile(r'adsp|glink|smp2p|qrtr|pas '),
    'power': re.compile(r'screenoff|idle|slumber|panel'),
}


def parse_exit(text):
    m = EXIT.match(text)
    if not m:
        return None
    pid, name, status, ns, code = int(m.group(1)), m.group(2), int(m.group(3), 16), int(m.group(4)), int(m.group(5), 16)
    if ns == 1:
        why = 'jetsam:' + JETSAM.get(code, str(code))
    elif ns == 2:
        why = 'signal:' + SIG.get(code, str(code))
    elif ns == 10 and code == 0xdeadfa11:
        why = 'springboard:user-force-quit(deadfa11)'
    elif status == 0:
        why = 'ok'
    elif status & 0x7f and status < 0x80 and status in SIG:
        why = 'signal:' + SIG[status]
    else:
        why = 'status=%x ns=%s(%d) code=%x' % (status, NS.get(ns, '?'), ns, code)
    if ns == 0 and status in SIG:
        why = 'signal:' + SIG[status]
    if ns == 0 and status >= 0x100:
        why = 'exit(%d)' % (status >> 8)
    return pid, name, status, ns, code, why


def run_facts(d: Path):
    out = {'run': d.name}
    s = (d / 'summary.txt')
    if s.exists():
        first = s.read_text(errors='replace').splitlines()[:1]
        out['summary'] = first[0] if first else ''
    lines = []
    ml = d / 'merged.log'
    if ml.exists():
        for raw in ml.read_text(errors='replace').splitlines():
            m = LINE.match(raw)
            if m:
                lines.append((int(m.group(1)), float(m.group(2)), m.group(3), m.group(4) == ' ', m.group(5)))
    out['records'] = len(lines)
    if not lines:
        dev = d / 'device.txt'
        if dev.exists():
            for l in dev.read_text(errors='replace').splitlines():
                if l.startswith(('pmic_info', 'uptime', 'sys.boot.reason:')) or 'found existing invalid buffer' in l:
                    out.setdefault('device', []).append(l[:200])
        return out
    args = [t for (_, _, k, _, t) in lines if k == 'A']
    if args:
        out['args'] = ' '.join(a for a in args[0].split() if a.startswith(KEYS))
    ups = [t for (_, _, k, _, t) in lines if k == 'H']
    if ups:
        out['last_heartbeat'] = ups[-1]
        mhz = re.findall(r'cpu (\d+)MHz', ' '.join(ups))
        out['hb_cpu_mhz'] = sorted(set(int(x) for x in mhz))
    g = [(sec, t) for (_, sec, k, _, t) in lines if k == 'G']
    out['gpu_rows_last'] = [t for (_, t) in g[-6:]]
    f = [t for (_, _, k, _, t) in lines if k == 'F']
    fps = [int(m.group(1)) for t in f for m in [re.match(r'FPS (\d+)', t)] if m]
    if fps:
        out['fps'] = {'n': len(fps), 'min': min(fps), 'max': max(fps), 'median': sorted(fps)[len(fps) // 2]}
    j = [t for (_, _, k, _, t) in lines if k == 'J']
    jv = [float(m.group(1)) for t in j for m in [re.match(r'jitter ([\d.]+)ms', t)] if m]
    if jv:
        out['jitter'] = {'n': len(jv), 'max_ms': max(jv), 'over_250ms': sum(1 for x in jv if x >= 250)}
    topics = collections.defaultdict(list)
    aks = collections.Counter()
    launches = collections.Counter()
    exits = []
    xcrash = []
    display_clients = 0
    for (seq, sec, k, ok, t) in lines:
        if k == 'U':
            if re.match(r'g[0-9a-f]+(\.[0-9a-f]+)? ', t) or t.startswith('g done'):
                continue
            for name, rx in TOPICS.items():
                if rx.search(t):
                    topics[name].append('%8.3f %s' % (sec, t))
                    break
            else:
                topics['other'].append('%8.3f %s' % (sec, t))
        elif k == 'R':
            m = AKS.match(t)
            if m:
                aks[int(m.group(1), 16)] += 1
            elif 'display client enter' in t:
                display_clients += 1
        elif k == 'S':
            m = EXEC.match(t)
            if m and m.group(2) != 'xpcproxy':
                launches[m.group(2)] += 1
            e = parse_exit(t)
            if e:
                exits.append((sec,) + e)
        elif k == 'X':
            if t.startswith('crash '):
                xcrash.append('%8.3f %s' % (sec, t))
        elif k in ('C',):
            topics['console'].append('%8.3f %s' % (sec, t))
    out['topics'] = {k: v for k, v in topics.items()}
    out['aks_fail'] = {('0x%x' % k): v for k, v in sorted(aks.items())}
    out['display_client_enter'] = display_clients
    out['launches'] = dict(launches.most_common())
    out['exits_bad'] = [{'t': e[0], 'pid': e[1], 'name': e[2], 'status': '%x' % e[3], 'ns': e[4], 'code': e[5],
                         'why': e[6]} for e in exits if e[6] != 'ok']
    out['exits_ok'] = collections.Counter(e[2] for e in exits if e[6] == 'ok')
    out['crash_x'] = xcrash
    return out


def main():
    root = Path(sys.argv[1])
    js = None
    if '--json' in sys.argv:
        js = Path(sys.argv[sys.argv.index('--json') + 1])
    runs = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    facts = [run_facts(p) for p in runs]
    agg = collections.defaultdict(lambda: collections.Counter())
    agg_runs = collections.defaultdict(set)
    loops = collections.defaultdict(dict)
    for f in facts:
        for e in f.get('exits_bad', []):
            agg[e['name']][e['why']] += 1
            agg_runs[e['name']].add(f['run'][:15] + ' ' + f['run'][16:])
        for name, n in f.get('launches', {}).items():
            if n >= 4:
                loops[name][f['run']] = n
    for f in facts:
        print('=' * 100)
        print(f['run'], '|', f.get('summary', ''), '| records', f['records'])
        for k in ('args', 'last_heartbeat', 'hb_cpu_mhz', 'fps', 'jitter', 'aks_fail', 'display_client_enter'):
            if k in f:
                print('  %-20s %s' % (k, f[k]))
        for l in f.get('device', []):
            print('  device:', l)
        for t in f.get('gpu_rows_last', []):
            print('  G', t[:200])
        for name, rows in f.get('topics', {}).items():
            uniq = []
            seen = set()
            for r in rows:
                key = re.sub(r'^\s*[\d.]+ ', '', r)
                key = re.sub(r'\d+', '#', key)
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            print('  [%s] %d lines, %d distinct' % (name, len(rows), len(uniq)))
            for r in uniq[:25]:
                print('     ', r[:260])
        if f.get('crash_x'):
            print('  [X crash]')
            for r in f['crash_x']:
                print('     ', r[:200])
        if f.get('exits_bad'):
            c = collections.Counter((e['name'], e['why']) for e in f['exits_bad'])
            print('  [bad exits]', ', '.join('%s %s x%d' % (n, w, k) for (n, w), k in c.most_common()))
        top = sorted(f.get('launches', {}).items(), key=lambda kv: -kv[1])[:12]
        if top:
            print('  [launches top]', ', '.join('%s x%d' % kv for kv in top))
    print('=' * 100)
    print('CROSS-RUN BAD EXITS (process: reason x count; runs)')
    for name, c in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
        print('  %-22s %-60s runs=%d %s' % (name, ', '.join('%s x%d' % kv for kv in c.most_common()), len(agg_runs[name]),
                                             ' '.join(sorted(agg_runs[name], reverse=True)[:6])))
    print('LAUNCH LOOPS (>=4 launches in a run)')
    for name, d in sorted(loops.items(), key=lambda kv: -max(kv[1].values())):
        print('  %-24s %s' % (name, ', '.join('%s:%d' % (k[:15], v) for k, v in sorted(d.items(), reverse=True)[:8])))
    if js:
        for f in facts:
            f['exits_ok'] = dict(f.get('exits_ok', {}))
        js.write_text(json.dumps(facts, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
