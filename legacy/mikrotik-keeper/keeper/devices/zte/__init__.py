"""Driver for the ZTE MC889 (Yettel BG firmware), DEVICE=zte. Interface: see `keeper.devices`.

TR-069 tree: `Device.Cellular.Interface.1.` (5G NR5G_*, LTE_*, LTE_CA.N, NR_CA.N, neighbour lists, lock values), FOTA, ThermSensor.
255 = the ZTE's "no value yet" (right after a boot / no service). Platform parts: `web` (goform page), `antennas` (modes kept after
boot + sweep), `locks` (LTE / 5G locks), `checkin` (operator relay).
Env: LOCK_UPGRADES (1: `UpgradesManaged=1` - firmware only via this ACS, which never sends one), LOCKTEST_CLEANUP_S (20: wait after
start before clearing the locks an interrupted lock test left).
"""
import re, threading, time

from keeper import acs, guard, locktest
from keeper.common import E, event, log, state
from keeper.devices.zte import antennas, checkin, locks, web
from keeper.devices.zte.locks import lock_value, locks_apply, nr_value   # noqa: F401 (driver interface)

NAME, MAKER = "ZTE", "ZTE"
CAN = {"antennas", "sweep", "lock_test", "night", "checkin"}
ready = web.ready                     # driver hook: wait until the CPE web is back (a rebooted ZTE needs minutes)
LOCKS_REBOOT = True                # a lock is applied only at boot: every lock change (and lock-test step) waits for a BOOT inform
CLEANUP_WAIT_S = float(E("LOCKTEST_CLEANUP_S", "20"))
CI = "Device.Cellular.Interface.1."
READ = [CI, "Device.DeviceInfo.UpTime", "Device.DeviceInfo.SoftwareVersion", "Device.ThermSensor.", "Device.X_ZTE-COM_ROUTER.NetworkDelay", "Device.FOTA.",
        "Device.ManagementServer.PeriodicInformInterval", "Device.ManagementServer.PeriodicInformEnable",
        "Device.ManagementServer.UpgradesManaged"]
EXTRA = ["zte_led_level"]          # X_ZTE-COM_LedSignalLevel: the ZTE's own signal bars (coarse; logged to compare with its 0-4 app grade)
LOCK_UPGRADES = E("LOCK_UPGRADES", "1") == "1"
st = {"boot_apply": True, "applying": False, "boot_gen": 0, "last_boot": 0.0, "prev_fota": None, "status_logged": False}
relay = checkin.relay


def num(v):
    """'' for the ZTE's placeholders (255, -20.0, -3276.8 ...), else the value."""
    return "" if v in (None, "", "None", "-20.0", "-3276.8", "255", "255.0") else v


def eci(hexid):
    """ZTE CELLID (hex E-UTRAN cell id, e.g. 4db3d) -> 'eNB/cell' (1243/61); eNB = id >> 8."""
    try: v = int(hexid, 16); return "%d/%d" % (v >> 8, v & 0xFF)
    except (TypeError, ValueError): return ""


