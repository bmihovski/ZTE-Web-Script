# night screen: in the window, after 30 idle minutes, the current lock + strong untested main-band cells are re-tested; the lock is put
# back (one reboot), rows are mode "night" (never in the daytime ranking), the finding recommends a daytime test; traffic stops it
import datetime, importlib.util, os, shutil, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18170"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-night"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18170", ZTE_PASS_HASH=m.S(m.PW), LOCKTEST_SETTLE_S="0", LOCKTEST_ONLINE_TIMEOUT_S="0",
                  LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3", NIGHT_SCREEN="02:00-05:00", NIGHT_ALWAYS="1", BANDSCAN_NIGHT="")   # the screen itself: research mode (every night)
os.environ.setdefault("LOCKTEST_IPERF", "0")   # the mock ZTE runs no iperf3 (a step would wait 160 s for it)
sys.path.insert(0, ROOT); from keeper import store
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks
brain.reg_st["dir"] = DATA
NOW = time.time(); T230 = time.mktime(datetime.datetime.now().replace(hour=2, minute=30).timetuple())
reg = brain.REG["lte"]
for cell, band, rsrp in [("100/340", "B1", -82), ("100/264", "B1", -86), ("100/434", "B1", -87), ("1550/434", "B3", -86), ("6400/112", "B20", -74), ("100/66", "B1", -95)]:
    reg[cell] = {"band": band, "rsrp": rsrp, "first": NOW - 86400, "last": NOW, "seen_min": 500, "anchor_min": 0, "ca_min": 0}
common.state({"lte_lock": {"cell": "340@100", "state": "active"}})
store.add(DATA, "lock_tests", {"time": common.stamp(), "lock": "434@1550", "mode": "aligned", "minutes": "12"})           # tested today -> skipped
ts_ = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
for mm in range(30):      # the 30 night minutes before 02:30 (keeper/nightplan.py): the B1 cells all the time, B20 112 once (3 %), 66@100 weak
    store.add(DATA, "samples", {"time": ts_(T230 - 1800 + 60 * mm), "lte_cell": "100/340", "lte_rsrp": "-82",
                                "lte_neighbours": "100/340:-82 100/264:-86 100/434:-87 100/66:-95" + (" 6400/112:-74" if mm == 3 else "")})
assert locktest.night_candidates(T230) == ["264@100", "434@100"], locktest.night_candidates(T230)                 # at night, strong, not the lock
locktest.NIGHT_BANDS = ["1", "3", "7"]                                                                     # the env form (2026-11-19: "B1" != "1" dropped every cell)
assert locktest.night_candidates(T230) == ["264@100", "434@100"], locktest.night_candidates(T230)
locktest.NIGHT_BANDS = ["3"]; assert locktest.night_candidates(T230) == []; locktest.NIGHT_BANDS = []
CELLS = {"340@100": ("100/340", "B1/100/340:-82/30@20", "8"), "264@100": ("100/264", "B1/100/264:-86/34@20", "11"), "434@100": ("100/434", "B1/100/434:-91/20@20", "7")}
stop = threading.Event()
def zte():
    seen = None              # the last reboot entry itself, not a count: sets.clear() + one new reboot gave the same count (flaky, 2026-11-18)
    while not stop.is_set():
        last = next((s for s in reversed(m.st["sets"]) if s[1] == "REBOOT_DEVICE"), None)
        if last is not None and last is not seen: seen = last; time.sleep(0.05); acs.st["boots"] = acs.st.get("boots", 0) + 1
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
from keeper import captest
common.state({"nr_lock": {"cell": "534@636576", "state": "active"}})     # your 5G lock (n78) - it must not hide the anchors' own 5G
def _meas():                                  # the modem's iperf3; NSA adds the 5G leg on traffic -> a sample during it shows n28
    meas.append(locktest.lt.get("iperf")); r = dict(list(brain.HIST)[-1]); r.update(time=time.strftime("%Y-%m-%d %H:%M:%S"), nr_cell="151600/77")
    brain.HIST.append(r); return 150.0, 25.5, "iperf3 a:1"
