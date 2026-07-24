# Station coordinates (external data)

`INLEES.NET` contains **no coordinates** — the 1990 DOS planner never drew a map.
The `stop_lat` / `stop_lon` in the GTFS feed are therefore joined in from outside
the archival binary. Everything here is third-party data, kept separate from the
decoded timetable and traceable per station.

All 469 stations are covered by two sources, applied in this order:

### 1. `station_coords.csv` — 362 stations
Matched by NS station **code** (and a few by name) against the open
**Rijden de Treinen** Dutch railway-stations dataset
(<https://www.rijdendetreinen.nl/open-data/treinstations>), which carries
`geo_lat` / `geo_lon` for current NL, and many neighbouring D/B/F, stations.

Columns: `idx, code, name, lat, lon, match, source_code`
(`match` = how it was linked: `code` or `name`; `source_code` = the code in the
source dataset).

### 2. `station_coords_wikipedia.csv` — 107 stations
The stations the rail dataset didn't cover: deeper-Germany/Belgium/France termini,
closed lines (Zoetermeer Stadslijn, Rotterdam Hofplein), Wadden ferry ports and
islands, and the bus-only stops. Resolved by **exact Wikipedia article title**
(rebuild with `python3 tools/geocode_wikipedia.py`) and validated to a per-country
bounding box. Text and coordinates from Wikipedia are CC BY-SA 4.0.

Columns: `idx, code, name, lat, lon, kind, wikidata, source_url`
- `kind`: `station` / `island` = the coordinate of that exact place;
  `place` = a town-level approximation (bus/ferry halts, a few closed light-rail
  stops), i.e. the coordinate of the town, not the exact stop.
- `wikidata`, `source_url`: the exact page each coordinate came from.

## Notes
- The nine Zoetermeer Stadslijn halts share the Zoetermeer station coordinate
  (town-level approximation).
- To regenerate: `station_coords.csv` was built by matching codes against the
  rail dataset; `station_coords_wikipedia.csv` by `tools/geocode_wikipedia.py`.
  `extract_reisplanner.py` joins both into `stops.txt` by station index.
