"""pytest entry: every scenario script and module self-check runs in its own Python process.

Why processes: the keeper reads its env once at import and keeps module state (history, locks, threads, fake modem servers),
so each scenario needs a fresh interpreter - the same way the container starts it. A scenario passes when it exits 0
(they are plain assert scripts; run one alone with `python3 test/scenarios/guard.py` to see its output). A SIGSEGV *after* the final
"OK test_<name>" line is the known CPython 3.13/macOS OpenSSL-at-exit crash, not the scenario (see run()).
"""
import glob, os, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCENARIOS = sorted(glob.glob(os.path.join(HERE, "scenarios", "*.py")))
SELF_CHECKS = ["keeper.store", "keeper.sqm", "keeper.alert"]           # modules with an `if __name__ == "__main__"` self-check


# never reach the real operator ACS from a test (a fresh data dir makes the ZTE check-in due): scenarios that test the relay
# set their own fake YETTEL_URL / CHECKIN_DAYS
SAFE_ENV = {"CHECKIN_DAYS": "0", "YETTEL_URL": "http://127.0.0.1:9/never"}


def run(args, cwd):
    p = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True, timeout=300, env=dict(SAFE_ENV, **os.environ))
    if p.returncode < 0 and "OK test_" in (p.stdout or "") and "Traceback" not in (p.stdout or ""):
        # CPython 3.13 on macOS: a flaky SIGSEGV in OPENSSL_cleanup at interpreter exit, *after* the scenario passed (the OS crash
        # log shows OPENSSL_cleanup -> impl_free). The scenario finished (it printed OK); accept it, but say so in the output.
        print("warning: %s exited with signal %d after OK (OpenSSL-at-exit crash, tolerated)" % (args[0], -p.returncode))
        return
    assert p.returncode == 0, "exit %d\n%s%s" % (p.returncode, p.stdout[-4000:], p.stderr[-4000:])


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: os.path.basename(p)[:-3])
def test_scenario(path):
    run([os.path.basename(path)], os.path.dirname(path))


@pytest.mark.parametrize("module", SELF_CHECKS)
def test_self_check(module):
    run(["-m", module], ROOT)
