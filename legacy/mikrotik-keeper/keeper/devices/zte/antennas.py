"""ZTE antenna modes (n78 only: antennas 5 / 6 omni or directional) - kept after every boot, and the antenna sweep.

The ZTE puts its antennas back to omni on every reboot (auto-select off survives); TR-069 has no antenna settings, so the keeper
logs in to the web page once ~60 s after a boot, sets the kept mode, logs out.
Sweep: the 4 modes, each SWEEP_SETTLE_S settle + SWEEP_READS readings; winner = best median 5G SINR on the cell most modes saw,
kept (AUTO=on) only if >= SWEEP_MIN_GAIN_DB better than the current mode.
The mode applied at boot is `effective()`: the LOCKED 5G cell's newest good lock-test mode when AUTO=on (a dryrun only names it), else
the kept / env mode - so a per-cell mode never overrides the default silently, and it ends when the cell is unlocked.
`sweep_for_test` is the NR lock test's sweep: per mode a short download + the latency probe (the internet test), then the mode with
the BEST INTERNET QUALITY is left set for the step - call-ready, loss, latency, jitter first, signal only breaks a tie - and the
current mode is replaced only on a clear win (brain.clearly_better). It never changes the kept (boot) mode.

Env: ANT_AUTO (0), ANT5 (1), ANT6 (4) - 1 omni, 4 directional; SWEEP_DAYS (7, 0 = on demand only), SWEEP_SETTLE_S (10),
SWEEP_READS (20), SWEEP_GAP_S (1), SWEEP_MIN_GAIN_DB (1), INTTEST_S (30 - the lock test's per-mode internet test window),
WAKE_S (60 - wake traffic per mode when the target 5G cell is not up), SWEEP_MAX_NR_SWITCHES (3 - 5G changes that make a mode
unstable), ANT_SET_TRIES (3) / ANT_SET_RETRY_S (2 - retries for the antenna goforms, re-login between),
ANT_READY_TIMEOUT_S (300 - wait for the web after a boot), SWEEP_READY_TIMEOUT_S (120 - wait for the web before a sweep).
"""
import threading, time

from keeper import brain, common, locktest
from keeper.common import E, event, log, record, stamp, state
from keeper.devices.zte.web import Web, web_lock, ready

AUTO, ANT5, ANT6 = E("ANT_AUTO", "0"), E("ANT5", "1"), E("ANT6", "4")
SWEEP_DAYS = float(E("SWEEP_DAYS", "7"))
SWEEP_SETTLE, SWEEP_READS, SWEEP_GAP = float(E("SWEEP_SETTLE_S", "10")), int(E("SWEEP_READS", "20")), float(E("SWEEP_GAP_S", "1"))
SWEEP_MIN_GAIN = float(E("SWEEP_MIN_GAIN_DB", "1"))
ANT_READY_TIMEOUT_S, SWEEP_READY_TIMEOUT_S = float(E("ANT_READY_TIMEOUT_S", "300")), float(E("SWEEP_READY_TIMEOUT_S", "120"))
SWEEP_MODES = [("1", "1"), ("4", "1"), ("1", "4"), ("4", "4")]          # (ant5, ant6): 1 omni, 4 directional
INTTEST_S = float(E("INTTEST_S", "30"))     # the lock test sweep: per-mode download + latency window (see brain.internet_test)
SWEEP_COLS = ["time", "trigger", "mode", "nr_cell", "sinr_p50", "rsrp_p50", "rx0_p50", "rx1_p50", "lte_sinr_p50", "quality_p50",
              "lat_p50", "lat_p95", "jitter_ms", "loss_pct", "call_ok", "down_mbps", "inttest", "lte_switches", "nr_switches", "note"]
sweep_st = {"running": False}
common.register_busy("antenna sweep", lambda: sweep_st["running"], fast=False)


def ant_cfg():
    """Antenna mode to keep: the one kept from the panel / sweep (state.json `antennas`) if any, else the env."""
    return state().get("antennas") or {"auto": AUTO, "ant5": ANT5, "ant6": ANT6}


