"""ZTE low-band ownership and durable restoration of a temporary radio configuration."""
import collections
import re
import threading
import time

from keeper import acs, alert, brain, common
from keeper.common import log, state
from keeper.devices.zte import locks
from keeper.devices.zte.web import Web, web_lock

BAND, LTE, NR = "LockLTEBand", "LockLTECell", "LockNr5gCell"
FIELDS = (BAND, LTE, NR)
BOOT_S = float(common.E("LOCKTEST_BOOT_WAIT_S", "180"))
changed = threading.Condition()
serial = threading.RLock()
latest = None
write_boot = None
awaiting_boot = None
radio = collections.deque(maxlen=240)
recovery = {"busy": False, "next": 0}
common.register_busy("radio restore pending", lambda: recovery.get("pending"))
common.register_busy("radio profile recovery", lambda: recovery["busy"])


def low_band(lte):
    if not lte or lte == "none":
        return None
    if not re.fullmatch(r"\d{1,4}@\d{1,7}", lte):
        raise ValueError("expected one LTE PCI@EARFCN")
    band = brain.band_of(lte.split("@")[1])
    return band[1:] if band in ("B8", "B20") else None


def observed(vals):
    global latest
    ci = locks.CI
    raw = {key: vals.get(ci + key) for key in FIELDS}
    with changed:
        latest = (time.monotonic(), acs.st["boots"], raw)
        if ci + "LTE_EARFCN" in vals:
            from keeper.devices import zte
            row = zte.row(vals)
            radio.append(dict(time=time.time(), boots=acs.st["boots"], **raw,
                              **{k: row[k] for k in ("lte_cell", "nr_cell", "lte_ca", "nr_sinr", "nr_rsrp", "nr_rsrq")}))
        changed.notify_all()


def band_mask(bands):
    if not isinstance(bands, str) or not re.fullmatch(r"\d+(,\d+)*", bands):
        raise RuntimeError("missing or invalid LockLTEBand readback")
    numbers = set(map(int, bands.split(",")))
    if min(numbers) < 1 or max(numbers) > 64:
        raise RuntimeError("unsupported LTE band list")
    return sum(1 << (n - 1) for n in numbers)


def wait_band(band, since):
    with changed:
        if not changed.wait_for(lambda: latest and latest[0] > since and latest[2][BAND] == band, 90):
            raise RuntimeError("missing fresh discovery band readback")


def spec(raw):
    if raw is None:
        raise RuntimeError("missing cell lock readback")
    if raw == "" or re.fullmatch(r"0(,0)+", raw):
        return "none"
    if not re.fullmatch(r"\d+,\d+(,\d+,\d+)?", raw):
        raise RuntimeError("unknown cell lock format %r" % raw)
    pci, channel = raw.split(",")[:2]
    return pci + "@" + channel


def snapshot():
    with changed:
        if not latest or time.monotonic() - latest[0] > 90 or latest[1] != acs.st["boots"]:
            raise RuntimeError("no fresh modem profile readback")
        raw = latest[2].copy()
    band_mask(raw[BAND])
    spec(raw[LTE])
    spec(raw[NR])
    return {"raw": raw, "prior_band": state().get("lte_prior_band")}


def matches(actual, target):
    for k, value in target.items():
        if actual.get(k) == value:
            continue
        if k == BAND or actual.get(k) is None or spec(value) != "none" or spec(actual[k]) != "none":
            return False
    return True


def _web(raw):
    """Stage all fields, read the configured values back, then request one reboot."""
    with web_lock:
        w = Web()
        err = w.login()
        if err:
            raise RuntimeError(err)
        try:
            mask = band_mask(raw[BAND])
            pci, ch = ("0", "0") if spec(raw[LTE]) == "none" else spec(raw[LTE]).split("@")
            forms = [{"goformId": "BAND_SELECT", "is_gw_band": "0", "gw_band_mask": "0",
                      "is_lte_band": "1", "lte_band_mask": "0x%011x" % mask},
                     {"goformId": "LTE_LOCK_CELL_SET", "lte_pci_lock": pci, "lte_earfcn_lock": ch}]
            if NR in raw:
                forms.append({"goformId": "NR5G_LOCK_CELL_SET", "nr5g_cell_lock": raw[NR] or "0,0,0,0"})
            for form in forms:
                if w.post(form).get("result") != "success":
                    raise RuntimeError(form["goformId"] + " refused")
            v = w.get("lte_band_lock,lte_pci_lock,lte_earfcn_lock,nr5g_cell_lock")
            lte = ",".join(v[k] or "0" for k in ("lte_pci_lock", "lte_earfcn_lock"))
            if (int(v["lte_band_lock"], 16) != mask or spec(lte) != spec(raw[LTE])
                    or (NR in raw and not matches({NR: v.get("nr5g_cell_lock")}, {NR: raw[NR]}))):
                raise RuntimeError("web band/cell readback mismatch")
            try:
                if w.post({"goformId": "REBOOT_DEVICE"}).get("result") != "success":
                    raise RuntimeError("web reboot refused")
            except OSError:
                pass  # a dropped connection needs the same BOOT proof as a successful response
        finally:
            w.logout()


