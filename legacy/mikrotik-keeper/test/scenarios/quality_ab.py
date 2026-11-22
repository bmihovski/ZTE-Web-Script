# quality A/B: calls go bad with good signal on an idle link (2026-11-12 evening, unstable weather - likely the site's backhaul):
# trial 1 = the best tested anchor on ANOTHER site, trial 2 = the modem's own choice; keep the clear winner 3 h, else restore.
import datetime, importlib.util, os, shutil, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18192"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-qab"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18192", ZTE_PASS_HASH=m.S(m.PW), LOCK_PAUSE="00:50-06:10")
sys.path.insert(0, ROOT)
from keeper import brain, common, guard, store
brain.reg_st["dir"] = DATA
def wait():
    for i in range(100):
        if not guard.guard["busy"]: return
        time.sleep(0.05)
def sets(): return [(s[1], s[3]) for s in m.st["sets"]]
def evs(): return [r for r in store.rows(DATA, "events") if r["event"] == "lte lock A/B"]
NOW = time.mktime(datetime.datetime.now().replace(hour=19, minute=40, second=0, microsecond=0).timetuple())
def fill(start, n, ok_every=0, down="0", lte="5", nr="11"):
    for i in range(n):
        t = datetime.datetime.fromtimestamp(start + i * 60).strftime("%Y-%m-%d %H:%M:%S")
        brain.HIST.append({"time": t, "uptime_s": "3600", "lte_cell": "100/340", "nr_cell": "636576/534", "lte_sinr": lte, "nr_sinr": nr,
                           "down_bps": down, "up_bps": "0", "call_ok": "1" if ok_every and i % ok_every == 0 else "0"})
def locked(day_start):
    common.state({"lte_lock": {"cell": "340@100", "state": "active"}, "nr_lock": {"cell": "534@636576", "state": "active"}, "quality_ab": None})
    brain.HIST.clear(); fill(day_start - 30 * 60, 30, ok_every=2); m.st["sets"].clear(); guard.guard["reboots"] = []
    for k in ("100/340", "3350/340", "1550/434"): brain.REG["lte"][k]["last"] = day_start - 600   # seen lately (202 stays stale)
# lock-test history: 340@100 (site 1243, yours) > 340@3350 (same eNB 1243) > 202@100 (site 999, not seen lately) > 434@1550 (site 1072)
for lock, anchor, site, ok in (("340@100", "100/340", "1243/81", "100.0"), ("340@3350", "3350/340", "1243/61", "99.0"),
                               ("202@100", "100/202", "999/1", "98.0"), ("434@1550", "1550/434", "1072/3", "97.0")):
    store.add(DATA, "lock_tests", {"time": "2026-11-02 09:00:00", "rat": "lte", "lock": lock, "anchor": anchor, "site": site, "minutes": "12",
                                   "on_target_pct": "100.0", "call_ok_pct": ok, "loss_pct": "0", "lat_p95": "65", "jitter_ms": "10",
                                   "down_mbps": "60", "up_mbps": "20", "load_s": "20", "load_method": brain.LTE_LOAD_METHOD, "anchor_sinr_p50": "5", "nr_sinr_p50": "8", "nr_arfcn": "636576", "mode": "as is"})
for k, last in (("100/340", NOW), ("3350/340", NOW), ("100/202", NOW - 8 * 3600), ("1550/434", NOW - 600)):
    brain.REG["lte"][k] = {"band": "B1", "rsrp": -90, "last": last, "seen_min": 300}
