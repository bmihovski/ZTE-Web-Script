"""Keeper deploy / verify / rollback (standard library; runs on the Mac, not in the container).

    python3 tools/deploy.py check                    # compile + pytest + userscript syntax
    python3 tools/deploy.py status                   # live snapshot: version, locks, lock test, antennas, DB
    python3 tools/deploy.py logs -n 20               # the keeper's container log lines
    python3 tools/deploy.py backup                   # download what is on the router (backups/<time>/ + manifest.json)
    python3 tools/deploy.py deploy                   # preflight, backup, upload, restart, verify (auto-rollback on failure)
    python3 tools/deploy.py deploy --skip-tests
    python3 tools/deploy.py verify                   # read-only: version / OpenAPI contract / config / fresh sample vs this tree
    python3 tools/deploy.py rollback backups/<time>  # upload that backup, restart, verify its version
    python3 tools/deploy.py validate --rat nr        # validate one lock-test run (read-only): baseline vs candidates, verdict
    python3 tools/deploy.py validate --rat nr --wait # ... after the running test finishes (poll every 15 s, --timeout 3600)

The validation loop: check -> deploy -> start/schedule a lock test -> `validate --wait` -> branch on the verdict (keep / apply /
no-lock / no-result; exit 0 / 2 / 3 with --expect-lock) -> fix + improve -> repeat. `validate` only reads the live API.
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


REVIEW_PATTERNS = [
    (r"reversed\(\s*brain\.HIST\s*\)", "brain.HIST iterated live: wrap in list() (the sampler appends -> RuntimeError)"),
    (r"for\s+[^\n]*\bin\s+REG\[[\"'](?:lte|nr)[\"']\]\.items\(\)", "REG dict iterated live: wrap in list() (dict changed size during iteration)"),
    (r"while\s+[^\n]*time\.time\(\)\s*-\s*t0", "wall-clock wait: use time.monotonic() (a clock jump aborts the wait)"),
    (r"mean\(\s*\[[^\]]*loss_pct[^\]]*or\s+0", "loss averaged with 0 for missing probes: drop None (never fabricate 0 % loss)"),
]


def review(base=BASE):
    """Static scan for the defect classes of docs/REVIEW-2026-11-03.md: live deque/dict iteration, wall-clock waits, fabricated
    0 % loss, and a sweep error path without a logout (the ZTE's single web session). Read-only; returns the findings (empty = clean)."""
    hits = []
    for p in sorted(glob.glob(os.path.join(base, "keeper", "**", "*.py"), recursive=True)):
        rel = os.path.relpath(p, base)
        with open(p, encoding="utf-8") as fh: lines = fh.read().splitlines()
        for i, line in enumerate(lines, 1):
            for rx, why in REVIEW_PATTERNS:
                if re.search(rx, line): hits.append("%s:%d %s" % (rel, i, why))
        if rel.endswith("devices/zte/antennas.py"):
            for fn in ("antenna_sweep", "sweep_for_test"):
                start = next((i for i, l in enumerate(lines) if l.startswith("def " + fn)), None)
                if start is None: continue
                block = lines[start:start + 140]
                for j, l in enumerate(block):
                    if l.startswith("    except") and not any("logout" in x for x in block[j:j + 8]):
                        hits.append("%s:%d %s: except without logout (the ZTE's single web session stays open)" % (rel, start + j + 1, fn))
    return hits


def check(base=BASE, skip_tests=False):
    """Local gate: compile, the scenario suite (unless skipped), the userscript syntax, the review scan."""
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
    hits = review(base)
    if hits:
        print("== review")
        for h in hits[:20]: print("   " + h)
        raise SystemExit("review: %d finding(s) - fix them (see docs/REVIEW-2026-11-03.md)" % len(hits))
    print("   compile / tests / userscript / review OK")


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


def _num(v):
    """float(value) or None (the API returns numbers as strings)."""
    try: return float(v)
    except (TypeError, ValueError): return None


def quality_values(r):
    """The keeper's 5G decision tuple (brain.quality_values): call-ready %, loss %, p95, jitter, then SINR, RSRP. The first
    metric that differs beyond its margin decides (call-ready 5 pp, loss 1 pp, p95 10 ms, jitter 10 ms); signal only breaks a tie."""
    n = lambda v, worst: worst if v is None else v
    return (n(_num(r.get("call_ok_pct")), -1), -n(_num(r.get("loss_pct")), 999), -n(_num(r.get("lat_p95")), 9999),
            -n(_num(r.get("jitter_ms")), 9999), n(_num(r.get("nr_sinr_p50")), -99), n(_num(r.get("nr_rsrp_p50")), -999))


def clearly_better(a, b):
    """Does quality tuple `a` clearly beat `b` (brain.clearly_better)?"""
    (ac, al, ap, aj, asr, arp), (bc, bl, bp, bj, bsr, brp) = a, b
    if abs(ac - bc) >= 5: return ac > bc
    if abs(al - bl) >= 1.0: return al > bl
    if abs(ap - bp) >= 10: return ap > bp
    if abs(aj - bj) >= 10: return aj > bj
    return asr - bsr >= 1 or (asr == bsr and arp - brp >= 2)


def lte_gain(best, base):
    """The LTE clear-win rule (brain.anchor_best): call-ready +1 pp, 5G SINR +2 dB or anchor SINR +3 dB."""
    return ((_num(best.get("call_ok_pct")) or 0) - (_num(base.get("call_ok_pct")) or 0) >= 1 or
            (_num(best.get("nr_sinr_p50")) or -99) - (_num(base.get("nr_sinr_p50")) or -99) >= 2 or
            (_num(best.get("anchor_sinr_p50")) or -99) - (_num(base.get("anchor_sinr_p50")) or -99) >= 3)


def run_verdict(rows, rat="nr", run=None, lock=None):
    """One lock-test run -> a verdict dict (pure: the rows exactly as /lock-tests returns them). The keeper's own ranking rules
    are applied to THIS run's rows (its baseline against its candidates), so a run can be validated on its own, independent of
    older results: a step is `ranked` only when it stayed on its cell (on-target >= 80 %) and is not `unstable`; `not measured`
    (0 min) and `off target` are listed separately. `lock` = the live lock cell for the rat; verdict keep | apply | no-lock | no-result."""
    runs = [r for r in rows if r.get("rat", "lte") == rat and r.get("run")]
    run = run or (max(r["run"] for r in runs) if runs else "")
    steps = [r for r in runs if r["run"] == run and ";" not in (r.get("lock") or "")]
    latest = {}
    for r in steps: latest[r["lock"]] = r                    # one row per step; a repeated lock keeps the last
    base = latest.get("none")
    if base and _num(base.get("call_ok_pct")) is None: base = None      # a not-measured baseline is no baseline
    out = {"rat": rat, "run": run, "baseline": None, "steps": [], "best": None, "gain": False, "verdict": "no-result", "why": "",
           "night": any(r.get("mode") == "night" for r in steps)}
    ranked = []
    for spec, r in latest.items():
        if spec == "none": continue
        if str(r.get("unstable")) == "1": status = "unstable"
        elif not _num(r.get("minutes")): status = "not measured"
        elif (_num(r.get("on_target_pct")) or 0) < 80: status = "off target"
        else: status = "ranked"; ranked.append(r)
        out["steps"].append({"lock": spec, "status": status, "minutes": r.get("minutes"), "on_target_pct": r.get("on_target_pct"),
                             "call_ok_pct": r.get("call_ok_pct"), "loss_pct": r.get("loss_pct"), "lat_p95": r.get("lat_p95"),
                             "jitter_ms": r.get("jitter_ms"), "sinr": r.get("nr_sinr_p50"), "rsrp": r.get("nr_rsrp_p50"),
                             "pin": r.get("pin") or "", "antennas": r.get("antennas"), "note": r.get("note")})
    if base: out["baseline"] = {k: base.get(k) for k in ("minutes", "on_target_pct", "call_ok_pct", "loss_pct", "lat_p95",
                                                         "jitter_ms", "nr_sinr_p50", "nr_rsrp_p50", "antennas", "note")}
    if not run: out["why"] = "no lock-test run found for rat=%s" % rat
    elif base is None: out["why"] = "the run's baseline was not measured"
    elif not ranked: out["why"] = "no candidate stayed on its cell (ranked)"
    else:
        best = max(ranked, key=quality_values if rat == "nr" else
                   (lambda r: (_num(r.get("call_ok_pct")) or 0, -(_num(r.get("no5g_min")) or 0),
                               (_num(r.get("anchor_sinr_p50")) or -20) + (_num(r.get("nr_sinr_p50")) or -20))))
        gain = clearly_better(quality_values(best), quality_values(base)) if rat == "nr" else lte_gain(best, base)
        out.update(best=best.get("lock"), gain=gain,
                   verdict=("no-lock" if not gain else
                            ("recommend" if out["night"] else ("keep" if best.get("lock") == lock else "apply"))))
    return out


def verdict_text(v, lock=None, auto=""):
    """Human lines for a verdict (the JSON output is for the loop)."""
    lines = ["run %s (%s, %d step%s) | live lock %s (AUTO=%s)" % (v["run"] or "?", v["rat"], len(v["steps"]),
             "" if len(v["steps"]) == 1 else "s", lock or "-", auto or "?")]
    b = v["baseline"]
    if b:
        lines.append("  baseline none: %s min, call %s %%, loss %s, p95 %s, jitter %s, SINR %s, RSRP %s | %s" % (
            b["minutes"], b["call_ok_pct"], b["loss_pct"], b["lat_p95"], b["jitter_ms"], b["nr_sinr_p50"], b["nr_rsrp_p50"],
            b["antennas"] or "-"))
    for s in v["steps"]:
        lines.append("  %s: %s, %s min, on %s %%, call %s %%, loss %s, p95 %s, jitter %s, SINR %s | %s%s%s" % (
            s["lock"], s["status"], s["minutes"], s["on_target_pct"], s["call_ok_pct"], s["loss_pct"], s["lat_p95"],
            s["jitter_ms"], s["sinr"], s["antennas"] or "-", (" | " + s["note"][:120]) if s["note"] else "",
            (" | known under %s, %s min" % (s["known_anchor"], s["known_min"])) if s.get("known_anchor") else ""))
    if v["verdict"] == "no-result":
        lines.append("  -> NO USABLE RESULT: " + v["why"])
    elif v["best"]:
        if v["verdict"] == "recommend": lines.append("  -> night screen (unloaded): %s beats the baseline - run an aligned daytime "
                                                     "test for it (a night winner is never auto-applied)" % v["best"])
        elif v["verdict"] == "keep": lines.append("  -> %s clearly beats the baseline; it is your lock: keep" % v["best"])
        elif v["verdict"] == "apply": lines.append("  -> %s clearly beats the baseline; not your lock%s" % (
            v["best"], " - the keeper should have auto-applied it (AUTO=on)" if auto == "on" else " - review to apply"))
        else: lines.append("  -> nothing clearly beats the baseline - no lock needed%s" % (
            " (remove the lock: POST /%s-lock?cell=none)" % ("nr" if v["rat"] == "nr" else "lte") if lock else ""))
    return lines


def validate(url=URL, rat="nr", run=None, wait=False, timeout=3600, expect_lock=None, as_json=False):
    """Read-only: validate one lock-test run (the newest, or `--run`). `--wait` first waits for a running test to finish
    (poll every 15 s, up to `timeout` s). Exit: 0 = a usable verdict, 2 = no usable result, 3 = --expect-lock mismatch."""
    if wait:
        t0 = time.monotonic(); last = None
        while time.monotonic() - t0 < timeout:
            lt = _get(url, "/lock-tests")
            if not lt.get("running"):
                break
            now = lt.get("phase") or lt.get("running")
            if now != last: print("waiting for the running test: %s (%s)" % (lt.get("running"), now)); last = now
            time.sleep(15)
        else:
            raise SystemExit("validate --wait timed out after %d s (the test is still running)" % timeout)
    rows = _get(url, "/lock-tests?rat=" + rat).get("results") or []
    st = _get(url, "/status"); c = st.get("config") or {}
    lock = ((c.get("nr_lock") or {}) if rat == "nr" else (c.get("lte_lock") or {})).get("cell") or ""
    auto = c.get("auto_actions") or ""
    v = run_verdict(rows, rat, run, lock)
    v["lock"], v["auto"] = lock, auto
    if rat == "nr":                      # an NSA 5G cell appears only under some LTE anchors: say which one served it
        try: cells = {c.get("cell"): c for c in (_get(url, "/cells").get("nr") or [])}
        except Exception: cells = {}
        pins = {r.get("lock"): (r.get("pin") or "") for r in rows if r.get("run") == v["run"]}
        for s in v["steps"]:
            cell = "%s/%s" % tuple(s["lock"].split("@")[::-1]) if "@" in s["lock"] else ""
            c = cells.get(cell) or {}
            best = c.get("best_anchor") or ""
            pin = pins.get(s["lock"]) or ""
            pinned = "%s/%s" % tuple(pin.split("@")[::-1]) if "@" in pin else ""
            if best and best != pinned:
                a = next((x for x in (c.get("anchors") or []) if x.get("anchor") == best), {})
                s["known_anchor"], s["known_min"] = best, a.get("min")
    if as_json: print(json.dumps(v, indent=1))
    else: print("\n".join(verdict_text(v, lock, auto)))
    if expect_lock is not None and (lock or "") != expect_lock:
        print("EXPECTED lock %s, live lock %s" % (expect_lock, lock or "-")); return 3
    return 0 if v["verdict"] != "no-result" else 2


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
    sub.add_parser("review", help="static scan for the defect classes of docs/REVIEW-2026-11-03.md (read-only)")
    p = sub.add_parser("deploy"); p.add_argument("--skip-tests", action="store_true"); p.add_argument("--force", action="store_true")
    p.add_argument("--no-rollback", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("--version"); p.add_argument("--no-contract", action="store_true")
    p = sub.add_parser("validate", help="validate one lock-test run (read-only): baseline vs candidates on the keeper's policy")
    p.add_argument("--rat", default="nr", choices=("lte", "nr")); p.add_argument("--run", help="the run id (default: the newest)")
    p.add_argument("--wait", action="store_true", help="wait for a running test to finish first"); p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--expect-lock", help="exit 3 unless the live lock for --rat is this cell"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("rollback"); p.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "check": check(BASE, a.skip_tests)
    elif a.cmd == "status": status(a.url)
    elif a.cmd == "logs":
        print("\n".join(_ssh(a.host, '/log print where message~"%s"' % CONTAINER).splitlines()[-a.n:]))
    elif a.cmd == "backup":
        bdir, man = backup(BASE, a.host, a.dst, a.url); print(bdir, man.get("version"), len(man["files"]))
    elif a.cmd == "review":
        hits = review(BASE)
        for h in hits: print(h)
        print("review: OK" if not hits else "review: %d finding(s)" % len(hits))
        sys.exit(1 if hits else 0)
    elif a.cmd == "deploy": deploy(BASE, a.host, a.dst, a.url, a.skip_tests, a.force, not a.no_rollback)
    elif a.cmd == "verify":
        problems, warnings, _ = verify(BASE, a.url, a.version or local_version(BASE), contract=not a.no_contract)
        for w in warnings: print("warning:", w)
        if problems:
            for x in problems: print("-", x)
            sys.exit(1)
        print("OK %s live, contract matches" % (a.version or local_version(BASE)))
    elif a.cmd == "rollback": rollback_dir(a.dir, a.host, a.dst, a.url)
    elif a.cmd == "validate": sys.exit(validate(a.url, a.rat, a.run, a.wait, a.timeout, a.expect_lock, a.json))


if __name__ == "__main__":
    main()
