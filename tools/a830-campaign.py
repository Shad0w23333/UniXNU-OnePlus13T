#!/usr/bin/env python3
"""Run the A830 bring-up images one after another through the photo-free loop and stop at the first problem.

  a830-campaign.py loop-s3 loop-s5 loop-s7 loop-s9 loop-full --repeat 1 --yes

For each image (UniXNU/out-gpu/<name>.img): gpu-test-loop/gpu-loop.py --yes runs it (Android -> fastboot boot ->
XNU logs and warm-resets itself -> Android -> decode). The campaign goes on to the next image only when the run came
back by itself and its GPU status rows show "STOP ok" (stop images) or no FAIL (full images). A run that did not come
back means XNU hung: the campaign stops and says so (reset the phone by hand, then read-ramoops.py reads the log).
Without --yes it only prints the plan. Every run's evidence directory stays under gpu-test-loop/runs/.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
LOOP = TOP / '工具/gpu-test-loop'
OUT = TOP / 'UniXNU/out-gpu'


def run_image(name, label, yes):
    image = OUT / f'{name}.img'
    if not image.is_file():
        sys.exit(f'campaign: {image} missing (run 工具/make-a830-images.sh)')
    command = [sys.executable, 'gpu-loop.py', '--image', str(image), '--label', label, '--android-timeout', '420']
    if yes:
        command.append('--yes')
    result = subprocess.run(command, cwd=LOOP, capture_output=True, text=True)
    text = result.stdout + result.stderr
    runs = sorted((LOOP / 'runs').glob(f'*-{label}-*'), key=lambda p: p.stat().st_mtime)
    return result.returncode, text, runs[-1] if runs else None


def verdict(text, evidence):
    if 'did not come back' in text or 'Android did not come back' in text:
        return 'hang', 'XNU did not reset back to Android (hang before the reset timer, or a stuck reset)'
    summary = (evidence / 'summary.txt').read_text(errors='replace') if evidence and (evidence / 'summary.txt').is_file() else text
    rows = [line.strip() for line in summary.splitlines() if 'GPU S' in line or ' S0 ' in line]
    if any(re.search(r'FAIL|fail', row) for row in rows):
        return 'fail', '; '.join(rows[-4:])
    if any('STOP ok' in row or 'RUN ok' in row or ' ok ' in row for row in rows):
        return 'ok', '; '.join(rows[-4:])
    return 'unknown', 'no readable GPU status row (log too damaged?)'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('images', nargs='+', help='out-gpu image names, e.g. loop-s3 loop-s5')
    parser.add_argument('--repeat', type=int, default=1, help='runs per image (the hang may be intermittent)')
    parser.add_argument('--yes', action='store_true', help='really reboot the phone (without it: plan only)')
    parser.add_argument('--keep-going', action='store_true', help='continue after an unreadable log')
    args = parser.parse_args()
    for name in args.images:
        for n in range(1, args.repeat + 1):
            label = f'{name}-c{n}'
            print(f'=== {name} run {n}/{args.repeat}', flush=True)
            code, text, evidence = run_image(name, label, args.yes)
            if not args.yes:
                print(text)
                continue
            state, detail = verdict(text, evidence)
            print(f'    {state}: {detail}\n    evidence: {evidence}', flush=True)
            if state == 'hang':
                print('campaign stopped: reset the phone by hand (power + volume down), then run\n'
                      f'  cd {LOOP} && python3 read-ramoops.py --label {label}-after-hang')
                return 2
            if state == 'fail' or (state == 'unknown' and not args.keep_going):
                print('campaign stopped at this image')
                return 1
    print('campaign finished: every image reached its stop point')
    return 0


if __name__ == '__main__':
    sys.exit(main())
