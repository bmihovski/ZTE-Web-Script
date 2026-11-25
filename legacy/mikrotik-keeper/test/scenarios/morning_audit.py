import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "tools", "morning_audit.py")
FIXTURE = os.path.join(ROOT, "test", "fixtures", "morning-20261124.json")


def run(args):
    p = subprocess.run([sys.executable, TOOL] + args, capture_output=True, text=True)
    return p.returncode, p.stdout


def load():
    with open(FIXTURE) as f:
        return json.load(f)


def write(doc, tmp):
    path = os.path.join(tmp, "m.json")
    with open(path, "w") as f:
        json.dump(doc, f)
    return path


def status_of(out, cid):
    for line in out.splitlines():
        if line.startswith(cid + " "):
            return line.split()[1]
    raise AssertionError(f"{cid} not in output:\n{out}")


tmp = tempfile.mkdtemp(prefix="morning_audit_")

# fixture: exits 0, R1 R2 R3 R4 R8 PASS
p = write(load(), tmp)
rc, out = run([p])
assert rc == 0, out
for cid in ("R1", "R2", "R3", "R4", "R8"):
    assert status_of(out, cid) == "PASS", (cid, out)

# B3: append a third B3 leg (66@1550, role second, ok, has5g) after the ok 340@1550 -> R3 FAIL, exit 1
doc = load()
legs = doc["morning"]["result"]["legs"]
i340 = next(i for i, l in enumerate(legs) if l["lte"] == "340@1550")
extra = copy.deepcopy(legs[i340])
extra["leg"] = "B3 #3 66@1550"
extra["lte"] = "66@1550"
legs.insert(i340 + 1, extra)
rc, out = run([write(doc, tmp)])
assert rc == 1, out
assert status_of(out, "R3") == "FAIL", out

# B1 #1 observed_nr "" -> R4 FAIL
doc = load()
for l in doc["morning"]["result"]["legs"]:
    if l["leg"] == "B1 #1 100/340":
        l["observed_nr"] = ""
rc, out = run([write(doc, tmp)])
assert rc == 1, out
assert status_of(out, "R4") == "FAIL", out

# B1 first leg removed, rejected unchanged -> R3 FAIL
doc = load()
legs = doc["morning"]["result"]["legs"]
legs[:] = [l for l in legs if l["leg"] != "B1 #1 100/340"]
rc, out = run([write(doc, tmp)])
assert rc == 1, out
assert status_of(out, "R3") == "FAIL", out

# B1 backups 434@100 failed then 202@100 ok, candidates ["434@100","202@100"] -> R3 PASS
doc = load()
for d in doc["morning"]["result"]["discoveries"]:
    if d["band"] == "1":
        d["candidates"] = ["434@100", "202@100"]
legs = doc["morning"]["result"]["legs"]
b1 = [l for l in legs if str(l.get("band")) == "1"]
first = next(l for l in b1 if l["role"] == "first")
b2 = copy.deepcopy(first)
b2.update({"leg": "B1 #2 434@100", "role": "second", "lte": "434@100",
           "ok": False, "has5g": True, "error": "no internet during the probe",
           "observed_nr": "", "down_mbps": None, "call_ok": 0,
           "loss_pct": None, "lat_p95": None, "jitter_ms": None})
b3 = copy.deepcopy(first)
b3.update({"leg": "B1 #3 202@100", "role": "second", "lte": "202@100",
           "ok": True, "has5g": True, "error": "",
           "observed_nr": "636576/534", "down_mbps": 20.0, "call_ok": 1,
           "loss_pct": 0.0, "lat_p95": 20.0, "jitter_ms": 2.0})
legs[:] = [l for l in legs if str(l.get("band")) != "1"]
legs.extend([first, b2, b3])
rc, out = run([write(doc, tmp)])
assert status_of(out, "R3") == "PASS", out

print("OK test_morning_audit")
