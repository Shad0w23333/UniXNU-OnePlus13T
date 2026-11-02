import json, runpy, subprocess, sys
from pathlib import Path
root=Path('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree')
Q=Path(sys.argv[1]); P=Q/'new'
source=P/'drivers/PaganiPanel/PaganiPanel.cpp'; obj=Q/'obj/PaganiPanel.o'
record=json.loads((root/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
compile_command=runpy.run_path(str(root/'scripts/build-arm-platform.py'))['compile_command']
arguments=iter(compile_command(record,source,obj))
command=[]
for value in arguments:
    if value=='-Xclang':
        option=next(arguments)
        if not option.startswith('-W'): command+=[value,option]
    elif not value.startswith('-W'): command.append(value)
command+=['-mgeneral-regs-only','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-c99-designator','-Wno-reorder-init-list','-Wno-deprecated-enum-enum-conversion','-Wno-unnecessary-virtual-specifier',
          '-I',str(P/'kits/Sm8750Platform'),'-I',str(root/'build/pagani-peripherals'),'-I',str(root/'.cache/xnu-work/osfmk'),
          '-I',str(root/'kits/Sm8750Platform'),'-I',str(root/'kits/oneplus13t'),'-I',str(root/'drivers/QcomGeniSpi')]
print(' '.join(str(c) for c in command)[:400])
print('writes:',[c for c in command if str(c).endswith(('.o','.d','.dia','.json'))][:6])
r=subprocess.run(command,cwd=record['directory'],capture_output=True,text=True)
print(r.returncode); print(r.stdout[-2000:]); print(r.stderr[-4000:])
