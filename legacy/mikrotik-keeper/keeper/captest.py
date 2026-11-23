"""Capacity test: the modem itself measures the link (TR-143 Download / UploadDiagnostics) 4x a day against the operator's own
Ookla server, and the SQM ceilings follow what the cell delivers in each period of the day.

Why the modem: the keeper runs on the RB450 (Cortex-A7, no AES instructions) and can't pull 200 Mbit/s itself; the modem's test
traffic starts and ends at the modem, so the RB450, its CPU and the shaper are out of the measurement. Probed 2026-11-13: the MC889
has full TR-143 (NumberOfConnections, PeriodOfFullLoading, *UnderFullLoading) + a ZTE Iperf3 object.
Server: Ookla's list (https, small, once a day) -> CAPTEST_SERVER_ID (Yettel Sofia 69006) if listed, else the nearest server of
CAPTEST_SPONSOR in CAPTEST_CC (Yettel Plovdiv 67058), else CAPTEST_HOST. Checked 2026-11-13 from the LAN: plain-HTTP
/speedtest/random*.jpg answer 200, /download?size= answers 503 - so the download is random4000x4000.jpg (~31 MB) per connection.
Run: one slot (CAPTEST_SLOTS) at a time, within 90 min after it, after CAPTEST_IDLE_MIN idle minutes, modem up >= 15 min, not during
a lock test / sweep / quality A/B; download then upload, results read from the modem's next informs (fast mode while busy).
Result: Mbit/s = bytes under full loading * 8 / PeriodOfFullLoading (us) - only the time all connections ran, no TCP warm-up;
older firmware: TestBytes / (EOMTime - BOMTime). Ceiling per slot = CAPTEST_SHARE x median of its last CAPTEST_KEEP results,
capped at the plan (SQM_PLAN_DOWN / SQM_PLAN_UP) - the shaper must sit just below the real bottleneck to own the queue.
Night slots (01:30, 04:30) learn their own ceilings (night cells / LOCK_PAUSE anchor) - they never touch the daytime ones.
Env: CAPTEST (1), CAPTEST_SLOTS (01:30,04:30,07:30,12:30,18:30,21:30), CAPTEST_CONN (4), CAPTEST_UP_BYTES (25000000), CAPTEST_SERVER_ID (69006),
CAPTEST_SPONSOR (Yettel), CAPTEST_CC (BG), CAPTEST_HOST (speedtest1.yettel.bg:8080), CAPTEST_IDLE_MIN (10), CAPTEST_SHARE (0.92; per direction CAPTEST_SHARE_DOWN / _UP),
CAPTEST_KEEP (5), CAPTEST_FRESH_H (3: a fresher lower test wins), CAPTEST_WEEKLY (Mon 09:05: weekly summary to Telegram, empty = off), SQM_PLAN_DOWN (200), SQM_PLAN_UP (30), CAPTEST_UP (0: no upload test, the upload ceiling is SQM_MAX_UP).
Upload: TR-143 specifies HTTP PUT; tools/tr143_sim.py showed 2026-11-13 that Ookla drops PUT (POST works). The modem's upload is
tried; an Error_* turns it off for good (state captest_up, one message) - the upload ceiling then stays SQM_MAX_UP.
"""
import datetime, json, re, statistics, threading, time, urllib.parse, urllib.request

from keeper import acs, alert, brain, common, guard, sqm, store
from keeper.common import E, event, log, state

DL, UL = "Device.IP.Diagnostics.DownloadDiagnostics.", "Device.IP.Diagnostics.UploadDiagnostics."
ON = E("CAPTEST", "1") == "1"   # 4 slots a day; the method: CAPTEST_METHOD (iperf3 - TR-143 can't measure on the MC889 B07)
SLOTS = sorted(s.strip() for s in E("CAPTEST_SLOTS", "01:30,04:30,07:30,12:30,18:30,21:30").split(",") if s.strip())   # night: 01:30, 04:30
MIN_UPTIME_S = 900                 # the modem up >= 15 min: not right after the LOCK_PAUSE / night-screen reboots
CONN, UP_BYTES = int(E("CAPTEST_CONN", "4")), int(E("CAPTEST_UP_BYTES", "25000000"))
UP_MEASURE = E("CAPTEST_UP_MEASURE", "1") == "1"   # iperf3 upload after each download: measured and shown (~35 MB), ceiling only if CAPTEST_UP=1
UP_CONN = int(E("CAPTEST_UP_CONN", "1"))   # upload streams: the MC889 gives no UplinkSpeed with 4 (2026-11-13), 27.8 Mbit/s with 1 (11-14)
UP = E("CAPTEST_UP", "0") == "1"     # upload ceiling learning: off - in NSA the upload rides the LTE anchor and sits at the plan cap (~28 Mbit/s,
                                     # 10-31 and 11-13): the upload ceiling stays SQM_MAX_UP, the live controller cuts below it
SERVER_ID, SPONSOR, CC = E("CAPTEST_SERVER_ID", "69006"), E("CAPTEST_SPONSOR", "Yettel"), E("CAPTEST_CC", "BG")
HOST = E("CAPTEST_HOST", "speedtest1.yettel.bg:8080")
IP3 = "Device.IP.Diagnostics.Iperf3."               # ZTE vendor object (probed 2026-11-13): the modem runs iperf3 itself
IPERFS = [x.strip() for x in E("CAPTEST_IPERF", "37.19.203.1:5201,169.150.252.2:5201").split(",") if x.strip()]   # Sofia, Athens (public)
METHOD = E("CAPTEST_METHOD", "iperf3")              # iperf3 (works) | tr143 (the MC889 B07 download stops after < 1 kB)
ISECS = int(E("CAPTEST_SECS", "10"))
IPERF_DONE = ("complete", "completed", "finish", "finished", "done", "success", "succeeded", "fail", "failed", "error", "stop", "stopped")
IDLE_MIN, SHARE, KEEP = int(E("CAPTEST_IDLE_MIN", "10")), float(E("CAPTEST_SHARE", "0.92")), int(E("CAPTEST_KEEP", "5"))
# per direction (2026-11-20): the modem's iperf3 (4 streams down, 1 up) read ~76 % of the line down (156 vs 205 unshaped), ~95 % up
SHARE_D = {d: float(E("CAPTEST_SHARE_" + d.upper(), str(SHARE))) for d in ("down", "up")}
FRESH_H = float(E("CAPTEST_FRESH_H", "3"))
CONFIRM_S = float(E("CAPTEST_CONFIRM_S", "600"))   # a single low result asks for a second test this much later (not the 1-h gap)   # a test this fresh that is LOWER than the period's ceiling wins (rain, a busy cell)
PLAN = sqm.PLAN                    # SQM_PLAN_DOWN / SQM_PLAN_UP (200 / 30)
TRIG_MAX, TRIG_GAP_S = int(E("CAPTEST_TRIGGER_MAX", "6")), 3600       # extra tests a day when the line changed; >= 1 h after the last test
SETUP_STABLE_S = 900                                                   # a new anchor / 5G cell must hold 15 min before it is worth a test
WINDOW_MIN, RESULT_WAIT_S = 90, 240
MIN_BYTES = 1_000_000              # a result under 1 MB / 1 s is not a speed (2026-11-13: 2.8 kB in 4 ms read as 5.9 Mbit/s -> ceiling 20)
WEEKLY = E("CAPTEST_WEEKLY", "Mon 09:05")    # the week's capacity summary to Telegram: "<Mon..Sun> HH:MM" (local), empty = off
MIN_TESTS = 2                      # valid results a period needs before its learned ceiling replaces SQM_MAX_*
LIST_URL = "https://www.speedtest.net/api/js/servers?engine=js&search=%s"
FINAL = ("Completed",)                                    # anything else that is not Requested / None is an Error_* (TR-143)
st = {"running": False, "base": None, "armed": 0.0, "seen_requested": False, "vals": None, "ev": threading.Event(),
      "servers": None, "servers_day": None}
