"""The ZTE MC889 web page API (goform): login with the password hash, read values, post settings.

Only for what TR-069 can't do: antenna modes, and lock fallbacks. The web page has ONE session - a keeper login logs the
browser out, so every use logs out again right after. Env: ZTE_HOST (192.168.6.3), ZTE_PASS_HASH (or ZTE_PASS) - never logged.
`ready()` waits for a rebooted CPE's web to answer (it needs minutes) before the keeper logs in or a lock-test step goes on.
"""
import hashlib, http.cookiejar, json, threading, time, urllib.parse, urllib.request

from keeper.common import E

ZTE = E("ZTE_HOST", "192.168.6.3")
web_lock = threading.Lock()          # one web session at a time (boot apply / sweep / lock fallback)


def sha(s):
    """SHA-256 hex, upper case (the ZTE's login / AD hashing)."""
    return hashlib.sha256(s.encode()).hexdigest().upper()


class Web:
    """One web session (cookie jar). Use under `web_lock`, always `logout()` when done."""

    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _req(self, path, data=None):
        r = urllib.request.Request("http://%s%s" % (ZTE, path), data=data, headers={"Referer": "http://%s/index.html" % ZTE})
        return json.loads(self.op.open(r, timeout=8).read().decode() or "{}")

    def get(self, cmd):
        """Read values: `cmd` = comma separated names (`nr5g_pci,Z5g_SINR`)."""
        return self._req("/goform/goform_get_cmd_process?isTest=false&multi_data=1&cmd=%s&_=%d" % (cmd, time.time() * 1000))

    def post(self, form):
        """One goform set (adds the AD token the firmware wants). Returns the JSON answer."""
        v = self.get("wa_inner_version,cr_version,RD")
        form = dict(form, isTest="false", AD=sha(sha(v.get("wa_inner_version", "") + v.get("cr_version", "")) + v.get("RD", "")))
        return self._req("/goform/goform_set_cmd_process", urllib.parse.urlencode(form).encode())

    def login(self):
        """Log in with ZTE_PASS_HASH; None on success, else a short reason."""
        h = E("ZTE_PASS_HASH") or (sha(E("ZTE_PASS")) if E("ZTE_PASS") else "")
        if not h: return "no ZTE_PASS_HASH in the container env"
        r = self.post({"goformId": "LOGIN", "password": sha(h.upper() + self.get("LD").get("LD", ""))}).get("result")
        if r != "0": return "login refused (wrong hash)" if r == "3" else "login failed (%s)" % r
        return None

    def set_ant(self, auto, a5, a6):
        """Antenna auto-select (`'1'`/`'0'`) and, when off, antennas 5 / 6 (`'1'` omni, `'4'` directional). Returns the goforms that failed."""
        sets = [{"goformId": "WAN_ANT_SWITCH_SET", "ant_switch_enable": auto}]
        if auto == "0":
            sets += [{"goformId": "BSP_ANTENNA_STATE_SET", "antenna_name": "5", "state": a5},
                     {"goformId": "BSP_ANTENNA_STATE_SET", "antenna_name": "6", "state": a6}]
        return [s["goformId"] for s in sets if self.post(s).get("result") != "success"]

    def logout(self):
        """Free the single web session for the browser (errors ignored)."""
        try: self.post({"goformId": "LOGOUT"})
        except Exception: pass


READY_TRIES, READY_GAP = int(E("CPE_READY_TRIES", "3")), float(E("CPE_READY_GAP_S", "5"))


def ready(timeout=120, tries=None, gap=None):
    """Wait until the CPE web answers `tries` times in a row (a rebooted ZTE needs minutes; CPE_READY_TRIES 3, CPE_READY_GAP_S 5):
    True when it is ready. Reads one public goform value - no login, so the browser's session is not taken - up to `timeout` s."""
    tries = READY_TRIES if tries is None else tries; gap = READY_GAP if gap is None else gap
    t0 = time.monotonic(); ok = 0
    while time.monotonic() - t0 < timeout:
        try:
            Web().get("wa_inner_version"); ok += 1
            if ok >= tries: return True
        except Exception:
            ok = 0
        time.sleep(gap)
    return False
