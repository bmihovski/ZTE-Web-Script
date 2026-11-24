"""Lock guard: keep the chosen LTE anchor and 5G cell locked; release a lock when its cell is lost, restore it when it is back.

Device neutral - the driver sets the locks (`locks_apply`) and says what the modem itself holds (`lock_value`).
Every lock change may cost a reboot (~2 min offline on the ZTE): set / restore only while the link is idle and at most
LOCK_MAX_REBOOTS_DAY a day; a release happens at once (the link is degraded then). LTE + 5G changes due together = one change.
State per lock in the state file: `{"cell": "340@100", "state": "pending|active|released", ...}` under `lte_lock` / `nr_lock`.

Env: LOCK_NOSERVICE_MIN (3), LOCK_BAD_MIN (10), NR_LOCK_LOST_MIN (10), LOCK_RETRY_H (1), NR_BACK_MIN (3), LOCK_MAX_REBOOTS_DAY (4),
OFFLINE_RELEASE_MIN (8: no inform + no internet with an LTE lock -> released on the web page, see offline_release),
QUALITY_AB (1) / AB_BAD_PCT (70) / AB_WINDOW_MIN (30) / AB_TRIAL_MIN (20) / AB_KEEP_H (3): bad calls with good signal on an idle link -> try unlocked once a day (quality_ab),
SITE_OUTAGE_MAX_DAY (6): no internet while registered -> try another site (eNB) at most this many times in 24 h, >= 30 min apart (quality_ab),
LOCK_PAUSE (e.g. "23:30-08:00": the LTE lock is released before this window and restored after it, once its cell is back - for a
cell the operator switches off at night; measured 2026-10-28 / 29: B1 340@100 gone ~00:59-01:03 until ~06:00, a 3-min outage each
night). The env's window is authoritative; 23:30-08:00 is the current one.
"""
import re, threading, time

from keeper import brain, common, devices
from keeper.common import E, event, log, state

LOCK_NOSERVICE_MIN, LOCK_BAD_MIN = int(E("LOCK_NOSERVICE_MIN", "3")), int(E("LOCK_BAD_MIN", "10"))
NR_LOCK_LOST_MIN = int(E("NR_LOCK_LOST_MIN", "10"))          # 5G off the locked cell (or no 5G with traffic) this long -> release
LOCK_RETRY_H = float(E("LOCK_RETRY_H", "1"))                  # earliest restore after a release (and only when the cell is back)
NR_BACK_MIN = int(E("NR_BACK_MIN", "3"))                      # 5G served by the cell again (unlocked) this long = it is back
LOCK_MAX_REBOOTS_DAY = int(E("LOCK_MAX_REBOOTS_DAY", "4"))
LOCK_PAUSE = E("LOCK_PAUSE", "")
QUALITY_AB = E("QUALITY_AB", "1") == "1"                     # bad calls with good signal on an idle link -> try unlocked (quality_ab)
# every A/B reboot waits until no one is in a CALL: steady traffic, every one of the last AB_QUIET_MIN minutes >= AB_CALL_BYTES in a
# direction (a Teams / Zoom audio call ~5-12 kB/s each way; "idle" < 0.5 Mbit/s up let it through). Bursts (updates, mail) are no call:
# 2026-11-20, 2 PCs + 2 phones: up p50 0.6 kB/s, steady >= 4 kB/s in 8.5 % of 5-min windows (all under 3 kB/s only 26 %)
AB_QUIET_MIN, AB_CALL_BYTES = int(E("AB_QUIET_MIN", "5")), float(E("AB_CALL_BYTES", "4000"))
AB_BAD_PCT, AB_WINDOW_MIN, AB_TRIAL_MIN, AB_KEEP_H = float(E("AB_BAD_PCT", "70")), int(E("AB_WINDOW_MIN", "30")), int(E("AB_TRIAL_MIN", "20")), float(E("AB_KEEP_H", "3"))
SITE_OUTAGE_MAX = int(E("SITE_OUTAGE_MAX_DAY", "6"))       # outage trials of another site per 24 h (each one reboot, ~4-5 min down)
SITE_OUTAGE_GAP_S = 1800                                       # ... at least this long after the previous A/B start
OFFLINE_RELEASE_MIN = float(E("OFFLINE_RELEASE_MIN", "8"))   # no inform AND no internet this long with an LTE lock held -> release it over the LAN
KEYS = {"lte": "lte_lock", "nr": "nr_lock"}
SPEC_RE = r"\d{1,3}@\d{1,6}"      # PCI@EARFCN, e.g. 434@1550 (one cell - the MC889 keeps only the first cell of a list)
guard = {"bad": 0, "nosvc": 0, "nr_bad": 0, "nr_back": 0, "busy": False, "reboots": [], "wait": {}}
common.register_busy("lock change", lambda: guard["busy"])


