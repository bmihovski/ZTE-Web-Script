# One-call refresh (2026-11-21): full band scan -> full A/B smoke -> one Telegram. The band scan and smoke threads are mocked;
# this pins the chain (stage order, the B1 rows, the smoke result, exactly one Telegram) and that a stage still running at its
# ceiling aborts the chain instead of starting the smoke on the band scan's lock (the 20-min ceiling was under the 6-band scan's
# own worst case, 6 x (2 x 90 + 25) = 1230 s).
import os, sys, tempfile, threading, time, types
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA)
sys.path.insert(0, ROOT)
from keeper import abrefresh, absmoke, alert, bandscan, store
for b, s, n, c, p, j, d, note in (("1", "100/340", "636576/534", "100.0", "40.9", "13.1", "30.74", ""),
                                  ("8", "3774/394", "156510/534", "100.0", "60.3", "21.4", "11.2", ""),
                                  ("20", "6400/112", "", "100.0", "68.4", "28.3", "8.31", ""),          # the scan saw no 5G leg
                                  ("28", "", "", "", "", "", "", "no service on B28 (no sample on the band)")):
    store.add(DATA, "band_scans", {"band": b, "serving": s, "nr_cell": n, "nr_arfcn": n.split("/")[0], "call_ok": c,
                                   "lat_p95": p, "jitter_ms": j, "down_mbps": d, "note": note})
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
# no blank fields in the Telegram: a band the scan saw no 5G leg on says so, and a band with no data is named as such
assert "B20: 6400/112 + nr cell missing" in rep[0], rep[0]
b28 = next(l for l in rep[0].splitlines() if l.startswith("B28:"))
assert "no service" in b28 and "B28:  +" not in b28, b28
assert next(b for b in res["band_scan"] if b["band"] == "28")["note"].startswith("no service"), res["band_scan"]
assert abrefresh.status()["stage"] == "done"

# a stage that never finishes: the chain must abort, not start the smoke on the band scan's band lock
calls = []
def bs_stuck(bands):
    bandscan.st.update(running="B1")                       # the scan starts and never clears its flag
    return True, "B1"
bandscan.start = bs_stuck
absmoke.run = lambda trigger="manual", notify=True: (calls.append(trigger), (True, trigger))[1]
rep.clear()
tt = [0]
def mono():
    tt[0] += 10 ** 9; return tt[0]                      # every read jumps: the ceiling expires at once
abrefresh.time = types.SimpleNamespace(monotonic=mono, sleep=lambda s: None, time=time.time)
ok, why = abrefresh.run("stuck"); assert ok, why
deadline = time.time() + 10
while abrefresh.st["running"] and time.time() < deadline: time.sleep(0.05)
assert abrefresh.st["running"] is None, abrefresh.st
res = abrefresh.st["result"]
assert calls == [], calls                                  # the smoke was NOT started on the scan's band lock
assert not res["smoke"] and "still running after" in res["trigger"], res
assert len(rep) == 1 and "still running after" in rep[0], rep
print("OK abrefresh")
