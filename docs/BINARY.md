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

**Where it still stalls.** The parse isn't uniformly clean (junctions like Sneek/Stavoren
read oddly — a `0xff`-terminator byte bleeds into the 1-based read), and the `+0xA`
packed-time format (word-indexed, `& 0x3ff` masks) isn't decoded. Nailing both wants
**dynamic analysis**: run `REISPLAN.EXE` under a heavy-debug DOSBox, break at the walker
`0x6889`, and dump the real in-memory node + `+0xA` table for a known route. That resolves
the packing from ground truth in one shot. **Static analysis has taken the route-graph as
far as it reliably goes** — the structure is characterised (segment graph, node format,
walker, 1-based offsets); a faithful port of the time-unpacking needs the debugger.

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
