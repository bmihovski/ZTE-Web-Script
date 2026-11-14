# capacity test through the real CWMP path: the ACS reads the running test's object, the TR-143 write goes out in the next
# session (whose read still shows the PREVIOUS test's result - ignored), "Requested", then the result; the shaper's ceiling
# follows the learned value for the period; the ceiling message is a Telegram rule.
import os, re, shutil, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-captest"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import acs, alert, brain, captest as c, common, sqm
from keeper.devices import zte as z
ENV = ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">'
       '<soap:Header><cwmp:ID soap:mustUnderstand="1">x</cwmp:ID></soap:Header><soap:Body>%s</soap:Body></soap:Envelope>')
def gpvr(vals):
    return ENV % ("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>" % "".join(
        "<ParameterValueStruct><Name>%s</Name><Value>%s</Value></ParameterValueStruct>" % kv for kv in vals.items()))
z.probe["want"] = False                                                          # no TR-143 names probe in this test
# wiring
assert c.extra_read in acs.hooks["read"] and c.on_values in acs.hooks["values"] and sqm.CEILING[0] is c.ceiling
assert c.maybe_test in z.minute_jobs() and any(re.search(rx, "capacity ceiling") for rx, k in alert.RULES)
# the read: the test's object only while a test runs
acs.st["step"] = "inform"; body, _ = acs.handle(""); assert c.DL not in body
c.st.update(running=True, base=c.DL)
acs.st["step"] = "inform"; body, _ = acs.handle(""); assert "<string>%s</string>" % c.DL in body, body
# one download test end to end through acs.handle
acs.tr["last_sample"] = time.monotonic() + 3600                                  # these reads make no sample rows
iv = {"Device.ManagementServer.PeriodicInformInterval": str(acs.FAST_INTERVAL), "Device.ManagementServer.PeriodicInformEnable": "1"}
out = {}
t = threading.Thread(target=lambda: out.update(r=c._one(c.DL, c.down_params("speedtest1.yettel.bg:8080"))), daemon=True); t.start()
for i in range(100):
    if acs.tr["writes"]: break
    time.sleep(0.02)
stale = dict(iv, **{c.DL + "DiagnosticsState": "Completed", c.DL + "ROMTime": "2026-11-13T04:30:00Z",
                    c.DL + "TestBytesReceivedUnderFullLoading": "1", c.DL + "PeriodOfFullLoading": "1"})
