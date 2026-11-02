#!/usr/bin/env python3
"""Host tests of 工具/make-phone-keyboard-layouts.py (python3 -B 工具/test-phone-keyboard-layouts.py -v).

Inputs: build-userspace/iphone-17.7-21H16/KBLayouts_iPhone.dat (21H16, the file the iPhone overlay carries today) and, when
KBLAYOUTS_IPAD names it, KBLayouts_iPad.dat of the 21H461 OS root. Tests that need a missing input are skipped."""
import importlib.util
import os
import random
import struct
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('kbmake', HERE / 'make-phone-keyboard-layouts.py')
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)

IPHONE = Path(os.environ.get('KBLAYOUTS_IPHONE', HERE.parent / 'build-userspace/iphone-17.7-21H16/KBLayouts_iPhone.dat'))
IPAD = Path(os.environ['KBLAYOUTS_IPAD']) if os.environ.get('KBLAYOUTS_IPAD') else None
WIDTH = 1216 / 3


def shapes_of(tree):
    out, stack, seen = [], [tree], set()
    while stack:
        o = stack.pop()
        if o is None or isinstance(o, (int, float, str)) or id(o) in seen:
            continue
        seen.add(id(o))
        if isinstance(o, mk.Shape):
            out.append(o)
        stack.extend(mk.children(o))
    return out


class Encoding(unittest.TestCase):
    """The writer's encodings read back through the reader (the decoder's logic) unchanged."""

    def test_compressed_ints(self):
        values = [0, 1, 63, 64, 127, 128, 8191, 8192, 2 ** 20, 2 ** 31 - 1, 2 ** 40]
        for v in values:
            blob = mk.enc_uint(v)
            self.assertEqual(mk.Graph(blob).uint(0), (v, len(blob)))
        for v in values + [-v for v in values]:
            blob = mk.enc_sint(v)
            self.assertEqual(mk.Graph(blob).sint(0), (v, len(blob)))

    def test_compressed_floats(self):
        rnd = random.Random(1)
        values = [0.0, 1.0, 0.5, 1 / 3, 2 / 3, 37.5, 40.666666, 405.3333333, 216.0, -1.0, -7.25, 1e-4, 1234.56]
        values += [rnd.randrange(0, 1300 * 6) / 6 for _ in range(500)] + [rnd.uniform(-50, 1300) for _ in range(500)]
        for v in values:
            kind, body = mk.enc_cfloat(v)
            got, end = mk.Graph(body).cfloat(0, kind)
            self.assertEqual(end, len(body))
            # Apple's writer stores a value within 0.001 of an integer (or of a sixth) as that integer (sixth)
            expected = {0: 0.0, 1: mk.frinta(mk.f32(v)), 2: mk.f32(mk.frinta(mk.f32(v) * 6) / 6), 3: mk.f32(v)}[kind]
            self.assertAlmostEqual(got, expected, delta=abs(expected) * 1e-6 + 1e-9, msg=f'{v} kind {kind}')
            self.assertAlmostEqual(got, mk.f32(v), delta=0.001 + abs(v) * 1e-6)
        self.assertEqual(mk.enc_cfloat(405.3333333)[0], 2)           # a 1/6 multiple: the int form, like Apple's writer
        self.assertEqual(mk.enc_cfloat(216.0)[0], 1)
        self.assertEqual(mk.enc_cfloat(0.0), (0, b''))

    def test_rects_and_values(self):
        for r in [(0.0, 7.0, 40.666666666666664, 46.0), (1.0808888673782349, 7.0, 38.333333333333336, 46.0), (0, 0, 0, 0)]:
            blob = mk.enc_rect(r)
            got, end = mk.Graph(blob).rect(0)
            self.assertEqual(end, len(blob))
            self.assertEqual(tuple(map(mk.f32, got)), tuple(map(mk.f32, r)))
        for v in [(100.0, 2), (0.0, 0), (0.0, 100), (12.5, 1)]:
            blob = mk.enc_value(v)
            got, end = mk.Graph(blob).value(0)
            self.assertEqual((got, end), (v, len(blob)))


