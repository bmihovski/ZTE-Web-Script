"""Morning cell check: no-lock -> serving cell and first measurable backup per band -> 5G gate -> A/B -> best 5G.

Daily (`MORNING` 08:00, with catch-up until `MORNING_END`): one no-lock leg (the full CA)
and, for B1/B3/B7, a band-lock discovery of the serving cell and up to two backups ranked by good measured pair quality, then
RSRP. Try the second backup only if the first cannot measure. Skip recent repeat reachability failures by LTE cell. The band
scan restores the previous band list after discovery. High-band cell locks allow CA; a B8/B20 cell lock
restricts LTE to its matching band for that leg. Discover B20/B8 on Saturday or when the
unlocked screen is poor or unavailable. Test them after the high bands if no high leg passes the screen. Learn the cells from the
current discovery and let each low LTE anchor attach its own n28 cell.

**5G is a hard gate:** a leg is a candidate only if the 5G leg was up during its probe (a sample with a real `nr_cell` in the probe
window). Only 5G legs are A/B/persisted; the no-lock is the default when it has 5G; a band with one 5G cell contributes A only; a
band with none is skipped. The probe is 25 s; the pair database (minutes, long-run call/loss/p95/jitter) is the stability guard
against a cell that looks good for 25 s and then switches (the B7 failover).

Each cell lock is one reboot on the ZTE; the no-lock start and the best restore are one each. A failed first backup can add a
reboot. The upload is measured on the restored winner (the modem's iperf3, 1 stream - the MC889 gives no upload
with 4); the daily probe is download + latency.

`POST /morning?low=1|0&apply=0` measures full down/up on eligible legs, records the comparison, and restores the managed locks.
`GET /morning` reports the applied check and the latest comparison. Omit `apply` for the usual morning check.
"""
from collections import Counter
import time, threading

from keeper import VERSION, acs, alert, bandscan, brain, captest, common, devices
from keeper.common import E, event, log, state, stamp

MORNING = E("MORNING", "08:00")                       # '' = off; at/after this HH:MM once a day (the missed day runs first thing)
MORNING_END = E("MORNING_END", "09:00")               # no catch-up after this: its lock legs reboot, 09:00-18:00 are call hours (2026-11-24)
BANDS = [b for b in E("MORNING_BANDS", "1,3,7").replace(" ", "").split(",") if b]
LOW = [b for b in E("MORNING_LOW_BANDS", "20,8").replace(" ", "").split(",") if b]
LOW_SAT = E("MORNING_LOW_SATURDAY", "1") == "1"
LOW_DOWN, LOW_CALL = float(E("MORNING_LOW_DOWN", "20")), float(E("MORNING_LOW_CALL", "90"))
PROBE_S, UP_SECS = int(E("MORNING_PROBE_S", "25")), int(E("MORNING_UP_SECS", "8"))
BAND_WAIT_S = float(E("MORNING_BAND_WAIT_S", "125"))
MIN_RSRP = float(E("MORNING_MIN_RSRP", "-110"))
WAKE_S, WAKE_LEAD_S = float(E("MORNING_WAKE_S", "25")), float(E("MORNING_WAKE_LEAD_S", "5"))   # keep traffic on so the NSA 5G is up
FIVE_G_WAIT_S = float(E("MORNING_5G_WAIT_S", "90"))     # high-band NSA wake window
LOW_WAKE_S = 30                                      # unscored modem download before a low-band probe
LINK_WAIT_S = float(E("MORNING_LINK_WAIT_S", "120"))
CANDIDATES = max(0, int(E("MORNING_CANDIDATES", "2")))
PAIR_MAX_LOSS = float(E("MORNING_PAIR_MAX_LOSS", "2"))
PAIR_MAX_JITTER = float(E("MORNING_PAIR_MAX_JITTER", "20"))
DEAD_N = max(1, int(E("MORNING_DEAD_N", "2")))
DEAD_DAYS = float(E("MORNING_DEAD_DAYS", "7"))
DEAD_S = float(E("MORNING_DEAD_S", "90"))

st = {"running": None, "stage": "", "started": None, "result": None, "legs": [], "why": ""}
common.register_busy("morning check", lambda: bool(st["running"]))


def _spec(v):
    """'3350/340' or '340@3350' -> '340@3350'; ''/no5G -> ''."""
    v = v or ""
    if "@" in v: return v
    if "/" in v: a, b = v.split("/", 1); return "%s@%s" % (b, a)
    return ""


