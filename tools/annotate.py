"""Annotate every byte of INLEES.NET with the field it belongs to.

Walks the whole file region by region using the same decoders that produce the
timetable, and emits a flat list of fields that tile the file exactly:

    fields[i] = (start, length, type_index)

Each type carries a category (for colour) and a `dec` key that tells the
viewer's JavaScript how to turn the raw bytes into a human value on hover — so
the page ships only boundaries + type codes, not a string per byte.

    python3 tools/annotate.py         # coverage report + field/type counts
"""
import csv
import os
import struct

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = os.path.join(REPO, "input", "90-91", "INLEES.NET")
STATIONS_CSV = os.path.join(REPO, "output", "90-91", "stations.csv")

# region boundaries (see docs/PLAN.md)
SECA, SECB, SECC, SECD = 0x0003c, 0x313a6, 0x33544, 0x37554
STATTBL, NAMEHASH, FNIDX = 0x37e2c, 0x3bc76, 0x3cc76
DATECAL, FARES, SEASON, JEUGD, LINKCORR = 0x3d05e, 0x3d344, 0x3d5cc, 0x3d6c8, 0x3d71c

MORE, LAST = 0xFFFE, 0xFFFF
TIME_MASK = 0x7FF
MODES = {3: "boot", 4: "bus", 7: "lopen", 8: "link"}


def load_stations():
    with open(STATIONS_CSV) as f:
        return {int(r["idx"]): r for r in csv.DictReader(f)}


