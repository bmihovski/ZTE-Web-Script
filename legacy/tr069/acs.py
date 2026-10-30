#!/usr/bin/env python3
"""Minimal one-shot TR-069 ACS: dumps the whole parameter tree of a CPE (ZTE MC889) to CSV.

Run:   python3 acs.py [port]          (default 7557)
Then on the ZTE (developer page #tr069config) set Server URL to  http://<this computer's LAN IP>:<port>/  and Apply.
MikroTik (ATL 5G, RouterOS 7 with the tr069-client package):  /tr069-client set enabled=yes acs-url=http://<this computer's LAN IP>:<port>/
  periodic-inform-enabled=yes periodic-inform-interval=60s   - then  /tr069-client set enabled=no  afterwards.
The ZTE calls in, the script asks for every parameter name, then every value, writes
tr069-dump-<time>.csv next to this file and ends the session. Ctrl+C to stop.
Afterwards put the operator's Server URL back on the ZTE.

No auth is requested, so the ZTE never sends its ACS username/password here.
Values of password/key parameters are masked in the CSV (the spec says the CPE returns them empty anyway).
"""
import csv, datetime, os, re, socket, sys, xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7557
CHUNK = 40                                   # parameter values per request; halved on a fault
SECRET = re.compile(r"password|passphrase|presharedkey|wepkey|secret", re.I)
HERE = os.path.dirname(os.path.abspath(__file__))

ENV = ('<?xml version="1.0" encoding="UTF-8"?>\n<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/" '
       'xmlns:soap-enc="http://schemas.xmlsoap.org/soap/encoding/" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cwmp="{ns}"><soap-env:Header>'
       '<cwmp:ID soap-env:mustUnderstand="1">{id}</cwmp:ID></soap-env:Header><soap-env:Body>{body}</soap-env:Body></soap-env:Envelope>')

st = {"ns": "urn:dslforum-org:cwmp-1-0", "root": "", "names": {}, "queue": [], "sent": None, "values": {}, "faults": {}, "n": 0,
      "reply_id": None, "informs": 0, "done": False}
RAW = os.path.join(HERE, "tr069-raw.log")   # every message both ways, for debugging


def local(tag): return tag.rsplit("}", 1)[-1]
def esc(s): return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def log(*a): print(datetime.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def raw(head, body):
    with open(RAW, "a") as f:
        f.write("==== %s %s\n%s\n" % (datetime.datetime.now().strftime("%H:%M:%S"), head, body))


def find(el, name):
    return [e for e in el.iter() if local(e.tag) == name]


def text(el, name):
    f = find(el, name)
    return (f[0].text or "") if f else ""


def gpv(names):
    st["sent"] = names
    items = "".join("<string>%s</string>" % esc(n) for n in names)
    return '<cwmp:GetParameterValues><ParameterNames soap-enc:arrayType="xsd:string[%d]">%s</ParameterNames></cwmp:GetParameterValues>' % (len(names), items)


def next_request():
    """body of the next ACS request, or None = end of session"""
    if st["queue"]:
        return gpv(st["queue"].pop(0))
    if st["names"]:
        write_csv()
    return None


def write_csv():
    path = os.path.join(HERE, "tr069-dump-%s.csv" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "value", "type", "writable", "fault"])
        for n in sorted(st["names"]):
            v, t = st["values"].get(n, ("", ""))
            if SECRET.search(n) and v:
                v = "[masked, %d chars]" % len(v)
            w.writerow([n, v, t, st["names"][n], st["faults"].get(n, "")])
    st["done"] = True
    log("wrote %s (%d parameters, %d values, %d faults)" % (path, len(st["names"]), len(st["values"]), len(st["faults"])))
    st["names"] = {}