def _best_pair(anchor, good_only=False, nr_band=None):
    """Best (rank, NR cell) for this LTE anchor. Good pairs have enough evidence and meet the loss/jitter caps."""
    best = None
    key = brain.anchor_key(anchor)
    for cell, e in list((brain.REG.get("nr") or {}).items()):
        if nr_band and brain.nr_band_of(cell.split("/", 1)[0]) != nr_band:
            continue
        pair = (e.get("anchors") or {}).get(key)
        if not pair:
            continue
        loss, jitter = brain.f(pair.get("loss")), brain.f(pair.get("jitter"))
        good = ((pair.get("min") or 0) >= brain.ANCHOR_MIN and loss is not None and loss <= PAIR_MAX_LOSS
                and jitter is not None and jitter <= PAIR_MAX_JITTER)
        if good_only and not good:
            continue
        q = brain.pair_quality(pair, key)
        rank = (good, q[:4], bool(brain.same_site(cell, key)), q[4:])
        if best is None or rank > best[0]:
            best = (rank, cell)
    return best


def _nr_for(anchor):
    best = _best_pair(anchor)
    return _spec(best[1]) if best else "none"


def _recent_failures(now):
    cutoff = now - DEAD_DAYS * 86400
    failures = {}
    for cell, times in (state().get("morning_dead") or {}).items():
        recent = [t for t in times if cutoff <= t <= now]
        if recent:
            failures[cell] = recent
    return failures


def _dead_cells(now=None):
    return {cell for cell, times in _recent_failures(time.time() if now is None else now).items() if len(times) >= DEAD_N}


def _note_reachability(leg):
    cell = leg["lte"]
    if cell == "none" or not (leg.get("failure") or (leg["ok"] and leg["has5g"])):
        return
    now = time.time()
    failures = _recent_failures(now)
    if leg.get("failure"):
        failures[cell] = (failures.get(cell, []) + [now])[-DEAD_N:]
    elif cell in failures:
        del failures[cell]
    else:
        return
    state({"morning_dead": failures})


def _wait_link(timeout=LINK_WAIT_S):
    dev = devices.current()
    try: dev.ready(timeout=timeout)
    except Exception: pass
    try: brain.wait_online(timeout=min(180, timeout))
    except Exception: pass


def _real_nr(row, nr_band=None):
    nr = row.get("nr_cell") or ""
    if "/" in nr and nr.split("/", 1)[0] not in ("0", "255"):
        if not nr_band or brain.nr_band_of(nr.split("/", 1)[0]) == nr_band:
            return nr
    return ""


def _has5g(mark, nr_band=None):
    """Look for a real 5G cell since the start of this leg's wake/probe, even when the history deque is full."""
    return any(_real_nr(row, nr_band) for row in brain.samples_since(mark))


def _q(l):
    return brain.quality_values(100.0 if l.get("call_ok") == 1 else (l.get("call_ok") or 0),
                                l.get("loss_pct"), l.get("lat_p95"), l.get("jitter_ms"))


def _warm_low(leg, profile):
    """Let traffic raise n28 before scoring any part of a low-band leg."""
    lte, nr = leg["lte"], leg["nr"]
    target = profile.snapshot()["raw"]
    if not profile.config_matches(target, lte, nr):
        raise RuntimeError("low-band lock readback does not match the requested configuration")
    warm = leg["warmup"] = {"seconds": LOW_WAKE_S}
    windows = []
    log("morning %s: warming n28 with a %d s modem download" % (leg["leg"], LOW_WAKE_S))
    down, _, how = captest.measure(secs=LOW_WAKE_S, load_windows=windows, download_only=True)
    warm.update(method=how, observations=[])
    if down is None or not windows:
        raise RuntimeError("n28 warm-up download failed (%s)" % how)
    started, finished = windows[0]
    warm["transfer_s"] = round(finished - started, 1)
    rows = [row for row in list(profile.radio) if row["time"] >= started]
    warm["observations"] = rows
    last = rows[-1] if rows else {}
    if (last.get("boots") != acs.st["boots"] or last.get("lte_cell") != brain.anchor_key(lte)
            or not profile.matches(last, target)):
        raise RuntimeError("no matching LTE configuration at the end of the n28 warm-up")
    if not profile.serving_matches(last, target, lte, nr, "n28"):
        label = "n28 " + nr if nr != "none" else "n28"
        leg["failure"] = "no_5g"
        raise RuntimeError("LTE %s attached but %s was not present at the end of the %d s warm-up" % (lte, label, LOW_WAKE_S))
    warm["nr_cell"] = last["nr_cell"]
    return last["nr_cell"]


