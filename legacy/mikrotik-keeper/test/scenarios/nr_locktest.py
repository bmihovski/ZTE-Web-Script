# NR (5G) lock test: the LTE anchor is pinned for every step, the baseline has the 5G leg unlocked, each 5G cell is measured;
# the antenna mode and the final cell are chosen by INTERNET QUALITY first (call-ready, loss, latency p95, jitter) - signal only
# breaks a tie; the sweep runs after "Aligned - measure now" (for the baseline too) and only the NR test sweeps
# part A: sections 1-3, 4-6 (B: 3b-3c, C: 6b-7)
import os, sys
os.environ["NRLT_PORT"] = "18131"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"))
from nr_locktest_env import *  # noqa: F401,F403 - the shared setup (mock ZTE, cells, helpers)
import http.client  # noqa
# 1) fixed position (align=0): the anchor is pinned, the baseline has 5G unlocked; every step still internet-tests the modes
locktest.lock_test(["none", "534@636576"], rat="nr", anchor=ANCHOR, heading="108")
r = rows()
assert set(r) == {"none", "534@636576"}
assert r["534@636576"]["rat"] == "nr" and r["534@636576"]["anchor"] == "1550/340" and r["534@636576"]["site"] == "1072/3"
assert r["534@636576"]["on_target_pct"] == "100.0" and r["534@636576"]["nr_sinr_p50"] == "9.0" and r["534@636576"]["nr_rsrp_p50"] == "-85.0"
assert r["534@636576"]["nr_cells"].startswith("534:") and int(r["534@636576"]["minutes"]) >= 3
assert r["534@636576"]["nr_arfcn"] == "636576" and r["none"]["nr_arfcn"] == "629988", r     # the 5G ARFCN the step measured
assert brain.nr_capacity(r["none"]) is not None and brain.nr_capacity(r["534@636576"]) is not None, r   # the baseline has a capacity too
assert r["534@636576"]["antennas"] == "ant5 dir / ant6 omni" and r["534@636576"]["heading"] == "108" and r["534@636576"]["mode"] == "fixed"
assert r["none"]["antennas"] == "ant5 dir / ant6 omni" and r["none"]["heading"] == "108" and r["none"]["mode"] == "fixed"
assert r["none"]["rat"] == "nr" and r["none"]["on_target_pct"] == "" and r["none"]["nr_sinr_p50"] == "6.0"
cell_sets = [x for x in sets() if x[0] in ("LTE_LOCK_CELL_SET", "NR5G_LOCK_CELL_SET")]
assert cell_sets == [("LTE_LOCK_CELL_SET", "340@1550"), ("NR5G_LOCK_CELL_SET", "0,0,0,0"),
                     ("LTE_LOCK_CELL_SET", "340@1550"), ("NR5G_LOCK_CELL_SET", "534,636576,78,30"),
                     ("LTE_LOCK_CELL_SET", "0@0"), ("NR5G_LOCK_CELL_SET", "0,0,0,0")], cell_sets
assert len(sets("REBOOT_DEVICE")) == 3 and bsp()[-2:] == [("5", "4"), ("6", "1")]   # the quality mode was applied, no align wait
assert len(store.rows(DATA, "antenna_sweeps")) == 8                        # baseline + cell, 4 modes each
assert common.state().get("lock_test") is None and brain.ctx.get("hold") is None and locktest.lt["running"] is None

