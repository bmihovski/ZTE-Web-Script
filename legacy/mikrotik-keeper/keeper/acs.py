"""TR-069 (CWMP) server side: the modem's ACS URL points here. Device neutral; the driver supplies what to read and its hooks.

One session = Inform -> InformResponse, empty POST -> GetParameterValues(driver READ) -> values -> optional SetParameterValues
(inform interval, queued writes, driver extras) -> optional driver RPC (MikroTik script Download) -> optional Reboot -> 204.
Fast mode: while a job runs (`common.busy()`), the inform interval is FAST_INTERVAL so queued writes go out within ~10 s.

Env: PORT (7557), INFORM_INTERVAL (60), FAST_INTERVAL (10), MIN_SAMPLE_S (55 - one sample a minute, also in fast mode).
"""
import re, threading, time, xml.etree.ElementTree as ET

from keeper import brain, common, devices, monitor
from keeper.common import E, event, log, raw, stamp

PORT, INTERVAL = int(E("PORT", "7557")), int(E("INFORM_INTERVAL", "60"))
FAST_INTERVAL = int(E("FAST_INTERVAL", "10"))
MIN_SAMPLE_S = float(E("MIN_SAMPLE_S", "55"))
INFORM_RE = re.compile(r":Inform[ >]")
ENV = ('<?xml version="1.0" encoding="UTF-8"?>\n<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/" '
       'xmlns:soap-enc="http://schemas.xmlsoap.org/soap/encoding/" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cwmp="{ns}"><soap-env:Header>'
       '<cwmp:ID soap-env:mustUnderstand="1">{id}</cwmp:ID></soap-env:Header><soap-env:Body>{body}</soap-env:Body></soap-env:Envelope>')
lock = threading.Lock()                 # one CWMP message at a time
st = {"ns": "urn:dslforum-org:cwmp-1-0", "step": None, "last_inform": 0.0, "boots": 0, "n": 0, "host": None}
tr = {"writes": [], "sent": [], "ok": None, "ev": threading.Event(), "vals_n": 0, "last_sample": -1e9}      # monotonic times: a clock jump changes nothing
common.register_busy("tr069 write", lambda: tr["writes"])


# ---------- SOAP helpers ----------
def local(tag):
    """`{urn:...}Inform` -> `Inform`."""
    return tag.rsplit("}", 1)[-1]


def esc(s):
    """XML-escape text."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def find(el, name):
    """All descendants of `el` with local tag `name`."""
    return [e for e in el.iter() if local(e.tag) == name]


def text(el, name):
    """Text of the first descendant `name`, '' if none."""
    f = find(el, name)
    return (f[0].text or "") if f else ""


def gpv(names):
    """GetParameterValues body."""
    return ('<cwmp:GetParameterValues><ParameterNames soap-enc:arrayType="xsd:string[%d]">%s</ParameterNames></cwmp:GetParameterValues>'
            % (len(names), "".join("<string>%s</string>" % esc(n) for n in names)))


def spv(pairs):
    """SetParameterValues body from [(name, xsd type, value)]."""
    items = "".join('<ParameterValueStruct><Name>%s</Name><Value xsi:type="%s">%s</Value></ParameterValueStruct>' % p for p in pairs)
    return ('<cwmp:SetParameterValues><ParameterList soap-enc:arrayType="cwmp:ParameterValueStruct[%d]">%s</ParameterList>'
            '<ParameterKey>keeper</ParameterKey></cwmp:SetParameterValues>' % (len(pairs), items))


# ---------- writes from other modules ----------
def fast():
    """Fast inform mode: a job that talks TR-069 is running (lock test, lock change, queued write / script)."""
    return bool(common.busy(fast_only=True))


def tr069_set(pairs, reboot=False, timeout=180):
    """Queue SetParameterValues for the next session and wait for the answer (fast mode makes that <= ~10 s).
    reboot=True: a TR-069 Reboot follows in the same session once the values are accepted. True when the modem accepted them."""
    tr["ev"].clear(); tr["ok"] = None
    if reboot: brain.ctx["pending"] = "reboot"
    tr["writes"] = list(pairs)
    if not tr["ev"].wait(timeout):
        tr["writes"] = []; brain.ctx["pending"] = None if reboot else brain.ctx.get("pending")
        return False
    return bool(tr["ok"])


def wait_informs(n=1, timeout=180):
    """Block until `n` more value reads arrived (or timeout). True if they did."""
    start = tr["vals_n"]; t0 = time.time()
    while tr["vals_n"] < start + n and time.time() - t0 < timeout: time.sleep(0.5)
    return tr["vals_n"] >= start + n


def reboot_if_pending():
    """End of a session: the driver's queued RPC first (MikroTik script Download), then the brain's / a lock's Reboot."""
    body = devices.current().pending_rpc(st.get("host") or "192.168.6.250:%d" % PORT)
    if body:
        st["step"] = "download"; return body, False
    if brain.ctx["pending"] == "reboot":
        brain.ctx["pending"] = None; st["step"] = "reboot"
        return '<cwmp:Reboot><CommandKey>keeper</CommandKey></cwmp:Reboot>', False
    return None, False


