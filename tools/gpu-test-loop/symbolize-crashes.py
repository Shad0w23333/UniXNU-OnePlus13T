#!/usr/bin/env python3
"""Name the addresses of the crash reports (X records) in a UniXNU ramoops log.

XNU with the device repository's patches/xnu/crash-trace.patch and BootProgress CrashTrace.h logs, for a process killed by a
fatal signal (SIGILL SIGTRAP SIGABRT SIGEMT SIGFPE SIGBUS SIGSEGV SIGSYS), the registers of the thread taking the signal and
up to 16 return addresses of its frame chain: a process name at most once in 10 s, a trace repeated within 30 s as one
"same" record. Addresses are tokens: c:<unslid dyld shared cache address>, m:<offset from the main executable's header>,
<letter>:<offset from that image's header>, <letter>@<file offset in that image>, ?:<raw address>. This script reads
merged.log (or a run directory), groups the records by pid, joins each with its S exit record and names the addresses:

  c:            from the 21H461 dyld shared cache: --a2s FILE (ipsw's address-to-symbol cache, "A2S" binary, searched in place
                without loading it; or a text table "<hex address> <symbol>", read once as a stream), and/or --dsc
                PATH/dyld_shared_cache_arm64 (`ipsw dyld a2f`: the containing function and its image; the cache's image list
                names the image of every address even without symbols). --systemos-dmg FILE attaches the Cryptex1,SystemOS
                image read-only for --dsc and detaches it at the end. Without --dsc and with ipsw at hand, a missing --a2s
                file is created by the first a2f run (about 40 s, 0.9 GB).
  m: <x>: <x>@  from Mach-O files: --image NAME=PATH, or --root DIR (searched by file name: a mounted OS volume, the
                NativeAdrenoMetal.bundle build directory, ...); fat files use their arm64 slice.

    symbolize-crashes.py runs/20261030-154643-phone-pack-1 --a2s dsc.a2s
    symbolize-crashes.py merged.log --dsc /Volumes/SystemOS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64
    symbolize-crashes.py RUN --a2s dsc.a2s --root UniXNU/build/adreno-metal-plugin --json crashes.json

Return addresses are looked up one byte before the address (the call), the pc at the address itself. Prints every crash with
its decoded signal, Mach exception and ESR, the named frames and the first frame outside the abort/exception machinery,
then the fatal exits per process from the S records (also those that have no X records). Exit 0, or 3 when the log has no
crash report at all.

The stall probe's samples (Q records, BootProgress StallProbe.h) are named the same way, after the crashes. A raw address of a process with Q img
records becomes a file offset in that file; k: tokens (the low 32 bits of an unslid kernel address) need --kernel PATH/kernel.macho.
"""
import argparse
import bisect
import collections
import json
import mmap
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stall_format  # noqa: E402

SIGNALS = {4: 'SIGILL', 5: 'SIGTRAP', 6: 'SIGABRT', 7: 'SIGEMT', 8: 'SIGFPE', 9: 'SIGKILL', 10: 'SIGBUS', 11: 'SIGSEGV',
           12: 'SIGSYS', 13: 'SIGPIPE', 14: 'SIGALRM', 15: 'SIGTERM', 24: 'SIGXCPU', 25: 'SIGXFSZ'}
FATAL = {4, 5, 6, 7, 8, 10, 11, 12}
EXCEPTIONS = {1: 'EXC_BAD_ACCESS', 2: 'EXC_BAD_INSTRUCTION', 3: 'EXC_ARITHMETIC', 4: 'EXC_EMULATION', 5: 'EXC_SOFTWARE',
              6: 'EXC_BREAKPOINT', 7: 'EXC_SYSCALL', 8: 'EXC_MACH_SYSCALL', 9: 'EXC_RPC_ALERT', 10: 'EXC_CRASH',
              11: 'EXC_RESOURCE', 12: 'EXC_GUARD', 13: 'EXC_CORPSE_NOTIFY'}
ACCESS_CODES = {1: 'KERN_INVALID_ADDRESS', 2: 'KERN_PROTECTION_FAILURE', 0x101: 'EXC_ARM_DA_ALIGN',
                0x102: 'EXC_ARM_DA_DEBUG', 0x103: 'EXC_ARM_SP_ALIGN', 0x104: 'EXC_ARM_SWP', 0x105: 'EXC_ARM_PAC_FAIL'}
EC = {0x00: 'unknown reason', 0x01: 'WFI/WFE', 0x07: 'SIMD/FP access', 0x0e: 'illegal execution state',
      0x15: 'SVC (a system call: the signal was sent, e.g. by abort -> pthread_kill)', 0x18: 'MSR/MRS trap',
      0x20: 'instruction abort from EL0', 0x21: 'instruction abort in the kernel', 0x22: 'PC alignment fault',
      0x24: 'data abort from EL0', 0x25: 'data abort in the kernel', 0x26: 'SP alignment fault',
      0x2c: 'floating-point exception', 0x30: 'breakpoint from EL0', 0x32: 'software step from EL0',
      0x34: 'watchpoint from EL0', 0x3c: 'BRK instruction'}
