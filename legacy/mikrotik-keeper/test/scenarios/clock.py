# the router's clock: verified by the modem's Inform time or by the internet's HTTP Date (2026-11-17: no NTP until the WAN has its
# dynamic address; the modem's own clock can be the wrong one). Whose clock was off = did OUR wall clock jump (NTP steps it, the
# monotonic clock does not move with it): jumped -> the router's, else the modem's.
import email.utils, http.server, os, sys, tempfile, threading, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
sys.path.insert(0, ROOT)
from keeper import common, store
evs = lambda: store.rows(common.DATA, "events")
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
hdr = lambda t: email.utils.formatdate(t, usegmt=True)
T = time.time()

# 1. fresh start, the modem's clock +86 min, ours right (constructed: we can't set the modem's clock): the internet confirms ours ->
#    verified, the modem blamed (case 6 is the real 2026-11-17 cut, where it was the router's clock)
assert common.check_clock(iso(T + 86 * 60), T) is False and not common.clock_ok()
assert common.check_net(None, T + 30) is False                                    # no internet yet (no WAN address): no evidence
assert common.check_net(hdr(T + 30), T + 30) is True and common.clock_ok()
e = evs()[-1]; assert e["event"] == "modem clock wrong" and "internet" in e["why"] and "records" not in e["why"], e
n = len(evs()); assert common.check_clock(iso(T + 86 * 60), T + 90) is True and len(evs()) == n   # still off: trusted, quiet

# 2. fresh start after a power cut, ours 7 h behind: the internet says so -> stay unverified (NTP not synced yet), no event
common.clock.update(ok=None, skew=None, modem_bad=False); B = T - 7 * 3600
assert common.check_clock(iso(T), B) is False
n = len(evs()); assert common.check_net(hdr(T + 60), B + 60) is False and common.check_net(hdr(T + 120), B + 120) is False
assert len(evs()) == n and not common.clock_ok()
assert common.check_net(hdr(T + 180), T + 180) is True                             # NTP stepped ours: the internet agrees
e = evs()[-1]; assert e["event"] == "clock verified" and "router's clock was 420 min behind" in e["why"] and "internet" in e["why"], e

# 3. the modem alone confirms after a fresh start with its own clock off and ours right: no jump -> the modem was wrong
common.clock.update(ok=None, skew=None, modem_bad=False)
assert common.check_clock(iso(T + 86 * 60), T + 200) is False
assert common.check_clock(iso(T + 260), T + 260) is True
e = evs()[-1]; assert e["event"] == "modem clock wrong" and "records" not in e["why"], e

# 6. replay of the real 3rd cut (test/fixtures/routeros-powercut-2026-11-17.log): the router booted on its old clock, the modem's
#    boot inform at our 14:53:15 is +86 min, INTERNET at 14:53:16, NTP steps us 14:53:31 -> 16:19:55, our next watchdog minute
m = [1000.0]; mono0 = common.mono; common.mono = lambda: m[0]
common.clock.update(ok=None, skew=None, modem_bad=False, net_said=False)
OFF = 5184; B = time.mktime(time.strptime("2026-11-17 14:53:15", "%Y-%m-%d %H:%M:%S"))
common.clock["start"] = (B - 62, 1000.0 - 62)                                    # the keeper started 14:52:13 (old clock)
assert common.check_clock(iso(B + OFF), B) is False and evs()[-1]["event"] == "clock not verified"
n = len(evs()); m[0] += 1
assert common.check_net(hdr(B + 1 + OFF), B + 1) is False and len(evs()) == n and not common.clock_ok()   # internet up, NTP not yet
m[0] = 1060.0; assert common.check_net(hdr(B + 60 + OFF), B + 60 + OFF) is True                         # stepped: agrees
e = evs()[-1]; assert e["event"] == "clock verified" and "86 min behind" in e["why"] and "confirmed by the internet" in e["why"], e
assert "records stamped 2026-11-17 14:52:13 to at most 14:54:15 carry that wrong time" in e["why"], e   # from the keeper's start
common.mono = mono0

# 7. a modem reboot with the WAN down (lock change; a test cell that is not there: minutes): verified stays verified, the internet is
#    not asked, a modem booting without network time is the modem's clock (test/fixtures/routeros-modem-reboot-2026-11-17.log)
common.clock.update(ok=True, skew=1.0, modem_bad=False); n = len(evs()); dead = common.CLOCK_URL; common.CLOCK_URL = "http://127.0.0.1:9/x"
assert common.check_clock("1970-01-01T00:00:20", time.time()) is True and common.clock_ok() and [e["event"] for e in evs()[n:]] == ["modem clock wrong"]
assert common.check_clock("1970-01-01T00:01:20", time.time() + 60) is True and len(evs()) == n + 1                     # said once
common.CLOCK_URL = dead

# 4. the real fetch: HTTP Date of a server (HEAD, plain HTTP: no CA store needed in the container); None when nothing answers
class H(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self): self.send_response(204); self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
d = common.fetch_date("http://127.0.0.1:%d/generate_204" % srv.server_port)
assert d and abs(email.utils.parsedate_to_datetime(d).timestamp() - time.time()) < 5, d
assert common.fetch_date("http://127.0.0.1:9/x") is None and common.fetch_date("") is None
# 5. wired: the watchdog's minute asks the internet only while the clock is not verified
from keeper import acs
common.CLOCK_URL = "http://127.0.0.1:%d/generate_204" % srv.server_port
common.clock.update(ok=None, skew=None, modem_bad=False); assert common.check_clock(iso(time.time() + 86 * 60)) is False
assert acs.clock_tick() is True and common.clock_ok()
common.CLOCK_URL = "http://127.0.0.1:9/x"; assert acs.clock_tick() is True                           # verified: no request at all
print("OK clock")
