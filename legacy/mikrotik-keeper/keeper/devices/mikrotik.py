"""Driver for the MikroTik ATL 5G (ATLGM, RouterOS 7.25, Quectel RG520F-EU), DEVICE=mikrotik. Interface: see `keeper.devices`.

TR-181 `Device.Cellular` + `X_MIKROTIK_` vendor values (names from the dumps of 2026-10-28, `test/fixtures/mikrotik-dump*.csv`).
`row()` maps one read to the shared sample fields; values only the ATL has are `mikrotik_*`.
Cell-monitor scans (`X_MIKROTIK_CellDiagnostics`, every SCAN_MIN) give the 4G + 5G neighbours and the 5G ARFCN (CarrierInfo5G has none).
Locks: `X_MIKROTIK_LteCellLock` is refused (9003), so the keeper sends a RouterOS script by TR-069 Download (`.alter` = run as a script)
with AT commands (tested 2026-10-28, both hold, both lost on a reboot - the guard sets them again after a boot). A lock costs no CPE
reboot (the ATL restarts its LTE interface instead), so lock tests here never wait for a BOOT inform (`LOCKS_REBOOT = False`):
  LTE `AT+QNWLOCK="common/4g",1,EARFCN,PCI` (`,0` removes) - 5G `AT+QNWLOCK="common/5g",PCI,ARFCN,SCS,band`.
Without the keeper (e.g. at the summer house) the ATL just runs unlocked; its TR-069 client keeps trying the ACS, harmless.
Env: SCAN_MIN (10, 0 = off), NR_ARFCN (off; `78:100=636576,78:20=629988` names 5G cells by Yettel's blocks before a scan).
"""
import os, re, threading, time

from keeper import acs, common, guard
from keeper.common import E, event, log, stamp, state

NAME, MAKER = "MikroTik", "MikroTik"
CAN = {"scan", "at", "lock_test"}
LOCKS_REBOOT = False               # the ATL restarts its LTE interface instead of rebooting: lock tests never wait for a BOOT inform
CLEANUP_WAIT_S = float(E("LOCKTEST_CLEANUP_S", "20"))
CI = "Device.Cellular.Interface.1."
DIAG = "Device.Cellular.X_MIKROTIK_CellDiagnostics."          # = /interface lte cell-monitor
READ = [CI, DIAG, "Device.DeviceInfo.UpTime", "Device.DeviceInfo.SoftwareVersion",
        "Device.ManagementServer.PeriodicInformInterval", "Device.ManagementServer.PeriodicInformEnable"]
EXTRA = ["nr_neighbours", "mikrotik_cqi", "mikrotik_mcs", "mikrotik_ri", "mikrotik_modulation", "mikrotik_mimo_rsrp", "mikrotik_5g_cqi", "mikrotik_5g_mcs",
         "mikrotik_5g_ri", "mikrotik_5g_modulation", "mikrotik_5g_band", "mikrotik_5g_bw", "mikrotik_link_downs"]
NONE = {"", "-255", "4294967295", "-2147483648", "-32768"}   # RouterOS "no value" (seen with no 5G leg)
SET_PARAMS = ("X_MIKROTIK_LteCellLock", "X_MIKROTIK_LteBands", "X_MIKROTIK_5GBands")     # writable, for POST /mikrotik/set experiments
SCAN_MIN = float(E("SCAN_MIN", "10"))
# OFF by default: the ATL's SIM roams between networks (2026-10-28: Yettel at 12:23, another network at 13:04), so a 100 MHz n78
# carrier is not always Yettel's 636576 - without NR_ARFCN (and before a scan) a 5G cell is named by band: n78/786
NR_ARFCN = dict(x.split("=") for x in os.environ.get("NR_ARFCN", "").split(",") if "=" in x)
RE_DIAG = r"Device\.Cellular\.X_MIKROTIK_CellDiagnostics\.Result\.(\d+)\.(\w+)$"
SCAN = [(DIAG + "Interface", "xsd:string", "Device.Cellular.Interface.1"), (DIAG + "Seconds", "xsd:unsignedInt", "10"),
        (DIAG + "DiagnosticsState", "xsd:string", "Requested")]
