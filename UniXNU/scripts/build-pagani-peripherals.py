#!/usr/bin/env python3
"""Qualify the pinned HID/backlight ABI and build physical-input/panel objects."""
import argparse
import json
from pathlib import Path
import struct
import subprocess

from original_stage7 import OriginalImage
from native_prelink import macho

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/pagani-peripherals'


def require(value, reason):
    if not value:
        raise ValueError(reason)


def run(command):
    return subprocess.check_output([str(x) for x in command], text=True, timeout=120)


def symbols_and_slots(path, classname, count):
    raw = path.read_bytes(); image = macho(raw)
    st = next(p for c, p, _ in image['commands'] if c == 2)
    sy, total, strings, _ = struct.unpack_from('<4I', raw, st + 8)
    symbols = []
    for i in range(total):
        ix, kind, _, _, address = struct.unpack_from('<IBBHQ', raw, sy + i * 16)
        symbols.append((raw[strings+ix:raw.index(b'\0', strings+ix)].decode(), address, kind))
    vtable = next(a for n, a, _ in symbols if n == f'__ZTV{len(classname)}{classname}')
    relocs = {}
    for segment in image['segments']:
        for sec in segment['sections']:
            if not sec['va'] <= vtable < sec['va'] + sec['size']:
                continue
            offset, length = struct.unpack_from('<2I', raw, sec['command_offset'] + 56)
            for i in range(length):
                at, bits = struct.unpack_from('<2I', raw, offset + i * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocs[sec['va'] + at] = symbols[bits & 0xffffff][0]
    slots = [relocs.get(vtable + 16 + i * 8) for i in range(count)]
    require(all(slots) and vtable + 16 + count * 8 not in relocs, f'{classname}: exact vtable extent')
    return symbols, slots


def qualify(original):
    read = original.read; names = original.names
    service = original.pointers(names['__ZTV9IOService'] + 16, 168)
    hid = original.pointers(0xfffffff005d045c0, 207)
    require([i for i in range(168) if hid[i] != service[i]] ==
            [0,1,7,12,14,21,23,76,81,82,86,87,91,92,93,101,132,140], 'Original HID overrides')
    require([i for i, a in enumerate(hid) if a == names['___cxa_pure_virtual']] == [180] and
            all(hid) and read(0xfffffff005d045c0 + 207 * 8, 24) == bytes(24), 'Original HID extent')
    require(read(0xfffffff004ad94f8,32).hex() ==
            'fd7bbfa9fd0300912bd90094489100f008011791080000f9fd7bc1a8c0035fd6', 'HID protected constructor')
    require(read(0xfffffff004ad9518,4).hex() == '28d90014', 'HID protected destructor')
    require(original.pointers(0xfffffff005d00f10,2) ==
            (names['__ZN9IOServiceC2EPK11OSMetaClass'],names['__ZN9IOServiceD2Ev']), 'HID base bindings')
    require(read(0xfffffff004ad9aec,52).hex() ==
            '680240f908a142f9e00313aae10314aa00013fd6680240f9603d003608d142f9e1630091e00313aa00013fd6a02d0035e00f40f9',
            'HID handleStart and report-descriptor call boundaries')
    require(hid[181] == 0xfffffff004add0c4 and read(0xfffffff004add110,28).hex() ==
            '680240f9081543f9e00313aae20316aae30315aae40314aa00013fd6', 'HID report dispatch boundary')
    require(read(0xfffffff0049abc1c,52).hex() ==
            'fd7bbfa9fd030091e14900940899009008613491080000f91f6000f91f8008391f9401f91fb001f91f6803b9fd7bc1a8c0035fd6',
            'Backlight constructor initializes exactly its own storage')
    require(read(0xfffffff0049abc84,4).hex() == 'cc490014' and
            original.pointers(0xfffffff005cba2b8,2) ==
            (names['__ZN9IOServiceC2EPK11OSMetaClass'],names['__ZN9IOServiceD2Ev']), 'Backlight IOService constructor/destructor chain')
    require(read(0xfffffff0049be3a8,24).hex() ==
            'f0970090105e41f900021fd6f0970090106241f900021fd6', 'Backlight base thunks')
    require(original.pointers(0xfffffff005cba220,1)[0] == names['__ZN9IOService10gMetaClassE'] and
            read(0xfffffff004291985,18) == b'AppleARMBacklight\0' and
            read(0xfffffff0049b52d8,4) == struct.pack('<I',0x52806e03), 'Backlight name, parent and 0x370 allocation')
    backlight = original.pointers(0xfffffff005ccbd18,169)
    require(all(backlight) and read(0xfffffff005ccbd18+169*8,24) == bytes(24), 'Backlight 169-slot extent')
    return service, hid


def headers():
    header = '''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Generated only after exact original-image ABI qualification.
#pragma once
#include <IOKit/IOService.h>
#include <IOKit/IOMemoryDescriptor.h>
class IOHIDDevice : public IOService {
 OSDeclareAbstractStructors(IOHIDDevice)
 unsigned char originalPrivate[0xd0-sizeof(IOService)];
public:
 using IOService::setProperty;
 IOReturn Dispatch(IORPC) override;
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 bool setProperty(const OSSymbol *,OSObject *) override;
 bool willTerminate(IOService *,IOOptionBits) override;
 IOReturn configureReport(IOReportChannelList *,IOReportConfigureAction,void *,void *) override;
 IOReturn updateReport(IOReportChannelList *,IOReportUpdateAction,void *,void *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool handleOpen(IOService *,IOOptionBits,void *) override;
 void handleClose(IOService *,IOOptionBits) override;
 bool handleIsOpen(const IOService *) const override;
 bool matchPropertyTable(OSDictionary *,SInt32 *) override;
 IOReturn message(UInt32,IOService *,void *) override;
 IOReturn newUserClient(task_t,void *,UInt32,OSDictionary *,IOUserClient **) override;
'''
    prototypes = {171:'OSString *newTransportString() const', 173:'OSString *newProductString() const',
                  180:'IOReturn newReportDescriptor(IOMemoryDescriptor **) const=0',
                  181:'IOReturn handleReport(IOMemoryDescriptor *,unsigned,IOOptionBits)'}
    for i in range(168,207):
        header += ' virtual ' + prototypes.get(i,f'void opaque{i}()') + ';\n'
    header += '};\nstatic_assert(sizeof(IOHIDDevice)==0xd0,"Original HID storage extent");\n'
    (OUT/'OriginalHidDeviceABI.h').write_text(header)
    (OUT/'OriginalHidEventABI.h').write_text('''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Generated after qualifying the exact 21H461 event tables and field accesses.
#pragma once
#include <stdint.h>
enum { PAGANI_HID_DRIVER_SLOTS=220 };
extern "C" {
extern uintptr_t pagani_original_hid_driver_vtable[];
extern uintptr_t pagani_original_hid_event_vtable[];
}
''')
    (OUT/'OriginalBacklightABI.h').write_text('''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opaque base storage and genuine metadata. The owned subclass uses IOService lifecycle.
#pragma once
#include <IOKit/IOService.h>
class AppleARMBacklight : public IOService {
 OSDeclareAbstractStructors(AppleARMBacklight)
 unsigned char originalPrivate[0x370-sizeof(IOService)];
public:
 virtual void unqualifiedBacklightMethod()=0;
};
static_assert(sizeof(AppleARMBacklight)==0x370,"Original backlight storage extent");
''')


def qualify_events(original):
    read=original.read
    driver=original.pointers(0xfffffff005d06a68,220)
    event=original.pointers(0xfffffff005d07318,37)
    require(all(driver) and read(0xfffffff005d06a68-16,16)==bytes(16) and
            read(0xfffffff005d06a68+220*8,24)==bytes(24), 'Original event-driver 220-slot table and prefix')
    require(all(event) and read(0xfffffff005d07318+37*8,24)==bytes(24), 'Original event 37-slot table')
    require(read(0xfffffff004af09bc,12).hex()=='a89000d008a12991080000f9' and
            read(0xfffffff004afb130,12).hex()=='6890009008610c91080000f9', 'Original event-driver/event constructor vptr stores')
    require(driver[198]==0xfffffff004aface8 and
            read(driver[198],32).hex()=='f85fbca9f65701a9f44f02a9fd7b03a9fdc30091f30302aaf40301aaf50300aa',
            'Original dispatchEvent takes self, event and options')
    require(read(0xfffffff004afade4,44).hex()==
            '489000f008013891032143f9e00315aae10314aae20313aafd7b43a9f44f42a9f65741a9f85fc4a860001fd6',
            'Original dispatchEvent forwards the same arguments to its event-service base')
    require([event[i] for i in (18,23,24,25)]==
            [0xfffffff004afcf90,0xfffffff004afcfb8,0xfffffff004afefdc,0xfffffff004afe4c4] and
            read(event[18],12).hex()=='080840f9000540b9c0035fd6', 'Original event type/integer/setter/fixed-value slots')
    require(read(0xfffffff004afcf50,4).hex()=='600e00f9' and
            read(0xfffffff004afb460,24).hex()=='600e40f9a00000b4080040f9081540f900013fd67f0e00f9',
            'Original event children store and release at offset 24')
    return {'_pagani_original_hid_driver_vtable':0xfffffff005d06a68,
            '_pagani_original_hid_event_vtable':0xfffffff005d07318}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernelcache',type=Path,required=True)
    parser.add_argument('--audit-only',action='store_true',help='Validate the original ABI and generate headers without a macOS compiler')
    args=parser.parse_args()
    original=OriginalImage(args.kernelcache)
    service,hid=qualify(original)
    bindings=qualify_events(original)
    OUT.mkdir(parents=True,exist_ok=True);headers()
    if args.audit_only:
        print('PASS: pinned HID/backlight and event-dispatch ABI; headers generated; no driver compilation')
        return
    for classname,base,methods,count,expected in [
            ('PaganiInput','IOHIDDevice',hid,207,[0,1,7,14,86,87,111,171,173,180]),
            ('PaganiPanel','AppleARMBacklight',service,169,[0,1,7,14,21,44,86,87,111,168])]:
        print(run(['python3',ROOT/'scripts/build-stage7-driver.py',classname]),end='')
        symbols,slots=symbols_and_slots(OUT/(classname+'.o'),classname,count)
        own=[];qualified={}
        if base=='IOHIDDevice':
            qualified.update({'__ZN11IOHIDDeviceC2EPK11OSMetaClass':0xfffffff004ad94f8,
                              '__ZN11IOHIDDeviceD2Ev':0xfffffff004ad9518,
                              '__ZN11IOHIDDevice10gMetaClassE':0xfffffff006ae6fa0,
                              '__ZTV11IOHIDDevice':0xfffffff005d045b0})
        else:
            qualified.update({'__ZN17AppleARMBacklightC2EPK11OSMetaClass':0xfffffff0049abc1c,
                              '__ZN17AppleARMBacklightD2Ev':0xfffffff0049abc84,
                              '__ZN17AppleARMBacklight10gMetaClassE':0xfffffff006ade638})
        for i,name in enumerate(slots):
            if name.startswith((f'__ZN{len(classname)}{classname}',f'__ZNK{len(classname)}{classname}')):
                own.append(i)
            elif base=='IOHIDDevice' and '11IOHIDDevice' in name:
                qualified[name]=methods[i]
            else:
                require(i<168 and original.names.get(name)==service[i], f'{classname}: native base slot {i}')
        require(own==expected,f'{classname}: only qualified/guarded overrides: {own}')
        undefined={n for n,_,kind in symbols if kind==1 and base in n}
        require(undefined<=qualified.keys(),f'{classname}: no unknown original imports: {undefined-qualified.keys()}')
        require(all(n in undefined for n in qualified if 'C2E' in n or 'D2E' in n or 'gMetaClass' in n),f'{classname}: actual original ancestry used')
        bindings.update({n:qualified[n] for n in undefined})
    for component in ('PaganiS3910','PaganiGeniSpi'):
        print(run(['python3',ROOT/'scripts/build-stage7-driver.py',component]),end='')
    asm=OUT/'peripheral-bindings.S'
    asm.write_text(''.join(f'.globl {n}\n.set {n}, {v:#x}\n' for n,v in sorted(bindings.items())))
    sdk=run(['xcrun','--sdk','iphoneos','--show-sdk-path']).strip()
    run(['xcrun','clang','-target','arm64-apple-ios17.0','-isysroot',sdk,'-c',asm,'-o',OUT/'peripheral-bindings.o'])
    llvm=Path(run(['brew','--prefix','llvm']).strip())/'bin/llvm-objcopy'
    for classname in ('PaganiInput','PaganiPanel'):
        obj=OUT/(classname+'.o');late=OUT/(classname+'.late.o')
        run([llvm,'--remove-section=__DATA,__mod_init_func',f'--globalize-symbol=__GLOBAL__sub_I_{classname}.cpp',obj,late])
        def text(path):
            raw=path.read_bytes();image=macho(raw)
            sec=next(s for g in image['segments'] for s in g['sections'] if s['name']=='__text')
            return raw[sec['fileoff']:sec['fileoff']+sec['size']]
        require(text(obj)==text(late),'Late registration preserves compiled instructions')
    (OUT/'abi-audit.json').write_text(json.dumps(dict(original_sha256=original.sha256,
        hid_slots=207,event_driver_slots=220,event_slots=37,backlight_slots=169,
        bindings=bindings,runtime_qualified=False),indent=2)+'\n')
    print('PASS: owned physical-input/panel objects and exact original ABI bindings')


if __name__=='__main__':
    main()
