"""Generate an interactive annotated-hexdump explainer for INLEES.NET.

Emits a single self-contained HTML page (docs/format_explainer.html):

  * the WHOLE file as a virtualized hexdump, every byte coloured by the field
    it belongs to. Field boundaries + type codes come from tools/annotate.py;
    the human label and decoded value are computed in JavaScript on hover, so
    the page ships boundaries, not a string per byte.
  * a region-map navigator and five hand-written "specimen" deep-dives.

Both the raw bytes and the annotations are derived from the archival file at
build time, so the page cannot drift from the source.

    python3 tools/make_explainer.py            # -> docs/format_explainer.html
"""
import base64
import json
import os
import struct

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(REPO, "input", "90-91", "INLEES.NET")
OUT = os.path.join(REPO, "docs", "format_explainer.html")

d = open(NET, "rb").read()
FILESIZE = len(d)


def sl(off, n):
    return list(d[off:off + n])


# ── the twelve regions the loader reads, in file order ───────────────────────
# kind drives the colour; several regions share a kind so the map stays legible.
REGIONS = [
    (0x00000, 0x0003c, "Header", "meta", "Fixed 60-byte preamble."),
    (0x0003c, 0x313a6, "A · Connection graph", "graph",
     "The timetable itself: per-station node records holding departure boards "
     "and line-segment geometry. 201,578 bytes — 80% of the file."),
    (0x313a6, 0x33544, "B · Transfer times", "transfer",
     "Per-junction minimum transfer times (default 2 min; stored exceptions)."),
    (0x33544, 0x37554, "C · Station names", "text",
     "469 name/code records. The station index sits right before each name."),
    (0x37554, 0x37e2c, "D · Footnote ranges", "cal",
     "Chained 8-byte day-range records that footnotes point into."),
    (0x37e2c, 0x3bc76, "Station table", "meta", "469 × 34-byte records."),
    (0x3bc76, 0x3cc76, "Name hash", "meta", "1024 pointers into section C."),
    (0x3cc76, 0x3d05e, "Footnote index", "cal",
     "250 × u32: footnote number → 1-based record in section D."),
    (0x3d05e, 0x3d344, "Date calendar", "cal",
     "371 × 2 bytes: the day-type of every date in the validity window."),
    (0x3d344, 0x3d5cc, "Fares", "fare", "36 distance bands × 18 bytes."),
    (0x3d5cc, 0x3d71c, "Season tickets", "fare",
     "14 bands: week / month / youth-month trajectkaart prices."),
    (0x3d71c, FILESIZE, "Link corrections", "value",
     "24 directed per-relation journey-time corrections — the final bytes "
     "of the file."),
]