_prev = {}                                    # byte counters of the last read -> rates
_scan = {"sig": None, "nr_fcn": {}}           # last cell-monitor result seen; 5G PCI -> ARFCN measured by the scans
mt = {"download": None, "scripts": {}, "n": 0, "last_vals": {}, "results": [], "done": threading.Event(), "ok": None, "last_scan": 0.0,
      "status_logged": False}
common.register_busy("script download", lambda: mt["download"])


# ---------- reading ----------
def v(vals, k):
    """Value of `Device.Cellular.Interface.1.<k>`, '' for RouterOS placeholders."""
    x = vals.get(CI + k, "")
    return "" if x in NONE else x


def carriers(vals, prefix):
    """`X_MIKROTIK_CarrierInfo.N` / `X_MIKROTIK_CarrierInfo5G.N` -> [(band, fcn, pci, rsrp, sinr, bw)]."""
    out = []
    for i in range(1, 9):
        g = lambda f: v(vals, "%s.%d.%s" % (prefix, i, f))
        if not g("PhysicalCellId"): break
        sinr = g("SINR") if g("SINR") and -50 < int(g("SINR")) < 100 else ""          # 250 / 253 = "no value" on secondary carriers
        fcn = g("Fcn") or _scan["nr_fcn"].get(g("PhysicalCellId")) or NR_ARFCN.get("%s:%s" % (g("Band"), g("Bandwidth")), "n" + g("Band"))
        out.append((g("Band"), fcn, g("PhysicalCellId"), g("RSRP"), sinr, g("Bandwidth")))
    return out


def neighbours(vals):
    """Cell-monitor results -> ("earfcn/pci:rsrp ..." LTE, "arfcn/pci:rsrp ..." 5G). The results stay readable until the next scan,
    so one scan is reported once (else every minute would count the same cells again). 5G band shows as 48 (n48/n78/n77)."""
    res = {}
    for k, x in vals.items():
        m = re.match(RE_DIAG, k)
        if m: res.setdefault(int(m.group(1)), {})[m.group(2).lower()] = x
    sig = tuple(sorted((i, tuple(sorted(d.items()))) for i, d in res.items()))
    if not res or sig == _scan["sig"]: return "", ""
    _scan["sig"] = sig
    lte, nr = [], []
    for i in sorted(res):
        d = res[i]; pick = lambda *keys: next((d[k] for k in d if any(w in k for w in keys) and d[k] not in NONE), "")
        pci, ch, rsrp, band = pick("physicalcell", "cellid", "pci"), pick("fcn", "earfcn", "arfcn", "channel"), pick("rsrp"), pick("band")
        if not pci or not ch: continue
        is_nr = band.lower().startswith("n") or (ch.isdigit() and int(ch) > 65535)
        (nr if is_nr else lte).append("%s/%s:%s" % (ch, pci, rsrp))
        if is_nr: _scan["nr_fcn"][pci] = ch
    return " ".join(lte), " ".join(nr)