common.register_busy("capacity test", lambda: st["running"])


# ---------- pure parts ----------
def pick(servers, want_id=SERVER_ID, sponsor=SPONSOR, cc=CC):
    """The operator's own Ookla server: `want_id` if listed, else the nearest one of `sponsor` in country `cc`; None if none."""
    ok = [s for s in servers or [] if sponsor.lower() in str(s.get("sponsor", "")).lower() and s.get("cc") == cc and s.get("host")]
    return next((s for s in ok if str(s.get("id")) == str(want_id)), None) or min(ok, key=lambda s: float(s.get("distance") or 9e9), default=None)


def down_params(host, url=None, conn=None, secs=None):
    """TR-143 download: CONN parallel GETs of the classic Ookla file (~31 MB each), file based (no time limit). Diagnostics (POST
    /captest): `url` another file, `conn` connections, `secs` > 0 = time based (TimeBasedTestDuration)."""
    p = [(DL + "DownloadURL", "xsd:string", url or "http://%s/speedtest/random4000x4000.jpg" % host),
         (DL + "NumberOfConnections", "xsd:unsignedInt", str(conn or CONN))]
    p.append((DL + "TimeBasedTestDuration", "xsd:unsignedInt", str(secs or 0)))   # always written: a diagnostic's value stays set on the modem
    return p + [(DL + "DiagnosticsState", "xsd:string", "Requested")]


def up_params(server):
    """TR-143 upload to the server's own upload URL (Ookla lists it), CONN connections, UP_BYTES in total."""
    url = server.get("url") or "http://%s/speedtest/upload.php" % server["host"]
    return [(UL + "UploadURL", "xsd:string", url), (UL + "TestFileLength", "xsd:unsignedInt", str(UP_BYTES)),
            (UL + "NumberOfConnections", "xsd:unsignedInt", str(CONN)), (UL + "DiagnosticsState", "xsd:string", "Requested")]


def iso(s):
    """TR-069 dateTime ('2026-11-13T07:30:01.123456Z', any fraction length) -> epoch, None if unset / unreadable."""
    m = re.match(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.(\d+))?\s*(Z|[+-]\d\d:?\d\d)?$", (s or "").strip())
    if not m or s.startswith("0001"): return None
    try: t = datetime.datetime.fromisoformat("%sT%s%s" % (m.group(1), m.group(2), "+00:00" if m.group(4) in (None, "Z") else m.group(4))).timestamp()
    except ValueError: return None
    return t + (float("0." + m.group(3)) if m.group(3) else 0.0)


def result(vals, base):
    """(Mbit/s or None, how / why) from a read of `base` (DL / UL). Full-loading counters first (all connections running, no
    warm-up), else bytes over BOM..EOM."""
    g = lambda k: vals.get(base + k, "")
    s = g("DiagnosticsState")
    if s not in FINAL: return None, s or "no result"
    b = brain.f(g("TestBytesReceivedUnderFullLoading") or g("TestBytesSentUnderFullLoading")); p = brain.f(g("PeriodOfFullLoading"))
    if b and p and b >= MIN_BYTES and p >= 1e6: return round(b * 8 / p, 1), "full load %.1f s" % (p / 1e6)
    t1 = iso(g("EOMTime"))
    for bk in ("TestBytesReceived", "TestBytesSent", "TotalBytesReceived", "TotalBytesSent"):
        b = brain.f(g(bk))
        for tk in ("BOMTime", "ROMTime"):
            t0 = iso(g(tk))
            if b and b >= MIN_BYTES and t0 and t1 and t1 - t0 >= 1: return round(b * 8 / (t1 - t0) / 1e6, 1), "%s %s-EOM %.1f s" % (bk, tk[:3], t1 - t0)
    seen = ", ".join("%s=%s" % (k, g(k)) for k in ("TestBytesReceived", "TotalBytesReceived", "TestBytesSent", "PeriodOfFullLoading",
                                                   "ROMTime", "BOMTime", "EOMTime") if g(k) != "")
    return None, "no valid transfer (>= 1 MB in >= 1 s) in the result (%s)" % (seen or "no counters read")


def slot_of(now):
    """The slot whose period `now` is in: the latest slot already started today (before the first one: yesterday's last)."""
    hm = time.strftime("%H:%M", time.localtime(now))
    return ([s for s in SLOTS if s <= hm] or SLOTS)[-1]


def due(now, done):
    """The slot to test now, or None: started <= WINDOW_MIN ago and not done today (`done`: {"YYYY-MM-DD slot": True})."""
    lt = time.localtime(now); day = time.strftime("%Y-%m-%d", lt); hm = time.strftime("%H:%M", lt)
    for s in SLOTS:
        h, m = map(int, s.split(":")); start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
        if start <= now < start + WINDOW_MIN * 60 and not done.get("%s %s" % (day, s)): return s
    return None


def slot_soon(now, within=TRIG_GAP_S):
    """A scheduled slot starts within `within` s: a triggered test waits for it (it measures the same thing)."""
    lt = time.localtime(now)
    for s in SLOTS:
        h, m = map(int, s.split(":"))
        for dd in (0, 1):
            start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + dd, h, m, 0, 0, 0, -1))
            if 0 <= start - now < within: return True
    return False


def setup_key(row):
    """'LTE anchor|5G cell' of a sample - what a capacity depends on besides the time of day. NSA drops the 5G leg when idle
    ('no5G'): the last 5G cell seen is kept. None without an anchor."""
    nr = row.get("nr_cell")
    if nr and nr not in ("no5G", "/"): st["nr_last"] = nr
    lte = row.get("lte_cell")
    return "%s|%s" % (lte, st.get("nr_last") or "-") if lte and lte != "/" else None


def trigger(row, now):
    """Collect the reasons for an extra test: the setup changed since the last counted test (stable SETUP_STABLE_S), or the
    shaper asked (sqm.want_test: climbed > +10 %, deep cuts). -> the pending reason or None."""
    key = setup_key(row)
    if key and key != st.get("seen_key"): st.update(seen_key=key, seen_since=now)
    tested = state().get("captest_setup")
    if key and not tested: state({"captest_setup": key})                    # first sample after the update: adopt, no test
    elif key and key != tested and now - st["seen_since"] >= SETUP_STABLE_S and not st.get("want"):
        st["want"] = {"why": "setup %s -> %s" % (tested, key), "at": now}
    w = sqm.st.pop("want_test", None)
    if w: st["want"] = w
    return (st.get("want") or {}).get("why")


def trig_today(now):
    """Triggered tests started on `now`'s calendar day (local time): the TRIG_MAX budget and the panel's 'today' count."""
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    return len([t for t in state().get("captest_trig") or [] if time.strftime("%Y-%m-%d", time.localtime(t)) == day])


def trig_ok(now):
    """Rate limit of the triggered tests: TRIG_MAX a day, >= TRIG_GAP_S after the last counted test, none just before a slot."""
    return trig_today(now) < TRIG_MAX and now - (state().get("captest_last_t") or 0) >= TRIG_GAP_S and not slot_soon(now)


