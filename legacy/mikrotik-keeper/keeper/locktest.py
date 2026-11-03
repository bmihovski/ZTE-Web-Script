"""Lock tests and the night screen (device neutral; drivers with `lock_test` / `night` in `CAN`).

LTE test: an unlocked baseline, then each candidate `PCI@EARFCN` locked, settle, measure, rows to `lock_tests`.
5G test (`rat=nr`): the LTE anchor is PINNED for every step (the keeper's LTE lock, else the serving cell, or `anchor=`), so the 5G
cells are compared on the same anchor; the baseline has the 5G leg unlocked, then each candidate `PCI@ARFCN` is locked. A ZTE step is
lock + reboot (one reboot carries the anchor and the 5G cell); on the ATL it is one RouterOS script and no reboot (`LOCKS_REBOOT`).
Only the 5G test sweeps the antennas (the baseline too), in both modes: each mode is internet-tested and the best INTERNET QUALITY
wins (call-ready, loss, latency, jitter - signal only breaks a tie), the mode is left set for the measurement and recorded in the
row; it is kept for the next boot only when the winner is auto-applied.
`on_target_pct` = share of minutes on the locked cell (LTE: `lte_cell`, 5G: `nr_cell`). The lock is ALWAYS removed at the end (night
screen: the guard's lock is put back instead).
Align mode (align=1) waits after each lock until you aligned the antenna (POST /lock-test-continue?heading=N); align=0 is the fixed
position (no wait, `heading=` is recorded). Skip / stop end a step / the test.
Night screen (LTE only): in NIGHT_SCREEN, after NIGHT_IDLE_MIN idle minutes, re-tests the current lock + NIGHT_CELLS (home set) + up to
NIGHT_MAX strong untested cells. Pushes the ranking to Telegram and RECOMMENDS a daytime test (never auto-applies - the night network
has B1 off and no load); traffic stops it. Auto-apply happens on the daytime test instead (real load), when AUTO=on.

Two modes: align=True waits for you per cell (on-go); align=False is a fixed position (already aligned, heading recorded) - an NR
test sweeps + internet-tests the modes in both. An unstable LTE anchor (serving-cell switches, < LOCKTEST_MIN_ANCHOR_PCT on the
pinned anchor, a > LOCKTEST_MAX_GAP_MIN data gap) makes the 5G row `unstable`: kept, never ranked.
A step that cannot be measured is recorded with its cause (`cell not reachable: ...` no 5G, `5G cell switching: ...`,
`antenna control failed: ...` web / goform) and skipped - a web failure skips the baseline too (a "not measured" reference is no
reference); a 5G-free or switching baseline is measured (it is the reference). A skipped step has no quality values (no fabricated
0 % loss).
Env: LOCKTEST_SETTLE_S (180), LOCKTEST_MEASURE_MIN (12), LOCKTEST_BOOT_WAIT_S (420), LOCKTEST_ALIGN_TIMEOUT_MIN (90),
LOCKTEST_READY_TIMEOUT_S (180 - wait for the CPE web), LOCKTEST_ONLINE_TIMEOUT_S (180 - wait for stable probes),
LOCKTEST_MAX_ANCHOR_SWITCHES (0), LOCKTEST_MIN_ANCHOR_PCT (80), LOCKTEST_MAX_GAP_MIN (3),
NIGHT_SCREEN (02:00-05:00, off), NIGHT_MAX (3), NIGHT_IDLE_MIN (30), NIGHT_RETEST_DAYS (7), NIGHT_CELLS (always re-check, e.g. 340@3350).
"""
import collections, re, threading, time

from keeper import acs, alert, brain, common, devices, guard, store
from keeper.common import DATA, E, event, log, record, stamp, state

LT_SETTLE_S, LT_MEASURE_MIN = float(E("LOCKTEST_SETTLE_S", "180")), float(E("LOCKTEST_MEASURE_MIN", "12"))
LT_BOOT_WAIT_S = float(E("LOCKTEST_BOOT_WAIT_S", "420"))
LT_ALIGN_TIMEOUT_MIN = float(E("LOCKTEST_ALIGN_TIMEOUT_MIN", "90"))
LT_READY_TIMEOUT_S = float(E("LOCKTEST_READY_TIMEOUT_S", "180"))     # wait for the CPE web after a reboot (a ZTE needs minutes)
LT_ONLINE_TIMEOUT_S = float(E("LOCKTEST_ONLINE_TIMEOUT_S", "180"))   # wait for stable probes before measuring

