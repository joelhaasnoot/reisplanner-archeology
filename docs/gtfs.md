# NS Reisplanner 90/91 → GTFS

GTFS feed reconstructed from `INLEES.NET` (NS Reisplanner, timetable valid
**27 May 1990 – 1 June 1991**). See `FORMAT.md` for the binary format.

## Status

| file | coverage | notes |
|------|----------|-------|
| `agency.txt` | ✅ complete | NS |
| `stops.txt` | ✅ all 469 stations | `stop_lat`/`stop_lon` empty — see below |
| `calendar.txt` | ✅ 114 services | one per distinct running-day set; day masks use bit0=Mon … bit6=Sun |
| `routes.txt` | ✅ 937 routes | one per origin/destination pair |
| `trips.txt` | ✅ 6,249 trips | whole network; `trip_short_name` = the 1990 train number |
| `stop_times.txt` | ✅ 51,653 stop_times | arrival/departure per stop, dwell included |

## How the trips are built
Straight out of `INLEES.NET` by `tools/decode_timetable.py` — the section-A node records
give each segment's departure board and each intermediate station's arrival offsets, and
per-segment runs are chained into whole journeys by train number. Nothing is hand-entered
and nothing is harvested off the emulator's screen any more; both of those were stopgaps
while the record format was unknown.

Each service is a journey's exact 371-day running set — the weekly day mask narrowed by the
footnote's dates and intersected along the chain. The weekly pattern goes in `calendar.txt`
and the footnote's excluded days become `calendar_dates.txt` removals, so a train that skips
Christmas Eve is represented correctly rather than as "daily".

Journeys that run past midnight keep counting (`24:11`), per GTFS convention.

## Transfers
`transfers.txt` comes from section B: per-junction minimum transfer times between specific
train pairs (`transfer_type=2`, `min_transfer_time` in seconds). The binary's default is
2 minutes and only the exceptions are stored — 1 minute, or 0 for same-platform pairs.

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