def counted(now, trig=None):
    """A test that counts for a ceiling starts: it answers any pending reason, and the setup it measures becomes the tested one."""
    st.update(want=None, trigger=trig, setup_now=st.get("seen_key")); st["confirm"] = None      # any counted test is the second result
    state(dict({"captest_last_t": now}, **({"captest_setup": st["seen_key"]} if st.get("seen_key") else {})))


def valid(r, d):
    """A stored result is a speed: its 'how' says >= 1 s (records from before the 1 MB / 1 s check included)."""
    m = re.search(r"([\d.]+) s$", str(r.get(d + "_how", "")))
    return bool(m) and float(m.group(1)) >= 1


def counted_results(hist, slot, d):
    """The last KEEP results of `slot` that count for a ceiling (no diagnostic, no TR-143 file test, no pre-v2 iperf3 record)."""
    return [r[d] for r in hist if r.get("slot") == slot and r.get(d) and valid(r, d) and not r.get("url") and not r.get("diag")
            and not (str(r.get("server", "")).startswith("iperf3") and not r.get("v"))][-KEEP:]


def fresh(hist, d, now, n=2):
    """The newest `n` counted results of any slot within FRESH_H of `now` (Mbit/s), newest first."""
    out = []
    for r in reversed(hist):
        try: t = time.mktime(time.strptime(r["time"][:19], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, TypeError, ValueError): continue
        if now - t > FRESH_H * 3600: break
        if counted_results([r], r.get("slot"), d): out.append(r[d])
        if len(out) == n: break
    return out


def ceilings(hist, slot, now=None):
    """{"down": Mbit/s, "up": Mbit/s} for `slot` from its last KEEP results: SHARE_D[d] x median, capped at the plan; {} without data.
    With `now`: when the 2 newest results of the last FRESH_H hours (any slot) are BOTH lower, the higher of the two wins - one
    rainy-day test is 1 of 5 in the median (2026-11-18), but ONE peak-hour test swings +-25 % (2026-11-20: Sofia 110 -> 178 in
    10 min), so a single low one only asks for a confirmation test (CONFIRM_S later). A higher one only joins the median."""
    out = {}
    for d in ("down", "up"):
        xs = counted_results(hist, slot, d)
        if len(xs) >= MIN_TESTS: out[d] = round(min(PLAN[d], SHARE_D[d] * statistics.median(xs)), 1)
        fs = fresh(hist, d, now) if now and d in out else []
        if len(fs) == 2 and all(SHARE_D[d] * f < out[d] for f in fs): out[d] = round(min(PLAN[d], SHARE_D[d] * max(fs)), 1)
    return out


def explain(now=None):
    """Per direction, in words: the limit now and why, the ceiling and where it comes from (tests / env / climb), what is next."""
    now = now or time.time(); hist = state().get("captest_hist") or []; slot = slot_of(now); last = sqm.st.get("last") or {}
    out = {}
    for d in ("down", "up"):
        xs = counted_results(hist, slot, d); learned = ceilings(hist, slot).get(d); low = ceilings(hist, slot, now).get(d)
        if d == "up" and not UP: src = "upload learning off (CAPTEST_UP=0): SQM_MAX_UP %g" % sqm.MAX["up"]
        elif learned and low is not None and low < learned:
            src = "two fresh tests (< %g h) measured less %s: %g x %g = %g, below the %s period's %g" % (FRESH_H, fresh(hist, d, now), SHARE_D[d], max(fresh(hist, d, now)), low, slot, learned)
        elif learned: src = "learned for the %s period: %g x the median %g of %d tests %s" % (slot, SHARE_D[d], statistics.median(xs), len(xs), xs)
        else: src = "not learned yet for the %s period (%d of %d tests): SQM_MAX_%s %g" % (slot, len(xs), MIN_TESTS, d.upper(), sqm.MAX[d])
        c = (sqm.st.get("climb") or {}).get(d) or {}
        climb = ("climbed x%g (+%d %%) above it - busy at the ceiling with clean latency" % (c["x"], round((c["x"] - 1) * 100)) if c.get("x", 1) > 1
                 else "no climb") + "; next +5 %% after %d more clean busy min at the ceiling (top: plan %g)" % (
                     max(0, round((sqm.CLIMB_S - c.get("clean", 0)) / 60)), PLAN[d])
        out[d] = {"limit": (last.get("limit") or {}).get(d), "rate": (last.get("rate") or {}).get(d), "ceiling": (last.get("ceiling") or {}).get(d),
                  "why": (last.get("why") or {}).get(d), "ceiling_from": src, "climb": climb}
    out["delay_ms"], out["delay_limit_ms"], out["time"] = last.get("delay_ms"), sqm.DELAY, last.get("time")
    return out


def ceiling(d, now):
    """The SQM ceiling for direction `d` now: learned for this period of the day, else the env SQM_MAX_* (sqm.MAX)."""
    if d == "up" and not UP: return sqm.MAX["up"]                                    # upload: your SQM_MAX_UP
    c = ceilings(state().get("captest_hist") or [], slot_of(now), now).get(d)
    return max(sqm.MIN[d], c) if c else sqm.MAX[d]


# ---------- the run ----------
def servers(now):
    """Ookla's list, fetched once a day (HTTPS, a few kB); the last good list if the fetch fails."""
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if st["servers_day"] != day:
        try:
            req = urllib.request.Request(LIST_URL % urllib.parse.quote(SPONSOR), headers={"User-Agent": "Mozilla/5.0 (X11; Linux) zte-keeper",
                                                                                     "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r: st["servers"] = json.loads(r.read())
            st["servers_day"] = day
        except Exception as e:
            log("capacity test: Ookla server list failed: %r - using %s" % (e, HOST))
    return st["servers"] or []


CPU_PARAM = "Device.DeviceInfo.ProcessStatus.CPUUsage"   # the modem's CPU % while its iperf3 runs (2026-11-20: is it the limit?)


def extra_read():
    """Names the ACS adds to its GetParameterValues while a test runs (the result arrives with the modem's next informs)."""
    return [st["base"], CPU_PARAM] if st["running"] and st["base"] else []


def cpu_note():
    """'; modem CPU max 97 % (2 reads)' from the reads during the test, '' without any."""
    xs = st.get("cpu") or []
    return "; modem CPU max %d %% (%d reads)" % (max(xs), len(xs)) if xs else ""


def on_values(vals):
    """ACS hook (every read): the running test's state; a final one (Completed / Error_*) after it was armed ends the wait."""
    if st["running"] and CPU_PARAM in vals:
        try: st.setdefault("cpu", []).append(int(float(vals[CPU_PARAM])))
        except (TypeError, ValueError): pass
    if st["running"] and st["base"] == IP3: return _ip3_values(vals)
    if not (st["running"] and st["base"]) or (st["base"] + "DiagnosticsState") not in vals: return
    s = vals[st["base"] + "DiagnosticsState"]
    rom = vals.get(st["base"] + "ROMTime", "")
    if st.get("first"):                                         # the first read after arming is in the session that carries our
        st.update(first=False, stale_rom=rom); return           # write (it goes out after the read): the OLD result, by construction
    if s == "Requested": st["seen_requested"] = True; return
    # a new result: we saw it running, or its ROMTime differs from the old one (no clock compare - the MC889 writes local time with
    # a 'Z', 2026-11-13: an 18:31 local result read as 20:31 UTC passed the old clock check and was taken for a 19:47 test)
    if s and s != "None" and (st["seen_requested"] or rom != st.get("stale_rom")):
        st["vals"] = vals; st["ev"].set()


class LocalTraffic(RuntimeError):
    """The router detected competing traffic or could not verify the test was quiet."""


def _traffic_reason(rate):
    if not rate:
        return ""
    down, up = rate["down"], rate["up"]
    if down * 125000 > brain.IDLE_DOWN or up * 125000 > brain.IDLE_UP:
        return "local traffic %.2f down / %.2f up Mbit/s" % (down, up)
    return ""


def _require_quiet():
    if not sqm.URL:
        return
    try:
        reason = _traffic_reason(sqm.wan_rate())
    except (OSError, RuntimeError, ValueError, IndexError, KeyError, TypeError):
        raise LocalTraffic("router WAN traffic unavailable") from None
    if reason:
        raise LocalTraffic(reason)


def wait_lan_quiet(timeout=90):
    """Wait for three quiet WAN readings before an observation retries a discarded transfer."""
    deadline = time.monotonic() + timeout
    good = 0
    while time.monotonic() < deadline:
        try:
            _require_quiet()
            good += 1
            if good == 3:
                return True
        except LocalTraffic:
            good = 0
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(5, remaining))
    return False


def _await_result(timeout, protect=False):
    if not protect or not sqm.URL:
        return st["ev"].wait(timeout)
    deadline = time.monotonic() + timeout
    try:
        while True:
            _require_quiet()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return st["ev"].is_set()
            if st["ev"].wait(min(2, remaining)):
                _require_quiet()
                return True
    except LocalTraffic:
        # Keep the device reserved until this transfer has finished. The caller discards its result.
        st["ev"].wait(max(0, deadline - time.monotonic()))
        raise


def _traffic_skip(now, slot, error):
    skip = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "slot": slot, "reason": str(error)}
    state({"captest_traffic_skip": skip})
    log("capacity test %s skipped: %s" % (slot, error))
    event({"event": "capacity test skipped", "to": slot, "why": str(error)})


def _one(base, params, protect=False):
    """Arm one TR-143 test and wait for its result: (Mbit/s or None, why)."""
    if protect:
        _require_quiet()
    st.update(base=base, seen_requested=False, vals=None, armed=time.time(), first=True, stale_rom=None); st["ev"].clear()
    ok = acs.tr069_set(params)                                  # the session that carries it first reads the previous test's result
    if not ok and any("NumberOfConnections" in p[0] for p in params):                    # an older object: one connection
        st["first"] = True; ok = acs.tr069_set([p for p in params if "NumberOfConnections" not in p[0]])
    if not ok: return None, "the modem refused the test parameters"
    if not _await_result(RESULT_WAIT_S, protect): return None, "no result within %d s" % RESULT_WAIT_S
    st.setdefault("raw", {})[base] = {k[len(base):]: v for k, v in st["vals"].items() if k.startswith(base) and "." not in k[len(base):]}
    return result(st["vals"], base)


def run(slot, now=None, forced=False, url=None, conn=None, secs=None):
    """Thread: download then upload test in `slot`, record the result, move the ceilings."""
    now = now or time.time(); st["raw"] = {}
    try:
        opts = {"protect": True} if not forced and sqm.URL else {}
        srv = pick(servers(now)) or {"host": HOST, "id": "fallback", "name": HOST}
        down, dwhy = _one(DL, down_params(srv["host"], url, conn, secs), **opts)
        off = "CAPTEST_UP=0, the ceiling is SQM_MAX_UP" if not UP else (state().get("captest_up") or {}).get("off")
        if off: up, uwhy = None, "upload test off (%s)" % off
        else:
            up, uwhy = _one(UL, up_params(srv), **opts)
            if up is None and (uwhy.startswith("Error_") or "refused" in uwhy):   # 2026-11-13: Ookla drops HTTP PUT (TR-143's method)
                state({"captest_up": {"off": uwhy, "time": time.strftime("%Y-%m-%d %H:%M:%S")}})
                event({"event": "capacity ceiling", "to": "upload test off", "why": "the modem's TR-143 upload failed (%s) - Ookla's"
                       " upload.php takes POST, not PUT; the upload ceiling stays SQM_MAX_UP" % uwhy})
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "slot": slot, "down": down, "up": up,
               "server": "%s %s" % (srv.get("id"), srv.get("name", "")), "down_how": dwhy, "up_how": uwhy, "forced": forced, "url": url, "diag": {k: v for k, v in (("conn", conn), ("secs", secs)) if v is not None} or None}
        state({"captest_last": {"rec": rec, "raw": {"down": st["raw"].get(DL), "up": st["raw"].get(UL)}}})
        hist = (state().get("captest_hist") or []) + [rec]
        before = ceilings(state().get("captest_hist") or [], slot)
        state({"captest_hist": hist[-80:]}); after = ceilings(hist, slot)
        log("capacity test %s: down %s Mbit/s (%s), up %s Mbit/s (%s) - %s" % (slot, down, dwhy, up, uwhy, rec["server"]))
        event({"event": "capacity test", "to": "down %s / up %s Mbit/s" % (down, up), "why": "%s slot %s: %s; %s" % (rec["server"], slot, dwhy, uwhy)})
        moved = [d for d in after if not before.get(d) or abs(after[d] - before[d]) > 0.1 * before[d]]
        if moved:
            event(ceiling_event(slot, after, rec))
    except LocalTraffic as e:
        _traffic_skip(now, slot, e)
    except Exception as e:
        log("capacity test failed: %r" % e)
    finally:
        st.update(running=False, base=None)


