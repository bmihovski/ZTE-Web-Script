#!/usr/bin/env python3
"""Fill keeper/sites.json from what CellMapper already loaded in your browser - no requests of its own, no login.

1. On cellmapper.net (Yettel = 284 / 05) open the map around home and click each tower you care about
   (eNB 1243, 1554, 1072, and the ones carrying 434 / 66 / 370), so the page loads their details.
2. DevTools (Cmd+Opt+I) -> Network -> right-click a request -> "Save all as HAR with content" -> e.g. cellmapper.har
3. python3 fill_sites.py cellmapper.har [more.har|response.json ...]          (prints what it found, then updates sites.json)
   python3 fill_sites.py --dry-run cellmapper.har                             (only prints)
   python3 fill_sites.py --keys cellmapper.har                                (shows the JSON field names, if nothing is found)

Only response bodies of cellmapper URLs are read; a HAR also holds your cookies - delete it afterwards (it is git-ignored).
Field names are matched loosely (siteID / eNB, latitude / lat, PCI, bearing / azimuth, EARFCN / channel ...).
"""
import base64, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITES = os.path.join(HERE, "keeper", "sites.json")
ID = ("siteid", "site_id", "enb", "enbid", "gnb", "gnbid", "towerid", "tower_id", "nodeid")
LAT, LON = ("latitude", "lat"), ("longitude", "lon", "lng", "long")
PCI = ("pci", "physicalcellid")
AZ = ("bearing", "azimuth", "direction", "heading", "sectorbearing")
CH = ("earfcn", "dl_earfcn", "arfcn", "nrarfcn", "channel", "dlchannel", "uarfcn")
RAT = ("rat", "networktype", "technology", "type", "system")


def pick(d, names):
    for k, v in d.items():
        if k.lower().replace(" ", "") in names and v not in (None, "", []): return v
    return None


def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def bodies(path):
    """JSON bodies from a HAR (cellmapper responses only) or a plain JSON file"""
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict) and "log" in data and "entries" in data.get("log", {}):
        for e in data["log"]["entries"]:
            if "cellmapper" not in e.get("request", {}).get("url", ""): continue
            c = e.get("response", {}).get("content", {}); t = c.get("text")
            if not t: continue
            if c.get("encoding") == "base64":
                try: t = base64.b64decode(t).decode("utf-8", "replace")
                except ValueError: continue
            try: yield e["request"]["url"], json.loads(t)
            except ValueError: pass
    else:
        yield path, data


def walk(o, parent_site=None, out=None, rat_hint=None):
    """collect sites {id: {...}} and cells; a cell is any dict with a PCI, attached to the nearest enclosing site or its own site id"""
    out = out if out is not None else {"sites": {}, "cells": []}
    if isinstance(o, list):
        for x in o: walk(x, parent_site, out, rat_hint)
    elif isinstance(o, dict):
        r = pick(o, RAT); rat_hint = str(r).upper() if r else rat_hint
        sid, lat, lon = pick(o, ID), num(pick(o, LAT)), num(pick(o, LON))
        site = parent_site
        if sid is not None and lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            sid = str(sid); site = out["sites"].setdefault(sid, {"enb": int(num(sid)) if num(sid) is not None else sid, "lat": lat, "lon": lon, "cells": []})
        pci = num(pick(o, PCI))
        if pci is not None:
            ch = pick(o, CH)
            out["cells"].append({"site": site["enb"] if site else (str(sid) if sid is not None else None),
                                 "rat": "NR" if rat_hint and "NR" in rat_hint else ("LTE" if rat_hint and "LTE" in rat_hint else None),
                                 "pci": int(pci), "channel": [int(num(c)) for c in (ch if isinstance(ch, list) else [ch]) if num(c) is not None],
                                 "azimuth": num(pick(o, AZ))})
        for v in o.values():
            if isinstance(v, (dict, list)): walk(v, site, out, rat_hint)
    return out


def keys(o, path="", seen=None):
    seen = seen if seen is not None else set()
    if isinstance(o, dict):
        for k, v in o.items():
            p = path + "." + k
            if p not in seen: seen.add(p); print("  ", p, "=", repr(v)[:60] if not isinstance(v, (dict, list)) else type(v).__name__)
            keys(v, p, seen)
    elif isinstance(o, list) and o:
        keys(o[0], path + "[]", seen)


def geo(lat1, lon1, lat2, lon2):
    """distance (m) and initial bearing (deg) from point 1 to point 2"""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * 6371000 * math.asin(math.sqrt(a))
    b = math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360
    return d, b


def merge(found, cfg):
    """found sites (with their cells) replace the template entries with the same eNB; other entries stay"""
    by = {}
    for s in found["sites"].values(): by[str(s["enb"])] = dict(s, cells=[])
    for c in found["cells"]:
        s = by.get(str(c["site"]))
        if not s: continue
        key = (c["rat"], c["pci"], c["azimuth"])
        old = next((x for x in s["cells"] if (x["rat"], x["pci"], x.get("azimuth")) == key), None)
        chans = sorted(set(c["channel"]) | set((old or {}).get("earfcn" if c["rat"] != "NR" else "arfcn", [])))
        cell = {"rat": c["rat"], "pci": c["pci"], ("arfcn" if c["rat"] == "NR" else "earfcn"): chans, "azimuth": c["azimuth"]}
        if old: s["cells"][s["cells"].index(old)] = cell
        else: s["cells"].append(cell)
    sites = [x for x in cfg.get("sites", []) if str(x.get("enb")) not in by] + sorted(by.values(), key=lambda s: str(s["enb"]))
    return dict(cfg, sites=[s for s in sites if s.get("lat") is not None or s.get("enb") not in (None, "")])


def show(cfg):
    z = cfg.get("zte") or {}
    print("site      lat        lon        dist m  point ZTE to  | cells: RAT PCI channels azimuth (off-axis from the ZTE: front <= 60 deg)")
    for s in cfg["sites"]:
        if s.get("lat") is None: print("%-8s (no position)" % s.get("enb")); continue
        d, b = geo(z["lat"], z["lon"], s["lat"], s["lon"]); back = (b + 180) % 360
        cells = []
        for c in s.get("cells", []):
            if c.get("pci") is None: continue
            off = "" if c.get("azimuth") is None else " off %d %s" % (abs((back - c["azimuth"] + 180) % 360 - 180),
                                                                       "front" if abs((back - c["azimuth"] + 180) % 360 - 180) <= 60 else "BEHIND")
            cells.append("%s %s %s az %s%s" % (c.get("rat"), c["pci"], c.get("earfcn") or c.get("arfcn"), c.get("azimuth"), off))
        print("%-8s %.6f %.6f %7.0f  %5.1f deg     | %s" % (s["enb"], s["lat"], s["lon"], d, b, "; ".join(cells) or "-"))


def main(argv):
    dry, show_keys = "--dry-run" in argv, "--keys" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files: print(__doc__); return 1
    found = {"sites": {}, "cells": []}
    for f in files:
        for url, body in bodies(f):
            if show_keys: print(url); keys(body)
            walk(body, None, found)
    print("found %d sites, %d cells" % (len(found["sites"]), len(found["cells"])))
    cfg = json.load(open(SITES)) if os.path.exists(SITES) else {"zte": {"lat": 42.626118, "lon": 23.384814}, "sites": []}
    new = merge(found, cfg)
    show(new)
    if not found["sites"]:
        print("nothing recognised - run again with --keys and send me the field names it prints"); return 2
    if not dry:
        json.dump(new, open(SITES, "w"), indent=1); print("updated", SITES)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
