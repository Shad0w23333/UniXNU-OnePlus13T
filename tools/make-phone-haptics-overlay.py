#!/usr/bin/env python3
"""Build the haptics overlays that give the J120 product of the iPad root what an iPhone's haptics read.

  make-phone-haptics-overlay.py [--draft PATH] [--resonance HZ] OS_ROOT OUTPUT_DIR

OS_ROOT is the mounted OS volume of the 21H461 IPSW (read only). OUTPUT_DIR gets two directories for the Data seed
(make-phone-seed.py --haptics-overlay OUTPUT_DIR puts them at /private/var/ngpu/tunings-j120 and /private/var/ngpu/mediaexperience-j120),
which the kernel shows, with phone-haptics-overlays.patch and only with boot-args pagani-haptics-ios=1 or 2 and pagani-haptics=1, as read-only
unions over

  /Library/Audio/Tunings/J120                         tunings-j120/
      Haptics/hapticengineconfig.plist              the haptic server's synth preset: -[AVHapticServer loadSynthPreset] (AudioToolbox)
                                                    loads /Library/Audio/Tunings/<product>/Haptics/hapticengineconfig.plist once
                                                    DeviceSupportsClosedLoopHaptics is on (pagani-haptics-ios=2, stage B), else fails with
                                                    -43; the generated preset (the haptics RE's draft make-j120-haptics.py: the Generic
                                                    preset's audio layers plus haptic tap and sine layers at the LRA's 170 Hz)
      Haptics/{Full,Mini,Micro}Tap<f>Hz.wav         the transient waveforms its zones name (paths under /Library/Audio/Tunings/J120/Haptics)
      VibeEngine/vibeengineconfig.plist             the legacy vibe engine's (libVibeSynthEngine) BaseSineFrequency, read while
                                                    closed-loop is off (stage A), as J207's of the same root
      UniXNU-haptics-overlay.txt                    the sentinel the kernel's stage 5 looks for (the overlay is visible)
  /System/Library/PrivateFrameworks/MediaExperience.framework/J120   mediaexperience-j120/
      SystemSoundVibrationPatterns.plist            D83's (iPhone 15 Pro, the identity's product) of the same root: J120's is empty
                                                    ("Default" => {}), so no system sound has a vibration
      SystemSoundRingerSettings.plist               D83's: which sounds vibrate in which ringer and silent states
      UniXNU-haptics-overlay.txt                    the sentinel

The J120 directories of the root stay visible below (AU, VAD, VPV5; CMSessionPresets.plist and the other MediaExperience tables).
Every plist is checked with plutil -lint and read back; every waveform the preset names must be in the overlay, a mono 16-bit 48 kHz
WAV; the D83 copies are byte for byte the root's. Nothing in OS_ROOT is modified. Prints what it made as JSON."""
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

DRAFT = Path('/Users/xiaochoumao/unixnu-13t/build-userspace/a830-inputs/haptics/ios/draft/make-j120-haptics.py')
TUNINGS = 'Library/Audio/Tunings/J120'
MX = 'System/Library/PrivateFrameworks/MediaExperience.framework'
MX_FILES = ('SystemSoundVibrationPatterns.plist', 'SystemSoundRingerSettings.plist')
SENTINEL = 'UniXNU-haptics-overlay.txt'
HAPTICS_PATH = '/Library/Audio/Tunings/J120/Haptics/'


def fail(message):
    sys.exit('make-phone-haptics-overlay: ' + message)


def lint(path):
    result = subprocess.run(['plutil', '-lint', str(path)], capture_output=True, text=True)
    if result.returncode:
        fail(f'{path}: plutil -lint: {result.stdout.strip()} {result.stderr.strip()}')
    with path.open('rb') as handle:
        return plistlib.load(handle)