def keep(want, src="set from the panel"):
    """API: keep `{"auto", "ant5", "ant6"}` (re-applied at every boot); None = back to the env. Returns the mode now kept.
    `src` says who kept it (the panel / the keeper's auto-apply) - the log and the `antennas kept` event carry it."""
    state({"antennas": want})
    log("antennas kept: %s (%s)" % (ant_cfg(), "env" if want is None else src))
    event({"event": "antennas kept", "to": str(ant_cfg()), "why": "" if want is None else src})
    return ant_cfg()


def mode_name(a5, a6):
    """`('4', '1')` -> `'ant5 dir / ant6 omni'`."""
    return "ant5 %s / ant6 %s" % ("dir" if a5 == "4" else "omni", "dir" if a6 == "4" else "omni")


def effective():
    """The mode to put on the modem: the locked 5G cell's newest good lock-test mode when AUTO=on, else the kept / env mode.
    Returns (mode, source, record); with AUTO=dryrun the record is returned but the kept / env mode is what gets applied."""
    kept = ant_cfg()
    w = state().get("nr_lock") or {}
    cell = w.get("cell") if w.get("state") in ("active", "pending") else None
    rec = locktest.cell_mode(cell) if cell and brain.AUTO in ("on", "dryrun") else None
    if rec and brain.AUTO == "on":
        return {"auto": "0", "ant5": rec["ant5"], "ant6": rec["ant6"]}, "cell %s (measured %s)" % (cell, rec["time"][:10]), rec
    return kept, ("kept" if state().get("antennas") else "env"), rec


def cell_info():
    """The locked 5G cell's learned mode for /status (None when no compatible cell is locked or there is no record)."""
    w = state().get("nr_lock") or {}
    return locktest.cell_mode(w.get("cell")) if w.get("state") in ("active", "pending") and w.get("cell") else None


def apply():
    """Log in, set the effective mode (the locked cell's learned mode with AUTO=on, else the kept / env mode), log out.
    None on success, else the reason."""
    a = effective()[0]; w = Web()
    err = w.login()
    if err: return err
    bad = w.set_ant(a["auto"], a["ant5"], a["ant6"])
    w.logout()
    return "apply failed: %s" % bad if bad else None


def apply_after_boot(uptime, keep=None):
    """Thread: wait until the ZTE has been up 60 s AND its web answers (a rebooted CPE needs minutes), then set the antennas
    (5 tries, 30 s apart). `keep` (callable) is checked after the wait and before every try: the driver passes its boot generation,
    so a worker whose boot has been superseded by a newer boot stands down instead of applying the old mode."""
    time.sleep(max(0, 60 - uptime))
    if not ready(timeout=ANT_READY_TIMEOUT_S):
        log("antennas: the ZTE web did not answer within %d s - the mode was not applied (set it on the web page)" % ANT_READY_TIMEOUT_S)
        return
    for i in range(5):
        if keep and not keep():
            log("antennas: a newer boot superseded this apply - standing down"); return
        with web_lock:
            try: err = apply()
            except Exception as e: err = "web error %r" % e
        if not err:
            a, src, rec = effective()
            name = "auto-select" if a["auto"] == "1" else mode_name(a["ant5"], a["ant6"])
            log("applied: %s (auto=%s ant5=%s ant6=%s, %s)" % (name, a["auto"], a["ant5"], a["ant6"], src))
            event({"event": "applied", "to": "%s, %s" % (name, src)})
            if rec and brain.AUTO != "on":                 # AUTO=dryrun: name the record, apply nothing from it
                log("AUTO=dryrun: 5G cell %s was measured with %s on %s (heading %s) - kept %s" % (
                    rec["cell"], rec["mode"], rec["time"], rec["heading"] or "?", name))
            return
        log(err)
        if "wrong hash" in err or "env" in err: return
        time.sleep(30)


