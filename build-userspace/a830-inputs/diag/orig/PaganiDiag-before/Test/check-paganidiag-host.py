#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Host checks of PaganiDiag beyond the core test (Test/run.sh runs them all):

  check-paganidiag-host.py patch     kernel/phone-diag-sysctl.patch is what scripts/make-diag-patch.py makes from the built tree's
                                     kern_exec.c, applies with git apply, and the patched kern_exec.c compiles with the build's own
                                     compile record (kern_exec.o.json) with zero errors; the only new warnings are XNU's startup-macro
                                     casts of the four declarations (lock group, mutex, two sysctls); the object defines both OIDs.
  check-paganidiag-host.py build     scripts/build-paganidiag.py into a temporary directory: verified ad hoc signature, 4 KiB pages,
                                     identifier, entitlements exactly paganidiag.entitlements, a thin arm64 iOS 17.0 MH_EXECUTE, the
                                     launchd job and the logging preference lint clean and as intended, trust.json's CDHash.
  check-paganidiag-host.py trust     scripts/make-diag-trust.py on the next2 owner-trust-apps.bundle: the old owner entries plus the
                                     daemon's, the Apple modules unchanged, refused when the daemon does not match trust.json.
  check-paganidiag-host.py seed      integration/make-phone-seed.diff applies to 工具/make-phone-seed.py; the patched copy stages the
                                     daemon into a copy of a phone seed (Test/check-seed.py checks it) and refuses a seed whose launch
                                     policy disables com.apple.DumpPanic.
  check-paganidiag-host.py layout    integration/make-a830-layout.diff applies to 工具/make-a830-layout.sh; with it the layout's own
                                     host test (工具/test-phone-island-layout.py) passes, A830_DIAG reaches make-phone-seed.py, the image
                                     gets boot-arg sheng-no-lc=1 (only with A830_DIAG) and an owner trust bundle without the daemon is
                                     refused.
Environment: XNU_WORK and XNU_OBJ point at another built tree; PAGANIDIAG_BASE_SEED at another phone seed. A check whose inputs are
missing says SKIP."""
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parents[1]
TOP = Path('/Users/xiaochoumao/unixnu-13t')
TREE = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree'
XNU_WORK = Path(os.environ.get('XNU_WORK', TREE / '.cache/xnu-work'))
XNU_OBJ = Path(os.environ.get('XNU_OBJ', TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT'))
SCRATCH = Path('/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad')
APPS_TRUST = SCRATCH / 'next2/metal-for-apps/pipeline/out/owner-trust-apps.bundle'
BASE_SEED = Path(os.environ.get('PAGANIDIAG_BASE_SEED', TOP / '工具/seeds/phone-island-apps.dbrdisk'))


class Skip(Exception):
    pass


def fail(message):
    raise SystemExit('check-paganidiag-host: FAIL: ' + message)


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs)


def compile_kern_exec(source, keep):
    """Compiles source with kern_exec.o.json's own command; returns (returncode, warning and error lines, object symbols)."""
    record_path = XNU_OBJ / 'kern_exec.o.json'
    if not record_path.is_file():
        raise Skip(f'no compile record {record_path}')
    record = json.JSONDecoder().raw_decode(record_path.read_text())[0]
    arguments, skip = [], False
    obj = Path(keep) / (Path(source).stem + '.o')
    for argument in record['arguments']:
        if skip:
            skip = False
            continue
        if argument == record['file']:
            arguments.append(str(source))
        elif argument == '-o':
            arguments += ['-o', str(obj)]
            skip = True
        elif not (argument.startswith('-MD') or argument.startswith('-MF') or argument == '-MMD'):
            arguments.append(argument)
    arguments += ['-iquote', str(XNU_WORK / 'bsd/kern')]
    environment = dict(os.environ, PATH=os.environ.get('PATH', '') + ':/opt/homebrew/opt/llvm/bin',
                       CCC_OVERRIDE_OPTIONS='# +-Wno-unnecessary-virtual-specifier')
    result = subprocess.run(arguments, cwd=record['directory'], capture_output=True, text=True, env=environment)
    lines = [re.sub(r':\d+:\d+:', ':L:', line.replace(str(source), 'kern_exec.c')) for line in result.stderr.splitlines()
             if ' warning: ' in line or ' error: ' in line]
    symbols = run('xcrun', 'nm', obj).stdout if obj.exists() else ''
    return result.returncode, lines, symbols


