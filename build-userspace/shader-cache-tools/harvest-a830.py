#!/usr/bin/env python3
"""The harvest of the render server's pipelines (jitter F2): what a phone compiled in a run goes into the next image's pack, so that the same
flows no longer compile on the render thread. Three steps, the middle one the owner's:

  harvest-a830.py capture [--dry-run]         the capture seed (the current final seed with the export marker) and its image
  (the owner runs the capture image through the flows: harvest-a830.py plan prints the list; gpu-loop.py reads <run>/ufs/export back)
  harvest-a830.py check RUN [RUN ...]         what the runs brought: files, bytes, what the pack would take, the costliest pipelines
  harvest-a830.py merge RUN [RUN ...] [--budget-mib N] [--journal] [--dry-run]    the harvested seed and its image; the most important run first

The marker and the pack are data: neither changes the plugin, Turnip or the owner trust bundle, so capture and merge both keep the final seed's
trust bundle and need no kernel, plugin or Turnip rebuild. What ends the validity of a harvest: another runtime build (a change of the bridge's
recorded patches) or another Turnip; a change of the frontend or the bridge's glue does not (make-shader-cache-a830.py checks both identities).

The budget: the payload takes 95 % of board.toml's size_mib (182.4 MiB of 192), the kernel and the seed leave about 5 MiB (2026-11-01), so a pack
has about 4 MiB, compressed; a raw export compresses by about 3.9 here. Over the budget the pack leaves files out (make-shader-cache-a830.py
merge_export: earlier runs first, the largest files first). Every step only reads the final seed. Never touches the phone, never commits."""
import argparse
import json
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path

sys.dont_write_bytecode = True
TOP = Path('/Users/xiaochoumao/unixnu-13t')
B = TOP / 'build-userspace'
FINAL = B / 'a830-inputs/final'
TOOL = B / 'make-shader-cache-a830.py'
STAGE = B / 'a830-inputs/fullrun/make-stage16.sh'
LAYOUT = TOP / '工具/make-a830-layout.sh'
LAYOUT_OUT = '测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用.img'
PANEL, RESET = '65', '5400'


def run(command, dry, **kwargs):
    print('+ ' + ' '.join(str(c) for c in command), flush=True)
    if not dry:
        subprocess.run([str(c) for c in command], check=True, **kwargs)


def stage_args():
    """BASE and FIX of make-stage16.sh, as that script computes them: the images keep the current boot arguments and fixups."""
    text = STAGE.read_text()
    lines = [l for l in text.splitlines() if re.match(r'(BASE|FIX)=', l)]
    if len(lines) != 2:
        raise SystemExit('make-stage16.sh no longer sets BASE and FIX on lines of their own')
    out = subprocess.run(['bash', '-c', 'cd %s && %s\nprintf "%%s\\n%%s\\n" "$BASE" "$FIX"' % (TOP, '\n'.join(lines))], capture_output=True, text=True, check=True)
    return out.stdout.split('\n')[:2]


def image(seed, trust, name, dry):
    """As make-stage16.sh's complete package: panel 65, a reset far away, sheng-log=1 (the export needs it)."""
    base, fixups = stage_args()
    env = dict(os.environ, A830_DROP_ARGS='startup_debug ', A830_PREBUILT_SEED=str(seed), A830_OWNER_TRUST=str(trust), A830_AUDIO='1', A830_SYSTEM_APPS='1',
               A830_BATTERY='1', A830_RESET_AFTER=RESET, A830_PANEL=PANEL, A830_EXTRA_FIXUPS=fixups, A830_EXTRA_ARGS=base + ' sheng-log=1')
    print('# A830_PREBUILT_SEED=%s A830_OWNER_TRUST=%s A830_EXTRA_ARGS="%s sheng-log=1"' % (seed, trust, base), flush=True)
    run([LAYOUT, 'phone-island-apps'], dry, env=env, cwd=TOP)
    out = TOP / LAYOUT_OUT
    run(['cp', out, TOP / '测试镜像' / name], dry)
    run(['cp', out.with_suffix('.payload.json'), (TOP / '测试镜像' / name).with_suffix('.payload.json')], dry)


def env_lines(args):
    return [x for line in args.env_line for x in ('--env-line', line)]


def capture(args):
    seed = args.output or FINAL / 's7-export.dbrdisk'
    run([sys.executable, '-B', TOOL, '--base', args.base, '--output', seed, '--replace-pack', '--with-cacheexport', '--force', *env_lines(args)], args.dry_run)
    if not args.no_image:
        image(seed, args.trust, 'boot-A830-收割-导出.img', args.dry_run)


def export_of(run_dir):
    export = Path(run_dir) / 'ufs/export'
    if not export.is_dir():
        raise SystemExit('%s has no ufs/export: no sheng-log=1 in that image, no marker in its seed, or the phone reset before an export was complete' % run_dir)
    return export


