# Plan — finishing the NS Reisplanner 90/91 extraction

## Objective
Produce **station-labelled departure/arrival boards** for every station (and from those,
multi-stop **GTFS trips**) for the whole 1990/91 network — the one thing not yet extracted.

## STATUS — DONE

The objective is met: the timetable is fully decoded and validated.

`tools/decode_timetable.py` reads the section-A node records straight from `INLEES.NET` and
emits every train with every stop; `extract_reisplanner.py` calls it, so the GTFS feed now
carries **5,751 journeys / 47,565 stop_times / 4,874 train numbers** instead of 9 hand-built
sample trips.

How it was closed, in short:

1. The debugger phase (a DOSBox-X built from source with `C_DEBUG` — see the
   `dosbox-x-debugger-build` memory) established the runtime layout and, decisively, that
   **in-memory node bytes are byte-identical to the file bytes**. That made the rest static.
2. `objdump -D -b binary -m i8086` on the load module gave the two functions that define the
   format: `0x6ae6` (node → entries, exact record boundaries) and `0x6889` (entry body
   layout). Full details in `BINARY.md`.
3. The node→event link turned out to be the **group number**: a board sub-record's group
   indexes the pair array of every intermediate station on that segment.

Two earlier conclusions had to be corrected along the way, both because they were inferred
from a small sample rather than from the code:

- `hdr[2] & 0x8000` was assumed to mark an endpoint entry. It does not; `hdr[1] == 0` does.
  Keying on the flag dropped whole corridors from the output.
- Departure times were assumed to be 10-bit like the relative offsets. They are 11-bit;
  the `0x3ff` mask corrupted everything after 17:03.

Both were caught by checking the decoded output against the planner's own answers rather
than against itself.

**Validation.** `python3 tools/decode_timetable.py --check` reproduces the golden trip
(train 8917) 8/8. `python3 tools/verify_query.py` drives the real `REISPLAN.EXE` under the
emulator and dumps its answer screen; `tools/compare_screen.py` checks each connection
against the decoded feed — times, and the exact **date** it runs. **40/40 across ten queries
on six dates.**

Querying more than one date is what found the two remaining bugs. A single-date test could
not have caught either:
- Sunday vs Tuesday exposed the day-pattern chaining collapse (legs are published once per
  day pattern; a "used" flag starved the weekend variant).
- Two Mondays in December exposed that footnotes vary *along* a journey, which forced the
  chain residual to become a full 371-day set.

**Footnotes are solved too** — the low byte beside the day mask is the footnote index after
all. The earlier "disproved" verdict recorded here was wrong, and wrong for an instructive
reason: the footnote *table* was being read from `0x3bc76`, which is actually the
station-name hash, so the dates it produced were noise and testing against them rejected a
correct hypothesis. The real table is section D via the index at `0x3cc76`. See `BINARY.md`.

**Coverage.** 456 of 469 stations now have service. The remaining 13 are regional bus stops
reachable only over "mode 8" links, which carry no timetable of their own.

**Everything in INLEES.NET is now parsed.** The loader's read sequence accounts for every
byte of the file, so this is a complete list rather than an estimate:

| region | offset | what it is | status |
|--------|--------|------------|--------|
| A | `0x3c` | connection graph: boards + segment offsets | ✅ timetable |
| B | `0x313a6` | per-junction minimum transfer times | ✅ `transfers.csv` |
| C | `0x33544` | station name/code records | ✅ `stations.csv` |
| D | `0x37554` | footnote date ranges | ✅ `footnotes.csv` |
| station table | `0x37e2c` | 469 × 34 | ✅ |
| name hash | `0x3bc76` | 1024 pointers into C | ✅ identified (not exported) |
| footnote index | `0x3cc76` | 250 × u32 into D | ✅ |
| date calendar | `0x3d05e` | 371 × 2 day types | ✅ `holidays.csv` |
| fares | `0x3d344` | 36 × 18 | ✅ `fares.csv` |
| season tickets | `0x3d5cc`, `0x3d6c8` | 14 × 18, 14 × 6 | ✅ `season_tickets.csv` |
| link corrections | `0x3d71c` | 24 × 6 | ✅ `link_corrections.csv` |

**Still unexplained**, all inside section A and none of it affecting the output:
- the per-segment pattern-id block (`249–256`, …);
- the top five bits of the time word (values 0–5);
- the high bits some train numbers carry (e.g. `0x7C46`);
*(The `0x3d71c` link-corrections table is now exported to `link_corrections.csv`: 24 directed
records `[from][to][s16 minutes]`, per-relation journey-time adjustments. Groningen↔Lauwersoog
is 55, matching that ferry-bus link's duration; most pairs are symmetric, but Amsterdam
Muiderpoort↔Arnhem is −9 one way and −1 the other, so the value is directed.)*

`NL_90_91.VKZ` (24 bytes) and the footnote *text* are not in `INLEES.NET` at all — the
planner prints `boot`/`bus`/`lopen` from its own strings.

