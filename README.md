# NS Reisplanner 90/91 — reverse-engineering / digital archeology

Recovering the schedule data from **NS REISPLANNER** (© CVI Centrum voor Informatieverwerking
N.V., Utrecht 1990; written by Eduard & Wim Tulp), the Dutch Railways DOS trip planner.
Timetable validity: **27 May 1990 – 1 June 1991**.

The goal: decode the program's compiled binary schedule database (`INLEES.NET`) and the
program itself (`REISPLAN.EXE`) well enough to extract the timetable — stations, running-days,
fares, network topology — and emit it as CSV and a GTFS feed.

## Repository layout
Organised by **edition** (timetable year) so more can be added later.
```
├── extract_reisplanner.py   # one script: input/<edition>/INLEES.NET -> output/<edition>/
├── input/                   # source material (tracked), one folder per edition
│   └── 90-91/
│       ├── INLEES.NET       #   compiled schedule database
│       ├── REISPLAN.EXE     #   the DOS program (Turbo-C)
│       ├── NL_90_91.VKZ     #   24-byte companion record
│       └── screenshots/     #   emulator screenshots (visual record)
├── docs/                    # documentation
│   ├── FORMAT.md            #   the INLEES.NET file format
│   ├── BINARY.md            #   REISPLAN.EXE reverse-engineering notes
│   ├── PLAN.md              #   plan for the remaining work
│   └── gtfs.md              #   notes on the generated GTFS feed
├── fixtures/                # ground-truth validation data
│   └── train_8917_sun.csv   #   one fully verified train
└── output/                  # generated — gitignored, rebuild anytime
    └── 90-91/               #   per-edition outputs
        ├── *.csv            #     stations, topology, calendar, footnotes, fares, events
        └── gtfs/            #     the GTFS feed
```

## Usage
```
python3 extract_reisplanner.py          # defaults to edition 90-91
python3 extract_reisplanner.py 91-92    # once input/91-92/INLEES.NET exists
```
Regenerates everything under `output/<edition>/` from `input/<edition>/INLEES.NET`:

| output (`output/90-91/…`) | contents | status |
|--------|----------|--------|
| `stations.csv` | all **469** stations (code, name, board pointer) | ✅ |
| `network_segments.csv` | topology — **249 line segments**, 468/469 stations placed | ✅ |
| `holidays.csv` | the 15 holiday/shift dates (names annotated — see note) | ✅ |
| `footnotes.csv` | all 85 footnotes with exact running-date ranges | ✅ |
| `trips.csv` | one row per journey: origin/destination + exact running dates | ✅ |
| `fares.csv` | 36-band distance→fare table (enkele reis / retour, 2e+1e, normal+reduced) | ✅ |
| `season_tickets.csv` | 13-band week-/maand-/jeugdmaandtrajectkaart prices (stored, not derived) | ✅ |
| `transfers.csv` | 1,418 minimum transfer times between train pairs at 49 junctions | ✅ |
| `connection_events.csv` | all 21,126 departure/arrival events (time, days, footnote, ref) | ✅ **unlabelled** |
| `timetable.csv` | the **full decoded timetable** — 6,729 journeys (incl. 223 ferry + 257 bus), 52,613 stops | ✅ |
| `gtfs/` | GTFS feed: 469 stops, 971 routes, 6,729 trips, 52,613 stop_times, calendar + footnote exceptions, fares | ✅ |

*The reverse-engineered section offsets in `extract_reisplanner.py` are for the 90-91 edition;
another edition may need them re-derived (see `docs/FORMAT.md`).*

**The timetable is fully decoded.** The last gap — attaching a *station* to each departure —
was closed by disassembling the search engine's node walker (`0x6ae6`) and body parser
(`0x6889`); `tools/decode_timetable.py` reads the records directly, no traversal needed.
Validated two ways: the golden trip `fixtures/train_8917_sun.csv` reproduces 8/8 stops, and
`tools/verify_query.py` runs real queries inside the emulator while `tools/compare_screen.py`
checks the answers — **45/45 connections across eleven queries on six dates**, times and exact
running dates (footnotes included), across train, ferry and bus legs. See **`docs/BINARY.md`**.

## Source & provenance
- The 1990 software (in `input/90-91/`) is © CVI N.V. / N.V. Nederlandse Spoorwegen, from the
  Internet Archive; included only as the subject of personal archival research.
- Holiday **dates** are decoded from the file; the holiday **names** in `holidays.csv` are
  annotated from known Dutch 1990–91 holidays, not stored in the data.
- Season-ticket prices are not in the data — the program derives them (weekly = retour×4,
  monthly = retour×15, youth-monthly = monthly×0.8).
- Sample GTFS trips are validated against the emulator's own output; a full-network trip set
  awaits the work in `docs/PLAN.md`.

## Tooling
Python + [Capstone](https://www.capstone-engine.org/) (16-bit disassembly). Emulator-driving
during research used DOSBox 0.74 + osascript (macOS).