def row(vals, now=None):
    """One read -> the shared sample fields + mikrotik_*. Rates from the byte counters (a counter reset = no rate that minute)."""
    now = now or time.time()
    g = lambda k: v(vals, k)
    lte = carriers(vals, "X_MIKROTIK_CarrierInfo")
    nr = carriers(vals, "X_MIKROTIK_CarrierInfo5G")
    lte_nb, nr_nb = neighbours(vals)
    pci5 = g("X_MIKROTIK_5G_PhysicalCellId")
    nr_cell = "no5G"
    if pci5 and g("X_MIKROTIK_5G_RSRP"):
        main = next((c for c in nr if c[2] == pci5), None)
        nr_cell = "%s/%s" % (main[1] if main and main[1][:1] != "n" else _scan["nr_fcn"].get(pci5) or
                             NR_ARFCN.get("%s:%s" % (g("X_MIKROTIK_5G_Band"), g("X_MIKROTIK_5G_Bandwidth")), "n" + g("X_MIKROTIK_5G_Band")), pci5)
    cid = g("X_MIKROTIK_CellId")
    rx, tx = vals.get(CI + "Stats.BytesReceived", ""), vals.get(CI + "Stats.BytesSent", "")
    down = up = ""
    if _prev.get("t") and rx.isdigit() and tx.isdigit() and str(_prev.get("rx", "")).isdigit() and str(_prev.get("tx", "")).isdigit():
        dt = max(1.0, now - _prev["t"])
        def w(a, b):        # 32-bit wrap only near the top; any other drop = counters reset (LTE interface restarted) -> no rate
            a, b = int(a), int(b)
            return a - b if a >= b else (a + 2 ** 32 - b if b > 2 ** 31 else None)
        d, u = w(rx, _prev["rx"]), w(tx, _prev["tx"])
        if d is not None and u is not None: down, up = str(int(d / dt)), str(int(u / dt))
    _prev.update(rx=rx, tx=tx, t=now)
    on5 = nr_cell != "no5G"
    return {
        "uptime_s": vals.get("Device.DeviceInfo.UpTime", ""),
        "tech": "NR5G" if on5 else g("X_MIKROTIK_CurrentAccessTechnology"),
        "nr_cell": nr_cell, "nr_rsrp": g("X_MIKROTIK_5G_RSRP") if on5 else "",
        "nr_rsrq": g("X_MIKROTIK_5G_RSRQ") if on5 else "", "nr_sinr": g("X_MIKROTIK_5G_SINR") if on5 else "",
        "nr_rssi": "", "nr_ssb_rsrp": "", "nr_ssb_sinr": "",
        "lte_cell": "%s/%s" % (lte[0][1], lte[0][2]) if lte else "",
        "lte_site": "%d/%d" % (int(cid) >> 8, int(cid) & 0xFF) if cid.isdigit() and lte else "",
        "lte_rsrp": g("X_MIKROTIK_RSRP") if lte else "", "lte_rsrq": g("X_MIKROTIK_RSRQ") if lte else "",
        "lte_sinr": g("X_MIKROTIK_SINR") if lte else "", "lte_rssi": g("RSSI") if lte else "",
        "lte_ca": " ".join("B%s/%s/%s:%s/%s%s" % (b, f, p, r, s, "@" + w if w else "") for b, f, p, r, s, w in lte),
        "lte_neighbours": lte_nb, "nr_neighbours": nr_nb, "rrc": "", "down_bps": down, "up_bps": up, "net_delay": "", "temps": "", "fota": "",
        "nr_ca": " ".join("%s/%s:%s/%s%s" % (f, p, r, s, "@" + w if w else "") for b, f, p, r, s, w in nr if p != pci5),
        "mikrotik_cqi": g("X_MIKROTIK_CQI"), "mikrotik_mcs": g("X_MIKROTIK_MCS"), "mikrotik_ri": g("X_MIKROTIK_RI"),
        "mikrotik_modulation": g("X_MIKROTIK_Modulation"), "mikrotik_mimo_rsrp": vals.get(CI + "X_MIKROTIK_MimoRSRP", "").replace(",-140", ""),
        "mikrotik_5g_cqi": g("X_MIKROTIK_5G_CQI") if on5 else "", "mikrotik_5g_mcs": g("X_MIKROTIK_5G_MCS"),
        "mikrotik_5g_ri": g("X_MIKROTIK_5G_RI") if on5 else "", "mikrotik_5g_modulation": g("X_MIKROTIK_5G_Modulation"),
        "mikrotik_5g_band": g("X_MIKROTIK_5G_Band") if on5 else "", "mikrotik_5g_bw": g("X_MIKROTIK_5G_Bandwidth") if on5 else "",
        "mikrotik_link_downs": g("X_MIKROTIK_LinkDowns")}


