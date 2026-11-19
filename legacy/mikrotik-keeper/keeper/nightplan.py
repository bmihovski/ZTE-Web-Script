"""The night planner (docs/NIGHTLY-PLAN.md items 1-3, 2026-11-18): which cells the night screen tests, from evidence.

Candidates = every LTE cell in the stored samples (serving + neighbour list) of the last NIGHT_LOOKBACK_DAYS, plus the cells of band
scans. A cell is tested at night only if its ARFCN is on at night (>= NIGHT_ARFCN_PCT of the night samples show any cell on it -
2026-11-18: 100/B1 absent from 01:00) and the cell itself is present in >= NIGHT_CELL_PCT of them, or a night band scan saw it
(2026-11-18: 493/202/236@1550 were picked by DAYTIME RSRP, present 1-12 % at night, each lock hung the ZTE up to 31 min).
Order: the night median RSRP, best first. Tested = a FULL test (iperf + nr_iperf, keeper >= 2026-11-18e) in the last NIGHT_RETEST_DAYS,
unless the RSRP rose >= 6 dB since; a cell unseen for 3 days is gone. Cells seen only by day go to `day_only` (the Saturday window).
"""
import collections, statistics, time

from keeper.common import E

LOOKBACK_DAYS = float(E("NIGHT_LOOKBACK_DAYS", "7"))
ARFCN_PCT, CELL_PCT = float(E("NIGHT_ARFCN_PCT", "50")), float(E("NIGHT_CELL_PCT", "50"))
RETEST_DAYS, RETEST_DB, GONE_DAYS = float(E("NIGHT_RETEST_DAYS", "30")), 6.0, 3.0
WEAK_DB = float(E("NIGHT_WEAK_DB", "10"))      # a cell this far under its ARFCN's best night RSRP is not worth a ~17-min step
MARGIN_S = 15 * 60
SCAN_MIN_DBM = -115.0   # a band-scan reading below this is noise, not a cell (2026-11-19: B1 "served" 100/393 at -127 with ARFCN 100 off)


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def _tod(t): return time.strftime("%H:%M", time.localtime(t))


def _in(tod, win):
    a, b = win
    return (a <= tod < b) if a <= b else (tod >= a or tod < b)


def _ts(row): return time.mktime(time.strptime(row["time"][:19], "%Y-%m-%d %H:%M:%S"))


def _cells(row):
    """{earfcn/pci: rsrp} of one sample: the neighbour list + the serving cell."""
    out = {}
    for t in (row.get("lte_neighbours") or "").split():
        k, _, v = t.partition(":"); v = _f(v)
        if "/" in k and v is not None: out[k] = v
    if row.get("lte_cell") and "/" in row["lte_cell"]: out.setdefault(row["lte_cell"], _f(row.get("lte_rsrp")))
    return out


def spec(cell):
    """'6400/112' -> '112@6400' (the lock spec)."""
    e, p = cell.split("/"); return "%s@%s" % (p, e)