READ_FIELDS = "nr5g_pci,nr5g_action_channel,Z5g_SINR,Z5g_rsrp,5g_rx0_rsrp,5g_rx1_rsrp,lte_snr,signal_quality,lte_pci,wan_active_channel"
WAKE_S = float(E("WAKE_S", "60"))           # the lock test: wake traffic this long when the target 5G cell is not up
SWEEP_MAX_NR_SWITCHES = int(E("SWEEP_MAX_NR_SWITCHES", "3"))   # 5G cell changes in one mode -> that mode is unstable
ANT_SET_TRIES, ANT_SET_RETRY_S = int(E("ANT_SET_TRIES", "3")), float(E("ANT_SET_RETRY_S", "2"))


def _read(w):
    """One web read (the fields the sweep uses)."""
    return w.get(READ_FIELDS)


def _cells(rd):
    """Web reads -> the 5G cells they saw (`ARFCN/PCI`)."""
    return ["%s/%d" % (x.get("nr5g_action_channel", ""), int(x["nr5g_pci"], 16)) for x in rd if x.get("nr5g_pci")]


def _last_cell(rd):
    """The cell of the newest web read (`no5G` when it saw none)."""
    c = _cells(rd[-1:]) if rd else []
    return c[0] if c else "no5G"


def _cell_of(x):
    """One web read -> its 5G cell (`ARFCN/PCI`) or `no5G`."""
    return "%s/%d" % (x.get("nr5g_action_channel", ""), int(x["nr5g_pci"], 16)) if x.get("nr5g_pci") else "no5G"


def _target_ok(rd, expected, n=3):
    """Is the target 5G cell up in the last `n` reads? (`expected` None = any 5G cell, the unlocked baseline.) One network handover
    must not look like 'the cell is gone': only when the last few reads all miss it is the cell considered absent."""
    cells = [_cell_of(x) for x in rd[-n:]]
    return any((c != "no5G") if expected is None else (c == expected) for c in cells)


def _set_mode(w, a5, a6):
    """Set one antenna mode, retrying ANT_SET_TRIES times: a browser page can take the single ZTE web session, and each retry logs
    in again (which takes it back). Returns "" or the list of goforms the ZTE refused (the exact reason)."""
    bad = []
    for i in range(max(1, ANT_SET_TRIES)):
        try: bad = w.set_ant("0", a5, a6)
        except Exception as e: bad = ["web error %r" % e]
        if not bad: return ""
        log("antenna set failed (%d/%d, %s): %s" % (i + 1, ANT_SET_TRIES, mode_name(a5, a6), bad))
        if i + 1 < ANT_SET_TRIES:
            time.sleep(ANT_SET_RETRY_S)
            try:
                err = w.login()
                if err: log("re-login before the retry failed: " + err)
            except Exception as e: log("re-login before the retry failed: %r" % e)
    return bad


