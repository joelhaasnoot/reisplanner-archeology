"""Decode the complete NS 90/91 timetable out of INLEES.NET section A.

The format was cracked by disassembling the search engine (see docs/BINARY.md):

  * `0x6ae6` walks a station's node record and gives the exact record boundaries:
    each *entry* is an 8-byte header followed by a body whose first word is the
    body length in words, then a terminator word (0xfffe = another entry
    follows, 0xffff = last entry of the node).
  * `0x6889` walks the body: `p = body; p += 2*p[0]` twice, i.e. the body starts
    with two length-prefixed blocks and the payload follows them.

Entry header (4 words):
    hdr[0] = the far end of this line segment
    hdr[1] = the near end, or 0 when THIS station is itself an endpoint
    hdr[2] = nominal running time (bit 15 is a flag, not the endpoint marker)
    hdr[3] = nominal running time in the other direction

Two entry flavours, told apart by hdr[1]:

  BOARD entry (hdr[1] == 0) -- departures from this station over the whole
  segment, in the direction "this station -> hdr[0]":
      body = [len] [idblock] { group, runtime, sublen, (time, mask<<8 | footnote,
      train) * n } separated by 0xfffe
      `time` is minutes since midnight in the low 11 bits, `mask` is the weekly
      day bitmap (0x7f = daily, 0x3f = Mon-Sat, 0x1f = Mon-Fri, 0x40 = Sun) and
      the low byte is the footnote index (0 = none), which narrows the weekly
      pattern to an exact set of dates.

  INTERMEDIATE entry (hdr[1] != 0) -- this station's position on segment (A,B):
      body = [len] [block1] [block2] then one (fromA, fromB) pair per group,
      each word packed as (group << 10) | minutes.
      fromA = arrival offset for an A->B run, fromB = arrival offset for a
      B->A run.  fromA + fromB = runtime - dwell, which is where the
      long-standing "1-minute asymmetry" comes from: the pair straddles the
      stop, so the dwell falls out as runtime - fromA - fromB.

So a train departing endpoint A at T on group g reaches intermediate station S
at T + pairs[g].fromA and leaves again at T + runtime - pairs[g].fromB.

Usage:  python3 tools/decode_timetable.py [--check]
"""
import argparse
import csv
import datetime
import os
import struct
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(REPO, "input", "90-91", "INLEES.NET")
STATIONS_CSV = os.path.join(REPO, "output", "90-91", "stations.csv")
OUT_DIR = os.path.join(REPO, "output", "90-91")
FIXTURE = os.path.join(REPO, "fixtures", "train_8917_sun.csv")

MORE_ENTRIES, LAST_ENTRY = 0xFFFE, 0xFFFF

# Validity window: 371 days from Sunday 27 May 1990.
NDAYS, FIRST_DOW = 371, 6
VALID_START = datetime.date(1990, 5, 27)
FNIDX = 0x3CC76          # footnote -> 1-based offset into section D
SECD, SECD_SIZE = 0x37554, 2264
ALL_DAYS = (1 << NDAYS) - 1


def footnote_bits(data, fn):
    """Footnote -> bitset of the days it runs. 0 means "no footnote".

    Section D holds 8-byte records (u32 next_1based, u16 first_day, u16 last_day)
    chained backwards from FNIDX[fn]. Most footnotes cover nearly the whole year
    and knock out a handful of holidays, which is why a footnoted train still
    turns up on almost any date you query -- and why an earlier reading of this
    byte as "not a footnote index" was wrong. The table really lives here; the
    4096 bytes at 0x3bc76 that were previously decoded as footnote bitmaps are
    the station-name hash instead.
    """
    if not fn:
        return ALL_DAYS
    off = struct.unpack_from("<I", data, FNIDX + fn * 4)[0]
    bits, seen = 0, set()
    while off:
        p = off - 1
        if p in seen or p + 8 > SECD_SIZE:
            break
        seen.add(p)
        nxt, a, b = struct.unpack_from("<IHH", data, SECD + p)
        if a <= b < NDAYS:
            bits |= ((1 << (b - a + 1)) - 1) << a
        off = nxt
    return bits


def weekly_bits(mask):
    """Weekly day mask (bit 0 = Monday) -> bitset over the validity window."""
    bits = 0
    for i in range(NDAYS):
        if (mask >> ((FIRST_DOW + i) % 7)) & 1:
            bits |= 1 << i
    return bits


def load_stations():
    with open(STATIONS_CSV) as f:
        rows = list(csv.DictReader(f))
    return {int(r["idx"]): r for r in rows}


