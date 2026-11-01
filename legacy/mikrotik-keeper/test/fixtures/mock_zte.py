# fake ZTE MC889 goform API for testing keeper.sh
import hashlib, http.server, json, threading, time, urllib.parse, sys
S = lambda x: hashlib.sha256(x.encode()).hexdigest().upper()
PW = "secret"
st = {"boot": time.time() - 120, "sessions": set(), "sets": [], "LD": "LD1234", "RD": "RD5678", "logins": 0, "bad_ad": 0, "no_referer": 0, "ppp_up_at": 0,
      "refuse": set()}   # goformIds the fake ZTE refuses (the browser page took the session, like the real one)
script = []   # (from_s, fields) timeline relative to start
T0 = time.time()
def fields():
    el = time.time() - T0; f = {"network_type": "ENDC", "nr5g_pci": "216", "nr5g_action_channel": "636576", "Z5g_SINR": "9.5", "Z5g_rsrp": "-79",
        "5g_rx0_rsrp": "-83", "5g_rx1_rsrp": "-88", "lte_pci": "154", "wan_active_channel": "3350", "lte_snr": "1.4", "nr5g_cell_lock": "", "Z5g_rssi": "-74.0", "lte_rssi": "-55"}
    for t, upd in script:
        if el >= t: f.update(upd)
    return f
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def sid(self):
        c = self.headers.get("Cookie", ""); return c.split("sid=")[1].split(";")[0] if "sid=" in c else None
    def reply(self, obj, cookie=None):
        b = json.dumps(obj, separators=(",", ":")).encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        if cookie: self.send_header("Set-Cookie", "sid=%s; path=/" % cookie)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if not self.headers.get("Referer"): st["no_referer"] += 1
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); out = {}; f = fields()
        logged = self.sid() in st["sessions"]
        for k in q.get("cmd", [""])[0].split(","):
            if k == "loginfo": out[k] = "ok" if logged else ""
            elif k == "LD": out[k] = st["LD"]
            elif k == "RD": out[k] = st["RD"]
            elif k == "wa_inner_version": out[k] = "MC889V2"
            elif k == "cr_version": out[k] = ""
            elif k == "ppp_status": out[k] = "ppp_connected" if time.time() >= st["ppp_up_at"] else "ppp_disconnected"   # public, like the real ZTE
            elif k == "system_uptime": out[k] = str(int(time.time() - st["boot"])) if logged else ""
            else: out[k] = f.get(k, "") if logged else ""
        self.reply(out)
    def do_POST(self):
        d = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        if d.get("AD") != S(S("MC889V2" + "") + st["RD"]): st["bad_ad"] += 1; return self.reply({"result": "failure"})
        if d["goformId"] == "LOGIN":
            st["logins"] += 1
            if d["password"] == S(S(PW) + st["LD"]):
                sid = str(st["logins"]); st["sessions"] = {sid}; return self.reply({"result": "0"}, sid)   # one web session at a time, like the real ZTE
            return self.reply({"result": "3"})
        if self.sid() not in st["sessions"]: return self.reply({"result": "failure"})
        if d["goformId"] in st["refuse"]: return self.reply({"result": "failure"})   # refused: the mode did not change (not recorded as set)
        st["sets"].append((round(time.time() - T0, 1), d["goformId"], d.get("antenna_name", ""), d.get("state", d.get("ant_switch_enable", "%s@%s" % (d.get("lte_pci_lock"), d.get("lte_earfcn_lock")) if "lte_pci_lock" in d else d.get("nr5g_cell_lock", "")))))
        self.reply({"result": "success"})
def browser_login_at(sec):   # someone logs in on the router web page -> the keeper's session ends
    def r(): time.sleep(sec); st["sessions"] = {"browser"}; print("MOCK browser login", flush=True)
    threading.Thread(target=r, daemon=True).start()
def reboot_at(sec, ppp_down=0):
    def r(): time.sleep(sec); st["boot"] = time.time() - 59; st["ppp_up_at"] = time.time() + ppp_down; st["sessions"].clear(); print("MOCK reboot", flush=True)
    threading.Thread(target=r, daemon=True).start()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
