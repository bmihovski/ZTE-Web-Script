"""On-demand comparison of the current, alternate, and unlocked uplink. Always restore the managed locks."""
import threading
import time

from keeper import VERSION, abplan, acs, alert, brain, captest, common, devices, guard
from keeper.common import E, event, log, stamp, state

BOOT_S = float(E("AB_SMOKE_BOOT_S", "180"))
st = {"running": None, "progress": None, "plan": None, "result": None, "cancel": None, "retry_at": 0.0}
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
    method = dev.locks_apply(lte, nr)
    if method is None:
        raise RuntimeError("the lock write was not accepted")
    if method == "unchanged" or not dev.LOCKS_REBOOT:
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
        profile = getattr(devices.current(), "profile", None)
        target = profile.snapshot()["raw"] if profile else None
        started = time.time()
        net = brain.internet_test(seconds=25, stop=stop)
        if stop.is_set(): return res
        if profile and profile.low_band(lte):
            if target[profile.BAND] != profile.low_band(lte):
                raise RuntimeError("requested LTE band restriction missing")
            proof = profile.transfer_evidence([(started, time.time())], target, lte, nr)
            res["radio"] = proof
            if not proof["verified"]:
                raise RuntimeError("missing matching band and serving pair during load")
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


def _antenna_load(profile, target, lte, mode, server, nr="none"):
    """Use the same transfer windows, probes and radio checks as a loaded lock comparison."""
    result = dict(zip(("ant5", "ant6"), mode))
    windows = []
    try:
        if not captest.wait_lan_quiet():
            raise RuntimeError("LAN traffic did not quiet")
        seconds = max(captest.ISECS, 2 * acs.FAST_INTERVAL + 5)
        try:
            down, up, how = captest.measure(secs=seconds, load_windows=windows, server=server)
        except captest.LocalTraffic as exc:
            result["load_retries"] = [str(exc)]
            if not captest.wait_lan_quiet():
                raise
            windows.clear()
            down, up, how = captest.measure(secs=seconds, load_windows=windows, server=server)
        complete = len(windows) == 2
        radio = profile.transfer_evidence(windows, target, lte, nr, "n78")
        radio["verified"] = complete and radio["verified"]
        rows = radio["observations"]
        signals = {}
        for row in rows:
            cell = row["nr_cell"]
            if brain.nr_band_of(cell.split("/")[0]) == "n78":
                signals.setdefault(cell, []).append(row)
        cells = [r["nr_cell"] for r in rows]
        config_ok = all(r["boots"] == acs.st["boots"] and profile.matches(r, target)
                        and (lte == "none" or r["lte_cell"] == brain.anchor_key(lte)) for r in rows)
        config_ok = complete and config_ok and all(radio["samples_per_transfer"]) and profile.config_matches(target, lte, nr)
        result.update(down_mbps=down, up_mbps=up, load_how=how,
                      radio=radio, config_verified=config_ok, transfer_windows=windows,
                      nr_switches=sum(a != b for a, b in zip(cells, cells[1:])),
                      n78_samples=sum(len(group) for group in signals.values()),
                      nr_signal={cell: dict(samples=len(group), **{
                          k: brain.med([brain.f(r.get(k)) for r in group])
                          for k in ("nr_sinr", "nr_rsrp", "nr_rsrq")})
                          for cell, group in signals.items()})
        if complete:
            result.update(brain.probe_windows(windows, min_points=2))
        else:
            result["error"] = "iperf3 did not complete both transfers (%s)" % how
    except RuntimeError as exc:
        result["error"] = str(exc)
    return result


def _antenna_profile(name, lte, stop, reverse=False, nr="none"):
    dev = devices.current()
    profile = dev.profile
    target = profile.snapshot()["raw"]
    if not profile.config_matches(target, lte, nr):
        raise RuntimeError("LTE/NR configuration does not match the antenna trial")
    modes = []
    order = dev.antennas.SWEEP_MODES
    with dev.antennas.trial_session() as set_mode:
        for mode in reversed(order) if reverse else order:
            if stop.is_set():
                break
            st["progress"] = dict(st["progress"], phase=dev.antennas.mode_name(*mode), modes=list(modes))
            try:
                set_mode(*mode)
            except RuntimeError as exc:
                modes.append({"ant5": mode[0], "ant6": mode[1], "error": str(exc)})
                continue
            if stop.wait(dev.antennas.SWEEP_SETTLE):
                break
            modes.append(_antenna_load(profile, target, lte, mode, captest.IPERFS[0], nr=nr))
    return {"leg": name, "lte": lte, "nr": nr, "modes": modes}


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


