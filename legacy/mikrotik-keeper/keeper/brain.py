"""Brain (device neutral): call-quality probe, 48 h history, cell registry, analysis, findings, auto reboot, read-only JSON API.

Goal: a connection good enough for Teams calls / screen sharing at any time - low latency, low jitter, no loss - then 5G on a
good cell and a strong LTE anchor, with as few reboots as possible. No speed tests: the probe runs all the time, also during
real calls, so it measures exactly what a call sees (about 30 TCP connects a minute, a few KB).
Never touches the network mode (Yettel = NSA only), never locks anything on its own - locks stay a human decision
(it recommends them). Its only own action is a TR-069 reboot to re-pick the 5G cell, with hard limits.

Env: AUTO (off | dryrun | on, default dryrun), MIN_SINR (3), BAD_MIN (15), NO5G_MIN (10), NOSVC_MIN (5), PREFERRED_NR (e.g. 636576/534),
     MAX_REBOOTS_DAY (3), REBOOT_GAP_H (2), IDLE_DOWN_BYTES (250000), IDLE_UP_BYTES (62500), BUSY_UP_BYTES (125000) - bytes/s,
     LAT_TARGETS (1.1.1.1:443,8.8.8.8:443; off = no probe), CALL_P95_MS (150), CALL_JITTER_MS (30), CALL_LOSS_PCT (3.4 = 1 of 30 probes),
     INTTEST_URL (https://speed.cloudflare.com/__down?bytes=2000000; off = no download), INTTEST_MB (2) - the lock test's load generator,
     ONLINE_STABLE (5) - consecutive successful probes before a lock-test step measures,
     WAKE_HOST (1.1.1.1), WAKE_SIZE (1000) - the NSA wake traffic when the target 5G cell is not up
"""
import collections, datetime, json, math, os, re, socket, statistics, subprocess, threading, time, urllib.parse, urllib.request

from keeper import common, store

E = os.environ.get
AUTO = E("AUTO", "dryrun")
MIN_SINR, BAD_MIN, NO5G_MIN = float(E("MIN_SINR", "3")), int(E("BAD_MIN", "15")), int(E("NO5G_MIN", "10"))
NOSVC_MIN = int(E("NOSVC_MIN", "5"))      # no mobile service at all this long -> reboot (a locked cell is released by the lock guard after 3)
PREFERRED = [c for c in E("PREFERRED_NR", "").split(",") if c]
MAX_REBOOTS_DAY, REBOOT_GAP = int(E("MAX_REBOOTS_DAY", "3")), float(E("REBOOT_GAP_H", "2")) * 3600
# the ZTE's rate counters (down_bps / up_bps columns) are BYTES per second: the busiest minute read 25,024,504 = 200 Mbit/s, the plan cap.
# idle: < 2 Mbit/s down and < 0.5 Mbit/s up; busy: >= 1 Mbit/s up (a Teams video call / screen share sends ~1-2.5 Mbit/s)
IDLE_DOWN, IDLE_UP, BUSY_UP = float(E("IDLE_DOWN_BYTES", "250000")), float(E("IDLE_UP_BYTES", "62500")), float(E("BUSY_UP_BYTES", "125000"))
TARGETS = [(h, int(p)) for h, p in (t.rsplit(":", 1) for t in E("LAT_TARGETS", "1.1.1.1:443,8.8.8.8:443").split(",") if ":" in t)]
# LAT_TARGETS=off: no latency probe - for a device that is NOT this router's uplink (e.g. the ATL under test while the ZTE carries
# the traffic): the probe measures the uplink, so its numbers would be the ZTE's
# Teams network targets (Microsoft): round trip < 100 ms, jitter < 30 ms, loss < 1% - per minute with ~30 probes a bit of slack
CALL_P95, CALL_JITTER, CALL_LOSS = float(E("CALL_P95_MS", "150")), float(E("CALL_JITTER_MS", "30")), float(E("CALL_LOSS_PCT", "3.4"))
LAT_COLS = ["lat_p50", "lat_p95", "lat_max", "jitter_ms", "loss_pct", "call_ok"]
INTTEST_URL, INTTEST_MB = E("INTTEST_URL", "https://speed.cloudflare.com/__down?bytes=2000000"), float(E("INTTEST_MB", "2"))
ONLINE_STABLE = int(E("ONLINE_STABLE", "5"))
HIST = collections.deque(maxlen=48 * 60 + 60)          # ~48 h at one sample a minute
ACTIONS = collections.deque(maxlen=50)                  # (epoch, what, reason, mode)
lat_buf = collections.deque(maxlen=400)                 # (epoch, ms or None)
ctx = {"pending": None, "last_hint": {}, "reboot_from": None}


# ---------- helpers ----------
def f(v):
    """float or None."""
    try: return float(v)
    except (TypeError, ValueError): return None


def pct(xs, p):
    """p-th percentile of the non-None values, rounded to 0.1; None when empty."""
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1)


def med(xs):
    """Median (see `pct`)."""
    return pct(xs, 50)


def ts(row):
    """Epoch seconds of a row's `time`."""
    return datetime.datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").timestamp()


def band_of(earfcn):
    """LTE downlink EARFCN -> band (the bands Yettel BG uses)"""
    e = f(earfcn)
    if e is None: return "?"
    for lo, hi, b in ((0, 599, "B1"), (1200, 1949, "B3"), (2750, 3449, "B7"), (3450, 3799, "B8"), (6150, 6449, "B20"), (9210, 9659, "B28")):
        if lo <= e <= hi: return b
    return "?"


def pct_txt(v):
    """`12.5%` or 'no data'."""
    return "no data" if v is None else "%s%%" % v


def share(rows, key="call_ok"):
    """Share in % of rows where `key` is 1 (default: call-ready minutes); None without data."""
    xs = [r.get(key) for r in rows if r.get(key) in ("1", "0", 1, 0)]
    return round(100 * sum(1 for x in xs if str(x) == "1") / len(xs), 1) if xs else None


# LTE bands: (MHz, Yettel's usual bandwidth). Low bands reach further and indoors -> more users share them; they also read
# ~10 dB higher RSRP than 2600 MHz at the same distance, so RSRP is only compared within one band.
BANDS = {"1": (2100, 20), "3": (1800, 20), "7": (2600, 20), "8": (900, 5), "20": (800, 10), "28": (700, 10)}


def parse_ca(s):
    """'B7/3350/340:-91/1@20 B3/1550/340:-90/8@20' -> [(band, earfcn, pci, rsrp, sinr, bw_mhz), ...]; first = anchor (PCC)
    (rows written before the bandwidth was logged have no '@bw': the band's usual bandwidth is used)"""
    return [(m.group(1), m.group(2), m.group(3), f(m.group(4)), f(m.group(5)),
             f(m.group(6)) or BANDS.get(m.group(1), (0, 0))[1] or None)
            for m in re.finditer(r"B(\w+)/(\d+)/(\d+):(-?[\d.]+)/(-?[\d.]+)(?:@([\d.]+))?", s or "")]


