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


def metric(v):
    """'' and None both mean missing -> None; anything else is a float."""
    if v is None or v == "":
        return None
    return float(v)


def q(leg):
    """Quality tuple for a leg, copied from keeper/brain.py quality_values (signal excluded)."""
    call_ok = leg.get("call_ok")
    call = 100 if call_ok == 1 else (metric(call_ok) or 0)
    loss = metric(leg.get("loss_pct"))
    p95 = metric(leg.get("lat_p95"))
    jitter = metric(leg.get("jitter_ms"))
    return (call, -999 if loss is None else -loss, -9999 if p95 is None else -p95, -9999 if jitter is None else -jitter)


def clearly_better(a, b):
    """First differing metric beyond its margin decides: call 5, loss 1.0, p95 10, jitter 10 - else False."""
    (ac, al, ap, aj), (bc, bl, bp, bj) = a, b
    if abs(ac - bc) >= 5:
        return ac > bc
    if abs(al - bl) >= 1.0:
        return al > bl
    if abs(ap - bp) >= 10:
        return ap > bp
    if abs(aj - bj) >= 10:
        return aj > bj
    return False


def eligible_legs(result):
    """Legs with band, role first/second, ok and has5g; per band keep the first leg of each role in list order."""
    by_band = {}
    for l in legs_of(result):
        if not l.get("band"):
            continue
        if l.get("role") not in ("first", "second"):
            continue
        if not (l.get("ok") and l.get("has5g")):
            continue
        b = str(l["band"])
        slot = by_band.setdefault(b, {})
        if l["role"] not in slot:
            slot[l["role"]] = l
    return by_band


def check_r5(result):
    problems = []
    by_band = eligible_legs(result)
    primaries, backups = [], []
    for b in sorted(by_band):
        slot = by_band[b]
        primary = slot.get("first") or slot.get("second")
        primaries.append(primary)
        if "first" in slot and "second" in slot:
            backups.append(slot["second"])
    a = max(primaries, key=q) if primaries else None
    b = None
    if a is not None:
        cands = [l for l in backups if l.get("lte") != a.get("lte")]
        b = max(cands, key=q) if cands else None
    a_lte = (a.get("lte") if a else None) or "none"
    b_lte = (b.get("lte") if b else None) or "none"
    got_a = ((result.get("a") or {}).get("lte")) or "none"
    got_b = ((result.get("b") or {}).get("lte")) or "none"
    if got_a != a_lte:
        problems.append(f"a.lte {got_a!r} != recomputed {a_lte!r}")
    if got_b != b_lte:
        problems.append(f"b.lte {got_b!r} != recomputed {b_lte!r}")
    return ("FAIL" if problems else "PASS", "; ".join(problems) or "A/B match the recomputed pick")


def check_r6(doc, result):
    problems = []
    by_band = eligible_legs(result)
    primaries, backups = [], []
    for b in sorted(by_band):
        slot = by_band[b]
        primary = slot.get("first") or slot.get("second")
        primaries.append(primary)
        if "first" in slot and "second" in slot:
            backups.append(slot["second"])
    a = max(primaries, key=q) if primaries else None
    b = None
    if a is not None:
        cands = [l for l in backups if l.get("lte") != a.get("lte")]
        b = max(cands, key=q) if cands else None
    no_lock = [l for l in legs_of(result) if l.get("leg") == "no lock"]
    no_lock = no_lock[0] if no_lock else None
    cand = [l for l in ([no_lock, a, b] if no_lock else [a, b]) if l and l.get("ok") and l.get("has5g")]
    if not cand:
        restored = "none"
    else:
        best = max(cand, key=q)
        if best is no_lock or (no_lock and no_lock.get("ok") and not clearly_better(q(best), q(no_lock))):
            restored = "none"
        else:
            restored = best.get("lte")
    got = ((result.get("restored") or {}).get("lte")) or "none"
    if got != restored:
        problems.append(f"restored.lte {got!r} != recomputed {restored!r}")
    status = doc.get("status") or {}
    st_time = str(status.get("time") or "")
    r_time = str(result.get("time") or "")
    if not problems and (st_time > r_time) and status.get("nr_cell"):
        return "PASS", f"restored {restored!r} matches; status after the run with 5G"
    if problems:
        return "FAIL", "; ".join(problems)
    return "WARN", "status taken before the run ended, or no 5G now"


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