def _row(trigger, a5, a6, rd, bad="", net=None, internet=False, extra="", no_target=False, set_failed=False):
    """One measured mode -> a sweep row (SWEEP_COLS + `_m` / `_no_target` / `_set_failed`)."""
    cells = _cells(rd)
    cell = max(set(cells), key=cells.count) if cells else "no5G"
    nr_sw = sum(1 for a, b in zip(cells, cells[1:]) if a != b)          # 5G serving-cell changes in this mode
    g = lambda k: brain.med([brain.f(x.get(k)) for x in rd if brain.f(x.get(k)) not in (None, -140.0)])
    lte = {("%s/%s" % (x.get("wan_active_channel", ""), x.get("lte_pci", ""))) for x in rd if x.get("wan_active_channel") and x.get("lte_pci")}
    sw = max(0, len(lte) - 1) if (internet and lte) else ""
    note = [("set failed: %s" % bad) if bad else "",
            ("5G cell switched %d times in this mode (unstable)" % nr_sw) if (internet and nr_sw >= SWEEP_MAX_NR_SWITCHES) else
            ("cell changed during the test" if (not internet and len(set(cells)) > 1) else ""),
            ("LTE anchor switched during the mode (%d)" % sw) if isinstance(sw, int) and sw else "", extra]
    row = {"time": stamp(), "trigger": trigger, "mode": mode_name(a5, a6), "nr_cell": cell, "sinr_p50": g("Z5g_SINR"),
           "rsrp_p50": g("Z5g_rsrp"), "rx0_p50": g("5g_rx0_rsrp"), "rx1_p50": g("5g_rx1_rsrp"), "lte_sinr_p50": g("lte_snr"),
           "quality_p50": g("signal_quality"), "nr_switches": nr_sw, "note": "; ".join(x for x in note if x),
           "_m": (a5, a6), "_no_target": no_target, "_set_failed": set_failed}
    if internet and net is not None:
        row.update({"lat_p50": net.get("lat_p50", ""), "lat_p95": net.get("lat_p95", ""), "jitter_ms": net.get("jitter_ms", ""),
                    "loss_pct": net.get("loss_pct", ""), "call_ok": net.get("call_ok", ""), "down_mbps": net.get("down_mbps", ""),
                    "inttest": net.get("error") or "ok", "lte_switches": sw})
    elif not internet:
        row["lat_p95"] = brain.latency_row(SWEEP_READS * SWEEP_GAP + 2).get("lat_p95", "")
    return row


def _measure(w, trigger, internet=False, expected=None):
    """The 4 modes on an open web session. internet=True is the lock test: per mode set the mode (with retries), check the target 5G
    cell over a few reads, wake the NSA leg (WAKE_S of small ping traffic) when it is absent, and run the internet test only when the
    cell is there. A mode the ZTE refuses to set (`_set_failed`), where the target never appears (`_no_target`), or where the 5G cell
    switches SWEEP_MAX_NR_SWITCHES times is invalid. Returns (rows, abort_reason) - the abort is only a hard web error."""
    rows = []
    log("antenna sweep (%s) started - the ZTE web page is logged out for ~%d s" % (
        trigger, len(SWEEP_MODES) * (SWEEP_SETTLE + (WAKE_S if internet else 0) + max(INTTEST_S if internet else 0, SWEEP_READS * SWEEP_GAP))))
    for a5, a6 in SWEEP_MODES:
        bad = _set_mode(w, a5, a6)
        if bad:                                                # refused after the retries: the exact goform list goes in the row
            if not internet: return rows, "the ZTE web login was taken over during %s (set failed: %s)" % (mode_name(a5, a6), bad)
            rows.append(_row(trigger, a5, a6, [], bad=", ".join(bad), internet=internet, set_failed=True))
            continue
        time.sleep(SWEEP_SETTLE)
        rd, net, ev, th = [], None, None, None
        for i in range(3 if internet else 1):                  # a few reads: a single network handover is not "the cell is gone"
            try: rd.append(_read(w))
            except Exception: pass
            if internet and i < 2: time.sleep(SWEEP_GAP)
        if not rd or not any(x.get("Z5g_SINR") or x.get("lte_snr") for x in rd):
            if not internet: return rows, "the ZTE web login was taken over during %s (no readings)" % mode_name(a5, a6)
            rows.append(_row(trigger, a5, a6, rd, internet=internet, extra="no readings after the mode was set", set_failed=True))
            continue
        if internet and not _target_ok(rd, expected):          # the target cell is not up: wake the NSA leg and look again
            extra = ""
            if WAKE_S > 0:
                ev = threading.Event()
                threading.Thread(target=brain.wake, args=(WAKE_S,), kwargs={"stop": ev}, daemon=True).start()
                t0 = time.time()
                while time.time() - t0 < WAKE_S:
                    try: rd.append(_read(w))
                    except Exception: pass
                    if _target_ok(rd, expected): break
                    time.sleep(2)
                ev.set()
                if not _target_ok(rd, expected):
                    extra = "target NR cell %s not seen after the wake (%d s)" % (expected or "any 5G", WAKE_S)
            else:
                extra = "target NR cell %s not seen" % (expected or "any 5G")
            if extra:
                rows.append(_row(trigger, a5, a6, rd, internet=internet, extra=extra, no_target=True))
                continue
        if internet:                                           # the link is up: the internet test runs with the signal reads
            net, ev = {}, threading.Event()
            th = threading.Thread(target=lambda: net.update(brain.internet_test(INTTEST_S, stop=ev)), daemon=True); th.start()
        for i in range(SWEEP_READS):
            try: rd.append(_read(w))
            except Exception: pass
            time.sleep(SWEEP_GAP)
        if th: th.join(INTTEST_S + 5)
        if not any(x.get("Z5g_SINR") or x.get("lte_snr") for x in rd):
            if ev: ev.set()
            if not internet: return rows, "the ZTE web login was taken over during %s (no readings)" % mode_name(a5, a6)
            rows.append(_row(trigger, a5, a6, rd, internet=internet, extra="no readings during the mode", set_failed=True))
            continue
        rows.append(_row(trigger, a5, a6, rd, net=net, internet=internet))
    return rows, None


