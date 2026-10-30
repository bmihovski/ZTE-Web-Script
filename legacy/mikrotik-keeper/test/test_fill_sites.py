# fill_sites.py: HAR -> sites.json (loose field names, base64 bodies, non-cellmapper ignored), merge, geometry
import base64, importlib.util, io, json, os, shutil, sys, tempfile, contextlib
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fs", os.path.join(here, "..", "fill_sites.py")); fs = importlib.util.module_from_spec(spec); spec.loader.exec_module(fs)
d = tempfile.mkdtemp(); os.makedirs(d + "/keeper"); fs.SITES = d + "/keeper/sites.json"
json.dump({"zte": {"lat": 42.626118, "lon": 23.384814}, "sites": [
    {"enb": 1243, "name": "", "lat": None, "lon": None, "cells": [{"rat": "LTE", "pci": 340, "earfcn": [3350, 100, 1550], "azimuth": None}]},
    {"enb": 999, "name": "mine", "lat": 42.6, "lon": 23.3, "cells": []}]}, open(fs.SITES, "w"))
lte = {"responseData": [{"siteID": 1243, "latitude": 42.6234, "longitude": 23.3850, "RAT": "LTE",      # ~300 m south of the ZTE
        "cells": {"318": {"PCI": 340, "Bearing": 180, "Channel": 1550}, "319": {"PCI": 340, "Bearing": 180, "Channel": 3350},
                  "320": {"PCI": 341, "Bearing": 10, "Channel": 1550}}}]}      # 340 points south (away from the ZTE), 341 north (at it)
nr = {"data": {"towers": [{"SiteID": "1554", "Lat": 42.6300, "Lng": 23.4000, "networkType": "NR",
       "sectors": [{"pci": 534, "azimuth": 250, "nrarfcn": 636576}]}]}}
har = {"log": {"entries": [
    {"request": {"url": "https://api.cellmapper.net/v6/getTowers?x"}, "response": {"content": {"text": json.dumps(lte)}}},
    {"request": {"url": "https://api.cellmapper.net/v6/getTowerInformation"}, "response": {"content": {"encoding": "base64", "text": base64.b64encode(json.dumps(nr).encode()).decode()}}},
    {"request": {"url": "https://tracker.example/x"}, "response": {"content": {"text": json.dumps({"siteID": 7, "latitude": 1, "longitude": 1, "pci": 1})}}}]}}
json.dump(har, open(d + "/cm.har", "w"))
out = io.StringIO()
with contextlib.redirect_stdout(out): rc = fs.main([d + "/cm.har"])
print(out.getvalue()); assert rc == 0
cfg = json.load(open(fs.SITES)); s = {str(x["enb"]): x for x in cfg["sites"]}
assert set(s) == {"999", "1243", "1554"}, s                       # template 1243 replaced, own entry kept, tracker ignored
c340 = [c for c in s["1243"]["cells"] if c["pci"] == 340]
assert len(c340) == 1 and c340[0]["earfcn"] == [1550, 3350] and c340[0]["azimuth"] == 180 and c340[0]["rat"] == "LTE", c340
assert s["1554"]["cells"] == [{"rat": "NR", "pci": 534, "arfcn": [636576], "azimuth": 250}], s["1554"]
dist, brg = fs.geo(42.626118, 23.384814, 42.6234, 23.3850); assert 290 < dist < 315 and 175 < brg < 185, (dist, brg)
o = out.getvalue()
assert "LTE 340 [1550, 3350] az 180.0 off 177 BEHIND" in o and "LTE 341 [1550] az 10.0 off 12 front" in o and "NR 534 [636576] az 250.0 off 0 front" in o, o
json.dump({"a": 1}, open(d + "/nothing.json", "w"))
with contextlib.redirect_stdout(io.StringIO()): assert fs.main([d + "/nothing.json"]) == 2           # nothing recognised -> exit 2, file untouched
shutil.rmtree(d); print("OK test_fill_sites")
