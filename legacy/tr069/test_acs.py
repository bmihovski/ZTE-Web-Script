# fake CPE session against acs.py: Inform -> GPN -> GPV chunks (one bad name -> Fault -> split) -> 204 + CSV
import csv, glob, http.client, os, re, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
for f in glob.glob(os.path.join(here, "tr069-dump-*.csv")): os.remove(f)
srv = subprocess.Popen([sys.executable, os.path.join(here, "acs.py"), "17557"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(0.7)
NS = "urn:dslforum-org:cwmp-1-2"
def env(body): return ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="%s" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                       '<soap:Header><cwmp:ID>zte-4711</cwmp:ID></soap:Header><soap:Body>%s</soap:Body></soap:Envelope>' % (NS, body))
TREE = {"Device.DeviceInfo.Manufacturer": "ZTE", "Device.ManagementServer.Password": "opsecret",
        "Device.X_ZTE-COM_NR.Neighbour.1.PCI": "534"}
TREE.update({"Device.Cellular.Interface.1.Stat%d" % i: str(i) for i in range(95)})
BAD = "Device.Cellular.Interface.1.Stat7"          # CPE refuses this one
c = http.client.HTTPConnection("127.0.0.1", 17557)
def post(body):
    c.request("POST", "/", body.encode(), {"Content-Type": "text/xml"}); r = c.getresponse(); return r.status, r.read().decode()
st, b = post(env('<cwmp:Inform><DeviceId><Manufacturer>ZTE</Manufacturer><ProductClass>MC889</ProductClass></DeviceId>'
                 '<Event><EventStruct><EventCode>0 BOOTSTRAP</EventCode></EventStruct></Event><ParameterList>'
                 '<ParameterValueStruct><Name>Device.DeviceInfo.SoftwareVersion</Name><Value>B10</Value></ParameterValueStruct>'
                 '</ParameterList></cwmp:Inform>'))
assert st == 200 and "InformResponse" in b and NS in b and ">zte-4711</cwmp:ID>" in b, b
st, b = post("")
assert "GetParameterNames" in b and "<ParameterPath>Device.</ParameterPath>" in b, b
infos = "".join("<ParameterInfoStruct><Name>%s</Name><Writable>0</Writable></ParameterInfoStruct>" % n for n in list(TREE) + ["Device.Cellular."])
st, b = post(env("<cwmp:GetParameterNamesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterNamesResponse>" % infos))
rounds = 0
while st == 200:
    rounds += 1; assert rounds < 50
    asked = re.findall(r"<string>(.*?)</string>", b)
    if BAD in asked:
        st, b = post(env("<soap:Fault><detail><cwmp:Fault><FaultCode>9005</FaultCode><FaultString>Invalid parameter name</FaultString></cwmp:Fault></detail></soap:Fault>"))
        continue
    vals = "".join('<ParameterValueStruct><Name>%s</Name><Value xsi:type="xsd:string">%s</Value></ParameterValueStruct>' % (n, TREE[n]) for n in asked)
    st, b = post(env("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>" % vals))
assert st == 204, st
# a later session (periodic inform every 10 s) must not start a second dump
st, b = post(env('<cwmp:Inform><Event><EventStruct><EventCode>2 PERIODIC</EventCode></EventStruct></Event><ParameterList/></cwmp:Inform>'))
assert "InformResponse" in b, b
st, b = post(""); assert st == 204, (st, b)
time.sleep(0.3); srv.terminate(); out = srv.communicate()[0]; print(out)
files = glob.glob(os.path.join(here, "tr069-dump-*.csv")); assert len(files) == 1
rows = {r["name"]: r for r in csv.DictReader(open(files[0]))}
assert len(rows) == len(TREE), len(rows)                         # "Device.Cellular." (object) not listed
assert rows["Device.X_ZTE-COM_NR.Neighbour.1.PCI"]["value"] == "534"
assert rows["Device.ManagementServer.Password"]["value"] == "[masked, 8 chars]"
assert "9005" in rows[BAD]["fault"] and rows[BAD]["value"] == ""
assert rows["Device.Cellular.Interface.1.Stat8"]["value"] == "8"
os.remove(files[0]); os.remove(os.path.join(here, "tr069-raw.log"))
print("OK test_acs")
