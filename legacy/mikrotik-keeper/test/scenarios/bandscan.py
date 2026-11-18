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
rows[0]["lte_ca"] = "B8/3774/112:-95/4@5"; rows[1]["lte_ca"] = "B8/3774/112:-93/6@5 B3/1550/340:-88/8@20"
assert bs.summarize("8", rows)["bw_mhz"] == 5, bs.summarize("8", rows)  # the serving carrier's bandwidth (B8 5 MHz, 2026-11-18)
# the refarm signal (3G spectrum released): a band's serving bandwidth differs from its last scan -> one Telegram note
assert bs.bw_change("8", 5, [{"band": "8", "bw_mhz": "5"}]) is None
assert bs.bw_change("8", 10, [{"band": "8", "bw_mhz": "5"}, {"band": "20", "bw_mhz": "10"}]) == "band scan: B8 bandwidth 5 -> 10 MHz - worth a lock test again"
assert bs.bw_change("8", 10, [{"band": "8", "bw_mhz": ""}]) is None and bs.bw_change("8", None, [{"band": "8", "bw_mhz": "5"}]) is None
assert bs.bw_change("8", 10, []) is None                                  # first scan: nothing to compare

# scan(): one write per band (no reboot), the samples of its window, then ONE write of the full list back; busy meanwhile
writes = []; seen_busy = []
def fake_set(pairs, reboot=False, timeout=180):
    writes.append((pairs, reboot)); seen_busy.append(common.busy())
    b = pairs[0][2]
    if b in ("8", "28"):                                   # the modem's next sample on that band (28: no service -> no LTE cell)
        brain.HIST.append({"time": st(time.time()), "lte_cell": "3774/112" if b == "8" else "", "lte_rsrp": "-94", "lte_rsrq": "-10",
                           "lte_sinr": "5", "lte_neighbours": "3774/36:-99", "lte_ca": "B8/3774/112:-94/5@10" if b == "8" else ""})
    return b != "99"                                       # 99: refused by the modem
acs.tr069_set = fake_set
common.state({"lte_lock": {"cell": "340@100", "state": "released"}})
store.add(common.DATA, "band_scans", {"time": st(0), "band": "8", "serving": "3774/112", "bw_mhz": "5", "run": "old"})   # the last scan: 5 MHz
from keeper import alert; tg = []; alert.report = lambda text: tg.append(text)
bs.scan(["8", "28", "99"])
assert tg == ["band scan: B8 bandwidth 5 -> 10 MHz - worth a lock test again"], tg
assert [w[0][0][2] for w in writes] == ["8", "28", "99", bs.ALL] and not any(r for p, r in writes) and all(p[0][0] == P for p, r in writes), writes
assert seen_busy[:3] == ["band scan"] * 3 and not common.busy() and bs.st["running"] is None, seen_busy
got = {r["band"]: r for r in store.rows(common.DATA, "band_scans") if r["run"] != "old"}
assert got["8"]["serving"] == "3774/112" and got["8"]["bw_mhz"] == "10" and got["28"]["note"].startswith("no service") and "not accepted" in got["99"]["note"], got
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
print("OK bandscan")