## Current state
**Done & reliable** (all regenerated by `../extract_reisplanner.py`, see `FORMAT.md`):
- 469 stations, network topology (249 line segments), full calendar (day-masks + date
  calendar + holidays + footnotes), fares (+ derived season-ticket rules), all 21,126
  departure events (times/days/footnote/ref, **unlabelled**), validated per-line trips.

**No remaining gap.** The route graph really is a compiled structure — departures are laid
out per line segment, not per station — but the mapping is a plain lookup once the entry
format is known, not a traversal that has to be re-run. See `BINARY.md`.

The approaches below are kept as a record of what was considered; approach 1 is the one that
worked, and it was cheaper than expected because step 2 (dynamic analysis) only had to prove
that static analysis would suffice.

## Approaches (ranked)

### 1. Dynamic analysis → reimplement (recommended)
Resolve the in-memory layout from ground truth, then port the traversal.
- **Tooling:** DOSBox-X (has a built-in debugger) or a `C_HEAVY_DEBUG` DOSBox build. Standard
  DOSBox 0.74-3 has no debugger, so this needs a different binary.
- **Steps:**
  1. Load `REISPLAN.EXE`, run a known query (Leeuwarden→Stavoren, 08:00, Sun 27 May 1990).
  2. Breakpoints: the search walker **`0x6889`**, the edge-finder **`0x3bc9`**, the counter
     **`0x3c16`**. (Module offsets; add the load segment. CS-relative from the map in `BINARY.md`.)
  3. Dump, for Leeuwarden and Mantgum: the fixed-up station record (34 B), the `field[22]`
     node, the `node+0xA` sub-table, and the edge/connection record `0x3bc9` returns.
  4. Compare against the raw file bytes to pin: the exact node record boundary, the neighbour/
     segment encoding at junctions, and the **`node+0xA` time-unpacking** (double word-indirect,
     `& 0x3ff`).
  5. Reimplement the walker + time-unpack in Python; iterate until it reproduces train 8917's
     known stops (08:22 → … → 09:11) exactly.
  6. Run it for all 469 stations → labelled boards → GTFS trips.
- **Effort:** medium-high (a few focused sessions). **Risk:** low once the dumps are in hand.

### 2. Pure static reimplementation
Same reimplementation without the debugger — decode `node+0xA`, the section-D edge list, and
the connection records purely from the file, validating against ground truth.
- **Effort:** high. **Risk:** medium-high (the packing ambiguity has to be guessed and checked;
  the 209/469 vs 469/469 base issue shows how easy it is to be subtly off).
- Only attempt if a debug DOSBox truly isn't available.

### 3. Emulator harvest (brute force, complete, no more RE)
Drive the planner itself to read out every board — the automation already works (osascript
keystrokes + DOSBox Ctrl+F5 screenshots, see the session history; `scratchpad/drive.sh`).
- **Steps:** for each of 469 stations, query a few destinations along each of its segments
  (topology from `../output/90-91/network_segments.csv` tells you which) at a fixed date; OCR/parse the result
  screens; assemble boards + trips. The self-labelling result screens are ground truth.
- **Effort:** high wall-clock (hundreds of scripted queries) but low intellectual risk; every
  row is verified by construction. Good as a *validation set* even if not the primary method.

### 4. Data-driven correlation (partial, cheap)
Use `../output/90-91/network_segments.csv` + the unlabelled `../output/90-91/connection_events.csv` + a handful of anchor
queries per line to time-match events to stations (as done for Leeuwarden–Stavoren).
- **Effort:** low per line. **Coverage:** only lines you anchor; **not** the full network.
- Good for filling high-value corridors quickly while (1) is in progress.

## Recommended path
**(1)** as the real solution — get DOSBox-X, capture the dumps at `0x6889`/`0x3bc9`, port the
traversal. Use **(3)** to build a validation set (e.g. 20 known routes) and **(4)** to ship
useful corridors in the meantime. Together these de-risk (1) and keep delivering value.

## Validation strategy
- **Golden trip:** train 8917 (Leeuwarden→Stavoren, `../fixtures/train_8917_sun.csv`) — any reimplementation
  must reproduce it to the minute.
- **Cross-checks:** the corridor queries already captured (Amsterdam–Utrecht–Eindhoven, etc.).
- **Sanity:** total events should reconcile with the 21,126 in `../output/90-91/connection_events.csv`; every
  labelled stop's station must lie on a segment from `../output/90-91/network_segments.csv`.

## Key breadcrumbs (so a fresh session can start cold)
- Load module = EXE minus the 7168-byte MZ header; Capstone `CS_MODE_16`.
- Station table: `0x37e2c`, 469×34; `+22`→board node (1-based, base `0x3b`), `+26`→name
  (section C), `+30`→section B.
- Search walker `0x6889`; edge-finder `0x3bc9` (linked list, key = station pair, next at `+0xA`);
  counter `0x3c16` (table `[0x1ac0]`, count `[0x1aa6]`).
- Node format: `[seg_endpoint_a][seg_endpoint_b] … [track-adjacency pairs] 0xffff`.
- Everything else (sections A–D, tail tables, calendar, footnotes, fares) is in `FORMAT.md`.
