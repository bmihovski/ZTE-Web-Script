# LTE lock guard: set when idle, keep, release when the cell is gone / bad, restore later when it is back, remove on request
import importlib.util, os, shutil, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18140"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-guard"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18140", ZTE_PASS_HASH=m.S(m.PW), LOCK_RETRY_H="1")
sys.path.insert(0, ROOT)
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks
def locks(): return [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"]
def wait():
    for i in range(50):
        if not guard.guard["busy"]: return
        time.sleep(0.05)
def row(cell="1550/434", up="3600", call="1", ca="B3/1550/434:-90/10@20", nb="1550/434:-86 3350/340:-92", down="1000"):
    return {"lte_cell": cell, "uptime_s": up, "call_ok": call, "lte_ca": ca, "lte_neighbours": nb, "down_bps": down, "up_bps": "1000"}
T = time.time()
assert guard.lock_guard(row()) is None                                      # nothing wanted -> nothing happens
guard.lock_request("434@1550"); assert common.state()["lte_lock"]["state"] == "pending"
assert guard.lock_guard(row(down="900000")) is None                         # busy (a call?) -> wait
assert guard.lock_guard(row()) == "set"; wait(); assert locks() == ["434@1550"] and common.state()["lte_lock"]["state"] == "active"
for i in range(20): assert guard.lock_guard(row()) is None                  # on the cell, fine
assert guard.lock_guard(row(cell="3350/340", up="120")) is None             # just booted: ignored
for i in range(9): assert guard.lock_guard(row(cell="3350/340")) is None   # off the cell, < 10 min
assert guard.lock_guard(row(cell="3350/340"), T) == "release"; wait()
w = common.state()["lte_lock"]; assert locks() == ["434@1550", "0@0"] and w["state"] == "released" and "not on 1550/434 for 10 min" in w["reason"], w
assert guard.lock_guard(row(cell="3350/340"), T + 600) is None                                # before the retry time
assert guard.lock_guard(row(cell="3350/340", nb="3350/340:-92"), T + 4000) is None            # retry time, but the cell is not visible
evs = lambda: [e for e in store.rows(DATA, "events") if e.get("event") == "lock restore pending"]
assert evs() and evs()[-1]["to"] == "lte 434@1550" and "neighbour list" in evs()[-1]["why"], evs()[-1]   # the wait is traceable
n = len(evs())
assert guard.lock_guard(row(cell="3350/340", nb="3350/340:-92"), T + 4060) is None and len(evs()) == n     # once an hour, not per minute
assert guard.lock_guard(row(cell="3350/340", nb="3350/340:-92"), T + 7700) is None and len(evs()) == n + 1   # an hour later: again
assert guard.lock_guard(row(cell="3350/340"), T + 4000) == "restore"; wait()                   # back in the neighbour list
assert locks()[-1] == "434@1550" and common.state()["lte_lock"]["state"] == "active"
for i in range(2): assert guard.lock_guard(row(cell="")) is None             # no service ...
assert guard.lock_guard(row(cell="")) == "release"; wait()                   # ... 3 min -> release
assert "no LTE service for 3 min" in common.state()["lte_lock"]["reason"] and locks()[-1] == "0@0"
locktest.lt["running"] = "x"; common.state({"lte_lock": dict(common.state()["lte_lock"], state="pending")}); assert guard.lock_guard(row()) is None; locktest.lt["running"] = None
guard.lock_request("none"); wait(); assert common.state()["lte_lock"] is None and locks()[-1] == "0@0"
# 5G lock: set together with the LTE lock (one reboot), released when 5G is off the cell 10 min, restored when served by it again
def nr_locks(): return [s[3] for s in m.st["sets"] if s[1] == "NR5G_LOCK_CELL_SET"]
def reboots(): return [s[1] for s in m.st["sets"]].count("REBOOT_DEVICE")
assert zlocks.nr_value("534@636576") == "534,636576,78,30" and zlocks.nr_value("212@156510") == "212,156510,28,15"
guard.guard["reboots"] = []; n0 = reboots()
guard.lock_request("340@100"); guard.lock_request("534@636576", "nr")
R = lambda **kw: dict(row(cell="100/340", ca="B1/100/340:-85/5@20", nb="100/340:-85"), nr_cell=kw.pop("nr", "636576/534"), **kw)
assert guard.lock_guard(R()) == "set"; wait()
assert locks()[-1] == "340@100" and nr_locks() == ["534,636576,78,30"] and reboots() == n0 + 1          # both, one reboot
for i in range(30): assert guard.lock_guard(R(nr="no5G")) is None                                     # idle without 5G: normal in NSA, no count
for i in range(9): assert guard.lock_guard(R(nr="636576/786")) is None
assert guard.lock_guard(R(nr="no5G", down_bps="900000"), T) == "release"; wait()                            # no 5G while there is traffic: counts
w = common.state()["nr_lock"]; assert w["state"] == "released" and "5G not on it for 10 min" in w["reason"] and nr_locks()[-1] == "0,0,0,0", w
assert common.state()["lte_lock"]["state"] == "active" and locks()[-1] == "340@100"                          # LTE lock untouched
for i in range(5): assert guard.lock_guard(R(nr="636576/534"), T + 600) is None                           # back, but before the retry time
assert guard.lock_guard(R(nr="636576/534"), T + 4000) == "restore"; wait(); assert nr_locks()[-1] == "534,636576,78,30"
guard.guard["reboots"] = [time.time()] * guard.LOCK_MAX_REBOOTS_DAY; common.state({"nr_lock": dict(common.state()["nr_lock"], state="pending")})
assert guard.lock_guard(R()) is None                                                                       # daily reboot budget used up
guard.guard["reboots"] = []; guard.lock_request("none", "nr"); wait(); guard.lock_request("none"); wait()
assert nr_locks()[-1] == "0,0,0,0" and locks()[-1] == "0@0" and common.state()["nr_lock"] is None
# reconcile with what the ZTE really has (power cut / failed unlock): released but still locked -> active, no reboot
guard.guard["reboots"] = []; guard.guard["changed_at"] = 0; n0 = len(m.st["sets"])
common.state({"lte_lock": {"cell": "340@100", "state": "released", "retry_at": 0, "reason": "x"}})
zlocks.seen["raw"] = {"LockLTECell": "340,100", "LockNr5gCell": ""}
assert guard.lock_guard(R(nr="636576/534")) is None and common.state()["lte_lock"]["state"] == "active" and len(m.st["sets"]) == n0
# the race: right after a keeper change (reboot in flight) a stale read still shows the lock - must NOT flip released->active
common.state({"lte_lock": {"cell": "340@100", "state": "released", "retry_at": time.time() + 3600, "reason": "x"}})
zlocks.seen["raw"] = {"LockLTECell": "340,100", "LockNr5gCell": ""}; guard.guard["changed_at"] = time.time()
assert guard.lock_guard(R(nr="636576/534")) is None and common.state()["lte_lock"]["state"] == "released"   # gated: stale read in the reboot window, no flip
guard.guard["changed_at"] = 0                                                                                  # change settled -> reconcile flips it
assert guard.lock_guard(R(nr="636576/534")) is None and common.state()["lte_lock"]["state"] == "active"
zlocks.seen["raw"] = {"LockLTECell": "", "LockNr5gCell": ""}                                 # active, but the ZTE lost it -> set again
assert guard.lock_guard(R(nr="636576/534")) == "set"; wait(); assert locks()[-1] == "340@100"
zlocks.seen["raw"] = None; guard.lock_request("none"); wait()
# 2026-10-28 01:03 replay: the ZTE lost all service while locked (tech None, LTE 255/255) -> "no LTE service" -> released after 3 min, not 10
C = zdev.CI; monitor.sample({C + "CurrentAccessTechnology": "None", C + "LTE_EARFCN": "255", C + "LTE_PCI": "255", C + "LTE_SINR": "255", C + "LTE_RSRP": "255",
                    "Device.DeviceInfo.UpTime": "15410"})
h = brain.HIST[-1]; assert h["lte_cell"] == "" and h["lte_sinr"] == "" and h["lte_rsrp"] == "", h
import csv; from keeper import store
ev = [e["event"] for e in store.rows(DATA, "events")]; print(ev)
assert ev == ["lte lock 434@1550", "lte lock removed", "lock restore pending", "lock restore pending", "lte lock 434@1550",
              "lte lock removed", "lte lock removed", "lte lock 340@100 + 5g lock 534@636576",
              "5g lock removed", "5g lock 534@636576", "5g lock removed", "lte lock removed", "lte lock 340@100 still set", "lte lock 340@100 still set",
              "lte lock 340@100 missing", "lte lock 340@100", "lte lock removed"], ev
print("OK test_guard")
