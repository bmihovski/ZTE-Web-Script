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
print("OK test_deploy_tool")
