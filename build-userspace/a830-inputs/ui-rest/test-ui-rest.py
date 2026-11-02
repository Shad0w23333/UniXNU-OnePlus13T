#!/usr/bin/env python3
"""ui-rest 主机检查：python3 -B build-userspace/a830-inputs/ui-rest/test-ui-rest.py -v
把 ui-rest 结论里的关键事实对着原始输入逐条核对（kernelcache、信任包、共享缓存、XNU 源、历次运行日志、launchd 表）。
只读；不碰手机；缺输入的项跳过。"""
import json
import os
import plistlib
import re
import struct
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
IN = HERE.parent
TOP = IN.parent.parent
sys.path.insert(0, str(HERE / 'tools'))
import tc  # noqa: E402

KC = TOP / 'UniXNU/out/ipsw/21H461/kernelcache.decompressed'
BUNDLE = TOP / '作者产物/build-63/original-static-system.bundle'
DSC = IN / 'systemos-21H461/System/Library/Caches/com.apple.dyld'
SYS_EXT = IN / 'systemos-21H461/System/Library/ExtensionKit/Extensions'
XNU = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work'
RUNS = TOP / '工具/gpu-test-loop/runs'
KERNEL = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/kernel/kernel.macho'
PAYLOAD = TOP / 'UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/payload/payload.json'
OS_PROFILE = TOP / 'UniXNU/profiles/os/ipados-17.7.11-21H461/profile.toml'
LAUNCHD = IN / 'osmnt-21H461/System/Library/xpc/launchd.plist'
VCP = IN / 'wallpaper/bin/VCPHEVC.videocodec'
FIXUPS = HERE / 'integrate/vt-fixups.txt'
PLK_TEXT = (0xfffffff00527c000, 0xa50000)        # __PRELINK_TEXT
PLK_EXEC = (0xfffffff005920000, 0x10f4000)       # __PLK_TEXT_EXEC


def need(*paths):
    missing = [str(p) for p in paths if not Path(p).exists()]
    if missing:
        raise unittest.SkipTest('缺输入: ' + ', '.join(missing))


def cdhash(path):
    d = Path(path).read_bytes()
    n = struct.unpack_from('<I', d, 16)[0]
    p = 32
    import hashlib
    for _ in range(n):
        cmd, size = struct.unpack_from('<II', d, p)
        if cmd == 0x1d:
            off, sz = struct.unpack_from('<II', d, p + 8)
            b = d[off:off + sz]
            cnt = struct.unpack_from('>I', b, 8)[0]
            for i in range(cnt):
                ty, o = struct.unpack_from('>II', b, 12 + i * 8)
                if ty == 0:
                    length = struct.unpack_from('>I', b, o + 4)[0]
                    return hashlib.sha256(b[o:o + length]).digest()[:20].hex()
        p += size
    raise ValueError(path)


def der(b, i):
    t, l = b[i], b[i + 1]
    i += 2
    if l & 0x80:
        n = l & 0x7f
        l = int.from_bytes(b[i:i + n], 'big')
        i += n
    return t, l, i


def lwcr(data, va):
    """AMFI 内嵌约束块（CoreEntitlements DER）的键名/取值文本，用于包含关系判断。"""
    o = va - PLK_TEXT[0] + PLK_TEXT[1]
    t, l, j = der(data, o)
    return data[o:j + l]


