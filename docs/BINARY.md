# REISPLAN.EXE — reverse-engineering notes

## Feasibility: yes, cleanly
- **Not packed.** MZ, 93066 bytes, 1711 relocations, normal entry stub
  (`MOV DX,135A; MOV CS:[1C7],DX; MOV AH,30; INT 21` = DOS-version check). No
  LZEXE/PKLITE/EXEPACK signature. The image is plain, directly disassemblable.
- **Compiler: Turbo-C (Borland, 1988), large memory model.** Confirmed by the embedded
  `"Turbo-C - Copyright (c) 1988 Borland Intl."` and by codegen: far returns (`retf`),
  far pointers via `lds`, `9a` far calls, an fd-flag table at DGROUP `0x1878` inside
  `_open`.
- Disassembles perfectly with Capstone (`CS_ARCH_X86 / CS_MODE_16`).

## Map so far (offsets are into the load module = file minus the 7168-byte header)
| item | module offset |
|------|---------------|
| `_open`  (INT 21h AH=3Dh)  | `0x0f9fc` |
| `_read`  (AH=3Fh)          | `0x1035a` |
| `_lseek` (AH=42h)          | `0x1056b` |
| `_close` (AH=3Eh)          | ~`0x10076` |
| string "Inlezen van de dienstregeling" | `0x13a23` |
| string "INLEES.NET"        | `0x13a4f` |
| fopen mode "rb"            | `0x13b50` |
| string "Ophalen en sorteren van stationsnamen" | `0x1473f` |
| string "Alles ingelezen"   | `0x13a5a` |

Flow (from strings): print "Inlezen van de dienstregeling" → `fopen("INLEES.NET","rb")`
→ read timetable + stations into allocated memory → **"Ophalen en sorteren van
stationsnamen"** (station names are *sorted at load time*, so the alphabetical index is
built in RAM, not stored in the file) → "Alles ingelezen".

## Reverse-engineering progress (Capstone, 16-bit)
Segment map recovered from the 1711-entry relocation table:
- Two big **code** segments: frame `0x028f` (module `0x028f0`) and `0x0b12` (`0x0b120`).
- **DGROUP** (near data) frame `0x135a` → base module `0x135a0`. *Confirmed*: string
  "Inlezen van de dienstregeling" resolves to DGROUP offset `0x483`, "INLEES.NET" to `0x4af`.

Routines / globals identified:
| what | location |
|------|----------|
| loader + copyright screen | fn @ `0x0069a` |
| `fopen("INLEES.NET","rb")` call site | `0x0083d`; FILE\* stored at DGROUP `0x1af6/0x1af8` |
| print (cprintf-style) | `0xb12:0x393d` |
| keyboard / station-list nav handler | fn @ `0x0dea0`; selected-station cursor at DGROUP `0x1ece`, max at `0x1ed6` |
| result-display routine | fn @ `0x01f41` (fmt " %-24s" at DGROUP `0x646`) |
| minutes→(H,M) splitter | `0x152:0xba` (divides by 60) |
| transport-mode table | DGROUP `0x622`: {train, snelbus, boot, bus, lopen} |
| ctype table | DGROUP `0x15e7` |

**"Solution" (connection) struct** — as read by the display routine:
- offsets `+2 / +6 / +0xa / +0xe`: the four leg times (minutes since midnight)
- `+0xc`: flags (bit `0x2000` tested — a leg/transfer flag)
- `+0x16`: transport-mode index (0–8 → the mode table above)

This proves the binary is fully tractable and pins the data structures the search *produces*.

## THE LOADER — authoritative file structure (from `0xbdc` / `0xc5a`)
The load routine (`build path → fopen(path,"rb")` at `0xf59:0x219` → alloc+read at `0xc5a`)
reads the header field-by-field, then reads each section into its **own far buffer**
(chunked reader `0xb3c`, 30000-byte freads via `0x1010:0x111`). This is the
"copies the correct structure into memory" step.

