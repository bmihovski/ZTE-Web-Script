# brain: decisions + guards, findings, latency, API, history load; acs: Reboot RPC; GET API over HTTP
import datetime, http.client, importlib.util, json, os, shutil, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-brain"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(DATA=DATA, PREFERRED_NR="")
sys.path.insert(0, ROOT)
from keeper import acs, api, brain, common, guard, locktest, monitor, store; from keeper.devices import zte as zdev; from keeper.devices.zte import antennas, locks as zlocks

T0 = time.time()
def row(i, cell="636576/534", sinr="7", b7="2", b3="8", lat=("40", "60"), up=3600, down="100000"):
    t = datetime.datetime.fromtimestamp(T0 - (300 - i) * 60).strftime("%Y-%m-%d %H:%M:%S")
    return {"time": t, "uptime_s": str(up + i * 60), "nr_cell": cell, "nr_sinr": "" if cell == "no5G" else sinr, "nr_rsrp": "-79",
            "lte_cell": "3350/340", "lte_ca": "B7/3350/340:-92/%s B1/100/340:-95/8 B3/1550/340:-91/%s B20/6400/121:-85/6" % (b7, b3),
            "lat_p50": lat[0], "lat_p95": lat[1], "lat_max": "90", "loss_pct": "0", "down_bps": down, "up_bps": "1000"}
ev = []; log = ev.append

# --- findings: 3 h on 534 (SINR 7), 2 h on 786 (SINR -1); B7 anchor 2 vs B3 8 ---
for i in range(300): brain.add(row(i, *(("636576/534", "7") if i < 180 else ("636576/786", "-1"))))
rep = brain.report(24)
print(json.dumps(rep["findings"], indent=1))
assert rep["samples"] == 300 and rep["switches"] == 1
assert any("LTE lock 340@1550" in x for x in rep["findings"]), rep["findings"]
assert any("5G 636576/786" in x and "NR lock 534" in x for x in rep["findings"]), rep["findings"]
c = {x["cell"]: x for x in rep["cells"]}; assert c["636576/786"]["sinr_p50"] == -1 and c["636576/534"]["minutes"] == 180
anchor = rep["lte_carriers"][0]; assert anchor["carrier"] == "B7 3350/340" and anchor["anchor_pct"] == 100 and anchor["sinr_p50"] == 2

# --- decisions: last 120 min on 786 at SINR -1 -> dryrun note, once per 30 min ---
now = T0
assert brain.decide(row(299, "636576/786", "-1"), log, now) and ev[-1]["event"] == "would reboot (dryrun)", ev
assert "SINR < 3" in ev[-1]["to"]
assert brain.decide(row(299, "636576/786", "-1"), log, now + 60) is None                     # no repeat within 30 min
brain.AUTO = "on"; brain.ctx["last_hint"].clear()
assert brain.decide(row(299, "636576/786", "-1", down="9000000"), log, now) is None and ev[-1]["event"] == "reboot held"   # busy link
assert "link busy" in ev[-1]["to"] and "Mbit/s" in ev[-1]["to"]
brain.ctx["last_hint"].clear()                                     # a Teams call: ~0.2 MB/s up (ZTE counters are bytes/s) -> no reboot
r_call = row(299, "636576/786", "-1"); r_call["up_bps"] = "200000"
assert brain.decide(r_call, log, now) is None and ev[-1]["event"] == "reboot held", ev[-1]
brain.ctx["last_hint"].clear()
assert brain.decide(row(299, "636576/786", "-1"), log, now) and brain.ctx["pending"] == "reboot" and ev[-1]["event"] == "reboot queued"
# keeper sends the Reboot RPC at the end of the next session
acs.st["step"] = "read"
body, echo = acs.handle(open(os.devnull).read() or
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0"><s:Body><cwmp:GetParameterValuesResponse>'
    '<ParameterList><ParameterValueStruct><Name>Device.ManagementServer.PeriodicInformInterval</Name><Value>60</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.ManagementServer.PeriodicInformEnable</Name><Value>1</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.ManagementServer.UpgradesManaged</Name><Value>1</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.Cellular.Interface.1.CurrentAccessTechnology</Name><Value>NR5G</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.Cellular.Interface.1.NR5G_PCI</Name><Value>786</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.Cellular.Interface.1.NR5G_SINR</Name><Value>-1</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.Cellular.Interface.1.NR5G_ARFCN</Name><Value>636576</Value></ParameterValueStruct>'
    '<ParameterValueStruct><Name>Device.DeviceInfo.UpTime</Name><Value>9000</Value></ParameterValueStruct>'
    '</ParameterList></cwmp:GetParameterValuesResponse></s:Body></s:Envelope>')