def _restore(w, cur):
    """Put the configured mode back on an open web session; returns the reason when even that failed."""
    if not w.set_ant(cur["auto"], cur["ant5"], cur["ant6"]):
        return ""
    w = Web(); err = w.login()
    if err or w.set_ant(cur["auto"], cur["ant5"], cur["ant6"]): return "; RESTORING %s FAILED - set it on the ZTE page" % mode_name(cur["ant5"], cur["ant6"])
    return ""


def antenna_sweep(trigger):
    """Measure the 4 modes on the web page (~2 min, the browser's ZTE session is logged out meanwhile); keep the winner with AUTO=on.
    Rows go to `antenna_sweeps`. Returns the verdict text, "aborted: ..." or None."""
    if sweep_st["running"]: return None
    sweep_st["running"] = True
    rows = []
    try:
        with web_lock:
            if not ready(timeout=SWEEP_READY_TIMEOUT_S):
                log("antenna sweep: the ZTE web is not reachable"); return "aborted: the ZTE web is not reachable"
            w = Web(); err = w.login()
            if err: log("antenna sweep: " + err); return None
            cur = ant_cfg()
            rows, aborted = _measure(w, trigger)
            if aborted:                                # nothing measured is trustworthy: put the configured mode back, record nothing
                aborted += _restore(w, cur)
                w.logout(); log("antenna sweep aborted: " + aborted); event({"event": "antenna sweep (%s)" % trigger, "to": "aborted", "why": aborted})
                return "aborted: " + aborted
            # winner: best median 5G SINR on the cell most modes saw (RSRP breaks ties); modes on another cell don't count
            main = max(set(r["nr_cell"] for r in rows), key=lambda c: sum(1 for r in rows if r["nr_cell"] == c))
            valid = [r for r in rows if r["nr_cell"] == main and r["sinr_p50"] is not None and not r["note"]]
            best = max(valid, key=lambda r: (r["sinr_p50"], r["rsrp_p50"] or -999)) if valid else None
            now = next((r for r in valid if r["_m"] == (cur["ant5"], cur["ant6"])), None)
            kept = cur
            if best and cur["auto"] == "0" and (now is None or best["sinr_p50"] - now["sinr_p50"] >= SWEEP_MIN_GAIN) and best["_m"] != (cur["ant5"], cur["ant6"]):
                text = "%s on %s: SINR %s vs current %s %s" % (best["mode"], main, best["sinr_p50"], mode_name(cur["ant5"], cur["ant6"]),
                                                               now["sinr_p50"] if now else "n/a")
                if brain.AUTO == "on":
                    kept = {"auto": "0", "ant5": best["_m"][0], "ant6": best["_m"][1]}
                    state({"antennas": kept}); verdict = "switched to " + text
                else:
                    verdict = "would switch (dryrun) to " + text
            else:
                verdict = "kept %s (best on %s: %s)" % (mode_name(cur["ant5"], cur["ant6"]) if cur["auto"] == "0" else "auto-select",
                                                       main, ("%s SINR %s" % (best["mode"], best["sinr_p50"])) if best else "n/a")
            w.set_ant(kept["auto"], kept["ant5"], kept["ant6"])
            w.logout()
        for r in rows:
            r.pop("_m"); record("antenna_sweeps", SWEEP_COLS, r)
        state({"last_sweep": time.time()})
        log("antenna sweep: " + verdict)
        event({"event": "antenna sweep (%s)" % trigger, "to": verdict,
               "why": " | ".join("%s: %s SINR %s RSRP %s%s" % (r["mode"], r["nr_cell"], r["sinr_p50"], r["rsrp_p50"],
                                                               " (%s)" % r["note"] if r["note"] else "") for r in rows)})
        return verdict
    except Exception as e:
        log("antenna sweep failed: %r" % e)
        return None
    finally:
        sweep_st["running"] = False