# ---------- one CPE message ----------
def handle(body):
    """CPE message -> (ACS reply body or None for 204, echo the CPE's cwmp:ID?)."""
    dev = devices.current()
    if not body.strip():
        if st["step"] == "inform":
            st["step"] = "read"
            return gpv(dev.READ), False
        return None, False
    msg = next(iter(find(ET.fromstring(body), "Body")[0]))
    kind = local(msg.tag)
    if msg.tag.startswith("{"): st["ns"] = msg.tag[1:].split("}")[0]
    if kind == "Inform":
        events = [text(e, "EventCode") for e in find(msg, "EventStruct")]
        st["last_inform"] = time.monotonic(); st["step"] = "inform"
        common.check_clock(text(msg, "CurrentTime"))
        if any(e.startswith(("0 ", "1 ")) for e in events):        # BOOTSTRAP / BOOT
            st["boots"] += 1
            event({"event": "boot " + "+".join(events)}); log("%s boot inform %s" % (dev.NAME, events))
            dev.on_boot()
        return '<cwmp:InformResponse><MaxEnvelopes>1</MaxEnvelopes></cwmp:InformResponse>', True
    if kind == "DownloadResponse":
        log("download accepted (status %s)" % text(msg, "Status")); dev.on_rpc(kind, msg, st["step"])
        return None, False
    if kind in ("TransferComplete", "AutonomousTransferComplete"):
        log("%s: %s %s" % (kind, text(msg, "FaultCode"), text(msg, "FaultString"))); dev.on_rpc(kind, msg, st["step"])
        return '<cwmp:%sResponse/>' % kind, True
    if kind == "GetRPCMethods":
        m = ["Inform", "TransferComplete", "AutonomousTransferComplete", "GetRPCMethods"]
        return ('<cwmp:GetRPCMethodsResponse><MethodList soap-enc:arrayType="xsd:string[%d]">%s</MethodList></cwmp:GetRPCMethodsResponse>'
                % (len(m), "".join("<string>%s</string>" % x for x in m))), True
    if kind == "GetParameterValuesResponse":
        vals = {text(p, "Name"): text(p, "Value") for p in find(msg, "ParameterValueStruct")}
        extra = dev.on_values(vals)                               # driver first: e.g. the ZTE's own lock values for the guard
        tr["vals_n"] += 1                                         # after on_values: wait_informs() callers then see this read's values
        if time.monotonic() - tr["last_sample"] >= MIN_SAMPLE_S:      # one sample a minute, also in fast mode
            tr["last_sample"] = time.monotonic(); monitor.sample(vals)
        want, iv = [], FAST_INTERVAL if fast() else INTERVAL
        if vals.get("Device.ManagementServer.PeriodicInformInterval") != str(iv) or vals.get("Device.ManagementServer.PeriodicInformEnable") not in ("1", "true"):
            log("setting PeriodicInformInterval %s -> %d%s" % (vals.get("Device.ManagementServer.PeriodicInformInterval"), iv, " (fast mode)" if iv == FAST_INTERVAL else ""))
            want += [("Device.ManagementServer.PeriodicInformEnable", "xsd:boolean", "1"),
                     ("Device.ManagementServer.PeriodicInformInterval", "xsd:unsignedInt", str(iv))]
        if tr["writes"]:
            tr["sent"], tr["writes"] = tr["writes"], []; want += tr["sent"]
        want += extra
        if want:
            st["step"] = "set"
            return spv(want), False
        return reboot_if_pending()
    if kind == "SetParameterValuesResponse":
        if tr["sent"]: tr["ok"] = True; tr["sent"] = []; tr["ev"].set()
        return reboot_if_pending()
    if kind == "RebootResponse":
        log("reboot accepted by the %s" % dev.NAME); event({"event": "reboot sent"})
        return None, False
    if kind == "Fault":
        log("CPE fault %s %s (step %s)" % (text(msg, "FaultCode"), text(msg, "FaultString"), st["step"]))
        dev.on_rpc(kind, msg, st["step"])
        if st["step"] == "set" and tr["sent"]:                  # our values were refused: no reboot, the caller falls back
            tr["ok"] = False; tr["sent"] = []; brain.ctx["pending"] = None; tr["ev"].set()
        return None, False
    return None, False


