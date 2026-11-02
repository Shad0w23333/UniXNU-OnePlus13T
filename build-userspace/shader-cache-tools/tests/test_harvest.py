#!/usr/bin/env python3
"""Host test of harvest-a830.py (jitter F2): the commands of its steps, in --dry-run, and what its check reads from a run. No seed is made, no
image, the phone is not touched.

  python3 -I build-userspace/shader-cache-tools/tests/test_harvest.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1] / 'harvest-a830.py'
failures = 0


def check(ok, what):
    global failures
    if not ok:
        print('FAIL: ' + what)
        failures += 1


def step(*args):
    r = subprocess.run([sys.executable, '-B', str(TOOL), *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def main():
    # capture: the final seed, the export marker, the pack replaced, an image with the UFS log.
    code, out = step('capture', '--dry-run')
    lines = out.splitlines()
    check(code == 0, 'capture runs: ' + out[-300:])
    first = next((l for l in lines if 'make-shader-cache-a830.py' in l), '')
    for word in ('--with-cacheexport', '--replace-pack', '--force', 's7-final.dbrdisk', 's7-export.dbrdisk'):
        check(word in first, 'capture passes ' + word)
    check('--merge-export' not in first and '--pack-budget-mib' not in first, 'capture merges nothing')
    check(any('sheng-log=1' in l and 's7-export.dbrdisk' in l for l in lines), 'the capture image has the UFS log and the capture seed')
    check(any('s7-final-owner-trust.bundle' in l for l in lines), 'the marker is data: the final trust bundle stays')
    check(not any('s6-' in l for l in lines), 'the flows type: no seed before the keyboard fix')
    check(any('make-a830-layout.sh phone-island-apps' in l for l in lines), 'the image comes from the layout script')
    check(any('boot-A830-收割-导出.img' in l for l in lines), 'its name')

    with tempfile.TemporaryDirectory(prefix='harvest-') as work:
        work = Path(work)
        # merge: both runs in the order given, the budget, the journal of the first; a run without an export is refused.
        code, out = step('merge', '--dry-run', '--journal', '--budget-mib', '3.5', '--env-line', 'METAL2VULKAN_SURFACE_CLEAN=seed', str(work / 'a'), str(work / 'b'))
        first = next((l for l in out.splitlines() if 'make-shader-cache-a830.py' in l), '')
        check(code == 0, 'merge runs')
        check(first.find(str(work / 'a/ufs/export')) < first.find(str(work / 'b/ufs/export')) and '--merge-export' in first, 'the runs in the order given')
        check('--pack-budget-mib 3.5' in first and '--replace-pack' in first and 's7-harvest.dbrdisk' in first, 'budget, pack and output')
        check('s7-final.dbrdisk' in first and 's6-' not in first, 'the harvested seed keeps the keyboard fix')
        check('--journal %s' % (work / 'a/ufs/export/pipelines.log') in first, 'the journal of the first run')
        check('--env-line METAL2VULKAN_SURFACE_CLEAN=seed' in first, 'the env line goes through')
        check('--with-cacheexport' not in first, 'the harvested seed has no export marker')
        code, out = step('merge', str(work / 'a'))
        check(code != 0 and 'ufs/export' in out, 'a run without an export is refused, naming why')
        code, out = step('check', str(work / 'a'))
        check(code != 0, 'check refuses it as well')
        code, out = step('merge')
        check(code != 0, 'merge needs a run')

        # check: files of the two kinds only, what they compress to, the costliest pipelines of the journal.
        export = work / 'run/ufs/export'
        (export / 'v1/aa/bb').mkdir(parents=True)
        (export / 'v1/aa/bb/cc').write_bytes(b'x' * 4096)
        (export / 'pipelines.log').write_text('R t=1 tid=2 main=0 us=1200000 ok=1 samples=1 topo=3 ds=0/0 cpu=1 label=big c0=80 | v a | f b\n'
                                              'S t=3 us=7000 ok=1 name=n k= | x\nF Apple1 12: x\n')
        (export / 'zz-oslog').mkdir()
        (export / 'zz-oslog/oslog.txt.gz').write_bytes(b'x' * 100)
        code, out = step('check', str(work / 'run'))
        check(code == 0, 'check runs: ' + out[-300:])
        facts = json.loads(out[:out.rindex('}') + 1])
        check(facts['files'] == 1 and facts['raw_bytes'] == 4096 and 0 < facts['packed_bytes'] < 4096, 'only the cache entries count')
        check(facts['pipelines'] == 2 and facts['pipeline_ms'] == 1207 and facts['costliest'][0] == [1200, 'big'], 'the journal')
        check('against a budget of 4.0 MiB' in out, 'the default budget')
    code, out = step('plan')
    check(code == 0 and '60 秒' in out and 'gpu-loop.py' in out, 'the plan names the wait and the run')
    if failures:
        return 1
    print('PASS: harvest steps: capture (final seed, marker, UFS log), merge (runs in order, budget, journal, env line, no marker), check (entries, journal), refusals')
    return 0


if __name__ == '__main__':
    sys.exit(main())
