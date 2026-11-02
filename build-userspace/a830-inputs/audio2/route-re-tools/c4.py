import ida_name
for ea, nm in idautils.Names():
    if "HALS_Device15SetPropertyData" in nm and "block_invoke.182" in nm:
        p(hex(ea), nm)
        dec(ea)
dec(0x1b6b5f6b0)
dec(0x1b675c2f8)
