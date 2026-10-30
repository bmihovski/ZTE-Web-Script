"""Operator check-in (ZTE only): once every CHECKIN_DAYS one TR-069 session is relayed to the operator ACS (YETTEL_URL).

The ZTE keeps pointing at the keeper and answers the operator's auth itself - nothing is stored. CHECKIN_MODE=observe (default):
the operator may read, every change request (set values, Download, Reboot, FactoryReset ...) is answered "9001 Request denied"
and logged as `operator wanted (denied)`. CHECKIN_MODE=full passes everything (use once to take a firmware the log showed).
Env: CHECKIN_DAYS (30, 0 = never), CHECKIN_MODE (observe | full), YETTEL_URL.
"""
import http.client, re, ssl, time, urllib.parse, xml.etree.ElementTree as ET

from keeper import acs
from keeper.common import E, event, log, raw, state

CHECKIN_DAYS = float(E("CHECKIN_DAYS", "30"))
CHECKIN_MODE = E("CHECKIN_MODE", "observe")
YETTEL = E("YETTEL_URL", "https://acs.fssd.bg:10305/acs/bulgaria")
CHANGES = ("SetParameterValues", "SetParameterAttributes", "AddObject", "DeleteObject", "Download", "ScheduleDownload",
           "Upload", "Reboot", "FactoryReset", "ChangeDUState", "ScheduleInform")
FAULT = ('<soap-env:Fault><faultcode>Client</faultcode><faultstring>CWMP fault</faultstring><detail><cwmp:Fault>'
         '<FaultCode>9001</FaultCode><FaultString>Request denied</FaultString></cwmp:Fault></detail></soap-env:Fault>')
st = {"proxy": None, "op_cookies": {}}       # proxy = (url parts, connection) while a check-in session runs


def due():
    """A check-in is due (CHECKIN_DAYS since the last one)."""
    return CHECKIN_DAYS > 0 and time.time() - state().get("last_checkin", 0) >= CHECKIN_DAYS * 86400


def op_post(body, hdr):
    """POST one message to the operator ACS on the relay connection (reconnects once)."""
    u, conn = st["proxy"]
    for attempt in (1, 2):              # the operator may close the connection between requests (e.g. after a 401)
        if conn is None:
            if u.scheme == "https":
                ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE   # ponytail: the ZTE doesn't pin it either
                conn = http.client.HTTPSConnection(u.hostname, u.port or 443, context=ctx, timeout=30)
            else:
                conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=30)
            st["proxy"] = (u, conn)
        try:
            conn.request("POST", u.path or "/", body.encode(), hdr)
            r = conn.getresponse(); data = r.read()
            for k, v in r.getheaders():          # keep the operator's session cookie for our own "denied" answers
                if k.lower() == "set-cookie":
                    name, _, val = v.split(";")[0].partition("=")
                    st["op_cookies"][name.strip()] = val
            raw("-> operator", body); raw("operator %s" % r.status, data.decode("utf-8", "replace"))
            return r, data
        except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
            conn.close(); conn = None
            if attempt == 2: raise


def describe(msg):
    """Short, password-free text of an operator request."""
    kind = acs.local(msg.tag)
    if kind == "Download":
        return "Download %s %s (%s bytes) %s" % (acs.text(msg, "FileType"), acs.text(msg, "URL"), acs.text(msg, "FileSize"), acs.text(msg, "TargetFileName"))
    pairs = [(acs.text(p, "Name"), acs.text(p, "Value")) for p in acs.find(msg, "ParameterValueStruct")]
    return "%s %s" % (kind, ", ".join("%s=%s" % (n, "[masked]" if re.search(r"pass|key|secret", n, re.I) else v) for n, v in pairs))


def relay_one(handler, body):
    """Forward one CPE request to the operator ACS; observe mode answers change requests itself (denied). -> (status, headers, body)."""
    if st["proxy"] is None:
        st["proxy"] = (urllib.parse.urlsplit(YETTEL), None); st["op_cookies"] = {}
        log("operator check-in (%s): relaying this session to %s" % (CHECKIN_MODE, YETTEL))
    hdr = {k: v for k, v in handler.headers.items() if k.lower() in ("content-type", "soapaction", "authorization", "cookie")}
    r, data = op_post(body, hdr)
    cut = False                         # True: we end the session ourselves (operator refused our "denied" answer)
    while CHECKIN_MODE == "observe" and r.status == 200 and data.strip():
        try:
            msg = next(iter(acs.find(ET.fromstring(data), "Body")[0]))
        except (ET.ParseError, IndexError, StopIteration):
            break
        if acs.local(msg.tag) not in CHANGES:
            break
        what = describe(msg)
        log("operator wanted: %s - denied (observe mode)" % what)
        event({"event": "operator wanted (denied)", "to": what})
        cid = re.search(r"<(?:\w+:)?ID[^>]*>([^<]*)<", data.decode("utf-8", "replace"))
        cookie = "; ".join("%s=%s" % kv for kv in st["op_cookies"].items()) or handler.headers.get("Cookie", "")
        h = {"Content-Type": 'text/xml; charset="utf-8"'}
        if cookie: h["Cookie"] = cookie
        ns = msg.tag[1:].split("}")[0] if msg.tag.startswith("{") else acs.st["ns"]
        r, data = op_post(acs.ENV.format(ns=ns, id=acs.esc(cid.group(1) if cid else ""), body=FAULT), h)
        if r.status == 401:
            log("operator wants auth again for the denied answer - ending the check-in")
            cut = True
            break
    out = [(k, v) for k, v in r.getheaders() if k.lower() in ("content-type", "www-authenticate", "set-cookie", "soapaction")]
    if cut or r.status == 204 or (r.status == 200 and not data.strip()):     # session over
        u, conn = st["proxy"]
        if conn: conn.close()
        st["proxy"] = None
        state({"last_checkin": time.time()})
        log("operator check-in done")
        event({"event": "operator check-in done"})
        if cut: return 204, [], b""
    return r.status, out, data


def relay(handler, body):
    """Driver interface: relay this message when a check-in runs or is due (an Inform starts one); None = handle it ourselves.
    A failed check-in is retried a day later."""
    if st["proxy"] is None and not (due() and acs.INFORM_RE.search(body)): return None
    try:
        return relay_one(handler, body)
    except Exception as e:
        log("operator check-in failed: %r - retry tomorrow" % e)
        st["proxy"] = None
        state({"last_checkin": time.time() - max(0, CHECKIN_DAYS - 1) * 86400})
        return None