**Header fields the loader consumes** (little-endian):
| file off | type | meaning | stored at |
|----------|------|---------|-----------|
| `0x20` | u16 | record count = **469** | DGROUP `0x1af4` |
| `0x22` | u32 | **section A size = 201578** (connection graph) | local |
| `0x26` | u32 | section B size = 8606 | local |
| `0x2a` | u32 | section C size = 16400 (≈ station table) | local |
| `0x2e` | u32 | section D size = 2264 | local |
| `0x32`,`0x34`,`0x36`,`0x38`,`0x3a` | u16 | structure params (36, 14, 35, 40, 52 …) | DGROUP `0x1af2/0x1a82/0x1a86/0x1a84/0x1aa6` |

**Sections, read in order into separate buffers:**
- **A** — 201578 B — the 6-byte-node connection graph (dep/arr boards).
- **B** — 8606 B.
- **C** — 16400 B — the station master table (code/name/index records).
- **D** — 2264 B.
- **469 × 34-byte record table** — read directly from the file into buffer `[0x1afc]`
  (`count 469 × 0x22`). Plus scratch buffers (two × 4096, one × 1000) it zero-fills.

So the file is **header + sized sections**, not one flat blob — and there is a dedicated
**469-entry, 34-byte-per-record table** that the search consults. That table (whatever a
"record" is — line/route/train pattern) is the prime suspect for the station→board index.

## The search engine = a packed route-graph (station field `+22`)
The loader's fixup loop (`0x10ef`) fixes up the three station-record pointers (`+22`,`+26`,
`+30`) by adding a section base, turning file offsets into absolute far pointers. `+26` =
name (section C, confirmed). **`+22` is the station's node in a route-graph**, not a flat
departure board.

The core graph walker is `0x6889` (and ~20 sibling search functions all do
`les bx, es:[bx+0x16]` to load field `+22`):
- `station_index × 34` + station-table base (`[0x1afc]`) → station record → load `+22`.
- The node's words: **`node[0]` = a neighbour station index** (the engine multiplies it by
  34 to fetch that neighbour's record), `node[2]`/`node[4]` = edge data.
- A sub-table at **`node+0xA`** is walked with word-indexing (`shl ax,1`) and `& 0x3ff`
  (10-bit) masks — the packed departure/time data.
- Records are stepped `add bx, 8` (8-byte stride).
- Helper `0x3bc9` returns a struct with a pointer at `+6` and a count at `+4` that the walk
  indexes into.

So the timetable is not stored as a per-station departure list anywhere — departures are
**computed** by traversing this graph (adjacency + packed times). The clean 6-byte boards
elsewhere in section A are structures this engine indexes into. Producing a labelled
per-station board network-wide therefore means **reimplementing the traversal**, not reading
a table.

**field[22] is 1-based.** The fixup caller (`0x108a`) subtracts 1 from each section base
(`add ax,0xffff; adc dx,-1`) before calling the fixup, so `node = secA_start + (field22 − 1)`
— i.e. base `0x3b` in file terms. At that base, `node[0]` is a valid station index (0–468)
for **469/469** stations, and the node begins a **neighbour list**: Mantgum's node =
`[255, 355]` = **Leeuwarden + Sneek**, its exact two line-neighbours ✓. So field[22] is the
station's **adjacency node** in the route graph.

**It's a segment graph.** The two leading indices are the station's **line-segment
endpoints**, not immediate neighbours:
- Mantgum → `[Leeuwarden, Sneek]` (the Leeuwarden–Sneek segment)
- Workum / Hindeloopen / Koudum → `[Stavoren, IJlst]` (the southern IJlst–Stavoren segment)

This matches the `ref`-group split found earlier (the boundary at Sneek). So the route
graph groups stations into **line segments**; each node carries its segment endpoints plus
the packed-time sub-table at `+0xA`, and the search hops segment-to-segment.

## RESOLVED BY DYNAMIC ANALYSIS (debugger dumps)

