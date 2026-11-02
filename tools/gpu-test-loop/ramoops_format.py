"""The UniXNU ramoops log (BootProgress RamoopsLog.c): record format, Linux's pstore view of a zone, the copy layouts,
finding the copies, the bit vote across them and CRC-checked decoding.

One record per line:  #SSSSSSSS MMMMMMMM LL CCCCCCCC K text\\n
S sequence number, M milliseconds since the log started, L text length (<= 200), C CRC32 (zlib) of bytes [0, 22) and
[30, 33 + L), K kind; lowercase hex.

The kernel writes every record to the same offset of every copy, and both Linux zone headers say start 0 / size the
whole zone, so after the next Android boot /sys/fs/pstore/console-ramoops-0 and pmsg-ramoops-0 show the copies at fixed
offsets. A copy is its 64-byte header (magic, version, copy number, layout, CRC), the boot area [64, 4096), the events
ring and the console ring:

  layout 3 (now)        copies of 0xfffc bytes, 4 in the console zone and 32 in pmsg; magic "UXNULOG3"; events ring
                        32 KiB, console ring 0x6ffc; copies per zone at 56, header CRC at 60 of [0,12)+[16,60)
  layout 2 (first runs) one copy of 0x3fff4 bytes in the console zone, 8 in pmsg; magic "UXNULOG2"; events ring 64 KiB,
                        console ring 0x2eff4; header CRC at 56 of [0,12)+[16,56)

What reaches Linux: a size field that lost bits shortens the file (the last copies are partial or missing), a start field
that gained bits (start <= size) rotates it, a damaged sig or a size that gained a bit makes Linux drop the zone. DRAM
loses bits across the reset (measured on the 13T: about 8% of the 1 bits, nearly all 1 -> 0). The decoder:
1. finds the layout and the copies: for each layout (3, then 2) it checks the 64 bytes at every copy offset of both
   files against the header that layout's writer puts there (a likelihood ratio under the decay model), undoes the
   rotation of a file whose start field gained bits, leaves out a copy whose header does not match, and keeps the layout
   whose headers match best;
2. reads the layout from the copy headers: an intact one, else their bit vote, else the vote repaired with the header
   CRC; else the matched layout's defaults;
3. votes every bit across the copies that reach it (a shortened file reaches fewer) with the maximum-likelihood
   threshold for the flip rates it measures and that number of copies: 1 -> 0 is far more likely than 0 -> 1, so a few
   1 votes beat many 0 votes (12 of 36, 11 of 32, 2 of 4 at the measured rates);
4. reads the voted areas and keeps every record whose CRC matches;
5. repairs the rest with the CRC: what every record has is restored (marker, spaces, length, the sequence number the
   neighbours imply, 7-bit ASCII, bit 5 of every hex digit), then the record's 16 least confident bits (where the copies
   disagreed most evenly) are solved for as a GF(2) system against the CRC syndrome (16 of the CRC's 32 bits stay to
   check the answer), then a 1-bit correction and a flipped CRC field; a repair must also look like something the writer
   wrote (known kind, printable text);
6. keeps what still fails as 'unverified' (best-effort text, marked, never counted as checked).
"""
from __future__ import annotations

import dataclasses
import json
import math
import re
import struct
import zlib
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HEADER = 33
TEXT_MAX = 200
SIG = 0x43474244
ZONE_HEADER = 12
DEFAULT_ZONES = (0x40000, 0x200000)
DEFAULT_PA = 0x880000000
COPY_HEADER = 64
BOOT_END = 4096
COPY_LIMIT = 0x10000                        # layout 3: copies of at most 64 KiB that tile each zone
COPY_LIMITS = (COPY_LIMIT, 0x8000, 0x4000, 1 << 31)     # pagani-ramoops-copy values the decoder tries without a header
VERSION = 3                                 # what RamoopsLog.c writes now
VERSIONS = (3, 2)                           # what the decoder reads, newest first
MAGICS = {2: b'UXNULOG2', 3: b'UXNULOG3'}
MAGIC = MAGICS[VERSION]
CRC_AT = {2: 56, 3: 60}                     # the header CRC32 of [0, 12) + [16, CRC_AT)
ZONE_NAMES = ('console', 'pmsg')
KINDS = b'BASRC+GDEPHTUWFJXMQ'
SPACES = (9, 18, 21, 30, 32)
FIXED = (0,) + SPACES + (19, 20)            # restored before a repair: marker, spaces, length
HEX_AT = tuple(range(1, 9)) + tuple(range(10, 18)) + (19, 20)      # seq, ms, length digits (the CRC field: 22..29)
KIND_NAMES = {
    'B': 'boot', 'A': 'boot-args', 'S': 'stage', 'R': 'root/status', 'C': 'console', '+': 'continued', 'G': 'gpu-rows',
    'D': 'display-rows', 'E': 'exception', 'P': 'panic', 'H': 'heartbeat', 'T': 'reset', 'U': 'trace', 'W': 'warning',
    'F': 'fps', 'J': 'jitter', 'X': 'crash', 'M': 'stall-meter', 'Q': 'stall-sample',
}
FATAL_SIGNALS = {4: 'SIGILL', 5: 'SIGTRAP', 6: 'SIGABRT', 7: 'SIGEMT', 8: 'SIGFPE', 10: 'SIGBUS', 11: 'SIGSEGV', 12: 'SIGSYS'}
_EXIT = re.compile(r'^exit pid=(-?\d+) (.*?) status=([0-9a-f]+) ')
_RECORD = re.compile(rb'#([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{2}) ([0-9a-f]{8}) ([\x20-\x7e]) ', re.S)
_HEX = b'0123456789abcdef'
DEFAULT_P1, DEFAULT_P0 = 0.08, 0.005        # flip rates of a 1 and of a 0 when a run cannot measure them
RING_TAIL = HEADER + TEXT_MAX + 1           # bytes appended to a ring so a record wrapping its end is read whole


def covered(raw: bytes, length: int) -> bytes:
    """The bytes the CRC covers: everything but the CRC field and the newline."""
    return bytes(raw[0:22]) + bytes(raw[30:HEADER + length])


def encode(seq: int, ms: int, kind: str, text: str) -> bytes:
    """Reference encoder (the kernel writer's record format)."""
    body = text.encode('ascii')
    assert len(body) <= TEXT_MAX and len(kind) == 1
    head = b'#%08x %08x %02x ' % (seq, ms, len(body))
    tail = b' ' + kind.encode('ascii') + b' ' + body
    return head + b'%08x' % zlib.crc32(head + tail) + tail + b'\n'


@dataclasses.dataclass
class Record:
    seq: int
    ms: int
    kind: str
    text: str
    source: str = ''            # vote | repaired:<how> | unverified

    @property
    def verified(self) -> bool:
        return self.source != 'unverified'

    def as_json(self) -> dict:
        return dataclasses.asdict(self)


def parse_at(buf: bytes, at: int) -> Optional[Tuple[Record, int]]:
    """A structurally valid record with a matching CRC at `at`, and the offset after it (the newline is not covered and
    not checked)."""
    m = _RECORD.match(buf, at)
    if not m:
        return None
    length = int(m.group(3), 16)
    end = at + HEADER + length
    if length > TEXT_MAX or end > len(buf):
        return None
    if zlib.crc32(covered(buf[at:end], length)) != int(m.group(4), 16):
        return None
    text = buf[at + HEADER:end].decode('ascii', 'replace')
    return Record(int(m.group(1), 16), int(m.group(2), 16), chr(buf[at + 31]), text), min(end + 1, len(buf))