@unittest.skipUnless(IPHONE.is_file(), f'no {IPHONE}')
class Generated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = IPHONE.read_bytes()
        cls.out, cls.info = mk.build(cls.data, 'Choco', 1216, 3.0)
        cls.facts = mk.verify(cls.data, cls.out, cls.info)        # raises on any mismatch
        cls.src, cls.dst = mk.Layouts(cls.data), mk.Layouts(cls.out)

    def test_head_tables_reencode_byte_exact(self):
        """The writer writes the official width tables byte for byte as Apple's writer did."""
        for table, start, end in ((self.src.portrait, 0, self.src.portrait_end),
                                  (self.src.landscape, self.src.portrait_end, self.src.landscape_end)):
            w = mk.Writer(mk.Graph(b''), 0, {}, lambda o: None)
            w.inline(table)
            self.assertEqual(bytes(w.out), self.data[start:end])

    def test_width_table(self):
        self.assertEqual(self.info['family'], 'iPhone-PortraitChoco405')
        expected = dict(self.src.portrait)
        expected[405] = 'iPhone-PortraitChoco405'
        self.assertEqual(self.dst.portrait, expected)
        self.assertEqual(self.dst.landscape, self.src.landscape)

    def test_everything_else_byte_identical(self):
        n = len(self.data) - self.src.graph_off
        self.assertEqual(self.out[self.dst.graph_off:self.dst.graph_off + n], self.data[self.src.graph_off:])
        self.assertEqual(self.out[self.dst.portrait_end:self.dst.landscape_end],
                         self.data[self.src.portrait_end:self.src.landscape_end])
        for name, off in self.src.offsets.items():
            self.assertEqual(self.dst.offsets[name], off, name)
        self.assertEqual(len(self.dst.offsets), len(self.src.offsets) + 680)

    def test_selection_at_405_333(self):
        """_UIKeyboardGetKBStarName: frinta(keyboard width) -> keyboardPrefixForWidth:andEdge: (portrait table)."""
        pick = lambda table, w: mk.Layouts.prefix_for(table, w)
        self.assertEqual(pick(self.src.portrait, WIDTH), 'iPhone-PortraitChoco')          # before: the 375-point family
        self.assertEqual(pick(self.dst.portrait, WIDTH), 'iPhone-PortraitChoco405')
        # only widths 404.5..413.x change family (frinta), so every real iPhone and the iPad identity (608) keep theirs
        for w in [x / 3 for x in range(900, 4200)]:
            before, after = pick(self.src.portrait, w), pick(self.dst.portrait, w)
            if 405 <= mk.frinta(w) < 414:
                self.assertEqual(after, 'iPhone-PortraitChoco405', w)
            else:
                self.assertEqual(after, before, w)
        for h in (667, 812, 844, 880, 896, 1024, 1320, 1366):
            self.assertEqual(pick(self.dst.landscape, h), pick(self.src.landscape, h))

    def test_new_family_is_405_wide(self):
        for name in ('QWERTY', 'Pinyin-Simplified', 'Pinyin10-Simplified', 'NumberPad', 'Emoji', 'QWERTY-Email', 'DecimalPad'):
            kb = self.dst.keyboard('iPhone-PortraitChoco405-' + name)
            old = self.src.keyboard('iPhone-PortraitChoco-' + name)
            self.assertEqual(kb.name, 'iPhone-PortraitChoco405-' + name)
            self.assertEqual(mk.f32(kb.props['KBshape'].frame[2]), mk.f32(WIDTH))
            self.assertEqual(kb.props['KBshape'].frame[3], old.props['KBshape'].frame[3])       # heights unchanged
            for s in shapes_of(kb):
                for x, y, w, h in (s.frame, s.padded):
                    self.assertGreaterEqual(x, -1e-6)
                    self.assertLessEqual(x + w, WIDTH + 0.01)

    def test_letter_rows_tile_the_width(self):
        kb = self.dst.keyboard('iPhone-PortraitChoco405-QWERTY')
        small = next(p for p in kb.subtrees if 'Small-Letters' in p.name)
        letters = small.subtrees[0]
        geometry_set = next(t for t in letters.subtrees if isinstance(t, mk.Tree) and t.type == 5)
        top = geometry_set.subtrees[0]                         # Ten-on-Ten: Q..P
        frames = [s.frame for s in top.subtrees]
        self.assertEqual(len(frames), 10)
        self.assertEqual(frames[0][0], 0.0)
        for a, b in zip(frames, frames[1:]):
            self.assertAlmostEqual(a[0] + a[2], b[0], places=4)
        self.assertAlmostEqual(frames[-1][0] + frames[-1][2], WIDTH, places=4)
        for x, _, w, _ in frames:                              # on the 1216-px grid
            self.assertAlmostEqual(x * 3, round(x * 3), places=3)
            self.assertAlmostEqual(w * 3, round(w * 3), places=3)

    def test_uikit_runtime_stretch_is_a_no_op(self):
        """With the new family UIKit still stretches by screen width / keyboard width (float32): 405.3333/405.33334. Its
        -[UIKBShapeOperator _scaleRange:factor:] at scale 3 must leave every key where the file puts it."""
        factor = WIDTH / mk.f32(WIDTH)
        for name in ('QWERTY', 'Pinyin-Simplified', 'Pinyin10-Simplified', 'NumberPad', 'Emoji'):
            for s in shapes_of(self.dst.keyboard('iPhone-PortraitChoco405-' + name)):
                for r in (s.frame, s.padded):
                    x, _, w, _ = mk.scale_rect(r, factor, 3.0, quirk=True)        # UIKit's own arithmetic, quirk included
                    self.assertAlmostEqual(x, r[0], delta=1e-4)
                    self.assertAlmostEqual(w, r[2], delta=1e-4)
                    self.assertAlmostEqual(r[0] * 3, round(r[0] * 3), places=3)     # every edge on the 1216-px grid
                    self.assertAlmostEqual(r[2] * 3, round(r[2] * 3), places=3)

    def test_copy_matches_uikit_scaling(self):
        """Each copied shape is what UIKit's own stretch of the original makes (scale 3, factor 405.333/375)."""
        factor = WIDTH / 375.0
        a = shapes_of(self.src.keyboard('iPhone-PortraitChoco-QWERTY'))
        b = shapes_of(self.dst.keyboard('iPhone-PortraitChoco405-QWERTY'))
        self.assertEqual(len(a), len(b))
        self.assertEqual(sorted(tuple(map(mk.f32, mk.scale_rect(s.frame, factor, 3.0))) for s in a),
                         sorted(tuple(map(mk.f32, s.frame)) for s in b))
        # and within one pixel of what UIKit itself would draw when it stretched the 375-point family (quirk included)
        for s in a:
            for r in (s.frame, s.padded):
                ours, uikit = mk.scale_rect(r, factor, 3.0), mk.scale_rect(r, factor, 3.0, quirk=True)
                self.assertLessEqual(abs(ours[0] - uikit[0]), 1 / 3 + 1e-9)
                self.assertLessEqual(abs(ours[0] + ours[2] - uikit[0] - uikit[2]), 1 / 3 + 1e-9)

    def test_shared_objects_stay_standalone(self):
        """The decoder's cache returns without advancing (objectForOffset:), so referenced offsets must never be read inline."""
        self.assertEqual(mk.standalone_violations(self.dst, sorted(self.dst.offsets)), [])
        self.assertEqual(mk.standalone_violations(self.src, sorted(self.src.offsets)), [])

    def test_deterministic(self):
        out2, _ = mk.build(self.data, 'Choco', 1216, 3.0)
        self.assertEqual(out2, self.out)


@unittest.skipUnless(IPHONE.is_file(), f'no {IPHONE}')
class OtherBase(unittest.TestCase):
    def test_truffle(self):
        data = IPHONE.read_bytes()
        out, info = mk.build(data, 'Truffle', 1216, 3.0)
        mk.verify(data, out, info)
        self.assertEqual(mk.Layouts.prefix_for(mk.Layouts(out).portrait, WIDTH), 'iPhone-PortraitTruffle405')


@unittest.skipUnless(IPAD and IPAD.is_file(), 'set KBLAYOUTS_IPAD to the 21H461 KBLayouts_iPad.dat')
class IPadSource(unittest.TestCase):
    def test_from_ipad_file(self):
        data = IPAD.read_bytes()
        out, info = mk.build(data, 'Choco', 1216, 3.0)
        mk.verify(data, out, info)
        lay = mk.Layouts(out)
        self.assertEqual(mk.Layouts.prefix_for(lay.portrait, WIDTH), 'iPhone-PortraitChoco405')
        self.assertEqual(mk.Layouts.prefix_for(lay.portrait, 608), 'iPhone-PortraitTruffle')     # iPad identity at @2x


if __name__ == '__main__':
    unittest.main()