def _write(target, *, web=False):
    global write_boot, awaiting_boot
    raw = target["raw"]
    try:
        before = snapshot()["raw"]
    except RuntimeError:
        before = {}
    uncertain = state().get("profile_write")
    late_boot = write_boot is not None and latest and latest[1] > write_boot
    if (not uncertain or late_boot) and matches(before, raw):
        if uncertain: state({"profile_write": None})
        return "unchanged"
    if uncertain == raw and awaiting_boot == (raw, acs.st["boots"]):
        raise RuntimeError("restore already acknowledged; waiting for BOOT and readback")
    boots = acs.st["boots"]
    values = dict(raw)
    for k, clear in ((LTE, locks.unlock_val(state().get("lte_lock_fmt") or {"lock": "{pci},{earfcn}"})), (NR, "0,0,0,0")):
        if k in values and spec(values[k]) == "none":
            values[k] = clear
    write_boot = boots
    awaiting_boot = None
    state({"profile_write": raw})
    acs.write_receipt.boots = None
    method = "tr069"
    if web or locks.LOCK_VIA == "web" or not acs.tr069_set([(locks.CI + k, "xsd:string", v) for k, v in values.items()], reboot=True):
        boots = acs.st["boots"]
        _web(raw)
        method = "web"
    elif acs.write_receipt.boots is not None:
        boots = acs.write_receipt.boots
    write_boot = boots
    awaiting_boot = (raw, boots)
    with changed:
        if not changed.wait_for(lambda: latest and latest[1] > boots and matches(latest[2], raw), BOOT_S):
            if acs.st["boots"] <= boots:
                detail = "no BOOT inform"
            elif not latest or latest[1] <= boots:
                detail = "BOOT inform received but no configuration readback"
            else:
                actual = {key: latest[2].get(key) for key in raw}
                detail = "requested %s, read %s" % (raw, actual)
            raise RuntimeError("no matching post-BOOT band/cell readback within %g s: %s" % (BOOT_S, detail))
    state({"profile_write": None})
    return method


def apply(lte, nr, web=False):
    with serial:
        saved = state()
        if saved.get("profile_rollback"):
            raise RuntimeError("previous radio profile change needs recovery")
        try:
            before = snapshot()
        except RuntimeError:
            if lte == "none" and nr is None and saved.get("lte_prior_band") and not saved.get("profile_restore"):
                return offline_release()
            raise
        prior = before["prior_band"]
        raw = before["raw"].copy()
        band = low_band(lte)
        if lte is not None:
            if band:
                prior = prior or raw[BAND]
                raw[BAND] = band
            elif prior:
                raw[BAND], prior = prior, None
            fmt = saved.get("lte_lock_fmt") or {"lock": "{pci},{earfcn}"}
            if lte == "none":
                raw[LTE] = ""
            else:
                pci, channel = lte.split("@")
                raw[LTE] = fmt["lock"].format(pci=pci, earfcn=channel)
        if nr is not None:
            raw[NR] = "" if nr == "none" else locks.nr_value(nr)
        else:
            del before["raw"][NR]
            del raw[NR]  # an LTE-only request leaves the NR lock untouched, including idle NSA
        rollback = before
        if lte == "none" and before["prior_band"] and not saved.get("profile_restore"):
            rollback = {"raw": raw, "prior_band": None}
        recovery["pending"] = True
        state({"profile_rollback": rollback})
        method = _write({"raw": raw}, web=web)
        state({"lte_prior_band": prior, "profile_rollback": None})
        recovery["pending"] = pending()
        return method