def idle(r):
    """The link is idle in this sample (rates under IDLE_DOWN_BYTES / IDLE_UP_BYTES)."""
    return (f(r.get("down_bps")) or 0) <= IDLE_DOWN and (f(r.get("up_bps")) or 0) <= IDLE_UP


def no5g_busy(r):
    """5G missing while there is traffic. In NSA the network adds the 5G leg only when data flows and releases it when the link
    is quiet, so 'no5G' on an idle minute is normal, not a fault (measured 2026-10-27: traffic brings it back)"""
    return r.get("nr_cell") == "no5G" and not idle(r)


def capacity(bw, sinr):
    """estimated Mbit/s per MIMO layer, before load: bandwidth x 3GPP truncated Shannon (TR 36.942 Annex A, LTE downlink):
    0.6 x log2(1 + SINR) bit/s/Hz, 0 below -10 dB SINR, at most 4.4 bit/s/Hz"""
    if bw is None or sinr is None or sinr < -10: return None if bw is None or sinr is None else 0.0
    return round(bw * min(4.4, 0.6 * math.log2(1 + 10 ** (sinr / 10))), 1)


NR_BW = {"636576": 100, "629988": 20}        # MHz, Yettel n78 blocks 3500-3600 / 3440-3460 MHz (CRC allocation); n28 (700 MHz) = 10
NR_DL_SHARE = float(E("NR_DL_SHARE", "0.74"))   # n78 is TDD: share of the time used for download (assumed DDDSU frame)


def capacity_now(row):
    """reference only: rough download ceiling from the current signal - per carrier Mbit/s per MIMO layer (TR 36.942, before load)
    and a total assuming 2 layers on LTE, 4 on n78. Upload is not estimated: the ZTE reports no uplink SINR."""
    cars = [{"carrier": "B%s %s/%s" % (b, ch, pci), "bw_mhz": bw, "sinr": sinr, "mbps_layer": capacity(bw, sinr), "layers": 2}
            for b, ch, pci, rsrp, sinr, bw in parse_ca(row.get("lte_ca"))]
    nr = [] if row.get("nr_cell") in (None, "", "no5G") else [(row["nr_cell"], f(row.get("nr_sinr")), None)]
    nr += [("%s/%s" % m.group(1, 2), f(m.group(4)), m.group(5)) for m in re.finditer(r"(\d+)/(\d+):(-?[\d.]*)/(-?[\d.]*)@?([\d.]*)", row.get("nr_ca") or "")]
    for cell, sinr, bw in nr:
        arfcn = cell.split("/")[0]
        bw = f(bw) or NR_BW.get(arfcn) or (10 if arfcn.isdigit() and 151600 <= int(arfcn) <= 160600 else None)
        c = capacity(bw, sinr); tdd = arfcn.isdigit() and int(arfcn) >= 620000
        cars.append({"carrier": "n " + cell, "bw_mhz": bw, "sinr": sinr, "mbps_layer": round(c * NR_DL_SHARE, 1) if c and tdd else c, "layers": 4 if tdd else 2})
    return {"carriers": cars, "est_down_mbps": round(sum(c["mbps_layer"] * c["layers"] for c in cars if c["mbps_layer"])),
            "note": "estimate before load: TR 36.942 x bandwidth x layers (assumed 2 LTE / 4 n78); upload not estimated (no uplink SINR)"}


# ---------- call-quality probe: TCP connect time (= one round trip, no raw-socket rights needed) ----------
def probe_loop():
    """Thread: TCP connect to LAT_TARGETS every ~2 s, alternating -> `lat_buf` (epoch, ms or None on loss, target)."""
    i = 0
    while True:
        host, port = TARGETS[i % len(TARGETS)]; i += 1
        t0 = time.time()
        try:
            s = socket.create_connection((host, port), timeout=2); ms = (time.time() - t0) * 1000; s.close()
        except OSError:
            ms = None
        lat_buf.append((time.time(), ms, host))
        time.sleep(max(0.2, 2 - (time.time() - t0)))   # ~30 probes a minute


def latency_row(window=60):
    """Latency fields of the last `window` s: p50 / p95 / max, jitter, loss %, call_ok (1 = within the Teams limits)."""
    now = time.time()
    pts = [x for x in list(lat_buf) if now - x[0] <= window]
    xs = [x[1] for x in pts]
    if not xs: return {c: "" for c in LAT_COLS}
    ok = [x for x in xs if x is not None]
    per = {}                                   # jitter per target: 1.1.1.1 and 8.8.8.8 sit at different distances, so alternating
    for x in pts:                              # between them is not jitter (before 2026-10-28s the offset was counted as jitter)
        if x[1] is not None: per.setdefault(x[2] if len(x) > 2 else "", []).append(x[1])
    diffs = [abs(a - b) for v in per.values() for a, b in zip(v, v[1:])]
    jitter = round(statistics.mean(diffs), 1) if diffs else ""
    p95, loss = pct(ok, 95), round(100 * (len(xs) - len(ok)) / len(xs), 1)
    good = p95 is not None and p95 <= CALL_P95 and jitter != "" and jitter <= CALL_JITTER and loss <= CALL_LOSS
    return {"lat_p50": pct(ok, 50) or "", "lat_p95": p95 or "", "lat_max": round(max(ok), 1) if ok else "",
            "jitter_ms": jitter, "loss_pct": loss, "call_ok": 1 if good else 0}


def internet_test(seconds=30, url=None, mb=None, stop=None):
    """A short download (`INTTEST_MB` from `INTTEST_URL`, "off" = none) while the latency probe keeps running, for `seconds`.
    The download is a load generator, not a speed test: it raises the NSA 5G leg, so the probe measures 5G and not only LTE; the
    rate is context only. Returns the probe's fields (lat_p50, lat_p95, jitter_ms, loss_pct, call_ok) plus down_mbps / tested_mb /
    error. A failed download leaves the probe values in place."""
    url = INTTEST_URL if url is None else url
    mb = INTTEST_MB if mb is None else mb
    out = {"down_mbps": None, "tested_mb": 0.0, "error": ""}
    stop = stop or threading.Event()
    if url and url != "off" and mb > 0:
        def pull():
            t0 = time.time(); n = 0
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "zte-keeper"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    while not stop.is_set() and n < mb * 1e6:
                        b = r.read(int(min(65536, mb * 1e6 - n)))
                        if not b: break
                        n += len(b)
                out["tested_mb"] = round(n / 1e6, 2)
                if n: out["down_mbps"] = round(n * 8 / 1e6 / max(0.001, time.time() - t0), 2)
            except Exception as e:
                out["error"] = repr(e)
        threading.Thread(target=pull, daemon=True).start()
    time.sleep(seconds)
    stop.set()
    out.update(latency_row(seconds + 2))
    return out