def parse_node(data, off):
    """Split one station node into its entries. Mirrors the walker at 0x6ae6."""
    entries, p = [], off
    while True:
        hdr = struct.unpack_from("<4H", data, p)
        body_at = p + 8
        length = struct.unpack_from("<H", data, body_at)[0]
        body = list(struct.unpack_from(f"<{length}H", data, body_at))
        end = body_at + 2 * length
        term = struct.unpack_from("<H", data, end)[0]
        entries.append((hdr, body))
        if term != MORE_ENTRIES:
            return entries
        p = end + 2


# Times and running times are 11-bit (a day is 1440 minutes); the top five bits
# are flags. Masking with 0x3ff -- the width used for the relative offsets in
# intermediate entries -- silently mangled every departure after 17:03.
TIME_MASK = 0x7FF


def parse_endpoint(body):
    """-> [(group, runtime, [(minutes, daymask, footnote, train), ...]), ...]"""
    n = body[1]
    i = 1 + n                       # skip the length-prefixed id block
    groups = []
    while i + 2 < len(body):
        group, runtime, sublen = body[i], body[i + 1] & TIME_MASK, body[i + 2]
        trips = []
        for j in range(i + 3, i + 2 + sublen, 3):
            # The middle word packs the weekly day mask in its high byte and
            # the FOOTNOTE index in its low byte (0 = no footnote). The
            # footnote is an exact set of running dates in section D; the
            # service runs on days matching BOTH the weekly mask and the
            # footnote. Most footnotes cover ~364 days, excluding a few
            # holidays -- which is why a footnoted train still shows up on
            # nearly any date you query.
            trips.append((body[j] & TIME_MASK, body[j + 1] >> 8,
                          body[j + 1] & 0xFF, body[j + 2]))
        groups.append((group, runtime, trips))
        i += 2 + sublen
        if i < len(body) and body[i] == MORE_ENTRIES:
            i += 1
    return groups


def parse_intermediate(body):
    """-> {group: (from_a, from_b)} for this station on its segment."""
    i = 1
    for _ in range(2):              # two length-prefixed blocks, then the payload
        i += body[i]
    pairs = {}
    payload = body[i:]
    for k in range(0, len(payload) - 1, 2):
        a, b = payload[k], payload[k + 1]
        group = a >> 10
        if group != b >> 10:        # the two halves of a pair share their group
            continue
        pairs[group] = (a & 0x3FF, b & 0x3FF)
    return pairs


# Non-rail links. hdr[1] carries a MODE code instead of a segment partner, and
# hdr[2]'s running time is 0 (the duration is in hdr[3]). Confirmed against the
# planner's own screen, which prints these words in place of a train number:
# Ameland->Holwerd "boot", Holwerd->Leeuwarden "bus", Rotterdam CS->Hofplein
# "lopen". Code 8 (e.g. Utrecht CS -> Utrecht streekbushaltes) is the same shape
# but has not been seen rendered, so it keeps a neutral label.
MODES = {3: "boot", 4: "bus", 7: "lopen", 8: "link"}


def build(data, stations):
    """-> (boards, segment_members)

    boards[(here, there)]  = [(group, runtime, trips)]
    members[(a, b)][sta]   = {group: (from_a, from_b)}
    """
    # A station can have SEVERAL entries toward the same neighbour -- e.g.
    # Amsterdam CS -> Utrecht CS has both the stoptrein via Abcoude and the
    # intercity. Keying by (here, there) alone silently dropped one of them.
    nodes = {}
    for idx, row in stations.items():
        off = int(row["board_secA_offset"])
        if not 0 <= off < len(data) - 16:
            continue
        try:
            nodes[idx] = parse_node(data, off)
        except struct.error:
            continue

    def is_mode_link(idx, hdr):
        """A mode link is MIRRORED: the peer carries the same code back.

        hdr[2] & 0x7fff == 0 alone is not enough -- plenty of genuine
        intermediates have a zero running time, and their hdr[1] is a real
        station index. Requiring the peer to mirror the entry isolates exactly
        the four non-rail codes.
        """
        if hdr[1] == 0 or (hdr[2] & 0x7FFF) != 0:
            return False
        return any(h[0] == idx and h[1] == hdr[1] and (h[2] & 0x7FFF) == 0
                   for h, _ in nodes.get(hdr[0], []))

    boards, members = defaultdict(list), defaultdict(dict)
    for idx, entries in nodes.items():
        for hdr, body in entries:
            a, b = hdr[0], hdr[1]
            # hdr[1] == 0 means "this station is an endpoint of that segment",
            # so the entry is a departure board. The 0x8000 bit on hdr[2] looked
            # like the endpoint marker but only covers part of them -- keying on
            # it dropped whole corridors (Amsterdam CS -> Utrecht CS included).
            if b == 0:
                boards[(idx, a)].append((body, parse_endpoint(body), None))
            elif is_mode_link(idx, hdr):
                # Ferries, buses and walking links use the same board layout.
                # Missing them left the Wadden islands, the ferry harbours and
                # the bus-only towns -- 28 stations -- with no service at all.
                boards[(idx, a)].append((body, parse_endpoint(body), MODES.get(b, f"mode{b}")))
            else:
                members[(a, b)][idx] = parse_intermediate(body)
    return boards, members


