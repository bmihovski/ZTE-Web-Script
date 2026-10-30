"""LTE lock tests and the night screen (device neutral; drivers with `lock_test` / `night` in `CAN`).

Lock test: an unlocked baseline, then each candidate `PCI@EARFCN` locked (driver `lock_lte` = lock + reboot), settle, measure,
rows to `lock_tests`. The lock is ALWAYS removed at the end (night screen: the guard's lock is put back instead).
Align mode waits after each lock until you aligned the antenna (POST /lock-test-continue?heading=N); skip / stop end a step / the test.
Night screen: in NIGHT_SCREEN, after NIGHT_IDLE_MIN idle minutes, re-tests the current lock + NIGHT_CELLS (home set) + up to NIGHT_MAX
strong untested cells. Pushes the ranking to Telegram and, when AUTO=on, auto-applies the winner; traffic stops it.

Env: LOCKTEST_SETTLE_S (180), LOCKTEST_MEASURE_MIN (12), LOCKTEST_BOOT_WAIT_S (420), LOCKTEST_ALIGN_TIMEOUT_MIN (90),
NIGHT_SCREEN (02:00-05:00, off), NIGHT_MAX (3), NIGHT_IDLE_MIN (30), NIGHT_RETEST_DAYS (7), NIGHT_CELLS (always re-check, e.g. 340@3350).
"""
import collections, threading, time

from keeper import acs, alert, brain, common, devices, guard, store
from keeper.common import DATA, E, event, log, record, stamp, state

LT_SETTLE_S, LT_MEASURE_MIN = float(E("LOCKTEST_SETTLE_S", "180")), float(E("LOCKTEST_MEASURE_MIN", "12"))
LT_BOOT_WAIT_S = float(E("LOCKTEST_BOOT_WAIT_S", "420"))
LT_ALIGN_TIMEOUT_MIN = float(E("LOCKTEST_ALIGN_TIMEOUT_MIN", "90"))
LT_COLS = ["time", "lock", "minutes", "on_target_pct", "anchor", "site", "anchor_sinr_p50", "anchor_sinr_p10", "anchor_rsrp_p50", "anchor_rsrq_p50",
           "ca", "nr_cells", "nr_sinr_p50", "no5g_min", "call_ok_pct", "lat_p95", "jitter_ms", "loss_pct", "note", "heading", "mode", "run"]
NIGHT = E("NIGHT_SCREEN", "02:00-05:00")
NIGHT_MAX, NIGHT_IDLE_MIN = int(E("NIGHT_MAX", "3")), int(E("NIGHT_IDLE_MIN", "30"))
NIGHT_RETEST_DAYS = float(E("NIGHT_RETEST_DAYS", "7"))
NIGHT_CELLS = [c for c in E("NIGHT_CELLS", "").replace(" ", "").split(",") if c]   # always re-check these (home set), same weekly throttle
NIGHT_BANDS = ("B1", "B3", "B7")                       # B8 / B20 / B28 measured worse here: far, loaded, n28 5G (2026-10-27)
lt = {"running": None, "plan": [], "done": [], "align": False, "waiting": False, "heading": "", "go": threading.Event(),
      "cut": None, "cut_ev": threading.Event()}         # cut: "skip" (this step) / "stop" (whole test)
common.register_busy("lock test", lambda: lt["running"])


class Cut(Exception):
    """The current step was skipped / the test stopped."""


def lt_wait(s):
    """Sleep `s` seconds unless the step is skipped / the test stopped (then raise Cut)."""
    lt["cut_ev"].wait(s)
    if lt["cut"]: raise Cut()


def lt_cut(what):
    """API: `'skip'` this step / `'stop'` the test. False when no test is past its start."""
    if not lt["running"] or lt["running"] == "starting": return False
    lt["cut"] = what; lt["cut_ev"].set(); lt["go"].set(); return True


def hm(after_s):
    """`HH:MM` in `after_s` seconds (for the panel's phase line)."""
    return time.strftime("%H:%M", time.localtime(time.time() + after_s))


def phase(text, log_it=False):
    """What the lock test does now + until when (shown in the panel)."""
    lt["phase"] = text
    if log_it: log("lock test " + text)