# Frames of the abort and exception machinery: the first frame after them is usually where the crash was decided.
MACHINERY_IMAGES = ('libsystem_kernel.dylib', 'libsystem_pthread.dylib', 'libsystem_c.dylib', 'libc++abi.dylib',
                    'libobjc.A.dylib', 'libdyld.dylib', 'libsystem_platform.dylib')
MACHINERY_SYMBOLS = ('abort', '__abort', 'pthread_kill', '__pthread_kill', 'abort_message', 'demangling_terminate_handler',
                     '_objc_terminate', 'std::__terminate', '__cxa_throw', '__cxa_rethrow', 'objc_exception_throw',
                     'objc_exception_rethrow', '__exceptionPreprocess', '-[NSException raise]', '+[NSException raise:format:',
                     '_CFThrowFormattedException', '__CFThrowFormattedException', '_os_crash', '__assert_rtn',
                     '_swift_runtime_on_report', 'swift::fatalError', '$ss17_assertionFailure')
LINE = re.compile(r'^\s*(\d+)\s+(\d+\.\d+)\s(\S)([ ?]) (.*)$')
TOKEN = re.compile(r'^([a-z?]):([0-9a-f]+)$|^([a-z])@([0-9a-f]+)$')
PREFIX = re.compile(r'^(crash|regs|map|img|bt|same) pid=(-?\d+)(?: (.*))?$')
EXIT = re.compile(r'^exit pid=(-?\d+) (.*?) status=([0-9a-f]+) ns=(\d+) code=([0-9a-f]+)$')


# ---- reading the log -------------------------------------------------------------------------------------------------------
def read_log(path: Path):
    """(seq, seconds, kind, verified, text) of every line of merged.log."""
    out = []
    for raw in path.read_text(errors='replace').splitlines():
        m = LINE.match(raw)
        if m:
            out.append((int(m.group(1)), float(m.group(2)), m.group(3), m.group(4) == ' ', m.group(5)))
    return out


def fields(text):
    """key=value pairs of a record (the first bare words go to '_words')."""
    out = {'_words': []}
    for word in text.split():
        key, sep, value = word.partition('=')
        if sep:
            out[key] = value
        else:
            out['_words'].append(word)
    return out


def hexint(value, default=None):
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return default


def parse_token(token):
    """(kind, letter, value): kind c / m / image / file / raw."""
    m = TOKEN.match(token or '')
    if not m:
        return None
    if m.group(3):
        return ('file', m.group(3), int(m.group(4), 16))
    letter, value = m.group(1), int(m.group(2), 16)
    if letter == 'c':
        return ('cache', 'c', value)
    if letter == 'm':
        return ('main', 'm', value)
    if letter == '?':
        return ('raw', '?', value)
    return ('image', letter, value)


class Crash:
    def __init__(self, pid):
        self.pid = pid
        self.name = '?'
        self.seconds = None
        self.verified = True
        self.info = {}
        self.regs = {}
        self.map = {}
        self.images = {}            # letter -> file name ('m' the main executable)
        self.frames = []            # tokens, in order
        self.end = None
        self.same_as = None         # a "same" record: the pid of the full report it repeats
        self.hash = None
        self.skipped = 0
        self.exit = None            # (seconds, status, namespace, code) of the S exit record
        self.repeats = []           # pids of "same" records that point here


def collect(records):
    """Crash reports by pid (in log order) and the fatal exits of the S records."""
    crashes = collections.OrderedDict()
    exits = []
    for seq, seconds, kind, verified, text in records:
        if kind == 'S':
            m = EXIT.match(text)
            if m:
                exits.append({'pid': int(m.group(1)), 'name': m.group(2), 'status': int(m.group(3), 16),
                              'namespace': int(m.group(4)), 'code': int(m.group(5), 16), 'seconds': seconds,
                              'verified': verified})
            continue
        if kind != 'X':
            continue
        m = PREFIX.match(text)
        if not m:
            continue
        tag, pid, rest = m.group(1), int(m.group(2)), m.group(3) or ''
        crash = crashes.setdefault(pid, Crash(pid))
        crash.verified &= verified
        if crash.seconds is None:
            crash.seconds = seconds
        f = fields(rest)
        if tag in ('crash', 'same'):
            crash.name = f['_words'][0] if f['_words'] else '?'
            crash.info = f
            crash.hash = f.get('hash')
            if tag == 'same':
                crash.same_as = int(f.get('as', '0') or 0)
                crash.skipped = int(f.get('skipped', '0') or 0)
        elif tag == 'regs':
            crash.regs = f
        elif tag == 'map':
            crash.map = f
            crash.hash = f.get('hash', crash.hash)
            crash.skipped = int(f.get('skipped', '0') or 0)
        elif tag == 'img':
            for key, value in f.items():
                if key != '_words' and len(key) == 1:
                    crash.images[key] = value
        elif tag == 'bt':
            words = rest.split()
            for word in words[1:]:
                if word.startswith('end='):
                    crash.end = word[4:]
                else:
                    crash.frames.append(word)
    for exit_record in exits:
        crash = crashes.get(exit_record['pid'])
        if crash and crash.exit is None:
            crash.exit = exit_record
    for crash in crashes.values():
        if crash.same_as is not None and crash.same_as in crashes:
            crashes[crash.same_as].repeats.append(crash.pid)
    return crashes, exits


