def find_in(fea, pat, maxn=10):
    f = ida_funcs.get_func(fea)
    n = 0
    for h in idautils.FuncItems(f.start_ea):
        d = idc.GetDisasm(h)
        if re.search(pat, d):
            p("  %x: %s" % (h, d)); n += 1
            if n >= maxn: break
import re
p("SetPropertyData sub_1E154 init checks:")
find_in(0x1E154, r"#0x6C\]")
p("GetPropertyData sub_518EC init checks:")
find_in(0x518EC, r"#0x6C\]")
p("deferred init +108 stores:")
find_in(0x14CC58, r"#0x6C\]")
strs(["SerializationContext is ready", "Attempt to change routes prior", "Beginning loop to wait for actuator port", "VirtualAudio PlugIn is not initialized yet"], maxhits=10)
