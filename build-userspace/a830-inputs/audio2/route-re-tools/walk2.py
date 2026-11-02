import re
BLOCKING = re.compile(r"(dispatch_sync|dispatch_barrier_sync|dispatch_semaphore_wait|dispatch_group_wait|dispatch_block_wait|send_message_with_reply_sync|CFRunLoopRun|pthread_cond_wait|pthread_cond_timedwait|_mach_msg\b|usleep|_sleep\b|nanosleep|FigSemaphoreWait|MXDispatchSync|MXDispatchAsyncAndWait|synchronousRemoteObjectProxy|sendSynchronous|WaitForMatching|IOKitWaitQuiet|IOServiceWaitQuiet|xpc_connection_send_barrier|CMSessionMgrWait|FigConditionVariableWait)")
DAEMON = re.compile(r"(?i)(CTServer|CoreTelephony|CTTelephony|_CTCall|commcenter|IDSService|IDSIDQuery|IDS[A-Z]|Bluetooth|BTSession|BTDevice|CBCentral|CLLocation|locationd|routined|FairPlay|fairplay|PerfPower|PowerLog|PLLog|watchdog|SBSGet|SBS[A-Z]|FBSDisplay|BKS|BKSHID|MobileGestalt|MGCopy|MGGet|xpc_connection_create|initWithMachServiceName|bootstrap_look_up|notify_register|IOServiceGetMatching|IOServiceOpen|AudioObject)")
def invoke_of_global_block(addr):
    # global block literal: isa, flags/reserved, invoke at +16
    inv = ida_bytes.get_qword(addr + 16) & 0xffffffffff
    if ida_funcs.get_func(inv):
        return inv
    return None
_objc_cache = {}
def objc_impls(sel):
    if sel in _objc_cache:
        return _objc_cache[sel]
    res = []
    pat = " " + sel + "]"
    for ea, nm in idautils.Names():
        if nm.startswith(("-[", "+[")) and nm.endswith(pat):
            res.append(ea)
    _objc_cache[sel] = res[:6]
    return _objc_cache[sel]
def edges(fea):
    """calls, block invokes and objc impls reachable from function fea, in address order"""
    f = ida_funcs.get_func(fea)
    out = []
    if not f:
        return out
    for h in idautils.FuncItems(f.start_ea):
        m = idc.print_insn_mnem(h)
        if m in ("BL", "B"):
            t = idc.get_operand_value(h, 0)
            g = ida_funcs.get_func(t)
            if m == "B" and g and g.start_ea == f.start_ea:
                continue
            n = sym(t)
            out.append((h, "call", t, n))
            mm = re.match(r"_objc_msgSend\$(.+)", n or "")
            if mm:
                for imp in objc_impls(mm.group(1)):
                    out.append((h, "objc", imp, ida_name.get_name(imp)))
        for r in idautils.DataRefsFrom(h):
            nm = ida_name.get_name(r) or ""
            if "block_literal_global" in nm:
                inv = invoke_of_global_block(r)
                if inv:
                    out.append((h, "gblock", inv, ida_name.get_name(inv)))
            elif ida_funcs.get_func(r) and ida_funcs.get_func(r).start_ea == r and r != f.start_ea:
                out.append((h, "fnref", r, ida_name.get_name(r)))
    return out
def walk2(root, maxdepth=6, stop=None, skip=()):
    seen = set()
    hits = []
    stack = [(ea_of(root), 0, [])]
    while stack:
        ea, d, path = stack.pop()
        f = ida_funcs.get_func(ea)
        if not f or f.start_ea in seen:
            continue
        seen.add(f.start_ea)
        nm = ida_name.get_name(f.start_ea)
        if any(s in nm for s in skip):
            continue
        for h, kind, t, n in edges(f.start_ea):
            if stop is not None and d == 0 and h >= stop:
                break
            if n and (BLOCKING.search(n) or DAEMON.search(n)):
                hits.append((d, path + [nm], hex(h), kind, n))
            g = ida_funcs.get_func(t)
            if g and d < maxdepth and idc.get_segm_name(t) and "text" in idc.get_segm_name(t):
                stack.append((t, d + 1, path + [nm]))
    return hits, seen