def facts_of(export):
    """What a run brought, from its files alone."""
    groups, raw, packed, count = {}, 0, 0, 0
    for path in export.rglob('*'):
        if not path.is_file():
            continue
        parts = path.relative_to(export).parts
        if len(parts) != 4 or parts[0] != 'v1':
            continue
        data = path.read_bytes()
        count += 1
        raw += len(data)
        packed += len(zlib.compress(data, 9))
        groups[parts[1]] = groups.get(parts[1], 0) + 1
    costly = []
    journal = export / 'pipelines.log'
    lines = journal.read_text(errors='replace').splitlines() if journal.is_file() else []
    for line in lines:
        m = re.match(r'[RS] .*?\bus=(\d+)\b.*? ok=(\d)', line)
        if m:
            label = re.search(r'\blabel=(\S+)', line)
            costly.append((int(m.group(1)), label.group(1) if label else '-', m.group(2) == '1'))
    costly.sort(reverse=True)
    return dict(files=count, raw_bytes=raw, packed_bytes=packed, groups=groups, journal_lines=len(lines), pipelines=len(costly),
                pipeline_ms=sum(c[0] for c in costly) // 1000, costliest=[(c[0] // 1000, c[1]) for c in costly[:5]])


def check(args):
    total = 0
    for run_dir in args.runs:
        facts = facts_of(export_of(run_dir))
        total += facts['packed_bytes']
        print(json.dumps(dict(run=str(run_dir), **facts), indent=1, ensure_ascii=False))
    print('compressed file by file: %.2f MiB against a budget of %.1f MiB%s' % (total / 2**20, args.budget_mib, '' if total <= args.budget_mib * 2**20 else
          '; the pack will leave files out, or raise size_mib in board.toml first'))


def merge(args):
    exports = [export_of(r) for r in args.runs] if not args.dry_run else [Path(r) / 'ufs/export' for r in args.runs]
    seed = args.output or FINAL / 's7-harvest.dbrdisk'
    command = [sys.executable, '-B', TOOL, '--base', args.base, '--output', seed, '--replace-pack', '--force', '--pack-budget-mib', args.budget_mib, *env_lines(args)]
    for export in exports:
        command += ['--merge-export', export]
    # The journal's pipelines come from the host as well: the host checks then prove the pack answers them with every Turnip compile forbidden.
    if args.journal:
        command += ['--journal', exports[0] / 'pipelines.log']
    run(command, args.dry_run)
    if not args.no_image:
        image(seed, args.trust, 'boot-A830-收割后.img', args.dry_run)


PLAN = """收割流程（jitter F2）：
1. 主线：python3 -B build-userspace/shader-cache-tools/harvest-a830.py capture
   产出 final/s7-export.dbrdisk（最终种子 + NativeAdrenoMetal.cacheexport）和 测试镜像/boot-A830-收割-导出.img。
2. 用户跑一遍（镜像带 sheng-log=1，5400 秒才复位）：
   python3 工具/gpu-test-loop/gpu-loop.py --image 测试镜像/boot-A830-收割-导出.img --label harvest --yes
   手机起来后按顺序做一遍，每项都从冷开始：桌面翻页、下拉通知中心、控制中心、App 切换器（含杀后台）、Spotlight、键盘（备忘录里打字、切换输入法）、
   设置里的各级页面、壁纸和海报页、小组件编辑、每个系统 App 打开再退出、锁屏再解锁、截屏。
   做完放着不动至少 60 秒：导出线程要看到目录连续两次（各 15 秒）不变才发，发完才算完整。然后再复位回安卓。
3. 主线：python3 -B build-userspace/shader-cache-tools/harvest-a830.py check 工具/gpu-test-loop/runs/<run>
   看 ufs/export 的文件数、压缩后大小和最贵的管线；压缩后超过预算时 merge 会按“先给排在前面的运行、同一运行先给大文件”裁掉放不下的。
4. 主线：python3 -B build-userspace/shader-cache-tools/harvest-a830.py merge 工具/gpu-test-loop/runs/<run> [更多 run] [--journal]
   产出 final/s7-harvest.dbrdisk 和 测试镜像/boot-A830-收割后.img。--journal 把 pipelines.log 也在主机回放一遍：严格消费者（禁止 Turnip 编译）
   证明这些管线都能从包里取到，但回放的条目算基线，不会被裁，预算紧时先不加。
5. 同样的流程再跑一轮，对照 gpu sec 行的 pl、vk 和 V cache 行的 hit/miss。
失效条件：改了 bridge 的 patches（运行时构建 ID 变）或改了 Turnip（pipelineCacheUUID 变）就要重新收割；只改前端、Rust 粘合层、内核不失效。"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # s7-final, not s6-final: the flows type (make-a830-final-seed.sh's s7 stops the keyboard's feedback loop), and the harvested seed replaces the
    # final seed of the images, which must not lose it. s7b-final is a variant to compare with, not a base.
    ap.add_argument('--base', type=Path, default=FINAL / 's7-final.dbrdisk', help='the seed to start from (the final seed)')
    ap.add_argument('--trust', type=Path, default=FINAL / 's7-final-owner-trust.bundle', help="that seed's owner trust bundle")
    ap.add_argument('--output', type=Path, help='the seed to write')
    ap.add_argument('--dry-run', action='store_true', help='print the commands only')
    ap.add_argument('--no-image', action='store_true', help='stop at the seed')
    ap.add_argument('--env-line', action='append', default=[], metavar='KEY=VALUE', help="a line for the seed's NativeAdrenoMetal.env, such as METAL2VULKAN_SURFACE_CLEAN=seed")
    ap.add_argument('--budget-mib', type=float, default=4.0, help='the largest pack (default 4; the payload had about 5 MiB left on 2026-11-01)')
    ap.add_argument('--journal', action='store_true', help="also replay the first run's pipelines.log on the host")
    ap.add_argument('step', choices=('plan', 'capture', 'check', 'merge'))
    ap.add_argument('runs', nargs='*', type=Path, help='evidence directories of gpu-loop.py, the most important first')
    args = ap.parse_args()
    if args.step == 'plan':
        print(PLAN)
    elif args.step == 'capture':
        capture(args)
    elif not args.runs:
        ap.error(args.step + ' needs a run directory')
    elif args.step == 'check':
        check(args)
    else:
        merge(args)


if __name__ == '__main__':
    main()
