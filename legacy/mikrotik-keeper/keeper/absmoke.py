"""A/B smoke (2026-11-21): the full five-switch test of the A/B configs, **with locks**:
A(high) -> B(high) -> no-lock -> A(low) -> B(low), then the BEST found config (or the no-lock when nothing clearly beats it).

Run at the start of the night test and the day test (`AB_SMOKE=1`), or manually (`POST /ab-smoke`; the wife runs it when critical).
Each leg: one lock change + reboot (`locks_apply`, both legs in one write), wait for the boot/web/link, the internet test; the per-leg
quality and the conclusion: `modem good` (some config has internet - check the computer/LAN), `provider issue` (the modem is
registered but no config gets data), `modem bad` (SIM/modem not ready). The smoke is NOT the automatic A/B: it is a test, busy, and
it blocks the guard/captest/night until done. The auto A/B (Mon-Fri CAPTEST slots) is a separate controller.
"""
import threading, time

from keeper import alert, brain, common, devices, guard, lowband
from keeper.common import event, log, state, stamp

st = {"running": None, "result": None, "started": None, "legs": [], "why": ""}
common.register_busy("ab smoke", lambda: bool(st["running"]))


def _spec(v):
    """'3350/340' or '340@3350' -> '340@3350'; ''/no5G -> ''."""
    v = v or ""
    if "@" in v: return v
    if "/" in v:
        a, b = v.split("/", 1)
        return "%s@%s" % (b, a)
    return ""


def _wait_link(timeout=240):
    dev = devices.current()
    try: dev.ready(timeout=timeout)
    except Exception: pass
    try: brain.wait_online(timeout=min(180, timeout))
    except Exception: pass


def _leg(name, lte, nr, seconds=25):
    """One leg: lock the pair (one reboot), wait, internet-test -> the result dict."""
    t0 = time.time(); res = {"leg": name, "lte": lte or "none", "nr": nr or "none", "time": stamp(), "ok": False, "error": ""}
    try:
        dev = devices.current()
        r = dev.locks_apply(lte or "none", nr or "none")
        if r is None: raise RuntimeError("the lock write was not accepted")
        _wait_link()
        net = brain.internet_test(seconds=seconds)
        h = brain.row_health(dict(list(brain.HIST)[-1]) if brain.HIST else {})
        res.update({"ok": net.get("down_mbps") is not None or net.get("call_ok") == 1, "down_mbps": net.get("down_mbps"),
                    "call_ok": net.get("call_ok"), "loss_pct": net.get("loss_pct"), "lat_p95": net.get("lat_p95"),
                    "jitter_ms": net.get("jitter_ms"), "health": h, "seconds": round(time.time() - t0, 1)})
    except Exception as e:
        res["error"] = repr(e)
    st["legs"].append(res)
    log("ab smoke %s: ok=%s down=%s call=%s %s" % (name, res["ok"], res.get("down_mbps"), res.get("call_ok"), res.get("error")))
    return res


def _high_a(row):
    """The previous good lock (state cell, else the serving) + its 5G (state nr_lock, else the sample's cell)."""
    l = state().get("lte_lock") or {}; cell = l.get("cell") if l.get("state") in ("active", "pending", "released") else ""
    cell = _spec(cell or row.get("lte_cell") or "")
    n = state().get("nr_lock") or {}; nr = n.get("cell") if n.get("state") in ("active", "pending", "released") else ""
    nr = _spec(nr or (row.get("nr_cell") if row.get("nr_cell") not in ("", "no5G") else ""))
    return (cell or "none"), (nr or "none")


def _high_b(row, now):
    """The best daytime other-site lock-tested anchor (`guard.ab_alt`) + its best 5G pair from the NR database."""
    cur = _high_a(row)[0]
    alt = guard.ab_alt(cur if cur != "none" else (row.get("lte_cell") or ""), row, now)
    if not alt: return None, None
    best = None
    for cell, e in (brain.REG.get("nr") or {}).items():
        a = (e.get("anchors") or {}).get(alt)
        if a:
            q = brain.pair_quality(a, alt)
            if best is None or q > best[0]: best = (q, cell)
    return alt, _spec(best[1]) if best else (_spec(row.get("nr_cell")) or "none")


def _low_legs():
    """The two low-band pairs from the last band scans (serving cell + n28), best first."""
    out = []
    for r in lowband.latest():
        s = r.get("serving") or ""; n = r.get("nr_cell") or ""
        if "/" not in s: continue
        out.append((_spec(s), _spec(n) or "none"))
    return out[:2]


