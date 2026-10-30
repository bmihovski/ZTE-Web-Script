"""Health-check scenarios ("self-tests") - read-only, fast, result pushed to Telegram. On demand (POST /checks) or on a daily
schedule (SELFTEST_AT). These are NOT the lock tests, which reboot the modem (see locktest / POST /lock-test); each check here only
reads live state and returns ok / warn / fail.

Scenarios (ENUM): clock, link, sqm, lock, night; "all" runs them in order.
Env: SELFTEST_AT ("09:45=all,21:00=link": run a scenario each day at that local time and push it; empty = none).
API: GET /checks (scenarios + last result), POST /checks?name=all|clock|link|sqm|lock|night (comma-separated too).
"""
import threading, time

from keeper import alert, brain, common, guard, locktest, sqm, store
from keeper.common import DATA, E, log, state

st = {"last": None}


def _last_sample():
    try: return brain.HIST[-1] if brain.HIST else (store.rows(DATA, "samples", last=1) or [None])[0]
    except Exception: return None


def chk_clock(now):
    if not common.clock_ok(): return "fail", "clock NOT verified (modem skew %s)" % common.clock.get("skew")
    return "ok", "verified (skew %+.0f s)" % (common.clock.get("skew") or 0)


def chk_link(now):
    r = _last_sample()
    if not r: return "warn", "no sample yet"
    on5g = r.get("nr_cell") not in ("", "no5G", None)
    sinr = brain.f(r.get("nr_sinr")) if on5g else brain.f(r.get("lte_sinr"))
    p95 = brain.f(r.get("lat_p95")); bad = []
    if sinr is not None and sinr < brain.MIN_SINR: bad.append("SINR %s < %g" % (sinr, brain.MIN_SINR))
    if p95 is not None and p95 > brain.CALL_P95: bad.append("lat p95 %s > %g" % (p95, brain.CALL_P95))
    if str(r.get("call_ok")) == "0": bad.append("not call-ready")
    txt = "5G %s / LTE %s, SINR %s, lat_p95 %s" % (r.get("nr_cell"), r.get("lte_cell"), sinr, p95)
    return ("warn" if bad else "ok"), txt + (" - " + "; ".join(bad) if bad else "")


def chk_sqm(now):
    if not sqm.URL: return "warn", "off (no SQM_URL)"
    if sqm.st.get("error"): return "fail", "error: %s" % sqm.st["error"]
    s = sqm.st.get("last")
    if not s: return "warn", "no tick yet"
    lim = s["limit"]; idle = s["rate"]["down"] < 0.1 * (lim["down"] or sqm.MAX["down"])
    if idle and lim["down"] and lim["down"] < 0.6 * sqm.MAX["down"]:
        return "warn", "down limit %g < %g Mbit while idle (stuck low?)" % (lim["down"], sqm.MAX["down"])
    return "ok", "up %g / down %s Mbit, delay %s ms" % (lim["up"], lim["down"] or "unlim", s.get("delay_ms"))


def chk_lock(now):
    w = state().get("lte_lock")
    if not w: return "ok", "no LTE lock set"
    r = _last_sample() or {}
    if w.get("state") == "active":
        on = (r.get("lte_cell") or "") in guard.lock_target(w["cell"])
        return ("ok" if on else "warn"), "%s active, on-target=%s (now %s)" % (w["cell"], on, r.get("lte_cell"))
    if w.get("state") == "released":
        inpause = bool(guard.pause_end(now))
        return ("ok" if inpause else "warn"), "%s released%s (%s)" % (w["cell"], " in LOCK_PAUSE" if inpause else " - should restore", w.get("reason", ""))
    return "warn", "%s state=%s" % (w["cell"], w.get("state"))


def chk_night(now):
    evs = store.rows(DATA, "events", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 12 * 3600)))
    blob = lambda e: e.get("event", "") + " " + (e.get("why") or "") + " " + (e.get("to") or "")
    rel = [e for e in evs if "lock removed" in blob(e) and "LOCK_PAUSE" in blob(e)]
    res = [e for e in evs if e.get("event", "").startswith(("lte lock", "5g lock")) and "restored" in blob(e)]
    ns = [e for e in evs if e.get("event") == "night screen"]
    bad = (locktest.lt["running"] is not None) or (bool(rel) and not res)
    txt = "LOCK_PAUSE release=%s restore=%s, night-screen=%s" % ("y" if rel else "n", "y" if res else "n", "ran" if ns else "none")
    return ("warn" if bad else "ok"), txt + (" - lock test STILL RUNNING" if locktest.lt["running"] else "")


CHECKS = {"clock": ("router clock verified", chk_clock), "link": ("5G/LTE serving, SINR + latency ok", chk_link),
          "sqm": ("SQM limits healthy / not stuck low", chk_sqm), "lock": ("guarded LTE/5G lock sane", chk_lock),
          "night": ("last 12 h: LOCK_PAUSE + night screen", chk_night)}
ORDER = ["clock", "link", "sqm", "lock", "night"]
RANK = {"ok": 0, "warn": 1, "fail": 2}


def run(names, now=None, push=True):
    """Run scenarios, push one Telegram message, return the result. names: list, or ['all']."""
    now = now or time.time()
    names = ORDER if ("all" in names or not names) else [n for n in ORDER if n in names]
    out, worst = [], "ok"
    for n in names:
        try: status, line = CHECKS[n][1](now)
        except Exception as e: status, line = "fail", "check error: %r" % e
        if RANK[status] > RANK[worst]: worst = status
        out.append({"name": n, "status": status, "detail": line})
    tag = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    text = "%s self-test: %s\n" % (common.device_name(), tag[worst]) + "\n".join("[%s] %s: %s" % (tag[r["status"]], r["name"], r["detail"]) for r in out)
    st["last"] = {"time": common.stamp(), "worst": worst, "checks": out}
    log("self-test %s (%s)" % (worst, " ".join("%s=%s" % (r["name"], r["status"]) for r in out)))
    if push: alert.report(text)
    return st["last"]


AT = E("SELFTEST_AT", "")


def _scheduler():
    fired = {}
    while True:
        time.sleep(20)
        now = time.time(); hm = time.strftime("%H:%M", time.localtime(now)); day = time.strftime("%Y-%m-%d", time.localtime(now))
        for part in (p for p in AT.split(",") if "=" in p):
            t, name = (x.strip() for x in part.split("=", 1))
            if t == hm and fired.get(t) != day:
                fired[t] = day; log("scheduled self-test %s=%s" % (t, name)); run([name], now)


def start():
    """Start the daily scheduler thread if SELFTEST_AT is set."""
    if AT.strip(): threading.Thread(target=_scheduler, daemon=True).start(); log("self-test schedule: %s" % AT)