# 2) align mode, two cells, AUTO=on: baseline and cells are swept; the MODE and the CELL are chosen by internet quality, not signal
m.st["sets"].clear(); brain.AUTO = "on"; reports.clear()
n0 = len(store.rows(DATA, "antenna_sweeps")); align(["108", "108", "108"])
locktest.lock_test(["none", "534@636576", "747@629988"], align=True, rat="nr", anchor=ANCHOR)
r = rows(); print(json.dumps(r, indent=1))
assert all(x["antennas"] == "ant5 dir / ant6 omni" for x in r.values()), r          # quality winner (p95 25 ms), not the signal winner (dir/dir)
assert all((x["ant5"], x["ant6"]) == ("4", "1") for x in r.values()), r              # the mode values are stored (boot apply)
assert locktest.cell_mode("534@636576")["mode"] == "ant5 dir / ant6 omni"            # the locked cell's newest good mode
assert all(x["heading"] == "108" and x["mode"] == "aligned" for x in r.values()), r  # the baseline waited for alignment too
sw = store.rows(DATA, "antenna_sweeps")[n0:]
assert len(sw) == 12, len(sw)                                                       # 4 modes x 3 steps
assert {x["trigger"] for x in sw} == {"lock test none", "lock test 534@636576", "lock test 747@629988"}, {x["trigger"] for x in sw}
worst = [x for x in sw if x["mode"] == "ant5 dir / ant6 dir" and x["trigger"] == "lock test 534@636576"][-1]
assert worst["sinr_p50"] == "9.5" and worst["loss_pct"] == "8.0" and worst["lat_p95"] == "120.0", worst    # best signal, worst internet
last = [x for x in sw if x["trigger"] == "lock test 747@629988" and x["mode"] == "ant5 dir / ant6 omni"][-1]
assert last["lat_p95"] == "25.0" and last["call_ok"] == "1" and last["down_mbps"] == "95.0" and last["inttest"] == "ok", last
assert bsp()[-2:] == [("5", "4"), ("6", "1")]                                       # the quality winner was applied for the measurements
iw = min(i for i, p in enumerate(phases) if p and "waiting for you to align" in p)
isw = min(i for i, p in enumerate(phases) if i > iw and p and "sweeping" in p)
im = min(i for i, p in enumerate(phases) if i > isw and p and p.startswith("none: measuring"))
assert iw < isw < im, (iw, isw, im)                                                 # align, sweep, measure - the baseline too
# the 534 cell has the better signal, 747 the better internet: 747 must win the ranking and the auto-apply
assert r["534@636576"]["nr_sinr_p50"] == "9.0" and r["534@636576"]["lat_p95"] == "80.0" and r["534@636576"]["loss_pct"] == "2.0"
assert r["747@629988"]["nr_sinr_p50"] == "6.0" and r["747@629988"]["lat_p95"] == "40.0" and r["747@629988"]["loss_pct"] == "0.0"
rk = brain.anchor_ranking("nr"); print([x["lock"] for x in rk["ranked"]])
assert [x["lock"] for x in rk["ranked"]] == ["747@629988", "534@636576"], rk["ranked"]
st = common.state()
assert st["nr_lock"]["cell"] == "747@629988" and st["nr_lock"]["state"] == "pending", st.get("nr_lock")
assert antennas.ant_cfg() == {"auto": "0", "ant5": "4", "ant6": "1"}                # the winning cell's quality mode is kept for every boot
kept_ev = [e for e in store.rows(DATA, "events") if e["event"] == "antennas kept"]
assert kept_ev and "auto-applied" in kept_ev[-1].get("why", ""), kept_ev[-1]        # the keep was the keeper's auto-apply, not the panel
assert reports and "signal alone would pick 534@636576" in reports[-1], reports[-1]
assert "internet quality picked 747@629988" in reports[-1], reports[-1]

# 3) the target 5G cell is not up in any mode: the cell step is SKIPPED (no 12-minute measurement), the baseline is measured
#    and the summary names the cell it could not see
common.state({"nr_lock": None}); brain.AUTO = "dryrun"; m.st["sets"].clear(); reports.clear()
good = m.fields
def no5g():
    f = good(); f["nr5g_pci"] = ""; f["Z5g_SINR"] = ""; return f
