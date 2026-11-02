#!/usr/bin/env python3
"""check-keyboard-seed.py 的主机测试（python3 -B 工具/test-check-keyboard-seed.py -v）：在普通目录上逐项破坏偏好，每一项都要被查出来。
只写临时目录，不碰真实种子，不碰手机。挂载整个种子的路径由 test-edit-phone-seed-launchd.py 的 WholeTool 覆盖。"""
import importlib.util
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('check_keyboard', HERE / 'check-keyboard-seed.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
edit_spec = importlib.util.spec_from_file_location('edit_seed', HERE / 'edit-phone-seed-launchd.py')
edit = importlib.util.module_from_spec(edit_spec)
edit_spec.loader.exec_module(edit)
CLI = [sys.executable, '-I', '-B', str(HERE / 'check-keyboard-seed.py')]
SUPPORT = 'com.apple.assistant.support.plist'
GLOBAL = '.GlobalPreferences.plist'
KEYBOARD = 'com.apple.keyboard.preferences.plist'
STATUS = 'Offline Dictation Status'
KEYBOARDS = ['en_US@sw=QWERTY;hw=Automatic', 'zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', 'emoji@sw=Emoji']
DICTATION = dict(en_US=False, zh_CN=False, yue_CN=False, wuu_CN=False, emoji=False)
# 与 s6-final 一样的 .GlobalPreferences 和键盘偏好，还没有打字修复。
S6_GLOBAL = dict(AppleLanguages=['en'], AppleLocale='en_US', CAEnableDeepFramebuffer=False)
S6_KEYBOARD = dict(KeyboardInlineCompletion=False, KeyboardVisceral=True)


class Directory(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.prefs = Path(self.work.name)
        self.write(SUPPORT, {STATUS: {}})
        self.write(GLOBAL, S6_GLOBAL | {STATUS: {}, 'AppleKeyboards': list(KEYBOARDS), 'AppleKeyboardsExpanded': 1})
        self.write(KEYBOARD, S6_KEYBOARD | {'DictationLanguagesEnabled': dict(DICTATION)})

    def write(self, name, data):
        (self.prefs / name).write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))

    def change(self, name, **keys):
        """改掉 name 里的键，值为 None 时删掉。"""
        data = plistlib.loads((self.prefs / name).read_bytes())
        for key, value in keys.items():
            key = STATUS if key == 'status' else key
            if value is None:
                data.pop(key)
            else:
                data[key] = value
        self.write(name, data)

    def bad(self, lang='en'):
        return check.check(self.prefs, lang)

    def assertOneFailure(self, text):
        found = self.bad()
        self.assertEqual(len(found), 1, found)
        self.assertIn(text, found[0])

    def test_the_fixed_preferences_pass(self):
        self.assertEqual(self.bad(), [])

    def test_what_the_tool_writes_passes(self):
        """--text-input 写出来的东西要能过这里，两份期望值是分开写的。"""
        (self.prefs / SUPPORT).unlink()
        self.write(GLOBAL, S6_GLOBAL)
        self.write(KEYBOARD, S6_KEYBOARD)
        for item in edit.recipe(text_input=True):
            edit.apply_edit(self.prefs, item)
        self.assertEqual(self.bad(), [])

    def test_the_seed_before_the_fix_fails_everywhere(self):
        (self.prefs / SUPPORT).unlink()
        self.write(GLOBAL, S6_GLOBAL)
        self.write(KEYBOARD, S6_KEYBOARD)
        self.assertEqual(len(self.bad()), 5, self.bad())

    def test_no_support_file(self):
        (self.prefs / SUPPORT).unlink()
        self.assertOneFailure('com.apple.assistant.support')

    def test_the_status_must_be_a_dictionary(self):
        for value in ('{}', ['en-US'], 0):
            self.change(SUPPORT, status=value)
            self.assertOneFailure('应为字典')

    def test_the_status_must_not_say_installed(self):
        self.change(SUPPORT, status={'en-US': {'Installed': True}})
        self.assertOneFailure('Installed=true')

    def test_a_status_with_nothing_installed_is_fine(self):
        self.change(SUPPORT, status={'en-US': {'Installed': False}})
        self.assertEqual(self.bad(), [])

    def test_the_global_fallback_is_checked(self):
        self.change(GLOBAL, status=None)
        self.assertOneFailure('.GlobalPreferences:Offline Dictation Status')
        self.change(GLOBAL, status={'en-US': {'Installed': True}})
        self.assertOneFailure('Installed=true')

    def test_the_keyboards_must_be_equal_and_in_order(self):
        for value in (KEYBOARDS[:2], list(reversed(KEYBOARDS)), [*KEYBOARDS, 'ja_JP-Kana@sw=Kana'],
                      ['en_US@sw=QWERTY;hw=Automatic', 'zh_Hans-Pinyin@sw=Pinyin10;hw=US', 'emoji@sw=Emoji'],
                      ['en_US@sw=QWERTY;hw=Automatic', 'zh_Hans-Pinyin@sw=Pinyin-Simplified', 'emoji@sw=Emoji'],
                      ['en_US@hw=Automatic;sw=QWERTY', KEYBOARDS[1], KEYBOARDS[2]]):
            self.change(GLOBAL, AppleKeyboards=value)
            self.assertOneFailure('AppleKeyboards')

    def test_expanded_must_be_the_integer_one(self):
        for value in (True, 0, 2, '1', 1.0):
            self.change(GLOBAL, AppleKeyboardsExpanded=value)
            self.assertOneFailure('AppleKeyboardsExpanded')
        self.change(GLOBAL, AppleKeyboardsExpanded=None)
        self.assertOneFailure('AppleKeyboardsExpanded')

    def test_the_language_must_stay(self):
        self.change(GLOBAL, AppleLocale='zh_CN')
        self.assertOneFailure('语言不是')
        self.change(GLOBAL, AppleLocale='en_US', AppleLanguages=['zh-Hans-CN', 'en'])
        self.assertOneFailure('语言不是')

    def test_lang_asks_for_the_other_language(self):
        self.assertEqual(len(self.bad('zh-Hans')), 1)
        self.change(GLOBAL, AppleLocale='zh_CN', AppleLanguages=['zh-Hans-CN', 'en'])
        self.assertEqual(self.bad('zh-Hans'), [])

    def test_every_dictation_language_must_be_there(self):
        for language in DICTATION:
            self.change(KEYBOARD, DictationLanguagesEnabled={key: False for key in DICTATION if key != language})
            self.assertOneFailure('DictationLanguagesEnabled')

    def test_a_dictation_language_must_be_false(self):
        for value in (True, 0, 'false'):
            self.change(KEYBOARD, DictationLanguagesEnabled=dict(DICTATION, emoji=value))
            self.assertOneFailure('DictationLanguagesEnabled')

    def test_the_dictation_languages_must_be_a_dictionary(self):
        self.change(KEYBOARD, DictationLanguagesEnabled=sorted(DICTATION))
        self.assertOneFailure('DictationLanguagesEnabled')
        self.change(KEYBOARD, DictationLanguagesEnabled=None)
        self.assertOneFailure('DictationLanguagesEnabled')

    def test_an_owner_other_than_mobile_or_a_wider_mode_is_reported(self):
        support = self.prefs / SUPPORT
        support.chmod(0o644)
        self.assertEqual(len(check.check_owner(self.prefs)), 1)
        support.chmod(0o600)
        expected = 0 if os.getuid() == check.MOBILE else 1
        self.assertEqual(len(check.check_owner(self.prefs)), expected)
        mobile = check.MOBILE                   # chown needs root: the file is ours, so make ours not mobile
        check.MOBILE = os.getuid() + 1
        try:
            self.assertEqual(len(check.check_owner(self.prefs)), 1)
        finally:
            check.MOBILE = mobile
        support.unlink()
        self.assertEqual(check.check_owner(self.prefs), [])

    def test_the_command_line(self):
        passed = subprocess.run(CLI + ['--prefs-dir', str(self.prefs)], capture_output=True, text=True)
        self.assertEqual((passed.returncode, passed.stdout.strip()), (0, '全部符合'), passed.stderr)
        other = subprocess.run(CLI + ['--prefs-dir', str(self.prefs), '--lang', 'zh-Hans'], capture_output=True, text=True)
        self.assertEqual(other.returncode, 1)
        self.assertIn('1 项不符合', other.stdout)
        self.change(GLOBAL, AppleKeyboardsExpanded=True)
        failed = subprocess.run(CLI + ['--prefs-dir', str(self.prefs)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn('不符合：', failed.stdout)
        both = subprocess.run(CLI + ['--prefs-dir', str(self.prefs), 'seed.dbrdisk'], capture_output=True, text=True)
        self.assertEqual(both.returncode, 2)
        neither = subprocess.run(CLI, capture_output=True, text=True)
        self.assertEqual(neither.returncode, 2)


if __name__ == '__main__':
    unittest.main()
