# antenna sweep: 4 modes measured on the (fake) ZTE web page, best kept per AUTO, browser session freed, results logged
import csv, importlib.util, json, os, shutil, sys
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18120"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-sweep"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:18120", ZTE_PASS_HASH=m.S(m.PW), SWEEP_SETTLE_S="0", SWEEP_READS="3", SWEEP_GAP_S="0.01")
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
print("OK test_sweep")