def _record_load(leg, mark, nr_band):
    """Measure comparable throughput and transfer-window quality, and record the radio path observed on this leg."""
    windows = []
    profile = getattr(devices.current(), "profile", None)
    target_config = profile.snapshot()["raw"] if profile else None
    # Allow at least two fast informs per transfer, including when Enable lands between reads.
    transfer_s = max(captest.ISECS, 2 * acs.FAST_INTERVAL + 5) if profile else None
    try:
        down, up, how = captest.measure(secs=transfer_s, load_windows=windows)
    except captest.LocalTraffic as e:
        leg["load_retries"] = [str(e)]
        log("morning %s: waiting to retry the load measurement: %s" % (leg["leg"], e))
        if not captest.wait_lan_quiet():
            raise
        windows.clear()
        down, up, how = captest.measure(secs=transfer_s, load_windows=windows)
    quality = brain.probe_windows(windows, min_points=2) if len(windows) == 2 else {}
    rows = brain.samples_since(mark)
    proof = None
    if profile:
        warmed_nr = (leg.get("warmup") or {}).get("nr_cell")
        proof = profile.transfer_evidence(windows, target_config, leg["lte"], leg["nr"], nr_band, expected_nr=warmed_nr)
        leg["radio"] = proof
        leg["transfer_seconds"] = [round(end - start, 1) for start, end in windows]
        if len(windows) != 2:
            leg["error"] = "iperf3 did not complete both transfers (%s)" % how
            return
        if not all(proof["samples_per_transfer"]):
            leg["error"] = "no fresh radio sample during %s transfer" % (
                " or ".join(direction for direction, count in zip(("download", "upload"), proof["samples_per_transfer"]) if not count))
            return
        if not proof["verified"]:
            leg["error"] = "unverified band or serving cells during down/up transfers"
            return
        if warmed_nr:
            rows = proof["observations"]   # the warm-up and idle gaps cannot count towards loaded stability
    lte_cells = Counter(r["lte_cell"] for r in rows if r.get("lte_cell"))
    anchor = lte_cells.most_common(1)[0][0] if lte_cells else ""
    target = brain.anchor_key(leg["lte"]) if leg["lte"] != "none" else anchor
    nr_cells = Counter(_real_nr(r, nr_band) for r in rows if r.get("lte_cell") == target)
    nr_cells.pop("", None)
    nr = nr_cells.most_common(1)[0][0] if nr_cells else ""
    if proof:
        transfer_rows = proof["observations"]
        anchor, nr = transfer_rows[0]["lte_cell"], transfer_rows[0]["nr_cell"]
        transfer_matches = len(transfer_rows)
    else:
        transfer_rows = [row for row in rows if any(start <= brain.ts(row) < end for start, end in windows)]
        transfer_matches = sum(1 for row in transfer_rows if row.get("lte_cell") == target and _real_nr(row, nr_band) == nr)
    leg.update(load_method=brain.LTE_LOAD_METHOD, load_s=round(sum(end - start for start, end in windows), 1),
               load_down_mbps=down, load_up_mbps=up, load_how=how, observed_anchor=anchor, observed_nr=nr,
               nr_samples=dict(nr_cells), radio_samples=len(rows), on_target_pct=round(100 * lte_cells[target] / len(rows), 1) if rows else None,
               nr_pct=round(100 * nr_cells[nr] / len(rows), 1) if rows else None,
               transfer_radio_samples=len(transfer_rows), transfer_radio_matches=transfer_matches,
               load_call_pct=quality.get("call_ok_pct"), load_loss_pct=quality.get("loss_pct"),
               load_p95=quality.get("lat_p95"), load_jitter_ms=quality.get("jitter_ms"))


