# LTE lock test: baseline + each candidate locked (lock + reboot each), measured, ranked; the lock is always removed at the end
# part A: up to 'nobody continues' (B: skip / stop, AUTO-apply, winner kept, no result, never booted)
import os, sys
os.environ["LT_PORT"] = "18130"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures"))
from locktest_env import *  # noqa: F401,F403 - the shared setup (mock ZTE, cells, zte loop)
threading.Thread(target=zte, daemon=True).start()
phases = []; threading.Thread(target=lambda: [phases.append(locktest.lt.get("phase")) or time.sleep(0.05) for _ in range(60)], daemon=True).start()
locktest.lock_test(["none", "434@1550", "340@1550", "370@1550"])
stop.set()
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
for r in rows.values(): print(r)
assert rows["434@1550"]["on_target_pct"] == "100.0" and rows["434@1550"]["anchor"] == "1550/434" and rows["434@1550"]["anchor_sinr_p50"] == "10.0"
assert rows["434@1550"]["site"] == "1072/3" and rows["none"]["site"] == "1243/61" and rows["370@1550"]["site"] == ""
assert all(r["rat"] == "lte" and r["antennas"] == "" for r in rows.values())    # an LTE test never sweeps the antennas
assert not [s for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"]
assert zdev.eci("4db3d") == "1243/61" and zdev.eci("") == "" and zdev.eci(None) == ""
# a second column change keeps the older file as .v2 instead of overwriting .v1
assert rows["434@1550"]["nr_cells"].startswith("534:") and rows["none"]["nr_cells"].startswith("786:") and rows["none"]["on_target_pct"] == ""
assert rows["370@1550"]["note"].startswith("no samples")                     # cell not reachable from here
locks = [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"]
assert locks == ["0@0", "434@1550", "340@1550", "370@1550", "0@0"], locks       # baseline unlocked, ... , always unlocked at the end
assert [s[1] for s in m.st["sets"]].count("REBOOT_DEVICE") == 5
assert common.state().get("lock_test") is None and brain.ctx.get("hold") is None and locktest.lt["running"] is None
ev = store.rows(DATA, "events"); assert [e["event"] for e in ev] == ["lock test none", "lock test 434@1550", "lock test 340@1550", "lock test 370@1550"], ev
# align mode: after each lock the keeper waits for "aligned" (heading), baseline measured as is
m.st["sets"].clear(); stop.clear(); threading.Thread(target=zte, daemon=True).start()
def aligner(headings):
    for h in headings:
        while not locktest.lt["waiting"]: time.sleep(0.05)
        assert locktest.lt["running"] in ("434@1550", "340@1550"), locktest.lt["running"]
        locktest.lt["heading"] = h; locktest.lt["go"].set()
        while locktest.lt["waiting"]: time.sleep(0.05)
threading.Thread(target=aligner, args=(["108", "302"],), daemon=True).start()
locktest.lock_test(["none", "434@1550", "340@1550"], align=True)
rows = {r["lock"]: r for r in store.rows(DATA, "lock_tests")}
assert (rows["none"]["heading"], rows["none"]["mode"]) == ("", "as is") and (rows["434@1550"]["heading"], rows["434@1550"]["mode"]) == ("108", "aligned")
assert rows["340@1550"]["heading"] == "302" and rows["434@1550"]["anchor_sinr_p50"] == "10.0"
assert [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"] == ["0@0", "434@1550", "340@1550", "0@0"]
# nobody continues -> the test stops, lock removed
m.st["sets"].clear(); locktest.LT_ALIGN_TIMEOUT_MIN = 0.01
locktest.lock_test(["none", "434@1550", "340@1550"], align=True); stop.set()
assert [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"] == ["0@0", "434@1550", "0@0"] and locktest.lt["running"] is None and not locktest.lt["waiting"], \
    [s[3] for s in m.st["sets"] if s[1] == "LTE_LOCK_CELL_SET"]
assert any("measuring until" in (x or "") for x in phases) and any("rebooting" in (x or "") for x in phases) and locktest.lt["phase"] == "", set(phases)
print("OK test_locktest")