def pstore_view(zone: bytes) -> Optional[bytes]:
    """What Linux (fs/pstore/ram_core.c, sig 0, no ECC) exposes for a zone image: None when it zaps the zone, b'' when
    the zone is empty, else data[start:size] + data[0:start]."""
    if len(zone) < ZONE_HEADER:
        return None
    sig, start, size = struct.unpack_from('<III', zone)
    capacity = len(zone) - ZONE_HEADER
    if sig != SIG:
        return None
    if size == 0 and start == 0:
        return b''
    if size > capacity or start > size:
        return None
    data = zone[ZONE_HEADER:]
    return data[start:size] + data[:start]


# ---- layouts --------------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Layout:
    copy_bytes: int
    copies: Tuple[int, int]                 # per zone (console, pmsg)
    events: Tuple[int, int]                 # offset, bytes
    console: Tuple[int, int]
    zones: Tuple[int, int]
    version: int = VERSION
    run_id: Optional[int] = None
    pa: Optional[int] = None
    from_header: bool = False

    @property
    def total(self) -> int:
        return self.copies[0] + self.copies[1]

    @property
    def areas(self) -> List[Tuple[str, int, int, bool]]:
        return [('boot', COPY_HEADER, self.events[0] - COPY_HEADER, False), ('events', self.events[0], self.events[1], True),
                ('console', self.console[0], self.console[1], True)]

    def geometry(self) -> tuple:
        return self.version, self.copy_bytes, self.copies, self.events, self.console, self.zones

    def number(self, zone: int, index: int) -> int:
        """The copy number the writer gives copy `index` of zone `zone`."""
        return index if zone == 0 else self.copies[0] + index

    def header(self, number: int = 0, run_id: int = 0) -> bytes:
        """The header the writer puts in front of copy `number` (with this run id and the CRC it implies)."""
        pa = DEFAULT_PA if self.pa is None else self.pa
        fields = [self.version, number, self.total, run_id, self.copy_bytes, self.events[0], self.events[1],
                  self.console[1], self.zones[0], self.zones[1], pa & 0xffffffff, pa >> 32]
        if self.version >= 3:
            fields.append(self.copies[0] | self.copies[1] << 16)
        h = MAGICS[self.version] + struct.pack('<%dI' % len(fields), *fields)
        h += struct.pack('<I', zlib.crc32(h[:12] + h[16:]))
        return h + bytes(COPY_HEADER - len(h))

    def header_mask(self) -> bytes:
        """The header bytes that do not depend on the run (all but the run id and the CRC)."""
        mask = bytearray(b'\xff' * COPY_HEADER)
        mask[20:24] = bytes(4)
        mask[CRC_AT[self.version]:CRC_AT[self.version] + 4] = bytes(4)
        return bytes(mask)

    def describe(self) -> str:
        return 'layout %d: %d copies of 0x%x (console %d, pmsg %d), events 0x%x+0x%x, console 0x%x+0x%x' % (
            self.version, self.total, self.copy_bytes, self.copies[0], self.copies[1], self.events[0], self.events[1],
            self.console[0], self.console[1])


