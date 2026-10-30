"""ZTE cell locks: LTE `LockLTECell`, 5G `LockNr5gCell` over TR-069 (+ Reboot - the ZTE applies a lock only at boot), web page fallback.

The LTE value format is learned once (`learn_lock_format`: a lock set on the web page WITHOUT reboot, read back over TR-069,
removed again - nothing changes on the radio), stored as `lte_lock_fmt` in state.json.
Cell lists don't work on the MC889 (measured 2026-10-27: `340,100;340,3350` is accepted but only `340,100` kept): single cells only.
Env: LOCK_VIA (tr069 | web).
"""
from keeper import acs, common, guard
from keeper.common import E, event, log, state
from keeper.devices.zte.web import Web, web_lock

LOCK_VIA = E("LOCK_VIA", "tr069")
CI = "Device.Cellular.Interface.1."
LOCK_PARAM = CI + "LockLTECell"
NR_PARAM = CI + "LockNr5gCell"
seen = {"lock_val": None, "raw": None, "learning": False}     # last read lock values (raw = all four, shown in /status)
common.register_busy("lock format learning", lambda: seen["learning"])


def nr_value(spec):
    """`'534@636576'` -> `'534,636576,78,30'` = PCI,ARFCN,band,SCS kHz (the ZTE's 5G lock format, as on its web page)."""
    pci, arfcn = spec.split("@")
    try: band, scs = guard.nr_band(arfcn)
    except ValueError as e: raise RuntimeError(str(e))
    return "%s,%s,%d,%d" % (pci, arfcn, band, scs)


def web_set(pci, earfcn, reboot):
    """LTE_LOCK_CELL_SET on the web page (0/0 = unlock), optionally REBOOT_DEVICE."""
    with web_lock:
        w = Web(); err = w.login()
        if err: raise RuntimeError(err)
        r = w.post({"goformId": "LTE_LOCK_CELL_SET", "lte_pci_lock": str(pci), "lte_earfcn_lock": str(earfcn)}).get("result")
        if not reboot: w.logout()
        if r != "success": raise RuntimeError("LTE_LOCK_CELL_SET %s@%s -> %s" % (pci, earfcn, r))
        if reboot:
            try: w.post({"goformId": "REBOOT_DEVICE"})
            except Exception: pass                  # the ZTE may drop the connection while it goes down


def web_lock_reboot(pci, earfcn):
    """LTE lock (0/0 = unlock) + reboot on the web page."""
    web_set(pci, earfcn, True)


def learn_lock_format(pci="434", earfcn="1550"):
    """One time: how `LockLTECell` shows a lock. Returns / stores `{"lock": "...{pci}...{earfcn}...", "unlock": ...}` or None."""
    prev = state().get("lock_test")
    seen["learning"] = True; state({"lock_test": prev or "learning"})   # if the keeper dies in between, the next start removes the lock
    try:
        acs.wait_informs(1); before = seen["lock_val"]
        web_set(pci, earfcn, False); acs.wait_informs(2); locked = seen["lock_val"]
        web_set(0, 0, False); acs.wait_informs(2); unlocked = seen["lock_val"]
        log("lock format: before %r, locked %s@%s -> %r, unlocked -> %r" % (before, pci, earfcn, locked, unlocked))
        if locked is None or locked == unlocked or str(pci) not in locked or str(earfcn) not in locked:
            event({"event": "lock format unknown", "why": "LockLTECell %r / %r - the keeper keeps using the web page for locks" % (locked, unlocked)})
            return None
        fmt = {"lock": locked.replace(str(earfcn), "{earfcn}").replace(str(pci), "{pci}"), "unlock": unlocked or ""}
        if fmt["lock"].format(pci=pci, earfcn=earfcn) != locked: return None
        state({"lte_lock_fmt": fmt}); event({"event": "lock format learned", "why": "LockLTECell lock %r, unlock %r" % (fmt["lock"], fmt["unlock"])})
        return fmt
    finally:
        seen["learning"] = False; state({"lock_test": prev})


def lte_lock_reboot(pci, earfcn):
    """Set (0/0 = remove) the LTE lock, then reboot. TR-069 once the format is learned, the web page otherwise. Returns 'tr069' / 'web'."""
    fmt = state().get("lte_lock_fmt")
    if not fmt and LOCK_VIA == "tr069" and str(pci) != "0": fmt = learn_lock_format()     # never learn on an unlock (learning sets a lock)
    if fmt and LOCK_VIA == "tr069":
        val = fmt["unlock"] if str(pci) == "0" else fmt["lock"].format(pci=pci, earfcn=earfcn)
        if acs.tr069_set([(LOCK_PARAM, "xsd:string", val)], reboot=True): return "tr069"
        log("TR-069 lock write %r not accepted - using the web page" % val)
    web_lock_reboot(pci, earfcn)
    return "web"


def lock_lte(spec):
    """Lock one cell `'434@1550'` + reboot; None / 'none' = unlock (driver interface, used by lock tests)."""
    if not spec or spec == "none": return lte_lock_reboot(0, 0)
    return lte_lock_reboot(*guard.spec_cells(spec)[0])


def locks_apply(lte=None, nr=None):
    """Set the LTE and/or 5G lock (`'pci@channel'`, 'none' = remove, None = leave), then ONE reboot. TR-069 first, web fallback."""
    if nr is None: return lock_lte(lte)
    pairs = [(NR_PARAM, "xsd:string", "" if nr == "none" else nr_value(nr))]
    if lte is not None:
        fmt = state().get("lte_lock_fmt") or (learn_lock_format() if LOCK_VIA == "tr069" and lte != "none" else None)
        p, e = ("0", "0") if lte == "none" else guard.spec_cells(lte)[0]
        pairs = pairs + [(LOCK_PARAM, "xsd:string", fmt["unlock"] if lte == "none" else fmt["lock"].format(pci=p, earfcn=e))] if fmt else None
    if pairs and LOCK_VIA == "tr069":
        if acs.tr069_set(pairs, reboot=True): return "tr069"
        log("TR-069 lock write %r not accepted - using the web page" % [v for _, _, v in pairs])
    with web_lock:
        w = Web(); err = w.login()
        if err: raise RuntimeError(err)
        if lte is not None:
            p, e = ("0", "0") if lte == "none" else guard.spec_cells(lte)[0]
            r = w.post({"goformId": "LTE_LOCK_CELL_SET", "lte_pci_lock": p, "lte_earfcn_lock": e}).get("result")
            if r != "success": raise RuntimeError("LTE_LOCK_CELL_SET %s@%s -> %s" % (p, e, r))
        r = w.post({"goformId": "NR5G_LOCK_CELL_SET", "nr5g_cell_lock": "0,0,0,0" if nr == "none" else nr_value(nr)}).get("result")
        if r != "success": raise RuntimeError("NR5G_LOCK_CELL_SET %s -> %s" % (nr, r))
        try: w.post({"goformId": "REBOOT_DEVICE"})
        except Exception: pass
    return "web"


def lock_value(key):
    """Driver interface: (label, raw value) of the ZTE's own lock for `lte_lock` / `nr_lock`, None before the first read."""
    raw = seen["raw"]
    if raw is None: return None
    label = "LockLTECell" if key == "lte_lock" else "LockNr5gCell"
    return label, raw.get(label)


def on_values(vals):
    """Remember the lock values of this read (for the guard, the format learning and /status)."""
    seen["lock_val"] = vals.get(LOCK_PARAM)
    seen["raw"] = {k: vals.get(CI + k) for k in ("LockLTECell", "LockNr5gCell", "LockLTEBand", "LockNr5gNSABand")}