`tools/dump_nodes.py` runs `REISPLAN.EXE` under a debug-enabled DOSBox-X, breaks once the
schedule is loaded, and dumps the live structures. See the memory note
`dosbox-x-debugger-build` for why the distro packages are useless here (no `C_DEBUG`) and
how the emulator was built.

**Confirmed against the live program:**
- The station table resolves at runtime (e.g. `21A5:0008`), 469 records of 34 bytes, and
  DGROUP `0x1af4` reads **469** — a reliable "am I in the loaded program" check.
- Mantgum's node opens `[255, 355]` = Leeuwarden + Sneek, exactly as predicted above.
- **The in-memory node bytes are byte-identical to the file bytes.** Section A is loaded
  verbatim, only the record *pointers* are fixed up. So the nodes need no debugger to read:
  static analysis of `INLEES.NET` is sufficient from here on.

**Offset correction.** The node's true file offset is `SEG_BASE + field22` with
`SEG_BASE = 0x3b`. `extract_reisplanner.py` always applied this internally (hence the
correct topology), but `stations.csv` exported the *raw* `field[22]` under the name
`board_secA_offset`, i.e. 0x3b too low for every downstream consumer. Now exported resolved,
with the raw value kept as `field22_raw`. Check: `node[0]` is a valid station index for
**469/469** with the correction, **54/469** without.

