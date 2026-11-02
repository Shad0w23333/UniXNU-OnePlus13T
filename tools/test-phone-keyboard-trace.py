#!/usr/bin/env python3
"""Host tests of the iPhone-identity keyboard trace (phone-keyboard-trace/phone-keyboard-trace.patch).

  test-phone-keyboard-trace.py [--xnu DIR] [-v]

1. pagani_kbtrace.h (the counting and line formatting, shared verbatim with the kernel) under the kernel's warning set
   (-Werror, -Wshorten-64-to-32, -Wconversion) with ASan/UBSan at -O0 and -O2: test_kbtrace.c.
2. The patch against a pristine xnu-10063.141.1 tree: it must apply cleanly to bsd/kern/kdebug.c, and the header it adds
   must be byte-identical to phone-keyboard-trace/pagani_kbtrace.h.
3. The patched kdebug.c functions themselves (declaration block, kdebug_trace64, kdebug_commpage_state, kdebug_init,
   pagani_kbtrace_flush, pagani_kbtrace_note) are cut out of the patched file and run in harness.c against stubs of the
   kernel calls: off without the boot-arg, typefilter and commpage when on, per-process first/summary lines, one pending
   summary, untouched kdebug behaviour, and the self-disable after a ktrace reset.
4. With UIKITCORE=<extracted 21H461 UIKitCore> (ipsw dyld extract), the debug ids at the UIKit call sites the trace relies
   on are re-read from the binary.
5. keyboard-prefs.py (the preference fixes the trace may call for) on a temporary Preferences directory.

--xnu DIR: an xnu-10063.141.1 tree to patch (read only; copied). Default: the build tree's .cache/xnu-work, else the
scratchpad xnu-fresh copy."""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE / 'phone-keyboard-trace'
PATCH = KIT / 'phone-keyboard-trace.patch'
XNU_CANDIDATES = [
    Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work'),
    Path('/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/apps-review/xnu-fresh'),
]
PRISTINE_KDEBUG_SHA1 = '7fac08e164ee3c30745768314f2fa48fb34803c2'   # xnu-10063.141.1 bsd/kern/kdebug.c
WARNINGS = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wshorten-64-to-32', '-Wsign-compare', '-Wconversion',
            '-Wno-sign-conversion']
# UIKitCore 21H461 call sites of kdebug_trace and the debug id each builds in w0 (see the keyboard-touch report).
UIKIT_SITES = {
    0x18a18a620: 0x2b8701ac,   # -[UIApplication sendEvent:]
    0x18a140b08: 0x2bae0010,   # -[UIKeyboardSceneDelegate setKeyWindowSceneInputViews:animationStyle:]
    0x18ac39e6c: 0x2bae006c,   # -[UIKeyboardLayout touchDown:]
    0x18ac3a0b0: 0x2bae0070,   # ___30-[UIKeyboardLayout touchDown:]_block_invoke_2
    0x18ac3a30c: 0x2bae0074,   # -[UIKeyboardLayout touchDragged:]
    0x18ac3aa94: 0x2bae007c,   # -[UIKeyboardLayout touchUp:]
    0x18ac3acd8: 0x2bae0080,   # ___28-[UIKeyboardLayout touchUp:]_block_invoke_2
    0x18a484808: 0x2bae0024,   # -[UIKeyboardTaskEntry execute:]
    0x18ac08340: 0x2bae0084,   # -[UIKeyboardImpl handleKeyboardInput:executionContext:]
    0x18ac08a3c: 0x2bae0088,   # ___55-[UIKeyboardImpl handleKeyboardInput:executionContext:]_block_invoke_2
    0x18ac0c15c: 0x2bae00a4,   # -[UIKeyboardImpl generateCandidatesAsynchronouslyWithRange:selectedCandidate:]
    0x18ac0c488: 0x2bae00a8,   # ___78-[UIKeyboardImpl generateCandidatesAsynchronouslyWithRange:selectedCandidate:]_block_invoke_3
    0x18ac295a8: 0x2bae001c,   # -[UIKeyboardImpl _handleKeyEvent:executionContext:]
}
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
    for opt in ('-O0', '-O2'):
        exe = work / f'test_kbtrace{opt}'
        run(['clang', *WARNINGS, '-fsanitize=address,undefined', '-fno-sanitize-recover=all', opt, '-g', f'-I{KIT}',
             str(KIT / 'test_kbtrace.c'), '-o', str(exe)])
        out = run([str(exe)])
        assert 'all checks passed' in out, out
    print('ok 1 pagani_kbtrace.h unit checks (-O0/-O2, ASan/UBSan, kernel warning set)')