# ---- symbol sources --------------------------------------------------------------------------------------------------------
class A2SBinary:
    """ipsw's address-to-symbol cache: "A2S" + version 2, u32 count, u32 string bytes, count x {u64 address, u32 string
    offset} sorted by address, then the NUL-terminated strings. Searched in place (mmap), never loaded."""
    HEADER = 12
    ENTRY = 12

    def __init__(self, path: Path):
        self.file = path.open('rb')
        self.map = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        if self.map[:3] != b'A2S':
            raise ValueError('%s: not an ipsw A2S file' % path)
        self.count, self.string_bytes = struct.unpack_from('<II', self.map, 4)
        self.strings = self.HEADER + self.ENTRY * self.count
        if self.strings + self.string_bytes != len(self.map):
            raise ValueError('%s: A2S sizes do not add up (version %d?)' % (path, self.map[3]))

    def entry(self, index):
        return struct.unpack_from('<QI', self.map, self.HEADER + self.ENTRY * index)

    def name(self, offset):
        start = self.strings + offset
        end = self.map.find(b'\0', start, min(len(self.map), start + 4096))
        return self.map[start:end if end >= 0 else start + 4096].decode('utf-8', 'replace')

    def lookup(self, address):
        """(symbol address, name) of the last symbol at or below address, or None."""
        lo, hi = 0, self.count
        while lo < hi:
            mid = (lo + hi) // 2
            if self.entry(mid)[0] <= address:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        start, offset = self.entry(lo - 1)
        return start, self.name(offset)

    def resolve(self, addresses):
        return {a: self.lookup(a) for a in addresses}


class A2SText:
    """A text table, one "<hex address> <symbol>" per line, in any order: read once; for every queried address the
    symbol with the largest address at or below it."""

    def __init__(self, path: Path):
        self.path = path

    def resolve(self, addresses):
        queries = sorted(set(addresses))
        best = [None] * len(queries)        # per bucket k: the largest symbol with queries[k-1] < address <= queries[k]
        with self.path.open('r', errors='replace') as stream:
            for line in stream:
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                address = hexint(parts[0][2:] if parts[0].lower().startswith('0x') else parts[0])
                if address is None:
                    continue
                k = bisect.bisect_left(queries, address)
                if k < len(queries) and (best[k] is None or best[k][0] < address):
                    best[k] = (address, parts[1].strip())
        out, last = {}, None
        for k, query in enumerate(queries):
            if best[k] is not None:
                last = best[k]
            out[query] = last
        return out


def open_a2s(path: Path):
    with path.open('rb') as stream:
        head = stream.read(4)
    return A2SBinary(path) if head[:3] == b'A2S' else A2SText(path)


class CacheImages:
    """The dyld shared cache's image list (dyld_cache_header imagesTextOffset/Count: uuid, load address, __TEXT size,
    path offset), from the main cache file."""

    def __init__(self, path: Path):
        data = path.read_bytes()
        if not data.startswith(b'dyld_v1'):
            raise ValueError('%s: not a dyld shared cache' % path)
        offset, count = struct.unpack_from('<QQ', data, 136)
        images = []
        for i in range(count):
            _, load, size, path_offset = struct.unpack_from('<16sQII', data, offset + 32 * i)
            end = data.find(b'\0', path_offset)
            images.append((load, size, data[path_offset:end].decode('utf-8', 'replace')))
        images.sort()
        self.starts = [image[0] for image in images]
        self.images = images

    def range(self, address):
        """(load address, __TEXT size, path) of the image whose __TEXT holds address, or None."""
        k = bisect.bisect_right(self.starts, address) - 1
        if k >= 0 and address < self.images[k][0] + self.images[k][1]:
            return self.images[k]
        return None

    def image(self, address):
        found = self.range(address)
        return found[2] if found else None