locktest.LT_IPERF = True; meas = []; cm0 = captest.measure; captest.measure = _meas
assert locktest.maybe_night(idle, T230) and not locktest.maybe_night(idle, T230)                                    # once a night
time.sleep(0.5)
while locktest.lt["running"]: time.sleep(0.1)
captest.measure = cm0; locktest.LT_IPERF = False
rows = [r for r in store.rows(DATA, "lock_tests") if r.get("mode") == "night"]
# each measured step also has the modem's throughput while locked + its boot time (2026-11-18: the score's biggest parts, and the
# window budget - a B3 lock hung the ZTE 31 min); the run's own iperf is flagged so the traffic check does not stop the screen
assert all(float(r["down_mbps"]) == 150.0 and float(r["up_mbps"]) == 25.5 for r in rows) and meas and all(meas), (rows, meas)   # the store keeps text
assert all(0 <= float(r["boot_s"]) <= 3 for r in rows), [r.get("boot_s") for r in rows]
assert "down_mbps" in locktest.LT_COLS and "boot_s" in locktest.LT_COLS
# the 5G cell while the iperf ran (2026-11-18: the idle window alone said "no5G"; NSA adds the leg on traffic)
assert all("151600/77:1" in r["nr_iperf"].split() for r in rows), [r.get("nr_iperf") for r in rows]   # the mock adds a sample every 0.2 s: one may follow the iperf
assert [r["lock"] for r in rows] == ["340@100", "264@100", "434@100"] and len({r["run"] for r in rows}) == 1, rows
locks = [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"]
assert locks == ["340@100", "264@100", "434@100", "340@100"], locks                                   # the lock put straight back
# 2026-11-18: the B20 night run kept the 5G lock on 534 (n78) - every B20 step showed "no5G", our lock, not the network. A night step
# frees 5G in the same write (no extra reboot) so the anchor's own pairing (n28?) shows; your 5G lock comes back with the LTE lock
nrs = [s[3] for s in m.st["sets"] if s[1] == "NR5G_LOCK_CELL_SET"]
assert nrs[:-1] == ["0,0,0,0"] * 3 and "534" in nrs[-1] and len(nrs) == 4, nrs
w5 = common.state()["nr_lock"]; assert w5["cell"] == "534@636576" and w5["state"] in ("active", "pending"), w5   # kept (the mock never reports a 5G lock -> the guard re-checks)
assert common.state()["lte_lock"]["state"] == "active" and "264@100" not in [r["lock"] for r in brain.anchor_ranking()["ranked"]]
f = brain.night_finding(); print(f)
assert f[0].startswith("RECOMMEND: night screen") and "264@100 anchor SINR 34" in f[0] and "434@100" not in f[0].split(" beat ")[0], f
# its own iperf is not "traffic": the screen is not stopped while lt["iperf"] is set
locktest.lt.update(night=True, running="434@100", cut=None, iperf=True); locktest.maybe_night(dict(idle, down_bps="900000"))
assert locktest.lt["cut"] is None, locktest.lt["cut"]
# nor is the modem's own reconnect right after a step's reboot (2026-11-18 08:56: the B8 step was "stopped by you" 3 s after its
# boot - uptime 72 s, 82 kB/s up): traffic counts only once the modem is up longer than the settle time
locktest.lt.update(iperf=False, cut=None); locktest.maybe_night(dict(idle, up_bps="82189", down_bps="57371", uptime_s="72"))
assert locktest.lt["cut"] is None, locktest.lt["cut"]
# nor its tail: a sample is the minute's average and arrives after the iperf ended (2026-11-20 02:27:06: 554 kB/s up, 6 s after the
# baseline's iperf - "stopped by you", the night over after its baseline)
locktest.lt.update(iperf=False, cut=None, iperf_end=time.monotonic()); locktest.maybe_night(dict(idle, up_bps="553907", down_bps="5554"))
assert locktest.lt["cut"] is None, locktest.lt["cut"]
locktest.lt.update(iperf_end=time.monotonic() - locktest.IPERF_TAIL_S - 1); locktest.maybe_night(dict(idle, up_bps="553907", down_bps="5554"))
assert locktest.lt["cut"] == "stop", locktest.lt["cut"]                                                    # later: real traffic stops it
locktest.lt.update(cut=None)
locktest.lt.update(night=False, running=None, iperf=False)
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
# repeated night no-inform on a cell (CETIN powers it down overnight) -> skip it at night for NIGHT_NOINFORM_DAYS, so the 2 a.m.
# run stops burning a reboot on it every night (2026-11-04: 201/236/202@1550 each failed "no boot inform" three nights running)
NX = time.time(); store.db(DATA).execute("DELETE FROM rec WHERE kind = 'lock_tests'")
t1 = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NX - 86400)); t2 = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NX - 2 * 86400))
for tt in (t1, t2): store.add(DATA, "lock_tests", {"time": tt, "lock": "434@1550", "mode": "night", "minutes": 0, "note": "not measured: no boot inform within 420 s"})
store.add(DATA, "lock_tests", {"time": t1, "lock": "100@1550", "mode": "night", "minutes": 0, "note": "no boot inform within 420 s"})   # only one night -> not skipped yet
assert locktest._night_unreachable(NX) == {"434@1550"}, locktest._night_unreachable(NX)
for _ in range(2): store.add(DATA, "lock_tests", {"time": t1, "lock": "264@100", "mode": "night", "minutes": 12, "note": ""})           # measured -> no vote
assert "264@100" not in locktest._night_unreachable(NX)
old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NX - 10 * 86400))
for _ in range(2): store.add(DATA, "lock_tests", {"time": old, "lock": "900@1550", "mode": "night", "minutes": 0, "note": "no boot inform within 420 s"})
assert "900@1550" not in locktest._night_unreachable(NX)                                                                              # outside the window -> retry
common.state({"lte_lock": {"cell": "340@100", "state": "active"}}); reg["1550/434"].update(last=NX, seen_min=500, rsrp=-86)
for mm in range(10): store.add(DATA, "samples", {"time": ts_(NX - 3 * 3600 + 60 * mm), "lte_cell": "1550/434", "lte_rsrp": "-86", "lte_neighbours": "1550/434:-86"})
assert "434@1550" not in locktest.night_candidates(NX), locktest.night_candidates(NX)                                                  # the only B3, but skipped
print("OK test_night_noinform_skip")
# the window's end is a hard stop: a step that would not finish before it is not started (2026-11-18: the 08:00 restore + work)
stop.clear(); threading.Thread(target=zte, daemon=True).start()
locktest.lock_test(["none", "264@100", "434@100"], night=True, deadline=time.time()); stop.set()
rows = [r for r in store.rows(DATA, "lock_tests") if r.get("run") == locktest.lt["run"]]
assert [r["lock"] for r in rows] == ["none", "264@100"] and rows[1]["note"].startswith("not tested: the night window ends"), rows
assert locktest.step_s() == 90 + locktest.LT_SETTLE_S + locktest.LT_MEASURE_MIN * 60 + (100 if locktest.LT_IPERF else 0)

