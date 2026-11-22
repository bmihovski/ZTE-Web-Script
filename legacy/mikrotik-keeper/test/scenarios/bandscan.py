# band scan (2026-11-18): LTE locked to one band at a time over TR-069 (LockLTEBand, no reboot), the serving cell + neighbours of each
# recorded, then the full band list back - cells never in the CA / neighbour list (B8 ARFCN 3774: 112@3774) reach the candidate table
import os, sys, tempfile, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), BANDSCAN_WAIT_S="0.3")
sys.path.insert(0, ROOT)
from keeper import acs, bandscan as bs, brain, common, store
P = "Device.Cellular.Interface.1.LockLTEBand"
st = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))

# pure: the samples of one band -> its serving cell(s), median values, the neighbours (best RSRP per cell)
rows = [{"time": st(1000), "lte_cell": "3774/112", "lte_rsrp": "-95", "lte_rsrq": "-11", "lte_sinr": "4", "lte_neighbours": "3774/112:-95 3774/36:-101 1550/340:-90"},
        {"time": st(1060), "lte_cell": "3774/112", "lte_rsrp": "-93", "lte_rsrq": "-10", "lte_sinr": "6", "lte_neighbours": "3774/36:-99"},
        {"time": st(1070), "lte_cell": "1550/340", "lte_rsrp": "-88", "lte_rsrq": "-9", "lte_sinr": "8", "lte_neighbours": ""}]   # not B8: ignored
s = bs.summarize("8", rows)
assert s["serving"] == "3774/112" and s["serving_band"] == "B8" and (s["rsrp"], s["rsrq"], s["sinr"]) == (-94.0, -10.5, 5.0), s
assert s["neighbours"] == "3774/112:-95 3774/36:-99 1550/340:-90" and s["samples"] == 2 and s["note"] == "", s
assert bs.summarize("28", rows)["note"] == "no service on B28 (no sample on the band)" and bs.summarize("28", rows)["serving"] == ""
assert s["bw_mhz"] is None, s                                           # no '@bw' logged: unknown, never the band's usual value
# 2026-11-21: internet link quality + the network's own 5G pair, not only radio. The band window's samples carry the pair
# (636576/534 under the band's serving cell) and the probe's call-ready/loss/p95/jitter; an internet test under load overrides.
rows[0].update(nr_cell="636576/534", nr_arfcn="636576", nr_sinr="9", nr_rsrp="-80", call_ok="1", loss_pct="0.0", lat_p95="30.0", jitter_ms="4.0")
rows[1].update(nr_cell="636576/534", nr_arfcn="636576", nr_sinr="11", nr_rsrp="-78", call_ok="1", loss_pct="2.0", lat_p95="34.0", jitter_ms="6.0")
sq = bs.summarize("8", rows)
assert sq["nr_cell"] == "636576/534" and sq["nr_arfcn"] == "636576" and (sq["nr_sinr"], sq["nr_rsrp"]) == (10.0, -79.0), sq
assert sq["call_ok"] == 100.0 and sq["lat_p95"] == 32.0 and sq["jitter_ms"] == 5.0 and sq["loss_pct"] == 1.0, sq
net = {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 25.0, "jitter_ms": 3.0, "down_mbps": 140.0}
sn = bs.summarize("8", rows, net=net)
assert sn["call_ok"] == 100.0 and (sn["lat_p95"], sn["jitter_ms"], sn["loss_pct"], sn["down_mbps"]) == (25.0, 3.0, 0.0, 140.0), sn
# a band with no sample gets no quality/pair (not a 0 %, which would read as a bad band)
assert bs.summarize("28", rows)["call_ok"] is None and bs.summarize("28", rows)["nr_cell"] == ""
# the live 2026-11-21 13:02 scan: the sample carries nr_cell (156510/534 = n28) but not nr_arfcn -> the ARFCN comes from the cell
n28 = [{"time": st(1000), "lte_cell": "3774/394", "lte_rsrp": "-95", "nr_cell": "156510/534", "nr_sinr": "8", "call_ok": "1"}]
assert bs.summarize("8", n28)["nr_arfcn"] == "156510", bs.summarize("8", n28)
rows[0]["lte_ca"] = "B8/3774/112:-95/4@5"; rows[1]["lte_ca"] = "B8/3774/112:-93/6@5 B3/1550/340:-88/8@20"
assert bs.summarize("8", rows)["bw_mhz"] == 5, bs.summarize("8", rows)  # the serving carrier's bandwidth (B8 5 MHz, 2026-11-18)
# the refarm signal (3G spectrum released): a band's serving bandwidth differs from its last scan -> one Telegram note
assert bs.bw_change("8", 5, [{"band": "8", "bw_mhz": "5"}]) is None
assert bs.bw_change("8", 10, [{"band": "8", "bw_mhz": "5"}, {"band": "20", "bw_mhz": "10"}]) == "band scan: B8 bandwidth 5 -> 10 MHz - worth a lock test again"
assert bs.bw_change("8", 10, [{"band": "8", "bw_mhz": ""}]) is None and bs.bw_change("8", None, [{"band": "8", "bw_mhz": "5"}]) is None
assert bs.bw_change("8", 10, []) is None                                  # first scan: nothing to compare

# A band write without reboot can return mixed primary and CA fields. PCI 112 and
# its signal values are identical, but neither a stale B7 primary nor a B7 CA read
# is evidence for the selected B8 anchor. NR must come from the selected LTE row.
b8 = dict(rows[0], lte_band_config="8", lte_cell="3774/112", lte_ca="B8/3774/112:-79/-4@5",
          nr_cell="156510/534", nr_arfcn="156510", lte_neighbours="3350/112:-79 3774/36:-99")
