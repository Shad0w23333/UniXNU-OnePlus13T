import json, os, re
import idaapi, idautils, idc, ida_funcs, ida_name, ida_pro, ida_auto
ida_auto.auto_wait()
job = json.load(open(os.environ["IDAQ_JOB"]))
out = open(job["out"], "w")
pats = [re.compile(p) for p in job["patterns"]]
def fname(ea):
    f = ida_funcs.get_func(ea)
    return "%s@%x" % (ida_name.get_name(f.start_ea), f.start_ea) if f else "nofunc"
cnt = 0
for seg in idautils.Segments():
    if idc.get_segm_name(seg) != "__text":
        continue
    for head in idautils.Heads(seg, idc.get_segm_end(seg)):
        d = idc.GetDisasm(head)
        for p in pats:
            if p.search(d):
                out.write("%x %s | %s\n" % (head, d, fname(head)))
                cnt += 1
                break
        if cnt > 3000:
            break
out.close()
ida_pro.qexit(0)
