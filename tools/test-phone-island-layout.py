#!/usr/bin/env python3
"""Host tests of the Dynamic Island layout (工具/make-a830-layout.sh phone-island, phone-island-apps):
python3 -B 工具/test-phone-island-layout.py -v

1. Dry runs of a copy of make-a830-layout.sh in a sandbox whose UniXNU/unixnu and 工具/make-phone-seed.py are stubs: the
   board.toml each variant hands to ./unixnu payload, the kernel arguments, the seed options, the image names, and board.toml
   restored afterwards (also when ./unixnu fails). Nothing outside the sandbox is written.
2. The island board through UniXNU's own profile merge and ADT generator (scripts/unixnu, the out-gpu hardware.dtb), parsed back.
3. The ADT values against the conditions read from the 21H461 shared cache (addresses in IslandPredicates), on the 13T's punch
   hole; with the extracted cache (A830_DSC_DIR) the constants and instructions behind them are read back from it.
4. A830_OWNED_MEMORY and A830_VM_COMPRESSOR: the board of one image, the ADT they give, what they refuse, and a sandbox copy of
   build-userspace/a830-inputs/fullrun/make-stage16.sh through this script: its three images, one change each, and their command lines.
Tests whose input is missing are skipped."""
import difflib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parent
LAYOUT = HERE / 'make-a830-layout.sh'
IMAGES = HERE / 'make-a830-images.sh'
BOARD = TOP / 'UniXNU-Pagani_SM8750-gpu/board.toml'
OS_PROFILE = TOP / 'UniXNU/profiles/os/ipados-17.7.11-21H461/profile.toml'
HARDWARE_DTB = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/device-tree/hardware.dtb'
KERNEL = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho'
STAGE16 = TOP / 'build-userspace/a830-inputs/fullrun/make-stage16.sh'
VT_FIXUPS = TOP / 'build-userspace/a830-inputs/ui-rest/integrate/vt-fixups.txt'
PROPERTIES = TOP / '一加13T原厂备份/设备信息/系统属性.txt'
# The 21H461 shared cache: A830_DSC_DIR, else the SystemOS volume (044-76034-038.dmg) mounted read-only under build-userspace (the
# scratchpad copy the tests used before was wiped with the session, 2026-10-31).
DSC_DIR = Path(os.environ.get('A830_DSC_DIR', str(TOP / 'build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/'
                                                         'com.apple.dyld')))
sys.path.insert(0, str(TOP / 'UniXNU/scripts'))

FIXUP = 'dyld_shared_cache_arm64.25,219d270,0000000000207940,0000000000487a40'
# The Core Image fix-up (every variant, board.toml [payload] text_fixups -> /chosen/text-fixups): _wrapGLIsUsable "mov w0, #1; ret"
# becomes "mov w0, #0; ret", the next function (_wrapGLIsAccelerated) compared unchanged.
CI_FIXUP = 'dyld_shared_cache_arm64.07,2e2cf98,20008052c0035fd620008052c0035fd6,00008052c0035fd620008052c0035fd6'
# The board's list replaces the OS profile's in UniXNU's merge: the profile's fix-ups come first, then the Core Image one.
PROFILE_FIXUPS = tomllib.loads(OS_PROFILE.read_text())['payload']['text_fixups'] if OS_PROFILE.is_file() else []
CI_LINE = 'text_fixups = [' + ', '.join(f'"{f}"' for f in PROFILE_FIXUPS + [CI_FIXUP]) + ']'
# The phone keyboard's frame (keyboard-405 F2): _UIKeyboardGetDerivedKeyboardForSpecificOrientation's 360x780 test becomes "height == 880".
KB_FIXUP = 'dyld_shared_cache_arm64.03,3503038,02c941fd0020621ee8179f1a698400f0,020d41fd2020621ef5179f1a05000014'


def fixup_line(ci, island, keyboard=True):
    """board.toml's [payload] text_fixups line: the profile's, then the Core Image, the island and the keyboard fix-ups (none: no
    line)."""
    extra = ([CI_FIXUP] if ci else []) + ([FIXUP] if island else []) + ([KB_FIXUP] if keyboard else [])
    return ['text_fixups = [' + ', '.join(f'"{f}"' for f in PROFILE_FIXUPS + extra) + ']'] if extra else []
J120_PRODUCT_ID = 'e9fefef6e6f9d12a5ba1a2551866cbbd749f0ec3'
WIDTH_PX, HEIGHT_PX, SCALE = 1216, 2640, 3

