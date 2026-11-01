# MikroTik ATL lock tests: same API and rows as the ZTE, but the lock is a RouterOS script with AT+QNWLOCK (no CPE reboot), so the
# step does not wait for a boot inform; 5G tests pin the LTE anchor too, one script carries both commands
import csv, os, re, shutil, sys, threading, time, datetime
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-mtlt"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DEVICE="mikrotik", DATA=DATA, LAT_TARGETS="off", SCAN_MIN="0", MIN_SAMPLE_S="999",
                  LOCKTEST_SETTLE_S="0", LOCKTEST_MEASURE_MIN="0.03", LOCKTEST_BOOT_WAIT_S="3", LOCKTEST_CLEANUP_S="0.05")
sys.path.insert(0, ROOT)
from keeper import acs, api, brain, common, devices, locktest, store
from keeper.devices import mikrotik as mk
vals = {r[0]: r[1] for r in csv.reader(open(os.path.join(here, "..", "fixtures", "mikrotik-dump.csv"))) if len(r) > 1 and r[0].startswith("Device.")}
ENV = '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0"><s:Header><cwmp:ID>1</cwmp:ID></s:Header><s:Body>%s</s:Body></s:Envelope>'
INF = ENV % ('<cwmp:Inform><DeviceId><Manufacturer>MikroTik</Manufacturer><ProductClass>ATLGM</ProductClass></DeviceId><Event><EventStruct>'
             '<EventCode>2 PERIODIC</EventCode></EventStruct></Event><CurrentTime>' + time.strftime("%Y-%m-%dT%H:%M:%S-02:00") + '</CurrentTime><ParameterList/></cwmp:Inform>')
GPV = ENV % ("<cwmp:GetParameterValuesResponse><ParameterList>%s</ParameterList></cwmp:GetParameterValuesResponse>" % "".join(
    "<ParameterValueStruct><Name>%s</Name><Value>%s</Value></ParameterValueStruct>" % kv for kv in vals.items()))
acts = []                                   # the scripts the ATL actually downloaded
stop = threading.Event()
def session():                              # one CWMP session: Inform -> read -> set? -> Download -> TransferComplete
    with acs.lock:
        r, _ = acs.handle(INF); assert "InformResponse" in r, r
        r, _ = acs.handle(""); assert "GetParameterValues" in r, r
        r, _ = acs.handle(GPV)
        while r:
            if "SetParameterValues" in r:
                r, _ = acs.handle(ENV % "<cwmp:SetParameterValuesResponse><Status>0</Status></cwmp:SetParameterValuesResponse>")
            elif "<cwmp:Download>" in r:
                n = int(re.findall(r"script-(\d+)", r)[0]); acts.append(mk.mt["scripts"][n])
                r, _ = acs.handle(ENV % "<cwmp:DownloadResponse><Status>1</Status></cwmp:DownloadResponse>"); assert r is None, r
                r, _ = acs.handle(ENV % ('<cwmp:TransferComplete><CommandKey>keeper-%d</CommandKey><FaultStruct><FaultCode>0</FaultCode>'
                                         '<FaultString></FaultString></FaultStruct></cwmp:TransferComplete>' % n)); assert "TransferCompleteResponse" in r, r
                r = None
            else: break
def agent():
    while not stop.is_set():
        if mk.mt["download"] or acs.tr["writes"]:
            try: session()
            except Exception as e: print("agent:", e)
        time.sleep(0.05)
acs.tr["last_sample"] = time.monotonic()    # the agent's reads must not add samples: the scenario injects them
threading.Thread(target=agent, daemon=True).start()

CELLS = {"none": ("3350/202", "no5G", "", "1265/63"), "534@636576": ("3350/202", "636576/534", "9.0", "1265/63"),
         "202@3350": ("3350/202", "636576/534", "8.0", "1265/63")}
def samples():                              # one sample every 0.2 s for the current lock (the shared fields the ATL fills)
    while not stop.is_set():
        cur = locktest.lt["running"]
        if cur in CELLS:
            lte, nr, sinr, site = CELLS[cur]
            brain.add({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "lte_cell": lte, "lte_site": site,
                       "lte_ca": "B7/3350/202:-99/8@20", "nr_cell": nr, "nr_sinr": sinr, "nr_rsrp": "-82", "nr_ca": "",
                       "call_ok": "1", "lat_p95": "40", "jitter_ms": "4", "loss_pct": "0", "down_bps": "0", "up_bps": "0", "uptime_s": "900"})
        time.sleep(0.2)
threading.Thread(target=samples, daemon=True).start()

assert devices.current() is mk and mk.LOCKS_REBOOT is False and "lock_test" in mk.CAN
assert api.capability("post", "/lock-test") == "lock_test" and api.capability("post", "/antenna-sweep") == "sweep" and "sweep" not in mk.CAN

# 1) 5G test on the ATL: the anchor and the 5G cell go into ONE script (no reboot, no boot wait), the step measures right away
t0 = time.time()
locktest.lock_test(["none", "534@636576"], rat="nr", anchor="202@3350")
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
print(rows)
assert rows["534@636576"]["rat"] == "nr" and rows["534@636576"]["anchor"] == "3350/202" and rows["534@636576"]["site"] == "1265/63"
assert rows["534@636576"]["on_target_pct"] == "100.0" and rows["534@636576"]["nr_sinr_p50"] == "9.0" and rows["534@636576"]["antennas"] == ""
assert rows["none"]["on_target_pct"] == "" and acs.st["boots"] == 0                        # no CPE reboot: no boot inform waited for
assert time.time() - t0 < 60, time.time() - t0
assert len(acts) == 3, acts
assert '4g\\",1,3350,202' in acts[0] and '5g\\",0' in acts[0]          # baseline: anchor pinned, 5G free
assert '5g\\",534,636576,30,78' in acts[1] and '4g\\",1,3350,202' in acts[1]
assert '4g\\",0' in acts[2] and '5g\\",0' in acts[2]                   # always unlocked at the end
assert all("/interface lte disable [find]" in a for a in acts)                                          # the data session is restarted

# 2) LTE test on the ATL: LTE lock only, 5G untouched
acts.clear()
locktest.lock_test(["none", "202@3350"], rat="lte")
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
assert rows["202@3350"]["rat"] == "lte" and rows["202@3350"]["on_target_pct"] == "100.0" and rows["202@3350"]["anchor"] == "3350/202"
assert len(acts) == 3 and all("common/5g" not in a for a in acts), acts
assert '4g\\",1,3350,202' in acts[1] and acts[2].count('4g\\",0') == 1

# 3) keeper died mid-test: the next start clears both AT locks with one script
acts.clear(); common.state({"lock_test": {"cell": "534@636576", "rat": "nr", "anchor": "202@3350"}})
mk.on_start(); time.sleep(0.6)
assert common.state().get("lock_test") is None and len(acts) == 1, (acts, common.state().get("lock_test"))
assert '4g\\",0' in acts[0] and '5g\\",0' in acts[0]
stop.set(); print("OK test_mikrotik_locktest")