# an unstable LTE anchor makes the 5G leg unstable: any serving-cell switch (0 = tolerated), < 80 % of the minutes on the pinned
# anchor, or a > 3 min gap without data (management lost) flags the step `unstable` - kept in the results, never ranked.
LT_MAX_SWITCHES = int(E("LOCKTEST_MAX_ANCHOR_SWITCHES", "0"))
LT_MIN_ANCHOR_PCT = float(E("LOCKTEST_MIN_ANCHOR_PCT", "80"))
LT_MAX_GAP_MIN = float(E("LOCKTEST_MAX_GAP_MIN", "3"))
LT_COLS = ["time", "rat", "lock", "minutes", "on_target_pct", "anchor", "site", "pin", "anchor_pct", "anchor_switches", "gap_min", "unstable",
           "anchor_sinr_p50", "anchor_sinr_p10", "anchor_rsrp_p50", "anchor_rsrq_p50", "ca", "nr_cells", "nr_arfcn", "nr_sinr_p50", "nr_rsrp_p50",
           "nr_ca", "no5g_min", "call_ok_pct", "lat_p95", "jitter_ms", "loss_pct", "antennas", "ant5", "ant6", "note", "heading", "mode", "run"]
NIGHT = E("NIGHT_SCREEN", "02:00-05:00")
NIGHT_MAX, NIGHT_IDLE_MIN = int(E("NIGHT_MAX", "3")), int(E("NIGHT_IDLE_MIN", "30"))
NIGHT_RETEST_DAYS = float(E("NIGHT_RETEST_DAYS", "7"))
NIGHT_CELLS = [c for c in E("NIGHT_CELLS", "").replace(" ", "").split(",") if c]   # always re-check these (home set), same weekly throttle
NIGHT_BANDS = ("B1", "B3", "B7")                       # B8 / B20 / B28 measured worse here: far, loaded, n28 5G (2026-10-27)
lt = {"running": None, "plan": [], "done": [], "align": False, "waiting": False, "heading": "", "fixed": "", "go": threading.Event(),
      "cut": None, "cut_ev": threading.Event(), "rat": "lte", "anchor": None, "modes": {}}   # modes: lock -> (ant5, ant6) of the step's sweep
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


def anchor_for(rat, spec=""):
    """The LTE anchor an NR test pins: the explicit `spec`, else the keeper's LTE lock (active / pending), else the serving cell.
    None for an LTE test; ValueError when an NR test has no anchor (the 5G cells would not be comparable)."""
    if rat != "nr": return None
    if spec:
        if not re.fullmatch(guard.SPEC_RE, spec): raise ValueError("anchor=PCI@EARFCN, e.g. 340@1550")
        return spec
    w = state().get("lte_lock") or {}
    if w.get("cell") and w.get("state") in ("active", "pending"): return w["cell"]
    for r in reversed(brain.HIST):
        c = r.get("lte_cell") or ""
        if re.fullmatch(r"\d{1,6}/\d{1,3}", c): return "%s@%s" % (c.split("/")[1], c.split("/")[0])
    raise ValueError("no LTE anchor known - pass anchor=PCI@EARFCN (a 5G test compares the cells on one anchor)")


