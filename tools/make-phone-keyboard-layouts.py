#!/usr/bin/env python3
"""Give TextInputUI a phone keyboard family drawn for the 13T's own portrait width (1216 px / 3 = 405.333 points).

  make-phone-keyboard-layouts.py [--base Choco|Truffle] [--width-px 1216] [--scale 3] INPUT OUTPUT
  make-phone-keyboard-layouts.py --check FILE [--width W]

INPUT is an official iOS 17.7 KBLayouts file that holds the iPhone families: KBLayouts_iPhone.dat of 21H16 (iPhone11,6) or
KBLayouts_iPad.dat of 21H461 (its iPhone-* families are the same keyboards, Armenian and Thai being newer). OUTPUT is written as
the file the iPhone identity reads (the overlay's KBLayouts_iPhone.dat), with OUTPUT.json next to it.

Why (iPadOS 17.7.11 21H461, dyld cache addresses):
  * _UIKeyboardGetKBStarName (0x18a45f688) names the phone keyboard "<prefix>-<layout>-<type>", prefix from
    -[TUIKBGraphSerialization keyboardPrefixForWidth:andEdge:] (0x191987344): the width table at the head of this file, keys sorted
    by floatValue descending, first key <= frinta(keyboard width). 405.333 gives 405 -> 375 -> iPhone-PortraitChoco (no "miss").
  * The keyboard's size is the family's design frame (+[UIKeyboardLayoutStar keyboardSizeForInputMode:screenTraits:keyboardType:],
    0x18a45f470: 375 x 216 for Choco) unless _UIKeyboardGetDerivedKeyboardForSpecificOrientation (0x18a2aedd0) supplies a derived
    keyboard with an explicit width. For the phone idiom it does so only for the reference sizes 320x693, 360x780, 390x844, 393x852,
    428x926 and 430x932 (iPads always get one, which is why the iPad identity's 608-point keyboard is fine). 405.333 x 880 gets
    none: the keyboard view is 375 wide (+[UIKeyboard defaultFrameForInterfaceOrientation:], -[UIKeyboardImpl updateLayout]) while
    -[UIKeyboardLayoutStar setKeyplaneName:] stretches the keys to the screen's 405.333 (stretchFactor 0x18a4f0010): keys and touch
    areas no longer line up. Every real iOS 17 iPhone is either a family width (320/375/414) or on that list.
  * So this adds a family whose design width IS 405.333: every iPhone-Portrait<base>-* keyboard copied as
    iPhone-Portrait<base>405-*, its shapes (the only absolute geometry in these keyboards; the few UIKBGeometry objects are merge
    factors in percent) scaled horizontally onto the 1216-pixel grid with UIKit's own rounding (-[UIKBShapeOperator
    _scaleRange:factor:], 0x18a3e79bc), and the portrait table gains 405 -> iPhone-Portrait<base>405. The keyboard then is 405.333
    wide like a native family; UIKit's remaining stretch (405.3333 / float 405.33334) leaves every key where it is.
Everything else stays: the landscape table bytes, the original graph bytes (the new objects are appended after them and refer back
to unchanged standalone objects, see Graph for why only those), and every original name with its offset. The output is parsed
back and checked before it is kept; 工具/test-phone-keyboard-layouts.py tests it, phone-keyboard/check-in-simulator.sh decodes it
with Apple's own TUIKBGraphSerialization.
"""
import argparse
import hashlib
import json
import math
import plistlib
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def f32(v):
    """The float32 the file and UIKit's float math hold for v, as a Python float."""
    return struct.unpack('<f', struct.pack('<f', float(v)))[0]


def frinta(v):
    """ARM64 FRINTA: round to nearest, ties away from zero."""
    return math.copysign(math.floor(abs(v) + 0.5), v)


# ---------------------------------------------------------------------------------------------------------------------------------
# Reader: -[TUIKBGraphSerialization objectForOffset:] and friends (TextInputUI 0x19192f6dc..0x191931e88)
# ---------------------------------------------------------------------------------------------------------------------------------
class Tree:
    """UIKBTree: type, name (bit 0), properties (bit 1), subtrees (bit 2), cache (bit 3)."""
    __slots__ = ('type', 'name', 'props', 'subtrees', 'cache', 'flags')


