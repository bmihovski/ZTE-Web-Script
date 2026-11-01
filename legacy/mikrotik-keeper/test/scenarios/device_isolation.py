# device isolation: every measured record (samples, events, lock tests, sweeps) is tagged with DEVICE, and the per-cell mode lookup,
# the lock-test ranking and /status only ever see the ACTIVE device's rows - a ZTE row never decides a MikroTik mode or vice versa.
# (The learned cell ids in cell-ids.json are network facts and stay shared by design; everything measured is per device.)
import json, os, shutil, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
DATA = "/tmp/zte-keeper-test-iso"; shutil.rmtree(DATA, ignore_errors=True); os.makedirs(DATA)
os.environ.update(DATA=DATA, NIGHT_SCREEN="off")
sys.path.insert(0, ROOT)
from keeper import brain, common, locktest, store
assert store.DEVICE == "zte" and common.state_file() == "state.json"       # this process is the ZTE keeper

def row(t, mode, a5, a6, sinr):
    return {"time": t, "rat": "nr", "lock": "534@636576", "antennas": mode, "ant5": a5, "ant6": a6, "on_target_pct": "100.0",
            "unstable": "0", "mode": "fixed", "call_ok_pct": "100.0", "lat_p95": "40.0", "jitter_ms": "4.0", "loss_pct": "0.0",
            "nr_sinr_p50": sinr, "nr_rsrp_p50": "-85.0", "heading": "108"}
now = time.strftime("%Y-%m-%d %H:%M:%S")
store.add(DATA, "lock_tests", row(now, "ant5 dir / ant6 omni", "4", "1", "9.0"))                       # ZTE (the default device)
store.add(DATA, "lock_tests", row(now, "ant5 omni / ant6 omni", "1", "1", "5.0"), device="mikrotik")   # an ATL row (hypothetical: the ATL today has no antennas)
brain.load_registry(DATA)
assert locktest.cell_mode("534@636576")["mode"] == "ant5 dir / ant6 omni"          # only the ZTE row decides here
assert len(store.rows(DATA, "lock_tests")) == 1 and [r["lock"] for r in brain.anchor_ranking("nr")["ranked"]] == ["534@636576"]
allrows = store.rows(DATA, "lock_tests", device="*")
assert len(allrows) == 2 and {r["antennas"] for r in allrows} == {"ant5 dir / ant6 omni", "ant5 omni / ant6 omni"}   # both are stored, tagged

# the same data dir under DEVICE=mikrotik sees only the ATL row (its own mode), never the ZTE one
code = r'''
import json, sys
sys.path.insert(0, %r)
from keeper import brain, common, locktest, store
brain.load_registry(%r)
print("JSON " + json.dumps({"device": store.DEVICE, "state": common.state_file(), "mode": locktest.cell_mode("534@636576"),
                            "rows": len(store.rows(%r, "lock_tests")), "ranked": [r["lock"] for r in brain.anchor_ranking("nr")["ranked"]]}))
''' % (ROOT, DATA, DATA)
p = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, DEVICE="mikrotik"), capture_output=True, text=True, timeout=30)
assert p.returncode == 0, p.stderr
d = json.loads([x for x in p.stdout.splitlines() if x.startswith("JSON ")][-1][5:])
assert d["device"] == "mikrotik" and d["state"] == "state-mikrotik.json", d
assert d["mode"]["mode"] == "ant5 omni / ant6 omni" and d["mode"]["ant5"] == "1", d          # its own row, not the ZTE one
assert d["rows"] == 1 and d["ranked"] == ["534@636576"], d
assert common.state_file() == "state.json"                                                  # the parent stays the ZTE keeper
print("OK test_device_isolation")
