"""Shared runtime of the keeper: environment, data dir, log, small state file, records and events, "busy" registry.

Every other module uses these instead of its own copies, so log lines, record formats and the per-device state file stay one thing.
Env: DATA (/keeper/data), MAX_MB (30 - per log file before it rotates to `.1`), RAW=1 (log every TR-069 message, contains IMEI / IMSI),
CLOCK_MAX_SKEW_S (300; 0 = trust the router clock unchecked), CLOCK_URL (http://www.google.com/generate_204: its HTTP Date verifies
our clock while unverified; empty = off).

Clock: the RB450 has no battery clock - after a power cut RouterOS starts with an old time and NTP fixes it only once the ZTE has
internet (2026-10-30: 18:00 instead of 06:25 for 75 s). The keeper's clock counts as verified only while the modem's own clock
(Inform CurrentTime) agrees with it; time-of-day actions (LOCK_PAUSE, night screen) wait for that. Intervals use time.monotonic().
"""
import calendar, datetime, email.utils, json, os, re, sys, threading, time, urllib.error, urllib.request

from keeper import alert, store

E = os.environ.get
DATA = E("DATA", "/keeper/data")
MAX_BYTES = int(float(E("MAX_MB", "30")) * 1e6)
DEVICE = store.DEVICE
CLOCK_MAX_SKEW = float(E("CLOCK_MAX_SKEW_S", "300"))
mono = time.monotonic        # the uptime clock (tests replay a real power cut through it)
CLOCK_URL = E("CLOCK_URL", "http://www.google.com/generate_204")   # its HTTP Date verifies our clock while it is not verified; empty = off
clock = {"ok": None, "skew": None, "bad_since": None, "bad_mono": None}
clock["start"] = (time.time(), mono())   # the keeper's start: its records are on the old clock from here until it is verified
EVENT_COLS = ["time", "uptime_s", "event", "from", "to", "nr_sinr_before", "lte_cell", "lte_sinr", "why"]
_state_lock = threading.Lock()
_log_lock = threading.Lock()


def stamp():
    """Local time as stored everywhere: `2026-10-28 01:12:52`."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def cpe_skew(ct, now=None):
    """Seconds the modem's Inform CurrentTime is ahead of our clock; None if unreadable. The ZTE sends local time with a wrong
    offset (`2026-10-28T12:28:50-02:00` at 12:28 EET), so both the local reading and the offset-aware one are tried - the closer counts."""
    now = now or time.time()
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$", (ct or "").strip())
    if not m: return None
    try:
        t = time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"); d = [time.mktime(t) - now]
        z = m.group(2)
        if z: d.append(calendar.timegm(t) - (0 if z == "Z" else (1 if z[0] == "+" else -1) * (int(z[1:3]) * 3600 + int(z[-2:]) * 60)) - now)
    except (ValueError, OverflowError):
        return None
    return min(d, key=abs)


def check_clock(ct, now=None):
    """Each Inform: compare our clock with the modem's; an event when it stops / starts agreeing."""
    now = now or time.time()
    skew = cpe_skew(ct, now)
    if skew is None: return clock_ok()                  # no readable modem time in this Inform (or a foreign/odd CPE): no evidence, leave the verdict
    ok = abs(skew) <= CLOCK_MAX_SKEW
    if clock["ok"] is True:     # verified in this run: a router reboot would have restarted the keeper, so it is the MODEM's clock
        clock["skew"] = skew    # (a modem booting with a wrong clock must not stop LOCK_PAUSE / night screen / tests)
        if not ok and not clock.get("modem_bad"):
            log("the modem's clock is %+.0f min from ours (verified) - its clock is wrong, ours stays trusted" % (skew / 60))
            event({"event": "modem clock wrong", "why": "modem clock %+.0f min from the router's, verified in this run - the router's time "
                                                       "stays trusted (LOCK_PAUSE / night screen / tests go on)" % (skew / 60)})
        clock["modem_bad"] = not ok
        return True
    was = clock["ok"]; clock["skew"] = skew
    if ok and was is False: return _verified(now, "the modem's time (it agrees now)")
    clock["ok"] = ok
    if ok == was or ok: return ok
    off = "%+.0f min" % (skew / 60)
    t0, m0 = clock["start"] if was is None else (now, mono())   # never verified in this run: wrong since the keeper's start
    clock.update(bad_since=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), bad_mono=m0, bad_offset=now - mono(), bad_skew=skew,
                 bad_off="%.0f min %s" % (abs(skew) / 60, "behind" if skew > 0 else "ahead"))
    log("clock not verified: the modem's clock is %s from ours - LOCK_PAUSE / night screen wait" % off)
    event({"event": "clock not verified", "why": "modem clock %s from the router's (router booted without NTP?)" % off})
    return ok


