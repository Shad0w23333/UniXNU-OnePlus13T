#!/usr/bin/env python3
"""Host tests of edit-phone-seed-launchd.py (python3 -B 工具/test-edit-phone-seed-launchd.py -v): the plist edits on plain directories, then
the whole tool on a synthetic APFS seed attached with hdiutil (64 MiB, built here; hdiutil and diskutil, macOS). Tests that need hdiutil are
skipped without it. Nothing outside temporary directories is written; no real seed is touched unless SEED_TEST_FINAL names one, then a
copy of it goes through the tool in the scratch directory SEED_TEST_SCRATCH."""
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parent
spec = importlib.util.spec_from_file_location('edit_seed', HERE / 'edit-phone-seed-launchd.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
TOOL = [sys.executable, '-B', str(HERE / 'edit-phone-seed-launchd.py')]
HAVE_MAC = all(shutil.which(name) for name in ('hdiutil', 'diskutil'))
SCRATCH = Path(os.environ.get('SEED_TEST_SCRATCH', tempfile.gettempdir()))
FINAL = os.environ.get('SEED_TEST_FINAL')
POLICY_JOBS = {'com.apple.CommCenter': True, 'com.apple.locationd': True, 'com.apple.watchdogd': True, 'com.apple.fairplayd.A2': True}
SPRINGBOARD = dict(BKNoWatchdogs=True, FBLaunchWatchdogExceptions={'com.apple.purplebuddy': 100.0}, MCForceShowCloudConfigurationUI=False,
                   SBDisableAutoDim=True, SBDontDimOrLockOnAC=True, SBDisableIdleSleep=True)
GLOBAL = dict(AppleLanguages=['en'], AppleLocale='en_US', CAEnableDeepFramebuffer=False)
KEYBOARD_PREFS = dict(KeyboardInlineCompletion=False, KeyboardVisceral=True)
KEYBOARDS = ['en_US@sw=QWERTY;hw=Automatic', 'zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', 'emoji@sw=Emoji']
DICTATION_OFF = dict(en_US=False, zh_CN=False, yue_CN=False, wuu_CN=False, emoji=False)
CHECK_KEYBOARD = [sys.executable, '-I', '-B', str(HERE / 'check-keyboard-seed.py')]


class Policy(unittest.TestCase):
    def jobs(self, fmt=plistlib.FMT_XML):
        return plistlib.dumps(POLICY_JOBS, fmt=fmt)

    def test_enable_removes_and_keeps_the_format(self):
        for fmt, magic in ((plistlib.FMT_XML, b'<?xml'), (plistlib.FMT_BINARY, b'bplist')):
            data, change = tool.edit_policy(self.jobs(fmt), ['com.apple.locationd'], [], None)
            self.assertTrue(data.startswith(magic))
            self.assertNotIn('com.apple.locationd', plistlib.loads(data))
            self.assertEqual(change['before'], sorted(POLICY_JOBS))
            self.assertEqual(change['after'], sorted(set(POLICY_JOBS) - {'com.apple.locationd'}))

    def test_disable_sets_true(self):
        data, change = tool.edit_policy(self.jobs(), [], ['com.apple.softwareupdateservicesd'], None)
        self.assertIs(plistlib.loads(data)['com.apple.softwareupdateservicesd'], True)
        self.assertIn('com.apple.softwareupdateservicesd', change['after'])

    def test_a_label_that_is_not_disabled_cannot_be_enabled(self):
        with self.assertRaisesRegex(tool.SeedError, 'not disabled'):
            tool.edit_policy(self.jobs(), ['com.apple.locatoind'], [], None)

    def test_enable_and_disable_of_one_label_is_refused(self):
        with self.assertRaisesRegex(tool.SeedError, 'at once'):
            tool.edit_policy(self.jobs(), ['com.apple.locationd'], ['com.apple.locationd'], None)

    def test_watchdogd_needs_force(self):
        with self.assertRaisesRegex(tool.SeedError, 'IOWatchdog'):
            tool.edit_policy(self.jobs(), ['com.apple.watchdogd'], [], None)
        data, _ = tool.edit_policy(self.jobs(), ['com.apple.watchdogd'], [], None, force=True)
        self.assertNotIn('com.apple.watchdogd', plistlib.loads(data))

    def test_dumppanic_needs_force(self):
        with self.assertRaisesRegex(tool.SeedError, 'PaganiDiag'):
            tool.edit_policy(self.jobs(), [], ['com.apple.DumpPanic'], None)

    def test_a_disable_of_an_unknown_label_needs_force(self):
        known = {'com.apple.mobileassetd'}
        with self.assertRaisesRegex(tool.SeedError, 'LaunchDaemons'):
            tool.edit_policy(self.jobs(), [], ['com.apple.mobileasetd'], known)
        tool.edit_policy(self.jobs(), [], ['com.apple.mobileassetd'], known)
        tool.edit_policy(self.jobs(), [], ['com.apple.mobileasetd'], known, force=True)

    def test_labels(self):
        self.assertEqual(tool.labels(['a.b,c.d', 'a.b']), ['a.b', 'c.d'])
        self.assertEqual(tool.labels(None), [])
        with self.assertRaises(tool.SeedError):
            tool.labels(['a b'])

    def test_not_a_dictionary(self):
        with self.assertRaises(tool.SeedError):
            tool.edit_policy(plistlib.dumps(['x']), [], [], None)


class Recipe(unittest.TestCase):
    def test_auto_lock(self):
        edit, = tool.recipe(auto_lock=30)
        self.assertEqual(edit.file, 'com.apple.springboard.plist')
        self.assertEqual(edit.set, {'SBAutoLockTime': 30})
        self.assertEqual(set(edit.remove), {'SBDisableAutoDim', 'SBDontDimOrLockOnAC', 'SBDisableIdleSleep'})

    def test_auto_lock_can_keep_the_idle_sleep_block(self):
        edit, = tool.recipe(auto_lock=30, keep_idle_sleep_block=True)
        self.assertEqual(set(edit.remove), {'SBDisableAutoDim', 'SBDontDimOrLockOnAC'})

    def test_auto_lock_range(self):
        for seconds in (0, 29, 3601, -1):
            with self.assertRaises(tool.SeedError):
                tool.recipe(auto_lock=seconds)
        self.assertEqual(tool.recipe(auto_lock=3600)[0].set, {'SBAutoLockTime': 3600})

    def test_no_auto_updates(self):
        files = {edit.file: edit.set for edit in tool.recipe(no_auto_updates=True)}
        self.assertIs(files['com.apple.softwareupdateservicesd.plist']['SUDisableAutoDownload'], True)
        self.assertIs(files['com.apple.softwareupdateservicesd.plist']['SUAutomaticUpdateV2Enabled'], False)
        self.assertEqual(files['com.apple.MobileAsset.plist'], {'MobileAssetServerURL': tool.BLACKHOLE, 'PallasUrlOverrideV2': tool.BLACKHOLE})
        files = {edit.file for edit in tool.recipe(no_auto_updates=True, keep_mobile_asset=True)}
        self.assertEqual(files, {'com.apple.softwareupdateservicesd.plist'})

    def test_sos_off(self):
        files = {edit.file: edit.set for edit in tool.recipe(sos_off=True)}
        self.assertIs(files['com.apple.SOS.plist']['SOSDeviceSupportedKey'], False)
        self.assertEqual(set(files), {'com.apple.SOS.plist', 'ByHost/com.apple.SOS..plist'})
        self.assertTrue(all(value is False for value in files['ByHost/com.apple.SOS..plist'].values()))

    def test_language(self):
        edit, = tool.recipe(language='zh-Hans')
        self.assertEqual(edit.file, '.GlobalPreferences.plist')
        self.assertEqual(edit.set['AppleLanguages'], ['zh-Hans-CN', 'en'])
        self.assertEqual(edit.set['AppleLocale'], 'zh_CN')
        with self.assertRaises(tool.SeedError):
            tool.recipe(language='fr')

    def test_text_input(self):
        files = {edit.file: edit for edit in tool.recipe(text_input=True)}
        self.assertEqual(set(files), {'com.apple.assistant.support.plist', '.GlobalPreferences.plist', 'com.apple.keyboard.preferences.plist'})
        self.assertEqual(files['com.apple.assistant.support.plist'].set, {'Offline Dictation Status': {}})
        glob = files['.GlobalPreferences.plist'].set
        self.assertEqual(set(glob), {'Offline Dictation Status', 'AppleKeyboards', 'AppleKeyboardsExpanded'})
        self.assertEqual(glob['Offline Dictation Status'], {})
        self.assertEqual(glob['AppleKeyboards'], KEYBOARDS)
        self.assertIs(type(glob['AppleKeyboardsExpanded']), int)            # -[TIPreferencesController updateInputModes:] writes @1
        self.assertEqual(glob['AppleKeyboardsExpanded'], 1)
        self.assertEqual(files['com.apple.keyboard.preferences.plist'].set, {'DictationLanguagesEnabled': DICTATION_OFF})
        self.assertTrue(all(edit.remove == () for edit in files.values()))

    def test_text_input_is_off_by_default(self):
        self.assertEqual(tool.recipe(), [])

    def test_a_recipe_hands_out_copies(self):
        edits = {edit.file: edit for edit in tool.recipe(text_input=True)}
        edits['.GlobalPreferences.plist'].set['AppleKeyboards'].append('x')
        edits['com.apple.keyboard.preferences.plist'].set['DictationLanguagesEnabled'].clear()
        again = {edit.file: edit for edit in tool.recipe(text_input=True)}
        self.assertEqual(again['.GlobalPreferences.plist'].set['AppleKeyboards'], KEYBOARDS)
        self.assertEqual(again['com.apple.keyboard.preferences.plist'].set['DictationLanguagesEnabled'], DICTATION_OFF)


class Preferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefs = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_existing_binary_keeps_its_other_keys_inode_and_mode(self):
        path = self.prefs / 'com.apple.springboard.plist'
        path.write_bytes(plistlib.dumps(SPRINGBOARD, fmt=plistlib.FMT_BINARY))
        path.chmod(0o640)
        inode = path.stat().st_ino
        done = tool.apply_edit(self.prefs, tool.recipe(auto_lock=45)[0])
        data = plistlib.loads(path.read_bytes())
        self.assertTrue(path.read_bytes().startswith(b'bplist'))
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(data, {k: v for k, v in SPRINGBOARD.items() if not k.startswith('SBD')} | {'SBAutoLockTime': 45})
        self.assertEqual(done['created'], [])
        self.assertIs(done['before']['SBDisableIdleSleep'], True)
        self.assertIsNone(done['after']['SBDisableIdleSleep'])

    def test_existing_xml_stays_xml(self):
        path = self.prefs / '.GlobalPreferences.plist'
        path.write_bytes(plistlib.dumps(GLOBAL, fmt=plistlib.FMT_XML))
        done = tool.apply_edit(self.prefs, tool.recipe(language='zh-Hans')[0])
        self.assertEqual(done['format'], 'xml')
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(data['AppleLocale'], 'zh_CN')
        self.assertIs(data['CAEnableDeepFramebuffer'], False)

    def test_a_new_plist_is_binary_0600(self):
        edit = tool.recipe(no_auto_updates=True, keep_mobile_asset=True)[0]
        done = tool.apply_edit(self.prefs, edit)
        path = self.prefs / edit.file
        self.assertTrue(path.read_bytes().startswith(b'bplist'))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(done['created'], [edit.file])

    def test_the_byhost_directory_is_created(self):
        edit = tool.recipe(sos_off=True)[1]
        done = tool.apply_edit(self.prefs, edit)
        self.assertEqual(stat.S_IMODE((self.prefs / 'ByHost').stat().st_mode), 0o755)
        self.assertEqual(done['created'], ['ByHost', 'ByHost/com.apple.SOS..plist'])

    def test_text_input_keeps_what_the_seed_had(self):
        glob = self.prefs / '.GlobalPreferences.plist'
        glob.write_bytes(plistlib.dumps(GLOBAL, fmt=plistlib.FMT_BINARY))
        glob.chmod(0o644)
        keyboard = self.prefs / 'com.apple.keyboard.preferences.plist'
        keyboard.write_bytes(plistlib.dumps(KEYBOARD_PREFS, fmt=plistlib.FMT_BINARY))
        keyboard.chmod(0o600)
        inodes = glob.stat().st_ino, keyboard.stat().st_ino
        done = {edit.file: tool.apply_edit(self.prefs, edit) for edit in tool.recipe(text_input=True)}
        self.assertEqual(done['com.apple.assistant.support.plist']['created'], ['com.apple.assistant.support.plist'])
        self.assertEqual(done['.GlobalPreferences.plist']['created'], [])
        self.assertEqual((glob.stat().st_ino, keyboard.stat().st_ino), inodes)
        self.assertEqual(stat.S_IMODE(glob.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(keyboard.stat().st_mode), 0o600)
        support = self.prefs / 'com.apple.assistant.support.plist'
        self.assertTrue(support.read_bytes().startswith(b'bplist'))
        self.assertEqual(stat.S_IMODE(support.stat().st_mode), 0o600)
        self.assertEqual(plistlib.loads(support.read_bytes()), {'Offline Dictation Status': {}})
        data = plistlib.loads(glob.read_bytes())
        self.assertEqual(data, GLOBAL | {'Offline Dictation Status': {}, 'AppleKeyboards': KEYBOARDS, 'AppleKeyboardsExpanded': 1})
        self.assertIs(type(data['AppleKeyboardsExpanded']), int)            # a plist integer, not <true/>
        data = plistlib.loads(keyboard.read_bytes())
        self.assertEqual(data, KEYBOARD_PREFS | {'DictationLanguagesEnabled': DICTATION_OFF})
        self.assertTrue(all(value is False for value in data['DictationLanguagesEnabled'].values()))

    def test_text_input_with_a_language(self):
        glob = self.prefs / '.GlobalPreferences.plist'
        glob.write_bytes(plistlib.dumps(GLOBAL, fmt=plistlib.FMT_BINARY))
        for edit in tool.recipe(language='zh-Hans', text_input=True):
            tool.apply_edit(self.prefs, edit)
        data = plistlib.loads(glob.read_bytes())
        self.assertEqual(data['AppleLocale'], 'zh_CN')
        self.assertEqual(data['AppleKeyboards'], KEYBOARDS)

    def test_a_file_that_is_no_dictionary_is_refused(self):
        (self.prefs / '.GlobalPreferences.plist').write_bytes(plistlib.dumps(['x']))
        with self.assertRaises(tool.SeedError):
            tool.apply_edit(self.prefs, tool.recipe(language='zh-Hans')[0])

    def test_a_shorter_plist_truncates(self):
        path = self.prefs / 'com.apple.springboard.plist'
        path.write_bytes(plistlib.dumps(SPRINGBOARD | {'Padding': 'x' * 4000}, fmt=plistlib.FMT_XML))
        data = plistlib.loads(path.read_bytes())
        del data['Padding']
        path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML) + b'\n' * 4000)     # a stale tail after the document would be junk
        tool.apply_edit(self.prefs, tool.Edit('com.apple.springboard.plist', {}, ('SBDisableAutoDim',)))
        self.assertLess(path.stat().st_size, 2000)


