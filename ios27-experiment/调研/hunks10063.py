#!/usr/bin/env python3
"""把 sheng 的 12377 bring-up 补丁按文件、按块拆开，逐块对我们打过补丁的 xnu-10063 副本做 dry-run。只读源树，副本在草稿目录。"""
import os,re,shutil,subprocess,sys
TOP='/Users/xiaochoumao/unixnu-13t'
SRC=TOP+'/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work'
PATCH=TOP+'/参考资料/上游-20261031/UniXNU-UniXNU-sheng/patches/xnu-12377/zzzz-sheng-ipados27-bringup.patch'
OUT='/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/5559c563-618d-4813-9848-be3768908d8c/scratchpad/upstream-merge/ipados27-parity/x10063'
text=open(PATCH,errors='replace').read()
files=re.split(r'(?m)^(?=diff --git )',text)
files=[f for f in files if f.startswith('diff --git')]
total=ok=0
for f in files:
    m=re.match(r'diff --git a/(\S+) b/',f)
    path=m.group(1)
    dst=os.path.join(OUT,path)
    os.makedirs(os.path.dirname(dst),exist_ok=True)
    srcf=os.path.join(SRC,path)
    if not os.path.exists(srcf):
        print(f'{path}: 10063 无此文件'); continue
    shutil.copy2(srcf,dst)
    head=f[:f.index('@@')]
    hunks=re.split(r'(?m)^(?=@@ )',f[f.index('@@'):])
    hunks=[h for h in hunks if h.startswith('@@')]
    res=[]
    for i,h in enumerate(hunks):
        p=head+h
        r=subprocess.run(['/opt/homebrew/bin/gpatch','-p1','--dry-run','-f','--fuzz=3','-d',OUT],input=p.encode(),capture_output=True)
        o=r.stdout.decode(errors='replace')
        st='OK' if r.returncode==0 else 'FAIL'
        if 'fuzz' in o and st=='OK': st='fuzz'
        ctx=re.match(r'@@ [^@]* @@ ?(.*)',h)
        res.append(st)
        total+=1; ok+= st!='FAIL'
    print(f'{path}: '+' '.join(res))
print('total',total,'ok',ok)
