#!/usr/bin/env python3
"""The bridge's runtime identity, computed the way UniXNU/scripts/build-adreno-metal-bridge.py computes it, but read-only: the translator
checkout must already be at its pin plus every recorded patch (nothing is applied here). Prints JSON: pin, tree, runtime_id."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TREE = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/tree')
SOURCE = TREE / '.cache/gpu-reference/metal2vulkan'
PATCH_DIR = TREE / 'drivers/AdrenoMetal/bridge/patches'


def patch_list():
    # The order build-adreno-metal-bridge.py applies them in (its PATCHES list), read from that script so a new patch is not missed.
    import re
    text = (TREE / 'scripts/build-adreno-metal-bridge.py').read_text()
    start = text.index('PATCHES = [')
    end = text.index(']]', start)
    return [PATCH_DIR / n for n in re.findall(r"'(\d{4}-[^']+\.patch)'", text[start:end])]


def git_tree(index_dir, *steps):
    index = Path(index_dir) / 'index'
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    subprocess.run(['git', '-C', str(SOURCE), 'read-tree', 'HEAD'], env=env, check=True)
    for step in steps:
        subprocess.run(['git', '-C', str(SOURCE)] + step, env=env, check=True, capture_output=True)
    tree = subprocess.check_output(['git', '-C', str(SOURCE), 'write-tree'], env=env, text=True).strip()
    index.unlink()
    return tree


def main():
    pin = json.loads((TREE / 'scripts/data/gpu-metal-vulkan-inputs.json').read_text())['translator']['commit']
    head = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    if head != pin:
        sys.exit('translator checkout is not at its pin')
    patches = patch_list()
    with tempfile.TemporaryDirectory(prefix='m2v-index-') as td:
        final = git_tree(td, *[['apply', '--cached', str(p)] for p in patches])
        current = git_tree(td, ['add', '-A'])
    runtime_id = hashlib.sha256((pin + '\n' + final).encode()).hexdigest()[:32]
    print(json.dumps(dict(pin=pin, patches=[p.name for p in patches], final_tree=final, current_tree=current,
                          at_final=current == final, runtime_id=runtime_id)))
    if current != final:
        sys.exit(1)


if __name__ == '__main__':
    main()