class Digest(unittest.TestCase):
    def test_a_change_shows_and_macos_names_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a').mkdir()
            (root / 'a/file').write_bytes(b'one')
            (root / 'link').symlink_to('a/file')
            (root / '.fseventsd').mkdir()
            (root / '.fseventsd/x').write_bytes(b'x')
            first = tool.digest(root)
            self.assertEqual(set(first), {'a', 'a/file', 'link'})
            (root / '.fseventsd/y').write_bytes(b'y')
            self.assertEqual(tool.changed_paths(first, tool.digest(root)), [])
            (root / 'a/file').write_bytes(b'two')
            (root / 'a/new').write_bytes(b'')
            (root / 'link').unlink()
            self.assertEqual(tool.changed_paths(first, tool.digest(root)), ['a/file', 'a/new', 'link'])
            (root / 'a/file').write_bytes(b'one')
            (root / 'a/new').unlink()
            (root / 'link').symlink_to('a/file')
            (root / 'a/file').chmod(0o600)
            self.assertEqual(tool.changed_paths(first, tool.digest(root)), ['a/file'])


def attach(raw, mount, *options):
    entities = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', *options, '-nobrowse',
                                                       '-mountpoint', str(mount), '-plist', str(raw)], stderr=subprocess.DEVNULL))
    return entities['system-entities'][0]['dev-entry']


