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
     SQM_CUT_PROOF (3) / SQM_CUT_COOL_S (600): see earn_keep(), SQM_PLAN_UP / SQM_PLAN_DOWN (30 / 200) and SQM_CLIMB_S (600): see climb(),
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
CUT_PROOF, CUT_COOL = int(E("SQM_CUT_PROOF", "3")), float(E("SQM_CUT_COOL_S", "600"))   # cuts that must lower the delay / pause when they don't
ERROR_WAIT_S = 300
CEILING = [lambda d, now: MAX[d]]          # the ceiling now per direction (keeper.captest learns one per period of the day)
PLAN = {"down": float(E("SQM_PLAN_DOWN", "200")), "up": float(E("SQM_PLAN_UP", "30"))}   # what Yettel sells: the climb's top
CLIMB_S, CLIMB = float(E("SQM_CLIMB_S", "600")), 1.05   # busy at the ceiling with a clean delay this long -> the ceiling +5 % (latency first: slow)
DEEP, DEEP_N = 0.7, 3                                   # cuts below 70 % of the ceiling, 3 in an hour -> the line lost capacity: ask for a test
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
    try:
        days, hours = DOWN_HOURS.split(); h1, h2 = hours.split("-")
        a, b = days.split("-") if "-" in days else (days, days)
        wd_a, wd_b = DAYS.index(a), DAYS.index(b)
    except (ValueError, KeyError):
        return True                                           # misconfigured SQM_DOWN_HOURS -> shape always (safe), never disable SQM in an error loop
    lt = time.localtime(t or time.time()); hhmm = time.strftime("%H:%M", lt)
    day_ok = wd_a <= lt.tm_wday <= wd_b
    time_ok = (h1 <= hhmm < h2) if h1 <= h2 else (hhmm >= h1 or hhmm < h2)     # window may wrap past midnight
    return day_ok and time_ok


def step(limit, rate, delay, cap=None):
    """one decision. limit / rate: {"up": Mbit/s, "down": Mbit/s}; delay = ms above the idle baseline (None = no probe data)"""
    new = dict(limit)
    for d in ("up", "down"):
        if not limit[d]: continue                         # 0 = not shaped (unlimited)
        load = rate[d] / limit[d]
        if load < 0.1:                                    # idle: the limit shapes nothing right now
            if delay is None or delay < DELAY: new[d] = limit[d] * 1.5     # ...and the link is calm -> climb back toward MAX, so the next burst isn't stuck at a low limit (a spike while idle isn't ours: leave it)
        elif delay is not None and delay > DELAY and load > 0.5: new[d] = limit[d] * 0.85
        elif delay is not None and delay < DELAY / 2 and load > 0.75: new[d] = limit[d] * 1.05
        new[d] = round(min((cap or MAX)[d], max(MIN[d], new[d])), 1)
    return new


def climb(k, learned, limit, rate, delay, dt=TICK, now=None):
    """The ceiling the shaper may use now in direction `k`: the learned one x a climb factor. Busy at that ceiling (load > 0.75)
    with the delay below DELAY/2 for CLIMB_S s in total -> +5 %, up to the plan; a delay above DELAY while busy -> back to the learned
    value at once. A new learned value (another period, a new test) starts from 1x. Climbing past +10 % asks for a test (want_test)."""
    c = st.setdefault("climb", {}).setdefault(k, {"base": None, "x": 1.0, "clean": 0.0, "asked": False})
    if c["base"] != learned: c.update(base=learned, x=1.0, clean=0.0, asked=False)
    top = lambda: round(min(max(PLAN[k], learned), learned * c["x"]), 1)
    if not limit.get(k): return top()                                       # unshaped (SQM_DOWN_HOURS): nothing to learn
    load = rate[k] / limit[k]
    if delay is not None and delay > DELAY and load > 0.5:
        if c["x"] > 1: c.update(x=1.0); log_climb(k, learned, "back to the learned %g (delay %s ms)" % (learned, delay), now)
        c["clean"] = 0.0
    elif limit[k] >= 0.98 * top() and load > 0.75 and delay is not None and delay < DELAY / 2:
        c["clean"] += dt
        if c["clean"] >= CLIMB_S and top() < PLAN[k]:
            c.update(x=round(c["x"] * CLIMB, 4), clean=0.0); log_climb(k, learned, "%g -> %g Mbit/s: busy at the ceiling with a clean delay" % (
                round(learned * c["x"] / CLIMB, 1), top()), now)
            if c["x"] > 1.1 and not c["asked"]:
                c["asked"] = True; want_test("%s climbed to %g (+%d %% over the learned %g)" % (k, top(), round((c["x"] - 1) * 100), learned), now)
    return top()


