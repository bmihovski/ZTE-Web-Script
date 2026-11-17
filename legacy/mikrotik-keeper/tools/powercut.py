#!/usr/bin/env python3
"""After a power cut: wait until the router answers, save its log, map it to real time and check what matters.

The router (RB450Gx4, no RTC) boots with an old clock; NTP fixes it only once the WAN has its dynamic address from the modem
('ntp change time A => B'). RouterOS keeps its log in memory since that boot, the keeper's container lines included - one source,
in real order. Lines before the NTP line carry the old time (+ the step = real time).
Checks: the keeper started; NTP stepped soon after INTERNET; the keeper flagged its clock before the step, verified it within 120 s
after, blamed the router (not the modem), and took no time-of-day action (capacity test, night screen, LOCK_PAUSE, lock changes)
on the wrong clock. Each WAN outage is listed; in one without a router boot (a modem reboot: lock change, a lock test
on a missing cell - minutes) the router's clock stays right, so a keeper 'clock not verified' there is a FAIL.

  tools/powercut.py --wait --follow 15      # start BEFORE the cut: waits for a new boot, logs until the router's and keeper's
                                            # clocks agree with the Mac and the keeper trusts its clock (or 15 min)
  tools/powercut.py                         # one look at the current boot's log now
  tools/powercut.py --log reports/x.log     # analyse a saved log again
Writes reports/powercut-<time>.log + .csv + .txt; exit 1 on a FAIL. The Mac's clock is the reference: each poll writes a CSV row
(Mac time, router clock + uptime, keeper clock from its HTTP Date + its verdict) - start it BEFORE the cut so the outage is on it. RouterOS keeps 1000 memory lines by default
(/system logging action set memory memory-lines=5000 if the boot scrolls out).
"""
import argparse, csv, datetime, email.utils, json, os, re, subprocess, sys, time, urllib.request

HOST, URL = "admin@192.168.6.1", os.environ.get("KEEPER_URL", "http://keeper.lan:7557")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE = re.compile(r"^\s*(?:(\d{4}-\d\d-\d\d) )?(\d\d:\d\d:\d\d) (\S+) (.*)$")
ACTIONS = re.compile(r"capacity test|night screen|LOCK_PAUSE|lock restore|lte lock|5g lock|lock test", re.I)
VERIFY_S, NTP_S, START_S = 120, 120, 60