def check_patch():
    original_path = XNU_WORK / 'bsd/kern/kern_exec.c'
    if not original_path.is_file():
        raise Skip(f'no {original_path}')
    with tempfile.TemporaryDirectory(prefix='paganidiag-patch-') as tmp:
        root = Path(tmp)
        fresh = root / 'fresh.patch'
        run(sys.executable, '-B', HERE / 'scripts/make-diag-patch.py', '--kern-exec', original_path, '--out', fresh)
        if fresh.read_text() != (HERE / 'kernel/phone-diag-sysctl.patch').read_text():
            fail('kernel/phone-diag-sysctl.patch is not what make-diag-patch.py makes from the built tree (run it again)')
        patched = root / 'patched'
        (patched / 'bsd/kern').mkdir(parents=True)
        shutil.copyfile(original_path, patched / 'bsd/kern/kern_exec.c')
        run('git', 'init', '-q', patched)
        run('git', 'apply', HERE / 'kernel/phone-diag-sysctl.patch', cwd=patched)
        base = root / 'base'
        base.mkdir()
        shutil.copyfile(original_path, base / 'kern_exec.c')
        rc_base, base_lines, _ = compile_kern_exec(base / 'kern_exec.c', base)
        rc, lines, symbols = compile_kern_exec(patched / 'bsd/kern/kern_exec.c', root)
        if rc_base:
            raise Skip('the unpatched kern_exec.c does not compile with its record here')
        errors = [line for line in lines if ' error: ' in line]
        if rc or errors:
            fail('the patched kern_exec.c does not compile:\n' + '\n'.join(errors[:10]))
        new = list(lines)
        for line in base_lines:
            if line in new:
                new.remove(line)
        allowed = re.compile(r"warning: cast from 'void \(\*\)\(struct (lck_grp_spec|lck_mtx_startup_spec|sysctl_oid) \*\)' to "
                             r"'typeof \((lck_grp_startup_init|lck_mtx_startup_init|sysctl_register_oid_early)\(&(__startup_lck_grp_spec_"
                             r"pagani_diag_lck_grp|__startup_lck_mtx_spec_pagani_diag_lock|sysctl__kern_pagani_diag_dropped|"
                             r"sysctl__kern_pagani_diag_line)\)\) .*\[-Wcast-function-type-strict\]$")
        unexpected = [line for line in new if not allowed.search(line)]
        if unexpected or len(new) != 4:
            fail(f'{len(new)} new warnings, unexpected:\n' + '\n'.join(unexpected))
        for symbol in ('_sysctl__kern_pagani_diag_line', '_sysctl__kern_pagani_diag_dropped'):
            if symbol not in symbols:
                fail(f'the object lacks {symbol}')
        print(f'PASS patch: applies to {original_path}; compiled with kern_exec.o.json, 0 errors, {len(lines)} warnings '
              f'({len(base_lines)} before; the 4 new ones are startup-macro casts); both OIDs in the object')


def check_build():
    with tempfile.TemporaryDirectory(prefix='paganidiag-build-') as tmp:
        out = Path(tmp)
        run(sys.executable, '-B', HERE / 'scripts/build-paganidiag.py', '--out', out)
        daemon = out / 'paganidiag'
        run('codesign', '--verify', '--strict', '--verbose=2', daemon)
        detail = subprocess.run(['codesign', '--display', '--verbose=4', str(daemon)], capture_output=True, text=True).stderr
        facts = dict(re.findall(r'^(CDHash|Page size|Identifier|Signature|Format)=(.*)$', detail, re.M))
        if facts.get('Page size') != '4096' or facts.get('Identifier') != 'org.unixnu.pagani.diag' or facts.get('Signature') != 'adhoc' \
                or facts.get('Format') != 'Mach-O thin (arm64)':
            fail('signature: ' + json.dumps(facts))
        signed = subprocess.run(['codesign', '-d', '--entitlements', ':-', str(daemon)], capture_output=True, check=True).stdout
        entitlements = plistlib.loads(signed) if signed else {}
        if entitlements != plistlib.loads((HERE / 'paganidiag.entitlements').read_bytes()):
            fail('signed entitlements: ' + json.dumps(entitlements))
        for name in ('org.unixnu.pagani.diag.plist', 'com.apple.migration.plist'):
            run('plutil', '-lint', out / name)
        job = plistlib.loads((out / 'org.unixnu.pagani.diag.plist').read_bytes())
        if job['ProgramArguments'] != ['/private/var/ngpu/bin/paganidiag'] or job.get('UserName') != 'root' or job.get('KeepAlive'):
            fail('launchd job: ' + json.dumps(job))
        trust = json.loads((out / 'trust.json').read_text())
        if trust['cdhash'] != facts['CDHash']:
            fail('trust.json does not name the daemon')
        info = run('xcrun', 'vtool', '-show-build', daemon).stdout
        if not re.search(r'platform IOS', info) or not re.search(r'minos 17\.0', info):
            fail('build version: ' + info)
        print(f'PASS build: {trust["executable_bytes"]} bytes, CDHash {trust["cdhash"]}, 4 KiB pages, arm64 iOS 17.0, '
              f'entitlements {sorted(entitlements)}')