# a weekday in NIGHT_SCREEN: one test night a week (2026-11-17: router + modem powered off the other nights). The day is the date of
# the night's morning: "Sat 02:00-05:00" = Friday night; "Fri-Sat" = two nights; no day = every night (as before)
sat = time.mktime((2026, 11, 21, 2, 30, 0, 0, 0, -1)); fri = sat - 86400; sun = sat + 86400                    # 2026-11-21 is a Saturday
NW = locktest.NIGHT
for spec, yes, no in (("Sat 02:00-05:00", [sat], [fri, sun, sat + 3 * 3600]), ("Fri-Sat 02:00-05:00", [fri, sat], [sun]),
                      ("Sun-Mon 02:00-05:00", [sun, sun + 86400], [sat]), ("02:00-05:00", [fri, sat, sun], [sat - 3600]),
                      ("Sat 23:00-01:00", [sat - 3.5 * 3600 + 86400], [sat - 3.5 * 3600])):     # a window past midnight: the evening's day
    locktest.NIGHT = spec
    assert all(locktest.in_night(t) for t in yes) and not any(locktest.in_night(t) for t in no), spec
locktest.NIGHT = "Sun 02:00-05:00"; assert not locktest.maybe_night(idle, sat)                     # wired into the night job
locktest.NIGHT = NW

