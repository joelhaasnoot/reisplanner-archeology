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
SECC      = 0x33540     # section C: station name/code records
SECC_END  = 0x37554
TBL       = 0x37e2c     # 469×34 station master table
NREC      = 469
T1        = 0x3bc76     # footnote date bitmaps, 47 bytes (371 bits) each
DATECAL   = 0x3d05e     # 371×2 per-date day-type calendar
FARES     = 0x3d344     # 36×18 fare table (distance -> prices in cents)
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
def parse_name_code(secc_off):
    """field[26] points into a section-C record; pull code + display name."""
    import re
    p = SECC + secc_off
    win = d[max(SECC,p-8):min(SECC_END, p+44)]
    runs = [m.group().decode("latin1").strip()
            for m in re.finditer(rb"[a-z][a-z0-9 '()\./\-]*", win)]
    runs = [r for r in runs if r]
    name = next((r for r in runs if " " in r or len(r) >= 4), runs[0] if runs else "")
    code = next((r for r in runs if " " not in r and 1 < len(r) <= 6), "")
    return code, name

stations = []
for i in range(NREC):
    off = TBL + i*34
    board_off = u32(off+22)   # -> section A board
    name_off  = u32(off+26)   # -> section C name/code
    aux_off   = u32(off+30)   # -> section B (sparse)
    code, name = parse_name_code(name_off)
    stations.append(dict(idx=i, code=code, name=name, board=board_off, aux=aux_off))