def check_trust():
    if not APPS_TRUST.is_file():
        raise Skip(f'no {APPS_TRUST}')
    with tempfile.TemporaryDirectory(prefix='paganidiag-trust-') as tmp:
        out = Path(tmp) / 'build'
        run(sys.executable, '-B', HERE / 'scripts/build-paganidiag.py', '--out', out)
        bundle = Path(tmp) / 'trust.bundle'
        run(sys.executable, '-B', HERE / 'scripts/make-diag-trust.py', APPS_TRUST, bundle, out)
        report = json.loads(bundle.with_suffix('.json').read_text())
        cdhash = json.loads((out / 'trust.json').read_text())['cdhash']
        if set(report['owner_hashes']) != set(report['base_hashes']) | {cdhash} or not report['apple_modules_unchanged']:
            fail('owner entries: ' + json.dumps(report))
        if bytes.fromhex(cdhash) not in bundle.read_bytes():
            fail('the bundle lacks the CDHash')
        again = subprocess.run([sys.executable, '-B', str(HERE / 'scripts/make-diag-trust.py'), str(APPS_TRUST), str(bundle), str(out)],
                               capture_output=True, text=True)
        if again.returncode == 0 or 'refuse to overwrite' not in again.stderr:
            fail('an existing output was not refused')
        (out / 'trust.json').write_text(json.dumps({'cdhash': '0' * 40}))
        stale = subprocess.run([sys.executable, '-B', str(HERE / 'scripts/make-diag-trust.py'), str(APPS_TRUST),
                                str(Path(tmp) / 'stale.bundle'), str(out)], capture_output=True, text=True)
        if stale.returncode == 0 or 'does not carry the CDHash' not in stale.stderr:
            fail('a daemon that does not match trust.json was not refused')
        print(f'PASS trust: {len(report["base_hashes"])} owner entries + {cdhash}, Apple modules unchanged, stale and existing refused')


def patched_copy(diff, original, directory):
    """A copy of original with the integration diff applied (patch -p1 relative to TOP), after a dry run against the real file."""
    if not original.is_file():
        raise Skip(f'no {original}')
    run('patch', '-p1', '--dry-run', '--quiet', '-i', diff, cwd=TOP)
    tree = Path(directory)
    target = tree / original.relative_to(TOP)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    run('patch', '-p1', '--quiet', '-i', diff, cwd=tree)
    return target


def check_seed():
    if not BASE_SEED.is_file():
        raise Skip(f'no phone seed {BASE_SEED}')
    with tempfile.TemporaryDirectory(prefix='paganidiag-seed-', dir=str(SCRATCH) if SCRATCH.is_dir() else None) as tmp:
        script = patched_copy(HERE / 'integration/make-phone-seed.diff', TOP / '工具/make-phone-seed.py', tmp)
        out = Path(tmp) / 'build'
        run(sys.executable, '-B', HERE / 'scripts/build-paganidiag.py', '--out', out)
        seed = Path(tmp) / 'seed/diag.dbrdisk'
        run(sys.executable, '-B', script, '--disable-jobs', '', '--diag-daemon', out, BASE_SEED, seed)
        report = json.loads(seed.with_suffix('.json').read_text())
        if report['diag_daemon']['cdhash'] != json.loads((out / 'trust.json').read_text())['cdhash']:
            fail('seed report: ' + json.dumps(report['diag_daemon']))
        checked = run(sys.executable, '-B', HERE / 'Test/check-seed.py', seed, out).stdout
        refused = subprocess.run([sys.executable, '-B', str(script), '--disable-jobs', 'com.apple.DumpPanic', '--diag-daemon', str(out),
                                  str(BASE_SEED), str(Path(tmp) / 'seed/refused.dbrdisk')], capture_output=True, text=True)
        if refused.returncode == 0 or 'disables com.apple.DumpPanic' not in refused.stderr:
            fail('a seed whose launch policy disables com.apple.DumpPanic was not refused: ' + refused.stderr[-400:])
        print('PASS seed: diff applies; ' + checked.strip().splitlines()[-1] + '; DumpPanic-disabled seed refused')