def _leg(name, lte, nr, band=None, role="", compare=False):
    """One leg: lock the pair (one reboot), wait for the link, internet-test -> the result dict (+`has5g` from the probe window)."""
    t0 = time.time(); res = {"leg": name, "band": band, "role": role, "lte": lte or "none", "nr": nr or "none",
                             "time": stamp(), "ok": False, "error": "", "has5g": False}
    stop = threading.Event()
    profile = None
    nr_band = "n28" if band in LOW else None
    try:
        dev = devices.current()
        profile = getattr(dev, "profile", None)
        if band and lte and brain.band_of(lte.split("@")[-1]) != "B" + band:
            raise RuntimeError("LTE band conflicts with cell EARFCN")
        r = dev.locks_apply(lte or "none", nr or "none")
        if r is None: raise RuntimeError("the lock write was not accepted")
        if band:
            since, deadline = time.time(), time.monotonic() + DEAD_S
            dev.ready(timeout=DEAD_S)
            if not brain.wait_online(timeout=max(0, deadline - time.monotonic()), since=since):
                res.update(failure="no_internet", error="no internet within %g s" % DEAD_S)
        else:
            _wait_link()
        if not res.get("failure"):
            warmed_nr = _warm_low(res, profile) if nr_band and profile else None
            history = list(brain.HIST)
            mark = history[-1] if history else None
            target_lte = brain.anchor_key(lte)
            target_nr = brain.anchor_key(nr) if nr and nr != "none" else None

            def seen_pair():
                if warmed_nr:
                    return True
                if not nr_band:
                    return _has5g(mark)
                return any(row.get("lte_cell") == target_lte and _real_nr(row, nr_band)
                           and (not target_nr or row["nr_cell"] == target_nr) for row in brain.samples_since(mark))

            wake_for = FIVE_G_WAIT_S + WAKE_LEAD_S + PROBE_S + (480 if compare else 5)
            threading.Thread(target=brain.wake, kwargs={"seconds": wake_for, "stop": stop},
                             daemon=True).start()
            deadline = time.monotonic() + FIVE_G_WAIT_S
            while not seen_pair():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(2, remaining))
            if band and not seen_pair():
                label = "n28 5G" if nr_band else "5G"
                if target_nr and nr_band:
                    label += " " + nr
                rows = brain.samples_since(mark)
                attached = any(row.get("lte_cell") == target_lte for row in rows)
                detail = "LTE %s attached" % lte if attached else "LTE %s not observed during wake" % lte
                res.update(failure="no_5g", error="%s; no %s within %g s" % (detail, label, FIVE_G_WAIT_S))
                if nr_band:
                    res["wake_radio"] = rows
            else:
                if not warmed_nr:
                    time.sleep(WAKE_LEAD_S)
                antennas = getattr(dev, "antennas", None)
                antenna_mode = None
                if antennas and not nr_band:
                    try:
                        antenna_mode = res["antenna_mode"] = antennas.verify_morning()
                    except Exception as exc:
                        res["antenna_mode_error"] = str(exc)
                        raise RuntimeError("morning antenna mode unverified: %s" % exc) from exc
                probe_start = time.time()
                net = brain.internet_test(seconds=PROBE_S)
                if antenna_mode and antenna_mode["boot"] != acs.st["boots"]:
                    antenna_mode["verified"] = False
                    res["antenna_mode_error"] = "modem rebooted during the probe"
                    raise RuntimeError(res["antenna_mode_error"])
                history = list(brain.HIST)
                h = brain.row_health(history[-1] if history else {})
                res.update({"ok": net.get("down_mbps") is not None or net.get("call_ok") == 1, "down_mbps": net.get("down_mbps"),
                            "call_ok": net.get("call_ok"), "loss_pct": net.get("loss_pct"), "lat_p95": net.get("lat_p95"),
                            "jitter_ms": net.get("jitter_ms"), "health": h, "has5g": seen_pair()})
                if res["down_mbps"] is None:
                    res["https_error"] = net.get("error") or "HTTPS probe returned no download rate"
                if band and not res["ok"] and brain.f(res["loss_pct"]) == 100:
                    res.update(failure="no_internet", error="no internet during the probe")
                seen = [_real_nr(row, nr_band) for row in brain.samples_since(mark)]
                res["observed_nr"] = next((nr for nr in reversed(seen) if nr), "")
                if profile and profile.low_band(lte) and res["ok"]:
                    proof = profile.transfer_evidence([(probe_start, time.time())], profile.snapshot()["raw"], lte, nr,
                                                      nr_band, expected_nr=warmed_nr)
                    res["probe_radio"] = proof
                    if not proof["verified"]:
                        raise RuntimeError("no confirmed low-band serving pair during probe")
                    res["observed_nr"] = proof["observations"][-1]["nr_cell"]
                if compare and res["ok"]:
                    _record_load(res, mark, nr_band)
                    if antenna_mode and antenna_mode["boot"] != acs.st["boots"]:
                        antenna_mode["verified"] = False
                        res["antenna_mode_error"] = "modem rebooted during the comparison"
                        raise RuntimeError(res["antenna_mode_error"])
    except Exception as e:
        res.update(ok=False, error=str(e))
    finally:
        stop.set()
    res["seconds"] = round(time.time() - t0, 1); st["legs"].append(res)
    if not compare:
        _note_reachability(res)
    https_down = res.get("down_mbps")
    https = "HTTPS down=%s" % (https_down if https_down is not None else "unavailable")
    if res.get("https_error"):
        https += " (%s)" % res["https_error"]
    load = ""
    if compare:
        down, up = res.get("load_down_mbps"), res.get("load_up_mbps")
        load = " | iperf3 down=%s up=%s" % (down, up) if down is not None and up is not None else " | iperf3 unavailable"
    log(("morning %s: ok=%s 5g=%s %s call=%s p95=%s%s %s" % (
        name, res["ok"], res["has5g"], https, res.get("call_ok"), res.get("lat_p95"), load, res["error"])).rstrip())
    if profile and state().get("profile_rollback"):
        raise RuntimeError("%s: %s; ending the run to restore the original configuration" % (name, res["error"]))
    return res


