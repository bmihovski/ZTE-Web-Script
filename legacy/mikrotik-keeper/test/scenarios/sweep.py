# antenna sweep: 4 modes measured on the (fake) ZTE web page, best kept per AUTO, browser session freed, results logged
import csv, importlib.util, json, os, shutil, sys
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18120"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-sweep"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:18120", ZTE_PASS_HASH=m.S(m.PW), SWEEP_SETTLE_S="0", SWEEP_READS="3", SWEEP_GAP_S="0.01", INTTEST_S="0.05", WAKE_S="0", ANT_SET_RETRY_S="0", CPE_READY_TRIES="1", CPE_READY_GAP_S="0")
sys.path.insert(0, ROOT)
from keeper import store
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks

SINR = {("1", "1"): "2.0", ("4", "1"): "3.5", ("1", "4"): "7.5", ("4", "4"): "9.0"}     # ant5 dir + ant6 dir best here
base = m.fields
def fields():
    f = base(); a = {"5": "1", "6": "1"}
    for t, gid, name, state in m.st["sets"]:
        if gid == "BSP_ANTENNA_STATE_SET": a[name] = state
    f["Z5g_SINR"] = SINR[(a["5"], a["6"])]; f["Z5g_rsrp"] = "-79"
    return f
m.fields = fields

