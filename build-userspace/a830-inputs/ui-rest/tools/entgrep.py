import re,sys,collections
pats=[b'com.apple.videotoolbox.decode-in-process', b'coremedia.videocodecd', b'com.apple.coremedia.decompressionsession', b'videotoolbox.hardwarevideodecoder']
hits=collections.defaultdict(list)
seen=set()
for l in open('ui-rest/tools/cdmap.tsv'):
    h,i,p=l.rstrip('\n').split('\t')
    if p in seen: continue
    seen.add(p)
    try:
        d=open(p,'rb').read()
    except: continue
    for x in pats:
        if x in d:
            hits[x].append(p.replace('osmnt-21H461','').replace('systemos-21H461','SYS'))
for x in pats:
    print(x.decode(), len(hits[x]))
    for p in hits[x][:40]:
        print('   ',p)