def ipsw_a2f(ipsw, dsc: Path, addresses, cache: Path = None):
    """`ipsw dyld a2f` for many addresses: {address: (start, name, image path)}. One batch run; when it fails (a single
    address outside every dylib fails the batch) one run per address."""
    out = {}
    addresses = sorted(set(addresses))
    if not addresses:
        return out
    with tempfile.TemporaryDirectory(prefix='symbolize-') as temporary:
        listing = Path(temporary) / 'addresses.txt'
        listing.write_text(''.join('0x%x\n' % a for a in addresses))
        base = [ipsw, 'dyld', 'a2f', str(dsc), '--json', '--no-color']
        if cache:
            base += ['--cache', str(cache)]
        result = subprocess.run(base + ['--in', str(listing)], capture_output=True, text=True)
        rows = None
        if result.returncode == 0:
            try:
                rows = json.loads(result.stdout)
            except ValueError:
                rows = None
        if rows is None:
            rows = []
            for address in addresses:
                one = subprocess.run(base + ['0x%x' % address], capture_output=True, text=True)
                if one.returncode == 0:
                    try:
                        value = json.loads(one.stdout)
                    except ValueError:
                        continue
                    rows.extend(value if isinstance(value, list) else [value])
    for row in rows or []:
        try:
            out[int(row['addr'])] = (int(row['start']), row.get('name') or '?', row.get('image') or '')
        except (KeyError, TypeError, ValueError):
            continue
    return out


class MachO:
    """Symbols of a Mach-O file (thin or fat; the arm64 slice, else the first): segments and defined section symbols."""
    CPU_ARM64 = 0x0100000c

    def __init__(self, path: Path):
        data = path.read_bytes()
        self.path = path
        self.slice = 0
        magic = struct.unpack_from('>I', data, 0)[0]
        if magic in (0xcafebabe, 0xcafebabf):
            count = struct.unpack_from('>I', data, 4)[0]
            wide = magic == 0xcafebabf
            choices = []
            for i in range(count):
                if wide:
                    cpu, sub, offset, size = struct.unpack_from('>iiQQ', data, 8 + 32 * i)
                else:
                    cpu, sub, offset, size = struct.unpack_from('>iiII', data, 8 + 20 * i)
                choices.append((cpu != self.CPU_ARM64, (sub & 0xffffff) == 2, offset, size))
            choices.sort()
            self.slice = choices[0][2]
            data = data[self.slice:self.slice + choices[0][3]]
        if struct.unpack_from('<I', data, 0)[0] != 0xfeedfacf:
            raise ValueError('%s: not a 64-bit Mach-O' % path)
        ncmds = struct.unpack_from('<I', data, 16)[0]
        at = 32
        self.segments = []
        symtab = None
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, at)
            if cmd == 0x19:
                name = data[at + 8:at + 24].split(b'\0')[0].decode()
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, at + 24)
                self.segments.append((name, vmaddr, vmsize, fileoff, filesize))
            elif cmd == 0x2:
                symtab = struct.unpack_from('<IIII', data, at + 8)
            at += size
        text = [s for s in self.segments if s[0] == '__TEXT']
        self.base = text[0][1] if text else 0
        symbols = []
        if symtab:
            symoff, nsyms, stroff, strsize = symtab
            for i in range(nsyms):
                strx, ntype, sect, _, value = struct.unpack_from('<IBBHQ', data, symoff + 16 * i)
                if ntype & 0xe0 or (ntype & 0x0e) != 0x0e or not sect:
                    continue
                end = data.find(b'\0', stroff + strx)
                symbols.append((value, data[stroff + strx:end].decode('utf-8', 'replace')))
        symbols.sort()
        self.symbol_starts = [s[0] for s in symbols]
        self.symbols = symbols

    def vmaddr(self, kind, value):
        """The VM address of an offset from the header (kind 'main'/'image') or of a file offset (kind 'file')."""
        if kind in ('main', 'image'):
            return self.base + value
        inside = value - self.slice
        for name, vmaddr, vmsize, fileoff, filesize in self.segments:
            if fileoff <= inside < fileoff + filesize:
                return vmaddr + inside - fileoff
        return None

    def lookup(self, vmaddr):
        k = bisect.bisect_right(self.symbol_starts, vmaddr) - 1
        return self.symbols[k] if k >= 0 else None


class Files:
    """Mach-O files by name: --image NAME=PATH first, then a walk of the --root directories (once, lazily)."""

    def __init__(self, explicit, roots):
        self.explicit = dict(explicit)
        self.roots = roots
        self.index = None
        self.parsed = {}

    def find(self, name):
        if name in self.explicit:
            return Path(self.explicit[name])
        if self.index is None:
            self.index = {}
            for root in self.roots:
                for directory, _, files in os.walk(root):
                    for file in files:
                        self.index.setdefault(file, Path(directory) / file)
        return self.index.get(name)

    def macho(self, name):
        if name not in self.parsed:
            path = self.find(name)
            try:
                self.parsed[name] = MachO(path) if path and path.is_file() else None
            except (ValueError, struct.error, OSError):
                self.parsed[name] = None
        return self.parsed[name]


