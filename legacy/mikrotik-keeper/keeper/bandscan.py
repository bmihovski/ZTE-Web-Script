"""Band scan (2026-11-18): lock LTE to one band at a time over TR-069 (`LockLTEBand`, no reboot), record the serving cell, the
neighbour list, the network's OWN 5G cell under that anchor (no NR lock: the NSA pair is the point) and the internet quality of the
band (call-ready / loss / p95 / jitter from the probe, plus a short load test that raises the 5G leg) - then put the full band list
back. Cells never in the CA / neighbour list (B8 ARFCN 3774: 112@3774) and the low bands (B20 / B28, bad-weather fallbacks) get
into the night candidates and the (5G cell, LTE anchor) NR database (the samples + `brain.track`).

Only while no LTE cell lock is held (a band lock on top of 340@100 = no service) AND no NR cell lock is active/pending: a pinned 5G
cell would make the scan see the lock instead of the network's own pair, and a band change can lose it -> the guard re-applies it
with a reboot (2026-11-21). LOCK_PAUSE / a test window. Busy while it runs (the lock guard, capacity tests and the night screen
wait). POST /band-scan?bands=8,1,3,7,20,28; GET /band-scans.
Env: BANDSCAN_WAIT_S (90 - per band, at least one sample on the band; ceiling 2x), BANDSCAN_TEST_S (25 - the per-band internet test
that raises the 5G leg; 0 = no load test), BANDSCAN_RESTORE (the modem's full list)."""
import collections, re, statistics, threading, time

from keeper import acs, alert, brain, common, store
from keeper.common import E, event, log, record, stamp, state

PARAM = "Device.Cellular.Interface.1.LockLTEBand"
ALL = E("BANDSCAN_RESTORE", "1,2,3,4,5,7,8,12,17,20,25,26,28,30,32,34,38,39,40,41,42,46,48")   # the MC889's list (tr069 dump)
WAIT_S = float(E("BANDSCAN_WAIT_S", "90"))
TEST_S = float(E("BANDSCAN_TEST_S", "25"))         # per band: a short load test raises the NSA 5G leg; the probe gives the quality
COLS = ["time", "band", "serving", "serving_band", "rsrp", "rsrq", "sinr", "neighbours", "samples", "note", "run", "bw_mhz",
        "nr_cell", "nr_arfcn", "nr_sinr", "nr_rsrp", "call_ok", "lat_p95", "jitter_ms", "loss_pct", "down_mbps"]
st = {"running": None, "run": ""}
common.register_busy("band scan", lambda: bool(st["running"]))


def _med(rows, key):
    xs = [brain.f(r.get(key)) for r in rows if brain.f(r.get(key)) is not None]
    return statistics.median(xs) if xs else None


def summarize(band, rows, net=None):
    """The samples of one band -> its most seen serving cell, that cell's median RSRP/RSRQ/SINR, every neighbour (best RSRP), the
    most seen 5G cell + its ARFCN/SINR/RSRP, and the band's internet quality (the probe's call-ready/loss/p95/jitter; `net` = an
    internet test's result under load, overriding). No sample on the band = no quality (`None`, not 0 %)."""
    bb = "B%s" % band
    on = [r for r in rows if r.get("lte_cell") and brain.band_of(r["lte_cell"].split("/")[0]) == bb]
    out = {"band": band, "bw_mhz": None, "serving": "", "serving_band": "", "rsrp": None, "rsrq": None, "sinr": None, "neighbours": "",
           "samples": len(on), "note": "", "nr_cell": "", "nr_arfcn": "", "nr_sinr": None, "nr_rsrp": None,
           "call_ok": None, "lat_p95": None, "jitter_ms": None, "loss_pct": None, "down_mbps": None}
    if not on: out["note"] = "no service on %s (no sample on the band)" % bb; return out
    cell = collections.Counter(r["lte_cell"] for r in on).most_common(1)[0][0]; mine = [r for r in on if r["lte_cell"] == cell]
    med = lambda k: _med(mine, k)
    nb = {}
    for r in on:
        for t in (r.get("lte_neighbours") or "").split():
            k, _, v = t.partition(":"); v = brain.f(v)
            if v is not None: nb[k] = max(nb.get(k, v), v)
    bws = [int(m.group(2)) for r in mine for m in [re.match(r"B\d+/(\d+/\d+):\S*?@(\d+)", r.get("lte_ca") or "")] if m and m.group(1) == cell]
    nr = [r for r in on if r.get("nr_cell") and r.get("nr_cell") != "no5G"]
    nrc = collections.Counter(r["nr_cell"] for r in nr).most_common(1)[0][0] if nr else ""
    nmine = [r for r in nr if r["nr_cell"] == nrc]
    net = net or {}
    def q(k):                                       # the load test under traffic wins over the idle samples; else the samples
        v = net.get(k)
        return v if v not in (None, "") else med(k)
    call = net.get("call_ok")
    if call is not None: call_ok = 100.0 if call else 0.0
    else: call_ok = round(100.0 * sum(1 for r in on if str(r.get("call_ok")) == "1") / len(on), 1) if on else None
    out.update(serving=cell, serving_band=bb, rsrp=med("lte_rsrp"), rsrq=med("lte_rsrq"), sinr=med("lte_sinr"),
               bw_mhz=collections.Counter(bws).most_common(1)[0][0] if bws else None,   # as logged ('@5'), never the band's usual value
               neighbours=" ".join("%s:%g" % kv for kv in nb.items()),
               nr_cell=nrc, nr_arfcn=next((r.get("nr_arfcn") for r in nmine if r.get("nr_arfcn")), "") or (nrc.split("/")[0] if "/" in nrc else ""),
               nr_sinr=_med(nmine, "nr_sinr"), nr_rsrp=_med(nmine, "nr_rsrp"),
               call_ok=call_ok, lat_p95=q("lat_p95"), jitter_ms=q("jitter_ms"), loss_pct=q("loss_pct"),
               down_mbps=net.get("down_mbps"))
    return out


