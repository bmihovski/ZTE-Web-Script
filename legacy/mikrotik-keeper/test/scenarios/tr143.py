# TR-143 probe: once per keeper start, at the end of a session, the ZTE is asked GetParameterNames(Device.IP.Diagnostics.);
# the answer (names) or a Fault is stored in state['tr143'] and shown in /status - it decides how a capacity test can run.
import os, shutil, sys
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-tr143"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import acs, brain, common
from keeper.devices import zte as z
assert acs.reboot_if_pending() == (None, False)                                         # not in the first 10 min (boot sessions)
z.probe["after"] = 0
ENV = ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">'
       '<soap:Header><cwmp:ID soap:mustUnderstand="1">x</cwmp:ID></soap:Header><soap:Body>%s</soap:Body></soap:Envelope>')
body, echo = acs.reboot_if_pending()
assert "<cwmp:GetParameterNames>" in body and "<ParameterPath>Device.IP.Diagnostics.</ParameterPath>" in body and "<NextLevel>false" in body, body
assert acs.reboot_if_pending() == (None, False)                                         # once per start
names = ["Device.IP.Diagnostics.", "Device.IP.Diagnostics.DownloadDiagnostics.DownloadURL", "Device.IP.Diagnostics.DownloadDiagnostics.DiagnosticsState",
         "Device.IP.Diagnostics.UploadDiagnostics.UploadURL"]
resp = "<cwmp:GetParameterNamesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterNamesResponse>" % "".join(
    "<ParameterInfoStruct><Name>%s</Name><Writable>1</Writable></ParameterInfoStruct>" % n for n in names)
brain.ctx["pending"] = "reboot"                                                         # a queued reboot still goes out after the answer
reply, _ = acs.handle(ENV % resp)
assert "<cwmp:Reboot>" in reply, reply
t = common.state()["tr143"]; assert t["supported"] and t["n"] == 4 and names[1] in t["names"], t
assert z.config()["tr143"]["supported"]
brain.ctx["pending"] = "reboot"; z.probe.update(want=True, sent=False)
assert "<cwmp:Reboot>" in acs.reboot_if_pending()[0] and z.probe["want"]                # a queued reboot goes first, the probe waits
brain.ctx["pending"] = None
# a ZTE without TR-143 answers with a Fault: recorded, no crash, the session just ends
z.probe.update(want=True, sent=False); brain.ctx["pending"] = None
acs.reboot_if_pending()
fault = ("<soap:Fault><faultcode>Client</faultcode><faultstring>CWMP fault</faultstring><detail><cwmp:Fault><FaultCode>9005</FaultCode>"
         "<FaultString>Invalid parameter name</FaultString></cwmp:Fault></detail></soap:Fault>")
reply, _ = acs.handle(ENV % fault)
t = common.state()["tr143"]; assert reply is None and t["supported"] is False and t["fault"] == "9005 Invalid parameter name", (reply, t)
# busy (a lock test / write): the probe waits for a quiet session
z.probe.update(want=True, sent=False); common.register_busy("test", lambda: True)
assert acs.reboot_if_pending() == (None, False) and z.probe["want"]
print("OK test_tr143")