# ---------- iperf3 (the ZTE's own client) ----------
IP3_OPTIONAL = ("Mode", "Omit", "OmitNum", "OutputReport", "LANTrafficBlock", "Parallel")   # dropped if the modem refuses them
                                                                                          # (Mode: refused 2026-11-13, already 'client')
DROP = set()            # optional names this modem refused: not written again until restart (one 9008 fault per start, not per test)


def ceiling_event(slot, after, rec):
    """The Telegram 'capacity ceiling' event: the new ceilings, and the upload's fixed cap with what was measured (CAPTEST_UP=0)."""
    up = "up %s Mbit/s" % after["up"] if "up" in after else "up stays %g Mbit/s%s" % (
        sqm.MAX["up"], " (measured %s)" % rec["up"] if rec.get("up") is not None else "")
    return {"event": "capacity ceiling", "to": "%s: down %s Mbit/s / %s" % (slot, after.get("down", "-"), up),
            "why": "this test: down %s / up %s Mbit/s; %g / %g x the median of the last %d tests in this period (plan %g / %g)"
                   % (rec.get("down"), rec.get("up"), SHARE_D["down"], SHARE_D["up"], KEEP, PLAN["down"], PLAN["up"])}


def iperf_params(server, secs=10, conn=4, reverse=True, plain=False):
    """Iperf3 client settings (no Enable - that starts it, written on its own). plain=True: every value as xsd:string."""
    host, _, port = server.partition(":")
    t = (lambda typ: "xsd:string") if plain else (lambda typ: typ)
    params = [(IP3 + "Mode", "xsd:string", "client"), (IP3 + "ServerAddress", "xsd:string", host),
            (IP3 + "ServerPort", t("xsd:unsignedInt"), port or "5201"), (IP3 + "DurationTestTime", t("xsd:unsignedInt"), str(secs)),
            (IP3 + "Reverse", t("xsd:boolean"), "1" if reverse else "0"), (IP3 + "Parallel", t("xsd:unsignedInt"), str(conn)),
            (IP3 + "Omit", t("xsd:boolean"), "1"), (IP3 + "OmitNum", t("xsd:unsignedInt"), "2"),
            (IP3 + "OutputReport", t("xsd:boolean"), "1"), (IP3 + "LANTrafficBlock", t("xsd:boolean"), "0")]
    skip = DROP | read_only()
    return [x for x in params if x[0][len(IP3):] not in skip]


