# NS Reisplanner 90/91 — `INLEES.NET` format (reverse-engineered)

**Program:** NS REISPLANNER, © CVI Centrum voor Informatieverwerking N.V., Utrecht 1990.
Authors: Eduard Tulp & Wim Tulp. Compiled with **Turbo-C (large model)** — see `BINARY.md`.
Timetable validity: **27 May 1990 – 1 June 1991**.

The archive holds three files:
| file | role |
|------|------|
| `REISPLAN.EXE` | the DOS program (93 KB, unpacked Turbo-C) |
| `INLEES.NET` | the schedule database — "inlees" = *read-in* (252 KB). All data lives here. |
| `NL_90_91.VKZ` | 24-byte pointer/config record (references "tilburg west"), not yet decoded |

The file layout below was recovered authoritatively by reverse-engineering the EXE's
**load routine** (it reads a header of section sizes, then reads each section into its own
buffer). Every structure was cross-checked against the emulator and against the data.

## Overall structure — header + sized sections
Data begins at file offset **`0x38`**. The header (`0x00–0x37`) holds section sizes;
each section is read into a separate memory buffer.

| section | file range | size | contents |
|---------|-----------|------|----------|
| header | `0x000000–0x000037` | 56 B | section sizes (see below) |
| **A** — connection graph | `0x000038–0x0313a2` | 201578 | all boards, as 6-byte nodes |
| **B** — aux pointer lists | `0x0313a2–0x033540` | 8606 | sparse per-station secondary pointers |
| **C** — station name/code records | `0x033540–0x037554` | 16400 | variable-length text records |
| **D** — region/route index | `0x037554–0x037e2c` | 2264 | 8-byte records (station-index ranges) |
| **station master table** | `0x037e2c–0x03bc76` | 469×34 | one 34-byte record per station |
| **T1 — footnote bitmaps** | `0x03bc76–0x03cc76` | 4096 | 85 × 47-byte date bitmaps |
| T2 | `0x03cc76–0x03d05e` | 1000 | (role open) |
| **date calendar** | `0x03d05e–0x03d344` | 371×2 | per-date day-type (`0x32`=371 days) |
| **fare table** | `0x03d344–0x03d5cc` | 36×18 | distance → 8 single/return fares (`0x34`=36) |
| 14×18 / 35×6 / 3×6 | `0x03d5cc–EOF` | — | small tables (`0x36`=14,`0x38`=35) |

### Header fields (little-endian, as the loader reads them)
| offset | type | value | meaning |
|--------|------|-------|---------|
| `0x20` | u16 | **469** | station / record count |
| `0x22` | u32 | 201578 | section A size |
| `0x26` | u32 | 8606 | section B size |
| `0x2a` | u32 | 16400 | section C size |
| `0x2e` | u32 | 2264 | section D size |
| `0x32`,`0x34`,`0x36`,`0x38`,`0x3a` | u16 | 371,36,14,35,40 | structure params |

## Station master table — ✅ the master index (469 stations)
`469` fixed **34-byte** records at `0x37e2c`, **sorted alphabetically by name** (record 0
= *aachen hbf (d)*, 255 = *leeuwarden*, 468 = *zwaagwesteinde*). This is the true station
list — the earlier 455-name figure was an incomplete text scan; **the correct count is 469**
(the 14 extra include Schiphol, Enkhuizen, Lelystad Centrum, Maastricht Randwyck, …).

Three u32 pointer fields per record:
| field offset | points into | meaning |
|--------------|-------------|---------|
| **`+22`** | section **A** | the station's **header** block (monotonic 1…201475) |
| **`+26`** | section **C** | the station's **name/code** record |
| **`+30`** | section **B** | secondary pointer, **0 for ~90%** (role partly open) |

Small u16 count fields sit at `+2` and `+12`.

⚠️ **`+22` points to a per-station *header*** (a small block with platform-adjacency
data, ending in `ff ff`), **not directly to the departure board.** For some stations the
board nodes sit right after the header; for ~half they do not (211/469 headers have no
adjacent nodes). The **header → departure-board link is still open**, so board nodes
cannot yet be attributed to stations network-wide (see "Still open").

→ Extracted to `output/90-91/stations.csv` (idx, code, name, board offset).

## Section C — station name/code records
Variable-length, packed text records (pointed to by station field `+26`). Each holds the
official NS **code** and the **display name**; the leading article is moved to the end in
parentheses (`'s-Hertogenbosch` → `hertogenbosch ('s)`, `'t Harde` → `harde ('t)`).
Codes are the same NS abbreviations still in use (`ah`=Arnhem, `asd`=Amsterdam, `lw`=Leeuwarden).
67+ records are German `(d)` / Belgian `(b)` border stations.

## Section A — the boards (6-byte nodes) ✅ node format decoded & validated
The connection graph is a sea of fixed **6-byte nodes** (departure/arrival boards, grouped
by line and delimited by `fe ff`/`ff ff`). The **node format** is fully decoded and
ground-truth-validated; what is *not* solved is reliably mapping every node to its station
(the header→board link above).

