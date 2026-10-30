# cell registry: tracking, first start from history, persistence, NEW / GONE detection, own site notes (distance / bearing)
import importlib.util, json, os, shutil, sys, time, datetime
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-reg"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
sys.path.insert(0, ROOT); from keeper import store
from keeper import brain as b
T = time.time()
def row(i, ca="B7/3350/340:-92/3@20 B3/1550/434:-96/10@20", nb="3350/340:-92 1550/434:-86 1550/66:-88", nr="636576/534", site="1243/61", t=None):
    return {"time": datetime.datetime.fromtimestamp(t or T - (100 - i) * 60).strftime("%Y-%m-%d %H:%M:%S"), "lte_ca": ca, "lte_neighbours": nb,
            "nr_cell": nr, "nr_sinr": "7", "nr_rsrp": "-79", "lte_site": site, "nr_rsrq": "-13"}
# first start: registry built from history on disk
import csv
with open(DATA + "/samples.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(row(0))); w.writeheader(); [w.writerow(row(i)) for i in range(100)]
json.dump({"zte": {"lat": 42.626118, "lon": 23.384814}, "sites": [{"enb": 1243, "name": "near", "lat": 42.6284, "lon": 23.3798}]}, open(DATA + "/sites.json", "w"))
b.start.__globals__["threading"] = type("T", (), {"Thread": lambda *a, **k: type("x", (), {"start": lambda s: None})()})   # no probe thread here
b.start(DATA)
e = b.REG["lte"]["1550/434"]; a = b.REG["lte"]["3350/340"]
assert e["seen_min"] == 100 and e["ca_min"] == 100 and e["anchor_min"] == 0 and e["sinr"] == 10 and e["bw"] == 20 and e["band"] == "B3", e
assert a["anchor_min"] == 100 and a["site"] == "1243/61" and b.REG["lte"]["1550/66"]["ca_min"] == 0 and b.REG["nr"]["636576/534"]["served_min"] == 100
assert os.path.exists(DATA + "/cells.json")
v = b.cells_view(); x = next(c for c in v["lte"] if c["cell"] == "3350/340")
assert x["site_name"] == "near" and 450 < x["site_dist_m"] < 520 and 295 < x["site_bearing"] < 310, x      # ~482 m at ~302 deg
assert [c["band"] for c in v["lte"]] == sorted(c["band"] for c in v["lte"])
# persistence round trip
b.save_registry(); b.REG["lte"].clear(); b.load_registry(DATA); assert b.REG["lte"]["1550/434"]["seen_min"] == 100
# NEW: a strong B3 cell appears; GONE: a long-seen cell disappears for > 24 h
for i in range(40): b.track(row(i, nb="3350/340:-92 1550/434:-86 1550/66:-88 1550/375:-85"), T + i * 60)
b.REG["lte"]["1550/66"]["seen_min"] = 900; b.REG["lte"]["1550/66"]["last"] = T - 2 * 86400
b.REG["lte"]["500/341"] = dict(b.REG["lte"]["1550/375"], band="B1")             # another device's cell that got in
assert b.forget(["500/341", "1/1"]) == ["500/341"] and "500/341" not in b.REG["lte"]
ch = b.network_changes(T + 3000); print(ch)
assert any(c.startswith("NEW: LTE B3 1550/375") for c in ch) and any(c.startswith("GONE: LTE B3 1550/66") for c in ch)
assert not any("1550/434" in c for c in ch)
# best anchor from lock tests: must stay on the cell, ranked by call-ready, no-5G, 5G SINR, capacity; must beat the baseline
cols = ["time", "lock", "minutes", "on_target_pct", "anchor", "site", "anchor_sinr_p50", "nr_cells", "nr_sinr_p50", "no5g_min", "call_ok_pct", "lat_p95"]
def lt(rows):                                     # replace the lock-test records (strings, as the keeper stores them)
    store.db(DATA).execute("DELETE FROM rec WHERE kind = 'lock_tests'")
    for r in rows: store.add(DATA, "lock_tests", {c: str(v) for c, v in zip(cols, r)})
lt([["t", "none", 12, "", "3350/340", "1243/61", 3, "786:8 534:4", -1, 0, 97.0, 60],
    ["t", "434@1550", 12, 100, "1550/434", "1072/3", 10, "534:12", 8, 0, 100.0, 48],
    ["t", "340@1550", 12, 100, "1550/340", "1243/1", 8, "534:12", 7, 0, 100.0, 50],
    ["t", "66@1550", 12, 100, "1550/66", "1554/3", -2, "", "", 12, 60.0, 90],
    ["t", "375@1550", 12, 0, "3350/340", "1243/61", 3, "786:12", -1, 0, 97.0, 60]])
rk = b.anchor_ranking(); print([r["lock"] for r in rk["ranked"]], rk["failed"])
assert [r["lock"] for r in rk["ranked"]] == ["434@1550", "340@1550", "66@1550"] and rk["failed"] == ["375@1550"]
assert rk["ranked"][0]["capacity"] == 41.5                       # B3 20 MHz (from the registry) at SINR 10
fs = b.anchor_finding(rk); print(fs)
assert fs[0].startswith("RECOMMEND: best LTE anchor 434@1550: call-ready 100.0%, 5G 534:12 SINR 8") and "site 1072/3" in fs[0] and "unlocked:" in fs[0]
assert "375@1550" in fs[1]
lt([["t", "none", 12, "", "3350/340", "", 8, "534:12", 7, 0, 100.0, 50], ["t", "434@1550", 12, 100, "1550/434", "", 9, "534:12", 8, 0, 100.0, 48]])
assert b.anchor_finding(b.anchor_ranking())[0].startswith("OK: lock tests - no candidate clearly beats")
b.REG["nr"].pop("255/255", None); b.track(row(1, nr="255/255"), T); assert "255/255" not in b.REG["nr"]   # boot placeholder ignored
r = row(1, nr="636576/747"); r["nr_ca"] = "629988/747:-90.7/@100"; b.track(r, T); b.track(dict(r, nr_ca="629988/747:-91.7/5@100"), T)   # 5G secondary carrier
e = b.REG["nr"]["629988/747"]; assert e["ca_min"] == 2 and e["served_min"] == 0 and e["ca_sinr"] == 5 and e["ca_rsrp"] < -90.7, e
assert next(x for x in b.cells_view()["nr"] if x["cell"] == "629988/747")["ca_min"] == 2
b.track(dict(row(1, nb="100/340:-82"), lte_cell="100/340", lte_site="1243/81"), T)                                  # serving cell id from TR-069 CELLID
assert b.REG["ids"]["lte:100:340"] == 1243 * 256 + 81 and next(x for x in b.cells_view()["lte"] if x["cell"] == "100/340")["site"] == "1243/81"
print("OK test_registry")
