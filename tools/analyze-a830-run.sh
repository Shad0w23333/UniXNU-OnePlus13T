#!/bin/zsh
# 读一轮 gpu-loop 证据目录里的关键行：analyze-a830-run.sh <runs/目录>
# 只读；按主题分组打印 merged.log 里的记录（一次性开机事实、诊断守护进程、键盘/点屏/音量/预装应用、GPU 计量、电池、异常）。
R=${1:?用法: analyze-a830-run.sh <runs 目录>}
L=$R/merged.log
echo "== summary"; head -8 $R/summary.txt | cut -c1-200
echo "== 一次性事实与叠加层"
grep -aE "overlay|overlays:|identity:|staged apps copy|wdog ok|text-fixup|gpu aperture" $L | cut -c1-260 | head -20
echo "== 诊断守护进程"
grep -aE " diag: " $L | cut -c1-260 | head -150
echo "== 键盘 / 点屏 / 音量 / 预装应用"
grep -aE "input script|kbt |audio route|audio: |app moves|app move |app state|staged apps watch" $L | cut -c1-260 | head -60
echo "== GPU 计量与电池"
grep -aE "gpu meter|\[battery\]" $L | cut -c1-260 | head -40
echo "== 异常"
grep -aE "panic|PANIC|GPU LOST|RECOVER|exit pid=[0-9]+ [^ ]+ status=[1-9a-f]" $L | grep -av "status=0 " | cut -c1-220 | head -30
echo "== 设备与复位"
grep -aE "^(battery|pmic_info|dload|uptime|sys.boot.reason)" $R/device.txt 2>/dev/null | head -12
