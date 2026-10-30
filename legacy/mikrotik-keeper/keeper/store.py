"""keeper storage: SQLite (Python standard library, nothing to install) in <DATA>/keeper.db.

One table rec(device, kind, time, data): every record is a JSON object, so a new field never needs a migration or a new file
(the old CSVs split into .v1/.v2 files on every column change). kinds: samples, events, lock_tests, antenna_sweeps, ui:<list>.
device: the uplink modem the record belongs to (DEVICE env: zte | mikrotik). Shared kinds have the same fields on every device,
so reports compare them; a device-only kind starts with its prefix (zte_..., mikrotik_...).
CSV files from older versions are imported once on first open and left in place (delete them yourself when happy).
Housekeeping once a day (low-end router: the database must never fill disk1): samples older than KEEP_DAYS (90) go, the app's
lists keep their newest UI_KEEP (20) saves, and while the data is over DB_MAX_MB (150) the oldest day of samples goes. Deleted space is
reused, so the file stops growing; it only shrinks with prune(vacuum=True) (POST /db/prune?vacuum=1). info() = GET /db.
Export for your own analysis: GET /export?kind=samples&from=2026-10-28&to=2026-10-29 (CSV)."""
import csv, datetime, io, json, os, re, shutil, sqlite3, threading, time

DEVICE = os.environ.get("DEVICE", "zte")

KEEP_DAYS = float(os.environ.get("KEEP_DAYS", "90"))
DB_MAX_MB = float(os.environ.get("DB_MAX_MB", "150"))
UI_KEEP = 20
KINDS = ("samples", "events", "lock_tests", "antenna_sweeps")
_db, _pruned, _lock = {}, {}, threading.RLock()


def db(d):
    """The SQLite connection of data dir `d` (created / migrated once, then cached)."""
    d = os.path.abspath(d or ".")
    with _lock:
        if d not in _db:
            os.makedirs(d, exist_ok=True)
            c = sqlite3.connect(os.path.join(d, "keeper.db"), check_same_thread=False, isolation_level=None)
            c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA journal_size_limit=8000000")     # the -wal file shrinks back to 8 MB
            c.execute("CREATE TABLE IF NOT EXISTS rec (device TEXT, kind TEXT, time TEXT, data TEXT)")
            c.execute("CREATE INDEX IF NOT EXISTS rec_dkt ON rec (device, kind, time)")
            c.execute("CREATE TABLE IF NOT EXISTS imported (name TEXT PRIMARY KEY)")
            _import(c, d)
            _db[d] = c
        return _db[d]


def _import(c, d):
    """one time: CSV files of older versions (samples.csv, samples.csv.1, samples.csv.v2, events.csv ...) -> rec (all from the ZTE)"""
    for name in sorted(os.listdir(d)):
        m = re.fullmatch(r"(samples|events|lock_tests|antenna_sweeps)\.csv(\.\d+|\.v\d+)?", name)
        if not m or c.execute("SELECT 1 FROM imported WHERE name = ?", (name,)).fetchone(): continue
        with open(os.path.join(d, name), newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("time")]
        c.execute("BEGIN")
        c.executemany("INSERT INTO rec VALUES ('zte', ?, ?, ?)", [(m.group(1), r["time"], json.dumps(r)) for r in rows])
        c.execute("INSERT INTO imported VALUES (?)", (name,))
        c.execute("COMMIT")


def add(d, kind, row, device=None):
    """Insert one record of `kind` for `device` (default: the active DEVICE); runs the daily housekeeping."""
    with _lock:
        c = db(d)
        c.execute("INSERT INTO rec VALUES (?, ?, ?, ?)", (device or DEVICE, kind, str(row.get("time", "")), json.dumps(row)))
        if kind == "samples" and time.time() - _pruned.get(c, 0) > 86400:
            _pruned[c] = time.time(); housekeeping(d)


