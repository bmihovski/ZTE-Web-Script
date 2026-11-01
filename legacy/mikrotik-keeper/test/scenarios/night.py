# night screen: in the window, after 30 idle minutes, the current lock + strong untested main-band cells are re-tested; the lock is put
# back (one reboot), rows are mode "night" (never in the daytime ranking), the finding recommends a daytime test; traffic stops it
import datetime, importlib.util, os, shutil, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18170"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-night"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18170", ZTE_PASS_HASH=m.S(m.PW), LOCKTEST_SETTLE_S="0", LOCKTEST_ONLINE_TIMEOUT_S="0",
                  LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3", NIGHT_SCREEN="02:00-05:00")
sys.path.insert(0, ROOT); from keeper import store
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks
brain.reg_st["dir"] = DATA
NOW = time.time(); T230 = time.mktime(datetime.datetime.now().replace(hour=2, minute=30).timetuple())
reg = brain.REG["lte"]
for cell, band, rsrp in [("100/340", "B1", -82), ("100/264", "B1", -86), ("100/434", "B1", -87), ("1550/434", "B3", -86), ("6400/112", "B20", -74), ("100/66", "B1", -95)]:
    reg[cell] = {"band": band, "rsrp": rsrp, "first": NOW - 86400, "last": NOW, "seen_min": 500, "anchor_min": 0, "ca_min": 0}
common.state({"lte_lock": {"cell": "340@100", "state": "active"}})
store.add(DATA, "lock_tests", {"time": common.stamp(), "lock": "434@1550", "mode": "aligned"})           # tested today -> skipped
assert locktest.night_candidates(T230) == ["264@100", "434@100"], locktest.night_candidates(T230)                 # B1 strong, not the lock; B20 / weak out
CELLS = {"340@100": ("100/340", "B1/100/340:-82/30@20", "8"), "264@100": ("100/264", "B1/100/264:-86/34@20", "11"), "434@100": ("100/434", "B1/100/434:-91/20@20", "7")}
stop = threading.Event()
def zte():
    seen = 0
    while not stop.is_set():
        n = sum(1 for s in m.st["sets"] if s[1] == "REBOOT_DEVICE")
        if n > seen: seen = n; time.sleep(0.3); acs.st["boots"] = acs.st.get("boots", 0) + 1
        if locktest.lt["running"] in CELLS:
            lte, ca, sinr = CELLS[locktest.lt["running"]]
            brain.add({"time": common.stamp(), "lte_cell": lte, "lte_ca": ca, "nr_cell": "636576/534", "nr_sinr": sinr, "call_ok": "1",
                         "lat_p95": "48", "jitter_ms": "4", "loss_pct": "0", "down_bps": "100", "up_bps": "100"})
        time.sleep(0.2)
threading.Thread(target=zte, daemon=True).start()
idle = {"down_bps": "100", "up_bps": "100", "nr_cell": "no5G", "time": common.stamp()}
assert not locktest.maybe_night(idle, T230)                                                                  # not 30 idle minutes yet
for i in range(30): brain.add(dict(idle, time=common.stamp()))
assert not locktest.maybe_night(idle, T230 - 3 * 3600)                                                       # outside the window
common.clock["ok"] = False; assert not locktest.maybe_night(idle, T230); common.clock["ok"] = True             # router clock not verified (power cut)
assert locktest.maybe_night(idle, T230) and not locktest.maybe_night(idle, T230)                                    # once a night
time.sleep(0.5)
while locktest.lt["running"]: time.sleep(0.1)
rows = [r for r in store.rows(DATA, "lock_tests") if r.get("mode") == "night"]
assert [r["lock"] for r in rows] == ["340@100", "264@100", "434@100"] and len({r["run"] for r in rows}) == 1, rows
locks = [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"]
assert locks == ["340@100", "264@100", "434@100", "340@100"], locks                                   # the lock put straight back
assert common.state()["lte_lock"]["state"] == "active" and "264@100" not in [r["lock"] for r in brain.anchor_ranking()["ranked"]]
f = brain.night_finding(); print(f)
assert f[0].startswith("RECOMMEND: night screen") and "264@100 anchor SINR 34" in f[0] and "434@100" not in f[0].split(" beat ")[0], f
# traffic during the screen -> stopped, lock put back
common.state({"night_last": None}); m.st["sets"].clear(); [e.update(last=T230 + 86400) for e in reg.values()]; store.db(DATA).execute("DELETE FROM rec WHERE kind = 'lock_tests'")
prev = list(locktest.lt["plan"]); locktest.NIGHT_CELLS = ["66@1550"]                                     # NIGHT_CELLS: always re-check
assert locktest.maybe_night(idle, T230 + 86400)
while locktest.lt["plan"] == prev: time.sleep(0.02)
assert locktest.lt["plan"][:2] == ["340@100", "66@1550"], locktest.lt["plan"]                            # the lock, then the home-set cell
while locktest.lt["running"] not in ("264@100", "434@100"): time.sleep(0.05)
locktest.maybe_night(dict(idle, down_bps="900000")); stop.set()
while locktest.lt["running"]: time.sleep(0.1)
assert [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"][-1] == "340@100" and any("stopped" in (r.get("note") or "") for r in store.rows(DATA, "lock_tests"))
# a step that never measured (no boot inform / no CPE web / link timeout) is no evidence about the cell: only a MEASURED night
# row (minutes > 0) with no service blacklists it for 30 days - the 2026-10-31 screen lost 202/236@1550 that way
t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 86400))
store.add(DATA, "lock_tests", {"time": t, "lock": "202@1550", "mode": "night", "anchor": "", "on_target_pct": "", "minutes": 0,
                               "note": "no boot inform within 420 s"})
store.add(DATA, "lock_tests", {"time": t, "lock": "236@1550", "mode": "night", "anchor": "", "on_target_pct": "0.0", "minutes": 12, "note": ""})
assert locktest._dead_cells(time.time()) == {"236@1550"}, locktest._dead_cells(time.time())

# the night Telegram summary itself (the finding is what /report shows; this is the message pushed at the end of the run)
from keeper import alert
reports = []
alert.report = lambda text: reports.append(text) or True
common.state({"lte_lock": {"cell": "340@100", "state": "active"}})
stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["340@100", "264@100"], night=True); stop.set()
assert reports and "night screen" in reports[-1] and "beat 340@100" in reports[-1] and "RECOMMEND" in reports[-1], reports[-1]

# a night run whose FIRST step (the baseline) cannot even be locked: the row carries mode night + the run id (the run stays one
# unit), the baseline counts as not measured -> the summary warns instead of comparing the candidates against each other
m.st["refuse"] = {"LTE_LOCK_CELL_SET"}; reports.clear(); stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["340@100", "264@100"], night=True); stop.set()
run_id = locktest.lt["run"]; m.st["refuse"] = set()
rows = [r for r in store.rows(DATA, "lock_tests") if r.get("run") == run_id]
assert rows and all(r.get("mode") == "night" for r in rows) and rows[0]["lock"] == "340@100" and rows[0]["note"].startswith("lock not set"), rows
assert brain.night_best()[0] == run_id and brain.night_best()[1] is None, (brain.night_best()[0], run_id)
assert reports and "night screen" in reports[-1] and "baseline was not measured" in reports[-1], reports[-1]
print("OK test_night")
