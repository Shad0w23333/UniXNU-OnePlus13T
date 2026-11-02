#!/usr/bin/env python3
"""Build the wallpaper overlay that gives the iPad root a default lock and home screen wallpaper for the iPhone identity (and for the
iPad identity at display-scale 3): the content of /Library/Wallpaper/Collections that the iPad root lacks.

  make-phone-wallpaper-overlay.py [--edge PIXELS] OS_ROOT OUTPUT_DIR

OS_ROOT is the mounted OS volume of the 21H461 IPSW (read only). OUTPUT_DIR gets, for /private/var/ngpu/wallpaper-collections in the
Data seed (make-phone-seed.py --wallpaper, or replace-phone-wallpaper.py for a seed made before), which the kernel shows over
/Library/Wallpaper/Collections when it carries phone-wallpaper-collections.patch (a read-only union, like the other GPU addon views):

  Collections~iphone.plist    WallpaperKit reads Collections/Collections~<GSDeviceName>.plist; without it (the iPad root has only
                              ~ipad) _loadSystemWallpaperCollections raises NSInvalidArgumentException on the missing "order" key.
  Collections~ipad.plist      the system list plus the collection below, so the iPad identity at display-scale 3 also finds a wallpaper.
  UniXNU_Any.wallpaperCollection
                              identifier 62879BFA-F700-4384-83E1-7AD1CC0D0817 with wallpapers 7565 and 7570: the identifiers
                              -[WKDefaultWallpaperManager _loadDefaultWallpaperInformation] falls back to when DefaultWallpapers~<device>
                              .plist is missing (iPhone: collection ...0817, wallpaper 7565; iPad: wallpaper 7570). When neither is
                              loaded, WallpaperKit synthesizes a black "Solid Black" wallpaper (400.Solid_Black.png) and PosterBoard
                              installs that as the default poster: the black lock and home screen of 2026-10-30/31.
                              Both bundles say logicalScreenClass "any", the exact string of _WKWallpaperRepresentingLogicalScreenClassAny
                              (+[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:] compares with isEqualToString:; the first overlay
                              wrote "Any" and both bundles were rejected), and carry no productTypes.
                              Their animation is the iPad7,1 "Flat" iPadOS 17 one (a single image layer, light and dark) with its two
                              HEIC images converted here to PNG (sips, macOS): this hardware has no HEVC decoder, and ImageIO leaves an
                              HEIC layer empty (UniXNU 910a19d). The PNG images are scaled to --edge pixels (default 2732, as upstream;
                              the layer stretches its contents to its 3416-unit bounds), 8-bit sRGB, and live in each animation's own
                              assets directory: no symbolic link into the sealed volume. 7570's two images are hard links to 7565's
                              (10.5 MB once; the 192 MiB boot payload had 18.6 MiB free on 2026-10-31): copy the directory with its
                              hard links (replace-phone-wallpaper.py and make-phone-seed.py do).
Nothing in OS_ROOT is modified. The result is checked with check-phone-wallpaper-overlay.py (WallpaperKit's rules, the CAML image
references, PNG format; with --render also Core Animation's own reader, when xcrun is there) before the script succeeds."""
import argparse
import hashlib
import importlib.util
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
COLLECTIONS = 'Library/Wallpaper/Collections'
IPAD_COLLECTION = 'iPadOS_17~ipad.wallpaperCollection'
FLAT = 'Wallpapers/7570.iPadOS_17_Flat-1024w-1366h@2x~ipad.wallpaper'
FLAT_CA = '7570.iPadOS_17_Flat_Background-1024w-1366h@2x~ipad.ca'
IMAGES = ('J120', 'J120-Dark')
# Static defaults of -[WKDefaultWallpaperManager _loadDefaultWallpaperInformation] (0x1b971f4e4): iPhone collection 62879BFA-...-0817
# wallpaper 7565, iPad ...-081E wallpaper 7570.
PHONE_COLLECTION = '62879BFA-F700-4384-83E1-7AD1CC0D0817'
STRIPE, BOKEH = '2939274C-F444-4AA8-806E-6E3FAFBA79B1', 'D4894D14-DB0F-4D04-AB02-07C39C5E5466'
NAME = 'UniXNU_Any.wallpaperCollection'
ANY = 'any'        # _WKWallpaperRepresentingLogicalScreenClassAny (CFString "any", dyld_shared_cache_arm64.17 file offset 0x5f28cf4)
EDGE = 2732
SOURCE_SIZE = 3416