def plan(samples, tests, scans, now, win, lock=None, dead=()):
    """-> {cells: night candidates best first, day_only, night_off: ARFCNs off at night, why: {spec: text}}. `win` = (HH:MM, HH:MM)."""
    night = [r for r in samples if _in(r["time"][11:16], win)]; day = [r for r in samples if not _in(r["time"][11:16], win)]
    seen_n, seen_d, rsrp_n, rsrp_d, last, arfcn_n = collections.Counter(), collections.Counter(), collections.defaultdict(list), collections.defaultdict(list), {}, collections.Counter()
    for r in night:
        cs = _cells(r)
        for a in {k.split("/")[0] for k in cs}: arfcn_n[a] += 1
        for k, v in cs.items(): seen_n[k] += 1; v is not None and rsrp_n[k].append(v); last[k] = max(last.get(k, 0), _ts(r))
    for r in day:
        for k, v in _cells(r).items(): seen_d[k] += 1; v is not None and rsrp_d[k].append(v); last[k] = max(last.get(k, 0), _ts(r))
    scan_n = {}
    for s in scans:                                   # a band scan at night = the cell is there at night (bands never in the CA)
        if not _in(s["time"][11:16], win): continue
        for t in ("%s:%s" % (s.get("serving"), s.get("rsrp")) + " " + (s.get("neighbours") or "")).split():
            k, _, v = t.partition(":"); v = _f(v)
            if "/" in k and v is not None and v >= SCAN_MIN_DBM: scan_n[k] = max(scan_n.get(k, -999), v); last[k] = max(last.get(k, 0), _ts(s))
    on = {a for a, n in arfcn_n.items() if night and 100.0 * n / len(night) >= ARFCN_PCT} | {k.split("/")[0] for k in scan_n}
    best = {}
    for k in set(rsrp_n) | set(scan_n):
        v = statistics.median(rsrp_n[k]) if rsrp_n[k] else scan_n.get(k)
        if v is not None: a = k.split("/")[0]; best[a] = max(best.get(a, -999), v)
    best_d = {}
    for k, xs in rsrp_d.items(): a = k.split("/")[0]; best_d[a] = max(best_d.get(a, -999), statistics.median(xs))
    full = {}
    for t in tests:                                   # the newest FULL test per cell (iperf + 5G under traffic)
        if (_f(t.get("minutes")) or 0) > 0 and t.get("down_mbps") not in (None, "") and t.get("nr_iperf") not in (None, "") \
                and now - _ts(t) < RETEST_DAYS * 86400:
            full[t["lock"]] = t
    cand, why, day_only = [], {}, []
    for k in set(seen_n) | set(scan_n) | set(seen_d):
        sp = spec(k)
        if sp == lock or sp in dead or now - last.get(k, 0) > GONE_DAYS * 86400: continue
        pct = 100.0 * seen_n[k] / len(night) if night else 0
        r_n = statistics.median(rsrp_n[k]) if rsrp_n[k] else scan_n.get(k)
        if k.split("/")[0] in on and (pct >= CELL_PCT or k in scan_n):
            if r_n is not None and r_n < best.get(k.split("/")[0], r_n) - WEAK_DB: continue
            t = full.get(sp)
            if t and not (r_n is not None and _f(t.get("anchor_rsrp_p50")) is not None and r_n >= _f(t["anchor_rsrp_p50"]) + RETEST_DB): continue
            cand.append((r_n if r_n is not None else -999, sp))
            why[sp] = "night %g dBm, %s" % (r_n, "%.0f %% of night samples" % pct if pct >= CELL_PCT else "band scan (ARFCN %s)" % k.split("/")[0])
        elif (k.split("/")[0] not in on and sp not in full and day and 100.0 * seen_d[k] / len(day) >= CELL_PCT and rsrp_d[k]
              and statistics.median(rsrp_d[k]) >= best_d[k.split("/")[0]] - WEAK_DB):    # day-only: its ARFCN is off at night (2026-11-19: 206 cells)
            day_only.append((statistics.median(rsrp_d[k]), sp))
    return {"cells": [s for _, s in sorted(cand, key=lambda x: (-x[0], x[1]))], "day_only": [s for _, s in sorted(day_only, key=lambda x: (-x[0], x[1]))],
            "night_off": sorted({k.split("/")[0] for k in seen_d} - on), "why": why}


def day_confirm(rows, within=5.0):
    """One night run's rows (baseline first) -> the cells within `within` score points of the baseline, to confirm by day
    (docs/NIGHTLY-PLAN.md item 7): a night result is an empty network; only a daytime test says it holds under load.
    Baseline or cell not fully scored (gate / missing column): no claim, not listed."""
    from keeper import brain
    if not rows: return []
    sc = lambda r: (lambda s: s["score"] if s["score"] is not None and not s["partial"] else None)(brain.lt_score(r))
    base = sc(rows[0])
    if base is None: return []
    return [r["lock"] for r in rows[1:] if sc(r) is not None and sc(r) >= base - within]


def budget(now, end, step_s, margin_s=MARGIN_S):
    """Cells that fit before `end` (the baseline goes first)."""
    return max(0, int((end - now - margin_s) // step_s) - 1)


def window_end(now, spec_):
    """'00:50-08:00': the epoch the current window ends, None outside it."""
    a, b = spec_.split("-"); t = _tod(now)
    if not _in(t, (a, b)): return None
    lt = time.localtime(now); end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(b[:2]), int(b[3:5]), 0, 0, 0, -1))
    return end if end > now else end + 86400