# ---- naming ----------------------------------------------------------------------------------------------------------------
def esr_text(esr):
    if esr is None:
        return ''
    ec = (esr >> 26) & 0x3f
    text = EC.get(ec, 'class 0x%02x' % ec)
    iss = esr & 0x1ffffff
    if ec in (0x20, 0x21, 0x24, 0x25):
        fsc = iss & 0x3f
        kinds = {0: 'address size fault', 1: 'translation fault', 2: 'access flag fault', 3: 'permission fault'}
        if fsc >> 2 in kinds:
            text += ', %s level %d' % (kinds[fsc >> 2], fsc & 3)
        elif fsc == 0x10:
            text += ', synchronous external abort'
        elif fsc == 0x21:
            text += ', alignment fault'
        else:
            text += ', fault status 0x%02x' % fsc
        if ec in (0x24, 0x25):
            text += ', write' if iss & 0x40 else ', read'
    elif ec == 0x3c:
        comment = iss & 0xffff
        text += ' #0x%x' % comment + (' (Swift/compiler trap)' if comment == 1 else
                                      ' (pointer authentication failure)' if comment & 0xfff0 == 0xc470 else '')
    return text


def signal_text(crash):
    info = crash.info
    signal = int(info.get('sig', '0') or 0)
    parts = ['%s (%d)' % (SIGNALS.get(signal, 'signal'), signal)]
    exception = int(info.get('exc', '0') or 0)
    esr = hexint((info.get('esr') or '0x0')[2:])
    syscall = esr is not None and ((esr >> 26) & 0x3f) == 0x15
    if exception and not syscall:
        code = hexint((info.get('code') or '0x0')[2:], 0)
        sub = hexint((info.get('sub') or '0x0')[2:], 0)
        name = EXCEPTIONS.get(exception, 'exception %d' % exception)
        detail = ACCESS_CODES.get(code, '0x%x' % code) if exception == 1 else '0x%x' % code
        parts.append('%s %s at 0x%x' % (name, detail, sub) if exception == 1 else '%s code %s sub 0x%x' % (name, detail, sub))
    return ', '.join(parts)


MAX_SYMBOL_DISTANCE = 0x100000     # a nearest symbol further below the address than this names nothing (a gap)


class Namer:
    def __init__(self, a2s=None, dsc=None, images=None, a2f=None, files=None):
        self.a2s, self.dsc, self.cache_images, self.a2f, self.files = a2s, dsc, images, a2f or {}, files
        self.a2s_names = {}

    def prepare(self, crashes):
        """Look every shared cache address up once (the a2s table is read or searched only here)."""
        wanted = set()
        for crash in crashes.values():
            for token, is_return in self.tokens(crash):
                parsed = parse_token(token)
                if parsed and parsed[0] == 'cache':
                    wanted.add(parsed[2] - 1 if is_return else parsed[2])
        if self.a2s is not None and wanted:
            self.a2s_names = self.a2s.resolve(sorted(wanted))
        return wanted

    @staticmethod
    def tokens(crash):
        out = []
        if crash.regs.get('pc'):
            out.append((crash.regs['pc'], False))
        if crash.regs.get('lr'):
            out.append((crash.regs['lr'], True))
        out.extend((frame, True) for frame in crash.frames)
        return out

    def name(self, crash, token, is_return):
        """(image, symbol + offset, symbol) of a token; empty strings for what cannot be named."""
        parsed = parse_token(token)
        if not parsed:
            return '', '', ''
        kind, letter, value = parsed
        if kind == 'raw':
            return '', '', ''
        if kind == 'cache':
            query = value - 1 if is_return else value
            if query in self.a2f:
                start, symbol, path = self.a2f[query]
                return os.path.basename(path), '%s + %d' % (symbol, value - start), symbol
            span = self.cache_images.range(query) if self.cache_images else None
            image = os.path.basename(span[2]) if span else ''
            found = self.a2s_names.get(query)
            if found and value - found[0] <= MAX_SYMBOL_DISTANCE and (not span or found[0] >= span[0]):
                return image, '%s + %d' % (found[1], value - found[0]), found[1]
            return image, '', ''
        file_name = crash.images.get(letter) or (crash.name if letter == 'm' else '')
        macho = self.files.macho(file_name) if self.files and file_name else None
        if not macho:
            return file_name, '', ''
        vmaddr = macho.vmaddr(kind, value)
        if vmaddr is None:
            return file_name, '', ''
        found = macho.lookup(vmaddr - 1 if is_return else vmaddr)
        if not found or vmaddr - found[0] > MAX_SYMBOL_DISTANCE:
            return file_name, '', ''
        return file_name, '%s + %d' % (found[1], vmaddr - found[0]), found[1]