def wake(seconds=60, host=None, size=None, stop=None):
    """Continuous small traffic for `seconds` to raise the NSA 5G leg when the target cell is not up: ping `host` with `size`-byte
    packets (`WAKE_HOST` 1.1.1.1, `WAKE_SIZE` 1000); `stop` (threading.Event) ends it early. If ping is not usable in the container
    it falls back to back-to-back small downloads. Returns True when traffic ran."""
    host, size = host or E("WAKE_HOST", "1.1.1.1"), int(size or E("WAKE_SIZE", "1000"))
    stop = stop or threading.Event()
    if seconds <= 0: return False
    t0 = time.time(); p = None
    try:
        p = subprocess.Popen(["ping", "-s", str(size), "-i", "1", "-c", str(max(1, int(seconds))), host],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        p = None
    if p is not None:                                  # ping: 1000-byte packets for the whole window (stops when the cell is up)
        while time.time() - t0 < seconds + 5 and p.poll() is None and not stop.is_set(): time.sleep(0.5)
        ran = time.time() - t0
        try: p.terminate()
        except Exception: pass
        if ran >= 1: return True                       # traffic went out even if ICMP is filtered (exit code 1)
    # fallback (no ping / no permission): small downloads back to back, ~16 KB each
    url = INTTEST_URL
    if not url or url == "off": return False
    while time.time() - t0 < seconds and not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=5) as r: r.read(16384)
        except Exception:
            time.sleep(1)
        time.sleep(0.2)
    return True


def wait_online(timeout=180, stable=None):
    """Wait until the last `stable` (ONLINE_STABLE, 5) latency probes all succeeded - the link is back after a reboot - up to
    `timeout` s. True when it is (also immediately with LAT_TARGETS=off: no probe). Checks that the newest probe is recent."""
    if not TARGETS: return True
    stable = stable or ONLINE_STABLE
    t0 = time.time()
    while time.time() - t0 < timeout:
        recent = list(lat_buf)[-stable:]
        if len(recent) >= stable and all(x[1] is not None for x in recent) and time.time() - recent[-1][0] <= 30: return True
        time.sleep(1)
    return False


def quality_values(call_ready=None, loss=None, lat=None, jitter=None, sinr=None, rsrp=None):
    """The keeper's decision tuple, highest wins: internet quality first - call-ready %, loss %, latency p95, jitter - and signal
    (5G SINR, RSRP) only as a tie-breaker. Missing values count as the worst."""
    n = lambda v, worst: worst if v is None else v
    return (n(f(call_ready), -1), -n(f(loss), 999), -n(f(lat), 9999), -n(f(jitter), 9999), n(f(sinr), -99), n(f(rsrp), -999))


def clearly_better(a, b):
    """Does quality tuple `a` clearly beat `b`? The first metric that differs beyond its margin decides (call-ready +5 pp,
    loss -1 pp, latency p95 -10 ms, jitter -10 ms), so a noisy short window does not flip a mode or a cell; signal (SINR +1 dB,
    then RSRP +2) only breaks a tie. a, b = quality_values(...)."""
    (ac, al, ap, aj, asr, arp), (bc, bl, bp, bj, bsr, brp) = a, b
    if abs(ac - bc) >= 5: return ac > bc
    if abs(al - bl) >= 1.0: return al > bl
    if abs(ap - bp) >= 10: return ap > bp
    if abs(aj - bj) >= 10: return aj > bj
    return asr - bsr >= 1 or (asr == bsr and arp - brp >= 2)


# ---------- history ----------
def load(data_dir):
    """fill HIST with the last 48 h from the store so analysis survives restarts"""
    cutoff = time.time() - 48 * 3600
    rows = [r for r in store.rows(data_dir, "samples", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cutoff)))
            if r.get("nr_cell") and r.get("time") and "255" not in r["nr_cell"].split("/")]       # 255 = boot placeholder (old files)
    for r in rows:
        try:
            if ts(r) >= cutoff: HIST.append(r)
        except ValueError:
            pass


def add(row, live=True):
    """Append a sample to the 48 h history; live=True also updates the cell registry."""
    HIST.append(dict(row))
    if live: track(row)


def window(hours):
    """Samples of the last `hours` from the in-memory history."""
    cutoff = time.time() - hours * 3600
    return [r for r in list(HIST) if ts(r) >= cutoff]


# ---------- decisions ----------
def decide(row, events_log, now=None):
    """after each sample: maybe queue a reboot (AUTO=on) or note what it would do (dryrun). Returns the reason or None."""
    now = now or time.time()
    up = f(row.get("uptime_s")) or 0
    if AUTO == "off" or up < 600 or ctx["pending"] or ctx.get("hold"):
        return None
    recent = list(HIST)[-max(BAD_MIN, NO5G_MIN):]
    reason = None
    last = recent[-NOSVC_MIN:] if len(recent) >= NOSVC_MIN else []
    if last and all(not r.get("lte_cell") for r in last):                  # 2026-10-28 01:03: 10 min without service, a reboot fixed it
        reason = "no mobile service for %d min" % NOSVC_MIN
    last = [r for r in list(HIST)[-60:] if not idle(r)][-NO5G_MIN:]          # only minutes with traffic: idle = 5G released on purpose
    if not reason and len(last) >= NO5G_MIN and all(r["nr_cell"] == "no5G" for r in last):
        reason = "no 5G during %d min with traffic" % NO5G_MIN
    last = recent[-BAD_MIN:]
    if not reason and len(last) >= BAD_MIN and all(r["nr_cell"] != "no5G" for r in last):
        sinrs = [f(r.get("nr_sinr")) for r in last]
        if all(s is not None and s < MIN_SINR for s in sinrs):
            reason = "5G %s SINR < %g for %d min (median %s)" % (row["nr_cell"], MIN_SINR, BAD_MIN, med(sinrs))
        elif PREFERRED and all(r["nr_cell"] not in PREFERRED for r in last):
            reason = "5G on %s, not a preferred cell (%s) for %d min" % (row["nr_cell"], ",".join(PREFERRED), BAD_MIN)
    if not reason:
        return None
    done = [a for a in list(ACTIONS) if a[3] == "on"]
    blocked = None
    if done and now - done[-1][0] < REBOOT_GAP:
        blocked = "last reboot %d min ago" % ((now - done[-1][0]) / 60)
    elif len([a for a in done if now - a[0] < 86400]) >= MAX_REBOOTS_DAY:
        blocked = "%d reboots in 24 h already" % MAX_REBOOTS_DAY
    elif (f(row.get("down_bps")) or 0) > IDLE_DOWN or (f(row.get("up_bps")) or 0) > IDLE_UP:
        blocked = "link busy (%.1f/%.1f Mbit/s down/up) - maybe a call" % ((f(row.get("down_bps")) or 0) * 8e-6, (f(row.get("up_bps")) or 0) * 8e-6)
    key = reason.split(" for ")[0] + (blocked or "")
    if now - ctx["last_hint"].get(key, 0) < 1800:          # same note at most every 30 min
        return None
    ctx["last_hint"][key] = now
    if blocked:
        events_log({"event": "reboot held", "to": "%s - %s" % (reason, blocked)})
        return None
    if AUTO == "on":
        ctx["pending"] = "reboot"; ctx["reboot_from"] = row["nr_cell"]
        ACTIONS.append((now, "reboot", reason, "on"))
        events_log({"event": "reboot queued", "to": reason})
    else:
        ACTIONS.append((now, "reboot", reason, "dryrun"))
        events_log({"event": "would reboot (dryrun)", "to": reason})
    return reason