assert guard.ab_alt("340@100", {}, NOW) == "434@1550"                          # same eNB and not-seen-lately skipped
row = {"uptime_s": "3600", "down_bps": "0", "up_bps": "0"}
common.clock["ok"] = True
# --- gates ---
locked(NOW)
assert guard.quality_ab(dict(row, uptime_s="300"), NOW) is None                  # just booted
brain.HIST.clear(); fill(NOW - 30 * 60, 30, ok_every=2, down="900000"); assert guard.quality_ab(row, NOW) is None   # our own traffic
brain.HIST.clear(); fill(NOW - 30 * 60, 30, ok_every=2, lte="1"); assert guard.quality_ab(row, NOW) is None        # weak anchor
brain.HIST.clear(); fill(NOW - 30 * 60, 30, ok_every=1); assert guard.quality_ab(row, NOW) is None                 # calls fine
locked(NOW); assert guard.quality_ab(row, time.mktime(datetime.datetime.now().replace(hour=2, minute=0).timetuple())) is None  # LOCK_PAUSE
guard.guard["reboots"] = [NOW] * guard.LOCK_MAX_REBOOTS_DAY; assert guard.quality_ab(row, NOW) is None             # no reboot left for the restore
# --- day 1: another site 50 % (no better), then the modem's own choice 100 % -> kept unlocked 3 h, then your locks back ---
locked(NOW)
assert guard.quality_ab(row, NOW) == "trial"; wait()
assert sets() == [("LTE_LOCK_CELL_SET", "434@1550"), ("NR5G_LOCK_CELL_SET", "0,0,0,0"), ("REBOOT_DEVICE", "")], sets()
assert common.state()["lte_lock"]["cell"] == "434@1550" and common.state()["lte_lock"]["ab"] and common.state()["nr_lock"]["state"] == "released"
assert guard.guard["reboots"] == []                                               # trials don't eat the daily lock budget
assert guard.quality_ab(row, NOW + 10 * 60) is None                               # trial running
fill(NOW + 6 * 60, 15, ok_every=2)                                                # 50 %
assert guard.quality_ab(dict(row, down_bps="900000"), NOW + 21 * 60) is None      # in use: the next reboot waits for an idle minute
assert guard.quality_ab(row, NOW + 22 * 60) == "trial"; wait()
assert sets()[-2:] == [("LTE_LOCK_CELL_SET", "0@0"), ("REBOOT_DEVICE", "")], sets()   # LTE off; 5G not written again
T = NOW + 22 * 60; fill(T + 6 * 60, 15, ok_every=1)                               # 100 %
assert guard.quality_ab(row, T + 21 * 60) == "kept"
ab = common.state()["quality_ab"]; assert ab["phase"] == "keep" and ab["arm"] == "free" and ab["res"] == {"site": 53.3, "free": 100.0}, ab
assert evs()[-1]["to"] == "kept unlocked for 3 h" and "LTE 434@1550 (another site) 53.3%" in evs()[-1]["why"], evs()[-1]
n = len(sets()); assert guard.quality_ab(row, T + 60 * 60) is None and len(sets()) == n   # keeping, nothing written
assert guard.quality_ab(row, T + 21 * 60 + 3 * 3600) == "restored"
w = common.state()["lte_lock"]; assert w["cell"] == "340@100" and w["state"] == "pending" and not w.get("ab"), w
assert common.state()["nr_lock"]["cell"] == "534@636576" and evs()[-1]["to"] == "back to your locks"
# your locks back = both legs in ONE write + ONE reboot (2026-11-17: 534 at 17:23:53, 340@100 at 17:26:53 - two reboots, each leg on its own 'back' check)
n = len(sets()); assert guard.lock_guard(row, T + 21 * 60 + 3 * 3600 + 60) == "set"; wait()
new = sets()[n:]; assert [k for k, v in new].count("REBOOT_DEVICE") == 1 and ("LTE_LOCK_CELL_SET", "340@100") in new, new
assert common.state()["lte_lock"]["state"] == "active" and common.state()["nr_lock"]["state"] == "active", (common.state()["lte_lock"], common.state()["nr_lock"])
assert guard.quality_ab(row, T + 4 * 3600) is None                               # once a day
# --- day 2: another site already >= 90 % -> kept, no second trial ---
D2 = NOW + 86400; locked(D2)
assert guard.quality_ab(row, D2) == "trial"; wait(); fill(D2 + 6 * 60, 15, ok_every=1)
assert guard.quality_ab(row, D2 + 21 * 60) == "kept"
assert common.state()["quality_ab"]["res"] == {"site": 100.0} and common.state()["lte_lock"]["cell"] == "434@1550" and evs()[-1]["to"] == "kept LTE 434@1550 (another site) for 3 h"
# --- day 3: another site 80 %, own choice 60 % -> the site wins and is put back (one more reboot) ---
D3 = D2 + 86400; locked(D3)
assert guard.quality_ab(row, D3) == "trial"; wait(); fill(D3 + 6 * 60, 15, ok_every=1); 
for i in (1, 2, 3): brain.HIST[-i] = dict(brain.HIST[-i], call_ok="0")    # 80 %
assert guard.quality_ab(row, D3 + 21 * 60) == "trial"; wait()
T3 = D3 + 21 * 60; fill(T3 + 6 * 60, 15, ok_every=2); 
for i in (2, 4): brain.HIST[-i] = dict(brain.HIST[-i], call_ok="1")       # ~60 %
assert guard.quality_ab(row, T3 + 21 * 60) == "kept"; wait()
assert sets()[-2:] == [("LTE_LOCK_CELL_SET", "434@1550"), ("REBOOT_DEVICE", "")] and common.state()["lte_lock"]["cell"] == "434@1550", sets()
# --- day 4: neither helps -> your locks back now, provider-wide ---
D4 = D3 + 86400; locked(D4)
assert guard.quality_ab(row, D4) == "trial"; wait(); fill(D4 + 6 * 60, 15, ok_every=2)
assert guard.quality_ab(row, D4 + 21 * 60) == "trial"; wait(); T4 = D4 + 21 * 60; fill(T4 + 6 * 60, 15, ok_every=2)
assert guard.quality_ab(row, T4 + 21 * 60) == "restored"
assert common.state()["lte_lock"]["cell"] == "340@100" and common.state()["lte_lock"]["state"] == "pending"   # back at once, one reboot
assert evs()[-1]["to"] == "locks restored" and "provider's side" in evs()[-1]["why"], evs()[-1]
# --- day 5: you change the lock during the trial -> the A/B stops and leaves your choice alone ---
D5 = D4 + 86400; locked(D5)
assert guard.quality_ab(row, D5) == "trial"; wait()
guard.lock_request("500@1550")
assert guard.quality_ab(row, D5 + 21 * 60) is None
assert common.state()["quality_ab"]["phase"] == "done" and common.state()["lte_lock"]["cell"] == "500@1550" and common.state()["lte_lock"]["state"] == "pending"
w = common.state()["nr_lock"]; assert w["cell"] == "534@636576" and w["state"] == "pending" and not w.get("ab"), w   # 5G back to yours, in the same write as your new LTE lock
# --- no other site known -> only the modem's own choice is tried ---
D6 = D5 + 86400; locked(D6); brain.REG["lte"]["1550/434"]["last"] = 0
assert guard.quality_ab(row, D6) == "trial" and common.state()["quality_ab"]["arms"] == ["free"]; wait()
assert sets()[0] == ("LTE_LOCK_CELL_SET", "0@0"), sets()
# --- an internet call blocks every A/B reboot (2026-11-20: "idle" is < 0.5 Mbit/s up, a Teams / Zoom audio call is ~5-12 kB/s each
# way - it looked idle, a trial reboot would have dropped the meeting): AB_QUIET_MIN minutes under AB_QUIET_UP / _DOWN bytes/s ---
D7 = D6 + 86400; locked(D7)
for r in list(brain.HIST)[-guard.AB_QUIET_MIN:]: r.update(up_bps="9000", down_bps="12000")          # a Teams audio call: "idle" by brain.idle
assert all(brain.idle(r) for r in list(brain.HIST)[-guard.AB_QUIET_MIN:]) and guard.quality_ab(row, D7) is None   # no trial during it
for r in list(brain.HIST)[-guard.AB_QUIET_MIN:]: r.update(up_bps="500", down_bps="800")             # the call is over
assert guard.quality_ab(row, D7) == "trial"; wait()
assert guard.quiet() and not guard.quiet([{"up_bps": "9000", "down_bps": "0"}] * guard.AB_QUIET_MIN)
assert not guard.quiet([{"up_bps": "0", "down_bps": "0"}] * (guard.AB_QUIET_MIN - 1))               # too few minutes: not proven quiet
# realistic (2026-11-20, 4 days, 2 PCs + 2 phones working from home: up p50 0.6 kB/s, p90 19; all-under-3k in only 26 % of 5-min
# windows): a CALL is STEADY traffic - every minute >= AB_CALL_BYTES in a direction; bursts (updates, mail, browsing) are not
burst = [{"up_bps": "500", "down_bps": "600"}] * 3 + [{"up_bps": "40000", "down_bps": "900000"}, {"up_bps": "700", "down_bps": "300"}]
assert guard.quiet(burst), "an update burst is no call"
assert not guard.quiet([{"up_bps": "1500", "down_bps": "8000"}] * guard.AB_QUIET_MIN), "a muted meeting: steady audio down"
assert guard.quiet([{"up_bps": "1500", "down_bps": "8000"}] * (guard.AB_QUIET_MIN - 1) + [{"up_bps": "1500", "down_bps": "900"}])   # not every minute
# --- the B cell (docs/NIGHTLY-PLAN.md item 6, 2026-11-18): the weighted score first, 5G pairing required, daytime results only ---
full = lambda lock, anchor, site, down, nr, mode="as is": store.add(DATA, "lock_tests", {"time": "2026-11-18 10:00:00", "rat": "lte", "lock": lock,
    "anchor": anchor, "site": site, "minutes": "12", "on_target_pct": "100.0", "call_ok_pct": "100", "anchor_sinr_p50": "5", "nr_sinr_p50": "8",
    "nr_arfcn": nr, "down_mbps": down, "load_s": "20", "load_method": brain.LTE_LOAD_METHOD, "up_mbps": "25", "loss_pct": "0", "lat_p95": "40", "jitter_ms": "5", "mode": mode})
