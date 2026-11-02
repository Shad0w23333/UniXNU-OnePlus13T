#!/usr/bin/env python3
"""Regenerate patches/mesa/turnip-darwin-kmd.patch from the working Mesa tree.

The patch takes its place in the recorded order (scripts/data/adreno-compiler-inputs.json): it is the difference, for the files
the Darwin backend touches, between "pinned Mesa + the recorded patches before it" and "the working tree with the recorded
patches after it taken back out" (turnip-builtin-shader-pack.patch, for one, edits lines this patch adds). Edit the Mesa tree, run
this; it then proves the working tree equals the pinned source plus every recorded patch (the build's identity check)."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from mesa_patch_identity import validate_recorded_patches

ROOT = Path(__file__).resolve().parents[1]
MESA = ROOT / '.cache/mesa-work'
NEW = ['src/freedreno/vulkan/tu_knl_darwin.cc']
OUT = ROOT / 'patches/mesa/turnip-darwin-kmd.patch'
# Every file the recorded patch already touches stays in it (tu_device.h carries the retirement state pointer): a file missing
# here would silently drop its hunk from the regenerated patch, and the identity check would then refuse the tree.
MINE = sorted(set(['meson.options', 'src/freedreno/vulkan/meson.build', 'src/freedreno/vulkan/tu_knl.cc',
                   'src/freedreno/vulkan/tu_knl.h', 'src/freedreno/vulkan/tu_device.cc', 'src/freedreno/vulkan/tu_device.h',
                   'src/vulkan/wsi/wsi_common_metal_layer.m', 'src/util/cache_ops_aarch64.c'] + NEW +
                  (re.findall(r'^diff --git a/(\S+) b/\1$', OUT.read_text(), re.M) if OUT.is_file() else [])))
pins = json.loads((ROOT / 'scripts/data/adreno-compiler-inputs.json').read_text())
position = pins['patches'].index(str(OUT.relative_to(ROOT)))
before, after = pins['patches'][:position], pins['patches'][position + 1:]


def git(tmp, *args, check=True):
    return subprocess.run(['git', '-c', 'user.email=a@b', '-c', 'user.name=n', *args], cwd=tmp, check=check,
                          capture_output=True, text=True).stdout


def touched(patch):
    return set(re.findall(r'^diff --git a/(\S+) b/\1$', (ROOT / patch).read_text(), re.M)) & set(MINE)


def apply(tmp, patch, *extra):
    # Only this patch's hunks on the backend's files; a recorded patch that touches one of them must apply exactly.
    files = touched(patch)
    if files:
        result = subprocess.run(['git', 'apply', *extra, *('--include=' + f for f in sorted(files)), str(ROOT / patch)],
                                cwd=tmp, capture_output=True, text=True)
        if result.returncode:
            raise SystemExit('Recorded patch ' + patch + ' does not fit the backend files in order:\n' + result.stderr)


tmp = Path(tempfile.mkdtemp(dir=ROOT / '.cache'))
try:
    base, edited = tmp / 'base', tmp / 'edited'
    for directory in (base, edited):
        git(tmp, 'init', '-q', directory.name)
    for f in MINE:
        if f not in NEW:
            (base / f).parent.mkdir(parents=True, exist_ok=True)
            (base / f).write_bytes(subprocess.run(['git', 'show', 'HEAD:' + f], cwd=MESA, check=True, capture_output=True).stdout)
        if (MESA / f).is_file():
            (edited / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(MESA / f, edited / f)
    for patch in before:
        apply(base, patch)
    for patch in reversed(after):
        apply(edited, patch, '--reverse')
    git(base, 'add', '-A')
    git(base, 'commit', '-q', '-m', 'pinned source and the recorded patches before the backend', '--allow-empty')
    for f in MINE:
        if (edited / f).is_file():
            (base / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(edited / f, base / f)
        elif (base / f).exists():
            (base / f).unlink()
    for f in NEW:
        if (base / f).is_file():
            git(base, 'add', '-N', f)
    OUT.write_text(git(base, '-c', 'diff.noprefix=false', '-c', 'diff.mnemonicPrefix=false', 'diff', '--binary', '--no-color',
                       '--no-ext-diff'))
finally:
    shutil.rmtree(tmp)
# Re-register the identity at once: the working tree must equal the pinned source plus the recorded patches, in their order.
validate_recorded_patches(ROOT, MESA, pins)
print('wrote', OUT.relative_to(ROOT), len(OUT.read_text().splitlines()), 'lines; the recorded patches reconstruct the working tree')
