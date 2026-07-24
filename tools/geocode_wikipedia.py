"""Fill coordinates for the stations the rail dataset didn't cover, from Wikipedia.

Fulltext search returns too many wrong-but-plausible hits, so this resolves each
station by EXACT article title instead — "Station Amsterdam De Vlugtlaan",
"Hamburg Hauptbahnhof", the island/town name — trying the station article first
and the town as a fallback, across the relevant language wikis. A coordinate is
accepted only when it falls inside that station's country box; anything else is
left blank for manual review. Every accepted coordinate keeps its source page
URL and Wikidata id.

    python3 tools/geocode_wikipedia.py         # -> data/station_coords_wikipedia.csv

Needs network. One-off enrichment, not part of the CI build.
"""
import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIONS = os.path.join(REPO, "output", "90-91", "stations.csv")
RAIL = os.path.join(REPO, "data", "station_coords.csv")
OUT = os.path.join(REPO, "data", "station_coords_wikipedia.csv")
UA = "reisplanner-archeology/1.0 (https://github.com/joelhaasnoot/reisplanner-archeology)"

# per-country sanity box: (lat0, lat1, lon0, lon1)
BOX = {"nl": (50.7, 53.6, 3.3, 7.3), "d": (47.2, 55.1, 5.8, 15.1),
       "b": (49.4, 51.6, 2.5, 6.5), "f": (42.0, 51.2, -1.0, 8.4),
       "l": (49.4, 50.2, 5.7, 6.6)}

ISLANDS = {"amld": "Ameland", "tsl": "Terschelling", "tx": "Texel",
           "vld": "Vlieland", "smo": "Schiermonnikoog", "urk": "Urk"}
# abbreviation / place fixes (bus & ferry halts resolve to their place)
FIX = {"zoeterm.": "zoetermeer", "raamsdonksv.": "raamsdonksveer",
       "made g.schalckenstr.": "made", "utrecht streekbushaltes": "utrecht",
       "druten koekoeksnest": "druten", "perkpolderhaven": "perkpolder",
       "den helder haven": "den helder", "hoek van holland haven": "hoek van holland",
       "hoek van holland strand": "hoek van holland", "breskens haven": "breskens",
       "breskens boulevard": "breskens", "kruiningen haven": "kruiningen",
       "oosterhout busstation": "oosterhout", "oosterhout sterrenlaan": "oosterhout",
       "oosterhout zuiderhout": "oosterhout", "raamsdonksv.busstation": "raamsdonksveer",
       "raamsdonksv.essenboom": "raamsdonksveer", "raamsdonksv.keizersveer": "raamsdonksveer"}
PARTICLES = {"van", "de", "den", "der", "het", "aan", "op", "bij", "en", "ter",
             "te", "'t", "im", "an", "am"}

# Explicit article titles where the generated guess fails (umlauts, disambiguation
# pages, capitalised "De" in a street name, bus/ferry halts -> their town).
MANUAL = {
    "asdv": ("nl", "Station Amsterdam De Vlugtlaan"),
    "dulken": ("de", "Dülken"), "gentdp": ("nl", "Station Gent-Dampoort"),
    "gm": ("nl", "Gemert-Bakel"), "grv": ("en", "Grave, Netherlands"),
    "ibbenb": ("de", "Ibbenbüren"), "krb": ("de", "Kranenburg (Niederrhein)"),
    "kzd": ("nl", "Koog aan de Zaan"), "ldv": ("nl", "Station Voorburg"),
    "lohne": ("de", "Löhne"), "md": ("nl", "Made (Noord-Brabant)"),
    "niklaa": ("nl", "Station Sint-Niklaas"),
    "oosb": ("nl", "Oosterhout (Noord-Brabant)"),
    "ooss": ("nl", "Oosterhout (Noord-Brabant)"),
    "oosz": ("nl", "Oosterhout (Noord-Brabant)"),
    "quenti": ("fr", "Gare de Saint-Quentin"), "troisp": ("nl", "Station Trois-Ponts"),
    "vryb": ("nl", "Station Venray"),
}


def kind_of(title):
    return "station" if re.search(r"^(Station|Bahnhof|Gare)\b|Hauptbahnhof", title) \
        else "place"


def clean(name):
    n = name.lower().strip()
    n = re.sub(r"\s*\([a-z]\)$", "", n).strip()
    if n in FIX:
        return FIX[n]
    for k, v in FIX.items():
        if n.startswith(k):
            return v
    return n


