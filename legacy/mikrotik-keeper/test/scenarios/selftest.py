# self-test scenarios: statuses, worst roll-up, and alert.report gating. Read-only, no device.
import os, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DATA=tempfile.mkdtemp(), SELFTEST_AT="", TG_TOKEN="", TG_CHAT="", SQM_URL="http://x/rest")
sys.path.insert(0, ROOT)
from keeper import selftest, common, sqm, brain, alert, locktest

common.clock["ok"] = True; common.clock["skew"] = -1.0
sqm.st["last"] = {"limit": {"up": 30, "down": 150}, "rate": {"up": 0, "down": 0}, "delay_ms": 5}; sqm.st["error"] = None
brain.HIST.append({"nr_cell": "636576/534", "nr_sinr": "8", "lte_cell": "100/340", "lte_sinr": "5", "lat_p95": "40", "call_ok": "1"})

res = selftest.run(["all"], push=False)
by = {c["name"]: c["status"] for c in res["checks"]}
assert res["worst"] == "ok", res
assert by == {"clock": "ok", "link": "ok", "sqm": "ok", "lock": "ok", "night": "ok"}, by
assert [c["name"] for c in res["checks"]] == selftest.ORDER                    # ENUM order

sqm.st["error"] = "boom"
assert selftest.run(["sqm"], push=False)["worst"] == "fail"                    # sqm error -> fail
sqm.st["error"] = None; sqm.st["last"]["limit"]["down"] = 29                   # stuck low while idle -> warn
assert selftest.run(["sqm"], push=False)["checks"][0]["status"] == "warn"

common.clock["ok"] = False
assert selftest.run(["clock"], push=False)["worst"] == "fail"                  # clock not verified -> fail

brain.HIST.append({"nr_cell": "no5G", "lte_cell": "100/340", "lte_sinr": "1", "lat_p95": "900", "call_ok": "0"})
assert selftest.run(["link"], push=False)["checks"][0]["status"] == "warn"     # bad SINR/latency/call -> warn

assert alert.report("x") is False                                             # alerts off -> no push, no crash
assert "all" not in selftest.CHECKS and set(selftest.ORDER) == set(selftest.CHECKS)   # ENUM sane
print("OK selftest")