def lt_eval(lock, rows, note):
    """Samples measured under one lock -> one `lock_tests` row (on-target %, anchor / 5G SINR, CA, call-ready %, latency)."""
    targets = set() if lock == "none" else guard.lock_target(lock)
    anchors = [brain.parse_ca(r.get("lte_ca")) for r in rows]
    a = [x[0] for x in anchors if x]
    main = collections.Counter(r.get("lte_cell") for r in rows).most_common(1)
    combos = collections.Counter(" + ".join("B%s/%s" % (c[0], c[2]) for c in x) for x in anchors if x).most_common(2)
    nr = collections.Counter(r["nr_cell"].split("/")[-1] for r in rows)
    sinr = [brain.f(r.get("nr_sinr")) for r in rows if r["nr_cell"] != "no5G"]
    sites = collections.Counter(r.get("lte_site") for r in rows if r.get("lte_site") and (not targets or r.get("lte_cell") in targets))
    return {"time": stamp(), "lock": lock, "minutes": len(rows), "site": sites.most_common(1)[0][0] if sites else "",
            "on_target_pct": round(100 * sum(1 for r in rows if r.get("lte_cell") in targets) / len(rows), 1) if targets and rows else "",
            "anchor": main[0][0] if main else "", "anchor_sinr_p50": brain.med([x[4] for x in a]), "anchor_sinr_p10": brain.pct([x[4] for x in a], 10),
            "anchor_rsrp_p50": brain.med([x[3] for x in a]), "anchor_rsrq_p50": brain.med([brain.f(r.get("lte_rsrq")) for r in rows]),
            "ca": "; ".join("%s (%d)" % c for c in combos), "nr_cells": " ".join("%s:%d" % c for c in nr.most_common()),
            "nr_sinr_p50": brain.med(sinr), "no5g_min": sum(1 for r in rows if brain.no5g_busy(r)), "call_ok_pct": brain.share(rows),
            "lat_p95": brain.med([brain.f(r.get("lat_p95")) for r in rows]), "jitter_ms": brain.med([brain.f(r.get("jitter_ms")) for r in rows]),
            "loss_pct": round(sum(brain.f(r.get("loss_pct")) or 0 for r in rows) / max(1, len(rows)), 2), "note": note}


