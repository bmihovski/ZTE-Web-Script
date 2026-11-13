"""Capacity test: the modem itself measures the link (TR-143 Download / UploadDiagnostics) 4x a day against the operator's own
Ookla server, and the SQM ceilings follow what the cell delivers in each period of the day.

Why the modem: the keeper runs on the RB450 (Cortex-A7, no AES instructions) and can't pull 200 Mbit/s itself; the modem's test
traffic starts and ends at the modem, so the RB450, its CPU and the shaper are out of the measurement. Probed 2026-11-13: the MC889
has full TR-143 (NumberOfConnections, PeriodOfFullLoading, *UnderFullLoading) + a ZTE Iperf3 object.
Server: Ookla's list (https, small, once a day) -> CAPTEST_SERVER_ID (Yettel Sofia 69006) if listed, else the nearest server of
CAPTEST_SPONSOR in CAPTEST_CC (Yettel Plovdiv 67058), else CAPTEST_HOST. Checked 2026-11-13 from the LAN: plain-HTTP
/speedtest/random*.jpg answer 200, /download?size= answers 503 - so the download is random4000x4000.jpg (~31 MB) per connection.
Run: one slot (CAPTEST_SLOTS) at a time, within 90 min after it, after CAPTEST_IDLE_MIN idle minutes, outside LOCK_PAUSE, not during
a lock test / sweep / quality A/B; download then upload, results read from the modem's next informs (fast mode while busy).
Result: Mbit/s = bytes under full loading * 8 / PeriodOfFullLoading (us) - only the time all connections ran, no TCP warm-up;
older firmware: TestBytes / (EOMTime - BOMTime). Ceiling per slot = CAPTEST_SHARE x median of its last CAPTEST_KEEP results,
capped at the plan (SQM_PLAN_DOWN / SQM_PLAN_UP) - the shaper must sit just below the real bottleneck to own the queue.
Env: CAPTEST (1), CAPTEST_SLOTS (07:30,12:30,18:30,21:30), CAPTEST_CONN (4), CAPTEST_UP_BYTES (25000000), CAPTEST_SERVER_ID (69006),
CAPTEST_SPONSOR (Yettel), CAPTEST_CC (BG), CAPTEST_HOST (speedtest1.yettel.bg:8080), CAPTEST_IDLE_MIN (10), CAPTEST_SHARE (0.92),
CAPTEST_KEEP (5), SQM_PLAN_DOWN (200), SQM_PLAN_UP (30), CAPTEST_UP (0: no upload test, the upload ceiling is SQM_MAX_UP).
Upload: TR-143 specifies HTTP PUT; tools/tr143_sim.py showed 2026-11-13 that Ookla drops PUT (POST works). The modem's upload is
tried; an Error_* turns it off for good (state captest_up, one message) - the upload ceiling then stays SQM_MAX_UP.
"""
import datetime, json, re, statistics, threading, time, urllib.parse, urllib.request

from keeper import acs, brain, common, guard, sqm
from keeper.common import E, event, log, state

DL, UL = "Device.IP.Diagnostics.DownloadDiagnostics.", "Device.IP.Diagnostics.UploadDiagnostics."
ON = E("CAPTEST", "1") == "1"
SLOTS = sorted(s.strip() for s in E("CAPTEST_SLOTS", "07:30,12:30,18:30,21:30").split(",") if s.strip())
CONN, UP_BYTES = int(E("CAPTEST_CONN", "4")), int(E("CAPTEST_UP_BYTES", "25000000"))
UP = E("CAPTEST_UP", "0") == "1"     # upload test: off - in NSA the upload rides the LTE anchor and sits at the plan cap (~28 Mbit/s,
                                     # 10-31 and 11-13): the upload ceiling stays SQM_MAX_UP, the live controller cuts below it
SERVER_ID, SPONSOR, CC = E("CAPTEST_SERVER_ID", "69006"), E("CAPTEST_SPONSOR", "Yettel"), E("CAPTEST_CC", "BG")
HOST = E("CAPTEST_HOST", "speedtest1.yettel.bg:8080")
IDLE_MIN, SHARE, KEEP = int(E("CAPTEST_IDLE_MIN", "10")), float(E("CAPTEST_SHARE", "0.92")), int(E("CAPTEST_KEEP", "5"))
PLAN = {"down": float(E("SQM_PLAN_DOWN", "200")), "up": float(E("SQM_PLAN_UP", "30"))}
WINDOW_MIN, RESULT_WAIT_S = 90, 240
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


def down_params(host):
    """TR-143 download: CONN parallel GETs of the classic Ookla file (~31 MB each), file based (no time limit)."""
    return [(DL + "DownloadURL", "xsd:string", "http://%s/speedtest/random4000x4000.jpg" % host),
            (DL + "NumberOfConnections", "xsd:unsignedInt", str(CONN)), (DL + "DiagnosticsState", "xsd:string", "Requested")]


