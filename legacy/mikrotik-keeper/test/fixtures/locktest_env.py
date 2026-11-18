# Shared setup of the LTE lock-test scenarios (locktest*.py, split 2026-11-18 so the parts run side by side): the mock ZTE,
# the injected cells, the fake reboot -> boot inform loop (`zte`, started by each section). `from locktest_env import *`.
import csv, importlib.util, os, shutil, sys, tempfile, threading, time, datetime
here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scenarios"); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
PORT = int(os.environ.get("LT_PORT", "18130"))   # one mock ZTE per part: the parts run side by side
sys.argv = ["x", str(PORT)]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = tempfile.mkdtemp(prefix="lt-")
os.environ.update(NIGHT_SCREEN="off", LOCK_VIA="web", DATA=DATA, ZTE_HOST="127.0.0.1:%d" % PORT, ZTE_PASS_HASH=m.S(m.PW), LOCKTEST_SETTLE_S="0", LOCKTEST_ONLINE_TIMEOUT_S="0", LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3")
os.environ.setdefault("LOCKTEST_IPERF", "0")   # the mock ZTE runs no iperf3 (a step would wait 160 s for it)
sys.path.insert(0, ROOT)
from keeper import store
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks
CELLS = {"none": ("3350/340", "B7/3350/340:-92/3@20 B3/1550/340:-93/8@20", "636576/786", "-1", "1243/61"),
         "434@1550": ("1550/434", "B3/1550/434:-90/10@20 B1/100/340:-96/8@20", "636576/534", "8", "1072/3"),
         "340@1550": ("1550/340", "B3/1550/340:-91/8@20 B7/3350/340:-92/3@20", "636576/534", "7", "1243/1")}
stop = threading.Event()
def zte():                     # fake ZTE: a reboot request -> boot inform; then one sample every 0.2 s for the current lock
    seen = None              # the last reboot entry itself, not a count: sets.clear() + one new reboot gave the same count (flaky, 2026-11-18)
    while not stop.is_set():
        last = next((s for s in reversed(m.st["sets"]) if s[1] == "REBOOT_DEVICE"), None)
        if last is not None and last is not seen: seen = last; time.sleep(0.05); acs.st["boots"] = acs.st.get("boots", 0) + 1
        cur = locktest.lt["running"]
        if cur in CELLS:
            lte, ca, nr, sinr, site = CELLS[cur]
            brain.add({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "lte_cell": lte, "lte_site": site, "lte_ca": ca, "lte_rsrq": "-12",
                         "nr_cell": nr, "nr_sinr": sinr, "call_ok": "1", "lat_p95": "50", "jitter_ms": "5", "loss_pct": "0"})
        time.sleep(0.2)