def _best(legs):
    """The best found: the leg with the best quality tuple; no-lock when no lock is clearly better (all worse than no-lock) or
    the no-lock itself is the best. Returns (lte, nr) - 'none'/'none' for the no-lock."""
    q = {}
    for x in legs:
        if not x.get("ok"): continue
        q[x["leg"]] = brain.quality_values(100.0 if x.get("call_ok") == 1 else (x.get("call_ok") or 0),
                                           x.get("loss_pct"), x.get("lat_p95"), x.get("jitter_ms"))
    if not q: return "none", "none"
    nl = q.get("no lock"); best = max(q, key=lambda k: q[k])
    if best == "no lock" or (nl and not brain.clearly_better(q[best], nl)): return "none", "none"
    x = next(x for x in legs if x["leg"] == best)
    return x["lte"], x["nr"]


def _classify(legs):
    net = [x for x in legs if x.get("ok")]
    h = next((x.get("health") for x in legs if x.get("health")), {})
    if net: verdict = "modem good"
    elif h.get("sim") is False: verdict = "modem bad"
    else: verdict = "provider issue"
    return {"verdict": verdict, "any_internet": bool(net)}


def _msg(r):
    lines = ["AB smoke (%s): %s" % (r.get("trigger", "?"), r["verdict"])]
    for x in r["legs"]:
        if x.get("ok"):
            lines.append("%s: internet, down %s Mbit/s, call-ready %s, p95 %s, jitter %s" % (
                x["leg"], x.get("down_mbps"), x.get("call_ok"), x.get("lat_p95"), x.get("jitter_ms")))
        else:
            lines.append("%s: no internet%s" % (x["leg"], (" (" + x["error"] + ")") if x.get("error") else (" (" + (x.get("note") or "no data") + ")")))
    lines.append({"modem good": "the modem works in at least one config - check the computer/LAN",
                  "provider issue": "the modem is registered but no config gets data - the provider/network",
                  "modem bad": "SIM/modem not ready - the modem"}[r["verdict"]])
    return "\n".join(lines)


def _run():
    dev = devices.current(); row = dict(list(brain.HIST)[-1]) if brain.HIST else {}
    t0 = time.time(); l = n = "none"
    try:
        a = _high_a(row); _leg("A high", a[0], a[1])
        b = _high_b(row, time.time())
        if b[0]: _leg("B high", b[0], b[1])
        else: st["legs"].append({"leg": "B high", "ok": False, "note": "no other-site daytime B candidate", "time": stamp()})
        _leg("no lock", "none", "none")
        for name, (lo, no) in zip(("A low", "B low"), _low_legs()): _leg(name, lo, no)
    except Exception as e:
        st["why"] = "%s failed: %r" % (st["why"], e); log("ab smoke failed: %r" % e)
    finally:
        try:
            l, n = _best(st["legs"])                             # the best found, or the no-lock when all are worse
            state({"lte_lock": ({"cell": l, "state": "active", "reason": "ab smoke: the best found"} if l != "none" else None),
                   "nr_lock": ({"cell": n, "state": "active", "reason": "ab smoke: the best found"} if n != "none" else None)})
            dev.locks_apply(l, n); _wait_link(120)
        except Exception as e: log("ab smoke restore failed: %r" % e)
        st["running"] = None
        res = {"trigger": st["why"], "time": stamp(), "started": st["started"], "seconds": round(time.time() - t0, 1),
               "legs": list(st["legs"]), "restored": {"lte": l, "nr": n}}
        res.update(_classify(res["legs"]))
        st["result"] = res; state({"ab_smoke": res})
        try: alert.report(_msg(res))
        except Exception as e: log("ab smoke telegram failed: %r" % e)
        event({"event": "ab smoke", "to": res["verdict"], "why": "%s: %d legs" % (res["trigger"], len(res["legs"]))})


def run(trigger="manual"):
    """Start the full smoke -> (started, why). Busy while it runs (the guard/captest/night wait)."""
    if st["running"]: return False, "busy: %s" % st["running"]
    st.update(running=trigger, result=None, started=stamp(), legs=[], why=trigger)
    threading.Thread(target=_run, daemon=True).start()
    return True, trigger


def status():
    """Read-only: the running smoke or the last result (for GET /ab)."""
    return {"running": st["running"], "trigger": st["why"], "result": st["result"] or state().get("ab_smoke")}
