# the keeper (python3 -m keeper, DEVICE=zte) against a fake ZTE (CWMP client + goform web) and a fake operator ACS
import csv, http.client, http.server, importlib.util, json, os, re, shutil, subprocess, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18101"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-py"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
json.dump({"last_checkin": time.time()}, open(DATA + "/state.json", "w"))          # check-in not due yet
open(DATA + "/samples.csv", "w").write("time,uptime_s,cell,sinr\n2026-10-26 15:11:42,651,636576/127,7.5\n")   # left over from v1

op = {"seen": [], "auth": [], "cookie": []}                                                      # fake operator ACS
class Op(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        b = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode(); op["seen"].append(b); op["auth"].append(self.headers.get("Authorization")); op["cookie"].append(self.headers.get("Cookie"))
        def send(code, body=b"", close=False, extra=()):
            self.send_response(code); [self.send_header(*h) for h in extra]
            self.send_header("Content-Length", str(len(body)))
            if close: self.send_header("Connection", "close"); self.close_connection = True
            self.end_headers(); self.wfile.write(body)
        if "Inform>" in b and not self.headers.get("Authorization"):
            return send(401, close=True, extra=[("WWW-Authenticate", 'Digest realm="acs"')])
        if "Inform>" in b: return send(200, b"<x><ID>9</ID><cwmp:InformResponse/></x>", extra=[("Set-Cookie", "s=1")])
        W = '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0"><s:Header><cwmp:ID>%s</cwmp:ID></s:Header><s:Body>%s</s:Body></s:Envelope>'
        if not b.strip(): return send(200, (W % ("op-gpv", "<cwmp:GetParameterValues>OPERATOR</cwmp:GetParameterValues>")).encode())
        if "GetParameterValuesResponse" in b:      # wants to change the interval
            return send(200, (W % ("op-spv", "<cwmp:SetParameterValues><ParameterList><ParameterValueStruct><Name>Device.ManagementServer.PeriodicInformInterval</Name>"
                                    "<Value>3600</Value></ParameterValueStruct><ParameterValueStruct><Name>Device.ManagementServer.ConnectionRequestPassword</Name>"
                                    "<Value>opsecret</Value></ParameterValueStruct></ParameterList></cwmp:SetParameterValues>")).encode())
        if ">op-spv<" in b and "9001" in b:          # denied -> offers firmware
            return send(200, (W % ("op-dl", "<cwmp:Download><FileType>1 Firmware Upgrade Image</FileType><URL>http://fw.example/B12.bin</URL>"
                                   "<FileSize>123</FileSize><TargetFileName>B12.bin</TargetFileName></cwmp:Download>")).encode())
        return send(204)
ops = http.server.ThreadingHTTPServer(("127.0.0.1", 18102), Op); threading.Thread(target=ops.serve_forever, daemon=True).start()

env = dict(os.environ, NIGHT_SCREEN="off", MIN_SAMPLE_S="0", ZTE_HOST="127.0.0.1:18101", ZTE_PASS_HASH=m.S(m.PW), PORT="18100", INFORM_INTERVAL="60", DATA=DATA, CPE_READY_TRIES="1", CPE_READY_GAP_S="0",
           CHECKIN_DAYS="30", YETTEL_URL="http://127.0.0.1:18102/acs/bulgaria", RAW="1")
p = subprocess.Popen([sys.executable, "-m", "keeper"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(0.8)
NS = "urn:dslforum-org:cwmp-1-2"
def env_(body, cid="z1"): return ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="%s"><s:Header><cwmp:ID>%s</cwmp:ID></s:Header>'
                                  '<s:Body>%s</s:Body></s:Envelope>' % (NS, cid, body))
def inform(ev): return env_('<cwmp:Inform><Event><EventStruct><EventCode>%s</EventCode></EventStruct></Event><CurrentTime>%s</CurrentTime><ParameterList>'
                            '<ParameterValueStruct><Name>Device.DeviceInfo.SoftwareVersion</Name><Value>B11</Value></ParameterValueStruct></ParameterList></cwmp:Inform>' % (ev, time.strftime("%Y-%m-%dT%H:%M:%S-02:00")), "inf-77")
def values(pci, interval, sinr, fota="version_idle", upm="0"):
    v = {"Device.Cellular.Interface.1.CurrentAccessTechnology": "NR5G", "Device.Cellular.Interface.1.NR5G_ARFCN": "636576",
         "Device.Cellular.Interface.1.NR5G_PCI": pci, "Device.Cellular.Interface.1.NR5G_SINR": sinr, "Device.Cellular.Interface.1.LTE_PCI": "340",
         "Device.Cellular.Interface.1.LTE_EARFCN": "3350", "Device.Cellular.Interface.1.LTE_SINR": "1",
         "Device.Cellular.Interface.1.LTE_CA.1.Band": "LTE BAND 7", "Device.Cellular.Interface.1.LTE_CA.1.DL_channel": "3350",
         "Device.Cellular.Interface.1.LTE_CA.1.PCI": "340", "Device.Cellular.Interface.1.LTE_CA.1.RSRP": "-91", "Device.Cellular.Interface.1.LTE_CA.1.SINR": "1",
         "Device.Cellular.Interface.1.LTE_CA.2.Band": "3", "Device.Cellular.Interface.1.LTE_CA.2.DL_channel": "1550",
         "Device.Cellular.Interface.1.LTE_CA.2.PCI": "340", "Device.Cellular.Interface.1.LTE_CA.2.RSRP": "-90", "Device.Cellular.Interface.1.LTE_CA.2.SINR": "8",
         "Device.Cellular.Interface.1.NR_CA.1.PCI": "747", "Device.Cellular.Interface.1.NR_CA.1.DL_channel": "629988", "Device.Cellular.Interface.1.NR_CA.1.RSRP": "-90.7",
         "Device.Cellular.Interface.1.NR_CA.1.SINR": "255", "Device.Cellular.Interface.1.NR_CA.1.DL_bandwidth": "100MHz", "Device.Cellular.Interface.1.NR_CA.2.PCI": "534", "Device.Cellular.Interface.1.NR_CA.2.DL_channel": "636576", "Device.Cellular.Interface.1.NR_CA.3.PCI": "",
         "Device.Cellular.Interface.1.LTE_NeighbourCellEARFCN": "3350;1550", "Device.Cellular.Interface.1.LTE_NeighbourCellPCI": "202;66",
         "Device.Cellular.Interface.1.LTE_NeighbourCellRSRP": "-97;-89", "Device.DeviceInfo.UpTime": "120",
         "Device.ThermSensor.1.ThermSensorTemp": "22", "Device.ThermSensor.2.ThermSensorTemp": "26",
         "Device.ManagementServer.PeriodicInformInterval": interval, "Device.ManagementServer.PeriodicInformEnable": "1",
         "Device.ManagementServer.UpgradesManaged": upm, "Device.FOTA.State": fota, "Device.FOTA.UpgradeState": "fota_idle",
         "Device.FOTA.NewVersion": "YETTEL_BG_MC889V2.0.0B07", "Device.DeviceInfo.SoftwareVersion": "YETTEL_BG_MC889V2.0.0B11"}
    return env_("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>"
                % "".join("<ParameterValueStruct><Name>%s</Name><Value>%s</Value></ParameterValueStruct>" % kv for kv in v.items()), "gpv-1")
def session(ev, pci, interval, sinr="6", auth=None, fota="version_idle", upm="1"):
    c = http.client.HTTPConnection("127.0.0.1", 18100); out = []
    def post(b, h={}):
        c.request("POST", "/", b.encode(), dict({"Content-Type": "text/xml"}, **h)); r = c.getresponse(); d = r.read().decode(); out.append((r.status, d)); return r.status, d
    s, b = post(inform(ev), {"Authorization": auth} if auth else {})
    if s == 401:      # operator challenge seen through the relay: answer it like the ZTE would
        c.close(); c = http.client.HTTPConnection("127.0.0.1", 18100); s, b = post(inform(ev), {"Authorization": "Digest username=x"})
    s, b = post("")
    if "GetParameterValues>" in b and "OPERATOR" not in b: s, b = post(values(pci, interval, sinr, fota, upm))
    elif "OPERATOR" in b: s, b = post(env_("<cwmp:GetParameterValuesResponse/>"))
    if "SetParameterValues" in b:
        assert ">60</Value>" in b or "UpgradesManaged" in b, b
        if upm == "0": assert "<Name>Device.ManagementServer.UpgradesManaged</Name><Value xsi:type=\"xsd:boolean\">1</Value>" in b, b
        s, b = post(env_("<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>"))
    assert s == 204, (s, b); return out

o = session("1 BOOT", "534", "3600", upm="0")                        # boot: InformResponse echoes the ID, read, set interval 60
assert "<cwmp:ID soap-env:mustUnderstand=\"1\">inf-77</cwmp:ID>" in o[0][1] and "InformResponse" in o[0][1], o[0]
assert "Device.Cellular.Interface.1." in o[1][1]
time.sleep(1.5)
assert [s[1] for s in m.st["sets"]] == ["WAN_ANT_SWITCH_SET", "BSP_ANTENNA_STATE_SET", "BSP_ANTENNA_STATE_SET", "LOGOUT"], m.st["sets"]
session("2 PERIODIC", "786", "60", "-1.5")                  # 5G switch, interval already right -> no SPV
assert len(m.st["sets"]) == 4                                # no web login without a boot
assert m.st["logins"] == 1
s = json.load(open(DATA + "/state.json")); s["last_checkin"] = 0; json.dump(s, open(DATA + "/state.json", "w"))
o = session("2 PERIODIC", "786", "60")                      # check-in due -> whole session relayed to the operator
assert any("OPERATOR" in d for _, d in o), o
assert op["auth"][0] is None and op["auth"][1] == "Digest username=x", op["auth"]
assert not any("SetParameterValues>" in d or "Download>" in d for _, d in o), o        # the ZTE never saw a change request
assert any(">op-dl<" in b and "9001" in b for b in op["seen"]) and op["cookie"][-1] == "s=1", (op["seen"][-1], op["cookie"])
assert json.load(open(DATA + "/state.json"))["last_checkin"] > time.time() - 60
session("2 PERIODIC", "534", "60", fota="version_downloading")   # next session is ours again; FOTA activity -> event
p.terminate(); out = p.communicate()[0]; print(out)
sys.path.insert(0, ROOT); from keeper import store
rows = [r for r in store.rows(DATA, "samples") if r.get("nr_cell")]; ev = store.rows(DATA, "events")
print(rows[0]); print([e["event"] for e in ev])
assert [r["nr_cell"] for r in rows] == ["636576/534", "636576/786", "636576/534"]
assert rows[0]["lte_ca"] == "B7/3350/340:-91/1 B3/1550/340:-90/8" and rows[0]["lte_neighbours"] == "3350/202:-97 1550/66:-89" and rows[0]["temps"] == "22/26" and rows[0]["nr_ca"] == "629988/747:-90.7/@100"
assert [e["event"] for e in ev] == ["boot 1 BOOT", "applied", "switch", "operator wanted (denied)", "operator wanted (denied)", "operator check-in done", "FOTA state", "switch"], ev
assert "PeriodicInformInterval=3600" in ev[3]["to"] and "opsecret" not in ev[3]["to"] and "fw.example/B12.bin" in ev[4]["to"], ev[3:5]
assert ev[6]["from"].startswith("version_idle/") and ev[6]["to"].startswith("version_downloading/"), ev[6]
assert ev[2]["from"] == "636576/534" and ev[2]["to"] == "636576/786" and ev[2]["nr_sinr_before"] == "6"
assert any(r.get("cell") == "636576/127" for r in store.rows(DATA, "samples")) and os.path.exists(DATA + "/samples.csv")   # v1 file imported, left in place
assert "Digest" not in open(DATA + "/tr069-raw.log").read()                      # auth headers never logged
assert "firmware YETTEL_BG_MC889V2.0.0B11" in out and "B07 (not newer than installed: no update)" in out, out
print("OK run_test_py")
