#!/usr/bin/env python3
"""在链接好的内核里走 __thread_starts 的修复链，确认早期路径用到的符号范围内没有一个修复位置。"""
import bisect,collections,json,struct,subprocess,sys
S=sys.argv[1]
path=S+'/l/mach.l'
d=open(path,'rb').read()
_,cpu,sub,ft,nc,sc,fl,_=struct.unpack_from('<8I',d,0)
off=32;segs=[];sects=[]
for i in range(nc):
    cmd,size=struct.unpack_from('<II',d,off)
    if cmd==0x19:
        name=d[off+8:off+24].rstrip(b'\0').decode()
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,off+24)
        ns=struct.unpack_from('<I',d,off+64)[0]
        segs.append((name,va,vs,fo,fs))
        p=off+72
        for k in range(ns):
            sn,sg=d[p:p+16].rstrip(b'\0').decode(),d[p+16:p+32].rstrip(b'\0').decode()
            addr,sz,fo2=struct.unpack_from('<QQI',d,p+32)
            sects.append((sg,sn,addr,sz,fo2))
            p+=80
    off+=size
ts=[s for s in sects if s[1]=='__thread_starts'][0]
base=[s for s in segs if s[0]=='__TEXT'][0]
raw=d[ts[4]:ts[4]+ts[3]]
words=struct.unpack('<%dI'%(len(raw)//4),raw)
flags=words[0];stride=8 if flags&1==1 else 4
print('thread_starts',hex(flags),'链数',sum(1 for w in words[1:] if w!=0xffffffff),'步长',stride)
def f2v(fo):
    for n,va,vs,fileoff,fs in segs:
        if fileoff<=fo<fileoff+fs: return va+fo-fileoff
def v2f(va):
    for n,sva,vs,fileoff,fs in segs:
        if sva<=va<sva+fs: return fileoff+va-sva
    return None
locs=[]
for w in words[1:]:
    if w==0xffffffff: continue
    va=base[1]+w
    while True:
        fo=v2f(va)
        val=struct.unpack_from('<Q',d,fo)[0]
        locs.append((va,val))
        delta=(val>>51)&0x7ff
        if not delta: break
        va+=delta*stride
locs.sort()
print('修复位置',len(locs),'认证',sum(1 for _,v in locs if v>>63))
c=collections.Counter()
for va,v in locs:
    for sg,sn,a,sz,_ in sects:
        if a<=va<a+sz: c[(sg,sn)]+=1;break
for k,v in sorted(c.items()): print(' ',k,v)
# 早期符号范围
sym=subprocess.check_output(['nm','-n',path],text=True).splitlines()
table=[]
for l in sym:
    p=l.split()
    if len(p)==3:
        table.append((int(p[0],16),p[2]))
table.sort()
names=[n for _,n in table];addrs=[a for a,_ in table]
res=json.load(open(S+'/early/result.json'))
want=[x for k,v in res['class'].items() for x in v if not x.startswith('l_.str') and not x.startswith('<')]
locva=[v for v,_ in locs]
miss=[];hits=[]
for s in want:
    if s not in names: miss.append(s);continue
    i=names.index(s)
    lo=addrs[i];hi=addrs[i+1]
    j=bisect.bisect_left(locva,lo)
    if j<len(locva) and locva[j]<hi: hits.append((s,hex(lo),hex(hi),hex(locva[j])))
print('早期符号',len(want),'链接产物里没找到（局部符号被去掉）',miss[:12],'落在修复位置的',hits)
# 早期函数：反汇编链接产物，看有没有对 __got/__auth_got/__auth_ptr 的访问
