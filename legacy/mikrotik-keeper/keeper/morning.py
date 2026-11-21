"""Morning cell check (2026-11-21): no-lock -> the day bands' best two cells under cell locks (CA kept) -> 5G gate -> A/B -> best 5G.

Daily (Mon-Fri, `MORNING` 08:00; a missed day runs as the first thing once the clock is verified): one no-lock leg (the full CA)
and, for B1/B3/B7, a band-lock discovery of the band's #1 (serving) and #2 (best neighbour by a known 5G pair, then RSRP), then a
cell lock + probe for each. The band lock is discovery only (it collapses the CA); the cell lock keeps the CA (2026-11-21: the
08:55 band locks show one carrier, the cell locks show 434 + 100/434 and 66 + 3350/66). Saturday (`MORNING_LOW_SATURDAY`) - or
when the high/no-lock quality is bad (down < `MORNING_LOW_DOWN`, call-ready < `MORNING_LOW_CALL`) - the low bands B8/B20 are added
with the same discovery (their PCI changes: Yettel re-numbers the low cells, so the cells are re-discovered every time, never
cached). The low bands are the fallback, not a daily candidate: the B8 5 MHz / B20 10 MHz anchors cannot match the n78 100 MHz CA
(measured 2026-11-21: B8 394+n28 15.3 Mbit/s vs no-lock 28.0; the B20 394 10 MHz did 50.8/28.9 on 2026-11-18).

**5G is a hard gate:** a leg is a candidate only if the 5G leg was up during its probe (a sample with a real `nr_cell` in the probe
window). Only 5G legs are A/B/persisted; the no-lock is the default when it has 5G; a band with one 5G cell contributes A only; a
band with none is skipped. The probe is 25 s; the pair database (minutes, long-run call/loss/p95/jitter) is the stability guard
against a cell that looks good for 25 s and then switches (the B7 failover).

Each cell lock is one reboot on the ZTE; the no-lock start and the best restore are one each. Daily: 8 reboots (~25 min); with the
low bands: 12 (~40 min). The upload is measured on the restored winner (the modem's iperf3, 1 stream - the MC889 gives no upload
with 4); the daily probe is download + latency.

Run it: `POST /morning?low=1|0` (manual; empty = auto: Saturday or a bad high baseline), `GET /morning` (state/result). It is busy.
"""
import time, threading

from keeper import acs, alert, bandscan, brain, captest, common, devices
from keeper.common import E, event, log, state, stamp

MORNING = E("MORNING", "08:00")                       # '' = off; at/after this HH:MM once a day (the missed day runs first thing)
MORNING_END = E("MORNING_END", "12:00")               # no catch-up after this (a 23:00 start is the night, not the morning)
BANDS = [b for b in E("MORNING_BANDS", "1,3,7").replace(" ", "").split(",") if b]
LOW = [b for b in E("MORNING_LOW_BANDS", "8,20").replace(" ", "").split(",") if b]
LOW_SAT = E("MORNING_LOW_SATURDAY", "1") == "1"
LOW_DOWN, LOW_CALL = float(E("MORNING_LOW_DOWN", "20")), float(E("MORNING_LOW_CALL", "90"))
PROBE_S, UP_SECS = int(E("MORNING_PROBE_S", "25")), int(E("MORNING_UP_SECS", "8"))
BAND_WAIT_S = float(E("MORNING_BAND_WAIT_S", "60"))
WAKE_S, WAKE_LEAD_S = float(E("MORNING_WAKE_S", "25")), float(E("MORNING_WAKE_LEAD_S", "5"))   # keep traffic on so the NSA 5G is up
FIVE_G_WAIT_S = float(E("MORNING_5G_WAIT_S", "90"))     # after a boot the NSA leg is added ~1 min later: wait for it under wake
LINK_WAIT_S = float(E("MORNING_LINK_WAIT_S", "120"))

st = {"running": None, "stage": "", "started": None, "result": None, "legs": [], "why": ""}
common.register_busy("morning check", lambda: bool(st["running"]))


def _spec(v):
    """'3350/340' or '340@3350' -> '340@3350'; ''/no5G -> ''."""
    v = v or ""
    if "@" in v: return v
    if "/" in v: a, b = v.split("/", 1); return "%s@%s" % (b, a)
    return ""