def status_line(vals):
    """Start-up log line: RouterOS and modem firmware, inform interval."""
    return "MikroTik status: RouterOS %s, modem %s %s, inform every %ss" % (
        vals.get("Device.DeviceInfo.SoftwareVersion", "?"), vals.get(CI + "X_MIKROTIK_Model", "?"),
        vals.get(CI + "X_MIKROTIK_ExtRevision", "?"), vals.get("Device.ManagementServer.PeriodicInformInterval", "?"))


# ---------- locks: AT commands in a RouterOS script ----------
def lock_cmds(lte, nr):
    """The lock wish -> AT commands for the RG520F (`'pci@channel'`, 'none' = remove, None = leave as is)."""
    out = []
    if lte is not None:
        out.append('AT+QNWLOCK="common/4g",0' if lte == "none" else 'AT+QNWLOCK="common/4g",1,%s,%s' % tuple(reversed(lte.split("@"))))
    if nr is not None:
        if nr == "none": out.append('AT+QNWLOCK="common/5g",0')
        else:
            pci, arfcn = nr.split("@"); band, scs = guard.nr_band(arfcn)
            out.append('AT+QNWLOCK="common/5g",%s,%s,%d,%d' % (pci, arfcn, scs, band))
    return out


def script(cmd, restart):
    """Queue a RouterOS script with AT commands (only AT+QNWLOCK allowed) and optionally an LTE interface restart (the data
    session). Sent as a TR-069 Download with the next session; served at /mikrotik/script-N.alter. Returns N. ValueError otherwise."""
    cmds = [cmd] if isinstance(cmd, str) else list(cmd)
    if not cmds or not all(re.fullmatch(r'AT\+QNWLOCK=[A-Za-z0-9",/]+', c) for c in cmds): raise ValueError("only AT+QNWLOCK=... is allowed")
    lines = ['/interface lte at-chat [find] input="%s"' % c.replace('"', '\\"') for c in cmds]
    if restart: lines += [":delay 3s", "/interface lte disable [find]", ":delay 3s", "/interface lte enable [find]"]
    mt["n"] += 1; mt["scripts"][mt["n"]] = "\n".join(lines) + "\n"; mt["download"] = mt["n"]
    return mt["n"]


def locks_apply(lte=None, nr=None):
    """The guard's / a lock test's lock change: AT commands in one script + LTE interface restart; waits (4 min) until the ATL ran it.
    A lock test step sets both legs (the pinned LTE anchor + the 5G cell) in ONE script; `nr=None` leaves the 5G leg alone."""
    if lte is None and nr is None: return None
    mt["done"].clear(); mt["ok"] = None; script(lock_cmds(lte, nr), True)
    if not mt["done"].wait(240): raise RuntimeError("the ATL did not run the lock script within 4 min")
    if not mt["ok"]: raise RuntimeError("the ATL refused / failed the lock script")
    return "tr069 script"


def ready(timeout=0):
    """No web page to wait for: a lock-test step can measure as soon as the ATL informs."""
    return True


def lock_value(key):
    """The ATL doesn't show an AT lock over TR-069 (`LteCellLock` stays ''): unknown."""
    return None


def nr_on(row, cell):
    """Served by the locked 5G cell? Before a scan gave its ARFCN, the ATL names a 5G cell `n78/PCI`."""
    nr = row.get("nr_cell") or ""
    return nr in guard.lock_target(cell) or (nr.startswith("n") and nr.split("/")[-1] == cell.split("@")[0])


# ---------- scans + experiments ----------
def maybe_scan(row=None, now=None):
    """Minute job: start a cell-monitor scan every SCAN_MIN minutes when nothing else runs. True when started."""
    now = now or time.time()
    if SCAN_MIN <= 0 or now - mt["last_scan"] < SCAN_MIN * 60 or common.busy(): return False
    mt["last_scan"] = now
    threading.Thread(target=acs.tr069_set, args=(SCAN,), daemon=True).start()
    return True


