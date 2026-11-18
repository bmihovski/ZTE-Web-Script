# the Saturday day test (docs/NIGHTLY-PLAN.md item 7, 2026-11-18): DAY_TEST "Sat 08:30-11:30" (the night-off cells are back ~08:00-08:10)
# tests the planner's day-only cells (ARFCN 100 is off at night) + the night results within 5 points of their baseline's score
# (daytime confirmation under real load); once a Saturday, after 10 idle minutes, as many as fit before 11:30, a hard stop
import os, sys, tempfile, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), LOCKTEST_IPERF="0", DAY_TEST="Sat 08:30-11:30")
sys.path.insert(0, ROOT)
from keeper import alert, brain, common, locktest, nightplan, store
from keeper.devices import zte as zdev
D = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))
SAT = D("2026-11-21 09:00")                                                             # a Saturday
assert locktest.in_window("Sat 08:30-11:30", SAT) and not locktest.in_window("Sat 08:30-11:30", SAT - 86400)
assert not locktest.in_window("Sat 08:30-11:30", D("2026-11-21 11:31")) and not locktest.in_window("", SAT)

# the night run's rows -> the ones to confirm by day: within 5 points of the baseline's score (both measured)
row = lambda lock, down, call: {"run": "R", "mode": "night", "lock": lock, "on_target_pct": "100.0", "down_mbps": down, "up_mbps": "25",
                                "call_ok_pct": call, "loss_pct": "0", "lat_p95": "45", "jitter_ms": "8", "nr_arfcn": "636576", "minutes": "12"}
night = [row("none", "160", "100"), row("394@6400", "150", "100"), row("112@6400", "30", "100"), row("434@1550", "158", "91.7")]
assert nightplan.day_confirm(night) == ["394@6400", "434@1550"], nightplan.day_confirm(night)          # 112@6400: 28 Mbit/s -> far below
assert nightplan.day_confirm([dict(night[0], call_ok_pct="")] + night[1:]) == []                       # baseline not measured: none

# maybe_day: the plan, once, idle-gated, deadline at the window's end
for r in night: store.add(common.DATA, "lock_tests", dict(r, time="2026-11-21 05:00:00"))
common.state({"night_plan": {"day_only": ["264@100"]}, "lte_lock": {"cell": "340@100", "state": "active"}})
runs = []; locktest.lock_test = lambda plan, **k: runs.append((plan, k))
alert.report = lambda text: runs.append(("telegram", text))
idle = {"down_bps": "100", "up_bps": "100", "time": common.stamp()}
for _ in range(locktest.DAY_IDLE_MIN): brain.HIST.append(dict(idle))
assert not locktest.maybe_day(idle, SAT - 86400)                                        # Friday: no
common.clock["ok"] = False; assert not locktest.maybe_day(idle, SAT); common.clock["ok"] = True        # clock not verified: no
assert locktest.maybe_day(idle, SAT); time.sleep(0.2)
plan, k = [x for x in runs if x[0] != "telegram"][0]
assert plan == ["340@100", "264@100", "394@6400", "434@1550"] and not k.get("night"), runs     # baseline = the day lock; day mode: 5G lock kept
assert abs(k["deadline"] - (time.time() + D("2026-11-21 11:30") - nightplan.MARGIN_S - SAT)) < 5, k
assert any(x[0] == "telegram" and "day test" in x[1] for x in runs), runs
assert not locktest.maybe_day(idle, SAT + 600)                                           # once a Saturday
common.state({"day_last": None}); brain.HIST.append(dict(idle, down_bps="900000"))
assert not locktest.maybe_day(idle, SAT + 1200)                                          # not 10 idle minutes: wait (no call cut)
assert locktest.maybe_day in zdev.minute_jobs()
print("OK daytest")