def explain_switch(row):
    """plain-language cause of a 5G cell change, from the samples before it (call before add(row))"""
    p = HIST[-1] if HIST else {}
    old, new = p.get("nr_cell", "?"), row.get("nr_cell", "?")
    up = f(row.get("uptime_s")) or 0
    anchor = parse_ca(p.get("lte_ca"))
    a_sinr = anchor[0][4] if anchor else f(p.get("lte_sinr"))
    if new == "no5G":
        cause = "5G leg released (LTE only)" + (" - link idle: normal in NSA, added again on traffic" if idle(row) else "")
    elif up < 300:
        cause = "after a ZTE boot"
    elif old == "no5G":
        cause = "5G leg re-added after a drop"
    elif p.get("lte_cell") and p.get("lte_cell") != row.get("lte_cell"):
        cause = "LTE anchor changed %s -> %s, 5G re-added on the new anchor" % (p.get("lte_cell"), row.get("lte_cell"))
    elif (p.get("rrc") or "").lower() not in ("", "connected"):
        cause = "after an idle period (RRC %s), 5G re-added on resume" % p.get("rrc")
    elif a_sinr is not None and a_sinr <= 0:          # ponytail: 1-min snapshots - a cause guess, not seen; 0 dB = anchor at its limit
        cause = "LTE anchor weak (SINR %s) just before - 5G leg likely released and re-added" % a_sinr
    else:
        cause = "network handover while connected (anchor SINR %s - a 5G cell change the network decided, by RSRP)" % a_sinr
    parts = [cause]
    o_s, n_s, o_r, n_r = f(p.get("nr_sinr")), f(row.get("nr_sinr")), f(p.get("nr_rsrp")), f(row.get("nr_rsrp"))
    if None not in (o_s, n_s, o_r, n_r) and new != "no5G":
        parts.append("SINR %s -> %s, RSRP %s -> %s" % (o_s, n_s, o_r, n_r))
        if n_s <= o_s - 3 and abs(n_r - o_r) <= 3:
            parts.append("network picks by RSRP (equal here), not by interference - the new cell is worse; an NR lock to %s prevents this"
                         % old.split("/")[-1])
    return "; ".join(parts)


def after_boot_cell(row, events_log):
    """first 5G cell after a keeper reboot -> learn where reboots land"""
    if ctx["reboot_from"] and row["nr_cell"] != "no5G":
        events_log({"event": "reboot result", "from": ctx["reboot_from"], "to": row["nr_cell"], "nr_sinr_before": row.get("nr_sinr", "")})
        ctx["reboot_from"] = None


# ---------- cell registry: every cell the ZTE sees, over weeks (cells.json) - our own map, from our own antenna position ----------
REG = {"lte": {}, "nr": {}, "ids": {}, "since": None}   # since = when watching started (cells seen then are the baseline, not "new")
# ids: "lte:EARFCN:PCI" -> ECI (eNB * 256 + sector), "nr:ARFCN:PCI" -> NCI - learned from TR-069 CELLID (LTE) and pushed by the app (LTE + 5G)
reg_st = {"dir": None, "n": 0, "sites": {}}
EW = 0.05                                    # EWMA weight per sample (~20 samples memory) for RSRP / SINR


def _ew(old, new):
    return new if old is None else (old if new is None else round(old + EW * (new - old), 2))


def track(row, now=None):
    """update the registry with one sample (neighbour list = RSRP of every LTE cell the ZTE measures; CA = SINR of used ones)"""
    now = now or time.time()
    if REG.get("since") is None: REG["since"] = now
    site = re.fullmatch(r"(\d+)/(\d+)", row.get("lte_site") or ""); cell = re.fullmatch(r"(\d+)/(\d+)", row.get("lte_cell") or "")
    if site and cell: REG.setdefault("ids", {})["lte:%s:%s" % cell.groups()] = int(site.group(1)) * 256 + int(site.group(2))
    ca = parse_ca(row.get("lte_ca"))
    used = {"%s/%s" % (c[1], c[2]): (i, c) for i, c in enumerate(ca)}
    seen = {"%s/%s" % m.group(1, 2): f(m.group(3)) for m in re.finditer(r"(\d+)/(\d+):(-?[\d.]+)", row.get("lte_neighbours") or "")}
    for k in set(seen) | set(used):
        e = REG["lte"].setdefault(k, {"band": band_of(k.split("/")[0]), "first": now, "seen_min": 0, "anchor_min": 0, "ca_min": 0})
        e["last"] = now; e["seen_min"] += 1
        e["rsrp"] = _ew(e.get("rsrp"), seen.get(k) if k in seen else (used[k][1][3] if k in used else None))
        if k in used:
            i, c = used[k]; e["ca_min"] += 1; e["sinr"] = _ew(e.get("sinr"), c[4]); e["bw"] = c[5]
            if i == 0:
                e["anchor_min"] += 1
                if row.get("lte_site"): e["site"] = row["lte_site"]
    nr = row.get("nr_cell")
    if nr and nr != "no5G" and "255" not in nr.split("/"):          # 255/255 = ZTE placeholder right after boot
        e = REG["nr"].setdefault(nr, {"first": now, "served_min": 0})
        e["last"] = now; e["served_min"] += 1
        e["sinr"] = _ew(e.get("sinr"), f(row.get("nr_sinr"))); e["rsrp"] = _ew(e.get("rsrp"), f(row.get("nr_rsrp")))
    for m in re.finditer(r"(\d+)/(\d+):(-?[\d.]*)/(-?[\d.]*)", row.get("nr_ca") or ""):     # 5G secondary carriers
        e = REG["nr"].setdefault("%s/%s" % m.group(1, 2), {"first": now, "served_min": 0})
        e["last"] = now; e["ca_min"] = e.get("ca_min", 0) + 1
        e["ca_rsrp"] = _ew(e.get("ca_rsrp"), f(m.group(3))); e["ca_sinr"] = _ew(e.get("ca_sinr"), f(m.group(4)))
    reg_st["n"] += 1
    if reg_st["dir"] and reg_st["n"] % 10 == 0: save_registry()


# per device: what the ZTE (outside) and the ATL (at the window) measure must not mix; cells.json stays the ZTE's file
CELLS_FILE = "cells.json" if store.DEVICE == "zte" else "cells-%s.json" % store.DEVICE


def forget(cells):
    """Drop cells from this device's registry (e.g. another device's cells that got in): ['500/341', ...] -> the ones removed."""
    gone = [c for c in cells if REG["lte"].pop(c, None) is not None or REG["nr"].pop(c, None) is not None]
    if gone: save_registry()
    return gone