class Shape:
    """UIKBShape: geometry (bit 0), frame and padded frame."""
    __slots__ = ('geometry', 'frame', 'padded', 'flags')


class Geometry:
    """UIKBGeometry: name, x y w h and the four paddings as (value, unit), explicit (bit 0), template (bit 1)."""
    __slots__ = ('name', 'values', 'flags')


class Merge:
    """UIKBMergeAction: remaining and disappearing key names, ordered keys (bit 0), factors (bit 1)."""
    __slots__ = ('remaining', 'disappearing', 'ordered', 'factors', 'flags')


class Graph:
    """Objects of one blob. Each offset is parsed once; objects keep their identity, so shared objects stay shared.

    -[TUIKBGraphSerialization objectForOffset:] (0x19192fab8) returns an object found in its deserialization cache WITHOUT
    advancing the offset. Apple's writer therefore keeps every shared object standalone: an offset that a reference (or the name
    table) points at is never also read inline inside another object, otherwise a later inline read would hit the cache and the
    parent's next field would be read from the wrong bytes. 'targets' collects the standalone offsets seen while parsing."""

    def __init__(self, blob):
        self.blob = blob
        self.parsed = {}        # offset -> (object, end offset)
        self.offset_of = {}     # id(object) -> offset where it starts (containers and the four classes)
        self.targets = set()    # offsets reached through a reference

    def uint(self, off):
        value = shift = 0
        while True:
            byte = self.blob[off]
            off += 1
            value |= (byte & 0x7f) << shift
            shift += 7
            if not byte & 0x80:
                return value, off

    def sint(self, off):
        first = self.blob[off]
        off += 1
        value = first & 0x3f
        shift = 6
        more = first & 0x80
        while more:
            byte = self.blob[off]
            off += 1
            value |= (byte & 0x7f) << shift
            shift += 7
            more = byte & 0x80
        return (-value if first & 0x40 else value), off

    def cfloat(self, off, kind):
        """-[TUIKBGraphSerialization compressedFloatForOffset:withKind:]: 0 zero, 1 int, 2 int/6, 3 raw float32 (float32 math)."""
        if kind == 3:
            return struct.unpack_from('<f', self.blob, off)[0], off + 4
        if kind in (1, 2):
            v, off = self.sint(off)
            v = f32(v if v >= 0 else v + (1 << 64))          # ucvtf s0, x0
            return (f32(v / 6.0) if kind == 2 else v), off
        return 0.0, off

    def rect(self, off):
        kinds = self.blob[off]
        off += 1
        out = []
        for i in range(4):
            v, off = self.cfloat(off, (kinds >> (2 * i)) & 3)
            out.append(v)
        return tuple(out), off

    def value(self, off):
        byte = self.blob[off]
        v, off = self.cfloat(off + 1, byte >> 4)
        return (v, 100 if byte & 0xf == 0xf else byte & 0xf), off

    def obj(self, off):
        hit = self.parsed.get(off)
        if hit is None:
            hit = self.parsed[off] = self._parse(off)
            if not isinstance(hit[0], (int, float, str)):
                self.offset_of.setdefault(id(hit[0]), off)
        return hit

    def at(self, off):
        self.targets.add(off)
        return self.obj(off)[0]

    def _parse(self, start):
        off = start
        tag = self.blob[off]
        off += 1
        t, n = tag & 0xf, tag >> 4
        if n == 0xf:
            n, off = self.uint(off)
        if t == 0:                                            # reference to the object at an absolute offset
            target, off = self.uint(off)
            return self.at(target), off
        if t == 1:
            return self.uint(off)
        if t == 2:
            return struct.unpack_from('<f', self.blob, off)[0], off + 4
        if t == 3:
            return self.blob[off:off + n].decode('utf-8'), off + n
        if t == 4:                                            # concatenation of n referenced strings, separator @""
            parts = []
            for _ in range(n):
                target, off = self.uint(off)
                parts.append(self.at(target))
            return ''.join(parts), off
        if t == 5:
            items = []
            for _ in range(n):
                item, off = self.obj(off)
                items.append(item)
            return items, off
        if t == 6:
            if not n:
                return frozenset(), off
            items, off = self.obj(off)
            return frozenset(hashable(i) for i in items), off
        if t == 7:
            if not n:
                return {}, off
            keys, off = self.obj(off)
            values, off = self.obj(off)
            return dict(zip((hashable(k) for k in keys), values)), off
        if t == 8:
            g = Geometry()
            g.flags = n
            g.name, off = self.obj(off)
            values = []
            for _ in range(8):
                v, off = self.value(off)
                values.append(v)
            g.values = tuple(values)
            return g, off
        if t == 9:
            s = Shape()
            s.flags, s.geometry = n, None
            if n & 1:
                s.geometry, off = self.obj(off)
            s.frame, off = self.rect(off)
            s.padded, off = self.rect(off)
            return s, off
        if t == 10:
            m = Merge()
            m.flags, m.ordered, m.factors = n, None, None
            m.remaining, off = self.obj(off)
            m.disappearing, off = self.obj(off)
            if n & 1:
                m.ordered, off = self.obj(off)
            if n & 2:
                m.factors, off = self.obj(off)
            return m, off
        if t == 11:
            tr = Tree()
            tr.flags = n
            if self.blob[off] != 0x01:
                raise ValueError(f'tree at {start:#x}: type is not an int')
            tr.type, off = self.uint(off + 1)
            tr.name = tr.subtrees = tr.cache = None
            tr.props = {}
            if n & 1:
                tr.name, off = self.obj(off)
            if n & 2:
                tr.props, off = self.obj(off)
            if n & 4:
                tr.subtrees, off = self.obj(off)
            if n & 8:
                tr.cache, off = self.obj(off)
            return tr, off
        if t == 12:                                           # array of references
            items = []
            for _ in range(n):
                target, off = self.uint(off)
                items.append(self.at(target))
            return items, off
        if t == 13:                                           # array of references, first absolute, then signed deltas
            items, target = [], 0
            for i in range(n):
                if i:
                    delta, off = self.sint(off)
                    target += delta
                else:
                    target, off = self.uint(off)
                items.append(self.at(target))
            return items, off
        raise ValueError(f'unknown object type {t} at {start:#x}')


