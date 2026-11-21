# the night planner (docs/NIGHTLY-PLAN.md items 1-3, 2026-11-18): candidates from the stored samples (+ band scans), only cells whose
# ARFCN and the cell itself are present at NIGHT (2026-11-18: 493/202/236@1550 were picked by daytime RSRP, 1-12 % present at night,
# each lock hung the ZTE up to 31 min), best night RSRP per ARFCN first, a full test (iperf + nr_iperf) counts as tested
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp())
sys.path.insert(0, ROOT)
from keeper import nightplan as npl
D = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))
NOW = D("2026-11-19 01:30"); WIN = ("00:50", "08:00")
S = []
def add(t, serving, nb):
    S.append({"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), "lte_cell": serving, "lte_rsrp": "-89", "lte_neighbours": nb})
for night in (D("2026-11-17 01:00"), D("2026-11-18 01:00")):          # two nights, 60 min each: B3 + B7 + B20, no B1 (100 off at night)
    for m in range(60):
        nb = "1550/340:-87 1550/434:-87 3350/340:-89 6400/112:-78 6400/121:-85 6400/52:-95" + (" 6400/394:-81" if m % 20 else "") + (" 1550/493:-93" if m == 7 else "")
        add(night + 60 * m, "3350/340", nb)
for m in range(120):                                                   # the day: B1 + B3 only (locked to 340@100)
    add(D("2026-11-18 12:00") + 60 * m, "100/340", "100/340:-81 100/264:-87 1550/340:-90 1550/493:-90")
tests = [{"time": "2026-11-18 05:16:36", "lock": "112@6400", "minutes": "12", "down_mbps": "28.5", "nr_iperf": "", "anchor_rsrp_p50": "-79"},       # 5G locked then: no nr_iperf -> retest
         {"time": "2026-11-18 05:34:06", "lock": "121@6400", "minutes": "12", "down_mbps": "32.6", "nr_iperf": "no5G:1", "anchor_rsrp_p50": "-89"},  # full; now -85: +4 dB -> skip
         {"time": "2026-11-14 02:00:00", "lock": "434@1550", "minutes": "12", "down_mbps": "150", "nr_iperf": "636576/747:1", "anchor_rsrp_p50": "-87"}]
scans = [{"time": "2026-11-18 01:10:00", "band": "8", "serving": "3774/112", "rsrp": "-79", "neighbours": "3774/112:-79 3774/36:-99"}]
p = npl.plan(S, tests, scans, NOW, WIN, lock="340@100", dead=set())
assert p["cells"][:4] == ["112@6400", "112@3774", "394@6400", "340@1550"], p["cells"]      # night RSRP order; B8 from the night band scan
assert "493@1550" not in p["cells"] and "121@6400" not in p["cells"] and "434@1550" not in p["cells"], p["cells"]   # 2 % at night / full test / full test
assert "52@6400" not in p["cells"]                                                                  # 17 dB under 6400's best: not worth a step
assert "264@100" not in p["cells"] and p["day_only"][:1] == ["264@100"] and "100" in p["night_off"], p             # ARFCN 100: not at night
assert "340@100" not in p["cells"] + p["day_only"]                                                                  # the lock itself
# 2026-11-19 live: day_only held 206 cells (every neighbour ever seen by day) -> only cells on an ARFCN OFF at night, seen in >=
# CELL_PCT of the day samples, within WEAK_DB of that ARFCN's best day RSRP (the Saturday day test takes them in order)
assert p["day_only"] == ["264@100"], p["day_only"]                  # 493@1550: its ARFCN is on at night (and it is a night non-starter)
S2 = S + [{"time": "2026-11-18 14:00:00", "lte_cell": "100/340", "lte_rsrp": "-80", "lte_neighbours": "100/340:-81 100/77:-90 100/41:-99"}]   # once by day
assert "77@100" not in npl.plan(S2, tests, scans, NOW, WIN, lock="340@100")["day_only"]                                                # 1 of 121 day samples
S3 = S + [dict(r, lte_neighbours=r["lte_neighbours"] + " 100/41:-99") for r in S if r["time"][11:13] in ("12", "13")]                     # always there, 18 dB under
assert "41@100" not in npl.plan(S3, tests, scans, NOW, WIN, lock="340@100")["day_only"]
# 2026-11-19 live: the night band scan locked B1 while ARFCN 100 is off - "served" 100/393 at -127 dBm (noise): no night presence
noise = scans + [{"time": "2026-11-18 01:12:00", "band": "1", "serving": "100/393", "rsrp": "-127", "neighbours": ""}]
pn = npl.plan(S, tests, noise, NOW, WIN, lock="340@100")
assert "100" in pn["night_off"] and "393@100" not in pn["cells"] and pn["day_only"] == ["264@100"], pn
assert p["why"]["394@6400"].startswith("night -81 dBm, 95 %") and p["why"]["112@3774"].startswith("night -79 dBm, band scan"), p["why"]
# 2026-11-21: a band scan's internet quality rides the why; the low bands (B20/B28) are the bad-weather fallbacks, not a radio-only pick
low = scans + [{"time": "2026-11-18 02:10:00", "band": "20", "serving": "6400/77", "rsrp": "-84", "call_ok": "67", "neighbours": ""}]
pl = npl.plan(S, tests, low, NOW, WIN, lock="340@100")
assert "77@6400" in pl["cells"] and "call-ready 67%" in pl["why"]["77@6400"], pl
p2 = npl.plan(S, tests[1:2] + [dict(tests[1], lock="434@1550", anchor_rsrp_p50="-95", time="2026-11-18 03:00:00")], [], NOW, WIN, lock=None, dead={"394@6400"})
assert "434@1550" in p2["cells"] and "394@6400" not in p2["cells"], p2["cells"]          # +8 dB since its test -> retest; dead -> out
old = [dict(r, time=r["time"].replace("2026-11-1", "2026-11-0")) for r in S if r["time"] < "2026-11-17"]           # only seen 10+ days ago
assert npl.plan(old, [], [], NOW, WIN, lock=None, dead=set())["cells"] == []                                       # unseen > 3 days: gone
# how many fit: (window end - now - 15 min margin) // step, the baseline first
assert npl.budget(D("2026-11-19 02:00"), D("2026-11-19 08:00"), step_s=18 * 60) == 18     # 345 min // 18 = 19 steps - 1 baseline
assert npl.budget(D("2026-11-19 07:30"), D("2026-11-19 08:00"), step_s=18 * 60) == 0
assert npl.window_end(D("2026-11-19 02:00"), "00:50-08:00") == D("2026-11-19 08:00") and npl.window_end(D("2026-11-19 09:00"), "00:50-08:00") is None
print("OK nightplan")