def row(vals):
    """One read -> the shared sample fields. Logs a `FOTA state` event when the ZTE's own updater wakes up."""
    g = lambda k: vals.get(CI + k, "")
    tech = g("CurrentAccessTechnology")
    nr = "%s/%s" % (g("NR5G_ARFCN"), g("NR5G_PCI")) if g("NR5G_PCI") not in ("", "255") and g("NR5G_ARFCN") not in ("", "255") \
        and "NR" in tech.upper() else "no5G"
    ca = []
    for i in range(1, 9):
        if not g("LTE_CA.%d.PCI" % i): break
        band = (re.findall(r"\d+", g("LTE_CA.%d.Band" % i)) or ["?"])[-1]
        bw = (re.findall(r"[\d.]+", g("LTE_CA.%d.DL_bandwidth" % i)) or [""])[0]      # "20MHz" / "20.0" / "10.0"
        ca.append("B%s/%s/%s:%s/%s%s" % (band, g("LTE_CA.%d.DL_channel" % i), g("LTE_CA.%d.PCI" % i),
                                          g("LTE_CA.%d.RSRP" % i), g("LTE_CA.%d.SINR" % i), ("@%g" % float(bw)) if bw else ""))
    nrca = []                                   # 5G secondary carriers (NR CA), e.g. 629988/747:-90.7/@100
    for i in range(1, 5):
        if num(g("NR_CA.%d.PCI" % i)) == "" or num(g("NR_CA.%d.DL_channel" % i)) == "": break
        if "%s/%s" % (g("NR_CA.%d.DL_channel" % i), g("NR_CA.%d.PCI" % i)) == nr: continue     # the ZTE lists the main 5G carrier here too
        bw = (re.findall(r"[\d.]+", g("NR_CA.%d.DL_bandwidth" % i)) or [""])[0]
        nrca.append("%s/%s:%s/%s%s" % (g("NR_CA.%d.DL_channel" % i), g("NR_CA.%d.PCI" % i), num(g("NR_CA.%d.RSRP" % i)),
                                       num(g("NR_CA.%d.SINR" % i)), ("@%g" % float(bw)) if bw else ""))
    nb = zip(g("LTE_NeighbourCellEARFCN").split(";"), g("LTE_NeighbourCellPCI").split(";"), g("LTE_NeighbourCellRSRP").split(";"))
    temps = [vals[k] for k in sorted(vals) if k.endswith("ThermSensorTemp")]
    out = {"uptime_s": vals.get("Device.DeviceInfo.UpTime", ""), "tech": tech, "nr_cell": nr,
           "nr_rsrp": num(g("NR5G_RSRP")), "nr_rsrq": num(g("NR5G_RSRQ")), "nr_sinr": num(g("NR5G_SINR")), "nr_rssi": num(g("NR5G_RSSI")),
           "nr_ssb_rsrp": num(g("NR5G_SSB_RSRP")), "nr_ssb_sinr": num(g("NR5G_SSB_SINR")),
           "lte_cell": "%s/%s" % (g("LTE_EARFCN"), g("LTE_PCI")) if num(g("LTE_EARFCN")) and num(g("LTE_PCI")) else "",   # "" = no LTE service
           "lte_site": eci(g("CELLID")), "lte_rsrp": num(g("LTE_RSRP")), "lte_rsrq": num(g("LTE_RSRQ")),
           "lte_sinr": num(g("LTE_SINR")), "lte_rssi": num(g("LTE_RSSI")), "lte_ca": " ".join(ca),
           "lte_neighbours": " ".join("%s/%s:%s" % t for t in nb if t[1]), "rrc": g("RRC"),
           "down_bps": g("Stats.CurrentDownstreamRate"), "up_bps": g("Stats.CurrentUpstreamRate"),
           "net_delay": vals.get("Device.X_ZTE-COM_ROUTER.NetworkDelay", ""), "temps": "/".join(temps),
           "fota": "%s %s" % (vals.get("Device.FOTA.State", ""), vals.get("Device.FOTA.NewVersion", "")), "nr_ca": " ".join(nrca) if nr != "no5G" else "",
           "zte_led_level": g("X_ZTE-COM_LedSignalLevel")}
    fota = "%s/%s/%s" % (vals.get("Device.FOTA.State", ""), vals.get("Device.FOTA.UpgradeState", ""), vals.get("Device.FOTA.NewVersion", ""))
    if "Device.FOTA.State" not in vals: fota = st["prev_fota"]          # not read this time: no change
    if fota != st["prev_fota"] and (st["prev_fota"] is not None or not fota.startswith("version_idle/fota_idle/")):
        event({"event": "FOTA state", "from": st["prev_fota"] or "", "to": fota, "uptime_s": vals.get("Device.DeviceInfo.UpTime", "")})
        log("FOTA %s -> %s" % (st["prev_fota"], fota))     # firmware update activity - check before it installs
    st["prev_fota"] = fota
    return out


def minute_jobs():
    """Sample jobs in order: scheduled sweep, lock guard, night screen."""
    return [antennas.maybe_sweep, guard.lock_guard, locktest.maybe_night]


def nr_on(row, cell):
    """Served by the locked 5G cell?"""
    return (row.get("nr_cell") or "") in guard.lock_target(cell)


def start_note():
    """Device part of the start log line."""
    return "ZTE antennas after boot: auto=%s ant5=%s ant6=%s, operator check-in every %s days" % (antennas.AUTO, antennas.ANT5, antennas.ANT6, checkin.CHECKIN_DAYS)


def on_start():
    """Keeper start: a lock test interrupted by a restart left its locks on the ZTE - take them off again (a 5G test also pinned an
    LTE anchor, so both legs are cleared; a plain LTE test / the format learning only touched LTE)."""
    t = state().get("lock_test")
    if not t: return
    label = t.get("cell") if isinstance(t, dict) else t
    nr = isinstance(t, dict) and t.get("rat") == "nr"
    log("lock test %s was interrupted - removing the %s lock" % (label, "LTE and 5G" if nr else "LTE"))
    def undo():
        time.sleep(CLEANUP_WAIT_S)
        try: locks.web_clear(nr=nr); state({"lock_test": None}); log("locks removed after the interrupted test")
        except Exception as e: log("removing the locks failed %r - remove them in the ZTE web page" % e)
    threading.Thread(target=undo, daemon=True).start()


