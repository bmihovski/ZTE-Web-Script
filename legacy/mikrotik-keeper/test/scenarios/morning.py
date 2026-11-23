# Real discovery, ranked backups, the 5G gate, A/B, restore/upload and the low-band fallback.
import collections, os, sys, tempfile, threading, time, types
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA, DEVICE="zte", ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import acs, alert, bandscan, brain, morning
from keeper.common import stamp, state

# --- the pair database in its REAL key format (earfcn/pci: "100/340"), not the lock spec ("340@100"): the lookup must convert
brain.REG["nr"] = {"636576/534": {"anchors": {"100/340": {"min": 100}, "1550/434": {"min": 100}, "1550/66": {"min": 100}, "3350/340": {"min": 100},
                    "100/66": {"min": 11, "ok": 11, "loss": 0, "p95": 56.59, "jitter": 8.54},
                    "100/202": {"min": 16, "ok": 15, "loss": 3.01, "p95": 58.83, "jitter": 28.87},
                    "3350/307": {"min": 12, "ok": 11, "loss": 18.94, "p95": 29.02, "jitter": 2.88}}},
                   "156510/534": {"anchors": {"3774/394": {"min": 100}, "3774/112": {"min": 100}}},
                   "156510/212": {"anchors": {"6400/394": {"min": 100}}}}
assert morning._nr_for("434@1550") == "534@636576", morning._nr_for("434@1550")   # the known pair (not "none")
assert morning._nr_for("999@999") == "none"

# A weak or missing unlocked baseline opens discovery, but a healthy high-band leg can avoid the fallback.
healthy = {"ok": True, "has5g": True, "call_ok": 1, "down_mbps": 30}
offline = dict(healthy, ok=False, has5g=False)
assert morning._fallback(offline, [offline]) == "high unavailable"
assert morning._fallback(offline, [healthy]) is None
assert morning._fallback(dict(healthy, down_mbps=16), [dict(healthy, call_ok=0)]) == "high degraded"
assert morning.LOW[:2] == ["20", "8"]

# Real Nov 22 B1 neighbours, then a scan where the good 100/66 pair is also visible.
from keeper.devices.zte import locks
def band_report(band):
    locks.on_values({locks.CI + k: v for k, v in {"LockLTEBand": band, "LockLTECell": "", "LockNr5gCell": ""}.items()})
band_report(bandscan.ALL)
writes = []
def band_write(params):
    writes.append(params)
    threading.Timer(0.002, band_report, args=(params[0][2],)).start()
    if params[0][2] != bandscan.ALL:
        sample = dict(brain.HIST[-1], lte_band_config=params[0][2])
        def report():
            brain.HIST.append(dict(sample, lte_neighbours=sample["lte_neighbours"] + " 100/121:-73"))
            brain.HIST.append(sample)
        threading.Timer(0.005, report).start()
    return True
acs.tr069_set = band_write
morning.BAND_WAIT_S = 0.03
brain.HIST.append({"time": stamp(), "lte_cell": "100/340", "lte_rsrp": -81, "lte_sinr": 5, "lte_rsrq": -13,
                   "nr_cell": "636576/534", "nr_arfcn": "636576", "nr_sinr": 12,
                   "lte_neighbours": "100/202:-93 100/500:-93 100/326:-89 100/499:-118 100/124:0", "lte_ca": "B1/100/340:-81/5@20"})
d = morning._discover("1")
assert d["serving"] == "100/340" and d.get("candidates") == ["326@100", "202@100"], d
assert [w[0][2] for w in writes] == ["1", bandscan.ALL], writes            # the band lock, then the full list back
assert d["nr"] == "636576/534", d
assert d["rejected"]["121@100"] == "one usable reading" and d["rejected"]["499@100"] == "below signal floor", d
assert d["rejected"]["124@100"] == "invalid RSRP", d
brain.HIST[-1]["lte_neighbours"] += " 100/66:-97"
assert morning._discover("1")["candidates"] == ["66@100", "326@100"]
brain.HIST[-1]["lte_neighbours"] += " 1550/302:-70"
assert morning._discover("1")["candidates"] == ["66@100", "326@100"]
anchors = brain.REG["nr"]["636576/534"]["anchors"]
anchors["100/500"] = {"min": 20, "ok": 19, "loss": 0, "p95": 56, "jitter": 8}
assert morning._discover("1")["candidates"] == ["66@100", "500@100"]  # better known pair beats the stronger known pair
del anchors["100/500"]
brain.HIST.append({"time": stamp(), "lte_cell": "3774/394", "lte_rsrp": -80, "lte_ca": "B8/3774/394:-80/4@5",
                   "lte_neighbours": "3350/112:-79 3774/112:-79", "nr_cell": "156510/534"})