def _verified(now, by):
    """The clock agrees again. Whose was off: OUR wall clock jumped since the disagreement began (NTP stepped it; the monotonic
    clock does not move with it) = the router's, and the records in between carry its wrong time; it did not = the modem's."""
    clock.update(ok=True, net_said=False)
    if abs((now - mono()) - clock.get("bad_offset", now - mono())) <= CLOCK_MAX_SKEW:
        clock["modem_bad"] = abs(clock.get("skew") or 0) > CLOCK_MAX_SKEW
        log("clock verified by %s: ours was right, the modem's was off" % by)
        event({"event": "modem clock wrong", "why": "modem clock %+.0f min from ours since %s; %s confirms ours - nothing was stamped "
                                                   "wrong, LOCK_PAUSE / night screen / tests go on" % ((clock.get("bad_skew") or 0) / 60, clock.get("bad_since"), by)})
        return True
    log("clock verified again by %s" % by)
    # the wall clock jumped when NTP fixed it: only the monotonic clock knows how long the wrong stamps ran (2026-11-17: 47 s, not
    # "04:27 until now" - the records after the fix carry real times and sort among the wrong ones)
    dur = round(mono() - clock["bad_mono"]) if clock.get("bad_mono") is not None else 0
    end = (datetime.datetime.strptime(clock["bad_since"], "%Y-%m-%d %H:%M:%S") + datetime.timedelta(seconds=dur)).strftime("%H:%M:%S")
    event({"event": "clock verified", "why": "the router's clock was %s (booted without NTP, e.g. after a power cut) - confirmed by %s; "
                                             "LOCK_PAUSE / night screen waited; records stamped %s to at most %s carry that wrong time" % (
                                                 clock.get("bad_off"), by, clock["bad_since"], end)})   # exact: tools/powercut.py
    return True


def fetch_date(url=None):
    """The `Date` header of one HEAD request (plain HTTP: the container has no CA store), None without an answer (no WAN yet)."""
    url = CLOCK_URL if url is None else url
    if not url: return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5) as r: return r.headers.get("Date")
    except urllib.error.HTTPError as e: return e.headers.get("Date")
    except Exception: return None


def check_net(date, now=None):
    """The watchdog, each minute while the clock is not verified: the internet's time (HTTP `Date`) vs ours. Agrees -> verified
    (no waiting for the modem, whose clock can be the wrong one); far off -> the router's NTP has not synced yet: wait, said once."""
    now = now or time.time()
    try: skew = email.utils.parsedate_to_datetime(date).timestamp() - now if date else None
    except (TypeError, ValueError, IndexError): skew = None
    if skew is None or clock["ok"] is True: return clock_ok()
    if abs(skew) > CLOCK_MAX_SKEW:
        if not clock.get("net_said"): log("internet time: ours is %+.0f min off - waiting for the router's NTP" % (-skew / 60))
        clock["net_said"] = True; return clock_ok()
    if clock["ok"] is None: clock["ok"] = True; return True      # never disagreed: verified quietly
    return _verified(now, "the internet's time (HTTP Date)")


def clock_ok():
    """Is the wall clock trustworthy for time-of-day decisions?"""
    return not CLOCK_MAX_SKEW or bool(clock["ok"])


def rotate(path):
    """Move `path` to `path.1` once it is over MAX_MB (one old copy kept)."""
    if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
        os.replace(path, path + ".1")