def handle(body):
    """CPE message -> ACS reply body (None = 204, session over)"""
    if not body.strip():                             # empty POST: CPE has nothing more to say, our turn
        if st["n"] == 1 and not st["names"] and not st["done"]:
            log("asking for all parameter names under '%s'" % st["root"])
            st["n"] = 2
            return '<cwmp:GetParameterNames><ParameterPath>%s</ParameterPath><NextLevel>false</NextLevel></cwmp:GetParameterNames>' % esc(st["root"])
        return next_request()
    root = ET.fromstring(body)
    st["reply_id"] = text(root, "ID") or None
    msg = next(e for e in find(root, "Body")[0])
    kind = local(msg.tag)
    st["ns"] = msg.tag[1:].split("}")[0] if msg.tag.startswith("{") else st["ns"]
    if kind == "Inform":
        events = [text(e, "EventCode") for e in find(msg, "EventStruct")]
        params = {text(p, "Name"): text(p, "Value") for p in find(msg, "ParameterValueStruct")}
        first = next(iter(params), "InternetGatewayDevice.")
        st["root"] = first.split(".")[0] + "."
        st["informs"] += 1
        log("Inform #%d from %s %s, events %s, data model root %s" % (st["informs"], text(msg, "Manufacturer"), text(msg, "ProductClass"), events, st["root"]))
        if st["informs"] == 1:
            for k, v in params.items():
                log("   %s = %s" % (k, "[masked]" if SECRET.search(k) else v))
        st["n"] = 1
        return '<cwmp:InformResponse><MaxEnvelopes>1</MaxEnvelopes></cwmp:InformResponse>'
    if kind == "GetParameterNamesResponse":
        for p in find(msg, "ParameterInfoStruct"):
            n = text(p, "Name")
            if not n.endswith("."):
                st["names"][n] = text(p, "Writable")
        names = sorted(st["names"])
        st["queue"] = [names[i:i + CHUNK] for i in range(0, len(names), CHUNK)]
        log("%d parameters, reading values in %d requests" % (len(names), len(st["queue"])))
        return next_request()
    if kind == "GetParameterValuesResponse":
        for p in find(msg, "ParameterValueStruct"):
            v = find(p, "Value")[0]
            st["values"][text(p, "Name")] = (v.text or "", v.get("{http://www.w3.org/2001/XMLSchema-instance}type", ""))
        left = sum(len(c) for c in st["queue"])
        if left and left % 400 < CHUNK:
            log("%d values read, %d left" % (len(st["values"]), left))
        return next_request()
    if kind == "Fault":
        code, why = text(msg, "FaultCode"), text(msg, "FaultString")
        sent = st["sent"] or []
        if len(sent) > 1:                            # one bad name spoils the chunk: split and retry
            h = len(sent) // 2
            st["queue"][:0] = [sent[:h], sent[h:]]
        else:
            for n in sent:
                st["faults"][n] = "%s %s" % (code, why)
            log("fault %s %s for %s" % (code, why, sent or "(no request)"))
        return next_request()
    log("unhandled CPE message %s - ending session" % kind)
    return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    return out
                out += self.rfile.read(size)
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        try:
            req = self.body().decode("utf-8", "replace")
            raw("CPE -> %s %s" % (self.command, dict(self.headers)), req)
            reply = handle(req)
        except Exception as e:                       # keep the server up, end this session
            log("error:", repr(e))
            reply = None
        if reply is None:
            raw("ACS -> 204", "")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        st["id"] = st.get("id", 0) + 1
        rid = st["reply_id"] if reply.startswith("<cwmp:InformResponse") else None   # must echo the ID of the CPE's Inform, or the CPE retries
        b = ENV.format(ns=st["ns"], id=esc(rid or "acs-%d" % st["id"]), body=reply).encode()
        raw("ACS ->", b.decode())
        self.send_response(200)
        self.send_header("Content-Type", 'text/xml; charset="utf-8"')
        self.send_header("SOAPAction", "")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    do_GET = do_POST


if __name__ == "__main__":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.168.6.5", 80)); ip = s.getsockname()[0]; s.close()
    except OSError:
        ip = "<this computer's IP>"
    log("ACS listening - set the ZTE Server URL to  http://%s:%d/" % (ip, PORT))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
