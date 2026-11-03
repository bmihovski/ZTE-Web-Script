# deploy tool: what it uploads (code only, never data), the sftp batch, the local version, the OpenAPI contract diff and the
# before/after DB compare - the pure parts, so a broken manifest/contract/rollback check fails here, not on the router
import datetime, importlib.util, json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
spec = importlib.util.spec_from_file_location("deploy_tool", os.path.join(ROOT, "tools", "deploy.py"))
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

files = d.files(ROOT)
assert "keeper/__init__.py" in files and "keeper/start.sh" in files and "keeper/keeper.sh" in files, files
assert "keeper/devices/mikrotik.py" in files and "keeper/devices/zte/antennas.py" in files
assert files == sorted(files) and len({f for f in files}) == len(files)
assert not [f for f in files if "__pycache__" in f or "/data" in f or f.startswith("test/") or f.startswith("tools/")], files

batch = d.sftp_batch(files, "disk1/zte-keeper/keeper")
lines = batch.splitlines()
assert lines[:2] == ["-mkdir disk1/zte-keeper/keeper/devices", "-mkdir disk1/zte-keeper/keeper/devices/zte"], lines[:2]
assert "put keeper/__init__.py disk1/zte-keeper/keeper/" in lines
assert "put keeper/devices/zte/antennas.py disk1/zte-keeper/keeper/devices/zte/" in lines
assert len(lines) == 2 + len(files)
g = d.get_batch(["keeper/__init__.py", "keeper/devices/zte/antennas.py"], "disk1/zte-keeper/keeper", "/tmp/b")
assert "-get disk1/zte-keeper/keeper/__init__.py /tmp/b/keeper/__init__.py" in g.splitlines()
assert "-get disk1/zte-keeper/keeper/devices/zte/antennas.py /tmp/b/keeper/devices/zte/antennas.py" in g.splitlines()
assert d.remote_path("disk1/x", "keeper/start.sh") == "disk1/x/start.sh" and d.remote_path("disk1/x", "keeper/devices/zte/locks.py") == "disk1/x/devices/zte/locks.py"

v = re.search(r'VERSION = "([^"]+)"', open(os.path.join(ROOT, "keeper", "__init__.py")).read()).group(1)
assert d.local_version(ROOT) == v

# contract: the live API must describe exactly what this tree's ROUTES describe (paths + query parameter names)
a = {"paths": {"/status": {"get": {"parameters": []}},
               "/lock-test": {"post": {"parameters": [{"name": "cells"}, {"name": "rat"}, {"name": "align"}]}}}}
b = json.loads(json.dumps(a))
assert d.contract_diff(a, b) == []
b["paths"].pop("/status"); assert any("/status" in x for x in d.contract_diff(a, b)), d.contract_diff(a, b)
b = json.loads(json.dumps(a)); del b["paths"]["/lock-test"]["post"]["parameters"][1]
assert any("rat" in x for x in d.contract_diff(a, b)), d.contract_diff(a, b)
b = json.loads(json.dumps(a)); b["paths"]["/extra"] = {"get": {}}
assert any("/extra" in x for x in d.contract_diff(a, b))

# DB: nothing may disappear across a deploy (samples may grow; a missing kind is a regression)
before = {"records": [{"device": "zte", "kind": "samples", "records": 100},
                      {"device": "zte", "kind": "lock_tests", "records": 5}]}
after = {"records": [{"device": "zte", "kind": "samples", "records": 101},
                     {"device": "zte", "kind": "lock_tests", "records": 5}]}
assert d.db_diff(before, after) == []
after["records"][0]["records"] = 99
assert any("samples" in x and "99" in x for x in d.db_diff(before, after)), d.db_diff(before, after)
after["records"].pop(1)
assert any("lock_tests" in x for x in d.db_diff(before, after))

assert d.backup_dir(datetime.datetime(2026, 11, 1, 14, 30, 0), "/tmp/x") == "/tmp/x/backups/20261101-143000"

# validate: one run's rows -> the keeper's verdict (the loop's read-only half). The rules must match brain's exactly.
def vrow(lock, run, call, loss, p95, jit, sinr, rsrp, on=100, minutes=12, unstable="", rat="nr", pin=""):
    return {"lock": lock, "run": run, "rat": rat, "call_ok_pct": call, "loss_pct": loss, "lat_p95": p95, "jitter_ms": jit,
            "nr_sinr_p50": sinr, "nr_rsrp_p50": rsrp, "on_target_pct": on, "minutes": minutes, "unstable": unstable,
            "pin": pin, "antennas": "ant5 omni / ant6 dir" if rat == "nr" else ""}