def save_registry():
    """Write the cell registry (per device) and the shared cell ids (atomic replace)."""
    if not reg_st["dir"]: return
    try:
        for name, part in ((CELLS_FILE, {k: v for k, v in REG.items() if k != "ids"}), ("cell-ids.json", REG.get("ids") or {})):
            p = os.path.join(reg_st["dir"], name)            # cell ids are network facts: shared by the devices; the rest per device
            with open(p + ".tmp", "w") as fh: json.dump(part, fh)
            os.replace(p + ".tmp", p)
    except OSError:
        pass


def load_registry(data_dir):
    """Load the registry (or build it from the history on the first start) and your optional sites.json."""
    reg_st["dir"] = data_dir
    try:
        with open(os.path.join(data_dir, CELLS_FILE)) as fh: REG.update(json.load(fh))
        try:
            with open(os.path.join(data_dir, "cell-ids.json")) as fh: REG.setdefault("ids", {}).update(json.load(fh))
        except (OSError, ValueError): pass
    except (OSError, ValueError):
        for r in list(HIST): track(r, ts(r))         # first start: build it from the samples already on disk
        reg_st["n"] = 0; save_registry()
    try:                                     # your own site notes - reference only, measurements stay the source
        with open(os.path.join(data_dir, "sites.json")) as fh: s = json.load(fh)
        reg_st["sites"] = {str(x.get("enb")): x for x in s.get("sites", []) if x.get("enb") is not None}
        reg_st["zte"] = s.get("zte") or {}
    except (OSError, ValueError):
        reg_st["sites"] = {}


def geo(lat1, lon1, lat2, lon2):
    """distance (m) and bearing (deg) from point 1 to point 2"""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    b = math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360
    return round(2 * 6371000 * math.asin(math.sqrt(a))), round(b, 1)


def cells_view(now=None):
    """registry as a list, newest knowledge first, with your site notes (distance / bearing from the ZTE) where the site is known"""
    now = now or time.time()
    def site_info(site):
        s = reg_st["sites"].get(str(site).split("/")[0]) if site else None
        z = reg_st.get("zte") or {}
        if not s or s.get("lat") is None or z.get("lat") is None: return {}
        d, b = geo(z["lat"], z["lon"], s["lat"], s["lon"])
        return {"site_name": s.get("name", ""), "site_dist_m": d, "site_bearing": b}
    day = lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    ids = REG.get("ids") or {}
    eci = lambda k: (lambda v: "%d/%d" % (v >> 8, v & 0xFF) if v else "")(ids.get("lte:" + k.replace("/", ":")))
    lte = [dict({"cell": k, "band": e["band"], "rsrp": e.get("rsrp"), "sinr_when_used": e.get("sinr"), "bw_mhz": e.get("bw"),
                 "seen_min": e["seen_min"], "ca_min": e["ca_min"], "anchor_min": e["anchor_min"], "site": e.get("site") or eci(k),
                 "first": day(e["first"]), "last": day(e["last"]), "lock_test": e.get("lock_test")}, **site_info(e.get("site") or eci(k)))
           for k, e in REG["lte"].items()]
    nr = [{"cell": k, "nci": ids.get("nr:" + k.replace("/", ":"), ""), "served_min": e["served_min"], "sinr": e.get("sinr"), "rsrp": e.get("rsrp"), "ca_min": e.get("ca_min", 0),
           "ca_sinr": e.get("ca_sinr"), "ca_rsrp": e.get("ca_rsrp"), "first": day(e["first"]), "last": day(e["last"])}
          for k, e in REG["nr"].items() if "255" not in k.split("/")]
    return {"lte": sorted(lte, key=lambda x: (x["band"], -(x["rsrp"] or -999))), "nr": sorted(nr, key=lambda x: -(x["served_min"] + x["ca_min"]))}


def network_changes(now=None):
    """new strong cells and cells that went away - the network evolves, so the best choice can change"""
    now = now or time.time(); out = []
    for k, e in REG["lte"].items():
        band_best = max((x.get("rsrp") or -999 for x in REG["lte"].values() if x["band"] == e["band"] and x is not e), default=-999)
        if e["first"] - (REG.get("since") or e["first"]) > 3600 and now - e["first"] < 86400 and e["seen_min"] >= 30 \
                and (e.get("rsrp") or -999) >= band_best - 6:
            out.append("NEW: LTE %s %s seen for %d min, RSRP %s (strong for its band) - consider it in the next lock test" % (e["band"], k, e["seen_min"], e.get("rsrp")))
        if e["seen_min"] >= 600 and now - e["last"] > 86400:
            out.append("GONE: LTE %s %s not seen for %d h (was seen %d min) - re-check the lock / re-run the lock test"
                       % (e["band"], k, (now - e["last"]) / 3600, e["seen_min"]))
    for k, e in REG["nr"].items():
        if e["served_min"] >= 600 and now - e["last"] > 86400:
            out.append("GONE: 5G %s not served for %d h" % (k, (now - e["last"]) / 3600))
    return out


# ---------- best anchor from the lock tests ----------
def nr_capacity(r):
    """5G cell of a lock-test row -> estimated Mbit/s per MIMO layer (Yettel n78 100 MHz / 20 MHz, n28 10 MHz; TDD share applied).
    The ARFCN comes from `nr_arfcn` (the cell the step measured - the unlocked baseline has no lock spec), else from the lock."""
    arfcn = r.get("nr_arfcn") or (r.get("lock") or "").split("@")[-1]
    bw = NR_BW.get(arfcn) or (100 if arfcn.isdigit() and 620000 <= int(arfcn) <= 653333 else 10 if arfcn.isdigit() and 151600 <= int(arfcn) <= 160600 else None)
    c = capacity(bw, f(r.get("nr_sinr_p50")))
    return round(c * NR_DL_SHARE, 1) if c and arfcn.isdigit() and int(arfcn) >= 620000 else c