# --- the normal setup (2026-11-17): the night screen runs only on a night armed from the ZTE helper (persisted), the router + modem
# are powered off the other nights; NIGHT_ALWAYS=1 is the research override (every night). An evening Telegram reminds.
from keeper import alert
evs = lambda: store.rows(DATA, "events")
locktest.NIGHT_ALWAYS = False; locktest.NIGHT = "02:00-05:00"
fri21 = time.mktime((2026, 11, 20, 21, 30, 0, 0, 0, -1)); sat0230 = fri21 + 5 * 3600; sat0130 = sat0230 - 3600   # Fri 20 Nov 21:30
common.state({"night_last": None, "night_armed": None, "night_remind_last": None})
assert not locktest.armed(sat0230) and not locktest.maybe_night(idle, sat0230)                       # not armed: no screen
assert locktest.night_arm(True, fri21) == {"armed": "2026-11-21"} and locktest.armed(sat0230)            # armed in the evening -> that night
assert evs()[-1]["event"] == "night screen armed" and evs()[-1]["to"] == "2026-11-21", evs()[-1]
assert locktest.night_arm(True, sat0130) == {"armed": "2026-11-21"}                                       # after midnight: this night
assert locktest.night_arm(True, sat0230 + 4 * 3600) == {"armed": "2026-11-22"}                            # after the window: the next one
assert locktest.night_arm(False, fri21) == {"armed": None} and not locktest.armed(sat0230)                # cancelled
locktest.NIGHT_ALWAYS = True; assert locktest.armed(sat0230); locktest.NIGHT_ALWAYS = False              # research: every night
# the weekly reminder (2026-11-17): from Friday NIGHT_REMIND (21:30) each evening until Arm is pressed, then quiet until next Friday;
# cancelling the arm brings it back; research mode (NIGHT_ALWAYS=1) never reminds
D = 86400; common.state({"night_armed": None, "night_armed_at": None, "night_remind_last": None}); n = len(reports)
assert not locktest.remind({}, fri21 - 60)                                                              # Fri 21:29: not yet
assert locktest.remind({}, fri21) and "Friday" in reports[-1] and "arm" in reports[-1], reports[-1:]
assert not locktest.remind({}, fri21 + 600)                                                             # once a day
assert locktest.remind({}, fri21 + D) and "not scheduled" in reports[-1] and "2026-11-20" in reports[-1], reports[-1:]   # Sat: still due
locktest.night_arm(True, fri21 + D + 3600)                                                              # armed Sat 22:30
assert not locktest.remind({}, fri21 + 2 * D) and not locktest.remind({}, fri21 + 5 * D)               # Sun..Wed: quiet (even if it failed)
locktest.night_arm(False, fri21 + 5 * D + 3600)                                                         # cancelled Wed 22:30
assert locktest.remind({}, fri21 + 6 * D) and "not scheduled" in reports[-1], reports[-1:]             # Thu: due again
locktest.night_arm(True, fri21 + 6 * D + 600)                                                           # armed Thu (last week's cycle)
assert locktest.remind({}, fri21 + 7 * D) and "Friday" in reports[-1], reports[-1:]                    # next Friday: a new week
locktest.night_arm(True, fri21 + 14 * D - 3600)                                                         # armed Fri 20:30 before the time
assert not locktest.remind({}, fri21 + 14 * D)
locktest.NIGHT_ALWAYS = True; assert not locktest.remind({}, fri21 + 21 * D); locktest.NIGHT_ALWAYS = False   # research: never
assert len(reports) == n + 4 and not any("can go off" in r or "armed for tonight" in r or "research mode" in r for r in reports[n:]), reports[n:]
locktest.NIGHT = "off"; assert locktest.morning(fri21) == "2026-11-21" and not locktest.maybe_night(idle, sat0230); locktest.NIGHT = "02:00-05:00"   # off: arming still answers
# a hand-picked night run (POST /lock-test?cells=...&night=1, 2026-11-18): night mode - recommend only, never auto-apply
got = []; lt0 = locktest.lock_test; locktest.lock_test = lambda plan, align=False, night=False, **k: got.append((plan, night, k.get("deadline")))
from keeper import nightplan; we0 = nightplan.window_end; nightplan.window_end = lambda now, spec: now + 3600   # inside the window: 1 h left
assert locktest.start(["112@6400", "394@6400"], night=True); time.sleep(0.2)
assert got[0][:2] == (["none", "112@6400", "394@6400"], True) and abs(got[0][2] - (time.time() + 3600 - nightplan.MARGIN_S)) < 5, got
nightplan.window_end = lambda now, spec: None; got.clear()                                                  # outside: no deadline
assert locktest.start(["112@6400"], night=True); time.sleep(0.2); assert got[0][2] is None, got
nightplan.window_end = we0
locktest.lock_test = lt0
# a test night starts with a band scan (bands never in the CA: B8 112@3774), then plans with what it found - once a night
from keeper import bandscan
calls, runs = [], []; bs0, lt1 = bandscan.start, locktest.lock_test
bandscan.start = lambda bands: (calls.append(list(bands)), (True, "B8"))[1]; locktest.lock_test = lambda plan, **k: runs.append(plan)
locktest.BANDSCAN_NIGHT = ["8"]; locktest.NIGHT_ALWAYS = True; T5 = T230 + 5 * 86400; common.state({"night_last": None, "night_scan": None})
for _ in range(locktest.NIGHT_IDLE_MIN): brain.HIST.append(dict(idle))
for mm in range(90): store.add(DATA, "samples", {"time": ts_(T5 - 1800 + 20 * mm), "lte_cell": "100/340", "lte_rsrp": "-82", "lte_neighbours": "100/77:-86"})   # most of the night samples
assert not locktest.maybe_night(idle, T5) and calls == [["8"]] and not runs                     # the scan first
assert locktest.maybe_night(idle, T5 + 60) and calls == [["8"]]; time.sleep(0.2); assert runs and "77@100" in runs[0], (runs, common.state().get("night_plan"))   # then the plan (264@100: fully tested above -> skipped)
bandscan.start, locktest.lock_test = bs0, lt1; locktest.BANDSCAN_NIGHT = []; locktest.NIGHT_ALWAYS = False
assert locktest.remind in zdev.minute_jobs()

# 2026-11-21: the low bands are the bad-weather fallbacks; NIGHT_BANDS must be able to keep a B20/B8 band-scan cell (the old
# 1,3,7 env dropped them). The plan's test window is LOCK_PAUSE's end, not NIGHT_SCREEN's start window.
store.add(DATA, "band_scans", {"time": ts_(T230 - 300), "band": "20", "serving": "6400/112", "rsrp": "-74", "neighbours": ""})
locktest.NIGHT_BANDS = ["20"]; assert "112@6400" in locktest.night_candidates(T230), locktest.night_candidates(T230)
locktest.NIGHT_BANDS = ["1", "3", "7"]; assert "112@6400" not in locktest.night_candidates(T230)
locktest.NIGHT_BANDS = []
guard.LOCK_PAUSE = "23:30-08:00"; assert locktest.night_window() == ("23:30", "08:00")
guard.LOCK_PAUSE = ""; assert locktest.night_window() == ("02:00", "05:00")   # the default NIGHT (the test env)
print("OK test_night")