def lock_test(plan, align=False, night=False):
    """Run a test (in a thread). plan: `["none", "434@1550", ...]`, one cell at a time, each with its own reboot.
    align=True: wait after each lock until the antenna is aligned; night=True: plan[0] is the current lock (baseline), rows are
    mode "night" (unloaded network: a shortlist only) and the lock is put back at the end."""
    if lt["running"]: return
    lock_lte = devices.current().lock_lte
    lt.update(running="starting", plan=list(plan), done=[], align=align, waiting=False, heading="", night=night, run=stamp()); lt["go"].clear()
    brain.ctx["hold"] = "lock test"                  # no keeper reboots meanwhile
    log("LTE lock test started: %s (~%d min)" % (", ".join(plan), len(plan) * (LT_MEASURE_MIN + LT_SETTLE_S / 60 + 2)))
    try:
        for lock in plan:
            lt["running"] = lock; lt["cut"] = None; lt["cut_ev"].clear(); start = locked = None; heading = ""
            state({"lock_test": lock})                 # if the keeper dies mid-test, the next start removes the lock
            boots = acs.st["boots"]
            phase("%s: setting the lock, rebooting (~2 min)" % lock)
            try:
                try:
                    lock_lte(lock)
                except RuntimeError as e:              # refused: record it, go on with the next one
                    res = lt_eval(lock, [], "lock not set: %s" % e); record("lock_tests", LT_COLS, res); lt["done"].append(res)
                    event({"event": "lock test %s" % lock, "to": "not set", "why": str(e)}); log("lock test %s: %s" % (lock, e))
                    continue
                t0 = time.time()
                while acs.st["boots"] == boots and time.time() - t0 < LT_BOOT_WAIT_S and not lt["cut"]: time.sleep(2)
                note = "" if acs.st["boots"] > boots else "no boot inform within %d s" % LT_BOOT_WAIT_S
                locked = time.time()
                phase("%s: %s, reconnecting until %s, then %s" % (lock, note or "rebooted with the lock", hm(LT_SETTLE_S), "you align" if align and lock != "none" else "measuring")); lt_wait(LT_SETTLE_S)
                heading = ""
                if align and lock != "none":         # the baseline is measured at the starting heading
                    lt["waiting"] = True; lt["go"].clear(); phase("%s: waiting for you to align (test stops at %s)" % (lock, hm(LT_ALIGN_TIMEOUT_MIN * 60)))
                    log("lock test: %s locked - align the antenna for it, then 'Aligned - measure now'" % lock)
                    event({"event": "lock test %s: align now" % lock, "why": "peak guide on LTE SINR; the lock stays until you continue"})
                    if not lt["go"].wait(LT_ALIGN_TIMEOUT_MIN * 60):
                        raise RuntimeError("no 'aligned' within %g min - test stopped" % LT_ALIGN_TIMEOUT_MIN)
                    lt["waiting"] = False; heading = lt["heading"]
                    lt_wait(LT_SETTLE_S / 3)
                phase("%s: measuring until %s" % (lock, hm(LT_MEASURE_MIN * 60)), True)
                start = time.time(); lt_wait(LT_MEASURE_MIN * 60)
            except Cut:                                # skip / stop from the panel or API: record what we have
                lt["waiting"] = False; what = "skipped" if lt["cut"] == "skip" else "stopped"
                since = start or locked                # skipped before measuring: keep what was seen while you aligned
                rows = [r for r in list(brain.HIST) if brain.ts(r) >= since] if since else []
                res = lt_eval(lock, rows, "%s by you%s" % (what, " after %.0f min measuring" % ((time.time() - start) / 60) if start
                                                           else " before measuring (values: while aligning)" if rows else ""))
                res.update(heading=heading if start else "", mode="cut", run=lt["run"])
                record("lock_tests", LT_COLS, res); lt["done"].append(res)
                event({"event": "lock test %s" % lock, "to": what, "why": res["note"]}); log("lock test %s: %s" % (lock, res["note"]))
                if what == "stopped": break
                continue
            rows = [r for r in list(brain.HIST) if brain.ts(r) >= start]
            res = lt_eval(lock, rows, note or ("" if rows else "no samples (no service on this cell?)"))
            res.update(heading=heading, mode="night" if night else "aligned" if heading else ("align" if align and lock != "none" else "as is"), run=lt["run"])
            record("lock_tests", LT_COLS, res); lt["done"].append(res)
            if res["anchor"] and lock != "none" and not night:          # night values (unloaded) don't go into the registry
                try:                                     # bookkeeping only - never stops the test
                    brain.REG["lte"].setdefault(res["anchor"], {"band": brain.band_of(res["anchor"].split("/")[0]), "first": time.time(), "last": time.time(),
                                                "seen_min": 0, "anchor_min": 0, "ca_min": 0})["lock_test"] = {k: res[k] for k in
                        ("time", "on_target_pct", "site", "anchor_sinr_p50", "nr_cells", "nr_sinr_p50", "call_ok_pct", "lat_p95")}
                    brain.save_registry()
                except Exception as e:
                    log("registry update failed: %r" % e)
            event({"event": "lock test %s" % lock, "to": "anchor %s SINR %s, 5G %s SINR %s, call-ready %s%%" % (res["anchor"], res["anchor_sinr_p50"],
                   res["nr_cells"], res["nr_sinr_p50"], res["call_ok_pct"]), "why": res["note"] or res["ca"]})
            log("lock test %s: %s" % (lock, res))
    except Exception as e:
        log("lock test failed: %r" % e)
    finally:
        w = state().get("lte_lock") or {}
        back = w["cell"] if night and w.get("state") == "active" else None       # night: put the guard's lock straight back (one reboot)
        try:
            lock_lte(back); log("lock test: %s, rebooting" % ("LTE lock %s put back" % back if back else "LTE lock removed"))
        except Exception as e:
            log("lock test: REMOVING THE LTE LOCK FAILED %r - remove it in the modem's web page" % e); back = None
        state({"lock_test": None}); brain.ctx["hold"] = None; lt.update(running=None, waiting=False, phase="", night=False)
        if night:
            run, base, others, win = brain.night_best()
            if run:
                dd = lambda r: "%s: anchor SINR %s, 5G %s SINR %s, call-ready %s%%" % (r["lock"], r.get("anchor_sinr_p50"), r.get("nr_cells"), r.get("nr_sinr_p50"), r.get("call_ok_pct"))
                lines = ["%s night screen %s" % (common.device_name(), run[:10])] + [("* " if win and r is win[0] else "  ") + dd(r) for r in others] + ["  baseline " + dd(base)]
                applied, cur = None, (state().get("lte_lock") or {}).get("cell")
                if win and brain.AUTO == "on" and win[0]["lock"] != cur:
                    try: guard.lock_request(win[0]["lock"]); applied = win[0]["lock"]           # guard reboots to it when idle (verify by day)
                    except Exception as e: lines.append("auto-apply failed: %r" % e)
                if applied: lines.append("-> AUTO-APPLIED %s (beat %s on an unloaded network; verify in the daytime)" % (applied, base["lock"]))
                elif win: lines.append("-> %s beat %s but %s" % (win[0]["lock"], base["lock"], "it is already the lock" if win[0]["lock"] == cur else "AUTO=%s, left as is" % brain.AUTO))
                else: lines.append("-> none beat %s, kept" % base["lock"])
                alert.report("\n".join(lines))
        for key in guard.KEYS.values():
            w = state().get(key)
            if w and w.get("state") == "active" and not (key == "lte_lock" and back): state({key: dict(w, state="pending")})   # guard re-locks when idle