def anchor_ranking(rat="lte"):
    """latest lock-test result per candidate of one RAT (lte: anchors, nr: 5G cells), ranked for calls: call-ready %, then no 5G
    drops, 5G SINR, capacity. A candidate only counts if the modem really stayed on it (>= 80 % of the minutes); the baseline is None
    when its newest row was not measured (a skipped step is no reference) or was unstable."""
    if not reg_st["dir"]: return {"rat": rat, "baseline": None, "ranked": [], "failed": [], "unstable": []}
    rows = store.rows(reg_st["dir"], "lock_tests")
    latest = {}
    for r in rows:                     # cell lists (the MC889 keeps only the first cell) and skipped / stopped steps are no results
        if r.get("rat", "lte") == rat and ";" not in r["lock"] and r.get("mode") not in ("cut", "night"): latest[r["lock"]] = r
    def cap(r):
        if rat == "nr": return nr_capacity(r)
        if not r.get("anchor"): return None
        e = REG["lte"].get(r["anchor"]) or {}
        return capacity(e.get("bw") or BANDS.get(band_of(r["anchor"].split("/")[0])[1:], (0, None))[1], f(r.get("anchor_sinr_p50")))
    def key(r):
        if rat == "nr":                 # 5G cells: internet quality first, signal only breaks a tie, capacity last
            return quality_values(r.get("call_ok_pct"), r.get("loss_pct"), r.get("lat_p95"), r.get("jitter_ms"),
                                  r.get("nr_sinr_p50"), r.get("nr_rsrp_p50")) + (nr_capacity(r) or 0,)
        # LTE: both legs count: a weak anchor drops the 5G leg, so anchor SINR + 5G SINR (a 1 dB better 5G must not beat a -1 dB anchor)
        return (f(r.get("call_ok_pct")) or 0, -(f(r.get("no5g_min")) or 0), (f(r.get("anchor_sinr_p50")) or -20) + (f(r.get("nr_sinr_p50")) or -20), cap(r) or 0)
    unstable = lambda r: str(r.get("unstable")) == "1"      # the 5G leg follows its LTE anchor: not a result when the anchor was unstable
    base = latest.get("none")
    if base and (unstable(base) or f(base.get("call_ok_pct")) is None): base = None   # skipped / not measured: not a reference
    ok = [dict(r, capacity=cap(r)) for k, r in latest.items() if k != "none" and not unstable(r) and (f(r.get("on_target_pct")) or 0) >= 80]
    return {"rat": rat, "baseline": dict(base, capacity=cap(base)) if base else None, "ranked": sorted(ok, key=key, reverse=True),
            "failed": [k for k, r in latest.items() if k != "none" and not unstable(r) and (f(r.get("on_target_pct")) or 0) < 80],
            "unstable": sorted(k for k, r in latest.items() if unstable(r))}


def night_best():
    """Latest night screen -> (run, base_row, other_rows, winner_rows best-first); winners beat the base on an unloaded network.
    (None, None, [], []) when there's nothing to judge."""
    if not reg_st["dir"]: return None, None, [], []
    rows = [r for r in store.rows(reg_st["dir"], "lock_tests") if r.get("mode") == "night" and r.get("run")]
    if not rows: return None, None, [], []
    run = max(r["run"] for r in rows); rows = [r for r in rows if r["run"] == run]
    base, others = rows[0], rows[1:]
    def better(r):
        return ((f(r.get("on_target_pct")) or 0) >= 80 and (f(r.get("call_ok_pct")) or 0) >= (f(base.get("call_ok_pct")) or 0) and
                ((f(r.get("anchor_sinr_p50")) or -99) - (f(base.get("anchor_sinr_p50")) or -99) >= 3 or
                 (f(r.get("nr_sinr_p50")) or -99) - (f(base.get("nr_sinr_p50")) or -99) >= 2))
    win = sorted((r for r in others if better(r)), key=lambda r: (f(r.get("anchor_sinr_p50")) or -99, f(r.get("nr_sinr_p50")) or -99), reverse=True)
    return run, base, others, win


def night_finding():
    """latest night screen as a one-line finding (RECOMMEND when a cell beat the lock, else INFO)"""
    run, base, others, win = night_best()
    if run is None: return []
    d = lambda r: "%s anchor SINR %s, 5G %s SINR %s" % (r["lock"], r.get("anchor_sinr_p50"), r.get("nr_cells"), r.get("nr_sinr_p50"))
    if win:
        return ["RECOMMEND: night screen %s: %s beat %s (%s) on an unloaded network -> run an aligned daytime lock test for it"
                % (run[:10], ", ".join(d(r) for r in win), base["lock"], d(base))]
    return ["INFO: night screen %s: %s - none beat %s (%s)" % (run[:10], ", ".join(d(r) for r in others) or "nothing measured", base["lock"], d(base))]


def anchor_best(rat="lte"):
    """Daytime ranking of one RAT (`lte`: anchors, `nr`: 5G cells) -> (best_row, base_row, gain) where gain = best clearly beats the
    unlocked baseline (under load). (None, None, False) with no ranked results or no baseline (we don't auto-apply without a baseline)."""
    rk = anchor_ranking(rat)
    if not rk["ranked"] or not rk["baseline"]: return (rk["ranked"][0] if rk["ranked"] else None), rk["baseline"], False
    best, base = rk["ranked"][0], rk["baseline"]
    if rat == "nr":                     # 5G: the same clear-win rule as the antenna mode (signal only when the quality is a tie)
        q = lambda r: quality_values(r.get("call_ok_pct"), r.get("loss_pct"), r.get("lat_p95"), r.get("jitter_ms"),
                                     r.get("nr_sinr_p50"), r.get("nr_rsrp_p50"))
        return best, base, clearly_better(q(best), q(base))
    gain = ((f(best.get("call_ok_pct")) or 0) - (f(base.get("call_ok_pct")) or 0) >= 1 or
            (f(best.get("nr_sinr_p50")) or -99) - (f(base.get("nr_sinr_p50")) or -99) >= 2 or
            (f(best.get("anchor_sinr_p50")) or -99) - (f(base.get("anchor_sinr_p50")) or -99) >= 3)
    return best, base, gain


def anchor_finding(rk):
    """Findings from the lock-test ranking: recommend the best anchor, or say no lock is needed / it is the lock already."""
    if not rk["ranked"]: return []
    best, base = rk["ranked"][0], rk["baseline"]
    d = lambda r: "call-ready %s%%, 5G %s SINR %s, anchor SINR %s (~%s Mbit/s/layer)%s" % (r.get("call_ok_pct"), r.get("nr_cells"), r.get("nr_sinr_p50"),
        r.get("anchor_sinr_p50"), r.get("capacity"), (", site " + r["site"]) if r.get("site") else "")
    if base:
        gain = ((f(best.get("call_ok_pct")) or 0) - (f(base.get("call_ok_pct")) or 0) >= 1 or
                (f(best.get("nr_sinr_p50")) or -99) - (f(base.get("nr_sinr_p50")) or -99) >= 2 or
                (f(best.get("anchor_sinr_p50")) or -99) - (f(base.get("anchor_sinr_p50")) or -99) >= 3)
        if not gain:
            return ["OK: lock tests - no candidate clearly beats the unlocked baseline (%s); best %s: %s - no lock needed" % (d(base), best["lock"], d(best))]
    try:
        with open(os.path.join(reg_st["dir"] or ".", common.state_file())) as fh: held = json.load(fh).get("lte_lock") or {}
    except (OSError, ValueError): held = {}
    if held.get("cell") == best["lock"]:
        out = ["OK: best tested LTE anchor %s is the keeper's lock (%s%s): %s" % (best["lock"], held.get("state"),
               ", %s" % held["reason"] if held.get("state") == "released" and held.get("reason") else "", d(best))]
    else:
        out = ["RECOMMEND: best LTE anchor %s: %s%s -> lock it (keeper LTE lock), then align for it"
               % (best["lock"], d(best), (" | unlocked: " + d(base)) if base else "")]
    if rk.get("failed"): out.append("INFO: lock tests where the ZTE could not stay on the cell (not usable from here): %s" % ", ".join(rk["failed"]))
    return out


