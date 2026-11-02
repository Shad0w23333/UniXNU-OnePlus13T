import sys, runpy
addrs = [int(a, 16) for a in sys.argv[5:]]
sys.argv = sys.argv[:5] + ['^$NOTHING']
g = runpy.run_path('mga.py', run_name='mga')
for a in addrs:
    print(hex(a), ' | '.join(g['summarize'](a)))