class LaunchConstraints(unittest.TestCase):
    def test_trust_cache_categories(self):
        """WebKit 四个扩展在 Cryptex 模块、类别 4；adattributiond 类别 1；SearchHelper 类别 5；海报扩展在静态模块、类别 4。"""
        need(BUNDLE, SYS_EXT)
        d = BUNDLE.read_bytes()
        table = {h: (mi, c) for mi, m in enumerate(tc.modules(d)) for h, ty, fl, c in tc.entries(m)}
        for name in ('WebContentExtension.appex/com.apple.WebKit.WebContent', 'GPUExtension.appex/com.apple.WebKit.GPU',
                     'NetworkingExtension.appex/com.apple.WebKit.Networking',
                     'WebContentCaptivePortalExtension.appex/com.apple.WebKit.WebContent.CaptivePortal'):
            self.assertEqual(table[cdhash(SYS_EXT / name)], (1, 4), name)
        s = IN / 'systemos-21H461/System/Library'
        self.assertEqual(table[cdhash(s / 'Frameworks/WebKit.framework/Daemons/adattributiond')], (1, 1))
        self.assertEqual(table[cdhash(s / 'PrivateFrameworks/SafariShared.framework/XPCServices/com.apple.Safari.SearchHelper.xpc/'
                                          'com.apple.Safari.SearchHelper')], (1, 5))
        r = IN / 'osmnt-21H461/System/Library/PrivateFrameworks'
        self.assertEqual(table[cdhash(r / 'WallpaperKit.framework/PlugIns/CollectionsPoster.appex/CollectionsPoster')], (0, 4))

    def test_category_constraints(self):
        """类别 4 的自身约束要卷事实，类别 5 不要（AMFI sub_FFFFFFF005AACCD0 的地址表）。"""
        need(KC)
        d = KC.read_bytes()
        cat4 = lwcr(d, 0xFFFFFFF0052E513B)
        for key in (b'on-authorized-authapfs-volume', b'on-system-volume', b'launch-type', b'validation-category'):
            self.assertIn(key, cat4)
        cat5 = lwcr(d, 0xFFFFFFF0052E51C1)
        self.assertIn(b'validation-category', cat5)
        self.assertNotIn(b'on-system-volume', cat5)

    def test_amfi_and_apfs_code(self):
        """AMFI 用 FSIOC_AUTH_FS(0x80084118) 问卷，APFS 的处理要求 role==1（ldrh #0x3c4）且挂载带 MNT_ROOTFS。"""
        need(KC)
        try:
            from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        except ImportError:
            self.skipTest('没有 capstone')
        d = KC.read_bytes()
        cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

        def text(va, n):
            o = va - PLK_EXEC[0] + PLK_EXEC[1]
            return ' ; '.join(f'{i.mnemonic} {i.op_str}' for i in cs.disasm(d[o:o + n], va))
        amfi = text(0xfffffff005ab8cf0, 0xac)
        self.assertRegex(amfi, r'mov w\d+, #0x4118')
        self.assertRegex(amfi, r'movk w\d+, #0x8008, lsl #16')
        system = text(0xfffffff005ab8d9c, 0x78)
        self.assertRegex(system, r'#(0xe|0x4000)')             # vfs_flags 的第 14 位 MNT_ROOTFS
        apfs = text(0xfffffff0066e39d0, 0x90)
        self.assertRegex(apfs, r'ldrh w\d+, \[x\d+, #0x3c4\]')  # apfs_superblock_t.apfs_role
        self.assertRegex(apfs, r'#(0xe|0x4000)')               # MNT_ROOTFS
        self.assertRegex(apfs, r'#0x50\b')                     # EAUTH 80

    def test_kernel_paths(self):
        """spawn 路径的 SIGKILL 丢 reason：PSIG_VFORK 走 runlocked，不调 set_thread_exit_reason。启动约束在 'signature enter' 之前。"""
        need(XNU / 'bsd/kern/kern_sig.c', XNU / 'bsd/kern/kern_exec.c')
        s = (XNU / 'bsd/kern/kern_sig.c').read_text(errors='replace')
        self.assertIn('if ((flavor & PSIG_VFORK) != 0) {\n\t\tgoto runlocked;', s)
        body = s[s.index('\nrunlocked:'):s.index('\nsigout_unlocked:')]
        self.assertNotIn('set_thread_exit_reason', body)
        e = (XNU / 'bsd/kern/kern_exec.c').read_text(errors='replace')
        self.assertLess(e.index('mac_proc_check_launch_constraints(p, imgp'), e.index('"exec process signature enter"'))

    def test_log_fingerprint(self):
        """历次日志：status=9 ns=0 的退出全部是 com.apple.WebKit，且都紧跟 load_machfile、没有 signature 行。"""
        need(RUNS)
        total = 0
        for log in RUNS.glob('*/merged.log'):
            rows = log.read_text(errors='replace').splitlines()
            for i, row in enumerate(rows):
                m = re.search(r' S  exit pid=(\d+) (\S+) status=9 ns=0 ', row)
                if not m:
                    continue
                self.assertEqual(m.group(2), 'com.apple.WebKit', row)
                total += 1
                pid = m.group(1)
                prior = [r for r in rows[max(0, i - 8):i] if f'pid={pid} ' in r]
                self.assertTrue(prior and 'load_machfile returned' in prior[-1], row)
                self.assertFalse(any('process signature' in r for r in prior), row)
        self.assertEqual(total, 23)

    def test_switch_in_kernel_and_room(self):
        """当前内核有 sheng-no-lc；加上它命令行不超 1000 字节。"""
        need(KERNEL, PAYLOAD)
        self.assertIn(b'sheng-no-lc\0', KERNEL.read_bytes())
        line = json.loads(PAYLOAD.read_text())['kernel_args']
        self.assertNotIn('sheng-no-lc', line)
        self.assertLessEqual(len(line) + len(' sheng-no-lc=1'), 1000)


