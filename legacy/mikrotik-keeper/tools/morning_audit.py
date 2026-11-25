#!/usr/bin/env python3
"""Audit one keeper morning run from a GET /morning JSON dump. Stdlib only; never imports keeper."""
import json
import re
import sys


def spec(cell):
    """'earfcn/pci' -> 'pci@earfcn'; a value with '@' is returned unchanged."""
    if not cell:
        return ""
    if "@" in str(cell):
        return str(cell)
    earfcn, _, pci = str(cell).partition("/")
    if not pci:
        return str(cell)
    return f"{pci}@{earfcn}"


def missing(v):
    return v is None or v == ""


def legs_of(result):
    return result.get("legs") or []


def discoveries_of(result):
    return result.get("discoveries") or []


def check_r1(doc, result):
    problems = []
    if result.get("complete") is not True:
        problems.append("complete is not true")
    if result.get("trigger") == "scheduled":
        started = str(result.get("started") or "")
        m = re.search(r"(\d{2}):(\d{2}):(\d{2})$", started)
        if not m:
            problems.append(f"started unparseable: {started!r}")
        else:
            hh, mm = int(m.group(1)), int(m.group(2))
            if not (8 <= hh < 9):
                problems.append(f"started {started} outside [08:00, 09:00)")
    if result.get("version") != doc.get("live_version"):
        problems.append(f"version {result.get('version')!r} != live_version {doc.get('live_version')!r}")
    return ("FAIL" if problems else "PASS", "; ".join(problems) or "run complete, on schedule, version matches")


def check_r2(result):
    no_lock = [l for l in legs_of(result) if l.get("leg") == "no lock"]
    if len(no_lock) != 1:
        return "FAIL", f"expected exactly one 'no lock' leg, found {len(no_lock)}"
    l = no_lock[0]
    if l.get("ok") or not missing(l.get("error")):
        return "PASS", "baseline leg present"
    return "FAIL", "no lock leg is not ok and has no error"


def check_r3(result, n):
    problems = []
    for disc in discoveries_of(result):
        band = str(disc.get("band"))
        serving_spec = spec(disc.get("serving"))
        cands = [spec(c) for c in (disc.get("candidates") or [])]
        rejected = disc.get("rejected") or {}
        blegs = [l for l in legs_of(result) if str(l.get("band")) == band]
        firsts = [l for l in blegs if l.get("role") == "first"]
        seconds = [l for l in blegs if l.get("role") == "second"]
        # list order: optional first leg, then backups
        ordered = []
        if firsts:
            ordered.append(firsts[0])
            ordered.extend(seconds)
        else:
            ordered = seconds
        if firsts:
            f = firsts[0]
            if f.get("lte") != serving_spec:
                problems.append(f"B{band}: first leg lte {f.get('lte')!r} != serving spec {serving_spec!r}")
        else:
            if serving_spec not in rejected:
                problems.append(f"B{band}: no first leg and serving spec {serving_spec!r} not in rejected")
        if len(seconds) > n:
            problems.append(f"B{band}: {len(seconds)} backups, at most {n}")
        for i, l in enumerate(seconds):
            if i < len(cands) and l.get("lte") != cands[i]:
                problems.append(f"B{band}: backup {i + 1} lte {l.get('lte')!r} != candidate {cands[i]!r}")
            if i >= len(cands):
                problems.append(f"B{band}: backup {i + 1} has no matching candidate")
        for i, l in enumerate(seconds[:-1]):
            if l.get("ok") and l.get("has5g"):
                problems.append(f"B{band}: backup {i + 1} ok with 5G but a later backup ran")
    return ("FAIL" if problems else "PASS", "; ".join(problems) or "band order and backups follow the plan")


def check_r4(result):
    problems = []
    for l in legs_of(result):
        if l.get("ok") and l.get("has5g") and missing(l.get("observed_nr")):
            problems.append(f"{l.get('leg')}: ok with 5G but no observed_nr")
        if not l.get("ok") and missing(l.get("error")):
            problems.append(f"{l.get('leg')}: not ok but no error")
    return ("FAIL" if problems else "PASS", "; ".join(problems) or "5G gate and errors consistent")


def check_r8(result):
    problems = []
    for l in legs_of(result):
        if not l.get("ok"):
            continue
        for field in ("call_ok", "lat_p95", "jitter_ms"):
            if missing(l.get(field)):
                problems.append(f"{l.get('leg')}: ok but {field} missing")
        if missing(l.get("down_mbps")) and missing(l.get("https_error")):
            problems.append(f"{l.get('leg')}: ok but no down_mbps and no https_error")
    return ("FAIL" if problems else "PASS", "; ".join(problems) or "every ok leg has its measurements")


def audit(doc, router_log=None, n=2):
    result = (doc.get("morning") or {}).get("result") or {}
    checks = []

    def add(cid, status, detail, rule):
        checks.append({"id": cid, "status": status, "detail": detail, "rule": rule})

    s, d = check_r1(doc, result)
    add("R1", s, d, "Morning run")
    s, d = check_r2(result)
    add("R2", s, d, "Baseline leg")
    s, d = check_r3(result, n)
    add("R3", s, d, "Morning cell check")
    s, d = check_r4(result)
    add("R4", s, d, "5G gate")
    # R5/R6 added in a later step
    s, d = check_r8(result)
    add("R8", s, d, "Data measurements")

    legs = []
    for l in legs_of(result):
        legs.append({
            "leg": l.get("leg"), "band": l.get("band"), "role": l.get("role"),
            "lte": l.get("lte"), "ok": l.get("ok"), "has5g": l.get("has5g"),
            "down_mbps": l.get("down_mbps"), "call_ok": l.get("call_ok"),
            "lat_p95": l.get("lat_p95"), "jitter_ms": l.get("jitter_ms"),
            "error": l.get("error"),
        })
    return {
        "source": None,
        "result_time": result.get("time"),
        "checks": checks,
        "legs": legs,
        "router": {},
    }


def main(argv):
    args = argv[1:]
    if not args:
        print("usage: morning_audit.py MORNING.json [--router-log FILE] [--candidates N] [--out AUDIT.json]")
        return 2
    source = args[0]
    router_log = None
    n = 2
    out = None
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--router-log":
            router_log = args[i + 1]; i += 2
        elif a == "--candidates":
            n = int(args[i + 1]); i += 2
        elif a == "--out":
            out = args[i + 1]; i += 2
        else:
            print(f"unknown argument: {a}")
            return 2
    with open(source) as f:
        doc = json.load(f)
    result = audit(doc, router_log=router_log, n=n)
    result["source"] = source
    text = json.dumps(result, indent=2)
    if out:
        with open(out, "w") as f:
            f.write(text + "\n")
    for c in result["checks"]:
        print(f"{c['id']} {c['status']} {c['detail']}")
    return 1 if any(c["status"] == "FAIL" for c in result["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
