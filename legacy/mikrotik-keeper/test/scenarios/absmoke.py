# A/B smoke (2026-11-21): the full five-switch test with locks - A high -> B high -> no-lock -> A low -> B low, the original back,
# the per-leg internet and the modem good / provider issue / modem bad verdict. The device writes are mocked here; the legs/order/
# restore/Telegram are what this scenario pins.
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA, DEVICE="zte", ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import absmoke, acs, alert, brain, captest, common, devices, guard, store
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

# The antenna trial uses the same restore scope but holds NR automatic on both LTE profiles.
original = {"raw": {"lte": "", "nr": ""}}
control = {"auto": "0", "ant5": "4", "ant6": "4"}
restores = []
z.profile = types.SimpleNamespace(LTE="lte", NR="nr", low_band=lambda lte: None,
    spec=lambda raw: raw or "none", finish_scope=lambda: restores.append(original))
z.antennas = types.SimpleNamespace(effective=lambda: (control, "kept", None))
def begin_scope(owner, antennas, web_notice=None):
    assert owner == "smoke" and antennas == control
    return original
z.profile.begin_scope = begin_scope
captest.sqm.URL = "mock"
matrix = []
def antenna_profile(name, lte, stop, reverse=False, nr="none"):
    assert not stop.is_set()
    matrix.append((name, lte, reverse))
    modes = [{"ant5": a5, "ant6": a6, "call_ok_pct": 100, "config_verified": True}
             for a5, a6 in [("1", "1"), ("4", "1"), ("1", "4"), ("4", "4")]]
    return {"leg": name, "lte": lte, "nr": nr, "modes": modes[::-1] if reverse else modes}
absmoke._antenna_profile = antenna_profile
calls.clear()
assert absmoke.run("test antenna", notify=False, antenna_lte="434@1550")[0]
deadline = time.monotonic() + 5
while absmoke.st["running"] and time.monotonic() < deadline: time.sleep(0.01)
res = common.state().get("antenna_ab")
assert res and len(res["legs"]) == 2 and all(len(x["modes"]) == 4 for x in res["legs"]), res
assert matrix == [("no lock", "none", False), ("LTE lock", "434@1550", True)], matrix
assert restores == [original] and res["restored"] == {"lte": "none", "nr": "none"}, res
assert res["verdict"] == "measured" and calls == [("434@1550", "none")]
assert absmoke.status()["antenna_trial"]["started"] == res["started"]

# A loaded sweep of the current profile compares only antenna settings.
original["raw"].update(lte="434@1550", nr="534@636576")
previous = common.state()["antenna_ab"]
calls.clear()
matrix.clear()
rep.clear()
alert.WEB_CLOSE_S = 0
assert absmoke.run("antenna trial", antenna_lte="current", ui=True)[0]
deadline = time.monotonic() + 5
while absmoke.st["running"] and time.monotonic() < deadline: time.sleep(0.01)
sweep = absmoke.status()["antenna_sweep"]
assert not calls and len(matrix) == 1 and matrix[0][1] == "434@1550", (calls, matrix)
assert sweep["verdict"] == "measured" and len(sweep["legs"][0]["modes"]) == 4, sweep
assert sweep["legs"][0]["nr"] == "534@636576" and sweep["restored"] == {"lte": "434@1550", "nr": "534@636576"}
assert common.state()["antenna_ab"] == previous and restores == [original, original]
assert len(rep) == 2 and "Close" in rep[0] and "Android" in rep[0], rep
assert "You can reopen" in rep[1] and "Loaded n78 sweep finished" in rep[1], rep

# Real sample validation, including a serving cell change during an otherwise valid LTE-lock measurement.
from keeper.devices.zte import profile
raw = {profile.BAND: "1,3,7", profile.LTE: "434,1550", profile.NR: ""}
def measure(secs, load_windows, server):
    assert secs >= 2 * acs.FAST_INTERVAL + 5 and server == "primary"
    load_windows.extend([(100, 125), (130, 155)])
    for t, cell, sinr in [(101, "636576/534", 4), (106, "636576/534", 2), (116, "636576/534", 3), (132, "636576/786", -6)]:
        profile.radio.append(dict(raw, time=t, boots=acs.st["boots"], lte_cell="1550/434", nr_cell=cell,
                                  nr_sinr=sinr, nr_rsrp=-86, nr_rsrq=-13))
    for t in (101, 106, 132, 138):
        brain.lat_buf.append((t, 20, "fixture"))
    return 120, 24, "down primary, up primary"
captest.measure = measure
captest.wait_lan_quiet = lambda: True
result = absmoke._antenna_load(profile, raw, "434@1550", ("1", "4"), "primary")
assert result["config_verified"] and not result["radio"]["verified"] and result["nr_switches"] == 1, result
assert result["call_ok_pct"] == 100 and result["down_mbps"] == 120 and result["n78_samples"] == 4, result
assert result["nr_signal"]["636576/534"] == {"samples": 3, "nr_sinr": 3, "nr_rsrp": -86, "nr_rsrq": -13}, result

# Traffic after a completed download must discard its window before retrying the same mode.
def interrupted(**opts):
    opts["load_windows"].append((5, 30))
    captest.measure = measure
    raise captest.LocalTraffic("local traffic during upload")
profile.radio.clear()
brain.lat_buf.clear()
captest.measure = interrupted
result = absmoke._antenna_load(profile, raw, "434@1550", ("1", "4"), "primary")
assert result["transfer_windows"] == [(100, 125), (130, 155)], result
assert result["load_retries"] == ["local traffic during upload"] and result["config_verified"], result

# Old stored runs expose a loaded score for complete, radio-verified windows after restart.
stable = dict(result, radio=dict(result["radio"], verified=True), nr_signal={"636576/534": {}})
failed = {"ant5": "4", "ant6": "4", "error": "local traffic"}
common.state({"antenna_ab": {"legs": [{"leg": "LTE lock", "modes": [stable, result, failed]}]}})
rows = absmoke.status()["antenna_trial"]["legs"][0]["modes"]
assert rows[0]["score"] == 88.0, rows[0]
assert rows[1]["score"] is None and rows[2]["score"] is None, rows
assert "score" not in common.state()["antenna_ab"]["legs"][0]["modes"][0]

# Store the good direction and its observed cell even when the other transfer failed.
def down_only(secs, load_windows, server):
    load_windows.append((100, 125))
    return 120, None, "down primary, up primary failed: no speed (Status=ServerBusy)"
captest.measure = down_only
partial = absmoke._antenna_load(profile, raw, "434@1550", ("1", "4"), "primary")
assert partial.get("down_mbps") == 120 and partial.get("up_mbps") is None, partial
assert "ServerBusy" in partial["error"] and "636576/534" in partial["nr_signal"], partial
assert not partial["radio"]["verified"] and not partial["config_verified"] and "call_ok_pct" not in partial, partial
print("OK test_absmoke")
