# tools/powercut.py: after a power cut, the router's log (memory, since its boot: RouterOS + the keeper's container lines) mapped to
# real time with the 'ntp change time A => B' line, then the checks that matter: the keeper flagged the wrong clock, verified it
# soon after NTP, blamed the right clock, and took no time-of-day action meanwhile. Fixture: the real 3rd cut of 2026-11-17.
import importlib.util, os
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
spec = importlib.util.spec_from_file_location("powercut", os.path.join(ROOT, "tools", "powercut.py"))
pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
LOG = open(os.path.join(ROOT, "test", "fixtures", "routeros-powercut-2026-11-17.log")).read()

lines = pc.parse(LOG)
assert len(lines) == 55 and lines[0]["stamp"] == "2026-11-17 14:51:58" and lines[0]["topics"] == "system,error,critical", lines[0]
tl = pc.timeline(lines)
assert tl["offset"] == 5184 and tl["power"] and tl["ntp_at"] == "2026-11-17 14:53:31", tl        # 14:53:31 -> 16:19:55
assert pc.real(lines[-2], tl) == pc.real(lines[-1], tl) - 54                                         # NTP line vs the keeper's verify
by = {n: (st, d) for st, n, d in pc.checks(lines)}
assert all(st in ("PASS", "INFO") for st, d in by.values()), by
assert "10 s after the container" in by["keeper start"][1], by["keeper start"]
assert by["ntp"][0] == "PASS" and "+86 min" in by["ntp"][1] and "15 s after INTERNET" in by["ntp"][1], by["ntp"]
assert by["flagged"][0] == "PASS" and "16 s before the NTP step" in by["flagged"][1], by["flagged"]
assert by["verified"][0] == "PASS" and "54 s after the NTP step" in by["verified"][1], by["verified"]
assert by["blame"][0] == "PASS" and by["no time-of-day actions"][0] == "PASS", by
assert by["wrong stamps"] == ("INFO", "2026-11-17 14:51:58 .. 14:53:31 (93 s) carry the router's old time: add 86 min"), by["wrong stamps"]
assert pc.worst(pc.checks(lines)) == "PASS"

# the failures it must catch: no flag before NTP, a capacity test on the wrong clock, a late verify, the modem blamed
bad = LOG.replace(" 2026-11-17 14:53:15 container,info,debug zte-keeper: 2026-11-17 14:53:15 clock not verified", " 2026-11-17 14:53:15 container,info,debug zte-keeper: 2026-11-17 14:53:15 (gone)")
bad = bad.replace("16:20:49 container,info,debug zte-keeper: 2026-11-17 16:20:49 clock verified again (modem +0 min)",
                  "16:23:00 container,info,debug zte-keeper: 2026-11-17 16:23:00 clock verified by the internet's time (HTTP Date): ours was right, the modem's was off")
bad = bad.replace(" 2026-11-17 14:53:16 interface,info ether5 detect INTERNET",
                  " 2026-11-17 14:53:16 interface,info ether5 detect INTERNET\n 2026-11-17 14:53:20 container,info,debug zte-keeper: 2026-11-17 14:53:20 capacity test (iperf3 a:1): down 150 Mbit/s")
b = {n: st for st, n, d in pc.checks(pc.parse(bad))}
assert b["flagged"] == b["no time-of-day actions"] == b["verified"] == b["blame"] == "FAIL", b
assert pc.worst(pc.checks(pc.parse(bad))) == "FAIL"

# a reboot with the clock right (no NTP step): nothing to flag, nothing to verify
ok = "\n".join(l for l in LOG.splitlines() if "ntp change time" not in l and "clock " not in l)
c = {n: st for st, n, d in pc.checks(pc.parse(ok))}
assert c["ntp"] == "INFO" and c["flagged"] == c["verified"] == c["blame"] == "PASS" and "wrong stamps" not in c, c
# the 2026-11-17 19:31 test cut (keeper i): RouterOS stamped the ntp line with the OLD time -> the step's real time is the message's
# '=> B', never the line's stamp (the script said 'FAIL verified 11594 s after the NTP step' for a 48-s verify)
L2 = pc.parse(open(os.path.join(ROOT, "test", "fixtures", "routeros-powercut-2026-11-17b.log")).read())
r2 = {n: (st, d) for st, n, d in pc.checks(L2)}
assert r2["ntp"] == ("PASS", "stepped +192 min (16:21:31 -> 19:33:57), 14 s after INTERNET"), r2["ntp"]
assert r2["verified"] == ("PASS", "48 s after the NTP step"), r2["verified"]
assert r2["flagged"][0] == r2["blame"][0] == r2["no time-of-day actions"][0] == "PASS" and pc.worst(pc.checks(L2)) == "PASS", r2

