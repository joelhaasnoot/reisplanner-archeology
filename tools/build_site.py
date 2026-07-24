"""Build the GitHub Pages site under docs/.

  * regenerates the byte explainer (docs/format_explainer.html) via make_explainer
  * zips the generated GTFS feed into docs/gtfs/reisplanner-90-91.gtfs.zip
  * writes docs/index.html, a landing page linking to both, with live stats

Run extract_reisplanner.py first so output/90-91/gtfs exists.

    python3 tools/build_site.py
"""
import csv
import datetime
import os
import zipfile

import make_explainer  # noqa: F401  (importing regenerates the explainer)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS_SRC = os.path.join(REPO, "output", "90-91", "gtfs")
DOCS = os.path.join(REPO, "docs")
GTFS_OUT_DIR = os.path.join(DOCS, "gtfs")
ZIP_NAME = "reisplanner-90-91.gtfs.zip"
ZIP_PATH = os.path.join(GTFS_OUT_DIR, ZIP_NAME)

GTFS_FILES = ["agency.txt", "stops.txt", "routes.txt", "trips.txt",
              "stop_times.txt", "calendar.txt", "calendar_dates.txt",
              "transfers.txt", "fare_attributes.txt"]


def rows(name):
    with open(os.path.join(GTFS_SRC, name)) as f:
        return sum(1 for _ in f) - 1            # minus header


def date_span():
    starts, ends = [], []
    with open(os.path.join(GTFS_SRC, "calendar.txt")) as f:
        for r in csv.DictReader(f):
            starts.append(r["start_date"])
            ends.append(r["end_date"])
    fmt = lambda s: datetime.datetime.strptime(s, "%Y%m%d").strftime("%-d %b %Y")
    return fmt(min(starts)), fmt(max(ends))