def hashable(v):
    return tuple(hashable(i) for i in v) if isinstance(v, list) else v


class Layouts:
    """A KBLayouts file (-[TUIKBGraphSerialization setSerializedData:], 0x19192e2f8): the portrait and the landscape
    width->prefix tables, a compressed-uint length, a binary plist name->offset, then the object graph."""

    def __init__(self, data):
        self.data = bytes(data)
        head = Graph(self.data)
        self.portrait, self.portrait_end = head.obj(0)
        self.landscape, self.landscape_end = head.obj(self.portrait_end)
        if not isinstance(self.portrait, dict) or not isinstance(self.landscape, dict):
            raise ValueError('no width tables')
        self.plist_len, self.plist_off = head.uint(self.landscape_end)
        self.graph_off = self.plist_off + self.plist_len
        self.offsets = plistlib.loads(self.data[self.plist_off:self.graph_off])
        if not isinstance(self.offsets, dict):
            raise ValueError('the name table is not a dictionary')
        self.graph = Graph(self.data[self.graph_off:])

    def keyboard(self, name):
        off = self.offsets.get(name)
        return None if off is None else self.graph.at(off)

    @staticmethod
    def prefix_for(table, width):
        """keyboardPrefixForWidth:andEdge: as called by _UIKeyboardGetKBStarName: frinta, then float compares."""
        w = f32(frinta(width))
        for key in sorted(table, key=f32, reverse=True):
            if f32(key) <= w:
                return table[key]
        return None


# ---------------------------------------------------------------------------------------------------------------------------------
# Writer: the encodings of -[TUIKBGraphSerialization dataFor...:] (TextInputUI 0x19198542c..0x191986a30)
# ---------------------------------------------------------------------------------------------------------------------------------
def enc_uint(v):
    if v < 0:
        raise ValueError('negative compressed uint')
    out = bytearray()
    while True:
        byte = v & 0x7f
        v >>= 7
        out.append(byte | (0x80 if v else 0))
        if not v:
            return bytes(out)


def enc_sint(v):
    a = abs(v)
    first = (a & 0x3f) | (0x40 if v < 0 else 0) | (0x80 if a > 0x3f else 0)
    out = bytearray([first])
    a >>= 6
    while a:
        out.append((a & 0x7f) | (0x80 if a > 0x7f else 0))
        a >>= 7
    return bytes(out)


