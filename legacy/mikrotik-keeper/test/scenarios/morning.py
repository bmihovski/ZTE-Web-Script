# Morning cell check (2026-11-21): the real band-lock discovery (#1 serving, #2 the best neighbour by a known 5G pair then RSRP;
# the PCI re-discovered every run), then the daily run (no-lock + B1/B3/B7 x 2 cells), the 5G gate (a leg without 5G in the probe
# window is not A/B/persisted), A = the best 5G #1, B = the best 5G #2, the no-lock on a tie, the best 5G restored + its upload,
# one Telegram; and the low run (B8/B20) where the B20 112 (no 5G) is excluded and the B20 394 (n28 212) competes.
import os, sys, tempfile, time, types
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA, DEVICE="zte", ZTE_HOST="127.0.0.1:9")
sys.path.insert(0, ROOT)
from keeper import acs, alert, bandscan, brain, morning
from keeper.common import stamp

# --- the pair database: 534 under the high anchors + 66@100 (the pair priority beats RSRP); 212 under the B20 394; 534 under B8
brain.REG["nr"] = {"636576/534": {"anchors": {"340@100": {"min": 100}, "66@100": {"min": 100}, "434@1550": {"min": 100}, "66@1550": {"min": 100}, "340@3350": {"min": 100}}},
                   "156510/534": {"anchors": {"394@3774": {"min": 100}, "112@3774": {"min": 100}}},
                   "156510/212": {"anchors": {"394@6400": {"min": 100}}}}
brain.pair_quality = lambda p, a: 1

# --- the real discovery: a fresh B1 sample; 100/66 has a known pair but a worse RSRP than 100/264 (the pair wins the #2 slot)
writes = []
acs.tr069_set = lambda params: (writes.append(params), True)[1]
brain.HIST.append({"time": stamp(), "lte_cell": "100/340", "lte_rsrp": -81, "lte_sinr": 5, "lte_rsrq": -13,
                   "nr_cell": "636576/534", "nr_arfcn": "636576", "nr_sinr": 12,
                   "lte_neighbours": "100/264:-89 100/66:-93", "lte_ca": "B1/100/340:-81/5@20"})
d = morning._discover("1")
assert d["serving"] == "100/340" and d["second"] == "66@100", d          # the known pair beats the 2 dB stronger 100/264
assert [w[0][2] for w in writes] == ["1", bandscan.ALL], writes            # the band lock, then the full list back
assert d["nr"] == "636576/534", d

# --- the daily run: no-lock + 3 bands x (#1 + #2)
class Z:
    def __init__(self): self.calls = []
    def locks_apply(self, lte, nr): self.calls.append((lte, nr)); return "tr069"
    def ready(self, timeout=0): return True
z = Z(); morning.devices = types.SimpleNamespace(current=lambda: z)
brain.wait_online = lambda timeout=0: True
brain.row_health = lambda row: {"sim": True, "registered": True, "signal": True, "data": True}
brain.wake = lambda **kw: False
morning.WAKE_LEAD_S = 0.0
morning.FIVE_G_WAIT_S = 0.0
morning.captest = types.SimpleNamespace(measure=lambda secs=8: (160.0, 29.0, "mock iperf3 up"))
rep = []; morning.alert.report = lambda m: rep.append(m)
seq = [(28.0, 1, 0.0, 62.5, 12.0, "636576/534"),     # no lock (5G)
       (30.2, 1, 0.0, 43.3, 10.5, "636576/534"),     # B1 #1 (5G) -> A
       (25.0, 1, 0.0, 50.0, 12.0, "no5G"),           # B1 #2 no 5G -> out
       (34.0, 1, 0.0, 46.0, 13.0, "636576/534"),     # B3 #1
       (33.0, 1, 0.0, 44.0, 12.0, "636576/534"),     # B3 #2 -> B
       (37.0, 1, 0.0, 45.0, 7.0, "636576/534"),      # B7 #1
       (20.0, 1, 0.0, 60.0, 15.0, "no5G")]           # B7 #2 no 5G -> out