low = morning._discover("8")
assert low["serving"] == "3774/394" and low["candidates"] == ["112@3774"], low
assert "112@3350" not in low["signals"], low

# --- the daily run: no-lock + 3 bands x (#1 + #2)
class Z:
    def __init__(self): self.calls = []
    def locks_apply(self, lte, nr):
        self.calls.append((lte, nr))
        return "tr069"
    def ready(self, timeout=0): return True
z = Z(); morning.devices = types.SimpleNamespace(current=lambda: z)
now = time.time()
brain.lat_buf.append((now - 2, 20))
assert brain.wait_online(timeout=0, stable=1)
assert not brain.wait_online(timeout=0, stable=1, since=now)
brain.lat_buf.append((now, 21))
assert brain.wait_online(timeout=0, stable=1, since=now)
brain.lat_buf.clear()
online_waits = []
def online(timeout=0, **kwargs):
    online_waits.append(timeout)
    return z.calls[-1][0] not in ("202@100", "202@3350")
brain.wait_online = online
brain.row_health = lambda row: {"sim": True, "registered": True, "signal": True, "data": True}
morning.WAKE_LEAD_S = 0.0
morning.FIVE_G_WAIT_S = 0.02
morning.captest = types.SimpleNamespace(measure=lambda secs=8: (160.0, 29.0, "mock iperf3 up"))
rep = []; morning.alert.report = lambda m: rep.append(m)
seq = [(28.0, 1, 0.0, 62.5, 12.0, "636576/534"),     # no lock (5G)
       (30.2, 1, 0.0, 43.3, 10.5, "636576/534"),     # B1 #1 (5G) -> A
       (25.0, 1, 0.0, 43.8, 12.0, "636576/534"),     # B1 #3 measures after PCI 202 fails -> B
       (34.0, 1, 0.0, 46.0, 13.0, "636576/534"),     # B3 #1
       (33.0, 1, 0.0, 44.0, 12.0, "636576/534"),     # B3 #2, so #3 is never locked
       (37.0, 1, 0.0, 45.0, 7.0, "636576/534"),      # B7 #1
       (20.0, 1, 0.0, 60.0, 15.0, "636576/534")]    # B7 #3 measures after PCI 202 fails
def wake(**kwargs):
    if z.calls[-1][0] not in ("112@6400", "500@100"):
        brain.HIST.append({"time": stamp(), "lte_cell": brain.anchor_key(z.calls[-1][0]), "nr_cell": seq[0][-1]})
brain.wake = wake
def it(seconds=25):
    d, c, l, p, j, nr = seq.pop(0)
    for _ in range(2): brain.HIST.append({"time": stamp(), "lte_cell": brain.anchor_key(z.calls[-1][0]), "nr_cell": nr})
    return {"down_mbps": d, "call_ok": c, "loss_pct": l, "lat_p95": p, "jitter_ms": j}
brain.internet_test = it
disc = {"1": {"serving": "100/340", "candidates": ["202@100", "326@100"], "nr": "636576/534"},
        "3": {"serving": "1550/434", "candidates": ["66@1550", "999@1550"], "nr": ""},
        "7": {"serving": "3350/340", "candidates": ["202@3350", "36@3350"], "nr": ""},
        "8": {"serving": "3774/394", "candidates": ["112@3774"], "nr": "156510/534"},
        "20": {"serving": "6400/112", "candidates": ["394@6400"], "nr": ""}}
discover = morning._discover
morning._discover = lambda b: dict(disc[b], band=b)
ok, why = morning.run(low=False, trigger="test"); assert ok, why
deadline = time.time() + 10
while morning.st["running"] and time.time() < deadline: time.sleep(0.05)
assert morning.st["running"] is None, morning.st
res = morning.st["result"]
assert [x["leg"] for x in res["legs"]] == ["no lock", "B1 #1 100/340", "B1 #2 202@100", "B1 #3 326@100", "B3 #1 1550/434",
                                            "B3 #2 66@1550", "B7 #1 3350/340", "B7 #2 202@3350", "B7 #3 36@3350"], res["legs"]
