#!/usr/bin/env python3
"""Check a phone wallpaper overlay (make-phone-wallpaper-overlay.py output, or the copy inside a Data seed) against what the 21H461
WallpaperKit, Core Animation and this hardware accept, so that the default lock and home screen wallpaper is not black.

  check-phone-wallpaper-overlay.py [--os-root OS_ROOT] [--render] [--json] OVERLAY_DIR

OVERLAY_DIR is what the kernel shows over /Library/Wallpaper/Collections (phone-wallpaper-collections.patch). OS_ROOT (default the
mounted 21H461 OS volume, when present) supplies the sealed collections the union keeps visible. --render also loads every layered
animation with macOS QuartzCore's CAPackage reader (phone-wallpaper-render.m, built with xcrun clang) and renders the lock and home
states of a 1216x2640 (13T pixels) window. Exit status 1 when a rule fails.

Rules, from the 21H461 shared cache (unslid addresses, SystemOS dyld_shared_cache_arm64*):
 W1 +[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:] (0x1b970f628): a wallpaper bundle is a directory whose extension, lowercased,
    is "wallpaper", holding Wallpaper.plist. Its logicalScreenClass must be WKLogicalScreenClassString() ("%.0fw-%.0fh@%@x~%@") or
    exactly "any" ([NSString isEqualToString:], case sensitive; _WKWallpaperRepresentingLogicalScreenClassAny = CFString "any",
    dyld_shared_cache_arm64.17 file offset 0x5f28cf4); otherwise "Wallpaper Bundle logical screen class ... does not match" and the
    bundle is not loaded. productTypes, when present, must hold the product type; unsupportedProductTypes must not.
 W2 -[WKDefaultWallpaperManager _loadDefaultWallpaperInformation] (0x1b971ec10), without /Library/Wallpaper/DefaultWallpapers~<device>
    .plist: iPhone collection 62879BFA-F700-4384-83E1-7AD1CC0D0817 wallpaper 7565, iPad collection ...081E wallpaper 7570; then that
    identifier in any loaded collection; else "No compatible wallpaper found on disk ...; falling back to synthesized wallpaper": a
    black 400.Solid_Black.png ("Solid Black"), which PosterBoard installs as the default CollectionsPoster poster
    (+[PBFInitialPosterHelper initialPosterProviderForRole:descriptorIdentifier:], 0x1ca8f7618). That is a black lock and home screen.
 W3 _loadSystemWallpaperCollections: Collections~<GSDeviceName>.plist with an "order" array of collection identifiers.
 C1 type LayeredAnimation: backgroundAnimationFileName (and the optional foreground/floating names) a .ca directory holding index.xml
    (rootDocument, default main.caml) and that CAML document; every CGImage it names (src) inside that directory.
 I1 no HEIC/HEIF image: no hardware HEVC decoder here, ImageIO leaves the layer empty (UniXNU 910a19d); PNG only (ImageIO decodes PNG
    on the CPU, never with the JPEG/HEIF hardware path), 8-bit RGB or RGBA, not interlaced, 1024..4096 pixels a side.
 I2 no symbolic link anywhere: the images live in the overlay itself (the old overlay drew the sealed HEIC through an absolute
    symlink across the bindfs union)."""
import argparse
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOP = HERE.parent
DEFAULT_OS_ROOT = TOP / 'build-userspace/a830-inputs/osmnt-21H461'
DSC_ANY = (TOP / 'build-userspace/a830-inputs/systemos-21H461/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64.17',
           0x5f28cf4)
ANY = 'any'
PHONE = dict(device='iphone', collection='62879BFA-F700-4384-83E1-7AD1CC0D0817', wallpaper=7565, product='iPhone16,1',
             screen='405w-880h@3x~iphone')
PAD = dict(device='ipad', collection='62879BFA-F700-4384-83E1-7AD1CC0D081E', wallpaper=7570, product='iPad7,1',
           screen='405w-880h@3x~ipad')