def enc_cfloat(v):
    """-[TUIKBGraphSerialization dataForCompressedFloat:kind:] (0x19198560c), in float32 like the original: (kind, bytes)."""
    s = f32(v)
    if s == 0.0:
        return 0, b''
    if s > 0:
        if abs(f32(frinta(s)) - s) < 0.001:
            return 1, enc_sint(int(frinta(s)))
        s6 = f32(s * 6.0)
        if abs(f32(frinta(s6) - s6)) < 0.001:
            return 2, enc_sint(int(frinta(s6)))
    return 3, struct.pack('<f', s)


def enc_tag(t, n):
    """mutableDataForObjectType:withSize: (0x19198542c): size <= 14 in the high nibble, else 0xF and a compressed uint."""
    return bytes([t | n << 4]) if n <= 14 else bytes([t | 0xf0]) + enc_uint(n)


def enc_rect(r):
    kinds, body = 0, b''
    for i, v in enumerate(r):
        k, b = enc_cfloat(v)
        kinds |= k << (2 * i)
        body += b
    return bytes([kinds]) + body


def enc_value(v):
    value, unit = v
    if unit != 100 and not 0 <= unit < 15:
        raise ValueError(f'unit {unit} cannot be encoded')
    k, b = enc_cfloat(value)
    return bytes([k << 4 | (0xf if unit == 100 else unit)]) + b


class Writer:
    """Appends objects after an existing graph, keeping Apple's rule (see Graph): an object that is referenced is standalone.

    * An unchanged object that is standalone in the original (a reference target or a keyboard root) is referenced at its
      original offset. An unchanged object that the original holds inline is written again, inline.
    * A rewritten object (it is or holds a Shape, or it is a renamed keyboard) is written once: standalone when the original
      had it standalone or it is a root, and then referenced wherever it occurs; otherwise inline in its one parent.
    Standalone objects are written before the first object that refers to them, so every reference points backwards."""

    def __init__(self, graph, base, changed, rewrite, roots=()):
        self.graph = graph
        self.base = base
        self.out = bytearray()
        self.done = {}                  # id(rewritten standalone object) -> its new offset
        self.changed = changed          # id(object) -> True when the object or something below it is rewritten
        self.rewrite = rewrite          # function(object) -> replacement fields (Shape frames, Tree names) or None
        self.roots = {id(r) for r in roots}

    def here(self):
        return self.base + len(self.out)

    def standalone(self, o):
        key = id(o)
        return key in self.roots or self.graph.offset_of.get(key, -1) in self.graph.targets

    def write_root(self, o):
        """Writes o standalone (with what it needs first) unless it is already there; returns its offset."""
        key = id(o)
        if not self.changed.get(key):
            return self.graph.offset_of[key]
        if key not in self.done:
            self._prepare(o)
            self.done[key] = self.here()
            self._encode(o)
        return self.done[key]

    def _prepare(self, o):
        """Writes the standalone rewritten objects that o's inline encoding will refer to."""
        stack = [c for c in reversed(self._fields(o))]
        while stack:
            c = stack.pop()
            if c is None or isinstance(c, (int, float, str)):
                continue
            if self.standalone(c):
                if self.changed.get(id(c)):
                    self.write_root(c)
            else:
                stack.extend(reversed(self._fields(c)))

    def _child(self, c):
        if isinstance(c, bool):
            raise ValueError('booleans are not part of this format')
        if isinstance(c, int):
            self.out += b'\x01' + enc_uint(c)
        elif isinstance(c, float):
            self.out += b'\x02' + struct.pack('<f', c)
        elif isinstance(c, str):
            data = c.encode('utf-8')
            self.out += enc_tag(3, len(data)) + data
        elif self.standalone(c):
            key = id(c)
            target = self.done[key] if self.changed.get(key) else self.graph.offset_of[key]
            self.out += b'\x00' + enc_uint(target)
        else:
            self._encode(c)

    def _fields(self, o):
        if isinstance(o, (list, tuple)):
            return list(o)
        if isinstance(o, frozenset):
            return sorted(o, key=repr)
        if isinstance(o, dict):
            return list(o.keys()) + list(o.values())
        if isinstance(o, Tree):
            name = self.rewrite(o) or o.name
            return [f for bit, f in ((1, name), (2, o.props), (4, o.subtrees), (8, o.cache)) if o.flags & bit]
        if isinstance(o, Shape):
            return [o.geometry] if o.flags & 1 else []
        if isinstance(o, Geometry):
            return [o.name]
        if isinstance(o, Merge):
            return [o.remaining, o.disappearing] + ([o.ordered] if o.flags & 1 else []) + ([o.factors] if o.flags & 2 else [])
        raise ValueError(f'cannot write {type(o).__name__}')

    def _encode(self, o):
        if isinstance(o, (list, tuple)):
            self.out += enc_tag(5, len(o))
            for item in o:
                self._child(item)
        elif isinstance(o, frozenset):
            self.out += enc_tag(6, 1 if o else 0)
            if o:
                items = sorted(o, key=repr)
                self.out += enc_tag(5, len(items))
                for item in items:
                    self._child(item)
        elif isinstance(o, dict):
            self.out += enc_tag(7, 1 if o else 0)
            if o:
                for part in (list(o.keys()), list(o.values())):
                    self.out += enc_tag(5, len(part))
                    for item in part:
                        self._child(item)
        elif isinstance(o, Tree):
            self.out += enc_tag(11, o.flags) + b'\x01' + enc_uint(o.type)
            for f in self._fields(o):
                self._child(f)
        elif isinstance(o, Shape):
            frame, padded = self.rewrite(o) or (o.frame, o.padded)
            self.out += enc_tag(9, o.flags)
            for f in self._fields(o):
                self._child(f)
            self.out += enc_rect(frame) + enc_rect(padded)
        elif isinstance(o, Geometry):
            self.out += enc_tag(8, o.flags)
            self._child(o.name)
            for v in o.values:
                self.out += enc_value(v)
        elif isinstance(o, Merge):
            self.out += enc_tag(10, o.flags)
            for f in self._fields(o):
                self._child(f)
        else:
            raise ValueError(f'cannot write {type(o).__name__}')

    def inline(self, o):
        """Encodes o in place (the head's width tables)."""
        self._encode(o)