def build_zip():
    os.makedirs(GTFS_OUT_DIR, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for name in GTFS_FILES:
            src = os.path.join(GTFS_SRC, name)
            if os.path.exists(src):
                z.write(src, name)
    return os.path.getsize(ZIP_PATH)


def human(n):
    return f"{n/1024/1024:.1f} MB" if n >= 1 << 20 else f"{n/1024:.0f} KB"


def main():
    if not os.path.isdir(GTFS_SRC):
        raise SystemExit("output/90-91/gtfs not found — run extract_reisplanner.py first")
    zbytes = build_zip()
    d0, d1 = date_span()
    stats = {
        "stops": rows("stops.txt"), "routes": rows("routes.txt"),
        "trips": rows("trips.txt"), "stop_times": rows("stop_times.txt"),
        "services": rows("calendar.txt"), "transfers": rows("transfers.txt"),
    }

    cards = f"""
    <a class="card" href="format_explainer.html">
      <div class="ic">⬡</div>
      <h2>Explore the binary</h2>
      <p>An interactive hexdump of <code>INLEES.NET</code> — every one of its
      251,820 bytes classified into 130,895 typed fields, decoded on hover.</p>
      <span class="go">Open the byte explainer →</span>
    </a>
    <a class="card" href="gtfs/{ZIP_NAME}" download>
      <div class="ic">↓</div>
      <h2>Download the timetable</h2>
      <p>The recovered schedule as a standard <strong>GTFS</strong> feed —
      the whole 1990/91 service, ready for any transit tool. {human(zbytes)} zipped.</p>
      <span class="go">{ZIP_NAME} →</span>
    </a>"""

    statrow = "".join(
        f'<div><b>{v:,}</b><span>{k.replace("_"," ")}</span></div>'
        for k, v in stats.items())

    html = INDEX.replace("__CARDS__", cards).replace("__STATS__", statrow) \
                .replace("__D0__", d0).replace("__D1__", d1)
    with open(os.path.join(DOCS, "index.html"), "w") as f:
        f.write(html)

    print(f"GTFS zip  : docs/gtfs/{ZIP_NAME}  ({human(zbytes)})")
    print(f"stats     : {stats}  · {d0} – {d1}")
    print("index     : docs/index.html")
    print("explainer : docs/format_explainer.html (regenerated)")


INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NS Reisplanner 90/91 — recovered timetable</title>
<style>
:root{
  --bg:#12161d; --panel:#181d26; --line:#2b3340; --ink:#d6dee9;
  --ink2:#9aa6b6; --ink3:#6b7688; --accent:#e6a24a;
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:light){:root:not([data-theme=dark]){
  --bg:#eef0ee; --panel:#f8f9f7; --line:#d9ddd6; --ink:#1d232b;
  --ink2:#4f5a68; --ink3:#7b8798; --accent:#b06f16; }}
:root[data-theme=light]{
  --bg:#eef0ee; --panel:#f8f9f7; --line:#d9ddd6; --ink:#1d232b;
  --ink2:#4f5a68; --ink3:#7b8798; --accent:#b06f16; }
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:64px 22px 72px}
.eyebrow{font-family:var(--mono);font-size:12px;letter-spacing:.24em;
  text-transform:uppercase;color:var(--accent);margin:0 0 16px}
h1{font-family:var(--mono);font-weight:700;font-size:clamp(28px,5.5vw,44px);
  letter-spacing:-.01em;margin:0;line-height:1.08;text-wrap:balance}
.lede{color:var(--ink2);font-size:17px;max-width:60ch;margin:18px 0 0}
.span{font-family:var(--mono);font-size:12.5px;color:var(--ink3);margin-top:14px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:38px 0 30px}
@media (max-width:640px){.cards{grid-template-columns:1fr}}
.card{display:block;background:var(--panel);border:1px solid var(--line);
  border-radius:14px;padding:22px;text-decoration:none;color:inherit;
  transition:border-color .15s,transform .15s}
.card:hover{border-color:color-mix(in srgb,var(--accent) 55%,var(--line));
  transform:translateY(-2px)}
.card .ic{font-family:var(--mono);font-size:22px;color:var(--accent);
  width:42px;height:42px;display:grid;place-items:center;border-radius:10px;
  background:color-mix(in srgb,var(--accent) 14%,transparent);margin-bottom:14px}
.card h2{font-size:19px;margin:0 0 6px}
.card p{color:var(--ink2);font-size:14px;margin:0 0 14px}
.card code{font-family:var(--mono);font-size:12.5px;
  background:color-mix(in srgb,var(--accent) 12%,transparent);padding:1px 5px;border-radius:4px}
.card .go{font-family:var(--mono);font-size:13px;color:var(--accent);font-weight:600}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:4px 0 34px}
.stats div{flex:1 1 120px;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;padding:12px 14px}
.stats b{display:block;font-family:var(--mono);font-size:20px;
  font-variant-numeric:tabular-nums}
.stats span{font-family:var(--mono);font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink3)}
.note{color:var(--ink2);font-size:14px;max-width:64ch}
.note code{font-family:var(--mono);font-size:12.5px;color:var(--ink)}
footer{margin-top:36px;padding-top:20px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:12.5px;color:var(--ink3)}
footer a{color:var(--accent);text-decoration:none}
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">Digital archeology</p>
  <h1>NS Reisplanner 90/91</h1>
  <p class="lede">The complete 1990 Dutch Railways timetable, reverse-engineered
  out of the DOS trip planner’s compiled binary and re-emitted as open data.</p>
  <p class="span">Timetable validity __D0__ – __D1__ · © CVI, Utrecht 1990</p>

  <div class="cards">__CARDS__</div>

  <div class="stats">__STATS__</div>

  <p class="note">The GTFS feed is generated from <code>INLEES.NET</code> by
  <code>extract_reisplanner.py</code> and validated stop-by-stop against the
  original program running in an emulator. Times, running-days (with footnote
  exceptions), routes, transfers and fares are all decoded from the file.</p>

  <footer>
    Source & method on
    <a href="https://github.com/joelhaasnoot/reisplanner-archeology">GitHub</a>.
    Not affiliated with NS. Timetable data © CVI Centrum voor
    Informatieverwerking N.V., Utrecht 1990.
  </footer>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    main()