def up_params(server):
    """TR-143 upload to the server's own upload URL (Ookla lists it), CONN connections, UP_BYTES in total."""
    url = server.get("url") or "http://%s/speedtest/upload.php" % server["host"]
    return [(UL + "UploadURL", "xsd:string", url), (UL + "TestFileLength", "xsd:unsignedInt", str(UP_BYTES)),
            (UL + "NumberOfConnections", "xsd:unsignedInt", str(CONN)), (UL + "DiagnosticsState", "xsd:string", "Requested")]


def iso(s):
    """TR-069 dateTime ('2026-11-13T07:30:01.123456Z', any fraction length) -> epoch, None if unset / unreadable."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$", (s or "").strip())
    if not m or s.startswith("0001"): return None
    try: t = datetime.datetime.fromisoformat(m.group(1) + ("+00:00" if m.group(3) in (None, "Z") else m.group(3))).timestamp()
    except ValueError: return None
    return t + (float("0." + m.group(2)) if m.group(2) else 0.0)


def result(vals, base):
    """(Mbit/s or None, how / why) from a read of `base` (DL / UL). Full-loading counters first (all connections running, no
    warm-up), else bytes over BOM..EOM."""
    g = lambda k: vals.get(base + k, "")
    s = g("DiagnosticsState")
    if s not in FINAL: return None, s or "no result"
    b = brain.f(g("TestBytesReceivedUnderFullLoading") or g("TestBytesSentUnderFullLoading")); p = brain.f(g("PeriodOfFullLoading"))
    if b and p: return round(b * 8 / p, 1), "full load %.1f s" % (p / 1e6)
    b = brain.f(g("TestBytesReceived") or g("TestBytesSent")); t0, t1 = iso(g("BOMTime")), iso(g("EOMTime"))
    if b and t0 and t1 and t1 > t0: return round(b * 8 / (t1 - t0) / 1e6, 1), "BOM-EOM %.1f s" % (t1 - t0)
    return None, "no bytes in the result"


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


def ceilings(hist, slot):
    """{"down": Mbit/s, "up": Mbit/s} for `slot` from its last KEEP results: SHARE x median, capped at the plan; {} without data."""
    out = {}
    for d in ("down", "up"):
        xs = [r[d] for r in hist if r.get("slot") == slot and r.get(d)][-KEEP:]
        if xs: out[d] = round(min(PLAN[d], SHARE * statistics.median(xs)), 1)
    return out


def ceiling(d, now):
    """The SQM ceiling for direction `d` now: learned for this period of the day, else the env SQM_MAX_* (sqm.MAX)."""
    if d == "up" and not UP: return sqm.MAX["up"]                                    # upload: your SQM_MAX_UP
    c = ceilings(state().get("captest_hist") or [], slot_of(now)).get(d)
    return max(sqm.MIN[d], c) if c else sqm.MAX[d]


# ---------- the run ----------
def servers(now):
    """Ookla's list, fetched once a day (HTTPS, a few kB); the last good list if the fetch fails."""
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if st["servers_day"] != day:
        try:
            with urllib.request.urlopen(LIST_URL % urllib.parse.quote(SPONSOR), timeout=15) as r: st["servers"] = json.loads(r.read())
            st["servers_day"] = day
        except Exception as e:
            log("capacity test: Ookla server list failed: %r - using %s" % (e, HOST))
    return st["servers"] or []


def extra_read():
    """Names the ACS adds to its GetParameterValues while a test runs (the result arrives with the modem's next informs)."""
    return [st["base"]] if st["running"] and st["base"] else []


def on_values(vals):
    """ACS hook (every read): the running test's state; a final one (Completed / Error_*) after it was armed ends the wait."""
    if not (st["running"] and st["base"]) or (st["base"] + "DiagnosticsState") not in vals: return
    s = vals[st["base"] + "DiagnosticsState"]
    if s == "Requested": st["seen_requested"] = True; return
    rom = iso(vals.get(st["base"] + "ROMTime", ""))
    if s and s != "None" and (st["seen_requested"] or (rom and rom >= st["armed"] - 60)):   # not the previous test's result
        st["vals"] = vals; st["ev"].set()


def _one(base, params):
    """Arm one TR-143 test and wait for its result: (Mbit/s or None, why)."""
    st.update(base=base, seen_requested=False, vals=None, armed=time.time()); st["ev"].clear()   # armed BEFORE the write: the
    ok = acs.tr069_set(params)                                  # session that carries it first reads the previous test's result
    if not ok and any("NumberOfConnections" in p[0] for p in params):                    # an older object: one connection
        ok = acs.tr069_set([p for p in params if "NumberOfConnections" not in p[0]])
    if not ok: return None, "the modem refused the test parameters"
    if not st["ev"].wait(RESULT_WAIT_S): return None, "no result within %d s" % RESULT_WAIT_S
    return result(st["vals"], base)


