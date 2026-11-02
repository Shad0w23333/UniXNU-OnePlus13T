#!/usr/bin/env python3
"""List the C++ classes of kexts in the pinned 21H461 J120 kernelcache (legacy prelinked format).

Read-only host analysis for the research notes. For each kext it finds the OSMetaClass constructor calls in the
kext's static initialisers (name, instance size, superclass), the class vtable (through the class's getMetaClass
slot), the slot count, the pure-virtual slots, and which slots override the parent. For every slot it also lists the
identifier-like strings the slot's function references in the kext (the kexts log with `%s::%s` and __FUNCTION__),
which is a hint for the method name, never a proof.

ipsw 3.1 cannot do this here: `ipsw kernel cpp`/`extract` only handle MH_FILESET kernelcaches.

Usage:
  python3 -I kext_classes.py --kernelcache UniXNU/out/ipsw/21H461/kernelcache.decompressed \
      --prelink-kit UniXNU-Pagani-Drivers/kits/NativePrelink --kext com.apple.iokit.IOUSBDeviceFamily [--class X] [--json OUT]
Addresses printed are the original (unslid) kernelcache addresses; the 13T kernel places them at original - 0x1000000.
"""
import argparse
import json
import re
import struct
import sys
from pathlib import Path

PINNED = 'f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'


