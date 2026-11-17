"""HTTP: the JSON API (userscript panel, app, Swagger UI at /docs) and the CWMP endpoint (POST / from the modem), one port.

`ROUTES` is the single description of the API: /openapi.json and /docs are generated from it, `test/scenarios/api.py` fails when a handled
path is missing there. A route with a capability (5th field) is refused with 409 when the active driver lacks it.
"""
import json, re, time, urllib.parse
from http.server import BaseHTTPRequestHandler

from keeper import VERSION, acs, alert, brain, captest, common, devices, guard, locktest, selftest, sqm, store
from keeper.common import DATA, log, state

UI_NAMES = ("zte_positions", "zte_ant_results", "zte_exp_results", "zte_exp_pending")     # app lists kept in the keeper's database
KINDS = ("samples", "events", "lock_tests", "antenna_sweeps")

ROUTES = [   # method, path, summary, [(param, example, description)], capability needed (None = any device)
    ("get", "/status", "Now: verdict, 5G / LTE cells, latency, download estimate, config, 24 h findings", [], None),
    ("get", "/report", "Analysis of a window: call quality per hour, 5G cells, LTE carriers and cells, findings", [("hours", "24", "window in hours")], None),
    ("get", "/events", "Latest events (switches, boots, locks, reboots, tests) with the keeper's explanation", [("n", "50", "how many")], None),
    ("get", "/cells", "Cell registry: every LTE cell measured, 5G cells used (incl. as 2nd carrier), site notes", [], None),
    ("get", "/samples", "Raw one-minute samples from the store (newest 500 of the range)",
     [("from", "2026-10-28 01:00", "from time"), ("to", "2026-10-28 01:15", "to time"), ("cols", "time,lte_cell,nr_cell,lte_sinr", "only these fields")], None),
    ("get", "/export", "CSV download for your own analysis",
     [("kind", "samples", " | ".join(KINDS)), ("from", "2026-10-28", "from time"), ("to", "2026-10-29", "to time"),
      ("device", "zte", "zte | mikrotik | * (default: the active DEVICE)")], None),
    ("get", "/anchors", "Lock-test ranking (latest result per cell) and the unlocked baseline, per RAT", [("rat", "lte", "lte | nr (default lte)")], None),
    ("get", "/lock-tests", "Lock test now or last (step, phase, plan, RAT, pinned anchor) + the last 40 results", [("rat", "", "lte | nr (default: all results)")], None),
    ("get", "/antenna-sweeps", "The last 40 rows of keeper antenna sweeps", [], None),
    ("post", "/lte-lock", "Keep an LTE cell locked (guarded: released when lost, restored when back) or remove it", [("cell", "340@100", "PCI@EARFCN or none")], None),
    ("post", "/nr-lock", "Keep a 5G cell locked (guarded) or remove it", [("cell", "534@636576", "PCI@ARFCN or none")], None),
    ("post", "/antennas", "Antenna mode the keeper re-applies at every ZTE boot (overrides ANT_AUTO / ANT5 / ANT6)",
     [("auto", "0", "1 = ZTE auto-select"), ("ant5", "4", "1 omni, 4 dir"), ("ant6", "4", "1 omni, 4 dir"), ("none", "", "back to the env")], "antennas"),
    ("post", "/lock-test", "Start a lock test (rat=lte: PCI@EARFCN; rat=nr: PCI@ARFCN with the LTE anchor pinned): baseline, then each "
     "cell (ZTE: lock + reboot; ATL: lock script, no reboot) + ~15 min. align=1 waits for you per cell; align=0 tests a fixed position",
     [("cells", "340@100,434@100", "PCI@EARFCN (rat=nr: PCI@ARFCN), comma separated"), ("rat", "lte", "lte | nr (nr pins the LTE anchor)"),
      ("align", "0", "1 = on-go: wait for 'aligned' before each cell; 0 = fixed position (the antenna is where it is)"),
      ("anchor", "340@1550", "rat=nr: the LTE anchor to pin (default: the keeper's LTE lock, else the serving cell)"),
      ("heading", "108", "align=0 and rat=nr: the antenna heading to record with the results (degrees)")], "lock_test"),
    ("post", "/lock-test-continue", "Align mode: the antenna is aligned for the locked cell - measure now (the 5G test then sweeps the antennas)",
     [("heading", "108", "antenna heading, degrees")], "lock_test"),
    ("post", "/lock-test-skip", "Skip the current lock-test step (what was measured is kept)", [], "lock_test"),
    ("post", "/lock-test-stop", "Stop the lock test (the lock is removed)", [], "lock_test"),
    ("post", "/antenna-sweep", "Run the keeper's antenna sweep (close every ZTE web page first)", [], "sweep"),
    ("post", "/cells/forget", "Drop cells from this device's registry (e.g. the other device's cells that got in)",
     [("cells", "500/341,3150/341", "EARFCN/PCI (5G: ARFCN/PCI), comma separated")], None),
    ("post", "/cells/refill", "Rebuild the 5G cell <-> LTE anchor mapping from the stored samples (registry only, no modem action)",
     [("days", "7", "samples window")], None),
    ("get", "/cell-ids", "Learned cell ids: 'lte:EARFCN:PCI' -> ECI (eNB*256+sector), 'nr:ARFCN:PCI' -> NCI (TR-069 + the app)", [], None),
    ("post", "/cell-ids", "Merge cell ids from the app (JSON body: {\"lte:100:340\": 318289}); known ids are not overwritten", [], None),
    ("get", "/userdata", "The app's saved list (newest copy)", [("name", "zte_positions", "|".join(UI_NAMES))], None),
    ("post", "/userdata", "Save one of the app's lists (JSON body; the newest 20 saves are kept)", [("name", "zte_positions", "|".join(UI_NAMES))], None),
    ("get", "/mikrotik/raw", "ATL: its last read X_MIKROTIK_ values + results of set / script experiments", [("prefix", "X_MIKROTIK_Lte", "name prefix")], "at"),
    ("post", "/mikrotik/set", "ATL experiment: SetParameterValues on a writable cellular value",
     [("param", "X_MIKROTIK_LteCellLock", "X_MIKROTIK_LteCellLock|X_MIKROTIK_LteBands|X_MIKROTIK_5GBands"), ("value", "3350,202", "value")], "at"),
    ("post", "/mikrotik/scan", "ATL: start a cell-monitor scan (4G + 5G neighbours, 10 s) - results in the next samples", [], "scan"),
    ("post", "/mikrotik/at", "ATL experiment: run an AT+QNWLOCK command via a RouterOS script (TR-069 Download .alter)",
     [("cmd", 'AT+QNWLOCK="common/4g",0', "the AT command"), ("restart", "1", "1 = restart the LTE interface afterwards")], "at"),
    ("get", "/mikrotik/script-1.alter", "The RouterOS script the ATL downloads (internal)", [], "at"),
    ("get", "/db", "Database: size, cap, records per device / kind (oldest, newest), free disk, old CSVs safe to delete", [], None),
    ("post", "/db/prune", "Delete old records (automatic daily: samples > KEEP_DAYS, over DB_MAX_MB the oldest days)",
     [("days", "30", "older than N days (>= 1)"), ("kind", "samples", "samples | events | lock_tests | antenna_sweeps | ui"),
      ("device", "*", "zte | mikrotik | * (all)"), ("vacuum", "1", "1 = shrink the file (needs free space ~ its size, blocks the keeper a moment)"),
      ("csv", "1", "1 = also delete the old CSV files already imported")], None),

    ("get", "/captest", "Capacity test (the modem's own TR-143 download to the operator's Ookla server): last result with the raw values, recent results, ceilings per period", [], "captest"),
    ("post", "/captest", "Run a capacity test now (no slot / idle wait; ~124 MB download by the modem; counts for this period's ceiling). "
     "Default: the modem's iperf3 download to Sofia (fallback Athens), counts like a scheduled test. Custom server / secs / conn / "
     "reverse=0 / url / mode=tr143: a diagnostic - never counts for a ceiling",
     [("url", "http://proof.ovh.net/files/100Mb.dat", "plain-HTTP file (optional)"), ("conn", "1", "connections 1-8 (optional)"),
      ("secs", "10", "time based test, seconds 1-30 (optional)"), ("mode", "iperf3", "iperf3 (default) | tr143 (optional)"),
      ("server", "37.19.203.1:5201", "iperf3 server host:port (optional)"), ("reverse", "1", "iperf3: 1 = download (default), 0 = upload")], "captest"),
    ("get", "/checks", "Self-test scenarios (ENUM) and the last run", [], None),
    ("post", "/checks", "Run self-test scenarios now and push the result to Telegram", [("name", "all", "all|clock|link|sqm|lock|night (comma-separated)")], None),
    ("post", "/tr069/read", "Read any TR-069 subtree with the modem's next inform (once; an invalid path costs one read)",
     [("path", "Device.IP.Diagnostics.Iperf3.", "a Device.* name or subtree ending in '.'")], None),
    ("get", "/tr069/read", "The values of the last POST /tr069/read (asked path, values, time)", [], None),
    ("get", "/openapi.json", "This API as OpenAPI 3", [], None),
    ("get", "/docs", "Swagger UI for this API", [], None),
]