def _q(r):
    """One measured mode -> the decision tuple (internet quality first, call-ready in % so the margins work like the cell rows)."""
    ok = r.get("call_ok")
    return brain.quality_values(None if ok in (None, "") else float(ok) * 100, r.get("loss_pct"), r.get("lat_p95"),
                                r.get("jitter_ms"), r.get("sinr_p50"), r.get("rsrp_p50"))


def sweep_for_test(expected, trigger="lock test"):
    """The NR lock test's sweep: measure the 4 modes with the internet test and leave the best-QUALITY mode set for the step.
    Only modes that saw `expected` (`ARFCN/PCI`; None = the cell most modes saw, for the unlocked baseline) count. The current mode is
    replaced only on a clear win (`brain.clearly_better`), so a noisy window does not flip it; the kept (boot) mode is NOT changed -
    the lock test keeps the winner's mode only when it auto-applies that cell.
    Returns {"mode","ant5","ant6","sinr","rsrp","call_ok","loss_pct","lat_p95","jitter_ms","down_mbps","cell",
             "quality_mode","switched","aborted","why","skip"}: when the sweep could not rank, `why` = the cause the row is labelled
    with - `web` (the ZTE web / goforms did not answer, so no mode could be set or read), `no5g` (the target cell never appeared),
    `switching` (it changed too often), `anchor` (the pinned LTE anchor was unstable - the step is measured, not skipped) - and
    `skip` the matching short reason."""
    miss = {"aborted": "", "mode": "", "cell": expected, "switched": False, "no_cell": False, "skip": "", "why": ""}
    if sweep_st["running"]: return dict(miss, aborted="an antenna sweep is already running", why="web", skip="an antenna sweep is already running")
    sweep_st["running"] = True
    rows = []
    try:
        with web_lock:
            if not ready(timeout=SWEEP_READY_TIMEOUT_S):
                return dict(miss, aborted="the ZTE web is not reachable", why="web", skip="the ZTE web is not reachable")
            w = Web(); err = w.login()
            if err: return dict(miss, aborted=err, why="web", skip=err)
            cur = ant_cfg()
            rows, aborted = _measure(w, trigger, internet=True, expected=expected)
            no_cell = bool(rows) and all(r.get("_no_target") for r in rows)
            web_fail = bool(rows) and all(r.get("_set_failed") for r in rows)
            sw_fail = bool(rows) and all(isinstance(r.get("nr_switches"), int) and r["nr_switches"] >= SWEEP_MAX_NR_SWITCHES for r in rows)
            why = skip = ""
            if aborted:                                  # a hard web error mid-sweep (the login was taken over / no readings)
                why, skip = "web", aborted
            else:
                main = expected
                if not main:
                    seen = [r["nr_cell"] for r in rows if r["nr_cell"] != "no5G"]
                    main = max(set(seen), key=seen.count) if seen else "no5G"
                valid = [r for r in rows if r["nr_cell"] == main and r["sinr_p50"] is not None and not r["note"]]
                if not valid:                            # all modes invalid: web control, cell switching, LTE anchor or no 5G
                    if web_fail:
                        why, skip = "web", "in every mode - the ZTE web page is logged in / not answering (goform results in the sweep rows)"
                    elif sw_fail:
                        why, skip = "switching", "in every mode (unstable)"
                    elif any(isinstance(r.get("lte_switches"), int) and r["lte_switches"] > 0 for r in rows):
                        why = "anchor"                       # the anchor was unstable: measure, the row is flagged `unstable`
                    else:
                        why = "no5g"
                        skip = (("no 5G on %s" % expected) if expected else "no 5G in any mode") + (" (wake %d s)" % WAKE_S if WAKE_S > 0 else "")
                    aborted = ("antenna control failed in every mode - the ZTE web page is logged in / not answering (goform results in the sweep rows)"
                               if why == "web" else "5G cell switching in every mode (unstable)" if why == "switching" else
                               "unstable LTE anchor during the sweep" if why == "anchor" else skip)
            if aborted:                                # the expected cell was not what the modes saw: put the configured mode back
                aborted += _restore(w, cur)
                w.logout(); log("lock test sweep: " + aborted)
                if rows:                               # a completed sweep that simply saw no 5G is still worth recording
                    for r in rows: r.pop("_m"); record("antenna_sweeps", SWEEP_COLS, r)
                return dict(miss, aborted=aborted, no_cell=no_cell if why != "anchor" else False, skip=skip, why=why)
            best = max(valid, key=_q)                  # the quality winner; the signal only breaks a quality tie
            cur_r = next((r for r in valid if r["_m"] == (cur["ant5"], cur["ant6"])), None) if cur["auto"] == "0" else None
            chosen = best if cur_r is None or brain.clearly_better(_q(best), _q(cur_r)) else cur_r
            mode = chosen["_m"]; w.set_ant("0", mode[0], mode[1])
            w.logout()
        for r in rows:
            r.pop("_m"); record("antenna_sweeps", SWEEP_COLS, r)
        state({"last_sweep": time.time()})
        switched = cur["auto"] != "0" or mode != (cur["ant5"], cur["ant6"])
        log("lock test sweep (%s): best internet %s on %s - %s %s (SINR %s, p95 %s ms, loss %s%%)" % (
            trigger, best["mode"], best["nr_cell"], "switched to" if switched else "kept", chosen["mode"], chosen["sinr_p50"],
            chosen["lat_p95"], chosen["loss_pct"]))
        return {"mode": chosen["mode"], "ant5": mode[0], "ant6": mode[1], "sinr": chosen["sinr_p50"], "rsrp": chosen["rsrp_p50"],
                "call_ok": chosen["call_ok"], "loss_pct": chosen["loss_pct"], "lat_p95": chosen["lat_p95"], "jitter_ms": chosen["jitter_ms"],
                "down_mbps": chosen["down_mbps"], "cell": chosen["nr_cell"], "quality_mode": best["mode"], "switched": switched, "aborted": "",
                "no_cell": False, "skip": "", "why": ""}
    except Exception as e:
        log("lock test sweep failed: %r" % e)
        return dict(miss, aborted="sweep failed: %r" % e, why="web", skip="sweep failed: %r" % e)
    finally:
        sweep_st["running"] = False


def start_sweep():
    """API: run a sweep now (in a thread). False if one is running."""
    if sweep_st["running"]: return False
    threading.Thread(target=antenna_sweep, args=("manual",), daemon=True).start()
    return True


def maybe_sweep(row):
    """Minute job: scheduled sweep every SWEEP_DAYS when no job runs, the ZTE is up > 15 min and the link is idle."""
    if not SWEEP_DAYS or common.busy() or time.time() - state().get("last_sweep", 0) < SWEEP_DAYS * 86400: return False
    if (brain.f(row.get("uptime_s")) or 0) < 900 or not brain.idle(row): return False
    state({"last_sweep": time.time()})          # don't start twice
    threading.Thread(target=antenna_sweep, args=("scheduled",), daemon=True).start()
    return True
