"""Discard measurements once and preserve operator state across restarts."""
import json
import os
from pathlib import Path
import sys
import tempfile

data = Path(tempfile.mkdtemp())
os.environ["DATA"] = str(data)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from keeper import brain, common, store

t = common.stamp()
row = {"time": t, "lte_cell": "3350/307", "nr_cell": "no5G", "lat_p95": "0.0", "loss_pct": "0.0", "lte_neighbours": "100/340:-90"}
store.add(data, "samples", row)
assert store.rows(data, "samples") == [row]
db = store.db(data)
assert db.execute("SELECT lte_channel,lte_pci,lat_p95 FROM sample").fetchone() == (3350, 307, 0)
db.execute("INSERT INTO rec VALUES ('zte','samples',?,?)", (t, json.dumps(row)))
assert len(store.rows(data, "samples")) == 2 and store.rows(data, "samples", last=1) == [row]
store.add(data, "samples", row, device="mikrotik")
store.add(data, "band_scans", {"time": t})
store.add(data, "ui:zte_positions", {"time": t, "data": ["home"]}, device="app")
(data / "cells.json").write_text('{"lte":{"3350/307":{}}}')
(data / "sites.json").write_text('{"sites":[]}')
(data / "cell-ids.json").write_text('{"lte:3350:307": 123}')
(data / "lock_tests.csv").write_text("time,lock\n%s,307@3350\n" % t)
common.state({"lte_lock": {"cell": "307@3350", "state": "active"}, "antennas": {"ant5": "4"},
              "morning_trial": {"stale": True}, "morning_dead": {"307@3350": [1]}, "captest_hist": [{"down": 100}]})
marker = data / ".reset-measurements"
marker.touch()
assert common.reset_measurements()
assert not marker.exists() and not (data / "cells.json").exists()
assert (data / "sites.json").is_file() and (data / "cell-ids.json").is_file()
brain.load_registry(data)
assert brain.REG["ids"] == {"lte:3350:307": 123}, brain.REG["ids"]
assert json.loads((data / "cell-ids.json").read_text()) == brain.REG["ids"]
s = common.state()
assert s["lte_lock"]["cell"] == "307@3350" and s["antennas"]["ant5"] == "4" and s["captest_hist"] == [{"down": 100}]
assert not s.get("morning_trial") and not s.get("morning_dead")
assert not store.rows(data, "samples", device="*") and not store.rows(data, "band_scans") and not store.rows(data, "lock_tests")
assert store.rows(data, "ui:zte_positions", device="app")[0]["data"] == ["home"]
store.add(data, "samples", row)
assert not common.reset_measurements() and store.rows(data, "samples") == [row]
assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
print("OK test_measurement_reset")
