# per-cell antenna mode: the newest GOOD NR lock-test row for the LOCKED 5G cell is the mode applied at boot - with AUTO=on it is
# applied, with dryrun only recommended, with off ignored; the kept/env mode returns when no compatible cell is locked
# (no age/heading gate: your call - the newest good record wins, its date/heading are shown so you can judge)
import importlib.util, os, shutil, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
sys.argv = ["x", "18133"]; spec = importlib.util.spec_from_file_location("m", os.path.join(here, "..", "fixtures", "mock_zte.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DATA = "/tmp/zte-keeper-test-cm"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(NIGHT_SCREEN="off", DATA=DATA, ZTE_HOST="127.0.0.1:18133", ZTE_PASS_HASH=m.S(m.PW), SWEEP_DAYS="0", CPE_READY_TRIES="1", CPE_READY_GAP_S="0")
sys.path.insert(0, ROOT)
from keeper import acs, api, brain, common, locktest, store
from keeper.devices import zte as zdev
from keeper.devices.zte import antennas, web as zweb

def ago(days):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
def row(t, mode="ant5 dir / ant6 omni", a5="4", a6="1", target="100.0", unstable="0", kind="fixed", rat="nr", lock="534@636576"):
    return {"time": t, "rat": rat, "lock": lock, "antennas": mode, "ant5": a5, "ant6": a6, "on_target_pct": target, "unstable": unstable,
            "mode": kind, "call_ok_pct": "100.0", "lat_p95": "40.0", "jitter_ms": "4.0", "loss_pct": "0.0", "heading": "108"}
for r in [row(ago(3)),                                                          # good, but an older one wins
          row(ago(2), "ant5 omni / ant6 omni", "1", "1"),                       # the newest good -> this mode
          row(ago(1), "ant5 dir / ant6 dir", "4", "4", kind="cut"),             # cut: not a result
          row(ago(0.5), "ant5 dir / ant6 dir", "4", "4", unstable="1"),         # unstable anchor: not a result
          row(ago(0.2), "ant5 dir / ant6 dir", "4", "4", target="50.0"),        # off target: not a result
          row(ago(0.1), "ant5 dir / ant6 dir", "4", "4", rat="lte", lock="1550/340")]:   # LTE row (no 5G cell mode)
    store.add(DATA, "lock_tests", r)

rec = locktest.cell_mode("534@636576"); print(rec)
assert rec and rec["mode"] == "ant5 omni / ant6 omni" and (rec["ant5"], rec["ant6"]) == ("1", "1") and rec["time"] == ago(2), rec
assert rec["heading"] == "108" and locktest.cell_mode("747@629988") is None and locktest.cell_mode("") is None and locktest.cell_mode(None) is None

# the kept mode is the fallback; the locked cell's record wins only with AUTO=on
common.state({"antennas": {"auto": "0", "ant5": "4", "ant6": "4"}, "nr_lock": {"cell": "534@636576", "state": "active"}})
brain.AUTO = "on"
eff, src, r = antennas.effective()
assert (eff["ant5"], eff["ant6"]) == ("1", "1") and "534@636576" in src and r["time"] == ago(2), (eff, src)
brain.AUTO = "dryrun"
eff, src, r = antennas.effective()
assert (eff["ant5"], eff["ant6"]) == ("4", "4") and src == "kept" and r["mode"] == "ant5 omni / ant6 omni", (eff, src, r)
brain.AUTO = "off"
assert (antennas.effective()[0]["ant5"], antennas.effective()[0]["ant6"]) == ("4", "4") and antennas.effective()[1] == "kept"
brain.AUTO = "on"
common.state({"nr_lock": None}); assert antennas.effective()[1] == "kept"
common.state({"nr_lock": {"cell": "747@629988", "state": "active"}}); assert antennas.effective()[1] == "kept"      # no record
common.state({"nr_lock": {"cell": "534@636576", "state": "released"}}); assert antennas.effective()[1] == "kept"    # not locked

# no measurement for the locked cell -> the env defaults (no per-cell record, nothing kept from the panel/sweep)
common.state({"antennas": None, "nr_lock": {"cell": "747@629988", "state": "active"}})
eff, src, rec = antennas.effective()
assert src == "env" and eff == {"auto": antennas.AUTO, "ant5": antennas.ANT5, "ant6": antennas.ANT6} and rec is None, (eff, src, rec)

# the boot apply puts the cell's mode on the modem (AUTO=on); dryrun puts the kept mode back and only names the record
common.state({"antennas": {"auto": "0", "ant5": "4", "ant6": "4"}, "nr_lock": {"cell": "534@636576", "state": "active"}})
m.st["sets"].clear(); brain.AUTO = "on"
antennas.apply_after_boot(9999)
assert [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"][-2:] == [("5", "1"), ("6", "1")]
assert m.st["sets"][-1][1] == "LOGOUT"
brain.AUTO = "dryrun"; m.st["sets"].clear()
antennas.apply_after_boot(9999)
assert [(s[2], s[3]) for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"][-2:] == [("5", "4"), ("6", "4")]

# a worker whose boot was superseded stands down (no second apply in the same / next boot)
m.st["sets"].clear(); antennas.apply_after_boot(9999, keep=lambda: False)
assert not [s for s in m.st["sets"] if s[1] == "BSP_ANTENNA_STATE_SET"], m.st["sets"]

# /status: the kept mode, what is applied and why, and the locked cell's learned record
brain.AUTO = "on"; cfg = zdev.config()
assert cfg["antennas"] == {"auto": "0", "ant5": "4", "ant6": "4"} and cfg["antennas_effective"]["ant5"] == "1"
assert cfg["antennas_source"].startswith("cell 534@636576") and cfg["antennas_cell"]["mode"] == "ant5 omni / ant6 omni"
ev = [e for e in store.rows(DATA, "events") if e["event"] == "applied"]
assert ev and any("534@636576" in e["to"] for e in ev), ev    # the AUTO=on apply named the source cell
# the boot apply is queued once per boot: two informs of the same boot must not apply twice, a real reboot (after 20 s) does
zdev.st.update(boot_apply=False, applying=False, boot_gen=0, last_boot=0.0)
zdev.on_boot(); zdev.on_boot()
assert zdev.st["boot_gen"] == 1 and zdev.st["boot_apply"] is True, zdev.st
zdev.st["last_boot"] -= 30; zdev.on_boot(); assert zdev.st["boot_gen"] == 2, zdev.st
zdev.st.update(boot_apply=False, applying=False, boot_gen=0, last_boot=0.0)
assert brain.wake(0) is False                                             # no wake window -> no traffic

# the REAL internet_test body (the other scenarios patch it, which is how a typo slipped through), and the readiness helpers
off = brain.internet_test(0.1, url="off")
assert off["down_mbps"] is None and off["tested_mb"] == 0.0 and off["error"] == "" and "lat_p95" in off, off
bad = brain.internet_test(0.5, url="http://127.0.0.1:9/x", mb=1)
assert bad["error"] and bad["tested_mb"] == 0.0 and bad["down_mbps"] is None, bad
targets, buf = brain.TARGETS, list(brain.lat_buf)
brain.TARGETS = []; assert brain.wait_online(0.1) is True
brain.TARGETS = [("127.0.0.1", 9)]; brain.lat_buf.clear(); brain.lat_buf.append((time.time(), None, "x"))
assert brain.wait_online(0.5) is False
n = time.time(); brain.lat_buf.clear(); [brain.lat_buf.append((n - i * 0.01, 12.0, "x")) for i in range(6)]
assert brain.wait_online(1, stable=5) is True
brain.TARGETS = targets; brain.lat_buf.clear(); [brain.lat_buf.append(x) for x in buf]
assert zweb.ready(timeout=5, tries=2, gap=0.05) is True
keep = zweb.ZTE; zweb.ZTE = "127.0.0.1:9"; assert zweb.ready(timeout=1, tries=1, gap=0.05) is False; zweb.ZTE = keep
print("OK test_cell_mode")
