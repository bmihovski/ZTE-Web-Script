# Manual night-mode testing restores both locks, reports separately from daytime, and stops for household traffic.
import importlib.util, os, shutil, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18170"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-night"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:18170", ZTE_PASS_HASH=m.S(m.PW), LOCKTEST_SETTLE_S="0", LOCKTEST_ONLINE_TIMEOUT_S="0",
                  LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3", LOCKTEST_CA_WAIT_S="0", LOCKTEST_RAISE_S="0", NIGHT_SCREEN="02:00-05:00")
os.environ.setdefault("LOCKTEST_IPERF", "0")   # the mock ZTE runs no iperf3 (a step would wait 160 s for it)
sys.path.insert(0, ROOT); from keeper import store
from keeper import acs, brain, common, locktest, store; from keeper.devices import zte as zdev
brain.reg_st["dir"] = DATA
common.state({"lte_lock": {"cell": "340@100", "state": "active"}})
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
from keeper import captest
common.state({"nr_lock": {"cell": "534@636576", "state": "active"}})     # your 5G lock (n78) - it must not hide the anchors' own 5G
def _meas():                                  # the modem's iperf3; NSA adds the 5G leg on traffic -> a sample during it shows n28
    meas.append(locktest.lt.get("iperf")); r = dict(list(brain.HIST)[-1]); r.update(time=time.strftime("%Y-%m-%d %H:%M:%S"), nr_cell="151600/77")
    brain.HIST.append(r); return 150.0, 25.5, "iperf3 a:1"
locktest.LT_IPERF = True; meas = []; cm0 = captest.measure; captest.measure = _meas
locktest.lock_test(["340@100", "264@100", "434@100"], night=True)
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
locktest.lt.update(night=True, running="434@100", cut=None, iperf=True); locktest.cut_night_on_traffic(dict(idle, down_bps="900000"))
assert locktest.lt["cut"] is None, locktest.lt["cut"]
# nor is the modem's own reconnect right after a step's reboot (2026-11-18 08:56: the B8 step was "stopped by you" 3 s after its
# boot - uptime 72 s, 82 kB/s up): traffic counts only once the modem is up longer than the settle time
locktest.lt.update(iperf=False, cut=None); locktest.cut_night_on_traffic(dict(idle, up_bps="82189", down_bps="57371", uptime_s="72"))
assert locktest.lt["cut"] is None, locktest.lt["cut"]
# nor its tail: a sample is the minute's average and arrives after the iperf ended (2026-11-20 02:27:06: 554 kB/s up, 6 s after the
# baseline's iperf - "stopped by you", the night over after its baseline)
locktest.lt.update(iperf=False, cut=None, iperf_end=time.monotonic()); locktest.cut_night_on_traffic(dict(idle, up_bps="553907", down_bps="5554"))
assert locktest.lt["cut"] is None, locktest.lt["cut"]
locktest.lt.update(iperf_end=time.monotonic() - locktest.IPERF_TAIL_S - 1); locktest.cut_night_on_traffic(dict(idle, up_bps="553907", down_bps="5554"))
assert locktest.lt["cut"] == "stop", locktest.lt["cut"]                                                    # later: real traffic stops it
locktest.lt.update(cut=None)
locktest.lt.update(night=False, running=None, iperf=False)
# a hand-picked night run (POST /lock-test?cells=...&night=1, 2026-11-18): night mode - recommend only, never auto-apply
got = []; lt0 = locktest.lock_test; locktest.lock_test = lambda plan, align=False, night=False, **k: got.append((plan, night, k.get("deadline")))
from keeper import nightplan; we0 = nightplan.window_end; nightplan.window_end = lambda now, spec: now + 3600   # inside the window: 1 h left
assert locktest.start(["112@6400", "394@6400"], night=True); time.sleep(0.2)
assert got[0][:2] == (["none", "112@6400", "394@6400"], True) and abs(got[0][2] - (time.time() + 3600 - nightplan.MARGIN_S)) < 5, got
nightplan.window_end = lambda now, spec: None; got.clear()                                                  # outside: no deadline
assert locktest.start(["112@6400"], night=True); time.sleep(0.2); assert got[0][2] is None, got
nightplan.window_end = we0
locktest.lock_test = lt0
assert locktest.cut_night_on_traffic in zdev.minute_jobs()
assert not any(job.__name__ in ("maybe_night", "maybe_day", "remind") for job in zdev.minute_jobs())
stop.set()
print("OK test_night")
