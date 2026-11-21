"""One-call refresh (2026-11-21): the full band scan, then the full A/B smoke, then ONE Telegram with both.

The band scan refreshes the B1/B3/B7/B8/B20/B28 rows and the NR database; the smoke then uses the refreshed low rows and the
lock-test A/B scores (A = the max scored, B = the next) and restores the best found. About 25 min. Manual (the wife can run it with
one request, POST /ab-refresh) - not the automatic A/B. The smoke's own Telegram is suppressed in the chain so there is exactly one
message; GET /ab-refresh shows the chain state/result and GET /ab embeds it.
"""
import threading, time

from keeper import absmoke, alert, bandscan, store
from keeper.common import DATA, E, event, log, state, stamp

SCAN_BANDS = [b for b in E("REFRESH_BANDS", "1,3,7,8,20,28").replace(" ", "").split(",") if b]
st = {"running": None, "stage": "", "started": None, "result": None, "why": ""}


def _bands():
    rows = store.rows(DATA, "band_scans"); out = []
    for b in SCAN_BANDS:
        r = next((x for x in reversed(rows) if str(x.get("band")) == b), None)
        if r: out.append({k: r.get(k) for k in ("band", "serving", "nr_cell", "nr_arfcn", "call_ok", "lat_p95", "jitter_ms", "down_mbps")})
    return out


def _msg(res):
    lines = ["A/B refresh (%s): band scan + smoke" % res.get("trigger")]
    for b in res.get("band_scan") or []:
        lines.append("B%s: %s + %s, call %s, p95 %s, jitter %s, %s Mbit/s" % (
            b.get("band"), b.get("serving"), b.get("nr_cell"), b.get("call_ok"), b.get("lat_p95"), b.get("jitter_ms"), b.get("down_mbps")))
    sm = res.get("smoke") or {}
    if sm:
        lines.append("smoke: %s" % sm.get("verdict"))
        for x in sm.get("legs") or []:
            lines.append("%s: %s" % (x.get("leg"), ("internet, down %s, call %s, p95 %s, jitter %s" % (
                x.get("down_mbps"), x.get("call_ok"), x.get("lat_p95"), x.get("jitter_ms"))) if x.get("ok") else "no internet"))
        r = sm.get("restored") or {}
        lines.append("restored: %s / %s" % (r.get("lte"), r.get("nr")))
    return "\n".join(lines)


def run(trigger="manual"):
    """Start the band scan -> smoke chain -> (started, why). The chain has no busy of its own (the band scan and smoke do), so
    it never blocks itself; the endpoint refuses while the chain, a band scan or a smoke is already running."""
    if st["running"]: return False, "busy: %s" % st["running"]
    if bandscan.st.get("running") or absmoke.st.get("running"): return False, "busy: band scan / smoke"
    st.update(running=trigger, stage="band scan", started=stamp(), result=None, why=trigger)
    threading.Thread(target=_run, daemon=True).start()
    return True, trigger


def _run():
    t0 = time.monotonic(); sm = None
    try:
        ok, why = bandscan.start(list(SCAN_BANDS))
        if not ok: raise RuntimeError("band scan refused: %s" % why)
        while bandscan.st.get("running") and time.monotonic() - t0 < 1200: time.sleep(2)     # the scan's own thread; ceiling only
        st["stage"] = "smoke"
        ok, why = absmoke.run("refresh", notify=False)
        if not ok: raise RuntimeError("smoke refused: %s" % why)
        while absmoke.st.get("running") and time.monotonic() - t0 < 2400: time.sleep(2)
        sm = absmoke.st.get("result") or {}
    except Exception as e:
        st["why"] = "%s failed: %r" % (st["why"], e); log("ab refresh failed: %r" % e)
    finally:
        st["running"] = None; st["stage"] = "done"
        res = {"trigger": st["why"], "time": stamp(), "started": st["started"], "seconds": round(time.monotonic() - t0, 1),
               "band_scan": _bands(), "smoke": sm}
        st["result"] = res; state({"ab_refresh": res})
        try: alert.report(_msg(res))
        except Exception as e: log("ab refresh telegram failed: %r" % e)
        event({"event": "ab refresh", "to": "done", "why": "%s: %d bands, smoke %s" % (
            res["trigger"], len(res["band_scan"]), (sm or {}).get("verdict"))})


def status():
    return {"running": st["running"], "stage": st["stage"], "result": st["result"] or state().get("ab_refresh")}
