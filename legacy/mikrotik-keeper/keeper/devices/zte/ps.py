"""Fast PS data-session recovery over the ZTE web page: DISCONNECT_NETWORK -> wait for ppp_disconnected -> CONNECT_NETWORK -> wait
for ppp_connected. No CPE reboot (~2 min); the radio stays attached, so this only helps a stuck PS/data session, not a lock to a
cell the modem cannot attach to (a lock is applied at boot, a reboot is needed to change it). 2026-11-21: from the web goform
discovery; `brain.offline_decide` tries it before an outage reboot.
"""
import time

from keeper.devices.zte.web import Web, web_lock


def _status(w):
    try: return (w.get("ppp_status") or {}).get("ppp_status", "")
    except Exception: return ""


def _wait(w, want, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if _status(w) == want: return True
        time.sleep(1)
    return False


def reconnect(timeout=45):
    """Disconnect + connect on one web session -> {'ok', 'down', 'up', 'seconds'} or {'ok': False, 'error': ...}."""
    with web_lock:
        w = Web()
        try: err = w.login()
        except Exception as e: return {"ok": False, "error": "web login failed: %r" % e}
        if err: return {"ok": False, "error": err}
        try:
            t0 = time.monotonic()
            r = w.post({"goformId": "DISCONNECT_NETWORK"}).get("result")
            if r != "success": return {"ok": False, "error": "DISCONNECT_NETWORK -> %s" % r}
            down = _wait(w, "ppp_disconnected", 20)
            r = w.post({"goformId": "CONNECT_NETWORK"}).get("result")
            if r != "success": return {"ok": False, "down": down, "error": "CONNECT_NETWORK -> %s" % r}
            up = _wait(w, "ppp_connected", timeout)
            return {"ok": up, "down": down, "up": up, "seconds": round(time.monotonic() - t0, 1)}
        except Exception as e:
            return {"ok": False, "error": "PS reconnect failed: %r" % e}
        finally:
            try: w.logout()
            except Exception: pass