def load_checker():
    spec = importlib.util.spec_from_file_location('wallpaper_check', HERE / 'check-phone-wallpaper-overlay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def convert(heic, png, edge):
    """HEIC -> PNG of EDGE pixels a side with macOS's own decoder (sips); 8-bit RGB, not interlaced, sRGB kept."""
    subprocess.run(['sips', '-s', 'format', 'png', '-Z', str(edge), str(heic), '--out', str(png)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    header = load_checker().png_header(png)
    if header is None or header[:2] != (edge, edge) or header[2] != 8 or header[3] not in (2, 6) or header[4]:
        sys.exit(f'sips made an unexpected PNG of {heic.name}: {header}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--edge', type=int, default=EDGE, help=f'PNG size in pixels (1024..{SOURCE_SIZE}, default {EDGE})')
    parser.add_argument('os_root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if not 1024 <= args.edge <= SOURCE_SIZE:
        sys.exit(f'--edge {args.edge}: 1024..{SOURCE_SIZE}')
    root, output = args.os_root, args.output
    system = root / COLLECTIONS
    flat = system / IPAD_COLLECTION / FLAT
    if not (flat / 'Wallpaper.plist').is_file() or not (flat / FLAT_CA / 'main.caml').is_file():
        sys.exit('No iPadOS 17 Flat wallpaper below ' + str(system))
    if output.exists() or output.is_symlink():
        sys.exit('Refuse to overwrite ' + str(output))
    if not shutil.which('sips'):
        sys.exit('sips (macOS) is needed to turn the HEIC images into PNG')
    ipad_list = plistlib.loads((system / 'Collections~ipad.plist').read_bytes())
    order = [uuid for uuid in ipad_list.get('order', []) if uuid != PHONE_COLLECTION]
    if not order:
        sys.exit('Collections~ipad.plist has no order')
    flat_info = plistlib.loads((flat / 'Wallpaper.plist').read_bytes())
    animation = flat_info['assets']['lockAndHome']['default']
    if animation.get('type') != 'LayeredAnimation' or animation.get('backgroundAnimationFileName') != FLAT_CA:
        sys.exit('Unexpected Flat wallpaper metadata')
    main_caml = (flat / FLAT_CA / 'main.caml').read_text()
    for image in IMAGES:
        # The CGImage values name their file twice: src (what the reader loads) and relativePath (the editor's copy of it).
        if main_caml.count(f'src="assets/{image}.HEIC"') == 0:
            sys.exit(f'main.caml of the Flat wallpaper does not use assets/{image}.HEIC')
        main_caml = main_caml.replace(f'src="assets/{image}.HEIC"', f'src="assets/{image}.png"')
        main_caml = main_caml.replace(f'relativePath="/assets/{image}.HEIC"', f'relativePath="/assets/{image}.png"')
    # The image layer keeps its name "J120.HEIC" (a label, as upstream left it); no file reference may stay HEIC.
    if re.search(r'(src|relativePath)="[^"]*\.heic"', main_caml, re.I):
        sys.exit('main.caml still points at a HEIC image')
    write = lambda path, value: path.write_bytes(plistlib.dumps(value, fmt=plistlib.FMT_XML))
    with tempfile.TemporaryDirectory(prefix='wallpaper-png-') as scratch:
        pngs = {}
        for image in IMAGES:
            heic = flat / FLAT_CA / 'assets' / f'{image}.HEIC'
            pngs[image] = Path(scratch) / f'{image}.png'
            convert(heic, pngs[image], args.edge)
        output.mkdir(parents=True)
        write(output / 'Collections~iphone.plist', {'order': [PHONE_COLLECTION, STRIPE, BOKEH]})
        write(output / 'Collections~ipad.plist', {**ipad_list, 'order': order + [PHONE_COLLECTION]})
        collection = output / NAME
        (collection / 'Wallpapers').mkdir(parents=True)
        info = plistlib.loads((system / IPAD_COLLECTION / 'Info.plist').read_bytes())
        info.update(CFBundleIdentifier='org.unixnu.wallpaper.any', CFBundleName='iPadOS 17')
        write(collection / 'Info.plist', info)
        write(collection / 'WallpaperCollection.plist', {'identifier': PHONE_COLLECTION, 'isLegacyContent': False,
                                                           'name': 'iPadOS 17', 'order': [7565, 7570], 'source': 'Apple'})
        first = {}
        for identifier in (7565, 7570):
            bundle = collection / 'Wallpapers' / f'{identifier}.UniXNU_Any.wallpaper'
            ca = f'{identifier}.UniXNU_Any_Background.ca'
            (bundle / ca / 'assets').mkdir(parents=True)
            for name in ('index.xml', 'assetManifest.caml'):
                shutil.copyfile(flat / FLAT_CA / name, bundle / ca / name)
            (bundle / ca / 'main.caml').write_text(main_caml)
            for image in IMAGES:
                target = bundle / ca / 'assets' / f'{image}.png'
                # 7570 shares 7565's images as hard links: the Data seed stores them once (the 192 MiB boot payload of 2026-10-31
                # has 18.6 MiB free beside the kernel and the seed). Readers see two ordinary files; a hard link is no symlink.
                if image in first:
                    os.link(first[image], target)
                else:
                    shutil.copyfile(pngs[image], target)
                    first[image] = target
            meta = {key: value for key, value in flat_info.items() if key not in ('productTypes', 'unsupportedProductTypes')}
            meta.update(identifier=identifier, logicalScreenClass=ANY)
            meta['assets'] = {'lockAndHome': {'default': {**animation, 'identifier': identifier, 'backgroundAnimationFileName': ca}}}
            write(bundle / 'Wallpaper.plist', meta)
    for path in sorted(output.rglob('*')):
        os_mode = 0o755 if path.is_dir() else 0o644
        path.chmod(os_mode)
    checker = load_checker()
    problems, facts = checker.check(output, root)
    if shutil.which('xcrun') and facts.get('animations'):
        checker.render(facts['animations'], problems, facts)
    for path in sorted(output.rglob('*')):
        if path.is_dir():
            print(f'{path.relative_to(output)}  dir')
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            print(f'{path.relative_to(output)}  {path.stat().st_size} bytes  {digest}')
    if problems:
        sys.exit('The overlay fails its check:\n  ' + '\n  '.join(problems))
    print(f'checked: default iphone {Path(facts["default"]["iphone"][0]).name}, ipad {Path(facts["default"]["ipad"][0]).name}, '
          f'{facts["unique_bytes"]} bytes ({facts["bytes"]} with the hard links counted twice)')


if __name__ == '__main__':
    main()