def _nr_for(anchor):
    """The best measured 5G pair for this LTE anchor (the NR database), else 'none'."""
    best = None
    for cell, e in list((brain.REG.get("nr") or {}).items()):
        a = (e.get("anchors") or {}).get(anchor)
        if not a: continue
        q = brain.pair_quality(a, anchor)
        if best is None or q > best[0]: best = (q, cell)
    return _spec(best[1]) if best else "none"


def _known_pair(spec):
    """Is this LTE cell a known anchor of a 5G cell (the pair database)?"""
    return any(spec in (e.get("anchors") or {}) for e in list((brain.REG.get("nr") or {}).values()))


def _wait_link(timeout=LINK_WAIT_S):
    dev = devices.current()
    try: dev.ready(timeout=timeout)
    except Exception: pass
    try: brain.wait_online(timeout=min(180, timeout))
    except Exception: pass


def _has5g(n0):
    """The 5G leg was up during the probe: of the samples added since the probe started (`n0` = len(brain.HIST) then), at least one
    with a real 5G cell. The probe's traffic raises the NSA leg; an idle release after it is not 'no 5G', and the previous leg's
    samples are before `n0` (never counted)."""
    rows = list(brain.HIST)[n0:]
    return any(r.get("nr_cell") not in (None, "", "no5G") and "255" not in str(r.get("nr_cell")).split("/") for r in rows)


def _q(l):
    return brain.quality_values(100.0 if l.get("call_ok") == 1 else (l.get("call_ok") or 0),
                                l.get("loss_pct"), l.get("lat_p95"), l.get("jitter_ms"))


def _leg(name, lte, nr, band=None, role=""):
    """One leg: lock the pair (one reboot), wait for the link, internet-test -> the result dict (+`has5g` from the probe window)."""
    t0 = time.time(); res = {"leg": name, "band": band, "role": role, "lte": lte or "none", "nr": nr or "none",
                             "time": stamp(), "ok": False, "error": "", "has5g": False}
    try:
        dev = devices.current()
        r = dev.locks_apply(lte or "none", nr or "none")
        if r is None: raise RuntimeError("the lock write was not accepted")
        _wait_link()
        n0 = len(brain.HIST)
        stop = threading.Event()                          # keep traffic on so the NSA 5G leg is up for the probe + the gate
        threading.Thread(target=brain.wake, kwargs={"seconds": FIVE_G_WAIT_S + WAKE_LEAD_S + PROBE_S + 5, "stop": stop},
                         daemon=True).start()
        t = time.monotonic()                              # the 5G is added ~1 min after a boot: wait for a sample, not a guess
        while time.monotonic() - t < FIVE_G_WAIT_S and not _has5g(n0): time.sleep(2)
        time.sleep(WAKE_LEAD_S)
        net = brain.internet_test(seconds=PROBE_S)
        stop.set()
        h = brain.row_health(dict(list(brain.HIST)[-1]) if brain.HIST else {})
        res.update({"ok": net.get("down_mbps") is not None or net.get("call_ok") == 1, "down_mbps": net.get("down_mbps"),
                    "call_ok": net.get("call_ok"), "loss_pct": net.get("loss_pct"), "lat_p95": net.get("lat_p95"),
                    "jitter_ms": net.get("jitter_ms"), "health": h, "has5g": _has5g(n0)})
    except Exception as e:
        res["error"] = repr(e)
    res["seconds"] = round(time.time() - t0, 1); st["legs"].append(res)
    log("morning %s: ok=%s 5g=%s down=%s call=%s p95=%s %s" % (name, res["ok"], res["has5g"], res.get("down_mbps"),
                                                              res.get("call_ok"), res.get("lat_p95"), res.get("error")))
    return res