assert body and "<cwmp:Reboot>" in body and brain.ctx["pending"] is None, body
# guards: a second reboot within REBOOT_GAP_H is held
brain.ctx["last_hint"].clear()
assert brain.decide(row(299, "636576/786", "-1"), log, now + 600) is None and "last reboot" in ev[-1]["to"], ev[-1]
# no 5G for 10 min (uptime > 10 min) -> reason
brain.HIST.clear(); brain.ACTIONS.clear(); brain.AUTO = "dryrun"; brain.ctx["last_hint"].clear()
for i in range(12): brain.add(row(i, "no5G"))                                  # idle: 5G released on purpose (NSA) -> nothing
assert brain.decide(row(11, "no5G"), log, now) is None
for i in range(12): brain.add(row(i, "no5G", down="900000"))                   # no 5G while there was traffic -> reason (acts when idle)
assert "no 5G during 10 min with traffic" in (brain.decide(row(11, "no5G"), log, now) or ""), ev[-1]
brain.HIST.clear(); brain.ctx["last_hint"].clear()
for i in range(6): brain.add(dict(row(i, "no5G", down="0"), lte_cell="", lte_ca=""))           # no service at all (tech None, LTE 255) -> reboot
assert "no mobile service for 5 min" in (brain.decide(dict(row(6, "no5G", down="0"), lte_cell="", lte_ca=""), log, now) or ""), ev[-1]
# fresh after a boot (uptime < 10 min) -> never act
brain.ctx["last_hint"].clear()
assert brain.decide(row(0, "no5G", up=120), log, now) is None

# --- latency window ---
brain.lat_buf.clear()
for ms in (20, 30, 40, 50, None, 200): brain.lat_buf.append((time.time(), ms))
lr = brain.latency_row(); assert lr["lat_p50"] == 40 and lr["lat_p95"] == 200 and abs(lr["loss_pct"] - 16.7) < 0.1, lr