RUN = "2026-11-01 22:07:00"
rows = [vrow("none", RUN, "91.7", "0.0", "67.0", "13.1", "7.0", "-79.0", on=""),
        vrow("534@636576", RUN, "100.0", "0.0", "62.7", "9.7", "7.0", "-78.0", pin="340@100")]
v = d.run_verdict(rows, "nr", lock="534@636576")
assert v["verdict"] == "keep" and v["best"] == "534@636576" and v["gain"] is True, v
assert [s["lock"] for s in v["steps"]] == ["534@636576"] and v["steps"][0]["status"] == "ranked", v["steps"]
assert v["steps"][0]["pin"] == "340@100", v["steps"][0]        # the JSON carries the step's LTE anchor
assert v["baseline"]["antennas"] == "ant5 omni / ant6 dir" and "keep" in "\n".join(d.verdict_text(v, "534@636576", "on"))
assert d.run_verdict(rows, "nr", lock="127@636576")["verdict"] == "apply"          # the winner is not the live lock
assert d.quality_values(rows[1]) > d.quality_values(rows[0]) and d.clearly_better(d.quality_values(rows[1]), d.quality_values(rows[0]))
same = [vrow("none", RUN, "100.0", "0.0", "60.0", "10.0", "7.0", "-78.0", on=""),
        vrow("534@636576", RUN, "100.0", "0.0", "58.0", "9.0", "7.5", "-77.0")]
assert d.run_verdict(same, "nr", lock="534@636576")["verdict"] == "no-lock", d.run_verdict(same, "nr", lock="534@636576")
skipped = [vrow("none", RUN, "100.0", "0.0", "60.0", "10.0", "7.0", "-78.0", on=""),
           vrow("786@636576", RUN, "", "", "", "", "", "", on="", minutes=0),
           vrow("127@636576", RUN, "100.0", "0.0", "50.0", "5.0", "9.0", "-80.0", unstable="1")]
v = d.run_verdict(skipped, "nr", lock="")
assert v["verdict"] == "no-result" and {s["lock"]: s["status"] for s in v["steps"]} == {"786@636576": "not measured", "127@636576": "unstable"}, v
assert "NO USABLE RESULT" in "\n".join(d.verdict_text(v)) and "not measured" not in v["why"]
nobase = [vrow("none", RUN, "", "", "", "", "", "", on="", minutes=0), vrow("534@636576", RUN, "100.0", "0.0", "50.0", "5.0", "9.0", "-80.0")]
assert d.run_verdict(nobase, "nr", lock="534@636576")["verdict"] == "no-result", d.run_verdict(nobase, "nr")
lte = [vrow("none", "R2", "90", "0", "50", "5", "", "", on="", rat="lte"),
       vrow("434@1550", "R2", "100", "0", "50", "5", "", "", rat="lte")]
assert d.run_verdict(lte, "lte", run="R2", lock="")["verdict"] == "apply", d.run_verdict(lte, "lte", run="R2")
lte_no = [vrow("none", "R6", "100", "0", "50", "5", "", "", on="", rat="lte"),
          vrow("340@1550", "R6", "100", "0", "52", "6", "", "", rat="lte")]
t = "\n".join(d.verdict_text(d.run_verdict(lte_no, "lte", run="R6", lock="340@100"), "340@100", "on"))
assert "/lte-lock?cell=none" in t and "/nr-lock" not in t, t      # the hint names the right leg
t = "\n".join(d.verdict_text(d.run_verdict([vrow("none", "R7", "100", "0", "50", "5", "9", "-80", on=""),
                                           vrow("534@636576", "R7", "100", "0", "52", "6", "8", "-79")], "nr", run="R7", lock="534@636576"), "534@636576", "on"))
assert "/nr-lock?cell=none" in t and "/lte-lock" not in t, t
near = [vrow("none", "R3", "98", "0", "50", "5", "", "", on="", rat="lte"),
        vrow("434@1550", "R3", "100", "0", "50", "5", "", "", rat="lte")]
assert d.lte_gain(near[1], near[0]) and not d.clearly_better(d.quality_values(near[1]), d.quality_values(near[0]))   # +2 pp: LTE wins, 5G does not
# a night screen run is never "apply" (unloaded network, B1 off): a gain only recommends a daytime test
night = [dict(vrow("none", "N1", "5", "0", "60", "10", "7", "-80", on="", rat="lte"), mode="night"),
         dict(vrow("340@1550", "N1", "5", "0", "55", "9", "12", "-75", rat="lte"), mode="night")]
v = d.run_verdict(night, "lte", run="N1", lock="")
assert v["night"] is True and v["verdict"] == "recommend" and "night screen (unloaded)" in "\n".join(d.verdict_text(v)), v
print("OK test_deploy_tool")