def sext(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def decode_bitmask(n, imms, immr, regsize):
    # ARM ARM DecodeBitMasks (immediate form only).
    combined = (n << 6) | (~imms & 0x3f)
    length = combined.bit_length() - 1
    if length < 1:
        return None
    size = 1 << length
    levels = size - 1
    s, r = imms & levels, immr & levels
    if s == levels:
        return None
    welem = (1 << (s + 1)) - 1
    elem = ((welem >> r) | (welem << (size - r))) & ((1 << size) - 1)
    value = 0
    for i in range(regsize // size):
        value |= elem << (i * size)
    return value


class Cache:
    def __init__(self, path, kit):
        sys.path.insert(0, str(kit))
        from native_prelink import macho, backed_offset, exports, unserialize_xml
        self.macho, self.backed_offset = macho, backed_offset
        self.data = Path(path).read_bytes()
        import hashlib
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.image = macho(self.data)
        self.names = {}
        for address, names in exports(self.data, self.image).items():
            for name in names:
                self.names[name] = address
        self.by_address = {}
        for name, address in self.names.items():
            self.by_address.setdefault(address, name)
        info = next(s for s in self.image['segments'] if s['name'] == '__PRELINK_INFO')
        at = backed_offset(self.image['segments'], info['va'], info['filesize'], len(self.data))
        self.entries = {e['CFBundleIdentifier']: e
                        for e in unserialize_xml(self.data[at:at + info['filesize']])['_PrelinkInfoDictionary']}
        self.code = [(s['va'], s['va'] + s['size']) for s in self.image['segments']
                     if s['name'] in ('__TEXT_EXEC', '__PLK_TEXT_EXEC') and s['size']]

    def read(self, va, size):
        at = self.backed_offset(self.image['segments'], va, size, len(self.data))
        return self.data[at:at + size]

    def u32(self, va):
        return struct.unpack('<I', self.read(va, 4))[0]

    def u64(self, va):
        return struct.unpack('<Q', self.read(va, 8))[0]

    def cstring(self, va, limit=256):
        try:
            blob = self.read(va, limit)
        except ValueError:
            blob = self.read(va, 1)
        return blob.split(b'\0', 1)[0].decode('latin1')

    def is_code(self, va):
        return any(a <= va < b for a, b in self.code)

    def kext(self, identifier):
        entry = self.entries[identifier]
        header = entry['_PrelinkExecutableLoadAddr']
        image = self.macho(self.data, self.backed_offset(self.image['segments'], header, 32, len(self.data)))
        sections = {(s['name'], c['name']): c for s in image['segments'] for c in s['sections']}
        return image, sections


def stub_target(cache, va):
    """adrp x16, P; ldr x16, [x16, #o]; br x16  ->  the GOT value (unslid)."""
    try:
        w0, w1, w2 = struct.unpack('<3I', cache.read(va, 12))
    except ValueError:
        return None
    if (w0 & 0x9f00001f) != 0x90000010 or (w1 & 0xffc003ff) != 0xf9400210 or w2 != 0xd61f0200:
        return None
    page = (va & ~0xfff) + (sext(((w0 >> 29) & 3) | (((w0 >> 5) & 0x7ffff) << 2), 21) << 12)
    got = page + ((w1 >> 10) & 0xfff) * 8
    return cache.u64(got)


def walk(cache, start, limit=600, on_call=None, on_ref=None):
    """Linear walk with a tiny constant tracker; stops at RET/BR/unconditional B."""
    regs = {}
    va = start
    for _ in range(limit):
        try:
            w = cache.u32(va)
        except ValueError:
            return
        rd = w & 31
        if (w & 0x9f000000) == 0x90000000:                       # ADRP
            regs[rd] = (va & ~0xfff) + (sext(((w >> 29) & 3) | (((w >> 5) & 0x7ffff) << 2), 21) << 12)
        elif (w & 0xff000000) == 0x91000000:                     # ADD Xd, Xn, #imm{, lsl 12}
            rn = (w >> 5) & 31
            imm = ((w >> 10) & 0xfff) << (12 if (w >> 22) & 1 else 0)
            regs[rd] = regs[rn] + imm if regs.get(rn) is not None else None
            if regs[rd] is not None and on_ref:
                on_ref(regs[rd])
        elif (w & 0xffc00000) == 0xf9400000:                     # LDR Xt, [Xn, #imm]
            rn = (w >> 5) & 31
            if rn in regs and regs[rn] is not None:
                try:
                    regs[rd] = cache.u64(regs[rn] + ((w >> 10) & 0xfff) * 8)
                except ValueError:
                    regs[rd] = None
            else:
                regs[rd] = None
        elif (w & 0x7f800000) == 0x52800000:                     # MOVZ
            regs[rd] = ((w >> 5) & 0xffff) << (((w >> 21) & 3) * 16)
        elif (w & 0x7f800000) == 0x72800000:                     # MOVK
            shift = ((w >> 21) & 3) * 16
            if rd in regs and regs[rd] is not None:
                regs[rd] = (regs[rd] & ~(0xffff << shift)) | (((w >> 5) & 0xffff) << shift)
        elif (w & 0x7f800000) == 0x32000000 and ((w >> 5) & 31) == 31:   # ORR Rd, ZR, #bitmask (MOV)
            sf = w >> 31
            regs[rd] = decode_bitmask((w >> 22) & 1, (w >> 10) & 0x3f, (w >> 16) & 0x3f, 64 if sf else 32)
        elif (w & 0x7fe0ffe0) == 0x2a0003e0:                     # MOV Rd, Rm
            rm = (w >> 16) & 31
            regs[rd] = regs.get(rm)
        elif (w & 0xfc000000) == 0x94000000:                     # BL
            target = va + (sext(w & 0x3ffffff, 26) << 2)
            if on_call:
                on_call(va, target, dict(regs))
            for r in range(0, 19):
                regs.pop(r, None)
        elif w == 0xd65f03c0 or (w & 0xfffffc1f) == 0xd61f0000:  # RET / BR
            return
        elif (w & 0xfc000000) == 0x14000000:                     # B (tail call or loop)
            target = va + (sext(w & 0x3ffffff, 26) << 2)
            if on_call:
                on_call(va, target, dict(regs))
            return
        va += 4


def classes(cache, identifier):
    image, sections = cache.kext(identifier)
    ctors = {address for name, address in cache.names.items() if name.startswith('__ZN11OSMetaClassC2EPKcPKS_j')}
    inits = sections.get(('__DATA_CONST', '__mod_init_func'))
    found = []
    if inits:
        for i in range(inits['size'] // 8):
            function = cache.u64(inits['va'] + i * 8)

            def call(va, target, regs):
                real = target if target in ctors else stub_target(cache, target)
                if real in ctors and regs.get(1):
                    found.append({'meta': regs.get(0), 'name': cache.cstring(regs[1]), 'super_meta': regs.get(2),
                                  'size': regs.get(3), 'init': function})
            walk(cache, function, limit=4000, on_call=call)
    text = sections[('__TEXT_EXEC', '__text')]
    blob = cache.read(text['va'], text['size'])
    getmeta = {}
    for off in range(0, len(blob) - 12, 4):
        w0, w1, w2 = struct.unpack_from('<3I', blob, off)
        if (w0 & 0x9f00001f) == 0x90000000 and (w1 & 0xffc003ff) == 0x91000000 and w2 == 0xd65f03c0:
            va = text['va'] + off
            value = (va & ~0xfff) + (sext(((w0 >> 29) & 3) | (((w0 >> 5) & 0x7ffff) << 2), 21) << 12) + ((w1 >> 10) & 0xfff)
            getmeta.setdefault(value, []).append(va)
    consts = [c for (seg, name), c in sections.items() if seg == '__DATA_CONST' and name == '__const']
    pointers = {}
    for c in consts:
        raw = cache.read(c['va'], c['size'])
        for off in range(0, len(raw) - 7, 8):
            pointers.setdefault(struct.unpack_from('<Q', raw, off)[0], []).append(c['va'] + off)
    service = cache.names['__ZTV9IOService'] + 16
    meta_slot = [i for i in range(168) if cache.by_address.get(cache.u64(service + i * 8), '').endswith('12getMetaClassEv')]
    slot = meta_slot[0] if meta_slot else 7
    for cls in found:
        cls['vtable'] = None
        for fn in getmeta.get(cls['meta'], []):
            for p in pointers.get(fn, []):
                start = p - slot * 8
                if cache.u64(start - 16) == 0 and cache.u64(start - 8) == 0:
                    cls['vtable'] = start
        if cls['vtable']:
            slots = []
            va = cls['vtable']
            while True:
                value = cache.u64(va)
                if not value or not cache.is_code(value):
                    break
                slots.append(value)
                va += 8
            cls['slots'] = slots
    return found, sections


def string_hints(cache, function, sections, limit=500):
    cstr = sections.get(('__TEXT', '__cstring'))
    hints = []
    if not cstr:
        return hints

    def ref(value):
        if cstr['va'] <= value < cstr['va'] + cstr['size']:
            s = cache.cstring(value, 96)
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{2,63}', s) and s not in hints:
                hints.append(s)
    walk(cache, function, limit=limit, on_ref=ref)
    return hints


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kernelcache', required=True)
    parser.add_argument('--prelink-kit', required=True, help='directory that holds native_prelink.py')
    parser.add_argument('--kext', action='append', required=True)
    parser.add_argument('--class', dest='only', action='append', default=[])
    parser.add_argument('--hints', action='store_true', help='print the per-slot string hints')
    parser.add_argument('--json')
    args = parser.parse_args()
    cache = Cache(args.kernelcache, args.prelink_kit)
    if cache.sha256 != PINNED:
        print(f'WARNING: kernelcache sha256 {cache.sha256} is not the pinned 21H461 J120 image', file=sys.stderr)
    pure = cache.names.get('___cxa_pure_virtual')
    meta_names = {}
    for name, address in cache.names.items():
        m = re.fullmatch(r'__ZN(\d+)(\w+?)10gMetaClassE', name)
        if m and len(m.group(2)) == int(m.group(1)):
            meta_names[address] = m.group(2)
    report = {}
    all_classes = []
    for identifier in args.kext:
        found, sections = classes(cache, identifier)
        for cls in found:
            meta_names.setdefault(cls['meta'], cls['name'])
        all_classes.append((identifier, found, sections))
    vt_by_name = {}
    for _, found, _ in all_classes:
        for cls in found:
            if cls.get('slots'):
                vt_by_name[cls['name']] = cls['slots']

    def parent_slots(name):
        if name in vt_by_name:
            return vt_by_name[name]
        symbol = f'__ZTV{len(name)}{name}'
        if symbol in cache.names:
            va, slots = cache.names[symbol] + 16, []
            while True:
                value = cache.u64(va)
                if not value or not cache.is_code(value):
                    return slots
                slots.append(value)
                va += 8
        return None

    for identifier, found, sections in all_classes:
        out = []
        for cls in found:
            if args.only and cls['name'] not in args.only:
                continue
            parent = meta_names.get(cls['super_meta'], hex(cls['super_meta'] or 0))
            item = {'class': cls['name'], 'size': cls['size'], 'parent': parent,
                    'meta': hex(cls['meta'] or 0), 'vtable': hex(cls['vtable']) if cls['vtable'] else None}
            slots = cls.get('slots') or []
            base = parent_slots(parent) or []
            item['slots'] = len(slots)
            item['parent_slots'] = len(base) if base else None
            item['pure_virtual'] = [i for i, v in enumerate(slots) if v == pure]
            item['overrides'] = [i for i, v in enumerate(slots) if i < len(base) and v != base[i]]
            item['new_slots'] = list(range(len(base), len(slots))) if base else []
            if args.hints:
                item['hints'] = {i: (cache.by_address.get(v) or ','.join(string_hints(cache, v, sections)[:4]))
                                 for i, v in enumerate(slots) if v != pure and (i in item['overrides'] or i in item['new_slots'])}
            out.append(item)
        report[identifier] = out
        print(f'== {identifier}: {len(out)} classes')
        for item in out:
            print(f"  {item['class']}  size={item['size'] if item['size'] is None else hex(item['size'])} parent={item['parent']} "
                  f"vtable={item['vtable']} slots={item['slots']} parent_slots={item['parent_slots']} "
                  f"pure={item['pure_virtual']} overrides={len(item['overrides'])} new={len(item['new_slots'])}")
            if args.hints:
                for i, hint in item['hints'].items():
                    print(f'      slot {i}: {hint}')
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1) + '\n')


if __name__ == '__main__':
    main()
