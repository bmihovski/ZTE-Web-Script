"""Keeper deploy / verify / rollback (standard library; runs on the Mac, not in the container).

    python3 tools/deploy.py check                    # compile + pytest + userscript syntax
    python3 tools/deploy.py status                   # live snapshot: version, locks, lock test, antennas, DB
    python3 tools/deploy.py logs -n 20               # the keeper's container log lines
    python3 tools/deploy.py backup                   # download what is on the router (backups/<time>/ + manifest.json)
    python3 tools/deploy.py deploy                   # preflight, backup, upload, restart, verify (auto-rollback on failure)
    python3 tools/deploy.py deploy --skip-tests
    python3 tools/deploy.py verify                   # read-only: version / OpenAPI contract / config / fresh sample vs this tree
    python3 tools/deploy.py rollback backups/<time>  # upload that backup, restart, verify its version

Deploy uploads only the code (`keeper/*.py`, the start scripts, the drivers) and never `keeper/data/`. It backs up what is on the
router first, and when the verification fails it puts the backup back. Host / destination / API: `--host admin@192.168.6.1`,
`--dst disk1/zte-keeper/keeper`, `--url http://keeper.lan:7557` (or KEEPER_HOST / KEEPER_DST / KEEPER_URL).
"""
import argparse, datetime, glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # legacy/mikrotik-keeper
HOST, DST, URL = "admin@192.168.6.1", "disk1/zte-keeper/keeper", "http://keeper.lan:7557"
CONTAINER = "zte-keeper"
SSH_TIMEOUT = 180


# ---------- what goes to the router (pure: unit-tested) ----------
def files(base=BASE):
    """The code files a deploy uploads, relative to the keeper project and sorted: the package modules, the start scripts and the
    drivers. `keeper/data/`, `__pycache__`, tests and tools are never uploaded."""
    b = os.path.abspath(base); k = os.path.join(b, "keeper")
    out = glob.glob(os.path.join(k, "*.py"))
    out += [p for p in (os.path.join(k, "start.sh"), os.path.join(k, "keeper.sh")) if os.path.isfile(p)]
    out += glob.glob(os.path.join(k, "devices", "*.py"))
    out += glob.glob(os.path.join(k, "devices", "zte", "*.py"))
    out = [p for p in out if os.path.isfile(p) and "__pycache__" not in p]
    return sorted(os.path.relpath(p, b).replace(os.sep, "/") for p in out)


def remote_path(dst, f):
    """The router path of a file: the deploy uploads `keeper/x.py` to `<dst>/x.py`, `keeper/devices/zte/x.py` to
    `<dst>/devices/zte/x.py` (the local `keeper/` prefix is the project folder, not a router folder)."""
    rel = f[len("keeper/"):] if f.startswith("keeper/") else f
    return dst + "/" + rel


def sftp_batch(file_list, dst, src=""):
    """The sftp batch: create the two driver folders (the `-` ignores 'already exists') and put every file into the folder its
    relative path needs (`keeper/devices/zte/x.py` -> `<dst>/devices/zte/`). `src` prefixes the local paths."""
    lines = ["-mkdir %s/devices" % dst, "-mkdir %s/devices/zte" % dst]
    for f in file_list:
        rel = f[len("keeper/"):] if f.startswith("keeper/") else f
        rdir = os.path.dirname(rel)
        rpath = dst + ("/" + rdir if rdir else "") + "/"
        lines.append("put %s %s" % (os.path.join(src, f) if src else f, rpath))
    return "\n".join(lines) + "\n"


def get_batch(file_list, dst, out):
    """The sftp batch that downloads the router files into a backup folder (same relative layout as the upload)."""
    return "\n".join("-get %s %s" % (remote_path(dst, f), os.path.join(out, f)) for f in file_list) + "\n"


def local_version(base=BASE):
    """The VERSION of this tree (keeper/__init__.py)."""
    with open(os.path.join(base, "keeper", "__init__.py")) as fh:
        m = re.search(r'VERSION = "([^"]+)"', fh.read())
    if not m: raise SystemExit("VERSION not found in keeper/__init__.py")
    return m.group(1)


