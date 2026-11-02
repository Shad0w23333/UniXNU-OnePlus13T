#!/usr/bin/env python3
"""TS/test-darwinboot.py 的草稿版：用实验目录的 DarwinBoot 和已有的 UniXNU.efi 跑 QEMU 验证。"""
import os,shutil,subprocess,sys
from pathlib import Path
S=Path(sys.argv[1]);TOP=Path(sys.argv[2]);P=Path(sys.argv[3]);name=sys.argv[4]
extra_fix=sys.argv[5:]
ROOT=S/'edk'/name
if ROOT.exists(): shutil.rmtree(ROOT)
ESP=ROOT/'esp';PAY=ESP/'EFI/UniXNU/Payloads';PAY.mkdir(parents=True)
env=dict(os.environ,PATH='/opt/homebrew/opt/llvm/bin:/opt/homebrew/opt/lld/bin:'+os.environ['PATH'],PYTHONDONTWRITEBYTECODE='1')
subprocess.run(['python3',P/'Test/make-fixtures.py',PAY,*extra_fix],check=True,env=env)
(ESP/'EFI/BOOT').mkdir(parents=True)
shutil.copyfile(S/'edk/ws-unixnu/Build/UniXNU/DEBUG_CLANGPDB/AARCH64/UniXNU.efi',ESP/'EFI/BOOT/BOOTAA64.EFI')
OUT=S/'edk/ws/build/DarwinBoot/DEBUG_CLANGPDB/AARCH64'
for n in ('DarwinBoot','DarwinBootValidation'):
    shutil.copyfile(OUT/f'{n}.efi',PAY/f'{n}.efi')
(ESP/'EFI/UniXNU/unixnu.toml').write_text('''version = 1
[boot]
timeout = 1
default = "synthetic"
[kits]
autoload = false
[[driver]]
path = "Payloads/DarwinBoot.efi"
[[driver]]
path = "Payloads/DarwinBootValidation.efi"
[[entry]]
id = "synthetic"
title = "Synthetic Darwin ABI entry"
type = "xnu"
path = "Payloads/kernel.macho"
args = "darwinboot.fixture=1"
initrd = "Payloads/ramdisk.bin"
''')
r=subprocess.run(['python3',TOP/'UniXNU/package/Scripts/qemu-run.py',ESP,'--log',ROOT/'qemu.log','--cpus','1','--no-gop','--timeout','40','--finish-on-expect',
 '--expect','DARWINBOOT: loader v2 installed EL1',
 '--expect','DARWINBOOT-VALIDATION: 33 rejected requests; firmware retained PASS',
 '--expect','DARWINBOOT-VALIDATION: 7 root policy sidecars refused PASS',
 '--expect','DARWINBOOT-VIDEO-VALIDATION: 8 malformed/unsupported GOP field sets refused PASS',
 '--expect','DARWINBOOT: inputs validated; exiting boot services',
 '--expect','DARWINBOOT-FIXTURE: boot_args, RAMDisk, TrustCache, zero-fill and MMU-off entry PASS',
 '--reject',r'DARWINBOOT[^\n]*FAIL','--reject','ASSERT'],env=env)
print('qemu rc',r.returncode)