def scan(bands, run=None):
    """Thread: per band one write (no reboot), its samples for WAIT_S (until one is on the band, at most 2x), a short load test
    (raises the 5G leg; the probe measures the band's internet quality), a record + an event, the (5G cell, anchor) pair into the NR
    database; at the end ONE write of the full list back (also after an error). `run` is the stamp every row carries (start() sets it
    synchronously, so a caller can select exactly this scan's rows)."""
    run = run or st.get("run") or stamp()
    try:
        for b in bands:
            st["running"] = b; t0 = time.time()
            if not acs.tr069_set([(PARAM, "xsd:string", b)]):
                record("band_scans", COLS, {"time": stamp(), "band": b, "note": "LockLTEBand=%s not accepted by the modem" % b, "run": run}); continue
            on_band = lambda: any(brain.ts(r) >= t0 - 1 and brain.band_of((r.get("lte_cell") or "").split("/")[0]) == "B%s" % b for r in list(brain.HIST))
            while time.time() < t0 + WAIT_S or (not on_band() and time.time() < t0 + 2 * WAIT_S): time.sleep(min(1, WAIT_S / 3))   # window, not a guess
            net = {}
            if TEST_S > 0:
                try: net = brain.internet_test(seconds=TEST_S)                 # load: the NSA 5G leg must be up for the pair + quality
                except Exception as e: log("band scan B%s internet test failed: %r" % (b, e))
            res = summarize(b, [r for r in list(brain.HIST) if brain.ts(r) >= t0 - 1], net=net); res.update(time=stamp(), run=run)
            if res.get("nr_cell") and res.get("serving"):                    # learn the pair even if the monitor missed the 5G leg
                try:
                    brain.track({"time": res["time"], "lte_cell": res["serving"], "nr_cell": res["nr_cell"], "nr_arfcn": res["nr_arfcn"],
                                 "nr_sinr": res["nr_sinr"], "nr_rsrp": res["nr_rsrp"], "lte_neighbours": res["neighbours"], "lte_site": "",
                                 "call_ok": "1" if res["call_ok"] == 100.0 else ("0" if res["call_ok"] is not None else ""),
                                 "loss_pct": res["loss_pct"], "lat_p95": res["lat_p95"], "jitter_ms": res["jitter_ms"]})
                    brain.save_registry()
                except Exception as e: log("band scan B%s pair update failed: %r" % (b, e))
            note = bw_change(b, res["bw_mhz"], store.rows(common.DATA, "band_scans"))
            record("band_scans", COLS, res)
            if note: log(note); alert.report(note)
            event({"event": "band scan B%s" % b, "to": res["serving"] or res["note"], "why": res["neighbours"][:300]})
    except Exception as e:
        log("band scan failed: %r" % e)
    finally:
        ok = acs.tr069_set([(PARAM, "xsd:string", ALL)]); st["running"] = None
        log("band scan done (%s): full band list %s" % (", ".join("B" + b for b in bands), "back" if ok else "NOT written back - check LockLTEBand"))


def bw_change(band, bw, rows):
    """The refarm signal (3G spectrum released -> B8 5 -> 10 MHz): `band`'s serving bandwidth differs from its last scan that logged
    one -> the note to send, else None (first scan, bandwidth unknown)."""
    last = next((r["bw_mhz"] for r in reversed(rows) if r.get("band") == band and r.get("bw_mhz")), None)
    if bw is None or last is None or int(float(last)) == int(bw): return None
    return "band scan: B%s bandwidth %d -> %d MHz - worth a lock test again" % (band, int(float(last)), int(bw))


def start(bands):
    """API: (started, why). Bands = LTE band numbers; refused while an LTE cell lock is held, an NR cell lock is active/pending, or
    another job runs."""
    bands = [str(b).strip() for b in bands]
    if not bands or not all(b.isdigit() and 1 <= int(b) <= 99 for b in bands): raise ValueError("bands=8,1,3 (LTE band numbers)")
    w = state().get("lte_lock") or {}
    if w.get("state") in ("active", "pending"):
        return False, "an LTE cell lock is active (%s): band locks only while unlocked (LOCK_PAUSE / a test window)" % w.get("cell")
    w = state().get("nr_lock") or {}
    if w.get("state") in ("active", "pending"):
        return False, "an NR cell lock is %s (%s): remove it first (the band scan needs the network's own 5G pair)" % (w.get("state"), w.get("cell"))
    if st["running"] or common.busy(): return False, "busy: %s" % (st["running"] or common.busy())
    st["run"] = stamp(); st["running"] = bands[0]   # set before the thread: the caller/chain sees the scan's run + state at once
    threading.Thread(target=lambda: scan(bands), daemon=True).start()
    return True, ", ".join("B" + b for b in bands)