def lt_eval(lock, rows, note="", rat="lte", antennas="", pin=None, ant5="", ant6=""):
    """Samples measured under one lock -> one `lock_tests` row. `on_target_pct` = share of minutes on the locked cell (LTE:
    `lte_cell`, 5G: `nr_cell`); `nr_arfcn` = the ARFCN the 5G cell was on (the baseline has no lock spec to take it from); anchor /
    CA / call quality describe the link, `nr_*` the 5G leg, `antennas` the mode the step used. With `pin` (a 5G test): the LTE anchor
    stability - serving-cell switches, % of minutes on the pinned anchor, longest sample gap - and `unstable` 1/0; an unstable row is
    kept but never ranked (the NR leg follows its anchor). A step with no samples (`rows` empty: not measured / not set) keeps its
    values empty - no fabricated 0 % loss or 0 switches that read like a measurement."""
    targets = set() if lock == "none" else guard.lock_target(lock)
    field = "nr_cell" if rat == "nr" else "lte_cell"
    anchors = [brain.parse_ca(r.get("lte_ca")) for r in rows]
    a = [x[0] for x in anchors if x]
    main = collections.Counter(r.get("lte_cell") for r in rows).most_common(1)
    combos = collections.Counter(" + ".join("B%s/%s" % (c[0], c[2]) for c in x) for x in anchors if x).most_common(2)
    nr = collections.Counter(r["nr_cell"].split("/")[-1] for r in rows)
    arfcn = collections.Counter(r["nr_cell"].split("/")[0] for r in rows if r.get("nr_cell") and r["nr_cell"] != "no5G").most_common(1)
    nrca = collections.Counter(" ".join(x.split(":")[0] for x in (r.get("nr_ca") or "").split()) for r in rows if r.get("nr_ca")).most_common(2)
    sinr = [brain.f(r.get("nr_sinr")) for r in rows if r["nr_cell"] != "no5G"]
    rsrp = [brain.f(r.get("nr_rsrp")) for r in rows if r["nr_cell"] != "no5G"]
    sites = collections.Counter(r.get("lte_site") for r in rows if r.get("lte_site") and (rat == "nr" or not targets or r.get("lte_cell") in targets))
    lte = [r.get("lte_cell") or "" for r in rows]
    switches = sum(1 for a, b in zip(lte, lte[1:]) if a != b) if pin else ""
    pin_pct = round(100 * sum(1 for a in lte if a and a in guard.lock_target(pin)) / len(rows), 1) if pin and rows else ""
    try: times = [brain.ts(r) for r in rows]
    except ValueError: times = []
    gap = round(max((abs(b - a) / 60 for a, b in zip(times, times[1:])), default=0.0), 1) if pin and len(times) > 1 else (0.0 if pin and times else "")
    bad = []
    if pin and rows:
        if switches > LT_MAX_SWITCHES: bad.append("%d LTE switch%s" % (switches, "" if switches == 1 else "es"))
        if pin_pct != "" and pin_pct < LT_MIN_ANCHOR_PCT: bad.append("%s%% on the pinned anchor" % pin_pct)
        if gap != "" and gap > LT_MAX_GAP_MIN: bad.append("%g min without data" % gap)
    unstable = (1 if bad else 0) if pin and rows else ""
    if bad: note = "; ".join(x for x in (note, "unstable anchor: " + ", ".join(bad)) if x)
    return {"time": stamp(), "rat": rat, "lock": lock, "minutes": len(rows), "site": sites.most_common(1)[0][0] if sites else "",
            "on_target_pct": round(100 * sum(1 for r in rows if r.get(field) in targets) / len(rows), 1) if targets and rows else "",
            "anchor": main[0][0] if main else "", "pin": pin or "", "anchor_pct": pin_pct, "anchor_switches": switches if rows else "",
            "gap_min": gap, "unstable": unstable, "anchor_sinr_p50": brain.med([x[4] for x in a]), "anchor_sinr_p10": brain.pct([x[4] for x in a], 10),
            "anchor_rsrp_p50": brain.med([x[3] for x in a]), "anchor_rsrq_p50": brain.med([brain.f(r.get("lte_rsrq")) for r in rows]),
            "ca": "; ".join("%s (%d)" % c for c in combos), "nr_cells": " ".join("%s:%d" % c for c in nr.most_common()),
            "nr_arfcn": arfcn[0][0] if arfcn else "", "nr_sinr_p50": brain.med(sinr), "nr_rsrp_p50": brain.med(rsrp),
            "nr_ca": "; ".join("%s (%d)" % c for c in nrca), "no5g_min": sum(1 for r in rows if brain.no5g_busy(r)), "call_ok_pct": brain.share(rows),
            "lat_p95": brain.med([brain.f(r.get("lat_p95")) for r in rows]), "jitter_ms": brain.med([brain.f(r.get("jitter_ms")) for r in rows]),
            "loss_pct": round(sum(brain.f(r.get("loss_pct")) or 0 for r in rows) / len(rows), 2) if rows else "", "antennas": antennas,
            "ant5": ant5, "ant6": ant6, "note": note}


def sweep_antennas(lock, rat, dev):
    """NR test only, after alignment: measure the antenna modes on the locked 5G cell and leave the best one set for the measurement.
    Returns (mode text, note, skip, why): ("", "", "", "") when the device has no antennas / no sweep, ("", "sweep: ...") when it could
    not rank, and `why` = the cause (`web` antenna control / `no5g` / `switching` / `anchor` unstable) so the row says what happened."""
    ant = getattr(dev, "antennas", None)
    if rat != "nr" or not ant or not hasattr(ant, "sweep_for_test"): return "", "", "", ""
    phase("%s: sweeping the antennas and testing the internet (4 modes, ~2 min)" % lock, True)
    expected = None if lock == "none" else "%s/%s" % (lock.split("@")[1], lock.split("@")[0])   # None = the serving cell (baseline)
    r = ant.sweep_for_test(expected, trigger="lock test %s" % lock)
    if r.get("aborted"): return "", "sweep: %s" % r["aborted"], r.get("skip", ""), r.get("why", "")
    lt["modes"][lock] = (r["ant5"], r["ant6"])
    return r["mode"], "", "", ""


def step_mode(night, align, heading, rat):
    """A row's `mode`: night | aligned (a heading was given) | fixed (5G) | as is (LTE)."""
    return "night" if night else "aligned" if align and heading else ("fixed" if rat == "nr" else "as is")


def step_pins(plan, pin=None, base_pin=None, fallback=None):
    """Resolve every step's LTE anchor ONCE, before the run. The registry pairs are updated by the run's own samples, so resolving
    per step could pin a different anchor mid-run (seen 2026-11-02: the baseline's samples flipped 534's best anchor between the
    baseline and the 534 step, making the comparison cross-anchor)."""
    return {lock: step_anchor(lock, pin, base_pin, fallback) for lock in plan}


