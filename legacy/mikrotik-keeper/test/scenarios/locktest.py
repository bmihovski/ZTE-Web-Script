# LTE lock test: baseline + each candidate locked (lock + reboot each), measured, ranked; the lock is always removed at the end
import csv, importlib.util, os, shutil, sys, threading, time, datetime
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18130"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-lt"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18130", ZTE_PASS_HASH=m.S(m.PW), LOCKTEST_SETTLE_S="0", LOCKTEST_ONLINE_TIMEOUT_S="0", LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3")
sys.path.insert(0, ROOT)
from keeper import store
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks
CELLS = {"none": ("3350/340", "B7/3350/340:-92/3@20 B3/1550/340:-93/8@20", "636576/786", "-1", "1243/61"),
         "434@1550": ("1550/434", "B3/1550/434:-90/10@20 B1/100/340:-96/8@20", "636576/534", "8", "1072/3"),
         "340@1550": ("1550/340", "B3/1550/340:-91/8@20 B7/3350/340:-92/3@20", "636576/534", "7", "1243/1")}
stop = threading.Event()
def zte():                     # fake ZTE: a reboot request -> boot inform; then one sample every 0.2 s for the current lock
    seen = 0
    while not stop.is_set():
        n = sum(1 for s in m.st["sets"] if s[1] == "REBOOT_DEVICE")
        if n > seen: seen = n; time.sleep(0.3); acs.st["boots"] = acs.st.get("boots", 0) + 1
        cur = locktest.lt["running"]
        if cur in CELLS:
            lte, ca, nr, sinr, site = CELLS[cur]
            brain.add({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "lte_cell": lte, "lte_site": site, "lte_ca": ca, "lte_rsrq": "-12",
                         "nr_cell": nr, "nr_sinr": sinr, "call_ok": "1", "lat_p95": "50", "jitter_ms": "5", "loss_pct": "0"})
        time.sleep(0.2)
threading.Thread(target=zte, daemon=True).start()
phases = []; threading.Thread(target=lambda: [phases.append(locktest.lt.get("phase")) or time.sleep(0.05) for _ in range(60)], daemon=True).start()
locktest.lock_test(["none", "434@1550", "340@1550", "370@1550"])
stop.set()
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
for r in rows.values(): print(r)
assert rows["434@1550"]["on_target_pct"] == "100.0" and rows["434@1550"]["anchor"] == "1550/434" and rows["434@1550"]["anchor_sinr_p50"] == "10.0"
assert rows["434@1550"]["site"] == "1072/3" and rows["none"]["site"] == "1243/61" and rows["370@1550"]["site"] == ""
assert all(r["rat"] == "lte" and r["antennas"] == "" for r in rows.values())    # an LTE test never sweeps the antennas
assert not [s for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"]
assert zdev.eci("4db3d") == "1243/61" and zdev.eci("") == "" and zdev.eci(None) == ""
# a second column change keeps the older file as .v2 instead of overwriting .v1
assert rows["434@1550"]["nr_cells"].startswith("534:") and rows["none"]["nr_cells"].startswith("786:") and rows["none"]["on_target_pct"] == ""
assert rows["370@1550"]["note"].startswith("no samples")                     # cell not reachable from here
locks = [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"]
assert locks == ["0@0", "434@1550", "340@1550", "370@1550", "0@0"], locks       # baseline unlocked, ... , always unlocked at the end
assert [s[1] for s in m.st["sets"]].count("REBOOT_DEVICE") == 5
assert common.state().get("lock_test") is None and brain.ctx.get("hold") is None and locktest.lt["running"] is None
ev = store.rows(DATA, "events"); assert [e["event"] for e in ev] == ["lock test none", "lock test 434@1550", "lock test 340@1550", "lock test 370@1550"], ev
# align mode: after each lock the keeper waits for "aligned" (heading), baseline measured as is
m.st["sets"].clear(); stop.clear(); threading.Thread(target=zte, daemon=True).start()
def aligner(headings):
    for h in headings:
        while not locktest.lt["waiting"]: time.sleep(0.05)
        assert locktest.lt["running"] in ("434@1550", "340@1550"), locktest.lt["running"]
        locktest.lt["heading"] = h; locktest.lt["go"].set()
        while locktest.lt["waiting"]: time.sleep(0.05)
threading.Thread(target=aligner, args=(["108", "302"],), daemon=True).start()
locktest.lock_test(["none", "434@1550", "340@1550"], align=True)
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
assert (rows["none"]["heading"], rows["none"]["mode"]) == ("", "as is") and (rows["434@1550"]["heading"], rows["434@1550"]["mode"]) == ("108", "aligned")
assert rows["340@1550"]["heading"] == "302" and rows["434@1550"]["anchor_sinr_p50"] == "10.0"
assert [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"] == ["0@0", "434@1550", "340@1550", "0@0"]
# nobody continues -> the test stops, lock removed
m.st["sets"].clear(); locktest.LT_ALIGN_TIMEOUT_MIN = 0.01
locktest.lock_test(["none", "434@1550", "340@1550"], align=True); stop.set()
assert [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"] == ["0@0", "434@1550", "0@0"] and locktest.lt["running"] is None and not locktest.lt["waiting"]
assert any("measuring until" in (x or "") for x in phases) and any("rebooting" in (x or "") for x in phases) and locktest.lt["phase"] == "", set(phases)
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
print("OK test_locktest")
