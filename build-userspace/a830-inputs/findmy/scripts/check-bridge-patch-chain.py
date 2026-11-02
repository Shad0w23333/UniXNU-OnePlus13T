#!/usr/bin/env python3
"""The translator source rule of scripts/build-adreno-metal-bridge.py, checked without building: the build tree's metal2vulkan checkout
must be its pin plus a prefix of the recorded patches (UniXNU/scripts/build-adreno-metal-bridge.py PATCHES, read from that script), and
the whole list, 0018 included, must apply. Works on scratch index files under findmy/build (as the build script does with its own);
the checkout's own index and working tree are not touched. Prints the tree after each step and which prefix the checkout holds."""
import ast
import os
from pathlib import Path
import subprocess
import sys

UNIXNU = Path('/Users/xiaochoumao/unixnu-13t/UniXNU')
SOURCE = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/tree/.cache/gpu-reference/metal2vulkan')
SCRATCH = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/findmy/build/scratch-index')
script = (UNIXNU / 'scripts/build-adreno-metal-bridge.py').read_text()
# The patch names, from the list literal the build script iterates.
start = script.index("for name in [") + len("for name in ")
names = ast.literal_eval(script[start:script.index("]]", start) + 1])
patches = [UNIXNU / 'drivers/AdrenoMetal/bridge/patches' / name for name in names]
for patch in patches:
    if not patch.is_file():
        sys.exit('missing patch ' + str(patch))


def tree(*steps):
    env = dict(os.environ, GIT_INDEX_FILE=str(SCRATCH))
    SCRATCH.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(['git', '-C', str(SOURCE), 'read-tree', 'HEAD'], env=env, check=True)
        for step in steps:
            subprocess.run(['git', '-C', str(SOURCE)] + step, env=env, check=True)
        return subprocess.check_output(['git', '-C', str(SOURCE), 'write-tree'], env=env, text=True).strip()
    finally:
        SCRATCH.unlink(missing_ok=True)


states = [tree(*[['apply', '--cached', str(p)] for p in patches[:count]]) for count in range(len(patches) + 1)]
current = tree(['add', '-A'])
for count, state in enumerate(states):
    print('%-48s %s' % ('pin' if not count else patches[count - 1].name, state))
print('%-48s %s' % ('checkout working tree', current))
if current not in states:
    sys.exit('FAIL: the checkout is not the pin plus a prefix of the recorded patches')
held = states.index(current)
print('PASS: the checkout holds the pin and %d of %d recorded patches; the build applies the rest (%s)' %
      (held, len(patches), ', '.join(p.name for p in patches[held:]) or 'none'))
