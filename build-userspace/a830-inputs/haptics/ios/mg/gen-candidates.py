# Candidate MobileGestalt key names from word combinations; prints matches against the obfuscated key list.
import sys, hashlib, base64, itertools
obf = set(l.strip() for l in open(sys.argv[1]) if l.strip())
known = {}
for l in open(sys.argv[2]):
    if "\t" in l:
        h, n = l.rstrip("\n").split("\t", 1); known[h] = n
def h(k): return base64.b64encode(hashlib.md5(b"MGCopyAnswer" + k.encode()).digest()).decode().rstrip("=")
pre = ["", "Device", "DeviceSupports", "Supports", "Has", "DeviceHas", "Is", "Product", "ProductSupports", "HW", "Hardware", "SupportsHardware",
       "Default", "Enable", "Enabled", "Allow", "Allows", "Can", "DeviceCan", "Uses", "Use", "Requires", "Max", "Min", "Main", "Builtin", "BuiltIn", "Internal",
       "Audio", "System", "UI", "Keyboard", "DeviceIs", "DeviceRequires", "DeviceUses", "Platform", "kMG"]
core = ["Haptics", "Haptic", "ClosedLoopHaptics", "ClosedLoopHaptic", "HapticFeedback", "Vibrator", "Vibration", "Vibrations", "TapticEngine", "Taptic",
        "Actuator", "HapticEngine", "HapticPlayback", "AudioHaptics", "CoreHaptics", "HapticsClosedLoop", "LiveHaptics", "HapticKeyboard", "KeyboardHaptics",
        "HapticTouch", "ForceTouch", "3DTouch", "Haptic3DTouch", "HapticClick", "HapticButton", "HapticButtons", "HapticsVersion", "HapticsType",
        "HapticActuator", "LRA", "ERM", "VibeEngine", "Vibe", "HapticMotor", "Motor", "HapticFeedbackLevel", "FeedbackSupportLevel", "FeedbackLevel",
        "Feedback", "HapticFeedbackSupportLevel", "SmoothHaptics", "AudioToHaptics", "AudioHapticSync", "HapticAudio", "HapticsAudio", "HapticPower",
        "ActuatorPower", "HapticsPower", "HapticsThermal", "HapticGain", "HapticIntensity", "HapticsIntensity", "VibrationIntensity", "VibratorType",
        "VibratorVersion", "HapticDevice", "HapticHardware", "HapticsHardware", "TapticEngineVersion", "TapticVersion", "ActuatorType", "ActuatorVersion",
        "Speaker", "Receiver", "AudioCodec", "ClosedLoop", "ClosedLoopVibrator", "VibratorClosedLoop", "HapticsCalibration", "ActuatorCalibration",
        "HapticCalibration", "VibratorCalibration", "TapticCalibration", "HapticBehavior", "HapticBehaviour", "PencilHaptics", "PencilHaptic",
        "TouchHaptics", "HapticMode", "HapticsMode", "SilentModeHaptics", "RingerSwitch", "RingerButton", "ActionButton", "RingerSwitchHaptics",
        "AOPHaptics", "HapticsOnAOP", "AOP", "AOPHaptic", "AlwaysOnHaptics", "HapticsSupport", "HapticSupport", "VibratorSupport", "VibrationSupport",
        "HomeButtonHaptics", "HapticHomeButton", "HomeButtonType", "VirtualHomeButton", "HapticFeedbackType", "FeedbackType", "UIFeedback",
        "SmartBattery", "Orb", "OrbCapability", "PressureSensitive", "Pressure", "ForceSensor", "HapticsForce", "ProxHaptics",
        "HapticsAsAudio", "AudioHapticPlayback", "VibratorHaptics", "Vibratory", "VibrationPattern", "VibrationPatterns", "MaxHapticChannels",
        "HapticChannels", "HapticChannelCount", "HapticSampleRate", "ActuatorSampleRate", "ActuatorResonantFrequency", "ResonantFrequency",
        "HapticResonantFrequency", "HapticFrequency", "VibratorFrequency", "LraFrequency", "LRAFrequency", "HapticLatency", "ActuatorLatency"]
suf = ["", "Capability", "Capabilities", "Support", "Supported", "Type", "Version", "Level", "Count", "Enabled", "Available", "Present", "Exists", "Info", "Config", "Configuration", "Mode", "Flag", "Feature", "Features"]
found = {}
for p, c, s in itertools.product(pre, core, suf):
    for k in {p + c + s, (p + c + s)[0].lower() + (p + c + s)[1:], (p + "-" + c + "-" + s).strip("-").lower()}:
        v = h(k)
        if v in obf and v not in found:
            found[v] = k
for v, k in sorted(found.items(), key=lambda x: x[1]):
    print(f"{v}\t{k}\t{'(already)' if v in known else ''}")
