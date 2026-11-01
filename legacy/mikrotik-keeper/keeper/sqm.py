"""Adaptive SQM: keep calls clean while you upload / download, on a mobile link whose speed changes all the time.
CAKE queue on the main MikroTik (one simple queue, created once by you - see README), the keeper moves its max-limit every SQM_TICK s
(RouterOS REST API; RouterOS can't limit a user to /queue - use a group read,write,rest-api limited to the container's address):
  latency rises above the idle baseline while a direction is busy  -> that direction's limit down 15 %   (the queue moves into CAKE)
  a direction is busy and latency stays near the baseline          -> its limit up 5 %                     (use what the cell gives)
  limits stay within SQM_MIN_* .. SQM_MAX_* (plan: 200 / 30 Mbit/s).  Idea from cake-autorate (lynxthecat), much simplified.
Latency = the keeper's own probe (brain.lat_buf: TCP connect to 1.1.1.1 / 8.8.8.8 every 2 s) - the same thing a call feels.
Env: SQM_URL (http://192.168.6.1/rest; off when empty), SQM_USER, SQM_PASS, SQM_QUEUE (sqm), SQM_IFACE (ether5 = the WAN),
     SQM_MIN_UP / SQM_MAX_UP / SQM_MIN_DOWN / SQM_MAX_DOWN (Mbit/s: 5 / 26 / 20 / 150 here), SQM_DELAY_MS (15), SQM_TICK (10),
     SQM_INSECURE=1 only if the router's HTTPS certificate is self-signed (LAN only),
     SQM_DOWN_HOURS ("Mon-Fri 09:00-18:00": shape the download only then - outside it the download is unlimited, full speed and no
     CAKE work for the CPU; empty = always). The upload is always shaped: cheap, and the biggest gain for calls.
A TR-069 cell/RAT/boot change rebases the delay baseline once (a new cell has a different round trip; without it a slower cell
reads as permanent congestion and the limit can stay pinned low). The probe alone cannot tell "new path" from "bufferbloat".
Measured 2026-10-31 (RB450Gx4, ether5 = routed WAN, NAT on the RB450, use-ip-firewall=no; single-stream, latency above idle):
download unshaped ~178 Mbit/s; at 120M fq_codel 112 Mbit/s p95 58-80 ms, CAKE 109-111 Mbit/s p95 62-93 (no better); uplink 28.2
Mbit/s unshaped vs 25.2 at 26M with p95 26 vs 33 ms -> queue=cake/fqc, SQM_MAX_UP=26, SQM_MAX_DOWN=150 (README)."""
import base64, json, os, ssl, statistics, threading, time, urllib.error, urllib.request

E = os.environ.get
URL, USER, PASS, QUEUE, IFACE = E("SQM_URL", ""), E("SQM_USER", ""), E("SQM_PASS", ""), E("SQM_QUEUE", "sqm"), E("SQM_IFACE", "ether5")
MIN = {"up": float(E("SQM_MIN_UP", "5")), "down": float(E("SQM_MIN_DOWN", "20"))}
MAX = {"up": float(E("SQM_MAX_UP", "30")), "down": float(E("SQM_MAX_DOWN", "200"))}
DELAY, TICK = float(E("SQM_DELAY_MS", "15")), float(E("SQM_TICK", "10"))
ERROR_WAIT_S = 300
DOWN_HOURS = E("SQM_DOWN_HOURS", "")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
st = {"limit": None, "id": None, "last": None, "changes": 0, "error": None, "path_seen": 0.0}