ROUTER_RE = re.compile(r" ?zte-keeper: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$")


def parse_router_log(path):
    lines = []
    with open(path) as f:
        for line in f:
            m = ROUTER_RE.search(line)
            if m:
                lines.append((m.group(1), m.group(2)))
    return lines


def check_r7(doc, result, router_log):
    if not router_log:
        return "SKIP", {}, "no --router-log given"
    lines = parse_router_log(router_log)
    started = str(result.get("started") or "")
    ended = str(result.get("time") or "")
    in_win = [(t, m) for t, m in lines if started <= t <= ended]
    reboots = [t for t, m in in_win if "reboot accepted" in m]
    boots = [t for t, m in in_win if "boot inform" in m and "1 BOOT" in m]
    locked = sum(1 for l in legs_of(result) if l.get("lte") not in (None, "", "none"))
    expected = 2 + locked
    unanswered = []
    for i, t in enumerate(reboots):
        nxt = reboots[i + 1] if i + 1 < len(reboots) else ended
        if not any(t <= bt < nxt for bt in boots):
            unanswered.append(t)
    failed_leg = any(not l.get("ok") for l in legs_of(result))
    problems = []
    if len(reboots) > expected:
        problems.append(f"{len(reboots)} reboots, expected at most {expected}")
    if unanswered and not failed_leg:
        problems.append(f"reboots with no boot before the next reboot: {unanswered}")
    router = {"reboots": len(reboots), "boots": len(boots), "expected_max": expected, "locked": locked, "unanswered": unanswered}
    if problems:
        return "FAIL", router, "; ".join(problems)
    if unanswered:
        return "PASS", router, f"{len(unanswered)} reboot(s) without a boot, explained by a failed leg"
    if locked > 0 and len(reboots) < locked:
        return "WARN", router, f"router log covers {len(reboots)} of {locked} lock reboots"
    return "PASS", router, f"{len(reboots)} reboots, {len(boots)} boots, within the expected {expected}"


def verify_report(report_path, audit_path):
    with open(audit_path) as f:
        audit_text = f.read()
    audit = json.loads(audit_text)
    with open(report_path) as f:
        report = f.read()
    problems = []
    for c in audit.get("checks") or []:
        if c.get("status") in ("FAIL", "WARN") and c["id"] not in report:
            problems.append(f"check {c['id']} is {c['status']} but not named in the report")
    for l in audit.get("legs") or []:
        label = l.get("leg")
        if label and label not in report:
            problems.append(f"leg {label!r} not named in the report")
    for num in re.findall(r"\d+(?:\.\d+)?", report):
        if num not in audit_text:
            problems.append(f"number {num} in the report does not occur in the audit JSON")
    return problems


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
    s, d = check_r5(result)
    add("R5", s, d, "A/B pick")
    s, d = check_r6(doc, result)
    add("R6", s, d, "Restore")
    s, router, d = check_r7(doc, result, router_log)
    add("R7", s, d, "Router log")
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
        "router": router,
    }


def main(argv):
    args = argv[1:]
    if not args:
        print("usage: morning_audit.py MORNING.json [--router-log FILE] [--candidates N] [--out AUDIT.json]")
        print("       morning_audit.py --verify-report REPORT.md AUDIT.json")
        return 2
    if args[0] == "--verify-report":
        if len(args) != 3:
            print("usage: morning_audit.py --verify-report REPORT.md AUDIT.json")
            return 2
        problems = verify_report(args[1], args[2])
        for p in problems:
            print(p)
        return 1 if problems else 0
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
