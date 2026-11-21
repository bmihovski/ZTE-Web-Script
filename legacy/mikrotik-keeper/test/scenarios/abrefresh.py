# One-call refresh (2026-11-21): full band scan -> full A/B smoke -> one Telegram. The band scan and smoke threads are mocked;
# this pins the chain (stage order, the B1 rows, the smoke result, exactly one Telegram).
import os, sys, tempfile, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA)
sys.path.insert(0, ROOT)
from keeper import abrefresh, absmoke, alert, bandscan, store
for b, s, n, c, p, j, d in (("1", "100/340", "636576/534", "100.0", "40.9", "13.1", "30.74"),
                            ("8", "3774/394", "156510/534", "100.0", "60.3", "21.4", "11.2"),
                            ("20", "6400/112", "156510/534", "100.0", "68.4", "28.3", "8.31")):
    store.add(DATA, "band_scans", {"band": b, "serving": s, "nr_cell": n, "nr_arfcn": n.split("/")[0], "call_ok": c,
                                   "lat_p95": p, "jitter_ms": j, "down_mbps": d})
def bs(bands):
    bandscan.st.update(running="1"); threading.Timer(0.5, lambda: bandscan.st.update(running=None)).start(); return True, "1"
bandscan.start = bs
def sm(trigger="manual", notify=True):
    absmoke.st.update(running="refresh")
    absmoke.st["result"] = {"trigger": trigger, "verdict": "modem good",
                            "legs": [{"leg": "A high", "ok": True, "down_mbps": 30, "call_ok": 1, "lat_p95": 20, "jitter_ms": 3}],
                            "restored": {"lte": "340@100", "nr": "534@636576"}}
    threading.Timer(0.5, lambda: absmoke.st.update(running=None)).start(); return True, "refresh"
absmoke.run = sm
rep = []; alert.report = lambda m: rep.append(m)
ok, why = abrefresh.run("test"); assert ok, why
deadline = time.time() + 10
while abrefresh.st["running"] and time.time() < deadline: time.sleep(0.05)
assert abrefresh.st["running"] is None, abrefresh.st
res = abrefresh.st["result"]; print(res)
assert res["band_scan"] and res["band_scan"][0]["band"] == "1" and res["band_scan"][0]["serving"] == "100/340", res["band_scan"]
assert res["smoke"]["verdict"] == "modem good", res["smoke"]
assert len(rep) == 1 and "B1" in rep[0] and "modem good" in rep[0], rep
assert abrefresh.status()["stage"] == "done"
print("OK abrefresh")