class StallThread:
    """A thread of a stall sample with what Namer needs of a crash: the images by letter, its tokens with raw addresses turned into file offsets."""

    def __init__(self, run, sample, thread):
        self.thread = thread
        self.sample = sample
        self.name = thread.name
        self.images = {}
        mappings = run.images.get(thread.pid, [])
        self.regs = {'pc': self.token(thread.pc, mappings), 'lr': self.token(thread.lr, mappings)}
        self.frames = [self.token(frame, mappings) for frame in thread.frames]

    def token(self, token, mappings):
        parsed = parse_token(token or '')
        if not parsed or parsed[0] != 'raw':
            return token
        for mapping in mappings:
            if mapping['start'] <= parsed[2] < mapping['end']:
                letter = next((k for k, v in self.images.items() if v == mapping['name']), None)
                if letter is None:
                    letter = chr(ord('a') + len(self.images))
                    self.images[letter] = mapping['name']
                return '%s@%x' % (letter, parsed[2] - mapping['start'] + mapping['offset'])
        return token


def stall_threads(run):
    return {(sample.id, tag): StallThread(run, sample, thread) for sample in run.samples for tag, thread in sample.threads.items()}


def kernel_name(kernel, token):
    """The symbol of a k: token, '' without --kernel."""
    if not kernel or not token or not token.startswith('k:'):
        return ''
    address = 0xfffffff000000000 | int(token[2:], 16)
    found = kernel.lookup(address - 1)
    return '%s + %d' % (found[1], address - found[0]) if found and address - found[0] <= MAX_SYMBOL_DISTANCE else ''


def describe_stalls(run, threads, namer, kernel=None):
    lines = []
    for sample in run.samples:
        lines.append('')
        lines.append('sample %d at %.3f s: %s stall %s #%s, %s ms in%s%s' % (
            sample.id, sample.seconds, sample.why, sample.stall, sample.n, sample.age,
            ' (%s ms since the newest frame, sign %s%s)' % (sample.since, sample.asked, '' if sample.focal is None else ', %s focal' % sample.focal)
            if sample.since is not None else '',
            ' (%s pid %s, %s open)' % (sample.name, sample.pid, sample.open) if sample.pid else ''))
        busy = [cpu for cpu in sample.cpus.values() if cpu.name]
        idle = sum(1 for cpu in sample.cpus.values() if cpu.idle)
        lines.append('  CPUs: %d idle, runq %s rt %s; %s' % (idle, sample.rq, sample.rt, ', '.join(
            '%d:%s/%s' % (cpu.index, cpu.name, cpu.pri) for cpu in busy)))
        for tag in sample.missing:
            lines.append('  %s: no such thread now' % tag)
        for tag, thread in sample.threads.items():
            named = threads[(sample.id, tag)]
            lines.append('  %s %s[%s] tid %s: %s for %s ms on %s%s%s' % (
                tag, thread.name, thread.pid, thread.tid, stall_format.STATES.get(thread.state, thread.state), thread.wait_ms, thread.cpu,
                ' (cross-call %s)' % thread.xcall if thread.xcall else '', '; ' + thread.wait_text() if thread.wait_text() else ''))
            for label, token in (('kpc', thread.kpc), ('cont', thread.continuation)):
                if token:
                    lines.append('    %-4s %-16s %s' % (label, token, kernel_name(kernel, token)))
            for index, token in enumerate(thread.kernel):
                lines.append(('    k%-3d %-16s %s' % (index, token, kernel_name(kernel, token))).rstrip())

            def add(label, token, is_return):
                image, symbol, _ = namer.name(named, token, is_return)
                lines.append(('    %-4s %-16s %-28s %s' % (label, token, image or '-', symbol or '')).rstrip())
            if named.regs.get('pc'):
                add('pc', named.regs['pc'], False)
            if named.regs.get('lr'):
                add('lr', named.regs['lr'], True)
            for index, token in enumerate(named.frames):
                add('#%d' % (index + 1), token, True)
            if thread.end:
                lines.append('    frame chain: %s' % ('complete' if thread.end == 'end' else 'stopped: %s' % thread.end))
    return lines


def machinery(image, symbol):
    if image in MACHINERY_IMAGES:
        return True
    return any(symbol.startswith(name) or symbol.lstrip('_').startswith(name.lstrip('_')) for name in MACHINERY_SYMBOLS)


