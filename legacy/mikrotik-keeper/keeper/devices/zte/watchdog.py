"""The ZTE's own watchdog - web UI only, TR-069 does not expose it (2026-11-21: the whole Device. tree, 4,982 names, has no watchdog
object; the firmware's goform does: `net_link_detect_*` + `watch_dog_reboot_enable`, set via `WATCH_DOG_SWITCH`).

An enabled watchdog + a locked cell the modem cannot attach to = a reboot loop: no WAN, the ping fails, the modem reboots before the
keeper gets a proper boot inform. The keeper reads the setting after every boot (in the antenna apply's web session - one session,
like the page) and exposes it in /status + `GET /zte/watchdog`; a change is an event, so an operator (or a hand) turning it on is
visible before the next lock test.
"""
from keeper.common import event, log, stamp, state
from keeper.devices.zte.web import Web, web_lock

CMDS = ("net_link_detect_enable,net_link_detect_url,net_link_detect_time_gap,net_link_detect_ping_times,net_link_detect_timeout,"
        "watch_dog_reboot_enable")


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _parse(v):
    """The web values -> the setting the keeper stores."""
    return {"enabled": v.get("net_link_detect_enable") == "1", "host": v.get("net_link_detect_url", ""),
            "pings": _int(v.get("net_link_detect_ping_times")), "gap_s": _int(v.get("net_link_detect_time_gap")),
            "timeout_s": _int(v.get("net_link_detect_timeout")), "reboot": v.get("watch_dog_reboot_enable") == "1"}


def read_with(w):
    """Read on an already logged-in web session -> the stored setting (an event on a change); {'error': ...} on failure."""
    try: v = w.get(CMDS)
    except Exception as e: return {"error": "watchdog read failed: %r" % e}
    if not v or all(x in ("", None) for x in v.values()):      # empty = not logged in (the page took the session)
        return {"error": "watchdog not readable (the web session was not logged in)"}
    return note(_parse(v))


def read():
    """Log in, read, log out (the browser's ZTE session ends) -> the setting / {'error': ...}."""
    with web_lock:
        w = Web(); err = w.login()
        if err: return {"error": err}
        try: return read_with(w)
        finally: w.logout()


def note(v):
    """Store the setting; log/event when it turns on or off (once, not on every read)."""
    old = (state().get("watchdog") or {}).get("values")
    state({"watchdog": {"values": v, "time": stamp()}})
    if old == v: return v
    detail = "ping %s, %s failed pings every %s s (timeout %s s), reboot %s" % (
        v["host"] or "?", v["pings"] if v["pings"] is not None else "?", v["gap_s"] if v["gap_s"] is not None else "?",
        v["timeout_s"] if v["timeout_s"] is not None else "?", "on" if v["reboot"] else "off")
    if v["enabled"]:
        log("ZTE watchdog ENABLED: %s - a lock to a cell that cannot attach will reboot the modem" % detail)
        event({"event": "watchdog enabled", "to": detail})
    elif old and old.get("enabled"):
        log("ZTE watchdog disabled")
        event({"event": "watchdog disabled"})
    return v
