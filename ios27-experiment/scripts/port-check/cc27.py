#!/usr/bin/env python3
"""Compile every driver source against the patched xnu-12377 arm64e compile records (scratch only)."""
import json,os,re,subprocess,sys,concurrent.futures as cf
from pathlib import Path
S=Path(sys.argv[1]); only=set(sys.argv[2:])
T=Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree')
D=T/'drivers'; K=T/'kits'; B=T/'build'
E=Path('/Users/xiaochoumao/unixnu-13t/ios27-experiment'); ED=E/'drivers'; EK=E/'kits'; GEN=S/'gen'; GEN.mkdir(exist_ok=True)
SERIES=os.environ.get('SERIES')=='1'
X=S/('xobj-s' if SERIES else 'xobj')/'DEVELOPMENT_ARM64_BCM2837'; XS=S/('xnu-series' if SERIES else 'x12377')
OUT=S/('o27s' if SERIES else 'o27'); OUT.mkdir(exist_ok=True)
def rec(p): return json.loads((X/p).read_text().strip().rstrip(','))
CXX=rec('iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'); CC=rec('osfmk/DEVELOPMENT/startup.o.json'); BSD=rec('bsd/DEVELOPMENT/kern_exec.o.json')
WARN=['-Wall','-Wextra','-Wno-unused-parameter','-Wno-c99-designator','-Wno-reorder-init-list','-Wno-deprecated-enum-enum-conversion','-Wno-unnecessary-virtual-specifier','-Wno-sign-conversion','-Wno-shorten-64-to-32','-Wno-unused-function','-ferror-limit=0']
def cmd(r,src,obj,extra):
    a=r['arguments'];c=[];i=0
    while i<len(a):
        v=a[i]
        if v==r['file']: c.append(str(src))
        elif v=='-o': c+=['-o',str(obj)];i+=1
        elif v in('-MF','-MJ','-MT','-MQ'): i+=1
        elif v in('-MD','-MMD','-MP'): pass
        elif v=='-Xclang' and a[i+1].startswith('-W'): i+=1
        elif not v.startswith('-W'): c.append(v)
        i+=1
    return c+WARN+extra
