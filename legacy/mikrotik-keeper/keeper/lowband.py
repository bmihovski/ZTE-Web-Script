"""Low-band A/B phase 1 (2026-11-21): the low bands (B8/B20; B28 has no service here) and the network's n28 5G under them are the
bad-weather fallback the band scan found (156510/534 under 3774/394 and 6400/112). `GET /low-band` is read-only: the last band-scan
row per low band (serving cell, n28 pair, call-ready/loss/p95/jitter/down) against the current high-band no-lock quality, with the
keeper's own quality policy verdict (`brain.clearly_better`). `POST /low-band-trial?band=8|20` starts a one-band band scan: a band
lock (`LockLTEBand`, no reboot, no cell lock), the network picks the low-band cell + n28, the same internet test, the full band list
back. Phase 2 is the automatic two-tier A/B (not this module).
"""
from keeper import bandscan, brain, store
from keeper.common import DATA, E, state

BANDS = [b for b in E("LOW_BANDS", "8,20").replace(" ", "").split(",") if b]


def latest():
    """The last band-scan row per low band."""
    rows = store.rows(DATA, "band_scans")
    return [dict(r) for b in BANDS for r in [next((x for x in reversed(rows) if str(x.get("band")) == b), None)] if r]


def current():
    """The current high-band no-lock quality (latest sample + live probe) and the locks."""
    h = dict(list(brain.HIST)[-1]) if brain.HIST else {}
    l = brain.latency_row()
    return {"time": h.get("time", ""), "lte_cell": h.get("lte_cell", ""), "nr_cell": h.get("nr_cell", ""),
            "nr_sinr": h.get("nr_sinr", ""), "nr_rsrp": h.get("nr_rsrp", ""), "lte_sinr": h.get("lte_sinr", ""),
            "call_ok": l.get("call_ok"), "loss_pct": l.get("loss_pct"), "lat_p95": l.get("lat_p95"), "jitter_ms": l.get("jitter_ms"),
            "lte_lock": (state().get("lte_lock") or {}).get("cell"), "nr_lock": (state().get("nr_lock") or {}).get("cell")}


def _call(v):
    """The probe's call_ok is 1/0; a band-scan row's call_ok is 0-100 %. Both -> 0-100 (None = unknown)."""
    try: x = float(v)
    except (TypeError, ValueError): return None
    return 100.0 if x == 1 else (0.0 if x == 0 else x)


def _q(call_ok, loss, p95, jitter, sinr=None, rsrp=None):
    return brain.quality_values(_call(call_ok), loss, p95, jitter, sinr, rsrp)


def preview():
    """The phase-1 read-only view: candidates, current, and the quality-policy verdict per candidate."""
    cur = current(); qc = _q(cur.get("call_ok"), cur.get("loss_pct"), cur.get("lat_p95"), cur.get("jitter_ms"),
                             cur.get("nr_sinr"), cur.get("nr_rsrp"))
    cands = []
    for r in latest():
        q = _q(r.get("call_ok"), r.get("loss_pct"), r.get("lat_p95"), r.get("jitter_ms"), r.get("nr_sinr"), r.get("nr_rsrp"))
        cands.append({"band": r.get("band"), "time": r.get("time"), "serving": r.get("serving", ""), "nr_cell": r.get("nr_cell", ""),
                      "nr_arfcn": r.get("nr_arfcn", ""), "call_ok": r.get("call_ok"), "loss_pct": r.get("loss_pct"),
                      "lat_p95": r.get("lat_p95"), "jitter_ms": r.get("jitter_ms"), "down_mbps": r.get("down_mbps"),
                      "note": r.get("note", ""), "better": brain.clearly_better(q, qc)})
    best = max((c for c in cands if c["better"]), key=lambda c: _q(c.get("call_ok"), c.get("loss_pct"), c.get("lat_p95"), c.get("jitter_ms")),
               default=None)
    return {"bands": BANDS, "current": cur, "candidates": cands, "recommend": best,
            "note": "Phase 1: read-only. POST /low-band-trial?band=8|20 starts the one-band trial (band lock, no reboot, unlocked only)."}


def start(band):
    """Start a one-band low-band trial (the band scan's one-band scan) -> (started, why)."""
    b = str(band).strip()
    if b not in BANDS: raise ValueError("band=8|20 (LOW_BANDS=%s)" % ",".join(BANDS))
    return bandscan.start([b])
