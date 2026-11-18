"""Band scan (2026-11-18): lock LTE to one band at a time over TR-069 (`LockLTEBand`, no reboot), record the serving cell and the
neighbour list of each, then put the full band list back - cells never in the CA / neighbour list (B8 ARFCN 3774: 112@3774) get into
the candidate table. Only while no LTE cell lock is held (a band lock on top of 340@100 = no service): LOCK_PAUSE / a test window.
Busy while it runs (the lock guard, capacity tests and the night screen wait). POST /band-scan?bands=8,1,3,7,20,28; GET /band-scans.
Env: BANDSCAN_WAIT_S (90 - per band, at least one sample on the band; ceiling 2x), BANDSCAN_RESTORE (the modem's full list)."""
import collections, re, statistics, threading, time

from keeper import acs, alert, brain, common, store
from keeper.common import E, event, log, record, stamp, state

PARAM = "Device.Cellular.Interface.1.LockLTEBand"
ALL = E("BANDSCAN_RESTORE", "1,2,3,4,5,7,8,12,17,20,25,26,28,30,32,34,38,39,40,41,42,46,48")   # the MC889's list (tr069 dump)
WAIT_S = float(E("BANDSCAN_WAIT_S", "90"))
COLS = ["time", "band", "serving", "serving_band", "rsrp", "rsrq", "sinr", "neighbours", "samples", "note", "run", "bw_mhz"]
st = {"running": None}
common.register_busy("band scan", lambda: bool(st["running"]))


def summarize(band, rows):
    """The samples of one band -> its most seen serving cell + that cell's median RSRP/RSRQ/SINR, every neighbour (best RSRP)."""
    bb = "B%s" % band
    on = [r for r in rows if r.get("lte_cell") and brain.band_of(r["lte_cell"].split("/")[0]) == bb]
    out = {"band": band, "bw_mhz": None, "serving": "", "serving_band": "", "rsrp": None, "rsrq": None, "sinr": None, "neighbours": "", "samples": len(on), "note": ""}
    if not on: out["note"] = "no service on %s (no sample on the band)" % bb; return out
    cell = collections.Counter(r["lte_cell"] for r in on).most_common(1)[0][0]; mine = [r for r in on if r["lte_cell"] == cell]
    med = lambda k: (lambda xs: statistics.median(xs) if xs else None)([brain.f(r.get(k)) for r in mine if brain.f(r.get(k)) is not None])
    nb = {}
    for r in on:
        for t in (r.get("lte_neighbours") or "").split():
            k, _, v = t.partition(":"); v = brain.f(v)
            if v is not None: nb[k] = max(nb.get(k, v), v)
    bws = [int(m.group(2)) for r in mine for m in [re.match(r"B\d+/(\d+/\d+):\S*?@(\d+)", r.get("lte_ca") or "")] if m and m.group(1) == cell]
    out.update(serving=cell, serving_band=bb, rsrp=med("lte_rsrp"), rsrq=med("lte_rsrq"), sinr=med("lte_sinr"),
               bw_mhz=collections.Counter(bws).most_common(1)[0][0] if bws else None,   # as logged ('@5'), never the band's usual value
               neighbours=" ".join("%s:%g" % kv for kv in nb.items()))
    return out


def scan(bands):
    """Thread: per band one write (no reboot), its samples for WAIT_S (until one is on the band, at most 2x), a record + an event;
    at the end ONE write of the full list back (also after an error)."""
    run = stamp()
    try:
        for b in bands:
            st["running"] = b; t0 = time.time()
            if not acs.tr069_set([(PARAM, "xsd:string", b)]):
                record("band_scans", COLS, {"time": stamp(), "band": b, "note": "LockLTEBand=%s not accepted by the modem" % b, "run": run}); continue
            on_band = lambda: any(brain.ts(r) >= t0 - 1 and brain.band_of((r.get("lte_cell") or "").split("/")[0]) == "B%s" % b for r in list(brain.HIST))
            while time.time() < t0 + WAIT_S or (not on_band() and time.time() < t0 + 2 * WAIT_S): time.sleep(min(1, WAIT_S / 3))   # window, not a guess
            res = summarize(b, [r for r in list(brain.HIST) if brain.ts(r) >= t0 - 1]); res.update(time=stamp(), run=run)
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
    """API: (started, why). Bands = LTE band numbers; refused while an LTE cell lock is held or another job runs."""
    bands = [str(b).strip() for b in bands]
    if not bands or not all(b.isdigit() and 1 <= int(b) <= 99 for b in bands): raise ValueError("bands=8,1,3 (LTE band numbers)")
    w = state().get("lte_lock") or {}
    if w.get("state") in ("active", "pending"):
        return False, "an LTE cell lock is active (%s): band locks only while unlocked (LOCK_PAUSE / a test window)" % w.get("cell")
    if st["running"] or common.busy(): return False, "busy: %s" % (st["running"] or common.busy())
    threading.Thread(target=lambda: scan(bands), daemon=True).start()
    return True, ", ".join("B" + b for b in bands)