def detach(device):
    subprocess.run(['hdiutil', 'detach', device], check=True, capture_output=True)


def expand(seed, raw):
    logical, extents = tool.packed_ramdisk.regions(seed.read_bytes())
    with raw.open('wb') as handle:
        handle.truncate(logical)
        for offset, data in extents:
            handle.seek(offset)
            handle.write(data)


def make_seed(tmp, sentinel=True):
    """A 64 MiB APFS container with the parts of a phone Data seed the tool looks at."""
    subprocess.run(['hdiutil', 'create', '-size', '64m', '-fs', 'APFS', '-layout', 'NONE', '-volname', 'Data', '-type', 'UDIF',
                    str(tmp / 'data.dmg')], check=True, capture_output=True)
    subprocess.run(['hdiutil', 'convert', str(tmp / 'data.dmg'), '-format', 'UDTO', '-o', str(tmp / 'data')], check=True, capture_output=True)
    raw = tmp / 'data.cdr'
    mount = tmp / 'make'
    mount.mkdir()
    device = attach(raw, mount, '-owners', 'off')
    try:
        (mount / 'db/com.apple.xpc.launchd').mkdir(parents=True)
        (mount / tool.POLICY).write_bytes(plistlib.dumps(POLICY_JOBS))
        (mount / tool.POLICY).chmod(0o644)
        (mount / tool.POLICY_MARKER).write_bytes(b'')
        prefs = mount / tool.PREFS
        prefs.mkdir(parents=True)
        (prefs / 'com.apple.springboard.plist').write_bytes(plistlib.dumps(SPRINGBOARD, fmt=plistlib.FMT_BINARY))
        (prefs / 'com.apple.springboard.plist').chmod(0o600)
        (prefs / '.GlobalPreferences.plist').write_bytes(plistlib.dumps(GLOBAL, fmt=plistlib.FMT_BINARY))
        (prefs / 'com.apple.keyboard.preferences.plist').write_bytes(plistlib.dumps(KEYBOARD_PREFS, fmt=plistlib.FMT_BINARY))
        (prefs / 'com.apple.keyboard.preferences.plist').chmod(0o600)
        (mount / 'ngpu/addons').mkdir(parents=True)
        (mount / 'ngpu/addons/NativeAdrenoMetal').write_bytes(os.urandom(4096))
        (mount / 'ngpu/addons/link').symlink_to('NativeAdrenoMetal')
        if sentinel:
            (mount / 'datamigrator').mkdir()
            (mount / tool.SENTINEL).write_bytes(b'')
    finally:
        detach(device)
    seed = tmp / 'seed.dbrdisk'
    tool.packed_ramdisk.pack(raw, seed)
    return seed


