"""ZTE antenna modes (n78 only: antennas 5 / 6 omni or directional) - kept after every boot, and the antenna sweep.

The ZTE puts its antennas back to omni on every reboot (auto-select off survives); TR-069 has no antenna settings, so the keeper
logs in to the web page once ~60 s after a boot, sets the kept mode, logs out.
Sweep: the 4 modes, each SWEEP_SETTLE_S settle + SWEEP_READS readings; winner = best median 5G SINR on the cell most modes saw,
kept (AUTO=on) only if >= SWEEP_MIN_GAIN_DB better than the current mode.

Env: ANT_AUTO (0), ANT5 (1), ANT6 (4) - 1 omni, 4 directional; SWEEP_DAYS (7, 0 = on demand only), SWEEP_SETTLE_S (10),
SWEEP_READS (20), SWEEP_GAP_S (1), SWEEP_MIN_GAIN_DB (1).
"""
import threading, time

from keeper import brain, common
from keeper.common import E, event, log, record, stamp, state
from keeper.devices.zte.web import Web, web_lock

AUTO, ANT5, ANT6 = E("ANT_AUTO", "0"), E("ANT5", "1"), E("ANT6", "4")
SWEEP_DAYS = float(E("SWEEP_DAYS", "7"))
SWEEP_SETTLE, SWEEP_READS, SWEEP_GAP = float(E("SWEEP_SETTLE_S", "10")), int(E("SWEEP_READS", "20")), float(E("SWEEP_GAP_S", "1"))
SWEEP_MIN_GAIN = float(E("SWEEP_MIN_GAIN_DB", "1"))
SWEEP_MODES = [("1", "1"), ("4", "1"), ("1", "4"), ("4", "4")]          # (ant5, ant6): 1 omni, 4 directional
SWEEP_COLS = ["time", "trigger", "mode", "nr_cell", "sinr_p50", "rsrp_p50", "rx0_p50", "rx1_p50", "lte_sinr_p50", "quality_p50", "lat_p95", "note"]
sweep_st = {"running": False}
common.register_busy("antenna sweep", lambda: sweep_st["running"], fast=False)


def ant_cfg():
    """Antenna mode to keep: the one kept from the panel / sweep (state.json `antennas`) if any, else the env."""
    return state().get("antennas") or {"auto": AUTO, "ant5": ANT5, "ant6": ANT6}


def keep(want):
    """API: keep `{"auto", "ant5", "ant6"}` (re-applied at every boot); None = back to the env. Returns the mode now kept."""
    state({"antennas": want})
    log("antennas kept: %s (%s)" % (ant_cfg(), "env" if want is None else "set from the panel")); event({"event": "antennas kept", "to": str(ant_cfg())})
    return ant_cfg()


def mode_name(a5, a6):
    """`('4', '1')` -> `'ant5 dir / ant6 omni'`."""
    return "ant5 %s / ant6 %s" % ("dir" if a5 == "4" else "omni", "dir" if a6 == "4" else "omni")


def apply():
    """Log in, set the kept mode, log out. None on success, else the reason."""
    a = ant_cfg(); w = Web()
    err = w.login()
    if err: return err
    bad = w.set_ant(a["auto"], a["ant5"], a["ant6"])
    w.logout()
    return "apply failed: %s" % bad if bad else None


def apply_after_boot(uptime):
    """Thread: wait until the ZTE has been up 60 s, then set the antennas (5 tries, 30 s apart)."""
    time.sleep(max(0, 60 - uptime))
    for i in range(5):
        with web_lock:
            try: err = apply()
            except Exception as e: err = "web error %r" % e
        if not err:
            a = ant_cfg(); log("applied: auto=%s ant5=%s ant6=%s" % (a["auto"], a["ant5"], a["ant6"])); event({"event": "applied"}); return
        log(err)
        if "wrong hash" in err or "env" in err: return
        time.sleep(30)


def antenna_sweep(trigger):
    """Measure the 4 modes on the web page (~2 min, the browser's ZTE session is logged out meanwhile); keep the winner with AUTO=on.
    Rows go to `antenna_sweeps`. Returns the verdict text, "aborted: ..." or None."""
    if sweep_st["running"]: return None
    sweep_st["running"] = True
    rows = []
    try:
        with web_lock:
            w = Web(); err = w.login()
            if err: log("antenna sweep: " + err); return None
            cur = ant_cfg()
            log("antenna sweep (%s) started - the ZTE web page is logged out for ~%d s" % (trigger, len(SWEEP_MODES) * (SWEEP_SETTLE + SWEEP_READS * SWEEP_GAP)))
            aborted = None
            for a5, a6 in SWEEP_MODES:
                bad = w.set_ant("0", a5, a6)
                time.sleep(SWEEP_SETTLE)
                rd = []
                for i in range(SWEEP_READS):
                    try: rd.append(w.get("nr5g_pci,nr5g_action_channel,Z5g_SINR,Z5g_rsrp,5g_rx0_rsrp,5g_rx1_rsrp,lte_snr,signal_quality"))
                    except Exception: pass
                    time.sleep(SWEEP_GAP)
                if bad or not any(x.get("Z5g_SINR") or x.get("lte_snr") for x in rd):     # the web login was taken over (an open ZTE page logs in again)
                    aborted = "the ZTE web login was taken over during %s (%s) - close the ZTE page / app, or use the userscript sweep" % (
                        mode_name(a5, a6), "set failed: %s" % bad if bad else "no readings")
                    break
                cells = ["%s/%d" % (x.get("nr5g_action_channel", ""), int(x["nr5g_pci"], 16)) for x in rd if x.get("nr5g_pci")]
                cell = max(set(cells), key=cells.count) if cells else "no5G"
                g = lambda k: brain.med([brain.f(x.get(k)) for x in rd if brain.f(x.get(k)) not in (None, -140.0)])
                rows.append({"time": stamp(), "trigger": trigger, "mode": mode_name(a5, a6), "nr_cell": cell, "sinr_p50": g("Z5g_SINR"),
                             "rsrp_p50": g("Z5g_rsrp"), "rx0_p50": g("5g_rx0_rsrp"), "rx1_p50": g("5g_rx1_rsrp"), "lte_sinr_p50": g("lte_snr"), "quality_p50": g("signal_quality"),     # the ZTE's 0-4 grade (its installer app)
                             "lat_p95": brain.latency_row(SWEEP_READS * SWEEP_GAP + 2).get("lat_p95", ""),
                             "note": ("set failed: %s" % bad) if bad else ("cell changed during the test" if len(set(cells)) > 1 else ""),
                             "_m": (a5, a6)})
            if aborted:                                # nothing measured is trustworthy: put the configured mode back, record nothing
                if w.set_ant(cur["auto"], cur["ant5"], cur["ant6"]):
                    w = Web(); err = w.login()
                    if err or w.set_ant(cur["auto"], cur["ant5"], cur["ant6"]): aborted += "; RESTORING %s FAILED - set it on the ZTE page" % mode_name(cur["ant5"], cur["ant6"])
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