def children(o):
    if isinstance(o, Tree):
        return [o.name, o.props, o.subtrees, o.cache]
    if isinstance(o, Shape):
        return [o.geometry]
    if isinstance(o, Geometry):
        return [o.name]
    if isinstance(o, Merge):
        return [o.remaining, o.disappearing, o.ordered, o.factors]
    if isinstance(o, dict):
        return list(o.keys()) + list(o.values())
    if isinstance(o, (list, tuple, frozenset)):
        return list(o)
    return []


def mark_changed(roots):
    """id -> True for every object below the roots that is a Shape or holds one (iteratively: the graphs are deep)."""
    changed, state, stack = {}, {}, [(r, False) for r in roots]
    while stack:
        o, leaving = stack.pop()
        if o is None or isinstance(o, (int, float, str)):
            continue
        key = id(o)
        if leaving:
            changed[key] = isinstance(o, Shape) or any(
                changed.get(id(c), False) for c in children(o) if c is not None and not isinstance(c, (int, float, str)))
            state[key] = 2
            continue
        if state.get(key):
            continue
        state[key] = 1
        stack.append((o, True))
        stack.extend((c, False) for c in children(o))
    return changed


# ---------------------------------------------------------------------------------------------------------------------------------
# The scaling UIKit applies when it stretches a keyplane: -[UIKBShapeOperator _scaleRange:factor:] (0x18a3e79bc)
# ---------------------------------------------------------------------------------------------------------------------------------
def round_to_scale(v, scale):
    return frinta(v) if scale == 1.0 else frinta(v * scale) / scale


def scale_range(origin, length, factor, scale, quirk=True):
    """-[UIKBShapeOperator _scaleRange:factor:]: origin and length rounded to the pixel grid, the rounding error of both added
    back to the length so the far edge stays where the exact product puts it. UIKit returns the bare factor for an origin or
    length of exactly 1.0 (quirk=True, kept to model UIKit); the data is written with quirk=False, all on the pixel grid, so that
    UIKit's own stretch at run time (by 405.3333/405.33334, see build) leaves it where it is."""
    new_origin = factor if quirk and origin == 1.0 else round_to_scale(origin * factor, scale)
    if quirk and length == 1.0:
        scaled = rounded = factor
    else:
        scaled = length * factor
        rounded = round_to_scale(scaled, scale)
    error = origin * factor - new_origin + scaled - rounded
    return new_origin, rounded + (1.0 if quirk and error == 1.0 else round_to_scale(error, scale))


