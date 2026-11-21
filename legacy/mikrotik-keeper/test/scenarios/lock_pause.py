# LOCK_PAUSE: the LTE lock's cell is switched off at night (B1 340@100, ~01:00-06:00 on 2026-10-28 / 29) -> released before the window,
# not restored inside it, restored after it once the cell is back
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), LOCK_PAUSE="00:50-06:10", NIGHT_SCREEN="off")
sys.path.insert(0, ROOT)
from keeper import common, guard
import keeper.devices as dv
calls = []
class Dev:                                   # a driver that only records lock changes
    NAME = "ZTE"
    def locks_apply(self, lte, nr): calls.append((lte, nr))
    def lock_value(self, key): return None
    def nr_on(self, row, cell): return False
dv._dev["m"] = Dev()
def wait():                                  # the lock change runs in a thread
    for i in range(100):
        if not guard.guard["busy"]: return
        time.sleep(0.05)
lt = time.localtime(); t0055 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 55, 0, 0, 0, -1)); t0700 = t0055 + 6 * 3600 + 5 * 60
assert guard.pause_end(t0055) == t0055 + 5 * 3600 + 15 * 60 and guard.pause_end(t0700) is None
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
# the modem's clock: the ZTE's wrong offset sign is tolerated, a router 12 h behind is not trusted, unreadable = not trusted
assert abs(common.cpe_skew(iso(t0055) + "-02:00", t0055)) < 1 and abs(common.cpe_skew(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0055)), t0055)) < 1
assert not common.clock_ok()                                                            # no Inform yet
assert common.check_clock(iso(t0700) + "-02:00", t0700 - 12 * 3600) is False and not common.clock_ok()
assert common.cpe_skew("1970-01-01T00:00:00", t0055) < -1e9 and common.cpe_skew("garbage") is None
common.state({"lte_lock": {"cell": "340@100", "state": "active"}})
row = {"lte_cell": "100/340", "uptime_s": "9000", "down_bps": "100", "up_bps": "100", "lte_neighbours": "100/340:-82", "lte_ca": "B1/100/340:-82/30@20"}
assert guard.lock_guard(row, t0055) is None                                             # router clock says 00:55 but is not verified: no pause release
common.clock["bad_mono"] -= 47                                                         # wrong for 47 s, then NTP fixed it
assert common.check_clock(iso(t0055), t0055) is True and common.clock_ok()
ev = [e["event"] for e in __import__("keeper.store", fromlist=["x"]).rows(common.DATA, "events")]
assert ev[-2:] == ["clock not verified", "clock verified"], ev
import datetime, re                              # the wrong stamps cover only the 47 s, not "until now" (2026-11-17: 04:27:02 .. 04:27:49)
why, bad = __import__("keeper.store", fromlist=["x"]).rows(common.DATA, "events")[-1]["why"], common.clock["bad_since"]
end = (datetime.datetime.strptime(bad, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(seconds=47)).strftime("%H:%M:%S")
assert re.search(r"records stamped %s to at most \d\d:\d\d:\d\d carry that wrong time" % re.escape(bad), why), why   # the 47 s + the test's own runtime
assert common.check_clock("") is True and common.clock["ok"] is True   # empty modem time in an Inform: no change to the verdict
from keeper import alert; alert.TOKEN = alert.CHAT = "x"; alert._post = lambda text: None
assert alert.on_event("ZTE", __import__("keeper.store", fromlist=["x"]).rows(common.DATA, "events")[-1]) and "clock was 720 min behind" in alert.sent[-1][2], alert.sent[-1:]
# verified in this run, then a modem clock off = the MODEM's clock (a router reboot would have restarted the keeper): stay trusted
# (constructed; the 2026-11-17 14:53 case that looked like this was the router after a 3rd power cut - see test/scenarios/clock.py case 6)
evn = lambda: [e["event"] for e in __import__("keeper.store", fromlist=["x"]).rows(common.DATA, "events")]
assert common.check_clock(iso(t0055 + 86 * 60), t0055) is True and common.clock_ok() and evn()[-1] == "modem clock wrong", evn()[-2:]
assert "router's time stays trusted" in __import__("keeper.store", fromlist=["x"]).rows(common.DATA, "events")[-1]["why"]
n = len(evn()); assert common.check_clock(iso(t0055 + 86 * 60), t0055 + 60) is True and len(evn()) == n      # said once
assert common.check_clock(iso(t0055), t0055 + 120) is True and len(evn()) == n                                # right again: quiet
assert guard.lock_guard(dict(row, down_bps="900000"), t0055) is None                    # busy: wait
assert guard.lock_guard(row, t0055) == "release"; wait()
w = common.state()["lte_lock"]; assert w["state"] == "released" and w["retry_at"] == t0055 + 5 * 3600 + 15 * 60 and "LOCK_PAUSE" in w["reason"], w
assert guard.lock_guard(dict(row, lte_cell="3350/340"), t0055 + 3 * 3600) is None       # inside the window: no restore
common.clock["ok"] = False                                                              # power cut again: router clock not verified
assert guard.lock_guard(dict(row, lte_cell="3350/340"), t0700) is None                  # -> no restore yet (could be the real night)
common.clock["ok"] = True
assert guard.lock_guard(dict(row, lte_cell="3350/340"), t0700) == "restore"; wait()   # after it, cell back -> restored
assert calls == [("none", None), ("340@100", None)], calls
# the 5G lock goes with it (2026-11-21: 534@636576 stayed locked all night - the band scan and the night steps should see the
# NSA pairs the network gives): released in the SAME write at the pause start, never restored inside it, back with the LTE lock
calls.clear(); n1 = t0055 + 86400; n7 = t0700 + 86400
common.state({"lte_lock": {"cell": "340@100", "state": "active"}, "nr_lock": {"cell": "534@636576", "state": "active"}})
Dev.nr_on = lambda self, row, cell: True                                            # 534 serves all night: no reason to keep it
assert guard.lock_guard(row, n1) == "release"; wait()
assert calls == [("none", "none")], calls                                               # one write, one reboot
w = common.state()["nr_lock"]; assert w["state"] == "released" and "LOCK_PAUSE" in w["reason"] and w["retry_at"] == n1 + 5 * 3600 + 15 * 60, w
guard.guard["nr_back"] = 99
assert guard.lock_guard(dict(row, lte_cell="3350/340"), n1 + 3 * 3600) is None          # inside: neither comes back
assert guard.lock_guard(dict(row, lte_cell="3350/340"), n7) == "restore"; wait()
assert calls[-1] == ("340@100", "534@636576"), calls                                    # both back in ONE write
assert common.state()["nr_lock"]["state"] == "active"
print("OK lock_pause")
