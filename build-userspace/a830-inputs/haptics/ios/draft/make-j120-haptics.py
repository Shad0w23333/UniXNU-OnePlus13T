#!/usr/bin/env python3
# Draft generator (UniXNU haptics/ios): the files the iPad root lacks for haptics on the J120 board name.
#   OUT/Library/Audio/Tunings/J120/Haptics/hapticengineconfig.plist   AVHapticServer (AudioToolbox) synth preset, loaded when
#                                                                      /product/haptics closed-loop != 0 (-[AVHapticServer loadSynthPreset])
#   OUT/Library/Audio/Tunings/J120/Haptics/{Full,Mini,Micro}Tap<f>Hz.wav  transient waveforms the preset's zones point at
#   OUT/Library/Audio/Tunings/J120/VibeEngine/vibeengineconfig.plist  LegacyVibeEngine (libVibeSynthEngine) config, only used while
#                                                                      closed-loop is 0 (stage A)
#   OUT/System/Library/PrivateFrameworks/MediaExperience.framework/J120/SystemSound{VibrationPatterns,RingerSettings}.plist
#                                                                      copies of the D83 (iPhone 15 Pro) tables from the same root
# Event type IDs follow CoreHaptics' table (+[EventToDictionaryConverter eventTypeToNSString:]): id = ZoneTypeID << 8 | LayerTypeID,
# e.g. HapticFullTap2..9 = 0x1F57..0x7357 (zones 31..115 step 12, layer 87), HapticSineLow..High = 0x3D61..0x4161, HapticSine 0x7F62,
# HapticSineTransient 0x7F63, AudioSine 0x7F01, AudioSineTransient 0x7F02 (the Generic preset's audio layers 1 and 2, zone 127).
# Everything about the haptic layers below is a draft to be checked against audiomxd's HapticSynth_Parser log on the device.
# usage: python3 -I make-j120-haptics.py OS_ROOT OUT [--resonance 170] [--rate 48000]
import argparse, math, plistlib, struct, wave
from pathlib import Path

TAP_FREQS = (80, 100, 125, 150, 200, 230, 270, 300)          # D47's FullTap*/MiniTap*/MicroTap* file names
TAP_ZONES = tuple(31 + 12 * i for i in range(8))               # 31, 43, ... 115  (Tap2..Tap9)
SINE_ZONES = (61, 62, 63, 64, 65)                              # HapticSineLow, LowMid, Mid, MidHigh, High
TAP_LAYERS = {'Micro': (85, 0.35), 'Mini': (86, 0.6), 'Full': (87, 1.0)}
HAPTICS_DIR = '/Library/Audio/Tunings/J120/Haptics'


def tap(freq, amp, rate):
    """One sine cycle at freq under a raised-cosine window, then a counter-phase half cycle at 60 % as a brake."""
    n1 = int(round(rate / freq))
    n2 = int(round(rate / freq / 2))
    out = []
    for i in range(n1):
        w = 0.5 - 0.5 * math.cos(2 * math.pi * i / n1)
        out.append(amp * w * math.sin(2 * math.pi * i / n1))
    for i in range(n2):
        out.append(-0.6 * amp * math.sin(math.pi * i / n2))
    out += [0.0] * int(rate * 0.002)
    return out


def write_wav(path, samples, rate):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<h', max(-32767, min(32767, int(s * 32767)))) for s in samples))


def haptic_layer(name, layer_id, zones, locality=None, sine=False, sustain=1.0):
    layer = {'Name': name, 'LayerType': 'Haptic', 'LayerTypeID': layer_id, 'LayerHasSine': sine,
             'GainEnvelope': {'AttackTime': 0.0 if not sine else 0.005, 'DecayTime': 0.005,
                              'ReleaseTime': 0.01 if not sine else 0.02, 'SustainLevel': sustain},
             'Parameters': {'GainParameter': {'MinimumGainInDecibels': -60.0},
                            'TransposeParameter': {'TransposeFactor': 1.0 if sine else 0.0}},
             'Zones': zones}
    if locality:
        layer.update({'Locality': locality, 'DefaultIntensity': 1.0, 'DefaultSharpness': 0.5})
    return layer


