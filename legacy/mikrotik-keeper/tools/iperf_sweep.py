#!/usr/bin/env python3
"""Rank public iperf3 servers for the capacity test: the modem's own iperf3 (on the MC889 - it never crosses the router's queue)
against each server in turn, through the keeper's DIAGNOSTIC /captest (any option = never a ceiling). One at a time (the modem
runs one test); a busy server answers "no speed". Writes test-logs/iperf-sweep-YYYYmmdd-HHMM.csv and prints a ranking.
  python3 tools/iperf_sweep.py                    # the default list, 1 round, 4 streams, 10 s
  python3 tools/iperf_sweep.py --rounds 2 --conn 8 --servers 37.19.203.1:5201,lg.serverscamp.com:5202
Stdlib only. ~1 min per server (the result arrives with the modem's informs)."""
import argparse, csv, json, os, re, statistics, sys, time, urllib.error, urllib.parse, urllib.request

SERVERS = ["37.19.203.1:5201",            # Sofia (DataPacket) - the primary
           "lg.serverscamp.com:5202", "lg.serverscamp.com:5203",   # Bucharest (ServersCamp, 5201-5205)
           "185.102.217.170:5201",        # Bucharest (DataPacket)
           "speedtest1.sox.rs:9206",      # Belgrade (SOX, 9206-9240)
           "169.150.252.2:5201",          # Athens
           "169.150.242.129:5201",        # Zagreb
           "185.180.12.40:5201", "iperf3-vie-at.alwyzon.net:5202",   # Vienna
           "156.146.52.1:5201", "iperf.pendc.com:5202"]               # Istanbul, Bursa
COLS = ["round", "time", "server", "conn", "down_mbps", "modem_cpu_max", "how"]


def get(url, path):
    with urllib.request.urlopen(url + path, timeout=15) as r: return json.load(r)


def one(url, server, conn, secs, ceiling_s=180):
    """POST a diagnostic download test, poll until it is no longer running (ceiling `ceiling_s`) -> (down Mbit/s or None, how)."""
    q = urllib.parse.urlencode({"mode": "iperf3", "server": server, "conn": conn, "secs": secs, "reverse": 1})
    t0 = time.time()
    while True:                                              # the modem runs one test: wait while another one runs
        try:
            with urllib.request.urlopen(urllib.request.Request(url + "/captest?" + q, method="POST", data=b""), timeout=15) as r:
                if json.load(r).get("started"): break
            why = "busy"
        except urllib.error.HTTPError as e: why = "HTTP %s" % e.code
        if time.time() - t0 > ceiling_s: return None, "not started: %s" % why
        time.sleep(3)                                        # poll period while the keeper is busy, not a fixed wait for a result
    t1 = time.time(); seen_running = False
    while time.time() - t1 < ceiling_s:
        c = get(url, "/captest")
        if c.get("running"): seen_running = True
        rec = ((c.get("last") or {}).get("rec") or {})
        if (seen_running or time.time() - t1 > 20) and not c.get("running") and (rec.get("diag") or {}).get("server") == server:
            return rec.get("down"), rec.get("down_how", "")
        time.sleep(3)
    return None, "no result within %d s" % ceiling_s


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("--url", default=os.environ.get("KEEPER_URL", "http://keeper.lan:7557"))
    p.add_argument("--servers", default=",".join(SERVERS)); p.add_argument("--rounds", type=int, default=1)
    p.add_argument("--conn", type=int, default=4); p.add_argument("--secs", type=int, default=10)
    a = p.parse_args(argv)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test-logs", time.strftime("iperf-sweep-%Y%m%d-%H%M.csv"))
    os.makedirs(os.path.dirname(out), exist_ok=True); res = {}
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS)
        for rnd in range(1, a.rounds + 1):
            for s in [x for x in a.servers.split(",") if x]:
                d, how = one(a.url, s, a.conn, a.secs)
                m = re.search(r"modem CPU max (\d+) %", how or ""); cpu = int(m.group(1)) if m else None    # keeper >= 2026-11-20i
                w.writerow([rnd, time.strftime("%H:%M:%S"), s, a.conn, d, cpu, how]); fh.flush()
                print("%s round %d %-32s %s%s" % (time.strftime("%H:%M:%S"), rnd, s, d if d is not None else "-- " + how[:60],
                                                  "  (modem CPU max %d %%)" % cpu if cpu is not None else ""), flush=True)
                res.setdefault(s, []).append(d)
    print("\nranking (median of the rounds, busy = no result):")
    for s, xs in sorted(res.items(), key=lambda kv: -(statistics.median([x for x in kv[1] if x is not None]) if any(x is not None for x in kv[1]) else -1)):
        ok = [x for x in xs if x is not None]
        print("  %-32s %s  (%d of %d answered)" % (s, "%.0f Mbit/s" % statistics.median(ok) if ok else "busy", len(ok), len(xs)))
    print("saved", out)


if __name__ == "__main__":
    sys.exit(main())
