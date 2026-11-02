#!/usr/bin/env python3
"""seedpeek.py SEED.dbrdisk SCRATCH [ -- 命令...]：把种子解包到 SCRATCH，只读挂载，打印 launchd 禁用表、Metal 客户端名单，
然后在挂载点执行给定的 shell 命令（$MNT 指向挂载点），最后卸载。只读，不改种子。"""
import importlib.util, os, plistlib, subprocess, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('rp', '/Users/xiaochoumao/unixnu-13t/工具/replace-phone-wallpaper.py')
rp = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(rp)
seed, scratch = Path(sys.argv[1]), Path(sys.argv[2])
cmd = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
scratch.mkdir(parents=True, exist_ok=True)
raw, mount = scratch / 'seed.raw', scratch / 'mount'
mount.mkdir(exist_ok=True)
if not raw.exists() or raw.stat().st_mtime < seed.stat().st_mtime:
    rp.unpack(seed, raw)
dev = rp.attach(raw, mount, readonly=True)
try:
    p = mount / 'db/com.apple.xpc.launchd/disabled.plist'
    if p.exists():
        d = plistlib.loads(p.read_bytes())
        print('disabled.plist:', sorted(k for k, v in d.items() if v), '| enabled:', sorted(k for k, v in d.items() if not v))
    else:
        print('没有 disabled.plist')
    for c in mount.rglob('NativeAdrenoMetal.clients'):
        print(c, repr(c.read_text()[:400]))
    if cmd:
        subprocess.run(' '.join(cmd), shell=True, env={**os.environ, 'MNT': str(mount)})
finally:
    rp.detach(dev)
