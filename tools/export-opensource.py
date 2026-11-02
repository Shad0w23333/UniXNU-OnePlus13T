#!/usr/bin/env python3
"""把工作区里我们自己的源码、脚本和文档导出到开源仓库的工作树（默认 ~/UniXNU-OnePlus13T），供 git 提交后推到 GitHub。

  export-opensource.py [目标目录]

只导出文本源码（按扩展名白名单，单个文件不超过 2 MiB），跳过符号链接、挂载点和下面这些目录：构建产物、手机日志、苹果文件、
专有固件、种子与镜像、原厂备份、上游参考快照。导出时把两台设备的序列号和个人邮箱换成占位符。目标目录里除 .git、README.md、
LICENSE 和 .gitignore 以外的内容会先清空再写，所以重跑就是一次同步。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOP = Path(os.environ.get('UNIXNU_TOP', '/Users/xiaochoumao/unixnu-13t'))
DST = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / 'UniXNU-OnePlus13T')
KEEP = {'.git', 'README.md', 'LICENSE', '.gitignore'}
MAX_BYTES = 2 * 1024 * 1024
EXT = {'.c', '.h', '.cc', '.cpp', '.hpp', '.m', '.mm', '.rs', '.s', '.S', '.inc', '.def', '.py', '.sh', '.toml', '.json', '.md', '.txt',
       '.patch', '.diff', '.dts', '.dtsi', '.yaml', '.yml', '.cfg', '.ld', '.lds', '.entitlements', '.xcconfig', '.snippet', '.lock',
       '.cmake', '.in', '.mk', '.plist', '.strings', '.modulemap', '.map', '.tsv', '.csv', '.conf', '.ini', '.fdf', '.dsc', '.dec', '.inf',
       '.asl', '.aslc', '.vfr', '.uni', '.wgsl', '.metal', '.glsl', '.comp', '.frag', '.vert'}
NAMES = {'LICENSE', 'LICENSE.txt', 'NOTICE', 'COPYING', 'Makefile', 'README', 'Cargo.toml', 'Cargo.lock', '.gitignore', 'series',
         'pagani-series', 'meson.build', 'CMakeLists.txt', 'unixnu'}
# Directory names never exported, wherever they are.
SKIP = {'.git', 'out', 'out-gpu', 'build', '.cache', 'runs', 'deps', 'ath12k-deps', 'odm-firmware', 'bt-local', 'evidence', 'regress',
        'final', 'snapshots', 'osmnt-21H461', 'systemos-21H461', 'dt', 'ipsw', 'images', 'inputs', 'pstore', 'ufs', '__pycache__',
        'node_modules', 'target', 'logs', 'opensource', 'iphone-17.7-21H16', 'shader-cache', 'tree', '.venv', 'firmware-blobs'}
# Text of other devices and people that has no place in a public tree.
SCRUB = [('SERIAL_13T', 'SERIAL_13T'), ('SERIAL_OTHER_DEVICE', 'SERIAL_OTHER_DEVICE'), ('author@example.com', 'author@example.com'),
         ('author@example.com', 'author@example.com')]
# The research trees keep only what we wrote: scripts and reports. Their dumps of Apple binaries (disassembly, symbol and string tables) and the
# copies of other projects under them stay out.
RESEARCH = {'.md', '.py', '.sh', '.patch', '.diff'}
RESEARCH_SKIP = {'re', 'strings', 'ios-data', 'm2v', 'crates', 'q', 'UniXNU-27', 'kits'}
# (source under TOP, target under DST, how): git = what git tracks plus untracked files it does not ignore; walk = the directory tree;
# research = the directory tree, RESEARCH files only.
SOURCES = [
    ('Adreno830', 'Adreno830', 'git'),
    ('UniXNU-Pagani-Drivers', 'UniXNU-Pagani-Drivers', 'git'),
    ('UniXNU-Pagani_SM8750-gpu', 'UniXNU-Pagani_SM8750', 'git'),
    ('UniXNU-sm8750-common', 'UniXNU-sm8750-common', 'git'),
    ('UniXNU', 'UniXNU', 'git'),
    ('工具', 'tools', 'walk'),
    ('build-userspace', 'build-userspace', 'research'),
    ('ios27-experiment', 'ios27-experiment', 'research'),
]
DOCS = [('功能盘点.md', 'docs/功能盘点.md'), ('测试清单.md', 'docs/测试清单.md'), ('路线图.md', 'docs/路线图.md')]


def wanted(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.suffix not in EXT and path.name not in NAMES:
        return False
    return path.stat().st_size <= MAX_BYTES


def git_files(repo: Path):
    out = subprocess.run(['git', '-C', str(repo), 'ls-files', '-co', '--exclude-standard', '-z'], capture_output=True, check=True).stdout
    for name in out.decode().split('\0'):
        if name and not set(Path(name).parts[:-1]) & SKIP:
            yield Path(name)


def walk_files(root: Path, skip=frozenset()):
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and d not in skip and not os.path.ismount(os.path.join(here, d))
                   and not os.path.islink(os.path.join(here, d))]
        for f in files:
            yield (Path(here) / f).relative_to(root)


def copy(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return
    for old, new in SCRUB:
        text = text.replace(old, new)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)
    dst.chmod(src.stat().st_mode & 0o755 | 0o644)


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    for entry in DST.iterdir():
        if entry.name not in KEEP:
            shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    count = 0
    for src, dst, how in SOURCES:
        root = TOP / src
        files = git_files(root) if how == 'git' else walk_files(root, RESEARCH_SKIP if how == 'research' else frozenset())
        for rel in files:
            path = root / rel
            if wanted(path) and (how != 'research' or path.suffix in RESEARCH):
                copy(path, DST / dst / rel)
                count += 1
    for src, dst in DOCS:
        copy(TOP / src, DST / dst)
        count += 1
    uefi = subprocess.run(['git', '-C', str(TOP / 'UEFI-Pagani_SM8750'), 'diff', 'HEAD'], capture_output=True, check=True).stdout
    if uefi:
        (DST / 'patches').mkdir(exist_ok=True)
        (DST / 'patches/uefi-mu-silicium-oneplus13t.patch').write_bytes(uefi)
        count += 1
    print(f'exported {count} files to {DST}')


if __name__ == '__main__':
    main()