# ---------- cell specs ----------
def spec_cells(spec):
    """`'434@1550'` -> `[('434', '1550')]` (pci, channel)."""
    return [tuple(c.split("@")) for c in spec.split(";")]


def lock_target(spec):
    """`'434@1550'` -> `{'1550/434'}` (the `lte_cell` / `nr_cell` sample format)."""
    return {"%s/%s" % (e, p) for p, e in spec_cells(spec)}


def nr_band(arfcn):
    """5G ARFCN -> (band, SCS kHz) for the bands Yettel uses; ValueError otherwise."""
    a = int(arfcn)
    if 620000 <= a <= 653333: return 78, 30
    if 151600 <= a <= 160600: return 28, 15
    raise ValueError("no 5G band known for ARFCN %s" % arfcn)


def holds(raw, cell):
    """Does a modem lock value (`'340,100'`, `'534,636576,78,30'`) hold `cell`? None when the value is unknown."""
    if raw is None: return None
    pci, ch = spec_cells(cell)[0]
    return {pci, ch} <= set(re.findall(r"\d+", raw))


def pause_end(now):
    """Epoch when the LOCK_PAUSE window around `now` ends, None outside it (or no LOCK_PAUSE). Windows may cross midnight."""
    if not LOCK_PAUSE: return None
    a, b = LOCK_PAUSE.split("-"); lt = time.localtime(now); t = time.strftime("%H:%M", lt)
    if not ((a <= t < b) if a <= b else (t >= a or t < b)): return None
    h, m = map(int, b.split(":")); end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    return end if end > now else end + 86400                  # ponytail: on a DST night the end is an hour off - harmless


# ---------- applying ----------
def lock_do(changes, why, count=True):
    """Apply `{"lte": spec|'none', "nr": spec|'none'}` together in the background (one reboot / one script).
    count=False: not counted against LOCK_MAX_REBOOTS_DAY (bounded callers: the once-a-day quality A/B)."""
    if guard["busy"]: return False
    guard["busy"] = True
    what = " + ".join("%s lock %s" % ("lte" if k == "lte" else "5g", "removed" if v == "none" else v) for k, v in sorted(changes.items()))
    def run():
        try:
            method = devices.current().locks_apply(changes.get("lte"), changes.get("nr"))
            if count and method != "unchanged": guard["reboots"] = [t for t in guard["reboots"] if time.time() - t < 86400] + [time.time()]   # count the reboot only after it was issued
            guard["changed_at"] = time.time()
            guard.update(bad=0, nosvc=0, nr_bad=0, nr_back=0)
            log("%s: %s" % (what, why)); event({"event": what, "why": why})
        except Exception as e:
            log("%s failed: %r" % (what, e))
        finally:
            guard["busy"] = False
    threading.Thread(target=run, daemon=True).start()
    return True


LOCK_RECONCILE_SETTLE = float(E("LOCK_RECONCILE_SETTLE_S", "120"))   # after a keeper lock change the modem reboots; its lock read-back is stale until ~this long