def step_anchor(lock, pin, base_pin, fallback=None):
    """The LTE anchor one NR step pins: the explicit `anchor=` for all cells; else the 5G cell's best known anchor (an NSA cell is
    served only under some anchors - 747 needs 1550/434); else the keeper's LTE lock / the serving cell (`fallback`); the baseline
    uses `base_pin` (the best known anchor across the tested cells) or the fallback."""
    if pin: return pin
    if lock == "none": return base_pin or fallback
    cell = "%s/%s" % tuple(lock.split("@")[::-1]) if "@" in lock else lock     # 804@636576 -> 636576/804 (the registry key)
    return brain.anchor_spec_for(cell) or fallback or base_pin


def lock_test(plan, align=False, night=False, rat="lte", anchor=None, heading=""):
    """Run a test (in a thread). plan: `["none", "434@1550", ...]`, one cell at a time, each with its own lock / reboot.
    rat="nr": every step pins an LTE anchor (PCI@EARFCN) and locks the 5G cell; "none" = the 5G leg unlocked, anchor pinned.
    The anchor is the explicit `anchor=` for all cells; with none, each 5G cell's best known anchor (`brain.anchor_spec_for`) is
    pinned and the baseline uses the best anchor across the tested cells (`brain.anchor_for_cells`).
    align=True (on-go): wait after each lock until the antenna is aligned; align=False (fixed position): no wait, `heading` is the
    position already set. An NR test sweeps + internet-tests the antenna modes in both modes (the baseline too).
    night=True (LTE only): plan[0] is the current lock (baseline), rows are mode "night" and the lock is put back at the end."""
    if lt["running"]: return
    dev = devices.current()
    pin = anchor if rat == "nr" else None                      # explicit anchor: one anchor for every cell
    base_pin = pin or (brain.anchor_for_cells([c for c in plan if c != "none"]) if rat == "nr" else None)
    fallback = None                                            # neither explicit nor known: the keeper's LTE lock / serving cell
    if rat == "nr" and not pin:
        try: fallback = anchor_for("nr")
        except ValueError: fallback = None
    pins = step_pins(plan, pin, base_pin, fallback) if rat == "nr" else {}
    lt.update(running="starting", plan=list(plan), done=[], align=align, waiting=False, heading="", fixed=heading, night=night, rat=rat,
              anchor=pin or base_pin or fallback, modes={}, run=stamp()); lt["go"].clear()
    brain.ctx["hold"] = "lock test"                  # no keeper reboots meanwhile
    log("%s lock test started%s%s: %s (~%d min)" % ("5G" if rat == "nr" else "LTE",
        " (LTE anchor %s)" % (pin or "per 5G cell, baseline %s" % base_pin if rat == "nr" else pin) if rat == "nr" else "",
        " at heading %s" % heading if rat == "nr" and heading and not align else "", ", ".join(plan),
        len(plan) * (LT_MEASURE_MIN + LT_SETTLE_S / 60 + 2)))
    try:
        for lock in plan:
            lt["running"] = lock; lt["cut"] = None; lt["cut_ev"].clear(); start = locked = None; heading = ""; ant = ""; a5 = a6 = ""
            step_pin = pins.get(lock) if rat == "nr" else None
            state({"lock_test": {"cell": lock, "rat": rat, "anchor": step_pin}})   # if the keeper dies mid-test, the next start removes the locks
            boots = acs.st["boots"]
            if rat == "nr" and not step_pin:                 # no explicit anchor, no known anchor for this cell, no fallback
                res = lt_eval(lock, [], "lock not set: no LTE anchor known for %s" % lock, rat)
                res.update(heading=heading, mode=step_mode(night, align, heading, rat), run=lt["run"])
                record("lock_tests", LT_COLS, res); lt["done"].append(res)
                event({"event": "lock test %s" % lock, "to": "not set", "why": res["note"]}); log("lock test %s: %s" % (lock, res["note"]))
                continue
            phase("%s: setting the lock%s" % (lock, ", rebooting (~2 min)" if dev.LOCKS_REBOOT else " (script, ~1 min)"))
            try:
                try:
                    if rat == "nr": dev.locks_apply(step_pin, lock)   # ONE change: the pinned anchor + the 5G cell (one reboot on the ZTE)
                    else: dev.locks_apply(lock, None)
                except RuntimeError as e:              # refused: record it, go on with the next one
                    res = lt_eval(lock, [], "lock not set: %s" % e, rat, pin=step_pin)
                    res.update(heading=heading, mode=step_mode(night, align, heading, rat), run=lt["run"])
                    record("lock_tests", LT_COLS, res); lt["done"].append(res)
                    event({"event": "lock test %s" % lock, "to": "not set", "why": str(e)}); log("lock test %s: %s" % (lock, e))
                    continue
                if dev.LOCKS_REBOOT:
                    t0 = time.time()
                    while acs.st["boots"] == boots and time.time() - t0 < LT_BOOT_WAIT_S and not lt["cut"]: time.sleep(2)
                    note = "" if acs.st["boots"] > boots else "no boot inform within %d s" % LT_BOOT_WAIT_S
                else:
                    note = ""                          # the driver blocked until the lock was applied: no CPE reboot to wait for
                if lt["cut"]: raise Cut()              # skip / stop during the boot wait: the cut branch records what there is
                if dev.LOCKS_REBOOT and note:
                    # The ZTE applies a lock only at boot: without the boot inform the step was never locked. Do not settle, wait
                    # for the link and measure 12 min of nothing - record it and go on (2026-11-02: a 56-min CPE outage after these
                    # reboots burned two 25-min night steps).
                    res = lt_eval(lock, [], "not measured: " + note, rat, pin=step_pin)
                    res.update(heading=heading, mode=step_mode(night, align, heading, rat), run=lt["run"])
                    record("lock_tests", LT_COLS, res); lt["done"].append(res)
                    event({"event": "lock test %s" % lock, "to": "not measured", "why": res["note"]})
                    log("lock test %s: %s" % (lock, res["note"]))
                    continue
                locked = time.time()
                phase("%s: %s, reconnecting until %s, then %s" % (lock, note or ("rebooted with the lock" if dev.LOCKS_REBOOT else "lock applied"),
                      hm(LT_SETTLE_S), "you align" if align and lock != "none" else "measuring")); lt_wait(LT_SETTLE_S)
                heading = ""
                rdy = getattr(dev, "ready", None)                          # the CPE web must be back before the sweep / you align
                if rat == "nr" and LT_READY_TIMEOUT_S > 0 and rdy:
                    phase("%s: waiting for the CPE web to come back (up to %d s)" % (lock, LT_READY_TIMEOUT_S))
                    if not rdy(LT_READY_TIMEOUT_S):
                        note = "; ".join(x for x in (note, "the CPE web was not reachable within %d s" % LT_READY_TIMEOUT_S) if x)
                        event({"event": "lock test %s: CPE web not back" % lock, "why": "no answer within %d s" % LT_READY_TIMEOUT_S})
                if align and (lock != "none" or rat == "nr"):   # an NR test sweeps the baseline too, so it aligns it too
                    lt["waiting"] = True; lt["go"].clear(); phase("%s: waiting for you to align (test stops at %s)" % (lock, hm(LT_ALIGN_TIMEOUT_MIN * 60)))
                    log("lock test: %s - align the antenna for it, then 'Aligned - measure now'" % ("none (5G unlocked)" if lock == "none" else lock + " locked"))
                    event({"event": "lock test %s: align now" % lock, "why": "peak guide on %s; the lock stays until you continue"
                           % ("5G SINR of the locked cell" if rat == "nr" and lock != "none" else
                              "5G SINR of the serving cell" if rat == "nr" else "LTE SINR")})
                    if not lt["go"].wait(LT_ALIGN_TIMEOUT_MIN * 60):
                        raise RuntimeError("no 'aligned' within %g min - test stopped" % LT_ALIGN_TIMEOUT_MIN)
                    lt["waiting"] = False; heading = lt["heading"]
                    lt_wait(LT_SETTLE_S / 3)
                elif rat == "nr":
                    heading = lt["fixed"]                     # fixed position: the heading already set (may be "")
                ant = ""
                skip = why = ""
                if rat == "nr":
                    ant, sweep_note, skip, why = sweep_antennas(lock, rat, dev)
                    a5, a6 = lt["modes"].get(lock) or ("", "")     # the mode the step used (for the per-cell boot apply)
                    if sweep_note: note = "; ".join(x for x in (note, sweep_note) if x)
                    if ant: lt_wait(LT_SETTLE_S / 3)
                if rat == "nr" and skip and (lock != "none" or why == "web"):
                    # the cell could not be measured (not seen / switching / antenna control): do not burn 12 min on it. A web /
                    # antenna-control failure skips the BASELINE too (a "not measured" reference is no reference); a 5G-free or
                    # switching baseline is measured - it is the reference.
                    label = {"web": "antenna control failed", "switching": "5G cell switching"}.get(why, "cell not reachable")
                    res = lt_eval(lock, [], ("not measured: " if lock == "none" else "") + label + ": " + skip, rat, pin=step_pin)
                    res.update(heading=heading, mode=step_mode(night, align, heading, rat), run=lt["run"])
                    record("lock_tests", LT_COLS, res); lt["done"].append(res)
                    event({"event": "lock test %s" % lock, "to": "not measured", "why": res["note"]}); log("lock test %s: %s" % (lock, res["note"]))
                    continue
                if LT_ONLINE_TIMEOUT_S > 0 and not brain.wait_online(LT_ONLINE_TIMEOUT_S):   # the link is back: measure from now
                    note = "; ".join(x for x in (note, "the link was not online within %d s" % LT_ONLINE_TIMEOUT_S) if x)
                    event({"event": "lock test %s: link not back" % lock, "why": "no successful probe within %d s" % LT_ONLINE_TIMEOUT_S})
                phase("%s: measuring until %s" % (lock, hm(LT_MEASURE_MIN * 60)), True)
                start = time.time(); lt_wait(LT_MEASURE_MIN * 60)
            except Cut:                                # skip / stop from the panel or API: record what we have
                lt["waiting"] = False; what = "skipped" if lt["cut"] == "skip" else "stopped"
                since = start or locked                # skipped before measuring: keep what was seen while you aligned
                rows = [r for r in list(brain.HIST) if brain.ts(r) >= since] if since else []
                res = lt_eval(lock, rows, "%s by you%s" % (what, " after %.0f min measuring" % ((time.time() - start) / 60) if start
                                                           else " before measuring (values: while aligning)" if rows else ""), rat, ant, step_pin, a5, a6)
                res.update(heading=heading if start else "", mode="cut", run=lt["run"])
                record("lock_tests", LT_COLS, res); lt["done"].append(res)
                event({"event": "lock test %s" % lock, "to": what, "why": res["note"]}); log("lock test %s: %s" % (lock, res["note"]))
                if what == "stopped": break
                continue
            rows = [r for r in list(brain.HIST) if brain.ts(r) >= start]
            res = lt_eval(lock, rows, note or ("" if rows else "no samples (no service on this cell?)"), rat, ant, step_pin, a5, a6)
            res.update(heading=heading, mode=step_mode(night, align, heading, rat), run=lt["run"])
            record("lock_tests", LT_COLS, res); lt["done"].append(res)
            if res["anchor"] and lock != "none" and not night and rat == "lte":          # night values (unloaded) don't go into the registry
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
            if rat == "nr":
                dev.locks_apply("none", "none"); log("lock test: LTE anchor and 5G lock removed%s" % (", rebooting" if dev.LOCKS_REBOOT else ""))
            else:
                dev.locks_apply(back or "none", None); log("lock test: %s, rebooting" % ("LTE lock %s put back" % back if back else "LTE lock removed"))
        except Exception as e:
            log("lock test: REMOVING THE LOCKS FAILED %r - remove them in the modem's web page" % e); back = None
        state({"lock_test": None}); brain.ctx["hold"] = None
        lt.update(running=None, waiting=False, phase="", night=False)   # plan / align / rat / anchor stay: they describe the last run
        na = lambda v: v if v not in (None, "", "None") else "n/a"
        if night:                                                           # night = unloaded, B1 off: report + recommend only, NEVER auto-apply
            run, base, others, win = brain.night_best()
            if run and base is None:                                        # a missing baseline is no reference
                alert.report("%s night screen %s: the baseline was not measured - no verdict (no service on the lock's cell?)"
                             % (common.device_name(), run[:10]))
            elif run:
                dd = lambda r: "%s: anchor SINR %s, 5G %s SINR %s, call-ready %s%%%s" % (r["lock"], na(r.get("anchor_sinr_p50")),
                    na(r["nr_cells"]), na(r["nr_sinr_p50"]), na(r.get("call_ok_pct")), (" - " + r["note"]) if r.get("note") else "")
                blabel = "the unlocked baseline" if base["lock"] == "none" else base["lock"]
                lines = ["%s night screen %s (unloaded; B1 off at night, not your daytime lock)" % (common.device_name(), run[:10])] + \
                        [("* " if win and r is win[0] else "  ") + dd(r) for r in others] + ["  unlocked baseline: " + dd(base).split(": ", 1)[1]]
                if win: lines.append("-> %s beat %s unloaded -> RECOMMEND an aligned daytime lock test (not auto-applied)" % (win[0]["lock"], blabel))
                else: lines.append("-> nothing beat %s, kept" % blabel)
                alert.report("\n".join(lines))
        elif rat == "nr":                                                   # 5G cells, same anchor: real load -> auto-apply the winner when AUTO=on
            rk = brain.anchor_ranking("nr")
            best, base, gain = brain.anchor_best("nr")                       # internet quality decides, signal only breaks a tie
            failed = rk.get("failed") or []
            if best or rk.get("unstable") or failed:
                lines = ["%s 5G lock test (LTE anchor per 5G cell%s; internet quality decides, signal breaks ties)" % (
                    common.device_name(), "" if (pin or base_pin) is None else
                    (" " + pin if pin else " - %s for the baseline" % base_pin))]
                if best:
                    dd = lambda r: "%s (anchor %s): call-ready %s%%, loss %s%%, p95 %s ms, jitter %s ms, 5G SINR %s, 5G RSRP %s" % (
                        r["lock"], r.get("pin") or "?", na(r.get("call_ok_pct")), na(r.get("loss_pct")), na(r.get("lat_p95")),
                        na(r.get("jitter_ms")), na(r.get("nr_sinr_p50")), na(r.get("nr_rsrp_p50")))
                    cur = (state().get("nr_lock") or {}).get("cell")
                    try: fresh = abs(time.time() - time.mktime(time.strptime(best.get("time", ""), "%Y-%m-%d %H:%M:%S"))) < 3 * 3600
                    except (ValueError, TypeError): fresh = False
                    sig = max(rk["ranked"], key=lambda r: (brain.f(r.get("nr_sinr_p50")) or -99, brain.f(r.get("nr_rsrp_p50")) or -999)) if rk["ranked"] else None
                    lines.append("* best " + dd(best))
                    if base: lines.append("  baseline (5G unlocked): " + dd(base).split(": ", 1)[1])
                    if sig and sig["lock"] != best["lock"]:
                        lines.append("  signal alone would pick %s (5G SINR %s) but internet quality picked %s" % (sig["lock"], na(sig.get("nr_sinr_p50")), best["lock"]))
                    def _pin(lock):
                        return next((r.get("pin") or "" for r in reversed(store.rows(DATA, "lock_tests"))
                                     if r.get("lock") == lock and r.get("rat") == "nr"), "")
                    if failed: lines.append("  not ranked (not seen / off target): " +
                                            ", ".join(f + anchor_hint(f, _pin(f)) for f in failed))
                    applied = None; anchored = None
                    if gain and fresh and brain.AUTO == "on" and best["lock"] != cur:
                        try:
                            guard.lock_request(best["lock"], "nr"); applied = best["lock"]
                            want = (best.get("pin") or "").strip()   # the winning PAIR: the 5G cell and the LTE anchor that serves it
                            cur_lte = (state().get("lte_lock") or {}).get("cell") or ""
                            if want and want != cur_lte:             # a pair must not move the LTE lock to a worse anchor
                                ok, why = brain.anchor_move_ok(want, cur_lte)
                                if ok: guard.lock_request(want); anchored = want
                                else: lines.append("  not moving the LTE lock to %s: %s - the 5G lock alone is applied" % (want, why))
                            mode = (lt.get("modes") or {}).get(best["lock"])         # the mode the winning step measured with, re-applied at every boot
                            if mode and getattr(dev, "antennas", None):
                                dev.antennas.keep({"auto": "0", "ant5": mode[0], "ant6": mode[1]},
                                                  src="auto-applied 5G lock %s and its measured mode" % best["lock"])
                        except Exception as e: lines.append("auto-apply failed: %r" % e)
                    if applied: lines.append("-> AUTO-APPLIED 5G lock %s%s%s" % (
                        applied, " with LTE anchor " + anchored if anchored else "", " with " + best["antennas"] if best.get("antennas") else ""))
                    elif gain and best["lock"] == cur: lines.append("-> best is already your 5G lock %s" % cur)
                    elif gain: lines.append("-> %s wins%s -> review to apply (lock it AND keep anchor %s for the same result)" % (best["lock"], "" if fresh else " (older result)", best.get("pin") or pin or base_pin or "?"))
                    else: lines.append("-> no 5G cell clearly beats the unlocked baseline on internet quality - no 5G lock needed")
                else:
                    lines.append("no 5G cell ranked - not seen / off target: " + ", ".join(failed) if failed else
                                 "no 5G cell ranked - the baseline and the candidates had an unstable LTE anchor")
                if rk.get("unstable"): lines.append("  NOT RANKED (unstable LTE anchor): " + ", ".join(rk["unstable"]))
                alert.report("\n".join(lines))
        else:                                                               # daytime LTE test = real load: auto-apply the winner when AUTO=on
            best, base, gain = brain.anchor_best()
            if not best:
                failed = brain.anchor_ranking("lte").get("failed") or []
                if failed: alert.report("%s daytime lock test: no candidate result (not on the tested cell: %s)" % (common.device_name(), ", ".join(failed)))
            if best:
                dd = lambda r: "%s: call-ready %s%%, 5G SINR %s, anchor SINR %s" % (r["lock"], na(r.get("call_ok_pct")), na(r.get("nr_sinr_p50")), na(r.get("anchor_sinr_p50")))
                cur = (state().get("lte_lock") or {}).get("cell")
                try: fresh = abs(time.time() - time.mktime(time.strptime(best.get("time", ""), "%Y-%m-%d %H:%M:%S"))) < 3 * 3600
                except (ValueError, TypeError): fresh = False
                lines = ["%s daytime lock test" % common.device_name(), "* best " + dd(best)] + (["  unlocked baseline: " + dd(base).split(": ", 1)[1]] if base else [])
                applied = None
                if gain and fresh and brain.AUTO == "on" and best["lock"] != cur:
                    try: guard.lock_request(best["lock"]); applied = best["lock"]
                    except Exception as e: lines.append("auto-apply failed: %r" % e)
                if applied: lines.append("-> AUTO-APPLIED %s (beats the unlocked baseline under load)" % applied)
                elif gain and best["lock"] == cur: lines.append("-> best is already your lock %s" % cur)
                elif gain: lines.append("-> %s wins%s -> review to apply" % (best["lock"], "" if fresh else " (older result)"))
                else: lines.append("-> no candidate clearly beats unlocked - no lock needed")
                alert.report("\n".join(lines))
        for key in guard.KEYS.values():
            w = state().get(key)
            if w and w.get("state") == "active" and not (key == "lte_lock" and back): state({key: dict(w, state="pending")})   # guard re-locks when idle