def build(os_root, out, resonance, rate):
    haptics = out / HAPTICS_DIR.lstrip('/')
    haptics.mkdir(parents=True, exist_ok=True)
    for kind, (layer_id, amp) in TAP_LAYERS.items():
        for f in TAP_FREQS:
            write_wav(haptics / f'{kind}Tap{f}Hz.wav', tap(f, amp, rate), rate)
    generic = plistlib.loads((os_root / 'Library/Audio/Tunings/Generic/Haptics/Config/hapticengineconfig.plist').read_bytes())
    events = generic['Events']
    layers = list(events[0]['Layers'])                           # Generic audio layers 127, 1, 2, 43 stay as they are
    for kind, (layer_id, _) in TAP_LAYERS.items():
        zones = [{'ZoneTypeID': z, 'WaveformPath': f'{HAPTICS_DIR}/{kind}Tap{f}Hz.wav', 'WaveformLooped': False}
                 for z, f in zip(TAP_ZONES, TAP_FREQS)]
        # Only the full taps carry a Locality: a Locality's transient list is indexed by sharpness, one family of 8 per Locality.
        layers.append(haptic_layer(f'Haptic {kind} Tap', layer_id, zones, 'Default' if kind == 'Full' else None))
    ratios = (0.75, 0.87, 1.0, 1.15, 1.3)
    sine_zones = [{'ZoneTypeID': z, 'WaveformPath': 'Sine', 'WaveformLooped': True, 'WaveformBaseFreq': resonance,
                   'RootFreq': round(resonance * r, 1)} for z, r in zip(SINE_ZONES, ratios)]
    layers.append(haptic_layer('Haptic Sine Sharpness', 97, sine_zones, 'Default', sine=True))
    one = [{'ZoneTypeID': 127, 'WaveformPath': 'Sine', 'WaveformLooped': True, 'WaveformBaseFreq': resonance, 'RootFreq': resonance}]
    layers.append(haptic_layer('Haptic Sine', 98, one, None, sine=True))
    layers.append(haptic_layer('Haptic Sine Transient', 99, one, None, sine=True, sustain=0.0))
    # FullGamut (keyboard AHAPs ask for it): the same waveforms under distinct program numbers.
    full_zones = [{'ZoneTypeID': z, 'WaveformPath': f'{HAPTICS_DIR}/FullTap{f}Hz.wav', 'WaveformLooped': False}
                  for z, f in zip(TAP_ZONES, TAP_FREQS)]
    layers.append(haptic_layer('Haptic Full Tap FullGamut', 88, full_zones, 'FullGamut'))
    layers.append(haptic_layer('Haptic Sine FullGamut', 96, sine_zones, 'FullGamut', sine=True))
    preset = {'Configuration': {'BaseSineFrequency': float(resonance)}, 'Events': [dict(events[0], Layers=layers)] + events[1:]}
    (haptics / 'hapticengineconfig.plist').write_bytes(plistlib.dumps(preset, fmt=plistlib.FMT_BINARY))
    vibe = out / 'Library/Audio/Tunings/J120/VibeEngine'
    vibe.mkdir(parents=True, exist_ok=True)
    (vibe / 'vibeengineconfig.plist').write_bytes(plistlib.dumps({'BaseSineFrequency': float(resonance), 'UseThermalMonitor': False},
                                                                  fmt=plistlib.FMT_BINARY))
    mx_src = os_root / 'System/Library/PrivateFrameworks/MediaExperience.framework/D83'
    mx_dst = out / 'System/Library/PrivateFrameworks/MediaExperience.framework/J120'
    mx_dst.mkdir(parents=True, exist_ok=True)
    for name in ('SystemSoundVibrationPatterns.plist', 'SystemSoundRingerSettings.plist'):
        (mx_dst / name).write_bytes((mx_src / name).read_bytes())
    return preset


def main():
    p = argparse.ArgumentParser()
    p.add_argument('os_root', type=Path)
    p.add_argument('out', type=Path)
    p.add_argument('--resonance', type=float, default=170.0)      # 1e6 / qcom,lra-period-us 0x16fa (5882 us)
    p.add_argument('--rate', type=int, default=48000)
    a = p.parse_args()
    preset = build(a.os_root, a.out, a.resonance, a.rate)
    ids = sorted({z['ZoneTypeID'] << 8 | l['LayerTypeID'] for l in preset['Events'][0]['Layers'] for z in l.get('Zones', [])})
    print('event ids:', ' '.join(map(str, ids)))


main()
