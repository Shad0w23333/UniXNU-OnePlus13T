exec(open("fixnr.py").read())
for nm in ["cmsmInitializeCMSessionManager"]:
    p("fixed:", fix_func(nm))
    p("fixed2:", fix_func(nm))
dec("cmsmInitializeCMSessionManager")