def ago(days):
    """Timestamp string `days` ago (local time, the stored format)."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))


def used_mb(c):
    """MB of the database in use (pages minus free pages)."""
    pc, fl, ps = (c.execute("PRAGMA " + p).fetchone()[0] for p in ("page_count", "freelist_count", "page_size"))
    return (pc - fl) * ps / 1e6


def housekeeping(d):
    """daily: retention, app-list saves, size cap. Returns rows deleted."""
    with _lock:
        c = db(d); n = c.execute("DELETE FROM rec WHERE kind = 'samples' AND time < ?", (ago(KEEP_DAYS),)).rowcount
        for (k,) in c.execute("SELECT DISTINCT kind FROM rec WHERE kind LIKE 'ui:%'").fetchall():
            n += c.execute("DELETE FROM rec WHERE kind = ? AND rowid NOT IN (SELECT rowid FROM rec WHERE kind = ? ORDER BY rowid DESC LIMIT ?)",
                           (k, k, UI_KEEP)).rowcount
        for _ in range(400):                       # over the cap: the oldest day of samples, again until under (ponytail: day steps)
            first = c.execute("SELECT min(time) FROM rec WHERE kind = 'samples'").fetchone()[0]
            if used_mb(c) <= DB_MAX_MB or not first or first >= ago(1): break
            day = str(datetime.date.fromisoformat(first[:10]) + datetime.timedelta(days=1))     # not +86400 s: DST days are 23 / 25 h
            n += c.execute("DELETE FROM rec WHERE kind = 'samples' AND time < ?", (day,)).rowcount
        return n


def prune(d, days, kind="samples", device="*", vacuum=False, csv_files=False):
    """delete records of one kind (samples | events | lock_tests | antenna_sweeps | ui) older than `days` (>= 1) days;
    vacuum: give the space back to disk1 (needs free space ~ the database size); csv_files: delete the old CSVs already imported"""
    if not (kind in KINDS or kind == "ui") or float(days) < 1: raise ValueError("kind=%s|ui, days >= 1" % "|".join(KINDS))
    with _lock:
        c = db(d); w = "kind LIKE 'ui:%'" if kind == "ui" else "kind = ?"
        a = (() if kind == "ui" else (kind,)) + (ago(float(days)),) + (() if device == "*" else (device,))
        out = {"deleted": c.execute("DELETE FROM rec WHERE %s AND time < ?%s" % (w, "" if device == "*" else " AND device = ?"), a).rowcount}
        if csv_files:
            names = [x[0] for x in c.execute("SELECT name FROM imported").fetchall() if os.path.exists(os.path.join(os.path.abspath(d), x[0]))]
            for x in names: os.remove(os.path.join(os.path.abspath(d), x))
            out["csv_deleted"] = names
        if vacuum:
            size = sum(os.path.getsize(p) for p in _files(d))
            if shutil.disk_usage(os.path.abspath(d)).free < size * 1.2: raise ValueError("vacuum needs ~%d MB free on the disk" % (size * 1.2 / 1e6))
            c.execute("VACUUM"); c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out["db"] = info(d)["db_mb"]
        return out


def _files(d):
    p = os.path.join(os.path.abspath(d), "keeper.db")
    return [x for x in (p, p + "-wal") if os.path.exists(x)]


def info(d):
    """GET /db: size, records per device / kind (oldest, newest), free disk, old CSVs safe to delete"""
    with _lock:
        c = db(d); dd = os.path.abspath(d)
        per = [{"device": r[0], "kind": r[1], "records": r[2], "oldest": r[3], "newest": r[4]} for r in
               c.execute("SELECT device, kind, count(*), min(time), max(time) FROM rec GROUP BY device, kind ORDER BY device, kind").fetchall()]
        csvs = {x[0]: round(os.path.getsize(os.path.join(dd, x[0])) / 1e6, 1) for x in c.execute("SELECT name FROM imported").fetchall()
                if os.path.exists(os.path.join(dd, x[0]))}
        return {"db_mb": round(sum(os.path.getsize(p) for p in _files(d)) / 1e6, 1), "used_mb": round(used_mb(c), 1), "cap_mb": DB_MAX_MB,
                "keep_days": KEEP_DAYS, "disk_free_mb": round(shutil.disk_usage(dd).free / 1e6), "records": per,
                "imported_csv_mb": csvs, "note": "imported_csv_mb: already in keeper.db - safe to delete (POST /db/prune?...&csv=1 or by hand)"}


def rows(d, kind, frm="", to="\uffff", last=None, device=None):
    """records of one kind of one device (default: the active DEVICE; "*" = all), oldest first; last=N -> only the newest N"""
    dev = device or DEVICE; w = "kind = ? AND time >= ? AND time <= ?" + ("" if dev == "*" else " AND device = ?")
    a = (kind, frm, to) + (() if dev == "*" else (dev,))
    with _lock:
        c = db(d)
        if last:
            q = c.execute("SELECT data FROM rec WHERE %s ORDER BY time DESC, rowid DESC LIMIT ?" % w, a + (int(last),)).fetchall()[::-1]
        else:
            q = c.execute("SELECT data FROM rec WHERE %s ORDER BY time, rowid" % w, a).fetchall()
    return [json.loads(x[0]) for x in q]


def to_csv(recs):
    """records -> CSV text, columns in order of first appearance"""
    cols = []
    for r in recs:
        cols += [k for k in r if k not in cols]
    out = io.StringIO(); w = csv.DictWriter(out, cols, extrasaction="ignore")
    w.writeheader(); w.writerows(recs)
    return out.getvalue()


if __name__ == "__main__":          # self-check
    import tempfile
    t = tempfile.mkdtemp()
    open(os.path.join(t, "events.csv"), "w").write("time,event\n%s,old\n" % ago(2))          # relative times: not date-bound
    add(t, "events", {"time": ago(0.1), "event": "new", "extra": 1})
    assert [r["event"] for r in rows(t, "events")] == ["old", "new"] and rows(t, "events", last=1)[0]["extra"] == 1
    _db.clear(); assert len(rows(t, "events")) == 2                  # reopen: imported only once
    assert to_csv(rows(t, "events")).splitlines()[0] == "time,event,extra"
    add(t, "events", {"time": ago(0.05), "event": "atl"}, device="mikrotik")
    assert len(rows(t, "events")) == 2 and [r["event"] for r in rows(t, "events", device="mikrotik")] == ["atl"] and len(rows(t, "events", device="*")) == 3
    for i in range(5): add(t, "ui:zte_positions", {"time": "2026-10-28 12:0%d:00" % i, "data": i})
    _pruned[db(t)] = time.time()                                    # no automatic daily run inside the test
    add(t, "samples", {"time": "2020-01-01 00:00:00"}); add(t, "samples", {"time": ago(0)})
    UI_KEEP = 2; assert housekeeping(t) == 4 and [r["data"] for r in rows(t, "ui:zte_positions")] == [3, 4]      # 1 old sample + 3 old saves
    DB_MAX_MB = 0; add(t, "samples", {"time": ago(3)}); assert housekeeping(t) == 1 and len(rows(t, "samples")) == 1   # cap: today kept
    i = info(t); assert i["imported_csv_mb"] == {"events.csv": 0.0} and {"device": "mikrotik", "kind": "events"} .items() <= i["records"][0].items()
    r = prune(t, 1, "events", device="zte", vacuum=True, csv_files=True)
    assert r["deleted"] == 1 and r["csv_deleted"] == ["events.csv"] and [x["event"] for x in rows(t, "events", device="*")] == ["new", "atl"]
    try: prune(t, 0); raise SystemExit("days 0 accepted")
    except ValueError: pass
    print("OK store")
