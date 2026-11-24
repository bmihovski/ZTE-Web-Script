# The lock test's measurement strategy (2026-11-22v): settle for the CA -> short download to raise the NSA 5G leg -> the full
# down/up WITH the probe measuring under load -> the score reads that loaded window (call-ready / p95 / jitter / loss), not the
# idle minutes. The idle remainder is only the anchor-stability evidence. This is the low-band trap: B8/B20 read perfect idle
# quality (p95 ~40, call 100) while the loaded leg is p95 139 / jitter 69 / call 0 (2026-11-21).
import os, sys, time
os.environ["LOCKTEST_IPERF"] = "1"          # the production default; the mock ZTE runs no iperf3, so measure() is mocked below
os.environ["LT_PORT"] = "18136"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"))
from locktest_env import *  # noqa: F401,F403 - the shared setup (mock ZTE, cells, zte loop)
threading.Thread(target=zte, daemon=True).start()

order = []
def raise_traffic(seconds=0, **kw):
    order.append("raise"); return {"down_mbps": 2.0, "call_ok": 1, "loss_pct": 0, "lat_p95": 40, "jitter_ms": 5}
def measure(secs=None, load_windows=None):
    order.append("measure"); time.sleep(0.35)   # long enough for a monitor sample in the load window
    missing_upload_probes = order.count("measure") == 3
    now = time.time()
    brain.lat_buf.clear()
    for offset in range(-81, -1):
        if not (-71 <= offset < -61 or -11 <= offset < -1):
            brain.lat_buf.append((now + offset, 20))  # healthy setup/polling gap; outside both transfers
    starts = (now - 71,) if missing_upload_probes else (now - 71, now - 11)
    for start in starts:
        for i, v in enumerate((150, 210, 150, 210, None)):
            brain.lat_buf.append((start + 0.5 + 2 * i, v))
    if load_windows is not None:
        load_windows.extend([(now - 71, now - 61), (now - 11, now - 1)])
    return 12.0, 6.0, "mock loaded"
brain.internet_test = raise_traffic
from keeper import captest
captest.measure = measure

locktest.lock_test(["none", "434@1550", "340@1550"])
stop.set()
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
r = rows["434@1550"]; print(r)
assert order == ["raise", "measure"] * 3, order
assert r["down_mbps"] == "12.0" and r["up_mbps"] == "6.0", r
assert r.get("load_method") == "iperf_phases" and float(r["load_s"]) == 20, r
# the row's quality is the LOADED window, not the idle samples (the fixture's idle rows are p95 50 / jitter 5 / call 1)
assert float(r["lat_p95"]) == 210 and float(r["jitter_ms"]) == 60 and float(r["loss_pct"]) == 20, r
assert float(r["call_ok_pct"]) == 0.0, r
assert r["nr_iperf"] and r["nr_iperf"].startswith("636576/534"), r      # the 5G cell the load raised
assert rows["none"]["load_s"] and float(rows["none"]["lat_p95"]) >= 150, rows["none"]    # the baseline is loaded too
sc = brain.lt_score(r)                                         # the score itself reads the loaded row
assert sc["score"] is not None and sc["score"] < 30, sc        # a bad loaded link is a bad score (not the idle ~90)
assert sc["parts"]["call"] == 0 and sc["parts"]["p95"] == 0, sc
missing = rows["340@1550"]
assert missing["call_ok_pct"] == "" and missing["lat_p95"] == "", missing
assert "incomplete down/up load probes" in missing["note"], missing
assert brain.loaded_anchor_result(rows.values())[0]["lock"] == "434@1550"

# one loaded run is noise: a 25 s window has two 10 s probe slices, so one slice = 25 call-ready points = 5 score points = the
# promotion gain (2026-11-24). Auto-apply needs the same winner with the gain in the loaded run before.
def run_rows(run, cand_call):
    for lock, call in (("none", "75.0"), ("434@1550", cand_call)):
        store.add(DATA, "lock_tests", {"time": run, "run": run, "rat": "lte", "lock": lock, "anchor": "" if lock == "none" else "1550/434",
            "minutes": "12", "on_target_pct": "100.0", "call_ok_pct": call, "loss_pct": "0", "lat_p95": "60", "jitter_ms": "10",
            "down_mbps": "150", "up_mbps": "28", "load_s": "50", "load_method": brain.LTE_LOAD_METHOD, "nr_arfcn": "636576", "mode": "as is"})
brain.reg_st["dir"] = DATA
run_rows("2020-01-01 10:00:00", "100.0")
assert brain.loaded_anchor_result(r for r in store.rows(DATA, "lock_tests") if r.get("run") == "2020-01-01 10:00:00")[2]   # +5.0: one slice wins
assert not brain.loaded_win_repeated("434@1550", "2020-01-01 10:00:00")                     # ... but no run before it
run_rows("2020-01-01 11:00:00", "100.0")
assert brain.loaded_win_repeated("434@1550", "2020-01-01 11:00:00") and not brain.loaded_win_repeated("340@1550", "2020-01-01 11:00:00")
run_rows("2020-01-01 12:00:00", "75.0")
assert not brain.loaded_win_repeated("434@1550", "2020-01-01 13:00:00")                     # the run before was a tie


# the CA wait: a single carrier is not the CA (note, no error); two carriers is
brain.HIST.clear(); locktest.LT_CA_WAIT_S = 0.2
brain.add({"lte_ca": "B3/1550/434:-90/10@20"})
t0 = time.time(); assert locktest._wait_ca() is False and time.time() - t0 < 4
brain.add({"lte_ca": "B3/1550/434:-90/10@20 B1/100/340:-96/8@20"})
assert locktest._wait_ca() is True
print("OK test_locktest_load")
