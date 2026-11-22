"""A/B candidates (2026-11-21): the ordered score dict `{score: [cells]}` of every measured option, and the A/B pair from it.

The pair rule: the top score's list if it holds more than one cell -> both (A = the first, B = the second); else the first of the
top + the first of the next score (if any); a single cell total -> A only, no B (the network is completely limited). A is always the
**max scored** option, never the previous lock or a fixed order.

High: the lock-tested anchors from `brain.anchor_ranking()` (nightly + daily; the band-scan cells have no lock-test score and are
excluded here - the daily/B1 promotion is a separate step). Low: the **newest band scan of each low band** (`LOW_BANDS` 8,20; the
serving cell + its n28 pair), scored by the call-ready (the single value the user asked for); ties keep both. The same band scan
also writes B1/B3/B7 - those are high bands and must never become the "low" leg.
"""
from keeper import brain, lowband


def _pair(d):
    keys = sorted(d, reverse=True)
    if not keys: return []
    first = list(d[keys[0]])
    if len(first) > 1: return first[:2]
    if len(keys) > 1 and d[keys[1]]: return [first[0], d[keys[1]][0]]
    return first[:1]


def high():
    """The A/B high pair from the lock-test scores: the max score first; empty when no scored anchor."""
    d = {}
    for r in (brain.anchor_ranking().get("ranked") or []):
        c, s = r.get("lock"), r.get("score")
        if not c or c == "none" or s is None or r.get("load_method") != brain.LTE_LOAD_METHOD or not (brain.f(r.get("load_s")) or 0) > 0: continue
        d.setdefault(round(float(s), 1), []).append(c)
    for k in d: d[k] = list(dict.fromkeys(d[k]))
    return _pair(d)


def low():
    """The A/B low pairs: the newest band scan per low band (B8/B20, `lowband.latest()`), the max call-ready first; ties keep both."""
    d = {}
    for r in lowband.latest():
        s, n = r.get("serving") or "", r.get("nr_cell") or ""
        if "/" not in s or brain.nr_band_of(n.split("/", 1)[0]) != "n28": continue
        try: q = float(r.get("call_ok"))
        except (TypeError, ValueError): continue
        spec = "%s@%s" % (s.split("/")[1], s.split("/")[0])
        nspec = ("%s@%s" % (n.split("/")[1], n.split("/")[0])) if "/" in n else "none"
        d.setdefault(round(q, 1), []).append((spec, nspec))
    for k in d: d[k] = list(dict.fromkeys(d[k]))
    return _pair(d)