**Node format.** An earlier reading of this ("word `[5]` is the ordinal position, words
`[6..]` are an unidentified 253–256 run") was a partial view of one entry of one node and is
superseded by the full format in **SOLVED** below. In particular `0xA` was never a fixed
offset — it is Mantgum's own body length. The one claim from that pass that survives intact
is that the low bits of the packed words are journey minutes and the high bits a service
variant; the variant is the *group* number, and it is what links a station to its departures.

## SOLVED: the node record format, and the whole timetable

Disassembling two more functions closed this out. `objdump -D -b binary -m i8086` on the
load module (EXE minus the 7168-byte header) is enough — no Capstone, no debugger.

**`0x6ae6` gives the record boundaries.** It walks a station node like this:

```
p = node
loop:  sta = p[0]            ; a station index
       ... use record of sta ...
       p += 8                ; the 4-word header
       p += 2 * *p           ; the body, whose first word is its length in words
       if *p == 0xffff: done ; else p += 2 and loop
```

So a node is a list of **entries**: a 4-word header, a length-prefixed body, and a
terminator word — `0xfffe` = another entry follows, `0xffff` = last. Every one of the 469
nodes parses cleanly under this rule, which settles the "218 long nodes have no boundary"
problem: they were simply multi-entry nodes.

**Entry header:**

| word | meaning |
|------|---------|
| `[0]` | the far end of this line segment (station index) |
| `[1]` | the near end — **`0` means this station is itself an endpoint** |
| `[2]` | running time; bit 15 is a flag, *not* the endpoint marker |
| `[3]` | running time in the other direction |

The earlier guess that `[2] & 0x8000` marks an endpoint was wrong and cost real coverage:
it holds for only some of them, and keying on it silently dropped whole corridors
(Amsterdam CS → Utrecht CS among them). `[1] == 0` is the actual test.

**`0x6889` gives the body layout.** It does `p = body+6; p += 2*p[0]; p += 2*p[0]` — the
body opens with two length-prefixed blocks, and the payload follows them.

**Board entry (`hdr[1] == 0`)** — departures from this station across the whole segment,
toward `hdr[0]`. After a single length-prefixed id block come sub-records separated by
`0xfffe`:

    group, runtime, sublen, (time, daymask<<8 | footnote, train_number) * n

`time` is **minutes since midnight in the low 11 bits** (the top five are flags). This is
the correction that matters: masking with `0x3ff`, the width used by the *relative* offsets
in intermediate entries, mangles every departure after 17:03. Two encodings do coexist in
section A, but they are told apart by entry type, not by guessing.

**Intermediate entry (`hdr[1] != 0`)** — after the two blocks, one `(fromA, fromB)` pair per
group, each word packed `(group << 10) | minutes`:

* `fromA` = arrival offset for a run in the A→B direction
* `fromB` = arrival offset for a run in the B→A direction
* `fromA + fromB = runtime − dwell`

That last identity **explains the 1-minute asymmetry** flagged earlier as unconfirmed: the
pair straddles the stop, so the dwell falls out of the arithmetic rather than being a bug.
Mantgum on group 0 is `(8, 11)` with runtime 20 — arrive Leeuwarden+8, wait 1, reach
Sneek 11 later.

`group` is the join between the two entry types: the sub-record's group number indexes the
intermediate's pair array directly. **This is the node→event link that blocked the project.**

### Reconstruction

```
train departs endpoint A at T on group g
  intermediate S arrives  T + pairs[g].fromA
             and departs  T + runtime − pairs[g].fromB
  endpoint  B arrives     T + runtime
```

Implemented in `tools/decode_timetable.py`; `extract_reisplanner.py` calls it for the GTFS
feed. Per-segment runs are then chained into whole journeys by train number.

### Validation

Two independent checks, both against the 1990 program itself.

1. **`fixtures/train_8917_sun.csv`** — 8/8 stops to the minute (Leeuwarden 08:22 →
   Stavoren 09:11), plus a stop at Sneek Noord that the hand-built fixture omitted.
2. **Live queries in the emulator.** `tools/verify_query.py` types a query into
   `REISPLAN.EXE` and dumps the answer screen out of video RAM; `tools/compare_screen.py`
   looks every connection up in `timetable.csv` and also checks that the decoded day mask
   actually includes the weekday the planner used. **32/32 connections across seven
   queries reproduce exactly**, times *and* running days:

   | query | date | connections |
   |-------|------|-------------|
   | amsterdam cs → utrecht cs 09:00 | Sun 27 May 1990 | 4/4 |
   | leeuwarden → stavoren 08:00 | Sun 27 May 1990 | 3/3 |
   | groningen → zwolle 10:00 | Sun 27 May 1990 | 5/5 |
   | rotterdam cs → den haag cs 14:00 | Sun 27 May 1990 | 2/2 |
   | maastricht → eindhoven 12:00 | Sun 27 May 1990 | 4/4 (incl. a 2-leg transfer) |
   | arnhem → nijmegen 17:00 | Sun 27 May 1990 | 6/6 |
   | arnhem → nijmegen 17:00 | Tue 29 May / Tue 17 Jul 1990 | 6/6 each |

The input form has no typed date field — **F1/F2 step the day, F3/F4 the month**, which is
how the non-Sunday queries were driven.

**Querying a second date found a real bug.** On Sunday the planner offers trains 4663 and
1859, but the decoded feed marked them Mon–Fri. The cause: every leg is published *twice*,
once under a weekday mask and once under a weekend mask (`0x1f` and `0x60`), and the
chaining pass marked each leg "used" after the first journey claimed it — collapsing the two
patterns into one and starving the weekend variant. Legs now carry a *residual* mask, so a
leg published as `0x7f` can serve both a weekday and a weekend journey. Train 4663 comes out
correctly as Zwolle→Vlissingen on weekdays but Zwolle→**Roosendaal** at the weekend.

This also confirms the mask bit order (bit 0 = Monday … bit 6 = Sunday): every one of the
32 connections runs on the weekday the planner used.

### SOLVED: footnotes, and how a service is tied to one

The mask word is `daymask << 8 | footnote`. The low byte **is** the footnote index — an
earlier note here declared that disproved, and that was wrong. The error was upstream: the
footnote *table* was being read from the wrong place, so every footnote's dates were
fiction, and testing against fiction "disproved" a correct hypothesis.

**Where the footnote table actually is.** Disassembling the loader (`0xe00`–`0x1010`) gives
a read sequence that accounts for *every byte of the file*:

| region | file offset | size | contents |
|--------|-------------|------|----------|
| A, B, C, D | `0x3c` … `0x37e2c` | — | as documented above |
| station table | `0x37e2c` | 469 × 34 | |
| **name hash** | `0x3bc76` | 4096 | 1024 far pointers into section C |
| **footnote index** | `0x3cc76` | 1000 | 250 × u32 → 1-based offset into section D |
| date calendar | `0x3d05e` | 371 × 2 | per-date day type |
| fares | `0x3d344` | 36 × 18 | |
| season tickets | `0x3d5cc`, `0x3d6c8` | 14×18, 14×6 | see below |
| link corrections | `0x3d71c` | 24×6 | symmetric station pairs + signed value |

The 4096 bytes at `0x3bc76` had been decoded as "footnote date bitmaps, 47 bytes each".
They are nothing of the kind: `0x125d` walks that block in 4-byte steps *relocating
pointers* against the section-C base, and dumping it yields station-name records — it is
the hash table behind the "Alfabetische lijst stationsnaam" panel. Reading a pointer table
as bitmaps produced a `footnotes.csv` full of plausible-looking date ranges that were pure
noise.

**The real structure.** `FNIDX[fn]` is a 1-based offset into **section D**, which is an
array of 8-byte records:

    (u32 next_1based, u16 first_day, u16 last_day)

chained *backwards*, each giving an inclusive range of day indices in the 371-day window
from 27 May 1990. Footnote 0 means "no footnote". Most footnotes cover ~364 days and knock
out a handful of holidays — which is exactly why a footnoted train still appears on almost
any date you query, the observation that had been misread as evidence against the link.

    footnote 33: all year except 24/27/28/31 Dec 1990, 29 Mar, 29 Apr, 10 May 1991
    footnote 30: 24 Dec 1990 only

**Verified against the planner.** Venlo → Den Haag CS at 21:00, on two Mondays a week
apart — same route, same weekday, same 21:31 → 23:49 slot, so the footnote is the only
variable:

| date | planner offers | decoded |
|------|----------------|---------|
| Mon 17 Dec 1990 | train **2526** | 2526, 365 running days ✓ |
| Mon 24 Dec 1990 | train **1580** | 1580, **1** running day (24 Dec) ✓ |

Train 2526 is the through Köln → Den Haag working; its Dutch legs carry footnote 36, which
drops 24 Dec, and 1580 (footnote 30, that date only) covers the gap. The decoder reproduces
the swap exactly.

**Consequence for chaining.** A journey's legs can carry *different* footnotes, so no single
footnote number describes a whole trip. The residual mechanism that fixed the weekday /
weekend collapse was therefore generalised from a 7-bit weekly mask to the concrete
**371-day set** (`weekly mask ∩ footnote days`), intersected along the chain. `trips.csv`
exports each journey's exact running dates; `daymask` is derived from that set, not from the
first leg. GTFS gets a service per distinct day set: weekly pattern in `calendar.txt`,
footnote holes as `calendar_dates.txt` removals.

`tools/compare_screen.py` now checks the exact queried **date**, not just the weekday:
**40/40 connections across ten queries on six dates.**

### Non-rail links: ferry, bus and walking

`hdr[1]` is not always a segment partner. When `hdr[2] & 0x7fff == 0` **and** the peer
station mirrors the entry back, `hdr[1]` is a **mode code** and `hdr[3]` is the duration:

| code | mode | example |
|------|------|---------|
| 3 | `boot` (ferry) | Ameland → Holwerd, 45 min; Enkhuizen → Stavoren, 82 min |
| 4 | `bus` | Groningen → Lauwersoog, 55 min; Den Helder → Den Helder Haven, 6 min |
| 7 | `lopen` (walking) | Rotterdam CS → Rotterdam Hofplein, 15 min |
| 8 | (same shape, never seen rendered) | Utrecht CS → Utrecht streekbushaltes, 10 min |

The mirror test matters: `hdr[2] & 0x7fff == 0` on its own also matches plenty of genuine
intermediates whose `hdr[1]` is a real station index. Requiring the peer to carry the same
code back isolates exactly these four.

Their bodies use the ordinary board layout, so they were being parsed all along — just
classified as intermediates and dropped. That left **28 stations with no service at all**:
the Wadden islands, the ferry harbours and the bus-only towns. Including them adds 223 boat
and 257 bus trips. Walking links carry no departures, which is correct — the planner prints
`lopen` with no times.

Verified end to end on *Ameland → Rotterdam Bergweg*, which the planner answers with five
legs across four modes; all five reproduce to the minute (`work/screen_ameland.txt`).

**Still unserved: 13 stations**, all regional bus stops (Oosterhout, Raamsdonksveer, Gemert,
Grave, Uden, Venray busstation, Utrecht streekbushaltes, Made, Druten). They are reachable
only over mode-8 links, which carry no timetable of their own.

### Section B = minimum transfer times at junctions

Station field `+30` points into section B. `0x5b43` shows the use: a per-station array of
6-byte records `(u16 train_a, u16 train_b, u16 minutes)` terminated by `0xffff`, scanned for
a matching train **pair**, returning the stored value — and **defaulting to 2** when no
record matches. So this is a minimum-transfer-time table with a 2-minute default, not a list
of guaranteed connections: the stored values are 1 minute (1367 records) and 0 (51), i.e.
pairs allowed to connect tighter than normal.

Only 49 of 469 stations have one, all junctions (Leiden 293 records, Amersfoort 181,
Utrecht CS 168), and in every record both numbers are real train numbers that both call at
that very station (Arnhem 23/23, Amersfoort 40/40, Amsterdam CS 40/40).

**Direction.** `p[0]` is the arriving train and `p[2]` the departing one. Checked by
measuring the arrival→departure gap both ways across all 1418 records: as written, 0.6% of
gaps are negative and the median is 3 minutes; reversed, 14.3% are negative. The remaining
0.6% (8 records) are unexplained.

Exported as `transfers.csv` and GTFS `transfers.txt` (`transfer_type=2`,
`min_transfer_time`). Because section B names trains by *number* while GTFS needs *trip
ids*, each record expands to a cross product over the day-pattern variants of both trains;
pairs whose running-day sets do not intersect are dropped, or the feed would assert
connections between services that never run on the same day.

### Station names: section C is 1-based too

`field[26]` is 1-based like `field[22]`. The record header sits at `SECC + off + 1` as
`[u16][u16 station_idx][u16][name, NUL-padded]`, with the code in a NUL-padded field six
bytes earlier; the index word equals the station's own number for **469/469**. Two bugs
came out of getting this wrong: `SECC` was recorded as `0x33540` (4 bytes early), and the
name/code were recovered by scanning a ±44-byte window for text runs and guessing. That
mispaired them whenever a name was long, so **119 of 469 names were actually the station
code** — Ameland appeared as `amld`, Rotterdam Bergweg as `rtbw`, Holwerd as `holw`, which
also made those stations impossible to find by name in the output.

### Season tickets: the two small fare tables

`0x3d5cc` (14 × 18) and `0x3d6c8` (14 × 6) open with the same first column as the main
36-band fare table — `8, 12, 16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72` then `0xffff` as an
"and above" catch-all. That is the **kilometre ladder**, identical to the main table's first
13 rows; the season tables simply stop at 72 km because those products were not sold further.

The product names come from the planner's own string table in `REISPLAN.EXE` at `0x15b09`,
which is the layout of the F8 "Prijzen" screen:

    Tarief afstand / 2e Klas / 1e Klas
      Enkele reis | Retour | Reductie enkele reis | Reductie retour
      * Avondretour | * Weekendretour      (* alleen met NS reductiekaart)
      Weektrajectkaart | Red. Weektrajectkaart
      Maandtrajectkaart | Red. Maandtrajectkaart
      Jeugdmaandkaart | Red. Jeugdmaandkaart

All three tables use one column convention — *(2e, reduced 2e, 1e, reduced 1e)* per product:

| table | columns | product |
|-------|---------|---------|
| `0x3d344`, 36 × 18 | 8 | Enkele reis, Retour |
| `0x3d5cc`, 14 × 18 | 8 | Weektrajectkaart, Maandtrajectkaart |
| `0x3d6c8`, 14 × 6 | 2 | Jeugdmaandkaart — **2e klas only**, which is why it has just two |

Worked example at the 8 km band (cents): retour 2e = 350, so week 2e = **1400**, maand 2e =
**5300**, jeugdmaand 2e = **4200**.

The arithmetic rules hold across all 13 bands — week = retour × 4 *exactly*, maand =
retour × 15 rounded to the nearest 100, jeugdmaand = retour × 12 — but the prices are
**tabulated, not derived**. An earlier note in `extract_reisplanner.py` claimed the opposite;
the rounding on the monthly card means the rule alone does not reproduce the stored values.
Exported as `season_tickets.csv`.

That the season tables' row count (13 + terminator) coincides with the 13 still-unserved
stations is a coincidence: the rows carry explicit kilometre values and nothing indexes them
by station.

### Driving the emulator (hard-won constraints)

`tools/verify_query.py` types a query into the running planner and dumps its answer screen
from video RAM. Four constraints, each found the hard way:

1. **The debugger console only accepts commands while the guest is stopped.** It cannot
   inject keystrokes at all, so every key must be queued with `AUTOTYPE` before the run.
2. **The guest must run free while AUTOTYPE types.** With `BPINT 16` armed it stops at
   nearly every keyboard poll and the keystrokes are dropped — only the first few land.
3. **So breakpoints must be armed up front.** The way back in is a breakpoint on the
   result-display routine, which fires only on a *successful* search. That also
   distinguishes "the query never ran" from "the breakpoint is not on the query path".
4. **A leading `space` is essential.** The program sits on "Alles ingelezen. Druk op een
   toets." and eats the first keystroke, which silently turned `amsterdam` into `msterdam`
   — an invalid station, so no search ever ran.

The input form has **no typed date field**: `F1`/`F2` step the day, `F3`/`F4` the month.
Fields are separated by `TAB`, not Enter. The result header echoes the date actually used,
which makes a wrong date self-evident.

Two operational notes. Run queries **one at a time** — several emulators competing for CPU
starve each other into timeouts. And delete the previous screen capture *before* each run,
or a failed run leaves the last successful capture in place to be read as if it were fresh.

Pressing `F8` for the price screen needs a second `AUTOTYPE` with a long initial wait, since
the keys cannot be sent after the fact; that attempt timed out, and the season-ticket
products were identified from the string table in `REISPLAN.EXE` instead.

### Superseded

The earlier NEGATIVE RESULT — "section A is not partitioned by station node", proved by
8/8 fixture stops mapping to the wrong station — stands as a fact but is no longer the
obstacle it looked like. Events are grouped by *line segment*, and the node's board entries
address them by segment and group. The old flat `connection_events.csv` scan (21,126
unlabelled 6-byte records) is a byte-level view of the same data and is kept only as a
cross-check; `timetable.csv` supersedes it.

**Still open (cosmetic):**
- The id block (`249–256`, `476–480`, …) that precedes the sub-records: these are stable
  per-segment pattern ids, but nothing in the output needs them.
- Some train numbers carry high bits (e.g. `0x7C46`); left raw, since they are identifiers.

## What's needed to finish (the real work)
The blocker for tracing the parse/index logic is a **segment map**: large-model code
spans multiple 64 KB segments and the string/data segments are relocated, so a raw
`push offset` can't be tied to a string without knowing each segment's base paragraph.
Steps:
1. Reconstruct the segment layout from the 1711-entry relocation table + linker order.
2. Resolve the `fopen("INLEES.NET")` call site and follow the read loop.
3. Decode the in-memory structures it builds, and the **search routine** that maps a
   station → its departure/arrival board. That routine *is* the authoritative
   offset→station / board-format definition we're missing.

This is a focused multi-hour 16-bit RE effort — very doable, just not a quick probe.