with open(os.path.join(OUT, "stations.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["idx","code","name","board_secA_offset"])
    for s in stations: w.writerow([s["idx"], s["code"], s["name"], s["board"]])

# ── 2. all connection-graph events (section A), UNLABELLED ───────────────────
# NOTE: field[22] points to a per-station *header* (platform data), and the
# header -> departure-board link is not yet decoded, so events cannot be reliably
# attributed to stations network-wide. We therefore dump every section-A node
# (time, running-days, footnote, train ref) WITHOUT a station label. Individual
# lines can still be reconstructed by content/time-matching (see trips below).
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
SEG_BASE = 0x3b
segs = defaultdict(list)
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
def footnote_days(F):
    bm = d[T1 + F*47 : T1 + F*47 + 47]
    return [i for i in range(NDAYS) if (bm[i//8] >> (i%8)) & 1]
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
    for F in range(1, 86):
        di = footnote_days(F)
        if di: w.writerow([F, len(di), "; ".join(to_ranges(di))])

# ── 5. fare table (distance -> single/return prices in cents) ────────────────
# 8 stored fares per band. Season tickets are NOT stored — the binary derives them:
#   weektrajectkaart = retour*4 ; maandtrajectkaart = retour*15 ; jeugdmaandkaart = maand*0.8
FARE_COLS = ["enkele_2e","red_enkele_2e","enkele_1e","red_enkele_1e",
             "retour_2e","red_retour_2e","retour_1e","red_retour_1e"]
with open(os.path.join(OUT, "fares.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["distance_km_max"] + [c+"_cents" for c in FARE_COLS])
    for i in range(NFARE):
        w.writerow(list(struct.unpack_from("<9H", d, FARES + i*18)))

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

wr("stops.txt", ["stop_id","stop_code","stop_name","stop_lat","stop_lon"],
   [[f"ns{s['idx']}", s["code"], article(s["name"]), "", ""] for s in stations])

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
wr("fare_attributes.txt", ["fare_id","price","currency_type","payment_method","transfers"],
   [[f"km{struct.unpack_from('<H',d,FARES+i*18)[0]}",
     f"{struct.unpack_from('<9H',d,FARES+i*18)[2]/100:.2f}","NLG","1",""] for i in range(NFARE)])

# trips + stop_times: VALIDATED sample trips.
# (A network-wide auto-dump needs the header->board link, still open — see README.)
# All trips below are self-labelled from the planner's own output (ground truth).
def sidx(name): return next(s["idx"] for s in stations if s["name"] == name)
routes = [["R_lw_stv","NS","","Leeuwarden - Stavoren","2"]]
trips, stimes = [], []
# Train 8917, Leeuwarden->Stavoren, verified stop-by-stop vs the emulator (Sun 27 May 1990)
LS_8917 = [("leeuwarden","8:22"),("mantgum","8:30"),("sneek","8:42"),("ijlst","8:47"),
           ("workum","8:56"),("hindeloopen","9:01"),("koudum-molkwerum","9:06"),("stavoren","9:11")]
trips.append(["R_lw_stv","su","lwstv_8917","Stavoren","0"])
def tmin(s): h,m=s.split(":"); return int(h)*60+int(m)
for seq,(nm,ts) in enumerate(LS_8917,1):
    stimes.append(["lwstv_8917", gtfs_time(tmin(ts)), gtfs_time(tmin(ts)), f"ns{sidx(nm)}", seq])

# validated mainline corridors harvested from the planner (self-labeling results)
CORRIDORS = {  # trip#: (route_id, headsign, [(station_name, hhmm)])
 "825":("R_A","Eindhoven",[("amsterdam cs","7:32"),("utrecht cs","8:04"),("eindhoven","8:57")]),
 "927":("R_A","Eindhoven",[("amsterdam cs","8:02"),("utrecht cs","8:34"),("eindhoven","9:27")]),
 "829":("R_A","Utrecht CS",[("amsterdam cs","8:32"),("utrecht cs","9:00")]),
 "5129":("R_B","Rotterdam CS",[("den haag cs","8:12"),("rotterdam cs","8:39")]),
 "1531":("R_B","Rotterdam CS",[("den haag cs","8:45"),("rotterdam cs","9:10")]),
 "525":("R_C","Groningen",[("amersfoort","8:39"),("zwolle","9:16"),("groningen","10:14")]),
 "723":("R_C","Groningen",[("zwolle","8:49"),("groningen","9:54")]),
 "6227":("R_D","Nijmegen",[("arnhem","8:06"),("nijmegen","8:22")]),
}
routes += [["R_A","NS","","Amsterdam - Eindhoven","2"],["R_B","NS","","Den Haag - Rotterdam","2"],
           ["R_C","NS","","Amersfoort - Groningen","2"],["R_D","NS","","Arnhem - Nijmegen","2"]]
def tmin(s): h,m=s.split(":"); return int(h)*60+int(m)
for num,(rid,head,stops) in CORRIDORS.items():
    tid=f"c{num}"; trips.append([rid,"su",tid,head,"0"])
    for seq,(nm,ts) in enumerate(stops,1):
        stimes.append([tid, gtfs_time(tmin(ts)), gtfs_time(tmin(ts)), f"ns{sidx(nm)}", seq])
# 'su' service (Sunday) for corridor trips harvested on Sun 27 May 1990
with open(os.path.join(GTFS,"calendar.txt"),"a",newline="") as f:
    csv.writer(f).writerow(["su",0,0,0,0,0,0,1,VALID_START.strftime("%Y%m%d"),VALID_END.strftime("%Y%m%d")])
wr("routes.txt", ["route_id","agency_id","route_short_name","route_long_name","route_type"], routes)
wr("trips.txt", ["route_id","service_id","trip_id","trip_headsign","direction_id"], trips)
wr("stop_times.txt", ["trip_id","arrival_time","departure_time","stop_id","stop_sequence"], stimes)

# ── summary ──────────────────────────────────────────────────────────────────
print(f"stations : {len(stations)}")
print(f"segments : {len(segs)} line segments ({sum(len(v) for v in segs.values())}/{NREC} stations placed)")
print(f"events   : {len(EVENTS)} connection-graph nodes (unlabelled)")
print(f"holidays : {len(holidays)}")
print(f"footnotes: {sum(1 for F in range(1,86) if footnote_days(F))}")
print(f"fares    : {NFARE} distance bands")
print(f"GTFS     : {len(stations)} stops, {len(routes)} routes, {len(trips)} trips, {len(stimes)} stop_times")
print(f"output   : {OUT}  (+ {GTFS})")