def describe(crash, namer, crashes):
    lines, record = [], {'pid': crash.pid, 'name': crash.name, 'seconds': crash.seconds, 'verified': crash.verified}
    exit_text = ''
    if crash.exit:
        exit_text = '; exit status=%x at %.3f s' % (crash.exit['status'], crash.exit['seconds'])
        record['exit'] = crash.exit
    head = '%s pid %d at %.3f s' % (crash.name, crash.pid, crash.seconds or 0.0)
    if crash.same_as is not None:
        lines.append('%s: same trace as pid %d (hash %s), %d crash(es) of this name left out before it%s%s' % (
            head, crash.same_as, crash.hash, crash.skipped, exit_text, '' if crash.verified else '  [unverified record]'))
        record.update(same_as=crash.same_as, hash=crash.hash, skipped=crash.skipped)
        return lines, record
    info = crash.info
    lines.append('%s: %s%s%s' % (head, signal_text(crash), exit_text, '' if crash.verified else '  [unverified records]'))
    esr = hexint((info.get('esr') or '')[2:])
    lines.append('  esr 0x%x: %s; far %s; thread %s' % (esr or 0, esr_text(esr), info.get('far', '?'), info.get('tid', '?')))
    if crash.map:
        lines.append('  slide %s, main executable at %s, trace hash %s, %s crash(es) of this name left out before it%s' % (
            crash.map.get('slide', '?'), crash.map.get('main', '?'), crash.map.get('hash', '?'), crash.map.get('skipped', '0'),
            ', Swift async frames %s' % crash.map['async'] if crash.map.get('async') else ''))
    if crash.images:
        lines.append('  images: ' + ', '.join('%s=%s' % kv for kv in sorted(crash.images.items())))
    frames = []

    def add(label, token, is_return):
        image, symbol, function = namer.name(crash, token, is_return)
        frames.append({'label': label, 'token': token, 'image': image, 'symbol': symbol, 'function': function})
        lines.append(('  %-4s %-16s %-28s %s' % (label, token, image or '-', symbol or '')).rstrip())

    add('pc', crash.regs.get('pc', '?'), False)
    add('lr', crash.regs.get('lr', '?'), True)
    for k, token in enumerate(crash.frames):
        add('#%d' % (k + 1), token, True)
    pc, lr = frames[0], frames[1]
    if lr['function'] and lr['function'] == pc['function']:
        lines.append('       (lr is inside the crashing function: the stale return address of its last call)')
    elif crash.frames and lr['token'] != crash.frames[0] and lr['function'] and lr['function'] != frames[2]['function']:
        lines.append('       (lr is not in the chain: the caller when the crashing function is a leaf, else stale)')
    x1 = crash.regs.get('x1', '')
    if x1.startswith('c:'):
        image, _, _ = namer.name(crash, x1, False)
        lines.append('  x0 %s, x1 %s (in the shared cache%s: a selector when the pc is in objc_msgSend)' % (
            crash.regs.get('x0', '?'), x1, ' ' + image if image else ''))
    else:
        lines.append('  x0 %s, x1 %s, fp %s, sp %s' % (crash.regs.get('x0', '?'), x1 or '?', crash.regs.get('fp', '?'),
                                                       crash.regs.get('sp', '?')))
    lines.append('  frame chain: %s' % {'end': 'complete (reached the first frame)', None: 'no bt record'}.get(
        crash.end, 'stopped: %s' % crash.end))
    first = next((f for f in frames if f['symbol'] and not machinery(f['image'], f['symbol'])), None)
    if first and first is not frames[0]:
        lines.append('  first frame outside the abort/exception machinery: %s %s %s' % (first['label'], first['image'],
                                                                                    first['symbol']))
    if crash.repeats:
        lines.append('  repeated by: pid ' + ', '.join(str(p) for p in crash.repeats))
    record.update(info={k: v for k, v in info.items() if k != '_words'}, regs={k: v for k, v in crash.regs.items() if k != '_words'},
                  map={k: v for k, v in crash.map.items() if k != '_words'}, images=crash.images, frames=frames, end=crash.end,
                  repeats=crash.repeats)
    return lines, record


def exit_summary(exits):
    lines = []
    by_name = collections.OrderedDict()
    for e in exits:
        signal = e['status'] & 0x7f
        if e['namespace'] == 2 or signal in FATAL or signal == 9:
            by_name.setdefault((e['name'], e['status']), []).append(e)
    for (name, status), items in by_name.items():
        signal = status & 0x7f
        lines.append('  %-17s status=%-3x %-8s x%-3d %s .. %s s, pids %s' % (
            name, status, SIGNALS.get(signal, ''), len(items), '%.1f' % items[0]['seconds'], '%.1f' % items[-1]['seconds'],
            ' '.join(str(e['pid']) for e in items[:12]) + (' ...' if len(items) > 12 else '')))
    return lines


