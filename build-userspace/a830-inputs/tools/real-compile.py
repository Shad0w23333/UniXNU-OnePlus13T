"""real-compile.py SOURCE...: compile each SOURCE (a patched copy of an xnu bsd/kern file) with the exact clang command of the build's
compile record for that file name (bsd/DEVELOPMENT/<name>.o.json; kdebug.o.json when there is none), to a throwaway object. The
per-file record matters: kern_exec.c carries -D XNU_OWNER_DATA_MOUNT=1 and friends, without which the owner Data code (and anything
patched into it) is compiled out. Prints errors and the warning count."""
import json, os, subprocess, sys, tempfile
O = ('/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/build/xnu/ios-obj/'
     'DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT')
X = '/Users/xiaochoumao/unixnu-13t/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree/.cache/xnu-work'
status = 0
for source in sys.argv[1:]:
    name = os.path.basename(source).rsplit('.', 1)[0]
    path = f'{O}/{name}.o.json'
    if not os.path.exists(path):
        path = f'{O}/kdebug.o.json'
    record = json.JSONDecoder().raw_decode(open(path).read())[0]
    out = tempfile.mktemp(suffix='.o')
    a, skip = [], False
    for x in record['arguments']:
        if skip:
            skip = False
            continue
        if x == record['file']:
            a.append(source)
            continue
        if x == '-o':
            a += ['-o', out]
            skip = True
            continue
        if x.startswith('-MD') or x.startswith('-MF') or x == '-MMD':
            continue
        a.append(x)
    a += ['-iquote', X + '/bsd/kern']
    r = subprocess.run(a, cwd=record['directory'], capture_output=True, text=True)
    errs = [l for l in r.stderr.splitlines() if ' error: ' in l]
    warns = [l for l in r.stderr.splitlines() if ' warning: ' in l]
    print(f'{os.path.basename(source)} record {os.path.basename(path)} rc {r.returncode} errors {len(errs)} warnings {len(warns)}')
    for l in errs[:20]:
        print('  ' + l)
    if os.path.exists(out):
        os.unlink(out)
    status |= r.returncode != 0
sys.exit(status)