OSF=['-I',str(XS/'osfmk')]
PER=['-I',str(B/'pagani-peripherals'),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-I',str(D/'QcomGeniSpi')]
STOR=['-I',str(B/'storage-abi/include'),'-I',str(EK/'PaganiVerifiedRoot/include'),'-I',str(EK),'-I',str(K),'-I',str(ED/'NativeUfs'),'-DNATIVE_VERIFIED_BLOCK=1','-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1','-mgeneral-regs-only']
SRC=['-DNATIVE_UFS_SOURCE_API=1','-DNATIVE_VERIFIED_POLICY_HANDOFF=1']
UFS=['-DNATIVE_UFS_SM8750=1','-DUFS_ROOT_PARTITION="unixnu"','-DNATIVE_UFS_READ_ONLY=1','-DNATIVE_UFS_LOG_WRITES=1']
AD=D/'Adreno830/platform/sm8750'
GROUP={}
def add(g,src,kind,extra=()): GROUP.setdefault(g,[]).append((Path(src),kind,list(extra)+OSF))
add('NativeAES',D/'NativeAES/NativeAES.cpp','cxx')
add('NativeKeyStoreEvents',ED/'NativeKeyStoreEvents/NativeKeyStoreEvents.cpp','cxx',['-mgeneral-regs-only'])
add('NativeKeyStoreEvents',ED/'NativeKeyStoreEvents/KeyStoreProbe.cpp','cxx',['-mgeneral-regs-only'])
add('Stage7Probe',D/'Stage7Probe/Stage7Probe.cpp','cxx',['-mgeneral-regs-only','-I',str(B/'native-legacy-display')])
for n in ('NativeLegacyDisplay','NativeGOPPresenter','DpuProbe'):
    add('NativeMobileDisplay',D/'NativeMobileDisplay'/(n+'.cpp'),'cxx',['-mgeneral-regs-only','-I',str(B/'native-legacy-display')])
add('PaganiInput',D/'PaganiInput/PaganiInput.cpp','cxx',['-mgeneral-regs-only']+PER)
add('PaganiInput',D/'PaganiInput/S3910.c','c',['-mgeneral-regs-only']+PER)
add('PaganiInput',D/'QcomGeniSpi/GeniSpi.c','c',['-mgeneral-regs-only']+PER)
add('PaganiPanel',D/'PaganiPanel/PaganiPanel.cpp','cxx',['-mgeneral-regs-only']+PER)
add('NativeUfs',ED/'NativeUfs/NativeUfs.cpp','cxx',UFS+STOR+SRC)
add('NativeUfs',ED/'NativeUfs/SourceStorage.cpp','cxx',UFS+STOR+SRC)
add('NativeUfs',ED/'NativeUfs/SourceRootBinding.cpp','cxx',UFS+STOR+SRC)
add('NativeUfs',ED/'NativeUfs/SourceRootPolicies.cpp','cxx',UFS+STOR+SRC)
add('NativeUfs',GEN/'IOBlockStorageDevice.cpp','cxx',UFS+STOR+SRC)
add('NativeUfsPlain',ED/'NativeUfs/NativeUfs.cpp','cxx',UFS+STOR)
add('NativeRamBlock',ED/'NativeRamBlock/NativeRamBlock.cpp','cxx',UFS+STOR+SRC)
add('NativeVirtioBlock',D/'NativeVirtioBlock/NativeVirtioBlock.cpp','cxx',UFS+['-I',str(B/'storage-abi/include'),'-I',str(K/'PaganiVerifiedRoot/include'),'-I',str(K),'-I',str(D/'NativeVirtioBlock'),'-DNATIVE_VERIFIED_BLOCK=1','-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1','-mgeneral-regs-only'])
add('PaganiArmCpu',ED/'PaganiArmCpu/ArmCpu.cpp','cxx')
add('PaganiArmDTPlatform',ED/'PaganiArmDTPlatform/ArmDTPlatform.cpp','cxx')
add('ShengPlatformPower',D/'ShengPlatformPower/ShengPlatformPower.cpp','cxx',['-mgeneral-regs-only','-I',str(B/'sheng-platform-power')])
BP=['-DXNU_BOOT_PROGRESS=1','-DXNU_EARLY_BOOT_PROGRESS=1','-ffreestanding','-fno-builtin','-fno-stack-protector','-mgeneral-regs-only','-fno-jump-tables','-I',str(ED/'PaganiDarwinBoot/DarwinBootPkg/Driver'),'-I',str(B/'boot-progress')]
for n in ('BootProgress','RamoopsLog','GunyahWdog','UfsLog','WeakDefaults'): add('BootProgress',ED/'BootProgress'/(n+'.c'),'c',BP)
add('LinkStub',S/'l/stubs.c','c',['-Wno-missing-prototypes'])
add('LinkStub',S/'l/RootAnchor.c','c')
add('PaganiVerifiedRoot',EK/'PaganiVerifiedRoot/src/verified_root.c','c',['-I',str(EK/'PaganiVerifiedRoot/include')])
add('PaganiVerifiedRoot',EK/'PaganiVerifiedRoot/adapters/xnu_crypto.c','c',['-I',str(EK/'PaganiVerifiedRoot/include')])
def objs(dirn,srcdir,extra):
    for o in sorted((B/dirn/'obj').glob('*.o')):
        n=o.name.split('.')[0]
        if '.late.' in o.name or 'bindings' in n: continue
        cand=[srcdir/(n+'.cpp'),srcdir/(n+'.c')]
        src=[c for c in cand if c.exists()]
        if not src: print('no source',o);continue
        yield src[0]
AU=D/'PaganiAudio'; AS=D/'PaganiAdsp'
for s in objs('pagani-adsp',AS,0): add('PaganiAdsp',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(AS),'-I',str(AD)])
for s in objs('pagani-audio',AU,0): add('PaganiAudio',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(AU),'-mgeneral-regs-only'])
for s in objs('pagani-battery',D/'PaganiBattery',0): add('PaganiBattery',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(D/'PaganiBattery'),'-I',str(AS),'-I',str(AD),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-mgeneral-regs-only'])
for s in objs('pagani-haptics',D/'PaganiHaptics',0): add('PaganiHaptics',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(D/'PaganiHaptics'),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-mgeneral-regs-only'])
for s in objs('pagani-sensors',D/'PaganiSensors',0): add('PaganiSensors',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(D/'PaganiSensors'),'-I',str(AS),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-I',str(B/'pagani-peripherals'),'-I',str(D/'PaganiInput'),'-I',str(AD),'-mgeneral-regs-only'])
for s in objs('pagani-speaker',D/'PaganiSpeaker',0): add('PaganiSpeaker',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(D/'PaganiSpeaker'),'-I',str(AS),'-I',str(AU),'-I',str(D/'PaganiInput'),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-I',str(AD),'-mgeneral-regs-only'])
for s in objs('pagani-torch',D/'PaganiTorch',0): add('PaganiTorch',s,'cxx' if s.suffix=='.cpp' else ('bsd' if 'Sysctl' in s.name else 'c'),['-I',str(D/'PaganiTorch'),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-mgeneral-regs-only'])
for s in objs('pagani-wifi',D/'PaganiWifi',0): add('PaganiWifi',s,'cxx' if s.suffix=='.cpp' else 'c',['-I',str(D/'PaganiWifi'),'-I',str(AD),'-I',str(K/'Sm8750Platform'),'-I',str(K/'oneplus13t'),'-I',str(D/'PaganiWifi/Ath12kWifi/include'),'-mgeneral-regs-only'])
def job(item):
    g,(src,kind,extra)=item
    obj=OUT/(g+'__'+src.stem+'.o')
    r={'cxx':CXX,'c':CC,'bsd':BSD}[kind]
    c=cmd(r,src,obj,extra)
    # BootProgress 按补丁系列打完的树取头文件，排在记录自带的路径之前
    if g=='BootProgress' and not SERIES:
        c=[c[0],'-I',str(S/'xnu-series/osfmk')]+c[1:]
    p=subprocess.run(c,cwd=r['directory'],capture_output=True,text=True)
    log=OUT/(g+'__'+src.stem+'.log'); log.write_text(p.stdout+p.stderr)
    t=p.stderr
    errs=[l for l in t.splitlines() if ': error:' in l or ' error: ' in l]
    warns=[l for l in t.splitlines() if ': warning:' in l]
    return g,src.name,p.returncode,len(errs),len(warns),errs[:400]
items=[(g,i) for g,v in GROUP.items() for i in v if not only or g in only]
res=[]
with cf.ThreadPoolExecutor(8) as ex:
    for r in ex.map(job,items): res.append(r)
json.dump(res,open(S/'cc27.json','w'),indent=0)
tot=0
for g,n,rc,e,w,_ in res:
    print(f'{g:22s} {n:30s} rc={rc} errors={e} warnings={w}'); tot+=e
print('files',len(res),'failed',sum(1 for r in res if r[2]),'errors',tot)