def serve(handler, req):
    """One POST from a CPE (the HTTP layer calls this) -> (status, headers, body). Another maker's CPE pointed here is answered
    and left alone; the driver may relay the session to the operator (ZTE check-in)."""
    dev = devices.current()
    if handler.headers.get("Host"): st["host"] = handler.headers["Host"]          # how the CPE reaches us (for download URLs)
    with lock:
        try:
            relayed = dev.relay(handler, req)
            if relayed: return relayed
            raw("CPE -> keeper", req)
            maker = re.search(r"<Manufacturer>([^<]*)<", req) if INFORM_RE.search(req) else None
            if maker:                                  # one device at a time: another maker, or another model of the same maker (two ZTEs)
                prod = (re.search(r"<ProductClass>([^<]*)<", req) or [None, ""])[1].strip()
                handler.foreign = bool(maker.group(1).strip()) and dev.MAKER.lower() not in maker.group(1).lower()
                if not handler.foreign and prod:       # same maker: pin to one model (CPE env, else the first seen) so a 2nd ZTE can't mix in
                    want = E("CPE", "") or common.state().get("cpe") or ""
                    if want: handler.foreign = want.lower() not in prod.lower()
                    else: common.state({"cpe": prod}); log("managing the %s %s (first seen) - set CPE= to pin it" % (maker.group(1), prod))
                if handler.foreign and not st.get("foreign_logged"):
                    st["foreign_logged"] = True
                    log("ignoring a %s CPE (%s) - this keeper manages %s" % (maker.group(1), prod or "?", E("CPE", "") or common.state().get("cpe") or dev.MAKER))
            if getattr(handler, "foreign", False):
                if not INFORM_RE.search(req): handler.foreign = False; return 204, [], b""
                reply, echo = '<cwmp:InformResponse><MaxEnvelopes>1</MaxEnvelopes></cwmp:InformResponse>', True
            else:
                reply, echo = handle(req)
        except Exception as e:
            log("error %r" % e); reply, echo = None, False
        if reply is None:
            st["step"] = None
            return 204, [], b""
        cid = re.search(r"<(?:\w+:)?ID[^>]*>([^<]*)<", req)
        st["n"] += 1
        b = ENV.format(ns=st["ns"], id=esc(cid.group(1) if echo and cid else "keeper-%d" % st["n"]), body=reply).encode()
        raw("keeper -> CPE", b.decode())
        return 200, [("Content-Type", 'text/xml; charset="utf-8"')], b


def watchdog():
    """Thread: warn once if the modem stops informing (e.g. its ACS URL was moved away from us)."""
    warned = False
    while True:
        time.sleep(60)
        quiet = time.monotonic() - st["last_inform"]
        if st["last_inform"] and quiet > 5 * INTERVAL + 300 and not common.busy():     # a lock test reboots the modem on purpose: not a real outage
            if not warned:
                log("no inform from the %s for %d s - check its TR-069 Server URL" % (devices.current().NAME, quiet)); event({"event": "no inform"})
            warned = True
        else:
            warned = False