def scale_rect(r, factor, scale, quirk=False):
    """-[UIKBShapeOperator _scaleRect:factor:] for a horizontal factor: x and width scaled, y and height kept."""
    if r == (0.0, 0.0, 0.0, 0.0):
        return r
    x, w = scale_range(r[0], r[2], factor, scale, quirk)
    return (x, r[1], w, r[3])


# ---------------------------------------------------------------------------------------------------------------------------------
def build(data, base, width_px, scale):
    src = Layouts(data)
    family = f'iPhone-Portrait{base}'
    names = sorted(n for n in src.offsets if n.startswith(family + '-'))
    if not names:
        raise SystemExit(f'{family}-* is not in the input')
    width = width_px / scale                                   # 405.333 points: UIScreen bounds of a 1216-px panel at @3x
    key = int(frinta(width))                                   # what _UIKeyboardGetKBStarName looks up (frinta, 0x18a45fa84)
    new_family = f'{family}{key}'
    design = {src.keyboard(n).props['KBshape'].frame[2] for n in names}
    if len(design) != 1:
        raise SystemExit(f'{family} keyboards are not all one width: {sorted(design)}')
    design = design.pop()
    factor = width / design
    if key in src.portrait or new_family in src.portrait.values():
        raise SystemExit(f'the portrait table already has {key} or {new_family}')
    if not (max((k for k in src.portrait if k < key), default=-1) <
            key < min((k for k in src.portrait if k > key), default=1 << 30)):
        raise SystemExit('the new key does not fit between the table entries')

    roots = [src.keyboard(n) for n in names]
    changed = mark_changed(roots)
    renamed = {}
    for n in names:
        tree = src.keyboard(n)
        if isinstance(tree.name, str) and tree.name.startswith(family + '-'):
            renamed[id(tree)] = new_family + tree.name[len(family):]
        changed[id(tree)] = True

    def rewrite(o):
        if isinstance(o, Tree):
            return renamed.get(id(o))
        if isinstance(o, Shape):
            return scale_rect(o.frame, factor, scale), scale_rect(o.padded, factor, scale)
        return None

    graph_bytes = data[src.graph_off:]
    writer = Writer(src.graph, len(graph_bytes), changed, rewrite, roots)
    new_offsets = {new_family + n[len(family):]: writer.write_root(tree) for n, tree in zip(names, roots)}

    offsets = dict(src.offsets)
    for n, off in new_offsets.items():
        if n in offsets:
            raise SystemExit(f'{n} exists already')
        offsets[n] = off
    plist = plistlib.dumps(offsets, fmt=plistlib.FMT_BINARY)
    portrait = dict(src.portrait)
    portrait[key] = new_family
    head = Writer(Graph(b''), 0, {}, lambda o: None)
    head.inline(portrait)
    out = (bytes(head.out) + data[src.portrait_end:src.landscape_end] + enc_uint(len(plist)) + plist + graph_bytes
           + bytes(writer.out))
    info = dict(base=family, family=new_family, table_key=key, width_points=width, design_width=design, factor=factor,
                scale=scale, keyboards=len(names), keyboard_objects=len({id(r) for r in roots}),
                appended_bytes=len(writer.out), input_bytes=len(data), output_bytes=len(out))
    return out, info


# ---------------------------------------------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------------------------------------------
def canon(o, memo, shape_map=None, rename=None):
    """A structural digest of an object, memoised by identity. shape_map(shape) -> (frame, padded) replaces shapes and rename
    (id of a tree -> name) replaces tree names, so an original mapped this way digests like its rewritten copy."""
    key = id(o)
    if key in memo:
        return memo[key]
    rec = lambda c: canon(c, memo, shape_map, rename)
    if isinstance(o, Tree):
        name = rename.get(key, o.name) if rename else o.name
        parts = ('T', o.type, o.flags, rec(name), rec(o.props), rec(o.subtrees), rec(o.cache))
    elif isinstance(o, Shape):
        frame, padded = shape_map(o) if shape_map else (o.frame, o.padded)
        parts = ('S', o.flags, rec(o.geometry), tuple(map(f32, frame)), tuple(map(f32, padded)))
    elif isinstance(o, Geometry):
        parts = ('G', o.flags, rec(o.name), tuple((f32(v), u) for v, u in o.values))
    elif isinstance(o, Merge):
        parts = ('M', o.flags) + tuple(rec(c) for c in children(o))
    elif isinstance(o, dict):
        parts = ('D',) + tuple(sorted((repr(k), rec(v)) for k, v in o.items()))
    elif isinstance(o, (list, tuple)):
        parts = ('L',) + tuple(rec(v) for v in o)
    elif isinstance(o, frozenset):
        parts = ('F',) + tuple(sorted(repr(v) for v in o))
    elif isinstance(o, float):
        parts = ('N', f32(o))
    else:
        parts = ('V', type(o).__name__, repr(o))
    digest = hashlib.sha1(repr(parts).encode()).hexdigest()
    if o is not None and not isinstance(o, (int, float, str)):
        memo[key] = digest
    return digest