for k in ("100/340", "1550/434", "6400/112", "6400/121", "3774/112"): brain.REG["lte"][k] = {"band": "B1", "rsrp": -90, "last": NOW, "seen_min": 300}
full("112@6400", "6400/112", "2000/1", "150", "")                    # best throughput, no 5G -> not a B cell
full("121@6400", "6400/121", "2001/1", "120", "636576")             # scored, 5G: beats 434@1550 on measured quality and throughput
full("112@3774", "3774/112", "2002/1", "190", "636576", "night")   # night only: no daytime confirmation -> not a B cell
assert guard.ab_alt("340@100", {}, NOW) == "121@6400", guard.ab_alt("340@100", {}, NOW)
# Legacy partial results cannot move the automatic trial to another site.
store.add(DATA, "lock_tests", {"time": "2026-11-18 10:00:00", "rat": "lte", "lock": "77@1550", "anchor": "1550/77", "site": "3000/1", "minutes": "12",
    "on_target_pct": "100.0", "call_ok_pct": "100", "anchor_sinr_p50": "9", "nr_sinr_p50": "12", "nr_arfcn": "636576", "loss_pct": "0",
    "lat_p95": "40", "jitter_ms": "5", "mode": "as is"})
brain.REG["lte"]["1550/77"] = {"band": "B3", "rsrp": -90, "last": NOW, "seen_min": 300}
assert guard.ab_alt("340@100", {}, NOW) == "121@6400", guard.ab_alt("340@100", {}, NOW)
print("OK test_quality_ab")