def _discover(band):
    """Band lock (no reboot): the band's serving cell (#1) and the best neighbour (#2: a known 5G pair first, then RSRP); the full
    band list back. The low cells' PCI changes are why this runs every time (never a cached cell list)."""
    wall, mono = time.time(), time.monotonic()
    out = {"band": band, "serving": "", "second": "", "nr": "", "error": ""}
    if not acs.tr069_set([(bandscan.PARAM, "xsd:string", band)]):
        out["error"] = "LockLTEBand=%s not accepted" % band; return out
    try:
        rows = []
        while time.monotonic() - mono < BAND_WAIT_S:
            rows = [r for r in list(brain.HIST) if brain.ts(r) >= wall - 1 and r.get("lte_cell")
                    and brain.band_of(r["lte_cell"].split("/")[0]) == "B" + band]
            if rows: break
            time.sleep(2)
        s = bandscan.summarize(band, rows)
        out.update(serving=s.get("serving") or "", nr=s.get("nr_cell") or "", rsrp=s.get("rsrp"), sinr=s.get("sinr"))
        first = _spec(out["serving"])
        nb = {}
        for t in (s.get("neighbours") or "").split():
            k, _, v = t.partition(":"); v = brain.f(v)
            if "/" in k and v is not None: nb[k] = v
        chosen = next((k for k, v in sorted(nb.items(), key=lambda kv: (_known_pair(_spec(kv[0])), kv[1]), reverse=True)
                       if _spec(k) != first), None)
        out["second"] = _spec(chosen) if chosen else ""
        return out
    except Exception as e:
        out["error"] = repr(e); return out
    finally:
        acs.tr069_set([(bandscan.PARAM, "xsd:string", bandscan.ALL)])


def _bands(low):
    """The day bands always; the low bands only when `low` (Saturday / a bad baseline / manual)."""
    return list(BANDS) + (list(LOW) if low else [])


def _ab(legs):
    """A = the best 5G #1, B = the best 5G #2 (the #2 becomes its band's #1 when #1 had no 5G); the no-lock is the baseline.
    The slots are the discovery's: #1 = the serving, #2 = the backup (a band with one 5G cell contributes A only).
    -> (a, b, best, restored_lte, restored_nr)."""
    per, firsts, seconds = {}, [], []
    for l in legs:
        if l.get("band") and l.get("role") in ("first", "second") and l.get("ok") and l.get("has5g"):
            per.setdefault(l["band"], {})[l["role"]] = l
    for d in per.values():
        first = d.get("first") or d.get("second")          # the band's primary 5G: its #1, else its #2
        second = d.get("second") if d.get("first") else None
        if first: firsts.append(first)
        if second: seconds.append(second)
    a = max(firsts, key=_q) if firsts else None
    b = max((x for x in seconds if not a or x.get("lte") != a.get("lte")), key=_q) if seconds else None
    nl = next((l for l in legs if l.get("leg") == "no lock"), None)
    cand = [x for x in (nl, a, b) if x and x.get("ok") and x.get("has5g")]
    if not cand: return a, b, None, "none", "none"
    best = max(cand, key=_q)
    if nl and _q(nl) == _q(best): best = nl                    # the no-lock is the default on a tie
    if best is nl: return a, b, nl, "none", "none"
    return a, b, best, best.get("lte"), best.get("nr")


def _msg(r):
    out = ["Morning check (%s): %s" % (r.get("trigger"), r.get("verdict"))]
    for x in r.get("legs") or []:
        if not x.get("ok"):
            out.append("%s: no internet%s" % (x.get("leg"), " (" + x["error"] + ")" if x.get("error") else "")); continue
        out.append("%s: %s Mbit/s, call %s, p95 %s, jitter %s, %s%s" % (
            x.get("leg"), x.get("down_mbps"), x.get("call_ok"), x.get("lat_p95"), x.get("jitter_ms"),
            "5G" if x.get("has5g") else "no 5G", ", up %s" % x.get("iperf_up") if x.get("iperf_up") else ""))
    a, b = r.get("a") or {}, r.get("b") or {}
    out.append("A: %s (5G %s), B: %s (5G %s)" % (a.get("lte", "-"), "yes" if a.get("has5g") else "no",
                                                 b.get("lte", "-"), "yes" if b.get("has5g") else "no"))
    rs = r.get("restored") or {}
    out.append("restored: %s / %s%s" % (rs.get("lte"), rs.get("nr"), ", up %s Mbit/s" % r.get("up") if r.get("up") else ""))
    return "\n".join(out)