def check_preset(preset, haptics_dir, root):
    """The preset's shape as loadSynthPreset reads it: Events[0] the layer table (each layer a LayerType and a LayerTypeID, each zone a
    ZoneTypeID and a waveform), the later events the Generic preset's sound events (a TypeID, layers of a LayerType with waveform zones).
    Every waveform file it names is in the overlay (/Library/Audio/Tunings/J120/Haptics) or on the root (the Generic sounds)."""
    if not isinstance(preset, dict) or not isinstance(preset.get('Events'), list) or not preset['Events']:
        fail('hapticengineconfig.plist: no Events')
    base = preset.get('Configuration', {}).get('BaseSineFrequency')
    if not isinstance(base, (int, float)) or not 50 <= base <= 400:
        fail(f'hapticengineconfig.plist: BaseSineFrequency {base!r}')
    named, on_root, layers = set(), set(), 0
    for index, event in enumerate(preset['Events']):
        if index and not isinstance(event.get('TypeID'), int):
            fail(f'hapticengineconfig.plist: event {index} without TypeID')
        for layer in event.get('Layers', []):
            layers += 1
            if layer.get('LayerType') not in ('Audio', 'Haptic') or (not index and not isinstance(layer.get('LayerTypeID'), int)):
                fail(f'hapticengineconfig.plist: event {index} layer {layer.get("Name")!r} without LayerType/LayerTypeID')
            for zone in layer.get('Zones', []):
                path = zone.get('WaveformPath')
                if not isinstance(path, str) or (not index and not isinstance(zone.get('ZoneTypeID'), int)):
                    fail(f'hapticengineconfig.plist: a zone of event {index} layer {layer.get("Name")!r} without ZoneTypeID/WaveformPath')
                if path.startswith(HAPTICS_PATH):
                    named.add(path[len(HAPTICS_PATH):])
                elif path.startswith('/'):
                    on_root.add(path)
    for name in sorted(named):
        wav = haptics_dir / name
        if not wav.is_file():
            fail(f'hapticengineconfig.plist names {HAPTICS_PATH}{name}, which the overlay lacks')
        with wave.open(str(wav), 'rb') as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 48000) or not w.getnframes():
                fail(f'{wav}: not a mono 16-bit 48 kHz WAV')
    for path in sorted(on_root):
        if not (root / path.lstrip('/')).is_file():
            fail(f'hapticengineconfig.plist names {path}, which the root lacks')
    return layers, sorted(named), sorted(on_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--draft', type=Path, default=DRAFT, help='the haptics RE\'s make-j120-haptics.py (default: %(default)s)')
    parser.add_argument('--resonance', type=float, default=170.0, help='the LRA resonance in Hz (default 170: 1e6 / 5882 us)')
    parser.add_argument('os_root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    root, output = args.os_root, args.output
    if not (root / TUNINGS / 'VAD').is_dir() or not (root / MX / 'J120/CMSessionPresets.plist').is_file():
        fail(f'{root}: no J120 tunings or MediaExperience tables (not the 21H461 OS volume)')
    for name in MX_FILES:
        if not (root / MX / 'D83' / name).is_file():
            fail(f'{root}: no D83 {name}')
    if (root / TUNINGS / 'Haptics').exists():
        fail(f'{root / TUNINGS / "Haptics"} exists: this root needs no tunings overlay')
    if not args.draft.is_file():
        fail(f'{args.draft}: the haptics RE\'s generator is not there')
    tunings, mx = output / 'tunings-j120', output / 'mediaexperience-j120'
    for target in (tunings, mx):
        if target.exists():
            fail(f'{target} exists (remove it to rebuild)')
    with tempfile.TemporaryDirectory(prefix='phone-haptics-') as tmp:
        made = Path(tmp) / 'out'
        result = subprocess.run([sys.executable, '-I', '-B', str(args.draft), str(root), str(made), '--resonance', str(args.resonance)],
                                capture_output=True, text=True)
        if result.returncode:
            fail(f'{args.draft} failed:\n{result.stdout}{result.stderr}')
        source = made / TUNINGS
        if not (source / 'Haptics/hapticengineconfig.plist').is_file() or not (source / 'VibeEngine/vibeengineconfig.plist').is_file():
            fail(f'{args.draft} did not make Haptics/hapticengineconfig.plist and VibeEngine/vibeengineconfig.plist')
        output.mkdir(parents=True, exist_ok=True)
        for directory in (tunings / 'Haptics', tunings / 'VibeEngine', mx):
            directory.mkdir(parents=True)
        for item in sorted((source / 'Haptics').iterdir()):
            if item.suffix not in ('.plist', '.wav') or not item.is_file():
                fail(f'{item}: unexpected in the draft\'s Haptics')
            shutil.copyfile(item, tunings / 'Haptics' / item.name)
        shutil.copyfile(source / 'VibeEngine/vibeengineconfig.plist', tunings / 'VibeEngine/vibeengineconfig.plist')
    for name in MX_FILES:
        shutil.copyfile(root / MX / 'D83' / name, mx / name)
    note = (f'UniXNU haptics overlay (make-phone-haptics-overlay.py): shown over /{{}} by phone-haptics-overlays.patch with '
            f'pagani-haptics-ios>=1; 21H461, resonance {args.resonance:g} Hz.\n')
    (tunings / SENTINEL).write_text(note.format(TUNINGS))
    (mx / SENTINEL).write_text(note.format(MX + '/J120'))
    for path in [*tunings.rglob('*'), *mx.rglob('*'), tunings, mx]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    # Read back: every plist lints and loads, the preset's waveforms are there, the D83 tables are the root's.
    preset = lint(tunings / 'Haptics/hapticengineconfig.plist')
    layers, waveforms, root_sounds = check_preset(preset, tunings / 'Haptics', root)
    vibe = lint(tunings / 'VibeEngine/vibeengineconfig.plist')
    if not isinstance(vibe, dict) or vibe.get('BaseSineFrequency') != float(args.resonance):
        fail(f'vibeengineconfig.plist: {vibe!r}')
    report = {'tunings': str(tunings), 'mediaexperience': str(mx), 'layers': layers, 'waveforms': len(waveforms),
              'root_sounds': len(root_sounds),
              'base_sine_hz': preset['Configuration']['BaseSineFrequency'], 'vibe_engine': vibe, 'files': {}}
    for name in MX_FILES:
        table = lint(mx / name)
        if not isinstance(table, dict) or not table or (mx / name).read_bytes() != (root / MX / 'D83' / name).read_bytes():
            fail(f'{mx / name}: not D83\'s table')
        report[name] = len(table)
    for path in sorted([*tunings.rglob('*'), *mx.rglob('*')]):
        if path.is_file():
            report['files'][str(path.relative_to(output))] = hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