# ---------- pure (test/scenarios/powercut.py) ----------
def parse(text):
    """RouterOS `/log print` -> [{"i", "stamp", "t", "topics", "msg"}]; a line without its date takes the last one seen (or today)."""
    out, day = [], time.strftime("%Y-%m-%d")
    for raw in text.splitlines():
        m = LINE.match(raw)
        if not m: continue
        day = m.group(1) or day; stamp = "%s %s" % (day, m.group(2))
        out.append({"i": len(out), "stamp": stamp, "t": time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S")), "topics": m.group(3), "msg": m.group(4)})
    return out


def first(lines, pred):
    return next((l for l in lines if pred(l)), None)


def keeper(l):
    return l["msg"].startswith("zte-keeper: ") and "***" not in l["msg"]


def timeline(lines):
    """The boot's landmarks; offset = the NTP step in s (0 = none), lines before it are on the old clock."""
    tl = {"boot": lines[0] if lines else None, "power": first(lines, lambda l: "without proper shutdown" in l["msg"]),
          "container": first(lines, lambda l: l["msg"].startswith("zte-keeper: *** started")),
          "keeper": first(lines, lambda l: keeper(l) and " start (v2 " in l["msg"]),
          "ip": first(lines, lambda l: "got IP address" in l["msg"]), "internet": first(lines, lambda l: "detect INTERNET" in l["msg"]),
          "ntp": None, "offset": 0, "ntp_at": None, "ntp_from": None, "ntp_to": None}
    for l in lines:
        m = re.search(r"ntp change time (\S+ \S+) => (\S+ \S+)", l["msg"])
        if m:
            a, b = (time.mktime(time.strptime(x, "%b/%d/%Y %H:%M:%S")) for x in m.groups())
            # the step's times come from the message: RouterOS stamps this line with the old OR the new time (2026-11-17: both seen)
            tl.update(ntp=l, offset=round(b - a), ntp_from=a, ntp_to=b, ntp_at=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(a))); break
    return tl


def real(l, tl):
    """Real epoch of a line: + the NTP step when it was logged before the step."""
    return l["t"] + (tl["offset"] if tl["ntp"] and l["i"] < tl["ntp"]["i"] else 0)


def outages(lines, tl):
    """WAN outages: from the boot (a router boot) or a 'lost IP address' to 'got IP address' and 'detect INTERNET'."""
    out, cur = [], None
    if tl["power"] or tl["container"]: cur = {"start": lines[0], "boot": True, "ip": None, "internet": None}; out.append(cur)
    for l in lines:
        if "lost IP address" in l["msg"]: cur = {"start": l, "boot": False, "ip": None, "internet": None}; out.append(cur)
        elif cur and "got IP address" in l["msg"] and not cur["ip"]: cur["ip"] = l
        elif cur and "detect INTERNET" in l["msg"] and not cur["internet"]: cur["internet"] = l; cur = None
    return out


def checks(lines):
    """[(PASS|WARN|FAIL|INFO, name, detail)]"""
    tl, out = timeline(lines), []
    add = lambda st, n, d: out.append((st, n, d))
    boot, off, ntp = tl["boot"], tl["offset"], tl["ntp"]
    add("INFO", "power", "router rebooted without proper shutdown at %s (its old clock)" % tl["power"]["stamp"] if tl["power"]
        else "no power-outage line (a planned reboot?)")
    ks, cs = tl["keeper"], tl["container"]; booted = bool(tl["power"] or cs)
    if not ks: add("FAIL" if booted else "INFO", "keeper start", "no keeper start line in the router's log" if booted else "not in this log (no router boot)")
    else:
        v = re.search(r"start \(v2 (\S+)\)", ks["msg"]); d = ks["t"] - (cs or boot)["t"]
        add("PASS" if d <= START_S else "WARN", "keeper start", "%d s after the container start (keeper %s)" % (d, v.group(1) if v else "?"))
    net = tl["internet"]; outs = outages(lines, tl)
    at = lambda o, k: "%s %s (+%d s)" % (k, o[k.lower()]["stamp"][11:], o[k.lower()]["t"] - o["start"]["t"]) if o[k.lower()] else "%s never" % k
    add("INFO", "wan", "; ".join("down %s%s -> %s, %s" % ("from the boot " if o["boot"] else "", o["start"]["stamp"][11:], at(o, "IP"),
        at(o, "INTERNET")) for o in outs) or "no WAN outage in this log")
    plain = [o for o in outs if not o["boot"]]           # the router stayed up: its clock is right, the keeper's verdict must hold
    alarm = [l for o in plain for l in lines if keeper(l) and "clock not verified" in l["msg"]
             and o["start"]["i"] < l["i"] < (o["internet"] or o["ip"] or lines[-1])["i"]]
    add("FAIL" if alarm else "PASS", "clock in WAN outages", "clock alarm at %s while only the WAN was down (no router boot)" % alarm[0]["stamp"][11:]
        if alarm else "no clock alarm in %d WAN outage(s) without a router boot" % len(plain) if plain else "no WAN outage without a router boot")
    if not ntp: add("INFO", "ntp", "no NTP step (the clock was right)")
    else:
        d = tl["ntp_from"] - net["t"] if net else None
        add("PASS" if d is not None and d <= NTP_S else "WARN", "ntp", "stepped %+d min (%s -> %s), %s" % (
            round(off / 60), tl["ntp_at"][11:], time.strftime("%H:%M:%S", time.localtime(tl["ntp_to"])), "%d s after INTERNET" % d if d is not None else "no INTERNET line"))
    big = abs(off) > 300
    kl = [l for l in lines if keeper(l)]
    ver = first([l for l in kl if not ntp or l["i"] > ntp["i"]], lambda l: "clock verified" in l["msg"]) if big else None
    if not big:
        add("PASS", "flagged", "nothing to flag"); add("PASS", "verified", "nothing to verify"); add("PASS", "blame", "no wrong clock")
        add("PASS", "no time-of-day actions", "the clock was right"); return out
    flag = first([l for l in kl if l["i"] < ntp["i"]], lambda l: "clock not verified" in l["msg"])
    add("PASS", "flagged", "clock not verified %d s before the NTP step" % (tl["ntp_from"] - flag["t"])) if flag else \
        add("FAIL", "flagged", "the keeper never flagged its clock before NTP fixed it")
    d = real(ver, tl) - tl["ntp_to"] if ver else None
    add("PASS" if d is not None and d <= VERIFY_S else "FAIL", "verified",
        "%d s after the NTP step" % d if d is not None else "no 'clock verified' after the NTP step")
    modem = [l for l in kl if re.search(r"the modem's was off|its clock is wrong, ours stays trusted|modem clock wrong", l["msg"])]
    add("FAIL" if modem else "PASS", "blame", "the keeper blamed the modem (%s) but the router's clock was stepped %+d min" % (
        modem[0]["stamp"], round(off / 60)) if modem else "the router's clock (it was stepped)")
    end = ver["i"] if ver else len(lines)
    act = [l for l in kl if l["i"] < end and "clock" not in l["msg"] and ACTIONS.search(l["msg"])]
    add("FAIL" if act else "PASS", "no time-of-day actions", "; ".join("%s %s" % (l["stamp"][11:], l["msg"][12:90]) for l in act[:5])
        if act else "none before the clock was verified")
    add("INFO", "wrong stamps", "%s .. %s (%d s) carry the router's old time: add %d min" % (
        boot["stamp"], tl["ntp_at"][11:], tl["ntp_from"] - boot["t"], round(off / 60)))
    return out


# ---------- the Mac's clock as the reference (NTP-synced; it runs through the cut): one row per poll ----------
def parse_uptime(v):
    """RouterOS uptime ('00:01:20', '1d02:03:04', '1w00:00:10', '1m20s') -> s; None if unreadable."""
    v = (v or "").strip(); m = re.fullmatch(r"(?:(\d+)w)?(?:(\d+)d)?(\d+):(\d\d):(\d\d)", v)
    if m: return sum(int(x or 0) * k for x, k in zip(m.groups(), (604800, 86400, 3600, 60, 1)))
    u = re.findall(r"(\d+)([wdhms])", v)
    return sum(int(n) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[k] for n, k in u) if u else None


def parse_clock(date, tm):
    """RouterOS date ('2026-11-17' or 'nov/17/2026') + time -> epoch (local)."""
    fmt = "%Y-%m-%d" if re.match(r"\d{4}-", date) else "%b/%d/%Y"
    return time.mktime(time.strptime("%s %s" % (date.strip(), tm.strip()), fmt + " %H:%M:%S"))


def parse_router(text):
    """The last 3 lines of `:put date; :put time; :put uptime` -> (router epoch, uptime s); (None, None) if unreadable."""
    ls = [l for l in (text or "").splitlines() if l.strip()][-3:]
    try: return parse_clock(ls[0], ls[1]), parse_uptime(ls[2])
    except (IndexError, ValueError): return None, None


def mac_checks(rows):
    """Rows {mac, router, uptime, keeper (its HTTP Date), verified, modem_skew} -> [(status, name, detail)] against the Mac's clock."""
    if not rows: return [("INFO", "mac", "no samples (--log only)")]
    hm = lambda t: time.strftime("%H:%M:%S", time.localtime(t))
    sk = lambda d: "%+d s" % d if abs(d) < 120 else "%+d min" % round(d / 60)
    out = []
    for name, k, tol in (("router clock (vs Mac)", "router", 5), ("keeper clock (vs Mac)", "keeper", 5)):
        xs = [(r[k] - r["mac"], r["mac"]) for r in rows if r.get(k) is not None]
        if not xs: out.append(("FAIL", name, "never answered")); continue
        w = max(xs, key=lambda x: abs(x[0]))
        out.append(("PASS" if abs(xs[-1][0]) <= tol else "FAIL", name, "now %s; worst %s at Mac %s" % (sk(xs[-1][0]), sk(w[0]), hm(w[1]))))
    kr = [r for r in rows if r.get("keeper") is not None and r.get("verified") is not None]
    bad = [r for r in kr if r["verified"] and abs(r["keeper"] - r["mac"]) > 300]
    run, longest = None, 0
    for r in kr:                                       # unverified while its clock is right: from the first such poll to the next verdict
        if not r["verified"] and abs(r["keeper"] - r["mac"]) <= 300: run = run if run is not None else r["mac"]
        elif run is not None: longest = max(longest, r["mac"] - run); run = None
        longest = max(longest, r["mac"] - run) if run is not None else longest
    if bad: out.append(("FAIL", "keeper trust", "verified on a clock %s off at Mac %s" % (sk(bad[0]["keeper"] - bad[0]["mac"]), hm(bad[0]["mac"]))))
    else: out.append(("WARN" if longest > VERIFY_S else "PASS", "keeper trust",
                      "never verified on a wrong clock; unverified on a right one for %d s" % longest))
    up = next((r for r in rows if r.get("uptime") is not None), None); back = next((i for i, r in enumerate(rows) if r.get("router") is not None), None)
    if up:
        d = "router booted at %s Mac time" % hm(up["mac"] - up["uptime"])
        if back: d += "; answered again at %s (down from before %s)" % (hm(rows[back]["mac"]), hm(rows[0]["mac"]))
        out.append(("INFO", "boot (Mac time)", d))
    return out


def rebooted(rows):
    """A NEW router boot in the polls: it went silent and came back, or its uptime dropped (a short cut between two polls)."""
    down, last = False, None
    for r in rows:
        if r.get("router") is None: down = True; continue
        if down or (last is not None and r.get("uptime") is not None and r["uptime"] < last): return True
        last = r.get("uptime") if r.get("uptime") is not None else last
    return False


def done(rows):
    """After that boot: the router's and the keeper's clocks agree with the Mac (5 s) and the keeper trusts its clock."""
    r = rows[-1] if rows else {}
    return rebooted(rows) and r.get("verified") is True and all(r.get(k) is not None and abs(r[k] - r["mac"]) <= 5 for k in ("router", "keeper"))


def worst(res):
    sts = [st for st, n, d in res if st != "INFO"]
    return "FAIL" if "FAIL" in sts else "WARN" if "WARN" in sts else "PASS"


def report(res):
    return "\n".join("%-4s %-24s %s" % r for r in res) + "\n=> %s" % worst(res)


# ---------- I/O ----------
def fetch(host):
    try: p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "/log print without-paging"],
                            capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError): return None
    return p.stdout if p.returncode == 0 else None