def check_layout():
    test = TOP / '工具/test-phone-island-layout.py'
    if not test.is_file():
        raise Skip(f'no {test}')
    with tempfile.TemporaryDirectory(prefix='paganidiag-layout-') as tmp:
        root = Path(tmp)
        script = patched_copy(HERE / 'integration/make-a830-layout.diff', TOP / '工具/make-a830-layout.sh', root)
        for name in ('make-a830-images.sh', 'test-phone-island-layout.py'):
            shutil.copyfile(TOP / '工具' / name, root / '工具' / name)
        for name in ('UniXNU-Pagani_SM8750-gpu', 'UniXNU', '一加13T原厂备份'):
            (root / name).symlink_to(TOP / name)
        result = subprocess.run([sys.executable, '-B', str(root / '工具/test-phone-island-layout.py')], capture_output=True, text=True)
        if result.returncode:
            fail('the layout test fails with the diff:\n' + result.stderr[-3000:])
        tests = re.search(r'Ran (\d+) tests', result.stderr)
        sys.path.insert(0, str(root / '工具'))
        import importlib
        layout_test = importlib.import_module('test-phone-island-layout')
        build = root / 'build'
        run(sys.executable, '-B', HERE / 'scripts/build-paganidiag.py', '--out', build)
        bundle = root / 'diag.bundle'
        if not APPS_TRUST.is_file():
            raise Skip(f'no {APPS_TRUST}')
        run(sys.executable, '-B', HERE / 'scripts/make-diag-trust.py', APPS_TRUST, bundle, build)
        box = layout_test.Sandbox()
        try:
            result, calls, seeds = box.run('phone-island-apps', A830_DIAG=str(build), A830_OWNER_TRUST=str(bundle))
            if result.returncode or not seeds or seeds[0][seeds[0].index('--diag-daemon') + 1] != str(build):
                fail('A830_DIAG did not reach make-phone-seed.py: ' + result.stderr[-800:] + json.dumps(seeds))
            if not calls or str(bundle) not in calls[0]['argv']:
                fail('the payload did not take the diag trust bundle')
            # launchd spawns com.apple.DumpPanic as a system service; AMFI refuses an owner-trusted binary there unless the image
            # skips the launch constraints (zzyc-owner-skip-launch-constraints.patch).
            if 'sheng-no-lc=1' not in layout_test.kernel_args(calls[0]['argv']):
                fail('a diag image lacks boot-arg sheng-no-lc=1: ' + json.dumps(layout_test.kernel_args(calls[0]['argv'])))
            result, calls, seeds = box.run('phone-island-apps', A830_DIAG=str(build), A830_OWNER_TRUST=str(APPS_TRUST))
            if result.returncode == 0 or 'PaganiDiag' not in result.stderr or calls:
                fail('an owner trust bundle without the daemon was not refused')
            result, calls, seeds = box.run('phone-island-apps')
            if result.returncode or any('--diag-daemon' in seed for seed in seeds):
                fail('without A830_DIAG the seed changed')
            if not calls or any(arg.startswith('sheng-no-lc') for arg in layout_test.kernel_args(calls[0]['argv'])):
                fail('without A830_DIAG the image changed its launch constraints')
        finally:
            box.close()
        print(f'PASS layout: diff applies; the layout test passes with it ({tests.group(1) if tests else "?"} tests); A830_DIAG reaches '
              f'the seed and adds sheng-no-lc=1, a trust bundle without the daemon is refused')


CHECKS = {'patch': check_patch, 'build': check_build, 'trust': check_trust, 'seed': check_seed, 'layout': check_layout}


def main():
    names = sys.argv[1:] or list(CHECKS)
    for name in names:
        if name not in CHECKS:
            sys.exit(__doc__)
        try:
            CHECKS[name]()
        except Skip as why:
            print(f'SKIP {name}: {why}')


if __name__ == '__main__':
    main()