```
byte 0 :  footnote  0 = none (91%); 1–85 = "voetnoot" exception code
                    (holiday/seasonal/school-vacation; footnote-definition
                    table not yet decoded)
byte 1 :  days      8-bit validity: bit0=Mon … bit6=Sun, bit7=holidays.
                    0x7f=daily, 0x3f=Mon–Sat, 0x1f=weekdays, 0xff=daily+holidays
byte 2-3: ref  u16  train identifier (see below) — an index, not a byte offset
byte 4-5: time u16  MINUTES SINCE MIDNIGHT
```

A board holds a station's departure and arrival events. Boards for one line share a `ref`
sequence, and are delimited internally by `fe ff` (sub) / `ff ff` (end).

**Ground-truth validation (DOSBox, Leeuwarden→Stavoren, Sun 27 May 1990):** train **8917**
(dep 8:22 / arr Stavoren 9:11) and its siblings match the boards to the minute. Aachen
Hbf's board decodes to a sparse 2-hourly international service (10:04, 12:04, 16:04 …),
exactly right. Decoding all ~21 000 nodes as minutes-since-midnight yields a textbook daily
rail curve.

## `ref` (node bytes 2-3) — train linkage ⚠️ partial
`ref` is a **train identifier**: a train's stops at successive stations share the same
`ref` within a storage segment (verified — train 8917 = `ref=0x1c96` at Mantgum/Sneek/IJlst).
It **steps by 4** like NS train numbers, and is consumed by the search as an *index*, not a
plain byte offset — so it is segment-local and reused across the file. Fully pinning its
target (the train-definition record) needs the search routine, which was not decoded.
Practical consequence: journeys can be chained **segment-by-segment via shared `ref`**, or
by **time-matching** (proven on Leeuwarden–Stavoren).

## Section D — region/route index
8-byte records whose fields are **contiguous ranges of station indices** (`[0,54]`,
`[56,79]`, `[81,157]`, …) plus pointer offsets — a spatial/tree search structure the
planner uses to narrow down stations. (Not distances.)

## Calendar / running-days — ✅ FULLY decoded
Three layers, all cracked and validated:

1. **Weekly pattern** — node byte 1 (bit0=Mon … bit6=Sun, bit7=holidays). `0x7f`=daily,
   `0x3f`=Mon–Sat, `0x1f`=weekdays. Validated (train 8917 = `0x7f`, ran Sun 27 May 1990).
2. **Date calendar** — 371×2 table at `0x3d05e`: maps every date in the validity window to
   its service day-type (day-of-week bit in the high byte, bits 8–14). **Public holidays are
   folded to Sunday** (and the day-after shifted to Monday). The dates match the exact
   1990–91 Dutch holidays — Whit Monday, Christmas, New Year, Easter Monday, **Koninginnedag
   (30 Apr)**, Ascension, etc. *(The file stores only these dates; the holiday **names** in
   `holidays.csv` are annotated by the extractor from known Dutch holidays, not from the data.)*
3. **Footnote exceptions** — node byte 0 (0 = none for 91%; 1–85 = footnote code). Each
   footnote indexes a **371-bit date bitmap** in table **T1** (`0x3bc76`, 47 bytes/footnote)
   giving the *exact* dates that train runs. E.g. footnote 10 = school-holiday service
   (summer + February + Easter breaks). The bitmap *is* the validity — straight to GTFS
   `calendar_dates.txt`.

Global window: 27 May 1990 – 1 June 1991 (371 days).

## Deliverables produced
One script, `../extract_reisplanner.py`, regenerates everything under `../output/`:
- `output/90-91/stations.csv` — the 469-station master list (idx, code, name, header offset). ✅ reliable
- `output/90-91/holidays.csv` — the 15 holiday/shift dates from the date calendar. ✅ reliable
- `output/90-91/footnotes.csv` — all 85 footnotes with their exact running-date ranges. ✅ reliable
- `output/90-91/fares.csv` — the 36-band distance→fare table (8 single/return fares per band). ✅ reliable.
  Season tickets are **not stored** — the EXE derives them: weekly = retour×4, monthly =
  retour×15, youth-monthly = monthly×0.8 (verified against the emulator).
- `output/90-91/network_segments.csv` — the network topology: 249 line segments, 468/469 stations
  placed on their segment, decoded from the route-graph adjacency nodes (field[22]). ✅ reliable
- `output/90-91/connection_events.csv` — every section-A node (time, days, footnote, ref) **UNLABELLED**
  (no station, pending the header→board link).
- `output/90-91/gtfs/` — GTFS feed: all 469 stops, calendar + calendar_dates (incl. holidays), fares,
  and **validated** sample trips (train 8917 Leeuwarden–Stavoren + 5 mainline corridors). See `gtfs.md`.
- `../fixtures/train_8917_sun.csv` — one fully verified train.
- `BINARY.md` — the EXE reverse-engineering notes.

## Still open
1. **Header → departure-board link** — station field `+22` reaches a per-station header, but
   not (for ~half the stations) the actual board nodes. This is the key gap for a reliable
   network-wide, station-labelled board dump. Section B (`+30`, sparse) is a candidate route.
2. **`ref` exact target** (train-definition record) — for perfect automatic journey chaining
   without time-matching; gated behind the search routine.
3. Table **T2** (1000 B) and the small 14×18 / 35×6 / 3×6 tail tables; the `NL_90_91.VKZ`
   companion file.

*(Resolved this pass: the footnote-definition table = **T1** date bitmaps; the date
calendar + holidays; and the **fare table** = the 36×18 distance→price table.)*