def expand(data, boards, members):
    """Every departure board -> a full list of (train, [(station, arr, dep)])."""
    trips = []
    for (here, there), variants in boards.items():
        seg = members.get((here, there))
        forward = seg is not None    # this board runs A->B
        if seg is None:
            seg = members.get((there, here), {})
        for _body, groups, mode in variants:
            for group, runtime, deps in groups:
                # Order the intermediate stops by their offset in this direction.
                stops = []
                for sta, pairs in seg.items():
                    if group not in pairs:
                        continue
                    from_a, from_b = pairs[group]
                    arr = from_a if forward else from_b
                    dep = runtime - (from_b if forward else from_a)
                    if arr > runtime or dep > runtime or dep < arr:
                        continue
                    stops.append((arr, dep, sta))
                stops.sort()
                for minutes, daymask, footnote, train in deps:
                    seq = [(here, None, minutes)]
                    seq += [(sta, minutes + arr, minutes + dep)
                            for arr, dep, sta in stops]
                    seq.append((there, minutes + runtime, None))
                    days = (weekly_bits(daymask)
                            & footnote_bits(data, footnote))
                    trips.append({"train": train, "daymask": daymask,
                                  "footnote": footnote, "days": days,
                                  "mode": mode or "trein",
                                  "from": here, "to": there, "group": group,
                                  "runtime": runtime, "stops": seq})
    return trips


def chain(trips):
    """Join per-segment runs into whole journeys, one journey per day pattern.

    A board only ever covers one line segment, so a train that crosses a
    junction shows up once per segment. Two runs of the same train number join
    when one ends where the next begins, at the same minute, on days both run --
    "days" being the concrete 371-day set (weekly mask narrowed by the
    footnote), not just the weekly pattern.

    The same leg is often published TWICE under different day masks -- e.g.
    train 1859 Amsterdam CS -> Nijmegen appears as 0x1f (Mon-Fri) and again as
    0x60 (weekend, footnote 10). A single pass that marked each leg "used"
    collapsed the two into one Mon-Fri journey and starved the weekend one,
    which is why the planner offered 1859 on a Sunday when the decoded feed
    said weekdays only. So legs carry a RESIDUAL day set instead of a used flag:
    a leg published as 0x7f can serve both a weekday and a weekend journey, and
    is only exhausted once every day it runs has been claimed.
    """
    out = []
    by_train = defaultdict(list)
    for t in trips:
        by_train[t["train"]].append(t)
    for train, runs in by_train.items():
        runs.sort(key=lambda r: r["stops"][0][2])
        residual = [r["days"] for r in runs]
        for i, run in enumerate(runs):
            while residual[i]:
                stops = list(run["stops"])
                days = residual[i]
                chosen = [i]
                while True:
                    end_sta, end_time = stops[-1][0], stops[-1][1]
                    best, best_overlap = None, 0
                    for j, cand in enumerate(runs):
                        overlap = residual[j] & days
                        if j in chosen or not overlap:
                            continue
                        if cand["stops"][0][0] != end_sta:
                            continue
                        # Junctions carry a dwell, so the onward run departs a
                        # minute or two after the inbound arrival. Only a
                        # journey that has ALREADY crossed midnight may pick up
                        # a board time from the next day; without that guard,
                        # unrelated runs sharing a train number chained into
                        # 76:42 nonsense.
                        shift = 1440 if end_time >= 1440 else 0
                        if not 0 <= cand["stops"][0][2] + shift - end_time <= 15:
                            continue
                        # Prefer the continuation that shares the most days, so
                        # a weekday journey follows its weekday legs rather
                        # than being cut short by a weekend-only one.
                        n = bin(overlap).count("1")
                        if n > best_overlap:
                            best, best_overlap, best_shift = j, n, shift
                    if best is None:
                        break
                    cand, shift = runs[best], best_shift
                    chosen.append(best)
                    days &= residual[best]
                    # The junction is one stop, not two: keep its arrival.
                    stops[-1] = (end_sta, end_time, cand["stops"][0][2] + shift)
                    stops += [(s, None if a is None else a + shift,
                               None if d is None else d + shift)
                              for s, a, d in cand["stops"][1:]]
                for j in chosen:
                    residual[j] &= ~days
                # Report the weekly pattern the surviving days actually form,
                # not the seed leg's -- legs of one train can carry different
                # masks AND different footnotes (train 2526 runs Koln->Den Haag
                # daily except that its Dutch legs carry footnote 36, which
                # drops 24 Dec; train 1580 covers that date instead).
                wk = 0
                for day in range(NDAYS):
                    if (days >> day) & 1:
                        wk |= 1 << ((FIRST_DOW + day) % 7)
                out.append({"train": train, "daymask": wk, "days": days,
                            "mode": run["mode"],
                            "footnote": run["footnote"], "stops": stops})
    return out


