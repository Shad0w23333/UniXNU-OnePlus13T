#!/usr/bin/env python3
"""Host tests of the keyboard trace version 2 (sysfix/keyboard/kbtrace/phone-keyboard-trace.patch).

  test-phone-keyboard-trace.py [--xnu DIR] [--dsc DSC --a2s A2S] [-v]

1. pagani_kbtrace.h (counting, pair timing and line formatting, shared verbatim with the kernel) under the kernel's warning
   set (-Werror, -Wshorten-64-to-32, -Wconversion, -Wimplicit-fallthrough) with ASan/UBSan at -O0 and -O2: test_kbtrace.c.
2. The patch against a pristine xnu-10063.141.1 bsd/kern/kdebug.c (sha1 7fac08e1...): applies cleanly and carries the tested
   header. The build tree's kdebug.c already carries version 1, so the pristine file is made by reverse-applying version 1
   (工具/phone-keyboard-trace/phone-keyboard-trace.patch) to a copy; the tree is only read.
3. The patched kdebug.c functions (declarations, kdebug_trace64, kdebug_commpage_state, kdebug_reset, kdebug_init,
   pagani_kbtrace_flush, pagani_kbtrace_note) cut out of the patched file and run in harness.c against stubs: off without the
   boot-arg, typefilter/commpage when on, first/sum/lat lines, arg2 reaching the kbd operation timing, one pending summary,
   untouched kdebug behaviour, self-disable after a ktrace reset.
4. The patched kdebug.c compiled with the build's own clang command (compile record kdebug.o.json, real-compile.py, object
   thrown away): 0 errors and no warning beyond those of version 1 (the record's 14 baseline warnings of xnu itself).
5. With --dsc and --a2s (21H461 dyld cache and ipsw symbol cache): the debug ids and kbd operation numbers the trace relies on
   are re-read from UIKitCore and TextInputCore with `ipsw dyld disass`.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE / 'kbtrace'
PATCH = KIT / 'phone-keyboard-trace.patch'
V1_PATCH = Path('/Users/xiaochoumao/unixnu-13t/工具/phone-keyboard-trace/phone-keyboard-trace.patch')
REAL_COMPILE = HERE.parent.parent / 'tools/real-compile.py'
XNU_DEFAULT = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work')
DSC_DEFAULT = HERE.parent.parent / 'systemos-21H461/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'
A2S_DEFAULT = HERE.parent.parent / 'dsc-21H461.a2s'
PRISTINE_KDEBUG_SHA1 = '7fac08e164ee3c30745768314f2fa48fb34803c2'   # xnu-10063.141.1 bsd/kern/kdebug.c
WARNINGS = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wshorten-64-to-32', '-Wsign-compare', '-Wconversion',
            '-Wno-sign-conversion', '-Wimplicit-fallthrough']
# (function, debug id built in w0 before its kdebug_trace call, operation in w2 or None)
SITES = [
    ('-[UIKeyboardAutocorrectionController setNeedsAutocorrection]', 0x2bae008c, None),
    ('___60-[UIKeyboardAutocorrectionController setNeedsAutocorrection]_block_invoke_2', 0x2bae0090, None),
    ('-[UIKeyboardLayout touchUp:]', 0x2bae007c, None),
    ('___28-[UIKeyboardLayout touchUp:]_block_invoke_2', 0x2bae0080, None),
    ('-[UIKeyboardImpl handleKeyboardInput:executionContext:]', 0x2bae0084, None),
    ('-[UIKeyboardImpl generateCandidatesAsynchronouslyWithRange:selectedCandidate:]', 0x2bae00a4, None),
    ('-[TIKeyboardInputManagerWrapper syncToKeyboardState:completionHandler:]', 0x2bae0034, 0x0),
    ('-[TIKeyboardInputManagerWrapper generateAutocorrectionsWithKeyboardState:candidateRange:requestToken:completionHandler:]',
     0x2bae0034, 0x1),
    ('___120-[TIKeyboardInputManagerWrapper generateAutocorrectionsWithKeyboardState:candidateRange:requestToken:'
     'completionHandler:]_block_invoke', 0x2bae0038, 0x1),
    ('-[TIKeyboardInputManager finishGeneratingInlineCompletions:completion:syncResponse:]', 0x2bae0034, 0x10),
    ('___84-[TIKeyboardInputManager finishGeneratingInlineCompletions:completion:syncResponse:]_block_invoke', 0x2bae0038, 0x10),
    ('-[TIKeyboardInputManagerPolymorph handleKeyboardInput:keyboardState:completionHandler:]', 0x2bae0034, 0x64),
]
VERBOSE = False


def run(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if VERBOSE or r.returncode:
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
    if r.returncode:
        raise SystemExit(f'FAILED: {" ".join(map(str, args))}')
    return r.stdout


def test_header(work):
    out = ''
    for opt in ('-O0', '-O2'):
        exe = work / f'test_kbtrace{opt}'
        run(['clang', *WARNINGS, '-fsanitize=address,undefined', '-fno-sanitize-recover=all', opt, '-g', f'-I{KIT}',
             str(KIT / 'test_kbtrace.c'), '-o', str(exe)])
        out = run([str(exe)])
        assert 'all checks passed' in out, out
    if VERBOSE:
        print(out)
    print('ok 1 pagani_kbtrace.h v2 unit checks (-O0/-O2, ASan/UBSan, kernel warning set)')


def pristine_kdebug(work, xnu):
    src = xnu / 'bsd/kern/kdebug.c'
    tree = work / 'pristine'
    (tree / 'bsd/kern').mkdir(parents=True)
    shutil.copy(src, tree / 'bsd/kern/kdebug.c')
    sha = run(['shasum', str(tree / 'bsd/kern/kdebug.c')]).split()[0]
    if sha != PRISTINE_KDEBUG_SHA1:
        if (xnu / 'bsd/kern/pagani_kbtrace.h').is_file():
            shutil.copy(xnu / 'bsd/kern/pagani_kbtrace.h', tree / 'bsd/kern/pagani_kbtrace.h')
        # The build's work tree carries version 2 since the kernel of 2026-10-31, version 1 before that.
        carried = PATCH if 'pagani_kbt_take_lat' in src.read_text() else V1_PATCH
        run(['patch', '-p1', '-R', '--batch', '-i', str(carried)], cwd=tree)
        sha = run(['shasum', str(tree / 'bsd/kern/kdebug.c')]).split()[0]
    if sha != PRISTINE_KDEBUG_SHA1:
        raise SystemExit(f'FAILED: no pristine xnu-10063.141.1 kdebug.c could be made from {src} ({sha})')
    return tree / 'bsd/kern/kdebug.c'


def function(text, name):
    """The definition of NAME: from its return-type line to the closing brace in column 0."""
    m = re.search(r'^(?:static\s+)?[a-z_0-9 ]+\n' + re.escape(name) + r'\(', text, re.M)
    if not m:
        raise SystemExit(f'FAILED: {name} not found in the patched kdebug.c')
    end = text.index('\n}\n', m.end())
    return text[m.start():end + 3]


def test_patch(work, xnu):
    if not xnu:
        print('skip 2-4 no xnu-10063.141.1 tree found (use --xnu)')
        return
    pristine = pristine_kdebug(work, xnu)
    tree = work / 'xnu'
    (tree / 'bsd/kern').mkdir(parents=True)
    shutil.copy(pristine, tree / 'bsd/kern/kdebug.c')
    run(['patch', '-p1', '--batch', '--forward', '-i', str(PATCH)], cwd=tree)
    rejects = list(tree.rglob('*.rej')) + list(tree.rglob('*.orig'))
    if rejects:
        raise SystemExit(f'FAILED: patch left {rejects}')
    if (tree / 'bsd/kern/pagani_kbtrace.h').read_bytes() != (KIT / 'pagani_kbtrace.h').read_bytes():
        raise SystemExit('FAILED: the header in the patch differs from kbtrace/pagani_kbtrace.h')
    print('ok 2 the v2 patch applies cleanly to pristine xnu-10063.141.1 bsd/kern/kdebug.c and carries the tested header')

    text = (tree / 'bsd/kern/kdebug.c').read_text()
    start = text.index('/*\n * Keyboard trace (boot-arg pagani-kbtrace=1')
    block = text[start:text.index('// Coprocessors (or "IOP"s)', start)]
    block = block.replace('#include <kern/thread_call.h>\n', '').replace('#include <pexpert/pexpert.h>\n', '')
    block = block.replace('extern void boot_progress_trace(const char *text) __attribute__((weak_import));\n', '')
    parts = [block] + [function(text, n) for n in ('kdebug_trace64', 'kdebug_commpage_state', 'kdebug_reset', 'kdebug_init',
                                                    'pagani_kbtrace_flush', 'pagani_kbtrace_note')]
    (work / 'patched_parts.inc').write_text('\n'.join(parts))
    exe = work / 'harness'
    out = ''
    for opt in ('-O0', '-O2'):
        run(['clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
             '-fsanitize=address,undefined', '-fno-sanitize-recover=all', opt, '-g', f'-I{tree / "bsd/kern"}', f'-I{work}',
             str(KIT / 'harness.c'), '-o', str(exe)])
        out = run([str(exe)])
        assert 'all checks passed' in out, out
    if VERBOSE:
        print(out)
    print('ok 3 patched kdebug.c functions in the stub harness (-O0/-O2, ASan/UBSan)')

    if not REAL_COMPILE.is_file():
        print('skip 4 real-compile.py not found')
        return
    env = dict(os.environ, TMPDIR=str(work))
    v1 = work / 'v1/bsd/kern'
    v1.mkdir(parents=True)
    shutil.copy(pristine, v1 / 'kdebug.c')
    run(['patch', '-p1', '--batch', '--forward', '-i', str(V1_PATCH)], cwd=work / 'v1')
    counts = {}
    for label, source in (('v1', v1 / 'kdebug.c'), ('v2', tree / 'bsd/kern/kdebug.c')):
        r = subprocess.run([sys.executable, '-I', str(REAL_COMPILE), str(source)], capture_output=True, text=True, env=env)
        m = re.search(r'rc (\d+) errors (\d+) warnings (\d+)', r.stdout)
        if r.returncode or not m or m.group(1) != '0' or m.group(2) != '0':
            sys.stdout.write(r.stdout + r.stderr)
            raise SystemExit(f'FAILED: real compile of the {label} kdebug.c')
        counts[label] = int(m.group(3))
    if counts['v2'] != counts['v1']:
        raise SystemExit(f'FAILED: version 2 adds warnings ({counts["v1"]} -> {counts["v2"]})')
    print(f'ok 4 real compile with the build record: 0 errors, {counts["v2"]} warnings = version 1 baseline (xnu\'s own)')


def test_sites(dsc, a2s):
    ipsw = shutil.which('ipsw')
    if not ipsw or not dsc or not Path(dsc).exists() or not a2s or not Path(a2s).exists():
        print('skip 5 no ipsw / dyld cache / a2s: call sites not re-read')
        return
    for name, want, op in SITES:
        image = 'TextInputCore' if name.lstrip('_').lstrip('0123456789').startswith(('-[TI', '+[TI')) else 'UIKitCore'
        addr = run([ipsw, 'dyld', 'symaddr', str(dsc), '--image', image, '--', name]).split()
        if not addr:
            raise SystemExit(f'FAILED: {name} not found')
        out = run([ipsw, 'dyld', 'disass', str(dsc), '--vaddr', addr[0].rstrip(':'), '--cache', str(a2s), '--no-color',
                   '--count', '400'])
        lo = hi = w2 = None
        ok = False
        for line in out.splitlines():
            m = re.search(r'\smov\s+w0, #(0x[0-9a-f]+)$', line)
            if m:
                lo, hi = int(m.group(1), 16), None
            m = re.search(r'\smovk\s+w0, #0x2bae, lsl #0x10$', line)
            if m:
                hi = 0x2bae
            m = re.search(r'\smov\s+[wx]2, #(0x[0-9a-f]+|\d+)$', line)
            if m:
                w2 = int(m.group(1), 0)
            if 'bl\t_kdebug_trace_stub' in line:
                if hi == 0x2bae and lo is not None and (hi << 16 | lo) == want and (op is None or w2 == op):
                    ok = True
                    break
                lo = hi = w2 = None
        if not ok:
            raise SystemExit(f'FAILED: {name} does not trace {want:#x}' + (f' op {op:#x}' if op is not None else ''))
    print(f'ok 5 {len(SITES)} UIKitCore/TextInputCore kdebug sites carry the expected debug ids and kbd operations')


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--xnu', default=str(XNU_DEFAULT))
    ap.add_argument('--dsc', default=str(DSC_DEFAULT))
    ap.add_argument('--a2s', default=str(A2S_DEFAULT))
    ap.add_argument('-v', action='store_true')
    args = ap.parse_args()
    VERBOSE = args.v
    xnu = Path(args.xnu) if args.xnu and (Path(args.xnu) / 'bsd/kern/kdebug.c').is_file() else None
    with tempfile.TemporaryDirectory(prefix='kbtrace2-') as tmp:
        work = Path(tmp)
        test_header(work)
        test_patch(work, xnu)
        test_sites(args.dsc, args.a2s)
    print('all keyboard trace v2 tests passed')


if __name__ == '__main__':
    main()
