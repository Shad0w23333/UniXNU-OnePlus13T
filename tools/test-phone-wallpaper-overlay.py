#!/usr/bin/env python3
"""Host tests of the phone wallpaper overlay (python3 -B 工具/test-phone-wallpaper-overlay.py -v): make-phone-wallpaper-overlay.py,
check-phone-wallpaper-overlay.py (WallpaperKit's rules, the CAML image references, PNG; Core Animation's own CAPackage reader through
phone-wallpaper-render.m) and replace-phone-wallpaper.py (a synthetic APFS seed, attached with hdiutil).

Inputs: the mounted 21H461 OS volume (WALLPAPER_OS_ROOT, default build-userspace/a830-inputs/osmnt-21H461), the SystemOS shared cache
for the "any" constant, sips/xcrun/hdiutil (macOS). Tests that need a missing input are skipped. Nothing outside temporary
directories is written; 工具/phone-wallpaper-overlay is only read (it must equal a fresh build)."""
import hashlib
import importlib.util
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parent
spec = importlib.util.spec_from_file_location('wallpaper_check', HERE / 'check-phone-wallpaper-overlay.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
OS_ROOT = Path(os.environ.get('WALLPAPER_OS_ROOT', TOP / 'build-userspace/a830-inputs/osmnt-21H461'))
FLAT_CA = (OS_ROOT / 'Library/Wallpaper/Collections/iPadOS_17~ipad.wallpaperCollection/Wallpapers/'
           '7570.iPadOS_17_Flat-1024w-1366h@2x~ipad.wallpaper/7570.iPadOS_17_Flat_Background-1024w-1366h@2x~ipad.ca')
SHIPPED = HERE / 'phone-wallpaper-overlay'
OLD = TOP / 'build-userspace/a830-inputs/wallpaper/old-overlay/phone-wallpaper-overlay'
BUNDLE = 'UniXNU_Any.wallpaperCollection/Wallpapers/{0}.UniXNU_Any.wallpaper'
CA = BUNDLE + '/{0}.UniXNU_Any_Background.ca'
HAVE_MAC = all(shutil.which(t) for t in ('sips', 'xcrun', 'hdiutil'))
SCRATCH = Path(os.environ.get('WALLPAPER_SCRATCH', tempfile.gettempdir()))


def digest_tree(directory):
    out = {}
    for path in sorted(Path(directory).rglob('*')):
        rel = str(path.relative_to(directory))
        if path.is_symlink():
            out[rel] = 'link ' + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            out[rel] = 'dir'
    return out


def generate(output):
    result = subprocess.run([sys.executable, '-B', str(HERE / 'make-phone-wallpaper-overlay.py'), str(OS_ROOT), str(output)],
                            capture_output=True, text=True)
    if result.returncode:
        raise AssertionError('make-phone-wallpaper-overlay.py failed:\n' + result.stdout + result.stderr)


@unittest.skipUnless(FLAT_CA.is_dir() and HAVE_MAC, 'needs the 21H461 OS volume and macOS sips/xcrun')
class Overlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='wallpaper-test-', dir=SCRATCH)
        cls.out = Path(cls.tmp.name) / 'overlay'
        generate(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_passes_wallpaperkit_rules(self):
        problems, facts = check.check(self.out, OS_ROOT)
        self.assertEqual(problems, [])
        self.assertEqual([Path(b).name for b in facts['default']['iphone']], ['7565.UniXNU_Any.wallpaper'])
        self.assertEqual([Path(b).name for b in facts['default']['ipad']], ['7570.UniXNU_Any.wallpaper'])

    def test_core_animation_loads_every_image_and_renders_colour(self):
        problems, facts = check.check(self.out, OS_ROOT)
        check.render(facts['animations'], problems, facts)
        self.assertEqual(problems, [])
        for ca, result in facts['render'].items():
            self.assertTrue(result['loaded'], ca)
            self.assertEqual(len(result['states']), 24, ca)           # Sleep/Lock/Home x 4 orientations x Light/Dark
            for state in result['states']:
                self.assertEqual((state['images'], state['missing']), (1, 0), state['name'])
                self.assertEqual({(s['width'], s['height'], s['bitsPerComponent']) for s in state['sizes']}, {(2732, 2732, 8)})
            for shot in result['renders']:
                self.assertTrue(shot['applied'], shot)
                self.assertLess(shot['dark'], 0.001, shot)
                self.assertGreater(shot['mean'], 60, shot)

    def test_logical_screen_class_is_the_wallpaperkit_constant(self):
        for identifier in (7565, 7570):
            plist = plistlib.loads((self.out / BUNDLE.format(identifier) / 'Wallpaper.plist').read_bytes())
            self.assertEqual(plist['logicalScreenClass'], 'any')
            self.assertNotIn('productTypes', plist)
            self.assertNotIn('unsupportedProductTypes', plist)
            self.assertEqual(plist['identifier'], identifier)
            animation = plist['assets']['lockAndHome']['default']
            self.assertEqual((animation['type'], animation['identifier']), ('LayeredAnimation', identifier))
            self.assertEqual(animation['backgroundAnimationFileName'], f'{identifier}.UniXNU_Any_Background.ca')

    def test_wallpaper_plist_is_the_flat_one_otherwise(self):
        flat = plistlib.loads((FLAT_CA.parent / 'Wallpaper.plist').read_bytes())
        ours = plistlib.loads((self.out / BUNDLE.format(7565) / 'Wallpaper.plist').read_bytes())
        for key in set(flat) | set(ours):
            if key not in ('identifier', 'logicalScreenClass', 'productTypes', 'assets'):
                self.assertEqual(flat.get(key), ours.get(key), key)

    def test_caml_changes_only_the_image_paths(self):
        sealed = (FLAT_CA / 'main.caml').read_text()
        for identifier in (7565, 7570):
            caml = (self.out / CA.format(identifier) / 'main.caml').read_text()
            self.assertIsNone(re.search(r'(src|relativePath)="[^"]*\.heic"', caml, re.I))
            self.assertEqual(caml.count('src="assets/J120.png"'), sealed.count('src="assets/J120.HEIC"'))
            self.assertEqual(caml.count('src="assets/J120-Dark.png"'), sealed.count('src="assets/J120-Dark.HEIC"'))
            self.assertEqual(caml.replace('.png"', '.HEIC"'), sealed)
            for name in ('index.xml', 'assetManifest.caml'):
                self.assertEqual((self.out / CA.format(identifier) / name).read_bytes(), (FLAT_CA / name).read_bytes())

    def test_images_are_8bit_png_inside_the_package_and_shared(self):
        self.assertEqual([p for p in self.out.rglob('*') if p.is_symlink()], [])
        for image in ('J120', 'J120-Dark'):
            a = self.out / CA.format(7565) / 'assets' / f'{image}.png'
            b = self.out / CA.format(7570) / 'assets' / f'{image}.png'
            self.assertEqual(check.png_header(a), (2732, 2732, 8, 2, 0))
            self.assertEqual(os.stat(a).st_ino, os.stat(b).st_ino)       # 7570 shares 7565's images (hard link)
            head = a.read_bytes()[:4096]
            self.assertTrue(b'sRGB' in head or b'iCCP' in head)            # tagged sRGB, as the HEIC is
        problems, facts = check.check(self.out, OS_ROOT)
        self.assertLess(facts['unique_bytes'], check.BUDGET)
        self.assertGreater(facts['bytes'], facts['unique_bytes'] * 1.9)

    def test_collection_lists(self):
        iphone = plistlib.loads((self.out / 'Collections~iphone.plist').read_bytes())['order']
        self.assertEqual(iphone[0], check.PHONE['collection'])
        sealed = plistlib.loads((OS_ROOT / 'Library/Wallpaper/Collections/Collections~ipad.plist').read_bytes())['order']
        ipad = plistlib.loads((self.out / 'Collections~ipad.plist').read_bytes())['order']
        self.assertEqual(ipad, sealed + [check.PHONE['collection']])

    def test_deterministic_and_shipped_overlay_is_current(self):
        with tempfile.TemporaryDirectory(prefix='wallpaper-test-', dir=SCRATCH) as again:
            generate(Path(again) / 'overlay')
            self.assertEqual(digest_tree(Path(again) / 'overlay'), digest_tree(self.out))
        if SHIPPED.is_dir():
            self.assertEqual(digest_tree(SHIPPED), digest_tree(self.out), '工具/phone-wallpaper-overlay is not a fresh build')

    def mutated(self, change):
        tmp = tempfile.TemporaryDirectory(prefix='wallpaper-test-', dir=SCRATCH)
        self.addCleanup(tmp.cleanup)
        copy = Path(tmp.name) / 'overlay'
        shutil.copytree(self.out, copy, symlinks=True)
        change(copy)
        return check.check(copy, OS_ROOT)[0]

    def test_rejects_capital_any(self):
        def change(d):
            for identifier in (7565, 7570):
                path = d / BUNDLE.format(identifier) / 'Wallpaper.plist'
                plist = plistlib.loads(path.read_bytes())
                plist['logicalScreenClass'] = 'Any'
                path.write_bytes(plistlib.dumps(plist))
        text = '\n'.join(self.mutated(change))
        self.assertIn("logicalScreenClass 'Any' is neither 'any'", text)
        self.assertIn('no loaded bundle for the default wallpaper 7565', text)
        self.assertIn('Solid Black', text)

    def test_rejects_heic_symlink_and_bad_png(self):
        def heic(d):
            for identifier in (7565, 7570):
                caml = d / CA.format(identifier) / 'main.caml'
                caml.write_text(caml.read_text().replace('assets/J120.png"', 'assets/J120.HEIC"'))
        self.assertTrue(any('HEIC/HEIF image' in p for p in self.mutated(heic)))

        def link(d):
            assets = d / CA.format(7565) / 'assets'
            shutil.rmtree(assets)
            assets.symlink_to(FLAT_CA / 'assets')
        problems = self.mutated(link)
        self.assertTrue(any('symbolic links in the overlay' in p for p in problems))
        self.assertTrue(any('through the symbolic link assets' in p for p in problems))

        def sixteen(d):
            for path in (d / CA.format(7565) / 'assets' / 'J120.png', d / CA.format(7570) / 'assets' / 'J120.png'):
                data = bytearray(path.read_bytes())
                data[24] = 16                                               # IHDR bit depth
                path.unlink()
                path.write_bytes(bytes(data))
        self.assertTrue(any('PNG bit depth 16' in p for p in self.mutated(sixteen)))

    def test_rejects_product_types_and_missing_order(self):
        def products(d):
            path = d / BUNDLE.format(7565) / 'Wallpaper.plist'
            plist = plistlib.loads(path.read_bytes())
            plist['productTypes'] = ['iPad7,1', 'iPad7,2']
            path.write_bytes(plistlib.dumps(plist))
        self.assertTrue(any('7565.UniXNU_Any.wallpaper is not loaded' in p and 'productTypes' in p for p in self.mutated(products)))

        def order(d):
            (d / 'Collections~iphone.plist').write_bytes(plistlib.dumps({'collections': []}))
        self.assertTrue(any('Collections~iphone.plist has no "order"' in p for p in self.mutated(order)))

    @unittest.skipUnless(OLD.is_dir(), 'no archived overlay of 2026-10-30')
    def test_the_overlay_of_2026_10_30_fails(self):
        text = '\n'.join(check.check(OLD, OS_ROOT)[0])
        self.assertIn("logicalScreenClass 'Any'", text)
        self.assertIn('Solid Black', text)
        self.assertIn('symbolic links in the overlay', text)
        problems, facts = [], {}
        check.render([str(OLD / CA.format(7565))], problems, facts)          # its HEIC link points at a path this Mac lacks
        self.assertTrue(any('did not load' in p or 'renders' in p for p in problems), problems)


@unittest.skipUnless(os.environ.get('WALLPAPER_SIM') == '1' and SHIPPED.is_dir() and HAVE_MAC, 'WALLPAPER_SIM=1 boots an iOS simulator')
class SimulatorWallpaperKit(unittest.TestCase):
    """Apple's own +[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:] (an installed iOS simulator runtime, default iOS 26.5; the rule
    is the one of 21H461's 0x1b970f628) on the shipped bundles and on capital-"Any" copies of them."""

    def test_wallpaperkit_loads_the_bundles(self):
        runtime = os.environ.get('WALLPAPER_SIM_RUNTIME', 'com.apple.CoreSimulator.SimRuntime.iOS-26-5')
        with tempfile.TemporaryDirectory(prefix='wallpaper-sim-', dir=SCRATCH) as tmp:
            tmp = Path(tmp).resolve()
            probe = tmp / 'wkcheck'
            subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-arch', 'arm64', '-mios-simulator-version-min=17.0',
                            '-fobjc-arc', '-framework', 'Foundation', str(HERE / 'phone-wallpaper-wkcheck.m'), '-o', str(probe)],
                           check=True, capture_output=True)
            capital = tmp / 'capital'
            for identifier in (7565, 7570):
                source = SHIPPED / BUNDLE.format(identifier)
                copy = capital / source.name
                copy.mkdir(parents=True)
                plist = plistlib.loads((source / 'Wallpaper.plist').read_bytes())
                plist['logicalScreenClass'] = 'Any'
                (copy / 'Wallpaper.plist').write_bytes(plistlib.dumps(plist))
            udid = subprocess.run(['xcrun', 'simctl', 'create', 'wallpaper-wkcheck', 'com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro',
                                   runtime], check=True, capture_output=True, text=True).stdout.strip()
            try:
                subprocess.run(['xcrun', 'simctl', 'boot', udid], check=True, capture_output=True)
                subprocess.run(['xcrun', 'simctl', 'bootstatus', udid, '-b'], check=True, capture_output=True)
                bundles = [str((SHIPPED / BUNDLE.format(i)).resolve()) for i in (7565, 7570)]
                bundles += [str(capital / Path(BUNDLE.format(i)).name) for i in (7565, 7570)]
                out = subprocess.run(['xcrun', 'simctl', 'spawn', udid, str(probe), *bundles], check=True, capture_output=True,
                                     text=True).stdout
            finally:
                subprocess.run(['xcrun', 'simctl', 'shutdown', udid], capture_output=True)
                subprocess.run(['xcrun', 'simctl', 'delete', udid], capture_output=True)
        self.assertIn("any constant: 'any'", out)
        verdicts = dict(reversed(line.split(None, 1)) for line in out.splitlines() if line.startswith(('LOADS', 'REJECT')))
        for path in bundles[:2]:
            self.assertEqual(verdicts.get(path), 'LOADS', out)
        for path in bundles[2:]:
            self.assertEqual(verdicts.get(path), 'REJECT', out)