def _managed_pair(saved):
    locks = (saved.get(k) or {} for k in ("lte_lock", "nr_lock"))
    return tuple(lock["cell"] if lock.get("state") == "active" else "none" for lock in locks)


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
        raise RuntimeError("locks restored, link has not recovered yet")
    state({"ab_smoke_restore": None})


def _run(original, notify, antenna_lte=None, ui=False):
    started = stamp()
    t0 = time.monotonic()
    stop = st["cancel"]
    trigger = st["running"]
    legs = []
    restored, restore_error, error = None, "", ""
    changed = False
    current_sweep = antenna_lte == "current"
    test_name = "Loaded n78 sweep" if current_sweep else "Loaded n78 comparison" if antenna_lte else "A/B smoke"
    scope = None
    profile = getattr(devices.current(), "profile", None)
    try:
        if profile:
            scope_args = {"antennas": devices.current().antennas.effective()[0]} if antenna_lte else {}
            if ui:
                scope_args["web_notice"] = test_name
            scope = profile.begin_scope("smoke", **scope_args)
            original = tuple(profile.spec(scope["raw"][k]) for k in (profile.LTE, profile.NR))
        if ui:
            st["progress"] = {"phase": "Close the ZTE apps now · starting in %d seconds" % alert.WEB_CLOSE_S, "legs": []}
            alert.web_test_start(test_name)
            if stop.wait(alert.WEB_CLOSE_S):
                if scope:
                    profile.finish_scope(commit={})
                    scope = None
                return
        if current_sweep:
            lte, nr = original
            if profile.low_band(lte) or (nr != "none" and brain.nr_band_of(nr.split("@")[1]) != "n78"):
                raise RuntimeError("the current locks restrict the modem to a low LTE band or non-n78 NR cell")
            plan = [("Current profile", lte, nr)]
        elif antenna_lte:
            plan = [("no lock", "none", "none"), ("LTE lock", antenna_lte, "none")]
        else:
            plan = _plan(original)
        st["plan"] = plan
        for step, (name, lte, nr) in enumerate(plan, 1):
            if stop.is_set(): break
            switch = changed or (lte, nr) != original or bool(profile and profile.low_band(lte))
            st["progress"] = {"step": step, "total": len(plan), "leg": name, "phase": "switching" if switch else "measuring",
                              "lte": lte, "nr": nr, "legs": list(legs)}
            if switch and not changed:
                if not scope: state({"ab_smoke_restore": list(original)})
                changed = True
            if antenna_lte:
                if switch:
                    _switch(lte, nr, stop)
                result = _antenna_profile(name, lte, stop, reverse=step > 1, nr=nr)
            else:
                result = _leg(name, lte, nr, switch, stop)
            if not stop.is_set(): legs.append(result)
    except Exception as e:
        error = str(e)
        log("ab smoke failed: %r" % e)
    finally:
        st["progress"] = dict(st["progress"] or {}, phase="restoring", legs=list(legs))
        try:
            if scope: profile.finish_scope()
            elif changed: _restore(original)
            restored = dict(zip(("lte", "nr"), original))
        except Exception as e:
            restore_error = str(e)
            st["retry_at"] = time.monotonic() + 60
            log("ab smoke restore pending: %r" % e)
        try:
            if antenna_lte:
                expected = 1 if current_sweep else 2
                summary = {"verdict": "incomplete" if error or restore_error or len(legs) != expected else "measured",
                           "error": error, "anchor": original[0] if current_sweep else antenna_lte}
            else:
                summary = _classify(legs)
            if stop.is_set(): summary.update(verdict="stopped", detail="Stopped by the operator.")
            res = dict(summary, trigger=trigger, time=stamp(), started=started, version=VERSION,
                       seconds=round(time.monotonic() - t0, 1), legs=legs, restored=restored, restore_error=restore_error)
            if antenna_lte:
                report = _antenna_report(res)
                res["verdict"] = report["verdict"]
                state({"antenna_sweep_loaded" if current_sweep else "antenna_ab": res})
            else:
                st["result"] = res
                state({"ab_smoke": res})
            if notify or ui:
                try:
                    if antenna_lte:
                        scored = sum(m["score"] is not None for leg in report["legs"] for m in leg["modes"])
                        msg = "%s finished (%s). %d/%d modes scored." % (test_name, res["verdict"], scored, 4 * expected)
                        if restore_error:
                            msg += "\nRestore pending: " + restore_error
                        elif error:
                            msg += "\n" + error
                    else:
                        msg = _msg(res)
                    if ui:
                        alert.web_test_end(msg, restore_pending=bool(restore_error))
                    else:
                        alert.report(msg)
                except Exception as e: log("ab smoke telegram failed: %r" % e)
            event({"event": "ab smoke", "to": res["verdict"], "why": "%s: %d legs" % (trigger, len(legs))})
        finally:
            with common.test_start_lock:
                st.update(running=None, progress=None, plan=None, cancel=None)


