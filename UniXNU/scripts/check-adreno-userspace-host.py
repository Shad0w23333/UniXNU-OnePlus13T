#!/usr/bin/env python3
"""Host checks of the A830 user-space changes that need no GPU and no host Vulkan driver.

  1. SurfaceMaintenance.h (plugin cache maintenance by direction, user-space patch M8), OwnerEnvironment.h (NativeAdrenoMetal.env, M3) and
     ClientRule.h (NativeAdrenoMetal.clients: names, prefixes, @apps/@extensions, exclusions; 2026-10-30), FamilyAnswer.h (RenderBox is
     told no Apple family above Apple2; 2026-10-31) and ClientReport.h (a client's report lines; 2026-10-31): C++ unit tests built with
     -Wall -Wextra -Werror and the address/undefined sanitizers.
  2. add-gpu-plugin-data.py: the --env file is refused where the plugin would ignore or cut a line.
  3. gpu_abi.py: the ABI directory the user-space builds use (drivers/Adreno830 first) and the refusal of two differing headers.
  4. The recorded Mesa patches (scripts/data/adreno-compiler-inputs.json) reconstruct the working Mesa tree, and the Turnip darwin backend
     carries the A830 changes: 0x44050001 named 830 (M5), one connection per process (M5), IO-coherent owned BOs on the kernel's flag (M6),
     no CPU wait on syncs already resolved to a ring fence (M7), and darwin_bo_finish clearing the sparse-array entry before the record is
     queued or anything is reaped (M10). Skipped with a note when .cache/mesa-work is absent.
  5. The A830 fixes of annibale-port/userspace-a830.md are recorded in their order: the upstream backports (turnip-a830-upstream.patch),
     the local Turnip/ir3 fixes (turnip-a830-fixes.patch) and the darwin backend's DSB SY before submission and per-memory-type coherence.
The behaviour of the Turnip changes over the host kernel stack is check-turnip-darwin-host.py's (host Turnip build required)."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'drivers/AdrenoMetal'
OUT = ROOT / 'build/adreno-userspace-host'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'scripts'))

# 1. C++ unit tests.
flags = ['xcrun', 'clang++', '-std=c++17', '-O2', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
         '-fno-sanitize-recover=all']
for name in ('surface_maintenance_host', 'owner_environment_host', 'client_rule_host', 'family_answer_host', 'client_report_host'):
    binary = OUT / name
    subprocess.run([*flags, KIT / 'bridge' / (name + '.cpp'), '-o', binary], check=True)
    with tempfile.TemporaryDirectory(prefix=name + '-', dir=OUT) as work:
        run = subprocess.run([str(binary), work], capture_output=True, text=True, timeout=60)
    (OUT / (name + '.log')).write_text(run.stdout + run.stderr)
    passed = [l for l in run.stdout.splitlines() if l.startswith('PASS')]
    if run.returncode or not passed:
        sys.exit('%s failed (exit %d); see %s' % (name, run.returncode, OUT / (name + '.log')))
    print(passed[0])

# 2. The Data seed tool's --env validation (the same rules as OwnerEnvironment.h).
spec = importlib.util.spec_from_file_location('add_gpu_plugin_data', ROOT / 'scripts/add-gpu-plugin-data.py')
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
with tempfile.TemporaryDirectory(prefix='env-', dir=OUT) as work:
    good = Path(work) / 'good.env'
    good.write_text('# first boots\nTU_DEBUG=sysmem,nocb\nMETAL2VULKAN_COHERENT_IMPORTS=0\n\n')
    seed.check_env(good)
    for bad in ('DYLD_INSERT_LIBRARIES=/x\n', 'TU_DEBUG\n', '=x\n', 'TU_X=' + 'y' * 260 + '\n'):
        (Path(work) / 'bad.env').write_text(bad)
        try:
            seed.check_env(Path(work) / 'bad.env')
        except SystemExit:
            continue
        sys.exit('add-gpu-plugin-data.py accepted an env line the plugin ignores: ' + bad.strip()[:40])
print('PASS: add-gpu-plugin-data.py --env accepts KEY=VALUE lines of the driver prefixes and refuses lines the plugin would ignore or cut')
# The --clients tokens are ClientRule.h's: names, prefix*, the classes and exclusions; anything else is refused.
for token in ('iconservicesagent', 'com.apple.WebKit.*', '*', '@apps', '@extensions', '!SpringBoard', '!@extensions'):
    if not seed.client_token(token):
        sys.exit('add-gpu-plugin-data.py refused the --clients token ' + token)
for token in ('@app', '@', '!', '!!x', '@apps*', 'a b', 'x/y', ''):
    if seed.client_token(token):
        sys.exit('add-gpu-plugin-data.py accepted the --clients token %r' % token)
print('PASS: add-gpu-plugin-data.py --clients takes the ClientRule.h tokens (names, prefix*, @apps, @extensions, !token) and nothing else')

# 3. ABI directory choice.
from gpu_abi import abi_dir
with tempfile.TemporaryDirectory(prefix='abi-', dir=OUT) as work:
    root = Path(work)
    for kit in ('drivers/Adreno740', 'drivers/Adreno830'):
        (root / kit).mkdir(parents=True)
    (root / 'drivers/Adreno740/NativeAdrenoABI.h').write_text('v2\n')
    saved = os.environ.pop('UNIXNU_GPU_ABI_DIR', None)
    try:
        assert abi_dir(root) == (root / 'drivers/Adreno740').resolve(), 'only A740 present'
        (root / 'drivers/Adreno830/NativeAdrenoABI.h').write_text('v2\n')
        assert abi_dir(root) == (root / 'drivers/Adreno830').resolve(), 'A830 preferred'
        assert abi_dir(root, root / 'drivers/Adreno740') == (root / 'drivers/Adreno740').resolve(), 'explicit directory'
        (root / 'drivers/Adreno830/NativeAdrenoABI.h').write_text('v3\n')
        try:
            abi_dir(root)
            sys.exit('gpu_abi.py accepted two different NativeAdrenoABI.h')
        except SystemExit as refusal:
            if 'differ' not in str(refusal):
                raise
    finally:
        if saved is not None:
            os.environ['UNIXNU_GPU_ABI_DIR'] = saved
print('PASS: gpu_abi.py picks drivers/Adreno830 over drivers/Adreno740, honours an explicit directory and refuses differing ABI headers')

# 4. Recorded Mesa patches and the Turnip darwin backend changes.
MESA = ROOT / '.cache/mesa-work'
pins = json.loads((ROOT / 'scripts/data/adreno-compiler-inputs.json').read_text())
order = pins['patches']
assert order.index('patches/mesa/turnip-a8xx-backports.patch') < order.index('patches/mesa/turnip-darwin-kmd.patch')
darwin = (ROOT / 'patches/mesa/turnip-darwin-kmd.patch').read_text()
backend = darwin[darwin.index('+++ b/src/freedreno/vulkan/tu_knl_darwin.cc'):]
finish = backend[backend.index('+darwin_bo_finish('):]
finish = finish[:finish.index('\n+}\n')]
steps = [finish.index('memset(bo, 0, sizeof(*bo));'), finish.index('darwin_reap_locked(dev, retire);'),
         finish.index('retire->entries[slot] = record;')]
if steps != sorted(steps):
    sys.exit('darwin_bo_finish must clear the sparse-array entry before it reaps or queues the record (M10)')
for needle, what in (('info->chip_id == 0x44050001', 'A830 chip id named 830 (M5)'),
                     ('TU_DARWIN_REOPEN', 'one connection per process (M5)'),
                     ('NativeAdrenoDeviceCachedCoherent', 'IO-coherent owned BOs on the kernel flag (M6)'),
                     ('TU_DARWIN_CPU_WAITS', 'ring-ordered waits (M7)')):
    if needle not in backend:
        sys.exit('turnip-darwin-kmd.patch lacks ' + what)
backports = (ROOT / 'patches/mesa/turnip-a8xx-backports.patch').read_text()
if re.search(r'^\+.*\bcs->(mem_to_reg|cond_write|reg_to_mem|rmw)\(', backports, re.M):
    sys.exit('turnip-a8xx-backports.patch uses tu_cs helpers the pinned Mesa does not have')
# The A830 fixes (annibale-port/userspace-a830.md): upstream backports and the local Turnip/ir3 fixes sit between the gen8 backports and
# the darwin backend; the darwin backend fences submissions and maps only HOST_CACHED | HOST_COHERENT memory IO-coherent.
names = ['patches/mesa/turnip-a8xx-backports.patch', 'patches/mesa/turnip-a830-upstream.patch', 'patches/mesa/turnip-a830-fixes.patch',
         'patches/mesa/turnip-darwin-kmd.patch']
if any(name not in order for name in names) or [order.index(name) for name in names] != sorted(order.index(name) for name in names):
    sys.exit('adreno-compiler-inputs.json must list the gen8 backports, turnip-a830-upstream, turnip-a830-fixes and the darwin backend in that order')
upstream = (ROOT / 'patches/mesa/turnip-a830-upstream.patch').read_text()
for needle, what in (('+   tu_cs_emit_wfi(cs);', '99fed09b (WFI after the CACHE_CLEAN in tu6_clear_lrz)'),
                     ('+        prefetch_sam_helpers_quirk = True,', '189ee824 (prefetch sam.s2en keeps helpers, a7xx_base)'),
                     ('+bool ir3_prefetch_sam_needs_helpers(', '189ee824'),
                     ('+        alias_predication_quirk = True,', '84f09016 (no alias.tex inside predt/predf/prede, a7xx_base)'),
                     ('+      block_is_predicated = terminator &&', '84f09016'),
                     ('+        QCTDD13523866_dummy_alu = True,', 'c503b0a1 (QCTDD13523866 dummy ALU, a7xx_gen3)'),
                     ('+   foreach_main_block (block, ir) {', 'a08a0b93 (main-shader blocks only)')):
    if needle not in upstream:
        sys.exit('turnip-a830-upstream.patch lacks ' + what)
fixes = (ROOT / 'patches/mesa/turnip-a830-fixes.patch').read_text()
end_cb = fixes[fixes.index('+#if defined(TU_HAS_DARWIN) && defined(__aarch64__)'):]
if '+   __asm__ volatile("dsb sy" ::: "memory");' not in end_cb[:1200] or 'vk_command_buffer_end' not in end_cb[:1600]:
    sys.exit('turnip-a830-fixes.patch must end tu_EndCommandBuffer with DSB SY in the darwin build')
if '+   abort();' not in fixes or '-   UNREACHABLE("");' not in fixes:
    sys.exit('turnip-a830-fixes.patch must make ir3_context_error() abort instead of reaching UNREACHABLE()')
if '+       (CHIP >= A8XX || !(flushes & TU_CMD_FLAG_CACHE_INVALIDATE))) {' not in fixes or \
        fixes.count('+         TU_CMD_FLAG_CCHE_INVALIDATE | TU_CMD_FLAG_WAIT_FOR_IDLE;') != 3:
    sys.exit('turnip-a830-fixes.patch must invalidate CCHE on A8XX next to UCHE, including the three framebuffer-feedback flushes')
chunk = backend[backend.index('+darwin_submit_chunk('):]
chunk = chunk[:chunk.index('\n+}\n')]
if chunk.find('dsb sy') < 0 or chunk.find('dsb sy') > chunk.find('NativeAdrenoMethodSubmit'):
    sys.exit('darwin_submit_chunk must issue DSB SY before it hands the submission to the kernel (D1)')
bo_init = backend[backend.index('+darwin_bo_init(struct tu_device *dev, struct vk_object_base *base,'):]
bo_init = bo_init[:bo_init.index('\n+}\n')]
if '(mem_property & VK_MEMORY_PROPERTY_HOST_CACHED_BIT) &&' not in bo_init or '.writeback = coherent ? 1u : 0u,' not in bo_init or \
        'bo->darwin_coherent = coherent;' not in bo_init or '.writeback = dw.coherent' in backend or \
        '(bo->darwin_coherent ? kIOMapDefaultCache : kIOMapWriteCombineCache)' not in backend:
    sys.exit('the darwin backend must map only HOST_CACHED | HOST_COHERENT memory IO-coherent, per BO for the GPU and the CPU (D2)')
if (MESA / '.git').exists():
    from mesa_patch_identity import validate_recorded_patches
    head = subprocess.check_output(['git', '-C', str(MESA), 'rev-parse', 'HEAD'], text=True).strip()
    if head != pins['mesa']['commit']:
        sys.exit('The Mesa working tree is not at the pinned commit')
    validate_recorded_patches(ROOT, MESA, pins)
    print('PASS: the recorded Mesa patches (with the gen8 backports before the darwin backend) reconstruct the working tree; '
          'the darwin backend carries the A830 changes M5, M6, M7 and M10')
else:
    print('PASS: the darwin backend patch carries the A830 changes M5, M6, M7 and M10 (no .cache/mesa-work: identity not checked)')
print('PASS: the A830 fixes are recorded in order: 99fed09b, 189ee824, 84f09016, c503b0a1+a08a0b93 (turnip-a830-upstream), DSB SY at '
      'vkEndCommandBuffer, ir3_context_error abort and A8XX CCHE invalidation (turnip-a830-fixes), DSB SY before each submission and '
      'IO-coherent mapping only for HOST_CACHED | HOST_COHERENT memory (turnip-darwin-kmd)')