STUB_UNIXNU = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
top = Path(os.environ['STUB_TOP'])
board = (top / 'UniXNU-Pagani_SM8750-gpu/board.toml').read_text()
with open(top / 'calls.jsonl', 'a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'board': board}) + '\n')
if os.environ.get('STUB_FAIL') == sys.argv[1]:
    sys.exit(3)
if sys.argv[1] == 'payload':
    rec = Path('out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/payload/payload.json')
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text('{"stub": true}\n')
elif sys.argv[1] == 'bootimg':
    out = Path(sys.argv[sys.argv.index('-o') + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b'stub image\n')
'''
STUB_SEED = r'''import json, os, sys
from pathlib import Path
with open(Path(os.environ['STUB_TOP']) / 'seed-calls.jsonl', 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\n')
out = Path(sys.argv[-1])
out.write_bytes(b'stub seed\n')
out.with_suffix('.json').write_text('{}\n')
'''


def phone_entry_args(images_text):
    """The --kernel-arg values of make-a830-images.sh's loop-m8-phone entry, read the way make-a830-layout.sh reads them."""
    text = images_text.replace('\\\n', ' ')
    line = [line for line in text.splitlines() if line.startswith('image loop-m8-phone ')]
    words = shlex.split(line[0], comments=True)
    return [words[i + 1] for i, word in enumerate(words) if word == '--kernel-arg']


class Sandbox:
    def __init__(self, images_text=None):
        self.dir = tempfile.TemporaryDirectory(prefix='island-layout-')
        self.top = Path(self.dir.name)
        board_dir = self.top / 'UniXNU-Pagani_SM8750-gpu'
        board_dir.mkdir()
        shutil.copy(BOARD, board_dir / 'board.toml')
        self.board = board_dir / 'board.toml'
        self.original = BOARD.read_text()
        tools = self.top / '工具'
        tools.mkdir()
        text = LAYOUT.read_text()
        old = 'TOP=/Users/xiaochoumao/unixnu-13t\n'
        if text.count(old) != 1:
            raise AssertionError('make-a830-layout.sh: no single TOP line')
        self.script = tools / 'make-a830-layout.sh'
        self.script.write_text(text.replace(old, f'TOP={self.top}\n'))
        self.images_text = images_text if images_text is not None else IMAGES.read_text()
        (tools / 'make-a830-images.sh').write_text(self.images_text)
        (tools / 'make-phone-seed.py').write_text(STUB_SEED)
        (tools / 'phone-wallpaper-overlay').mkdir()
        build = self.top / 'build-userspace/tree/build'
        build.mkdir(parents=True)
        (build / 'data-a830-publish-gmem.dbrdisk').write_bytes(b'base seed\n')
        (self.top / 'UniXNU').mkdir()
        profile = self.top / 'UniXNU/profiles/os/ipados-17.7.11-21H461/profile.toml'
        profile.parent.mkdir(parents=True)
        shutil.copy(OS_PROFILE, profile)
        stub = self.top / 'UniXNU/unixnu'
        stub.write_text(STUB_UNIXNU)
        stub.chmod(0o755)

    def nm(self, pages):
        """A stub llvm-nm, first on the PATH of the runs from now on, whose kernel has this many pages of bootstrap tables."""
        tools = self.top / 'bin'
        tools.mkdir(exist_ok=True)
        stub = tools / 'llvm-nm'
        stub.write_text('#!/bin/sh\n'
                        'echo "                 U _printf"\n'
                        'echo "fffffff007190000 S _before"\n'
                        'echo "fffffff007195000 S _bootstrap_pagetables"\n'
                        f'echo "{0xfffffff007195000 + pages * 4096:016x} S _avail_end"\n')
        stub.chmod(0o755)
        self.path = f'{tools}:{os.environ["PATH"]}'

    def run(self, variant, **env):
        environment = {k: v for k, v in os.environ.items() if not k.startswith('A830_') and k != 'LAYOUT_OUT_OF_BOX'}
        if getattr(self, 'path', None):
            environment['PATH'] = self.path
        environment.update(env, STUB_TOP=str(self.top))
        for name in ('calls.jsonl', 'seed-calls.jsonl'):
            (self.top / name).unlink(missing_ok=True)
        result = subprocess.run(['bash', str(self.script), variant], env=environment, capture_output=True, text=True)
        calls = [json.loads(line) for line in (self.top / 'calls.jsonl').read_text().splitlines()] \
            if (self.top / 'calls.jsonl').exists() else []
        seeds = [json.loads(line) for line in (self.top / 'seed-calls.jsonl').read_text().splitlines()] \
            if (self.top / 'seed-calls.jsonl').exists() else []
        return result, calls, seeds

    def close(self):
        self.dir.cleanup()


def kernel_args(argv):
    return [argv[i + 1] for i, word in enumerate(argv) if word == '--kernel-arg']


def merged(board_text):
    from unixnu.profiles import merge
    return merge(tomllib.loads(OS_PROFILE.read_text()), tomllib.loads(board_text))


def changed_lines(before, after):
    added, removed = [], []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith('+ '):
            added.append(line[2:])
        elif line.startswith('- '):
            removed.append(line[2:])
    return [line for line in added if line.strip()], removed


IDENTITIES = {
    'island': ['product-name = "iPhone 15 Pro"', 'product-description = "iPhone 15 Pro"', 'artwork-device-idiom = "phone"',
               'compatible-device-fallback = "iPhone15,2"', '[adt.root.string]', 'model = "iPhone16,1"',
               'artwork-scale-factor = 3', 'artwork-device-subtype = 2556', 'display-scale = 3'],
    'xsmax': ['product-name = "iPhone XS Max"', 'product-description = "iPhone XS Max"', 'artwork-device-idiom = "phone"',
              'compatible-device-fallback = "iPhone11,2"', '[adt.root.string]', 'model = "iPhone11,6"',
              'artwork-scale-factor = 3', 'artwork-device-subtype = 2688', 'display-scale = 3'],
}


class LayoutScript(unittest.TestCase):
    """make-a830-layout.sh in a sandbox: what each variant writes, and what it leaves alone."""

    def setUp(self):
        self.box = Sandbox()
        self.base = phone_entry_args(self.box.images_text)

    def tearDown(self):
        self.box.close()

    def check_run(self, variant, identity, target='J120', fixup=True, apps=False, out_of_box=False, ci=True, **env):
        result, calls, seeds = self.box.run(variant, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.board.read_text(), self.box.original, 'board.toml not restored')
        self.assertEqual([c['argv'][0] for c in calls], ['payload', 'bootimg'])
        board = calls[0]['board']
        self.assertEqual(calls[1]['board'], board)
        added, removed = changed_lines(self.box.original, board)
        self.assertEqual(sorted(removed), ['display-scale = 2'])
        # /chosen/pagani-build-time: this build's time, or A830_BUILD_TIME.
        built = [line for line in added if line.startswith('pagani-build-time = ')]
        self.assertEqual(len(built), 1)
        seconds = int(built[0].split(' = ')[1])
        if 'A830_BUILD_TIME' in env:
            self.assertEqual(seconds, int(env['A830_BUILD_TIME']))
        else:
            self.assertGreater(seconds, 1700000000)
        added.remove(built[0])
        expected = IDENTITIES[identity] + [f'target-type = "{target}"'] + fixup_line(ci, fixup, env.get('A830_KEYBOARD_F2') != '0')
        corner = env.get('A830_CORNER_RADIUS')
        if corner:
            expected += ['[adt.product.bytes]', f'display-corner-radius = "{round(float(corner) * 3):08x}"'[:0] +
                         'display-corner-radius = "' + round(float(corner) * 3).to_bytes(4, 'little').hex() + '03000000"']
        self.assertEqual(sorted(added), sorted(expected))
        tomllib.loads(board)
        # Kernel arguments: the loop-m8-phone entry's, minus the timed reset out of the box, plus the variant's own.
        want = [a for a in self.base if not (out_of_box and a.startswith('pagani-reset-after='))]
        if apps:
            want.append('sheng-staged-apps=1')
        want += env.get('A830_EXTRA_ARGS', '').split()
        self.assertEqual(kernel_args(calls[0]['argv']), want)
        self.assertEqual(kernel_args(calls[1]['argv']), want)
        # The seed: phone-plus options, --migrate for the apps forms, named after the layout.
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0][-1], str(self.box.top / f'工具/seeds/{variant}.dbrdisk'))
        self.assertEqual('--migrate' in seeds[0], apps)
        self.assertEqual(seeds[0][:4], ['--gib', '2', '--disable-jobs', 'com.apple.fairplayd.A2,com.apple.routined'])
        argv = calls[0]['argv']
        self.assertEqual(argv[argv.index('--data-ramdisk') + 1], str(self.box.top / f'工具/seeds/{variant}.dbrdisk'))
        return board, want, result

    def test_island(self):
        board, args, result = self.check_run('phone-island', 'island')
        self.assertTrue((self.box.top / '测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛.img').is_file())
        self.assertTrue((self.box.top / 'UniXNU/out-gpu/loop-m8-phone-island.img').is_file())
        self.assertIn('seed phone-island.dbrdisk', result.stdout)
        config = merged(board)
        adt = config['adt']
        self.assertEqual(adt['root']['string'], {'model': 'iPhone16,1', 'target-type': 'J120', 'compatible': 'J120AP'})
        self.assertEqual(adt['product']['u32']['artwork-device-subtype'], 2556)
        self.assertEqual(adt['product']['u32']['artwork-scale-factor'], 3)
        self.assertEqual(adt['product']['string']['artwork-device-idiom'], 'phone')
        self.assertEqual(adt['product']['string']['artwork-display-gamut'], 'P3')
        self.assertEqual(adt['product']['bytes']['product-id'], J120_PRODUCT_ID)
        self.assertEqual(adt['chosen']['u32']['display-scale'], 3)
        self.assertEqual(adt['buttons']['u32']['home-button-type'], 2)
        self.assertIn('function-button_ringerab', adt['buttons']['u32'])
        self.assertNotIn('function-button_ringeren', json.dumps(adt))
        # The whole command line as UniXNU's payload builds it (board arguments overridden by name, then the extras).
        from unixnu.payload import check_args
        names = {a.split('=', 1)[0] for a in args}
        line = check_args([a for a in config['payload']['kernel_args'] if a.split('=', 1)[0] not in names] + args)
        self.assertLessEqual(len(line), 1000)

    def test_build_time(self):
        """A830_BUILD_TIME replaces the build's time and reaches the ADT as a u32."""
        board, _, _ = self.check_run('phone-island', 'island', A830_BUILD_TIME='1793400000')
        self.assertEqual(merged(board)['adt']['chosen']['u32']['pagani-build-time'], 1793400000)

    def test_build_time_out_of_range(self):
        result, calls, _ = self.box.run('phone-island', A830_BUILD_TIME='4294967296')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('out of range', result.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(self.box.board.read_text(), self.box.original, 'board.toml not restored')

    def test_island_apps(self):
        self.check_run('phone-island-apps', 'island', apps=True)
        self.assertTrue((self.box.top / '测试镜像/boot-A830-回路-M8桌面-iPhone15Pro灵动岛-系统应用.img').is_file())

    def test_island_out_of_box(self):
        self.check_run('phone-island', 'island', out_of_box=True, LAYOUT_OUT_OF_BOX='1')
        self.assertTrue((self.box.top / '测试镜像/boot-A830-GPU桌面-iPhone15Pro灵动岛-开箱即用.img').is_file())
        self.assertTrue((self.box.top / 'UniXNU/out-gpu/m8-phone-island.img').is_file())

    def test_island_apps_out_of_box(self):
        self.check_run('phone-island-apps', 'island', apps=True, out_of_box=True, LAYOUT_OUT_OF_BOX='1')
        self.assertTrue((self.box.top / '测试镜像/boot-A830-GPU桌面-iPhone15Pro灵动岛-系统应用-开箱即用.img').is_file())

    def test_island_target_type(self):
        self.check_run('phone-island', 'island', target='D331p', A830_TARGET_TYPE='D331p')

    def test_extra_fixups(self):
        """A830_EXTRA_FIXUPS: the listed fix-ups follow the others in board.toml's text_fixups, in order; unset adds nothing."""
        extra = ['dyld_shared_cache_arm64.07,321e900,fd7bbfa9fd030091,20008052c0035fd6', 'dyld_shared_cache_arm64.07,2fd6f58,f4630139,ff630139']
        result, calls, _ = self.box.run('phone-island-apps', A830_EXTRA_FIXUPS=' '.join(extra))
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = tomllib.loads(calls[0]['board'])['payload']['text_fixups']
        self.assertEqual(listed[-2:], extra)
        self.assertEqual(listed[:-2], PROFILE_FIXUPS + [CI_FIXUP, FIXUP, KB_FIXUP])
        self.assertLessEqual(len(listed), 32)

    def test_island_without_fixup(self):
        self.check_run('phone-island', 'island', fixup=False, A830_ISLAND_FIXUP='0')

    def test_island_corner_radius(self):
        board, _, _ = self.check_run('phone-island', 'island', A830_CORNER_RADIUS='55')
        config = merged(board)
        self.assertEqual(config['adt']['product']['bytes']['display-corner-radius'], 'a500000003000000')
        self.assertEqual(config['adt']['product']['bytes']['product-id'], J120_PRODUCT_ID)

    def test_plus_and_apps_unchanged(self):
        """The XS Max variants keep their identity, names and arguments (no fix-up)."""
        self.check_run('phone-plus', 'xsmax', fixup=False)
        self.assertTrue((self.box.top / '测试镜像/boot-A830-回路-M8桌面-iPhone布局-增强.img').is_file())
        self.check_run('phone-apps', 'xsmax', fixup=False, apps=True)
        self.assertTrue((self.box.top / '测试镜像/boot-A830-回路-M8桌面-iPhone布局-增强-系统应用.img').is_file())
        self.check_run('phone-plus', 'xsmax', fixup=False, out_of_box=True, LAYOUT_OUT_OF_BOX='1')
        self.assertTrue((self.box.top / '测试镜像/boot-A830-GPU桌面-iPhone布局-增强-开箱即用.img').is_file())

    def test_ci_fixup_off(self):
        """A830_CI_GL_FIXUP=0 leaves the Core Image fix-up out: the island and the keyboard ones stay; all three off, no text_fixups."""
        board, _, _ = self.check_run('phone-island', 'island', ci=False, A830_CI_GL_FIXUP='0')
        self.assertEqual(tomllib.loads(board)['payload']['text_fixups'], PROFILE_FIXUPS + [FIXUP, KB_FIXUP])
        board, _, _ = self.check_run('phone-plus', 'xsmax', fixup=False, ci=False, A830_CI_GL_FIXUP='0')
        self.assertEqual(tomllib.loads(board)['payload']['text_fixups'], PROFILE_FIXUPS + [KB_FIXUP])
        board, _, _ = self.check_run('phone-plus', 'xsmax', fixup=False, ci=False, A830_CI_GL_FIXUP='0', A830_KEYBOARD_F2='0')
        self.assertNotIn('text_fixups', board)
        board, _, _ = self.check_run('phone-island', 'island', fixup=False, ci=False, A830_CI_GL_FIXUP='0', A830_ISLAND_FIXUP='0',
                                     A830_KEYBOARD_F2='0')
        self.assertNotIn('text_fixups', board)

    def test_ci_fixup_in_payload(self):
        """The Core Image fix-up reaches UniXNU's payload table (outside the command line) for the island and the XS Max forms."""
        for variant, identity, fixup in (('phone-island', 'island', True), ('phone-apps', 'xsmax', False)):
            board, _, _ = self.check_run(variant, identity, fixup=fixup, apps=variant.endswith('apps'))
            want = PROFILE_FIXUPS + [CI_FIXUP] + ([FIXUP] if fixup else []) + [KB_FIXUP]
            self.assertEqual(tomllib.loads(board)['payload']['text_fixups'], want)
            self.assertEqual(merged(board)['payload']['text_fixups'], want)
            self.assertEqual(len(PROFILE_FIXUPS), 4)
            self.assertTrue(any(f.startswith('BTAudioHALPlugin,') for f in PROFILE_FIXUPS))

    def test_ci_fixup_board_conflict(self):
        """A board.toml that already carries text_fixups is refused (the fix-up would be lost), and restored."""
        self.box.original = self.box.original.replace('[payload]\n', '[payload]\ntext_fixups = ["x,1,00,00"]\n', 1)
        self.box.board.write_text(self.box.original)
        result, calls, _ = self.box.run('phone-island')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already has text_fixups', result.stderr)
        self.assertEqual(calls, [])
        self.assertEqual(self.box.board.read_text(), self.box.original)

    def test_owner_trust_and_extra_args(self):
        """A830_OWNER_TRUST replaces the image's owner trust bundle; A830_EXTRA_ARGS come after the variant's arguments."""
        trust = str(self.box.top / 'next2/owner-trust-apps.bundle')
        _, _, _ = self.check_run('phone-island', 'island', A830_OWNER_TRUST=trust, A830_EXTRA_ARGS='pagani-gpu-va=16 x=1')
        _, calls, _ = self.box.run('phone-island', A830_OWNER_TRUST=trust, A830_EXTRA_ARGS='pagani-gpu-va=16')
        for call in calls:
            argv = call['argv']
            self.assertEqual(argv[argv.index('--owner-trust-cache') + 1], trust)
            self.assertEqual(kernel_args(argv)[-1:], ['pagani-gpu-va=16'])
            self.assertFalse(any(a.startswith('text-fixup') for a in kernel_args(argv)))
        _, calls, _ = self.box.run('phone-island')
        argv = calls[0]['argv']
        self.assertEqual(argv[argv.index('--owner-trust-cache') + 1],
                         str(self.box.top / 'build-userspace/tree/build/adreno-metal-plugin/owner-trust.bundle'))

    def test_battery_arg(self):
        """A830_BATTERY=1 adds pagani-battery=1 (PaganiBattery from the fuel gauge alone: pagani-adsp stays unset) after the variant's
        arguments and before A830_EXTRA_ARGS, within the command line's limit; unset or 0 adds nothing."""
        from unixnu.payload import KERNEL_ARGS_LIMIT, check_args
        for value, extra in (('1', ['pagani-battery=1']), ('0', []), ('', [])):
            result, calls, _ = self.box.run('phone-island-apps', A830_BATTERY=value, A830_EXTRA_ARGS='x=1')
            self.assertEqual(result.returncode, 0, result.stderr)
            want = self.base + ['sheng-staged-apps=1'] + extra + ['x=1']
            for call in calls:
                self.assertEqual(kernel_args(call['argv']), want)
            names = {a.split('=', 1)[0] for a in want}
            line = check_args([a for a in merged(calls[0]['board'])['payload']['kernel_args']
                               if a.split('=', 1)[0] not in names] + want)
            self.assertLessEqual(len(line), KERNEL_ARGS_LIMIT)
            self.assertNotIn('pagani-adsp', line)
            self.assertEqual(self.box.board.read_text(), self.box.original)

    def test_fixup_slot_follows_entry(self):
        """A text-fixup0 already in the loop-m8-phone entry stays a boot argument; the island's fix-up is in /chosen/text-fixups."""
        self.box.close()
        text = IMAGES.read_text().replace('image loop-m8-phone boot-A830-回路-M8桌面-iPhone布局 ',
                                          'image loop-m8-phone boot-A830-回路-M8桌面-iPhone布局 --kernel-arg text-fixup0=x,1,00,00 ', 1)
        self.box = Sandbox(text)
        result, calls, _ = self.box.run('phone-island')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = kernel_args(calls[0]['argv'])
        self.assertIn('text-fixup0=x,1,00,00', args)
        self.assertFalse(any(FIXUP in a for a in args))
        self.assertIn(FIXUP, tomllib.loads(calls[0]['board'])['payload']['text_fixups'])

    def test_failure_restores_board(self):
        result, calls, _ = self.box.run('phone-island', STUB_FAIL='bootimg')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.box.board.read_text(), self.box.original)

    def test_bad_corner_radius_restores_board(self):
        result, calls, _ = self.box.run('phone-island', A830_CORNER_RADIUS='-1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(self.box.board.read_text(), self.box.original)

    def test_unknown_variant(self):
        result, calls, seeds = self.box.run('phone-isle')
        self.assertEqual(result.returncode, 2)
        self.assertIn('phone-island', result.stderr)
        self.assertEqual((calls, seeds), ([], []))
        self.assertEqual(self.box.board.read_text(), self.box.original)


class MemoryOptions(unittest.TestCase):
    """A830_OWNED_MEMORY and A830_VM_COMPRESSOR: one image's window and compressor, off and unchanged by default."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.close()

    def board(self, **env):
        result, calls, _ = self.box.run('phone-island-apps', **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.box.board.read_text(), self.box.original, 'board.toml not restored')
        self.assertEqual(calls[0]['board'], calls[1]['board'])
        return calls[0]['board']

    def refused(self, **env):
        result, calls, _ = self.box.run('phone-island-apps', **env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(self.box.board.read_text(), self.box.original, 'board.toml not restored')
        return result.stderr

    def test_defaults_stay(self):
        """board.toml itself says 6 GiB and has no compressor, and neither switch set changes that."""
        for text in (self.box.original, self.board()):
            parsed = tomllib.loads(text)
            self.assertEqual(parsed['payload']['adt']['owned_memory_size'], '0x180000000')
            self.assertEqual(parsed['payload']['dram_size'], '0x180000000')
            self.assertEqual(parsed['adt']['defaults']['u32'], {'sleep-disabled': 1})

    def test_window_replaces_only_its_line(self):
        self.box.nm(34)
        plain = self.board(A830_BUILD_TIME='1800000000')
        for value in ('0x200000000', '8589934592'):
            board = self.board(A830_OWNED_MEMORY=value, A830_BUILD_TIME='1800000000')
            self.assertEqual(changed_lines(plain, board), (['owned_memory_size = "0x200000000"'], ['owned_memory_size = "0x180000000"']))

    def test_window_outside_the_bounds_is_refused(self):
        """The ADT generator's and DarwinBoot's bounds, before anything is built."""
        self.box.nm(34)     # a kernel that would take them: this is about the numbers
        for value in ('0xfffffff', '0x200200000', '0x180001000', '0x1', '-0x200000000'):
            self.assertIn('A830_OWNED_MEMORY takes 256 MiB to 8 GiB', self.refused(A830_OWNED_MEMORY=value))
        self.assertIn('is not a number', self.refused(A830_OWNED_MEMORY='six'))

    def test_the_smallest_and_largest_windows_pass_the_bounds(self):
        self.box.nm(34)
        self.assertIn('owned_memory_size = "0x10000000"', self.board(A830_OWNED_MEMORY='0x10000000'))
        self.assertIn('owned_memory_size = "0x200000000"', self.board(A830_OWNED_MEMORY='0x200000000'))

    def test_a_kernel_without_the_pool_is_refused(self):
        """16 pages: exactly the 6 GiB of today. 19 are not enough for the worst case of 8 GiB, 21 are."""
        self.box.nm(16)
        self.assertIn('needs 21 bootstrap table pages and this kernel has 16', self.refused(A830_OWNED_MEMORY='0x200000000'))
        self.assertIn('owned_memory_size = "0x180000000"', self.board(A830_OWNED_MEMORY='0x180000000'))
        self.box.nm(20)
        self.assertIn('this kernel has 20', self.refused(A830_OWNED_MEMORY='0x200000000'))
        self.box.nm(21)
        self.assertIn('owned_memory_size = "0x200000000"', self.board(A830_OWNED_MEMORY='0x200000000'))

    def test_6_gib_needs_no_kernel_check(self):
        """No llvm-nm stub, no kernel in the sandbox: nothing to look at for the window of every kernel so far."""
        self.assertIn('owned_memory_size = "0x100000000"', self.board(A830_OWNED_MEMORY='0x100000000'))

    def test_a_kernel_that_cannot_be_read_is_refused(self):
        self.assertIn('make-a830-layout', self.refused(A830_OWNED_MEMORY='0x200000000'))

    def test_compressor(self):
        parsed = tomllib.loads(self.board(A830_VM_COMPRESSOR='2'))
        self.assertEqual(parsed['adt']['defaults']['u32'], {'sleep-disabled': 1, 'kern.vm_compressor': 2})
        self.assertEqual(parsed['payload']['adt']['owned_memory_size'], '0x180000000')
        for value in ('0', ''):
            self.assertEqual(tomllib.loads(self.board(A830_VM_COMPRESSOR=value))['adt']['defaults']['u32'], {'sleep-disabled': 1})

    def test_compressor_costs_no_argument(self):
        _, plain, _ = self.box.run('phone-island-apps')
        _, with_it, _ = self.box.run('phone-island-apps', A830_VM_COMPRESSOR='2')
        self.assertEqual(kernel_args(plain[0]['argv']), kernel_args(with_it[0]['argv']))
        self.assertFalse(any('compressor' in arg for arg in kernel_args(with_it[0]['argv'])))
        self.assertNotIn('compressor', ' '.join(tomllib.loads(with_it[0]['board'])['payload']['kernel_args']))

    def test_other_compressor_modes_are_refused(self):
        for value in ('32', '8', '4', 'on'):
            self.assertIn('A830_VM_COMPRESSOR', self.refused(A830_VM_COMPRESSOR=value))

    @unittest.skipUnless(HARDWARE_DTB.is_file(), f'no {HARDWARE_DTB}')
    def test_the_adt_they_give(self):
        """Through UniXNU's merge and ADT generator: the two properties DarwinBoot and vm_fault_init read, and nothing else moved."""
        from unixnu import devicetree
        self.box.nm(34)

        def adt(**env):
            blob = devicetree.generate(HARDWARE_DTB, merged(self.board(**env)), unique_chip_id='1', data=True, trust=True, addon=True)
            (_, nodes), end = parse_adt(blob)
            self.assertEqual(end, len(blob))
            return nodes
        plain = adt()
        self.assertNotIn('kern.vm_compressor', plain['defaults'][0])
        self.assertEqual(struct.unpack('<Q', plain['chosen'][0]['darwinboot-memory-size'])[0], 0x180000000)
        nodes = adt(A830_OWNED_MEMORY='0x200000000', A830_VM_COMPRESSOR='2')
        self.assertEqual(struct.unpack('<I', nodes['defaults'][0]['kern.vm_compressor'])[0], 2)
        self.assertEqual(nodes['defaults'][0]['kern.maxvnodes'], plain['defaults'][0]['kern.maxvnodes'])
        self.assertEqual(struct.unpack('<I', nodes['defaults'][0]['sleep-disabled'])[0], 1)
        self.assertEqual(struct.unpack('<Q', nodes['chosen'][0]['darwinboot-memory-size'])[0], 0x200000000)
        # dram-size is the DTS's, not the window's (phone tests of the window: memory-storage.md).
        self.assertEqual(nodes['chosen'][0]['dram-size'], plain['chosen'][0]['dram-size'])
        self.assertEqual(struct.unpack('<Q', nodes['chosen'][0]['dram-size'])[0], 0x180000000)


@unittest.skipUnless(STAGE16.is_file() and VT_FIXUPS.is_file(), f'no {STAGE16}')
class Stage16(unittest.TestCase):
    """make-stage16.sh, a copy in the sandbox, through make-a830-layout.sh with UniXNU stubbed: nothing is built."""

    NAMES = ('boot-A830-阶段16-完整包', 'boot-A830-阶段16-8G', 'boot-A830-阶段16-压缩器', 'boot-A830-阶段16-亮度A1', 'boot-A830-阶段16-USB探针')

    @classmethod
    def setUpClass(cls):
        cls.box = Sandbox()
        box = cls.box
        box.nm(34)
        final = box.top / 'build-userspace/a830-inputs/final'
        final.mkdir(parents=True)
        (final / 's7-final.dbrdisk').write_bytes(b'seed\n')
        (final / 's7-final.json').write_text('{}\n')
        (final / 's7-final-owner-trust.bundle').write_bytes(b'trust\n')
        fixups = box.top / VT_FIXUPS.relative_to(TOP)
        fixups.parent.mkdir(parents=True)
        shutil.copy(VT_FIXUPS, fixups)
        (box.top / '测试镜像').mkdir()
        text = STAGE16.read_text()
        old = 'cd /Users/xiaochoumao/unixnu-13t\n'
        assert text.count(old) == 1
        script = box.top / 'make-stage16.sh'
        script.write_text(text.replace(old, f'cd {box.top}\n'))
        environment = {k: v for k, v in os.environ.items() if not k.startswith('A830_')}
        environment.update(PATH=box.path, STUB_TOP=str(box.top))
        box.script.chmod(0o755)
        cls.result = subprocess.run(['bash', str(script)], env=environment, capture_output=True, text=True)
        calls = [json.loads(line) for line in (box.top / 'calls.jsonl').read_text().splitlines()] if (box.top / 'calls.jsonl').exists() else []
        cls.images = [(calls[i], calls[i + 1]) for i in range(0, len(calls), 2)]

    @classmethod
    def tearDownClass(cls):
        cls.box.close()

    def test_the_images_with_their_records(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertEqual(len(self.images), len(self.NAMES))
        for name in self.NAMES:
            self.assertTrue((self.box.top / '测试镜像' / f'{name}.img').is_file(), name)
            self.assertTrue((self.box.top / '测试镜像' / f'{name}.payload.json').is_file(), name)
        self.assertEqual(self.box.board.read_text(), self.box.original)

    def lines(self):
        """The command line each image would boot with: the board's arguments the extra ones do not replace, as payload.py makes it."""
        from unixnu.payload import check_args
        lines = []
        for payload, _ in self.images:
            extra = kernel_args(payload['argv'])
            names = {item.split('=', 1)[0] for item in extra}
            board = merged(payload['board'])['payload']['kernel_args']
            lines.append(check_args([item for item in board if item.split('=', 1)[0] not in names] + extra))
        return lines

    def test_each_image_changes_one_thing(self):
        boards = [tomllib.loads(payload['board']) for payload, _ in self.images]
        self.assertEqual([b['payload']['adt']['owned_memory_size'] for b in boards], ['0x180000000', '0x200000000', '0x180000000', '0x180000000', '0x180000000'])
        self.assertEqual([b['adt']['defaults']['u32'].get('kern.vm_compressor') for b in boards], [None, None, 2, None, None])
        words = [line.split() for line in self.lines()]
        self.assertEqual([next(w for w in line if w.startswith('pagani-reset-after=')) for line in words],
                         ['pagani-reset-after=5400', 'pagani-reset-after=240', 'pagani-reset-after=420', 'pagani-reset-after=240', 'pagani-reset-after=240'])
        full, window, compressor, panel, usb = [set(line) - {w for w in line if w.startswith('pagani-reset-after=')} for line in words]
        self.assertEqual(full, window)
        self.assertEqual(full, compressor)
        self.assertEqual((panel - full, full - panel), ({'pagani-panel=577'}, {'pagani-panel=65'}))
        self.assertEqual((usb - full, full - usb), ({'pagani-battery=7'}, {'pagani-battery=1'}))

    def test_the_command_lines(self):
        for line in self.lines():
            words = line.split()
            self.assertNotIn('pagani-input-script', line)
            self.assertTrue({'pagani-panel=65', 'pagani-panel=577'} & set(words), line)
            self.assertIn('sheng-log=1', words)
            self.assertIn('pagani-kbtrace=1', words)
            self.assertNotIn('vm_compressor', line)
            self.assertNotIn('startup_debug=0', words)

    def test_seed_and_trust_are_s7(self):
        for payload, image in self.images:
            for call in (payload, image):
                argv = call['argv']
                self.assertTrue(argv[argv.index('--data-ramdisk') + 1].endswith('final/s7-final.dbrdisk'))
                self.assertTrue(argv[argv.index('--owner-trust-cache') + 1].endswith('final/s7-final-owner-trust.bundle'))

    def test_refuses_without_the_seed(self):
        box = Sandbox()
        try:
            text = STAGE16.read_text().replace('cd /Users/xiaochoumao/unixnu-13t\n', f'cd {box.top}\n')
            fixups = box.top / VT_FIXUPS.relative_to(TOP)
            fixups.parent.mkdir(parents=True)
            shutil.copy(VT_FIXUPS, fixups)
            (box.top / 's.sh').write_text(text)
            result = subprocess.run(['bash', str(box.top / 's.sh')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('s7-final', result.stderr)
        finally:
            box.close()


def parse_adt(blob, off=0):
    nprops, nchildren = struct.unpack_from('<II', blob, off)
    off += 8
    props = {}
    for _ in range(nprops):
        key, size = struct.unpack_from('<32sI', blob, off)
        off += 36
        props[key.rstrip(b'\0').decode()] = blob[off:off + size]
        off += size + (-size % 4)
    children = {}
    for _ in range(nchildren):
        child, off = parse_adt(blob, off)
        children[child[0]['name'].rstrip(b'\0').decode()] = child
    return (props, children), off


@unittest.skipUnless(HARDWARE_DTB.is_file(), f'no {HARDWARE_DTB}')
class AdtGeneration(unittest.TestCase):
    """The island board through UniXNU's merge and ADT generator, read back node by node."""

    @classmethod
    def setUpClass(cls):
        box = Sandbox()
        try:
            result, calls, _ = box.run('phone-island')
            assert result.returncode == 0, result.stderr
            board = calls[0]['board']
        finally:
            box.close()
        from unixnu import devicetree
        blob = devicetree.generate(HARDWARE_DTB, merged(board), unique_chip_id='1', data=True, trust=True, addon=True)
        (cls.root, cls.nodes), end = parse_adt(blob)
        assert end == len(blob)

    def u32(self, node, key):
        return struct.unpack('<I', self.nodes[node][0][key])[0]

    def string(self, node, key):
        return self.nodes[node][0][key].rstrip(b'\0').decode()

    def test_chosen_text_fixups(self):
        """/chosen/text-fixups: the OS profile's four fix-ups, then the Core Image and the island ones, each NUL-terminated."""
        self.assertEqual(self.nodes['chosen'][0]['text-fixups'],
                         b''.join(f.encode() + b'\0' for f in PROFILE_FIXUPS + [CI_FIXUP, FIXUP, KB_FIXUP]))

    def test_root(self):
        self.assertEqual(self.root['model'], b'iPhone16,1\0')
        self.assertEqual(self.root['target-type'], b'J120\0')
        self.assertTrue(self.root['compatible'].startswith(b'J120AP\0'))

    def test_product(self):
        self.assertEqual(self.u32('product', 'artwork-device-subtype'), 2556)
        self.assertEqual(self.u32('product', 'artwork-scale-factor'), 3)
        self.assertEqual(self.string('product', 'artwork-device-idiom'), 'phone')
        self.assertEqual(self.string('product', 'product-name'), 'iPhone 15 Pro')
        self.assertEqual(self.string('product', 'product-description'), 'iPhone 15 Pro')
        self.assertEqual(self.string('product', 'compatible-device-fallback'), 'iPhone15,2')
        self.assertEqual(self.nodes['product'][0]['product-id'].hex(), J120_PRODUCT_ID)
        self.assertNotIn('display-corner-radius', self.nodes['product'][0])

    def test_chosen_and_buttons(self):
        self.assertEqual(self.u32('chosen', 'display-scale'), 3)
        buttons = self.nodes['buttons'][0]
        self.assertEqual(self.u32('buttons', 'home-button-type'), 2)
        self.assertIn('function-button_ringerab', buttons)
        self.assertNotIn('function-button_ringeren', buttons)
        # The OS profile's text fix-ups travel in /chosen/text-fixups, with the Core Image and the island ones.
        self.assertIn(b'BTAudioHALPlugin', self.nodes['chosen'][0]['text-fixups'])


# What the 21H461 shared cache decides, read from it (2026-10-30, ipsw dyld extract + llvm-objdump):
#   SBFEffectiveArtworkSubtype() = [MGCopyAnswer("oPeik/9e8lQWMszEjbPzng")["ArtworkDeviceSubType"] intValue]   SpringBoardFoundation
#   island on  <=> subtype in {2556, 2796}: -[SpringBoard _completeStartupAfterMainSceneConnect:] 0x1cfa5ef70, SBUIIsSystemApertureEnabled
#              0x1a80bf8ac, SBSIsSystemApertureAvailable block 0x1938ff0b0, getDeviceSupportsSystemAperture block 0x1cfc04bdc
#   SBHScreenTypeForCurrentDevice (SpringBoardHome): exact {referenceBounds size, scale, subtype, home button} match of 44 types
#              (_SBHGetScreenSpecification), else for phones without a home button the first table size the screen is at least
#              (BSSizeGreaterThanOrEqualToSize, __SBHScreenTypeLookup 0x1cf456d20...): 430x932 23, 428x926 13, 414x896 5, 393x852 21,
#              390x844 11, 375x812 3, else 4
#   island sensor region: SBUISystemApertureLayoutMetrics sensorRegionSize 125x36.667 (125.333 when native/scale == 430x932) at
#              y = minimumScreenEdgeInset 11.333, centred in the screen bounds (-[SBSystemApertureViewController
#              _frameForSensorRegionInBounds:], UIRectCenteredXInRectScale)
#   status-bar gap: screen type 21's exclusion rect (402, 34, 375, 110) pixels / native scale (+[UISDisplaySingleRectShape(SpringBoard)
#              sb_displayShapeForScreenType:] 0x1cfe7c6dc), laid out by _UIStatusBarVisualProvider_DynamicSplit (exclusion y > 0)
SPECS = {3: (375, 812, 3, 2436, 2), 5: (414, 896, 3, 2688, 2), 11: (390, 844, 3, 2532, 2), 13: (428, 926, 3, 2778, 2),
         21: (393, 852, 3, 2556, 2), 22: (320, 693, 3, 2556, 2), 23: (430, 932, 3, 2796, 2), 24: (375, 812, 3, 2796, 2)}
FALLBACK = [((430, 932), 23), ((428, 926), 13), ((414, 896), 5), ((393, 852), 21), ((390, 844), 11), ((375, 812), 3)]


def island_enabled(subtype):
    return subtype in (2556, 2796)


def screen_type(width, height, scale, subtype, home_button):
    for kind, spec in SPECS.items():
        if spec == (width, height, scale, subtype, home_button):
            return kind
    assert home_button == 2, 'only the no-home-button phone fallback is modelled'
    return next((kind for (w, h), kind in FALLBACK if width >= w and height >= h), 4)


def sensor_region_px(width_pt, height_pt):
    large = (width_pt, height_pt) == (430, 932)
    w, h, top = (125 + 1 / 3 if large else 125.0), 36 + 2 / 3, 11 + 1 / 3
    x = width_pt / 2 - w / 2
    return x * SCALE, top * SCALE, (x + w) * SCALE, (top + h) * SCALE


def hole():
    """ro.oplus.display.screenhole.positon of the stock system (x1,y1:x2,y2 pixels), 569,40:647,118 when the backup is absent."""
    value = '569,40:647,118'
    if PROPERTIES.is_file():
        match = re.search(r'\[ro\.oplus\.display\.screenhole\.positon\]: \[([0-9,:]+)\]', PROPERTIES.read_text(errors='replace'))
        if match:
            value = match.group(1)
    a, b = value.split(':')
    return tuple(int(v) for v in a.split(',') + b.split(','))


class IslandPredicates(unittest.TestCase):
    """The island board's values satisfy the conditions above, and the island lands on the 13T's hole."""

    @classmethod
    def setUpClass(cls):
        box = Sandbox()
        try:
            result, calls, _ = box.run('phone-island')
            assert result.returncode == 0, result.stderr
            cls.adt = merged(calls[0]['board'])['adt']
            cls.payload = merged(calls[0]['board'])['payload']
            cls.args = kernel_args(calls[0]['argv'])
        finally:
            box.close()
        cls.scale = cls.adt['chosen']['u32']['display-scale']
        cls.width, cls.height = WIDTH_PX / cls.scale, HEIGHT_PX / cls.scale

    def test_island_on(self):
        self.assertTrue(island_enabled(self.adt['product']['u32']['artwork-device-subtype']))
        self.assertFalse(island_enabled(2688))
        self.assertTrue(self.adt['root']['string']['model'].startswith('iPhone'))   # DeviceClassNumber 1: phone (BSPlatform 0)
        self.assertEqual(self.adt['buttons']['u32']['home-button-type'], 2)

    def test_screen_type(self):
        self.assertEqual(self.scale, 3)
        self.assertEqual(self.adt['product']['u32']['artwork-scale-factor'], 3)
        subtype = self.adt['product']['u32']['artwork-device-subtype']
        self.assertEqual(screen_type(self.width, self.height, 3, subtype, 2), 21)
        self.assertEqual(SPECS[21][3], subtype)                       # the screen type's own subtype is the island's
        self.assertEqual(screen_type(self.width, self.height, 3, 2688, 2), 21)   # the XS Max variants got it as well
        self.assertEqual(screen_type(393, 852, 3, 2556, 2), 21)
        self.assertEqual(screen_type(430, 932, 3, 2796, 2), 23)
        self.assertEqual(screen_type(414, 896, 3, 2688, 2), 5)

    def test_island_covers_hole(self):
        x1, y1, x2, y2 = sensor_region_px(self.width, self.height)
        hx1, hy1, hx2, hy2 = hole()
        self.assertAlmostEqual((x1 + x2) / 2, (hx1 + hx2) / 2, places=6)          # 608: centred on the hole
        self.assertTrue(x1 < hx1 and hx2 < x2 and y1 < hy1 and hy2 < y2, ((x1, y1, x2, y2), (hx1, hy1, hx2, hy2)))
        self.assertEqual((round(x2 - x1), round(y1), round(y2)), (375, 34, 144))

    def test_status_bar_gap(self):
        # In /chosen/text-fixups with the profile's and the Core Image one, not on the command line.
        self.assertFalse([a for a in self.args if re.match(r'text-fixup[0-7]=', a)])
        self.assertEqual([f for f in self.payload['text_fixups'] if f.startswith('dyld_shared_cache_arm64.25,')], [FIXUP])
        name, offset, original, replacement = FIXUP.split(',')
        self.assertEqual(struct.unpack('<d', bytes.fromhex(original))[0], 402.0)
        x = struct.unpack('<d', bytes.fromhex(replacement))[0]
        self.assertAlmostEqual((x + 375 / 2) / 3, self.width / 2, places=9)     # gap centred like the island
        self.assertAlmostEqual((402 + 375 / 2) / 3, 393 / 2, places=9)          # unpatched: centred on 393 points
        self.assertGreater(self.width / 2 - (402 + 375 / 2) / 3, 6)             # 6.17 points off on this screen
        self.assertLessEqual((int(offset, 16) & 0xfff) + len(original) // 2, 0x1000)   # one page (native-text-fixup)
        self.assertRegex(FIXUP, r'^[A-Za-z0-9_.\-]{1,63},[0-9a-fA-F]{1,16},(?:[0-9a-fA-F]{2}){1,16},(?:[0-9a-fA-F]{2}){1,16}$')

    @unittest.skipUnless(KERNEL.is_file(), f'no {KERNEL}')
    def test_kernel_reads_boot_fixups(self):
        data = KERNEL.read_bytes()
        for i in range(8):
            self.assertIn(f'text-fixup{i}'.encode() + b'\0', data)


class DyldCache:
    def __init__(self, directory):
        self.maps, self.files = [], {}
        for path in sorted(directory.glob('dyld_shared_cache_arm64*')):
            if path.suffix in ('.a2s', '.map') or path.name.endswith('.symbols'):
                continue
            with open(path, 'rb') as f:
                head = f.read(0x200)
            if not head.startswith(b'dyld_v1'):
                continue
            offset, count = struct.unpack_from('<II', head, 0x10)
            with open(path, 'rb') as f:
                f.seek(offset)
                table = f.read(32 * count)
            for i in range(count):
                address, size, file_offset = struct.unpack_from('<QQQ', table, 32 * i)
                self.maps.append((address, size, file_offset, path))

    def locate(self, address):
        for start, size, file_offset, path in self.maps:
            if start <= address < start + size:
                return path, file_offset + address - start
        raise KeyError(hex(address))

    def read(self, address, n):
        path, offset = self.locate(address)
        with open(path, 'rb') as f:
            f.seek(offset)
            return f.read(n)

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def doubles(self, address, n):
        return struct.unpack(f'<{n}d', self.read(address, 8 * n))


def cmp_imm(register, value):      # SUBS XZR, Xn, #imm12
    return 0xF100001F | value << 10 | register << 5


def movz_w(register, value):       # MOVZ Wd, #imm16
    return 0x52800000 | value << 5 | register


@unittest.skipUnless(DSC_DIR.is_dir(), f'no extracted 21H461 shared cache at {DSC_DIR}')
class CacheConstants(unittest.TestCase):
    """The constants and compares used above, read back from the 21H461 shared cache."""

    @classmethod
    def setUpClass(cls):
        cls.dsc = DyldCache(DSC_DIR)

    def test_subtype_compares(self):
        d = self.dsc
        self.assertEqual(d.word(0x1cfa5ef70), cmp_imm(0, 2796))   # SpringBoard startup: cmp x0, #0xaec
        self.assertEqual(d.word(0x1cfa5ef78), cmp_imm(0, 2556))   #                       cmp x0, #0x9fc
        self.assertEqual(d.word(0x1a80bf8ac), cmp_imm(0, 2556))   # SBUIIsSystemApertureEnabled
        self.assertEqual(d.word(0x1a80bf8b0), movz_w(8, 2796))
        self.assertEqual(d.word(0x1938ff0b0), cmp_imm(8, 2556))   # SBSIsSystemApertureAvailable
        self.assertEqual(d.word(0x1938ff0b4), movz_w(9, 2796))

    def test_screen_tables(self):
        metrics = self.dsc.doubles(0x193967c60, 28)               # _SBDisplayMetrics, read by both lookups
        sizes = list(zip(metrics[0::2], metrics[1::2]))
        self.assertEqual(sizes[5], (375.0, 812.0))
        self.assertEqual(sizes[6], (390.0, 844.0))
        self.assertEqual(sizes[7], (414.0, 896.0))
        self.assertEqual(sizes[8], (428.0, 926.0))
        self.assertEqual(sizes[9], (393.0, 852.0))
        self.assertEqual(sizes[10], (430.0, 932.0))

    def test_sensor_metrics(self):
        self.assertEqual(self.dsc.doubles(0x1a80ddde0, 3), (125.0, 125 + 1 / 3, 36 + 2 / 3))
        self.assertAlmostEqual(self.dsc.doubles(0x1a80dde30, 1)[0], 11 + 1 / 3, places=12)

    def test_exclusion_rect(self):
        self.assertEqual(self.dsc.doubles(0x1d0231268, 2), (457.0, 402.0))
        self.assertEqual(self.dsc.doubles(0x1d0231320, 2), (375.0, 110.0))
        name, offset, original, _ = FIXUP.split(',')
        path, file_offset = self.dsc.locate(0x1d0231270)
        self.assertEqual((path.name, file_offset), (name, int(offset, 16)))
        with open(path, 'rb') as f:
            f.seek(file_offset)
            self.assertEqual(f.read(8).hex(), original)

    def test_core_image_gl_fixup(self):
        """CIGLIsUsable's _wrapGLIsUsable at 0x1976f4f98 (CoreImage) is "mov w0, #1; ret", then _wrapGLIsAccelerated the same, in
        dyld_shared_cache_arm64.07 at 0x2e2cf98; the fix-up writes "mov w0, #0" over the first instruction only, inside one page."""
        name, offset, original, replacement = CI_FIXUP.split(',')
        path, file_offset = self.dsc.locate(0x1976f4f98)
        self.assertEqual((path.name, file_offset), (name, int(offset, 16)))
        with open(path, 'rb') as f:
            f.seek(file_offset)
            self.assertEqual(f.read(16).hex(), original)
        ret = 0xD65F03C0
        self.assertEqual(struct.unpack('<4I', bytes.fromhex(original)), (movz_w(0, 1), ret, movz_w(0, 1), ret))
        self.assertEqual(struct.unpack('<4I', bytes.fromhex(replacement)), (movz_w(0, 0), ret, movz_w(0, 1), ret))
        self.assertLessEqual((file_offset & 0x3FFF) + 16, 0x4000)   # one 16 KiB page carries the whole fix-up

    def test_keyboard_frame_fixup(self):
        """F2 at 0x18a2af038 (UIKitCore _UIKeyboardGetDerivedKeyboardForSpecificOrientation), dyld_shared_cache_arm64.03 at 0x3503038:
        "ldr d2,[x8,#0x390]; fcmp d0,d2; cset w8,eq; adrp x9" becomes "ldr d2,[x8,#0x218]; fcmp d1,d2; cset w21,eq; b +20", with
        880.0 at x8's page + 0x218 (0x18b33c218) and the branch landing after the test's "and w21,w8,w9"."""
        name, offset, original, replacement = KB_FIXUP.split(',')
        path, file_offset = self.dsc.locate(0x18a2af038)
        self.assertEqual((path.name, file_offset), (name, int(offset, 16)))
        with open(path, 'rb') as f:
            f.seek(file_offset)
            self.assertEqual(f.read(16).hex(), original)
            f.seek(file_offset + 0x1c)
            self.assertEqual(struct.unpack('<I', f.read(4))[0], 0x0A090115)       # and w21, w8, w9 (skipped)
        self.assertEqual(self.dsc.doubles(0x18b33c218, 1), (880.0,))
        words = struct.unpack('<4I', bytes.fromhex(replacement))
        self.assertEqual(words[3], 0x14000005)                                     # b +20: 0x18a2af044 -> 0x18a2af058
        self.assertLessEqual((file_offset & 0x3FFF) + 16, 0x4000)


if __name__ == '__main__':
    unittest.main()
