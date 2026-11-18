# NR lock test, part C (sections 6b-7; the shared setup: test/fixtures/nr_locktest_env.py)
import os, sys
os.environ["NRLT_PORT"] = "18241"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"))
from nr_locktest_env import *  # noqa: F401,F403 - the shared setup (mock ZTE, cells, helpers)
import http.client  # noqa
# 6b) unstable LTE anchor: the 5G row is kept (with the reason) but never ranked or auto-applied
common.state({"nr_lock": None}); brain.AUTO = "dryrun"; reports.clear(); m.st["sets"].clear()
ANCHOR_FLIP["on"] = True
locktest.lock_test(["none", "534@636576"], rat="nr", anchor=ANCHOR, heading="108")
ANCHOR_FLIP["on"] = False
r = rows(); u = r["534@636576"]
assert u["unstable"] == "1" and int(u["anchor_switches"]) >= 1 and float(u["anchor_pct"]) < 80, u
assert "unstable anchor" in u["note"] and r["none"]["unstable"] == "1", r["none"]
rk = brain.anchor_ranking("nr")
assert "534@636576" in rk["unstable"] and "534@636576" not in [x["lock"] for x in rk["ranked"]], rk
assert rk["baseline"] is None and not common.state().get("nr_lock") and brain.anchor_best("nr")[2] is False

# 6c) the failed-cell hint: a 5G cell that was served under another LTE anchor says so (747 needs 1550/434, not 340@100)
brain.REG["nr"]["636576/747"] = {"first": 1, "served_min": 12,
                                 "anchors": {"1550/434": {"min": 12, "ok": 12, "p95": 51, "jitter": 11, "sinr": 8, "rsrp": -80}}}
assert locktest.anchor_hint("747@636576", "340@100") == " (known under 1550/434, 12 min)", locktest.anchor_hint("747@636576", "340@100")
brain.REG["nr"].setdefault("636576/534", {"first": 1, "served_min": 0, "anchors": {}})["anchors"]["1550/340"] = \
    {"min": 60, "ok": 60, "p95": 50, "jitter": 8, "sinr": 9, "rsrp": -85}   # part A's runs gave 534 its history under the pinned anchor
assert locktest.anchor_hint("747@636576", "434@1550") == "" and locktest.anchor_hint("534@636576", ANCHOR) == ""
assert locktest.anchor_hint("127@636576", ANCHOR) == ""
assert locktest.anchor_hint("747@636576", "") == " (known under 1550/434, 12 min)"

# 6d) per-cell anchor + the pair auto-apply: the mapping says 804@636576 is served by the anchor 100/340 (the keeper's lock is
# 434@1550). Without an explicit anchor the test pins the cell's best known anchor; the baseline pins the best anchor across the
# tested cells; a clear winning pair auto-applies the 5G cell AND its anchor (the pair is what reproduces the result).
CELLS["none"] = ("100/340", "629988/747", "6.0", "1243/81", "55", "6", "0", "1")      # the baseline must serve the pinned anchor
brain.REG["nr"]["636576/804"] = {"first": 1, "served_min": 12,
                                 "anchors": {"100/340": {"min": 12, "ok": 12, "p95": 40, "jitter": 4, "sinr": 9, "rsrp": -80}}}
assert brain.anchor_spec_for("636576/804") == "340@100" and brain.anchor_for_cells(["804@636576"]) == "340@100"
# the anchors are resolved ONCE before the run (the run's own samples update the pairs; a mid-run flip would make the steps and the
# baseline cross-anchor - seen 2026-11-02)
assert locktest.step_pins(["none", "804@636576"], None, "340@100", "434@1550") == {"none": "340@100", "804@636576": "340@100"}
assert locktest.step_pins(["none", "747@636576"], None, "434@1550", "340@100") == {"none": "434@1550", "747@636576": "434@1550"}
# the pair auto-apply may move the LTE lock: give the gate two ranked LTE anchors (340@100 good, 434@1550 good but weaker)
for _t, _a, _s in (("11:00:00", "340@100", "6.0"), ("11:10:00", "434@1550", "5.0")):
    store.add(DATA, "lock_tests", {"time": "2026-11-02 " + _t, "rat": "lte", "lock": _a, "minutes": "12", "on_target_pct": "100",
                                   "anchor": "100/340" if _a == "340@100" else "1550/434", "call_ok_pct": "100.0",
                                   "anchor_sinr_p50": _s, "lat_p95": "50", "jitter_ms": "8", "nr_cells": "534:6", "no5g_min": "0",
                                   "mode": "as is"})
assert brain.anchor_move_ok("340@100", "434@1550") == (True, "")       # 6.0 vs 5.0 dB: a fair move, allowed
m.st["sets"].clear(); brain.AUTO = "on"; common.state({"lte_lock": {"cell": "434@1550", "state": "active"}, "nr_lock": None})
reports.clear(); stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["none", "804@636576"], rat="nr"); stop.set()
r = rows()
assert r["none"]["pin"] == "340@100" and r["804@636576"]["pin"] == "340@100", (r["none"]["pin"], r["804@636576"]["pin"])
assert r["804@636576"]["anchor"] == "100/340" and r["804@636576"]["anchor_pct"] == "100.0" and r["804@636576"]["unstable"] == "0", r["804@636576"]
assert common.state()["nr_lock"]["cell"] == "804@636576" and common.state()["lte_lock"]["cell"] == "340@100", common.state()
assert "with LTE anchor 340@100" in reports[-1] and "804@636576 (anchor 340@100)" in reports[-1], reports[-1]
CELLS["none"] = ("1550/340", "629988/747", "6.0", "1072/3", "55", "6", "0", "1")

# 7) keeper died mid-test: the next start clears BOTH legs (the pinned anchor and the 5G lock), whatever the test held
m.st["sets"].clear(); common.state({"lock_test": {"cell": "534@636576", "rat": "nr", "anchor": ANCHOR}})
zdev.on_start(); time.sleep(0.4)
assert common.state().get("lock_test") is None
assert [x for x in sets() if x[0] in ("LTE_LOCK_CELL_SET", "NR5G_LOCK_CELL_SET")] == [
    ("LTE_LOCK_CELL_SET", "0@0"), ("NR5G_LOCK_CELL_SET", "0,0,0,0")], sets()

stop.set(); print("OK test_nr_locktest_c")
