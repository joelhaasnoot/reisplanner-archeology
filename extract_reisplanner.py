#!/usr/bin/env python3
"""
extract_reisplanner.py — extract the NS Reisplanner 90/91 schedule from INLEES.NET.

Produces raw CSVs (stations, boards, holidays, footnotes, fares) and a GTFS feed.
All offsets/structure come from reverse-engineering REISPLAN.EXE — see FORMAT.md / BINARY.md.

Usage:  python3 extract_reisplanner.py [edition]
        edition defaults to "90-91"; reads input/<edition>/INLEES.NET,
        writes output/<edition>/.  (Only 90-91 exists today; add more under input/.)

NOTE: the section offsets below were reverse-engineered from the 90-91 edition. A
different edition may need its offsets re-derived (see docs/FORMAT.md).
"""
import struct, csv, os, sys
from datetime import date, timedelta

# ── paths ────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
EDITION = sys.argv[1] if len(sys.argv) > 1 else "90-91"
INLEES = os.path.join(HERE, "input", EDITION, "INLEES.NET")
OUT = os.path.join(HERE, "output", EDITION)
GTFS = os.path.join(OUT, "gtfs")

# ── file structure constants (recovered from the loader) ─────────────────────
SECA_BASE = 0x3c        # section A (connection graph) base for board offsets
SECC      = 0x33544     # section C: station name/code records (exact
                        # start from the loader read sequence; the old 0x33540
                        # was 4 bytes early and broke the record alignment)
SECC_END  = 0x37554
TBL       = 0x37e2c     # 469×34 station master table
NREC      = 469
# The loader's read sequence accounts for every byte of the file, which is how
# these were pinned down (see docs/BINARY.md):
#   A,B,C,D, station table, then NAMEHASH, FNIDX, DATECAL, FARES, and 3 more.
NAMEHASH  = 0x3bc76     # 1024 far pointers into section C: station-name hash.
                        # NOT footnote bitmaps -- reading it as 47-byte bitmaps
                        # produced a plausible-looking but entirely fake
                        # footnotes.csv until the loader was disassembled.
FNIDX     = 0x3cc76     # 250 u32: footnote -> 1-based offset into section D
SECD      = 0x37554     # section D: 8-byte footnote records (see footnote_days)
DATECAL   = 0x3d05e     # 371×2 per-date day-type calendar
FARES     = 0x3d344     # 36×18 fare table (distance -> prices in cents)
NFOOTNOTE = 89          # populated slots in FNIDX
NFARE     = 36
VALID_START = date(1990, 5, 27)
NDAYS       = 371       # header field 0x20-region: days in validity window
VALID_END   = VALID_START + timedelta(days=NDAYS - 1)

DAYMASKS = {0x0f,0x1f,0x2f,0x3f,0x4f,0x5f,0x6f,0x7f,
            0x8f,0x9f,0xaf,0xbf,0xcf,0xdf,0xef,0xff}

d = open(INLEES, "rb").read()
u16 = lambda o: struct.unpack_from("<H", d, o)[0]
u32 = lambda o: struct.unpack_from("<I", d, o)[0]
os.makedirs(GTFS, exist_ok=True)

def hhmm(t): return f"{t//60:02d}:{t%60:02d}"
def gtfs_time(t): return f"{t//60:02d}:{t%60:02d}:00"
def daystr(m): return "".join("MTWTFSS"[b] for b in range(7) if (m>>b)&1) + ("H" if m&0x80 else "")

# ── 1. stations (469, alphabetical) ──────────────────────────────────────────
# field[26] is 1-BASED like field[22], so the record header sits at
# SECC + off + 1:  [u16][u16 station_idx][u16][name, NUL-padded], with the
# station code in a NUL-padded field 6 bytes (u16 + u32) before the header.
# Verified: the index word equals the station's own number for 469/469.
#
# The previous version scanned a +-44 byte window for text runs and guessed
# which was the name and which the code. That mispaired them whenever a name
# was long or a neighbouring record intruded: 119 of 469 names came out as the
# station CODE instead ("amld" for Ameland, "rtbw" for Rotterdam Bergweg,
# "holw" for Holwerd), which also made those stations unfindable by name.
def parse_name_code(secc_off):
    """field[26] -> (code, display name) from the section-C record."""
    b = SECC + secc_off + 1
    end = d.index(b"\x00", b + 6)
    name = d[b+6:end].decode("latin1")
    q = b - 7                                   # skip the 6-byte header
    while q > SECC and d[q] == 0:
        q -= 1
    st = q
    while st > SECC and 32 <= d[st-1] < 127:
        st -= 1
    return d[st:q+1].decode("latin1"), name