def find_xnu(arg):
    for c in ([Path(arg)] if arg else XNU_CANDIDATES):
        if (c / 'bsd/kern/kdebug.c').is_file():
            return c
    return None


def function(text, name):
    """The definition of NAME: from its return-type line to the closing brace in column 0."""
    m = re.search(r'^(?:static\s+)?[a-z_0-9 ]+\n' + re.escape(name) + r'\(', text, re.M)
    if not m:
        raise SystemExit(f'FAILED: {name} not found in the patched kdebug.c')
    end = text.index('\n}\n', m.end())
    return text[m.start():end + 3]


def test_patch(work, xnu):
    if not xnu:
        print('skip 2-3 no xnu-10063.141.1 tree found (use --xnu)')
        return
    src = xnu / 'bsd/kern/kdebug.c'
    sha = run(['shasum', str(src)]).split()[0]
    if sha != PRISTINE_KDEBUG_SHA1:
        raise SystemExit(f'FAILED: {src} is not the pristine xnu-10063.141.1 kdebug.c ({sha}); a patch already touched it?')
    tree = work / 'xnu'
    (tree / 'bsd/kern').mkdir(parents=True)
    shutil.copy(src, tree / 'bsd/kern/kdebug.c')
    run(['patch', '-p1', '--batch', '--forward', '-i', str(PATCH)], cwd=tree)
    rejects = list(tree.rglob('*.rej')) + list(tree.rglob('*.orig'))
    if rejects:
        raise SystemExit(f'FAILED: patch left {rejects}')
    added = (tree / 'bsd/kern/pagani_kbtrace.h').read_bytes()
    if added != (KIT / 'pagani_kbtrace.h').read_bytes():
        raise SystemExit('FAILED: the header in the patch differs from phone-keyboard-trace/pagani_kbtrace.h')
    print('ok 2 the patch applies cleanly to xnu-10063.141.1 bsd/kern/kdebug.c and carries the tested header')

    text = (tree / 'bsd/kern/kdebug.c').read_text()
    start = text.index('/*\n * Keyboard trace (boot-arg pagani-kbtrace=1')
    block = text[start:text.index('// Coprocessors (or "IOP"s)', start)]
    block = block.replace('#include <kern/thread_call.h>\n', '').replace('#include <pexpert/pexpert.h>\n', '')
    block = block.replace('extern void boot_progress_trace(const char *text) __attribute__((weak_import));\n', '')
    parts = [block] + [function(text, n) for n in ('kdebug_trace64', 'kdebug_commpage_state', 'kdebug_reset', 'kdebug_init',
                                                    'pagani_kbtrace_flush', 'pagani_kbtrace_note')]
    for p in parts[1:]:
        assert 'assert(' not in p or 'kdebug_init' in p
    (work / 'patched_parts.inc').write_text('\n'.join(parts))
    exe = work / 'harness'
    for opt in ('-O0', '-O2'):
        run(['clang', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
             '-fsanitize=address,undefined', '-fno-sanitize-recover=all', opt, '-g', f'-I{KIT}', f'-I{work}',
             str(KIT / 'harness.c'), '-o', str(exe)])
        out = run([str(exe)])
        assert 'all checks passed' in out, out
    if VERBOSE:
        print(out)
    print('ok 3 patched kdebug.c functions in the stub harness (-O0/-O2, ASan/UBSan)')