# --- history survives a restart: load reads the store (old samples.csv + v1-renamed file imported once) ---
import csv, tempfile
OLD = tempfile.mkdtemp()                                                       # a data dir from an older version (CSV only)
with open(os.path.join(OLD, "samples.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(row(0))); w.writeheader(); [w.writerow(row(i)) for i in range(5)]
with open(os.path.join(OLD, "samples.csv.v1"), "w") as fh: fh.write("time,uptime_s,cell,sinr\n2026-10-26 15:11:42,651,636576/127,7.5\n")   # v1: ignored
brain.HIST.clear(); brain.load(OLD); assert len(brain.HIST) == 5, len(brain.HIST)
assert os.path.exists(OLD + "/keeper.db") and os.path.exists(OLD + "/samples.csv")             # imported, CSV left in place

# --- call quality: busy minutes (uploads) with high latency -> bufferbloat; hours under 90 % call-ready -> warning ---
brain.HIST.clear()
for i in range(200):
    busy = 100 <= i < 130
    r = row(i, lat=("45", "320" if busy else "70")); r["jitter_ms"] = "40" if busy else "8"; r["call_ok"] = "0" if busy else "1"
    if busy: r["up_bps"] = "200000"                         # 1.6 Mbit/s up: a call / screen share
    brain.add(r)
rep = brain.report(24); print(rep["call"], rep["under_load"])
assert rep["under_load"]["busy_minutes"] == 30 and rep["under_load"]["busy_lat_p95"] == 320 and rep["under_load"]["idle_lat_p95"] == 70
assert rep["call"]["ok_pct"] == 85.0 and any(x.startswith("WARN: call-ready 85.0%") for x in rep["findings"]), rep["findings"]
assert any("bufferbloat" in x for x in rep["findings"]) and any(x.startswith("WARN: calls at risk") for x in rep["findings"]), rep["findings"]
# probe -> per-minute call verdict with jitter
brain.lat_buf.clear()
for ms in [40, 42, 41, 43, 40] * 6: brain.lat_buf.append((time.time(), ms))
lr = brain.latency_row(); assert lr["call_ok"] == 1 and lr["jitter_ms"] < 3, lr
brain.lat_buf.clear()
for ms in [40, 120, 35, 140, 50, None] * 5: brain.lat_buf.append((time.time(), ms))
lr = brain.latency_row(); assert lr["call_ok"] == 0 and lr["jitter_ms"] > 30 and lr["loss_pct"] > 10, lr
brain.lat_buf.clear()                                             # two targets at different distances: the offset is not jitter
for i in range(30): brain.lat_buf.append((time.time(), 30.0 if i % 2 else 70.0, "1.1.1.1" if i % 2 else "8.8.8.8"))
lr = brain.latency_row(); assert lr["jitter_ms"] == 0 and lr["call_ok"] == 1, lr

# --- switch explainer ---
brain.HIST.clear()
prev = row(0, sinr="8"); prev["rrc"] = "Connected"; prev["lte_ca"] = "B7/3350/340:-92/0 B3/1550/340:-91/8"; brain.add(prev)
new = row(1, "636576/786", "-1"); new["nr_rsrp"] = "-80"; new["rrc"] = "Connected"
w = brain.explain_switch(new); print(w)
assert w.startswith("LTE anchor weak (SINR 0.0)") and "SINR 8.0 -> -1.0" in w and "NR lock to 534" in w, w
prev["lte_ca"] = "B7/3350/340:-92/2"; brain.HIST.clear(); brain.add(prev)     # 2 dB: not called weak any more
assert brain.explain_switch(new).startswith("network handover while connected")
new2 = dict(new, lte_cell="1550/340"); assert brain.explain_switch(new2).startswith("LTE anchor changed 3350/340 -> 1550/340")
assert brain.explain_switch(dict(new, uptime_s="120")).startswith("after a ZTE boot")
brain.HIST.clear(); brain.add(dict(prev, nr_cell="no5G")); assert brain.explain_switch(new).startswith("5G leg re-added after a drop")
brain.HIST.clear(); brain.add(dict(prev, rrc="Idle")); assert brain.explain_switch(new).startswith("after an idle period")

# --- LTE cell ranking + "no data" instead of None% ---
brain.HIST.clear()
for i in range(130):
    r = row(i, *(("636576/534", "7") if i < 100 else ("636576/786", "-1")))
    r["lte_neighbours"] = "3350/340:-92 1550/340:-90 6400/112:-74 6400/121:-82 100/434:-94"
    if i < 100: r["call_ok"] = ""                              # old rows without call data
    brain.add(r)
rep = brain.report(24)
cells = {x["cell"]: x for x in rep["lte_cells"]}
assert cells["6400/112"]["band"] == "B20" and cells["6400/112"]["rsrp_p50"] == -74 and not cells["6400/112"]["in_ca"]
assert cells["1550/340"]["in_ca"] and cells["3350/340"]["band"] == "B7"
b20 = [x["cell"] for x in rep["lte_cells"] if x["band"] == "B20"]; assert b20[0] == "6400/112", b20       # grouped by band, strongest first
assert cells["6400/121"]["ca_pct"] == 100.0 and cells["6400/112"]["ca_pct"] == 0.0
fs = rep["findings"]; print(fs)
assert any(x.startswith("INFO: B20 (800 MHz, 10 MHz wide): 6400/112 is 8 dB stronger than the used 6400/121") and "low band" in x for x in fs), fs
assert not any("stronger than the anchor" in x for x in fs)                 # no RSRP comparison across bands any more
rec = next(x for x in fs if "LTE lock" in x); print(rec)
assert rec.endswith("A/B test LTE lock 340@1550 (the test also shows whether CA stays)") and "~16.4 Mbit/s" in rec and "B20 6400/121 800 MHz 10 MHz-wide SINR 6.0 (~13.9 Mbit/s per layer, low band" in rec, rec
import tempfile; _d = tempfile.mkdtemp(); _old = brain.reg_st["dir"]; brain.reg_st["dir"] = _d           # already lock-tested -> not recommended again
open(_d + "/lock_tests.csv", "w").write("time,lock,on_target_pct,anchor_sinr_p50,call_ok_pct\n2026-10-27 15:18:15,340@1550,100.0,-1.0,100.0\n")
open(_d + "/lock_tests.csv", "a").write("2026-10-27 18:40:00,340@100;340@3350,100.0,9.0,100.0\n")
assert [r["lock"] for r in brain.anchor_ranking()["ranked"]] == ["340@1550"]                     # list rows are not results
assert not any("A/B test LTE lock 340@1550" in x for x in brain.report(24)["findings"])
_d = tempfile.mkdtemp(); brain.reg_st["dir"] = _d                                              # old CSV files are imported into the store once
open(_d + "/lock_tests.csv", "w").write("time,lock,on_target_pct,anchor_sinr_p50,nr_sinr_p50,no5g_min,call_ok_pct\n"
    "1,434@1550,100,-1,8,0,100\n2,340@100,100,5,7,0,100\n")                                   # measured 2026-10-27: both legs decide
assert [r["lock"] for r in brain.anchor_ranking()["ranked"]] == ["340@100", "434@1550"]
json.dump({"lte_lock": {"cell": "340@100", "state": "active"}}, open(_d + "/state.json", "w"))
assert brain.anchor_finding(brain.anchor_ranking())[0].startswith("OK: best tested LTE anchor 340@100 is the keeper's lock (active)")
brain.reg_st["dir"] = _old; _d = tempfile.mkdtemp()
open(_d + "/samples.csv", "w").write("time,nr_cell,nr_sinr\n%s,255/255,255\n%s,636576/534,7\n" % (brain.HIST[-1]["time"], brain.HIST[-1]["time"]))
n = len(brain.HIST); brain.load(_d); assert len(brain.HIST) == n + 1 and brain.HIST[-1]["nr_cell"] == "636576/534"
assert brain.parse_ca("B7/3350/340:-92/2@20 B20/6400/121:-85/6@10")[1][5] == 10 and brain.parse_ca("B3/1550/340:-90/8")[0][5] == 20   # old rows: usual bw
cn = brain.capacity_now({"lte_ca": "B1/100/340:-82/5@20 B3/1550/340:-90/7@20", "nr_cell": "636576/534", "nr_sinr": "10", "nr_ca": "629988/747:-90.7/@20"})
assert [c["carrier"] for c in cn["carriers"]] == ["B1 100/340", "B3 1550/340", "n 636576/534", "n 629988/747"], cn
assert cn["carriers"][2]["bw_mhz"] == 100 and cn["carriers"][2]["layers"] == 4 and cn["carriers"][3]["mbps_layer"] is None   # SCC: no SINR -> no estimate
assert cn["carriers"][2]["mbps_layer"] == round(brain.capacity(100, 10) * 0.74, 1) and cn["est_down_mbps"] == round(2 * (brain.capacity(20, 5) + brain.capacity(20, 7)) + 4 * cn["carriers"][2]["mbps_layer"])
assert brain.capacity_now({"nr_cell": "156510/212", "nr_sinr": "1"})["carriers"][0]["bw_mhz"] == 10                       # n28
assert brain.no5g_busy({"nr_cell": "no5G", "down_bps": "900000"}) and not brain.no5g_busy({"nr_cell": "no5G", "down_bps": "1000", "up_bps": "0"})
assert brain.capacity(20, 8) == 34.4 and brain.capacity(10, 6) == 13.9                               # 3GPP TR 36.942 curve
assert brain.capacity(20, 30) == 88.0 and brain.capacity(20, -12) == 0.0 and brain.capacity(None, 5) is None   # cap 4.4 b/s/Hz, floor -10 dB
assert any("call-ready no data" in x for x in fs) and not any("None%" in x for x in fs), fs

# --- API over HTTP (real keeper process) ---
from keeper import store
for i in range(5): store.add(DATA, "samples", dict(row(i), time=(datetime.datetime.now() + datetime.timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")))
env = dict(os.environ, NIGHT_SCREEN="off", PORT="18110", DATA=DATA, ZTE_HOST="127.0.0.1:9", LAT_TARGETS="127.0.0.1:9")
p = subprocess.Popen([sys.executable, "-m", "keeper"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(1)
try:
    for path in ("/status", "/report?hours=24", "/events?n=5", "/nope"):
        cn = http.client.HTTPConnection("127.0.0.1", 18110); cn.request("GET", path); r = cn.getresponse(); d = json.loads(r.read())
        assert r.getheader("Access-Control-Allow-Origin") == "*"
        if path == "/status": assert d["nr_cell"] == "636576/534" and d["verdict"] == "good" and d["auto"] == "dryrun" and d["config"]["antennas"]["ant6"] == "4", d
        if path.startswith("/report"): assert d["samples"] >= 5 and d["findings"], d
        if path == "/nope": assert r.status == 404
finally:
    p.terminate(); print(p.communicate()[0])
sm = brain.api("/samples?from=2000-01-01%2000:00&cols=time,nr_cell", {}, DATA)[1]["rows"]
assert sm and set(sm[-1]) == {"time", "nr_cell"}, sm[-1]
# _lte_gain is quality-first: quality decides; signal only breaks a quality tie (regression for the old weak-OR gain)
q = {"loss_pct": "0", "lat_p95": "50", "jitter_ms": "5", "anchor_rsrp_p50": "-80"}
assert brain._lte_gain(dict(q, call_ok_pct="90", anchor_sinr_p50="15"), dict(q, call_ok_pct="100", anchor_sinr_p50="3")) is False   # worse call-ready, better signal -> no gain
assert brain._lte_gain(dict(q, call_ok_pct="100", anchor_sinr_p50="3"), dict(q, call_ok_pct="90", anchor_sinr_p50="3")) is True    # +10pp call-ready -> gain
assert brain._lte_gain(dict(q, call_ok_pct="100", anchor_sinr_p50="15"), dict(q, call_ok_pct="100", anchor_sinr_p50="3")) is True  # quality tied, +12dB -> signal breaks the tie
print("OK _lte_gain")
print("OK test_brain")