# WAN outages, each on its own; for the boot it starts at the boot
assert by["wan"] == ("INFO", "down from the boot 14:51:58 -> IP 14:53:14 (+76 s), INTERNET 14:53:16 (+78 s)"), by["wan"]
assert by["clock in WAN outages"][0] == "PASS", by["clock in WAN outages"]

# a modem reboot (lock change) with the router up: no router boot, its clock right; the WAN down ~70 s; no clock alarm in it
MR = open(os.path.join(ROOT, "test", "fixtures", "routeros-modem-reboot-2026-11-17.log")).read()
m = {n: (st, d) for st, n, d in pc.checks(pc.parse(MR))}
assert pc.worst(pc.checks(pc.parse(MR))) == "PASS" and m["keeper start"] == ("INFO", "not in this log (no router boot)"), m
assert m["wan"] == ("INFO", "down 17:26:59 -> IP 17:28:08 (+69 s), INTERNET 17:28:11 (+72 s)"), m["wan"]
assert m["clock in WAN outages"] == ("PASS", "no clock alarm in 1 WAN outage(s) without a router boot"), m["clock in WAN outages"]
alarm = MR.replace(" 2026-11-17 17:28:07 container", " 2026-11-17 17:27:30 container,info,debug zte-keeper: 2026-11-17 17:27:30 clock not verified: x\n 2026-11-17 17:28:07 container")
x = {n: (st, d) for st, n, d in pc.checks(pc.parse(alarm))}
assert x["clock in WAN outages"][0] == "FAIL" and "17:27:30" in x["clock in WAN outages"][1], x["clock in WAN outages"]

# --- the Mac's clock as the reference (NTP-synced, it runs through the cut): one row per poll ---
import time
assert pc.parse_uptime("00:01:20") == 80 and pc.parse_uptime("1d02:03:04") == 93784 and pc.parse_uptime("1w00:00:10") == 604810
assert pc.parse_uptime("1m20s") == 80 and pc.parse_uptime("") is None
T0 = time.mktime(time.strptime("2026-11-17 16:18:00", "%Y-%m-%d %H:%M:%S"))                      # Mac time when the poll began
assert pc.parse_clock("2026-11-17", "16:20:10") == T0 + 130 and pc.parse_clock("nov/17/2026", "16:20:10") == T0 + 130
assert pc.parse_router(":put output\n2026-11-17\n14:52:20\n00:00:22\n") == (T0 - 5140, 22)          # date, time, uptime lines
OFF = 5184
rows = [dict(mac=T0, router=None, uptime=None, keeper=None, verified=None, modem_skew=None),                 # router down
        dict(mac=T0 + 30, router=T0 + 30 - OFF, uptime=22, keeper=None, verified=None, modem_skew=None),       # back, old clock
        dict(mac=T0 + 90, router=T0 + 90 - OFF, uptime=82, keeper=T0 + 90 - OFF, verified=False, modem_skew=OFF),
        dict(mac=T0 + 150, router=T0 + 150, uptime=142, keeper=T0 + 151, verified=False, modem_skew=1),        # NTP stepped it
        dict(mac=T0 + 210, router=T0 + 210, uptime=202, keeper=T0 + 210, verified=True, modem_skew=1)]
