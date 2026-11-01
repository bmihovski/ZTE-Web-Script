# night screen winner selection (brain.night_best / night_finding) - drives the auto-apply + Telegram report.
import os, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), TG_TOKEN="", TG_CHAT="")
sys.path.insert(0, ROOT)
from keeper import brain, store
D = os.environ["DATA"]; brain.reg_st["dir"] = D

RUN = "2026-10-30 03:00:00"
def row(lock, t, asinr, nsinr, ot=100, cok=100):
    return {"time": t, "lock": lock, "mode": "night", "run": RUN, "on_target_pct": ot, "call_ok_pct": cok,
            "anchor_sinr_p50": asinr, "nr_sinr_p50": nsinr, "nr_cells": "534"}
# baseline first (earliest), then two candidates; 340@3350 clearly beats, 340@100 does not
store.add(D, "lock_tests", row("none",     "2026-10-30 03:00:00", 2, 10))
store.add(D, "lock_tests", row("340@100",  "2026-10-30 03:16:00", 3, 11))
store.add(D, "lock_tests", row("340@3350", "2026-10-30 03:31:00", 8, 12))

run, base, others, win = brain.night_best()
assert base["lock"] == "none", base
assert [w["lock"] for w in win] == ["340@3350"], [w["lock"] for w in win]      # only the real winner, best-first
assert len(others) == 2
f = brain.night_finding(); assert f and f[0].startswith("RECOMMEND") and "340@3350" in f[0], f

# a later run where nothing beats the baseline -> INFO, no winner (auto-apply would be a no-op)
R2 = "2026-10-31 03:00:00"
for lock, t in (("none", "2026-10-31 03:00:00"), ("340@100", "2026-10-31 03:16:00")):
    store.add(D, "lock_tests", dict(row(lock, t, 8, 12), run=R2))
run, base, others, win = brain.night_best()
assert run == R2 and not win, (run, [w["lock"] for w in win])
assert brain.night_finding()[0].startswith("INFO")
# daytime ranking (loaded) -> anchor_best picks the winner with a clear gain (this is what auto-applies by day)
for lock, t, cok, asinr, nsinr in (("none", "2026-10-31 10:00:00", 90, 2, 8), ("340@100", "2026-10-31 10:20:00", 100, 8, 11)):
    store.add(D, "lock_tests", {"time": t, "lock": lock, "mode": "manual", "run": "", "on_target_pct": 100,
                                "call_ok_pct": cok, "anchor_sinr_p50": asinr, "nr_sinr_p50": nsinr, "nr_cells": "534"})
best, base, gain = brain.anchor_best()
assert best["lock"] == "340@100" and base["lock"] == "none" and gain, (best and best["lock"], base and base["lock"], gain)
# a daytime ranking with no real gain -> gain False (no auto-apply)
store.add(D, "lock_tests", {"time": "2026-10-31 11:00:00", "lock": "none", "mode": "manual", "run": "", "on_target_pct": 100,
                            "call_ok_pct": 100, "anchor_sinr_p50": 8, "nr_sinr_p50": 11, "nr_cells": "534"})
best, base, gain = brain.anchor_best()
assert not gain, (best["lock"], gain)
print("OK night_apply (+daytime)")
