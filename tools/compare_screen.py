"""Compare a captured planner answer screen against the decoded timetable.

`tools/verify_query.py` saves the guest's own result screen; this reads the
connections off it and looks each one up in output/90-91/timetable.csv. The
screen is ground truth produced by the 1990 program itself, so a mismatch is a
decoding bug, not a screen-parsing quirk -- unless the line failed to parse,
which is reported separately.

Usage:  python3 tools/compare_screen.py work/screen_lw_stv.txt [...]
"""
import csv
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMETABLE = os.path.join(REPO, "output", "90-91", "timetable.csv")

# " leeuwarden              stavoren                    8:22   9:11    8917"
# The last column is a train number OR a mode word (boot / bus / lopen), and a
# walking leg has no times at all.
LINE = re.compile(r"^\s{2,}(\S.*?)\s{3,}(\S.*?)\s{3,}"
                  r"(\d{1,2}:\d{2})\s+(\d{1,2}:\d{2})\s+(\w+)\s*$")
WALK = re.compile(r"^\s{2,}(\S.*?)\s{3,}(\S.*?)\s{3,}(lopen)\s*$")

DOW = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

# The header echoes the date the planner actually used ("... 29 mei 1990"), so
# the day of week is ground truth for checking the weekly day masks (bit 0 =
# Monday ... bit 6 = Sunday).
MONTHS = {m: i + 1 for i, m in enumerate(
    "januari februari maart april mei juni juli augustus september oktober "
    "november december".split())}
DATE = re.compile(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})")


def header_weekday(header):
    m = DATE.search(header)
    if not m:
        return None, None
    import datetime
    month = MONTHS.get(m.group(2))
    if not month:
        return None, None
    d = datetime.date(int(m.group(3)), month, int(m.group(1)))
    return d, d.weekday()


TRIPS = os.path.join(REPO, "output", "90-91", "trips.csv")


def load_trips():
    trips, cur = [], None
    with open(TIMETABLE) as f:
        for r in csv.DictReader(f):
            if r["seq"] == "0":
                cur = []
                trips.append(cur)
            cur.append(r)
    return trips


def load_rundates():
    """trip_id -> set of dates it runs, from the decoded footnote day sets."""
    import datetime
    out = {}
    with open(TRIPS) as f:
        for r in csv.DictReader(f):
            days = set()
            for part in r["date_ranges"].split(";"):
                part = part.strip()
                if not part:
                    continue
                a, _, b = part.partition("..")
                a = datetime.date.fromisoformat(a)
                b = datetime.date.fromisoformat(b) if b else a
                while a <= b:
                    days.add(a)
                    a += datetime.timedelta(days=1)
            out[r["trip_id"]] = days
    return out


def mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def main(paths):
    trips = load_trips()
    rundates = load_rundates()
    by_train, by_mode = {}, {}
    modes = {}
    with open(TRIPS) as f:
        for r in csv.DictReader(f):
            modes[r["trip_id"]] = r["mode"]
    for t in trips:
        by_train.setdefault(t[0]["train"], []).append(t)
        by_mode.setdefault(modes.get(t[0]["trip_id"], "trein"), []).append(t)

    total = matched = 0
    for path in paths:
        rows = open(path).read().split("\n")
        header = rows[0].strip()
        date, dow = header_weekday(header)
        print(f"\n=== {os.path.basename(path)} ===")
        print(f"    query: {header}"
              + (f"   [{DOW[dow]}]" if dow is not None else ""))
        for row in rows[1:]:
            if WALK.match(row):
                continue           # untimed walking transfer: nothing to check
            m = LINE.match(row)
            if not m:
                continue
            frm, to, dep, arr, train = (g.strip() for g in m.groups())
            total += 1
            verdict = "MISSING"
            # boot/bus rows name the mode, not a train number, so look those
            # up by (origin, destination, times) across every non-rail trip.
            cands = by_train.get(train, []) if train.isdigit() else by_mode.get(train, [])
            for t in cands:
                names = [s["station"] for s in t]
                if frm not in names or to not in names:
                    continue
                i, j = names.index(frm), names.index(to)
                if i >= j:
                    continue
                d = t[i]["departure"] or t[i]["arrival"]
                a = t[j]["arrival"] or t[j]["departure"]
                if not (d and a and mins(d) == mins(dep) and mins(a) == mins(arr)):
                    verdict = f"TIMES DIFFER: decoded {d} -> {a}"
                    continue
                # The planner offered this connection on `date`, so the
                # decoded service must run on that exact DATE -- not merely on
                # that weekday. This is what checks the footnote decoding.
                runs = rundates.get(t[0]["trip_id"], set())
                if date is not None and date not in runs:
                    verdict = (f"DATE MISMATCH: not in {len(runs)} running days")
                    continue
                verdict = (f"ok   ({t[0]['daymask']}, {len(runs)}d, "
                           f"{j - i + 1} stops)")
                matched += 1
                break
            print(f"    {verdict:38s} {train:>6}  {frm} {dep} -> {to} {arr}")
    print(f"\n{matched}/{total} connections reproduced exactly")
    return 0 if matched == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