def read_only():
    """Optional Iperf3 names the modem itself reports read-only (TR-143 probe, GetParameterNames Writable=0): never written."""
    w = (state().get("tr143") or {}).get("writable") or {}
    return {n[len(IP3):] for n, ok in w.items() if n.startswith(IP3) and not ok and n[len(IP3):] in IP3_OPTIONAL}


def write_settings(params, tries=6):
    """Write `params`, adapting to the modem's per-parameter refusals: an optional one is dropped, a required one is retried
    as a plain string. -> (ok, notes)."""
    params, notes = list(params), []
    for _ in range(tries):
        if acs.tr069_set(params): return True, notes
        bad = [n for n, code, why in acs.tr.get("refused") or []]
        if not bad:                                                    # no detail: everything as a string, once
            if all(t == "xsd:string" for _, t, _ in params): return False, notes + ["refused without details"]
            params = [(n, "xsd:string", v) for n, t, v in params]; notes.append("all as strings"); continue
        for n in bad:
            short = n[len(IP3):]
            if short in IP3_OPTIONAL: params = [p for p in params if p[0] != n]; notes.append("dropped " + short); DROP.add(short)
            elif any(p[0] == n and p[1] != "xsd:string" for p in params):
                params = [(p[0], "xsd:string", p[2]) if p[0] == n else p for p in params]; notes.append(short + " as string")
            else: return False, notes + ["refused %s" % short]
    return False, notes + ["gave up"]


def start_iperf():
    """Enable = 1 on its own (the start): boolean 1, string 1, then 'true'. -> (ok, how)."""
    for typ, val in (("xsd:boolean", "1"), ("xsd:string", "1"), ("xsd:string", "true")):
        if acs.tr069_set([(IP3 + "Enable", typ, val)]): return True, "Enable=%s (%s)" % (val, typ)
    return False, "Enable refused (%s)" % "; ".join("%s %s" % (c, w) for _, c, w in acs.tr.get("refused") or []) or "no detail"


def _ip3_values(vals):
    """Every read while iperf3 runs is kept (we learn the ZTE's formats from them); done when a LastTest / Status / speed field
    changed from the pre-start read and Status is not 'running'-like - or Enable went back to 0 after we saw it 1."""
    v = {k[len(IP3):]: x for k, x in vals.items() if k.startswith(IP3)}
    if not v: return
    st["ip3_snaps"].append(dict(v, _t=time.strftime("%H:%M:%S")))
    if st.get("first"): st.update(first=False, ip3_base=v); return             # the read in the session that carries our write
    if not st.get("ip3_started"): return              # before OUR Enable was accepted the modem shows the previous run's result
    b = st.get("ip3_base") or {}; s = v.get("Status", "").lower()
    if v.get("Enable") == "1" or s == "requested" or any(w in s for w in ("run", "progress")): st["ip3_seen_on"] = True
    new = any(v.get(k) and v.get(k) != b.get(k) for k in ("DownlinkLastTest", "UplinkLastTest"))   # the minute of the run (MC889)
    if (st.get("ip3_seen_on") or new) and (any(w == s for w in IPERF_DONE) or (new and v.get("Enable") != "1")):
        st["vals"] = v; st["ev"].set()                # 2026-11-13 22:40: the old 22:17 'Complete 144' was taken before the start


def mbit(v):
    """'176.3 Mbits/sec' / '176300000' (bit/s) / '176.3' (Mbit/s) -> Mbit/s; None if unreadable."""
    m = re.search(r"([\d.]+)\s*([kKmMgG]?)(bits?|bps|B)?", str(v or ""))
    if not m: return None
    x = float(m.group(1)); u = m.group(2).upper()
    if u: return round(x * {"K": 1e-3, "M": 1.0, "G": 1e3}[u], 1)
    return round(x / 1e6, 1) if x > 100000 else round(x, 1)         # a bare big number = bit/s, a small one = Mbit/s


def ip3_result(v, reverse=True):
    """(Mbit/s or None, how): iperf's own receiver summary in Report first, else Downlink/UplinkSpeed."""
    rep = str(v.get("Report") or "")
    sums = re.findall(r"\[SUM\].*?([\d.]+\s*[KMG]bits/sec).*?receiver", rep) or re.findall(r"([\d.]+\s*[KMG]bits/sec).*?receiver", rep)
    if sums: return mbit(sums[-1]), "iperf3 receiver (Report)"
    sp = mbit(v.get("DownlinkSpeed" if reverse else "UplinkSpeed"))
    if sp: return sp, "iperf3 %s=%s" % ("DownlinkSpeed" if reverse else "UplinkSpeed", v.get("DownlinkSpeed" if reverse else "UplinkSpeed"))
    f = "DownlinkSpeed" if reverse else "UplinkSpeed"
    return None, "iperf3: no speed (Status=%s, %s=%s, Report=%s)" % (v.get("Status"), f, v.get(f), rep[:120])


def _iperf_once(server, secs, conn, reverse, load_windows=None, protect=False):
    """One iperf3 run by the modem against `server`: (Mbit/s or None, why, the reads)."""
    if protect:
        _require_quiet()
    st.update(base=IP3, first=True, vals=None, ip3_snaps=[], ip3_base=None, ip3_seen_on=False, ip3_started=False, cpu=[]); st["ev"].clear()
    ok, notes = write_settings(iperf_params(server, secs, conn, reverse))
    if ok:
        st["ev"].clear(); ok, how = start_iperf(); notes.append(how)
        started = time.time()                         # CPE accepted Enable, the closest clock marker for transfer start
        st["ip3_started"] = ok
    if not ok: return None, "the modem refused the iperf3 settings: %s" % ", ".join(notes), st["ip3_snaps"]
    if not _await_result(secs + 150, protect):
        reads = st["ip3_snaps"]
        last = reads[-1] if reads else {}
        detail = "last Status=%s, Enable=%s" % (last.get("Status"), last.get("Enable")) if reads else "no modem reads"
        return None, "iperf3: no fresh result within %d s (%s, %s)" % (secs + 150, ", ".join(notes), detail), reads
    sp, why = ip3_result(st["vals"], reverse)
    if sp and load_windows is not None:
        load_windows.append((started, min(time.time(), started + secs)))
    return sp, "%s (%s)%s, %d streams %d s" % (why, ", ".join(notes), cpu_note(), conn, secs), st["ip3_snaps"]     # ends in 'N s': valid()


