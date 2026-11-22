"""On-demand comparison of the current, alternate, and unlocked uplink. Always restore the managed locks."""
import threading
import time

from keeper import VERSION, abplan, acs, alert, brain, common, devices, guard
from keeper.common import E, event, log, stamp, state

BOOT_S = float(E("AB_SMOKE_BOOT_S", "180"))
st = {"running": None, "progress": None, "result": None, "cancel": None, "retry_at": 0.0}
common.register_busy("ab smoke", lambda: bool(st["running"]))


def _spec(v):
    """Convert a sample's channel/PCI to a lock command."""
    if "/" not in (v or ""): return ""
    channel, pci = v.split("/", 1)
    return "%s@%s" % (pci, channel)


def _last_sample():
    try: return brain.HIST[-1]
    except IndexError: return {}


def _switch(lte, nr, stop):
    """Only a new BOOT inform proves that a ZTE lock was applied."""
    dev = devices.current()
    boots = acs.st["boots"]
    if dev.locks_apply(lte, nr) is None:
        raise RuntimeError("the lock write was not accepted")
    if not dev.LOCKS_REBOOT:
        return
    deadline = time.monotonic() + BOOT_S
    while acs.st["boots"] == boots and time.monotonic() < deadline:
        # Finish an in-flight reboot before attempting the restore even when the operator requests Stop.
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    if acs.st["boots"] == boots:
        raise RuntimeError("no boot inform within %g s" % BOOT_S)
    if not stop.is_set() and not dev.ready(timeout=25, stop=stop):
        raise RuntimeError("modem web interface unavailable after boot")


def _fresh_sample(mark, stop, timeout=45):
    deadline = time.monotonic() + timeout
    while not stop.is_set():
        row = _last_sample()
        if row is not mark and row.get("time") and 0 <= time.time() - brain.ts(row) < 90:
            return row
        remaining = deadline - time.monotonic()
        if remaining <= 0: break
        stop.wait(min(1, remaining))
    return {}


def _leg(name, lte, nr, switch, stop):
    mark = _last_sample()
    res = {"leg": name, "lte": lte, "nr": nr, "time": stamp(), "ok": False, "measured": False, "error": ""}
    try:
        if switch:
            _switch(lte, nr, stop)
            st["progress"] = dict(st["progress"], phase="waiting for radio")
        row = _fresh_sample(mark if switch else None, stop)
        if stop.is_set(): return res
        actual_lte = row.get("lte_cell")
        if lte != "none" and actual_lte != brain.anchor_key(lte):
            raise RuntimeError("no fresh observation on the requested LTE anchor")
        st["progress"] = dict(st["progress"], phase="measuring")
        net = brain.internet_test(seconds=25, stop=stop)
        if stop.is_set(): return res
        row = _last_sample() if row else row
        actual_lte, actual_nr = row.get("lte_cell"), row.get("nr_cell")
        if lte != "none" and actual_lte != brain.anchor_key(lte):
            raise RuntimeError("LTE anchor changed during the measurement")
        ok = (brain.f(net.get("down_mbps")) or 0) > 0 or (
            brain.f(net.get("loss_pct")) is not None and float(net["loss_pct"]) < 100)
        measured = ok or brain.f(net.get("loss_pct")) == 100
        res.update({k: net.get(k) for k in ("down_mbps", "call_ok", "loss_pct", "lat_p95", "jitter_ms")})
        res.update(ok=ok, measured=measured, observed_lte=actual_lte, observed_nr=actual_nr,
                   health=brain.row_health(row) if row else None,
                   error="" if measured else (net.get("error") or "no fresh probe evidence"))
    except Exception as e:
        res["error"] = str(e)
    log("ab smoke %s: ok=%s down=%s %s" % (name, res["ok"], res.get("down_mbps"), res["error"]))
    return res


def _nr_for(anchor, row):
    """Prefer the highest quality observed NSA pair. Leave NR free for an unproven anchor."""
    key = brain.anchor_key(anchor)
    pairs = [(brain.pair_quality(a, key), cell) for cell, e in list((brain.REG.get("nr") or {}).items())
             if (a := (e.get("anchors") or {}).get(key))]
    if pairs: return _spec(max(pairs)[1])
    if row.get("lte_cell") == key: return _spec(row.get("nr_cell")) or "none"
    return "none"


def _classify(legs):
    reachable = [x for x in legs if x.get("ok")]
    measured = [x for x in legs if x.get("measured")]
    current = next((x for x in measured if x["leg"] == "current lock"), None)
    unlocked = next((x for x in measured if x["leg"] == "no lock"), None)
    if current and not current["ok"] and reachable:
        verdict = "configuration suspected"
    elif reachable:
        verdict = "uplink reachable"
    elif any((x.get("health") or {}).get("sim") is False for x in measured):
        verdict = "SIM or modem fault suspected"
    elif unlocked and (unlocked.get("health") or {}).get("registered"):
        verdict = "mobile uplink suspected"
    else:
        verdict = "inconclusive"
    advice = {
        "configuration suspected": "The current lock failed while another configuration reached the internet.",
        "uplink reachable": "Keeper probes reached the internet. Check the LAN or PC if the problem persists there.",
        "SIM or modem fault suspected": "The modem reported a SIM fault during a measured leg.",
        "mobile uplink suspected": "Fresh probes failed even with locks released and the modem registered.",
        "inconclusive": "The test did not establish a working uplink or a mobile-network failure.",
    }
    return {"verdict": verdict, "detail": advice[verdict], "any_internet": bool(reachable)}


