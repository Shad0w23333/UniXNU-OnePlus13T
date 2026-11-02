#!/bin/bash
# Check a generated KBLayouts file with Apple's own decoder: builds kbwalk.m for the iOS simulator, creates a throw-away simulator
# device on the newest installed iOS runtime, decodes every keyboard of the new family (in order and reversed, one shared cache)
# and then every keyboard of the file in a shuffled order, and deletes the device again. The simulator's TextInputUI is a newer
# build than 21H461's; its KBLayouts format and decoder are the same (its own KBLayouts files have the same head and tables).
# Usage: check-in-simulator.sh FILE [FAMILY-PREFIX] [WIDTH]    (defaults iPhone-PortraitChoco405 405.3333)
set -euo pipefail
FILE=$(cd "$(dirname "${1:?usage: check-in-simulator.sh FILE [FAMILY-PREFIX] [WIDTH]}")" && pwd)/$(basename "$1")
PREFIX=${2:-iPhone-PortraitChoco405}
WIDTH=${3:-405.3333}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(mktemp -d)
UDID=
cleanup() {
    if [ -n "$UDID" ]; then
        xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true
        xcrun simctl delete "$UDID" >/dev/null 2>&1 || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT
read -r RUNTIME DEVTYPE < <(xcrun simctl list runtimes -j available | python3 -c '
import json, sys
runtimes = [r for r in json.load(sys.stdin)["runtimes"] if r.get("platform") == "iOS" and r.get("isAvailable")]
if not runtimes:
    sys.exit("no iOS simulator runtime")
r = max(runtimes, key=lambda r: [int(x) for x in r["version"].split(".")])
phones = [d["identifier"] for d in r.get("supportedDeviceTypes", []) if d.get("productFamily") == "iPhone"]
print(r["identifier"], phones[0])')
xcrun --sdk iphonesimulator clang -arch arm64 -mios-simulator-version-min=17.0 -fobjc-arc -framework Foundation \
    -framework CoreGraphics "$HERE/kbwalk.m" -o "$WORK/kbwalk"
python3 -B - "$HERE/../make-phone-keyboard-layouts.py" "$FILE" "$PREFIX" "$WORK" <<'PY'
import importlib.util, random, sys
spec = importlib.util.spec_from_file_location('mk', sys.argv[1])
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)
lay = mk.Layouts(open(sys.argv[2], 'rb').read())
new = sorted(n for n in lay.offsets if n.startswith(sys.argv[3] + '-'))
if not new:
    sys.exit('no ' + sys.argv[3] + '-* keyboards in the file')
everything = list(lay.offsets)
random.Random(7).shuffle(everything)
for name, names in (('new', new), ('new-reversed', new[::-1]), ('all-shuffled', everything)):
    open(f'{sys.argv[4]}/{name}.txt', 'w').write('\n'.join(names) + '\n')
PY
UDID=$(xcrun simctl create unixnu-kbcheck "$DEVTYPE" "$RUNTIME")
xcrun simctl boot "$UDID"
status=0
for list in new new-reversed; do
    echo "== $list"
    xcrun simctl spawn "$UDID" "$WORK/kbwalk" "$FILE" "$WORK/$list.txt" "$WIDTH" || status=1
done
echo "== all-shuffled"
xcrun simctl spawn "$UDID" "$WORK/kbwalk" "$FILE" "$WORK/all-shuffled.txt" || status=1
exit $status
