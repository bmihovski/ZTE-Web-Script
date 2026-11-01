# DEVICE=mikrotik: the keeper reads the ATL 5G over TR-069 (values of the real dump), stores samples as device "mikrotik" with the
# shared fields + mikrotik_*, ignores a ZTE pointed at it, refuses lock / test / antenna actions (monitoring + auto reboot only so far)
import csv, http.client, json, os, re, shutil, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-mt"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
sys.path.insert(0, ROOT); from keeper import store
vals = {r[0]: r[1] for r in csv.reader(open(os.path.join(here, "..", "fixtures", "mikrotik-dump.csv"))) if len(r) > 1 and r[0].startswith("Device.")}
p = subprocess.Popen([sys.executable, "-m", "keeper"], cwd=ROOT, env=dict(os.environ, NIGHT_SCREEN="off", PORT="18180", DATA=DATA, DEVICE="mikrotik",
                     LAT_TARGETS="off", MIN_SAMPLE_S="0", SCAN_MIN="0"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(1)
ENV = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0"><s:Header><cwmp:ID>1</cwmp:ID>'
       '</s:Header><s:Body>%s</s:Body></s:Envelope>')
INF = ENV % ('<cwmp:Inform><DeviceId><Manufacturer>%s</Manufacturer><ProductClass>%s</ProductClass></DeviceId><Event><EventStruct><EventCode>1 BOOT'
             '</EventCode></EventStruct></Event><CurrentTime>' + time.strftime("%Y-%m-%dT%H:%M:%S-02:00") + '</CurrentTime><ParameterList/></cwmp:Inform>')
def session(maker, product, values):
    c = http.client.HTTPConnection("127.0.0.1", 18180)
    c.request("POST", "/", (INF % (maker, product)).encode()); r = c.getresponse(); assert "InformResponse" in r.read().decode()
    c.request("POST", "/", b""); r = c.getresponse(); b = r.read().decode()
    if r.status == 204: return None
    assert "Device.Cellular.Interface.1." in b and "Device.FOTA." not in b, b                     # the ATL's read list
    body = ENV % ("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>" % "".join(
        "<ParameterValueStruct><Name>%s</Name><Value>%s</Value></ParameterValueStruct>" % kv for kv in values.items()))
    c.request("POST", "/", body.encode()); r = c.getresponse(); return r.status, r.read().decode()
def call(method, path):
    c = http.client.HTTPConnection("127.0.0.1", 18180); c.request(method, path, b"" if method == "POST" else None); r = c.getresponse()
    return r.status, json.loads(r.read())
try:
    assert session("ZTE", "MC889", {}) is None                                                      # the other device: left alone
    s, b = session("MikroTik", "ATLGM", vals)
    assert s == 204 or ("SetParameterValues" in b and "UpgradesManaged" not in b), b             # interval only, no ZTE firmware guard
    time.sleep(0.5)
    st = call("GET", "/status")[1]
    assert st["lte_anchor"] == "B7 3350/202 SINR 8.0" and st["nr_cell"] == "no5G", st
    assert call("POST", "/lock-test?rat=x")[0] == 400 and call("POST", "/lock-test?rat=nr&cells=534@700000")[0] == 400  # validated, never started
    assert call("POST", "/antenna-sweep")[0] == 409                                                                     # still ZTE-only
    # experiment 2: AT+QNWLOCK through a RouterOS script (TR-069 Download of a .alter file served by the keeper)
    s2, q = call("POST", '/mikrotik/at?cmd=AT%2BQNWLOCK%3D%22common%2F4g%22%2C1%2C3350%2C202&restart=1')
    assert s2 == 200 and 'input="AT+QNWLOCK=\\"common/4g\\",1,3350,202"' in q["script"] and "/interface lte enable" in q["script"], q
    assert call("POST", "/mikrotik/at?cmd=AT%2BCFUN%3D0")[0] == 400                                   # only cell-lock commands
    c = http.client.HTTPConnection("127.0.0.1", 18180)
    c.request("POST", "/", (INF % ("MikroTik", "ATLGM")).replace("1 BOOT", "2 PERIODIC").encode()); c.getresponse().read()
    c.request("POST", "/", b""); r = c.getresponse(); r.read()
    body = ENV % ("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>" % "".join(
        "<ParameterValueStruct><Name>%s</Name><Value>%s</Value></ParameterValueStruct>" % kv for kv in vals.items()))
    c.request("POST", "/", body.encode()); r = c.getresponse(); b = r.read().decode()
    if "SetParameterValues" in b:                                                                      # fast inform interval first
        c.request("POST", "/", (ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>").encode())
        r = c.getresponse(); b = r.read().decode()
    assert "<cwmp:Download>" in b and "3 Vendor Configuration File" in b and "/mikrotik/script-1.alter" in b, b
    c.request("POST", "/", (ENV % "<cwmp:DownloadResponse><Status>1</Status></cwmp:DownloadResponse>").encode()); assert c.getresponse().status == 204
    c2 = http.client.HTTPConnection("127.0.0.1", 18180); c2.request("GET", "/mikrotik/script-1.alter"); sc = c2.getresponse().read().decode()
    assert sc.startswith('/interface lte at-chat [find] input="AT+QNWLOCK=') and sc.endswith("enable [find]\n"), sc
    raw = call("GET", "/mikrotik/raw?prefix=X_MIKROTIK_LteCellLock")[1]
    assert raw["values"] == {"X_MIKROTIK_LteCellLock": ""} and raw["results"][-1]["download"] == "accepted", raw
    # guarded 5G lock on the ATL: set when idle as an AT script (+ LTE interface restart); the ATL runs it -> active; a boot -> set again
    assert call("POST", "/nr-lock?cell=569@636576") == (200, {"nr_lock": call("GET", "/status")[1]["config"]["nr_lock"]})
    def sess(events="2 PERIODIC", done=False):
        c = http.client.HTTPConnection("127.0.0.1", 18180)
        c.request("POST", "/", (INF % ("MikroTik", "ATLGM")).replace("1 BOOT", events).encode()); c.getresponse().read()
        c.request("POST", "/", b""); r = c.getresponse(); r.read()
        c.request("POST", "/", body.encode()); r = c.getresponse(); b = r.read().decode()
        while "SetParameterValues" in b:
            c.request("POST", "/", (ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>").encode())
            r = c.getresponse(); b = r.read().decode()
        if "<cwmp:Download>" in b:
            c.request("POST", "/", (ENV % "<cwmp:DownloadResponse><Status>1</Status></cwmp:DownloadResponse>").encode()); c.getresponse().read()
            n = re.findall(r"script-(\d+)", b)[0]
            c2 = http.client.HTTPConnection("127.0.0.1", 18180); c2.request("GET", "/mikrotik/script-%s.alter" % n); sc = c2.getresponse().read().decode()
            c3 = http.client.HTTPConnection("127.0.0.1", 18180)
            c3.request("POST", "/", (ENV % "<cwmp:TransferComplete><CommandKey>keeper-%s</CommandKey><FaultStruct><FaultCode>0</FaultCode>"
                                     "<FaultString></FaultString></FaultStruct></cwmp:TransferComplete>" % n).encode()); c3.getresponse().read()
            return sc
    sc = None
    for i in range(4):
        sc = sess() or sc
        if sc: break
    assert sc and 'AT+QNWLOCK=\\"common/5g\\",569,636576,30,78' in sc and "/interface lte enable" in sc, sc
    time.sleep(1); assert call("GET", "/status")[1]["config"]["nr_lock"]["state"] == "active"
    sess("1 BOOT"); time.sleep(0.5)
    st2 = call("GET", "/status")[1]["config"]["nr_lock"]; assert st2["state"] in ("pending", "active"), st2           # lost on reboot -> set again
finally:
    p.terminate(); out = p.communicate()[0]; print(out)
assert "MikroTik status: RouterOS 7.25, modem RG520F-EU" in out and "MikroTik boot inform" in out, out
assert "DEVICE=mikrotik" in out and "ant5" not in out and "applied" not in out and "operator check-in" not in out, out   # no ZTE parts, no relay to the operator                  # nothing of the ZTE is touched
r = store.rows(DATA, "samples", device="mikrotik")
assert len(r) >= 2 and r[0]["lte_site"] == "1265/63" and r[0]["mikrotik_cqi"] == "10" and store.rows(DATA, "samples", device="zte") == [], r
print("OK test_mikrotik")