def measure(secs=None, load_windows=None, *, download_only=False, server=None):
    """A lock-test step's throughput, now and synchronous: the modem's iperf3 down (CONN streams) then up (UP_CONN), the first server
    that answers. -> (down, up, which servers). Optionally collect one estimated transfer interval per successful direction.
    Include the causes for directions that exhaust their servers. Specify a server to keep the same path across confirmed legs.
    With download_only, make one download attempt to warm up NSA.
    Never a ceiling, not in captest_hist (2026-11-18: the night cells' score needs it)."""
    if st["running"]: return None, None, "a capacity test is running"
    st["running"] = True; out, how = {}, []
    try:
        directions = (("down", True, CONN),)
        if not download_only:
            directions += (("up", False, UP_CONN),)
        servers = [server] if server else IPERFS[:1] if download_only else IPERFS
        for d, rev, conn in directions:
            failures = []
            for srv in servers:
                sp, why, _ = _iperf_once(srv, secs or ISECS, conn, rev, load_windows=load_windows, protect=load_windows is not None)
                if sp:
                    out[d] = sp
                    how.append("%s %s" % (d, srv))
                    break
                if download_only:
                    how.append(why)
                else:
                    failures.append("%s: %s" % (srv, why))
            else:
                if failures:
                    how.append("%s failed (%s)" % (d, "; ".join(failures)))
        return out.get("down"), out.get("up"), ", ".join(how) or "no iperf3 result"
    finally:
        st.update(running=False, base=None)


def run_iperf(now, servers, secs, conn, reverse, slot=None, diag=None, protect=False):
    """Thread: the modem's iperf3 against the first server that answers (Sofia, then Athens - public servers take one client at
    a time); the result counts for the period's ceiling unless `diag` (a custom diagnostic run). Measured 2026-11-13 22:15: 144
    Mbit/s download, Status Requested -> Complete, DownlinkSpeed '144Mbits/sec' (the MC889 fills no Report)."""
    try:
        slot = slot or slot_of(now); tried = []
        opts = {"protect": True} if protect and sqm.URL else {}
        for srv in servers:
            sp, why, reads = _iperf_once(srv, secs, conn, reverse, **opts); tried.append("%s: %s" % (srv, why))
            if sp: break
        d = "down" if reverse else "up"
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "slot": slot, "down": None, "up": None, d: sp,
               d + "_how": why if sp else "; ".join(tried), "server": "iperf3 " + srv, "forced": diag is not None or None, "diag": diag,
               "v": 2}                                       # v2: started-check (records without it may be the previous run's result)
        if not diag: rec.update(setup=st.get("setup_now"), trigger=st.get("trigger"))   # what was measured, and why now (None = the slot)
        if reverse and sp and UP_MEASURE and not diag:                   # the upload: same server first, then the others
            utried = []                                                  # 2026-11-13 23:17: Athens upload ended in 6 s, 'Mbits/sec' (no number)
            for u in [srv] + [x for x in servers if x != srv]:
                usp, uwhy, ureads = _iperf_once(u, secs, UP_CONN, False, **opts); reads = reads + ureads; utried.append("%s: %s" % (u, uwhy))
                if usp: break
            rec.update(up=usp, up_how=(uwhy if u == srv else utried[-1]) if usp else "; ".join(utried))
        applied = lambda c: {k: x for k, x in c.items() if k == "down" or UP}       # the upload ceiling only with CAPTEST_UP=1
        before = applied(ceilings(state().get("captest_hist") or [], slot))
        hist = ((state().get("captest_hist") or []) + [rec])[-80:]
        state({"captest_hist": hist, "captest_last": {"rec": rec, "raw": {"iperf3_reads": reads[-12:]}}})
        log("capacity test (iperf3 %s): %s" % (srv, "; ".join("%s %s Mbit/s - %s" % (d, rec[d], rec[d + "_how"]) for d in ("down", "up") if rec.get(d + "_how"))))
        event({"event": "capacity test", "to": "down %s%s Mbit/s (iperf3)" % (rec["down"], " / up %s" % rec["up"] if "up_how" in rec else ""),
               "why": "%s slot %s: %s%s" % (srv, slot, rec.get("down_how") or rec.get("up_how"), "; up: %s" % rec["up_how"] if rec.get("up_how") and rec.get("down_how") else "")})
        after = applied(ceilings(hist, slot))
        learned = ceilings(hist, slot).get("down")
        if not diag and rec.get("down") and learned and SHARE_D["down"] * rec["down"] < learned and not str(rec.get("trigger") or "").startswith("confirm"):
            st["confirm"] = {"at": time.time() + CONFIRM_S, "why": "confirm a low result: down %s Mbit/s (%g x = %g < the %s period's %g)"
                             % (rec["down"], SHARE_D["down"], round(SHARE_D["down"] * rec["down"], 1), slot, learned)}
        moved = [k for k in after if not before.get(k) or abs(after[k] - before[k]) > 0.1 * before[k]]
        if moved:
            event(ceiling_event(slot, after, rec))
    except LocalTraffic as e:
        _traffic_skip(now, slot, e)
    except Exception as e:
        log("iperf3 test failed: %r" % e)
    finally:
        st.update(running=False, base=None)


def force(now=None, url=None, conn=None, secs=None, mode=None, server=None, reverse=True):
    """API (POST /captest): a test now - no slot, no idle wait (your own traffic lowers the result). False while one runs or the
    keeper is busy (lock test, sweep, lock change). It counts for the current period's ceiling like a scheduled one."""
    now = now or time.time()
    with common.test_start_lock:
        if st["running"] or common.busy(): return False
        st["running"] = True
    if (mode or ("tr143" if url else METHOD)) == "iperf3":          # a url= is a TR-143 file
        custom = any(x is not None for x in (server, secs, conn)) or not reverse
        diag = {"mode": "iperf3", "server": server, "secs": secs, "conn": conn, "reverse": reverse} if custom else None
        if not diag: counted(now, "by hand (POST /captest)")
        threading.Thread(target=run_iperf, args=(now, [server] if server else IPERFS, secs or ISECS, conn or CONN, reverse, None, diag, not diag),
                         daemon=True).start()
    else:
        threading.Thread(target=run, args=(slot_of(now), now, True, url, conn, secs), daemon=True).start()
    return True


def info(now=None):
    """API (GET /captest): running, slots, the last test with the modem's raw values, recent results, the ceilings now."""
    now = now or time.time(); hist = state().get("captest_hist") or []
    return {"running": st["running"], "slots": SLOTS, "period_now": slot_of(now), "upload_test": UP, "host_fallback": HOST,
            "traffic_skip": state().get("captest_traffic_skip"),
            "server_list": "ok %s" % st["servers_day"] if st["servers"] else "not fetched yet / failed (fallback %s)" % HOST,
            "last": state().get("captest_last"), "recent": hist[-20:],
            "ceiling_now": {d: ceiling(d, now) for d in ("down", "up")},
            "ceilings_by_period": {s: ceilings(hist, s) for s in SLOTS},
            "shaper_ceiling_now": (sqm.st.get("last") or {}).get("ceiling"), "climb": sqm.st.get("climb"), "climbs": sqm.st.get("climbs"),
            "explain": explain(now), "counted_by_period": {s: {d: len(counted_results(hist, s, d)) for d in ("down", "up")} for s in SLOTS},
            "trigger": {"pending": st.get("want"), "setup_now": st.get("seen_key"), "setup_tested": state().get("captest_setup"),
                        "today": trig_today(now), "max_a_day": TRIG_MAX}}


