#!/usr/bin/env python3
"""只读核对 Data 种子里打字修复需要的偏好（edit-phone-seed-launchd.py --text-input 写的那些），不改种子，不碰手机。

  check-keyboard-seed.py SEED.dbrdisk [--lang en|zh-Hans] [--scratch DIR]
  check-keyboard-seed.py --prefs-dir DIR [--lang en|zh-Hans]     DIR = mobile/Library/Preferences 的拷贝

核对的内容（依据 a830-inputs/round-1101/keyboard.md 第 11 节和 keyboard-verify.md 第 3 节）：
  com.apple.assistant.support 的 Offline Dictation Status 是字典（环的闭合条件），没有 Installed=true；
  .GlobalPreferences 里同一个键（沙盒读不到上面那个域时的兜底），要求相同；
  AppleKeyboards 逐字相等，顺序也相等：标识不规范时 -enabledInputModeIdentifiers: 的快路径永远不命中；
  AppleKeyboardsExpanded 是整数 1；
  AppleLanguages、AppleLocale 还是 --lang 指定的语言（默认 en，修复不改系统语言）；
  DictationLanguagesEnabled 含 en_US、zh_CN、yue_CN、wuu_CN、emoji，全部是布尔假：缺一个键，切到对应键盘时字典就被清空；
  只查种子时另外核对 com.apple.assistant.support.plist 属主是 mobile、模式 0600（cfprefsd 以 mobile 身份读它）。

期望值是故意另写一份的，不从 edit-phone-seed-launchd.py 引入：那边改错了，这里才查得出来。
退出码：0 全部符合，1 有不符合。在 macOS 上运行（hdiutil）。"""
import argparse
import plistlib
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
TOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOP / 'UniXNU/drivers/DarwinBoot/Test'))

PREFS = 'mobile/Library/Preferences'
SUPPORT = 'com.apple.assistant.support.plist'
STATUS = 'Offline Dictation Status'
MOBILE = 501
KEYBOARDS = ['en_US@sw=QWERTY;hw=Automatic', 'zh_Hans-Pinyin@sw=Pinyin-Simplified;hw=Automatic', 'emoji@sw=Emoji']
LANGUAGE = {'en': (['en'], 'en_US'), 'zh-Hans': (['zh-Hans-CN', 'en'], 'zh_CN')}
DICTATION = {'en_US', 'zh_CN', 'yue_CN', 'wuu_CN', 'emoji'}


def load(prefs, name):
    path = Path(prefs) / name
    return plistlib.loads(path.read_bytes()) if path.is_file() else None


def check_status(where, plist):
    """STATUS 不是字典时环就闭合；有 Installed=true 时听写又会显得可用，按了却用不了。"""
    status = None if plist is None else plist.get(STATUS)
    if not isinstance(status, dict):
        return [f'{where}:{STATUS} 应为字典，现在是 {status!r}（环的闭合条件）']
    if any(isinstance(value, dict) and value.get('Installed') for value in status.values()):
        return [f'{where}:{STATUS} 里有 Installed=true：会让听写看起来可用']
    return []


def check(prefs, lang):
    """DIR 里全部不符合项的说明，符合时为空表。"""
    bad = check_status('com.apple.assistant.support', load(prefs, SUPPORT))
    glob = load(prefs, '.GlobalPreferences.plist') or {}
    bad += check_status('.GlobalPreferences', glob)
    if glob.get('AppleKeyboards') != KEYBOARDS:
        bad.append(f'.GlobalPreferences:AppleKeyboards 应为 {KEYBOARDS}，现在是 {glob.get("AppleKeyboards")!r}')
    expanded = glob.get('AppleKeyboardsExpanded')
    if type(expanded) is not int or expanded != 1:      # True 也能让 boolValue 为真，但 updateInputModes: 写的是整数 1
        bad.append(f'.GlobalPreferences:AppleKeyboardsExpanded 应为整数 1，现在是 {expanded!r}')
    languages, locale = LANGUAGE[lang]
    if glob.get('AppleLanguages') != languages or glob.get('AppleLocale') != locale:
        bad.append(f'语言不是 {lang}：{glob.get("AppleLanguages")!r} {glob.get("AppleLocale")!r}')
    keyboard = load(prefs, 'com.apple.keyboard.preferences.plist') or {}
    enabled = keyboard.get('DictationLanguagesEnabled')
    if not isinstance(enabled, dict) or not DICTATION <= set(enabled) or any(value is not False for value in enabled.values()):
        bad.append(f'com.apple.keyboard.preferences:DictationLanguagesEnabled 应含 {sorted(DICTATION)} 且全为 false，现在是 {enabled!r}')
    return bad


def check_owner(prefs):
    """只在 -owners on 的挂载上有意义。"""
    path = Path(prefs) / SUPPORT
    if not path.is_file():
        return []                                       # check() 已经报了
    info = path.stat()
    if info.st_uid != MOBILE or stat.S_IMODE(info.st_mode) != 0o600:
        return [f'{SUPPORT} 应为属主 {MOBILE}、模式 600，现在是 {info.st_uid}、{stat.S_IMODE(info.st_mode):o}']
    return []


def from_seed(seed, lang, scratch):
    import packed_ramdisk
    logical, extents = packed_ramdisk.regions(seed.read_bytes())
    with tempfile.TemporaryDirectory(prefix='kbseed-', dir=scratch) as work:
        raw, mount = Path(work) / 'seed.raw', Path(work) / 'mnt'
        with raw.open('wb') as handle:
            handle.truncate(logical)
            for offset, data in extents:
                handle.seek(offset)
                handle.write(data)
        mount.mkdir()
        out = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-readonly', '-imagekey', 'diskimage-class=CRawDiskImage', '-nobrowse',
                                                      '-owners', 'on', '-mountpoint', str(mount), '-plist', str(raw)], stderr=subprocess.DEVNULL))
        device = out['system-entities'][0]['dev-entry']
        try:
            return check(mount / PREFS, lang) + check_owner(mount / PREFS)
        finally:
            subprocess.run(['hdiutil', 'detach', device], check=True, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('seed', nargs='?', type=Path)
    parser.add_argument('--prefs-dir', type=Path)
    parser.add_argument('--lang', choices=sorted(LANGUAGE), default='en')
    parser.add_argument('--scratch', type=Path, help='放 2 GiB 稀疏展开文件的目录')
    args = parser.parse_args()
    if bool(args.seed) == bool(args.prefs_dir):
        parser.error('SEED.dbrdisk 和 --prefs-dir 要且只要一个')
    bad = check(args.prefs_dir, args.lang) if args.prefs_dir else from_seed(args.seed, args.lang, args.scratch)
    for line in bad:
        print('不符合：' + line)
    print('全部符合' if not bad else f'{len(bad)} 项不符合')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