stations = []
for i in range(NREC):
    off = TBL + i*34
    board_off = u32(off+22)   # -> section A board
    name_off  = u32(off+26)   # -> section C name/code
    aux_off   = u32(off+30)   # -> section B (sparse)
    code, name = parse_name_code(name_off)
    stations.append(dict(idx=i, code=code, name=name, board=board_off, aux=aux_off))

# field[22] is 1-based; a node's real file offset is SEG_BASE + field22 (see 2b).
# Export the RESOLVED offset: emitting the raw field value under the name
# "board_secA_offset" made this column 0x3b too low for every consumer of the CSV
# (verified against debugger dumps of the live program -- see docs/BINARY.md).
SEG_BASE = 0x3b
with open(os.path.join(OUT, "stations.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["idx","code","name","field22_raw","board_secA_offset"])
    for s in stations:
        w.writerow([s["idx"], s["code"], s["name"], s["board"], SEG_BASE + s["board"]])

# ── 2. all connection-graph events (section A), UNLABELLED ───────────────────
# A raw byte-level scan for 6-byte event records, kept as a cross-check only.
# The real timetable now comes from section 6 (tools/decode_timetable.py), which
# parses the node records properly and DOES attribute every departure to its
# station. This scan finds the same bytes without knowing which station they
# belong to, so it stays useful for spotting anything the parser skips.
def all_events():
    o, out = SECA_BASE, []
    while o + 6 <= SECA_BASE + 201578:
        b0, b1, t = d[o], d[o+1], u16(o+4)
        if b1 in DAYMASKS and 0 < t <= 1440:
            out.append((o, b0, b1, u16(o+2), t)); o += 6
        else:
            o += 1
    return out
EVENTS = all_events()
with open(os.path.join(OUT, "connection_events.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["secA_offset","time","days","holiday","footnote","ref"])
    for o, b0, b1, ref, t in EVENTS:
        w.writerow([o, hhmm(t), daystr(b1), "Y" if b1&0x80 else "", b0 or "", f"0x{ref:04x}"])

# ── 2b. network topology (route-graph adjacency nodes, field[22], 1-based) ───
# field[22] is 1-based: node = section-A + (field22 - 1)  (base 0x3b in file terms).
# node[0],node[1] = the station's line-SEGMENT endpoints (station indices).
from collections import defaultdict
segs = defaultdict(list)                      # SEG_BASE defined above (section 1)
for i, s in enumerate(stations):
    p = SEG_BASE + s["board"]
    a, b = struct.unpack_from("<HH", d, p)
    if a < NREC and b < NREC and a != b:
        segs[tuple(sorted((a, b)))].append(i)
with open(os.path.join(OUT, "network_segments.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["segment","endpoint_a","endpoint_b","num_stations","member_stations"])
    for k in sorted(segs, key=lambda k: -len(segs[k])):
        w.writerow([f"{stations[k[0]]['code']}-{stations[k[1]]['code']}",
                    stations[k[0]]["name"], stations[k[1]]["name"], len(segs[k]),
                    "; ".join(sorted(stations[m]["name"] for m in segs[k]))])

# ── 3. date calendar -> holidays ─────────────────────────────────────────────
BIT2DOW = {8:0,9:1,10:2,11:3,12:4,13:5,14:6}       # calendar bits 8..14 = Mon..Sun
def cal_dow(i):
    v = u16(DATECAL + i*2)
    sb = [b for b in range(8,16) if (v>>b)&1]
    return BIT2DOW.get(sb[0]) if sb else None
# NOTE: the file has NO holiday names — only the dates (which get folded to Sunday).
# The 'name_inferred' column below is annotated by THIS SCRIPT from known Dutch
# 1990-91 holidays; it is NOT extracted from INLEES.NET.
HOLIDAY_NAMES = {
    "1990-06-04":"Tweede Pinksterdag","1990-12-25":"Eerste Kerstdag",
    "1990-12-26":"Tweede Kerstdag","1991-01-01":"Nieuwjaarsdag",
    "1991-04-01":"Tweede Paasdag","1991-04-30":"Koninginnedag",
    "1991-05-09":"Hemelvaartsdag","1991-05-20":"Tweede Pinksterdag",
}
DAYS = "Mon Tue Wed Thu Fri Sat Sun".split()
holidays = []   # (date, served_as_dow)  — dates ARE from the data; names are inferred
with open(os.path.join(OUT, "holidays.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date","weekday","served_as","name_inferred"])
    for i in range(NDAYS):
        dt = VALID_START + timedelta(days=i); cal = cal_dow(i)
        if cal is not None and cal != dt.weekday():
            holidays.append((dt, cal))
            w.writerow([dt, DAYS[dt.weekday()], DAYS[cal],
                        HOLIDAY_NAMES.get(str(dt), "(day-after shift)")])

# ── 4. footnotes -> exact running dates ──────────────────────────────────────
# footnote F -> FNIDX[F] -> a backward-linked chain of 8-byte section-D records,
# each (u32 next_1based, u16 first_day, u16 last_day) over the 371-day window.
# Footnote 0 means "no footnote": the service is not date-restricted.
def footnote_days(F):
    off = u32(FNIDX + F*4)
    days, seen = set(), set()
    while off:
        p = off - 1
        if p in seen or p + 8 > 2264:      # section D is 2264 bytes
            break
        seen.add(p)
        nxt, a, b = struct.unpack_from("<IHH", d, SECD + p)
        if a <= b < NDAYS:
            days.update(range(a, b + 1))
        off = nxt
    return sorted(days)
def to_ranges(days_idx):
    r, k = [], 0
    while k < len(days_idx):
        a = days_idx[k]; b = a
        while k+1 < len(days_idx) and days_idx[k+1] == b+1: k += 1; b = days_idx[k]
        d0, d1 = VALID_START+timedelta(days=a), VALID_START+timedelta(days=b)
        r.append(str(d0) if d0 == d1 else f"{d0}..{d1}"); k += 1
    return r
with open(os.path.join(OUT, "footnotes.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["footnote","days_running","date_ranges"])
    for F in range(1, NFOOTNOTE):
        di = footnote_days(F)
        if di: w.writerow([F, len(di), "; ".join(to_ranges(di))])

# ── 5. fare tables (distance -> prices in cents) ─────────────────────────────
# Three tables share one km ladder and one column convention
# (2e, reduced 2e, 1e, reduced 1e) per product. The product names are the
# planner's own, lifted from the string table in REISPLAN.EXE at 0x15b09:
#   "2e Klas" "1e Klas" / "Enkele reis" "Retour" "Reductie enkele reis"
#   "Reductie retour" / "Weektrajectkaart" "Red. Weektrajectkaart"
#   "Maandtrajectkaart" "Red. Maandtrajectkaart" / "Jeugdmaandkaart"
#   "Red. Jeugdmaandkaart".
#
# Season tickets ARE stored, contrary to an earlier note here that they were
# derived. The derivation rules do hold as arithmetic -- week = retour x 4
# exactly, maand = retour x 15 rounded to the nearest 100, jeugdmaand =
# retour x 12 -- but the tables are tabulated and the rounding means the rules
# alone do not reproduce them.
#
# The season tables stop at 72 km (13 bands + an 0xffff catch-all) because
# those products were not sold beyond that distance; the single/return table
# runs the full 36 bands.
SEASON = 0x3d5cc        # 14 x 18: week- and maandtrajectkaart
JEUGD  = 0x3d6c8        # 14 x 6 : jeugdmaandkaart (2e klas only)
NSEASON = 14
FARE_COLS = ["enkele_2e","red_enkele_2e","enkele_1e","red_enkele_1e",
             "retour_2e","red_retour_2e","retour_1e","red_retour_1e"]
SEASON_COLS = ["week_2e","red_week_2e","week_1e","red_week_1e",
               "maand_2e","red_maand_2e","maand_1e","red_maand_1e"]
JEUGD_COLS  = ["jeugdmaand_2e","red_jeugdmaand_2e"]
def band(v): return "" if v == 0xffff else v      # 0xffff = "and above"
with open(os.path.join(OUT, "fares.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["distance_km_max"] + [c+"_cents" for c in FARE_COLS])
    for i in range(NFARE):
        r = struct.unpack_from("<9H", d, FARES + i*18)
        w.writerow([band(r[0])] + list(r[1:]))
with open(os.path.join(OUT, "season_tickets.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["distance_km_max"] + [c+"_cents" for c in SEASON_COLS + JEUGD_COLS])
    for i in range(NSEASON):
        a = struct.unpack_from("<9H", d, SEASON + i*18)
        b = struct.unpack_from("<3H", d, JEUGD + i*6)
        w.writerow([band(a[0])] + list(a[1:]) + list(b[1:]))

# Journey-time corrections: 24 x 6 = [u16 from][u16 to][s16 minutes]. These are
# per-relation adjustments the planner adds on top of the summed segment times
# for a handful of specific station pairs (major relations plus the Lauwersoog
# ferry-bus). Pairs are listed in both directions; most are symmetric, but
# Amsterdam Muiderpoort<->Arnhem is not (-9 vs -1), so the value is directed.
LINKCORR = 0x3d71c
NLINKCORR = 24
with open(os.path.join(OUT, "link_corrections.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["from_idx","from_name","to_idx","to_name","correction_min"])
    for i in range(NLINKCORR):
        a, b, v = struct.unpack_from("<HHh", d, LINKCORR + i*6)
        w.writerow([a, stations[a]["name"], b, stations[b]["name"], v])

# ── 6. GTFS feed ─────────────────────────────────────────────────────────────
def article(nm):
    import re
    mo = re.match(r"^(.*) \('([a-z])\)$", nm)
    return (f"'{mo.group(2)}-"+mo.group(1)).title() if mo else nm.title()

def wr(fn, header, rows):
    with open(os.path.join(GTFS, fn), "w", newline="") as f:
        w = csv.writer(f); w.writerow(header)
        for r in rows: w.writerow(r)

wr("agency.txt", ["agency_id","agency_name","agency_url","agency_timezone","agency_lang"],
   [["NS","Nederlandse Spoorwegen","https://www.ns.nl","Europe/Amsterdam","nl"]])

wr("feed_info.txt",
   ["feed_publisher_name","feed_publisher_url","feed_lang",
    "feed_start_date","feed_end_date","feed_version"],
   [["reisplanner-archeology — decoded from NS Reisplanner 90/91",
     "https://github.com/joelhaasnoot/reisplanner-archeology", "nl",
     VALID_START.strftime("%Y%m%d"), VALID_END.strftime("%Y%m%d"), "90-91"]])

# Station coordinates are NOT in INLEES.NET; they are joined in by NS station
# code from an external rail-station dataset, with Wikipedia filling the rest
# (foreign, closed-line, ferry and bus stops). Provenance per station lives in
# data/station_coords*.csv — see data/README.md.
COORDS = {}
for fn in ("station_coords.csv", "station_coords_wikipedia.csv"):
    p = os.path.join(HERE, "data", fn)
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            COORDS.setdefault(int(r["idx"]), (r["lat"], r["lon"]))
wr("stops.txt", ["stop_id","stop_code","stop_name","stop_lat","stop_lon"],
   [[f"ns{s['idx']}", s["code"], article(s["name"]),
     COORDS.get(s["idx"], ("", ""))[0], COORDS.get(s["idx"], ("", ""))[1]]
    for s in stations])

# calendar: one service per weekly day-mask seen in the connection graph
masks = set(b1 for o,b0,b1,ref,t in EVENTS)
def cal_row(m):
    days = [(m>>b)&1 for b in range(7)]
    return [f"m{m:02x}"] + days + [VALID_START.strftime("%Y%m%d"), VALID_END.strftime("%Y%m%d")]
wr("calendar.txt",
   ["service_id","monday","tuesday","wednesday","thursday","friday","saturday","sunday","start_date","end_date"],
   [cal_row(m) for m in sorted(masks)])

# calendar_dates: holidays run Sunday service -> add to Sunday-masks, remove from others
cd = []
for m in sorted(masks):
    sid = f"m{m:02x}"; runs_sun = (m>>6)&1
    for dt, served in holidays:
        ymd = dt.strftime("%Y%m%d"); nat = dt.weekday()
        in_mask_natural = (m>>nat)&1
        in_mask_served  = (m>>served)&1
        if in_mask_served and not in_mask_natural: cd.append([sid, ymd, 1])   # added
        elif in_mask_natural and not in_mask_served: cd.append([sid, ymd, 2]) # removed
wr("calendar_dates.txt", ["service_id","date","exception_type"], cd)

# fare_attributes / fare_rules from the fare table (2e klas enkele reis = price col 2)
wr("fare_attributes.txt",
   ["fare_id","price","currency_type","payment_method","transfers","agency_id"],
   [[f"km{struct.unpack_from('<H',d,FARES+i*18)[0]}",
     f"{struct.unpack_from('<9H',d,FARES+i*18)[2]/100:.2f}","NLG","1","","NS"]
    for i in range(NFARE)])

# trips + stop_times: the FULL decoded timetable.
# tools/decode_timetable.py reads the section-A node records directly (format
# recovered from the search engine at 0x6ae6 / 0x6889 — see docs/BINARY.md) and
# returns every train with its stops. Validated stop-by-stop against the
# emulator's own answers (fixtures/train_8917_sun.csv).
sys.path.insert(0, os.path.join(HERE, "tools"))
import decode_timetable as tt

station_rows = {s["idx"]: {"name": s["name"],
                           "board_secA_offset": SEG_BASE + s["board"]}
                for s in stations}
journeys = tt.decode(d, station_rows)

# One GTFS route per origin/destination pair, so the feed stays navigable.
routes, route_id, trips, stimes, services, tripseq = [], {}, [], [], {}, {}
calls = defaultdict(list)          # (train number, station idx) -> trip_ids
tripdays = {}                      # trip_id -> its 371-day running set
for j in sorted(journeys, key=lambda x: (x["train"], x["stops"][0][2])):
    a, b = j["stops"][0][0], j["stops"][-1][0]
    key = (a, b)
    if key not in route_id:
        route_id[key] = f"R{len(route_id)}"
        routes.append([route_id[key], "NS", "",
                       f"{article(stations[a]['name'])} - {article(stations[b]['name'])}",
                       "2"])
    base = f"t{j['train']}_{j['stops'][0][2]}"
    tripseq[base] = tripseq.get(base, 0) + 1
    tid = base if tripseq[base] == 1 else f"{base}_{tripseq[base]}"
    # A service is the weekly mask NARROWED by the footnote's exact dates, so
    # each (mask, footnote) pair is its own GTFS service.
    # A journey's real service is its concrete 371-day set: legs can carry
    # different footnotes, so no single footnote number describes the whole
    # trip (train 2526 is daily to Venlo but its Dutch legs skip 24 Dec).
    svc = f"s{abs(hash(j['days'])) % (1 << 32):08x}"
    services[svc] = j["days"]
    trips.append([route_id[key], svc, tid,
                  article(stations[b]["name"]), "0", str(j["train"])])
    for seq, (sta, arr, dep) in enumerate(j["stops"], 1):
        stimes.append([tid, gtfs_time(arr if arr is not None else dep),
                       gtfs_time(dep if dep is not None else arr),
                       f"ns{sta}", seq])
        calls[(j["train"], sta)].append(tid)
    tripdays[tid] = j["days"]

# Rewrite the calendar over the services the timetable actually uses: the
# weekly pattern goes in calendar.txt, and the footnote's excluded dates become
# calendar_dates.txt removals (on top of the holiday shifts computed above).
cal, cdates = [], list(cd)
for svc, bits in sorted(services.items()):
    # Weekly pattern the day set forms, plus explicit removals for the days the
    # footnotes knock out. Exact, and compact because footnotes drop few days.
    mask = 0
    for i in range(NDAYS):
        if (bits >> i) & 1:
            mask |= 1 << ((VALID_START + timedelta(days=i)).weekday())
    days = [(mask >> b) & 1 for b in range(7)]
    cal.append([svc] + days + [VALID_START.strftime("%Y%m%d"),
                               VALID_END.strftime("%Y%m%d")])
    for i in range(NDAYS):
        dt = VALID_START + timedelta(days=i)
        if days[dt.weekday()] and not (bits >> i) & 1:
            cdates.append([svc, dt.strftime("%Y%m%d"), 2])
wr("calendar.txt",
   ["service_id","monday","tuesday","wednesday","thursday","friday","saturday","sunday","start_date","end_date"],
   cal)
wr("calendar_dates.txt", ["service_id","date","exception_type"], cdates)
wr("routes.txt", ["route_id","agency_id","route_short_name","route_long_name","route_type"], routes)
wr("trips.txt", ["route_id","service_id","trip_id","trip_headsign","direction_id","trip_short_name"], trips)
wr("stop_times.txt", ["trip_id","arrival_time","departure_time","stop_id","stop_sequence"], stimes)

# ── 7. minimum transfer times (section B) ────────────────────────────────────
# Station field +30 -> a per-station array of 6-byte records
# (u16 train_a, u16 train_b, u16 minutes), terminated by 0xffff. The lookup at
# 0x5b43 returns the stored value for a matching train PAIR and otherwise
# defaults to 2, so this is a minimum-transfer-time override, not a list of
# guaranteed connections: the stored values are 1 minute (1367 records) and
# 0 (51), i.e. pairs that are allowed to connect tighter than normal.
SECB = 0x313a6
tr_map, raw_tr = {}, []
for st in stations:
    a = st["aux"]
    if not a:
        continue
    off = SECB + a - 1
    k = 0
    while off + k*6 + 6 <= SECB + 8606:
        ta, tb, mins = struct.unpack_from("<3H", d, off + k*6)
        if ta == 0xffff:
            break
        k += 1
        raw_tr.append([st["idx"], st["name"], ta, tb, mins])
        # A train number can have several day-pattern variants, so only pair
        # trips that actually run on some common day -- otherwise the cross
        # product invents connections between services that never coexist
        # (which showed up as negative arrival->departure gaps).
        for f in calls.get((ta, st["idx"]), []):
            for t in calls.get((tb, st["idx"]), []):
                if f != t and (tripdays.get(f, 0) & tripdays.get(t, 0)):
                    # The same (from_trip, to_trip) at a station can be produced
                    # by more than one raw record; keep the tightest time so the
                    # GTFS composite key (from/to stop + from/to trip) is unique.
                    key = (st["idx"], f, t)
                    if key not in tr_map or mins < tr_map[key]:
                        tr_map[key] = mins
with open(os.path.join(OUT, "transfers.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["station_idx","station","train_a","train_b","min_transfer_min"])
    for r in raw_tr:
        w.writerow(r)
transfers = [[f"ns{s}", f"ns{s}", f, t, "2", m * 60]
             for (s, f, t), m in tr_map.items()]
wr("transfers.txt",
   ["from_stop_id","to_stop_id","from_trip_id","to_trip_id","transfer_type","min_transfer_time"],
   transfers)

# ── summary ──────────────────────────────────────────────────────────────────
print(f"stations : {len(stations)}")
print(f"segments : {len(segs)} line segments ({sum(len(v) for v in segs.values())}/{NREC} stations placed)")
print(f"events   : {len(EVENTS)} connection-graph nodes (unlabelled)")
print(f"timetable: {len(journeys)} trains decoded, {sum(len(j['stops']) for j in journeys)} stops")
print(f"holidays : {len(holidays)}")
print(f"footnotes: {sum(1 for F in range(1,86) if footnote_days(F))}")
print(f"fares    : {NFARE} distance bands + {NSEASON} season-ticket bands")
print(f"transfers: {len(raw_tr)} min-transfer records at {sum(1 for s in stations if s['aux'])} stations -> {len(transfers)} GTFS transfers")
print(f"GTFS     : {len(stations)} stops, {len(routes)} routes, {len(trips)} trips, {len(stimes)} stop_times")
print(f"output   : {OUT}  (+ {GTFS})")