class Annotator:
    def __init__(self, data):
        self.d = data
        self.N = len(data)
        self.types = []            # list of (cat, name, dec)
        self._tix = {}
        self.fields = []           # (start, length, type_index)

    def T(self, cat, name, dec):
        k = (cat, name, dec)
        if k not in self._tix:
            self._tix[k] = len(self.types)
            self.types.append({"cat": cat, "name": name, "dec": dec})
        return self._tix[k]

    def F(self, start, length, t):
        if length > 0:
            self.fields.append((start, length, t))

    # ── section A: the connection graph ─────────────────────────────────────
    def walk_node(self, off):
        """Yield (entry_off, hdr, body, body_at, term_off, next_off)."""
        p = off
        while True:
            hdr = struct.unpack_from("<4H", self.d, p)
            body_at = p + 8
            length = struct.unpack_from("<H", self.d, body_at)[0]
            body = list(struct.unpack_from(f"<{length}H", self.d, body_at))
            term_off = body_at + 2 * length
            term = struct.unpack_from("<H", self.d, term_off)[0]
            yield p, hdr, body, body_at, term_off
            if term != MORE:
                return
            p = term_off + 2

    def annotate_A(self, stations):
        d = self.d
        # entry offsets for every station node, to resolve mode links (mirror).
        nodes = {}
        for idx, row in stations.items():
            off = int(row["board_secA_offset"])
            if 0 <= off < self.N - 16:
                try:
                    nodes[idx] = [(h, b) for (_, h, b, _, _) in self.walk_node(off)]
                except struct.error:
                    pass

        def is_mode(idx, hdr):
            if hdr[1] == 0 or (hdr[2] & 0x7FFF) != 0:
                return False
            return any(h[0] == idx and h[1] == hdr[1] and (h[2] & 0x7FFF) == 0
                       for h, _ in nodes.get(hdr[0], []))

        # types
        t_far = self.T("index", "far endpoint", "sta")
        t_near = self.T("marker", "near end / marker", "near")
        t_rtf = self.T("time", "runtime →", "rt11")
        t_rtb = self.T("time", "runtime ←", "rt11")
        t_blen = self.T("len", "body length", "u16w")
        t_ilen = self.T("len", "id-block length", "u16")
        t_ids = self.T("pointer", "line node refs", "noderef")
        t_grp = self.T("len", "group id", "u16")
        t_grt = self.T("time", "segment runtime", "rt11")
        t_slen = self.T("len", "departure-list length", "u16")
        t_time = self.T("time", "departure", "time11")
        t_foot = self.T("foot", "footnote", "footb")
        t_mask = self.T("mask", "day mask", "maskb")
        t_train = self.T("train", "train number", "train")
        t_sep = self.T("marker", "group separator", "term")
        t_term = self.T("marker", "entry terminator", "term")
        t_blk = self.T("len", "index block", "words")
        t_pair = self.T("time", "stop offset pair", "pair")
        t_raw = self.T("marker", "undecoded body", "hex")

        seen = set()
        for idx, row in stations.items():
            off = int(row["board_secA_offset"])
            if not (0 <= off < self.N - 16) or off in seen:
                continue
            try:
                entries = list(self.walk_node(off))
            except struct.error:
                continue
            for entry_off, hdr, body, body_at, term_off in entries:
                if entry_off in seen:
                    continue
                seen.add(entry_off)
                self.F(entry_off, 2, t_far)
                self.F(entry_off + 2, 2, t_near)
                self.F(entry_off + 4, 2, t_rtf)
                self.F(entry_off + 6, 2, t_rtb)
                self.F(body_at, 2, t_blen)          # body[0] = length
                board = (hdr[1] == 0) or is_mode(idx, hdr)
                try:
                    if board:
                        self._A_board(body, body_at, t_ilen, t_ids, t_grp,
                                      t_grt, t_slen, t_time, t_foot, t_mask,
                                      t_train, t_sep)
                    else:
                        self._A_inter(body, body_at, t_blk, t_pair)
                except (struct.error, IndexError):
                    # never leave a hole: cover the rest of the body raw
                    self.F(body_at + 2, term_off - (body_at + 2), t_raw)
                self.F(term_off, 2, t_term)

        return SECB  # A ends at SECB

    def _A_board(self, body, body_at, t_ilen, t_ids, t_grp, t_grt, t_slen,
                 t_time, t_foot, t_mask, t_train, t_sep):
        boff = lambda k: body_at + 2 * k
        n = body[1]
        self.F(boff(1), 2, t_ilen)                  # id-block count
        self.F(boff(2), 2 * (n - 1), t_ids)         # id words
        i = 1 + n
        while i + 2 < len(body):
            self.F(boff(i), 2, t_grp)
            self.F(boff(i + 1), 2, t_grt)
            self.F(boff(i + 2), 2, t_slen)
            sublen = body[i + 2]
            for j in range(i + 3, i + 2 + sublen, 3):
                self.F(boff(j), 2, t_time)
                self.F(boff(j + 1), 1, t_foot)      # low byte
                self.F(boff(j + 1) + 1, 1, t_mask)  # high byte
                self.F(boff(j + 2), 2, t_train)
            i += 2 + sublen
            if i < len(body) and body[i] == MORE:
                self.F(boff(i), 2, t_sep)
                i += 1

    def _A_inter(self, body, body_at, t_blk, t_pair):
        boff = lambda k: body_at + 2 * k
        i = 1
        for _ in range(2):
            L = body[i]
            self.F(boff(i), 2 * L, t_blk)
            i += L
        k = i
        while k + 1 < len(body):
            self.F(boff(k), 4, t_pair)
            k += 2
        if k < len(body):                           # odd trailing word
            self.F(boff(k), 2, t_blk)

    # ── the tail regions ────────────────────────────────────────────────────
    def annotate_rest(self, stations):
        d = self.d
        self.F(0, SECA, self.T("marker", "file header", "hex"))

        # B: minimum transfer times, 6-byte records terminated by 0xffff.
        t_trec = self.T("pointer", "transfer record", "transfer")
        t_bterm = self.T("marker", "array terminator", "term")
        p = SECB
        while p + 2 <= SECC:
            w = struct.unpack_from("<H", d, p)[0]
            if w == LAST or p + 6 > SECC:
                self.F(p, 2, t_bterm)
                p += 2
            else:
                self.F(p, 6, t_trec)
                p += 6

        # C: names/codes. Printable runs are text; the rest is structure.
        t_text = self.T("text", "name / code", "text")
        t_pad = self.T("marker", "record structure", "hex")
        p = SECC
        while p < SECD:
            if 32 <= d[p] < 127:
                s = p
                while p < SECD and 32 <= d[p] < 127:
                    p += 1
                if p - s >= 2:
                    self.F(s, p - s, t_text)
                else:
                    self.F(s, p - s, t_pad)
            else:
                s = p
                while p < SECD and not (32 <= d[p] < 127):
                    p += 1
                self.F(s, p - s, t_pad)

        # D: footnote date-range records (u32 next, u16 first, u16 last).
        t_next = self.T("pointer", "next range", "u32ptr")
        t_d0 = self.T("time", "first day", "day")
        t_d1 = self.T("time", "last day", "day")
        for p in range(SECD, STATTBL - 7, 8):
            self.F(p, 4, t_next)
            self.F(p + 4, 2, t_d0)
            self.F(p + 6, 2, t_d1)

        # station table: 469 x 34, one field per record.
        t_strec = self.T("index", "station record", "starec")
        for k, p in enumerate(range(STATTBL, NAMEHASH - 33, 34)):
            self.F(p, 34, t_strec)

        # name hash: 1024 x u32 pointers into C.
        t_hash = self.T("pointer", "name-hash pointer", "u32ptr")
        for p in range(NAMEHASH, FNIDX - 3, 4):
            self.F(p, 4, t_hash)

        # footnote index: 250 x u32 (footnote -> 1-based record in D).
        t_fidx = self.T("pointer", "footnote index", "u32ptr")
        for p in range(FNIDX, DATECAL - 3, 4):
            self.F(p, 4, t_fidx)

        # date calendar: 371 x 2 bytes, day-type of each date.
        t_cal = self.T("foot", "calendar day", "daytype")
        for k, p in enumerate(range(DATECAL, FARES - 1, 2)):
            self.F(p, 2, t_cal)

        # fares: 36 x 18 = distance cap + 8 prices.
        t_km = self.T("len", "distance band", "kmband")
        t_price = self.T("price", "fare", "cents")
        for p in range(FARES, SEASON - 17, 18):
            self.F(p, 2, t_km)
            for c in range(8):
                self.F(p + 2 + c * 2, 2, t_price)

        # season tickets: 14 x 18 (week/month) then 14 x 6 (youth month).
        for p in range(SEASON, JEUGD - 17, 18):
            self.F(p, 2, t_km)
            for c in range(8):
                self.F(p + 2 + c * 2, 2, t_price)
        for p in range(JEUGD, LINKCORR - 5, 6):
            self.F(p, 2, t_km)
            self.F(p + 2, 2, t_price)
            self.F(p + 4, 2, t_price)

        # link corrections: 24 x 6 = from, to, signed minutes.
        t_from = self.T("index", "correction from", "sta")
        t_to = self.T("index", "correction to", "sta")
        t_min = self.T("value", "correction", "smin")
        for p in range(LINKCORR, self.N - 5, 6):
            self.F(p, 2, t_from)
            self.F(p + 2, 2, t_to)
            self.F(p + 4, 2, t_min)

    # ── finish: sort, fill gaps, verify a clean tiling ──────────────────────
    def finalize(self):
        t_gap = self.T("marker", "unclassified", "hex")
        self.fields.sort(key=lambda f: f[0])
        out, cur = [], 0
        for start, length, t in self.fields:
            if start < cur:                         # overlap -> trim
                length -= (cur - start)
                start = cur
                if length <= 0:
                    continue
            if start > cur:                         # gap -> fill
                out.append((cur, start - cur, t_gap))
            out.append((start, length, t))
            cur = start + length
        if cur < self.N:
            out.append((cur, self.N - cur, t_gap))
        self.fields = out
        return out