# ── specimens: real byte slices with hand-verified field annotations ─────────
# Each field: [start, length, category, label, value, note]. Offsets are within
# the slice. Categories map to the legend/colour palette below.
SPECIMENS = [
    {
        "id": "board",
        "title": "A departure board",
        "kicker": "section A · 0x00A24",
        "lead": "Arnhem’s board for trains toward Zevenaar. This one entry "
                "is the whole format in miniature: an 8-byte header, a "
                "length-prefixed reference block, then one triple per "
                "departure — time, day-pattern, train number. Cracking this "
                "record is what unlocked the timetable.",
        "base": 0x00A24,
        "bytes": sl(0x00A24, 32),
        "fields": [
            [0, 2, "pointer", "far endpoint", "463 → zevenaar",
             "hdr[0]: the other end of this line segment."],
            [2, 2, "marker", "board marker", "0",
             "hdr[1] == 0 means this station is itself an endpoint — so "
             "this entry is a departure board, not an intermediate stop."],
            [4, 2, "time", "runtime →", "14 min",
             "hdr[2]: nominal running time in this direction."],
            [6, 2, "time", "runtime ←", "10 min",
             "hdr[3]: nominal running time the other way."],
            [8, 2, "len", "body length", "11 words",
             "The body’s length in 16-bit words, counting this word."],
            [10, 2, "len", "reference-block length", "4",
             "A length-prefixed block of node references for this line "
             "follows (counting this word)."],
            [12, 6, "pointer", "line node refs", "156, 157, 158",
             "References into the internal node list (values 0–474: the 469 "
             "stations plus 6 virtual endpoints). This is NOT the public "
             "station index — reading them as station names gives nonsense "
             "(they’d point at Zoetermeer for an Aachen line), so their exact "
             "role is unconfirmed; the timetable decode does not need them."],
            [18, 2, "len", "group id", "1",
             "Departures are grouped by the stopping pattern they follow; this "
             "is group 1."],
            [20, 2, "time", "segment runtime", "0x400B → 11 min",
             "Low 11 bits are the running time (11 min); the high bits are "
             "flags. Masking to 10 bits mangled every departure after 17:03 "
             "until this was found."],
            [22, 2, "len", "block length", "4", "Length of this group’s "
             "departure list."],
            [24, 2, "time", "departure", "0x0141 → 05:21",
             "Minutes since midnight in the low 11 bits: 321 = 05:21."],
            [26, 1, "foot", "footnote", "0 = none",
             "Low byte of the mask word: an index into the footnote table that "
             "narrows the weekly pattern to exact dates. 0 = runs on every day "
             "the weekly mask allows."],
            [27, 1, "mask", "day mask", "0x1F = Mon–Fri",
             "High byte: the weekly running-day bitmap. 0x1F = Monday through "
             "Friday. (0x7F daily, 0x40 Sundays only.)"],
            [28, 2, "train", "train number", "2321",
             "The service number printed on the departure board."],
            [30, 2, "marker", "terminator", "0xFFFF = last",
             "0xFFFF ends the node; 0xFFFE would mean another entry follows."],
        ],
    },
    {
        "id": "name",
        "title": "A station name record",
        "kicker": "section C · 0x36C36",
        "lead": "The record for Zevenaar. The station index sits immediately "
                "before the name — the link that ties every board entry to "
                "a real place. An earlier reading guessed the index from a "
                "sliding window and mispaired 119 of 469 names; reading it from "
                "this field fixed all of them.",
        "base": 0x36C36,
        "bytes": sl(0x36C36, 16),
        "fields": [
            [0, 2, "marker", "field", "0", "Leading word, always zero here."],
            [2, 2, "index", "station index", "463 → zevenaar",
             "The index every section-A entry uses to name this station."],
            [4, 2, "len", "field", "5",
             "A small per-record value adjacent to the name."],
            [6, 8, "text", "name", "“zevenaar”",
             "The station name as printed, Latin-1, NUL-terminated."],
            [14, 2, "marker", "terminator", "00 00",
             "NUL padding closes the name. The 2-letter code (“zv”) "
             "lives in a separate NUL-padded slot earlier in the record."],
        ],
    },
    {
        "id": "foot",
        "title": "A footnote → date-range chain",
        "kicker": "footnote index 0x3CC76 → section D 0x37554",
        "lead": "Footnote 1. The index gives a 1-based pointer into section D; "
                "each section-D record is a run of consecutive days and a link "
                "to the next run. Following the chain turns a footnote into the "
                "exact set of dates a train runs — which is how a query on "
                "one specific date can be checked against the decoded output.",
        "base": 0x3CC7A,
        "bytes": sl(0x3CC7A, 4),
        "extra": {
            "base": 0x37564,
            "bytes": sl(0x37564, 8),
            "label": "section D record 17",
            "fields": [
                [0, 4, "pointer", "next record", "9",
                 "1-based link to the next day-range in the chain; 0 ends it."],
                [4, 2, "time", "first day", "0",
                 "Day 0 of the validity window = Sun 27 May 1990."],
                [6, 2, "time", "last day", "79",
                 "Day 79 = 14 Aug 1990. So this run covers 27 May–14 Aug, "
                 "then the chain continues at record 9."],
            ],
        },
        "fields": [
            [0, 4, "pointer", "section-D pointer", "17 (1-based)",
             "Footnote 1 points at record 17 in section D. 1-based: subtract 1 "
             "for the byte offset."],
        ],
    },
    {
        "id": "fare",
        "title": "A fare band",
        "kicker": "section 0x3D344",
        "lead": "The cheapest distance band (up to 8 km) with its eight prices "
                "in cents. The fares are tabulated, not computed — the "
                "week/month season tickets derive from these by fixed "
                "multipliers, but rounding means only the stored table "
                "reproduces them exactly.",
        "base": 0x3D344,
        "bytes": sl(0x3D344, 18),
        "fields": [
            [0, 2, "len", "distance cap", "≤ 8 km",
             "Upper bound of this band in kilometres."],
            [2, 2, "price", "enkel 2e", "ƒ2.00", "Single, 2nd class."],
            [4, 2, "price", "enkel 2e korting", "ƒ1.00",
             "Single, 2nd class, reduced (railrunner / off-peak)."],
            [6, 2, "price", "enkel 1e", "ƒ3.00", "Single, 1st class."],
            [8, 2, "price", "enkel 1e korting", "ƒ1.75",
             "Single, 1st class, reduced."],
            [10, 2, "price", "retour 2e", "ƒ3.50", "Return, 2nd class."],
            [12, 2, "price", "retour 2e korting", "ƒ2.00",
             "Return, 2nd class, reduced."],
            [14, 2, "price", "retour 1e", "ƒ5.25", "Return, 1st class."],
            [16, 2, "price", "retour 1e korting", "ƒ3.00",
             "Return, 1st class, reduced."],
        ],
    },
    {
        "id": "linkcorr",
        "title": "A link correction",
        "kicker": "section 0x3D794 · the last table in the file",
        "lead": "The final table: 24 directed journey-time corrections the "
                "planner adds on top of the summed segment times for specific "
                "relations. This one is the Groningen–Lauwersoog ferry bus; "
                "its 55-minute value matches the link’s own duration. Most "
                "pairs are symmetric, but Amsterdam Muiderpoort–Arnhem is "
                "−9 one way and −1 the other — so the value is "
                "directed. Newly exported to link_corrections.csv.",
        "base": 0x3D794,
        "bytes": sl(0x3D794, 6),
        "fields": [
            [0, 2, "index", "from", "151 → groningen", "Origin station."],
            [2, 2, "index", "to", "256 → lauwersoog",
             "Destination station."],
            [4, 2, "value", "correction", "+55 min",
             "Signed minutes added to this relation’s computed time."],
        ],
    },
]

LEGEND = [
    ("pointer", "pointer / offset"),
    ("index", "station index"),
    ("len", "length / count"),
    ("time", "time / day number"),
    ("mask", "day mask"),
    ("foot", "footnote"),
    ("train", "train number"),
    ("text", "text"),
    ("price", "price"),
    ("value", "signed value"),
    ("marker", "marker / zero"),
]


# ── whole-file byte annotation (every byte -> a typed field) ─────────────────
import annotate

stations_full = annotate.load_stations()
TYPES, FIELDS = annotate.build(d, stations_full)

# region kinds -> a palette colour, for the map navigator.
REGION_CAT = {"meta": "marker", "graph": "time", "transfer": "len",
              "text": "text", "cal": "foot", "fare": "price", "value": "value"}