def _discover(band):
    """Discover the serving cell and ranked backups on one band, then restore the prior list."""
    out = {"band": band, "serving": "", "candidates": [], "nr": "", "error": ""}
    profile = getattr(devices.current(), "profile", None)
    prior = profile.snapshot()["raw"][profile.BAND] if profile else bandscan.ALL
    try:
        if profile: state({"profile_write": {"LockLTEBand": band}})
        if not acs.tr069_set([(bandscan.PARAM, "xsd:string", band)]):
            out["error"] = "LockLTEBand=%s not accepted" % band
            return out
        history = list(brain.HIST)
        mark = history[-1] if history else None
        deadline = time.monotonic() + BAND_WAIT_S
        while True:
            rows = [r for r in brain.samples_since(mark) if bandscan.confirmed(r, band, require_mask=True)]
            remaining = deadline - time.monotonic()
            if len(rows) >= 2 or remaining <= 0:
                break
            time.sleep(min(2, remaining))
        s = bandscan.summarize(band, rows)
        out.update(serving=s.get("serving") or "", nr=s.get("nr_cell") or "", rsrp=s.get("rsrp"), sinr=s.get("sinr"),
                   samples=len(rows), rejected={})
        first = _spec(out["serving"])
        if first:
            strength = brain.f(s.get("rsrp"))
            if strength is None or not MIN_RSRP <= strength <= -40:
                out["rejected"][first] = "serving signal missing or below floor"
        dead = _dead_cells()
        signals = {}
        for row in rows:
            current = {}
            for token in (row.get("lte_neighbours") or "").split():
                cell, _, reading = token.partition(":")
                v = brain.f(reading)
                spec = _spec(cell)
                if (spec and spec != first and v is not None
                        and bandscan.cell_on_band(cell, band)):
                    current[spec] = max(v, current.get(spec, v))
            for spec, v in current.items():
                signals.setdefault(spec, []).append(v)
        nb = {}
        for spec, readings in signals.items():
            usable = [v for v in readings if MIN_RSRP <= v <= -40]
            if spec in dead:
                out["rejected"][spec] = "recent reachability failures"
            elif len(usable) < 2:
                out["rejected"][spec] = ("one usable reading" if usable else
                                         "invalid RSRP" if any(v > -40 for v in readings) else "below signal floor")
            elif band in LOW:
                pair = _best_pair(spec, nr_band="n28")
                if pair:
                    key = brain.anchor_key(spec)
                    minutes = (brain.REG["nr"].get(pair[1], {}).get("anchors", {}).get(key) or {}).get("min", 0)
                    nb[spec] = (pair[0][0], minutes, pair[0], min(usable))
                else:
                    out["rejected"][spec] = "no recorded n28 pair"
            else:
                pair = _best_pair(spec, good_only=True)
                nb[spec] = (pair[0] if pair else (), min(usable))
        out["signals"] = signals
        out["candidates"] = sorted(nb, key=nb.get, reverse=True)[:CANDIDATES]
        return out
    except Exception as e:
        out["error"] = repr(e); return out
    finally:
        if profile: state({"profile_write": {"LockLTEBand": prior}})
        if not acs.tr069_set([(bandscan.PARAM, "xsd:string", prior)]):
            raise RuntimeError("discovery band restore refused")
        if profile:
            profile.wait_band(prior, time.monotonic())
            state({"profile_write": None})


