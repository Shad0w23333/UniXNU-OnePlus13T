#!/bin/zsh
# 模拟器实证：编译 heicprobe.m，建一个临时模拟器，解码 21H461 的 J120.HEIC，打印后删除模拟器。不碰手机。
set -e
HERE=${0:A:h}
IN=${HERE:h:h}
HEIC=$(find "$IN/osmnt-21H461/Library/Wallpaper" -name J120.HEIC | head -1)
xcrun -sdk iphonesimulator clang -arch arm64 -mios-simulator-version-min=17.0 -fobjc-arc -w -framework Foundation -framework ImageIO \
    -framework CoreGraphics -framework CoreVideo -framework CoreMedia -framework VideoToolbox "$HERE/heicprobe.m" -o "$HERE/heicprobe"
codesign -s - -f "$HERE/heicprobe" 2>/dev/null
U=$(xcrun simctl create ui-rest-heic com.apple.CoreSimulator.SimDeviceType.iPhone-16 com.apple.CoreSimulator.SimRuntime.iOS-26-5 | tail -1)
trap 'xcrun simctl shutdown "$U" 2>/dev/null; xcrun simctl delete "$U" 2>/dev/null' EXIT
xcrun simctl boot "$U"
xcrun simctl bootstatus "$U" -b > /dev/null
xcrun simctl spawn "$U" "$HERE/heicprobe" "$HEIC" 2>&1 | sed -E 's#已加载 .*/RuntimeRoot#已加载 #'
