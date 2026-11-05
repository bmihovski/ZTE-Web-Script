"""Telegram alerts: one message when something needs you (lock released / missing, no mobile service, reboots, calls at risk,
night-screen recommendation, ZTE silent, firmware activity,
router clock was wrong after a power cut - sent once it is right again). Env: TG_TOKEN (from @BotFather), TG_CHAT (your chat id); off when empty.
Each kind of alert at most once per ALERT_GAP_MIN (30); sending never blocks or breaks the keeper."""
import json, os, re, threading, time, urllib.parse, urllib.request

TOKEN, CHAT = os.environ.get("TG_TOKEN", ""), os.environ.get("TG_CHAT", "")
GAP = float(os.environ.get("ALERT_GAP_MIN", "30")) * 60
CALL_REBOOT_GRACE_S = float(os.environ.get("CALL_REBOOT_GRACE_S", "900"))   # no call-risk alarm in the first 15 min after a modem reboot/deploy - the reconnect is not a call problem
_last, sent, _retry = {}, [], []
_send_lock = threading.Lock()          # serialise actual sends: no interleaved "telegram alert failed" prints during a reboot storm          # _retry: messages that failed (no internet, modem rebooting) - sent again later
# events (keeper.event "event" field) worth a message: regex -> alert kind
RULES = [(r"lock .*removed|lock .* still set|lock .* missing", "lock"), (r"^(lte|5g) lock ", "lock"), (r"^reboot (queued|held)$", "reboot"),
         (r"^no inform$", "silent"), (r"^FOTA state$", "firmware"), (r"^night screen$", "night"), (r"^clock verified$", "clock")]


def send(text, kind, now=None):
    """queue one message; False when this kind was sent less than ALERT_GAP_MIN ago (or alerts are off)"""
    now = now or time.time()
    if not (TOKEN and CHAT) or now - _last.get(kind, 0) < GAP: return False
    _last[kind] = now; sent.append((now, kind, text))
    threading.Thread(target=_post, args=(text,), daemon=True).start()
    return True


def _post(text):
    with _send_lock:
        try:
            data = urllib.parse.urlencode({"chat_id": CHAT, "text": text[:3500], "disable_web_page_preview": "true"}).encode()
            urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % TOKEN, data=data, timeout=10).read()
        except Exception as e:
            print("telegram alert failed: %s - retried within a minute" % type(e).__name__, flush=True)   # never the URL: it holds the token
            if len(_retry) < 20: _retry.append((time.time(), text))


def report(text, now=None):
    """On-demand / scheduled push (self-test): no ALERT_GAP gate. False when alerts are off."""
    if not (TOKEN and CHAT): return False
    sent.append((now or time.time(), "report", text))
    threading.Thread(target=_post, args=(text,), daemon=True).start()
    return True


def flush(now=None):
    """Send the messages that failed before (called every minute); each says when it happened."""
    now = now or time.time()
    while _retry:
        t, text = _retry.pop(0)
        threading.Thread(target=_post, args=(text if now - t < 90 else "[%s] %s" % (time.strftime("%H:%M", time.localtime(t)), text),), daemon=True).start()


def on_event(device, ev):
    """keeper.event() hook"""
    for rx, kind in RULES:
        if re.search(rx, ev.get("event", "")):
            return send("%s: %s%s%s" % (device, ev.get("event"), " -> " + ev["to"] if ev.get("to") else "", " (%s)" % ev["why"] if ev.get("why") else ""), kind)
    return False


def check_calls(device, rows, now=None):
    """calls at risk: under 80 % call-ready over the last 15 minutes with traffic (also sends the messages that failed before)"""
    flush(now)
    busy = [r for r in rows[-15:] if r.get("call_ok") in ("0", "1")]
    if len(busy) >= 10 and sum(r["call_ok"] == "1" for r in busy) / len(busy) < 0.8:
        last = busy[-1]
        try: up = float(last.get("uptime_s"))
        except (TypeError, ValueError): up = None
        if up is not None and up < CALL_REBOOT_GRACE_S:
            return False                       # fresh reboot/deploy: the reconnect minutes are not a call problem
        return send("%s: calls at risk - %d of the last %d min not call-ready (latency p95 %s ms, jitter %s ms, loss %s %%; 5G %s, LTE %s)"
                    % (device, sum(r["call_ok"] == "0" for r in busy), len(busy), last.get("lat_p95"), last.get("jitter_ms"),
                       last.get("loss_pct"), last.get("nr_cell"), last.get("lte_cell")), "calls", now)
    return False


if __name__ == "__main__":          # self-check (nothing is sent: _post replaced)
    TOKEN, CHAT = "x", "y"; out = []; _post = out.append
    assert on_event("ZTE", {"event": "lte lock removed", "why": "released LTE 340@100: no LTE service for 3 min"})
    assert not on_event("ZTE", {"event": "lte lock 340@100", "why": "restored"})              # same kind within 30 min
    assert on_event("ZTE", {"event": "reboot queued", "to": "no mobile service for 5 min"}) and not on_event("ZTE", {"event": "switch"})
    rows = [{"call_ok": "0" if i % 2 else "1", "lat_p95": "180", "jitter_ms": "40", "loss_pct": "0", "nr_cell": "no5G", "lte_cell": "100/340"} for i in range(15)]
    assert check_calls("ZTE", rows) and not check_calls("ZTE", rows)
    assert not check_calls("ATL", [{"call_ok": "1"}] * 15)
    assert not check_calls("ZTE", [dict(r, uptime_s="120") for r in rows], now=2_000_000_000)    # modem up 120 s (deploy/reboot) -> suppressed
    assert check_calls("ZTE", [dict(r, uptime_s="3600") for r in rows], now=2_000_000_000)       # modem up 1 h -> real call-risk still fires
    fails = []
    def _post(text):                                                    # first try fails (no internet), the retry goes out
        if not fails: fails.append(text); _retry.append((900.0, text)); return
        out.append(text)
    n = len(out); send("ZTE: lte lock removed", "x", 100000.0); time.sleep(0.2); flush(100000.0); time.sleep(0.2)
    assert out[n:] == ["[%s] ZTE: lte lock removed" % time.strftime("%H:%M", time.localtime(900.0))] and not _retry, out[n:]
    print("OK alert", [k for _, k, _ in sent])