# dryrun (default): measures all 4, recommends, puts the configured mode (ant5 omni / ant6 dir) back
v = antennas.antenna_sweep("manual"); print(v)
assert v.startswith("would switch (dryrun) to ant5 dir / ant6 dir on 636576/534: SINR 9.0 vs current ant5 omni / ant6 dir 7.5"), v
ant = [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"]
assert ant[:8] == [("5", "1"), ("6", "1"), ("5", "4"), ("6", "1"), ("5", "1"), ("6", "4"), ("5", "4"), ("6", "4")], ant
assert ant[-2:] == [("5", "1"), ("6", "4")] and m.st["sets"][-1][1] == "LOGOUT"                   # restored + browser freed
rows = store.rows(DATA, "antenna_sweeps"); assert len(rows) == 4 and rows[3]["sinr_p50"] == "9.0", rows
ev = store.rows(DATA, "events"); assert ev[-1]["event"] == "antenna sweep (manual)" and "ant5 omni / ant6 omni: 636576/534 SINR 2.0" in ev[-1]["why"]
assert "antennas" not in common.state()

# AUTO=on: the winner is kept and used for later boots
brain.AUTO = "on"; m.st["sets"].clear()
v = antennas.antenna_sweep("manual"); print(v)
assert v.startswith("switched to ant5 dir / ant6 dir") and antennas.ant_cfg() == {"auto": "0", "ant5": "4", "ant6": "4"}
ant = [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"]; assert ant[-2:] == [("5", "4"), ("6", "4")]
# next sweep: already the best -> kept, nothing changes
v = antennas.antenna_sweep("manual"); print(v); assert v.startswith("kept ant5 dir / ant6 dir"), v
# small gain (< 1 dB) -> keep the current mode
SINR[("4", "4")] = "7.9"; common.state({"antennas": {"auto": "0", "ant5": "1", "ant6": "4"}})
v = antennas.antenna_sweep("manual"); print(v); assert v.startswith("kept ant5 omni / ant6 dir"), v

# scheduler: due + idle + up > 15 min -> starts; not again right after
common.state({"last_sweep": 0})
assert antennas.maybe_sweep({"uptime_s": "600", "down_bps": "0", "up_bps": "0"}) is False         # ZTE up < 15 min
assert antennas.maybe_sweep({"uptime_s": "3600", "down_bps": "9000000", "up_bps": "0"}) is False  # busy
assert antennas.maybe_sweep({"uptime_s": "3600", "down_bps": "0", "up_bps": "0"}) is True
assert antennas.maybe_sweep({"uptime_s": "3600", "down_bps": "0", "up_bps": "0"}) is False
# web login taken over (no readings) -> aborted after the first mode, configured mode put back, nothing recorded
import time
while antennas.sweep_st["running"]: time.sleep(0.05)                              # the scheduled sweep above runs in the background
n = len(store.rows(DATA, "antenna_sweeps")); good = m.fields; m.fields = lambda: {}; m.st["sets"].clear()
common.state({"antennas": {"auto": "0", "ant5": "4", "ant6": "4"}})
v = antennas.antenna_sweep("manual"); print(v); m.fields = good
assert v.startswith("aborted: the ZTE web login was taken over during ant5 omni / ant6 omni (no readings)"), v
ant = [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"]; assert ant == [("5", "1"), ("6", "1"), ("5", "4"), ("6", "4")], ant
assert len(store.rows(DATA, "antenna_sweeps")) == n and not antennas.sweep_st["running"]
# POST /antennas: keep a mode from the panel (userscript sweep winner) or go back to the env
import http.client, threading as th
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 18121), api.H); th.Thread(target=srv.serve_forever, daemon=True).start()
def post(path):
    c = http.client.HTTPConnection("127.0.0.1", 18121); c.request("POST", path, b""); r = c.getresponse(); return r.status, json.loads(r.read())
assert post("/antennas?auto=0&ant5=1&ant6=4") == (200, {"antennas": {"auto": "0", "ant5": "1", "ant6": "4"}}) and antennas.ant_cfg()["ant5"] == "1"
assert post("/antennas?auto=0&ant5=9&ant6=4")[0] == 400 and antennas.ant_cfg()["ant5"] == "1"
assert post("/antennas?none")[1]["antennas"] == {"auto": antennas.AUTO, "ant5": antennas.ANT5, "ant6": antennas.ANT6}
srv.shutdown()

# sweep_for_test (the NR lock test's sweep): the mode is chosen by INTERNET QUALITY first (signal only breaks a tie), a clear margin
# is needed to replace the current mode, the chosen mode is left set for the step, and the kept (boot) mode is never changed
SINR[("4", "4")] = "9.0"
NET = {("1", "1"): {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 40.0, "jitter_ms": 4.0, "down_mbps": 90.0},
       ("4", "1"): {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 25.0, "jitter_ms": 4.0, "down_mbps": 95.0},    # best internet, weak signal
       ("1", "4"): {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 42.0, "jitter_ms": 5.0, "down_mbps": 92.0},    # the env (current) mode
       ("4", "4"): {"call_ok": 1, "loss_pct": 8.0, "lat_p95": 120.0, "jitter_ms": 30.0, "down_mbps": 20.0}}  # best signal, worst internet
def cur_mode():
    a = {"5": "1", "6": "1"}
    for t, gid, name, state in m.st["sets"]:
        if gid == "BSP_ANTENNA_STATE_SET": a[name] = state
    return a["5"], a["6"]
real_net = brain.internet_test
brain.internet_test = lambda seconds=30, url=None, mb=None, stop=None: dict({"lat_p50": 30.0, "tested_mb": 2.0, "error": ""}, **NET[cur_mode()])
try:
    # the policy: call-ready, loss, latency p95, jitter first; SINR/RSRP only as a tie-breaker
    q = brain.quality_values
    assert q(100, 0, 45, 5, 3, -90) > q(100, 0, 80, 5, 12, -70)          # a worse signal but a better latency wins
    assert q(100, 0, 45, 5, 3, -90) > q(99, 0, 45, 5, 9, -80)            # call-ready comes first
    assert q(0, 0, 45, 5, 9, -80) < q(100, 30, 500, 90, -5, -120)        # not call-ready = worst, even with the best signal
    assert q(None, None, None, None, 9, -80) < q(0, 99, 999, 99, -9, -140)   # no internet data = worst
    cb = brain.clearly_better
    assert cb(q(100, 0, 25, 5, 3, -90), q(100, 0, 42, 5, 9, -70))        # 17 ms better p95 = a clear win
    assert not cb(q(100, 0, 38, 5, 5, -70), q(100, 0, 42, 5, 9, -70))    # 4 ms better p95 is inside the margin: no switch
    assert cb(q(100, 0, 42, 5, 9, -70), q(100, 0, 42, 5, 5, -80))        # quality equal: the better signal breaks the tie
    assert not cb(q(100, 0, 42, 5, 9, -70), q(100, 0, 42, 5, 9, -70))    # identical: no clear win

    # the test sweep applies the quality winner (dir/omni, p95 25 ms) - not the signal winner (dir/dir, 9.5 dB) - for the step
    common.state({"antennas": None}); m.st["sets"].clear()
    before = len(store.rows(DATA, "antenna_sweeps"))
    r = antennas.sweep_for_test("636576/534", trigger="lock test 534@636576"); print(r)
    assert (r["ant5"], r["ant6"]) == ("4", "1") and r["mode"] == "ant5 dir / ant6 omni" and r["switched"], r
    assert r["sinr"] == 3.5 and r["call_ok"] == 1 and r["loss_pct"] == 0.0 and r["lat_p95"] == 25.0 and r["down_mbps"] == 95.0, r
    assert r["quality_mode"] == "ant5 dir / ant6 omni" and not r["aborted"], r
    assert [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"][-2:] == [("5", "4"), ("6", "1")]
    rows = store.rows(DATA, "antenna_sweeps"); assert len(rows) == before + 4 and rows[-1]["trigger"] == "lock test 534@636576"
    worst = [x for x in rows if x["mode"] == "ant5 dir / ant6 dir"][-1]
    assert worst["sinr_p50"] == "9.0" and worst["loss_pct"] == "8.0" and worst["lat_p95"] == "120.0" and worst["call_ok"] == "1", worst
    chosen = [x for x in rows if x["mode"] == "ant5 dir / ant6 omni" and x["trigger"] == "lock test 534@636576"][-1]
    assert chosen["lat_p95"] == "25.0" and chosen["jitter_ms"] == "4.0" and chosen["down_mbps"] == "95.0", chosen
    assert common.state().get("antennas") is None                                   # the kept (boot) mode is untouched

    # a mode that is not call-ready never wins, even with the best signal
    NET[("4", "4")] = {"call_ok": 0, "loss_pct": 25.0, "lat_p95": 400.0, "jitter_ms": 90.0, "down_mbps": 1.0}
    r = antennas.sweep_for_test("636576/534"); assert (r["ant5"], r["ant6"]) == ("4", "1"), r
    # the current mode is already the quality winner: no switch
    common.state({"antennas": {"auto": "0", "ant5": "4", "ant6": "1"}}); m.st["sets"].clear()
    r = antennas.sweep_for_test("636576/534"); assert (r["ant5"], r["ant6"]) == ("4", "1") and not r["switched"], r
    assert [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"][-2:] == [("5", "4"), ("6", "1")]
    # an LTE anchor switch during a mode's window invalidates that mode; if every mode switches the sweep is aborted
    page = m.fields; m.st["sets"].clear(); cnt = {"n": 0}
    def lte_switch():
        f = page(); cnt["n"] += 1; f["lte_pci"] = "154" if cnt["n"] % 2 else "155"; return f
    m.fields = lte_switch; common.state({"antennas": None})
    r = antennas.sweep_for_test("636576/534"); m.fields = page; print(r)
    assert r["aborted"].startswith("unstable LTE anchor") and r["mode"] == "", r
    sw = store.rows(DATA, "antenna_sweeps")[-4:]
    assert all("LTE anchor switched" in x["note"] and int(x["lte_switches"]) >= 1 for x in sw), sw
    assert not common.state().get("antennas")
    # antenna config retries: a transient failure (browser page took the web session) is retried twice, then the exact goforms
    class WStub:
        def __init__(self, fail): self.n = 0; self.fail = fail
        def set_ant(self, auto, a5, a6): self.n += 1; return ["BSP_ANTENNA_STATE_SET"] if self.n <= self.fail else []
        def login(self): return None
    w = WStub(2); assert antennas._set_mode(w, "4", "1") == "" and w.n == 3
    w = WStub(9); assert antennas._set_mode(w, "4", "1") == ["BSP_ANTENNA_STATE_SET"] and w.n == 3

    # 5G cell switching in a mode: >= 3 changes there make that mode unstable; if every mode switches, the sweep is skipped
    page = m.fields; cnt = {"n": 0}
    def flip():
        f = page(); cnt["n"] += 1; f["nr5g_pci"] = "216" if cnt["n"] % 2 else "7f"      # 534 / 127 on 636576
        return f
    m.fields = flip; common.state({"antennas": None}); m.st["sets"].clear()
    r = antennas.sweep_for_test("636576/534"); m.fields = page; print(r)
    assert r["aborted"].startswith("5G cell switching") and r["skip"] and r["no_cell"] is False, r
    last = store.rows(DATA, "antenna_sweeps")[-1]
    assert int(last["nr_switches"]) >= 3 and "5G cell switched" in last["note"], last
    # baseline (expected=None): the cell most modes saw decides; only modes that saw it count
    common.state({"antennas": None}); NET[("4", "4")] = {"call_ok": 1, "loss_pct": 8.0, "lat_p95": 120.0, "jitter_ms": 30.0, "down_mbps": 20.0}
    r = antennas.sweep_for_test(None, trigger="lock test none")
    assert (r["ant5"], r["ant6"]) == ("4", "1") and r["cell"] == "636576/534", r
    # the sweep only sees another 5G cell -> no winner for the locked one, the configured mode is put back, nothing is claimed
    good = m.fields; m.st["sets"].clear()
    m.fields = lambda: dict(good(), nr5g_pci="157", nr5g_action_channel="629988")     # 0x157 = 343, another n78 block
    r = antennas.sweep_for_test("636576/534"); m.fields = good; print(r)
    assert r["aborted"] == "no 5G on 636576/534" and r["mode"] == "" and r["no_cell"] is True and r["skip"] == "no 5G on 636576/534", r
    assert "target NR cell 636576/534 not seen" in store.rows(DATA, "antenna_sweeps")[-1]["note"]
    assert [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"][-2:] == [("5", "1"), ("6", "4")]
finally:
    brain.internet_test = real_net
print("OK test_sweep")
