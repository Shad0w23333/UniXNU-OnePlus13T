#!/usr/bin/env python3
"""直接解析 arm64e 目标文件：节、符号、重定位。从早期入口走调用图，判定每个被引用的全局量在链式修复之前能不能用。"""
import collections,json,re,struct,subprocess,sys
O=sys.argv[1]
OBJS=['BootProgress','RamoopsLog','GunyahWdog','UfsLog','Entry']
ROOTS=sys.argv[2:]
RT={0:'UNSIGNED',1:'SUBTRACTOR',2:'BRANCH26',3:'PAGE21',4:'PAGEOFF12',5:'GOT_LOAD_PAGE21',6:'GOT_LOAD_PAGEOFF12',7:'POINTER_TO_GOT',8:'TLVP_PAGE21',9:'TLVP_PAGEOFF12',10:'ADDEND',11:'AUTHENTICATED_POINTER'}
def parse(path):
    d=open(path,'rb').read()
    magic,cpu,sub,ft,nc,sc,fl,_=struct.unpack_from('<8I',d,0)
    off=32;sections=[];syms=[]
    for i in range(nc):
        cmd,size=struct.unpack_from('<II',d,off)
        if cmd==0x19:
            nsects=struct.unpack_from('<I',d,off+64)[0]
            p=off+72
            for s in range(nsects):
                sn,sg=d[p:p+16].rstrip(b'\0').decode(),d[p+16:p+32].rstrip(b'\0').decode()
                addr,sz,fo,al,ro,nr,fl2=struct.unpack_from('<QQIIIII',d,p+32)
                sections.append(dict(seg=sg,sect=sn,addr=addr,size=sz,off=fo,reloff=ro,nreloc=nr,align=al))
                p+=80
        elif cmd==2:
            so,ns,st,ss=struct.unpack_from('<4I',d,off+8)
            for k in range(ns):
                sx,ty,se,de,va=struct.unpack_from('<IBBHQ',d,so+k*16)
                name=d[st+sx:d.index(b'\0',st+sx)].decode()
                syms.append(dict(name=name,type=ty,sect=se,value=va,desc=de))
        off+=size
    relocs=[]
    for i,s in enumerate(sections):
        for k in range(s['nreloc']):
            a,w=struct.unpack_from('<iI',d,s['reloff']+k*8)
            sn=w&0xffffff;pc=(w>>24)&1;ln=(w>>25)&3;ext=(w>>27)&1;ty=(w>>28)&15
            relocs.append(dict(sec=i,off=a,sym=(syms[sn]['name'] if ext else None),secnum=(sn if not ext else None),type=RT.get(ty,ty),length=ln))
    return sections,syms,relocs
fn={};owner={}
info={}
for o in OBJS:
    secs,syms,rel=parse(f'{O}/BootProgress__{o}.o')
    info[o]=(secs,syms,rel)
    tx=[i for i,s in enumerate(secs) if s['sect']=='__text'][0]
    labels=sorted((s['value'],s['name']) for s in syms if s['sect']==tx+1 and not (s['type']&0xe0) and not s['name'].startswith('ltmp') )
    starts=[v for v,_ in labels]
    # 函数边界：相同地址取第一个；附带别名
    addr2name={}
    for v,n in labels: addr2name.setdefault(v,n)
    ordered=sorted(addr2name)
    import bisect
    for v,n in addr2name.items():
        fn[n]=dict(obj=o,start=v,calls=set(),refs=collections.defaultdict(set))
        owner.setdefault(n,o)
    for s in syms:
        if s['sect'] and not (s['type']&0xe0):
            sect=secs[s['sect']-1]
            if s['name'].startswith('ltmp'): continue
            owner.setdefault(s['name'],o)
    for r in rel:
        if r['sec']!=tx: continue
        i=bisect.bisect_right(ordered,r['off'])-1
        if i<0: continue
        f=addr2name[ordered[i]]
        if r['type']=='ADDEND': continue
        if r['sym'] is None:
            # 节内部的非外部引用：记成 节名+偏移
            sec=secs[r['secnum']-1]
            if r['type']=='BRANCH26': continue
            fn[f]['refs'][f"<{sec['seg']},{sec['sect']}>"].add(r['type']);continue
        if r['type']=='BRANCH26': fn[f]['calls'].add(r['sym'])
        else: fn[f]['refs'][r['sym']].add(r['type'])
