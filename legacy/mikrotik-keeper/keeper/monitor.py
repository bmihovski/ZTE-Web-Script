"""Per-minute pipeline: one read of the modem -> one sample -> store, switch events, brain, the driver's jobs, alerts, decisions.

The driver maps its own values to the shared sample fields (`SAMPLE_COLS`), so analysis, reports and the guard never see the
device model; fields only one device has are its `EXTRA`.
"""
from keeper import alert, brain, devices
from keeper.common import event, log, record, stamp

import time

SAMPLE_COLS = ["time", "uptime_s", "tech", "nr_cell", "nr_rsrp", "nr_rsrq", "nr_sinr", "nr_rssi", "nr_ssb_rsrp", "nr_ssb_sinr",
               "lte_cell", "lte_site", "lte_rsrp", "lte_rsrq", "lte_sinr", "lte_rssi", "lte_ca", "lte_neighbours", "rrc",
               "down_bps", "up_bps", "net_delay", "temps", "fota", "nr_ca"] + brain.LAT_COLS
"""Shared sample fields. `nr_cell` = `ARFCN/PCI` or `no5G`; `lte_cell` = `EARFCN/PCI` or '' (no service);
`lte_ca` = `B7/3350/340:-91/1@20 ...` (band/EARFCN/PCI:RSRP/SINR@MHz, anchor first); rates in bytes/s."""
st = {"prev_cell": None, "prev_sinr": "", "prev_lte": None, "prev_uptime": None}


def sample(vals):
    """One TR-069 read -> `finish()` with the driver's row."""
    finish(dict({"time": stamp()}, **devices.current().row(vals)))


def finish(row):
    """Store the sample, log 5G switches with their explanation, run the driver's jobs (lock guard ...), alerts, auto reboot."""
    dev = devices.current()
    nr = row["nr_cell"]
    if nr == "no5G": row["nr_sinr"] = ""
    row.update(brain.latency_row())
    record("samples", SAMPLE_COLS + dev.EXTRA, row)
    switched = st["prev_cell"] is not None and nr != st["prev_cell"]
    why = brain.explain_switch(row) if switched else ""
    brain.add(row)
    brain.after_boot_cell(row, event)
    if switched:
        event({"event": "switch", "from": st["prev_cell"], "to": nr, "nr_sinr_before": st["prev_sinr"], "uptime_s": row["uptime_s"],
               "lte_cell": row["lte_cell"], "lte_sinr": row["lte_sinr"], "why": why})
        log("5G %s -> %s: %s" % (st["prev_cell"], nr, why))
    st["prev_cell"] = nr
    if row["nr_sinr"]: st["prev_sinr"] = row["nr_sinr"]
    lte = row.get("lte_cell")                                  # a cell / anchor / reboot change makes the SQM delay baseline
    try: upn = float(row.get("uptime_s"))                      # meaningless (a new path has a different round trip): rebase it
    except (TypeError, ValueError): upn = None
    if (switched or (st["prev_lte"] is not None and lte != st["prev_lte"]) or
            (st["prev_uptime"] is not None and upn is not None and upn < st["prev_uptime"])):
        ctx = getattr(brain, "ctx", None)
        if ctx is not None: ctx["path_change_at"] = time.time()
    st["prev_lte"] = lte
    if upn is not None: st["prev_uptime"] = upn
    for job in dev.minute_jobs(): job(row)
    alert.check_calls(dev.NAME, list(brain.HIST)[-15:])
    why = brain.decide(row, event)
    if why: log("%s: %s" % ("reboot queued" if brain.ctx["pending"] else "would reboot (dryrun)", why))