def week_text(now):
    """The week's capacity summary: counted tests, the ceilings now, and the shaper's evidence that keeps phases 3 / 4 closed (2026-11-17:
    busy minutes' latency ~ idle + 1-2 ms, so there is no rate where the delay rises to learn a lower ceiling from). Latency = the
    minute's max delay above the idle baseline (sqm_delay); busy = rx >= 50 % of the download limit; cut = the limit fell > 10 % in the minute."""
    frm = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 7 * 86400)); hist = state().get("captest_hist") or []
    wk = [r for r in hist if r.get("time", "") >= frm and not r.get("diag") and not r.get("url")]
    g = lambda v: "-" if v is None else "%g" % round(v, 1)
    rng = lambda xs: "%s-%s (median %s)" % (g(min(xs)), g(max(xs)), g(statistics.median(xs))) if xs else "none"
    f, med = brain.f, lambda xs: statistics.median(xs) if xs else None
    rows = [r for r in store.rows(common.DATA, "samples", frm) if f(r.get("sqm_rx")) is not None]
    load = lambda r: f(r["sqm_rx"]) / f(r["sqm_down"]) if f(r.get("sqm_down")) else 0
    dly = lambda sel: g(med([f(r["sqm_delay"]) for r in rows if sel(r) and f(r.get("sqm_delay")) is not None]))
    idle = lambda r: f(r["sqm_rx"]) < 1 and (f(r.get("sqm_tx")) or 0) < 0.5
    cpu = [r for r in rows if (f(r.get("sqm_cpu")) or 0) >= 90]; spikes = sqm.st.get("spikes") or []
    return "\n".join([
        "capacity week to %s: %d tests (%d extra) - down %s, up %s Mbit/s" % (time.strftime("%Y-%m-%d", time.localtime(now)), len(wk),
            len([r for r in wk if r.get("trigger")]), rng([r["down"] for r in wk if r.get("down")]), rng([r["up"] for r in wk if r.get("up")])),
        "ceilings down/up: " + " · ".join("%s %s/%s" % (s, g(c.get("down")), g(c.get("up"))) for s in SLOTS for c in [ceilings(hist, s)]),
        "shaper: %d busy min (>= 50 %% of the limit), %d at the ceiling; latency median idle %s / busy %s / at the ceiling %s ms; %d min with a cut; "
        "climbs since start: %d" % (len([r for r in rows if load(r) >= 0.5]), len([r for r in rows if load(r) >= 0.9]), dly(idle),
            dly(lambda r: load(r) >= 0.5), dly(lambda r: load(r) >= 0.9),
            len([r for r in rows if (f(r.get("sqm_down_min")) or 0) and f(r["sqm_down_min"]) < 0.9 * (f(r.get("sqm_down")) or 0)]), len(sqm.st.get("climbs") or [])),
        "router CPU >= 90 %%: %d min (%d idle)%s" % (len(cpu), len([r for r in cpu if idle(r)]), "; last spike: " + spikes[-1] if spikes else "")])


def weekly(row, now=None):
    """Minute job: week_text() to Telegram once a week at CAPTEST_WEEKLY (or the first minute after it that day: a power cut)."""
    now = now or time.time(); lt = time.localtime(now)
    try: day, hm = WEEKLY.split()
    except ValueError: return False                                     # empty / misconfigured: off
    if not common.clock_ok() or time.strftime("%a", lt) != day or time.strftime("%H:%M", lt) < hm: return False
    wk = time.strftime("%G-W%V", lt)
    if state().get("captest_weekly") == wk: return False
    state({"captest_weekly": wk})                                       # first: a failing summary must not retry every minute
    txt = week_text(now); log("weekly capacity summary: " + txt.replace("\n", " | ")); alert.report(txt)
    return True


def maybe_test(row, now=None):
    """Minute job: start a due slot's test when the link has been idle IDLE_MIN minutes and nothing else runs. True when started."""
    now = now or time.time()
    if not ON or st["running"] or common.busy() or not common.clock_ok(): return False     # lock test / night screen / sweep: busy
    if (brain.f(row.get("uptime_s")) or 0) < MIN_UPTIME_S: return False
    if (state().get("quality_ab") or {}).get("phase") == "trial": return False
    done = state().get("captest_done") or {}
    why = trigger(row, now)
    cf = st.get("confirm"); confirming = bool(cf) and now >= cf["at"] and trig_today(now) < TRIG_MAX and not slot_soon(now)
    if confirming: why = cf["why"]                   # the confirmation of a low result: inside the 1-h gap, once
    s = due(now, done)
    if not s and not (why and (confirming or trig_ok(now))): return False
    if not s and guard.pause_end(now): return False    # LOCK_PAUSE: the released lock is no setup change and the empty night network
    last = list(brain.HIST)[-IDLE_MIN:]                # is no ceiling; it held the night screen ~40 min (2026-11-20 01:22) - the day's turn
    if len(last) < IDLE_MIN or _traffic_reason(sqm.recent_rate()): return False
    if any(not brain.idle(r) or _traffic_reason({"down": brain.f(r.get("sqm_rx")) or 0, "up": brain.f(r.get("sqm_tx")) or 0}) for r in last):
        return False
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    with common.test_start_lock:
        if st["running"] or common.busy(): return False
        trig = None
        if s:
            done = state().get("captest_done") or {}
            slot_key = "%s %s" % (day, s)
            if done.get(slot_key): return False
            cutoff = time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400))
            state({"captest_done": {k: v for k, v in done.items() if k[:10] >= cutoff} | {slot_key: True}})
        else:
            s, trig = slot_of(now), why
            state({"captest_trig": [t for t in state().get("captest_trig") or [] if now - t < 86400] + [now]})
        counted(now, trig)
        st["running"] = True
    if METHOD == "iperf3": threading.Thread(target=run_iperf, args=(now, IPERFS, ISECS, CONN, True, s, None, True), daemon=True).start()
    else: threading.Thread(target=run, args=(s, now), daemon=True).start()
    return True


_h = state().get("captest_hist") or []
if any(r.get("down") and not valid(r, "down") for r in _h):                        # 2026-11-13: drop the 5.9 Mbit/s non-result
    state({"captest_hist": [dict(r, down=None, down_how="invalid: " + str(r.get("down_how"))) if r.get("down") and not valid(r, "down") else r for r in _h]})
acs.hooks["read"].append(extra_read); acs.hooks["values"].append(on_values)   # the ACS reads the test's result while it runs
sqm.CEILING[0] = ceiling                                                        # the shaper's ceiling: learned per period


