# API description: every path the keeper handles is in ROUTES; /openapi.json, /docs (Swagger UI) and /export work over HTTP
import http.client, json, os, re, shutil, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-api"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
sys.path.insert(0, ROOT); from keeper import store
src = open(os.path.join(ROOT, "keeper", "api.py")).read() + open(os.path.join(ROOT, "keeper", "brain.py")).read()
handled = set(re.findall(r'path == "(/[\w./-]+)"', src)) | set(re.findall(r'u\[0\] == "(/[\w./-]+)"', src)) | {"/status"}
handled |= {p for t in re.findall(r'path in \(([^)]*)\)', src) for p in re.findall(r'"(/[\w./-]+)"', t)}
from keeper import api as api_mod
listed = {r[1] for r in api_mod.ROUTES}
missing = {h for h in handled if not any(l.startswith(h) for l in listed)}
assert not missing, missing                                     # a new endpoint must be described in ROUTES
store.add(DATA, "events", {"time": "2026-01-01 00:00:00", "event": "old"})
RECENT = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 3600))       # an hour ago (not date-bound: /db/prune days=1 below)
store.add(DATA, "events", {"time": RECENT, "event": "lte lock removed", "why": "no LTE service, 3 min"})
store.add(DATA, "lock_tests", {"time": RECENT, "lock": "394@6400", "on_target_pct": "100.0", "down_mbps": "74.4", "up_mbps": "21.6",
                               "call_ok_pct": "100.0", "loss_pct": "0.0", "lat_p95": "34.6", "jitter_ms": "3.2", "nr_cells": "no5G:12"})
p = subprocess.Popen([sys.executable, "-m", "keeper"], cwd=ROOT, env=dict(os.environ, NIGHT_SCREEN="off", CHECKIN_DAYS="0", PORT="18160", DATA=DATA, ZTE_HOST="127.0.0.1:9",
                     LAT_TARGETS="127.0.0.1:9", SLOW_REQUEST_S="0"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(1)
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", 18160); c.request("GET", path); r = c.getresponse(); return r.status, r.getheader("Content-Type"), r.read().decode()
try:
    s, t, b = get("/openapi.json"); spec = json.loads(b)
    assert s == 200 and spec["openapi"].startswith("3.") and set(spec["paths"]) == listed and spec["paths"]["/lte-lock"]["post"]["parameters"][0]["name"] == "cell"
    s, t, b = get("/docs"); assert s == 200 and t.startswith("text/html") and "swagger-ui-bundle.js" in b and "/openapi.json" in b
    s, t, b = get("/export?kind=events&from=" + RECENT[:10]); assert s == 200 and t == "text/csv" and b.splitlines()[0] == "time,event,why" and "no LTE service, 3 min" in b, b
    s, t, b = get("/export?kind=nope"); assert s == 400
    def post(path, body):
        c = http.client.HTTPConnection("127.0.0.1", 18160); c.request("POST", path, body.encode(), {"Content-Type": "text/plain"}); r = c.getresponse()
        return r.status, json.loads(r.read())
    assert post("/cell-ids", '{"lte:100:340": 318289, "nr:636576:534": 12345, "bad": 1}') == (200, {"added": 2, "ids": 2})
    st, e = post("/lock-test?cells=534@636576&rat=nr&night=1", ""); assert st == 400 and "night=1" in e["error"], (st, e)   # night: LTE only
    st, e = post("/band-scan?bands=8,x", ""); assert st == 400 and "bands=" in e["error"], (st, e)            # the band scan
    s_, t_, b_ = get("/band-scans"); assert s_ == 200 and json.loads(b_) == {"running": None, "scans": []}, b_
    assert "night_plan" in json.loads(get("/lock-tests")[2])                          # the planner's last decision (cells, why, day_only)
    from keeper import common; np0 = common.state().get("night_plan"); s_, t_, b_ = get("/night-plan"); x = json.loads(b_)         # preview: what a night would test now
    assert s_ == 200 and set(x) >= {"cells", "day_only", "night_off", "why", "night_bands"} and common.state().get("night_plan") == np0, x
    r = json.loads(get("/lock-tests")[2])["results"][-1]; assert r["score"] == 70.0 and r["score_parts"]["down"] == 9.3 and r["score_gate"] == "", r
    st, armed = post("/night-screen?arm=1", ""); assert st == 200 and re.fullmatch(r"\d{4}-\d\d-\d\d", armed["armed"] or ""), armed   # the panel's button
    cfg = json.loads(get("/status")[2])["config"]; assert cfg["night_armed"] == armed["armed"] and cfg["night_always"] is False, cfg
    assert post("/night-screen?arm=0", "") == (200, {"armed": None}) and json.loads(get("/status")[2])["config"]["night_armed"] is None
    assert post("/cell-ids", '{"lte:100:340": 1}')[1]["added"] == 0 and json.loads(get("/cell-ids")[2])["lte:100:340"] == 318289   # known ids kept
    assert post("/userdata?name=zte_positions", '[{"label": "window 108", "heading": 108}]') == (200, {"saved": "zte_positions"})
    assert json.loads(get("/userdata?name=zte_positions")[2])["data"][0]["heading"] == 108
    assert json.loads(get("/userdata?name=zte_ant_results")[2])["data"] is None and post("/userdata?name=x", "1")[0] == 400
    d = json.loads(get("/db")[2]); assert d["cap_mb"] == 150 and any(r["kind"] == "events" for r in d["records"]), d
    assert post("/db/prune?days=0", "")[0] == 400
    s2, r2 = post("/db/prune?days=1&kind=events&vacuum=1", ""); assert s2 == 200 and r2["deleted"] == 1, r2      # the January event
    # another CPE (the MikroTik ATL during development) pointed here: answered, never read or changed; the ZTE still is
    INF = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0"><s:Header><cwmp:ID>1</cwmp:ID>'
           '</s:Header><s:Body><cwmp:Inform><DeviceId><Manufacturer>%s</Manufacturer><ProductClass>%s</ProductClass></DeviceId><Event><EventStruct>'
           '<EventCode>2 PERIODIC</EventCode></EventStruct></Event><CurrentTime>' + time.strftime("%Y-%m-%dT%H:%M:%S-02:00") + '</CurrentTime><ParameterList/></cwmp:Inform></s:Body></s:Envelope>')
    for maker, product, follow in (("MikroTik", "ATLGM", None), ("ZTE", "MC889", "GetParameterValues")):
        c = http.client.HTTPConnection("127.0.0.1", 18160)
        c.request("POST", "/", (INF % (maker, product)).encode()); r = c.getresponse(); assert r.status == 200 and "InformResponse" in r.read().decode()
        c.request("POST", "/", b""); r = c.getresponse(); b2 = r.read().decode()
        assert (r.status == 204) if follow is None else (follow in b2), (maker, r.status, b2[:200])
finally:
    p.terminate(); out = p.communicate()[0]; print(out)
# every request over SLOW_REQUEST_S is logged with its time (0 here: all) - the router's CPU spikes vs the ZTE panel's 60-s polls
assert re.search(r"slow request: GET /openapi\.json \d+\.\d+ s", out) and re.search(r"slow request: GET /docs \d+\.\d+ s", out), out[-2000:]
o = subprocess.run([sys.executable, "-m", "keeper"], cwd=ROOT, env=dict(os.environ, NIGHT_SCREEN="off", PORT="18161", DATA=DATA, DEVICE="huawei"),
                   capture_output=True, text=True, timeout=20)
assert o.returncode == 2 and "DEVICE=huawei unknown" in o.stdout, o
print("OK test_api")