def start(cells, align=False):
    """API: start a test of `cells` after an unlocked baseline (in a thread). False if a test is already running or a job is busy."""
    if lt["running"] or common.busy(): return False
    threading.Thread(target=lock_test, args=(["none"] + cells, align), daemon=True).start()
    return True


# ---------- night screen ----------
def night_candidates(now):
    """Strong cells of the main bands seen in the last 6 h, not the lock, not tested in NIGHT_RETEST_DAYS - strongest first."""
    lock = (state().get("lte_lock") or {}).get("cell")
    cut = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - NIGHT_RETEST_DAYS * 86400))
    tested = {r["lock"] for r in store.rows(DATA, "lock_tests", cut) if r.get("mode") != "cut"}
    reg = brain.REG["lte"]; out = []
    for k, e in reg.items():
        if e.get("band") not in NIGHT_BANDS or now - e.get("last", 0) > 6 * 3600 or e.get("seen_min", 0) < 60 or e.get("rsrp") is None: continue
        best = max(x.get("rsrp") or -999 for x in reg.values() if x.get("band") == e["band"])
        spec = "%s@%s" % (k.split("/")[1], k.split("/")[0])
        if e["rsrp"] >= best - 6 and spec != lock and spec not in tested: out.append((e["rsrp"], spec))
    return [x for _, x in sorted(out, reverse=True)][:NIGHT_MAX]


def maybe_night(row, now=None):
    """With each sample: start the night screen once a night, in the window, after NIGHT_IDLE_MIN idle minutes;
    traffic during the screen stops it (the lock is put back). True when it started."""
    now = now or time.time()
    if lt.get("night") and lt["running"] and not brain.idle(row) and not lt["cut"]:
        log("night screen: traffic - stopping"); lt_cut("stop"); return False
    if NIGHT in ("", "off") or common.busy() or not common.clock_ok(): return False     # old clock after a power cut: not night yet
    a, b = NIGHT.split("-"); t = time.strftime("%H:%M", time.localtime(now)); day = time.strftime("%Y-%m-%d", time.localtime(now))
    if not (a <= t < b) or state().get("night_last") == day: return False
    last = list(brain.HIST)[-NIGHT_IDLE_MIN:]
    if len(last) < NIGHT_IDLE_MIN or not all(brain.idle(r) for r in last): return False
    state({"night_last": day}); cands = night_candidates(now)
    w = state().get("lte_lock") or {}
    lockcell = w["cell"] if w.get("state") == "active" else None
    cut = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - NIGHT_RETEST_DAYS * 86400))
    tested = {r["lock"] for r in store.rows(DATA, "lock_tests", cut) if r.get("mode") != "cut"}
    extra = [c for c in NIGHT_CELLS if c != lockcell and c not in tested]       # home-set cells due for a re-check
    if not cands and not extra:
        log("night screen: no new candidates"); return False
    plan = [lockcell or "none"] + extra + cands
    seen = set(); plan = [c for c in plan if not (c in seen or seen.add(c))][:1 + NIGHT_MAX]
    log("night screen: %s" % ", ".join(plan)); event({"event": "night screen", "to": ", ".join(plan), "why": "strong cells not tested in %g days" % NIGHT_RETEST_DAYS})
    threading.Thread(target=lock_test, args=(plan,), kwargs={"night": True}, daemon=True).start()
    return True
