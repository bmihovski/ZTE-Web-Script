# cell lock over TR-069: learn the LockLTECell format once (web set without reboot), then lock/unlock = SetParameterValues + Reboot RPC,
# fast inform interval while acting, web page only as fallback (refused value / format not learnable)
import importlib.util, os, re, shutil, sys, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18150"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-tr"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", SWEEP_DAYS="0", DATA=DATA, ZTE_HOST="127.0.0.1:18150", ZTE_PASS_HASH=m.S(m.PW))
sys.path.insert(0, ROOT)
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks
zte = {"lock": "", "reflect": True, "reboots": 0, "intervals": [], "refuse": "999"}
def web_lock():                           # what the web page's LTE_LOCK_CELL_SET stored, as the ZTE shows it over TR-069
    s = [x[3] for x in m.st["sets"] if x[1] == "LTE_LOCK_CELL_SET"]
    return None if not s else ("" if s[-1] == "0@0" else s[-1].replace("@", ","))
seen_web = [0]
ENV = '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0"><s:Header><cwmp:ID>1</cwmp:ID></s:Header><s:Body>%s</s:Body></s:Envelope>'
def session():
    n = len([x for x in m.st["sets"] if x[1] == "LTE_LOCK_CELL_SET"])
    if n != seen_web[0] and zte["reflect"]: seen_web[0] = n; zte["lock"] = web_lock()
    with acs.lock:
        acs.handle(ENV % ("<cwmp:Inform><Event><EventStruct><EventCode>2 PERIODIC</EventCode></EventStruct></Event><CurrentTime>" + time.strftime("%Y-%m-%dT%H:%M:%S-02:00") + "</CurrentTime><ParameterList/></cwmp:Inform>"))
        acs.handle("")
        r, _ = acs.handle(ENV % ("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>" % "".join(
            "<ParameterValueStruct><Name>%s</Name><Value>%s</Value></ParameterValueStruct>" % kv for kv in [
                (zlocks.LOCK_PARAM, zte["lock"]), ("Device.ManagementServer.PeriodicInformInterval", zte["intervals"][-1] if zte["intervals"] else "60"),
                ("Device.ManagementServer.PeriodicInformEnable", "1"), ("Device.ManagementServer.UpgradesManaged", "1"), ("Device.DeviceInfo.UpTime", "9000")])))
        while r:
            if "SetParameterValues" in r:
                vals = dict(re.findall(r"<Name>(.*?)</Name><Value[^>]*>(.*?)</Value>", r))
                if zte["refuse"] in vals.get(zlocks.LOCK_PARAM, "x"):
                    r, _ = acs.handle(ENV % "<s:Fault><detail><cwmp:Fault><FaultCode>9007</FaultCode><FaultString>Invalid parameter value</FaultString></cwmp:Fault></detail></s:Fault>"); continue
                if zlocks.LOCK_PARAM in vals: zte["lock"] = vals[zlocks.LOCK_PARAM]
                if zlocks.NR_PARAM in vals: zte["nr"] = vals[zlocks.NR_PARAM]
                if "Device.ManagementServer.PeriodicInformInterval" in vals: zte["intervals"].append(vals["Device.ManagementServer.PeriodicInformInterval"])
                r, _ = acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>1</Status></cwmp:SetParameterValuesResponse>")
            elif "<cwmp:Reboot>" in r:
                zte["reboots"] += 1; r, _ = acs.handle(ENV % "<cwmp:RebootResponse/>")
            else: break
stop = threading.Event()
def run():
    while not stop.is_set(): session(); time.sleep(0.1)
threading.Thread(target=run, daemon=True).start()
def web_sets(): return [x[3] for x in m.st["sets"] if x[1] in ("LTE_LOCK_CELL_SET", "REBOOT_DEVICE")]
# first lock: learn the format (web, no reboot), then TR-069 write + TR-069 reboot
assert zlocks.lte_lock_reboot("340", "1550") == "tr069"
assert common.state()["lte_lock_fmt"] == {"lock": "{pci},{earfcn}", "unlock": ""}, common.state().get("lte_lock_fmt")
assert web_sets() == ["434@1550", "0@0"], web_sets()                   # learning only - no web reboot
assert zte["lock"] == "340,1550" and zte["reboots"] == 1
assert "10" in zte["intervals"]                                         # fast mode while acting
time.sleep(0.5); assert zte["intervals"][-1] == "60"                    # ... and back afterwards
assert common.state().get("lock_test") is None
assert api.config()["zte_locks_raw"]["LockLTECell"] == "340,1550" and api.config()["lte_lock_fmt"]["lock"] == "{pci},{earfcn}"
# unlock over TR-069
assert zlocks.lte_lock_reboot(0, 0) == "tr069" and zte["lock"] == "" and zte["reboots"] == 2 and web_sets() == ["434@1550", "0@0"]
# LTE + 5G lock in one SetParameterValues, one reboot; 5G unlock = ""
assert zlocks.locks_apply("340@100", "534@636576") == "tr069" and zte["lock"] == "340,100" and zte["nr"] == "534,636576,78,30" and zte["reboots"] == 3
assert zlocks.locks_apply("none", "none") == "tr069" and zte["lock"] == "" and zte["nr"] == "" and zte["reboots"] == 4
# cell lists are not taken (the MC889 keeps only the first cell of a list)
assert re.fullmatch(guard.SPEC_RE, "434@1550") and not re.fullmatch(guard.SPEC_RE, "434@1550;121@6400")
# a value the ZTE refuses -> no TR-069 reboot, web page used instead
assert zlocks.lte_lock_reboot("999", "1550") == "web" and zte["reboots"] == 4         # refused: no TR-069 reboot
assert [x[1] for x in m.st["sets"]][-2:] == ["LTE_LOCK_CELL_SET", "REBOOT_DEVICE"] and m.st["sets"][-2][3] == "999@1550"
# format not learnable (ZTE does not show web locks over TR-069) -> web page, nothing stored
fmt = common.state()["lte_lock_fmt"]; common.state({"lte_lock_fmt": None, "lock_test": "434@1550"})
assert zlocks.learn_lock_format() == fmt and common.state().get("lock_test") == "434@1550"     # learning keeps the lock test's fail-safe marker
common.state({"lock_test": None})
common.state({"lte_lock_fmt": None}); zte["reflect"] = False; zte["lock"] = ""
acs.wait_informs.__defaults__ = (1, 3)
assert zlocks.lte_lock_reboot("434", "1550") == "web" and not common.state().get("lte_lock_fmt")
stop.set()
import csv; from keeper import store
ev = [e["event"] for e in store.rows(DATA, "events")]; print(ev)
assert ev.index("lock format learned") < ev.index("lock format unknown") and ev.count("reboot sent") == 4   # 4 TR-069 reboots
print("OK test_tr069lock")