@unittest.skipUnless(HAVE_MAC, 'needs hdiutil and diskutil')
class WholeTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='seed-launchd-test-', dir=SCRATCH)
        cls.tmp = Path(cls.scratch.name)
        cls.seed = make_seed(cls.tmp)
        (cls.tmp / 'seed.json').write_text(json.dumps({'plugin_cdhash': 'ab' * 20, 'plugin': {'x': 1}}))
        cls.seed_bytes = cls.seed.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def run_tool(self, *arguments):
        return subprocess.run(TOOL + list(arguments), capture_output=True, text=True)

    def inside(self, seed):
        """Everything in SEED: digest, volume UUID, policy, preferences, owner of each preference."""
        work = Path(tempfile.mkdtemp(dir=self.tmp))
        raw, mount = work / 'seed.raw', work / 'mount'
        expand(seed, raw)
        mount.mkdir()
        device = attach(raw, mount, '-readonly', '-owners', 'on')
        try:
            prefs = mount / tool.PREFS
            return dict(digest=tool.digest(mount), uuid=tool.volume_uuid(mount), policy=plistlib.loads((mount / tool.POLICY).read_bytes()),
                        springboard=plistlib.loads((prefs / 'com.apple.springboard.plist').read_bytes()),
                        files={str(p.relative_to(prefs)): (plistlib.loads(p.read_bytes()), p.stat().st_uid) for p in prefs.rglob('*.plist')})
        finally:
            detach(device)
            shutil.rmtree(work)

    def test_all_options_change_only_their_files(self):
        out = self.tmp / 'all.dbrdisk'
        trust = self.tmp / 'trust.bundle'
        trust.write_bytes(b'trust')
        done = self.run_tool('--enable', 'com.apple.locationd,com.apple.CommCenter', '--disable', 'com.apple.softwareupdateservicesd',
                             '--auto-lock', '--no-auto-updates', '--sos-off', '--language', 'zh-Hans', '--no-budget',
                             '--trust', str(trust), '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.seed.read_bytes(), self.seed_bytes, 'SEED must stay as it was')
        before, after = self.inside(self.seed), self.inside(out)
        self.assertEqual(before['uuid'], after['uuid'])
        self.assertEqual(set(after['policy']), {'com.apple.watchdogd', 'com.apple.fairplayd.A2', 'com.apple.softwareupdateservicesd'})
        self.assertEqual(after['springboard'], {k: v for k, v in SPRINGBOARD.items() if not k.startswith('SBD')} | {'SBAutoLockTime': 30})
        self.assertEqual(after['files']['.GlobalPreferences.plist'][0]['AppleLanguages'], ['zh-Hans-CN', 'en'])
        self.assertIs(after['files']['com.apple.SOS.plist'][0]['SOSDeviceSupportedKey'], False)
        self.assertIs(after['files']['ByHost/com.apple.SOS..plist'][0]['SOSCallWithVolumeLockHoldKey'], False)
        self.assertIs(after['files']['com.apple.softwareupdateservicesd.plist'][0]['SUDisableAutoDownload'], True)
        self.assertEqual(after['files']['com.apple.MobileAsset.plist'][0]['PallasUrlOverrideV2'], tool.BLACKHOLE)
        changed = tool.changed_paths(before['digest'], after['digest'])
        prefix = tool.PREFS + '/'
        self.assertEqual(set(changed), {tool.POLICY, *[prefix + name for name in (
            'com.apple.springboard.plist', '.GlobalPreferences.plist', 'com.apple.SOS.plist', 'ByHost', 'ByHost/com.apple.SOS..plist',
            'com.apple.softwareupdateservicesd.plist', 'com.apple.MobileAsset.plist')]})
        report = json.loads(out.with_suffix('.json').read_text())
        self.assertEqual(sorted(report['changed']), sorted(changed))
        self.assertEqual(report['plugin_cdhash'], 'ab' * 20)
        self.assertEqual(report['volume_uuid'], before['uuid'])
        self.assertEqual(report['seed_sha256'], tool.packed_ramdisk.inspect(self.seed)[0]['sha256'])
        self.assertEqual(report['output']['sha256'], tool.packed_ramdisk.inspect(out)[0]['sha256'])
        self.assertTrue(report['migration_sentinel'])
        self.assertNotIn('warning', report)
        copy = self.tmp / 'all-owner-trust.bundle'
        self.assertEqual(copy.read_bytes(), b'trust')
        self.assertEqual(report['owner_trust']['sha256'], hashlib.sha256(b'trust').hexdigest())

    def test_the_final_seed_scripts_step(self):
        """make-a830-final-seed.sh: --sos-off and --disable in one run, then its closing grep over --check --json finds the label."""
        out = self.tmp / 'step.dbrdisk'
        trust = self.tmp / 'step-trust.bundle'
        trust.write_bytes(b'trust')
        label = 'com.apple.softwareupdateservicesd'
        done = self.run_tool('--sos-off', '--disable', label, '--no-budget', '--trust', str(trust), '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before, after = self.inside(self.seed), self.inside(out)
        self.assertEqual(set(after['policy']), set(before['policy']) | {label})
        prefix = tool.PREFS + '/'
        self.assertEqual(set(tool.changed_paths(before['digest'], after['digest'])), {
            tool.POLICY, *[prefix + name for name in ('com.apple.SOS.plist', 'ByHost', 'ByHost/com.apple.SOS..plist')]})
        check = self.run_tool('--check', '--json', '--scratch', str(self.tmp), str(out))
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn(f'"{label}"', check.stdout)
        self.assertNotIn(f'"{label}"', self.run_tool('--check', '--json', '--scratch', str(self.tmp), str(self.seed)).stdout)

    def test_text_input_changes_its_preferences_and_nothing_else(self):
        out = self.tmp / 'typing.dbrdisk'
        trust = self.tmp / 'typing-trust.bundle'
        trust.write_bytes(b'trust')
        done = self.run_tool('--text-input', '--no-budget', '--trust', str(trust), '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.seed.read_bytes(), self.seed_bytes, 'SEED must stay as it was')
        before, after = self.inside(self.seed), self.inside(out)
        self.assertEqual(before['uuid'], after['uuid'])
        self.assertEqual(before['policy'], after['policy'])
        prefix = tool.PREFS + '/'
        names = ('com.apple.assistant.support.plist', '.GlobalPreferences.plist', 'com.apple.keyboard.preferences.plist')
        self.assertEqual(set(tool.changed_paths(before['digest'], after['digest'])), {prefix + name for name in names})
        self.assertEqual(after['files'][names[0]], ({'Offline Dictation Status': {}}, tool.MOBILE))
        self.assertEqual(after['files'][names[1]][0], GLOBAL | {'Offline Dictation Status': {}, 'AppleKeyboards': KEYBOARDS, 'AppleKeyboardsExpanded': 1})
        self.assertEqual(after['files'][names[2]][0], KEYBOARD_PREFS | {'DictationLanguagesEnabled': DICTATION_OFF})
        self.assertEqual((self.tmp / 'typing-owner-trust.bundle').read_bytes(), b'trust')
        shown = self.run_tool('--check', '--scratch', str(self.tmp), str(out))
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        self.assertIn('Offline Dictation Status = {}', shown.stdout)
        self.assertIn('AppleKeyboardsExpanded = 1', shown.stdout)
        self.assertIn('zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', shown.stdout)
        self.assertIn('"emoji": false', shown.stdout)
        listed = json.loads(self.run_tool('--check', '--json', '--scratch', str(self.tmp), str(out)).stdout)['preferences']
        self.assertEqual(listed['com.apple.assistant.support.plist'], {'Offline Dictation Status': {}})
        self.assertEqual(listed['com.apple.keyboard.preferences.plist'], {'DictationLanguagesEnabled': DICTATION_OFF})
        self.assertEqual(listed['.GlobalPreferences.plist']['AppleKeyboards'], KEYBOARDS)
        passed = subprocess.run(CHECK_KEYBOARD + ['--scratch', str(self.tmp), str(out)], capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        failed = subprocess.run(CHECK_KEYBOARD + ['--scratch', str(self.tmp), str(self.seed)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)

    def test_the_read_back_catches_a_preference_of_the_wrong_kind(self):
        uuid = self.inside(self.seed)['uuid']
        wrong = tool.Edit('.GlobalPreferences.plist', {'Offline Dictation Status': {}}, ())
        with self.assertRaisesRegex(tool.SeedError, 'reads back different'):
            tool.verify_output(self.seed, self.tmp, sorted(POLICY_JOBS), [wrong], uuid)

    def test_check_lists_and_changes_nothing(self):
        out = self.tmp / 'listed.dbrdisk'
        done = self.run_tool('--enable', 'com.apple.locationd', '--no-budget', '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        before = out.read_bytes()
        shown = self.run_tool('--check', '--scratch', str(self.tmp), str(out))
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        self.assertIn('com.apple.watchdogd', shown.stdout)
        self.assertNotIn('com.apple.locationd', shown.stdout)
        self.assertIn('SBDisableAutoDim = True', shown.stdout)
        self.assertIn('migration sentinel /private/var/datamigrator/migrate: yes', shown.stdout)
        as_json = self.run_tool('--check', '--json', '--scratch', str(self.tmp), str(out))
        state = json.loads(as_json.stdout)
        self.assertEqual(state['policy']['disabled'], sorted(set(POLICY_JOBS) - {'com.apple.locationd'}))
        self.assertIsNone(state['preferences']['com.apple.SOS.plist'])
        self.assertEqual(out.read_bytes(), before)

    def test_the_read_back_of_output_catches_a_wrong_seed(self):
        uuid = self.inside(self.seed)['uuid']
        policy = sorted(POLICY_JOBS)
        unchanged = tool.Edit('com.apple.springboard.plist', {'SBDisableAutoDim': True}, ())
        self.assertEqual(tool.verify_output(self.seed, self.tmp, policy, [unchanged], uuid)['volume_uuid'], uuid)
        for edit in (tool.Edit('com.apple.springboard.plist', {'SBAutoLockTime': 30}, ()),
                     tool.Edit('com.apple.springboard.plist', {}, ('SBDisableAutoDim',)),
                     tool.Edit('com.apple.SOS.plist', {'SOSDeviceSupportedKey': False}, ())):
            with self.assertRaisesRegex(tool.SeedError, 'reads back different'):
                tool.verify_output(self.seed, self.tmp, policy, [edit], uuid)
        with self.assertRaisesRegex(tool.SeedError, 'volume UUID'):
            tool.verify_output(self.seed, self.tmp, policy, [], '00000000-0000-0000-0000-000000000000')
        with self.assertRaisesRegex(tool.SeedError, 'policy reads back'):
            tool.verify_output(self.seed, self.tmp, policy[1:], [], uuid)

    def test_refusals_leave_no_output(self):
        out = self.tmp / 'refused.dbrdisk'
        for arguments, expected in ((['--enable', 'com.apple.watchdogd'], 'IOWatchdog'), (['--enable', 'com.apple.locatoind'], 'not disabled'),
                                    (['--disable', 'com.apple.DumpPanic'], 'PaganiDiag'), (['--auto-lock', '5'], 'seconds'),
                                    ([], 'Nothing to change'), (['--keep-idle-sleep-block'], 'goes with --auto-lock'),
                                    (['--keep-mobile-asset'], 'goes with --no-auto-updates')):
            done = self.run_tool(*arguments, '--no-budget', '--scratch', str(self.tmp), str(self.seed), str(out))
            self.assertNotEqual(done.returncode, 0, arguments)
            self.assertIn(expected, done.stderr)
            self.assertFalse(out.exists())

    def test_an_output_is_not_overwritten(self):
        out = self.tmp / 'taken.dbrdisk'
        out.write_bytes(b'mine')
        done = self.run_tool('--sos-off', '--no-budget', '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(out.read_bytes(), b'mine')

    def test_budget_refuses_a_kernel_that_does_not_fit(self):
        out = self.tmp / 'large.dbrdisk'
        kernel = self.tmp / 'kernel'
        with kernel.open('wb') as handle:
            handle.truncate(190 << 20)
        done = self.run_tool('--sos-off', '--kernel', str(kernel), '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('no longer fits', done.stderr)
        self.assertFalse(out.exists())

    def test_budget_is_reported(self):
        out = self.tmp / 'budget.dbrdisk'
        kernel = self.tmp / 'small-kernel'
        kernel.write_bytes(b'\0' * 4096)
        done = self.run_tool('--sos-off', '--kernel', str(kernel), '--scratch', str(self.tmp), str(self.seed), str(out))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        payload = json.loads(out.with_suffix('.json').read_text())['payload']
        self.assertGreater(payload['headroom'], 0)
        self.assertEqual(payload['kernel'], str(kernel))

    def test_auto_lock_without_a_sentinel_warns(self):
        tmp = Path(tempfile.mkdtemp(dir=self.tmp))
        seed = make_seed(tmp, sentinel=False)
        out = tmp / 'new.dbrdisk'
        done = self.run_tool('--auto-lock', '60', '--no-budget', '--scratch', str(tmp), str(seed), str(out))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('nothing reads SBAutoLockTime', done.stderr)
        self.assertIn('warning', json.loads(out.with_suffix('.json').read_text()))


@unittest.skipUnless(FINAL and HAVE_MAC, 'SEED_TEST_FINAL names no real seed')
class RealSeed(unittest.TestCase):
    def test_final_seed(self):
        final = Path(FINAL)
        with tempfile.TemporaryDirectory(prefix='seed-launchd-final-', dir=SCRATCH) as tmp:
            tmp = Path(tmp)
            out = tmp / 'variant.dbrdisk'
            done = subprocess.run(TOOL + ['--enable', 'com.apple.locationd', '--auto-lock', '--no-auto-updates', '--sos-off', '--language',
                                          'zh-Hans', '--scratch', str(tmp), str(final), str(out)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            report = json.loads(out.with_suffix('.json').read_text())
            self.assertEqual(report['policy']['owner'], '0:0')
            self.assertEqual(report['policy']['mode'], '644')
            self.assertGreaterEqual(report['payload']['headroom'], 0)
            self.assertEqual(sorted(report['changed']), sorted([tool.POLICY, *[f'{tool.PREFS}/{name}' for name in (
                'com.apple.springboard.plist', '.GlobalPreferences.plist', 'com.apple.SOS.plist', 'ByHost', 'ByHost/com.apple.SOS..plist',
                'com.apple.softwareupdateservicesd.plist', 'com.apple.MobileAsset.plist')]]))

    def test_final_seed_scripts_step(self):
        """The step of make-a830-final-seed.sh on a real seed: the label is the OS volume's, the policy and SOS's preferences are all it changes."""
        final = Path(FINAL)
        with tempfile.TemporaryDirectory(prefix='seed-launchd-step-', dir=SCRATCH) as tmp:
            tmp = Path(tmp)
            out = tmp / 'step.dbrdisk'
            done = subprocess.run(TOOL + ['--sos-off', '--disable', 'com.apple.powerlogHelperd', '--scratch', str(tmp), str(final), str(out)],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            report = json.loads(out.with_suffix('.json').read_text())
            self.assertGreaterEqual(report['payload']['headroom'], 0)
            self.assertLessEqual(set(report['changed']), {tool.POLICY, *[f'{tool.PREFS}/{name}' for name in (
                'com.apple.SOS.plist', 'ByHost', 'ByHost/com.apple.SOS..plist')]})
            self.assertIn(tool.POLICY, report['changed'])
            check = subprocess.run(TOOL + ['--check', '--json', '--scratch', str(tmp), str(out)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertIn('"com.apple.powerlogHelperd"', check.stdout)

    def test_final_seed_text_input(self):
        """The s7 step of make-a830-final-seed.sh on a real seed: three preference files and nothing else, and check-keyboard-seed.py agrees."""
        final = Path(FINAL)
        with tempfile.TemporaryDirectory(prefix='seed-launchd-typing-', dir=SCRATCH) as tmp:
            tmp = Path(tmp)
            out = tmp / 'typing.dbrdisk'
            done = subprocess.run(TOOL + ['--text-input', '--scratch', str(tmp), str(final), str(out)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            report = json.loads(out.with_suffix('.json').read_text())
            self.assertGreaterEqual(report['payload']['headroom'], 0)
            self.assertEqual(sorted(report['changed']), sorted(f'{tool.PREFS}/{name}' for name in (
                'com.apple.assistant.support.plist', '.GlobalPreferences.plist', 'com.apple.keyboard.preferences.plist')))
            check = subprocess.run(CHECK_KEYBOARD + ['--scratch', str(tmp), str(out)], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)


if __name__ == '__main__':
    unittest.main()
