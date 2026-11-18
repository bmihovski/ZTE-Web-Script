# The modules' own `if __name__ == "__main__"` self-checks, side by side - a scenario so BOTH runners have them (2026-11-18:
# tools/test_parallel.py, the deploy gate, ran only test/scenarios/*.py and never these; pytest had them as test_self_check)
import os, subprocess, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MODULES = ["keeper.store", "keeper.sqm", "keeper.alert", "keeper.captest"]
ps = {m: subprocess.Popen([sys.executable, "-m", m], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for m in MODULES}
bad = []
for m, p in ps.items():
    out = p.communicate(timeout=240)[0]
    if p.returncode: bad.append("%s exit %d\n%s" % (m, p.returncode, out[-3000:]))
assert not bad, "\n".join(bad)
print("OK test_self_checks (%s)" % ", ".join(MODULES))
