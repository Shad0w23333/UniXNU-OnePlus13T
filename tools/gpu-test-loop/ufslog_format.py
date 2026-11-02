"""The UFS log (BootProgress UfsLog.c): the header, the slots, the merged log, the export; reading it from the phone is read-only.

The window is the last 32 MiB of the unixnu partition, the export region the 256 MiB below it. Block 0 of the window is the header
(magic PGLOG001, CRC32 at its end), blocks 1..32 the kernel's message buffer, from block 33 slots of 16 blocks. A slot is one snapshot of copy 0 of
the ramoops log (ramoops_format.py), bit for bit as it was in DRAM: nothing here votes or repairs, every record just has to pass its CRC,
and the slots merged by sequence number give a log longer than any ring. Logical slot n lies at position n below slot_pinned and at
pinned + (n - pinned) % (count - pinned) above; the header's slots_written says how many are on the medium and is written last.

A window with a damaged header is still read: slot position 0 is always a run's first, so its copy header names the newest run, and every
slot of that run is taken as it comes (an export cannot be found without the header).

The window stays on the partition across runs and nothing erases it, so a reader has to ask whether it belongs to the run it is looking at:
judge() compares the header's boot_id with the one read before the run, and its ramoops run with the one pstore shows. Only a window that is
this run's goes into the run's log; one it cannot tell stays in ufs/ with a warning, whatever it says.

The header also says where the kernel put the window (first_lba). judge() holds that against Android's partition table: a difference means the
partition moved, or a kernel wrote where it should not, and neither is a log to believe.
"""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import re
import shlex
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import ramoops_device as rd
import ramoops_format as rf

BLOCK = 4096
WINDOW_BLOCKS = 8192                        # NativeUfs kLogReserveBlocks
EXPORT_BLOCKS = 65536                       # NativeUfs kExportBlocks
MAGIC = b'PGLOG001'
VERSION = 1
PARTITION = '/dev/block/by-name/unixnu'
HEADER = struct.Struct('<8sIIQQ24I')
FIELDS = ('magic', 'version', 'header_bytes', 'boot_id', 'first_lba', 'ramoops_run', 'window_blocks', 'msg_first', 'msg_blocks',
          'msg_bytes', 'slot_first', 'slot_blocks', 'slot_count', 'slot_pinned', 'slots_written', 'uptime_s', 'rounds', 'write_errors',
          'gpt_open', 'gpt_last', 'state', 'export_first', 'export_blocks', 'export_bytes', 'export_generation', 'export_below',
          'period_ms', 'max_round_us', 'crc')
STATES = {2: 'writing', 3: 'stopped after write errors', 4: 'stopped: the GPT changed', 5: 'panic flush', 6: 'last snapshot before a reset'}
EXPORT_MAGIC = b'SHEXR001'
STREAM_MAGIC = b'M2VCACH1'


class FormatError(ValueError):
    pass


@dataclasses.dataclass
class Header:
    version: int
    header_bytes: int
    boot_id: int
    first_lba: int
    ramoops_run: int
    window_blocks: int
    msg_first: int
    msg_blocks: int
    msg_bytes: int
    slot_first: int
    slot_blocks: int
    slot_count: int
    slot_pinned: int
    slots_written: int
    uptime_s: int
    rounds: int
    write_errors: int
    gpt_open: int
    gpt_last: int
    state: int
    export_first: int
    export_blocks: int
    export_bytes: int
    export_generation: int
    export_below: int
    period_ms: int
    max_round_us: int
    crc: int

    def describe(self) -> str:
        export = 'export generation %d: %d bytes at block %d' % (self.export_generation, self.export_bytes, self.export_first) \
            if self.export_bytes else 'no export'
        return ('UFS log at LBA %d: boot_id 0x%x, ramoops run 0x%x, state %d (%s), up %d s, %d rounds, %d slots, %d write errors, gpt %d/%d, '
                'period %d ms, longest round %d us, %s' % (self.first_lba, self.boot_id, self.ramoops_run, self.state,
                                                           STATES.get(self.state, 'unknown'), self.uptime_s, self.rounds,
                                                           self.slots_written, self.write_errors, self.gpt_open, self.gpt_last,
                                                           self.period_ms, self.max_round_us, export))