if __name__ == "__main__":                       # self-check of the pure parts (no modem, no network)
    L = [{"sponsor": "Yettel Bulgaria", "cc": "BG", "id": "67058", "host": "speedtest2.yettel.bg:8080", "distance": 82, "url": "http://speedtest2.yettel.bg:8080/speedtest/upload.php"},
         {"sponsor": "Yettel Bulgaria", "cc": "BG", "id": "69006", "host": "speedtest1.yettel.bg:8080", "distance": 3, "url": "http://speedtest1.yettel.bg:8080/speedtest/upload.php"},
         {"sponsor": "Yettel d.o.o.", "cc": "RS", "id": "14476", "host": "x:8080", "distance": 1}]
    assert pick(L)["id"] == "69006" and pick(L[:1] + L[2:])["id"] == "67058" and pick(L[2:]) is None and pick([]) is None
    assert pick(L, want_id="1")["id"] == "69006"                                         # id gone: the nearest Yettel BG
    assert down_params("h:8080")[0][2] == "http://h:8080/speedtest/random4000x4000.jpg"
    assert up_params(L[1])[0][2] == L[1]["url"] and up_params({"host": "h:8080"})[0][2] == "http://h:8080/speedtest/upload.php"
    assert iso("2026-11-13T07:30:01.500000Z") - iso("2026-11-13T07:30:00Z") == 1.5
    assert abs(iso("2026-11-13T07:30:02.123456789Z") - iso("2026-11-13T07:30:00.123Z") - 2.000456) < 1e-5 and iso("0001-01-01T00:00:00Z") is None
    v = {DL + "DiagnosticsState": "Completed", DL + "TestBytesReceivedUnderFullLoading": "112500000", DL + "PeriodOfFullLoading": "5000000"}
    assert result(v, DL) == (180.0, "full load 5.0 s")
    v = {DL + "DiagnosticsState": "Completed", DL + "TestBytesReceived": "25000000", DL + "BOMTime": "2026-11-13T07:30:00Z", DL + "EOMTime": "2026-11-13T07:30:08Z"}
    assert result(v, DL) == (25.0, "TestBytesReceived BOM-EOM 8.0 s")
    assert result({UL + "DiagnosticsState": "Error_NoTransferComplete"}, UL) == (None, "Error_NoTransferComplete")
    v = {DL + "DiagnosticsState": "Completed", DL + "TestBytesReceived": "0", DL + "TotalBytesReceived": "62500000",
         DL + "ROMTime": "2026-11-13 18:30:00", DL + "BOMTime": "", DL + "EOMTime": "2026-11-13 18:30:05.000"}
    assert result(v, DL) == (100.0, "TotalBytesReceived ROM-EOM 5.0 s"), result(v, DL)          # space-separated dates, total bytes, ROM
    r = result({DL + "DiagnosticsState": "Completed", DL + "TestBytesReceived": "0", DL + "EOMTime": "garbage"}, DL)
    assert r[0] is None and "TestBytesReceived=0" in r[1] and "EOMTime=garbage" in r[1], r                     # the values in the why
    assert iso("2026-11-13T18:30:00+02:00") == iso("2026-11-13T16:30:00Z")
    assert mbit("176.3 Mbits/sec") == 176.3 and mbit("176300000") == 176.3 and mbit("176.3") == 176.3 and mbit("950 Kbits/sec") == 1.0 and mbit("") is None
    rep = "[SUM]   0.00-10.00  sec   205 MBytes   172 Mbits/sec  12   sender\n[SUM]   0.00-10.00  sec   203 MBytes   170 Mbits/sec        receiver"
    assert ip3_result({"Report": rep}) == (170.0, "iperf3 receiver (Report)") and ip3_result({"DownlinkSpeed": "168.5"})[0] == 168.5
    assert ip3_result({"Status": "Error"})[0] is None
    p = dict((k[len(IP3):], (t, v)) for k, t, v in iperf_params("37.19.203.1:5201", 10, 4, True))
    assert p["ServerAddress"][1] == "37.19.203.1" and p["Reverse"][1] == "1" and p["Parallel"][1] == "4" and "Enable" not in p
    sent = []
    def fake(params, reboot=False, timeout=180):                               # the modem refuses Omit (optional) and Reverse typed
        sent.append([n[len(IP3):] for n, t, v in params]); bad = []
        if any(n.endswith(".Omit") for n, t, v in params): bad.append((IP3 + "Omit", "9007", "Invalid parameter value"))
        if any(n.endswith(".Reverse") and t != "xsd:string" for n, t, v in params): bad.append((IP3 + "Reverse", "9006", "Invalid parameter type"))
        acs.tr["refused"] = bad; return not bad
    real, acs.tr069_set = acs.tr069_set, fake
    ok, notes = write_settings(iperf_params("h:5201"))
    assert ok and notes == ["dropped Omit", "Reverse as string"] and "Omit" not in sent[-1], (ok, notes, sent)
    acs.tr069_set = lambda params, reboot=False, timeout=180: (acs.tr.update(refused=[(IP3 + "Mode", "9008", "read only")] if any(
        n.endswith(".Mode") for n, t, v in params) else []) or not any(n.endswith(".Mode") for n, t, v in params))
    ok, notes = write_settings(iperf_params("h:5201")); assert ok and notes == ["dropped Mode"], notes      # the real MC889
    assert "Mode" in DROP and not any(n.endswith(".Mode") for n, t, v in iperf_params("h:5201"))           # ... not written again
    assert write_settings(iperf_params("h:5201")) == (True, []); DROP.clear()
    real_state = globals()["state"]
    globals()["state"] = lambda u=None: {"tr143": {"writable": {IP3 + "Mode": False, IP3 + "ServerAddress": False, IP3 + "Omit": True}}}
    names = [n[len(IP3):] for n, t, v in iperf_params("h:5201")]
    assert "Mode" not in names and "ServerAddress" in names and "Omit" in names, names   # only optional read-only names are skipped
    globals()["state"] = real_state
    assert "UplinkSpeed=Mbits/sec" in ip3_result({"Status": "Complete", "UplinkSpeed": "Mbits/sec"}, False)[1]   # the real 23:17 upload
    acs.tr069_set = lambda params, reboot=False, timeout=180: params[0][1] == "xsd:string" and params[0][2] == "true"
    assert start_iperf() == (True, "Enable=true (xsd:string)")
    acs.tr069_set = real
    assert all(t == "xsd:string" for _, t, _ in iperf_params("h", plain=True))
    v = {DL + "DiagnosticsState": "Completed", DL + "TestBytesReceived": "0", DL + "TotalBytesReceived": "2827", DL + "PeriodOfFullLoading": "120254",
         DL + "BOMTime": "2026-11-13T18:31:01.679582Z", DL + "EOMTime": "2026-11-13T18:31:01.683401Z"}     # the real 18:30 result
    assert result(v, DL)[0] is None and "TotalBytesReceived=2827" in result(v, DL)[1], result(v, DL)
    assert not valid({"down": 5.9, "down_how": "TotalBytesReceived BOM-EOM 0.0 s"}, "down") and valid({"down_how": "full load 5.5 s"}, "down")
    SLOTS = ["07:30", "12:30", "18:30", "21:30"]
    T = time.mktime((2026, 11, 13, 7, 40, 0, 0, 0, -1))
    assert due(T, {}) == "07:30" and due(T, {"2026-11-13 07:30": True}) is None and due(T + 2 * 3600, {}) is None   # 09:40: out of the window
    assert slot_of(T) == "07:30" and slot_of(T - 3 * 3600) == "21:30" and slot_of(T + 12 * 3600) == "18:30"
    SLOTS = ["01:30", "04:30", "07:30", "12:30", "18:30", "21:30"]
    assert slot_of(T - 3 * 3600) == "04:30" and slot_of(T - 6.5 * 3600) == "21:30" and due(T - 3 * 3600, {}) == "04:30"   # 04:40 -> the 04:30 slot
    SLOTS = ["07:30", "12:30", "18:30", "21:30"]
    H = [{"slot": "18:30", "down": x, "up": u, "down_how": "full load 5.0 s", "up_how": "TestBytesSent BOM-EOM 8.0 s"}
         for x, u in ((150, 25), (170, 27), (90, 20), (160, 26), (300, 40), (165, 26))]
    c = ceilings(H, "18:30"); assert c == {"down": 151.8, "up": 23.9}, c                 # last 5: median 165 / 26 x 0.92
    G = {"slot": "07:30", "down": 260, "up": 45, "down_how": "full load 5.0 s", "up_how": "full load 8.0 s"}
    assert ceilings([G], "07:30") == {} and ceilings([G, G], "07:30") == {"down": 200.0, "up": 30.0}   # 2 tests needed; capped at the plan
    assert ceilings([G, dict(G, down_how="TotalBytesReceived BOM-EOM 0.0 s")], "07:30").get("down") is None   # an invalid one doesn't count
    assert ceilings(H, "07:30") == {}
    print("OK captest")