stale_primary = dict(b8, lte_cell="3350/112")
stale_ca = dict(b8, lte_ca="B7/3350/112:-79/-4@5")
stale_mask = dict(b8, lte_band_config="7")
assert all(not bs.summarize("8", [r])["serving"] for r in (stale_primary, stale_ca, stale_mask))
others = [dict(b8, lte_cell="3774/394", lte_ca="B8/3774/394:-79/-4@5", nr_cell="156510/212", nr_arfcn="156510")] * 2
valid = bs.summarize("8", [b8, b8, b8, stale_primary, stale_ca, stale_mask, *others])
assert valid["serving"] == "3774/112" and valid["nr_cell"] == "156510/534"
assert valid["samples"] == 5 and "3350/112:-79" in valid["neighbours"], valid
assert bs.summarize("8", [b8], net={"down_mbps": 12})["down_mbps"] == 12

# scan(): one write per band (no reboot), the samples of its window, then ONE write of the full list back; busy meanwhile
writes = []; seen_busy = []
def fake_set(pairs, reboot=False, timeout=180):
    writes.append((pairs, reboot)); seen_busy.append(common.busy())
    b = pairs[0][2]
    if b in ("8", "28"):                                   # the modem's next sample on that band (28: no service -> no LTE cell)
        row = {"time": st(time.time()), "lte_band_config": b, "lte_cell": "3774/112" if b == "8" else "", "lte_rsrp": "-94", "lte_rsrq": "-10",
                           "lte_sinr": "5", "lte_neighbours": "3774/36:-99", "lte_ca": "B8/3774/112:-94/5@10" if b == "8" else "",
                           "nr_cell": "636576/534" if b == "8" else "no5G", "nr_arfcn": "636576" if b == "8" else "",
                           "nr_sinr": "9" if b == "8" else "", "nr_rsrp": "-80" if b == "8" else "",
                           "call_ok": "1" if b == "8" else "", "loss_pct": "0" if b == "8" else "",
                           "lat_p95": "28" if b == "8" else "", "jitter_ms": "3" if b == "8" else ""}
        threading.Timer(0.01, brain.HIST.append, args=(row,)).start()
    return b != "99"                                       # 99: refused by the modem
acs.tr069_set = fake_set
common.state({"lte_lock": {"cell": "340@100", "state": "released"}})
store.add(common.DATA, "band_scans", {"time": st(0), "band": "8", "serving": "3774/112", "bw_mhz": "5", "run": "old"})   # the last scan: 5 MHz
from keeper import alert; tg = []; alert.report = lambda text: tg.append(text)
brain.internet_test = lambda **kw: {"call_ok": 1, "loss_pct": 0.0, "lat_p95": 30.0, "jitter_ms": 4.0, "down_mbps": 120.0}
bs.scan(["8", "28", "99"])
assert tg == ["band scan: B8 bandwidth 5 -> 10 MHz - worth a lock test again"], tg
assert [w[0][0][2] for w in writes] == ["8", "28", "99", bs.ALL] and not any(r for p, r in writes) and all(p[0][0] == P for p, r in writes), writes
assert seen_busy[:3] == ["band scan"] * 3 and not common.busy() and bs.st["running"] is None, seen_busy
got = {r["band"]: r for r in store.rows(common.DATA, "band_scans") if r["run"] != "old"}
assert got["8"]["serving"] == "3774/112" and got["8"]["bw_mhz"] == "10" and got["28"]["note"].startswith("no service") and "not accepted" in got["99"]["note"], got
assert got["8"]["nr_cell"] == "636576/534" and got["8"]["nr_arfcn"] == "636576" and got["8"]["call_ok"] == "100.0" and float(got["8"]["down_mbps"]) == 120.0, got["8"]
assert got["28"]["call_ok"] in (None, "") and got["28"]["nr_cell"] == "", got["28"]
# the band's pair goes into the NR database (5G cell 636576/534 under anchor 3774/112): night / A-B candidate material
assert "3774/112" in brain.REG["nr"]["636576/534"]["anchors"], brain.REG["nr"].get("636576/534")
assert len({r["run"] for r in got.values()}) == 1

# start(): refused while an LTE cell lock is held (a B8 band lock on top of 340@100 = no service) or another job runs; bands validated
common.state({"lte_lock": {"cell": "340@100", "state": "active"}})
assert bs.start(["8"]) == (False, "an LTE cell lock is active (340@100): band locks only while unlocked (LOCK_PAUSE / a test window)")
common.state({"lte_lock": {"cell": "340@100", "state": "released"}})
for bad in (["x"], [], ["8", "0"]):
    try: bs.start(bad); raise AssertionError(bad)
    except ValueError: pass
done = threading.Event(); bs.scan = lambda bands: done.set()
assert bs.start(["8", "1"]) == (True, "B8, B1") and done.wait(2)
# 2026-11-21: the band scan must not run under an NR cell lock. The scan needs the network's own 5G pair per band; with the cell
# pinned it sees the lock instead, and a band change can lose it -> the guard re-applies it with a reboot.
common.state({"nr_lock": {"cell": "534@636576", "state": "active"}})
assert bs.start(["8"]) == (False, "an NR cell lock is active (534@636576): remove it first (the band scan needs the network's own 5G pair)")
common.state({"nr_lock": {"cell": "534@636576", "state": "pending"}})
assert bs.start(["8"]) == (False, "an NR cell lock is pending (534@636576): remove it first (the band scan needs the network's own 5G pair)")
common.state({"nr_lock": None})
print("OK bandscan")