def _healthy(leg):
    call = 100 if leg.get("call_ok") == 1 else (leg.get("call_ok") or 0)
    return leg.get("ok") and leg.get("has5g") and call >= LOW_CALL and (brain.f(leg.get("down_mbps")) or 0) >= LOW_DOWN


def _fallback(baseline, highs):
    legs = [baseline, *highs]
    if any(_healthy(leg) for leg in legs):
        return None
    return "high degraded" if any(leg.get("ok") and leg.get("has5g") for leg in legs) else "high unavailable"


def _test_band(discovery, compare):
    band = discovery["band"]
    st["stage"] = "band %s" % band
    first = _spec(discovery["serving"])
    low = band in LOW
    why = (discovery.get("rejected") or {}).get(first)
    if first in _dead_cells():
        why = "recent reachability failures"
    if not why:
        nr = "none" if low else _spec(discovery["nr"]) or _nr_for(first)
        _leg("B%s #1 %s" % (band, discovery["serving"]), first, nr, band=band, role="first", compare=compare)
    else:
        log("morning B%s: skipped %s (%s)" % (band, first, why))
    for slot, cell in enumerate(discovery["candidates"], start=2):
        nr = "none" if low else _nr_for(cell)
        leg = _leg("B%s #%d %s" % (band, slot, cell), cell, nr, band=band, role="second", compare=compare)
        loaded = all(brain.f(leg.get(k)) is not None for k in ("load_down_mbps", "load_up_mbps", "load_call_pct",
                                                               "load_loss_pct", "load_p95", "load_jitter_ms"))
        if leg["ok"] and leg["has5g"] and (not compare or loaded):
            break


def _decision(legs):
    measured = []
    baseline_how = next((leg.get("load_how") for leg in legs if leg["lte"] == "none"), None)
    for leg in legs:
        if leg.get("antenna_mode_error") or not leg.get("load_s"):
            continue
        nr = leg.get("observed_nr") or ""
        row = dict(lock=leg["lte"], minutes=leg["radio_samples"], on_target_pct=leg["on_target_pct"],
                   nr_arfcn=nr.split("/", 1)[0], load_s=leg["load_s"], load_method=leg["load_method"],
                   down_mbps=leg.get("load_down_mbps"), up_mbps=leg.get("load_up_mbps"), call_ok_pct=leg.get("load_call_pct"),
                   loss_pct=leg.get("load_loss_pct"), lat_p95=leg.get("load_p95"), jitter_ms=leg.get("load_jitter_ms"))
        score = brain.lt_score(row)
        leg["score"] = None if score["partial"] else score["score"]
        leg["comparable_server"] = bool(baseline_how and leg.get("load_how") == baseline_how)
        radio_agrees = leg["transfer_radio_samples"] == leg["transfer_radio_matches"]
        if leg["lte"] == "none" or (leg["comparable_server"] and leg["radio_samples"] >= 2
                                    and (leg.get("nr_pct") or 0) >= 80 and radio_agrees):
            leg["load_comparable"] = leg["score"] is not None
            measured.append(row)
    best, base, gain = brain.loaded_anchor_result(measured)
    return {"lte": best["lock"] if best else None, "score": best["score"] if best else None,
            "baseline": base["score"] if base else None, "gain": gain,
            "verdict": "candidate" if gain else "unlocked" if base and best else "incomplete"}


def _ab(legs):
    """A = the best measured serving leg, B = the best measured backup. A band's only measured leg is eligible for A.
    -> (a, b, best, restored_lte, restored_nr)."""
    per, firsts, seconds = {}, [], []
    for l in legs:
        if l.get("band") and l.get("role") in ("first", "second") and l.get("ok") and l.get("has5g"):
            per.setdefault(l["band"], {}).setdefault(l["role"], l)
    for d in per.values():
        first = d.get("first") or d.get("second")          # the band's primary 5G: its #1, else its #2
        second = d.get("second") if d.get("first") else None
        if first: firsts.append(first)
        if second: seconds.append(second)
    a = max(firsts, key=_q) if firsts else None
    b = max((x for x in seconds if not a or x.get("lte") != a.get("lte")), key=_q) if seconds else None
    nl = next((l for l in legs if l.get("leg") == "no lock"), None)
    cand = [x for x in (nl, a, b) if x and x.get("ok") and x.get("has5g")]
    if not cand: return a, b, None, "none", "none"
    best = max(cand, key=_q)
    # max keeps the first eligible leg on a tie: the unlocked baseline when it qualified.
    if best is nl or (nl and nl.get("ok") and not brain.clearly_better(_q(best), _q(nl))):
        return a, b, nl, "none", "none"
    return a, b, best, best.get("lte"), _spec(best.get("observed_nr")) or best.get("nr")


