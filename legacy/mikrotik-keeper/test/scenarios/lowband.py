# Low-band A/B phase 1 (2026-11-21): GET /low-band = the last B8/B20 scan (serving cell, n28 pair, call-ready/loss/p95/jitter/down)
# against the current high-band no-lock quality, with the quality-policy verdict; POST /low-band-trial?band=8|20 = a one-band scan.
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA)
sys.path.insert(0, ROOT)
from keeper import brain, common, lowband, store
ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
store.add(DATA, "band_scans", {"time": ts, "band": "8", "serving": "3774/394", "nr_cell": "156510/534", "nr_arfcn": "156510",
                               "nr_sinr": "3", "nr_rsrp": "-82", "call_ok": "100.0", "loss_pct": "0.0", "lat_p95": "60.3",
                               "jitter_ms": "21.4", "down_mbps": "11.2", "note": ""})
store.add(DATA, "band_scans", {"time": ts, "band": "20", "serving": "6400/112", "nr_cell": "156510/534", "nr_arfcn": "156510",
                               "nr_sinr": "6", "nr_rsrp": "-78", "call_ok": "100.0", "loss_pct": "0.0", "lat_p95": "68.4",
                               "jitter_ms": "28.3", "down_mbps": "8.31", "note": ""})
common.state({"lte_lock": None, "nr_lock": None})
now = time.time(); brain.lat_buf.clear(); brain.HIST.clear()
for i in range(30): brain.lat_buf.append((now - 60 + 2 * i, 30.0, "1.1.1.1"))
brain.HIST.append({"time": ts, "lte_cell": "3350/340", "nr_cell": "636576/534", "nr_sinr": "9", "nr_rsrp": "-80", "lte_sinr": "1"})
p = lowband.preview()
assert p["bands"] == ["8", "20"] and len(p["candidates"]) == 2, p
assert not any(c["better"] for c in p["candidates"]), p          # high band: 30 ms p95 beats the low band's 60/68 ms (quality first)
# a bad high-band no-lock (loss / no call) makes the low-band candidates clearly better
brain.lat_buf.clear()
for i in range(30): brain.lat_buf.append((now - 60 + 2 * i, None, "1.1.1.1"))
p2 = lowband.preview()
assert all(c["better"] for c in p2["candidates"]), p2
assert p2["recommend"] is not None and p2["recommend"]["band"] in ("8", "20"), p2
# the trial: a one-band band scan; only the low bands in LOW_BANDS
try: lowband.start("28"); raise AssertionError("B28 must be refused")
except ValueError: pass
calls = []; lowband.bandscan.start = lambda bands: (calls.append(list(bands)), (True, "B8"))[1]
assert lowband.start("8") == (True, "B8") and lowband.start("20") == (True, "B8") and calls == [["8"], ["20"]], calls
print("OK lowband")