# 符号定义位置
where={}
for o,(secs,syms,rel) in info.items():
    for s in syms:
        if s['sect'] and not (s['type']&0xe0) and not s['name'].startswith('ltmp'):
            sc=secs[s['sect']-1]
            where[s['name']]=(o,sc['seg'],sc['sect'],s['value'])
# 含指针的范围：非 DWARF 节的 UNSIGNED/AUTHENTICATED_POINTER 重定位
ptrsec=collections.Counter()
ptrs=collections.defaultdict(list)
for o,(secs,syms,rel) in info.items():
    for r in rel:
        sc=secs[r['sec']]
        if sc['seg']=='__DWARF' or sc['sect']=='__text' or sc['seg']=='__LD': continue
        if r['type'] in('UNSIGNED','AUTHENTICATED_POINTER','POINTER_TO_GOT'):
            ptrsec[(o,sc['seg'],sc['sect'],r['type'])]+=1
            ptrs[(o,sc['seg'],sc['sect'])].append(sc['addr']+r['off'] if False else r['off'])
print('各目标文件里带指针修复的节：')
for k,v in sorted(ptrsec.items()): print('  ',k,v)
seen=collections.OrderedDict();st=[(r,None) for r in ROOTS][::-1]
while st:
    f,v=st.pop()
    if f in seen: continue
    seen[f]=v
    if f in fn:
        for c in sorted(fn[f]['calls'],reverse=True): st.append((c,f))
print('可达函数：',[f for f in seen if f in fn])
print('外部调用：',[ (f,seen[f]) for f in seen if f not in fn])
refs=collections.defaultdict(lambda:[set(),set()])
for f in seen:
    if f in fn:
        for s,t in fn[f]['refs'].items():
            refs[s][0].add(f);refs[s][1]|=t
kinds=collections.Counter(t for s,v in refs.items() for t in v[1])
print('引用方式：',kinds)
cls=collections.defaultdict(list)
for s,v in sorted(refs.items()):
    w=where.get(s)
    cls[f'{w[1]},{w[2]}' if w else ('节内' if s.startswith('<') else '未定义')].append(s)
for k,v in sorted(cls.items()):
    print(f'{k}: {len(v)}')
    print('   ',' '.join(x for x in v if not x.startswith('l_.str')))
bad=[]
for s,v in refs.items():
    w=where.get(s)
    if not w or w[1:3] not in ptrs and (w[0],w[1],w[2]) not in ptrs: continue
    secs,syms,rel=info[w[0]]
    same=sorted(x['value'] for x in syms if x['sect'] and secs[x['sect']-1]['seg']==w[1] and secs[x['sect']-1]['sect']==w[2])
    i=same.index(w[3]);end=same[i+1] if i+1<len(same) else 1<<62
    sc=[x for x in secs if x['seg']==w[1] and x['sect']==w[2]][0]
    hit=[p for p in ptrs[(w[0],w[1],w[2])] if w[3]<=sc['addr']+p<end]
    if hit: bad.append((s,w,len(hit)))
print('被引用且含指针的符号：',bad)
print('被引用的符号里走 GOT 或认证指针的：',[(s,sorted(v[1])) for s,v in refs.items() if v[1]-{'PAGE21','PAGEOFF12'}])
json.dump({'reach':[f for f in seen if f in fn],'class':cls},open(O+'/../early/result.json','w'),indent=0,default=list)
