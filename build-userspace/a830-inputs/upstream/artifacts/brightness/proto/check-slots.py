import struct,sys
sys.path.insert(0,'/Users/xiaochoumao/unixnu-13t/UniXNU-Pagani-Drivers/kits/NativePrelink')
from native_prelink import macho
from pathlib import Path
path=Path(sys.argv[1]); classname='PaganiPanel'; count=169
raw=path.read_bytes(); image=macho(raw)
st=next(p for c,p,_ in image['commands'] if c==2)
sy,total,strings,_=struct.unpack_from('<4I',raw,st+8)
symbols=[]
for i in range(total):
    ix,kind,_,_,address=struct.unpack_from('<IBBHQ',raw,sy+i*16)
    symbols.append((raw[strings+ix:raw.index(b'\0',strings+ix)].decode(),address,kind))
vtable=next(a for n,a,_ in symbols if n==f'__ZTV{len(classname)}{classname}')
relocs={}
for segment in image['segments']:
    for sec in segment['sections']:
        if not sec['va']<=vtable<sec['va']+sec['size']: continue
        offset,length=struct.unpack_from('<2I',raw,sec['command_offset']+56)
        for i in range(length):
            at,bits=struct.unpack_from('<2I',raw,offset+i*8)
            if bits>>27&1 and bits>>28==0 and bits>>25&3==3:
                relocs[sec['va']+at]=symbols[bits&0xffffff][0]
slots=[relocs.get(vtable+16+i*8) for i in range(count)]
print('all slots',all(slots),'extent ok',vtable+16+count*8 not in relocs)
own=[i for i,n in enumerate(slots) if n and n.startswith((f'__ZN{len(classname)}{classname}',f'__ZNK{len(classname)}{classname}'))]
print('own',own, own==[0,1,7,14,21,44,86,87,111,168])
print('undefined with Backlight',[n for n,_,k in symbols if k==1 and 'AppleARMBacklight' in n])
print('undefined others',[n for n,_,k in symbols if k==1 and 'AppleARMBacklight' not in n][:60])
