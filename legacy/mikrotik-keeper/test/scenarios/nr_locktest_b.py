# NR lock test, part B (sections 3b-3c; the shared setup: test/fixtures/nr_locktest_env.py)
import os, sys
os.environ["NRLT_PORT"] = "18231"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"))
from nr_locktest_env import *  # noqa: F401,F403 - the shared setup (mock ZTE, cells, helpers)
import http.client  # noqa
# 3b) a refused antenna set in every mode is `antenna control failed`, not `cell not reachable`; a web failure skips the BASELINE
#     too (a skipped baseline is then not a baseline: no auto-apply against it), and a cell is measured once the web is back
common.state({"nr_lock": None}); brain.AUTO = "on"; m.st["sets"].clear(); reports.clear()
m.st["refuse"] = {"WAN_ANT_SWITCH_SET", "BSP_ANTENNA_STATE_SET"}
locktest.lock_test(["none", "534@636576"], rat="nr", anchor=ANCHOR, heading="108")
m.st["refuse"] = set()
r = rows()
assert r["none"]["minutes"] == "0" and r["none"]["note"].startswith("not measured: antenna control failed"), r["none"]
assert r["534@636576"]["minutes"] == "0" and r["534@636576"]["note"].startswith("antenna control failed"), r["534@636576"]
assert not r["534@636576"]["note"].startswith("cell not reachable") and r["534@636576"]["loss_pct"] == "", r["534@636576"]
assert brain.anchor_ranking("nr")["baseline"] is None and not common.state().get("nr_lock"), brain.anchor_ranking("nr")
m.st["refuse"] = {"WAN_ANT_SWITCH_SET", "BSP_ANTENNA_STATE_SET"}
n0 = sum(1 for x in store.rows(DATA, "lock_tests") if x["lock"] == "none" and x["minutes"] == "0")
def clear_refuse():
    for _ in range(1200):
        if sum(1 for x in store.rows(DATA, "lock_tests") if x["lock"] == "none" and x["minutes"] == "0") > n0: m.st["refuse"] = set(); return
        time.sleep(0.02)
threading.Thread(target=clear_refuse, daemon=True).start()
locktest.lock_test(["none", "534@636576"], rat="nr", anchor=ANCHOR, heading="108")
m.st["refuse"] = set()
r = rows()
assert r["none"]["minutes"] == "0" and r["none"]["note"].startswith("not measured: antenna control failed"), r["none"]
assert int(r["534@636576"]["minutes"]) >= 1 and r["534@636576"]["on_target_pct"] == "100.0", r["534@636576"]
rk = brain.anchor_ranking("nr")
assert rk["baseline"] is None, rk["baseline"]                       # a "not measured" baseline is not a baseline
assert "534@636576" in [x["lock"] for x in rk["ranked"]], rk["ranked"]
assert brain.anchor_best("nr")[2] is False and not common.state().get("nr_lock"), (brain.anchor_best("nr"), common.state())
assert "AUTO-APPLIED" not in reports[-1] and "baseline (5G unlocked)" not in reports[-1], reports[-1]

# 3c) a 5G cell that switches in every mode is skipped with its own reason (`5G cell switching`), never "cell not reachable";
#     a 5G-free/switching baseline is still measured (it is the reference)
common.state({"nr_lock": None}); brain.AUTO = "dryrun"; m.st["sets"].clear(); reports.clear()
good2 = m.fields; seen_n = {"n": 0}
def switching():
    f = good2(); seen_n["n"] += 1
    f["nr5g_pci"] = "2eb" if seen_n["n"] % 2 else "216"                # the other PCI on the same ARFCN: the cell keeps changing
    return f
m.fields = switching
locktest.lock_test(["none", "534@636576"], rat="nr", anchor=ANCHOR)
m.fields = good2
r = rows()
assert r["none"]["minutes"] != "0" and "5G cell switching" in r["none"]["note"], r["none"]
assert r["534@636576"]["minutes"] == "0" and r["534@636576"]["note"].startswith("5G cell switching"), r["534@636576"]
assert "cell not reachable" not in r["534@636576"]["note"] and r["534@636576"]["loss_pct"] == "" and r["534@636576"]["anchor_switches"] == "", r["534@636576"]


stop.set(); print("OK test_nr_locktest_b")