def start(cells, align=False, rat="lte", anchor=None, heading=""):
    """API: start a test of `cells` after an unlocked baseline (in a thread). `rat` lte | nr (nr pins `anchor`); `heading` is the
    fixed antenna position to record (align=False). False if a test is already running or a job is busy."""
    if lt["running"] or common.busy(): return False
    threading.Thread(target=lock_test, args=(["none"] + cells, align),
                     kwargs={"rat": rat, "anchor": anchor, "heading": heading}, daemon=True).start()
    return True


def anchor_hint(lock, pin=None):
    """A failed 5G step -> ' (known under 1550/434, 12 min)' when the cell was served under another LTE anchor than the pin: an NSA
    5G cell can be there only under some anchors (747 needs 1550/434, not 100/340)."""
    if "@" not in lock: return ""
    cell = "%s/%s" % tuple(lock.split("@")[::-1])
    best = brain.best_anchor(cell)
    if not best: return ""
    pinned = "%s/%s" % tuple(pin.split("@")[::-1]) if pin and "@" in pin else ""
    if best == pinned: return ""
    a = next((x for x in brain.nr_anchors(cell) if x["anchor"] == best), {})
    return " (known under %s, %s min)" % (best, a.get("min", "?"))


def cell_mode(cell):
    """The antenna mode the newest GOOD NR lock test measured `cell` with (used at boot for the locked cell), or None.
    Good = a completed step (not cut / night), `unstable` != 1, on-target >= 80 %, with the mode values stored. No age / heading
    gate (your choice): the record's date and heading are returned so the keeper can show how old it is."""
    if not cell: return None
    for r in reversed(store.rows(DATA, "lock_tests")):
        if (r.get("lock") == cell and r.get("rat") == "nr" and r.get("antennas") and r.get("ant5") and r.get("ant6")
                and str(r.get("unstable")) != "1" and r.get("mode") not in ("cut", "night")
                and (brain.f(r.get("on_target_pct")) or 0) >= 80):
            return {"cell": cell, "mode": r["antennas"], "ant5": r["ant5"], "ant6": r["ant6"], "time": r.get("time", ""),
                    "heading": r.get("heading", ""), "call_ok_pct": r.get("call_ok_pct", ""), "lat_p95": r.get("lat_p95", "")}
    return None


