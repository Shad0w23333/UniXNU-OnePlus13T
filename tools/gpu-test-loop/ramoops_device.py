"""adb/fastboot helpers of the photo-free test loop and the evidence directory of one run.

Reading is read-only on the phone (getprop, ls, cat/base64 of /sys/fs/pstore and /sys/module/ramoops/parameters through
su). Rebooting is only done by gpu-loop.py, and only with --yes.
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

import ramoops_format as rf

PSTORE = '/sys/fs/pstore'
COPIES = (('console', 'console-ramoops'), ('pmsg', 'pmsg-ramoops'))
FACTS = {
    'bootreason': 'getprop ro.boot.bootreason',
    'sys.boot.reason': 'getprop sys.boot.reason',
    'sys.boot.reason.last': 'getprop sys.boot.reason.last',
    'boot_completed': 'getprop sys.boot_completed',
    'build': 'getprop ro.build.fingerprint',
    'kernel': 'uname -a',
    'uptime': 'cat /proc/uptime',
    'boot_id': 'cat /proc/sys/kernel/random/boot_id',
    'battery': "dumpsys battery | grep -E 'powered|status|level|voltage|temperature'",
}
RAMOOPS_PARAMETERS = ('mem_address', 'mem_size', 'mem_type', 'record_size', 'console_size', 'pmsg_size', 'ftrace_size', 'ecc',
                      'max_reason')


class ToolError(RuntimeError):
    pass


class Phone:
    def __init__(self, serial: Optional[str] = None, adb: str = 'adb', fastboot: str = 'fastboot', verbose: bool = True):
        self.serial = serial
        self.adb_path = shutil.which(adb) or adb
        self.fastboot_path = shutil.which(fastboot) or fastboot
        self.verbose = verbose

    def log(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    def _adb(self, *args: str, timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess:
        command = [self.adb_path] + (['-s', self.serial] if self.serial else []) + list(args)
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ToolError('timed out: ' + ' '.join(map(shlex.quote, command))) from error
        except FileNotFoundError as error:
            raise ToolError('adb not found (%s)' % self.adb_path) from error
        if check and result.returncode:
            raise ToolError('%s: %s' % (' '.join(map(shlex.quote, command)), result.stderr.decode(errors='replace').strip()))
        return result

    def fastboot(self, *args: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
        command = [self.fastboot_path] + (['-s', self.serial] if self.serial else []) + list(args)
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ToolError('timed out: ' + ' '.join(map(shlex.quote, command))) from error
        except FileNotFoundError as error:
            raise ToolError('fastboot not found (%s)' % self.fastboot_path) from error
        if check and result.returncode:
            raise ToolError('%s: %s' % (' '.join(map(shlex.quote, command)),
                                        (result.stderr or result.stdout).decode(errors='replace').strip()))
        return result

    # ---- state ------------------------------------------------------------------------------------------------------------
    def adb_state(self) -> str:
        """'device' (Android with adbd), 'recovery', 'unauthorized', ... or '' when adb sees nothing."""
        result = self._adb('get-state', timeout=10, check=False)
        if result.returncode == 0:
            return result.stdout.decode(errors='replace').strip()
        # adb reports a phone it cannot use on stderr: 'unauthorized' (the RSA prompt) and 'offline' are a phone in Android.
        error = (result.stderr or b'').decode(errors='replace').lower()
        for state in ('unauthorized', 'offline'):
            if state in error:
                return state
        return ''

    def restart_adb(self) -> None:
        """adb kill-server and start-server: a phone back in Android stayed invisible for minutes twice (2026-10-30 17:08, 18:16)."""
        self._adb('kill-server', timeout=15, check=False)
        self._adb('start-server', timeout=30, check=False)

    @staticmethod
    def usb_phones() -> str:
        """Phones on this Mac's USB as ioreg sees them: OnePlus (0x22d9), Google's adb/fastboot (0x18d1), Qualcomm (0x05c6)."""
        try:
            text = subprocess.run(['ioreg', '-p', 'IOUSB', '-l', '-w0'], capture_output=True, timeout=20).stdout.decode(errors='replace')
        except (OSError, subprocess.SubprocessError):
            return 'ioreg failed'
        found, name = [], ''
        for line in text.splitlines():
            if '"USB Product Name"' in line:
                name = line.split('=', 1)[1].strip().strip('"')
            elif '"idVendor"' in line:
                vendor = int(line.split('=', 1)[1].strip())
                if vendor in (0x22d9, 0x18d1, 0x05c6):
                    found.append('%04x %s' % (vendor, name))
        return ', '.join(found) or 'nothing (no phone on USB)'

    def in_fastboot(self) -> bool:
        result = self.fastboot('devices', timeout=10, check=False)
        lines = [line.split() for line in result.stdout.decode(errors='replace').splitlines() if line.strip()]
        if self.serial:
            return any(parts and parts[0] == self.serial for parts in lines)
        return bool(lines)

    def shell(self, command: str, timeout: float = 30, root: bool = False, check: bool = True) -> str:
        line = 'su -c %s' % shlex.quote(command) if root else command
        return self._adb('shell', line, timeout=timeout, check=check).stdout.decode(errors='replace').strip()

    def boot_completed(self) -> bool:
        try:
            return self.adb_state() == 'device' and self.shell('getprop sys.boot_completed', timeout=10) == '1'
        except ToolError:
            return False

    def battery(self) -> Dict[str, str]:
        """Android's battery service (dumpsys battery, no root): 'level', 'status', 'USB powered', 'voltage', ...; {} when
        it does not answer. XNU does not charge the battery the way Android does (no charger PD without the DSP) and runs the
        screen, CPU and GPU hard, so a night of runs drains it; the loop checks the level before every run."""
        try:
            out = self.shell('dumpsys battery', timeout=15, check=False)
        except ToolError:
            return {}
        facts = {}
        for line in out.splitlines():
            key, _, value = line.strip().partition(':')
            if value:
                facts[key.strip()] = value.strip()
        return facts

    def require_root(self) -> None:
        out = self.shell('id', root=True, check=False)
        if 'uid=0' not in out:
            raise ToolError('su did not give root (got %r); grant shell root in KernelSU first' % out)

    # ---- reading --------------------------------------------------------------------------------------------------------
    def read_root_file(self, path: str, timeout: float = 60) -> bytes:
        """A binary file through su: base64 on the phone (no pty/CRLF damage), raw cat when base64 is missing."""
        quoted = shlex.quote(path)
        result = self._adb('exec-out', 'su -c %s' % shlex.quote('base64 -w 0 %s' % quoted), timeout=timeout, check=False)
        text = result.stdout.strip()
        if result.returncode == 0 and text:
            try:
                data = base64.b64decode(text, validate=True)
            except binascii.Error:
                data = None
            if data is not None:
                return data
        return self._adb('exec-out', 'su -c %s' % shlex.quote('cat %s' % quoted), timeout=timeout).stdout

    def file_size(self, path: str) -> Optional[int]:
        out = self.shell('stat -c %%s %s' % shlex.quote(path), root=True, check=False)
        return int(out) if out.isdigit() else None

    def pull_pstore(self) -> Dict[str, dict]:
        """name -> {'file': pstore name or None, 'data': bytes or None, 'size': stat size} for console and pmsg."""
        listing = self.shell('ls %s' % PSTORE, root=True, check=False).split()
        out: Dict[str, dict] = {}
        for name, prefix in COPIES:
            files = sorted(f for f in listing if f.startswith(prefix))
            entry = {'file': None, 'data': None, 'size': None, 'listing': listing}
            if files:
                path = '%s/%s' % (PSTORE, files[0])
                entry['file'] = files[0]
                entry['size'] = self.file_size(path)
                data = self.read_root_file(path)
                if entry['size'] is not None and entry['size'] != len(data):
                    raise ToolError('%s: read %d bytes, stat says %d' % (path, len(data), entry['size']))
                entry['data'] = data
            out[name] = entry
        return out

    def facts(self) -> Dict[str, str]:
        facts = {}
        for key, command in FACTS.items():
            try:
                facts[key] = self.shell(command, timeout=10, check=False)
            except ToolError as error:
                facts[key] = 'error: %s' % error
        for name in RAMOOPS_PARAMETERS:
            facts['ramoops.' + name] = self.shell('cat /sys/module/ramoops/parameters/%s' % name, root=True, check=False)
        # Why a pstore file is missing: ramoops reports a zone it found invalid at boot ("found existing invalid buffer").
        facts['dmesg.ramoops'] = self.shell("dmesg | grep -i -E 'ramoops|pstore|persistent_ram' | tail -n 30", root=True,
                                            check=False)
        # What the PMIC did at the previous resets (pmic-pon-log at boot: PON/Reset Trigger, Reset Type; OPLUS's copy of the PON
        # history in /sys/pmic_info) and the download-mode state that can make the firmware pick a warm reset. Read only.
        facts['dmesg.pmic_pon'] = self.shell("dmesg | grep -E 'PMIC PON log|PMICWD|disallowed SPMI|qcom_dload' | tail -n 60",
                                             root=True, check=False)
        facts['pmic_info'] = self.shell('cat /sys/pmic_info/poff_reason /sys/pmic_info/pon_reason /sys/pmic_info/pmic_history_count',
                                        root=True, check=False)
        facts['dload'] = self.shell('cat /sys/module/qcom_dload_mode/parameters/download_mode /sys/kernel/dload/dload_mode',
                                    root=True, check=False)
        return facts


def wait_until(predicate, timeout: float, interval: float = 2.0, tick=None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        if tick:
            tick(deadline - time.monotonic())
        time.sleep(interval)
    return predicate()


def default_runs() -> Path:
    return Path(__file__).resolve().parent / 'runs'


def evidence_dir(runs: Path, label: Optional[str]) -> Path:
    stamp = _dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in (label or ''))
    path = runs / (stamp + ('-' + safe if safe else ''))
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_evidence(directory: Path, copies: Dict[str, Optional[bytes]], extra: dict) -> rf.Decoded:
    """The raw pstore files, the decoded and merged log, the summary and the facts of one run."""
    for name, data in copies.items():
        if data is not None:
            (directory / ('%s-ramoops-0.bin' % name)).write_bytes(data)
    decoded = rf.decode(copies)
    with (directory / 'records.jsonl').open('w') as stream:
        for record in decoded.records:
            stream.write(json.dumps(record.as_json(), ensure_ascii=True) + '\n')
    (directory / 'merged.log').write_text(rf.render(decoded.records))
    summary = rf.summary(decoded)
    (directory / 'summary.txt').write_text(summary)
    info = dict(extra)
    info['decode'] = decoded.summary_stats()
    info['copies'] = {name: {'bytes': len(data) if data is not None else None,
                             'sha256': hashlib.sha256(data).hexdigest() if data is not None else None}
                      for name, data in copies.items()}
    (directory / 'decode.json').write_text(json.dumps(info, indent=2, sort_keys=True, default=str) + '\n')
    return decoded


def load_saved(directory: Path) -> Dict[str, Optional[bytes]]:
    """Saved files of a run: pstore files (<name>-ramoops-0[.bin]), or raw zone images (<name>.zone: header and data, as
    read from the reserved memory). A zone image is decoded from its whole data area whatever its Linux header says (the
    copies carry their own headers), so a zone Linux dropped or cut short still gives everything that is left in it."""
    copies: Dict[str, Optional[bytes]] = {}
    for name, prefix in COPIES:
        data = None
        for candidate in ('%s-0.bin' % prefix, '%s-0' % prefix, prefix):
            path = directory / candidate
            if path.is_file():
                data = path.read_bytes()
                break
        zone = directory / ('%s.zone' % name)
        if data is None and zone.is_file():
            data = zone.read_bytes()[rf.ZONE_HEADER:] or None
        copies[name] = data
    if not any(copies.values()):
        raise ToolError('%s: no console-ramoops-0/pmsg-ramoops-0 copy (or .zone image) found' % directory)
    return copies


def image_facts(image: Path) -> dict:
    """Size, SHA-256 and, from the payload record unixnu writes beside the image, its kernel arguments."""
    facts = {'path': str(image), 'bytes': image.stat().st_size}
    digest = hashlib.sha256()
    with image.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    facts['sha256'] = digest.hexdigest()
    for record in (image.with_suffix('.payload.json'), image.parent / (image.stem + '.payload.json')):
        if record.is_file():
            try:
                payload = json.loads(record.read_text())
            except ValueError:
                continue
            facts['payload_record'] = str(record)
            facts['kernel_args'] = payload.get('kernel_args')
            break
    return facts


def boot_args_of(line: Optional[str]) -> Dict[str, str]:
    out = {}
    for item in (line or '').split():
        key, _, value = item.partition('=')
        out[key] = value
    return out


def describe(info: List[str]) -> str:
    return '\n'.join('  ' + line for line in info)