m.fields = no5g
align(["90", "90"])
locktest.lock_test(["none", "534@636576"], align=True, rat="nr", anchor=ANCHOR)
m.fields = good
r = rows()
assert r["534@636576"]["minutes"] == "0" and r["534@636576"]["antennas"] == "", r["534@636576"]
assert r["534@636576"]["note"].startswith("cell not reachable") and "no 5G on 636576/534" in r["534@636576"]["note"], r["534@636576"]
# a skipped step has no measurement: no fabricated 0 % loss / 0 anchor switches (they read like measured values)
assert r["534@636576"]["loss_pct"] == "" and r["534@636576"]["anchor_switches"] == "" and r["534@636576"]["anchor_pct"] == "", r["534@636576"]
assert int(r["none"]["minutes"]) >= 1 and r["none"]["antennas"] == "" and "sweep: no 5G in any mode" in r["none"]["note"], r["none"]
assert not common.state().get("nr_lock")
assert reports and "534@636576" in reports[-1] and "not seen / off target" in reports[-1], reports[-1]

# 4) a 5G ARFCN with no known band: the step is recorded as not set, the test still ends unlocked
m.st["sets"].clear()
locktest.lock_test(["none", "534@700000"], rat="nr", anchor=ANCHOR)
r = rows()
assert r["534@700000"]["note"].startswith("lock not set: no 5G band known for ARFCN 700000") and r["534@700000"]["minutes"] == "0"
assert [x for x in sets("NR5G_LOCK_CELL_SET")][-1] == ("NR5G_LOCK_CELL_SET", "0,0,0,0")

# 4b) the stability rule: a serving-LTE switch, < 80 % on the pinned anchor, or a > 3 min sample gap flags the row
def mrow(t, lte="1550/340"):
    return {"time": t, "lte_cell": lte, "lte_site": "1072/3", "lte_ca": "B3/1550/340:-90/10@20", "lte_rsrq": "-12", "nr_cell": "636576/534",
            "nr_sinr": "9", "nr_rsrp": "-85", "call_ok": "1", "lat_p95": "40", "jitter_ms": "4", "loss_pct": "0", "down_bps": "0", "up_bps": "0"}
switched = [mrow("2026-01-01 00:00:00"), mrow("2026-01-01 00:01:00"), mrow("2026-01-01 00:02:00", "1550/341"), mrow("2026-01-01 00:03:00", "1550/341")]
res = locktest.lt_eval("534@636576", switched, rat="nr", pin=ANCHOR)
assert res["unstable"] == 1 and res["anchor_switches"] == 1 and res["anchor_pct"] == 50.0 and "unstable anchor" in res["note"], res
stable = [mrow("2026-01-01 00:00:00"), mrow("2026-01-01 00:01:00"), mrow("2026-01-01 00:02:00")]
r2 = locktest.lt_eval("534@636576", stable, rat="nr", pin=ANCHOR)
assert r2["unstable"] == 0 and r2["anchor_switches"] == 0 and r2["anchor_pct"] == 100.0, r2
gappy = [mrow("2026-01-01 00:00:00"), mrow("2026-01-01 00:06:00")]
r3 = locktest.lt_eval("534@636576", gappy, rat="nr", pin=ANCHOR)
assert r3["gap_min"] == 6.0 and r3["unstable"] == 1 and "min without data" in r3["note"], r3
assert locktest.lt_eval("534@636576", [], rat="nr", pin=ANCHOR)["unstable"] == ""
assert locktest.lt_eval("534@636576", stable)["unstable"] == ""                      # LTE rows are not gated this way

# 5) anchor resolution: explicit, the keeper's LTE lock, else the serving cell; nothing at all -> ValueError
common.state({"lte_lock": {"cell": "434@1550", "state": "active"}})
assert locktest.anchor_for("nr") == "434@1550" and locktest.anchor_for("nr", "340@100") == "340@100"
common.state({"lte_lock": {"cell": "434@1550", "state": "released"}})     # a released lock is not the anchor to test
assert locktest.anchor_for("nr") == "340@1550"                            # the serving cell from the samples
common.state({"lte_lock": None}); assert locktest.anchor_for("lte") is None and locktest.anchor_for("nr") == "340@1550"
hist = list(brain.HIST); brain.HIST.clear()
try: locktest.anchor_for("nr"); raise AssertionError("no anchor accepted")
except ValueError: pass
brain.HIST.extend(hist)