CAML = '{http://www.apple.com/CoreAnimation/1.0}'
ANIMATION_KEYS = ('backgroundAnimationFileName', 'foregroundAnimationFileName', 'floatingAnimationFileNameKey')
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
MIN_EDGE, MAX_EDGE = 1024, 4096
BUDGET = 16 * 1024**2          # the seed is packed page by page, uncompressed, into the boot payload (18.6 MiB free on 2026-10-31)
WINDOW = (1216, 2640)          # the 13T panel in pixels (405.33x880 points at 3x)
RENDER_STATES = ('Lock PortraitUp Light', 'Home PortraitUp Light', 'Lock PortraitUp Dark', 'Home PortraitUp Dark')
MAX_DARK = 0.02


def load_plist(path, problems, what):
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        problems.append(f'{what}: {path} unreadable ({error})')
        return None
    return value


def png_header(path):
    """(width, height, bit depth, colour type, interlace) of a PNG file, or None."""
    with path.open('rb') as handle:
        head = handle.read(33)
    if len(head) < 33 or head[:8] != PNG_SIGNATURE or head[12:16] != b'IHDR':
        return None
    width, height, depth, colour, _, _, interlace = struct.unpack('>IIBBBBB', head[16:29])
    return width, height, depth, colour, interlace


def any_constant():
    """The bytes of _WKWallpaperRepresentingLogicalScreenClassAny in the local shared cache, when it is there."""
    path, offset = DSC_ANY
    if not path.is_file():
        return None
    with path.open('rb') as handle:
        handle.seek(offset)
        return handle.read(4)


def image_references(caml, problems, what):
    """The src of every CGImage value in a CAML document."""
    try:
        tree = ET.parse(caml)
    except (OSError, ET.ParseError) as error:
        problems.append(f'{what}: {caml.name} is no CAML document ({error})')
        return []
    found = []
    for element in tree.iter():
        if element.get('type') == 'CGImage':
            source = element.get('src')
            if not source:
                problems.append(f'{what}: a CGImage in {caml.name} has no src')
                continue
            found.append((source, element.get('relativePath')))
    return found


def check_animation(ca, problems, facts, what):
    if ca.is_symlink() or not ca.is_dir():
        problems.append(f'{what}: {ca.name} is not a directory of the overlay')
        return
    index = load_plist(ca / 'index.xml', problems, what) if (ca / 'index.xml').is_file() else None
    if index is None:
        problems.append(f'{what}: {ca.name}/index.xml missing or unreadable')
        return
    root = index.get('rootDocument', 'main.caml')
    caml = ca / root
    if not caml.is_file():
        problems.append(f'{what}: {ca.name}/{root} missing')
        return
    references = image_references(caml, problems, what)
    if not references:
        problems.append(f'{what}: {ca.name}/{root} names no image')
    for source, relative in sorted(set(references)):
        label = f'{what}: {ca.name}/{source}'
        if re.search(r'\.(heic|heif|hif|avif)$', source, re.I):
            problems.append(f'{label}: HEIC/HEIF image, which this hardware cannot decode (no AppleAVD/VXD HEVC decoder)')
            continue
        if source.startswith('/') or '..' in Path(source).parts:
            problems.append(f'{label}: the image must lie inside the .ca directory')
            continue
        if relative is not None and relative.lstrip('/') != source:
            problems.append(f'{label}: relativePath {relative!r} differs from src')
        path = ca / source
        walked = ca
        for part in Path(source).parts:
            walked = walked / part
            if walked.is_symlink():
                problems.append(f'{label}: reached through the symbolic link {walked.relative_to(ca)}')
                break
        else:
            if not path.is_file():
                problems.append(f'{label}: missing')
                continue
            header = png_header(path)
            if header is None:
                problems.append(f'{label}: not a PNG file')
                continue
            width, height, depth, colour, interlace = header
            if depth != 8 or colour not in (2, 6) or interlace:
                problems.append(f'{label}: PNG bit depth {depth}, colour type {colour}, interlace {interlace} '
                                '(8-bit RGB or RGBA, not interlaced)')
            if not (MIN_EDGE <= width <= MAX_EDGE and MIN_EDGE <= height <= MAX_EDGE):
                problems.append(f'{label}: {width}x{height} pixels')
            facts.setdefault('images', []).append(dict(path=str(path), width=width, height=height, bytes=path.stat().st_size))