def build(data, stations):
    a = Annotator(data)
    a.annotate_A(stations)
    a.annotate_rest(stations)
    a.finalize()
    return a.types, a.fields


def main():
    data = open(NET, "rb").read()
    stations = load_stations()
    types, fields = build(data, stations)
    # verify a perfect tiling
    cur, gaps, overlaps = 0, 0, 0
    for start, length, t in fields:
        if start > cur:
            gaps += 1
        elif start < cur:
            overlaps += 1
        cur = max(cur, start + length)
    covered = sum(l for _, l, _ in fields)
    gapcat = next(i for i, ty in enumerate(types) if ty["name"] == "unclassified")
    unclassified = sum(l for _, l, t in fields if t == gapcat)
    print(f"file           : {len(data):,} bytes")
    print(f"fields         : {len(fields):,}")
    print(f"types          : {len(types)}")
    print(f"covered        : {covered:,} bytes ({covered/len(data)*100:.2f}%)")
    print(f"gaps/overlaps  : {gaps} / {overlaps}")
    print(f"unclassified   : {unclassified:,} bytes "
          f"({unclassified/len(data)*100:.2f}%)")
    # rough packed size: varint(len) + 1 byte type
    packed = 0
    for _, l, _ in fields:
        packed += 1
        while l >= 0x80:
            packed += 1
            l >>= 7
    print(f"packed est     : {packed:,} bytes -> ~{packed*4//3:,} b64")
    # per-category byte share
    from collections import Counter
    cat = Counter()
    for _, l, t in fields:
        cat[types[t]["cat"]] += l
    print("by category    :", ", ".join(f"{c} {v/len(data)*100:.0f}%"
          for c, v in cat.most_common()))


if __name__ == "__main__":
    main()