def _msg(r):
    out = ["Morning check (%s): %s" % (r.get("trigger"), r.get("verdict"))]
    for x in r.get("legs") or []:
        if not x.get("ok"):
            out.append("%s: %s" % (x.get("leg"), x.get("error") or "no internet")); continue
        down = x.get("down_mbps")
        https = "HTTPS %s Mbit/s" % down if down is not None else "HTTPS download unavailable"
        out.append("%s: %s, call %s, p95 %s, jitter %s, %s%s" % (
            x.get("leg"), https, x.get("call_ok"), x.get("lat_p95"), x.get("jitter_ms"),
            "5G" if x.get("has5g") else "no 5G", ", up %s" % x.get("iperf_up") if x.get("iperf_up") else ""))
    a, b = r.get("a") or {}, r.get("b") or {}
    out.append("A: %s (5G %s), B: %s (5G %s)" % (a.get("lte", "-"), "yes" if a.get("has5g") else "no",
                                                 b.get("lte", "-"), "yes" if b.get("has5g") else "no"))
    rs = r.get("restored") or {}
    out.append("restored: %s / %s%s" % (rs.get("lte"), rs.get("nr"), ", up %s Mbit/s" % r.get("up") if r.get("up") else ""))
    return "\n".join(out)


def _managed_locks():
    saved = state()
    locks = []
    for key in ("lte_lock", "nr_lock"):
        lock = saved.get(key) or {}
        locks.append(lock["cell"] if lock.get("state") == "active" else "none")
    return locks