assert [x["has5g"] for x in res["legs"]] == [True, True, False, True, True, True, True, False, True], res["legs"]
assert res["a"]["lte"] == "340@100" and res["b"]["lte"] == "326@100", (res.get("a"), res.get("b"))
assert res["restored"]["lte"] == "340@100", res["restored"]
offline = [x for x in res["legs"] if x["lte"].startswith("202@")]
assert all(x["error"] == "no internet within 90 s" and x.get("loss_pct") is None for x in offline), offline
assert max(online_waits[1:-1]) <= 90 and not seq, online_waits
assert res["up"] == 29.0, res["up"]
assert z.calls[-1] == ("340@100", "534@636576") and z.calls[0] == ("none", "none"), z.calls
assert len(rep) == 1 and "5G" in rep[0] and "A:" in rep[0] and "restored" in rep[0], rep

# --- the low run: B8/B20 added; the B20 112 (no 5G) is excluded, the B20 394 (n28 212) competes
z.calls.clear(); rep.clear(); seq[:] = [(28.0, 1, 0.0, 62.5, 12.0, "636576/534"),
    (30.2, 1, 0.0, 43.3, 10.5, "636576/534"), (25.0, 1, 0.0, 43.8, 12.0, "636576/534"),
    (34.0, 1, 0.0, 46.0, 13.0, "636576/534"), (33.0, 1, 0.0, 44.0, 12.0, "636576/534"),
    (37.0, 1, 0.0, 45.0, 7.0, "636576/534"), (20.0, 1, 0.0, 60.0, 15.0, "636576/534"),
    (50.8, 1, 0.0, 74.0, 16.3, "156510/212"),     # B20 #2 394 (n28 212)
    (15.3, 1, 0.0, 72.6, 21.3, "156510/534"),      # B8 #1 (n28 534)
    (14.0, 1, 0.0, 70.0, 20.0, "156510/534")]      # B8 #2
ok, why = morning.run(low=True, trigger="test-low"); assert ok, why
deadline = time.time() + 10
while morning.st["running"] and time.time() < deadline: time.sleep(0.05)
res2 = morning.st["result"]
assert len(res2["legs"]) == 13 and res2["legs"][-3]["lte"] == "394@6400", res2["legs"][-4:]
assert res2["legs"][-4]["has5g"] is False and res2["legs"][-3]["has5g"] is True, res2["legs"][-4:]
assert res2["restored"]["lte"] == "340@100" and res2["up"] == 29.0, res2["restored"]
assert len(rep) == 1 and "B20" in rep[0], rep

# A comparison measures both transfers and restores the exact managed locks, including a released NR lock.
original_lte = {"cell": "434@1550", "state": "active", "ab": True}
original_nr = {"cell": "534@636576", "state": "released", "ab": True}
state({"lte_lock": original_lte, "nr_lock": original_nr})
z.calls.clear(); morning.BANDS = ["3"]
seq[:] = [(31.0, 1, 0.0, 40.0, 9.0, "636576/534")] * 3
def measure(secs=8, load_windows=None):
    now = time.time()
    load_windows.extend([(now - 31, now - 21), (now - 11, now - 1)])
    for start, end in load_windows:
        for i in range(5): brain.lat_buf.append((start + i, 40 + i))
    return (100.0 if z.calls[-1][0] == "none" else 150.0), 20.0, "test iperf"
from keeper.captest import LocalTraffic
attempts = []
def interrupted(secs=8, load_windows=None):
    attempts.append(z.calls[-1][0])
    if len(attempts) == 1:
        raise LocalTraffic("local transfer interrupted the baseline")
    return measure(secs, load_windows)
morning.captest.measure = interrupted
morning.captest.LocalTraffic = LocalTraffic
morning.captest.wait_lan_quiet = lambda: True
ok, why = morning.run(low=False, apply=False, bands=["3"], trigger="compare", notify=False); assert ok, why
deadline = time.time() + 10
while morning.st["running"] and time.time() < deadline: time.sleep(0.05)
trial = state()["morning_trial"]
assert not trial["apply"] and trial["decision"]["gain"] and trial["decision"]["lte"] in ("434@1550", "66@1550"), trial
assert [x["load_s"] for x in trial["legs"]] == [20.0] * 3, trial["legs"]
assert attempts[:2] == ["none", "none"] and z.calls.count(("none", "none")) == 1
assert trial["legs"][0]["load_retries"] == ["local transfer interrupted the baseline"]
assert state()["lte_lock"] == original_lte and state()["nr_lock"] == original_nr
assert z.calls[-1] == ("434@1550", "none"), z.calls
assert state()["morning"]["trigger"] == "test-low"