class Heic(unittest.TestCase):
    def test_vcphevc_tile_entries(self):
        """VCPHEVC 的解码器类版本 3，startTileSession/decodeTile 两槽非空（VT 的要求：*class>=3、class[8]、class[9]）。"""
        need(VCP)
        d = VCP.read_bytes()
        segs = []
        o = 32
        for _ in range(struct.unpack_from('<I', d, 16)[0]):
            c, s = struct.unpack_from('<II', d, o)
            if c == 0x19:
                vm, vs, fo, fs = struct.unpack_from('<4Q', d, o + 24)
                segs.append((vm, fs, fo))
            o += s

        def at(a):
            return next(fo + a - vm for vm, fs, fo in segs if vm <= a < vm + fs)
        table = 0x1E9D2BA68
        cls = struct.unpack_from('<Q', d, at(table) + 16)[0] & ((1 << 36) - 1)
        slot = [struct.unpack_from('<Q', d, at(cls) + i * 8)[0] for i in range(10)]
        self.assertEqual(slot[0], 3)
        self.assertEqual(slot[8] & ((1 << 36) - 1), 0x1c8708c38)
        self.assertEqual(slot[9] & ((1 << 36) - 1), 0x1c870ad54)

    def test_fixups_match_cache(self):
        """两条文本修补：原字节在子缓存里逐字节相符，换算出的 VA 是函数/指令本身，新字节解码正确，符合内核解析规则。"""
        need(FIXUPS, DSC)
        import tomllib
        try:
            from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        except ImportError:
            self.skipTest('没有 capstone')
        cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        lines = FIXUPS.read_text().split()
        self.assertEqual(len(lines), 2)
        want = [(0x197ae6900, 'mov w0, #1 ; ret'), (0x19789ef58, 'strb wzr, [sp, #0x58]')]
        for line, (va, asm) in zip(lines, want):
            name, off, old, new = line.split(',')
            off = int(off, 16)
            old, new = bytes.fromhex(old), bytes.fromhex(new)
            self.assertLess(len(name), 64)
            self.assertEqual(len(old), len(new))
            self.assertLessEqual(len(old), 16)
            self.assertEqual(off // 4096, (off + len(old) - 1) // 4096, '跨页')
            with open(DSC / name, 'rb') as fh:
                h = fh.read(0x200)
                mo, mc = struct.unpack_from('<II', h, 0x10)
                fh.seek(mo)
                maps = [struct.unpack('<QQQII', fh.read(32)) for _ in range(mc)]
                addr, size, fo, mp, ip = next(m for m in maps if m[2] <= off < m[2] + m[1])
                self.assertTrue(ip & 4, '不在可执行映射')
                self.assertEqual(addr + off - fo, va)
                fh.seek(off)
                self.assertEqual(fh.read(len(old)), old)
            self.assertEqual(' ; '.join(f'{i.mnemonic} {i.op_str}'.strip() for i in cs.disasm(new, va)), asm)
        if OS_PROFILE.is_file():
            base = tomllib.loads(OS_PROFILE.read_text())['payload']['text_fixups']
            self.assertLessEqual(len(base) + 3 + len(lines), 32)
            self.assertFalse(set(lines) & set(base))


class Jobs(unittest.TestCase):
    def test_launchd_table(self):
        """launchd 表里有 videocodecd、WebKit 扩展路径；App cryptex 上的 8 个任务在两个卷上都没有程序文件（EX_CONFIG 的来源）。"""
        need(LAUNCHD)
        t = plistlib.loads(LAUNCHD.read_bytes())
        d = t['LaunchDaemons']
        vc = d['/System/Library/LaunchDaemons/com.apple.videocodecd.plist']
        self.assertTrue(vc['MachServices']['com.apple.coremedia.videocodecd.decompressionsession'])
        self.assertEqual(vc['ProgramArguments'], ['/usr/libexec/videocodecd'])
        self.assertTrue((IN / 'osmnt-21H461/usr/libexec/videocodecd').is_file())
        web = t['AppExtensions']['/System/Library/ExtensionKit/Extensions/WebContentExtension.appex']
        self.assertEqual(web['_executablePath'], '/System/Library/ExtensionKit/Extensions/WebContentExtension.appex/com.apple.WebKit.WebContent')
        app = sorted(v['Label'] for v in d.values() if str((v.get('ProgramArguments') or [''])[0]).startswith('/System/Cryptexes/App/')
                     or str(v.get('Program', '')).startswith('/System/Cryptexes/App/'))
        self.assertEqual(len(app), 8, app)
        self.assertIn('com.apple.Safari.History', app)
        self.assertIn('com.apple.WebBookmarks.webbookmarksd', app)


if __name__ == '__main__':
    unittest.main()