def log(msg):
    """Write one complete container log line and append the same message to `keeper.log`."""
    with _log_lock:
        now = stamp()
        sys.stdout.write("%s %s\n" % (now, msg))
        sys.stdout.flush()
        path = os.path.join(DATA, "keeper.log"); rotate(path)
        with open(path, "a") as f: f.write("%s,%s\n" % (now, msg))


def raw(head, body):
    """With RAW=1: append one TR-069 message to `tr069-raw.log` (debugging only - holds IMEI / IMSI / ICCID)."""
    if E("RAW") == "1":
        with _log_lock:
            path = os.path.join(DATA, "tr069-raw.log"); rotate(path)
            with open(path, "a") as f: f.write("==== %s %s\n%s\n" % (stamp(), head, body))


def state_file():
    """Per device, so locks / antennas of the two modems never mix: `state.json` (ZTE), `state-mikrotik.json` (ATL)."""
    return "state.json" if DEVICE == "zte" else "state-%s.json" % DEVICE


def state(update=None):
    """Read the small persistent state (locks, last sweep, operator check-in ...); `update` merges keys and writes it back."""
    path = os.path.join(DATA, state_file())
    with _state_lock:
        try:
            with open(path) as fh: s = json.load(fh)
        except (OSError, ValueError):
            s = {}
        if update:
            s.update(update)
            with open(path + ".tmp", "w") as fh: json.dump(s, fh, separators=(",", ":"))
            os.replace(path + ".tmp", path)
    return s


RESET_STATE_KEYS = ("morning", "morning_trial", "morning_dead", "morning_last", "ab_smoke", "ab_refresh", "antenna_ab",
                    "antenna_sweep_loaded",
                    "night_plan", "night_armed", "night_armed_at", "night_last", "night_remind_last",
                    "night_smoke", "night_scan", "day_last", "day_smoke")


def reset_measurements():
    """Consume an operator-created marker before the probes and registry have started."""
    marker = os.path.join(DATA, ".reset-measurements")
    if not os.path.isfile(marker): return False
    store.reset_measurements(DATA)
    for name in ("cells.json", "cells-mikrotik.json"):
        path = os.path.join(DATA, name)
        if os.path.exists(path): os.unlink(path)
    for name in ("state.json", "state-mikrotik.json"):
        path = os.path.join(DATA, name)
        if not os.path.exists(path): continue
        with open(path) as fh: saved = json.load(fh)
        for key in RESET_STATE_KEYS: saved.pop(key, None)
        if (saved.get("quality_ab") or {}).get("phase") == "done": saved.pop("quality_ab")
        with open(path + ".tmp", "w") as fh: json.dump(saved, fh, separators=(",", ":"))
        os.replace(path + ".tmp", path)
    os.unlink(marker)
    return True


def record(kind, cols, row):
    """One record into keeper.db; only `cols` are kept, values as strings (as the CSV files had)."""
    store.add(DATA, kind, {c: "" if row.get(c) is None else str(row.get(c)) for c in cols})


def device_name():
    """Display name of the active device: ZTE / MikroTik."""
    return {"zte": "ZTE", "mikrotik": "MikroTik"}.get(DEVICE, DEVICE)


def event(row):
    """Store an event (switch, boot, lock change ...) and let alert.py decide whether it is worth a Telegram message."""
    record("events", EVENT_COLS, dict({"time": stamp()}, **row))
    alert.on_event(device_name(), row)


# ---------- busy registry: background jobs that must not overlap (each module registers its own) ----------
_busy = {}
test_start_lock = threading.Lock()  # morning and capacity probes check and reserve the same modem atomically


def register_busy(name, check, fast=True):
    """`check()` is truthy while job `name` runs (lock test, lock change, antenna sweep, TR-069 write, script download).
    fast=False: the job doesn't need fast informs (it works on the web page, not over TR-069)."""
    _busy[name] = (check, fast)


def busy(fast_only=False, exclude=None):
    """Name of a running job, or None. Jobs that reboot / re-configure the modem start only when this is None."""
    return next((n for n, (check, fast) in _busy.items() if n != exclude and (fast or not fast_only) and check()), None)