by = {n: (st, d) for st, n, d in pc.mac_checks(rows)}
assert by["router clock (vs Mac)"] == ("PASS", "now +0 s; worst -86 min at Mac 16:18:30"), by["router clock (vs Mac)"]
assert by["keeper clock (vs Mac)"][0] == "PASS" and "worst -86 min at Mac 16:19:30" in by["keeper clock (vs Mac)"][1], by
assert by["keeper trust"] == ("PASS", "never verified on a wrong clock; unverified on a right one for 60 s"), by["keeper trust"]
assert by["boot (Mac time)"] == ("INFO", "router booted at 16:18:08 Mac time; answered again at 16:18:30 (down from before 16:18:00)"), by["boot (Mac time)"]
bad = rows[:3] + [dict(rows[2], mac=T0 + 120, verified=True)] + [dict(r, verified=False) for r in rows[3:]] + \
      [dict(rows[4], mac=T0 + 400, router=T0 + 400, keeper=T0 + 400, verified=False)]
b = {n: (st, d) for st, n, d in pc.mac_checks(bad)}
assert b["keeper trust"][0] == "FAIL" and "verified on a clock -87 min off at Mac 16:20:00" in b["keeper trust"][1], b["keeper trust"]
w = {n: st for st, n, d in pc.mac_checks([dict(r, verified=False) for r in rows[3:]] + [dict(rows[4], mac=T0 + 400, router=T0 + 400, keeper=T0 + 400, verified=False)])}
assert w["keeper trust"] == "WARN", w
assert pc.mac_checks([]) == [("INFO", "mac", "no samples (--log only)")]
# --wait waits for a NEW boot: the old boot's log already PASSes (2026-11-17 19:31: started before the cut, it read the 16:18 boot's
# log and stopped after 3 s). New boot = the router went silent and came back, or its uptime dropped (a 10-s cut between polls)
up = lambda mac, upt, rt=True: dict(mac=mac, router=mac if rt else None, uptime=upt if rt else None, keeper=mac if rt else None, verified=True if rt else None, modem_skew=1)
assert not pc.rebooted([up(T0, 11594), up(T0 + 3, 11598)])                                    # the 2026-11-17 19:31 run: no boot yet
assert pc.rebooted([up(T0, 11594), up(T0 + 5, 0, rt=False), up(T0 + 60, 40)])                  # silent, back
assert pc.rebooted([up(T0, 11594), up(T0 + 5, 3)])                                             # uptime dropped between two polls
assert pc.rebooted([up(T0, 0, rt=False), up(T0 + 60, 40)])                                     # started during the cut
assert not pc.rebooted([up(T0, 0, rt=False), up(T0 + 5, 0, rt=False)])                         # still down
# done = after that boot, the router's and the keeper's clocks agree with the Mac and the keeper trusts its clock
assert not pc.done([up(T0, 11594), up(T0 + 3, 11598)])                                          # all fine, but no boot yet
assert not pc.done([up(T0, 11594), up(T0 + 5, 0, rt=False), dict(up(T0 + 60, 40), router=T0 + 60 - 11400, verified=False)])  # old clock
assert not pc.done([up(T0, 11594), up(T0 + 5, 0, rt=False), dict(up(T0 + 90, 70), verified=False)])                         # not verified yet
assert pc.done([up(T0, 11594), up(T0 + 5, 0, rt=False), dict(up(T0 + 90, 70), verified=False), up(T0 + 150, 130)])

# sample(), real body: the keeper's clock from its HTTP Date + its verdict; an unreachable router (or no ssh binary) is just None
import http.server, json, threading
class K(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({"config": {"clock": {"verified": True, "modem_skew_s": -1.5}}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
os.environ.update(NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
srv = http.server.HTTPServer(("127.0.0.1", 0), K); threading.Thread(target=srv.serve_forever, daemon=True).start()
r = pc.sample("nobody@keeper-test.invalid", "http://127.0.0.1:%d" % srv.server_port)
assert r["router"] is None and r["verified"] is True and r["modem_skew"] == -1.5 and abs(r["keeper"] - r["mac"]) < 3, r
path0 = os.environ["PATH"]; os.environ["PATH"] = "/nonexistent"
try:
    r = pc.sample("admin@192.168.6.1", "http://127.0.0.1:%d" % srv.server_port); assert r["router"] is None and r["verified"] is True, r
    assert pc.fetch("admin@192.168.6.1") is None
finally: os.environ["PATH"] = path0
print("OK powercut")