def lock_reconcile(now, row=None):
    """The keeper's lock state must match what the modem really has (a failed write, a power cut, a lock changed by hand):
    released / pending but the modem already has it -> active (no reboot); active but the modem has no lock -> pending.
    `row` (the current sample) keeps the NSA idle release from looking like a lost 5G lock: the ZTE drops the 5G leg when the link
    is idle and its `LockNr5gCell` read-back is empty then, while the lock is still configured (2026-11-21: a reboot every 5 min)."""
    dev = devices.current()
    for key in KEYS.values():
        if key == "nr_lock" and row and row.get("nr_cell") == "no5G" and brain.idle(row):
            continue                                  # idle without 5G is normal in NSA: the lock is not lost, do not re-apply (one reboot each time)
        w = state().get(key); lv = dev.lock_value(key) if w else None
        has = holds(lv[1], w["cell"]) if lv else None
        if has and key == "lte_lock" and hasattr(dev, "profile") and dev.profile.low_band(w["cell"]):
            try:
                raw = dev.profile.snapshot()
                has = bool(raw["prior_band"] and raw["raw"][dev.profile.BAND] == dev.profile.low_band(w["cell"]))
            except RuntimeError:
                continue
        if has is None: continue
        label, raw = lv
        if w["state"] in ("released", "pending") and has:
            if guard.get("changed_at") and now < guard["changed_at"] + LOCK_RECONCILE_SETTLE:
                continue                                  # a just-issued change is still rebooting: this read-back is stale, don't flip back to active
            state({key: dict(w, state="active", since=now)}); log("%s %s: the %s still has it - marked active, no reboot" % (key, w["cell"], dev.NAME))
            if w["state"] == "pending": continue          # 2026-11-14 03:07: after a night screen the lock was never lost - a saved reboot, not an alert
            event({"event": "%s %s still set" % (key.replace("_", " "), w["cell"]), "why": "%s %s = %r (an unlock did not go through)" % (dev.NAME, label, raw)})
        elif w["state"] == "active" and not has:
            state({key: dict(w, state="pending")}); log("%s %s: the %s has no such lock (%s = %r) - set again when idle" % (key, w["cell"], dev.NAME, label, raw))
            event({"event": "%s %s missing" % (key.replace("_", " "), w["cell"]), "why": "%s %s = %r" % (dev.NAME, label, raw)})