# ---- main ----------------------------------------------------------------------------------------------------------------
def attach_dmg(dmg: Path, mount: Path):
    mount.mkdir(parents=True, exist_ok=True)
    out = subprocess.check_output(['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage', '-readonly', '-nobrowse',
                                   '-noverify', '-noautoopen', '-mountpoint', str(mount), '-plist', str(dmg)],
                                  stderr=subprocess.DEVNULL)
    entities = plistlib.loads(out)['system-entities']
    return next(e['dev-entry'] for e in entities if 'dev-entry' in e)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__.split('\n\n', 1)[1])
    parser.add_argument('log', type=Path, help='merged.log or a run directory holding one')
    parser.add_argument('--a2s', type=Path, help='ipsw A2S cache or a text table "<hex address> <symbol>"')
    parser.add_argument('--dsc', type=Path, help='dyld_shared_cache_arm64 of 21H461 (ipsw dyld a2f, image names)')
    parser.add_argument('--systemos-dmg', type=Path, help='the Cryptex1,SystemOS image to attach read-only for --dsc')
    parser.add_argument('--root', type=Path, action='append', default=[], help='directory searched for Mach-O files by name')
    parser.add_argument('--image', action='append', default=[], help='NAME=PATH of a Mach-O file')
    parser.add_argument('--ipsw', default=shutil.which('ipsw'), help='the ipsw tool (default: from PATH)')
    parser.add_argument('--no-a2f', action='store_true', help='do not run ipsw dyld a2f even with --dsc')
    parser.add_argument('--kernel', type=Path, help='kernel.macho of the run: names the k: tokens of the stall samples')
    parser.add_argument('--json', type=Path, help='also write the crashes as JSON')
    args = parser.parse_args(argv)

    log = args.log / 'merged.log' if args.log.is_dir() else args.log
    log_records = read_log(log)
    crashes, exits = collect(log_records)
    stall_run = stall_format.parse(log_records)
    stalls = stall_threads(stall_run)
    device = mount = None
    try:
        if args.systemos_dmg:
            mount = Path(tempfile.mkdtemp(prefix='systemos-'))
            device = attach_dmg(args.systemos_dmg, mount)
            args.dsc = mount / 'System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'
        a2s = open_a2s(args.a2s) if args.a2s and args.a2s.is_file() else None
        cache_images = CacheImages(args.dsc) if args.dsc and args.dsc.is_file() else None
        files = Files([image.split('=', 1) for image in args.image if '=' in image], args.root)
        namer = Namer(a2s, args.dsc, cache_images, None, files)
        wanted = namer.prepare(dict(crashes, **{'stall-%d-%s' % key: thread for key, thread in stalls.items()}))
        if args.dsc and args.dsc.is_file() and args.ipsw and not args.no_a2f and wanted:
            cache = args.a2s if args.a2s and (a2s is None or isinstance(a2s, A2SBinary)) else None
            inside = [a for a in wanted if cache_images is None or cache_images.range(a)]
            namer.a2f = ipsw_a2f(args.ipsw, args.dsc, inside, cache)
    finally:
        if device:
            subprocess.run(['hdiutil', 'detach', device], capture_output=True)
        if mount:
            try:
                mount.rmdir()
            except OSError:
                pass

    out, records = [], []
    sources = []
    if namer.a2s is not None:
        sources.append('a2s %s' % args.a2s)
    if namer.a2f:
        sources.append('ipsw a2f on %s' % args.dsc)
    if cache_images:
        sources.append('cache image list')
    if args.root or args.image:
        sources.append('Mach-O files')
    out.append('%s: %d crash report(s), %d stall sample(s); names from %s' % (log, len(crashes), len(stall_run.samples),
                                                                               ', '.join(sources) or 'nothing (tokens only)'))
    for crash in crashes.values():
        lines, record = describe(crash, namer, crashes)
        out.append('')
        out.extend(lines)
        records.append(record)
    summary = exit_summary(exits)
    if summary:
        out.append('')
        out.append('FATAL EXITS (S records; status: the signal, +0x80 core dumped; ns 2: signal namespace)')
        out.extend(summary)
    if stall_run.samples:
        kernel = None
        if args.kernel and args.kernel.is_file():
            kernel = MachO(args.kernel)
        out.append('')
        out.append('STALL SAMPLES (Q records; stall-report.py makes the table of them)')
        out.extend(describe_stalls(stall_run, stalls, namer, kernel))
    print('\n'.join(out))
    if args.json:
        args.json.write_text(json.dumps({'log': str(log), 'crashes': records, 'exits': exits}, indent=1) + '\n')
    return 0 if crashes or stall_run.samples else 3


if __name__ == '__main__':
    sys.exit(main())
