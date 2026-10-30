"""Shared runtime of the keeper: environment, data dir, log, small state file, records and events, "busy" registry.

Every other module uses these instead of its own copies, so log lines, record formats and the per-device state file stay one thing.
Env: DATA (/keeper/data), MAX_MB (30 - per log file before it rotates to `.1`), RAW=1 (log every TR-069 message, contains IMEI / IMSI),
CLOCK_MAX_SKEW_S (300; 0 = trust the router clock unchecked).

Clock: the RB450 has no battery clock - after a power cut RouterOS starts with an old time and NTP fixes it only once the ZTE has
internet (2026-10-30: 18:00 instead of 06:25 for 75 s). The keeper's clock counts as verified only while the modem's own clock
(Inform CurrentTime) agrees with it; time-of-day actions (LOCK_PAUSE, night screen) wait for that. Intervals use time.monotonic().
"""
import calendar, datetime, json, os, re, threading, time

from keeper import alert, store

E = os.environ.get
DATA = E("DATA", "/keeper/data")
MAX_BYTES = int(float(E("MAX_MB", "30")) * 1e6)
DEVICE = store.DEVICE
CLOCK_MAX_SKEW = float(E("CLOCK_MAX_SKEW_S", "300"))
clock = {"ok": None, "skew": None, "bad_since": None}
EVENT_COLS = ["time", "uptime_s", "event", "from", "to", "nr_sinr_before", "lte_cell", "lte_sinr", "why"]
_state_lock = threading.Lock()


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
    skew = cpe_skew(ct, now)
    if skew is None: return clock_ok()                  # no readable modem time in this Inform (or a foreign/odd CPE): no evidence, leave the verdict
    ok = abs(skew) <= CLOCK_MAX_SKEW
    was = clock["ok"]; clock.update(ok=ok, skew=skew)
    if ok == was: return ok
    off = "unreadable (%r)" % ct if skew is None else "%+.0f min" % (skew / 60)
    if not ok:
        clock["bad_since"] = stamp()
        clock["bad_off"] = "unverifiable (modem time %r)" % ct if skew is None else "%.0f min %s" % (abs(skew) / 60, "behind" if skew > 0 else "ahead")
        log("clock not verified: the modem's clock is %s from ours - LOCK_PAUSE / night screen wait" % off)
        event({"event": "clock not verified", "why": "modem clock %s from the router's (router booted without NTP?)" % off})
    elif was is False:
        log("clock verified again (modem %s)" % off)
        event({"event": "clock verified", "why": "the router's clock was %s (booted without NTP, e.g. after a power cut); LOCK_PAUSE / night "
                                                 "screen waited; records stamped from %s until now carry the old time" % (clock.get("bad_off"), clock["bad_since"])})
    return ok


def clock_ok():
    """Is the wall clock trustworthy for time-of-day decisions?"""
    return not CLOCK_MAX_SKEW or bool(clock["ok"])


def rotate(path):
    """Move `path` to `path.1` once it is over MAX_MB (one old copy kept)."""
    if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
        os.replace(path, path + ".1")


def log(msg):
    """Print (container log) and append to `keeper.log`."""
    print(stamp(), msg, flush=True)
    path = os.path.join(DATA, "keeper.log"); rotate(path)
    with open(path, "a") as f: f.write("%s,%s\n" % (stamp(), msg))


def raw(head, body):
    """With RAW=1: append one TR-069 message to `tr069-raw.log` (debugging only - holds IMEI / IMSI / ICCID)."""
    if E("RAW") == "1":
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
            with open(path + ".tmp", "w") as fh: json.dump(s, fh)
            os.replace(path + ".tmp", path)
    return s


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


def register_busy(name, check, fast=True):
    """`check()` is truthy while job `name` runs (lock test, lock change, antenna sweep, TR-069 write, script download).
    fast=False: the job doesn't need fast informs (it works on the web page, not over TR-069)."""
    _busy[name] = (check, fast)


def busy(fast_only=False):
    """Name of a running job, or None. Jobs that reboot / re-configure the modem start only when this is None."""
    return next((n for n, (check, fast) in _busy.items() if (fast or not fast_only) and check()), None)