# Two offline observations persist across runs, independently per EARFCN. Expired evidence no longer suppresses a retry.
assert morning._dead_cells() == {"202@100", "202@3350"}
assert morning._dead_cells(time.time() + 8 * 86400) == set()
brain.HIST.clear()
brain.HIST.append({"time": stamp(), "lte_cell": "100/340", "lte_rsrp": -81, "lte_neighbours": "100/202:-73 100/326:-89 100/500:-93"})
assert discover("1")["candidates"] == ["326@100", "500@100"]

# An online leg with no 5G is unsuccessful without running a download. The preceding leg's 5G cannot open the gate.
brain.HIST = collections.deque([{"time": stamp(), "nr_cell": "636576/534"}], maxlen=1)
morning.FIVE_G_WAIT_S = 0.001
no5g = morning._leg("B1 #2", "500@100", "none", band="1", role="second")
assert not no5g["ok"] and not no5g["has5g"] and "no 5G" in no5g["error"] and not seq, no5g
mark = brain.HIST[-1]
brain.HIST.append({"time": stamp(), "nr_cell": "636576/534"})
assert morning._has5g(mark)
assert len(state()["morning_dead"]["202@100"]) == 2
morning._note_reachability({"lte": "202@100", "ok": True, "has5g": True})
assert morning._dead_cells() == {"202@3350"}

# The live 394 trial had verified LTE internet, but the pinned n28 pair never appeared.
# A different n28 cell on the same anchor must not start the requested-pair measurement.
brain.HIST.clear()
brain.wake = lambda **kw: brain.HIST.append({"time": stamp(), "lte_cell": "3774/394", "nr_cell": "156510/212"})
wrong_nr = morning._leg("B8 394@3774", "394@3774", "534@156510", band="8", role="first", compare=True)
assert wrong_nr["failure"] == "no_5g" and not wrong_nr["has5g"], wrong_nr
assert "LTE 394@3774 attached" in wrong_nr["error"] and "534@156510" in wrong_nr["error"], wrong_nr
assert "cell not reachable" not in wrong_nr["error"] and wrong_nr.get("load_s") is None, wrong_nr

# Scheduled legs use HTTPS to screen the path. A failed HTTPS transfer can
# coexist with successful latency probes and must keep the download error.
brain.HIST.clear()
brain.wake = lambda **kw: brain.HIST.append({"time": stamp(), "lte_cell": "100/202", "nr_cell": "636576/534"})
brain.internet_test = lambda **kw: {"down_mbps": None, "call_ok": 1, "loss_pct": 0.0,
                                    "lat_p95": 23.3, "jitter_ms": 4.5, "error": "HTTPS connection timed out"}
messages = []
brain.wait_online = lambda **kw: True
real_log, morning.log = morning.log, messages.append
scheduled = morning._leg("B1 #2 202@100", "202@100", "534@636576", band="1", role="second")
morning.log = real_log
assert scheduled["ok"] and scheduled["has5g"] and scheduled["down_mbps"] is None, scheduled
assert scheduled.get("https_error") == "HTTPS connection timed out", scheduled
assert "HTTPS connection timed out" in messages[-1] and "iperf3" not in messages[-1], messages
assert "HTTPS download unavailable" in morning._msg({"legs": [scheduled], "restored": {}})

# Evidence from the full live comparison on 2026-11-22: five 10 s downloads
# finished between informs. Full comparisons need the same duration floor
# as a one-cell comparison, while a failed lock must unwind straight to restore.
class ComparisonProfile:
    LTE, NR = "LockLTECell", "LockNr5gCell"
    def snapshot(self): return {"raw": {self.LTE: "", self.NR: ""}}
    def begin_scope(self, owner): return self.snapshot()
    def low_band(self, lte): return None
    def transfer_evidence(self, windows, target, lte, nr, nr_band, expected_nr=None):
        return {"verified": False, "samples_per_transfer": [0, 1], "observations": []}
    def finish_scope(self):
        state({"profile_rollback": None})
    def spec(self, value): return value or "none"

z.profile = ComparisonProfile()
morning.captest.ISECS = 10
transfer_durations = []
def loaded(secs=None, load_windows=None):
    transfer_durations.append(secs)
    now = time.time()
    load_windows.extend([(now - 30, now - 20), (now - 10, now)])
    return 50, 20, "test"