def on_boot():
    """BOOT inform: put the antenna mode back after the next read (the ZTE resets it to omni). The ZTE often sends two informs
    seconds apart for one boot: only one apply is queued per boot (20 s window, generation)."""
    now = time.time()
    if now - st["last_boot"] > 20:
        st["boot_gen"] += 1; st["boot_apply"] = True; st["last_boot"] = now


def on_values(vals):
    """After each read: lock values, the firmware status line (once), antenna apply after a boot, firmware guard write."""
    locks.on_values(vals)
    if not st.get("cellids_logged"):         # once: the cell ids the CPE reports (one short line - the full interface list was a gNB hunt)
        st["cellids_logged"] = True
        g2 = lambda k: vals.get(CI + k, "")
        nci = g2("NR5G_CELLID") or g2("NCGI") or g2("gNBID")
        log("ZTE cell ids: LTE %s%s; 5G %s" % (eci(g2("CELLID")) or "?",
                                               (" (ECGI %s)" % g2("ECGI")) if g2("ECGI") else "",
                                               ("NCI %s" % nci) if nci else "NCI/gNB not reported in NSA"))
    if not st["status_logged"]:
        st["status_logged"] = True
        cur, offered = vals.get("Device.DeviceInfo.SoftwareVersion") or "", vals.get("Device.FOTA.NewVersion") or ""
        bn = lambda v: int((re.findall(r"B(\d+)$", v) or ["-1"])[0])      # YETTEL_BG_MC889V2.0.0B11 -> 11
        log("ZTE status: firmware %s, UpgradesManaged=%s, FOTA %s/%s, FOTA NewVersion %s%s, inform every %ss"
            % (cur or "?", vals.get("Device.ManagementServer.UpgradesManaged"), vals.get("Device.FOTA.State"), vals.get("Device.FOTA.UpgradeState"),
               offered or "-", " (NEWER than installed!)" if cur and offered and bn(offered) > bn(cur) else
               " (not newer than installed: no update)" if offered else "", vals.get("Device.ManagementServer.PeriodicInformInterval")))
    if st["boot_apply"] and not st["applying"]:
        st["boot_apply"] = False; st["applying"] = True; gen = st["boot_gen"]
        def run():                                     # one apply per boot: a newer boot makes this worker stand down
            try:
                antennas.apply_after_boot(int(vals.get("Device.DeviceInfo.UpTime") or 0), keep=lambda: st["boot_gen"] == gen)
            finally:
                st["applying"] = False
        threading.Thread(target=run, daemon=True).start()
    if LOCK_UPGRADES and vals.get("Device.ManagementServer.UpgradesManaged") not in ("1", "true"):
        log("setting UpgradesManaged %s -> 1 (no firmware updates except via this ACS)" % vals.get("Device.ManagementServer.UpgradesManaged"))
        return [("Device.ManagementServer.UpgradesManaged", "xsd:boolean", "1")]
    return []


def pending_rpc(host):
    """No extra RPCs on the ZTE."""
    return None


def on_rpc(kind, msg, step):
    """A TransferComplete on the ZTE = firmware / file transfer (we never start one): logged as an event."""
    if kind in ("TransferComplete", "AutonomousTransferComplete"):
        event({"event": "firmware/transfer complete %s" % acs.text(msg, "FaultCode"), "why": acs.text(msg, "FaultString")})


def config():
    """Device part of /status -> config (field names the userscript panel reads)."""
    s = state; eff, src, rec = antennas.effective()
    return {"antennas": antennas.ant_cfg(), "antennas_effective": eff, "antennas_source": src, "antennas_cell": rec,
            "antennas_env": {"auto": antennas.AUTO, "ant5": antennas.ANT5, "ant6": antennas.ANT6},
            "sweep_days": antennas.SWEEP_DAYS, "sweep_running": antennas.sweep_st["running"], "last_sweep": s().get("last_sweep"),
            "lte_lock_fmt": s().get("lte_lock_fmt"), "zte_locks_raw": locks.seen["raw"], "checkin_days": checkin.CHECKIN_DAYS,
            "lock_upgrades": LOCK_UPGRADES}
