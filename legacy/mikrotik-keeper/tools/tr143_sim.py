#!/usr/bin/env python3
"""TR-143 simulation against an Ookla server: what the MC889 will do when the keeper asks it for a capacity test.

Download: N parallel GETs of /speedtest/random4000x4000.jpg; the result counts only the window when ALL connections were running
(TR-143 TestBytesReceivedUnderFullLoading / PeriodOfFullLoading) - no TCP warm-up, no tail.
Upload: the same payload with PUT (what TR-143 specifies) and POST (what Ookla's web client sends) to the server's upload.php,
so we know which one the server accepts before the modem tries it.

Run it from a LAN machine (the traffic goes through the RB450 and its shaper, so speeds are the shaped ones - the point is which
requests the server accepts and that the counters work; the modem's own test bypasses the router):
    python3 tools/tr143_sim.py                          # speedtest1.yettel.bg:8080, 4 connections, 25 MB up
    python3 tools/tr143_sim.py --conn 1 --up-mb 5       # quick
    python3 tools/tr143_sim.py --host speedtest2.yettel.bg:8080 --no-down
Stdlib only. Exit code 0 when the download worked and at least one upload method was accepted.
"""
import argparse, http.client, os, sys, threading, time

FILE = "/speedtest/random4000x4000.jpg"
UPLOAD = "/speedtest/upload.php"


def get(host, path, out, i, chunk=65536):
    """One download connection: records (time, total bytes) after every read."""
    marks, n = [], 0
    try:
        h, p = host.split(":") if ":" in host else (host, "80")
        c = http.client.HTTPConnection(h, int(p), timeout=30)
        c.request("GET", "%s?x=%d.%d" % (path, time.time() * 1000, i), headers={"User-Agent": "tr143-sim", "Cache-Control": "no-cache"})
        r = c.getresponse(); marks.append((time.monotonic(), 0))
        if r.status != 200: out[i] = {"status": r.status, "marks": marks, "error": r.reason}; return
        while True:
            b = r.read(chunk)
            if not b: break
            n += len(b); marks.append((time.monotonic(), n))
        out[i] = {"status": 200, "marks": marks, "bytes": n}
    except Exception as e:
        out[i] = {"status": None, "marks": marks, "error": repr(e)}


def bytes_at(marks, t):
    """Bytes this connection had received at time t (last mark before t)."""
    b = 0
    for m, n in marks:
        if m > t: break
        b = n
    return b


def download(host, conn):
    out = [None] * conn
    ts = [threading.Thread(target=get, args=(host, FILE, out, i)) for i in range(conn)]
    t0 = time.monotonic(); [t.start() for t in ts]; [t.join() for t in ts]
    ok = [o for o in out if o and o["status"] == 200 and len(o["marks"]) > 2]
    if len(ok) < conn:
        return {"ok": False, "why": "; ".join("conn %d: %s %s" % (i, (o or {}).get("status"), (o or {}).get("error", "")) for i, o in enumerate(out) if o not in ok)}
    start = max(o["marks"][1][0] for o in ok)              # all connections delivering ...
    end = min(o["marks"][-1][0] for o in ok)               # ... until the first one finished
    total = sum(o["bytes"] for o in ok); elapsed = max(o["marks"][-1][0] for o in ok) - t0
    res = {"ok": True, "bytes": total, "elapsed_s": round(elapsed, 2), "avg_mbit": round(total * 8 / elapsed / 1e6, 1)}
    if end > start:
        full = sum(bytes_at(o["marks"], end) - bytes_at(o["marks"], start) for o in ok)
        res.update(full_load_s=round(end - start, 2), full_load_mbit=round(full * 8 / (end - start) / 1e6, 1))
    return res


def upload(host, method, size):
    """One upload of `size` random bytes with `method`; status, server answer, Mbit/s."""
    h, p = host.split(":") if ":" in host else (host, "80")
    body = os.urandom(min(size, 1 << 20)); sent = 0
    try:
        c = http.client.HTTPConnection(h, int(p), timeout=60)
        c.putrequest(method, "%s?x=%d" % (UPLOAD, time.time() * 1000))
        c.putheader("Content-Length", str(size)); c.putheader("Content-Type", "application/octet-stream"); c.putheader("User-Agent", "tr143-sim")
        c.endheaders(); t0 = time.monotonic()
        while sent < size:
            part = body[: min(len(body), size - sent)]; c.send(part); sent += len(part)
        r = c.getresponse(); ans = r.read(200).decode("latin-1", "replace").strip(); t = time.monotonic() - t0
        return {"method": method, "status": r.status, "answer": ans[:80], "sent": sent, "s": round(t, 2), "mbit": round(sent * 8 / t / 1e6, 1),
                "accepted": r.status == 200}
    except Exception as e:
        return {"method": method, "status": None, "answer": repr(e)[:80], "sent": sent, "accepted": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="speedtest1.yettel.bg:8080"); ap.add_argument("--conn", type=int, default=4)
    ap.add_argument("--up-mb", type=float, default=25); ap.add_argument("--no-down", action="store_true"); ap.add_argument("--no-up", action="store_true")
    a = ap.parse_args(argv)
    good_down = good_up = True
    if not a.no_down:
        d = download(a.host, a.conn); good_down = d["ok"]
        print("download %s x%d: %s" % (a.host, a.conn, d))
    if not a.no_up:
        res = [upload(a.host, m, int(a.up_mb * 1e6)) for m in ("PUT", "POST")]
        for r in res: print("upload   %-4s %s" % (r["method"], r))
        good_up = any(r["accepted"] for r in res)
        print("-> TR-143 upload (PUT): %s" % ("accepted - the modem can use upload.php" if res[0]["accepted"] else
              "REFUSED - the modem's upload needs another target (iperf3)" + (" ; POST works" if res[1]["accepted"] else "")))
    return 0 if good_down and good_up else 1


if __name__ == "__main__":
    sys.exit(main())