def _run(low=None, notify=True, trigger="manual", apply=True, bands=None, cell=None, nr=None):
    t0 = time.time(); st["legs"] = []
    a = b = best = decision = fallback = up = None
    restored = {}
    discoveries = []
    complete = False
    scope = None
    profile = getattr(devices.current(), "profile", None)
    highs = [b for b in BANDS if bands is None or b in bands]
    lows = [b for b in LOW if bands is None or b in bands]
    requested_bands = highs + (lows if low is True else [])
    try:
        if profile:
            scope = profile.begin_scope("morning")
        if cell and (not captest.sqm.URL or not captest.wait_lan_quiet()):
            raise RuntimeError("local traffic before comparison")
        baseline = _leg("no lock", "none", "none", compare=not apply)
        if baseline.get("antenna_mode_error"):
            raise RuntimeError("unverified antenna mode on the unlocked baseline")
        saturday = low is None and LOW_SAT and time.localtime().tm_wday == 5
        look_low = low is True or saturday or (low is None and not _healthy(baseline))
        st["stage"] = "discover"
        if cell:
            st["stage"] = "band " + lows[0]
            if not captest.wait_lan_quiet(): raise RuntimeError("local traffic before low leg")
            _leg("B%s %s" % (lows[0], cell), cell, nr or "none", band=lows[0], role="first", compare=True)
        requested_bands = highs + (lows if look_low else [])
        discovery_bands = [] if cell else dict.fromkeys(requested_bands)
        for band in discovery_bands:  # discovery needs the unlocked CPE
            d = _discover(band)
            discoveries.append(d)
            if d.get("error") or not d.get("serving"):
                log("morning B%s: %s" % (band, d.get("error") or "no serving cell"))
        usable = [d for d in discoveries if d.get("serving") and not d.get("error")]
        for d in usable:
            if d["band"] in highs:
                _test_band(d, not apply)
        if look_low:
            fallback = "manual" if low is True else "Saturday" if saturday else _fallback(baseline, st["legs"][1:])
        if fallback:
            for d in usable:
                if d["band"] not in LOW:
                    continue
                n = len(st["legs"])
                _test_band(d, not apply)
                if low is None and not saturday and any(_healthy(leg) for leg in st["legs"][n:]):
                    break
        a, b, best, rl, rn = _ab(st["legs"])
        complete = True
    except Exception as e:
        st["why"] = "%s failed: %s" % (st["why"], e); log("morning check failed: %s" % e)
    finally:
        st["stage"] = "restore"
        try:
            commit = apply and complete and best is not None
            lte, nr = (rl, rn) if commit else _managed_locks()
            if profile and not scope:
                raise RuntimeError("no configuration snapshot; no restore required")
            if scope and not commit:
                profile.finish_scope()
                lte, nr = [profile.spec(scope["raw"][k]) for k in (profile.LTE, profile.NR)]
            elif devices.current().locks_apply(lte, nr) is None:
                raise RuntimeError("restore write was not accepted")
            restored = {"lte": lte, "nr": nr}
            if commit:
                managed = {"lte_lock": ({"cell": lte, "state": "active", "reason": "morning: the best 5G"} if lte != "none" else None),
                       "nr_lock": ({"cell": nr, "state": "active", "reason": "morning: the best 5G"} if nr != "none" else None)}
                if scope:
                    if not brain.wait_online(timeout=90, since=time.time()): raise RuntimeError("selected profile offline")
                    profile.finish_scope(commit=managed)
                else: state(managed)
            _wait_link(120)
            if commit:
                _d, up, how = captest.measure(secs=UP_SECS)
                log("morning restore upload: %s" % how)
        except Exception as e:
            log("morning restore failed: %r" % e)
            st["why"] = "%s restore failed: %r" % (st["why"], e)
            complete = False
        legs = list(st["legs"])
        coverage = None
        if not apply:
            decision = _decision(legs)
            measured = {leg.get("band") for leg in legs if leg.get("load_comparable")}
            coverage = {"baseline": None in measured, "requested_bands": requested_bands,
                        "missing_bands": [band for band in requested_bands if band not in measured]}
        h = next((x.get("health") for x in legs if x.get("health")), {})
        verdict = ("modem good" if any(x.get("ok") for x in legs) else "modem bad" if h.get("sim") is False
                   else "inconclusive" if not apply or any(x.get("antenna_mode_error") for x in legs) else "provider issue")
        res = {"trigger": st["why"], "time": stamp(), "started": st["started"], "seconds": round(time.time() - t0, 1),
               "version": VERSION, "apply": apply, "complete": complete, "fallback": fallback, "discoveries": discoveries,
               "legs": legs, "a": a or {}, "b": b or {}, "decision": decision, "coverage": coverage, "restored": restored, "up": up,
               "any_5g": any(x.get("has5g") for x in legs), "verdict": verdict}
        st["result"] = res; state({"morning" if apply else "morning_trial": res})
        if notify:
            try: alert.report(_msg(res))
            except Exception as e: log("morning telegram failed: %r" % e)
        event({"event": "morning check" if apply else "morning comparison", "to": res["verdict"],
               "why": "%s: %d legs, restored %s/%s" % (res["trigger"], len(legs), restored.get("lte"), restored.get("nr"))})
        st["running"] = None; st["stage"] = "done"


def run(low=None, trigger="manual", notify=True, apply=True, bands=None, cell=None, nr=None):
    """Start the morning check -> (started, why). Busy while it runs (the guard/captest/night wait). A manual run counts as the
    day's check (no scheduled catch-up right after it)."""
    with common.test_start_lock:
        busy = common.busy()
        if busy: return False, "busy: %s" % busy
        if (state().get("quality_ab") or {}).get("phase") == "trial":
            return False, "busy: quality A/B trial"
        if apply:
            state({"morning_last": time.strftime("%Y-%m-%d", time.localtime())})
        st.update(running=trigger, result=None, started=stamp(), legs=[], why=trigger, stage="start")
        threading.Thread(target=_run, args=(low, notify, trigger, apply, bands, cell, nr), daemon=True).start()
    return True, trigger


def status():
    """Read-only: the running check or the last result (for GET /morning)."""
    saved = state()
    return {"running": st["running"], "stage": st["stage"], "trigger": st["why"],
            "result": saved.get("morning"), "trial": saved.get("morning_trial")}


def maybe_morning(row=None, now=None):
    """With each sample: run the check once a day at/after MORNING (a missed morning runs as the first thing: `morning_last` is
    only set when it starts). No idle wait - it reboots, so it is for the gym window."""
    now = now or time.time()
    if not MORNING or common.busy() or not common.clock_ok(): return False
    hhmm = time.strftime("%H:%M", time.localtime(now))
    if hhmm < MORNING or (MORNING_END and hhmm >= MORNING_END): return False
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if state().get("morning_last") == day: return False
    started, _ = run(low=None, trigger="scheduled")
    if started:
        log("morning check: starting (MORNING=%s)" % MORNING)
    return started