class SharedCache(unittest.TestCase):
    @unittest.skipUnless(check.DSC_ANY[0].is_file(), 'no SystemOS shared cache')
    def test_logical_screen_class_any_is_lowercase(self):
        self.assertEqual(check.any_constant(), b'any\0')


@unittest.skipUnless(HAVE_MAC and FLAT_CA.is_dir() and SHIPPED.is_dir(), 'needs hdiutil, the OS volume and the shipped overlay')
class SeedTool(unittest.TestCase):
    """replace-phone-wallpaper.py on a 64 MiB APFS container that looks like a phone Data seed."""

    def test_replace_and_check(self):
        sys.path.insert(0, str(TOP / 'UniXNU/drivers/DarwinBoot/Test'))
        import packed_ramdisk
        with tempfile.TemporaryDirectory(prefix='wallpaper-seed-test-', dir=SCRATCH) as tmp:
            tmp = Path(tmp)
            subprocess.run(['hdiutil', 'create', '-size', '64m', '-fs', 'APFS', '-layout', 'NONE', '-volname', 'Data', '-type', 'UDIF',
                            str(tmp / 'data.dmg')], check=True, capture_output=True)
            subprocess.run(['hdiutil', 'convert', str(tmp / 'data.dmg'), '-format', 'UDTO', '-o', str(tmp / 'data')], check=True,
                           capture_output=True)
            raw = tmp / 'data.cdr'
            mount = tmp / 'mnt'
            mount.mkdir()
            attached = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
                                                               '-nobrowse', '-owners', 'off', '-mountpoint', str(mount), '-plist',
                                                               str(raw)]))
            try:
                (mount / 'mobile/Library').mkdir(parents=True)
                (mount / 'ngpu/addons').mkdir(parents=True)
                (mount / 'ngpu/addons/marker').write_bytes(b'unchanged')
                if OLD.is_dir():
                    shutil.copytree(OLD, mount / 'ngpu/wallpaper-collections', symlinks=True)
            finally:
                subprocess.run(['hdiutil', 'detach', attached['system-entities'][0]['dev-entry']], check=True, capture_output=True)
            packed_ramdisk.pack(raw, tmp / 'seed.dbrdisk')
            tool = [sys.executable, '-B', str(HERE / 'replace-phone-wallpaper.py')]
            if OLD.is_dir():
                before = subprocess.run(tool + ['--check', '--scratch', str(tmp), str(tmp / 'seed.dbrdisk')], capture_output=True,
                                        text=True)
                self.assertEqual(before.returncode, 1, before.stdout + before.stderr)
                self.assertIn('Solid Black', before.stdout)
            made = subprocess.run(tool + ['--no-budget', '--scratch', str(tmp), str(tmp / 'seed.dbrdisk'), str(tmp / 'new.dbrdisk')],
                                  capture_output=True, text=True)
            self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
            report = json.loads((tmp / 'new.json').read_text())
            self.assertEqual(report['default'], {'iphone': ['7565.UniXNU_Any.wallpaper'], 'ipad': ['7570.UniXNU_Any.wallpaper']})
            after = subprocess.run(tool + ['--check', '--render', '--scratch', str(tmp), str(tmp / 'new.dbrdisk')],
                                   capture_output=True, text=True)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            # the rest of the volume is untouched and the shared images stay one copy
            logical, extents = packed_ramdisk.regions((tmp / 'new.dbrdisk').read_bytes())
            raw2 = tmp / 'new.raw'
            with raw2.open('wb') as handle:
                handle.truncate(logical)
                for offset, data in extents:
                    handle.seek(offset)
                    handle.write(data)
            mount2 = tmp / 'mnt2'
            mount2.mkdir()
            attached = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
                                                               '-readonly', '-nobrowse', '-owners', 'off', '-mountpoint',
                                                               str(mount2), '-plist', str(raw2)]))
            try:
                self.assertEqual((mount2 / 'ngpu/addons/marker').read_bytes(), b'unchanged')
                inside = mount2 / 'ngpu/wallpaper-collections'
                self.assertEqual(digest_tree(inside), digest_tree(SHIPPED))
                a = inside / CA.format(7565) / 'assets/J120.png'
                b = inside / CA.format(7570) / 'assets/J120.png'
                self.assertEqual(os.stat(a).st_ino, os.stat(b).st_ino)
            finally:
                subprocess.run(['hdiutil', 'detach', attached['system-entities'][0]['dev-entry']], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
