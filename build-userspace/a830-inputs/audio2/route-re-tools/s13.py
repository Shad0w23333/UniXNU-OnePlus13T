exec(open("fixnr.py").read())
exec(open("walk2.py").read())
fix_func("cmsmInitializeCMSessionManager")
hits, seen = walk2("cmsmInitializeCMSessionManager", maxdepth=5, stop=0x1988b4398,
                   skip=("fig_log", "os_log", "LogCurrentState", "MXInstallSysdiagnoseBlock", "CopyDebugDesc", "Sysdiagnose"))
p("visited", len(seen))
import collections
agg = collections.OrderedDict()
for d, path, h, kind, n in hits:
    key = (path[-1], n)
    if key not in agg:
        agg[key] = (d, " > ".join(path[-4:]), h, kind)
for (fnm, n), (d, path, h, kind) in agg.items():
    p("%d %-90s %s %s -> %s" % (d, path[:90], h, kind, n))
