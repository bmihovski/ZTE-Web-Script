# A/B smoke (2026-11-21): the full five-switch test with locks - A high -> B high -> no-lock -> A low -> B low, the original back,
# the per-leg internet and the modem good / provider issue / modem bad verdict. The device writes are mocked here; the legs/order/
# restore/Telegram are what this scenario pins.
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA, DEVICE="zte", ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import absmoke, alert, brain, common, devices, guard, store
ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
store.add(DATA, "band_scans", {"time": ts, "band": "8", "serving": "3774/394", "nr_cell": "156510/534", "nr_arfcn": "156510",
                               "call_ok": "100.0", "loss_pct": "0.0", "lat_p95": "60.3", "jitter_ms": "21.4", "down_mbps": "11.2", "note": ""})
store.add(DATA, "band_scans", {"time": ts, "band": "20", "serving": "6400/112", "nr_cell": "156510/534", "nr_arfcn": "156510",
                               "call_ok": "100.0", "loss_pct": "0.0", "lat_p95": "68.4", "jitter_ms": "28.3", "down_mbps": "8.31", "note": ""})
common.state({"lte_lock": {"cell": "340@100", "state": "released"}, "nr_lock": {"cell": "534@636576", "state": "released"}, "dev": False})
z = devices.current(); calls = []
z.locks_apply = lambda l, n: (calls.append((l, n)), "tr069")[1]
z.ready = lambda timeout=0: True
brain.wait_online = lambda timeout=0: True
brain.REG["nr"] = {"636576/534": {"anchors": {"1550@434": {"time": ts}}}}
brain.pair_quality = lambda p, a: 1
guard.ab_alt = lambda cur, row, now: "1550@434"
seq = [dict(down_mbps=30, call_ok=1, loss_pct=0, lat_p95=20, jitter_ms=3),   # A high: p95 20 beats the no-lock 30
       dict(down_mbps=20, call_ok=1, loss_pct=0, lat_p95=30, jitter_ms=5),
       dict(down_mbps=25, call_ok=1, loss_pct=0, lat_p95=30, jitter_ms=5),   # no lock
       dict(down_mbps=10, call_ok=0, loss_pct=1, lat_p95=40, jitter_ms=8),
       dict(down_mbps=8, call_ok=0, loss_pct=1, lat_p95=40, jitter_ms=8)]
brain.internet_test = lambda seconds=25: seq.pop(0)
brain.row_health = lambda row: {"sim": True, "registered": True, "signal": True, "data": True}
rep = []; alert.report = lambda m: rep.append(m)
ok, why = absmoke.run("test"); assert ok, why
deadline = time.time() + 10
while absmoke.st["running"] and time.time() < deadline: time.sleep(0.05)
assert absmoke.st["running"] is None, absmoke.st
res = absmoke.st["result"]; print(res)
legs = [x["leg"] for x in res["legs"]]
assert legs == ["A high", "B high", "no lock", "A low", "B low"], legs
assert calls == [("340@100", "534@636576"), ("1550@434", "534@636576"), ("none", "none"),
                 ("394@3774", "534@156510"), ("112@6400", "534@156510"), ("340@100", "534@636576")], calls   # the best found (A high 30 > no-lock 25)
assert res["verdict"] == "modem good" and res["any_internet"], res
assert rep and "modem good" in rep[0], rep
assert res["restored"] == {"lte": "340@100", "nr": "534@636576"}, res
assert (common.state().get("lte_lock") or {}).get("cell") == "340@100"
# the no-lock wins: the best is the no-lock itself -> restore the no-lock
calls.clear(); seq[:] = [dict(down_mbps=5, call_ok=0, loss_pct=2, lat_p95=40, jitter_ms=10),
                          dict(down_mbps=4, call_ok=0, loss_pct=2, lat_p95=40, jitter_ms=10),
                          dict(down_mbps=50, call_ok=1, loss_pct=0, lat_p95=10, jitter_ms=2),   # the no-lock is the best
                          dict(down_mbps=3, call_ok=0, loss_pct=2, lat_p95=40, jitter_ms=10),
                          dict(down_mbps=2, call_ok=0, loss_pct=2, lat_p95=40, jitter_ms=10)]
ok, why = absmoke.run("test2"); assert ok, why
deadline = time.time() + 10
while absmoke.st["running"] and time.time() < deadline: time.sleep(0.05)
res2 = absmoke.st["result"]
assert calls[-1] == ("none", "none"), calls
assert res2["restored"] == {"lte": "none", "nr": "none"}, res2
assert common.state().get("lte_lock") is None and common.state().get("nr_lock") is None
assert res2["verdict"] == "modem good"
print("OK absmoke")
