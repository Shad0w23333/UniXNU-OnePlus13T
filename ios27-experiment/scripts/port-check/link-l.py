#!/usr/bin/env python3
"""把 L 里程碑的驱动对象打成归档，接到已编好的 12377 内核目标文件上试链接。firehose 和 corecrypto 用桩，只证明符号闭合。"""
import json,os,re,shlex,subprocess,sys
from pathlib import Path
S=Path(sys.argv[1]); OUT=S/'l'; OUT.mkdir(exist_ok=True)
SERIES=os.environ.get('SERIES')=='1'
O=S/('o27s' if SERIES else 'o27')
log=(S/'link2.log').read_text().splitlines()
line=[l for l in log if '-Wl,-threaded_starts_section' in l and ' -o ' in l and 'replacecontents' not in l][-1]
objcopy='/opt/homebrew/opt/llvm/bin/llvm-objcopy'
LATE={'NativeUfs__NativeUfs':'NativeUfs.cpp','NativeUfs__IOBlockStorageDevice':'IOBlockStorageDevice.cpp','NativeRamBlock__NativeRamBlock':'NativeRamBlock.cpp','NativeKeyStoreEvents__NativeKeyStoreEvents':'NativeKeyStoreEvents.cpp'}
objs=[]
for n in ['BootProgress__BootProgress','BootProgress__RamoopsLog','BootProgress__GunyahWdog','BootProgress__UfsLog','BootProgress__WeakDefaults','BootProgress__Entry','LinkStub__RootAnchor','LinkStub__stubs',
          'NativeUfs__NativeUfs','NativeUfs__IOBlockStorageDevice','NativeUfs__SourceStorage','NativeUfs__SourceRootBinding','NativeUfs__SourceRootPolicies',
          'NativeRamBlock__NativeRamBlock','NativeKeyStoreEvents__NativeKeyStoreEvents','NativeKeyStoreEvents__KeyStoreProbe',
          'PaganiVerifiedRoot__verified_root','PaganiVerifiedRoot__xnu_crypto','PaganiArmCpu__ArmCpu','PaganiArmDTPlatform__ArmDTPlatform']:
    src=O/(n+'.o')
    if n in LATE:
        dst=OUT/(n+'.late.o')
        subprocess.run([objcopy,'--remove-section=__DATA,__mod_init_func',f'--globalize-symbol=__GLOBAL__sub_I_{LATE[n]}',src,dst],check=True)
        objs.append(dst)
    else: objs.append(src)
extra=[Path(p) for p in sys.argv[2:]]
archive=OUT/'libPaganiSourceStorage.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun','ar','rcs',archive,*objs,*extra],check=True)
cwd=S/('xobj-s' if SERIES else 'xobj-link')/'DEVELOPMENT_ARM64_BCM2837'
out=OUT/'mach.l'
if SERIES:
    # 模板取自用 26.6 工具链跑出来的链接命令，只换目录
    line=line.replace('/xobj-link/','/xobj-s/').replace('/w/x12377/','/w/xnu-series/')
cmd=shlex.split(line)
# 换输出，追加归档
i=cmd.index('-o'); cmd[i+1]=str(out)
cmd+=[f'-Wl,-force_load,{archive}']
(OUT/'link.cmd').write_text(shlex.join(cmd))
env=dict(os.environ,DEVELOPER_DIR='/Applications/Xcode-26.6.app/Contents/Developer')
r=subprocess.run(cmd,cwd=cwd,env=env,capture_output=True,text=True)
(OUT/'link.log').write_text(r.stdout+r.stderr)
print('rc',r.returncode)
t=r.stderr
und=re.findall(r'^  "([^"]+)", referenced from:\n((?:      .*\n)+)',t,re.M)
print('未定义',len(und))
for s,w in und: print(' ',s,'<-',' '.join(x.strip() for x in w.splitlines()[:3])[:120])
print('\n'.join(l for l in t.splitlines() if 'duplicate' in l.lower() or l.startswith('ld: ')and 'warning' not in l)[:1500])