def backup_dir(now, base=BASE):
    """Where a backup taken at `now` goes: `<base>/backups/YYYYmmdd-HHMMSS`."""
    return os.path.join(base, "backups", now.strftime("%Y%m%d-%H%M%S"))


def contract_diff(expected, live):
    """Differences between this tree's API and the live one: path set and query parameter names per method."""
    out = []
    ep, lp = expected.get("paths", {}), live.get("paths", {})
    for p in sorted(set(ep) - set(lp)): out.append("missing path %s" % p)
    for p in sorted(set(lp) - set(ep)): out.append("unexpected path %s" % p)
    for p in sorted(set(ep) & set(lp)):
        for m in ("get", "post"):
            if m in ep[p] and m not in lp[p]: out.append("%s %s missing" % (m.upper(), p)); continue
            if m in ep[p] and m in lp[p]:
                en = [x.get("name") for x in (ep[p][m].get("parameters") or [])]
                ln = [x.get("name") for x in (lp[p][m].get("parameters") or [])]
                if en != ln: out.append("%s %s parameters %s != %s" % (m.upper(), p, en, ln))
    return out


def db_diff(before, after):
    """Records that disappeared or shrank across a deploy (samples may grow; a missing device/kind is a regression)."""
    out = []
    b = {(r.get("device"), r.get("kind")): r.get("records", 0) for r in (before or {}).get("records", [])}
    a = {(r.get("device"), r.get("kind")): r.get("records", 0) for r in (after or {}).get("records", [])}
    for k in sorted(set(b) - set(a)): out.append("%s/%s missing after the deploy" % k)
    for k in sorted(set(b) & set(a)):
        if a[k] < b[k]: out.append("%s/%s %d -> %d records" % (k[0], k[1], b[k], a[k]))
    return out


# ---------- local / live helpers ----------
def _get(url, path, timeout=10):
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _api(url, timeout=3):
    try: return _get(url, "/openapi.json", timeout)
    except Exception: return None


def _wait_api(url, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        spec = _api(url)
        if spec: return spec
        time.sleep(2)
    return None


def _epoch(text):
    try: return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError): return None


def _wait_fresh_status(url, since, timeout=150):
    """The first /status whose sample time is at/after `since` (the keeper has read the modem once after the restart)."""
    t0 = time.time(); st = None
    while time.time() - t0 < timeout:
        try:
            st = _get(url, "/status")
            if (_epoch(st.get("time")) or 0) >= since: return st
        except Exception: pass
        time.sleep(3)
    return st


def local_spec(base=BASE):
    """The OpenAPI document this tree generates - a subprocess, so the tool never imports the keeper into its own process."""
    data = tempfile.mkdtemp(prefix="keeper-deploy-")
    try:
        env = dict(os.environ, DEVICE="zte", DATA=data)
        code = "import json; from keeper.api import openapi; print(json.dumps(openapi()))"
        p = subprocess.run([sys.executable, "-c", code], cwd=base, env=env, capture_output=True, text=True)
        if p.returncode: raise SystemExit("cannot build the local OpenAPI document:\n" + p.stderr[-2000:])
        return json.loads(p.stdout)
    finally:
        shutil.rmtree(data, ignore_errors=True)


def check(base=BASE, skip_tests=False):
    """Local gate: compile, the scenario suite (unless skipped), the userscript syntax."""
    print("== check")
    p = subprocess.run([sys.executable, "-m", "compileall", "-q", "keeper"], cwd=base)
    if p.returncode: raise SystemExit("compileall failed")
    if not skip_tests:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=base)
        if p.returncode: raise SystemExit("pytest failed - fix it or pass --skip-tests")
    js = os.path.join(os.path.dirname(base), "zte-script-legacy.js")
    if os.path.isfile(js) and shutil.which("node"):
        p = subprocess.run(["node", "--check", js], capture_output=True, text=True)
        if p.returncode: raise SystemExit("userscript syntax: " + p.stderr[-1000:])
    print("   compile / tests / userscript OK")


def _ssh(host, command):
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
                           capture_output=True, text=True, timeout=SSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SystemExit("ssh %s timed out" % host)
    if p.returncode: raise SystemExit("ssh %s failed: %s" % (host, (p.stderr or p.stdout)[-1000:]))
    return p.stdout