def run(trigger="manual", notify=True, antenna_lte=None, ui=False):
    """Reserve the modem before publishing a run so other probes defer."""
    with common.test_start_lock:
        saved = state()
        if antenna_lte and not getattr(devices.current(), "profile", None):
            return False, "antenna trials require the ZTE modem"
        if antenna_lte and (not captest.IPERFS or not captest.sqm.URL):
            return False, "antenna trials require iperf and WAN traffic checks"
        why = common.busy()
        if why: return False, "busy: " + why
        if (saved.get("quality_ab") or {}).get("phase") in ("trial", "keep"):
            return False, "automatic quality A/B is active"
        if saved.get("ab_smoke_restore"): return False, "previous smoke restore is pending"
        locks = [saved.get(k) or {} for k in ("lte_lock", "nr_lock")]
        if any(lock.get("state") == "pending" for lock in locks):
            return False, "managed locks are pending"
        original = _managed_pair(saved)
        st.update(running=trigger, progress={"phase": "planning", "legs": []}, plan=None, result=None, cancel=threading.Event())
        threading.Thread(target=_run, args=(original, notify and not antenna_lte, antenna_lte, ui), daemon=True).start()
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


def _antenna_report(trial):
    """Report loaded scores, or the reason a mode is unscored, and use them to determine completeness."""
    if not trial:
        return None
    legs = []
    for leg in trial.get("legs", []):
        modes = []
        for mode in leg["modes"]:
            score = None
            error = mode.get("error")
            if not error:
                if not mode.get("config_verified"):
                    error = "LTE/NR configuration or in-transfer readback unverified"
                elif not (mode.get("radio") or {}).get("verified"):
                    error = ("n78 serving path unverified during transfers" if mode.get("n78_samples")
                             else "no n78 observed during transfers")
                else:
                    channels = " ".join(cell.split("/")[0] for cell in mode.get("nr_signal", {}))
                    scored = brain.lt_score(dict(mode, nr_arfcn=channels))
                    score = None if scored["partial"] else scored["score"]
                    if score is None:
                        error = scored["gate"] or "incomplete loaded quality or throughput"
            row = dict(mode, score=score)
            if error:
                row["error"] = error
            modes.append(row)
        legs.append(dict(leg, modes=modes))
    report = dict(trial, legs=legs)
    if report.get("verdict") == "measured" and (not legs or any(
            len(leg["modes"]) != 4 or any(mode["score"] is None for mode in leg["modes"]) for leg in legs)):
        report["verdict"] = "incomplete"
    return report


def status():
    current = st.copy()
    saved = state()
    managed = _managed_pair(saved)
    quality = saved.get("quality_ab") or {}
    sample = _last_sample()
    if current["running"]:
        profile = "Smoke in progress"
    elif quality.get("phase") in ("trial", "keep"):
        profile = "Automatic quality A/B · " + ("alternate site" if quality.get("arm") == "site" else "unlocked")
    elif managed != ("none", "none"):
        profile = "Current lock"
    elif any((saved.get(k) or {}).get("state") == "pending" for k in ("lte_lock", "nr_lock")):
        profile = "Lock pending"
    else:
        profile = "Unlocked"
    active = {"profile": profile, "lte_lock": managed[0], "nr_lock": managed[1],
              "lte": _spec(sample.get("lte_cell")), "nr": _spec(sample.get("nr_cell"))}
    plan = (current["plan"] or []) if current["running"] else _plan(managed)
    profile = getattr(devices.current(), "profile", None)
    return {"running": current["running"], "progress": current["progress"], "active": active,
            "plan": [dict(leg=name, lte=lte, nr=nr, lte_band=profile.low_band(lte) if profile else None) for name, lte, nr in plan],
            "stopping": bool(current["cancel"] and current["cancel"].is_set()),
            "restore_pending": bool(saved.get("ab_smoke_restore") or (saved.get("profile_restore") or {}).get("owner") == "smoke"),
            "result": current["result"] or saved.get("ab_smoke"), "antenna_trial": _antenna_report(saved.get("antenna_ab")),
            "antenna_sweep": _antenna_report(saved.get("antenna_sweep_loaded"))}