body, _ = acs.handle(gpvr(stale))                                                # this session carries our write ...
assert "<cwmp:SetParameterValues>" in body and "random4000x4000.jpg" in body and "Requested" in body, body
assert not c.st["ev"].is_set()                                                   # ... and its read of the old result is ignored
acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>")
acs.handle(gpvr(dict(iv, **{c.DL + "DiagnosticsState": "Requested"})))
assert not c.st["ev"].is_set()
acs.handle(gpvr(dict(iv, **{c.DL + "DiagnosticsState": "Completed", c.DL + "ROMTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            c.DL + "TestBytesReceivedUnderFullLoading": "126501460", c.DL + "PeriodOfFullLoading": "5460000"})))
t.join(5); assert out["r"] == (185.4, "full load 5.5 s"), out
c.st.update(running=False, base=None)
# the shaper follows the learned ceiling of the period; without data the env max
T = time.mktime((2026, 11, 13, 19, 0, 0, 0, 0, -1))
assert sqm.CEILING[0]("down", T) == sqm.MAX["down"]
common.state({"captest_hist": [{"slot": "18:30", "down": 140.0, "up": 22.0, "down_how": "full load 5.5 s", "up_how": "full load 8.0 s"}]})
assert sqm.CEILING[0]("down", T) == sqm.MAX["down"]                                  # one test is not enough to replace your ceiling
common.state({"captest_hist": common.state()["captest_hist"] * 2})
assert sqm.CEILING[0]("down", T) == 128.8 and sqm.CEILING[0]("up", T) == sqm.MAX["up"]                       # upload: SQM_MAX_UP (CAPTEST_UP=0)
c.UP = True; assert sqm.CEILING[0]("up", T) == 20.2; c.UP = False                                             # learned only with CAPTEST_UP=1
assert sqm.step({"up": 20.0, "down": 128.0}, {"up": 18, "down": 120}, 1, {"up": 20.2, "down": 128.8}) == {"up": 20.2, "down": 128.8}   # +5 % stops at the ceiling
assert sqm.step({"up": 20.0, "down": 128.0}, {"up": 18, "down": 120}, 1)["down"] == 134.4                                           # no cap given: env max as before
# the minute job waits while a quality A/B trial runs
common.state({"quality_ab": {"phase": "trial"}}); assert not c.maybe_test({}, T)
# API: POST /captest runs one now (not while one runs), GET /captest shows the last result with the raw values
from keeper import api
assert api.capability("post", "/captest") == "captest" and api.capability("get", "/captest") == "captest" and "captest" in z.CAN
calls = []; real = c.run; c.run = lambda slot, now, forced=False, url=None, conn=None, secs=None: calls.append((slot, forced, url, conn, secs))
assert c.force(mode="tr143") and calls and calls[-1][1] is True
c.st["running"] = False
assert c.force(url="http://proof.ovh.net/files/100Mb.dat") and calls[-1][2] == "http://proof.ovh.net/files/100Mb.dat"
c.st["running"] = False
assert c.down_params("h", "http://x/y")[0][2] == "http://x/y"
c.st["running"] = False; assert c.force(mode="tr143", conn=1, secs=10) and calls[-1][3:] == (1, 10)
p = dict((k.split(".")[-1], v) for k, t, v in c.down_params("h", conn=1, secs=10)); assert p["NumberOfConnections"] == "1" and p["TimeBasedTestDuration"] == "10"
_G = {"slot": "18:30", "down": 150.0, "down_how": "full load 5.0 s"}
assert c.ceilings([_G, _G, dict(_G, diag={"conn": 1}, down=5.0)], "18:30")["down"] == 138.0   # diagnostics don't count
G = {"slot": "18:30", "down": 150.0, "down_how": "full load 5.0 s"}
assert c.ceilings([G, dict(G, url="http://x/y", down=10.0)], "18:30") == {} and c.ceilings([G, G], "18:30")["down"] == 138.0   # diagnostics don't count
c.st["running"] = True; assert not c.force(); c.st["running"] = False; c.run = real
common.state({"captest_last": {"rec": {"down": 180.0}, "raw": {"down": {"DiagnosticsState": "Completed"}, "up": None}}})
i = c.info(); assert i["last"]["raw"]["down"]["DiagnosticsState"] == "Completed" and i["ceiling_now"]["up"] == sqm.MAX["up"] and "18:30" in i["ceilings_by_period"], i
assert "/captest" in str(api.openapi())
# one-off read of any subtree: in the next read only, values captured, then dropped
acs.peek.update(want="Device.IP.Diagnostics.Iperf3."); acs.st["step"] = "inform"; body, _ = acs.handle("")
assert "<string>Device.IP.Diagnostics.Iperf3.</string>" in body and acs.peek["want"] is None
acs.handle(gpvr(dict(iv, **{"Device.IP.Diagnostics.Iperf3.Status": "Idle", "Device.IP.Diagnostics.Iperf3.Mode": "Client"})))
assert acs.peek["got"] == {"Device.IP.Diagnostics.Iperf3.Status": "Idle", "Device.IP.Diagnostics.Iperf3.Mode": "Client"}, acs.peek
acs.st["step"] = "inform"; body, _ = acs.handle(""); assert "Iperf3" not in body                    # once only
assert api.capability("post", "/tr069/read") is None and c.ON      # scheduled tests on again (iperf3)
assert "TimeBasedTestDuration" in str(c.down_params("h")) and dict((k.split(".")[-1], v) for k, t, v in c.down_params("h"))["TimeBasedTestDuration"] == "0"
# iperf3 by the modem: settings written in the next session, every read kept, done when a result field changes
import threading as _th
c.st["running"] = True
th = _th.Thread(target=c.run_iperf, args=(time.time(), ["37.19.203.1:5201"], 10, 4, True, None, {"mode": "iperf3"}), daemon=True); th.start()
for i in range(100):
    if acs.tr["writes"]: break
    time.sleep(0.02)
I3 = c.IP3
body, _ = acs.handle(gpvr(dict(iv, **{I3 + "Enable": "0", I3 + "Status": "", I3 + "DownlinkSpeed": "", I3 + "DownlinkLastTest": ""})))
assert "<string>" not in body and "37.19.203.1" in body and I3 + "Enable" not in body, body                # the settings go out (no Enable)
acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>")
for i in range(100):                                                                                     # then Enable alone
    if acs.tr["writes"]: break
    time.sleep(0.02)
body, _ = acs.handle(gpvr(dict(iv, **{I3 + "Enable": "0", I3 + "Status": ""})))
assert I3 + "Enable" in body and "37.19.203.1" not in body, body
acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>")
for i in range(100):                                                                                     # the thread marks the start
    if c.st.get("ip3_started"): break
    time.sleep(0.02)
acs.handle(gpvr(dict(iv, **{I3 + "Enable": "1", I3 + "Status": "Requested", I3 + "DownlinkSpeed": ""})))
acs.handle(gpvr(dict(iv, **{I3 + "Enable": "0", I3 + "Status": "Complete", I3 + "DownlinkSpeed": "171.4Mbits/sec", I3 + "DownlinkLastTest": "13/11/2026 22/17"})))
th.join(5)
L = common.state()["captest_last"]; assert L["rec"]["down"] == 171.4 and L["rec"]["diag"]["mode"] == "iperf3" and len(L["raw"]["iperf3_reads"]) == 4, L
assert not c.st["running"] and c.valid(L["rec"], "down") and L["rec"]["down_how"].endswith("4 streams 10 s"), L["rec"]
# the scheduled / plain run counts; a custom one is a diagnostic; plain POST uses iperf3 by default
calls2 = []; real_ip = c.run_iperf; c.run_iperf = lambda *a: calls2.append(a)
c.st["running"] = False; assert c.force() and calls2[-1][1] == c.IPERFS and calls2[-1][6] is None          # plain: counts
c.st["running"] = False; assert c.force(server="1.2.3.4:5201") and calls2[-1][6]["server"] == "1.2.3.4:5201"   # custom: diagnostic
c.run_iperf = real_ip; c.st["running"] = False
assert c.ON and c.METHOD == "iperf3"
acs.tr["sent"] = [("x", "xsd:string", "1")]; acs.st["step"] = "set"
acs.handle(ENV % ('<soap:Fault><faultcode>Client</faultcode><faultstring>CWMP fault</faultstring><detail><cwmp:Fault><FaultCode>9003</FaultCode>'
                  '<FaultString>Invalid arguments</FaultString><SetParameterValuesFault><ParameterName>%sOmit</ParameterName><FaultCode>9007</FaultCode>'
                  '<FaultString>Invalid parameter value</FaultString></SetParameterValuesFault></cwmp:Fault></detail></soap:Fault>' % I3))
assert acs.tr["refused"] == [(I3 + "Omit", "9007", "Invalid parameter value")] and acs.tr["ok"] is False, acs.tr["refused"]
# stale result before our start (2026-11-13 22:40): the old Complete / 144 / 22:17 must never be taken
OLD = {I3 + "Enable": "0", I3 + "Status": "Complete", I3 + "DownlinkSpeed": "144Mbits/sec", I3 + "DownlinkLastTest": "13/11/2026 22/17"}
out2 = {}
th = _th.Thread(target=lambda: out2.update(r=c._iperf_once("37.19.203.1:5201", 10, 4, True)), daemon=True); c.st["running"] = True; th.start()
for i in range(100):
    if acs.tr["writes"]: break
    time.sleep(0.02)
acs.handle(gpvr(dict(iv, **OLD)))                                                                        # settings session
acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>")
for i in range(100):
    if acs.tr["writes"]: break
    time.sleep(0.02)
acs.handle(gpvr(dict(iv, **OLD)))                                                                        # Enable session: still old
assert not c.st["ev"].is_set(), "a stale result ended the wait"
acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>")
for i in range(100):
    if c.st.get("ip3_started"): break
    time.sleep(0.02)
acs.handle(gpvr(dict(iv, **OLD)))                                                                        # started, still old: wait
assert not c.st["ev"].is_set(), "an unchanged result after the start ended the wait"
acs.handle(gpvr(dict(iv, **dict(OLD, **{I3 + "DownlinkSpeed": "151Mbits/sec", I3 + "DownlinkLastTest": "13/11/2026 22/43"}))))   # new minute
th.join(5); assert out2["r"][0] == 151.0, out2
c.st["running"] = False
assert c.ceilings([{"slot": "21:30", "down": 144.0, "down_how": "x, 4 streams 10 s", "server": "iperf3 a"}] * 2, "21:30") == {}   # pre-v2: no
# scheduled run: download then the upload on the same server; the upload is shown but sets no ceiling (CAPTEST_UP=0)
fake = lambda srv, secs, conn, rev: ((190.0, "dl, 4 streams 10 s", [{"x": 1}]) if rev else (26.5, "ul, 4 streams 10 s", [{"y": 1}]))
real1, c._iperf_once = c._iperf_once, fake
common.state({"captest_hist": []}); c.st["running"] = True
c.run_iperf(time.time(), ["37.19.203.1:5201"], 10, 4, True, "07:30")
r = common.state()["captest_last"]["rec"]; assert r["down"] == 190.0 and r["up"] == 26.5 and r["up_how"] == "ul, 4 streams 10 s", r
c.run_iperf(time.time(), ["37.19.203.1:5201"], 10, 4, True, "07:30")
assert c.ceilings(common.state()["captest_hist"], "07:30") == {"down": 174.8, "up": 24.4}                     # learned ...
from keeper import store                                                                        # the Telegram message: no bare 'up -'
ev = [e for e in store.rows(DATA, "events") if e.get("event") == "capacity ceiling"][-1]
assert ev["to"] == "07:30: down 174.8 Mbit/s / up stays %g Mbit/s (measured 26.5)" % sqm.MAX["up"] and ev["why"].startswith("this test: down 190.0 / up 26.5"), ev
T7 = time.mktime((2026, 11, 14, 8, 0, 0, 0, 0, -1)); assert c.ceiling("down", T7) == 174.8 and c.ceiling("up", T7) == sqm.MAX["up"]   # ... applied: down only
# the upload fails on the download's server -> tried on the next one
calls3 = []
def fake3(srv, secs, conn, rev):
    calls3.append((srv, rev, conn))
    return (178.0, "dl, 4 streams 10 s", []) if rev else ((24.0, "ul, 4 streams 10 s", []) if srv.startswith("37.") else (None, "no speed", []))
c._iperf_once = fake3; c.st["running"] = True
c.run_iperf(time.time(), ["169.150.252.2:5201", "37.19.203.1:5201"], 10, 4, True, "07:30")
r = common.state()["captest_last"]["rec"]
assert calls3 == [("169.150.252.2:5201", True, 4), ("169.150.252.2:5201", False, 1), ("37.19.203.1:5201", False, 1)] and r["up"] == 24.0, (calls3, r)
c._iperf_once = real1; c.st["running"] = False
# night slots: allowed inside LOCK_PAUSE, but never right after a reboot; a lock test / night screen (busy) waits
assert "01:30" in c.SLOTS and "04:30" in c.SLOTS
N = time.mktime((2026, 11, 14, 1, 45, 0, 0, 0, -1)); common.state({"captest_done": {}, "quality_ab": None}); common.clock["ok"] = True
for i in range(12): brain.HIST.append({"down_bps": "0", "up_bps": "0"})
started = []; real_ip = c.run_iperf; c.run_iperf = lambda *a: started.append(a)
assert not c.maybe_test({"uptime_s": "300"}, N)                                          # 5 min after the 00:50 pause reboot: wait
assert c.maybe_test({"uptime_s": "3000"}, N) and started[-1][5] == "01:30"
c.st["running"] = False
# triggered tests (idle only): the setup changed and held 15 min, or the shaper asked; >= 1 h apart, none just before a slot, max a day
D = time.mktime((2026, 11, 14, 10, 0, 0, 0, 0, -1))                                      # 10:00 - the next slot (12:30) is far
common.state({"captest_setup": None, "captest_trig": [], "captest_last_t": 0}); c.st.update(want=None, seen_key=None, nr_last=None)
sqm.st["want_test"] = None
R = lambda lte, nr="no5G": {"uptime_s": "3000", "lte_cell": lte, "nr_cell": nr}
assert not c.maybe_test(R("100/340", "636576/534"), D) and common.state()["captest_setup"] == "100/340|636576/534"   # first: adopted
assert not c.maybe_test(R("3350/340"), D + 60) and c.st["seen_key"] == "3350/340|636576/534"   # new anchor; 5G idle keeps the last cell
n = len(started)
assert not c.maybe_test(R("3350/340"), D + 600)                                          # not stable for 15 min yet
assert c.maybe_test(R("3350/340"), D + 960) and len(started) == n + 1 and started[-1][5] == "07:30", started[-1]
assert c.st["trigger"] == "setup 100/340|636576/534 -> 3350/340|636576/534" and common.state()["captest_setup"] == "3350/340|636576/534"
c.st["running"] = False
sqm.want_test("down climbed to 165.4 (+10 % over the learned 150)", D + 1000)
assert not c.maybe_test(R("3350/340"), D + 1000)                                         # < 1 h after the last test: waits
assert c.maybe_test(R("3350/340"), D + 960 + 3600) and c.st["trigger"].startswith("down climbed")
c.st["running"] = False
sqm.want_test("down: the shaper cut below 70 % ...", D)
assert not c.maybe_test(R("3350/340"), time.mktime((2026, 11, 14, 11, 45, 0, 0, 0, -1)))      # 12:30 slot within 1 h: it will measure
common.state({"captest_trig": [D + 7200] * c.TRIG_MAX, "captest_last_t": 0})
assert not c.maybe_test(R("3350/340"), D + 5 * 3600) and c.st["want"]                     # 6 today: the reason waits for tomorrow
for i in range(12): brain.HIST.append({"down_bps": "9000000", "up_bps": "0"})
common.state({"captest_trig": []}); assert not c.maybe_test(R("3350/340"), D + 5 * 3600)  # busy line: never during your traffic
c.run_iperf = real_ip
# a counted record carries the setup and the reason; a diagnostic doesn't
c._iperf_once = lambda srv, secs, conn, rev: (150.0, "dl, 4 streams 10 s", []) if rev else (25.0, "ul, 1 streams 10 s", [])
c.st.update(running=True, setup_now="3350/340|636576/534", trigger="setup x -> y"); c.run_iperf(D, ["a:1"], 10, 4, True, "07:30")
r = common.state()["captest_last"]["rec"]; assert r["setup"] == "3350/340|636576/534" and r["trigger"] == "setup x -> y", r
c.st["running"] = True; c.run_iperf(D, ["a:1"], 10, 4, False, None, {"mode": "iperf3"})
assert "setup" not in common.state()["captest_last"]["rec"]
c._iperf_once = real1; c.st["running"] = False
assert set(c.info(D)["trigger"]) == {"pending", "setup_now", "setup_tested", "today", "max_a_day"}
sqm.st.update(last={"limit": {"down": 150.0, "up": 28.0}, "rate": {"down": 140.0, "up": 1.0}, "ceiling": {"down": 150.0, "up": 28.0},
                    "why": {"down": "at the ceiling 150", "up": "holding"}, "delay_ms": 2.0, "time": "10:00:00"}, climb={"down": {"x": 1.1025, "clean": 300}})
x = c.info(D)["explain"]                                                                  # the panel's 'why' for each direction
assert x["down"]["ceiling_from"].startswith("learned for the 07:30 period: 0.92 x the median") and x["down"]["why"] == "at the ceiling 150", x
assert "+10 %" in x["down"]["climb"] and "after 5 more clean busy min" in x["down"]["climb"] and x["delay_limit_ms"] == sqm.DELAY, x
assert x["up"]["ceiling_from"].startswith("upload learning off") if not c.UP else "learned" in x["up"]["ceiling_from"] or "not learned" in x["up"]["ceiling_from"]
print("OK test_captest")
