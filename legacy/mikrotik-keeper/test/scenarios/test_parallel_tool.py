# tools/test_parallel.py: the scenarios in parallel - one docker container each on the Mac (own loopback + /tmp: the fixed ports
# 18100-18192 and the shared /tmp dirs cannot collide), or plain processes; a log per scenario + summary.txt, exit 1 on any FAIL
import importlib.util, os, subprocess, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
spec = importlib.util.spec_from_file_location("tp", os.path.join(ROOT, "tools", "test_parallel.py"))
tp = importlib.util.module_from_spec(spec); spec.loader.exec_module(tp)

# the docker command for one scenario: isolated network, the repo read-only, the test env, the logs volume
c = tp.docker_cmd("/repo", "night.py", image="python:3.13-slim")
assert c[:4] == ["docker", "run", "--rm", "--network=none"] and "-v" in c and "/repo:/src:ro" in c, c
assert c[-4:] == ["timeout", str(tp.TIMEOUT), "python3", "night.py"] and "python:3.13-slim" in c, c
for e in ("PYTHONDONTWRITEBYTECODE=1", "LOCKTEST_IPERF=0", "CHECKIN_DAYS=0"): assert e in c, (e, c)
assert c[c.index("-w") + 1] == "/src/test/scenarios", c

# a real run (plain processes here; the VM has no docker): one OK, one FAIL, one hung -> summary, logs, exit 1
d = tempfile.mkdtemp(); sc = os.path.join(d, "scenarios"); os.makedirs(sc); out = os.path.join(d, "logs")
open(os.path.join(sc, "good.py"), "w").write('print("OK good")\n')
open(os.path.join(sc, "bad.py"), "w").write('assert 1 == 2, "boom"\n')
open(os.path.join(sc, "slow.py"), "w").write('import time\ntime.sleep(30)\n')
rc = tp.main(["--scenarios", sc, "--logs", out, "--jobs", "3", "--timeout", "2"])
summ = open(os.path.join(out, "summary.txt")).read().split("\n")
assert rc == 1 and "OK   good" in summ and any(l.startswith("FAIL bad") for l in summ) and any(l.startswith("FAIL slow") and "timeout" in l for l in summ), summ
assert "boom" in open(os.path.join(out, "bad.log")).read() and "OK good" in open(os.path.join(out, "good.log")).read()
os.remove(os.path.join(sc, "bad.py")); os.remove(os.path.join(sc, "slow.py"))
assert tp.main(["--scenarios", sc, "--logs", out, "--jobs", "2"]) == 0 and open(os.path.join(out, "summary.txt")).read().startswith("OK   good")
assert tp.main(["--scenarios", sc, "--logs", out, "--only", "nothing_matches"]) == 2          # a filter that selects nothing: an error
print("OK test_parallel_tool")
