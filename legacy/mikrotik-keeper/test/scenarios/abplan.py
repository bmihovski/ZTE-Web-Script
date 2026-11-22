# A/B candidates (2026-11-21): the pair rule (A = the max score, B = the next) and the REAL anchor_ranking body.
# 2026-11-21o shipped two bugs the patched pair-rule test hid: `anchor_ranking()` never set a score (so `high()` was always
# empty -> the one-call refresh ran 3 legs, no A high/B high), and `low()` took any band scan (B1/B3 as "low"). This scenario
# therefore also calls the real body (no brain.anchor_ranking patch) and pins the low pair to the LOW bands.
import os, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = tempfile.mkdtemp(); os.environ.update(DATA=DATA)
sys.path.insert(0, ROOT)
from keeper import abplan, brain, store

# --- the pair rule (a patched ranking: the rule itself) ---
real_ranking = brain.anchor_ranking
brain.anchor_ranking = lambda: {"ranked": [{"lock": "434@1550", "load_method": brain.LTE_LOAD_METHOD, "load_s": 20, "score": 90}, {"lock": "340@3350", "load_method": brain.LTE_LOAD_METHOD, "load_s": 20, "score": 80},
                                           {"lock": "370@1550", "load_method": brain.LTE_LOAD_METHOD, "load_s": 20, "score": 70}]}
assert abplan.high() == ["434@1550", "340@3350"], abplan.high()          # the max under A, the next under B
brain.anchor_ranking = lambda: {"ranked": [{"lock": "434@1550", "load_method": brain.LTE_LOAD_METHOD, "load_s": 20, "score": 90}, {"lock": "340@3350", "load_method": brain.LTE_LOAD_METHOD, "load_s": 20, "score": 90}]}
assert abplan.high() == ["434@1550", "340@3350"], abplan.high()          # the same top score -> both
brain.anchor_ranking = lambda: {"ranked": [{"lock": "434@1550", "load_method": brain.LTE_LOAD_METHOD, "load_s": 20, "score": 90}]}
assert abplan.high() == ["434@1550"], abplan.high()                      # a single cell -> no B

# --- the REAL body: the ranked rows must carry the (complete) lock-test score, or high() is empty in production ---
brain.anchor_ranking = real_ranking
brain.reg_st["dir"] = DATA
def lt(lock, **kw):
    r = {"lock": lock, "rat": "lte", "run": "2026-11-18 13:04:31", "time": "2026-11-18 13:56:56", "minutes": "12",
         "on_target_pct": "100.0", "unstable": "", "call_ok_pct": "100.0", "loss_pct": "0.0", "lat_p95": "64.5",
         "jitter_ms": "11.1", "down_mbps": "167.0", "up_mbps": "26.3", "nr_arfcn": "636576", "nr_iperf": "636576/534:1",
         "mode": "as is", "load_method": brain.LTE_LOAD_METHOD, "load_s": "20"}
    r.update(kw); return r
store.add(DATA, "lock_tests", lt("202@3350", load_method="", lat_p95="20"))
store.add(DATA, "lock_tests", lt("434@1550"))                                                   # 89.9, complete
store.add(DATA, "lock_tests", lt("66@1550", lat_p95="57.8", jitter_ms="8.9", down_mbps="159.0", up_mbps="26.0"))   # 89.7
store.add(DATA, "lock_tests", lt("340@100", run="2026-11-02 08:49:37", time="2026-11-02 09:22:28", nr_iperf="",
                                 down_mbps="", up_mbps=""))                                     # partial: no iperf
assert abplan.high() == ["434@1550", "66@1550"], abplan.high()           # the real body: A = max, B = next
rk = {r["lock"]: r for r in brain.anchor_ranking()["ranked"]}
assert rk["434@1550"]["score"] == 89.9 and rk["434@1550"]["score_partial"] == [], rk["434@1550"]
assert rk["340@100"]["score"] is None and rk["340@100"]["score_partial"] == ["down", "up"], rk["340@100"]   # partial = no A/B score

# --- the low pair: the NEWEST band scan per LOW band (B8, B20), never the high-band rows the same scan writes ---
for b, s, n, c, t in (("1", "100/340", "636576/534", "100.0", "2026-11-21 17:29:17"),
                      ("3", "1550/434", "636576/534", "100.0", "2026-11-21 17:31:14"),
                      ("7", "3350/340", "636576/534", "100.0", "2026-11-21 17:33:11"),
                      ("8", "3774/112", "156510/534", "80.0", "2026-11-20 02:06:57"),     # an older B8 row: not the latest
                      ("8", "3774/394", "156510/534", "100.0", "2026-11-21 17:35:07"),
                      ("20", "6400/112", "", "100.0", "2026-11-21 17:37:05")):
    store.add(DATA, "band_scans", {"time": t, "band": b, "serving": s, "nr_cell": n, "call_ok": c,
                                   "lat_p95": "43.3", "jitter_ms": "10.5", "down_mbps": "30.17"})
assert abplan.low() == [("394@3774", "534@156510")], abplan.low()  # low without observed n28 is unproven
print("OK abplan")