# ---------- night screen (LTE) ----------
def _dead_cells(now):
    """Cells a night screen locked in the last 30 days but got no service on (a measured row: minutes > 0, no anchor, 0 % on-target)
    - don't burn reboots on them again. A step that never measured (no boot inform, the CPE web / the link did not come back) is no
    evidence about the cell and must not blacklist it."""
    cut = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 30 * 86400))
    return {r["lock"] for r in store.rows(DATA, "lock_tests", cut)
            if r.get("mode") == "night" and not r.get("anchor") and (brain.f(r.get("on_target_pct")) or 0) == 0
            and (brain.f(r.get("minutes")) or 0) > 0}


def night_candidates(now):
    """Strong cells of the main bands seen in the last 6 h, not the lock, not tested in NIGHT_RETEST_DAYS - strongest first."""
    lock = (state().get("lte_lock") or {}).get("cell")
    cut = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - NIGHT_RETEST_DAYS * 86400))
    tested = {r["lock"] for r in store.rows(DATA, "lock_tests", cut) if r.get("mode") != "cut"}
    reg = brain.REG["lte"]; dead = _dead_cells(now); out = []
    for k, e in reg.items():
        if e.get("band") not in NIGHT_BANDS or now - e.get("last", 0) > 6 * 3600 or e.get("seen_min", 0) < 60 or e.get("rsrp") is None: continue
        best = max(x.get("rsrp") or -999 for x in reg.values() if x.get("band") == e["band"])
        spec = "%s@%s" % (k.split("/")[1], k.split("/")[0])
        if e["rsrp"] >= best - 6 and spec != lock and spec not in tested and spec not in dead: out.append((e["rsrp"], spec))
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
    extra = [c for c in NIGHT_CELLS if c != lockcell and c not in tested and c not in _dead_cells(now)]   # home-set cells due for a re-check
    if not cands and not extra:
        log("night screen: no new candidates"); return False
    plan = [lockcell or "none"] + extra + cands
    seen = set(); plan = [c for c in plan if not (c in seen or seen.add(c))][:1 + NIGHT_MAX]
    log("night screen: %s" % ", ".join(plan)); event({"event": "night screen", "to": ", ".join(plan), "why": "strong cells not tested in %g days" % NIGHT_RETEST_DAYS})
    threading.Thread(target=lock_test, args=(plan,), kwargs={"night": True}, daemon=True).start()
    return True
