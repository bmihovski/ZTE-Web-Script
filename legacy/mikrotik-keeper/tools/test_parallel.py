#!/usr/bin/env python3
"""Run the test scenarios in parallel: a log per scenario + summary.txt in test-logs/ (git-ignored), exit 1 on any FAIL.

  tools/test_parallel.py --docker            # on the Mac (OrbStack): one python:3.13-slim container per scenario - its own loopback
                                             # and /tmp, so the fixed ports (18100-18192) and shared /tmp dirs cannot collide
  tools/test_parallel.py --only night,clock  # a selection (substring of the file name)
  tools/test_parallel.py                     # plain processes (the agent's VM has no docker) - mind the shared ports
Each scenario: PYTHONDONTWRITEBYTECODE=1, LOCKTEST_IPERF=0 (the mock ZTE runs no iperf3), CHECKIN_DAYS=0; docker: --network=none and
the repo mounted read-only. The run takes ~ the slowest scenario (nr_locktest ~2 min) instead of the sum."""
import argparse, concurrent.futures, glob, os, shutil, subprocess, sys, time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT, IMAGE = 300, "python:3.13-slim"
ENV = {"PYTHONDONTWRITEBYTECODE": "1", "LOCKTEST_IPERF": "0", "CHECKIN_DAYS": "0", "YETTEL_URL": "http://127.0.0.1:9/never"}


def docker_cmd(repo, name, image=IMAGE, timeout=TIMEOUT):
    """docker run for one scenario: no network, the repo read-only at /src, the test env, the scenario's own timeout."""
    c = ["docker", "run", "--rm", "--network=none", "-v", "%s:/src:ro" % repo]
    for k, v in ENV.items(): c += ["-e", "%s=%s" % (k, v)]
    return c + ["-w", "/src/test/scenarios", image, "timeout", str(timeout), "python3", name]


def run_one(path, a):
    """(name, ok, why): its output in <logs>/<name>.log."""
    name = os.path.basename(path)[:-3]; cwd = os.path.dirname(path)
    cmd = docker_cmd(BASE, name + ".py", a.image, a.timeout) if a.docker else [sys.executable, name + ".py"]
    with open(os.path.join(a.logs, name + ".log"), "w") as fh:
        try:
            p = subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, env=dict(os.environ, **ENV),
                               timeout=a.timeout + (60 if a.docker else 0))
            rc = p.returncode
        except subprocess.TimeoutExpired: rc = 124
    out = open(os.path.join(a.logs, name + ".log"), errors="replace").read()
    if rc == 124: return name, False, "timeout %g s" % a.timeout
    if rc < 0 and "OK " in out and "Traceback" not in out: return name, True, "signal %d after OK (macOS OpenSSL-at-exit crash, tolerated)" % -rc
    return name, rc == 0, "" if rc == 0 else "exit %d" % rc


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--docker", action="store_true"); p.add_argument("--image", default=IMAGE)
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 4); p.add_argument("--timeout", type=float, default=TIMEOUT)
    p.add_argument("--only", default="", help="comma-separated substrings of scenario names")
    p.add_argument("--scenarios", default=os.path.join(BASE, "test", "scenarios")); p.add_argument("--logs", default=os.path.join(BASE, "test-logs"))
    a = p.parse_args(argv)
    if a.docker and not shutil.which("docker"): print("no docker on this machine"); return 2
    only = [x for x in a.only.split(",") if x]
    files = [f for f in sorted(glob.glob(os.path.join(a.scenarios, "*.py"))) if not only or any(o in os.path.basename(f) for o in only)]
    if not files: print("no scenario matches %r in %s" % (a.only, a.scenarios)); return 2
    os.makedirs(a.logs, exist_ok=True); t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        res = sorted(ex.map(lambda f: run_one(f, a), files))
    lines = [("OK   %s" % n + (" (%s)" % why if why else "")) if ok else "FAIL %s (%s)" % (n, why) for n, ok, why in res]
    open(os.path.join(a.logs, "summary.txt"), "w").write("\n".join(lines) + "\n")
    bad = [l for l in lines if l.startswith("FAIL")]
    print("\n".join(bad or lines[-3:]) + "\n%d scenarios, %d failed, %.0f s (%s, %d jobs) - logs in %s" % (
        len(lines), len(bad), time.time() - t0, "docker" if a.docker else "processes", a.jobs, a.logs))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