def run(slot, now=None):
    """Thread: download then upload test in `slot`, record the result, move the ceilings."""
    now = now or time.time()
    try:
        srv = pick(servers(now)) or {"host": HOST, "id": "?", "name": "fallback"}
        down, dwhy = _one(DL, down_params(srv["host"]))
        off = "CAPTEST_UP=0, the ceiling is SQM_MAX_UP" if not UP else (state().get("captest_up") or {}).get("off")
        if off: up, uwhy = None, "upload test off (%s)" % off
        else:
            up, uwhy = _one(UL, up_params(srv))
            if up is None and (uwhy.startswith("Error_") or "refused" in uwhy):   # 2026-11-13: Ookla drops HTTP PUT (TR-143's method)
                state({"captest_up": {"off": uwhy, "time": time.strftime("%Y-%m-%d %H:%M:%S")}})
                event({"event": "capacity ceiling", "to": "upload test off", "why": "the modem's TR-143 upload failed (%s) - Ookla's"
                       " upload.php takes POST, not PUT; the upload ceiling stays SQM_MAX_UP" % uwhy})
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "slot": slot, "down": down, "up": up,
               "server": "%s %s" % (srv.get("id"), srv.get("name", "")), "down_how": dwhy, "up_how": uwhy}
        hist = (state().get("captest_hist") or []) + [rec]
        before = ceilings(state().get("captest_hist") or [], slot)
        state({"captest_hist": hist[-80:]}); after = ceilings(hist, slot)
        log("capacity test %s: down %s Mbit/s (%s), up %s Mbit/s (%s) - %s" % (slot, down, dwhy, up, uwhy, rec["server"]))
        event({"event": "capacity test", "to": "down %s / up %s Mbit/s" % (down, up), "why": "%s slot %s: %s; %s" % (rec["server"], slot, dwhy, uwhy)})
        moved = [d for d in after if not before.get(d) or abs(after[d] - before[d]) > 0.1 * before[d]]
        if moved:
            event({"event": "capacity ceiling", "to": "%s: down %s / up %s Mbit/s" % (slot, after.get("down", "-"), after.get("up", "-")),
                   "why": "%g x the median of the last %d tests in this period (plan %g / %g)" % (SHARE, KEEP, PLAN["down"], PLAN["up"])})
    except Exception as e:
        log("capacity test failed: %r" % e)
    finally:
        st.update(running=False, base=None)


def maybe_test(row, now=None):
    """Minute job: start a due slot's test when the link has been idle IDLE_MIN minutes and nothing else runs. True when started."""
    now = now or time.time()
    if not ON or st["running"] or common.busy() or not common.clock_ok() or guard.pause_end(now): return False
    if (state().get("quality_ab") or {}).get("phase") == "trial": return False
    done = state().get("captest_done") or {}
    s = due(now, done)
    if not s: return False
    last = list(brain.HIST)[-IDLE_MIN:]
    if len(last) < IDLE_MIN or not all(brain.idle(r) for r in last): return False
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    state({"captest_done": {k: v for k, v in done.items() if k[:10] >= time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400))}
           | {"%s %s" % (day, s): True}})
    st["running"] = True
    threading.Thread(target=run, args=(s, now), daemon=True).start()
    return True


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
    assert result(v, DL) == (25.0, "BOM-EOM 8.0 s")
    assert result({UL + "DiagnosticsState": "Error_NoTransferComplete"}, UL) == (None, "Error_NoTransferComplete")
    SLOTS = ["07:30", "12:30", "18:30", "21:30"]
    T = time.mktime((2026, 11, 13, 7, 40, 0, 0, 0, -1))
    assert due(T, {}) == "07:30" and due(T, {"2026-11-13 07:30": True}) is None and due(T + 2 * 3600, {}) is None   # 09:40: out of the window
    assert slot_of(T) == "07:30" and slot_of(T - 3 * 3600) == "21:30" and slot_of(T + 12 * 3600) == "18:30"
    H = [{"slot": "18:30", "down": x, "up": u} for x, u in ((150, 25), (170, 27), (90, 20), (160, 26), (300, 40), (165, 26))]
    c = ceilings(H, "18:30"); assert c == {"down": 151.8, "up": 23.9}, c                 # last 5: median 165 / 26 x 0.92
    assert ceilings([{"slot": "07:30", "down": 260, "up": 45}], "07:30") == {"down": 200.0, "up": 30.0}   # capped at the plan
    assert ceilings(H, "07:30") == {}
    print("OK captest")
