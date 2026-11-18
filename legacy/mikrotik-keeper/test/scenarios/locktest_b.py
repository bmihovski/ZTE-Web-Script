# LTE lock test, part B (the shared setup: test/fixtures/locktest_env.py)
import os, sys
os.environ["LT_PORT"] = "18251"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"))
from locktest_env import *  # noqa: F401,F403 - the shared setup (mock ZTE, cells, zte loop)
# skip a step while it waits for alignment, stop the test while measuring: rows recorded, lock removed, keeper free again
m.st["sets"].clear(); locktest.LT_ALIGN_TIMEOUT_MIN = 5; locktest.LT_MEASURE_MIN = 0.2; stop.clear(); threading.Thread(target=zte, daemon=True).start()
assert not locktest.lt_cut("skip")                                                   # nothing running -> refused
def cutter():
    while not locktest.lt["waiting"]: time.sleep(0.05)
    assert locktest.lt["running"] == "434@1550"; locktest.lt_cut("skip")
    while locktest.lt["running"] != "340@1550" or not locktest.lt["waiting"]: time.sleep(0.05)
    locktest.lt["heading"] = "301"; locktest.lt["go"].set()
    while "measuring" not in (locktest.lt.get("phase") or ""): time.sleep(0.05)
    time.sleep(0.5); locktest.lt_cut("stop")
threading.Thread(target=cutter, daemon=True).start(); t0 = time.time()
locktest.lock_test(["none", "434@1550", "340@1550", "66@1550"], align=True); stop.set()
assert time.time() - t0 < 60 and locktest.lt["running"] is None and not locktest.lt["waiting"]
rows = store.rows(DATA, "lock_tests")[-3:]
assert [r["lock"] for r in rows] == ["none", "434@1550", "340@1550"] and rows[1]["note"].startswith("skipped by you") and rows[1]["mode"] == "cut", rows
assert rows[2]["note"].startswith("stopped by you after") and rows[2]["heading"] == "301", rows[2]
assert [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"] == ["0@0", "434@1550", "340@1550", "0@0"]   # 66 never locked, unlocked at the end

# daytime LTE test with AUTO=on: the winner clearly beats the unlocked baseline -> the summary AUTO-APPLIES it (lock state
# pending, no device write here) and the tested anchor's row goes into the cell registry (bookkeeping only)
from keeper import alert
brain.reg_st["dir"] = DATA; reports = []
alert.report = lambda text: reports.append(text) or True
m.st["sets"].clear(); brain.AUTO = "on"; common.state({"lte_lock": None, "nr_lock": None})
locktest.LT_MEASURE_MIN = 0.03; stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["none", "434@1550"]); stop.set()
r = {x["lock"]: x for x in store.rows(DATA, "lock_tests")}
assert r["434@1550"]["anchor"] == "1550/434" and r["434@1550"]["call_ok_pct"] == "100.0", r["434@1550"]
assert r["none"]["call_ok_pct"] == "100.0" and r["434@1550"]["anchor_sinr_p50"] == "10.0", (r["none"], r["434@1550"])
assert common.state()["lte_lock"]["cell"] == "434@1550" and common.state()["lte_lock"]["state"] == "pending", common.state().get("lte_lock")
assert reports and "AUTO-APPLIED 434@1550" in reports[-1] and "unlocked baseline" in reports[-1], reports[-1]
assert brain.REG["lte"]["1550/434"]["lock_test"]["time"] == r["434@1550"]["time"], brain.REG["lte"].get("1550/434")

# the winner is the lock already kept -> the summary says so and does not lock it again (no extra reboot)
common.state({"lte_lock": {"cell": "434@1550", "state": "active"}}); reports.clear()
m.st["sets"].clear(); stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["none", "434@1550"]); stop.set()
assert reports and "best is already your lock 434@1550" in reports[-1], reports[-1]

# no candidate result at all (nothing stayed on the tested cell) -> the summary names the cells, no "best"
store.db(DATA).execute("DELETE FROM rec WHERE kind = 'lock_tests'"); reports.clear()
locktest.lock_test(["none", "370@1550"]); stop.set()
rows = {x["lock"]: x for x in store.rows(DATA, "lock_tests")}
assert rows["370@1550"]["minutes"] == "0" and not rows["370@1550"]["anchor"], rows["370@1550"]
assert reports and "no candidate result" in reports[-1] and "370@1550" in reports[-1], reports[-1]

# a lock that never booted is not measured: the ZTE applies a lock only at boot, so the step was never locked - do not settle,
# wait for the link and measure 12 min of nothing (2026-11-02 night: a 56-min CPE outage burned two 25-min night steps)
m.st["sets"].clear(); common.state({"lte_lock": None}); brain.AUTO = "dryrun"
locktest.LT_BOOT_WAIT_S = 0; stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["none", "434@1550"]); stop.set(); locktest.LT_BOOT_WAIT_S = 3
rows = store.rows(DATA, "lock_tests")[-2:]
assert [r["lock"] for r in rows] == ["none", "434@1550"], rows
assert all(r["minutes"] == "0" and r["note"].startswith("not measured: no boot inform") for r in rows), rows
assert all(r["rat"] == "lte" and r["mode"] == "as is" and r.get("run") for r in rows), rows
print("OK test_locktest_b")