def lock_guard(row, now=None):
    """Called with every sample: release a lost lock, restore / set one when idle. Returns 'release' / 'restore' / 'set' / None."""
    now = now or time.time()
    if not common.busy(): lock_reconcile(now, row)
    want = {k: state().get(key) for k, key in KEYS.items()}
    if not any(want.values()) or common.busy(): return None
    idle = brain.idle(row)
    booted = (brain.f(row.get("uptime_s")) or 0) < 300                  # just booted: let it settle
    rel, whys = {}, []
    w = want["lte"]
    if w and w.get("state") == "active" and not booted:
        targets = lock_target(w["cell"]); on = row.get("lte_cell") in targets
        nosvc = not row.get("lte_cell") or row.get("lte_cell") == "/"
        ca = brain.parse_ca(row.get("lte_ca")); a_sinr = ca[0][4] if ca else None
        poor = str(row.get("call_ok")) == "0" and a_sinr is not None and a_sinr <= 0
        guard["nosvc"] = guard["nosvc"] + 1 if nosvc else 0
        guard["bad"] = guard["bad"] + 1 if (not on or poor) else 0
        why, retry, pe = None, now + LOCK_RETRY_H * 3600, pause_end(now) if common.clock_ok() else None     # old clock after a power cut: no pause release
        if pe and idle: why, retry = "LOCK_PAUSE %s (the cell is switched off at night) until %s" % (LOCK_PAUSE, time.strftime("%H:%M", time.localtime(pe))), pe
        elif guard["nosvc"] >= LOCK_NOSERVICE_MIN: why = "no LTE service for %d min" % guard["nosvc"]
        elif guard["bad"] >= LOCK_BAD_MIN: why = ("not on %s" % "/".join(sorted(targets)) if not on else "calls at risk with anchor SINR %s" % a_sinr) + " for %d min" % guard["bad"]
        if why: rel["lte"] = "none"; whys.append("LTE %s: %s" % (w["cell"], why)); state({"lte_lock": dict(w, state="released", released_at=now, retry_at=retry, reason=why)})
        wn = want["nr"]
        if why and pe and idle and wn and wn.get("state") == "active":   # the pause frees 5G too, in the same write: the night's band scan +
            rel["nr"] = "none"; whys.append("5G %s: %s" % (wn["cell"], why))   # steps see the NSA pairs the network gives (2026-11-21)
            state({"nr_lock": dict(wn, state="released", released_at=now, retry_at=retry, reason=why, pause=True)}); want["nr"] = None
    w = want["nr"]
    if w and w.get("state") == "active" and not booted:
        if devices.current().nr_on(row, w["cell"]): guard["nr_bad"] = 0
        elif not (row.get("nr_cell") == "no5G" and brain.idle(row)): guard["nr_bad"] += 1     # idle without 5G is normal (NSA): no count
        if guard["nr_bad"] >= NR_LOCK_LOST_MIN:
            why = "5G not on it for %d min (now %s)" % (guard["nr_bad"], row.get("nr_cell"))
            rel["nr"] = "none"; whys.append("5G %s: %s" % (w["cell"], why)); state({"nr_lock": dict(w, state="released", released_at=now, retry_at=now + LOCK_RETRY_H * 3600, reason=why)})
    if rel:
        guard["wait"].update({k: 0 for k in rel})            # the release is logged; the wait starts after it
        lock_do(rel, "released %s - restored when the cell is back (%s)" % ("; ".join(whys), "after the pause" if "LOCK_PAUSE" in whys[0]
                                                                          else "after %g h at the earliest" % LOCK_RETRY_H))
        return "release"
    w = want["nr"]                                                       # 5G released: served by the cell again (unlocked) = back
    if w and w.get("state") == "released": guard["nr_back"] = guard["nr_back"] + 1 if devices.current().nr_on(row, w["cell"]) else 0
    if not idle or len([t for t in guard["reboots"] if now - t < 86400]) >= LOCK_MAX_REBOOTS_DAY: return None
    todo, whys = {}, []
    for k, w in want.items():
        if not w or ((k == "lte" or w.get("pause")) and LOCK_PAUSE and (pause_end(now) or not common.clock_ok())): continue      # no lock inside LOCK_PAUSE (or before the clock is verified)
        if w.get("state") == "pending": todo[k] = w["cell"]; whys.append("%s set" % w["cell"])
        elif w.get("state") == "released" and now >= w.get("retry_at", 0):
            if k == "lte":
                seen = {"%s/%s" % m.group(1, 2): brain.f(m.group(3)) for m in re.finditer(r"(\d+)/(\d+):(-?[\d.]+)", row.get("lte_neighbours") or "")}
                back = [t for t in lock_target(w["cell"]) if t in seen and ((brain.REG["lte"].get(t) or {}).get("rsrp") is None
                                                                            or seen[t] >= brain.REG["lte"][t]["rsrp"] - 6)]
                if back: todo[k] = w["cell"]; whys.append("%s restored - back: %s" % (w["cell"], ", ".join("%s (RSRP %s)" % (t, seen[t]) for t in back)))
                else: wait_note("lte", w, "%s not in the neighbour list yet" % w["cell"], now)
            elif w.get("pause"):                       # freed by the pause: back with the LTE lock, in the same write
                if "lte" in todo or (want.get("lte") or {}).get("state") != "released":
                    todo[k] = w["cell"]; whys.append("%s restored - the pause is over" % w["cell"])
                else: wait_note("nr", w, "back with the LTE lock", now)
            elif guard["nr_back"] >= NR_BACK_MIN:
                todo[k] = w["cell"]; whys.append("%s restored - 5G served by it again for %d min" % (w["cell"], guard["nr_back"]))
            else:
                wait_note("nr", w, "5G not served by it again yet (%d min)" % guard["nr_back"], now)
    if not todo: return None
    for k in todo: guard["wait"].pop(k, None); state({KEYS[k]: dict(want[k], state="active", since=now)})
    restore = any(want[k].get("state") == "released" for k in todo)
    lock_do(todo, "; ".join(whys))
    return "restore" if restore else "set"


ARMS = {"site": "another site", "free": "the modem's own choice"}


def _site(r):
    return str(r.get("site") or "").split("/")[0]                    # '1243/81' -> eNB 1243