def test_uikit_sites():
    path = os.environ.get('UIKITCORE')
    if not path or not Path(path).is_file():
        print('skip 4 UIKITCORE not set: UIKit call sites not re-read')
        return
    objdump = shutil.which('llvm-objdump') or '/opt/homebrew/opt/llvm/bin/llvm-objdump'
    for pc, want in sorted(UIKIT_SITES.items()):
        out = run([objdump, '-d', '--no-show-raw-insn', '--print-imm-hex', f'--start-address={pc - 0x40:#x}',
                   f'--stop-address={pc + 4:#x}', path])
        lo = hi = None
        for line in out.splitlines():
            m = re.search(r'\bmov\s+w0, #(0x[0-9a-f]+)', line)
            if m:
                lo, hi = int(m.group(1), 16), None
            m = re.search(r'\bmovk\s+w0, #(0x[0-9a-f]+), lsl #16', line)
            if m:
                hi = int(m.group(1), 16)
            m = re.search(r'\bmov\s+w0, w23', line)     # -[UIKeyboardTaskEntry execute:] keeps the id in w23
            if m:
                lo, hi = 0x24, 0x2bae
        if lo is None or ((hi or 0) << 16 | lo) != want or not re.search(rf'^\s*{pc:x}:\s+bl\s', out, re.M):
            raise SystemExit(f'FAILED: UIKit site {pc:#x} does not trace {want:#x}')
    print(f'ok 4 {len(UIKIT_SITES)} UIKit kdebug sites carry the expected debug ids')


def test_prefs(work):
    import importlib.util
    import plistlib
    spec = importlib.util.spec_from_file_location('keyboard_prefs', KIT / 'keyboard-prefs.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    prefs = work / 'Preferences'
    prefs.mkdir()
    xml = prefs / 'com.apple.keyboard.plist'
    xml.write_bytes(plistlib.dumps({'KeyboardLastUsed': 'en_US', 'KeyboardDisableHitTesting': False}, fmt=plistlib.FMT_XML))
    xml.chmod(0o640)
    inode = xml.stat().st_ino
    report = mod.apply(prefs, ['com.apple.keyboard:KeyboardDisableHitTesting=true',
                               'com.apple.keyboard.ContinuousPath:KeyboardContinuousPathEnabled=false'])
    data = plistlib.loads(xml.read_bytes())
    assert data == {'KeyboardLastUsed': 'en_US', 'KeyboardDisableHitTesting': True}, data
    assert xml.read_bytes().startswith(b'<?xml') and xml.stat().st_ino == inode and oct(xml.stat().st_mode & 0o777) == '0o640'
    cp = prefs / 'com.apple.keyboard.ContinuousPath.plist'
    assert cp.read_bytes().startswith(b'bplist') and plistlib.loads(cp.read_bytes()) == {'KeyboardContinuousPathEnabled': False}
    assert oct(cp.stat().st_mode & 0o777) == '0o600'
    assert [r['created'] for r in report] == [False, True]
    assert report[0]['before'] == {'KeyboardDisableHitTesting': False}
    for bad in ('com.apple.keyboard:KeyboardDisableHitTesting=yes', 'com.evil:Key=true', 'com.apple.keyboard:=true',
                '../x:Key=true', 'com.apple.keyboard:Key=true;rm'):
        try:
            mod.parse(bad)
        except ValueError:
            continue
        raise SystemExit(f'FAILED: {bad!r} accepted')
    (prefs / 'com.apple.keyboard.preferences.plist').write_bytes(plistlib.dumps(['not a dict']))
    try:
        mod.apply(prefs, ['com.apple.keyboard.preferences:KeyboardAutocorrection=false'])
        raise SystemExit('FAILED: a non-dictionary plist was overwritten')
    except ValueError:
        pass
    print('ok 5 keyboard-prefs.py merges keys in place, keeps format/owner/mode, rejects bad specs')


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--xnu')
    ap.add_argument('-v', action='store_true')
    args = ap.parse_args()
    VERBOSE = args.v
    with tempfile.TemporaryDirectory(prefix='kbtrace-') as tmp:
        work = Path(tmp)
        test_header(work)
        test_patch(work, find_xnu(args.xnu))
        test_uikit_sites()
        test_prefs(work)
    print('all phone-keyboard-trace tests passed')


if __name__ == '__main__':
    main()