def pack_fields(fields):
    """varint(length) + 1 byte type, per field, in file order."""
    out = bytearray()
    for _start, length, t in fields:
        v = length
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)
        out.append(t)
    return base64.b64encode(bytes(out)).decode()


FIELDS_B64 = pack_fields(FIELDS)
maxidx = max(stations_full)
NAMES = [stations_full[i]["name"] if i in stations_full else "" for i in range(maxidx + 1)]

payload = {
    "regions": [{"start": s, "end": e, "name": n, "cat": REGION_CAT[k],
                 "note": note, "kind": k} for (s, e, n, k, note) in REGIONS],
    "specimens": SPECIMENS,
    "types": TYPES,
    "names": NAMES,
    "modes": {3: "boot", 4: "bus", 7: "lopen", 8: "link"},
    "off": {"STATTBL": annotate.STATTBL, "DATECAL": annotate.DATECAL,
            "SECD": annotate.SECD, "FNIDX": annotate.FNIDX},
    "start": "1990-05-27",
    "nfields": len(FIELDS),
    "filesize": FILESIZE,
}

DATA = json.dumps(payload, separators=(",", ":"))
B64 = base64.b64encode(d).decode()

# The body-only fragment (style + markup + script). This is what the Artifact
# host wants — it supplies its own <!doctype>/<head>/<body> skeleton. For a
# standalone file that opens over file://, FRAG is wrapped in DOC below.
FRAG = """<style>
:root{
  --bg:#12161d; --panel:#181d26; --panel2:#1e2530; --line:#2b3340;
  --ink:#d6dee9; --ink2:#9aa6b6; --ink3:#6b7688;
  --accent:#e6a24a; --accent2:#f0b968;
  --cat-pointer:#b389e6; --cat-index:#57a2ea; --cat-len:#25b7a0;
  --cat-time:#5bbf63; --cat-mask:#e6a94a; --cat-foot:#ee7f4d;
  --cat-train:#e07cc4; --cat-text:#46c4d1; --cat-price:#e56f88;
  --cat-value:#c9b53f; --cat-marker:#8b97a8;
  --tint:16%; --tint-hi:44%; --edge:64%;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){
  --bg:#eef0ee; --panel:#f8f9f7; --panel2:#ffffff; --line:#d9ddd6;
  --ink:#1d232b; --ink2:#4f5a68; --ink3:#7b8798;
  --accent:#b06f16; --accent2:#8a5510;
  --cat-pointer:#7d4bc9; --cat-index:#1f6fc4; --cat-len:#0a8a78;
  --cat-time:#2f9440; --cat-mask:#b5791a; --cat-foot:#c65a26;
  --cat-train:#c0479e; --cat-text:#0e8b98; --cat-price:#c53f5f;
  --cat-value:#8f7e14; --cat-marker:#5a6675;
  --tint:22%; --tint-hi:55%; --edge:78%;
}}
:root[data-theme=light]{
  --bg:#eef0ee; --panel:#f8f9f7; --panel2:#ffffff; --line:#d9ddd6;
  --ink:#1d232b; --ink2:#4f5a68; --ink3:#7b8798;
  --accent:#b06f16; --accent2:#8a5510;
  --cat-pointer:#7d4bc9; --cat-index:#1f6fc4; --cat-len:#0a8a78;
  --cat-time:#2f9440; --cat-mask:#b5791a; --cat-foot:#c65a26;
  --cat-train:#c0479e; --cat-text:#0e8b98; --cat-price:#c53f5f;
  --cat-value:#8f7e14; --cat-marker:#5a6675;
  --tint:22%; --tint-hi:55%; --edge:78%;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:0 20px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

header.hero{border-bottom:1px solid var(--line);
  background:linear-gradient(180deg,var(--panel),transparent);
  position:relative;overflow:hidden}
header.hero::after{content:"";position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(0deg,transparent 0 3px,
    color-mix(in srgb,var(--accent) 6%,transparent) 3px 4px);opacity:.5}
.hero .wrap{padding:52px 20px 40px;position:relative;z-index:1}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.24em;
  text-transform:uppercase;color:var(--accent);margin:0 0 16px}
h1{font-family:var(--mono);font-weight:700;font-size:clamp(30px,6vw,50px);
  letter-spacing:-.01em;margin:0;text-wrap:balance;line-height:1.05}
h1 .dim{color:var(--ink3)}
.tagline{max-width:62ch;color:var(--ink2);font-size:17px;margin:18px 0 0}
.meta{font-family:var(--mono);font-size:12.5px;color:var(--ink3);margin-top:22px;
  display:flex;gap:22px;flex-wrap:wrap}
.meta b{color:var(--ink2);font-weight:600}

section{padding:44px 0;border-bottom:1px solid var(--line)}
.h{font-family:var(--mono);font-size:12px;letter-spacing:.2em;
  text-transform:uppercase;color:var(--ink3);margin:0 0 8px}
h2{font-size:24px;margin:0 0 6px;letter-spacing:-.01em;text-wrap:balance}
.sub{color:var(--ink2);max-width:68ch;margin:0 0 22px}

/* file map / navigator */
.map{display:flex;height:56px;border-radius:9px;overflow:hidden;
  border:1px solid var(--line);background:var(--panel)}
.seg{position:relative;cursor:pointer;border-right:1px solid var(--bg);
  transition:filter .15s}
.seg:last-child{border-right:0}
.seg::before{content:"";position:absolute;inset:0;
  background:color-mix(in srgb,var(--c) 42%,transparent)}
.seg:hover{filter:brightness(1.25)}
.seg.on{outline:2px solid var(--accent);outline-offset:-2px;z-index:2}
.maprow{display:flex;justify-content:space-between;font-family:var(--mono);
  font-size:11px;color:var(--ink3);margin-top:8px}
.legend{display:flex;flex-wrap:wrap;gap:7px 14px;margin:0 0 14px;
  font-family:var(--mono);font-size:12px}
.legend span{display:inline-flex;align-items:center;gap:7px;color:var(--ink2);
  cursor:default}
.legend i{width:11px;height:11px;border-radius:3px;
  background:color-mix(in srgb,var(--c) 66%,transparent)}

/* whole-file hex viewer */
.viewer{margin-top:18px;border:1px solid var(--line);border-radius:12px;
  overflow:hidden;background:var(--panel)}
.hv-head,.hv-row{font-family:var(--mono);font-size:13px;white-space:nowrap;
  display:flex;align-items:center;height:22px;line-height:22px}
.hv-head{height:30px;line-height:30px;border-bottom:1px solid var(--line);
  color:var(--ink3);background:var(--panel2);padding:0;
  position:sticky;top:0;z-index:3;font-size:12px;width:max-content;min-width:100%}
.hv-off{flex:none;width:9ch;color:var(--ink3);-webkit-user-select:none;
  user-select:none;padding-left:12px}
.hv-hex{flex:none}
.hb{display:inline-block;width:1.75em;text-align:center;color:var(--ink);
  border-radius:3px;
  background:color-mix(in srgb,var(--c,var(--cat-marker)) var(--tint),transparent)}
.hb.gap{margin-left:.65em}
.hb.fst{box-shadow:inset 1.4px 0 0 color-mix(in srgb,var(--c) 60%,transparent)}
.hb.c-pointer{--c:var(--cat-pointer)} .hb.c-index{--c:var(--cat-index)}
.hb.c-len{--c:var(--cat-len)} .hb.c-time{--c:var(--cat-time)}
.hb.c-mask{--c:var(--cat-mask)} .hb.c-foot{--c:var(--cat-foot)}
.hb.c-train{--c:var(--cat-train)} .hb.c-text{--c:var(--cat-text)}
.hb.c-price{--c:var(--cat-price)} .hb.c-value{--c:var(--cat-value)}
.hb.c-marker{--c:var(--cat-marker)}
.hb.pad{background:none;color:var(--ink3)}
.hb.on{background:color-mix(in srgb,var(--c) var(--tint-hi),transparent);
  outline:1.4px solid var(--c)}
.hv-asc{flex:none;padding:0 14px 0 18px;color:var(--ink3);
  border-left:1px solid var(--line);margin-left:12px}
.hv-asc [data-o]{border-radius:2px}
.hv-asc b{color:var(--ink);font-weight:400}
.hv-asc .np{color:var(--ink3);opacity:.5}
.hv-asc .on{background:color-mix(in srgb,var(--c,var(--cat-text)) var(--tint-hi),transparent);
  color:var(--ink)}
.hv-scroll{height:64vh;min-height:380px;overflow:auto;
  position:relative;background:var(--panel)}
.hv-sizer{position:relative}
.hv-rows{position:absolute;left:0;top:0;width:max-content}
.hv-row:hover{background:color-mix(in srgb,var(--accent) 5%,transparent)}
.status{font-family:var(--mono);font-size:12.5px;border-top:1px solid var(--line);
  background:var(--panel2);padding:10px 14px;display:flex;gap:8px 14px;
  flex-wrap:wrap;align-items:center;min-height:46px}
.status .pill{padding:1px 9px;border-radius:20px;
  background:color-mix(in srgb,var(--c,var(--ink3)) 24%,transparent);color:var(--ink)}
.status .o{color:var(--accent);font-weight:600}
.status .muted{color:var(--ink3)}
.status .fld{color:var(--ink);font-weight:600}
.status .val{color:var(--ink)}
.status .hint{color:var(--ink3)}

/* specimen cards */
.leg2{display:flex;flex-wrap:wrap;gap:8px 16px;margin:0 0 4px;
  font-family:var(--mono);font-size:12px}
.leg2 span{display:inline-flex;align-items:center;gap:7px;color:var(--ink2)}
.leg2 i{width:11px;height:11px;border-radius:3px;background:var(--c)}
.spec{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:22px;margin-top:20px}
.spec-head{display:flex;justify-content:space-between;align-items:baseline;
  gap:16px;flex-wrap:wrap;margin-bottom:6px}
.spec-head h3{margin:0;font-size:19px}
.kick{font-family:var(--mono);font-size:12px;color:var(--accent);letter-spacing:.04em}
.lead{color:var(--ink2);font-size:14.5px;margin:0 0 14px;max-width:74ch}
.jump{font-family:var(--mono);font-size:12px;background:none;cursor:pointer;
  color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);
  border-radius:6px;padding:3px 10px;margin:0 0 16px}
.jump:hover{background:color-mix(in srgb,var(--accent) 12%,transparent)}
.dumpwrap{overflow-x:auto;padding-bottom:2px}
.sublabel{font-family:var(--mono);font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink3);margin:14px 0 6px}
.dump{font-family:var(--mono);font-size:14px;width:max-content;
  border-collapse:separate;border-spacing:0}
.dump .off{color:var(--ink3);padding-right:16px;text-align:right;
  white-space:nowrap;-webkit-user-select:none;user-select:none}
.dump td{padding:2px 0}
.hexcell{display:inline-flex}
.byte{--c:var(--cat-marker);display:inline-block;min-width:1.55em;
  text-align:center;padding:3px 0;margin:1px;border-radius:4px;cursor:pointer;
  color:var(--ink);background:color-mix(in srgb,var(--c) var(--tint),transparent);
  box-shadow:inset 0 -2px 0 color-mix(in srgb,var(--c) var(--edge),transparent);
  transition:background .12s,transform .12s}
.byte.gap{margin-left:.7em}
.byte.on{background:color-mix(in srgb,var(--c) var(--tint-hi),transparent);
  outline:1.5px solid var(--c);transform:translateY(-1px)}
.byte.dim{opacity:.32}
.asc{padding-left:20px;color:var(--ink3);white-space:pre}
.asc .ascb{border-radius:3px}
.asc .on{background:color-mix(in srgb,var(--cat-text) var(--tint-hi),transparent);
  outline:1px solid var(--cat-text);color:var(--ink)}
.detail{margin-top:16px;background:var(--panel2);border:1px solid var(--line);
  border-left:3px solid var(--c,var(--line));border-radius:8px;padding:13px 15px;
  min-height:78px}
.detail .hint{color:var(--ink3);font-size:14px}
.detail .drow{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;margin-bottom:5px}
.detail .lbl{font-family:var(--sans);font-weight:650;font-size:15px}
.detail .rng{font-family:var(--mono);font-size:11.5px;color:var(--ink3)}
.detail .val{font-family:var(--mono);font-size:13px;color:var(--ink);
  background:color-mix(in srgb,var(--c) 18%,transparent);padding:1px 8px;border-radius:5px}
.detail .note{color:var(--ink2);font-size:14px;margin:0}
footer{padding:34px 0 60px;color:var(--ink3);font-size:13px;font-family:var(--mono)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<header class="hero"><div class="wrap">
  <p class="eyebrow">Reverse-engineering field guide</p>
  <h1>INLEES.NET<span class="dim"> · every byte annotated</span></h1>
  <p class="tagline">The complete 1990 NS Reisplanner timetable compiled into
  one 246&nbsp;KB binary. All __SIZE__ bytes are shown below, and every one is
  tagged with the field it belongs to — __NFIELDS__ fields in total, decoded
  live from the file. Hover any byte to read it.</p>
  <div class="meta">
    <span><b>__SIZE__</b> bytes</span>
    <span><b>__NFIELDS__</b> fields</span>
    <span><b>469</b> stations</span>
    <span><b>6,729</b> trips</span>
  </div>
</div></header>

<section id="whole"><div class="wrap">
  <p class="h">The entire file</p>
  <h2>All __SIZE__ bytes, every one classified</h2>
  <p class="sub">Each byte is coloured by what it is. Scroll the dump — the
  repeating green/pink bands in section A are departure events (time · day-mask ·
  footnote · train number). Hover a byte for its field and decoded value; click a
  region band or legend swatch to jump.</p>
  <div class="legend" id="legend"></div>
  <div class="map" id="mapbar"></div>
  <div class="maprow"><span>0x00000</span><span>0x__ENDHEX__</span></div>
  <div class="viewer">
    <div class="hv-scroll" id="hvscroll">
      <div class="hv-head"><span class="hv-off">offset</span>
        <span class="hv-hex" id="colhdr"></span>
        <span class="hv-asc">ascii</span></div>
      <div class="hv-sizer" id="hvsizer">
        <div class="hv-rows" id="hvrows"></div>
      </div>
    </div>
    <div class="status" id="status">
      <span class="hint">Hover any byte to inspect it.</span>
    </div>
  </div>
</div></section>

<section id="specs"><div class="wrap">
  <p class="h">The structures, in prose</p>
  <h2>Five annotated specimens</h2>
  <p class="sub">The same bytes, curated: five slices of the file with every
  field explained. Jump to any of them in the full dump above.</p>
  <div class="leg2" id="leg2"></div>
  <div id="specimens"></div>
</div></section>

<footer><div class="wrap">
  Generated from input/90-91/INLEES.NET · NS Reisplanner 90/91 ·
  byte annotations produced by the decoder, verified against the running 1990 program.
</div></footer>

<script>
const DATA = __DATA__;
const BYTES = Uint8Array.from(atob("__B64__"), c=>c.charCodeAt(0));
const N = BYTES.length;
const TYPES = DATA.types, NAMES = DATA.names, MODES = DATA.modes, OFF = DATA.off;
const catVar = c => `var(--cat-${c})`;
const hx = (v,n) => v.toString(16).toUpperCase().padStart(n,'0');
const rd16 = o => BYTES[o] | (BYTES[o+1]<<8);
const rd32 = o => (BYTES[o] | (BYTES[o+1]<<8) | (BYTES[o+2]<<16) | (BYTES[o+3]<<24))>>>0;
const rds16 = o => { const v=rd16(o); return v>=0x8000 ? v-0x10000 : v; };
const nm = i => (NAMES[i]||('#'+i));
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const BASEDAY = Date.UTC(1990,4,27);
function dayDate(n){ const d=new Date(BASEDAY+n*86400000);
  return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; }
function hhmm(m){ return `${String((m/60)|0).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`; }
function maskName(m){
  const nice={0x7f:'daily',0x3f:'Mon–Sat',0x1f:'Mon–Fri',0x40:'Sun only',0x60:'weekend'};
  if(nice[m]) return nice[m];
  const L='MTWTFSS'; let s='';
  for(let b=0;b<7;b++) s+= (m>>b)&1 ? L[b] : '·';
  return s;
}

/* ---- unpack the field table: varint(len)+byte(type) ---- */
const F = DATA.nfields;
const fstart = new Uint32Array(F+1);
const ftype = new Uint8Array(F);
(function(){
  const blob = Uint8Array.from(atob("__FIELDS__"), c=>c.charCodeAt(0));
  let p=0, pos=0;
  for(let i=0;i<F;i++){
    let len=0, sh=0, b;
    do{ b=blob[p++]; len|=(b&0x7f)<<sh; sh+=7; }while(b&0x80);
    ftype[i]=blob[p++];
    fstart[i]=pos; pos+=len;
  }
  fstart[F]=pos;
})();
/* per-byte -> field index, for O(1) hover + colour */
const byteField = new Uint32Array(N);
for(let i=0;i<F;i++){ for(let o=fstart[i];o<fstart[i+1];o++) byteField[o]=i; }

/* ---- decode one field to a human value ---- */
function decodeField(i){
  const s=fstart[i], len=fstart[i+1]-s, t=ftype[i], dec=TYPES[t].dec;
  switch(dec){
    case 'sta':      return `${rd16(s)} → ${nm(rd16(s))}`;
    case 'near':{ const v=rd16(s);
      return v===0 ? '0 · board (this station is an endpoint)'
        : MODES[v] ? `${v} · non-rail: ${MODES[v]}` : `${v} → ${nm(v)}`; }
    case 'rt11':{ const v=rd16(s); return `${v&0x7ff} min`+(v>0x7ff?' (+flags)':''); }
    case 'time11':{ const v=rd16(s); return `${hhmm(v&0x7ff)}`+(v>0x7ff?' (+flags)':''); }
    case 'footb':{ const v=BYTES[s]; return v? `footnote ${v}` : 'none'; }
    case 'maskb':{ const v=BYTES[s]; return `0x${hx(v,2)} · ${maskName(v)}`; }
    case 'train':{ const v=rd16(s);
      return `${v&0x7fff}`+(v>0x7fff?` (0x${hx(v,4)}, high bit set)`:''); }
    case 'u16':    return `${rd16(s)}`;
    case 'u16w':   return `${rd16(s)} words`;
    case 'term':{ const v=rd16(s);
      return v===0xffff?'0xFFFF · last entry':v===0xfffe?'0xFFFE · more follows':`0x${hx(v,4)}`; }
    case 'noderef':{ const a=[]; for(let o=s;o+1<s+len;o+=2) a.push(rd16(o));
      return a.join(', ')+' · internal node ids (0–474; not the public station index)'; }
    case 'words':{ const a=[]; for(let o=s;o+1<s+len;o+=2) a.push(rd16(o));
      return a.slice(0,10).join(', ')+(a.length>10?` … (${a.length} words)`:''); }
    case 'pair':{ const a=rd16(s), b=rd16(s+2);
      return (a>>10)===(b>>10) ? `group ${a>>10}: +${a&0x3ff} / +${b&0x3ff} min`
        : `0x${hx(a,4)} 0x${hx(b,4)}`; }
    case 'transfer':{ const ta=rd16(s),tb=rd16(s+2),mn=rd16(s+4);
      return ta===0xffff?'terminator':`train ${ta} → ${tb}: ${mn} min`; }
    case 'u32ptr':{ const v=rd32(s); return v? `→ ${v}` : '0 · none / end'; }
    case 'day':{ const v=rd16(s); return `day ${v} · ${dayDate(v)}`; }
    case 'daytype':{ const k=((s-OFF.DATECAL)/2)|0; return `${dayDate(k)} · type 0x${hx(rd16(s),4)}`; }
    case 'starec':{ const k=((s-OFF.STATTBL)/34)|0; return `station ${k}: ${nm(k)}`; }
    case 'kmband':{ const v=rd16(s); return v===0xffff?'≤ ∞ (and above)':`≤ ${v} km`; }
    case 'cents':  return `ƒ${(rd16(s)/100).toFixed(2)}`;
    case 'smin':   return `${rds16(s)>=0?'+':''}${rds16(s)} min`;
    default:{ const a=[]; for(let o=s;o<s+len&&o<s+8;o++) a.push(hx(BYTES[o],2));
      return a.join(' ')+(len>8?` … (${len} B)`:''); }
  }
}

/* ---- regions: map navigator ---- */
function regionAt(o){ let lo=0,hi=DATA.regions.length-1;
  while(lo<=hi){const m=(lo+hi)>>1,r=DATA.regions[m];
    if(o<r.start)hi=m-1; else if(o>=r.end)lo=m+1; else return r;}
  return DATA.regions[DATA.regions.length-1]; }
const bar=document.getElementById('mapbar');
DATA.regions.forEach(r=>{
  const seg=document.createElement('div'); seg.className='seg';
  seg.style.flex=(r.end-r.start)/N; seg.style.setProperty('--c',catVar(r.cat));
  seg.title=`${r.name}  ·  0x${hx(r.start,5)}–0x${hx(r.end,5)}  ·  ${(r.end-r.start).toLocaleString()} bytes`;
  seg.addEventListener('click',()=>jumpTo(r.start)); bar.appendChild(seg);
});
function markMap(o){ const ri=DATA.regions.findIndex(r=>o>=r.start&&o<r.end);
  [...bar.children].forEach((c,i)=>c.classList.toggle('on',i===ri)); }

/* ---- category legend (drives the byte colours) ---- */
const CATS=[['index','station index'],['pointer','pointer / offset'],
  ['len','length / count'],['time','time / runtime'],['mask','day mask'],
  ['foot','footnote'],['train','train number'],['text','text'],
  ['price','price'],['value','signed value'],['marker','marker / structure']];
const lg=document.getElementById('legend');
CATS.forEach(([c,label])=>{ const s=document.createElement('span');
  s.style.setProperty('--c',catVar(c)); s.innerHTML=`<i></i>${label}`; lg.appendChild(s); });

/* ---- column header ---- */
let colh='';
for(let c=0;c<16;c++) colh+=`<span class="hb pad${c===8?' gap':''}">${hx(c,2)}</span>`;
document.getElementById('colhdr').innerHTML=colh;

/* ---- virtualized whole-file dump ---- */
const ROWH=22, COLS=16, PAD=8;
const scroll=document.getElementById('hvscroll');
const sizer=document.getElementById('hvsizer');
const rows=document.getElementById('hvrows');
const totalRows=Math.ceil(N/COLS);
sizer.style.height=(totalRows*ROWH)+'px';
const escMap={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',' ':'&nbsp;'};
const esc=ch=>escMap[ch]||ch;
function render(){
  const st=scroll.scrollTop, vh=scroll.clientHeight;
  const first=Math.max(0,Math.floor(st/ROWH)-PAD);
  const last=Math.min(totalRows-1,Math.ceil((st+vh)/ROWH)+PAD);
  let html='';
  for(let r=first;r<=last;r++){
    const base=r*COLS; let hex='',asc='';
    for(let c=0;c<COLS;c++){
      const o=base+c, gap=c===8?' gap':'';
      if(o>=N){ hex+=`<span class="hb pad${gap}"> </span>`; continue; }
      const fi=byteField[o], cat=TYPES[ftype[fi]].cat, fst=(o===fstart[fi])?' fst':'';
      hex+=`<span class="hb c-${cat}${gap}${fst}" data-o="${o}">${hx(BYTES[o],2)}</span>`;
      const v=BYTES[o];
      asc+= (v>=32&&v<127)?`<b data-o="${o}">${esc(String.fromCharCode(v))}</b>`
                          :`<span class="np" data-o="${o}">·</span>`;
    }
    html+=`<div class="hv-row"><span class="hv-off">0x${hx(base,5)}</span>`+
          `<span class="hv-hex">${hex}</span><span class="hv-asc">${asc}</span></div>`;
  }
  rows.style.transform=`translateY(${first*ROWH}px)`;
  rows.innerHTML=html;
  if(pinned>=0) paintRange(pinned);
  markMap(Math.min(Math.floor((st+vh/2)/ROWH)*COLS,N-1));
}
let raf=null;
scroll.addEventListener('scroll',()=>{ if(raf)return;
  raf=requestAnimationFrame(()=>{raf=null;render();}); });
new ResizeObserver(render).observe(scroll);

/* ---- hover / inspect ---- */
const status=document.getElementById('status');
let pinned=-1;
function paintRange(fi){
  const s=fstart[fi], e=fstart[fi+1];
  rows.querySelectorAll('.on').forEach(x=>x.classList.remove('on'));
  rows.querySelectorAll('[data-o]').forEach(el=>{
    const o=+el.dataset.o; if(o>=s&&o<e) el.classList.add('on'); });
}
function inspect(o){
  const fi=byteField[o], t=ftype[fi], r=regionAt(o), cat=TYPES[t].cat;
  const s=fstart[fi], len=fstart[fi+1]-s;
  status.style.setProperty('--c',catVar(cat));
  const rng = len===1?`byte 0x${hx(s,5)}`:`0x${hx(s,5)}–0x${hx(fstart[fi+1]-1,5)} · ${len} B`;
  status.innerHTML=`<span class="o">0x${hx(o,5)}</span>`+
    `<span class="pill">${r.name}</span>`+
    `<span class="fld">${TYPES[t].name}</span>`+
    `<span class="val">= ${decodeField(fi)}</span>`+
    `<span class="muted">${rng}</span>`;
  pinned=fi; paintRange(fi); markMap(o);
}
rows.addEventListener('mouseover',e=>{ const el=e.target.closest('[data-o]');
  if(el) inspect(+el.dataset.o); });
scroll.addEventListener('mouseleave',()=>{
  pinned=-1; rows.querySelectorAll('.on').forEach(x=>x.classList.remove('on'));
  status.style.removeProperty('--c');
  status.innerHTML='<span class="hint">Hover any byte to inspect it.</span>'; });

render();

function jumpTo(off){
  document.getElementById('whole').scrollIntoView({behavior:'smooth',block:'start'});
  scroll.scrollTop=Math.max(0,(Math.floor(off/COLS)-2))*ROWH;
  render(); inspect(off);
}

/* ================= annotated specimen cards ================= */
const SPECCATS=[['pointer','pointer / offset'],['index','station index'],
  ['len','length / count'],['time','time / day'],['mask','day mask'],
  ['foot','footnote'],['train','train number'],['text','text'],
  ['price','price'],['value','signed value'],['marker','marker / zero']];
const leg2=document.getElementById('leg2');
SPECCATS.forEach(([c,label])=>{ const s=document.createElement('span');
  s.style.setProperty('--c',catVar(c)); s.innerHTML=`<i></i>${label}`; leg2.appendChild(s); });
const specHost=document.getElementById('specimens');
function fmap(fields){const m={};fields.forEach((f,i)=>{for(let k=f[0];k<f[0]+f[1];k++)m[k]=i;});return m;}
function renderDump(bytes,fields,base,specId,tag){
  const fm=fmap(fields), perRow=16;
  const tbl=document.createElement('table'); tbl.className='dump';
  for(let row=0;row*perRow<bytes.length;row++){
    const tr=document.createElement('tr');
    const off=document.createElement('td'); off.className='off';
    off.textContent='0x'+hx(base+row*perRow,5); tr.appendChild(off);
    const hxtd=document.createElement('td');
    const wrap=document.createElement('span'); wrap.className='hexcell';
    const asc=document.createElement('td'); asc.className='asc';
    for(let c=0;c<perRow;c++){
      const idx=row*perRow+c; if(idx>=bytes.length) break;
      const fi=fm[idx], b=bytes[idx];
      const cell=document.createElement('span');
      cell.className='byte'+(c===8?' gap':''); cell.textContent=hx(b,2);
      if(fi!==undefined){ cell.style.setProperty('--c',catVar(fields[fi][2]));
        cell.dataset.fi=fi; cell.dataset.spec=specId; cell.dataset.tag=tag; }
      else{ cell.style.setProperty('--c','var(--cat-marker)'); cell.style.opacity=.4; }
      wrap.appendChild(cell);
      const a=document.createElement('span');
      a.textContent=(b>=32&&b<127)?String.fromCharCode(b):'·';
      if(fi!==undefined&&fields[fi][2]==='text'){ a.className='ascb';
        a.dataset.fi=fi; a.dataset.spec=specId; a.dataset.tag=tag; }
      asc.appendChild(a);
    }
    hxtd.appendChild(wrap); tr.appendChild(hxtd); tr.appendChild(asc); tbl.appendChild(tr);
  }
  return tbl;
}
DATA.specimens.forEach(spec=>{
  const el=document.createElement('div'); el.className='spec';
  el.innerHTML=`<div class="spec-head"><h3>${spec.title}</h3>
    <span class="kick">${spec.kicker}</span></div><p class="lead">${spec.lead}</p>`;
  const jb=document.createElement('button'); jb.className='jump';
  jb.textContent='▸ show in full dump';
  jb.addEventListener('click',()=>jumpTo(spec.base)); el.appendChild(jb);
  const dw=document.createElement('div'); dw.className='dumpwrap';
  dw.appendChild(renderDump(spec.bytes,spec.fields,spec.base,spec.id,'main'));
  if(spec.extra){ const sl=document.createElement('div'); sl.className='sublabel';
    sl.textContent='↓ '+spec.extra.label+'  ·  0x'+hx(spec.extra.base,5);
    dw.appendChild(sl);
    dw.appendChild(renderDump(spec.extra.bytes,spec.extra.fields,spec.extra.base,spec.id,'extra')); }
  el.appendChild(dw);
  const det=document.createElement('div'); det.className='detail'; det.id='det-'+spec.id;
  det.innerHTML='<div class="hint">Hover a byte to read the field it belongs to.</div>';
  el.appendChild(det); specHost.appendChild(el);
});
function fieldsFor(spec,tag){const s=DATA.specimens.find(x=>x.id===spec);
  return tag==='extra'?s.extra.fields:s.fields;}
function clearSpec(spec){
  document.querySelectorAll(`.byte[data-spec="${spec}"],.ascb[data-spec="${spec}"]`)
    .forEach(b=>{b.classList.remove('on');b.classList.remove('dim');});
  const det=document.getElementById('det-'+spec); det.style.removeProperty('--c');
  det.innerHTML='<div class="hint">Hover a byte to read the field it belongs to.</div>'; }
function focusField(spec,tag,fi){
  const f=fieldsFor(spec,tag)[fi], cat=f[2];
  document.querySelectorAll(`.byte[data-spec="${spec}"]`).forEach(b=>{
    if(b.dataset.tag===tag&&b.dataset.fi==fi){b.classList.add('on');b.classList.remove('dim');}
    else b.classList.add('dim'); });
  document.querySelectorAll(`.ascb[data-spec="${spec}"]`).forEach(a=>{
    a.classList.toggle('on',a.dataset.tag===tag&&a.dataset.fi==fi); });
  const det=document.getElementById('det-'+spec); det.style.setProperty('--c',catVar(cat));
  const start=f[0],end=f[0]+f[1]-1, rng=f[1]===1?`byte ${start}`:`bytes ${start}–${end}`;
  det.innerHTML=`<div class="drow"><span class="lbl">${f[3]}</span>
    <span class="val">${f[4]}</span><span class="rng">${rng} · ${f[1]}×</span></div>
    <p class="note">${f[5]}</p>`; }
document.addEventListener('mouseover',e=>{ const t=e.target.closest('.byte,.ascb');
  if(!t||t.dataset.fi===undefined) return; focusField(t.dataset.spec,t.dataset.tag,+t.dataset.fi); });
document.addEventListener('mouseout',e=>{ const t=e.target.closest('.byte,.ascb');
  if(!t||t.dataset.spec===undefined) return;
  const to=e.relatedTarget&&e.relatedTarget.closest&&e.relatedTarget.closest('.byte,.ascb');
  if(to&&to.dataset.spec===t.dataset.spec) return; clearSpec(t.dataset.spec); });
document.addEventListener('click',e=>{ const t=e.target.closest('.byte,.ascb');
  if(!t||t.dataset.fi===undefined) return; focusField(t.dataset.spec,t.dataset.tag,+t.dataset.fi); });
</script>
"""

DOC = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
       '<title>INLEES.NET — every byte annotated</title>\n</head>\n<body>\n'
       '__FRAG__\n</body>\n</html>\n')

frag = (FRAG
        .replace("__DATA__", DATA)
        .replace("__B64__", B64)
        .replace("__FIELDS__", FIELDS_B64)
        .replace("__SIZE__", f"{FILESIZE:,}")
        .replace("__NFIELDS__", f"{len(FIELDS):,}")
        .replace("__ENDHEX__", f"{FILESIZE:X}"))

FRAG_OUT = os.path.join(REPO, "docs", "format_explainer.frag.html")
with open(OUT, "w") as f:
    f.write(DOC.replace("__FRAG__", frag))
with open(FRAG_OUT, "w") as f:
    f.write(frag)
print(f"wrote {OUT} (standalone)")
print(f"wrote {FRAG_OUT} (artifact fragment)")
print(f"file={FILESIZE:,}  fields={len(FIELDS):,}  frag={len(frag):,} bytes")
