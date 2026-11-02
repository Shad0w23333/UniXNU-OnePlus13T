# Parse an XNU OSUnserializeXML-style plist (ID/IDREF) into Python objects; dump kexts as JSON.
import sys, json, xml.etree.ElementTree as ET
data = open(sys.argv[1], 'rb').read().rstrip(b'\0')
root = ET.fromstring(data)
ids = {}
def conv(e):
    if 'IDREF' in e.attrib:
        return ids[e.attrib['IDREF']]
    t = e.tag
    if t == 'dict':
        it = list(e); r = {}
        for i in range(0, len(it), 2):
            r[it[i].text] = conv(it[i+1])
        v = r
    elif t == 'array': v = [conv(x) for x in e]
    elif t == 'string': v = e.text or ''
    elif t == 'integer':
        s = (e.text or '0').strip(); v = int(s, 0)
    elif t == 'true': v = True
    elif t == 'false': v = False
    elif t == 'data': v = (e.text or '').strip()
    elif t == 'set': v = [conv(x) for x in e]
    else: v = e.text
    if 'ID' in e.attrib: ids[e.attrib['ID']] = v
    return v
top = conv(root)
kexts = top['_PrelinkInfoDictionary']
out = []
for k in kexts:
    out.append({key: val for key, val in k.items() if not key.startswith('_Prelink')})
json.dump(out, open(sys.argv[2], 'w'), indent=1, default=str)
print(len(out))