# 6) API: rat/anchor on POST /lock-test, rat filters on the reads, the OpenAPI document describes them
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", PORT + 1), api.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
import http.client
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", PORT + 1); c.request("GET", path); x = c.getresponse(); return x.status, json.loads(x.read())
def post(path):
    c = http.client.HTTPConnection("127.0.0.1", PORT + 1); c.request("POST", path, b""); x = c.getresponse(); return x.status, json.loads(x.read())
calls = []; real_start = locktest.start
locktest.start = lambda cells, align=False, rat="lte", anchor=None, heading="", night=False: calls.append((cells, align, rat, anchor, heading)) or True   # = start()'s signature
try:
    assert post("/lock-test?rat=nr&cells=534@636576&anchor=340@1550&align=1") == (202, {"started": True, "rat": "nr", "anchor": "340@1550", "heading": "", "plan": ["none", "534@636576"]})
    assert calls[-1] == (["534@636576"], True, "nr", "340@1550", ""), calls
    assert post("/lock-test?rat=nr&cells=534@636576")[1]["anchor"] is None             # no explicit anchor: per 5G cell
    assert calls[-1] == (["534@636576"], False, "nr", None, ""), calls
    assert post("/lock-test?rat=nr&cells=747@629988&heading=108")[1]["heading"] == "108" and calls[-1][4] == "108"
    assert post("/lock-test?cells=434@1550") == (202, {"started": True, "rat": "lte", "anchor": None, "heading": "", "plan": ["none", "434@1550"]})
    assert post("/lock-test?rat=x")[0] == 400 and post("/lock-test?rat=nr&cells=534@700000")[0] == 400
    n_before = len(calls)
    assert post("/lock-test?rat=nr&cells=534@636576,bad")[0] == 400 and len(calls) == n_before      # a typo is refused, not dropped
    assert post("/lock-test?cells=434@1550;340@100")[0] == 400 and len(calls) == n_before           # one cell per lock (the MC889 keeps the first)
    assert post("/lock-test?rat=nr&cells=bad")[0] == 400
finally:
    locktest.start = real_start
s, lt = get("/lock-tests?rat=nr"); assert s == 200 and lt["results"] and all(x["rat"] == "nr" for x in lt["results"]), lt
assert lt["rat"] == "nr" and lt["anchor"] == ANCHOR and lt["plan"] == ["none", "534@700000"], lt   # the last run's RAT/anchor, not "lte"/None
assert get("/lock-tests?rat=lte")[1]["results"] == []
a = get("/anchors?rat=nr")[1]
assert a["baseline"] and a["baseline"]["nr_arfcn"] == "629988" and a["baseline"]["capacity"] is not None, a["baseline"]   # the baseline has a capacity
assert [x["lock"] for x in a["ranked"]] == ["747@629988"] and a["failed"] == ["534@636576", "534@700000"], a   # 534 not reachable / 700000 could not be set
assert get("/anchors?rat=lte")[1]["ranked"] == []
spec = get("/openapi.json")[1]; p = spec["paths"]["/lock-test"]["post"]["parameters"]; names = {x["name"]: x for x in p}
assert names["rat"]["schema"]["enum"] == ["lte", "nr"] and "anchor" in names and names["anchor"]["example"] == "340@1550"
assert {x["name"] for x in spec["paths"]["/lock-tests"]["get"]["parameters"]} == {"rat"} and {x["name"] for x in spec["paths"]["/anchors"]["get"]["parameters"]} == {"rat"}
assert {x["name"] for x in spec["paths"]["/lock-test-continue"]["post"]["parameters"]} == {"heading"}   # cut/continue stay shared
srv.shutdown()


stop.set(); print("OK test_nr_locktest")
