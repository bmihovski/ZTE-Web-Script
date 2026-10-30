# MikroTik driver against the real dumps (test/fixtures/mikrotik-dump*.csv, IDs masked): shared fields, rates, scans, AT lock commands
import csv, os, sys
here = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(here, "..", ".."))
os.environ.update(DEVICE="mikrotik", DATA="/tmp/zte-keeper-test-mtdrv"); os.makedirs("/tmp/zte-keeper-test-mtdrv", exist_ok=True)
sys.path.insert(0, ROOT)
from keeper.devices import mikrotik as mk

load = lambda n: {r[0]: r[1] for r in csv.reader(open(os.path.join(here, "..", "fixtures", n))) if len(r) > 1}
CI, DIAG = mk.CI, mk.DIAG
vals = load("mikrotik-dump.csv"); r = mk.row(vals, 1000.0)                                  # idle: 5G released, LTE only
assert r["lte_cell"] == "3350/202" and r["lte_site"] == "1265/63" and r["lte_ca"] == "B7/3350/202:-99/8@20", r
assert r["nr_cell"] == "no5G" and r["nr_sinr"] == "" and r["lte_sinr"] == "8" and r["mikrotik_mimo_rsrp"] == "-100,-99", r
vals[CI + "Stats.BytesReceived"] = str(int(vals[CI + "Stats.BytesReceived"]) + 60000)
assert mk.row(vals, 1060.0)["down_bps"] == "1000"                                           # 60000 bytes in 60 s
r = mk.row(load("mikrotik-dump-5g.csv"))                                                     # traffic: 5G leg up, 4 LTE carriers
assert r["nr_cell"] == "n78/786" and r["nr_sinr"] == "0" and r["mikrotik_5g_bw"] == "100" and r["tech"] == "NR5G", r
mk.NR_ARFCN["78:100"] = "636576"; assert mk.row(load("mikrotik-dump-5g.csv"))["nr_cell"] == "636576/786"; mk.NR_ARFCN.clear()   # NR_ARFCN set
v2 = dict(vals); v2[CI + "Stats.BytesReceived"] = "100"; v2[CI + "Stats.BytesSent"] = "100"
assert mk.row(v2, 1120.0)["down_bps"] == "" and mk.row(v2, 1180.0)["down_bps"] == "0"       # counters reset (interface restart): no fake rate
assert r["lte_ca"] == "B7/3350/202:-98/0@20 B1/100/202:-91/2@20 B3/1550/202:-93/0@20 B20/6400/323:-87/@10" and r["nr_ca"] == "", r
r = mk.row(load("mikrotik-dump-lock.csv"))                                                   # AT+QNWLOCK="common/4g",1,6400,112
assert r["lte_cell"] == "6400/112" and r["lte_site"] == "1589/183" and r["nr_cell"] == "no5G", r
sc = {DIAG + "Result.1.PhysicalCellId": "786", DIAG + "Result.1.Band": "48", DIAG + "Result.1.Fcn": "636576", DIAG + "Result.1.RSRP": "-82",
      DIAG + "Result.2.PhysicalCellId": "202", DIAG + "Result.2.Band": "1", DIAG + "Result.2.Fcn": "100", DIAG + "Result.2.RSRP": "-87"}
assert mk.neighbours(sc) == ("100/202:-87", "636576/786:-82") and mk.neighbours(sc) == ("", "")           # real names; reported once
assert mk.row(dict(load("mikrotik-dump-5g.csv"), **sc))["nr_cell"] == "636576/786"                         # 5G channel from the scan
assert mk.lock_cmds("434@1550", "569@636576") == ['AT+QNWLOCK="common/4g",1,1550,434', 'AT+QNWLOCK="common/5g",569,636576,30,78']
assert mk.lock_cmds("none", None) == ['AT+QNWLOCK="common/4g",0']
assert mk.nr_on({"nr_cell": "n78/569"}, "569@636576") and not mk.nr_on({"nr_cell": "636576/534"}, "569@636576")
print("OK mikrotik_driver", mk.status_line(vals))