morning.captest.measure = loaded
leg = {"leg": "B1 202@100", "lte": "202@100", "nr": "none"}
morning._record_load(leg, None, None)
assert transfer_durations == [2 * acs.FAST_INTERVAL + 5], transfer_durations
assert "no fresh radio sample" in leg["error"] and leg.get("load_down_mbps") is None, leg

# B8 can show LTE-only at idle and acquire n28 partway through modem download.
# Exercise the real radio validation on a warm-up followed by the scored probe.
from keeper.devices.zte import profile as radio_profile
z.profile = radio_profile
radio_profile.radio.clear()
raw = {"LockLTEBand": "8", "LockLTECell": "112,3774", "LockNr5gCell": ""}
radio_profile.latest = (time.monotonic(), acs.st["boots"], raw)
seen = []
n28 = "156510/534"
target = "3774/112"

def radio(lte=target, nr=n28, when=None):
    radio_profile.radio.append(dict(raw, time=time.time() if when is None else when,
                                    boots=acs.st["boots"], lte_cell=lte, nr_cell=nr))
def warm_download(secs=None, load_windows=None, *, download_only=False):
    seen.append(("iperf", secs, download_only))
    now = time.time()
    load_windows.append((now - 30, now))
    radio(nr="no5G", when=now - 24)
    radio(nr=n28, when=now - 1)
    return 8.25, None, "down Sofia"
def quick_probe(**kw):
    seen.append("probe")
    radio()
    return {"down_mbps": 12, "call_ok": 1, "loss_pct": 0, "lat_p95": 35, "jitter_ms": 6}
morning.captest.measure = warm_download
brain.wake = lambda **kw: None
brain.internet_test = quick_probe
res = morning._leg("B8 112@3774", "112@3774", "none", band="8", role="first")
assert seen == [("iperf", 30, True), "probe"], (seen, res)
assert res["ok"] and res["observed_nr"] == n28 and res["warmup"]["nr_cell"] == n28, res
assert "load_down_mbps" not in res, res

seen.clear()
def wrong_pair(secs=None, load_windows=None, *, download_only=False):
    seen.append("warm")
    now = time.time()
    load_windows.append((now - 30, now))
    radio(nr="156510/309", when=now - 1)
    return 8.25, None, "down Sofia"
radio_profile.latest = (time.monotonic(), acs.st["boots"], dict(raw, LockNr5gCell="534,156510,28,15"))
# Keep the configured lock correct while the modem reports a different serving NR cell.
raw["LockNr5gCell"] = "534,156510,28,15"
morning.captest.measure = wrong_pair
res = morning._leg("B8 112@3774", "112@3774", "534@156510", band="8", role="first", compare=True)
assert seen == ["warm"] and not res["ok"] and res["failure"] == "no_5g" and "534@156510" in res["error"], res
assert "load_down_mbps" not in res

def lan_busy(**kw):
    raise LocalTraffic("local traffic during warm-up")
morning.captest.measure = lan_busy
res = morning._leg("B8 112@3774", "112@3774", "534@156510", band="8", role="first")
assert not res["ok"] and not res.get("failure") and res["error"] == "local traffic during warm-up", res
morning.captest.measure = loaded

z.profile = ComparisonProfile()
z.calls.clear()
def failed_lock(lte, nr):
    z.calls.append((lte, nr))
    if lte != "none":
        state({"profile_rollback": {"raw": {}}})
        raise RuntimeError("no BOOT inform")
    return "unchanged"
z.locks_apply = failed_lock
brain.wake = lambda **kw: brain.HIST.append({"time": stamp(), "lte_cell": "100/340", "nr_cell": "636576/534"})
brain.internet_test = lambda **kw: {"down_mbps": 30, "call_ok": 1}
ok, why = morning.run(low=True, apply=False, trigger="comparison", notify=False)
assert ok, why
deadline = time.monotonic() + 10
while morning.st["running"] and time.monotonic() < deadline: time.sleep(0.01)
trial = state()["morning_trial"]
assert z.calls == [("none", "none"), ("434@1550", "534@636576")], z.calls
assert not trial["complete"] and trial["restored"] == {"lte": "none", "nr": "none"}, trial
assert trial["coverage"]["missing_bands"] == ["3", "20", "8"] and len(trial["legs"]) == 2, trial
print("OK test_morning")