ENUMS = {"rat": ["lte", "nr"], "align": ["0", "1"], "auto": ["0", "1"], "ant5": ["1", "4"], "ant6": ["1", "4"]}


def openapi():
    """OpenAPI 3 document from ROUTES (params with a known value set get an `enum`)."""
    paths = {}
    for m, path, summary, params, cap in ROUTES:
        paths.setdefault(path, {})[m] = {"summary": summary + (" [%s only]" % cap if cap else ""), "responses": {"200": {"description": "OK"}},
                                         "parameters": [{"name": n, "in": "query", "required": False, "description": d,
                                                         "schema": dict({"type": "string"}, **({"enum": ENUMS[n]} if n in ENUMS else {})),
                                                         "example": ex} for n, ex, d in params]}
        if m == "post" and path in ("/userdata", "/cell-ids"):
            paths[path][m]["requestBody"] = {"content": {"text/plain": {"schema": {"type": "string"}, "example": '{"lte:100:340": 318289}'}}}
    return {"openapi": "3.0.3", "info": {"title": "zte-keeper", "version": VERSION,
            "description": "TR-069 keeper for the uplink modem (DEVICE=%s): monitor, analysis, guarded cell locks. POSTs act on the modem." % common.DEVICE},
            "paths": paths}


DOCS = ('<!doctype html><html><head><meta charset="utf-8"><title>zte-keeper API</title>'
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head><body><div id="ui"></div>'
        '<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>'
        '<script>SwaggerUIBundle({url: "/openapi.json", dom_id: "#ui"})</script></body></html>').encode()


