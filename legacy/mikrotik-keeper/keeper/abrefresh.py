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
SCAN_MAX_S = float(E("REFRESH_SCAN_MAX_S", "1800"))     # the 6-band scan's worst case ~ 6 x (2 x BANDSCAN_WAIT_S + BANDSCAN_TEST_S)
SMOKE_MAX_S = float(E("REFRESH_SMOKE_MAX_S", "2400"))   # five legs + the best-found restore
st = {"running": None, "stage": "", "started": None, "result": None, "why": ""}


def _wait(stage, running, limit):
    """Wait for one stage; a stage still running after its ceiling aborts the chain (never start the next one on its lock)."""
    t0 = time.monotonic()
    while running() and time.monotonic() - t0 < limit: time.sleep(2)
    if running(): raise RuntimeError("%s still running after %d s: not starting the next stage" % (stage, limit))


def _bands(run):
    """This run's row per band (`run` = the band scan's stamp). No run / no row on a band = not scanned: never an older scan's row
    presented as this run's (an aborted scan would otherwise print stale bands as fresh)."""
    if not run: return []
    rows = store.rows(DATA, "band_scans"); out = []
    for b in SCAN_BANDS:
        r = next((x for x in reversed(rows) if str(x.get("band")) == b and x.get("run") == run), None)
        if r: out.append({k: r.get(k) for k in ("band", "serving", "nr_cell", "nr_arfcn", "call_ok", "lat_p95", "jitter_ms", "down_mbps", "note")})
    return out


def _msg(res):
    lines = ["A/B refresh (%s): band scan + smoke" % res.get("trigger")]
    for b in res.get("band_scan") or []:
        if not b.get("serving"):                       # no data on the band: say why, never print a row of blanks
            note = b.get("note") or "no service"
            pre = "no service on B%s" % b.get("band")
            if note.startswith(pre): note = "no service" + note[len(pre):]
            lines.append("B%s: %s" % (b.get("band"), note)); continue
        lines.append("B%s: %s + %s, call %s, p95 %s, jitter %s, %s Mbit/s" % (
            b.get("band"), b.get("serving"), b.get("nr_cell") or "nr cell missing", b.get("call_ok"), b.get("lat_p95"),
            b.get("jitter_ms"), b.get("down_mbps")))
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
    t0 = time.monotonic(); sm = None; run = ""
    try:
        ok, why = bandscan.start(list(SCAN_BANDS))
        if not ok: raise RuntimeError("band scan refused: %s" % why)
        run = bandscan.st.get("run") or ""              # set synchronously by start(): exactly this scan's rows
        _wait("band scan", lambda: bandscan.st.get("running"), SCAN_MAX_S)
        st["stage"] = "smoke"
        ok, why = absmoke.run("refresh", notify=False)
        if not ok: raise RuntimeError("smoke refused: %s" % why)
        _wait("smoke", lambda: absmoke.st.get("running"), SMOKE_MAX_S)
        sm = absmoke.st.get("result") or {}
    except Exception as e:
        st["why"] = "%s failed: %r" % (st["why"], e); log("ab refresh failed: %r" % e)
    finally:
        try:
            res = {"trigger": st["why"], "time": stamp(), "started": st["started"], "seconds": round(time.monotonic() - t0, 1),
                   "band_scan": _bands(run), "smoke": sm}
            st["result"] = res; state({"ab_refresh": res})
            try: alert.report(_msg(res))
            except Exception as e: log("ab refresh telegram failed: %r" % e)
            event({"event": "ab refresh", "to": "done", "why": "%s: %d bands, smoke %s" % (
                res["trigger"], len(res["band_scan"]), (sm or {}).get("verdict"))})
        finally:
            st.update(stage="done", running=None)


def status():
    return {"running": st["running"], "stage": st["stage"], "result": st["result"] or state().get("ab_refresh")}