def _msg(result):
    lines = ["A/B smoke (%s): %s" % (result["trigger"], result["verdict"])]
    for leg in result["legs"]:
        text = "down %s Mbit/s, loss %s%%, p95 %s ms" % (leg.get("down_mbps"), leg.get("loss_pct"), leg.get("lat_p95"))
        lines.append("%s: %s" % (leg["leg"], text if leg["measured"] else leg["error"]))
    if result["restore_error"]: lines.append("Restore pending: " + result["restore_error"])
    return "\n".join(lines)


def _plan(original):
    lte, nr = original
    plan = [("current lock", lte, nr)] if (lte, nr) != ("none", "none") else []
    row = _last_sample()
    high = [(cell, _nr_for(cell, row)) for cell in abplan.high() if cell != lte]
    plan += [(name, *pair) for name, pair in zip(("A high", "B high"), high)]
    plan.append(("no lock", "none", "none"))
    plan += [(name, *pair) for name, pair in zip(("A low", "B low"), abplan.low()) if pair != original]
    return plan


def _restore(pair):
    started = time.time()
    _switch(*pair, threading.Event())
    if not brain.wait_online(timeout=60, since=started):
        log("ab smoke: locks restored, link has not recovered yet")
    state({"ab_smoke_restore": None})


def _run(original, notify):
    started = stamp()
    t0 = time.monotonic()
    stop = st["cancel"]
    trigger = st["running"]
    legs = []
    restored, restore_error = None, ""
    changed = False
    try:
        plan = _plan(original)
        for step, (name, lte, nr) in enumerate(plan, 1):
            if stop.is_set(): break
            switch = changed or (lte, nr) != original
            st["progress"] = {"step": step, "total": len(plan), "leg": name, "phase": "switching" if switch else "measuring",
                              "lte": lte, "nr": nr, "legs": list(legs)}
            if switch and not changed:
                state({"ab_smoke_restore": list(original)})
                changed = True
            result = _leg(name, lte, nr, switch, stop)
            if not stop.is_set(): legs.append(result)
    except Exception as e:
        log("ab smoke failed: %r" % e)
    finally:
        st["progress"] = dict(st["progress"] or {}, phase="restoring", legs=list(legs))
        try:
            if changed: _restore(original)
            restored = dict(zip(("lte", "nr"), original))
        except Exception as e:
            restore_error = str(e)
            st["retry_at"] = time.monotonic() + 60
            log("ab smoke restore pending: %r" % e)
        try:
            summary = _classify(legs)
            if stop.is_set(): summary.update(verdict="stopped", detail="Stopped by the operator.")
            res = dict(summary, trigger=trigger, time=stamp(), started=started, version=VERSION,
                       seconds=round(time.monotonic() - t0, 1), legs=legs, restored=restored, restore_error=restore_error)
            st["result"] = res
            state({"ab_smoke": res})
            if notify:
                try: alert.report(_msg(res))
                except Exception as e: log("ab smoke telegram failed: %r" % e)
            event({"event": "ab smoke", "to": res["verdict"], "why": "%s: %d legs" % (trigger, len(legs))})
        finally:
            with common.test_start_lock:
                st.update(running=None, progress=None, cancel=None)


def run(trigger="manual", notify=True):
    """Reserve the modem before publishing a run so other probes defer."""
    with common.test_start_lock:
        saved = state()
        why = common.busy()
        if why: return False, "busy: " + why
        if (saved.get("quality_ab") or {}).get("phase") in ("trial", "keep"):
            return False, "automatic quality A/B is active"
        if saved.get("ab_smoke_restore"): return False, "previous smoke restore is pending"
        locks = [saved.get(k) or {} for k in ("lte_lock", "nr_lock")]
        if any(lock.get("state") == "pending" for lock in locks):
            return False, "managed locks are pending"
        original = tuple(lock["cell"] if lock.get("state") == "active" else "none" for lock in locks)
        st.update(running=trigger, progress={"phase": "planning", "legs": []}, result=None, cancel=threading.Event())
        threading.Thread(target=_run, args=(original, notify), daemon=True).start()
    return True, trigger


def stop():
    with common.test_start_lock:
        cancel = st["cancel"]
        if not cancel: return False
        cancel.set()
    return True


def recover(row):
    """Restore an interrupted smoke after a keeper restart or a failed restore."""
    if time.monotonic() < st["retry_at"]: return
    with common.test_start_lock:
        pair = state().get("ab_smoke_restore")
        if not pair or common.busy() or not guard.quiet(row=row): return
        st.update(running="recovery", progress={"phase": "restoring", "legs": []})
        st["retry_at"] = time.monotonic() + 60
    def work():
        try: _restore(pair)
        except Exception as e: log("ab smoke restore pending: %r" % e)
        finally:
            with common.test_start_lock: st.update(running=None, progress=None)
    threading.Thread(target=work, daemon=True).start()


def status():
    current = st.copy()
    saved = state()
    return {"running": current["running"], "progress": current["progress"],
            "stopping": bool(current["cancel"] and current["cancel"].is_set()),
            "restore_pending": bool(saved.get("ab_smoke_restore")), "result": current["result"] or saved.get("ab_smoke")}