def parse_header(block: bytes) -> Tuple[Optional[Header], str]:
    """The header in a window's first block, or None and why not ('blank': a window nothing wrote)."""
    if len(block) < HEADER.size:
        return None, 'short window'
    if not any(block[:BLOCK]):
        return None, 'blank'
    fields = dict(zip(FIELDS, HEADER.unpack_from(block)))
    magic = fields.pop('magic')
    if magic != MAGIC:
        return None, 'no header magic (%r)' % magic
    if fields['version'] != VERSION or fields['header_bytes'] != HEADER.size:
        return None, 'header version %d, %d bytes' % (fields['version'], fields['header_bytes'])
    if zlib.crc32(block[:HEADER.size - 4]) != fields['crc']:
        return None, 'header CRC'
    h = Header(**fields)
    end = h.slot_first + h.slot_count * h.slot_blocks
    if not h.slot_blocks or not h.window_blocks or end > h.window_blocks or h.msg_first + h.msg_blocks > h.slot_first or \
            h.slot_pinned >= h.slot_count or h.msg_bytes > h.msg_blocks * BLOCK or h.slot_blocks * BLOCK > 1 << 20:
        return None, 'header geometry'
    return h, ''


def position(h: Header, logical: int) -> int:
    pinned = h.slot_pinned
    return logical if logical < pinned else pinned + (logical - pinned) % (h.slot_count - pinned)


def live_slots(h: Header) -> List[int]:
    """The logical slots the window still holds, oldest first: the pinned ones and the last turn of the ring."""
    pinned, ring = h.slot_pinned, h.slot_count - h.slot_pinned
    return list(range(min(h.slots_written, pinned))) + list(range(max(pinned, h.slots_written - ring), h.slots_written))


def scan_records(data: bytes, limit: int) -> List[rf.Record]:
    """The records of an area that pass their CRC, nothing else."""
    out = []
    at = data.find(b'#')
    while 0 <= at < limit:
        parsed = rf.parse_at(data, at)
        if parsed:
            out.append(parsed[0])
            at = data.find(b'#', parsed[1])
        else:
            at = data.find(b'#', at + 1)
    return out


def slot_records(slot: bytes, layout: rf.Layout) -> List[rf.Record]:
    records = []
    for _, offset, size, ring in layout.areas:
        data = slot[offset:offset + size]
        extended = data + data[:rf.RING_TAIL] if ring and len(data) == size else data
        records.extend(scan_records(extended, len(data)))
    return records


@dataclasses.dataclass
class UfsLog:
    header: Optional[Header]
    why: str                                # why there is no header
    decoded: rf.Decoded
    run: Optional[int]                      # the ramoops run the slots belong to

    @property
    def blank(self) -> bool:
        return self.why == 'blank'

    def msgbuf(self, window: bytes) -> bytes:
        h = self.header
        if h is None:
            return b''
        start = h.msg_first * BLOCK
        return window[start:start + h.msg_bytes]


def _slots_of_header(window: bytes, h: Header) -> List[Tuple[int, bytes]]:
    size = h.slot_blocks * BLOCK
    out = []
    for logical in live_slots(h):
        start = (h.slot_first + position(h, logical) * h.slot_blocks) * BLOCK
        out.append((logical, window[start:start + size]))
    return out


# A window with no usable header: the geometry UfsLog.c writes.
_SLOT_FIRST, _SLOT_BLOCKS = 33, 16