def capability(method, path):
    """Capability a request needs (None = any device)."""
    for m, p, _, _, cap in ROUTES:
        if m == method and (path == p or (p.startswith("/mikrotik/script-") and path.startswith("/mikrotik/script-"))): return cap
    return None


def config():
    """/status -> config: what the keeper is set to and doing now (core + the driver's part)."""
    dev = devices.current()
    return dict({"device": common.DEVICE, "lock_test": locktest.lt["running"], "lock_test_rat": locktest.lt.get("rat"),
                 "lock_test_anchor": locktest.lt.get("anchor"), "lte_lock": state().get("lte_lock"), "nr_lock": state().get("nr_lock"),
                 "fast_mode": acs.fast(), "busy": common.busy(), "inform_s": acs.INTERVAL,
                 "clock": {"verified": common.clock_ok(), "modem_skew_s": common.clock["skew"], "unverified_since": common.clock["bad_since"]},
                 "manages": common.E("CPE", "") or state().get("cpe") or dev.MAKER,
                 "sqm": {"on": bool(sqm.URL), "now": sqm.st["last"], "changes": sqm.st["changes"], "error": sqm.st["error"]},
                 "alerts": {"on": bool(alert.TOKEN and alert.CHAT), "last": [{"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), "kind": k, "text": x}
                                                                             for t, k, x in alert.sent[-5:]]},
                 "auto_actions": brain.AUTO, "min_sinr": brain.MIN_SINR, "preferred_nr": brain.PREFERRED, "max_reboots_day": brain.MAX_REBOOTS_DAY,
                 "call_limits": {"p95_ms": brain.CALL_P95, "jitter_ms": brain.CALL_JITTER, "loss_pct": brain.CALL_LOSS}}, **dev.config())


SLOW_S = float(common.E("SLOW_REQUEST_S", "2"))    # requests slower than this are logged (the router's CPU spikes vs the panel's polls)


class H(BaseHTTPRequestHandler):
    """One HTTP connection: API requests and the modem's CWMP session (keep-alive)."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        """Silence the default per-request stderr log."""
        pass

    def body(self):
        """Request body (Content-Length or chunked - the ZTE sends chunked)."""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while True:
                n = int(self.rfile.readline().split(b";")[0], 16)
                if n == 0:
                    self.rfile.readline(); return out
                out += self.rfile.read(n); self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def parse_request(self):
        """The request's start (after its first line arrived - keep-alive idle time is not the request's)."""
        self.t0 = time.monotonic(); return super().parse_request()

    def send(self, status, headers, data):
        """Send a full response with Content-Length (keep-alive); log it when it took SLOW_REQUEST_S or more."""
        self.send_response(status)
        for k, v in headers: self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        try: self.end_headers(); self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass      # the browser / app went away first (page reload): nothing to answer
        secs = time.monotonic() - getattr(self, "t0", time.monotonic())
        if secs >= SLOW_S: log("slow request: %s %s %.1f s" % (self.command, self.path, secs))

    def json(self, code, obj):
        """JSON response with CORS open and no caching."""
        self.send(code, [("Content-Type", "application/json"), ("Access-Control-Allow-Origin", "*"), ("Cache-Control", "no-store")],
                  json.dumps(obj, indent=1).encode())

    def refused(self, method, path):
        """409 when the active driver lacks the route's capability."""
        cap = capability(method, path)
        if cap and cap not in devices.current().CAN:
            self.json(409, {"error": "%s needs '%s' - not supported on DEVICE=%s" % (path, cap, common.DEVICE)}); return True
        return False

    def do_GET(self):
        """API reads (JSON / CSV / Swagger UI)."""
        # read-only API (CWMP uses POST only)
        u = urllib.parse.urlsplit(self.path); path, q = u.path.rstrip("/") or "/", urllib.parse.parse_qs(u.query)
        g = lambda k, d="": q.get(k, [d])[0]
        if self.refused("get", path): return
        dev = devices.current()
        try:
            if path == "/lock-tests":
                lt = locktest.lt; rat = g("rat")
                if rat not in ("", "lte", "nr"): raise ValueError("rat=lte|nr")
                rows = store.rows(DATA, "lock_tests")
                if rat: rows = [r for r in rows if r.get("rat", "lte") == rat]
                code, obj = 200, {"running": lt["running"], "phase": lt.get("phase", ""), "plan": lt["plan"], "align": lt["align"],
                                  "rat": lt.get("rat"), "anchor": lt.get("anchor"), "waiting": lt["waiting"], "results": rows[-40:]}
            elif path == "/antenna-sweeps":
                code, obj = 200, store.rows(DATA, "antenna_sweeps", last=40)
            elif path == "/userdata":
                if g("name") not in UI_NAMES: raise ValueError("name=" + "|".join(UI_NAMES))
                last = store.rows(DATA, "ui:" + g("name"), last=1, device="app")     # the app's lists: not per modem
                code, obj = 200, last[0] if last else {"name": g("name"), "time": None, "data": None}
            elif path == "/cell-ids":
                code, obj = 200, brain.REG.get("ids") or {}
            elif path.startswith("/mikrotik/script-"):   # the ATL downloads its script here (TR-069 Download)
                n = int(re.findall(r"script-(\d+)", path)[0])
                return self.send(200, [("Content-Type", "text/plain")], dev.mt["scripts"].get(n, "").encode())
            elif path == "/mikrotik/raw":
                pre = g("prefix", "X_MIKROTIK_")
                code, obj = 200, {"values": {k: v for k, v in dev.mt["last_vals"].items() if k.startswith(pre)}, "results": dev.mt["results"][-10:]}
            elif path == "/db":
                code, obj = 200, store.info(DATA)
            elif path == "/tr069/read":
                code, obj = 200, {"asked": acs.peek["asked"], "pending": acs.peek["want"], "time": acs.peek["time"], "values": acs.peek["got"],
                                  "note": None if acs.peek["got"] or not acs.peek["asked"] else "not answered (yet): wait for the next inform, or the path is invalid"}
            elif path == "/captest":
                code, obj = 200, captest.info()
            elif path == "/checks":
                code, obj = 200, {"scenarios": {n: selftest.CHECKS[n][0] for n in selftest.ORDER}, "last": selftest.st["last"]}
            elif path == "/openapi.json":
                code, obj = 200, openapi()
            elif path == "/docs":
                return self.send(200, [("Content-Type", "text/html; charset=utf-8")], DOCS)
            elif path == "/export":    # CSV download: /export?kind=samples&from=2026-10-28&to=2026-10-29
                kind = g("kind", "samples")
                if kind not in KINDS: raise ValueError("kind=" + "|".join(KINDS))
                data = store.to_csv(store.rows(DATA, kind, g("from"), g("to") or "￿", device=g("device") or None)).encode()
                return self.send(200, [("Content-Type", "text/csv"), ("Access-Control-Allow-Origin", "*"),
                                       ("Content-Disposition", 'attachment; filename="%s.csv"' % kind)], data)
            else:
                code, obj = brain.api(self.path, config(), DATA)
        except ValueError as e:
            code, obj = 400, {"error": str(e)}
        except Exception as e:
            code, obj = 500, {"error": repr(e)}
        self.json(code, obj)

    def do_POST(self):
        """API actions; anything else (POST / from the modem) is a CWMP message for `acs.serve`."""
        u = urllib.parse.urlsplit(self.path); path, q = u.path.rstrip("/") or "/", urllib.parse.parse_qs(u.query, keep_blank_values=True)
        g = lambda k, d="": q.get(k, [d])[0] or d          # blank = default (?none still shows up in q)
        if path == "/" or not any(m == "post" and p == path for m, p, *_ in ROUTES):       # the modem's CWMP session
            if path != "/" and any(p == path for m, p, *_ in ROUTES):                        # a known route, wrong method
                return self.send(405, [("Content-Type", "application/json"), ("Allow", "GET")], b'{"error": "method not allowed"}')
            return self.send(*acs.serve(self, self.body().decode("utf-8", "replace")))
        raw_body = self.body()
        if self.refused("post", path): return
        dev = devices.current()
        try:
            code, res = 200, None
            if path in ("/lte-lock", "/nr-lock"):    # ?cell=434@1550 (5G: 534@636576) keep it locked (guarded) | none
                cell = g("cell")
                if not (cell == "none" or re.fullmatch(r"\d{1,4}@\d{1,7}", cell)): raise ValueError("cell=PCI@EARFCN (5G: PCI@ARFCN) or none")
                try: res = guard.lock_request(cell, "nr" if path == "/nr-lock" else "lte")
                except RuntimeError as e: raise ValueError(str(e))
            elif path == "/mikrotik/scan":
                res = {"accepted": dev.scan_now(), "note": "GET /mikrotik/raw?prefix=Device.Cellular.X_MIKROTIK_CellDiagnostics"}
            elif path == "/mikrotik/set":
                res = {"accepted": dev.set_param(g("param"), g("value")), "param": g("param"), "value": g("value"), "note": "read back: GET /mikrotik/raw after the next inform"}
            elif path == "/mikrotik/at":
                n = dev.script(g("cmd"), g("restart") == "1"); res = {"queued": n, "script": dev.mt["scripts"][n]}
            elif path in ("/userdata", "/cell-ids"):    # body = JSON from the app (text/plain: no CORS preflight)
                if len(raw_body) > 4e6: raise ValueError("too big")
                data = json.loads(raw_body or b"null")
                if path == "/cell-ids":
                    ids = brain.REG.setdefault("ids", {}); n = 0
                    for k, v in (data or {}).items():
                        if re.fullmatch(r"(lte|nr):\d+:\d+", k) and str(v).isdigit() and k not in ids: ids[k] = int(v); n += 1
                    brain.save_registry(); res = {"added": n, "ids": len(ids)}
                else:
                    if g("name") not in UI_NAMES: raise ValueError("name=" + "|".join(UI_NAMES))
                    store.add(DATA, "ui:" + g("name"), {"time": common.stamp(), "name": g("name"), "data": data}, device="app"); res = {"saved": g("name")}
            elif path == "/antennas":          # ?auto=0&ant5=4&ant6=4 keep this mode (re-applied at every boot) | ?none = back to the env
                want = {"auto": g("auto") or "0", "ant5": g("ant5"), "ant6": g("ant6")}
                if not ("none" in q or (want["auto"] in ("0", "1") and want["ant5"] in ("1", "4") and want["ant6"] in ("1", "4"))):
                    raise ValueError("auto=0|1, ant5=1|4, ant6=1|4 (1 omni, 4 dir) or none")
                res = {"antennas": dev.antennas.keep(None if "none" in q else want)}
            elif path in ("/lock-test-skip", "/lock-test-stop"):
                what = "skip" if path.endswith("skip") else "stop"; ok = locktest.lt_cut(what)
                code, res = 200 if ok else 409, {what: ok, "cell": locktest.lt["running"]}
            elif path == "/lock-test-continue":   # align mode: aligned for the locked cell -> measure (?heading=118)
                ok = locktest.lt["waiting"]
                if ok: locktest.lt["heading"] = (g("heading") or "?")[:12]; locktest.lt["go"].set()
                code, res = 200 if ok else 409, {"measuring": ok, "cell": locktest.lt["running"]}
            elif path == "/lock-test":        # /lock-test?cells=434@1550,340@100[&rat=nr&anchor=340@1550&align=1] (a baseline "none" runs first)
                rat = g("rat", "lte")
                if rat not in ("lte", "nr"): raise ValueError("rat=lte|nr")
                raw = [c for c in g("cells").split(",") if c]
                bad = [c for c in raw if not re.fullmatch(guard.SPEC_RE, c)]
                if bad: raise ValueError("cells=PCI@EARFCN (rat=nr: PCI@ARFCN), comma separated, one cell per lock - bad: " + ", ".join(bad))
                cells = raw
                if rat == "nr":
                    for c in cells:
                        try: guard.nr_band(c.split("@")[1])
                        except ValueError: raise ValueError("cells=PCI@ARFCN with a known 5G band (n78 620000-653333, n28 151600-160600): " + c)
                spec = (g("anchor") or "").strip()
                # an explicit anchor pins every cell; without one each 5G cell uses its best known anchor (an NSA cell is served
                # only under some anchors) and the baseline the best anchor across the tested cells
                anchor = locktest.anchor_for(rat, spec) if (cells and spec) else None
                heading = (g("heading") or "")[:12]
                ok = bool(cells) and locktest.start(cells, g("align") == "1", rat, anchor, heading)
                code, res = 202 if ok else (400 if not cells else 429), {"started": ok, "rat": rat, "anchor": anchor if ok else None,
                                                                        "heading": heading if ok else "", "plan": ["none"] + cells if ok else []}
            elif path == "/antenna-sweep":
                ok = dev.antennas.start_sweep()
                code, res = 202 if ok else 429, {"started": ok}
            elif path == "/cells/forget":     # /cells/forget?cells=500/341,3150/341
                cells = [c for c in g("cells").split(",") if re.fullmatch(r"\d{1,6}/\d{1,4}", c)]
                if not cells: raise ValueError("cells=EARFCN/PCI,...")
                res = {"removed": brain.forget(cells)}; log("cells forgotten: %s" % res["removed"])
            elif path == "/cells/refill":     # /cells/refill?days=7 - rebuild the 5G cell <-> LTE anchor mapping from the samples
                res = {"cells": brain.backfill_anchors(max(1, min(90, int(g("days") or 7))))}
                log("anchor mapping refilled from the samples: %d 5G cells" % len(res["cells"]))
            elif path == "/tr069/read":        # /tr069/read?path=Device.IP.Diagnostics.Iperf3.
                p = g("path")
                if not re.fullmatch(r"Device\.[\w.-]+", p): raise ValueError("path=Device.<name> or Device.<subtree>.")
                acs.peek.update(want=p); code, res = 202, {"queued": p, "note": "values with the next inform (<= 60 s): GET /tr069/read"}
            elif path == "/captest":           # a capacity test now
                url = g("url")
                if url and not re.fullmatch(r"http://[\w.-]+(:\d+)?/\S*", url): raise ValueError("url=http://host[:port]/path (plain HTTP)")
                conn, secs = g("conn"), g("secs")
                if conn and not (conn.isdigit() and 1 <= int(conn) <= 8): raise ValueError("conn=1..8")
                if secs and not (secs.isdigit() and 1 <= int(secs) <= 30): raise ValueError("secs=1..30")
                mode, server = g("mode"), g("server")
                if mode not in ("", "iperf3", "tr143"): raise ValueError("mode=iperf3|tr143 (default: CAPTEST_METHOD)")
                if server and not re.fullmatch(r"[\w.-]+(:\d{1,5})?", server): raise ValueError("server=host[:port]")
                ok = captest.force(url=url or None, conn=int(conn) if conn else None, secs=int(secs) if secs else None,
                                   mode=mode or None, server=server or None, reverse=g("reverse", "1") != "0")
                code, res = 202 if ok else 429, {"started": ok, "note": "result in ~1-3 min: GET /captest" if ok else "a test or another job is running"}
            elif path == "/checks":            # /checks?name=all (or clock,link,sqm,lock,night) - runs now, pushes to Telegram
                res = selftest.run([n.strip() for n in g("name", "all").split(",") if n.strip()])
            elif path == "/db/prune":          # /db/prune?days=30&kind=samples&device=*&vacuum=1&csv=1
                res = store.prune(DATA, float(g("days", "0")), g("kind", "samples"), g("device", "*"), g("vacuum") == "1", g("csv") == "1")
                log("db prune %s: %s" % (u.query, res))
        except ValueError as e:
            code, res = 400, {"error": str(e)}
        self.send(code, [("Content-Type", "application/json"), ("Access-Control-Allow-Origin", "*")], json.dumps(res).encode())
