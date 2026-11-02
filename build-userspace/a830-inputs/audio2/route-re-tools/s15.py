exec(open("fixnr.py").read())
exec(open("walk2.py").read())
fix_func("cmsmInitializeCMSessionManager")
hits, seen = walk2("cmsmInitializeCMSessionManager", maxdepth=6, stop=0x1988b4398,
                   skip=("fig_log", "os_log", "LogCurrentState", "MXInstallSysdiagnoseBlock", "CopyDebugDesc", "Sysdiagnose"))
kw = re.compile(r"(?i)(telephony|commcenter|CoreTelephony|CTCall|baseband|bluetooth|BTServer|identityservices|IDSService|locationd|CLLocation|routined|fairplay|PerfPower|powerlog|watchdog|SpringBoard|FrontBoard|backboard|mediaserverd|com\.apple\.[a-z.]+)")
res = {}
for f in seen:
    for h in idautils.FuncItems(f):
        for r in idautils.DataRefsFrom(h):
            s = idc.get_strlit_contents(r, -1, 0)
            nm = ida_name.get_name(r) or ""
            txt = None
            if s:
                txt = s.decode("latin1", "replace")
            elif nm.startswith("cfstr") or "cfstr" in nm.lower():
                pp = ida_bytes.get_qword(r + 16)
                s2 = idc.get_strlit_contents(pp, -1, 0)
                if s2: txt = s2.decode("latin1", "replace")
            elif nm.startswith("OBJC_CLASS") or "classRef" in nm:
                txt = nm
            if txt and kw.search(txt):
                res.setdefault(txt[:120], set()).add(ida_name.get_name(f))
for k in sorted(res):
    p("%-90s <- %s" % (k, sorted(res[k])[:4]))