def _sftp(host, batch):
    try:
        p = subprocess.run(["sftp", "-q", "-b", "-", host], input=batch, capture_output=True, text=True, timeout=SSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise SystemExit("sftp %s timed out" % host)
    if p.returncode: raise SystemExit("sftp %s failed: %s" % (host, (p.stderr or p.stdout)[-1500:]))
    return p.stdout


def upload(host, dst, src, file_list):
    """Upload the files (their paths relative to `src`) into the router's folder structure."""
    _sftp(host, sftp_batch(file_list, dst, src=src))


def backup(base=BASE, host=HOST, dst=DST, url=URL):
    """Download the remote code + a manifest (version, sha256 per file, the live /db) into backups/<time>/."""
    live = _api(url)
    try: snap = _get(url, "/db") if live else None
    except Exception: snap = None
    bdir = backup_dir(datetime.datetime.now(), base)
    b = os.path.abspath(base); fl = files(base)
    for f in fl: os.makedirs(os.path.dirname(os.path.join(bdir, f)) or bdir, exist_ok=True)
    _sftp(host, get_batch(fl, dst, bdir))
    have = [f for f in fl if os.path.isfile(os.path.join(bdir, f))]
    version = ""
    try:
        version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(bdir, "keeper", "__init__.py")).read()).group(1)
    except (OSError, AttributeError): pass
    man = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "host": host, "dst": dst, "version": version,
           "api_version": (live or {}).get("info", {}).get("version", ""),
           "files": [{"name": f, "sha256": hashlib.sha256(open(os.path.join(bdir, f), "rb").read()).hexdigest()} for f in have],
           "db": snap}
    with open(os.path.join(bdir, "manifest.json"), "w") as fh: json.dump(man, fh, indent=1)
    return bdir, man


def restart(host=HOST):
    """Stop and start the container; returns the keeper's log lines (including the new `start (v2 ...)`)."""
    return _ssh(host, '/container stop [find name="%s"]; :delay 20s; /container start [find name="%s"]; :delay 5s; '
                      '/log print where message~"%s"' % (CONTAINER, CONTAINER, CONTAINER))


def verify(base=BASE, url=URL, expected_version=None, before=None, since=None, contract=True, require_keys=True):
    """Read-only verification. Returns (problems, warnings, spec): version, OpenAPI contract, the new config keys, a fresh
    sample (when `since`) and the before/after DB compare (warnings)."""
    problems, warnings = [], []
    spec = _wait_api(url) if since else _api(url)
    if not spec: return ["the API did not answer within 120 s at %s" % url], warnings, None
    got = spec.get("info", {}).get("version", "")
    if expected_version and got != expected_version: problems.append("version %s != %s" % (got, expected_version))
    if contract:
        problems += ["contract: " + x for x in contract_diff(local_spec(base), spec)]
    st = _wait_fresh_status(url, since) if since else (_get(url, "/status") if spec else None)
    if not st:
        problems.append("no /status")
    else:
        c = st.get("config") or {}
        if require_keys:
            for k in ("antennas_effective", "antennas_source", "antennas_cell", "lock_test_rat", "lock_test_anchor"):
                if k not in c: problems.append("config lacks %s" % k)
        if since and (_epoch(st.get("time")) or 0) < since: problems.append("no fresh sample after the restart")
        if before:
            try: warnings += ["db: " + x for x in db_diff(before, _get(url, "/db"))]
            except Exception as e: warnings.append("db compare failed: %r" % e)
    return problems, warnings, spec


def rollback_dir(bdir, host=HOST, dst=DST, url=URL):
    """Put a backup back (upload its files, restart, verify its version)."""
    with open(os.path.join(bdir, "manifest.json")) as fh: man = json.load(fh)
    fl = [f["name"] for f in man.get("files", [])]
    if not fl: raise SystemExit("backup %s has no files" % bdir)
    upload(host, dst, bdir, fl)
    t0 = time.time(); restart(host)
    problems, warnings, _ = verify(bdir, url, man.get("version") or "", since=t0, contract=False, require_keys=False)
    if problems: raise SystemExit("rollback verification failed: " + "; ".join(problems))
    print("   rolled back to %s (%d files)" % (man.get("version") or "?", len(fl)))


