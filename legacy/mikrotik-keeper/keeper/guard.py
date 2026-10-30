"""Lock guard: keep the chosen LTE anchor and 5G cell locked; release a lock when its cell is lost, restore it when it is back.

Device neutral - the driver sets the locks (`locks_apply`) and says what the modem itself holds (`lock_value`).
Every lock change may cost a reboot (~2 min offline on the ZTE): set / restore only while the link is idle and at most
LOCK_MAX_REBOOTS_DAY a day; a release happens at once (the link is degraded then). LTE + 5G changes due together = one change.
State per lock in the state file: `{"cell": "340@100", "state": "pending|active|released", ...}` under `lte_lock` / `nr_lock`.

Env: LOCK_NOSERVICE_MIN (3), LOCK_BAD_MIN (10), NR_LOCK_LOST_MIN (10), LOCK_RETRY_H (1), NR_BACK_MIN (3), LOCK_MAX_REBOOTS_DAY (4),
LOCK_PAUSE ("00:50-06:10": the LTE lock is released before this window and restored after it, once its cell is back - for a cell the
operator switches off at night; measured 2026-10-28 / 29: B1 340@100 gone ~00:59-01:03 until ~06:00, a 3-min outage each night).
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
KEYS = {"lte": "lte_lock", "nr": "nr_lock"}
SPEC_RE = r"\d{1,3}@\d{1,6}"      # PCI@EARFCN, e.g. 434@1550 (one cell - the MC889 keeps only the first cell of a list)
guard = {"bad": 0, "nosvc": 0, "nr_bad": 0, "nr_back": 0, "busy": False, "reboots": []}
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
def lock_do(changes, why):
    """Apply `{"lte": spec|'none', "nr": spec|'none'}` together in the background (one reboot / one script)."""
    if guard["busy"]: return
    guard["busy"] = True; guard["reboots"] = [t for t in guard["reboots"] if time.time() - t < 86400] + [time.time()]
    what = " + ".join("%s lock %s" % ("lte" if k == "lte" else "5g", "removed" if v == "none" else v) for k, v in sorted(changes.items()))
    def run():
        try:
            devices.current().locks_apply(changes.get("lte"), changes.get("nr")); guard.update(bad=0, nosvc=0, nr_bad=0, nr_back=0)
            log("%s: %s" % (what, why)); event({"event": what, "why": why})
        except Exception as e:
            log("%s failed: %r" % (what, e))
        finally:
            guard["busy"] = False
    threading.Thread(target=run, daemon=True).start()


def lock_reconcile(now):
    """The keeper's lock state must match what the modem really has (a failed write, a power cut, a lock changed by hand):
    released / pending but the modem already has it -> active (no reboot); active but the modem has no lock -> pending."""
    dev = devices.current()
    for key in KEYS.values():
        w = state().get(key); lv = dev.lock_value(key) if w else None
        has = holds(lv[1], w["cell"]) if lv else None
        if has is None: continue
        label, raw = lv
        if w["state"] in ("released", "pending") and has:
            state({key: dict(w, state="active", since=now)}); log("%s %s: the %s still has it - marked active, no reboot" % (key, w["cell"], dev.NAME))
            event({"event": "%s %s still set" % (key.replace("_", " "), w["cell"]), "why": "%s %s = %r (an unlock did not go through)" % (dev.NAME, label, raw)})
        elif w["state"] == "active" and not has:
            state({key: dict(w, state="pending")}); log("%s %s: the %s has no such lock (%s = %r) - set again when idle" % (key, w["cell"], dev.NAME, label, raw))
            event({"event": "%s %s missing" % (key.replace("_", " "), w["cell"]), "why": "%s %s = %r" % (dev.NAME, label, raw)})


def lock_guard(row, now=None):
    """Called with every sample: release a lost lock, restore / set one when idle. Returns 'release' / 'restore' / 'set' / None."""
    now = now or time.time()
    if not common.busy(): lock_reconcile(now)
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
    w = want["nr"]
    if w and w.get("state") == "active" and not booted:
        if devices.current().nr_on(row, w["cell"]): guard["nr_bad"] = 0
        elif not (row.get("nr_cell") == "no5G" and brain.idle(row)): guard["nr_bad"] += 1     # idle without 5G is normal (NSA): no count
        if guard["nr_bad"] >= NR_LOCK_LOST_MIN:
            why = "5G not on it for %d min (now %s)" % (guard["nr_bad"], row.get("nr_cell"))
            rel["nr"] = "none"; whys.append("5G %s: %s" % (w["cell"], why)); state({"nr_lock": dict(w, state="released", released_at=now, retry_at=now + LOCK_RETRY_H * 3600, reason=why)})
    if rel:
        lock_do(rel, "released %s - restored when the cell is back (%s)" % ("; ".join(whys), "after the pause" if "LOCK_PAUSE" in whys[0]
                                                                          else "after %g h at the earliest" % LOCK_RETRY_H))
        return "release"
    w = want["nr"]                                                       # 5G released: served by the cell again (unlocked) = back
    if w and w.get("state") == "released": guard["nr_back"] = guard["nr_back"] + 1 if devices.current().nr_on(row, w["cell"]) else 0
    if not idle or len([t for t in guard["reboots"] if now - t < 86400]) >= LOCK_MAX_REBOOTS_DAY: return None
    todo, whys = {}, []
    for k, w in want.items():
        if not w or (k == "lte" and LOCK_PAUSE and (pause_end(now) or not common.clock_ok())): continue      # no LTE lock inside LOCK_PAUSE (or before the clock is verified)
        if w.get("state") == "pending": todo[k] = w["cell"]; whys.append("%s set" % w["cell"])
        elif w.get("state") == "released" and now >= w.get("retry_at", 0):
            if k == "lte":
                seen = {"%s/%s" % m.group(1, 2): brain.f(m.group(3)) for m in re.finditer(r"(\d+)/(\d+):(-?[\d.]+)", row.get("lte_neighbours") or "")}
                back = [t for t in lock_target(w["cell"]) if t in seen and ((brain.REG["lte"].get(t) or {}).get("rsrp") is None
                                                                            or seen[t] >= brain.REG["lte"][t]["rsrp"] - 6)]
                if back: todo[k] = w["cell"]; whys.append("%s restored - back: %s" % (w["cell"], ", ".join("%s (RSRP %s)" % (t, seen[t]) for t in back)))
            elif guard["nr_back"] >= NR_BACK_MIN:
                todo[k] = w["cell"]; whys.append("%s restored - 5G served by it again for %d min" % (w["cell"], guard["nr_back"]))
    if not todo: return None
    for k in todo: state({KEYS[k]: dict(want[k], state="active", since=now)})
    restore = any(want[k].get("state") == "released" for k in todo)
    lock_do(todo, "; ".join(whys))
    return "restore" if restore else "set"


def lock_request(cell, kind="lte"):
    """API: `cell` = 'pci@channel' -> keep it locked (set when the link is idle); 'none' -> remove. RuntimeError for an unknown 5G band."""
    key = KEYS[kind]
    if cell == "none":
        had = state().get(key); state({key: None})
        if had: lock_do({kind: "none"}, "removed on request")
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