def proper(s):
    ws = s.split()
    return " ".join(w if (i and w in PARTICLES) else w[:1].upper() + w[1:]
                    for i, w in enumerate(ws))


def candidates(code, name, tag):
    """-> list of (lang, title, kind)."""
    if code in MANUAL:
        lang, title = MANUAL[code]
        return [(lang, title, kind_of(title))]
    if code in ISLANDS:
        return [("nl", ISLANDS[code], "island")]
    P = proper(clean(name))
    if tag == "nl":
        return [("nl", f"Station {P}", "station"), ("en", f"{P} station", "station"),
                ("nl", P, "place")]
    if tag == "d":
        city = re.sub(r"\s*Hbf$", "", P, flags=re.I)
        return [("de", f"{city} Hauptbahnhof", "station"),
                ("en", f"{city} Hauptbahnhof", "station"),
                ("de", f"Bahnhof {P}", "station"),
                ("de", f"{P} Hauptbahnhof", "station"),
                ("en", f"{P} station", "station"),
                ("de", city, "place"), ("de", P, "place")]
    if tag == "b":
        return [("nl", f"Station {P}", "station"), ("en", f"{P} railway station", "station"),
                ("nl", P, "place")]
    if tag == "f":
        return [("fr", f"Gare de {P}", "station"), ("en", f"{P} station", "station"),
                ("fr", P, "place")]
    if tag == "l":
        return [("en", "Luxembourg railway station", "station"),
                ("en", "Luxembourg City", "place")]
    return [("en", P, "place")]


def api(lang, params):
    params = {**params, "format": "json", "formatversion": "2"}
    url = f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def lookup(lang, title):
    """Exact-title coordinate. -> (lat, lon, title, url, qid) or None."""
    try:
        j = api(lang, {"action": "query", "titles": title, "redirects": 1,
                       "prop": "coordinates|info|pageprops", "inprop": "url",
                       "ppprop": "wikibase_item"})
    except Exception:
        return None
    for p in (j.get("query") or {}).get("pages") or []:
        if p.get("missing"):
            continue
        for c in p.get("coordinates") or []:
            if c.get("lat") is not None:
                return (c["lat"], c["lon"], p.get("title"), p.get("fullurl"),
                        (p.get("pageprops") or {}).get("wikibase_item", ""))
    return None


def main():
    have = {r["idx"] for r in csv.DictReader(open(RAIL))}
    todo = [r for r in csv.DictReader(open(STATIONS)) if r["idx"] not in have]
    print(f"{len(todo)} stations to geocode via Wikipedia\n")

    out, miss = [], []
    for s in todo:
        m = re.search(r"\(([a-z])\)$", s["name"].strip())
        tag = m.group(1) if m else "nl"
        box = BOX.get(tag, BOX["nl"])
        result = None
        for lang, title, kind in candidates(s["code"], s["name"], tag):
            hit = lookup(lang, title)
            time.sleep(0.12)
            if not hit:
                continue
            lat, lon, rtitle, url, qid = hit
            if box[0] <= lat <= box[1] and box[2] <= lon <= box[3]:
                result = (lat, lon, rtitle, url, qid, kind)
                break
        if result:
            lat, lon, rtitle, url, qid, kind = result
            out.append([s["idx"], s["code"], s["name"], f"{lat:.6f}", f"{lon:.6f}",
                        kind, qid, url])
            flag = "" if kind in ("station", "island") else "  [place-approx]"
            print(f"  ok   {s['code']:7s} {s['name']:30s} -> {rtitle} ({lat:.4f},{lon:.4f}){flag}")
        else:
            miss.append(s)
            print(f"  MISS {s['code']:7s} {s['name']}")

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "code", "name", "lat", "lon", "kind", "wikidata", "source_url"])
        for r in sorted(out, key=lambda x: int(x[0])):
            w.writerow(r)
    st = sum(1 for r in out if r[5] in ("station", "island"))
    print(f"\nresolved {len(out)}/{len(todo)}  ({st} exact station/island, "
          f"{len(out)-st} place-approx); unresolved {len(miss)}")
    print(f"-> {OUT}")
    if miss:
        print("unresolved:", ", ".join(f"{s['code']}={s['name']}" for s in miss))


if __name__ == "__main__":
    main()