def log_climb(k, learned, what, now=None):
    st.setdefault("climbs", []).append({"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now or time.time())), "dir": k, "what": what})
    st["climbs"] = st["climbs"][-20:]


def want_test(why, now=None):
    """Ask keeper.captest for a capacity test at the next idle moment (it rate-limits; the newest reason wins)."""
    st["want_test"] = {"why": why, "at": now or time.time()}


def deep_cut(k, old, new, cap, now):
    """A cut below DEEP x the ceiling: the line gives less than the period's ceiling. DEEP_N of them in an hour -> want_test."""
    if not (old and new and new < old and new < DEEP * cap): return
    xs = [t for t in st.setdefault("deep", {}).get(k, []) if now - t < 3600] + [now]
    st["deep"][k] = xs
    if len(xs) >= DEEP_N:
        st["deep"][k] = []; want_test("%s: the shaper cut below %d %% of the ceiling %g %d times in 1 h" % (k, DEEP * 100, cap, DEEP_N), now)


def reason(old, new, rate, delay, cap, was=None):
    """Why the limit is what it is after this tick, in words (the panel and /status show it).
    was = the previous tick's learned ceiling when it just changed (a new period / test): a limit that only follows it says so."""
    if not (old and new): return "unshaped (outside SQM_DOWN_HOURS)"         # incl. the tick that turns it off
    load, dl = round(100 * rate / old), "latency +%s ms" % delay if delay is not None else "no latency data"
    if was is not None and new != old and new == cap: return "new period ceiling %g (was %g)" % (cap, was)
    if new < old: return "cut %g -> %g: busy (%d %% of the limit) and %s > %g ms" % (old, new, load, dl, DELAY)
    if new > old: return ("idle and calm: back up toward the ceiling %g" % cap if load < 10 else
                          "busy (%d %%) and clean (%s): +5 %% -> %g" % (load, dl, new))
    if old >= 0.98 * cap: return ("idle, limit parked at the ceiling %g (%s)" % (cap, dl) if load < 10 else
                                  "at the ceiling %g (%d %% used, %s)" % (cap, load, dl))
    if now_cooling(): return "holding: cuts paused - the last ones did not lower the latency (queue outside the router)"
    return "holding: %d %% used, %s" % (load, dl)


def cpu_spy(cpu, now, own=None):
    """A core at >= 90 %: which process? RouterOS /tool/profile for 2 s -> 'name usage %' of the top 3 (idle / total skipped), kept
    in st["spikes"] (/status sqm.now.cpu_spikes, the weekly summary). At most every 10 min. The spikes came every ~8 min in hour-long
    windows, mostly on an idle link (2026-11-13..17) - not the shaper's traffic, so the keeper alone cannot say whose they are."""
    if not cpu or max(cpu) < 90 or now - st.get("spy_t", 0) < 600 or st.get("spy_off") or now - st.get("t0", 0) < 120: return None
    st["spy_t"] = now; sp = st.setdefault("spikes", [])
    try:
        use = {}
        r = rest("POST", "/tool/profile", {"duration": "2s"})
        for x in r if isinstance(r, list) else []:          # anything else: shown raw below
            n, u = x.get("name"), float(str(x.get("usage", 0)).rstrip("%") or 0)
            if n not in ("idle", "total"): use[n] = max(use.get(n, 0), u)
        txt = ", ".join("%s %g %%" % (n, u) for n, u in sorted(use.items(), key=lambda kv: -kv[1])[:3]) or "no profile data (raw: %s)" % json.dumps(r)[:300]
    except Exception as e:
        if "not enough permissions" in str(e):     # a policy, not a hiccup: asking again every 10 min only fills the log (2026-11-17 16:46)
            st["spy_off"] = True
            sp.append("profile off until restart: no permission - give the SQM_USER group the policy /tool/profile needs (%s)" % e)
        else: sp.append("profile failed: %s: %s" % (type(e).__name__, e))
        del sp[:-20]; return None
    sp.append("%s core %d %%: %s%s" % (time.strftime("%m-%d %H:%M", time.localtime(now)), max(cpu), txt,
                                       "; keeper %d %%" % own if own is not None else "")); del sp[:-20]    # own: % of one core
    return txt


def now_cooling(now=None):
    now = now or time.time(); return any(now < s.get("cool", 0) for s in (st.get("streak") or {}).values())


def earn_keep(limit, new, delay, now):
    """A cut must lower the latency. After CUT_PROOF cuts in a row in one direction with the delay not >= 20 % below the delay
    before the first cut, the queue is not ours (it sits in the radio / operator buffers): back to the limit before the streak,
    and no cuts in that direction for CUT_COOL s. Without this a download whose delay sits upstream slides 15 % a tick to the
    floor (10-31: fq_codel at 120M of ~178 still added 58-80 ms) - throughput lost for no latency gain."""
    for k in ("up", "down"):
        s = st.setdefault("streak", {}).setdefault(k, {"n": 0})
        cut = bool(limit[k]) and bool(new[k]) and new[k] < limit[k]
        if cut and now < s.get("cool", 0): new[k] = limit[k]; continue             # cooling down: this direction is not cut
        if not cut: s["n"] = 0; continue
        if s["n"] == 0: s.update(d0=delay, lim0=limit[k])
        s["n"] += 1
        if s["n"] > CUT_PROOF and delay is not None and s.get("d0") is not None and delay > 0.8 * s["d0"]:
            new[k] = s["lim0"]; s.update(n=0, cool=now + CUT_COOL); st["useless"] = st.get("useless", 0) + 1
            want_test("%s: %d cuts did not lower the latency (+%g ms) - the line may have lost capacity (rain, a busy cell)"
                      % (k, CUT_PROOF + 1, delay), now)     # a fresh lower result becomes the ceiling for 3 h (captest.ceilings)
    return new


def minute():
    """The shaper over the last minute, for the sample (monitor.finish): limits now (Mbit/s, 0 = unlimited), the lowest limit,
    the highest delay above idle, the highest rates the router saw, the busiest CPU core; then reset. {} while SQM is off."""
    a, st["acc"] = st.get("acc"), {}
    if not a or not st.get("limit"): return {}
    r = lambda v: "" if v is None else round(v, 1)
    return {"sqm_up": r(st["limit"]["up"]), "sqm_down": r(st["limit"]["down"]), "sqm_up_min": r(a.get("up_min")),
            "sqm_down_min": r(a.get("down_min")), "sqm_delay": r(a.get("delay")), "sqm_rx": r(a.get("rx")), "sqm_tx": r(a.get("tx")),
            "sqm_cpu": a.get("cpu", ""), "sqm_useless": st.get("useless", 0)}


def _acc(new, rate, d, cpu):
    a = st.setdefault("acc", {}); mx = lambda k, v: a.__setitem__(k, v if a.get(k) is None or (v is not None and v > a[k]) else a[k])
    for k in ("up", "down"):
        if new[k]: a[k + "_min"] = min(a.get(k + "_min") or new[k], new[k])
    mx("delay", d); mx("rx", rate["down"]); mx("tx", rate["up"]); mx("cpu", max(cpu) if cpu else None)


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
    d = delay_now(lat_buf, now, idle, rebase)
    learned = {k: CEILING[0](k, now) for k in MAX}; was = st.get("learned") or learned; st["learned"] = learned
    caps = {k: climb(k, learned[k], st["limit"], rate, d, TICK, now) for k in MAX}
    new = earn_keep(st["limit"], step(st["limit"], rate, d, caps), d, now)
    for k in caps: deep_cut(k, st["limit"][k], new[k], caps[k], now)
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
    pt = time.process_time(); own = 100 * (pt - st["pt"][0]) / (now - st["pt"][1]) if st.get("pt") and now > st["pt"][1] else None
    st["pt"] = (pt, now); cpu_spy(cpu, now, own)          # own = the keeper's CPU (all its threads) since the last tick
    _acc(new, rate, d, cpu)
    move = any(new[k] != st["limit"][k] and (not new[k] or not st["limit"][k] or abs(new[k] - st["limit"][k]) >= 0.02 * st["limit"][k]) for k in new)
    shown = new if move else st["limit"]                  # < 2 % is not applied: say why the limit in force is what it is
    st["last"] = {"time": time.strftime("%H:%M:%S"), "rate": rate, "ceiling": caps,
                  "why": {k: reason(st["limit"][k], shown[k], rate[k], d, caps[k], was[k] if was[k] != learned[k] else None) for k in new}, "delay_ms": None if d is None else round(d, 1), "limit": new,
                  "cpu": cpu, "cpu_max_since_start": st.get("cpu_max"), "cpu_spikes": (st.get("spikes") or [])[-5:], "useless_cut_streaks": st.get("useless", 0)}
    if move:
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
            n = (st.get("spikes") or [None])[-1]       # the newest spike before (a capped list keeps its length)
            ch = tick(lat_buf, path_change=(path_change() if path_change else 0.0)); st["error"] = None
            if (st.get("spikes") or [None])[-1] != n: log("router CPU spike: %s" % st["spikes"][-1])
            if ch and st["changes"] % 30 == 1: log("SQM limit now %s/%s Mbit/s up/down (delay %s ms)" % (ch["up"], ch["down"], st["last"]["delay_ms"]))
        except Exception as e:
            if st["error"] != str(e): log("SQM: %s - retry in %d s" % (e, ERROR_WAIT_S))
            st["error"] = str(e); st["id"] = None
            time.sleep(ERROR_WAIT_S)


def start(lat_buf, log, path_change=None):
    """Start the SQM thread when SQM_URL is set. path_change() -> epoch of the last TR-069 cell/RAT/boot change."""
    st["t0"] = time.time()                              # cpu_spy skips the keeper's own start
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
    CEILING[0] = lambda d, now: {"down": 150.0, "up": 30.0}[d]; st.pop("learned", None); tick([], now)   # idle, parked at the ceilings
    CEILING[0] = lambda d, now: {"down": 143.5, "up": 30.0}[d]; tick([], now)                            # next period: lower ceiling
    assert st["last"]["why"]["down"] == "new period ceiling 143.5 (was 150)", st["last"]["why"]
    assert st["last"]["why"]["up"] == "idle, limit parked at the ceiling 30 (no latency data)", st["last"]["why"]
    CEILING[0] = lambda d, now: {"down": 141.0, "up": 30.0}[d]; tick([], now)                            # -1.7 %: not applied
    assert st["limit"]["down"] == 143.5 and st["last"]["why"]["down"] == "idle, limit parked at the ceiling 141 (no latency data)"
    CEILING[0] = lambda d, now: MAX[d]
    # a cut must lower the delay: 3 cuts with the delay still high -> back to the limit before the streak + 10 min cool-down
    st.update(streak={}, useless=0, want_test=None); t = 5000.0; lim = {"up": 26.0, "down": 150.0}
    for i in range(3):
        n = earn_keep(lim, step(lim, {"up": 1, "down": lim["down"]}, 60), 60, t + i * 10)
        assert n["down"] < lim["down"], (i, n); lim = n
    n = earn_keep(lim, step(lim, {"up": 1, "down": lim["down"]}, 58), 58, t + 30)
    assert n["down"] == 150.0 and st["useless"] == 1, n                                  # 4th cut, delay 58 vs 60: useless -> back
    assert "did not lower the latency" in (st["want_test"] or {}).get("why", ""), st["want_test"]   # rain / a busy cell: measure the line (2026-11-18)
    assert earn_keep(n, step(n, {"up": 1, "down": 150}, 60), 60, t + 40)["down"] == 150.0   # cooling: no cut
    assert earn_keep(n, step(n, {"up": 1, "down": 150}, 60), 60, t + 700)["down"] == 127.5  # cool-down over: cuts again
    st.update(streak={}); lim = {"up": 26.0, "down": 150.0}
    for i, dl in enumerate((60, 40, 25)):                                                # cuts that work (60 -> 40 -> 25 -> 10)
        lim = earn_keep(lim, step(lim, {"up": 1, "down": lim["down"]}, dl), dl, t + i * 10)
    n = earn_keep(lim, step(lim, {"up": 1, "down": lim["down"]}, 16), 16, t + 30)
    assert n["down"] < lim["down"] and st["useless"] == 1, n                             # 16 < 0.8 * 60: kept cutting
    st.update(acc={}, limit={"up": 26.0, "down": 150.0})
    _acc({"up": 26.0, "down": 140.0}, {"up": 2.0, "down": 130.0}, 12.0, [10, 40]); _acc({"up": 25.0, "down": 150.0}, {"up": 3.0, "down": 90.0}, 30.0, [5])
    mm = minute(); assert mm["sqm_down_min"] == 140.0 and mm["sqm_up_min"] == 25.0 and mm["sqm_delay"] == 30.0 and mm["sqm_rx"] == 130.0 \
        and mm["sqm_tx"] == 3.0 and mm["sqm_cpu"] == 40 and mm["sqm_down"] == 150.0, mm
    assert minute() == {}                                                                # reset after reading
    # climb: busy at the ceiling with a clean delay for CLIMB_S -> +5 %; a delay -> back to the learned value; past +10 % -> want a test
    st.update(climb={}, climbs=[], want_test=None); L = 150.0; t = 9000.0
    assert climb("down", L, {"down": 150.0}, {"down": 140.0}, 2, 10, t) == 150.0                    # clean, but not long enough yet
    for i in range(int(CLIMB_S / 10)): top = climb("down", L, {"down": 150.0}, {"down": 140.0}, 2, 10, t)
    assert top == 157.5 and st["climbs"][-1]["what"].startswith("150 -> 157.5"), (top, st["climbs"])
    for x in (1.05 ** 2, 1.05 ** 3):                                                            # keeps climbing while the shaper follows
        for i in range(int(CLIMB_S / 10)): top = climb("down", L, {"down": top}, {"down": top * 0.9}, 2, 10, t)
    assert top == 173.6 and st["want_test"]["why"] == "down climbed to 165.4 (+10 % over the learned 150)", (top, st["want_test"])   # asked once, at > +10 %
    assert climb("down", L, {"down": 170.0}, {"down": 160.0}, 2, 10, t) == 173.6                  # not at the ceiling: no clean time
    assert climb("down", L, {"down": 170.0}, {"down": 160.0}, 40, 10, t) == 150.0 and "back to the learned 150" in st["climbs"][-1]["what"]
    assert climb("down", 120.0, {"down": 120.0}, {"down": 110.0}, 2, 10, t) == 120.0 and st["climb"]["down"]["x"] == 1.0   # new period
    st["climb"]["up"] = {"base": 29.0, "x": 1.1, "clean": 0.0, "asked": True}
    assert climb("up", 29.0, {"up": 30.0}, {"up": 29.0}, 2, 10, t) == 30.0                        # never above the plan
    assert climb("down", 120.0, {"down": 0}, {"down": 180.0}, 2, 10, t) == 120.0                  # unshaped: nothing learned
    # deep cuts: 3 cuts below 70 % of the ceiling within an hour -> want a test
    st.update(deep={}, want_test=None)
    for i in range(2): deep_cut("down", 100.0, 85.0, 150.0, t + i * 60)
    assert st["want_test"] is None; deep_cut("down", 130.0, 110.0, 150.0, t + 300); assert st["want_test"] is None   # 110 >= 70 % of 150: not deep
    deep_cut("down", 100.0, 85.0, 150.0, t + 400); assert "cut below 70 % of the ceiling 150 3 times" in st["want_test"]["why"]
    st.update(deep={}, want_test=None); deep_cut("down", 100.0, 85.0, 150.0, t); deep_cut("down", 100.0, 85.0, 150.0, t + 1800)
    deep_cut("down", 100.0, 85.0, 150.0, t + 4000); assert st["want_test"] is None                 # spread over > 1 h: no
    assert reason(150.0, 127.5, 140.0, 40, 150.0).startswith("cut 150 -> 127.5: busy (93 %") and reason(0, 0, 9, 1, 150.0).startswith("unshaped")
    assert reason(100.0, 150.0, 1.0, 2, 150.0).startswith("idle and calm") and reason(140.0, 147.0, 130.0, 2, 150.0) == "busy (93 %) and clean (latency +2 ms): +5 % -> 147"
    assert reason(150.0, 150.0, 140.0, 2, 150.0).startswith("at the ceiling 150 (93 %")
    assert reason(146.7, 0, 0.1, 2, 146.7).startswith("unshaped")                                     # 00:00: off, not a cut
    assert reason(150.0, 150.0, 0.4, 2, 150.0) == "idle, limit parked at the ceiling 150 (latency +2 ms)"
    assert reason(150.0, 143.5, 1.0, 2, 143.5, 150.0) == "new period ceiling 143.5 (was 150)"
    assert reason(143.5, 153.6, 1.0, 2, 153.6, 143.5) == "new period ceiling 153.6 (was 143.5)"
    assert reason(150.0, 127.5, 140.0, 40, 143.5, 150.0).startswith("cut 150 -> 127.5")              # a real cut stays a cut
    # a core >= 90 %: which process? /tool/profile for 2 s -> the top 3 (idle / total skipped), at most every 10 min
    rest = lambda method, path, body=None: (calls.append((method, path, body)) or [{"name": "idle", "usage": "50"}, {"name": "networking", "usage": "30.5"},
        {"name": "container", "usage": "12"}, {"name": "networking", "usage": "35"}, {"name": "total", "usage": "50"}, {"name": "ethernet", "usage": "1"}])
    calls.clear(); st.pop("spy_t", None); st["spikes"] = []; st["t0"] = 1e6 - 60
    assert cpu_spy([99], 1e6) is None and calls == []                          # the keeper's own start (2 min): its load, not news
    st["t0"] = 0
    assert cpu_spy([3, 95], 1e6) == "networking 35 %, container 12 %, ethernet 1 %" and calls == [("POST", "/tool/profile", {"duration": "2s"})]
    assert cpu_spy([3, 95], 1e6 + 60) is None and cpu_spy([50, 60], 1e6 + 900) is None and len(calls) == 1   # 10-min gap; no spike: no call
    rest = lambda method, path, body=None: (_ for _ in ()).throw(RuntimeError("not allowed"))
    assert cpu_spy([99], 1e6 + 1200) is None and st["spikes"][-1].startswith("profile failed: RuntimeError")
    # an answer with nothing to rank says what it was (2026-11-17 17:19: "no profile data" - the REST format was never seen)
    rest = lambda method, path, body=None: [{"name": "idle", "usage": "99"}]
    st["spy_t"] = 0; assert cpu_spy([99], 1e6 + 1290, own=87.4) and st["spikes"][-1].endswith("; keeper 87 %"), st["spikes"][-1:]  # its share
    st["spy_t"] = 1e6 + 1200
    assert cpu_spy([99], 1e6 + 1300 + 600) == "no profile data (raw: [{\"name\": \"idle\", \"usage\": \"99\"}])", st["spikes"][-1:]
    rest = lambda method, path, body=None: {"name": "networking", "usage": "40"}                     # one object, not a list
    st["spy_t"] = 0; assert cpu_spy([99], 1e6 + 1950) == "no profile data (raw: {\"name\": \"networking\", \"usage\": \"40\"})", st["spikes"][-1:]
    st["spy_t"] = 1e6 + 1200
    # the router user may not run /tool/profile (2026-11-17 16:46, HTTP 500 "not enough permissions (9)"): off until restart, said once
    PERM = 'POST /tool/profile -> HTTP 500 {"detail":"not enough permissions (9)","error":500,"message":"Internal Server Error"}'
    calls.clear(); rest = lambda method, path, body=None: (calls.append(path) or (_ for _ in ()).throw(RuntimeError(PERM)))
    assert cpu_spy([99], 1e6 + 1900) is None and "no permission" in st["spikes"][-1] and calls == ["/tool/profile"], st["spikes"][-1:]
    n = len(st["spikes"]); assert cpu_spy([99], 1e6 + 9e5) is None and calls == ["/tool/profile"] and len(st["spikes"]) == n   # no call, no log
    print("OK sqm")