def default_layout(zones: Tuple[int, int] = DEFAULT_ZONES, version: int = VERSION,
                   limit: int = COPY_LIMIT) -> Optional[Layout]:
    """The writer's layout for these zone sizes (RamoopsLog.c ramoops_log_vm_ready of that layout version; `limit` is
    pagani-ramoops-copy, layout 3 only); None when the zones are too small for a copy."""
    space = (zones[0] - ZONE_HEADER, zones[1] - ZONE_HEADER)
    if version == 2:
        copy = min(space) & ~3
        if copy < BOOT_END + 3 * 4096:
            return None
        copies = [space[0] // copy, space[1] // copy]
        if copies[0] + copies[1] > 16:
            copies[1] = max(16 - copies[0], 0)
        events = 0x10000 if copy >= BOOT_END + 2 * 0x10000 else ((copy - BOOT_END) // 3) & ~3
    else:
        copy = limit
        for s in space:
            copy = min(copy, (s // -(-s // limit)) & ~3)            # the largest size <= the limit that tiles the zone
        if copy < BOOT_END + 3 * 4096:
            return None
        copies = [space[0] // copy, space[1] // copy]
        if copies[0] + copies[1] > 64:
            if copies[0] <= 32:
                copies[1] = 64 - copies[0]
            elif copies[1] <= 32:
                copies[0] = 64 - copies[1]
            else:
                copies = [32, 32]
        rest = copy - BOOT_END
        events = 0x8000 if rest >= 0x8000 + 0x4000 else (rest // 2) & ~3
    return Layout(copy, (copies[0], copies[1]), (BOOT_END, events), (BOOT_END + events, copy - BOOT_END - events),
                  tuple(zones), version)


def parse_copy_header(h: bytes) -> Optional[Layout]:
    """The layout an intact copy header states (its CRC matches and its fields are consistent), else None."""
    if len(h) < COPY_HEADER:
        return None
    version = next((v for v, magic in MAGICS.items() if h[:8] == magic), None)
    if version is None:
        return None
    at = CRC_AT[version]
    ver, _, count, run, copy, events_offset, events, console, zone0, zone1, pa_low, pa_high = struct.unpack_from('<12I', h, 8)
    if ver != version or struct.unpack_from('<I', h, at)[0] != zlib.crc32(h[:12] + h[16:at]):
        return None
    if not (0x2000 <= zone0 <= 1 << 31) or not (0x2000 <= zone1 <= 1 << 31):
        return None
    if version == 2:
        layout = default_layout((zone0, zone1), 2)
        if layout is None or (layout.copy_bytes, layout.events, layout.console[1], layout.total) != (
                copy, (events_offset, events), console, count):
            return None
    else:
        per_zone = struct.unpack_from('<I', h, 56)[0]
        copies = (per_zone & 0xffff, per_zone >> 16)
        if events_offset != BOOT_END or not BOOT_END + 3 * 4096 <= copy <= COPY_LIMIT or copy & 3 or \
                events_offset + events + console != copy or copies[0] + copies[1] != count or not 0 < count <= 64 or \
                copies[0] * copy > zone0 - ZONE_HEADER or copies[1] * copy > zone1 - ZONE_HEADER:
            return None
        layout = Layout(copy, copies, (events_offset, events), (events_offset + events, console), (zone0, zone1), 3)
    layout.run_id, layout.pa, layout.from_header = run, pa_low | pa_high << 32, True
    return layout


# ---- finding the copies ---------------------------------------------------------------------------------------------------
# A header is tested twice under the decay model: its 1 bits against blank space (where a 1 only comes from a gained
# bit), its 0 bits against random bytes (text, a misaligned copy). Each log-likelihood ratio must reach half of what a
# header decayed at the default rates gets; blank space fails the first, text and misaligned data the second.
_V11 = math.log((1 - DEFAULT_P1) / DEFAULT_P0)      # header bit expected 1, read 1 (blank space: a gained bit)
_V10 = math.log(DEFAULT_P1 / (1 - DEFAULT_P0))      # expected 1, read 0
_W00 = math.log((1 - DEFAULT_P0) / 0.5)             # expected 0, read 0 (random bytes: either)
_W01 = math.log(DEFAULT_P0 / 0.5)                   # expected 0, read 1


@dataclasses.dataclass
class _Template:
    value: int                              # the expected header bits (masked)
    mask: int                               # the bits a run does not change
    need: Tuple[float, float]               # half the two scores a header decayed at the default rates gets


def _template(layout: Layout, number: int) -> _Template:
    mask = int.from_bytes(layout.header_mask(), 'little')
    value = int.from_bytes(layout.header(number), 'little') & mask
    ones = value.bit_count()
    zeros = mask.bit_count() - ones
    expect = (ones * ((1 - DEFAULT_P1) * _V11 + DEFAULT_P1 * _V10), zeros * ((1 - DEFAULT_P0) * _W00 + DEFAULT_P0 * _W01))
    return _Template(value, mask, (expect[0] / 2, expect[1] / 2))


def _score(observed: bytes, template: _Template) -> Optional[float]:
    """How well 64 bytes match the expected header: the sum of both log-likelihood ratios, or None when either stays
    below what it needs (or the bytes are not all there)."""
    if len(observed) < COPY_HEADER:
        return None
    o = int.from_bytes(observed[:COPY_HEADER], 'little') & template.mask
    t = template.value
    n11, n10, n01 = (t & o).bit_count(), (t & ~o).bit_count(), (o & ~t).bit_count()
    n00 = template.mask.bit_count() - n11 - n10 - n01
    ones, zeros = n11 * _V11 + n10 * _V10, n00 * _W00 + n01 * _W01
    return ones + zeros if ones >= template.need[0] and zeros >= template.need[1] else None


def _window(data: bytes, start: int, offset: int, n: int) -> bytes:
    """Bytes [offset, offset + n) of the zone data behind a file that Linux showed rotated by `start`
    (data[start:size] + data[0:start]); only the file's own length is there."""
    if offset + n > len(data):
        return b''
    if not start:
        return data[offset:offset + n]
    at = (offset - start) % len(data)
    end = at + n
    return data[at:end] if end <= len(data) else data[at:] + data[:end - len(data)]


def _rotations(size: int) -> List[int]:
    """Start values a zone header written with start 0 can come back with (one or two bits gained) that Linux accepts."""
    bits = [1 << j for j in range(size.bit_length()) if 1 << j < size]
    out = list(bits)
    for i, a in enumerate(bits):
        out.extend(a | b for b in bits[i + 1:] if a | b < size)
    return out


@dataclasses.dataclass
class Copy:
    file: str
    index: int                              # in the file
    number: int                             # the copy number the writer gave it
    data: bytes                             # the copy (shorter when the file ends inside it)
    score: float                            # header log-likelihood ratio (0: taken without a matching header)


@dataclasses.dataclass
class Placement:
    layout: Layout
    copies: List[Copy]
    rotations: Dict[str, int]               # the start value undone per file (0: none)
    inside: Dict[str, int]                  # copies whose header lies inside the file
    left_out: Dict[str, List[int]]          # of those, the ones whose header does not match
    score: float


def place(files: Dict[str, Optional[bytes]], layout: Layout, everything: bool = False, rotate: bool = True) -> Placement:
    """The copies of `layout` in both files: a copy whose header does not match is left out (unless `everything`), and a
    file whose copies only match after undoing a rotation is realigned (unless not `rotate`)."""
    copies: List[Copy] = []
    rotations: Dict[str, int] = {}
    inside_count: Dict[str, int] = {}
    left_out: Dict[str, List[int]] = {}
    total = 0.0
    for zone, name in enumerate(ZONE_NAMES):
        data = files.get(name)
        if not data:
            continue
        offsets = [k * layout.copy_bytes for k in range(layout.copies[zone])]
        inside = [k for k, offset in enumerate(offsets) if offset + COPY_HEADER <= len(data)]
        templates = {k: _template(layout, layout.number(zone, k)) for k in inside}

        def scores(start: int) -> Dict[int, Optional[float]]:
            return {k: _score(_window(data, start, offsets[k], COPY_HEADER), templates[k]) for k in inside}

        def rank(found: Dict[int, Optional[float]]) -> Tuple[int, float]:
            good = [s for s in found.values() if s is not None]
            return len(good), sum(good)

        start, found = 0, scores(0)
        base = best = rank(found)
        if rotate and base[0] < len(inside):
            for candidate in _rotations(len(data)):
                trial = scores(candidate)
                r = rank(trial)
                if r[0] > base[0] and r > best:
                    start, found, best = candidate, trial, r
        view = data if not start else data[len(data) - start:] + data[:len(data) - start]
        rotations[name] = start
        inside_count[name] = len(inside)
        left_out[name] = [k for k in inside if found[k] is None]
        for k in inside:
            if everything or found[k] is not None:
                copies.append(Copy(name, k, layout.number(zone, k), view[offsets[k]:offsets[k] + layout.copy_bytes],
                                   found[k] or 0.0))
                total += found[k] or 0.0
    return Placement(layout, copies, rotations, inside_count, left_out, total)


def _zone_sizes(files: Dict[str, Optional[bytes]]) -> List[Tuple[int, int]]:
    """Zone sizes to try: the 13T's, and the power of two each file's length implies (pagani-ramoops-console/-pmsg)."""
    options = []
    for zone, name in enumerate(ZONE_NAMES):
        sizes = [DEFAULT_ZONES[zone]]
        data = files.get(name)
        if data:
            implied = 1 << (len(data) + ZONE_HEADER - 1).bit_length()
            if implied not in sizes and 0x2000 <= implied <= 1 << 31:
                sizes.append(implied)
        options.append(sizes)
    return [(a, b) for a in options[0] for b in options[1]]


def candidate_layouts(files: Dict[str, Optional[bytes]]) -> List[Layout]:
    """The layouts a writer may have used: both versions, the 13T's zone sizes and those the file lengths imply, and
    (layout 3) the copy limits of pagani-ramoops-copy that give different copies."""
    out: List[Layout] = []
    for version in VERSIONS:
        for zones in _zone_sizes(files):
            for limit in (COPY_LIMITS if version >= 3 else (COPY_LIMIT,)):
                layout = default_layout(zones, version, limit)
                if layout is not None and all(layout.geometry() != other.geometry() for other in out):
                    out.append(layout)
    return out


def locate(files: Dict[str, Optional[bytes]]) -> Placement:
    """The layout whose copy headers match best, and its copies; with no matching header at all, every copy of the newest
    layout. Rotations are searched for the best layout (for every one when no header matched at all)."""
    layouts = candidate_layouts(files)
    placements = [place(files, layout, rotate=False) for layout in layouts]
    best = max(placements, key=lambda p: p.score)
    if not best.copies or any(best.left_out.get(name) for name in ZONE_NAMES):
        retry = [best.layout] if best.copies else layouts
        for placement in (place(files, layout) for layout in retry):
            if placement.score > best.score:
                best = placement
    if not best.copies:
        best = place(files, default_layout(DEFAULT_ZONES, VERSION), everything=True)
    return best


# ---- the bit vote ---------------------------------------------------------------------------------------------------------
def _count_planes(values: Sequence[int], width: int) -> List[int]:
    """Bit-sliced per-bit count of ones over `values` (big integers): planes[j] holds bit j of every count."""
    planes = [0] * width
    for value in values:
        carry = value
        for j in range(width):
            planes[j], carry = planes[j] ^ carry, planes[j] & carry
            if not carry:
                break
    return planes


def _at_least(planes: List[int], threshold: int, mask: int) -> int:
    """Bits whose count is >= threshold (a bit-sliced comparison, most significant plane first)."""
    if threshold <= 0:
        return mask
    if threshold >= 1 << len(planes):
        return 0
    greater, equal = 0, mask
    for j in reversed(range(len(planes))):
        if threshold >> j & 1:
            equal &= planes[j]
        else:
            greater |= equal & planes[j]
            equal &= ~planes[j]
    return (greater | equal) & mask


def ml_threshold(n: int, p1: float, p0: float) -> int:
    """Smallest count of 1 votes out of n that makes 1 the likelier bit."""
    a = math.log((1 - p1) / p0)     # weight of a 1 vote
    b = math.log((1 - p0) / p1)     # weight of a 0 vote
    for k in range(0, n + 1):
        if k * a - (n - k) * b >= 0:
            return max(k, 1)
    return n


@dataclasses.dataclass
class Vote:
    image: bytes                            # the voted copy
    copies: List[bytes]                     # the copies (padded to copy bytes)
    available: List[int]                    # bytes each copy really has
    p1: float
    p0: float
    thresholds: Dict[int, int]              # copies reaching a byte -> 1 votes needed


def _rates(slices: Sequence[Tuple[int, int, List[int]]], copy_bytes: int) -> Tuple[float, float]:
    """The flip rates of a 1 and of a 0, measured against a vote with the default thresholds over the stretches that at
    least three copies reach; zero bits count only in bytes that vote says were written (blank space would dilute p0)."""
    ones = lost = zeros = gained = 0
    low = int.from_bytes(b'\x01' * copy_bytes, 'little')
    for mask, n, planes in slices:
        if n < 3:
            continue
        reference = _at_least(planes, ml_threshold(n, DEFAULT_P1, DEFAULT_P0), mask)
        spread = reference
        for shift in range(1, 8):
            spread |= reference >> shift
        written = (spread & low & mask) * 0xff & mask
        blank = ~reference & written
        support = sum((planes[j] & reference).bit_count() << j for j in range(len(planes)))
        ones += reference.bit_count() * n
        lost += reference.bit_count() * n - support
        zeros += blank.bit_count() * n
        gained += sum((planes[j] & blank).bit_count() << j for j in range(len(planes)))
    p1 = min(max(lost / ones, 1e-4), 0.45) if ones else DEFAULT_P1
    p0 = min(max(gained / zeros, 1e-5), 0.2) if zeros else DEFAULT_P0
    return p1, p0


def vote(copies: List[bytes], copy_bytes: int) -> Vote:
    """Every bit of a copy voted over the copies that reach it (stretch by stretch: a copy cut short reaches less)."""
    padded = [c + bytes(copy_bytes - len(c)) for c in copies]
    available = [len(c) for c in copies]
    ints = [int.from_bytes(c, 'little') for c in padded]
    edges = sorted(set([0, copy_bytes] + [a for a in available if 0 < a < copy_bytes]))
    slices = []
    for lo, hi in zip(edges, edges[1:]):
        members = [v for v, a in zip(ints, available) if a >= hi]
        if members:
            mask = ((1 << ((hi - lo) * 8)) - 1) << (lo * 8)
            slices.append((mask, len(members), _count_planes([v & mask for v in members], len(members).bit_length())))
    p1, p0 = _rates(slices, copy_bytes)
    result = 0
    thresholds: Dict[int, int] = {}
    for mask, n, planes in slices:
        result |= _at_least(planes, thresholds.setdefault(n, ml_threshold(n, p1, p0)), mask)
    return Vote(result.to_bytes(copy_bytes, 'little'), padded, available, p1, p0, thresholds)


# ---- repair ---------------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _unit_syndromes(n: int) -> Tuple[Tuple[int, ...], Dict[int, int]]:
    """lin(e_i) = crc32(e_i) ^ crc32(0^n) for every single-bit vector of an n-byte message, and the inverse map."""
    zero = zlib.crc32(bytes(n))
    units = []
    buf = bytearray(n)
    for i in range(n * 8):
        buf[i >> 3] = 1 << (i & 7)
        units.append(zlib.crc32(buf) ^ zero)
        buf[i >> 3] = 0
    inverse: Dict[int, int] = {}
    for i, v in enumerate(units):
        inverse.setdefault(v, i)
    return tuple(units), inverse


def _flip(message: bytearray, bit: int) -> None:
    message[bit >> 3] ^= 1 << (bit & 7)


def _uncover(message: bytes) -> bytes:
    """A record (without its newline) from its covered bytes, with the CRC field they imply."""
    return bytes(message[:22]) + b'%08x' % zlib.crc32(message) + bytes(message[22:])


def _hex_candidates(field: bytes) -> List[int]:
    """The values a hex field can have: itself when it is hex, or each 1-bit repair of a single non-hex digit."""
    if all(c in _HEX for c in field):
        return [int(field, 16)] if field else []
    bad = [i for i, c in enumerate(field) if c not in _HEX]
    if len(bad) != 1:
        return []
    out = []
    for bit in range(8):
        fixed = bytearray(field)
        fixed[bad[0]] ^= 1 << bit
        if fixed[bad[0]] in _HEX:
            out.append(int(bytes(fixed), 16))
    return out


def _near_values(field: bytes) -> List[int]:
    """The CRC values whose hex text is one bit away from a (damaged) CRC field."""
    out = []
    for i in range(min(len(field), 8)):
        for bit in range(8):
            fixed = bytearray(field[:8])
            fixed[i] ^= 1 << bit
            if len(fixed) == 8 and all(c in _HEX for c in fixed):
                out.append(int(bytes(fixed), 16))
    return out


def _bit_distance(a: bytes, b: bytes) -> int:
    return sum(bin(x ^ y).count('1') for x, y in zip(a, b)) + 8 * abs(len(a) - len(b))


def _solve(vectors: Sequence[int], target: int) -> Optional[int]:
    """The unique subset (bit mask over `vectors`) whose XOR is `target`, or None (no or several solutions)."""
    pivots: Dict[int, Tuple[int, int]] = {}
    for index, vector in enumerate(vectors):
        combo = 1 << index
        while vector:
            top = vector.bit_length() - 1
            if top not in pivots:
                pivots[top] = (vector, combo)
                break
            pv, pc = pivots[top]
            vector ^= pv
            combo ^= pc
        else:
            return None                     # a dependent vector: more than one solution
    combo = 0
    while target:
        top = target.bit_length() - 1
        if top not in pivots:
            return None
        pv, pc = pivots[top]
        target ^= pv
        combo ^= pc
    return combo


def _record(message: bytes, seq: Optional[int] = None, bounds: Optional['Bounds'] = None) -> Optional[Record]:
    """The record a repaired message stands for, when it is one the writer could have written: it parses, its kind is
    known, its text printable, its sequence number is the one its neighbours imply (when they do) and both it and its
    time lie between the neighbours' (when the area runs in order there)."""
    parsed = parse_at(_uncover(message) + b'\n', 0)
    if parsed is None:
        return None
    record = parsed[0]
    if record.kind.encode() not in KINDS or any(not 0x20 <= ord(c) <= 0x7e for c in record.text):
        return None
    if seq is not None and record.seq != seq:
        return None
    if bounds is not None and not (bounds.seq[0] < record.seq < bounds.seq[1] and bounds.ms[0] <= record.ms <= bounds.ms[1]):
        return None
    return record


def _llr(k: int, n: int, p1: float, p0: float) -> float:
    return k * math.log((1 - p1) / p0) - (n - k) * math.log((1 - p0) / p1)


def _ascii(raw: bytearray, hex_at: Iterable[int]) -> None:
    """What every byte of a record is: 7-bit ASCII, and a hex digit ('0'-'9', 'a'-'f') has bit 5 set."""
    for i in range(len(raw)):
        raw[i] &= 0x7f
    for i in hex_at:
        if i < len(raw):
            raw[i] |= 0x20


@dataclasses.dataclass
class Bounds:
    """What the valid records on both sides of a damaged stretch imply for a record in it (the area runs in sequence
    order there): its sequence number lies strictly between theirs, its time between theirs."""
    seq: Tuple[int, int]
    ms: Tuple[int, int]


WEAK_BITS = 16      # unknowns of the GF(2) solve: 32 - 16 bits of the CRC are left to check the answer (~2^-16)
LOW_WEIGHT_BITS = 32    # errors of up to three bits among these are tried one by one (each check ~2^-32)
LOST_WEAK_BITS = 24     # a bit no copy kept, together with up to two of these
CRC_SPLIT_BITS = 6      # split bits of the CRC field flipped (one or two at a time) for further CRC values


def _repairs(stage: str, message: bytes, weak: Sequence[int], lost: frozenset, target: int) -> List[bytes]:
    """The messages a repair stage makes of `message` for one CRC value: every candidate it finds (several mean that
    at least one of them is wrong; the caller keeps a result only when exactly one passes the checks)."""
    units, inverse = _unit_syndromes(len(message))
    syndrome = zlib.crc32(message) ^ target
    patterns: List[Tuple[int, ...]] = []
    if stage == 'fields':
        patterns = [()] if not syndrome else []
    elif not syndrome:
        return []
    elif stage == '1bit':
        bit = inverse.get(syndrome)
        patterns = [(bit,)] if bit is not None else []
    elif stage == 'weak-bits':
        combo = _solve([units[b] for b in weak[:WEAK_BITS]], syndrome) if weak else None
        if combo is not None:
            patterns = [tuple(b for i, b in enumerate(weak[:WEAK_BITS]) if combo >> i & 1)]
    elif stage == 'low-weight':
        chosen = weak[:LOW_WEIGHT_BITS]
        single = {}
        for b in chosen:
            single.setdefault(units[b], b)
        found = set()
        if syndrome in single:
            found.add((single[syndrome],))
        for i, a in enumerate(chosen):
            c = single.get(syndrome ^ units[a])
            if c is not None and c != a:
                found.add(tuple(sorted((a, c))))
            for b in chosen[i + 1:]:
                c = single.get(syndrome ^ units[a] ^ units[b])
                if c is not None and c not in (a, b):
                    found.add(tuple(sorted((a, b, c))))
        patterns = sorted(found)
    elif stage == 'lost-bit':
        chosen = weak[:LOST_WEAK_BITS]
        found = set()
        subsets: List[Tuple[int, ...]] = [()] + [(a,) for a in chosen] + \
            [(a, b) for i, a in enumerate(chosen) for b in chosen[i + 1:]]
        for subset in subsets:
            rest = syndrome
            for b in subset:
                rest ^= units[b]
            c = inverse.get(rest)
            if c is not None and c in lost and c not in subset:
                found.add(tuple(sorted(subset + (c,))))
        patterns = sorted(found)
    out = []
    for pattern in patterns:
        repaired = bytearray(message)
        for b in pattern:
            _flip(repaired, b)
        if zlib.crc32(repaired) == target:
            out.append(bytes(repaired))
    return out


def soft_repair(voted: bytes, copies: Sequence[bytes], available: Sequence[int], at: int, blob_length: int,
                seq: Optional[int], p1: float, p0: float, bounds: Optional[Bounds] = None) -> Optional[Tuple[Record, str]]:
    """One record from the voted bytes of an area and the per-copy bytes behind them (`copies`, the same area of every
    copy, `available` bytes each), and how it was repaired. Every result matches its CRC and passes the checks of
    _record; a stage that finds two different records gives none. The stages run from the least to the most likely to
    find a wrong answer by chance, each for every candidate length and sequence number before the next:
      fields          only restored bytes were wrong
      1bit            one more bit anywhere
      low-weight      up to three of the 32 least confident bits
      weak-bits       any of the 16 least confident bits (a GF(2) solve against the CRC syndrome)
      lost-bit        a 1 that no copy kept (read 0 everywhere), with up to two of the 24 least confident bits
      crc-field       the CRC field itself is what is damaged (within 2 bits of the CRC of the record)
      1bit+crc-field  one bit anywhere and one bit of the CRC field
      <stage>+crc     fields, low-weight and weak-bits again with one or two split bits of the CRC field flipped"""
    def restored(raw: bytes, hex_at: Iterable[int]) -> bytes:
        out = bytearray(raw)
        _ascii(out, hex_at)
        return bytes(out)

    crc_field = restored(voted[at + 22:at + 30], range(8))
    head = restored(voted[at:at + 21], HEX_AT)
    lengths = []
    for value in [blob_length - HEADER - 1, blob_length - HEADER] + _hex_candidates(head[19:21]):
        if 0 <= value <= TEXT_MAX and value not in lengths and at + HEADER + value <= len(voted):
            lengths.append(value)
    if not lengths:
        return None
    # Per-bit counts of ones over the copies that reach each byte, and how many do.
    counts = []
    for position in range(HEADER + max(lengths)):
        offset = at + position
        reach = [c[offset] for c, a in zip(copies, available) if offset < a]
        counts.append((len(reach), [sum((v >> bit) & 1 for v in reach) for bit in range(8)]))
    targets = _hex_candidates(crc_field)
    # Further CRC values: the field with one or two of its least confident split bits flipped.
    split = sorted((abs(_llr(counts[22 + i][1][bit], counts[22 + i][0], p1, p0)), i, bit) for i in range(8)
                   for bit in (0, 1, 2, 3, 4, 6) if 22 + i < len(counts) and 0 < counts[22 + i][1][bit] < counts[22 + i][0])
    split = [(i, bit) for _, i, bit in split[:CRC_SPLIT_BITS]]
    alternatives: List[int] = []
    for flips in [(f,) for f in split] + [(f, g) for k, f in enumerate(split) for g in split[k + 1:]]:
        field = bytearray(crc_field)
        for i, bit in flips:
            field[i] ^= 1 << bit
        if all(c in _HEX for c in field):
            value = int(bytes(field), 16)
            if value not in targets and value not in alternatives:
                alternatives.append(value)
    combos = []
    for length in lengths:
        raw = bytearray(voted[at:at + HEADER + length])
        raw[0] = 0x23
        for i in SPACES:
            raw[i] = 0x20
        raw[19:21] = b'%02x' % length
        _ascii(raw, HEX_AT)
        positions = list(range(0, 22)) + list(range(30, HEADER + length))
        for s in ([seq, None] if seq is not None else [None]):
            fixed = bytearray(raw)
            if s is not None:
                fixed[1:9] = b'%08x' % s
            message = bytes(covered(fixed, length))
            # The bits the copies disagree on, least confident first, and the 0 bits no copy has a 1 in (restored
            # bits are certain).
            weak, lost = [], set()
            for index, position in enumerate(positions):
                if position in FIXED or (s is not None and 1 <= position <= 8):
                    continue
                n, ones = counts[position]
                if n < 2:
                    continue
                for bit in range(7):
                    if bit == 5 and position in HEX_AT:
                        continue
                    k = ones[bit]
                    if 0 < k < n:
                        weak.append((abs(_llr(k, n, p1, p0)), index * 8 + bit))
                    elif k == 0 and not (message[index] >> bit) & 1:
                        lost.add(index * 8 + bit)
            weak.sort()
            combos.append((message, [b for _, b in weak], frozenset(lost), seq if s is not None else None))
    stages = [('fields', targets), ('1bit', targets), ('low-weight', targets), ('weak-bits', targets), ('lost-bit', targets),
              ('crc-field', None), ('1bit+crc-field', None), ('fields+crc', alternatives),
              ('low-weight+crc', alternatives), ('weak-bits+crc', alternatives)]
    for stage, values in stages:
        results: Dict[Tuple[int, int, str, str], Record] = {}
        for message, weak, lost, expect in combos:
            if stage == 'crc-field':
                if len(crc_field) == 8 and _bit_distance(b'%08x' % zlib.crc32(message), crc_field) <= 2:
                    record = _record(message, expect, bounds)
                    if record:
                        results[(record.seq, record.ms, record.kind, record.text)] = record
                continue
            if stage == '1bit+crc-field':
                candidates = [m for value in _near_values(crc_field) for m in _repairs('1bit', message, weak, lost, value)]
            else:
                kind = stage.split('+')[0]
                candidates = [m for value in values for m in _repairs(kind, message, weak, lost, value)]
            for candidate in candidates:
                record = _record(candidate, expect, bounds)
                if record:
                    results[(record.seq, record.ms, record.kind, record.text)] = record
        if len(results) == 1:
            return next(iter(results.values())), stage
        if len(results) > 1:
            return None                     # two different records fit: neither can be trusted
    return None


def best_effort(voted: bytes, at: int, blob_length: int, seq: Optional[int]) -> Optional[Record]:
    """The voted text of a record that no repair could check, when its header still reads plausibly."""
    blob = voted[at:at + blob_length]
    if len(blob) < HEADER:
        return None
    seqs = _hex_candidates(bytes(blob[1:9]))
    number = seq if seq is not None else (seqs[0] if len(seqs) == 1 else None)
    if number is None or number <= 0:
        return None
    ms_values = _hex_candidates(bytes(blob[10:18]))
    length = blob_length - HEADER - 1
    if not 0 <= length <= TEXT_MAX:
        lengths = _hex_candidates(bytes(blob[19:21]))
        length = lengths[0] if lengths and lengths[0] <= TEXT_MAX else min(max(len(blob) - HEADER, 0), TEXT_MAX)
    text = bytes(blob[HEADER:HEADER + length])
    kind = chr(blob[31]) if 0x20 <= blob[31] <= 0x7e else '?'
    return Record(number, ms_values[0] if len(ms_values) == 1 else 0, kind,
                  ''.join(chr(c) if 0x20 <= c <= 0x7e else '?' for c in text), 'unverified')


# ---- the copy header ------------------------------------------------------------------------------------------------------
def read_header(copies: Sequence[Copy]) -> Tuple[Optional[Layout], str]:
    """The layout the copy headers state, and how it was read: 'intact' (one header passed its CRC), 'voted' (the bit
    vote of all of them did), 'repaired' (the vote with its least confident bits solved for against the CRC)."""
    headers = [c.data[:COPY_HEADER] for c in copies if len(c.data) >= COPY_HEADER]
    for h in headers:
        layout = parse_copy_header(h)
        if layout:
            return layout, 'intact'
    if len(headers) < 2:
        return None, 'one damaged header'
    n = len(headers)
    threshold = ml_threshold(n, DEFAULT_P1, DEFAULT_P0)
    counts = [[sum((h[i] >> bit) & 1 for h in headers) for bit in range(8)] for i in range(COPY_HEADER)]
    voted = bytes(sum(1 << bit for bit in range(8) if counts[i][bit] >= threshold) for i in range(COPY_HEADER))
    layout = parse_copy_header(voted)
    if layout:
        return layout, 'voted'
    for version, magic in sorted(MAGICS.items(), key=lambda item: _bit_distance(voted[:8], item[1])):
        if _bit_distance(voted[:8], magic) > 4:
            continue
        at = CRC_AT[version]
        fixed = bytearray(voted)
        fixed[:8] = magic
        fixed[8:12] = struct.pack('<I', version)
        message = bytes(fixed[:12]) + bytes(fixed[16:at])
        positions = [i for i in range(12, at) if i not in (12, 13, 14, 15)]
        weak = sorted((abs(_llr(counts[i][bit], n, DEFAULT_P1, DEFAULT_P0)), (i if i < 12 else i - 4) * 8 + bit)
                      for i in positions for bit in range(8) if 0 < counts[i][bit] < n)
        weak = [b for _, b in weak[:WEAK_BITS]]
        units, _ = _unit_syndromes(len(message))
        syndrome = zlib.crc32(message) ^ struct.unpack_from('<I', fixed, at)[0]
        combo = 0 if not syndrome else _solve([units[b] for b in weak], syndrome) if weak else None
        if combo is None:
            continue
        repaired = bytearray(message)
        for i, b in enumerate(weak):
            if combo >> i & 1:
                _flip(repaired, b)
        header = bytes(repaired[:12]) + bytes(4) + bytes(repaired[12:]) + bytes(fixed[at:])
        layout = parse_copy_header(header)
        if layout:
            return layout, 'repaired'
    return None, 'no header passed its CRC (%d voted)' % n


# ---- decoding -------------------------------------------------------------------------------------------------------------
def _near(byte: int, want: int) -> bool:
    return bin(byte ^ want).count('1') <= 1


def _split(gap: bytes) -> List[Tuple[int, bytes]]:
    """A damaged stretch cut into record-sized blobs: at every newline followed by (almost) a marker, and where a
    readable length field ends a record before the blob does."""
    starts = [0] + [i for i in range(1, len(gap)) if _near(gap[i - 1], 0x0a) and _near(gap[i], 0x23)]
    out: List[Tuple[int, bytes]] = []
    for s, e in zip(starts, starts[1:] + [len(gap)]):
        blob = gap[s:e]
        offset = s
        while True:
            cut = None
            for length in (_hex_candidates(blob[19:21]) if len(blob) >= 21 else []):
                end = HEADER + length + 1
                if length <= TEXT_MAX and end + HEADER < len(blob) and _near(blob[end], 0x23):
                    cut = end
                    break
            if cut is None:
                break
            out.append((offset, blob[:cut]))
            offset += cut
            blob = blob[cut:]
        if blob:
            out.append((offset, blob))
    return out


def _field_seq(blob: bytes) -> Optional[int]:
    values = _hex_candidates(bytes(blob[1:9])) if len(blob) >= 9 else []
    return values[0] if len(values) == 1 else None


@dataclasses.dataclass
class Damaged:
    offset: int                             # in the area (a ring extended by its first bytes)
    length: int                             # bytes up to the next record
    seq: Optional[int]                      # implied by the neighbours, else read from the record
    bounds: Optional[Bounds] = None         # the neighbours' sequence numbers and times, when they run in order


def scan_area(data: bytes, limit: int) -> Tuple[List[Tuple[int, int, Record]], List[Damaged]]:
    """Intact records of an area that start before `limit` (the area size; a ring is passed extended by its first
    bytes) and the damaged stretches between them, cut into record-sized pieces with the sequence numbers their
    neighbours imply."""
    valid: List[Tuple[int, int, Record]] = []
    at = data.find(b'#')
    while 0 <= at < limit:
        parsed = parse_at(data, at)
        if parsed:
            valid.append((at, parsed[1], parsed[0]))
            at = data.find(b'#', parsed[1])
        else:
            at = data.find(b'#', at + 1)
    pieces: List[Damaged] = []
    bounds = [(0, 0, None)] + valid + [(limit, limit, None)]
    for (_, prev_end, prev), (next_at, _, nxt) in zip(bounds, bounds[1:]):
        if next_at <= prev_end:
            continue
        gap = data[prev_end:next_at]
        if not gap.strip(b'\x00'):
            continue                        # blank space of a ring that has not wrapped
        blobs = [(prev_end + o, b) for o, b in _split(gap) if len(b) >= HEADER and b.strip(b'\x00')]
        hints: List[Optional[int]] = [None] * len(blobs)
        bounds = None
        if prev is not None and nxt is not None and prev.seq < nxt.seq and prev.ms <= nxt.ms:
            bounds = Bounds((prev.seq, nxt.seq), (prev.ms, nxt.ms))
            if nxt.seq - prev.seq - 1 == len(blobs):
                hints = [prev.seq + 1 + i for i in range(len(blobs))]
        for (offset, blob), hint in zip(blobs, hints):
            pieces.append(Damaged(offset, len(blob), hint if hint is not None else _field_seq(blob), bounds))
    return valid, pieces


@dataclasses.dataclass
class Decoded:
    records: List[Record]
    layout: Optional[Layout]
    copies: Dict[str, dict]
    repaired: Dict[str, int]
    unverified: int
    missing: List[Tuple[int, int]]
    vote: Dict[str, object]
    notes: List[str]

    def summary_stats(self) -> dict:
        return {'records': len(self.records), 'verified': sum(1 for r in self.records if r.verified),
                'unverified': self.unverified, 'repaired': self.repaired, 'missing_seq_ranges': self.missing,
                'copies': self.copies, 'vote': self.vote, 'notes': self.notes,
                'layout': self.layout.describe() if self.layout else None,
                'first_seq': self.records[0].seq if self.records else None,
                'last_seq': self.records[-1].seq if self.records else None}


def _file_notes(files: Dict[str, Optional[bytes]], placement: Placement) -> List[str]:
    notes = []
    layout = placement.layout
    for zone, name in enumerate(ZONE_NAMES):
        data = files.get(name)
        if not data:
            continue
        whole = layout.zones[zone] - ZONE_HEADER
        if len(data) < whole:
            notes.append('%s-ramoops-0 has %d of %d bytes: the zone\'s size field lost bits (Linux showed a shorter file); '
                         '%d of its %d copies start inside it' % (name, len(data), whole, placement.inside.get(name, 0),
                                                                  layout.copies[zone]))
        if placement.rotations.get(name):
            notes.append('%s-ramoops-0 was rotated by 0x%x (the zone\'s start field gained bits): realigned' %
                         (name, placement.rotations[name]))
        if placement.left_out.get(name):
            notes.append('%s copies %s left out: their headers do not match layout %d' %
                         (name, ', '.join(map(str, placement.left_out[name])), layout.version))
    return notes


def decode(files: Dict[str, Optional[bytes]]) -> Decoded:
    """The log from the pstore files (name -> bytes, None when Linux showed no file): 'console' and 'pmsg'."""
    notes: List[str] = []
    for name in ZONE_NAMES:
        if not files.get(name):
            notes.append('no %s-ramoops-0: Linux dropped that zone (damaged header) or it was empty' % name)
    placement = locate(files)
    layout = placement.layout
    stated, how = read_header(placement.copies) if placement.copies else (None, 'no copy')
    if stated is not None and stated.geometry() != layout.geometry():
        again = place(files, stated)                # the headers state other zone sizes: cut the copies by them
        if again.copies:
            placement = again
        else:
            notes.append('the copy headers state %s, but no copy matches it' % stated.describe())
            stated, how = None, 'stated layout not found'
    if stated is not None:
        layout = dataclasses.replace(stated)
        if how != 'intact':
            notes.append('copy header %s from %d copies' % (how, len(placement.copies)))
    elif placement.score > 0:
        notes.append('copy header: %s; layout %d assumed (its header pattern matched)' % (how, layout.version))
    elif placement.copies:
        notes.append('copy header: %s; no header matched at all: layout %d assumed, every copy used' % (how, layout.version))
    notes.extend(_file_notes(files, placement))
    copies = placement.copies
    stats = {}
    for zone, name in enumerate(ZONE_NAMES):
        data = files.get(name)
        stats[name] = {'bytes': len(data) if data else 0, 'copies': sum(1 for c in copies if c.file == name),
                       'layout_copies': layout.copies[zone], 'rotation': placement.rotations.get(name, 0),
                       'left_out': placement.left_out.get(name, [])}
    if not copies:
        why = 'Linux showed neither file (both zone headers damaged across the reset, or no XNU run before this boot)' \
            if not any(files.get(name) for name in ZONE_NAMES) else 'no copy in the files'
        return Decoded([], None, stats, {}, 0, [], {}, notes + ['no copy to read: ' + why])
    v = vote([c.data for c in copies], layout.copy_bytes)
    merged: Dict[int, Record] = {}
    repaired: Dict[str, int] = {}
    unverified: Dict[int, Record] = {}
    for _, offset, size, ring in layout.areas:
        tail = RING_TAIL if ring else 0
        data = v.image[offset:offset + size]
        extended = data + data[:tail]
        area_copies = [c[offset:offset + size] + c[offset:offset + tail] for c in v.copies]
        area_available = []
        for a in v.available:
            have = max(0, min(a - offset, size))
            area_available.append(have + (min(max(a - offset, 0), tail) if have == size else 0))
        valid, pieces = scan_area(extended, size)
        for _, _, record in valid:
            record.source = 'vote'
            merged.setdefault(record.seq, record)
        for piece in pieces:
            if piece.seq is not None and piece.seq in merged:
                continue
            result = soft_repair(extended, area_copies, area_available, piece.offset, piece.length, piece.seq, v.p1, v.p0,
                                 piece.bounds)
            if result is not None and result[0].seq not in merged:
                record, how = result
                record.source = 'repaired:' + how
                merged[record.seq] = record
                repaired[record.source] = repaired.get(record.source, 0) + 1
                continue
            guess = best_effort(extended, piece.offset, piece.length, piece.seq)
            if guess is not None and guess.seq not in merged:
                unverified.setdefault(guess.seq, guess)
    for seq, record in unverified.items():
        merged.setdefault(seq, record)
    records = [merged[k] for k in sorted(merged)]
    missing: List[Tuple[int, int]] = []
    seqs = [r.seq for r in records]
    for a, b in zip(seqs, seqs[1:]):
        if b > a + 1:
            missing.append((a + 1, b - 1))
    vote_info = {'copies': len(copies), 'p1': round(v.p1, 5), 'p0': round(v.p0, 6),
                 'thresholds': {str(k): t for k, t in sorted(v.thresholds.items())}, 'version': layout.version,
                 'layout': 'header' if layout.from_header else 'default', 'run_id': layout.run_id}
    if records and not any(r.verified for r in records):
        notes.append('no record passed its CRC')
    return Decoded(records, layout, stats, repaired, sum(1 for r in records if not r.verified), missing, vote_info, notes)


# ---- presentation ---------------------------------------------------------------------------------------------------------
def joined(records: Iterable[Record]) -> List[Record]:
    """Records with their '+' continuations appended; a continuation whose predecessor was lost stays a '+' line."""
    out: List[Record] = []
    previous = None
    for rec in records:
        if rec.kind == '+' and out and previous is not None and rec.seq == previous + 1:
            out[-1].text += rec.text
            if not rec.verified:
                out[-1].source = 'unverified'
        else:
            out.append(Record(rec.seq, rec.ms, rec.kind, rec.text, rec.source))
        previous = rec.seq
    return out


def render(records: Iterable[Record]) -> str:
    """merged.log: seq, seconds, kind ('?' after it when unverified), text."""
    lines = []
    for rec in joined(records):
        lines.append('%8d %10.3f %s%s %s' % (rec.seq, rec.ms / 1000.0, rec.kind, ' ' if rec.verified else '?', rec.text))
    return '\n'.join(lines) + ('\n' if lines else '')


def gpu_rows(records: Sequence[Record], kind: str = 'G') -> Tuple[Optional[int], List[str]]:
    """The newest complete set of status rows ("<set>.<row>/<rows> text"), checked records only."""
    sets: Dict[int, Dict[int, str]] = {}
    sizes: Dict[int, int] = {}
    for rec in records:
        if rec.kind != kind or not rec.verified:
            continue
        m = re.match(r'(\d+)\.(\d+)/(\d+) ?(.*)$', rec.text)
        if not m:
            continue
        number, row, count = int(m.group(1)), int(m.group(2)), int(m.group(3))
        sets.setdefault(number, {})[row] = m.group(4)
        sizes[number] = count
    for number in sorted(sets, reverse=True):
        count = sizes[number]
        rows = sets[number]
        if count == 0 or all(i in rows for i in range(count)):
            return number, [rows[i] for i in range(count)] if count else []
    if sets:
        number = max(sets)
        return number, [sets[number][i] for i in sorted(sets[number])]
    return None, []


def _line(r: Record) -> str:
    return '%10.3f%s %s' % (r.ms / 1000.0, ' ' if r.verified else '?', r.text)


def summary(decoded: Decoded) -> str:
    records = decoded.records
    out: List[str] = []
    stats = decoded.summary_stats()
    out.append('records %d (seq %s..%s): %d verified, %d unverified (marked ?)' % (
        stats['records'], stats['first_seq'], stats['last_seq'], stats['verified'], stats['unverified']))
    if decoded.layout:
        out.append('  %s (%s)' % (decoded.layout.describe(), 'from the copy headers' if decoded.layout.from_header else
                                  'assumed: no copy header passed its CRC'))
    out.append('  vote %s' % json.dumps(decoded.vote, sort_keys=True))
    for name, info in sorted(decoded.copies.items()):
        out.append('  file %-8s %s' % (name, json.dumps(info, sort_keys=True)))
    if decoded.repaired:
        out.append('  repaired %s' % json.dumps(decoded.repaired, sort_keys=True))
    if decoded.missing:
        out.append('  missing seq (overwritten by a ring, or undecodable): %s%s' % (', '.join(
            '%d-%d' % r if r[0] != r[1] else str(r[0]) for r in decoded.missing[:20]),
            ' ...' if len(decoded.missing) > 20 else ''))
    for note in decoded.notes:
        out.append('  note: %s' % note)
    full = joined(records)
    boot = [r for r in full if r.kind in 'BA']
    if boot:
        out.append('')
        out.append('LOG HEADER')
        out.extend('  %s%s' % ('' if r.verified else '? ', r.text) for r in boot[:8])
    stages = [r for r in full if r.kind == 'S' and not r.text.startswith(('pid=', 'exit pid='))]
    if stages:
        out.append('')
        out.append('LAST STAGES')
        out.extend('  ' + _line(r) for r in stages[-8:])
    roots = [r for r in full if r.kind == 'R']
    gpu_stage = [r for r in roots if 'GPU' in r.text.upper()]
    if roots:
        out.append('')
        out.append('LAST STATUS STAGE  ' + _line(roots[-1]))
    if gpu_stage:
        out.append('LAST GPU STAGE     ' + _line(gpu_stage[-1]))
    number, rows = gpu_rows(records)
    out.append('')
    out.append('GPU STATUS ROWS%s' % ('' if number is None else ' (set %d)' % number))
    if rows:
        out.extend('  ' + row for row in rows)
    else:
        out.append('  (none logged)')
    number, rows = gpu_rows(records, 'D')
    if number is not None:
        out.append('DISPLAY PROBE ROWS (set %d)' % number)
        out.extend('  ' + row for row in rows)
    fatal: Dict[Tuple[str, int], List[Record]] = {}
    for r in full:
        m = _EXIT.match(r.text) if r.kind == 'S' else None
        if m and int(m.group(3), 16) & 0x7f in FATAL_SIGNALS:
            fatal.setdefault((m.group(2), int(m.group(3), 16)), []).append(r)
    if fatal:
        out.append('')
        out.append('FATAL EXITS (S exit records killed by a fatal signal; status: the signal, +0x80 core)')
        for (name, status), chosen in fatal.items():
            out.append('  %-16s status=%-3x %-8s x%-3d %.3f .. %.3f s' % (name, status, FATAL_SIGNALS[status & 0x7f], len(chosen),
                                                                        chosen[0].ms / 1000.0, chosen[-1].ms / 1000.0))
    crashes = [r for r in full if r.kind == 'X']
    if crashes:
        out.append('')
        out.append('CRASH REPORTS (X records, the last 30; symbolize-crashes.py names c: m: a: tokens)')
        out.extend('  ' + _line(r) for r in crashes[-30:])
    for title, kinds, last in (('KERNEL EXCEPTIONS', 'E', 4), ('PANIC', 'P', 80), ('RESET', 'T', 8)):
        chosen = [r for r in full if r.kind in kinds]
        if chosen:
            out.append('')
            out.append(title)
            out.extend('  ' + _line(r) for r in chosen[-last:])
    beats = [r for r in full if r.kind == 'H']
    if beats:
        out.append('')
        out.append('LAST HEARTBEAT ' + _line(beats[-1]))
    console = [r for r in full if r.kind in 'CU']
    if console:
        out.append('')
        out.append('LAST CONSOLE LINES')
        out.extend('  %10.3f%s %s %s' % (r.ms / 1000.0, ' ' if r.verified else '?', r.kind, r.text) for r in console[-15:])
    if records:
        last = records[-1]
        out.append('')
        out.append('LAST RECORD  %d at %.3f s: %s %s%s' % (last.seq, last.ms / 1000.0, last.kind, last.text,
                                                         '' if last.verified else '  (unverified)'))
    return '\n'.join(out) + '\n'