def ab_alt(cur, row, now):
    """The best lock-tested LTE anchor on ANOTHER site (eNB) that the modem saw in the last 6 h, or None. When a site's backhaul is
    the trouble (rain on its microwave link, a power cut), every cell of that site has it - the modem's own choice (strongest)
    would stay there, so the A/B tries another site first. B cell (docs/NIGHTLY-PLAN.md item 6): daytime results only (anchor_ranking
    drops night rows), with a complete current-method load score and observed 5G pairing."""
    rk = brain.anchor_ranking()["ranked"]
    here = next((_site(r) for r in rk if r["lock"] == cur), None) or _site({"site": row.get("lte_site")})
    rk = sorted((r for r in rk if r.get("load_method") == brain.LTE_LOAD_METHOD and (brain.f(r.get("load_s")) or 0) > 0 and r.get("score") is not None
                 and r.get("score_parts", {}).get("5g")), key=lambda r: r["score"], reverse=True)
    for r in rk:
        e = brain.REG["lte"].get(r.get("anchor") or "") or {}
        if r["lock"] != cur and _site(r) and _site(r) != here and now - e.get("last", 0) <= 6 * 3600: return r["lock"]
    return _registry_alt(here, now)


def _registry_alt(here, now):
    """No lock-tested anchor on another site: the B1/B3/B7 cell of another eNB that served as the anchor longest and was seen in the
    last 6 h, cells with an n78 pair first (a low band needs the band-restricted profile). Radio history only - for an outage or a
    20-min trial, where any working site beats the current one; the trial result decides what is kept."""
    n78 = {a for c, e in (brain.REG.get("nr") or {}).items() if brain.nr_band_of(c.split("/")[0]) == "n78" for a in (e.get("anchors") or {})}
    best = None
    for cell, e in (brain.REG.get("lte") or {}).items():
        if (not _site(e) or _site(e) == here or (brain.f(e.get("anchor_min")) or 0) <= 0 or now - (e.get("last") or 0) > 6 * 3600
                or brain.band_of(cell.split("/")[0]) not in ("B1", "B3", "B7")): continue
        key = (cell in n78, brain.f(e.get("anchor_min")) or 0, brain.f(e.get("rsrp")) or -200)
        if best is None or key > best[0]: best = (key, cell)
    if not best: return None
    earfcn, pci = best[1].split("/")
    return "%s@%s" % (pci, earfcn)


def _ab_apply(ab, arm, now):
    """Put a trial arm on the modem: one reboot, not counted against LOCK_MAX_REBOOTS_DAY (the A/B is bounded: once a day)."""
    o = ab["orig"]; mark = dict(released_at=now, retry_at=now + 10 * 3600, ab=True, reason=ab["why"])
    if arm == "site": state({"lte_lock": {"cell": ab["alt"], "state": "active", "since": now, "ab": True, "reason": ab["why"]}}); ch = {"lte": ab["alt"]}
    else: state({"lte_lock": dict(o["lte"], state="released", **mark)}); ch = {"lte": "none"}
    if o.get("nr") and not (state().get("nr_lock") or {}).get("ab"):
        state({"nr_lock": dict(o["nr"], state="released", **mark)}); ch["nr"] = "none"
    lock_do(ch, "%s - trial: %s" % (ab["why"], "LTE %s on %s" % (ab["alt"], ARMS["site"]) if arm == "site" else ARMS["free"]), count=False)
    return dict(ab, arm=arm, start=now)


def _ab_restore(ab, now):
    """Your locks back: pending -> the guard's next idle minute writes both legs at once, ONE reboot. Not 'released': each leg then
    waited for its own 'back' check and got its own reboot (2026-11-17: 17:23:53 the 5G, 17:26:53 the LTE). A lock changed by hand stays.
    You were unlocked: the trial lock is removed now (the guard keeps no lock to restore). False = not started (a lock change runs)."""
    if not any(ab["orig"].values()):
        w = state().get("lte_lock")
        if w and w.get("ab"):
            if not lock_do({"lte": "none"}, "A/B over - back to unlocked", count=False): return False
            state({"lte_lock": None})
        return True
    for k, o in ab["orig"].items():
        w = state().get(KEYS[k])
        if o and w and w.get("ab"):
            state({KEYS[k]: {x: v for x, v in dict(o, state="pending", requested=now, reason="A/B over").items() if x not in ("released_at", "retry_at")}})
    return True