def it(seconds=25):
    d, c, l, p, j, nr = seq.pop(0)
    for _ in range(2): brain.HIST.append({"time": stamp(), "nr_cell": nr})
    return {"down_mbps": d, "call_ok": c, "loss_pct": l, "lat_p95": p, "jitter_ms": j}
brain.internet_test = it
disc = {"1": {"serving": "100/340", "second": "264@100", "nr": "636576/534"},
        "3": {"serving": "1550/434", "second": "66@1550", "nr": ""},
        "7": {"serving": "3350/340", "second": "307@3350", "nr": ""},
        "8": {"serving": "3774/394", "second": "112@3774", "nr": "156510/534"},
        "20": {"serving": "6400/112", "second": "394@6400", "nr": ""}}
morning._discover = lambda b: dict(disc[b], band=b)
ok, why = morning.run(low=False, trigger="test"); assert ok, why
deadline = time.time() + 10
while morning.st["running"] and time.time() < deadline: time.sleep(0.05)
assert morning.st["running"] is None, morning.st
res = morning.st["result"]
assert [x["leg"] for x in res["legs"]] == ["no lock", "B1 #1 100/340", "B1 #2 264@100", "B3 #1 1550/434",
                                            "B3 #2 66@1550", "B7 #1 3350/340", "B7 #2 307@3350"], res["legs"]
assert [x["has5g"] for x in res["legs"]] == [True, True, False, True, True, True, False], res["legs"]
assert res["a"]["lte"] == "340@100" and res["b"]["lte"] == "66@1550", (res.get("a"), res.get("b"))
assert res["restored"] == {"lte": "340@100", "nr": "534@636576"}, res["restored"]
assert res["up"] == 29.0, res["up"]
assert z.calls[-1] == ("340@100", "534@636576") and z.calls[0] == ("none", "none"), z.calls
assert len(rep) == 1 and "5G" in rep[0] and "A:" in rep[0] and "restored" in rep[0], rep

# --- the low run: B8/B20 added; the B20 112 (no 5G) is excluded, the B20 394 (n28 212) competes
z.calls.clear(); rep.clear(); seq[:] = [(28.0, 1, 0.0, 62.5, 12.0, "636576/534"),
    (30.2, 1, 0.0, 43.3, 10.5, "636576/534"), (25.0, 1, 0.0, 50.0, 12.0, "no5G"),
    (34.0, 1, 0.0, 46.0, 13.0, "636576/534"), (33.0, 1, 0.0, 44.0, 12.0, "636576/534"),
    (37.0, 1, 0.0, 45.0, 7.0, "636576/534"), (20.0, 1, 0.0, 60.0, 15.0, "no5G"),
    (15.3, 1, 0.0, 72.6, 21.3, "156510/534"),      # B8 #1 (n28 534)
    (14.0, 1, 0.0, 70.0, 20.0, "156510/534"),      # B8 #2
    (10.6, 0, 0.0, 139.1, 68.8, "no5G"),           # B20 #1 112 no 5G -> out
    (50.8, 1, 0.0, 74.0, 16.3, "156510/212")]      # B20 #2 394 (n28 212)
ok, why = morning.run(low=True, trigger="test-low"); assert ok, why
deadline = time.time() + 10
while morning.st["running"] and time.time() < deadline: time.sleep(0.05)
res2 = morning.st["result"]
assert len(res2["legs"]) == 11 and res2["legs"][-1]["lte"] == "394@6400", res2["legs"][-1]
assert res2["legs"][-2]["has5g"] is False and res2["legs"][-1]["has5g"] is True, res2["legs"][-2:]
assert res2["restored"]["lte"] == "340@100" and res2["up"] == 29.0, res2["restored"]
assert len(rep) == 1 and "B20" in rep[0], rep
print("OK morning")