def bundle_loads(bundle, plist, identity):
    """W1 for one identity: whether shouldLoadWallpaperBundleAtURL: keeps the bundle, and why not."""
    if bundle.suffix.lower() != '.wallpaper' or not bundle.is_dir() or not (bundle / 'Wallpaper.plist').is_file():
        return False, 'not a .wallpaper directory with Wallpaper.plist'
    screen = plist.get('logicalScreenClass')
    if not isinstance(screen, str) or screen not in (identity['screen'], ANY):
        return False, f'logicalScreenClass {screen!r} is neither {ANY!r} nor {identity["screen"]!r} (case sensitive)'
    unsupported = plist.get('unsupportedProductTypes')
    if isinstance(unsupported, list) and identity['product'] in unsupported:
        return False, f'unsupportedProductTypes lists {identity["product"]}'
    products = plist.get('productTypes')
    if isinstance(products, list) and products and identity['product'] not in products:
        return False, f'productTypes {products} without {identity["product"]}'
    return True, ''


def collections(directory):
    """identifier -> (collection directory, WallpaperCollection.plist) of the *.wallpaperCollection directories in DIRECTORY."""
    found = {}
    if directory and directory.is_dir():
        for collection in sorted(directory.glob('*.wallpaperCollection')):
            try:
                info = plistlib.loads((collection / 'WallpaperCollection.plist').read_bytes())
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            if isinstance(info.get('identifier'), str):
                found[info['identifier']] = (collection, info)
    return found


def check(overlay, os_root=None):
    """The problems (a list of strings) and facts (a dict) of OVERLAY."""
    overlay = Path(overlay)
    problems, facts = [], {}
    constant = any_constant()
    if constant is not None and constant != ANY.encode() + b'\0':
        problems.append(f'the shared cache holds {constant!r} at the logicalScreenClass "any" constant: rules are stale')
    links = [str(p.relative_to(overlay)) for p in overlay.rglob('*') if p.is_symlink()]
    if links:
        problems.append('symbolic links in the overlay: ' + ', '.join(links))
    files = [p for p in overlay.rglob('*') if p.is_file() and not p.is_symlink()]
    facts['bytes'] = sum(p.stat().st_size for p in files)
    # Hard links (7570 shares 7565's images) take their space once in the seed.
    facts['unique_bytes'] = sum({(s.st_dev, s.st_ino): s.st_size for s in (p.stat() for p in files)}.values())
    if facts['unique_bytes'] > BUDGET:
        problems.append(f'the overlay takes {facts["unique_bytes"]} bytes, more than {BUDGET}: the seed rides in the 192 MiB boot '
                        'payload beside the 127 MB kernel')
    sealed = collections(Path(os_root) / 'Library/Wallpaper/Collections' if os_root else None)
    own = collections(overlay)
    visible = {**sealed, **own}
    for identity in (PHONE, PAD):
        device = identity['device']
        listing = overlay / f'Collections~{device}.plist'
        if not listing.is_file() and os_root:
            listing = Path(os_root) / f'Library/Wallpaper/Collections/Collections~{device}.plist'
        order = (load_plist(listing, problems, f'Collections~{device}') or {}).get('order') if listing.is_file() else None
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            problems.append(f'Collections~{device}.plist has no "order" array of identifiers (W3)')
            continue
        # The system's own list may name collections that arrive later as MobileAsset content (com.apple.MobileAsset.Wallpaper);
        # WallpaperKit skips those. Only an identifier the overlay adds must be on disk.
        system_list = Path(os_root) / f'Library/Wallpaper/Collections/Collections~{device}.plist' if os_root else None
        system_order = (plistlib.loads(system_list.read_bytes()).get('order') or []) if system_list and system_list.is_file() else []
        loaded = {}
        for identifier in order:
            if identifier not in visible:
                if sealed and identifier not in system_order:
                    problems.append(f'Collections~{device}.plist names {identifier}, which no collection carries')
                continue
            collection, info = visible[identifier]
            for bundle in sorted((collection / 'Wallpapers').glob('*')):
                plist = load_plist(bundle / 'Wallpaper.plist', [], '') if (bundle / 'Wallpaper.plist').is_file() else None
                if not isinstance(plist, dict):
                    continue
                ok, why = bundle_loads(bundle, plist, identity)
                if collection.parent == overlay and not ok:
                    problems.append(f'{device}: {bundle.name} is not loaded (W1): {why}')
                if ok:
                    loaded.setdefault(identifier, {}).setdefault(plist.get('identifier'), []).append((bundle, plist))
        wanted = identity['wallpaper']
        hits = loaded.get(identity['collection'], {}).get(wanted) or \
            next((v[wanted] for v in loaded.values() if wanted in v), None)
        if not hits:
            problems.append(f'{device}: no loaded bundle for the default wallpaper {wanted} (W2): WallpaperKit synthesizes the black '
                            '"Solid Black" wallpaper (400.Solid_Black.png)')
            continue
        facts.setdefault('default', {})[device] = [str(b) for b, _ in hits]
        for bundle, plist in hits:
            what = f'{device} {bundle.name}'
            for location in (plist.get('assets') or {}).values():
                for appearance in (location or {}).values():
                    if appearance.get('type') != 'LayeredAnimation':
                        continue
                    names = [appearance[k] for k in ANIMATION_KEYS if appearance.get(k)]
                    if not appearance.get('backgroundAnimationFileName'):
                        problems.append(f'{what}: LayeredAnimation without backgroundAnimationFileName')
                    for name in names:
                        check_animation(bundle / name, problems, facts, what)
                        facts.setdefault('animations', []).append(str(bundle / name))
    facts['animations'] = sorted(set(facts.get('animations', [])))
    return problems, facts