def site_down(now, rows=None):
    """No internet while the modem stays registered: every probe of the last 2 min failed and the last 3 samples have an LTE cell
    and are not call-ready. The serving site's backhaul or what is behind it - the modem keeps the strongest cell, so only a lock
    to another site moves it (a site that goes off the air is left by the modem itself)."""
    rows = (list(brain.HIST) if rows is None else list(rows))[-3:]
    return (len(rows) == 3 and brain.no_internet(now)
            and all(r.get("lte_cell") not in (None, "", "/") and str(r.get("call_ok")) == "0" for r in rows))


def quiet(rows=None, row=None):
    """No call on the line: not every one of the last AB_QUIET_MIN samples carries >= AB_CALL_BYTES/s in the
    same direction - a call is steady, bursts are not - and the current minute is idle. Fewer samples: not proven."""
    rows = (list(brain.HIST) if rows is None else list(rows))[-AB_QUIET_MIN:]
    if len(rows) < AB_QUIET_MIN or (row and not brain.idle(row)): return False        # this minute in use (a download): no reboot
    return not any(all((brain.f(r.get(k)) or 0) >= AB_CALL_BYTES for r in rows) for k in ("up_bps", "down_bps"))


def quality_ab(row, now=None):
    """Minute job, at most once a day. Calls go bad while the signal is good and the link idle (2026-11-12 evening: call-ready
    77 -> 20 % on 340@100 + 534, SINR 5 / 11, ~0 Mbit/s, 1-s stalls from lost packets; unstable weather) - not our load, not the
    radio, so likely the site's backhaul. Trials of AB_TRIAL_MIN each (one reboot each, at an idle minute):
      1. the best tested anchor on another site (ab_alt), 5G unlocked;  2. everything unlocked (the modem's own choice).
    Trial 1 already >= 90 % and +15 pp -> no trial 2. The best trial clearly better (+15 pp) -> kept AB_KEEP_H, then your locks
    come back; none better -> your locks back now and a 'provider-wide' note. Unlocked (no LTE or 5G lock): trial 1 only, and the
    anchor's SINR is no gate (B7 340@3350 serves at ~1 dB). An outage (site_down: no internet while registered) starts it at once,
    with no window, call or once-a-day wait, up to SITE_OUTAGE_MAX_DAY times in 24 h, 30 min apart; after AB_KEEP_H the home
    site is tried again. Returns 'trial' / 'kept' / 'restored' / None."""
    now = now or time.time(); ab = state().get("quality_ab") or {}; day = time.strftime("%Y-%m-%d", time.localtime(now))
    ph = ab.get("phase")
    if ph in ("trial", "keep") and not all((state().get(KEYS[k]) or {}).get("ab") for k in ([k for k, o in ab["orig"].items() if o] or ["lte"])):
        _ab_restore(ab, now)                                       # you changed a lock: stop, the other one goes back to yours
        state({"quality_ab": dict(ab, phase="done", result="stopped: locks changed by hand")}); return None
    if ph == "keep":
        if now < ab["until"] or not quiet(row=row): return None                  # a call on the line: your locks come back after it
        if not _ab_restore(ab, now): return None
        state({"quality_ab": dict(ab, phase="done")})
        event({"event": "lte lock A/B", "to": "back to your locks", "why": "%g h on %s are over" % (AB_KEEP_H, ARMS[ab["arm"]])})
        return "restored"
    if ph == "trial":
        if now - ab["start"] < AB_TRIAL_MIN * 60: return None
        pct = brain.share([r for r in list(brain.HIST) if brain.ts(r) >= ab["start"] + 300])       # skip the reboot
        if pct is None and now - ab["start"] < 2 * AB_TRIAL_MIN * 60: return None
        res = dict(ab.get("res") or {}, **{ab["arm"]: pct})
        nxt = [x for x in ab["arms"] if x not in res]
        if nxt and not (pct is not None and pct >= max(ab["before"] + 15, 90)):
            if quiet(row=row) and not common.busy():
                state({"quality_ab": _ab_apply(dict(ab, res=res), nxt[0], now)}); return "trial"
            if now - ab["start"] < 2 * AB_TRIAL_MIN * 60: return None                                  # in use: wait for a quiet line
        if not quiet(row=row): return None            # the winner back / your locks back = a reboot: never during a call
        txt = ", ".join("%s %s%%" % ("LTE %s (%s)" % (ab["alt"], ARMS[x]) if x == "site" else ARMS[x], res[x]) for x in res) + \
            " vs %s%% on your locks" % ab["before"]
        best = max((x for x in res if res[x] is not None), key=lambda x: res[x], default=None)
        if best and res[best] >= ab["before"] + 15:
            if best != ab["arm"]: ab = _ab_apply(ab, best, now)                                          # the winner back (one reboot)
            state({"quality_ab": dict(ab, res=res, phase="keep", arm=best, until=now + AB_KEEP_H * 3600, result=best)})
            log("quality A/B: kept %s - %s" % (ARMS[best], txt))
            event({"event": "lte lock A/B", "to": "kept %s for %g h" % ("LTE %s (another site)" % ab["alt"] if best == "site" else "unlocked", AB_KEEP_H), "why": txt})
            return "kept"
        if not _ab_restore(ab, now): return None
        state({"quality_ab": dict(ab, res=res, phase="done", result="restored")})
        log("quality A/B: no better - %s" % txt)
        event({"event": "lte lock A/B", "to": "locks restored", "why": txt + " - neither another site nor the modem's own choice helps:"
               " the provider's side, nothing to fix from here"})
        return "restored"
    if not QUALITY_AB or state().get("dev") or common.busy() or not common.clock_ok() or pause_end(now): return None
    w, nrw = state().get("lte_lock"), state().get("nr_lock")
    if (w.get("state") != "active" if w else nrw) or (brain.f(row.get("uptime_s")) or 0) < 600: return None   # unlocked = both locks off
    if len([t for t in guard["reboots"] if now - t < 86400]) >= LOCK_MAX_REBOOTS_DAY: return None      # the restore needs one
    outages = [t for t in ab.get("outages") or [] if now - t < 86400]
    if site_down(now):                          # no internet while registered: another site now, a call is impossible anyway
        if len(outages) >= SITE_OUTAGE_MAX or now - (ab.get("start") or 0) < SITE_OUTAGE_GAP_S: return None
        rows = list(brain.HIST)[-3:]; outages.append(now)
        lte, nr = brain.med([brain.f(r.get("lte_sinr")) for r in rows]), brain.med([brain.f(r.get("nr_sinr")) for r in rows])
        why = "A/B: no internet for 2 min while registered on %s (anchor SINR %s, 5G %s)" % (row.get("lte_cell"), lte, nr)
    else:
        if ab.get("day") == day: return None
        rows = list(brain.HIST)[-AB_WINDOW_MIN:]
        ok = brain.share(rows)
        if len(rows) < AB_WINDOW_MIN - 5 or ok is None or ok >= AB_BAD_PCT: return None
        if sum(1 for r in rows if brain.idle(r)) < 0.8 * len(rows) or not brain.idle(row): return None      # our own traffic: the SQM's job
        if not quiet(row=row): return None                                                                  # a call: never reboot under it
        lte, nr = brain.med([brain.f(r.get("lte_sinr")) for r in rows]), brain.med([brain.f(r.get("nr_sinr")) for r in rows])
        if lte is None or (w and lte < brain.MIN_SINR) or (nr is not None and nr < brain.MIN_SINR): return None   # weak signal: the guard's job
        why = "A/B: call-ready %s%% in %d min with good signal (anchor SINR %s, 5G %s) on an idle link" % (ok, len(rows), lte, nr)
    alt = ab_alt(w["cell"] if w else "none", row, now)
    arms = (["site"] if alt else []) + (["free"] if w else [])          # unlocked already is the modem's own choice
    if not arms: return None
    ab = {"day": day, "phase": "trial", "before": brain.share(rows) or 0.0, "alt": alt, "arms": arms, "outages": outages,
          "orig": {"lte": w, "nr": nrw if w and (nrw or {}).get("state") == "active" else None}, "why": why}
    state({"quality_ab": _ab_apply(ab, ab["arms"][0], now)})
    return "trial"


