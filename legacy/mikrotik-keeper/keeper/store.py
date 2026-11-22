"""keeper storage: SQLite (Python standard library, nothing to install) in <DATA>/keeper.db.

Minute samples have typed cells and metrics with a compact JSON payload for driver fields. Other records and legacy samples
use rec(device, kind, time, data). Kinds: samples, events, lock_tests, antenna_sweeps, band_scans, ui:<list>.
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
KINDS = ("samples", "events", "lock_tests", "antenna_sweeps", "band_scans")
SAMPLE_INTS = ("uptime_s", "call_ok", "down_bps", "up_bps")
SAMPLE_REALS = ("lte_rsrp", "lte_rsrq", "lte_sinr", "nr_rsrp", "nr_rsrq", "nr_sinr",
                "lat_p50", "lat_p95", "lat_max", "jitter_ms", "loss_pct", "sqm_rx", "sqm_tx")
CELL_COLS = ("lte_channel", "lte_pci", "nr_channel", "nr_pci")
SAMPLE_COLS = CELL_COLS + SAMPLE_INTS + SAMPLE_REALS
SAMPLE_FIELDS = ", ".join(SAMPLE_COLS)
SAMPLE_TABLE = ("CREATE TABLE IF NOT EXISTS sample (device TEXT NOT NULL, time TEXT NOT NULL, "
                + ", ".join([*(k + " INTEGER" for k in CELL_COLS + SAMPLE_INTS), *(k + " REAL" for k in SAMPLE_REALS)])
                + ", data TEXT NOT NULL)")
SAMPLE_NULLS = ", ".join("NULL" for _ in SAMPLE_COLS)
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
            c.execute(SAMPLE_TABLE)
            c.execute("CREATE INDEX IF NOT EXISTS sample_dt ON sample (device, time)")
            c.execute("CREATE INDEX IF NOT EXISTS sample_t ON sample (time)")
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


def encode(row):
    return json.dumps(row, separators=(",", ":"))


def _sample_values(row):
    """Extract canonical cell pairs and lossless numeric strings, leaving other fields in the driver payload."""
    extra = dict(row)
    values = []
    for rat in ("lte", "nr"):
        key = rat + "_cell"
        cell = extra.get(key)
        m = re.fullmatch(r"(0|[1-9][0-9]{0,6})/(0|[1-9][0-9]{0,3})", cell) if isinstance(cell, str) else None
        values.extend(map(int, m.groups()) if m else (None, None))
        if m: del extra[key]
    for k, cast in [(k, int) for k in SAMPLE_INTS] + [(k, float) for k in SAMPLE_REALS]:
        v = extra.get(k)
        typed = None
        if isinstance(v, str) and re.fullmatch(r"-?[0-9]{1,15}(?:\.[0-9]+)?", v):
            try: typed = cast(v)
            except ValueError: pass
        if typed is not None and str(typed) == v: del extra[k]
        else: typed = None
        values.append(typed)
    return (*values, encode(extra))


def _sample_row(raw):
    data, channel_lte, pci_lte, channel_nr, pci_nr, *metrics = raw
    row = json.loads(data)
    if channel_lte is not None: row["lte_cell"] = "%d/%d" % (channel_lte, pci_lte)
    if channel_nr is not None: row["nr_cell"] = "%d/%d" % (channel_nr, pci_nr)
    row.update((k, str(v)) for k, v in zip(SAMPLE_COLS[len(CELL_COLS):], metrics) if v is not None)
    return row


def add(d, kind, row, device=None):
    """Insert one record of `kind` for `device` (default: the active DEVICE); runs the daily housekeeping."""
    with _lock:
        c = db(d)
        t = str(row.get("time", ""))
        if kind == "samples":
            placeholders = ", ".join("?" for _ in range(len(SAMPLE_COLS) + 3))
            c.execute("INSERT INTO sample VALUES (" + placeholders + ")", (device or DEVICE, t, *_sample_values(row)))
        else:
            c.execute("INSERT INTO rec VALUES (?, ?, ?, ?)", (device or DEVICE, kind, t, encode(row)))
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
        c = db(d)
        def remove(before):
            return (c.execute("DELETE FROM sample WHERE time < ?", (before,)).rowcount
                    + c.execute("DELETE FROM rec WHERE kind = 'samples' AND time < ?", (before,)).rowcount)
        n = remove(ago(KEEP_DAYS))
        for (k,) in c.execute("SELECT DISTINCT kind FROM rec WHERE kind LIKE 'ui:%'").fetchall():
            n += c.execute("DELETE FROM rec WHERE kind = ? AND rowid NOT IN (SELECT rowid FROM rec WHERE kind = ? ORDER BY rowid DESC LIMIT ?)",
                           (k, k, UI_KEEP)).rowcount
        for _ in range(400):                       # over the cap: the oldest day of samples, again until under (ponytail: day steps)
            if used_mb(c) <= DB_MAX_MB: break
            first = c.execute("SELECT min(t) FROM (SELECT min(time) AS t FROM sample "
                              "UNION ALL SELECT min(time) FROM rec WHERE kind = 'samples')").fetchone()[0]
            if not first or first >= ago(1): break
            day = str(datetime.date.fromisoformat(first[:10]) + datetime.timedelta(days=1))     # not +86400 s: DST days are 23 / 25 h
            n += remove(day)
        return n


def reset_measurements(d):
    """One-shot maintenance while the keeper is starting. Keep user lists and compact the retained rows."""
    with _lock:
        c = db(d)
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("DELETE FROM sample")
            c.execute("DELETE FROM rec WHERE substr(kind, 1, 3) != 'ui:'")
            c.executemany("UPDATE rec SET data = ? WHERE rowid = ?",
                          [(encode(json.loads(payload)), ident) for ident, payload in c.execute("SELECT rowid,data FROM rec")])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        # 'imported' continues to prevent archived CSVs from being reloaded on a restart.
        c.execute("VACUUM")
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def prune(d, days, kind="samples", device="*", vacuum=False, csv_files=False):
    """delete records of one kind (samples | events | lock_tests | antenna_sweeps | ui) older than `days` (>= 1) days;
    vacuum: give the space back to disk1 (needs free space ~ the database size); csv_files: delete the old CSVs already imported"""
    if not (kind in KINDS or kind == "ui") or float(days) < 1: raise ValueError("kind=%s|ui, days >= 1" % "|".join(KINDS))
    with _lock:
        c = db(d); w = "kind LIKE 'ui:%'" if kind == "ui" else "kind = ?"
        a = (() if kind == "ui" else (kind,)) + (ago(float(days)),) + (() if device == "*" else (device,))
        out = {"deleted": c.execute("DELETE FROM rec WHERE %s AND time < ?%s" % (w, "" if device == "*" else " AND device = ?"), a).rowcount}
        if kind == "samples":
            out["deleted"] += c.execute("DELETE FROM sample WHERE time < ?" + ("" if device == "*" else " AND device = ?"), a[1:]).rowcount
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
               c.execute("SELECT device, kind, sum(n), min(oldest), max(newest) FROM ("
                         "SELECT device, kind, count(*) AS n, min(time) AS oldest, max(time) AS newest FROM rec GROUP BY device, kind "
                         "UNION ALL SELECT device, 'samples', count(*), min(time), max(time) FROM sample GROUP BY device"
                         ") GROUP BY device, kind ORDER BY device, kind").fetchall()]
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
        if kind == "samples":
            sw = w.removeprefix("kind = ? AND ")
            query = ("SELECT data, " + SAMPLE_FIELDS + " FROM ("
                     "SELECT data, " + SAMPLE_FIELDS + ", time, 1 AS source, rowid AS id FROM sample WHERE " + sw
                     + " UNION ALL SELECT data, " + SAMPLE_NULLS + ", time, 0, rowid FROM rec WHERE " + w + ")")
            args = a[1:] + a
            if last:
                raw = c.execute(query + " ORDER BY time DESC, source DESC, id DESC LIMIT ?", args + (int(last),)).fetchall()[::-1]
            else:
                raw = c.execute(query + " ORDER BY time, source, id", args).fetchall()
            return [_sample_row(r) for r in raw]
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