def _slots_without_header(window: bytes, notes: List[str]) -> Tuple[List[Tuple[int, bytes]], Optional[int]]:
    size = _SLOT_BLOCKS * BLOCK
    found = []
    for at in range(_SLOT_FIRST * BLOCK, len(window) - size + 1, size):
        layout = rf.parse_copy_header(window[at:at + rf.COPY_HEADER])
        if layout is not None:
            found.append(((at - _SLOT_FIRST * BLOCK) // size, layout.run_id, window[at:at + size]))
    if not found or found[0][0] != 0:
        notes.append('no slot with a copy header at position 0: nothing to read without the header')
        return [], None
    run = found[0][1]
    slots = [(position, data) for position, slot_run, data in found if slot_run == run]
    notes.append('no usable header: %d slots of ramoops run 0x%x taken from their own copy headers (others left out: %d); a slot the '
                 'run had not finished may be among them' % (len(slots), run, len(found) - len(slots)))
    return slots, run


def decode_window(window: bytes) -> UfsLog:
    """The merged log of a window (WINDOW_BLOCKS blocks)."""
    notes: List[str] = []
    h, why = parse_header(window[:BLOCK])
    if h is None and why == 'blank' and not window.strip(b'\0'):
        decoded = rf.Decoded([], None, {}, {}, 0, [], {'source': 'ufs', 'copies': 0}, ['the UFS log window is blank: no run wrote here'])
        return UfsLog(None, why, decoded, None)
    if why == 'blank':
        why = 'a blank header block in front of slots'
    if h is None:
        notes.append('UFS log header unusable: %s' % why)
        slots, run = _slots_without_header(window, notes)
    else:
        slots, run = _slots_of_header(window, h), h.ramoops_run
    merged: Dict[int, rf.Record] = {}
    layout = None
    used = wrong_run = no_copy = conflicts = 0
    for logical, slot in slots:
        stated = rf.parse_copy_header(slot[:rf.COPY_HEADER])
        if stated is None:
            no_copy += 1
            if layout is None:
                continue                    # no layout yet to cut it by
        elif stated.run_id != run:
            wrong_run += 1
            continue
        else:
            layout = layout or stated
        used += 1
        for record in slot_records(slot, stated or layout):
            old = merged.get(record.seq)
            if old is None:
                record.source = 'ufs'
                merged[record.seq] = record
            elif (old.ms, old.kind, old.text) != (record.ms, record.kind, record.text):
                conflicts += 1
    if wrong_run:
        notes.append('%d slots hold another ramoops run: left out' % wrong_run)
    if no_copy:
        notes.append('%d slots without an intact copy header%s' % (no_copy, ': read with the layout of the others' if layout else ''))
    if conflicts:
        notes.append('%d records differ between slots: the first was kept' % conflicts)
    records = [merged[k] for k in sorted(merged)]
    missing = [(a.seq + 1, b.seq - 1) for a, b in zip(records, records[1:]) if b.seq > a.seq + 1]
    if h is not None:
        if h.slots_written > h.slot_count:
            notes.append('the ring of slots turned over: %d of %d slots written are left' % (len(slots), h.slots_written))
        if h.state != 2 and h.state in STATES:
            notes.append('state %d: %s' % (h.state, STATES[h.state]))
        if h.state in (3, 4):
            notes.append('the writer stopped; the header on the medium is the last that went through')
        if h.write_errors:
            notes.append('%d rounds failed' % h.write_errors)
        if not h.slots_written:
            notes.append('the run opened the window but finished no round')
    if records and records[0].seq != 1:
        notes.append('the log starts at record %d: earlier ones left the rings before the first round' % records[0].seq)
    stats = {'window': {'slots': len(slots), 'slots_used': used, 'records': len(records)}}
    info = {'source': 'ufs', 'copies': used, 'run_id': run, 'boot_id': h.boot_id if h else None}
    decoded = rf.Decoded(records, layout, stats, {}, 0, missing, info, notes)
    return UfsLog(h, why, decoded, run)


# ---- the export ------------------------------------------------------------------------------------------------------------
def export_stream(blob: bytes) -> bytes:
    """The M2VCACH1 stream of an export blob ("SHEXR001", u32 stream bytes, u32 0, the stream; CacheExport.h)."""
    if len(blob) < 16 or blob[:8] != EXPORT_MAGIC:
        raise FormatError('no %r blob in the export' % EXPORT_MAGIC)
    raw, zero = struct.unpack_from('<II', blob, 8)
    if zero or len(blob) < 16 + raw:
        raise FormatError('export blob truncated')
    stream = blob[16:16 + raw]
    if stream[:8] != STREAM_MAGIC:
        raise FormatError('no %r stream in the export' % STREAM_MAGIC)
    return stream


def _safe(root: Path, relative: str) -> Path:
    """A path of the stream inside root: never out of it, whatever the phone wrote."""
    if not relative or relative.startswith('/') or '\0' in relative or '..' in relative.split('/'):
        raise FormatError('unsafe path in the export: %r' % relative)
    return root / relative


def unpack_stream(stream: bytes, out: Path) -> dict:
    """The stream's files under `out`; every .gz under zz-oslog also as text."""
    def need(at: int, n: int) -> None:
        if n < 0 or at + n > len(stream):
            raise FormatError('export stream truncated')

    at = 8
    need(at, 4)
    (length,) = struct.unpack_from('<I', stream, at)
    at += 4
    need(at, length)
    root = stream[at:at + length].decode('utf-8', 'replace')
    at += length
    need(at, 4)
    (count,) = struct.unpack_from('<I', stream, at)
    at += 4
    files = []
    for _ in range(count):
        need(at, 24)
        plen, mode, uid, gid, size = struct.unpack_from('<IIIIQ', stream, at)
        at += 24
        need(at, plen)
        relative = stream[at:at + plen].decode('utf-8', 'replace')
        at += plen
        need(at, size)
        data = stream[at:at + size]
        at += size
        target = _safe(out, relative)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as error:                    # paths that cannot be files here: "a" and "a/b", a name too long
            raise FormatError('export file %r: %s' % (relative, error)) from error
        entry = dict(path=relative, mode=mode, uid=uid, gid=gid, size=size, sha256=hashlib.sha256(data).hexdigest())
        if relative.startswith('zz-oslog/') and relative.endswith('.gz'):
            try:
                text = gzip.decompress(data)
            except (OSError, EOFError, zlib.error) as error:
                entry['text'] = 'not gzip: %s' % error
            else:
                target.with_suffix('').write_bytes(text)
                entry['text'] = target.with_suffix('').name
        files.append(entry)
    if at != len(stream):
        raise FormatError('trailing bytes after the export stream')
    return dict(directory=root, files=files)


def unpack_export(blob: bytes, directory: Path) -> dict:
    """<directory>/export/ (the cache directory's files, as make-shader-cache-a830.py --merge-export takes them, zz-oslog also as text) and
    export-manifest.json beside it."""
    stream = export_stream(blob)
    manifest = unpack_stream(stream, directory / 'export')
    manifest['blob_sha256'] = hashlib.sha256(blob).hexdigest()
    (directory / 'export-manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    return manifest


# ---- the phone (read-only) ---------------------------------------------------------------------------------------------------
BLANK = 'blank'                             # a window read and found blank: what `before` says to judge()


class PhoneRead:
    """adb + su: blockdev, readlink, cat of sysfs and dd without of=. Android's shell has 32-bit arithmetic, so every block number is worked out
    here."""

    def __init__(self, phone, partition: str = PARTITION):
        self.phone = phone
        self.partition = partition
        self.blocks: Optional[int] = None

    def size(self) -> Optional[int]:
        """The partition's blocks, None when it is not there."""
        out = self.phone.shell('blockdev --getsize64 %s' % self.partition, root=True, check=False)
        self.blocks = int(out) // BLOCK if out.isdigit() and int(out) >= (WINDOW_BLOCKS + EXPORT_BLOCKS) * BLOCK else None
        return self.blocks

    def read(self, skip: int, count: int, timeout: float = 300) -> bytes:
        command = 'dd if=%s bs=%d skip=%d count=%d 2>/dev/null' % (self.partition, BLOCK, skip, count)
        data = self.phone._adb('exec-out', 'su -c %s' % shlex.quote(command), timeout=timeout).stdout
        if len(data) != count * BLOCK:
            raise FormatError('read %d bytes of %d at block %d of %s' % (len(data), count * BLOCK, skip, self.partition))
        return data

    def window(self) -> bytes:
        return self.read(self.blocks - WINDOW_BLOCKS, WINDOW_BLOCKS)

    def before(self) -> Union[Header, str, None]:
        """What the window holds before a run: its header, BLANK for a first block nothing wrote, None for a block that says nothing."""
        h, why = parse_header(self.read(self.blocks - WINDOW_BLOCKS, 1, 30))
        return BLANK if why == 'blank' else h

    def window_lba(self) -> Optional[int]:
        """The window's first absolute LBA by Android's partition table, None when it cannot be told. sysfs keeps a partition's start in
        512-byte sectors, whatever its block size."""
        name = self.phone.shell('readlink -f %s' % self.partition, root=True, check=False).rsplit('/', 1)[-1]
        if self.blocks is None or not re.fullmatch(r'[a-z]+[0-9]+', name):
            return None
        start = self.phone.shell('cat /sys/class/block/%s/start' % name, root=True, check=False)
        return int(start) // 8 + self.blocks - WINDOW_BLOCKS if start.isdigit() and not int(start) % 8 else None

    def export(self, h: Header) -> bytes:
        """The complete export the header names."""
        if h.export_bytes > (h.export_blocks // 2) * BLOCK or h.export_first + h.export_bytes // BLOCK >= h.export_blocks:
            raise FormatError('export out of bounds in the header')
        count = (h.export_bytes + BLOCK - 1) // BLOCK
        region = self.blocks - h.window_blocks - h.export_below
        return self.read(region + h.export_first, count, 1200)[:h.export_bytes]


# ---- is it this run's? ---------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Verdict:
    kind: str                               # current, old, unknown, none
    text: str


def judge(log: UfsLog, before: Union[Header, str, None], pstore_run: Optional[int], first_lba: Optional[int] = None) -> Verdict:
    """Does the window belong to the run being read? `before`: what it held before the run (its header, BLANK, None when that is not known);
    `pstore_run`: the run pstore shows; `first_lba`: where Android's partition table puts it. Only 'current' may go into the run's log."""
    if log.blank or not log.decoded.records and log.header is None:
        return Verdict('none', 'no UFS log (%s)' % log.why)
    h = log.header
    if first_lba is not None and h is not None and h.first_lba != first_lba:
        return Verdict('old', 'UFS LOG AT THE WRONG PLACE: its header says LBA %d, the partition table puts the window at LBA %d: the partition '
                              'moved, or a kernel wrote where it should not. Compare /sys/class/block/*/start with the GPT before the next run' %
                       (h.first_lba, first_lba))
    known = isinstance(before, Header)
    if known and h is not None and h.boot_id == before.boot_id:
        return Verdict('old', 'OLD UFS LOG: boot_id 0x%x is the one the window held before this run, so this run did not write it '
                              '(no sheng-log=1, the window was refused, or it died before the writer started)' % h.boot_id)
    if pstore_run is not None and log.run is not None and pstore_run != log.run:
        return Verdict('old', 'OLD UFS LOG: its ramoops run 0x%x is not the run 0x%x that pstore shows for the last boot' % (log.run, pstore_run))
    if pstore_run is not None and log.run == pstore_run:
        return Verdict('current', 'UFS log and pstore show the same ramoops run 0x%x' % pstore_run)
    if before == BLANK:
        return Verdict('current', 'UFS log: the window was blank before this run')
    if known and h is not None:
        return Verdict('current', 'UFS log: new boot_id 0x%x (0x%x before this run)' % (h.boot_id, before.boot_id))
    return Verdict('unknown', 'UFS log of ramoops run %s: nothing to compare it with, so it may be older than the last run: it stays in ufs/, '
                              'out of the merged log' % ('?' if log.run is None else '0x%x' % log.run))


# ---- evidence ------------------------------------------------------------------------------------------------------------------
def write_log_files(directory: Path, decoded: rf.Decoded, extra: dict, head: str = '') -> None:
    """records.jsonl, merged.log, summary.txt and decode.json, as read-ramoops.py writes them."""
    with (directory / 'records.jsonl').open('w') as stream:
        for record in decoded.records:
            stream.write(json.dumps(record.as_json(), ensure_ascii=True) + '\n')
    (directory / 'merged.log').write_text(rf.render(decoded.records))
    (directory / 'summary.txt').write_text(head + rf.summary(decoded))
    info = dict(extra)
    info['decode'] = decoded.summary_stats()
    (directory / 'decode.json').write_text(json.dumps(info, indent=2, sort_keys=True, default=str) + '\n')


def write_evidence(directory: Path, window: bytes, log: UfsLog, verdict: Verdict, export: Optional[bytes] = None,
                   extra: Optional[dict] = None) -> None:
    """One UFS log as an evidence directory: the log files, msgbuf.txt, the header, the window (gzip) and export/."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'ufs-window.bin.gz').write_bytes(gzip.compress(window, 1))
    h = log.header
    head = (h.describe() if h else 'UFS log without a usable header: %s' % log.why) + '\n' + verdict.text + '\n\n'
    info = dict(extra or {})
    info['ufs'] = dataclasses.asdict(h) if h else {'why': log.why}
    info['verdict'] = dataclasses.asdict(verdict)
    if h:
        (directory / 'ufs-header.json').write_text(json.dumps(dataclasses.asdict(h), indent=1) + '\n')
        (directory / 'msgbuf.txt').write_bytes(log.msgbuf(window))
    if export is not None:
        try:
            manifest = unpack_export(export, directory)
        except (FormatError, OSError) as error:
            (directory / 'export.bin').write_bytes(export)
            log.decoded.notes.append('the export did not unpack: %s (the blob is in export.bin)' % error)
        else:
            info['export'] = dict(files=len(manifest['files']), directory=manifest['directory'], bytes=len(export))
    write_log_files(directory, log.decoded, info, head)


def combined(ufs: rf.Decoded, pstore: rf.Decoded) -> rf.Decoded:
    """The UFS log first and, of the same run, what only pstore still has (the seconds after the last round, the reset's records)."""
    merged = {record.seq: record for record in ufs.records}
    added = 0
    for record in pstore.records:
        if record.seq not in merged and record.verified:
            merged[record.seq] = dataclasses.replace(record, source='pstore:' + record.source)
            added += 1
    records = [merged[k] for k in sorted(merged)]
    missing = [(a.seq + 1, b.seq - 1) for a, b in zip(records, records[1:]) if b.seq > a.seq + 1]
    notes = list(ufs.notes) + ['%d records added from pstore' % added]
    return rf.Decoded(records, ufs.layout or pstore.layout, dict(ufs.copies, **pstore.copies), pstore.repaired, 0, missing, ufs.vote, notes)


def promote(directory: Path, ufs: rf.Decoded, pstore: rf.Decoded, extra: dict, head: str) -> rf.Decoded:
    """The run directory's log files become the merged log; what read-ramoops.py wrote there moves to pstore/."""
    keep = directory / 'pstore'
    keep.mkdir(exist_ok=True)
    for name in ('records.jsonl', 'merged.log', 'summary.txt', 'decode.json'):
        path = directory / name
        if path.exists():
            path.replace(keep / name)
    decoded = combined(ufs, pstore) if pstore.records else ufs
    write_log_files(directory, decoded, extra, head)
    return decoded


def read_export(reader: PhoneRead, log: UfsLog, report) -> Optional[bytes]:
    """The export the header names, None when it names none or the read fails: a failure costs the export, not the log."""
    if not log.header or not log.header.export_bytes:
        return None
    try:
        return reader.export(log.header)
    except (rd.ToolError, FormatError) as error:
        report('UFS log export not read: %s' % error)
        return None


def read_beside_pstore(phone, directory: Path, decoded: rf.Decoded, before: Union[Header, str, None], extra: dict, report) -> rf.Decoded:
    """What gpu-loop.py and read-ramoops.py do after reading pstore: the window into <directory>/ufs/ and, when it is this run's, the merged log
    at the top of the directory. Returns the log to print. A failure here leaves the pstore evidence as it is. `report` takes one line of
    text."""
    try:
        reader = PhoneRead(phone)
        if reader.size() is None:
            return decoded
        window = reader.window()
        place = reader.window_lba()
    except (rd.ToolError, FormatError) as error:
        report('UFS log not read: %s' % error)
        return decoded
    log = decode_window(window)
    verdict = judge(log, before, decoded.vote.get('run_id'), place)
    if verdict.kind == 'none':
        return decoded
    write_evidence(directory / 'ufs', window, log, verdict, read_export(reader, log, report) if verdict.kind != 'old' else None, extra)
    report(('' if verdict.kind == 'current' else 'WARNING: ') + verdict.text)
    if log.header:
        report(log.header.describe())
    if verdict.kind != 'current':
        return decoded
    head = (log.header.describe() + '\n' if log.header else '') + verdict.text + '\n\n'
    return promote(directory, log.decoded, decoded, dict(extra, ufs=dict(records=len(log.decoded.records), verdict=verdict.text)), head)
