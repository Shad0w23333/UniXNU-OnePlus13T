#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Write the XNU patch of PaganiDiag (phone-diag-sysctl.patch): kern.pagani_diag_line / kern.pagani_diag_dropped and the daemon's start.

  make-diag-patch.py [--kern-exec PATH] [--out PATCH]

PATH is bsd/kern/kern_exec.c as the patch series before this patch leaves it (default: the GPU line's built tree, .cache/xnu-work; it is
only read). The patch adds bsd/kern/pagani_diag.h (kernel/pagani_diag.h: the line and the limits, pure C) and, in kern_exec.c, the
block kernel/kern_exec-diag.inc before exec_activate_image plus one call after exec_activate_image saved the path. It belongs in the
device series after phone-keyboard-trace.patch. The script writes the unified diff (a/ b/ prefixes, as the series is applied with git
apply) and proves it applies with git apply --check and patch -p1 --dry-run to a copy of PATH. Without --out it prints the patch."""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DEFAULT_KERN_EXEC = Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/'
                         '.cache/xnu-work/bsd/kern/kern_exec.c')
BLOCK_ANCHOR = '/*\n * exec_activate_image\n *\n'
CALL_ANCHOR = ('\terror = exec_save_path(imgp, imgp->ip_user_fname, imgp->ip_seg, &excpath);\n\tif (error) {\n'
               '\t\tgoto bad_notrans;\n\t}\n\n\t/* Use excpath, which contains the copyin-ed exec path */\n')
CALL = ('\terror = pagani_diag_launch(imgp, p, &excpath);   /* PaganiDiag: the daemon in place of DumpPanic */\n'
        '\tif (error) {\n\t\tgoto bad_notrans;\n\t}\n\n')


def fail(message):
    raise SystemExit('make-diag-patch: ' + message)


def make(original):
    if 'pagani_diag' in original:
        fail('the file already has PaganiDiag')
    if original.count(BLOCK_ANCHOR) != 1 or original.count(CALL_ANCHOR) != 1:
        fail('exec_activate_image is not the expected one (comment and exec_save_path call)')
    for needed in ('\nexec_save_path(struct image_params *imgp, user_addr_t path, int seg, const char **excpath)\n',
                   '\nexec_reset_save_path(struct image_params *imgp)\n', '#include <sys/sysctl.h>\n', '#include <pexpert/pexpert.h>\n'):
        if needed not in original:
            fail('kern_exec.c lacks ' + needed.strip())
    if original.index(BLOCK_ANCHOR) < original.index('\nexec_reset_save_path(struct image_params *imgp)\n'):
        fail('exec_activate_image comes before exec_reset_save_path')
    block = (HERE / 'kernel/kern_exec-diag.inc').read_text()
    text = original.replace(BLOCK_ANCHOR, block + BLOCK_ANCHOR, 1)
    head, tail = CALL_ANCHOR.split('\n\t/* Use excpath', 1)
    return text.replace(CALL_ANCHOR, head + '\n' + CALL + '\t/* Use excpath' + tail, 1)


def unified(path, original, changed):
    with tempfile.TemporaryDirectory(prefix='diag-patch-') as tmp:
        root = Path(tmp)
        for side, content in (('a', original), ('b', changed)):
            target = root / side / path
            target.parent.mkdir(parents=True)
            target.write_text(content)
        result = subprocess.run(['diff', '-u', f'a/{path}', f'b/{path}'], cwd=root, capture_output=True, text=True)
        if result.returncode != 1:
            fail('diff failed: ' + result.stderr)
        lines = result.stdout.splitlines(keepends=True)
        lines[0], lines[1] = f'--- a/{path}\n', f'+++ b/{path}\n'
        return ''.join(lines)


def new_file(path, content):
    lines = content.splitlines(keepends=True)
    if not content.endswith('\n'):
        fail(f'{path} must end with a line break')
    return f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n' + ''.join('+' + line for line in lines)


def make_patch(original):
    changed = make(original)
    header = (HERE / 'kernel/pagani_diag.h').read_text()
    return unified('bsd/kern/kern_exec.c', original, changed) + new_file('bsd/kern/pagani_diag.h', header), changed


def prove(original, patch, changed):
    with tempfile.TemporaryDirectory(prefix='diag-patch-apply-') as tmp:
        root = Path(tmp)
        target = root / 'bsd/kern/kern_exec.c'
        target.parent.mkdir(parents=True)
        target.write_text(original)
        (root / 'diag.patch').write_text(patch)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', 'apply', '--check', 'diag.patch'], cwd=root, check=True)
        subprocess.run(['patch', '-p1', '--dry-run', '--quiet', '-i', 'diag.patch'], cwd=root, check=True)
        subprocess.run(['git', 'apply', 'diag.patch'], cwd=root, check=True)
        if target.read_text() != changed or (root / 'bsd/kern/pagani_diag.h').read_text() != (HERE / 'kernel/pagani_diag.h').read_text():
            fail('the applied patch differs from the intended files')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kern-exec', type=Path, default=DEFAULT_KERN_EXEC)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    original = args.kern_exec.read_text()
    patch, changed = make_patch(original)
    prove(original, patch, changed)
    if args.out:
        args.out.write_text(patch)
        print(f'PASS: {args.out} ({len(patch.splitlines())} lines) applies to {args.kern_exec}')
    else:
        sys.stdout.write(patch)


if __name__ == '__main__':
    main()
