# NS Reisplanner 90/91 → GTFS

GTFS feed reconstructed from `INLEES.NET` (NS Reisplanner, timetable valid
**27 May 1990 – 1 June 1991**). See `FORMAT.md` for the binary format.

## Status

| file | coverage | notes |
|------|----------|-------|
| `agency.txt` | ✅ complete | NS |
| `stops.txt` | ✅ all 455 stations | `stop_lat`/`stop_lon` empty — see below |
| `calendar.txt` | ✅ 16 day-mask patterns + `su` | bit0=Mon … bit6=Sun; `m7f`=daily, `m1f`=weekdays, `m3f`=Mon–Sat; `su`=Sunday-observed |
| `routes.txt` | 6 routes | 1 branch + 5 mainline corridors |
| `trips.txt` | 62 trips | see two methods below |
| `stop_times.txt` | for those trips | |

## Two reconstruction methods
1. **Binary-reconstructed** (Leeuwarden–Stavoren, 29 trips, service from real day-masks):
   full intermediate stops chained out of `INLEES.NET`, verified to the minute vs the
   emulator. Works cleanly on sparse lines with near-unique times.
2. **Query-harvested** (5 corridors, 33 trips, service `su`): read directly off the
   DOSBox result screens, which self-label origin/destination/time/train#. Endpoint stops
   only (intermediate stops for these fast trains not yet filled). Through-trains merged
   (825, 927 = Amsterdam→Utrecht→Eindhoven; 525 = Amersfoort→Zwolle→Groningen).
   Corridors: Amsterdam–Eindhoven, Amsterdam–Den Haag (via Leiden), Den Haag–Rotterdam,
   Amersfoort–Groningen, Arnhem–Nijmegen.

   *Caveat:* `su` marks these Sunday-only because that's the day queried (27 May 1990);
   their true weekly validity would come from the binary day-masks.

## Coordinates
`stop_lat`/`stop_lon` are intentionally empty. The `stop_code` values are the **official
NS station codes still in use today** (`asd`, `ut`, `gvc`, `lw`, `stv`…), so coordinates
can be filled by joining `stop_code` against any modern NS/OSM station list — no decoding
of the 1990 binary needed.

## Why only one line has trips
`stops.txt`, `calendar.txt`, `agency.txt` are network-complete. Trips are limited to the
**Leeuwarden–Stavoren** line because that is the line whose station boards we have
positively identified (node offset → station name). The binary's internal board-labeling
layer is not yet cracked (see `FORMAT.md` "station → board naming"), so trips for the
other lines can't be auto-labeled yet.

The reconstruction method itself is general: read each station's arrival board, then chain
trains across stations by time + day-mask. Train 8917 round-trips exactly
(08:22 Leeuwarden → 09:11 Stavoren). Once the offset→station map is solved, the same
pipeline emits trips for the whole network.

## Known artifacts (this proof-of-concept)
- A few trips appear to terminate at Koudum-Molkwerum: Stavoren has a second arrival board
  our single anchor doesn't cover, so the final leg occasionally fails to match.
- Northbound (Stavoren→Leeuwarden) trips are not yet included.
- Some `calendar.txt` masks (e.g. `m0f` = Mon–Thu) are rare and may be encoding edge cases.
