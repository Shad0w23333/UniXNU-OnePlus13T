import os, plistlib, sys
root, sysos = 'osmnt-21H461', 'systemos-21H461'
miss = []
tot = 0
for d in ('System/Library/LaunchDaemons', 'Library/LaunchDaemons', 'System/Library/NanoLaunchDaemons'):
    p = os.path.join(root, d)
    if not os.path.isdir(p): continue
    for f in sorted(os.listdir(p)):
        if not f.endswith('.plist'): continue
        try:
            j = plistlib.load(open(os.path.join(p, f), 'rb'))
        except Exception as e:
            continue
        prog = j.get('Program') or (j.get('ProgramArguments') or [None])[0]
        if not isinstance(prog, str): continue
        tot += 1
        rp = prog.lstrip('/')
        ex = os.path.lexists(os.path.join(root, rp)) or os.path.lexists(os.path.join(sysos, rp))
        if rp.startswith('System/Cryptexes/OS/'):
            ex = os.path.exists(os.path.join(sysos, rp[len('System/Cryptexes/OS/'):]))
        link = os.path.islink(os.path.join(root, rp))
        tgt = os.readlink(os.path.join(root, rp)) if link else ''
        if not ex or link:
            miss.append((j.get('Label'), prog, tgt, ex))
print(tot)
for m in miss[:60]:
    print(m)