# ---------- analysis ----------
def report(hours=24):
    """Analysis of the last `hours`: call quality (overall, per hour, under load), 5G cells, LTE carriers and cells, findings."""
    rows = window(hours)
    out = {"hours": hours, "samples": len(rows), "from": rows[0]["time"] if rows else None, "to": rows[-1]["time"] if rows else None}
    if not rows:
        out["findings"] = ["no data yet"]; return out
    out["call"] = {"ok_pct": share(rows), "lat_p50": med([f(r.get("lat_p50")) for r in rows]), "lat_p95": med([f(r.get("lat_p95")) for r in rows]),
                   "jitter_ms": med([f(r.get("jitter_ms")) for r in rows]),
                   "loss_pct": round(statistics.mean([f(r.get("loss_pct")) or 0 for r in rows]), 2)}
    by_hour = collections.defaultdict(list)
    for r in rows: by_hour[r["time"][11:13]].append(r)
    out["by_hour"] = {h: {"call_ok_pct": share(rs), "lat_p95": med([f(r.get("lat_p95")) for r in rs]),
                          "jitter_ms": med([f(r.get("jitter_ms")) for r in rs])} for h, rs in sorted(by_hour.items())}
    busy = [r for r in rows if (f(r.get("up_bps")) or 0) >= BUSY_UP]
    idle = [r for r in rows if (f(r.get("up_bps")) or 0) <= IDLE_UP and (f(r.get("down_bps")) or 0) <= IDLE_DOWN]
    out["under_load"] = {"busy_minutes": len(busy), "busy_lat_p95": med([f(r.get("lat_p95")) for r in busy]),
                         "idle_lat_p95": med([f(r.get("lat_p95")) for r in idle]), "busy_call_ok_pct": share(busy)}
    cells = collections.defaultdict(list)
    for r in rows: cells[r["nr_cell"]].append(r)
    out["cells"] = sorted(({
        "cell": c, "minutes": len(rs), "share_pct": round(100 * len(rs) / len(rows), 1), "call_ok_pct": share(rs),
        "sinr_p10": pct([f(r.get("nr_sinr")) for r in rs], 10), "sinr_p50": med([f(r.get("nr_sinr")) for r in rs]),
        "rsrp_p50": med([f(r.get("nr_rsrp")) for r in rs]),
        "lat_p95": med([f(r.get("lat_p95")) for r in rs]), "jitter_ms": med([f(r.get("jitter_ms")) for r in rs]),
        "loss_pct": round(statistics.mean([f(r.get("loss_pct")) or 0 for r in rs]), 2)} for c, rs in cells.items()),
        key=lambda x: -x["minutes"])
    out["switches"] = sum(1 for a, b in zip(rows, rows[1:]) if a["nr_cell"] != b["nr_cell"])
    out["no5g_minutes"] = sum(1 for r in rows if no5g_busy(r))                # with traffic only; idle minutes without 5G are normal
    out["no5g_idle_minutes"] = len(cells.get("no5G", [])) - out["no5g_minutes"]
    bands = collections.defaultdict(lambda: {"sinr": [], "rsrp": [], "anchor": 0})
    for r in rows:
        for i, (b, ch, pci, rsrp, sinr, bw) in enumerate(parse_ca(r.get("lte_ca"))):
            k = "B%s %s/%s" % (b, ch, pci)
            bands[k]["sinr"].append(sinr); bands[k]["rsrp"].append(rsrp); bands[k]["bw"] = bw
            if i == 0: bands[k]["anchor"] += 1
    out["lte_carriers"] = sorted(({"carrier": k, "mhz": BANDS.get(k.split(" ")[0][1:], (None,))[0], "bw_mhz": v["bw"],
                                   "anchor_pct": round(100 * v["anchor"] / len(rows), 1), "sinr_p50": med(v["sinr"]),
                                   "rsrp_p50": med(v["rsrp"]), "capacity": capacity(v["bw"], med(v["sinr"])), "samples": len(v["sinr"])}
                                  for k, v in bands.items()), key=lambda x: -x["anchor_pct"])
    seen = collections.defaultdict(list)          # LTE cells the ZTE measures (neighbour list incl. serving): RSRP only
    for r in rows:
        for m in re.finditer(r"(\d+)/(\d+):(-?[\d.]+)", r.get("lte_neighbours") or ""):
            seen["%s/%s" % (m.group(1), m.group(2))].append(f(m.group(3)))
    ca_n = collections.Counter("%s/%s" % (c[1], c[2]) for r in rows for c in parse_ca(r.get("lte_ca")))
    cells_all = [{"cell": k, "band": band_of(k.split("/")[0]), "rsrp_p50": med(v), "seen_pct": round(100 * len(v) / len(rows), 1),
                  "ca_pct": round(100 * ca_n.get(k, 0) / len(rows), 1), "in_ca": ca_n.get(k, 0) >= 0.2 * len(rows)}
                 for k, v in seen.items() if len(v) >= 0.1 * len(rows)]          # rarely seen cells are noise
    out["lte_cells"] = [x for band in sorted({c["band"] for c in cells_all})
                        for x in sorted((c for c in cells_all if c["band"] == band), key=lambda c: -(c["rsrp_p50"] or -999))[:6]]
    out["actions"] = [{"time": datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"), "what": w, "reason": why, "mode": m}
                      for t, w, why, m in list(ACTIONS) if time.time() - t < hours * 3600]
    out["findings"] = findings(out)
    return out


def findings(rep):
    """turn the numbers into plain conclusions + what a human should decide (the keeper never locks on its own)"""
    fs = []
    if rep["samples"] < 120:
        fs.append("only %d min of data - conclusions get reliable after a few hours" % rep["samples"])
    c = rep.get("call") or {}
    if c.get("ok_pct") is not None:
        fs.append("%s: call-ready %s%% of minutes (latency p95 %s ms, jitter %s ms, loss %s%%)"
                  % ("OK" if c["ok_pct"] >= 95 else "WARN", c["ok_pct"], c["lat_p95"], c["jitter_ms"], c["loss_pct"]))
        bad = [h for h, v in (rep.get("by_hour") or {}).items() if v["call_ok_pct"] is not None and v["call_ok_pct"] < 90]
        if bad:
            fs.append("WARN: calls at risk at %s h (under 90%% call-ready minutes)" % ",".join(bad))
    ul = rep.get("under_load") or {}
    if ul.get("busy_minutes", 0) >= 10 and ul.get("busy_lat_p95") is not None and ul.get("idle_lat_p95") is not None \
            and ul["busy_lat_p95"] - ul["idle_lat_p95"] >= 80:
        fs.append("RECOMMEND: latency rises from %s to %s ms while you upload (calls, screen share) - bufferbloat -> add a cake / "
                  "fq_codel queue on the MikroTik uplink" % (ul["idle_lat_p95"], ul["busy_lat_p95"]))
    car = rep.get("lte_carriers") or []
    anchor = next((x for x in car if x["anchor_pct"] >= 50), None)
    if anchor and anchor["capacity"] is not None:
        # compare carriers by estimated capacity (bandwidth x 3GPP truncated Shannon), never by RSRP across bands; a low band that only wins on
        # RSRP is not a candidate (it reaches further and indoors, so more users share it)
        tested = {r["lock"] for r in anchor_ranking().get("ranked") or []}
        cand = [x for x in car if x is not anchor and x["capacity"] is not None and x["samples"] >= 60      # skip cells a lock test already measured
                and "%s@%s" % tuple(reversed(x["carrier"].split(" ")[1].split("/"))) not in tested
                and x["capacity"] >= 1.5 * anchor["capacity"] and x["sinr_p50"] - anchor["sinr_p50"] >= 3]
        if cand:
            b = max(cand, key=lambda x: (x["capacity"], x["rsrp_p50"] or -999, x["mhz"] or 0))   # ties: stronger, then higher band
            ch, pci = b["carrier"].split(" ")[1].split("/")
            def d(x): return "%s %s MHz %s MHz-wide SINR %s (~%s Mbit/s per layer%s)" % (x["carrier"], x["mhz"], x["bw_mhz"], x["sinr_p50"], x["capacity"],
                                                                               ", low band: shared by more users" if (x["mhz"] or 9999) < 1000 else "")
            fs.append("RECOMMEND: LTE anchor %s is weak; on the same aggregation: %s -> A/B test LTE lock %s@%s (the test also shows whether CA stays)"
                      % (d(anchor), "; ".join(d(x) for x in sorted(car, key=lambda x: -(x["capacity"] or 0)) if x is not anchor), pci, ch))
    cells = [x for x in rep.get("cells", []) if x["cell"] != "no5G" and x["sinr_p50"] is not None]
    if len(cells) > 1:
        best = max(cells, key=lambda x: x["sinr_p50"])
        for x in cells:
            if x is not best and x["share_pct"] >= 10 and best["sinr_p50"] - x["sinr_p50"] >= 4:
                fs.append("RECOMMEND: 5G %s (SINR %s, call-ready %s, %s%% of the time) is clearly worse than %s (SINR %s, call-ready %s) "
                          "-> consider NR lock %s or PREFERRED_NR=%s" % (x["cell"], x["sinr_p50"], pct_txt(x["call_ok_pct"]), x["share_pct"],
                          best["cell"], best["sinr_p50"], pct_txt(best["call_ok_pct"]), best["cell"].split("/")[1], best["cell"]))
    lte = rep.get("lte_cells") or []
    for band in sorted({x["band"] for x in lte if x["in_ca"]}):            # RSRP only means something within one band
        used = [x for x in lte if x["band"] == band and x["in_ca"] and x["rsrp_p50"] is not None]
        other = [x for x in lte if x["band"] == band and not x["in_ca"] and x["seen_pct"] >= 50 and x["rsrp_p50"] is not None]
        if used and other:
            u, o = max(used, key=lambda x: x["rsrp_p50"]), max(other, key=lambda x: x["rsrp_p50"])
            if o["rsrp_p50"] - u["rsrp_p50"] >= 6:
                mhz = BANDS.get(band[1:], (0, 0))
                fs.append("INFO: %s (%s MHz, %s MHz wide): %s is %d dB stronger than the used %s, but its SINR/load are unknown (strong but far or "
                          "busy cells often have poor SINR) - only a lock test would tell%s" % (band, mhz[0], mhz[1], o["cell"],
                          o["rsrp_p50"] - u["rsrp_p50"], u["cell"], "; low band, low priority" if mhz[0] < 1000 else ""))
    fs += anchor_finding(anchor_ranking()) + night_finding()
    fs += network_changes()
    if rep.get("no5g_minutes", 0) >= 30:
        fs.append("WARN: %d min without 5G while there was traffic" % rep["no5g_minutes"])
    if not any(x.startswith(("RECOMMEND", "WARN", "NEW", "GONE")) for x in fs):
        fs.append("OK: nothing to change")
    return fs


def status(cfg):
    """/status: verdict now, current values, download estimate, config, findings of the last 24 h."""
    last = HIST[-1] if HIST else {}
    sinr = f(last.get("nr_sinr"))
    if not last: verdict = "unknown"
    elif no5g_busy(last) or (f(last.get("loss_pct")) or 0) >= 20: verdict = "poor"
    elif str(last.get("call_ok")) == "0" or (sinr is not None and sinr < MIN_SINR): verdict = "fair"
    else: verdict = "good"
    ca = parse_ca(last.get("lte_ca"))
    return {"verdict": verdict, "call_ready": str(last.get("call_ok")) == "1", "time": last.get("time"),
            "nr_cell": last.get("nr_cell"), "nr_sinr": last.get("nr_sinr"), "nr_rsrp": last.get("nr_rsrp"), "nr_rsrq": last.get("nr_rsrq"),
            "lte_anchor": ("B%s %s/%s SINR %s" % (ca[0][0], ca[0][1], ca[0][2], ca[0][4])) if ca else last.get("lte_cell"),
            "lte_ca": last.get("lte_ca"), "latency": {c: last.get(c) for c in LAT_COLS}, "uptime_s": last.get("uptime_s"),
            "temps": last.get("temps"), "fota": last.get("fota"), "auto": AUTO, "capacity": capacity_now(last) if last else None, "pending": ctx["pending"], "config": cfg,
            "findings_24h": report(24)["findings"]}


def api(path, cfg, data_dir):
    """GET endpoints -> (status, dict)"""
    u = path.split("?", 1); q = dict(p.split("=", 1) for p in (u[1].split("&") if len(u) > 1 else []) if "=" in p)
    if u[0] in ("/", "/status"): return 200, status(cfg)
    if u[0] == "/report": return 200, report(float(q.get("hours", "24")))
    if u[0] == "/cells": return 200, cells_view()
    if u[0] == "/samples":                  # raw minutes: ?from=2026-10-28 01:00&to=2026-10-28 01:15&cols=time,lte_cell (newest 500)
        uq = lambda v: urllib.parse.unquote_plus(v)
        lo, hi = uq(q.get("from", "")), uq(q.get("to", "9999"))
        cols = [c for c in uq(q.get("cols", "")).split(",") if c]
        rows = store.rows(data_dir, "samples", lo, hi, last=500)            # from the store: the whole retention (KEEP_DAYS), not just 48 h
        return 200, {"rows": [{c: r.get(c) for c in cols} if cols else r for r in rows]}
    if u[0] == "/anchors":                  # ?rat=lte|nr (default lte): the lock-test ranking of that RAT
        rat = q.get("rat") or "lte"
        return (200, anchor_ranking(rat)) if rat in ("lte", "nr") else (400, {"error": "rat=lte|nr"})
    if u[0] == "/events":
        return 200, store.rows(data_dir, "events", last=int(q.get("n", "50")))
    return 404, {"error": "unknown path", "paths": ["/status", "/report?hours=24", "/events?n=50", "/cells"]}


def start(data_dir):
    """Load history + registry, start the latency probe (unless LAT_TARGETS=off)."""
    load(data_dir)
    load_registry(data_dir)
    if TARGETS: threading.Thread(target=probe_loop, daemon=True).start()
