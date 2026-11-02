for a in [0x1edd49a4c, 0x1edd49a50, 0x1edd49a40, 0x1edd49a7c]:
    p("XREFS %x" % a)
    for r in idautils.XrefsTo(a):
        p("   from %x type %d in %s" % (r.frm, r.type, fn(r.frm)))
dis(0x1987fb89c - 0x40, 40)