def _run(low=None, notify=True, trigger="manual"):
    t0 = time.time(); st["legs"] = []
    a = b = best = None; rl = rn = "none"; up = None
    try:
        _leg("no lock", "none", "none")
        lw = low
        if lw is None:                                        # auto: Saturday, or the high baseline is bad
            lw = bool(LOW_SAT and time.localtime().tm_wday == 5)
            nl = st["legs"][0]
            if not lw and nl.get("ok") and nl.get("has5g"):
                call = 100.0 if nl.get("call_ok") == 1 else (nl.get("call_ok") or 0)
                lw = (nl.get("down_mbps") or 0) < LOW_DOWN or call < LOW_CALL
        st["stage"] = "discover"
        cands = []                                            # ALL discoveries first, while no cell lock is held: a cell lock
        for band in _bands(lw):                               # refuses the next band lock (2026-11-21 live: B3/B7/B8/B20 refused)
            d = _discover(band)
            if d.get("error") or not d.get("serving"):
                log("morning B%s: %s" % (band, d.get("error") or "no serving cell")); continue
            cands.append(d)
        for d in cands:
            band = d["band"]; st["stage"] = "band %s" % band
            _leg("B%s #1 %s" % (band, d["serving"]), _spec(d["serving"]),
                 _spec(d["nr"]) if d.get("nr") and d["nr"] != "no5G" else _nr_for(_spec(d["serving"])), band=band, role="first")
            if d.get("second"):
                _leg("B%s #2 %s" % (band, d["second"]), d["second"], _nr_for(d["second"]), band=band, role="second")
        st["stage"] = "restore"
        a, b, best, rl, rn = _ab(st["legs"])
        state({"lte_lock": ({"cell": rl, "state": "active", "reason": "morning: the best 5G"} if rl != "none" else None),
               "nr_lock": ({"cell": rn, "state": "active", "reason": "morning: the best 5G"} if rn != "none" else None)})
        devices.current().locks_apply(rl, rn); _wait_link(120)
        if best is not None:                                  # the persisted winner's upload (the modem's iperf3, 1 stream)
            _d, up, how = captest.measure(secs=UP_SECS)
            log("morning restore upload: %s" % how)
    except Exception as e:
        st["why"] = "%s failed: %r" % (st["why"], e); log("morning check failed: %r" % e)
    finally:
        legs = list(st["legs"])
        h = next((x.get("health") for x in legs if x.get("health")), {})
        res = {"trigger": st["why"], "time": stamp(), "started": st["started"], "seconds": round(time.time() - t0, 1),
               "legs": legs, "a": a or {}, "b": b or {}, "restored": {"lte": rl, "nr": rn}, "up": up,
               "any_5g": any(x.get("has5g") for x in legs),
               "verdict": "modem good" if any(x.get("ok") for x in legs) else ("modem bad" if h.get("sim") is False else "provider issue")}
        st["result"] = res; state({"morning": res})
        if notify:
            try: alert.report(_msg(res))
            except Exception as e: log("morning telegram failed: %r" % e)
        event({"event": "morning check", "to": res["verdict"], "why": "%s: %d legs, restored %s/%s" % (
            res["trigger"], len(legs), rl, rn)})
        st["running"] = None; st["stage"] = "done"


def run(low=None, trigger="manual", notify=True):
    """Start the morning check -> (started, why). Busy while it runs (the guard/captest/night wait). A manual run counts as the
    day's check (no scheduled catch-up right after it)."""
    if st["running"]: return False, "busy: %s" % st["running"]
    state({"morning_last": time.strftime("%Y-%m-%d", time.localtime())})
    st.update(running=trigger, result=None, started=stamp(), legs=[], why=trigger, stage="start")
    threading.Thread(target=_run, args=(low, notify, trigger), daemon=True).start()
    return True, trigger


def status():
    """Read-only: the running check or the last result (for GET /morning)."""
    return {"running": st["running"], "stage": st["stage"], "trigger": st["why"],
            "result": st["result"] or state().get("morning")}


def maybe_morning(row=None, now=None):
    """With each sample: run the check once a day at/after MORNING (a missed morning runs as the first thing: `morning_last` is
    only set when it starts). No idle wait - it reboots, so it is for the gym window."""
    now = now or time.time()
    if not MORNING or common.busy() or not common.clock_ok(): return False
    hhmm = time.strftime("%H:%M", time.localtime(now))
    if hhmm < MORNING or (MORNING_END and hhmm >= MORNING_END): return False
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if state().get("morning_last") == day: return False
    state({"morning_last": day})
    log("morning check: starting (MORNING=%s)" % MORNING)
    return run(low=None, trigger="scheduled")[0]