def build_renderer(directory):
    source = HERE / 'phone-wallpaper-render.m'
    binary = Path(directory) / 'phone-wallpaper-render'
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O1', '-Wall', '-framework', 'Foundation', '-framework', 'QuartzCore',
                    '-framework', 'CoreGraphics', str(source), '-o', str(binary)], check=True, capture_output=True)
    return binary


def render(animations, problems, facts):
    """Loads and renders every animation with Core Animation's own reader (the device reads the same CAML)."""
    with tempfile.TemporaryDirectory(prefix='wallpaper-render-') as scratch:
        binary = build_renderer(scratch)
        for ca in animations:
            run = subprocess.run([str(binary), ca, str(WINDOW[0]), str(WINDOW[1]), *RENDER_STATES], capture_output=True, text=True)
            try:
                result = json.loads(run.stdout)
            except ValueError:
                problems.append(f'{ca}: the Core Animation reader failed ({run.stderr.strip()[:200]})')
                continue
            facts.setdefault('render', {})[ca] = result
            name = Path(ca).name
            if not result.get('loaded'):
                problems.append(f'{name}: CAPackage did not load: {result.get("error")}')
                continue
            if not result.get('layerImages'):
                problems.append(f'{name}: no layer holds an image after loading')
            for state in result.get('states', []):
                if state['missing']:
                    problems.append(f'{name}: state {state["name"]} sets {state["missing"]} image(s) that did not load')
            for shot in result.get('renders', []):
                if shot.get('error') or not shot.get('applied'):
                    problems.append(f'{name}: state {shot["state"]} could not be applied ({shot.get("error")})')
                elif shot['dark'] > MAX_DARK:
                    problems.append(f'{name}: {shot["state"]} renders {shot["dark"]:.0%} black in the 13T window')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--os-root', type=Path, default=DEFAULT_OS_ROOT if DEFAULT_OS_ROOT.is_dir() else None)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('overlay', type=Path)
    args = parser.parse_args()
    if not args.overlay.is_dir():
        sys.exit(f'{args.overlay} is not a directory')
    problems, facts = check(args.overlay, args.os_root)
    if args.render and facts.get('animations'):
        if not shutil.which('xcrun'):
            sys.exit('--render needs xcrun (Xcode command line tools)')
        render(facts['animations'], problems, facts)
    if args.json:
        print(json.dumps(dict(problems=problems, facts=facts), indent=2, default=str))
    else:
        for line in problems:
            print('FAIL ' + line)
        for device, bundles in facts.get('default', {}).items():
            print(f'default {device}: ' + ', '.join(Path(b).name for b in bundles))
        for ca, result in facts.get('render', {}).items():
            for shot in result.get('renders', []):
                print(f'render {Path(ca).name} {shot["state"]}: black {shot.get("dark", 1):.3f} mean {shot.get("mean", 0):.1f}')
        print(('PASS' if not problems else 'FAIL') + f' {args.overlay} ({facts.get("unique_bytes", 0)} bytes, '
              f'{facts.get("bytes", 0)} with hard links counted twice)')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