COLS = ["mac", "router", "uptime", "keeper", "verified", "modem_skew"]


def sample(host, url):
    """One row, the Mac's time first: the router's clock + uptime (one ssh), the keeper's clock (its HTTP Date) + verdict."""
    row = dict.fromkeys(COLS); row["mac"] = time.time()
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", host,
                            ":put [/system clock get date]; :put [/system clock get time]; :put [/system resource get uptime]"],
                           capture_output=True, text=True, timeout=15)
        if p.returncode == 0: row["router"], row["uptime"] = parse_router(p.stdout)
    except (subprocess.TimeoutExpired, OSError): pass           # OSError: no ssh binary
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=5) as r:
            row["keeper"] = email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp()
            c = (json.loads(r.read().decode()).get("config") or {}).get("clock") or {}
            row.update(verified=c.get("verified"), modem_skew=c.get("modem_skew_s"))
    except Exception: pass
    return row


def keeper_now(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=10) as r: s = json.loads(r.read().decode())
        c = (s.get("config") or {}).get("clock") or {}
        return "keeper now: %s, clock verified %s (modem skew %s s)" % (s.get("time"), c.get("verified"), c.get("modem_skew_s"))
    except Exception as e: return "keeper now: no answer (%s)" % type(e).__name__


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--log"); a.add_argument("--host", default=HOST); a.add_argument("--wait", action="store_true")
    a.add_argument("--follow", type=float, default=0, help="minutes to keep logging until the keeper verified its clock")
    a = a.parse_args(argv)
    rows = []
    if a.log: text = open(a.log).read()
    else:
        out = os.path.join(BASE, "reports"); os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "powercut-%s.log" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        fh = open(path[:-4] + ".csv", "w", newline=""); w = csv.DictWriter(fh, COLS); w.writeheader()
        def poll():
            r = sample(a.host, URL); rows.append(r); w.writerow(r); fh.flush(); return r
        poll()
        if a.wait:                     # the router's log still holds the LAST boot until it reboots: wait for the new one
            while not rebooted(rows): print("%s  waiting for the router to reboot (cut the power now) ..." % time.strftime("%H:%M:%S")); time.sleep(5); poll()
        text = fetch(a.host) if rows[-1]["router"] is not None else None
        while text is None and a.wait:
            print("%s  router not answering yet ..." % time.strftime("%H:%M:%S")); time.sleep(5)
            text = fetch(a.host) if poll()["router"] is not None else None
        if text is None: raise SystemExit("no router log from %s" % a.host)
        end = time.time() + a.follow * 60
        while True:
            open(path, "w").write(text)
            if time.time() >= end or done(rows): break
            print("%s  logging (%s) ..." % (time.strftime("%H:%M:%S"), {n: st for st, n, d in checks(parse(text))})); time.sleep(15)
            poll(); text = fetch(a.host) or text
        poll(); fh.close()
        print("log: %s (+ .csv: Mac / router / keeper clocks per poll)" % path)
    res = checks(parse(text)) + mac_checks(rows); txt = report(res) + ("" if a.log else "\n" + keeper_now(URL))
    print(txt)
    if not a.log: open(path[:-4] + ".txt", "w").write(txt + "\n")
    return 1 if worst(res) == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