def decode(data, stations):
    """The whole pipeline: raw INLEES.NET -> chained journeys.

    `stations` maps station index -> {"board_secA_offset": ..., "name": ...},
    so extract_reisplanner.py can pass its own station list straight in.
    """
    boards, members = build(data, stations)
    return chain(expand(data, boards, members))


def date_ranges(days):
    """Day bitset -> ["1990-05-27..1990-08-14", "1990-08-16", ...]."""
    out, i = [], 0
    while i < NDAYS:
        if (days >> i) & 1:
            j = i
            while j + 1 < NDAYS and (days >> (j + 1)) & 1:
                j += 1
            a = VALID_START + datetime.timedelta(days=i)
            b = VALID_START + datetime.timedelta(days=j)
            out.append(str(a) if a == b else f"{a}..{b}")
            i = j + 1
        else:
            i += 1
    return out


def hhmm(m):
    # GTFS convention: a trip that runs past midnight keeps counting (24:11).
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def check(trips, stations):
    """Validate against the hand-built golden trip (train 8917, Sunday)."""
    with open(FIXTURE) as f:
        want = [(r["station"].lower(), r["time"]) for r in csv.DictReader(f)]
    got = {}
    for t in trips:
        if t["train"] != 8917:
            continue
        for sta, arr, dep in t["stops"]:
            name = stations[sta]["name"].lower()
            got.setdefault(name, hhmm(arr if arr is not None else dep))
    ok = True
    print("golden trip 8917 (Leeuwarden -> Stavoren, Sunday)")
    for name, want_t in want:
        have = got.get(name, "--:--")
        flag = "ok " if have == want_t else "FAIL"
        ok &= have == want_t
        print(f"  {flag} {name:20s} expected {want_t}  decoded {have}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="validate against fixtures/train_8917_sun.csv only")
    args = ap.parse_args()

    data = open(NET, "rb").read()
    stations = load_stations()
    boards, members = build(data, stations)
    trips = chain(expand(data, boards, members))
    print(f"boards   : {len(boards)} departure boards")
    print(f"segments : {len(members)} segments with intermediate stops")
    print(f"trips    : {len(trips)}")
    print(f"trains   : {len({t['train'] for t in trips})} distinct numbers")
    print(f"stoptimes: {sum(len(t['stops']) for t in trips)}")

    ok = check(trips, stations)
    if args.check:
        return 0 if ok else 1

    os.makedirs(OUT_DIR, exist_ok=True)
    ordered = sorted(trips, key=lambda x: (x["train"], x["stops"][0][2]))
    # One train can run the same departure under several day patterns (a
    # weekday and a weekend variant, or a holiday-only relief working), so
    # train+time alone is NOT unique -- de-duplicate or they overwrite in
    # trips.csv and collide as GTFS trip_ids.
    used = defaultdict(int)
    for t in ordered:
        base = f"t{t['train']}_{t['stops'][0][2]}"
        used[base] += 1
        t["trip_id"] = base if used[base] == 1 else f"{base}_{used[base]}"

    path = os.path.join(OUT_DIR, "timetable.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trip_id", "train", "daymask", "seq", "station_idx",
                    "station", "arrival", "departure"])
        for t in ordered:
            for i, (sta, arr, dep) in enumerate(t["stops"]):
                w.writerow([t["trip_id"], t["train"], f"{t['daymask']:#04x}",
                            i, sta, stations[sta]["name"], hhmm(arr),
                            hhmm(dep)])
    # A chained journey can span legs with DIFFERENT footnotes, so no single
    # footnote number describes it. The exact running dates do.
    tpath = os.path.join(OUT_DIR, "trips.csv")
    with open(tpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trip_id", "train", "mode", "origin", "destination",
                    "daymask", "days_running", "date_ranges"])
        for t in ordered:
            w.writerow([t["trip_id"], t["train"], t["mode"],
                        stations[t["stops"][0][0]]["name"],
                        stations[t["stops"][-1][0]]["name"],
                        f"{t['daymask']:#04x}", bin(t["days"]).count("1"),
                        "; ".join(date_ranges(t["days"]))])
    print(f"output   : {path}")
    print(f"           {tpath}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
