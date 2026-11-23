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

# RouterOS expects one stdout line per keeper message, even with concurrent API handlers.
import contextlib, io, tempfile
sys.path.insert(0, ROOT)
from keeper import common
class Output(io.StringIO):
    def __init__(self):
        super().__init__()
        self.writes = []
    def write(self, value):
        self.writes.append(value)
        return super().write(value)

with tempfile.TemporaryDirectory() as common.DATA:
    output = Output()
    with contextlib.redirect_stdout(output):
        common.log("slow request: GET /events 2.2 s")
    assert len(output.writes) == 1 and output.writes[0].endswith("GET /events 2.2 s\n"), output.writes
print("OK test_self_checks (%s)" % ", ".join(MODULES))