def lte_held():
    """An LTE lock the keeper holds (set or to be set) - not released."""
    w = state().get("lte_lock"); return bool(w) and w.get("state") != "released"


def offline_release(quiet_s, now=None):
    """Called by the ACS watchdog every minute. The guard runs on samples, and samples come from the modem's informs: a ZTE locked to
    a cell that is gone has no WAN and never informs, so the guard never runs (2026-11-13: power back ~04:00 inside the night
    switch-off, router clock stale so no LOCK_PAUSE, 340@100 still in the modem - 25 min down until released by hand).
    No inform for OFFLINE_RELEASE_MIN (counted from the keeper start too) + every latency probe of the last 2 min failed + an LTE
    lock held + nothing busy (no lock test / sweep / lock change) -> release the LTE lock on the web page (LAN, no TR-069 session)
    and reboot; it is restored later like any release, once its cell is back. The 5G lock stays (it does not block the LTE attach).
    One try per 10 min. True when it released."""
    now = now or time.time()
    w = state().get("lte_lock")
    if not w or w.get("state") == "released" or quiet_s < OFFLINE_RELEASE_MIN * 60 or common.busy(): return False
    if not brain.no_internet(now): return False                                  # internet works (or no probe): not this case
    unlock = getattr(devices.current(), "offline_unlock_lte", None)
    if not unlock or now - guard.get("offline_try", 0) < 600: return False
    guard["offline_try"] = now
    why = "no inform for %d min and no internet - the locked cell is gone" % (quiet_s // 60)
    try: unlock()
    except Exception as e:
        log("offline release of LTE %s failed: %r - next try in 10 min" % (w["cell"], e)); return False
    guard["reboots"] = [t for t in guard["reboots"] if now - t < 86400] + [now]; guard["changed_at"] = now
    state({"lte_lock": dict(w, state="released", released_at=now, retry_at=now + LOCK_RETRY_H * 3600, reason=why)})
    log("lte lock removed: released LTE %s: %s" % (w["cell"], why))
    event({"event": "lte lock removed", "why": "released LTE %s: %s - restored when the cell is back (web page, offline)" % (w["cell"], why)})
    return True


def wait_note(kind, w, why, now):
    """A released lock whose cell is not back yet: say so once an hour. Without this the events stay silent from the release until
    the restore (2026-11-03: 340@100 released by LOCK_PAUSE at 00:50, back in the neighbours 08:18, restored 08:24 - nothing in
    between, so the morning status could not explain the wait)."""
    if now - (guard["wait"].get(kind) or 0) < 3600: return
    guard["wait"][kind] = now
    log("%s lock %s: restore pending - %s" % (kind, w["cell"], why))
    event({"event": "lock restore pending", "to": "%s %s" % (kind, w["cell"]), "why": why})


def lock_request(cell, kind="lte"):
    """API: `cell` = 'pci@channel' -> keep it locked (set when the link is idle); 'none' -> remove. RuntimeError for an unknown 5G band."""
    key = KEYS[kind]
    if cell == "none":
        had = state().get(key); state({key: None})
        if had or (kind == "lte" and state().get("lte_prior_band")): lock_do({kind: "none"}, "removed on request")
        return {key: None}
    if kind == "nr":
        try: nr_band(cell.split("@")[1])
        except ValueError as e: raise RuntimeError(str(e))
    state({key: {"cell": cell, "state": "pending", "requested": time.time()}})
    return {key: state()[key]}


def boot_lost():
    """For modems that forget locks on a reboot (the ATL): active locks -> pending, the guard sets them again when idle."""
    for key in KEYS.values():
        w = state().get(key)
        if w and w.get("state") == "active":
            state({key: dict(w, state="pending")}); log("%s %s: lost with the reboot - set again when idle" % (key, w["cell"]))
