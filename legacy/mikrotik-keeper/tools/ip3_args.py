#!/usr/bin/env python3
"""Dry-run of the keeper's iperf3 settings against what the ZTE says it accepts - no test, no write, nothing on the modem changes.

The keeper's TR-143 probe (once per keeper start, ~10 min in) stores the modem's GetParameterNames answer with each name's
Writable flag; this tool fetches it from /status and checks every value the keeper would write for a download and an upload run:
    write    - writable on the modem
    skip     - optional and read-only: the keeper leaves it out (no fault)
    FAULT    - required but read-only or unknown to the modem: the test would fail with 9003/9005/9008

    python3 tools/ip3_args.py                              # keeper.lan:7557
    python3 tools/ip3_args.py --keeper 192.168.6.250:7557 --server 169.150.252.2:5201
    python3 tools/ip3_args.py --status status.json         # offline: a saved GET /status
Exit code 1 when anything would fault. Stdlib only.
"""
import argparse, json, os, sys, tempfile, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keeper", default="keeper.lan:7557"); ap.add_argument("--status", help="a saved GET /status JSON instead of the keeper")
    ap.add_argument("--server", default=None, help="iperf3 host:port (default: the first of CAPTEST_IPERF)")
    a = ap.parse_args(argv)
    if a.status: st = json.load(open(a.status))
    else: st = json.load(urllib.request.urlopen("http://%s/status" % a.keeper, timeout=15))
    tr = (st.get("config") or {}).get("tr143") or {}
    w = tr.get("writable")
    if not w:
        print("no Writable flags in /status config.tr143 yet (%s) - the probe runs once, ~10 min after a keeper start (2026-11-14c or newer)"
              % (tr.get("time") or "never probed")); return 2
    os.environ.update(DATA=tempfile.mkdtemp(prefix="ip3args-"), NIGHT_SCREEN="off")    # the keeper's own code builds the values, nothing persists
    sys.path.insert(0, ROOT)
    from keeper import captest as c
    srv = a.server or c.IPERFS[0]; bad = 0
    print("modem probe %s: %d names, %d under %s" % (tr.get("time"), len(w), sum(n.startswith(c.IP3) for n in w), c.IP3))
    for title, conn, rev in (("download", c.CONN, True), ("upload", c.UP_CONN, False)):
        print("\n%s (%s, %d streams):" % (title, srv, conn))
        for n, typ, val in c.iperf_params(srv, c.ISECS, conn, rev) + [(c.IP3 + "Enable", "xsd:boolean", "1")]:
            short = n[len(c.IP3):]; ok = w.get(n)
            if ok: verdict = "write"
            elif ok is False and short in c.IP3_OPTIONAL: verdict = "skip (optional, read-only)"
            else: verdict = "FAULT (%s)" % ("read-only" if ok is False else "not on the modem"); bad += 1
            print("  %-18s %-14s %-12s %s" % (short, typ, val, verdict))
    print("\n%s" % ("OK - the modem accepts every value the keeper writes" if not bad else "%d value(s) would fault - fix captest.iperf_params" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