def deploy(base=BASE, host=HOST, dst=DST, url=URL, skip_tests=False, force=False, do_rollback=True):
    """The full gate: preflight -> backup -> upload -> restart -> verify; auto-rollback when the verification fails."""
    version = local_version(base)
    print("== preflight")
    live = _api(url)
    if live and not force:
        try:
            lt = _get(url, "/lock-tests")
            if lt.get("running"): raise SystemExit("a lock test is running (%s, %s) - stop it or pass --force" % (lt.get("running"), lt.get("phase")))
        except SystemExit: raise
        except Exception: pass
    check(base, skip_tests)
    print("== backup")
    bdir, man = backup(base, host, dst, url)
    print("   %s (%d files, router version %s)" % (bdir, len(man["files"]), man.get("api_version") or man.get("version") or "?"))
    print("== upload")
    upload(host, dst, base, files(base))
    print("== restart")
    t0 = time.time()
    out = restart(host)
    print("   " + "\n   ".join(out.strip().splitlines()[-3:]))
    print("== verify")
    problems, warnings, spec = verify(base, url, version, before=man.get("db"), since=t0)
    for w in warnings: print("   warning:", w)
    if problems:
        print("!! verification failed:")
        for x in problems: print("   -", x)
        if do_rollback and man.get("files"):
            print("== rollback")
            rollback_dir(bdir, host, dst, url)
        raise SystemExit(1)
    print("OK: %s live, contract matches, fresh sample, %d files uploaded" % (version, len(man["files"])))


def status(url=URL):
    """Read-only live snapshot."""
    spec = _api(url)
    if not spec: raise SystemExit("keeper not reachable at " + url)
    st = _get(url, "/status"); db = _get(url, "/db"); lt = _get(url, "/lock-tests")
    c = st.get("config") or {}
    print("version %s | verdict %s | %s | 5G %s" % (spec["info"]["version"], st.get("verdict"), st.get("time"), st.get("nr_cell")))
    print("lte lock %s | nr lock %s" % ((c.get("lte_lock") or {}).get("cell"), (c.get("nr_lock") or {}).get("cell")))
    print("antennas kept %s | effective %s | source %s" % (c.get("antennas"), c.get("antennas_effective"), c.get("antennas_source")))
    print("lock test %s | phase %s | plan %s" % (lt.get("running"), lt.get("phase"), lt.get("plan")))
    print("db " + ", ".join("%s/%s %d" % (r["device"], r["kind"], r["records"]) for r in db.get("records", [])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="keeper deploy / verify / rollback")
    ap.add_argument("--host", default=os.environ.get("KEEPER_HOST", HOST))
    ap.add_argument("--dst", default=os.environ.get("KEEPER_DST", DST))
    ap.add_argument("--url", default=os.environ.get("KEEPER_URL", URL))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("--skip-tests", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("logs"); p.add_argument("-n", type=int, default=20)
    sub.add_parser("backup")
    p = sub.add_parser("deploy"); p.add_argument("--skip-tests", action="store_true"); p.add_argument("--force", action="store_true")
    p.add_argument("--no-rollback", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("--version"); p.add_argument("--no-contract", action="store_true")
    p = sub.add_parser("rollback"); p.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "check": check(BASE, a.skip_tests)
    elif a.cmd == "status": status(a.url)
    elif a.cmd == "logs":
        print("\n".join(_ssh(a.host, '/log print where message~"%s"' % CONTAINER).splitlines()[-a.n:]))
    elif a.cmd == "backup":
        bdir, man = backup(BASE, a.host, a.dst, a.url); print(bdir, man.get("version"), len(man["files"]))
    elif a.cmd == "deploy": deploy(BASE, a.host, a.dst, a.url, a.skip_tests, a.force, not a.no_rollback)
    elif a.cmd == "verify":
        problems, warnings, _ = verify(BASE, a.url, a.version or local_version(BASE), contract=not a.no_contract)
        for w in warnings: print("warning:", w)
        if problems:
            for x in problems: print("-", x)
            sys.exit(1)
        print("OK %s live, contract matches" % (a.version or local_version(BASE)))
    elif a.cmd == "rollback": rollback_dir(a.dir, a.host, a.dst, a.url)


if __name__ == "__main__":
    main()
