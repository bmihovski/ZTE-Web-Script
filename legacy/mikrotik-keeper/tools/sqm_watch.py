#!/usr/bin/env python3
"""Log the shaper live while you load the line (a big download, an upload): one CSV row every --every seconds from
/status sqm.now - the 10-s decisions the per-minute samples average away (cuts, the climb past the ceiling, the latency).
  python3 tools/sqm_watch.py                    # -> test-logs/sqm-YYYYmmdd-HHMM.csv, Ctrl-C to stop
  python3 tools/sqm_watch.py --every 2 --url http://keeper.lan:7557
Stdlib only; never writes to the keeper."""
import argparse, csv, json, os, sys, time, urllib.request

COLS = ["t", "sqm_time", "down_rate", "down_limit", "down_ceiling", "up_rate", "up_limit", "up_ceiling", "delay_ms",
        "cpu_max", "useless", "down_why", "up_why"]


def row(st, t):
    n = ((st.get("config") or {}).get("sqm") or {}).get("now") or {}
    g = lambda k, d: (n.get(k) or {}).get(d)
    return [t, n.get("time"), g("rate", "down"), g("limit", "down"), g("ceiling", "down"), g("rate", "up"), g("limit", "up"),
            g("ceiling", "up"), n.get("delay_ms"), max(n.get("cpu") or [0]), n.get("useless_cut_streaks"), g("why", "down"), g("why", "up")]


def main(argv=None):
    p = argparse.ArgumentParser(); p.add_argument("--url", default=os.environ.get("KEEPER_URL", "http://keeper.lan:7557"))
    p.add_argument("--every", type=float, default=5); p.add_argument("--out")
    a = p.parse_args(argv)
    out = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test-logs", time.strftime("sqm-%Y%m%d-%H%M.csv"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); print("logging to", out, "- Ctrl-C to stop", flush=True)
        try:
            while True:
                t = time.strftime("%H:%M:%S")
                try:
                    with urllib.request.urlopen(a.url + "/status", timeout=8) as r: r_ = row(json.load(r), t)
                except Exception as e: r_ = [t] + [""] * (len(COLS) - 2) + ["error %s" % type(e).__name__]
                w.writerow(r_); fh.flush(); print(" ".join(str(x) for x in r_[:9]), flush=True)
                time.sleep(a.every)     # the sampling period itself, not a wait for a condition
        except KeyboardInterrupt: print("\nsaved", out)


if __name__ == "__main__":
    assert row({"config": {"sqm": {"now": {"time": "x", "rate": {"down": 1.0}, "limit": {"down": 2.0}, "cpu": [3, 9]}}}}, "t")[:5] == ["t", "x", 1.0, 2.0, None]
    sys.exit(main())
