# offline release: the ZTE locked to a cell that is gone has no WAN and never informs -> no samples -> the guard never runs.
# The watchdog path releases the LTE lock on the web page when: no inform for OFFLINE_RELEASE_MIN + no internet + a lock held + idle
# (2026-11-13: power back ~04:00 inside the night switch-off, stale router clock, 340@100 still locked - 25 min down until released by hand)
import importlib.util, os, shutil, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18190"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-offline"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="tr069", DATA=DATA, ZTE_HOST="127.0.0.1:18190", ZTE_PASS_HASH=m.S(m.PW), LOCK_RETRY_H="1",
                  LOCK_PAUSE="00:50-06:10", OFFLINE_RELEASE_MIN="8")
sys.path.insert(0, ROOT)
from keeper import acs, brain, common, guard, locktest
def sets(): return [(s[1], s[3] if len(s) > 3 else "") for s in m.st["sets"]]
def probes(ok, n=60, now=None):
    now = now or time.time(); brain.lat_buf.clear()
    for i in range(n): brain.lat_buf.append((now - 2 * (n - i), 20.0 if ok else None, "1.1.1.1"))
Q = 9 * 60                                                                     # 9 min without an inform
common.state({"lte_lock": {"cell": "340@100", "state": "active"}, "lte_lock_fmt": {"lock": "{pci},{earfcn}", "unlock": ""}})
probes(False)
assert not guard.offline_release(5 * 60) and not m.st["sets"]                   # < 8 min quiet: a normal boot takes 1-3 min
probes(True); assert not guard.offline_release(Q) and not m.st["sets"]          # internet works -> not offline (informs stopped for another reason)
probes(False, n=5); assert not guard.offline_release(Q)                         # too few probes to judge
probes(False); locktest.lt["running"] = "434@1550"
assert not guard.offline_release(Q) and not m.st["sets"]; locktest.lt["running"] = None   # a lock test reboots on purpose: never
common.state({"lte_lock": {"cell": "340@100", "state": "released"}}); assert not guard.offline_release(Q)   # nothing held
# all conditions: released over the WEB page (no TR-069 session exists while offline - no 180 s wait), then a reboot
common.state({"lte_lock": {"cell": "340@100", "state": "active"}}); probes(False)
t0 = time.time(); assert guard.offline_release(Q); assert time.time() - t0 < 10, "must not wait for a TR-069 session"
assert sets()[-2:] == [("LTE_LOCK_CELL_SET", "0@0"), ("REBOOT_DEVICE", "")], sets()
w = common.state()["lte_lock"]; assert w["state"] == "released" and "no inform for 9 min and no internet" in w["reason"] and w["retry_at"] > time.time(), w
assert common.state().get("nr_lock") is None                                   # the 5G lock is not touched
# one try per outage: released now -> no repeat; a failed web login retries only after 10 min
n = len(m.st["sets"]); assert not guard.offline_release(Q) and len(m.st["sets"]) == n
common.state({"lte_lock": {"cell": "340@100", "state": "active"}}); guard.guard["offline_try"] = 0
m.st["refuse"] = {"LTE_LOCK_CELL_SET"}; assert not guard.offline_release(Q) and common.state()["lte_lock"]["state"] == "active"
m.st["refuse"] = set(); assert not guard.offline_release(Q)                     # rate limit: 10 min between tries
T2 = time.time() + 601; probes(False, now=T2); assert guard.offline_release(Q, now=T2) and common.state()["lte_lock"]["state"] == "released"
# the device without the hook (ATL) is skipped
common.state({"lte_lock": {"cell": "340@100", "state": "active"}}); guard.guard["offline_try"] = 0
import keeper.devices.zte as z; h = z.offline_unlock_lte; del z.offline_unlock_lte
assert not guard.offline_release(Q); z.offline_unlock_lte = h
print("OK test_offline")