def begin_scope(owner=None, antennas=None, web_notice=None):
    with serial:
        if pending():
            raise RuntimeError("previous radio profile restore pending")
        original = snapshot()
        original["owner"] = owner
        if antennas is not None:
            original["antennas"] = antennas
        if web_notice:
            original["web_notice"] = web_notice
        state({"profile_restore": original})
        recovery["pending"] = True
        return original


def finish_scope(commit=None):
    """Remove the journal only after modem configuration and fresh online probes agree."""
    with serial:
        saved = state()
        target = saved.get("profile_restore") or saved.get("profile_rollback")
        if not target:
            return
        if commit is None:
            _write(target)
            if target.get("antennas"):
                from keeper.devices.zte.antennas import restore_trial_mode
                restore_trial_mode(target["antennas"])
            if not brain.wait_online(timeout=90, since=time.time()):
                raise RuntimeError("profile restore pending: no fresh internet probes")
            update = dict(target.get("managed") or {})
            if target.get("owner") == "lock_test": update["lock_test"] = None
            state(dict(update, lte_prior_band=target["prior_band"], profile_rollback=None, profile_restore=None))
        else:
            state(dict(commit, profile_restore=None))
        recovery["pending"] = False
        return target


def pending():
    s = state()
    return bool(s.get("profile_restore") or s.get("profile_rollback"))


def recover(row=None):
    with common.test_start_lock:
        if not pending() or common.busy(exclude="radio restore pending") or time.monotonic() < recovery["next"]:
            return
        recovery.update(busy=True, next=time.monotonic() + 600)
    def work():
        try:
            target = finish_scope()
            if target and target.get("web_notice"):
                alert.web_test_end("%s: keeper finished recovering the starting settings." % target["web_notice"])
        except Exception as e:
            log("radio profile restore pending: %s" % e)
        finally:
            recovery["busy"] = False
    threading.Thread(target=work, daemon=True).start()


def offline_release():
    """The offline guard cannot take a new CWMP snapshot. Release only the affected fields."""
    with serial:
        prior = state().get("lte_prior_band")
        if not prior:
            return locks.web_lock_reboot(0, 0)
        target = {"raw": {LTE: "", BAND: prior}, "prior_band": None}
        managed = state().get("lte_lock")
        if managed:
            now = time.time()
            target["managed"] = {"lte_lock": dict(managed, state="released", released_at=now,
                retry_at=now + locks.guard.LOCK_RETRY_H * 3600, reason="offline release")}
        recovery["pending"] = True
        state({"profile_rollback": target})
        method = _write(target, web=True)
        state(dict(target.get("managed", {}), profile_rollback=None, lte_prior_band=None))
        recovery["pending"] = False
        return method


def config_matches(raw, lte, nr):
    restricted = low_band(lte)
    return (spec(raw[LTE]) == lte and spec(raw[NR]) == nr
            and (not restricted or raw[BAND] == restricted))


def serving_matches(row, target, lte, nr, natural_band=None, expected_nr=None):
    """Check the configured locks and the serving cells in the same read."""
    lte_cell, nr_cell = row["lte_cell"], row["nr_cell"]
    return (row["boots"] == acs.st["boots"] and matches(row, target)
            and "/" in lte_cell and "/" in nr_cell
            and (lte == "none" or lte_cell == brain.anchor_key(lte))
            and (nr == "none" or nr_cell == brain.anchor_key(nr))
            and (not expected_nr or nr_cell == expected_nr)
            and (not natural_band or brain.nr_band_of(nr_cell.split("/")[0]) == natural_band))


def transfer_evidence(windows, target, lte, nr, natural_band=None, expected_nr=None):
    """Require a matching radio observation inside each successful transfer, including the control."""
    observations = list(radio)
    groups = [[r for r in observations if start <= r["time"] < end] for start, end in windows]
    rows = [r for group in groups for r in group]
    anchors = {r["lte_cell"] for r in rows}
    pairs = {r["nr_cell"] for r in rows}
    valid = (config_matches(target, lte, nr) and bool(groups) and all(groups) and len(anchors) == len(pairs) == 1
             and all(serving_matches(row, target, lte, nr, natural_band, expected_nr) for row in rows))
    return {"verified": bool(valid), "samples_per_transfer": list(map(len, groups)), "observations": rows}