def scan_now():
    """API: scan now (blocks until the ATL accepted the request)."""
    return acs.tr069_set(SCAN, timeout=150)


def set_param(param, value):
    """API experiment: SetParameterValues on one writable cellular value (SET_PARAMS)."""
    if param not in SET_PARAMS: raise ValueError("param=" + "|".join(SET_PARAMS))
    return acs.tr069_set([(CI + param, "xsd:string", value)], timeout=150)


# ---------- driver hooks ----------
def minute_jobs():
    """Sample jobs in order: lock guard, cell-monitor scan."""
    return [guard.lock_guard, maybe_scan]


def start_note():
    """Device part of the start log line."""
    return "the ZTE is left alone (no antennas, no web login)"


def on_start():
    """Keeper start: an interrupted lock test left its AT locks on the ATL (they survive a keeper restart) - clear them with one script."""
    t = state().get("lock_test")
    if not t: return
    def undo():
        time.sleep(CLEANUP_WAIT_S)
        try: locks_apply("none", "none"); state({"lock_test": None}); log("AT locks removed after the interrupted lock test")
        except Exception as e: log("removing the AT locks failed %r - they go away with the ATL's next reboot" % e)
    threading.Thread(target=undo, daemon=True).start()


def on_boot():
    """The RG520F forgets AT cell locks on a reboot: the guard sets them again."""
    guard.boot_lost()


def on_values(vals):
    """Keep the raw values for /mikrotik/raw; log the status line once."""
    mt["last_vals"] = {k[len(CI):] if k.startswith(CI) else k: x for k, x in vals.items()}
    if not mt["status_logged"]:
        mt["status_logged"] = True; log(status_line(vals))
    return []


def pending_rpc(host):
    """A queued script -> TR-069 Download body (FileType 3 + `.alter` URL = RouterOS runs it), else None."""
    if not mt["download"]: return None
    n, mt["download"] = mt["download"], None
    url = "http://%s/mikrotik/script-%d.alter" % (host, n)
    log("MikroTik: download %s: %s" % (url, mt["scripts"][n].strip().replace("\n", " | ")))
    return ('<cwmp:Download><CommandKey>keeper-%d</CommandKey><FileType>3 Vendor Configuration File</FileType><URL>%s</URL><Username></Username>'
            '<Password></Password><FileSize>%d</FileSize><TargetFileName></TargetFileName><DelaySeconds>0</DelaySeconds><SuccessURL></SuccessURL>'
            '<FailureURL></FailureURL></cwmp:Download>' % (n, acs.esc(url), len(mt["scripts"][n].encode())))


def on_rpc(kind, msg, step):
    """Script download results (for `locks_apply` and /mikrotik/raw); refused writes are recorded too."""
    t = lambda name: acs.text(msg, name)
    if kind == "DownloadResponse":
        mt["results"].append({"time": stamp(), "download": "accepted", "status": t("Status")})
    elif kind in ("TransferComplete", "AutonomousTransferComplete"):
        mt["results"].append({"time": stamp(), "transfer": t("CommandKey"), "fault": t("FaultCode"), "text": t("FaultString")})
        mt["ok"] = t("FaultCode") in ("", "0"); mt["done"].set()
        event({"event": "script download done, fault %s" % t("FaultCode"), "why": t("FaultString")})
    elif kind == "Fault":
        if step == "download":
            mt["results"].append({"time": stamp(), "download": "fault", "fault": t("FaultCode"), "text": t("FaultString")})
            mt["ok"] = False; mt["done"].set()
        if step == "set" and acs.tr["sent"]:
            mt["results"].append({"time": stamp(), "set": [x for _, _, x in acs.tr["sent"]], "fault": t("FaultCode"), "text": t("FaultString")})


def relay(handler, body):
    """No operator check-in for the ATL (its ACS is the keeper only)."""
    return None


def config():
    """Device part of /status -> config."""
    return {"scan_min": SCAN_MIN, "last_scan": mt["last_scan"] or None, "script_queued": mt["download"]}
