# Shared setup of the NR lock-test scenarios (nr_locktest*.py, split 2026-11-18 so the parts run side by side): the mock ZTE,
# the injected cells, the antenna-mode quality table, the fake reboot -> boot inform loop. `from nr_locktest_env import *`.
import datetime, importlib.util, json, os, shutil, sys, tempfile, threading, time
here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scenarios"); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
PORT = int(os.environ.get("NRLT_PORT", "18131"))   # one mock ZTE (PORT) + API (PORT + 1) per part: the parts run side by side
sys.argv = ["x", str(PORT)]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = tempfile.mkdtemp(prefix="nrlt-")
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:%d" % PORT, ZTE_PASS_HASH=m.S(m.PW),
                  LOCKTEST_SETTLE_S="0", LOCKTEST_ONLINE_TIMEOUT_S="0", LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3", LOCKTEST_CLEANUP_S="0.05",
                  SWEEP_SETTLE_S="0", SWEEP_READS="5", SWEEP_GAP_S="0.03", SWEEP_DAYS="0", INTTEST_S="0.2", WAKE_S="0", CPE_READY_TRIES="1", CPE_READY_GAP_S="0")
os.environ.setdefault("LOCKTEST_IPERF", "0")   # the mock ZTE runs no iperf3 (a step would wait 160 s for it)
sys.path.insert(0, ROOT)
from keeper import acs, alert, api, brain, common, locktest, store
from keeper.devices import zte as zdev
from keeper.devices.zte import antennas
brain.load_registry(DATA)                        # the ranking (/anchors, auto-apply) needs the registry dir

ANCHOR = "340@1550"
# lock -> (lte_cell, nr_cell, 5G SINR, site, latency p95, jitter, loss %, call-ready)   injected samples = the measurement window
CELLS = {"none": ("1550/340", "629988/747", "6.0", "1072/3", "55", "6", "0", "1"),
         "534@636576": ("1550/340", "636576/534", "9.0", "1072/3", "80", "20", "2", "1"),      # best signal, worse internet
         "804@636576": ("100/340", "636576/804", "9.0", "1243/81", "40", "4", "0", "1"),      # a cell its own anchor serves (100/340)
         "747@629988": ("1550/340", "629988/747", "6.0", "1072/3", "40", "4", "0", "1")}      # worse signal, best internet
# web page: 5G SINR per antenna mode - dir/dir has the best signal ...
SINR = {("1", "1"): "4.0", ("4", "1"): "6.0", ("1", "4"): "8.0", ("4", "4"): "9.5"}
# ... and the internet test per mode: dir/omni is the best quality, dir/dir the worst (signal must not win)
NET = {("1", "1"): {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 40.0, "jitter_ms": 4.0, "down_mbps": 90.0},
       ("4", "1"): {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 25.0, "jitter_ms": 4.0, "down_mbps": 95.0},
       ("1", "4"): {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 42.0, "jitter_ms": 5.0, "down_mbps": 92.0},
       ("4", "4"): {"call_ok": 1, "loss_pct": 8.0, "lat_p95": 120.0, "jitter_ms": 30.0, "down_mbps": 20.0}}
base_fields = m.fields
def mode():
    a = {"5": "1", "6": "1"}
    for t, gid, name, state in m.st["sets"]:
        if gid == "BSP_ANTENNA_STATE_SET": a[name] = state
    return a["5"], a["6"]
CELL_OF = {"none": ("2eb", "629988"), "534@636576": ("216", "636576"), "804@636576": ("324", "636576"), "747@629988": ("2eb", "629988")}   # hex PCI, ARFCN
def fields():                                   # the fake ZTE page: the serving 5G cell follows the lock, the SINR the antenna mode
    f = base_fields(); f["Z5g_SINR"] = SINR[mode()]
    pci, ch = CELL_OF.get(locktest.lt["running"] or "none", ("216", "636576"))
    f["nr5g_pci"], f["nr5g_action_channel"] = pci, ch
    return f
m.fields = fields
real_net = brain.internet_test
def fake_net(seconds=30, url=None, mb=None, stop=None):     # the per-mode internet test (no real download in a test)
    return dict({"lat_p50": 30.0, "tested_mb": 2.0, "error": ""}, **NET[mode()])
brain.internet_test = fake_net
reports = []
alert.report = lambda text: reports.append(text) or True
def sets(gid=None):
    return [(s[1], s[3]) for s in m.st["sets"] if gid in (None, s[1])]
def bsp():
    return [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"]
def rows():
    return {r["lock"]: r for r in store.rows(DATA, "lock_tests")}

stop = threading.Event(); ANCHOR_FLIP = {"on": False}
def zte():                                      # reboot request -> boot inform; one sample every 0.2 s for the current lock
    seen = None; k = 0       # the last reboot entry itself, not a count: sets.clear() + one new reboot gave the same count (flaky, 2026-11-18)
    while not stop.is_set():
        last = next((s for s in reversed(m.st["sets"]) if s[1] == "REBOOT_DEVICE"), None)
        if last is not None and last is not seen: seen = last; time.sleep(0.3); acs.st["boots"] = acs.st.get("boots", 0) + 1
        cur = locktest.lt["running"]
        if cur in CELLS:
            lte, nr, sinr, site, lat, jit, loss, ok = CELLS[cur]; k += 1
            if ANCHOR_FLIP["on"] and k % 2: lte = "1550/341"          # an unstable anchor: the serving cell alternates
            brain.add({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "lte_cell": lte, "lte_site": site,
                       "lte_ca": "B3/1550/340:-90/10@20", "lte_rsrq": "-12", "nr_cell": nr, "nr_sinr": sinr, "nr_rsrp": "-85",
                       "call_ok": ok, "lat_p95": lat, "jitter_ms": jit, "loss_pct": loss, "down_bps": "0", "up_bps": "0", "uptime_s": "600"})
        time.sleep(0.2)
threading.Thread(target=zte, daemon=True).start()
phases = []                                      # every phase the test sets, in order (a phase can last milliseconds)
real_phase = locktest.phase
locktest.phase = lambda text, log_it=False: phases.append(text) or real_phase(text, log_it)
def align(headings):                             # answer "Aligned - measure now" for each step that waits
    def run():
        for h in headings:
            while not locktest.lt["waiting"] and not stop.is_set(): time.sleep(0.02)
            if stop.is_set(): return
            locktest.lt["heading"] = h; locktest.lt["go"].set()
            while locktest.lt["waiting"] and not stop.is_set(): time.sleep(0.02)
    th = threading.Thread(target=run, daemon=True); th.start(); return th

