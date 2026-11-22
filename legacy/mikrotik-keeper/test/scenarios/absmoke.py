# A/B smoke (2026-11-21): the full five-switch test with locks - A high -> B high -> no-lock -> A low -> B low, the original back,
# the per-leg internet and the modem good / provider issue / modem bad verdict. The device writes are mocked here; the legs/order/
# restore/Telegram are what this scenario pins.
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA, DEVICE="zte", ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import absmoke, acs, alert, brain, common, devices, guard, store
import threading
ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
store.add(DATA, "band_scans", {"time": ts, "band": "8", "serving": "3774/394", "nr_cell": "156510/534", "nr_arfcn": "156510",
                               "call_ok": "100.0", "loss_pct": "0.0", "lat_p95": "60.3", "jitter_ms": "21.4", "down_mbps": "11.2", "note": ""})
store.add(DATA, "band_scans", {"time": ts, "band": "20", "serving": "6400/112", "nr_cell": "156510/534", "nr_arfcn": "156510",
                               "call_ok": "100.0", "loss_pct": "0.0", "lat_p95": "68.4", "jitter_ms": "28.3", "down_mbps": "8.31", "note": ""})
common.state({"lte_lock": {"cell": "340@100", "state": "active"}, "nr_lock": {"cell": "534@636576", "state": "active"}, "dev": False})
# This is the device-neutral smoke orchestration fixture. low_profile exercises the ZTE journal.
import types
z = types.SimpleNamespace(LOCKS_REBOOT=True)
absmoke.devices = types.SimpleNamespace(current=lambda: z)
calls = []
def apply(l, n):
    calls.append((l, n))
    acs.st["boots"] += 1
    brain.HIST.append({"time": common.stamp(), "lte_cell": brain.anchor_key(l), "nr_cell": brain.anchor_key(n)})
    return "tr069"

brain.HIST.append({"time": common.stamp(), "lte_cell": "100/340", "nr_cell": "636576/534"})
z.locks_apply = apply
z.ready = lambda **kw: True
brain.wait_online = lambda **kw: True
# the pair database in its REAL key format (earfcn/pci: "1550/66"), not the lock spec ("66@1550"): the A/B high leg must get
# the anchor's measured 5G pair, never the pre-run sample's cell (the fallback here is the n28 cell 534@156510)
brain.REG["nr"] = {"636576/534": {"anchors": {"1550/66": {"min": 100}, "100/340": {"min": 100}}},
                   "156510/534": {"anchors": {"3774/394": {"min": 100}}}}
brain.pair_quality = lambda p, a: 1
assert absmoke._nr_for("340@100", {"nr_cell": "156510/534"}) == "534@636576", absmoke._nr_for("340@100", {"nr_cell": "156510/534"})
assert absmoke._nr_for("66@1550", {"nr_cell": "156510/534"}) == "534@636576"
assert absmoke._nr_for("999@999", {"nr_cell": "156510/534"}) == "none"             # no pair history on this anchor
absmoke.abplan.high = lambda: ["340@100", "66@1550"]
absmoke.abplan.low = lambda: [("394@3774", "534@156510"), ("112@6400", "534@156510")]
preview = absmoke.status()
assert preview["active"] == {"profile": "Current lock", "lte_lock": "340@100", "nr_lock": "534@636576",
                             "lte": "340@100", "nr": "534@636576"}, preview
assert [(x["leg"], x["lte"], x["nr"]) for x in preview["plan"]] == [
    ("current lock", "340@100", "534@636576"), ("A high", "66@1550", "534@636576"),
    ("no lock", "none", "none"), ("A low", "394@3774", "534@156510"), ("B low", "112@6400", "534@156510")]
assert not calls and not preview["running"]
seq = [dict(down_mbps=30, call_ok=1, loss_pct=0, lat_p95=20, jitter_ms=3),   # A high: p95 20 beats the no-lock 30
       dict(down_mbps=20, call_ok=1, loss_pct=0, lat_p95=30, jitter_ms=5),
       dict(down_mbps=25, call_ok=1, loss_pct=0, lat_p95=30, jitter_ms=5),   # no lock
       dict(down_mbps=10, call_ok=0, loss_pct=1, lat_p95=40, jitter_ms=8),
       dict(down_mbps=8, call_ok=0, loss_pct=1, lat_p95=40, jitter_ms=8)]
brain.internet_test = lambda **kw: seq.pop(0)
brain.row_health = lambda row: {"sim": True, "registered": True, "signal": True, "data": True}
rep = []
def _rep(m):                       # a slow Telegram send: with `running` cleared before the result/Telegram, a waiter would see a
    time.sleep(0.3); rep.append(m) # done run with an empty rep (the 2026-11-22 docker race this pins)
alert.report = _rep
ok, why = absmoke.run("test"); assert ok, why
deadline = time.time() + 10
while absmoke.st["running"] and time.time() < deadline: time.sleep(0.05)
assert absmoke.st["running"] is None, absmoke.st
res = absmoke.st["result"]; print(res)
legs = [x["leg"] for x in res["legs"]]
assert legs == ["current lock", "A high", "no lock", "A low", "B low"], legs
assert calls == [("66@1550", "534@636576"), ("none", "none"),
                 ("394@3774", "534@156510"), ("112@6400", "534@156510"), ("340@100", "534@636576")], calls   # A = the max scored, B = the next; best found the A high
assert res["verdict"] == "uplink reachable" and res["any_internet"], res
assert rep and "uplink reachable" in rep[0], rep
assert res["restored"] == {"lte": "340@100", "nr": "534@636576"}, res
assert (common.state().get("lte_lock") or {}).get("cell") == "340@100"
# Stop during a measurement, without starting another leg or keeping a partial winner.
entered = threading.Event()

def wait_for_stop(**kwargs):
    if not calls:
        return dict(down_mbps=30, call_ok=1, loss_pct=0)
    entered.set()
    assert kwargs["stop"].wait(5)
    return {}

calls.clear()
brain.internet_test = wait_for_stop
assert absmoke.run("cancel test")[0]
assert entered.wait(5)
p = absmoke.status()["progress"]
assert p["phase"] == "measuring" and p["step"] == 2 and p["total"] == 5, p
active = absmoke.status()
assert active["active"]["profile"] == "Smoke in progress" and active["active"]["lte"] == "66@1550", active
assert active["plan"] == preview["plan"], active
assert absmoke.stop()
deadline = time.monotonic() + 10
while absmoke.status()["running"] and time.monotonic() < deadline: time.sleep(0.05)
res2 = absmoke.status()["result"]
assert res2["verdict"] == "stopped" and res2["restored"] == {"lte": "340@100", "nr": "534@636576"}, res2
assert calls == [("66@1550", "534@636576"), ("340@100", "534@636576")], calls
assert not common.state().get("ab_smoke_restore")
assert not absmoke.stop()
assert absmoke._classify([{"ok": False, "error": "no boot inform"}])["verdict"] == "inconclusive"
assert absmoke._classify([{"ok": False, "measured": True, "health": {"sim": True, "registered": True}, "leg": "no lock"}])["verdict"] == "mobile uplink suspected"
assert absmoke._classify([{"ok": True, "leg": "no lock"}, {"ok": False, "measured": True, "leg": "current lock"}])["verdict"] == "configuration suspected"
print("OK test_absmoke")