def rest(method, path, body=None):
    """One RouterOS REST call (Basic auth) -> parsed JSON."""
    req = urllib.request.Request(URL.rstrip("/") + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Basic " + base64.b64encode(("%s:%s" % (USER, PASS)).encode()).decode()})
    ctx = ssl._create_unverified_context() if E("SQM_INSECURE") == "1" else None       # your router's own self-signed cert, LAN only
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r: return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:          # RouterOS says why in the body, e.g. {"error":401,"message":"Unauthorized"}
        raise RuntimeError("%s %s -> HTTP %d %s" % (method, path, e.code, e.read()[:200].decode("utf-8", "replace"))) from None


def mbit(s):
    """RouterOS rate '25M' / '1500k' / '25000000' -> Mbit/s"""
    s = str(s).strip().upper(); f = {"K": 1e-3, "M": 1.0, "G": 1e3}.get(s[-1:], 1e-6)
    return float(s[:-1] if s[-1:] in "KMG" else s) * f


def down_on(t=None):
    """Is download shaping on at time `t` (SQM_DOWN_HOURS, e.g. 'Mon-Fri 09:00-18:00'; empty = always)?"""
    if not DOWN_HOURS: return True
    days, hours = DOWN_HOURS.split()
    a, b = days.split("-") if "-" in days else (days, days)
    lt = time.localtime(t or time.time()); h1, h2 = hours.split("-")
    return DAYS.index(a) <= lt.tm_wday <= DAYS.index(b) and h1 <= time.strftime("%H:%M", lt) < h2


def step(limit, rate, delay):
    """one decision. limit / rate: {"up": Mbit/s, "down": Mbit/s}; delay = ms above the idle baseline (None = no probe data)"""
    new = dict(limit)
    for d in ("up", "down"):
        if not limit[d]: continue                         # 0 = not shaped (unlimited)
        load = rate[d] / limit[d]
        if load < 0.1:                                    # idle: the limit shapes nothing right now
            if delay is None or delay < DELAY: new[d] = limit[d] * 1.5     # ...and the link is calm -> climb back toward MAX, so the next burst isn't stuck at a low limit (a spike while idle isn't ours: leave it)
        elif delay is not None and delay > DELAY and load > 0.5: new[d] = limit[d] * 0.85
        elif delay is not None and delay < DELAY / 2 and load > 0.75: new[d] = limit[d] * 1.05
        new[d] = round(min(MAX[d], max(MIN[d], new[d])), 1)
    return new


def delay_now(lat_buf, now, idle, rebase=False):
    """ms above the idle round trip, per probe target: median of the last TICK s minus that target's idle baseline, averaged over
    the targets. The baseline is a running average of the medians seen while the link was idle (idle=True updates it), so
    cellular jitter at idle reads ~0 and a long download doesn't raise its own baseline. Per target: 1.1.1.1 and 8.8.8.8 have
    different round trips. None until a baseline exists (the first idle tick). rebase=True (the cell/RAT/boot just changed) lets a
    *higher* round trip replace the baseline once: the old baseline belonged to the old cell, so without it a slower cell reads as
    permanent congestion. The probe loop alone cannot tell "new path" from "bufferbloat"."""
    by = {}
    for x in list(lat_buf):
        if x[1] is not None and now - x[0] <= max(TICK, 6): by.setdefault(x[2] if len(x) > 2 else "", []).append(x[1])
    base, d = st.setdefault("base", {}), []
    for k, xs in by.items():
        m = statistics.median(xs)
        if k in base and m < base[k]: base[k] = m              # a lower round trip proves the baseline is lower: follow at once
        elif k in base and rebase and m > base[k]: base[k] = m  # the path changed and the new cell is slower: adopt its round trip
        elif idle: base[k] = m if k not in base else base[k] + 0.2 * (m - base[k])   # (a baseline from a slow start, e.g. right
                                                                                      # after a ZTE boot on LTE only, read -126 ms)
        if k in base: d.append(m - base[k])
    return round(statistics.mean(d), 1) if d else None


def tick(lat_buf, now=None, path_change=0.0):
    """One SQM step: read the queue / traffic, decide, PATCH the limit when it changes >= 2 %.
    path_change = epoch of the last TR-069 cell/RAT/boot change (0 = none); the delay baseline is rebased once per change."""
    now = now or time.time()
    if st["id"] is None:
        q = [x for x in rest("GET", "/queue/simple") if x.get("name") == QUEUE]
        if not q: raise RuntimeError("no simple queue named %r on the router" % QUEUE)
        st["id"] = q[0][".id"]; up, down = q[0]["max-limit"].split("/")
        st["limit"] = {"up": min(MAX["up"], mbit(up) or MAX["up"]), "down": min(MAX["down"], mbit(down) or MAX["down"])}
    m = rest("POST", "/interface/monitor-traffic", {"interface": IFACE, "once": True})[0]
    rate = {"up": int(m["tx-bits-per-second"]) / 1e6, "down": int(m["rx-bits-per-second"]) / 1e6}
    over = [k for k in rate if st["limit"][k] and rate[k] > 5 and rate[k] > 1.25 * st["limit"][k]]
    st["over"] = st.get("over", 0) + 1 if over else 0
    if st["over"] >= 3:          # traffic passes the queue: cutting further does nothing (and leaves a low limit once it is fixed)
        st["over"] = 0
        raise RuntimeError("queue %r is not shaping: %s %.0f Mbit/s for 30 s with limit %.0f - fasttrack still on, or the queue's "
                           "target / dst don't match the traffic" % (QUEUE, over[0], rate[over[0]], st["limit"][over[0]]))
    idle = all(rate[k] < 0.1 * (st["limit"][k] or MAX[k]) for k in rate)
    rebase = bool(path_change) and path_change > st.get("path_seen", 0) and now - path_change < 180
    if rebase: st["path_seen"] = path_change
    d = delay_now(lat_buf, now, idle, rebase); new = step(st["limit"], rate, d)
    if rebase:                      # do not raise the limit on the rebase tick: let the next ticks see if the new path is calm
        for k in new:
            if st["limit"][k] and new[k] > st["limit"][k]: new[k] = st["limit"][k]
    if not down_on(now):                                  # outside SQM_DOWN_HOURS: download unlimited, remember the shaped limit
        if st["limit"]["down"]: st["down_kept"] = st["limit"]["down"]
        new["down"] = 0
    elif not new["down"]:
        new["down"] = st.get("down_kept") or MAX["down"]
    try: cpu = [int(c.get("load", 0)) for c in rest("GET", "/system/resource/cpu")]   # per core %, the price of CAKE
    except Exception: cpu = []
    if cpu: st["cpu_max"] = max(st.get("cpu_max", 0), max(cpu))
    st["last"] = {"time": time.strftime("%H:%M:%S"), "rate": rate, "delay_ms": None if d is None else round(d, 1), "limit": new,
                  "cpu": cpu, "cpu_max_since_start": st.get("cpu_max")}
    if any(new[k] != st["limit"][k] and (not new[k] or not st["limit"][k] or abs(new[k] - st["limit"][k]) >= 0.02 * st["limit"][k]) for k in new):
        fmt = lambda v: "%dk" % (v * 1000) if v else "0"    # 0 = unlimited
        rest("PATCH", "/queue/simple/%s" % st["id"], {"max-limit": "%s/%s" % (fmt(new["up"]), fmt(new["down"]))})
        st["limit"] = new; st["changes"] += 1
        return new
    return None


def loop(lat_buf, log, path_change=None):
    """Thread: `tick()` every SQM_TICK s. After an error (wrong password, no queue, router busy) it waits ERROR_WAIT_S (300) -
    a failing login every 10 s would flood the router's log; the queue keeps its last limit meanwhile."""
    while True:
        time.sleep(TICK)
        try:
            ch = tick(lat_buf, path_change=(path_change() if path_change else 0.0)); st["error"] = None
            if ch and st["changes"] % 30 == 1: log("SQM limit now %s/%s Mbit/s up/down (delay %s ms)" % (ch["up"], ch["down"], st["last"]["delay_ms"]))
        except Exception as e:
            if st["error"] != str(e): log("SQM: %s - retry in %d s" % (e, ERROR_WAIT_S))
            st["error"] = str(e); st["id"] = None
            time.sleep(ERROR_WAIT_S)


def start(lat_buf, log, path_change=None):
    """Start the SQM thread when SQM_URL is set. path_change() -> epoch of the last TR-069 cell/RAT/boot change."""
    if URL: threading.Thread(target=loop, args=(lat_buf, log, path_change), daemon=True).start()


if __name__ == "__main__":          # self-check of the decision rule
    lim = {"up": 20.0, "down": 150.0}
    assert step(lim, {"up": 19, "down": 10}, 40) == {"up": 17.0, "down": 150.0}          # upload fills the queue: up down 15 %, down kept
    assert step(lim, {"up": 18, "down": 140}, 2) == {"up": 21.0, "down": 157.5}           # busy and clean: both up 5 %
    assert step({"up": 29.0, "down": 199.0}, {"up": 28, "down": 190}, 1) == {"up": 30.0, "down": 200.0}   # capped at the plan
    assert step({"up": 5.5, "down": 21.0}, {"up": 5, "down": 20}, 90) == {"up": 5.0, "down": 20.0}        # floor
    assert step(lim, {"up": 1, "down": 3}, 50) == lim                                   # idle link: latency spike is not ours, keep
    assert step({"up": 10.0, "down": 20.0}, {"up": 0.01, "down": 0.01}, 3) == {"up": 15.0, "down": 30.0}   # idle and calm: climb back toward MAX (fixes "speedtest stuck at a low limit")
    assert mbit("25M") == 25 and mbit("1500k") == 1.5 and mbit("30000000") == 30
    now = 1000.0; st["base"] = {}
    buf = [(now - 8, 30.0, "1.1.1.1"), (now - 6, 50.0, "8.8.8.8"), (now - 4, 34.0, "1.1.1.1"), (now - 2, 54.0, "8.8.8.8")]
    assert delay_now(buf, now, False) is None and delay_now(buf, now, True) == 0.0       # no baseline yet; idle sets it (32 / 52)
    load = [(now - 8, 72.0, "1.1.1.1"), (now - 6, 92.0, "8.8.8.8"), (now - 4, 72.0, "1.1.1.1"), (now - 2, 92.0, "8.8.8.8")]
    assert delay_now(load, now, False) == 40.0 and st["base"] == {"1.1.1.1": 32.0, "8.8.8.8": 52.0}    # loaded: baseline kept
    low = [(now - 4, 20.0, "1.1.1.1"), (now - 2, 40.0, "8.8.8.8")]
    assert delay_now(low, now, False) == 0.0 and st["base"] == {"1.1.1.1": 20.0, "8.8.8.8": 40.0}        # lower: baseline follows down
    st["base"] = {"1.1.1.1": 32.0, "8.8.8.8": 52.0}
    assert delay_now(load, now, False, rebase=True) == 0.0 and st["base"] == {"1.1.1.1": 72.0, "8.8.8.8": 92.0}  # new cell, slower path: adopt it once
    st["base"] = {}
    assert step({"up": 20.0, "down": 0}, {"up": 19, "down": 180}, 40) == {"up": 17.0, "down": 0}          # unlimited stays unlimited
    DOWN_HOURS = "Mon-Fri 09:00-18:00"
    wed10, wed19, sat10 = (time.mktime((2026, 10, 28, h, 0, 0, 0, 0, -1)) + dd * 86400 for h, dd in ((10, 0), (19, 0), (10, 3)))
    assert down_on(wed10) and not down_on(wed19) and not down_on(sat10)
    DOWN_HOURS = ""
    calls = []                                                                      # a queue that doesn't shape: error, no more cuts
    rest = lambda method, path, body=None: (calls.append((method, body)) or
        ([{".id": "*1", "name": QUEUE, "max-limit": "30M/150M"}] if method == "GET" else [{"tx-bits-per-second": "1000000", "rx-bits-per-second": "280000000"}]))
    st.update(id=None, limit=None)
    errs = 0
    for i in range(3):
        try: tick([], now)
        except RuntimeError as e: errs += 1; assert "not shaping: down 280" in str(e), e
    assert errs == 1 and not any(m == "PATCH" for m, _ in calls), calls                # no delay data -> no cut; 3rd tick -> error
    calls.clear(); st.update(id=None, limit=None, over=0); DOWN_HOURS = "Mon-Fri 09:00-18:00"           # 1970-01-01 = Thursday night
    rest = lambda method, path, body=None: (calls.append((method, body)) or
        ([{".id": "*1", "name": QUEUE, "max-limit": "30M/150M"}] if method == "GET" else [{"tx-bits-per-second": "100000", "rx-bits-per-second": "300000000"}]))
    tick([], now); tick([], now)
    assert [b for m, b in calls if m == "PATCH"] == [{"max-limit": "30000k/0"}] and st["down_kept"] == 150.0, calls   # once, no "not shaping"
    DOWN_HOURS = ""; tick([], now)
    assert [b for m, b in calls if m == "PATCH"][-1] == {"max-limit": "30000k/150000k"}                                 # back: the kept limit
    calls.clear(); st.update(id=None, limit=None, over=0, base={"1.1.1.1": 32.0, "8.8.8.8": 52.0}, path_seen=0.0)
    DOWN_HOURS = ""
    rest = lambda method, path, body=None: (calls.append((method, body)) or
        ([{".id": "*1", "name": QUEUE, "max-limit": "20M/100M"}] if method == "GET" else [{"tx-bits-per-second": "1000000", "rx-bits-per-second": "5000000"}]))
    tick(load, now, path_change=now)              # cell change: rebase the baseline, but do not raise the limit on this tick
    assert st["base"] == {"1.1.1.1": 72.0, "8.8.8.8": 92.0} and st["path_seen"] == now, st["base"]
    assert not [b for m, b in calls if m == "PATCH"], calls
    tick(load, now)                               # next tick: normal behaviour, and the same event is not rebased twice
    assert [b for m, b in calls if m == "PATCH"] == [{"max-limit": "30000k/150000k"}], calls
    print("OK sqm")