def standalone_violations(lay, names):
    """Offsets that are both referenced (or a root) and read inline inside another object, walking from the given names.
    Apple's files have none; the decoder's cache hit without advancing (Graph) would misread such a file."""
    blob, g = lay.graph.blob, lay.graph
    inline, standalone, seen = set(), set(), set()
    work = [(lay.offsets[n], False) for n in names]
    while work:
        off, is_inline = work.pop()
        (inline if is_inline else standalone).add(off)
        if off in seen:
            continue
        seen.add(off)
        tag = blob[off]
        t, n = tag & 0xf, tag >> 4
        p = off + 1
        if n == 0xf:
            n, p = g.uint(p)
        if t == 0:
            target, p = g.uint(p)
            work.append((target, False))
            continue
        if t in (4, 12, 13):
            target = 0
            for i in range(n):
                if t == 13 and i:
                    delta, p = g.sint(p)
                    target += delta
                else:
                    target, p = g.uint(p)
                work.append((target, False))
            continue
        fields = 0
        if t == 5:
            fields = n
        elif t == 6:
            fields = 1 if n else 0
        elif t == 7:
            fields = 2 if n else 0
        elif t == 8:
            fields = 1
        elif t == 9:
            fields = n & 1
        elif t == 10:
            fields = 2 + (n & 1) + (n >> 1 & 1)
        elif t == 11:
            _, p = g.uint(p + 1)
            fields = bin(n & 0xf).count('1')
        for _ in range(fields):
            if blob[p] & 0xf == 0 and blob[p] >> 4 != 0xf:     # a reference: its own bytes are inline, its target is not
                inline.add(p)
                target, q = g.uint(p + 1)
                work.append((target, False))
                p = q
            else:
                work.append((p, True))
                p = g.obj(p)[1]
    return sorted(inline & standalone)


