# capacity test through the real CWMP path: the ACS reads the running test's object, the TR-143 write goes out in the next
# session (whose read still shows the PREVIOUS test's result - ignored), "Requested", then the result; the shaper's ceiling
# follows the learned value for the period; the ceiling message is a Telegram rule.
import os, re, shutil, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-captest"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import acs, alert, captest as c, common, sqm
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
t = threading.Thread(target=lambda: out.update(r=c._one(c.DL, c.down_params("speedtest1.yettel.bg:8080")))); t.start()
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
common.state({"captest_hist": [{"slot": "18:30", "down": 140.0, "up": 22.0}]})
assert sqm.CEILING[0]("down", T) == 128.8 and sqm.CEILING[0]("up", T) == sqm.MAX["up"]                       # upload: SQM_MAX_UP (CAPTEST_UP=0)
c.UP = True; assert sqm.CEILING[0]("up", T) == 20.2; c.UP = False                                             # learned only with CAPTEST_UP=1
assert sqm.step({"up": 20.0, "down": 128.0}, {"up": 18, "down": 120}, 1, {"up": 20.2, "down": 128.8}) == {"up": 20.2, "down": 128.8}   # +5 % stops at the ceiling
assert sqm.step({"up": 20.0, "down": 128.0}, {"up": 18, "down": 120}, 1)["down"] == 134.4                                           # no cap given: env max as before
# the minute job waits while a quality A/B trial runs
common.state({"quality_ab": {"phase": "trial"}}); assert not c.maybe_test({}, T)
print("OK test_captest")
