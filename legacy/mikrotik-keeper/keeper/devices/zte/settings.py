"""The ZTE's web-only settings (TR-069 has none of these): connection mode / roaming, PS no-service restart, power save,
MTU/MSS, thermal control, UPnP, network select and the NR band-lock list - read after every boot in the antenna apply's web
session, and on demand with GET /zte/settings. A change is logged; the audit flags the settings that hurt stability/latency
(PS no-service restart, UPnP, power save, roaming, a wrong MTU/MSS, an NR band list without n78). 2026-11-21: the discovery.

The watchdog (`net_link_detect_*`) stays in `watchdog.py`; `read()` refreshes it in the same login.
"""
from keeper.common import log, stamp, state
from keeper.devices.zte import watchdog
from keeper.devices.zte.web import Web, web_lock

CMDS = ("connectionMode,ConnectionMode,autoConnectWhenRoaming,roam_setting_option,ps_no_service_restart_flag,auto_power_save,"
        "mtu,tcp_mss,thermal_control_enable,thermal_led_enable,upnpEnabled,net_select_mode,net_select,nr5g_band_lock,"
        "ppp_status,modem_main_state")


def _flag(v):
    v = str(v or "").strip().lower()
    if v in ("1", "on", "true", "enable", "enabled"): return True
    if v in ("0", "off", "false", "disable", "disabled"): return False
    return None


def _int(v):
    try: return int(str(v).strip())
    except (TypeError, ValueError): return None


def _parse(v):
    """The web values -> the setting the keeper stores."""
    return {"connection_mode": v.get("connectionMode") or v.get("ConnectionMode") or "",
            "roaming": _flag(v.get("roam_setting_option")) if v.get("roam_setting_option") else _flag(v.get("autoConnectWhenRoaming")),
            "ps_restart": _flag(v.get("ps_no_service_restart_flag")), "power_save": _flag(v.get("auto_power_save")),
            "mtu": _int(v.get("mtu")), "mss": _int(v.get("tcp_mss")),
            "thermal": _flag(v.get("thermal_control_enable")), "thermal_led": _flag(v.get("thermal_led_enable")),
            "upnp": _flag(v.get("upnpEnabled")), "net_select_mode": v.get("net_select_mode") or "",
            "net_select": v.get("net_select") or "", "nr_band_lock": v.get("nr5g_band_lock") or "",
            "ppp_status": v.get("ppp_status") or "", "modem_main_state": v.get("modem_main_state") or ""}


def warnings(s, wd=None):
    """Settings that hurt stability / latency (or close a LAN door): the audit's list."""
    w = []
    if s.get("ps_restart"):
        w.append("PS no-service restart is ON (ps_no_service_restart_flag=1): the modem restarts the data session (and may reboot) "
                 "when service is lost - a candidate for the 'endless reboots'; test it off")
    if s.get("upnp"):
        w.append("UPnP is ON (upnpEnabled=1): no SSDP answer was seen, but the LAN exposure is on; keep it off")
    if s.get("power_save"):
        w.append("auto power save is ON: it can add latency; keep it off")
    if s.get("roaming"):
        w.append("roaming is ON: the modem may attach to another operator; keep it off")
    if s.get("mtu") is not None and s.get("mtu") != 1500: w.append("MTU is %s (expected 1500)" % s["mtu"])
    if s.get("mss") is not None and s.get("mss") != 1460: w.append("TCP MSS is %s (expected 1460)" % s["mss"])
    bl = s.get("nr_band_lock") or ""
    if bl and "78" not in [x.strip() for x in bl.split(",")]:
        w.append("n78 is not in the NR band lock (%s) - the 5G leg may fall back to a slower band" % bl)
    if s.get("net_select_mode") and s.get("net_select_mode") != "auto_select":
        w.append("network select is %s (expected auto_select)" % s["net_select_mode"])
    if wd and wd.get("enabled"):
        w.append("the ZTE watchdog is ENABLED (%s): a failed ping can reboot the modem into a loop; keep it off" %
                 (wd.get("host") or "?"))
    return w


def note(s):
    """Store the setting; log the changed keys (not every read)."""
    old = (state().get("web_settings") or {}).get("values")
    state({"web_settings": {"values": s, "time": stamp()}})
    if old is not None and old != s:
        keys = [k for k in s if s.get(k) != old.get(k)]
        log("ZTE web settings changed: %s" % ", ".join("%s=%s" % (k, s.get(k)) for k in keys))


def read_with(w):
    """Read on an already logged-in web session -> the stored setting; {'error': ...} on failure."""
    try: v = w.get(CMDS)
    except Exception as e: return {"error": "web settings read failed: %r" % e}
    if not v or not (v.get("mtu") or v.get("ps_no_service_restart_flag")):      # empty = not logged in (the page took the session)
        return {"error": "web settings not readable (the web session was not logged in)"}
    s = _parse(v); note(s); return s


def read():
    """Log in, refresh the watchdog + settings, log out (the browser's ZTE session ends) -> values/warnings or {'error': ...}."""
    with web_lock:
        w = Web()
        try: err = w.login()
        except Exception as e: return {"error": "web login failed: %r" % e}
        if err: return {"error": err}
        try:
            wd = watchdog.read_with(w)
            s = read_with(w)
            if "error" in s: return s
            return {"values": s, "watchdog": None if "error" in wd else wd, "warnings": warnings(s, wd), "time": stamp()}
        finally:
            try: w.logout()
            except Exception: pass


def audit():
    """The stored setting's audit (no web login): warnings for /status and GET /zte/settings."""
    s = (state().get("web_settings") or {}).get("values") or {}
    return warnings(s, (state().get("watchdog") or {}).get("values")) if s else []