def verify(data, out, info):
    """Parse OUTPUT back and check it against INPUT; returns the facts the report prints. Raises on any mismatch."""
    src, dst = Layouts(data), Layouts(out)
    family, new_family, key = info['base'], info['family'], info['table_key']
    problems = []
    expected = dict(src.portrait)
    expected[key] = new_family
    if dst.portrait != expected:
        problems.append('portrait table')
    if dst.landscape != src.landscape or out[dst.portrait_end:dst.landscape_end] != data[src.portrait_end:src.landscape_end]:
        problems.append('landscape table bytes')
    graph_len = len(data) - src.graph_off
    if out[dst.graph_off:dst.graph_off + graph_len] != data[src.graph_off:]:
        problems.append('original graph bytes')
    for n, off in src.offsets.items():
        if dst.offsets.get(n) != off:
            problems.append(f'offset of {n}')
            break
    if set(dst.offsets) - set(src.offsets) != {new_family + n[len(family):] for n in src.offsets if n.startswith(family + '-')}:
        problems.append('new names')
    width = info['width_points']
    factor, scale = info['factor'], info['scale']
    names = sorted(n for n in src.offsets if n.startswith(family + '-'))
    shape_map = lambda s: (scale_rect(s.frame, factor, scale), scale_rect(s.padded, factor, scale))
    # the copy is the original with its shapes scaled and each keyboard tree's own name moved to the new family
    rename = {}
    for n in names:
        tree = src.keyboard(n)
        if isinstance(tree.name, str) and tree.name.startswith(family + '-'):
            rename[id(tree)] = new_family + tree.name[len(family):]
    memo_mapped, memo_src, memo_dst = {}, {}, {}
    widths = set()
    for n in names:
        a, b = src.keyboard(n), dst.keyboard(new_family + n[len(family):])
        if b is None:
            problems.append(f'missing {new_family + n[len(family):]}')
            break
        if canon(a, memo_mapped, shape_map, rename) != canon(b, memo_dst):
            problems.append(f'structure of {n}')
            break
        if canon(dst.keyboard(n), memo_dst) != canon(a, memo_src):
            problems.append(f'original {n} changed')
            break
        widths.add(f32(b.props['KBshape'].frame[2]))
    for n in sorted(set(src.offsets) - set(names))[::7]:        # a sample of the other families: still the same objects
        if canon(dst.keyboard(n), memo_dst) != canon(src.keyboard(n), memo_src):
            problems.append(f'original {n} changed')
            break
    if widths != {f32(width)}:
        problems.append(f'keyboard widths {sorted(widths)}')
    # every shape of the new family inside [0, width]
    inside = True
    for n in names:
        stack, seen = [dst.keyboard(new_family + n[len(family):])], set()
        while stack:
            o = stack.pop()
            if o is None or isinstance(o, (int, float, str)) or id(o) in seen:
                continue
            seen.add(id(o))
            if isinstance(o, Shape):
                for x, _, w, _ in (o.frame, o.padded):
                    if x < -0.001 or x + w > f32(width) + 0.01:
                        inside = False
            stack.extend(children(o))
    if not inside:
        problems.append('a shape outside the keyboard')
    clash = standalone_violations(dst, sorted(dst.offsets))
    if clash:
        problems.append(f'{len(clash)} referenced offsets are also read inline (first {clash[:3]})')
    selection = {str(w): Layouts.prefix_for(dst.portrait, w) for w in (320, 375, 390, 393, 400, width, 410, 414, 428, 430, 768)}
    if selection[str(width)] != new_family or selection['414'] != src.portrait.get(414) or selection['375'] != src.portrait.get(375):
        problems.append('selection')
    if problems:
        raise SystemExit('verification failed: ' + ', '.join(problems))
    return dict(selection=selection, landscape_880=Layouts.prefix_for(dst.landscape, 880), new_keyboards=len(names))


def check(path, width):
    lay = Layouts(Path(path).read_bytes())
    fams = {}
    for n in lay.offsets:
        fam = '-'.join(n.split('-')[:2])
        fams[fam] = fams.get(fam, 0) + 1
    print(json.dumps(dict(portrait={str(k): v for k, v in sorted(lay.portrait.items())},
                          landscape={str(k): v for k, v in sorted(lay.landscape.items())},
                          names=len(lay.offsets), families=dict(sorted(fams.items())),
                          portrait_for_width={str(width): Layouts.prefix_for(lay.portrait, width)},
                          landscape_for_height=Layouts.prefix_for(lay.landscape, 880)), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', choices=('Choco', 'Truffle'), default='Choco',
                        help='family to redraw (Choco, 375 pt, is what iOS picks below 414: 390/393-point iPhones use it)')
    parser.add_argument('--width-px', type=int, default=1216)
    parser.add_argument('--scale', type=float, default=3.0)
    parser.add_argument('--check', metavar='FILE')
    parser.add_argument('--width', type=float, default=1216 / 3)
    parser.add_argument('input', nargs='?', type=Path)
    parser.add_argument('output', nargs='?', type=Path)
    args = parser.parse_args()
    if args.check:
        check(args.check, args.width)
        return
    if not args.input or not args.output:
        parser.error('INPUT and OUTPUT are required')
    if args.output.exists() and args.output.resolve() == args.input.resolve():
        sys.exit('refuse to overwrite the input')
    data = args.input.read_bytes()
    out, info = build(data, args.base, args.width_px, args.scale)
    info.update(verify(data, out, info))
    info.update(input=str(args.input), input_sha256=hashlib.sha256(data).hexdigest(), output=str(args.output),
                output_sha256=hashlib.sha256(out).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_name(args.output.name + '.tmp')
    tmp.write_bytes(out)
    tmp.replace(args.output)
    args.output.with_name(args.output.name + '.json').write_text(json.dumps(info, indent=2) + '\n')
    print(json.dumps(info, indent=2))


if __name__ == '__main__':
    main()
