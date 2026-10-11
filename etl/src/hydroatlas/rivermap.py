"""Per-river schematic pages: one SVG per river (transit-map style).

Not a geographic projection — a horizontal stem (source left, mouth
right) with the river's stations as nodes along it, direct tributaries
as 45° stubs angling *upstream* (to the left of their confluence) that
link to the tributary's own page, and the receiving waterbody (parent
river or basin hub) as an outflow stub past the mouth end.  Plain <a>
navigation, no JS.

Top-level pages: the Wisła and Odra group pages double as basin hubs
(stem + stubs for their children plus basin orphans); /rzeki/przymorza/
is a plain HTML table of coastal rivers (template-side, no SVG).
Everything else is a plain river page: stem + stations + child stubs +
parent stub.

Labels are placed with width-aware collision checks plus obstacle
avoidance: all stub line segments are computed before placement, and a
label row is rejected when its padded box would be crossed by a stub
line (stub labels also dodge station label boxes).  The canvas grows
(fixed pixel width, horizontal scroll in .map-frame) and placement is
retried both when a label finds no free row and when too many labels
remain crossed (MAX_CROSSINGS), so no label is silently dropped while
space could still be made.

Confluence estimate: the child's lowest-km station with coordinates is
projected onto the parent's km-ordered station polyline; when either
side lacks coordinates the stubs get evenly spaced slots.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .paths import site_dir
from .wikidata import load_snapshot, load_stations, resolve

W = 880
X0 = 70.0
LINE = "#1a4e8a"
ACCENT = "#64748b"
PAPER = "#ffffff"
RESERVED_KEYS = {"przymorza"}
# Fixed URLs for the two top-level basin hubs (their groups merge two
# river_keys each, so the canonical key would otherwise be arbitrary).
HUB_KEYS = {"Wisła": "wisla", "Odra": "odra"}
BASINS = {"wisla": "Wisła", "odra": "Odra", "przymorza": "Rzeki Przymorza"}
# Station fractions are padded away from the stem ends so the line
# visibly continues past the first station (source) and the last (mouth).
PAD0, PAD1 = 0.07, 0.93
# Vertical layout, relative to the stem: station label rows (near-up,
# mid-up, far-up / near-dn, mid-dn, far-dn) and tributary-stub label
# rows, kept in separate bands so the two kinds never collide.  Station
# rows come in three per side so a stretch where stub lines cross both
# near rows still has a clear far row; stub rows sit beyond them and
# get four depths per side (rows 0,2,4,6 up / 1,3,5,7 down) so
# congested stretches can spill further out instead of losing the label.
STATION_ROW_Y = (-16.0, -34.0, -52.0, 26.0, 44.0, 62.0)
STUB_DEPTHS = (44.0, 66.0, 88.0, 110.0)
STUB_ROW_Y = (-72.0, 82.0, -94.0, 104.0, -116.0, 126.0, -138.0, 146.0)
STUB_ROWS_UP = (0, 2, 4, 6)
STUB_ROWS_DN = (1, 3, 5, 7)
HALO = 'stroke="#ffffff" stroke-width="3" paint-order="stroke"'
# Placement tolerates this many labels still crossed by a stub line
# before it grows the canvas: the densest clusters (e.g. the Wisła
# source stretch) cannot be made fully clean at any practical width,
# and growing further only adds horizontal scroll.
MAX_CROSSINGS = 4


# ------------------------------------------------------------ geometry

def _pad(f: float) -> float:
    return PAD0 + (PAD1 - PAD0) * f


def _fraction_by_km(km: list[float]) -> list[float]:
    """Source (max km) at left, mouth (min km) at right — the usual
    river-profile reading direction."""
    lo, hi = min(km), max(km)
    if hi <= lo:
        n = len(km)
        raw = [0.5 + (i - (n - 1) / 2) * 0.06 for i in range(n)]
    else:
        raw = [(hi - k) / (hi - lo) for k in km]
    return [_pad(f) for f in raw]


def _project(lat: float, lon: float, poly: list[tuple[float, float, float]]) -> float | None:
    """Nearest point on a (lat, lon, fraction) polyline -> fraction."""
    if not poly:
        return None
    if len(poly) == 1:
        return poly[0][2]
    best_d: float | None = None
    best_f = poly[0][2]
    for (la1, lo1, f1), (la2, lo2, f2) in zip(poly, poly[1:]):
        # planar approximation in degrees (short segments; fine for schematics)
        dlat, dlon = la2 - la1, lo2 - lo1
        seg2 = dlat * dlat + dlon * dlon
        if seg2 == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((lat - la1) * dlat + (lon - lo1) * dlon) / seg2))
        pl, po = la1 + t * dlat, lo1 + t * dlon
        d = (lat - pl) ** 2 + (lon - po) ** 2
        if best_d is None or d < best_d:
            best_d, best_f = d, f1 + t * (f2 - f1)
    return best_f


def layout_stations(records: list[dict]) -> list[tuple[dict, float]]:
    """Ordered (record, fraction) pairs along the stem."""
    with_km = sorted(
        (r for r in records if r.get("km") is not None), key=lambda r: r["km"]
    )
    fractions: dict[str, float] = {}

    if with_km:
        for rec, f in zip(with_km, _fraction_by_km([r["km"] for r in with_km])):
            fractions[rec["id"]] = f

    poly = [
        (r["lat"], r["lon"], fractions[r["id"]])
        for r in with_km
        if r.get("lat") and r.get("lon")
    ]
    # Stations without km but with coordinates: project onto the polyline.
    for rec in records:
        if rec["id"] in fractions:
            continue
        if poly and rec.get("lat") and rec.get("lon"):
            f = _project(rec["lat"], rec["lon"], poly)
            if f is not None:
                fractions[rec["id"]] = f
    # Whatever is left (no km, no coords, or empty polyline): even fill —
    # registry order preserved among the leftovers.
    leftover = [r for r in records if r["id"] not in fractions]
    for i, rec in enumerate(leftover):
        fractions[rec["id"]] = _pad((i + 1) / (len(leftover) + 1))

    ordered = sorted(records, key=lambda r: (fractions[r["id"]], r["id"]))
    return [(r, fractions[r["id"]]) for r in ordered]


def _child_confluence_x(
    child_recs: list[dict],
    poly: list[tuple[float, float, float]],
    sibling_i: int,
    sibling_n: int,
    x0: float,
    x1: float,
) -> float:
    """X coordinate where the child's stub leaves the parent's stem."""
    # The child's downstream-most station with coordinates is the one
    # nearest its mouth, i.e. nearest the confluence.
    with_xy = [r for r in child_recs if r.get("lat") and r.get("lon")]
    with_km = [r for r in with_xy if r.get("km") is not None]
    probe = min(with_km, key=lambda r: r["km"]) if with_km else (
        with_xy[0] if with_xy else None
    )
    frac = _project(probe["lat"], probe["lon"], poly) if probe else None
    if frac is None:
        frac = 0.08 + 0.84 * (sibling_i + 1) / (sibling_n + 1)
    return x0 + max(0.0, min(1.0, frac)) * (x1 - x0)


# ------------------------------------------------------------- records

def index_stations(stations: list[dict], groups: dict) -> None:
    groups["_by_id"] = {s["id"]: s for s in stations}


def _records(group: dict, groups: dict) -> list[dict]:
    by_id = groups["_by_id"]
    return [by_id[sid] for sid in group["stations"]]


# ---------------------------------------------------------------- svg

def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _text_w(text: str, font: float, italic: bool = False) -> float:
    """Approximate rendered label width.

    0.60 × font × len is measured against the fallback fonts the SVGs
    actually render with (Noto Sans in Inkscape, DejaVu Sans in some
    browsers — Roboto is not CDN-loaded): worst-case DejaVu advance for
    the Wisła station names is ~1.07 × the old 0.58 factor, which made
    same-row labels collide in wider fallbacks.
    """
    w = 0.60 * font * len(str(text))
    return w * 1.04 if italic else w


# Label boxes are inflated by this margin so text does not graze
# obstacle lines; ascent/descent match the measured glyph bands.
_PAD = 3.0


def _label_box(
    x0: float, x1: float, baseline: float, font: float
) -> tuple[float, float, float, float]:
    """Padded AABB of a label laid out between x0..x1 on ``baseline``."""
    return (x0 - _PAD, baseline - 0.85 * font - _PAD,
            x1 + _PAD, baseline + 0.30 * font + _PAD)


def _seg_box_cost(
    ax: float, ay: float, bx: float, by: float,
    box: tuple[float, float, float, float],
) -> tuple[int, float]:
    """(violations, overlap length) of segment a→b through a label box.

    Liang–Barsky clip of the segment against the box; a hit counts as
    one violation and reports how much of the segment lies inside, so
    equal-violation rows prefer the crossing with least ink overlap.
    """
    x0, y0, x1, y1 = box
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0.0:
            if q < 0.0:
                return (0, 0.0)
        else:
            r = q / p
            if p < 0.0:
                if r > t1:
                    return (0, 0.0)
                t0 = max(t0, r)
            else:
                if r < t0:
                    return (0, 0.0)
                t1 = min(t1, r)
    if t0 > t1:
        return (0, 0.0)
    return (1, (t1 - t0) * ((dx * dx + dy * dy) ** 0.5))


def _rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[int, float]:
    """(violations, overlap area) of two label boxes."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x0 >= x1 or y0 >= y1:
        return (0, 0.0)
    return (1, (x1 - x0) * (y1 - y0))


class _LabelRows:
    """Width-aware label placement across a fixed set of rows."""

    def __init__(self, gap: float = 10.0) -> None:
        self.rows: list[list[tuple[float, float]]] = []
        self.gap = gap

    def place(
        self,
        rows: tuple[int, ...],
        x0: float,
        x1: float,
        cost=None,
    ) -> int | None:
        """Claim a free row for [x0, x1] — the first row in ``rows``
        without a label collision.  With ``cost`` (a callable mapping a
        row to a (violations, magnitude) tuple for lines/boxes crossing
        the candidate), the free row with the fewest violations wins;
        ties keep the earlier, preferred row.  Returns None only when
        every row is label-blocked."""
        best_row: int | None = None
        best_cost: tuple[int, float] = (0, 0.0)
        for row in rows:
            while len(self.rows) <= row:
                self.rows.append([])
            if any(
                not (x1 + self.gap <= a or b + self.gap <= x0)
                for a, b in self.rows[row]
            ):
                continue
            c = (0, 0.0) if cost is None else cost(row)
            if best_row is None or c < best_cost:
                best_row, best_cost = row, c
            if best_cost == (0, 0.0):
                break
        if best_row is None:
            return None
        self.rows[best_row].append((x0, x1))
        return best_row


def _stem_svg(
    group: dict,
    stub_groups: list[dict],
    groups: dict,
    parent: tuple[str, str] | None = None,
    height: int = 300,
) -> str:
    """Render the schematic, growing the canvas until every label fits
    and few enough labels are still crossed by stub lines.

    ``parent`` is the (key, name) of the receiving waterbody shown as an
    outflow stub past the mouth end of the stem.
    """
    records = _records(group, groups)
    layout = layout_stations(records)
    font = 11 if len(layout) <= 18 else 9
    right_margin = 46.0
    if parent:
        right_margin = _text_w(parent[1], 11, italic=True) + 56.0

    width = float(W)
    svg = ""
    for _ in range(8):
        svg, dropped, crossings = _render_stem(
            group, stub_groups, groups, layout, font, parent, right_margin, height, width
        )
        if (dropped == 0 and crossings <= MAX_CROSSINGS) or width >= 4200:
            break
        width = min(width * 1.5, 4400.0)
    return svg


def _render_stem(
    group: dict,
    stub_groups: list[dict],
    groups: dict,
    layout: list[tuple[dict, float]],
    font: int,
    parent: tuple[str, str] | None,
    right_margin: float,
    height: int,
    width: float,
) -> tuple[str, int, int]:
    """One placement pass; returns (svg, labels that did not fit,
    labels still crossed by a stub line / colliding with a station)."""
    x0, x1 = X0, width - right_margin
    stem_y = height / 2.0
    poly = [
        (r["lat"], r["lon"], f) for r, f in layout if r.get("lat") and r.get("lon")
    ]
    st_rows, stub_rows = _LabelRows(), _LabelRows(gap=12.0)
    dropped = 0
    crossings = 0

    # Geometry pass: every tributary stub segment (confluence → deep
    # end) is known before any label is placed, so labels can avoid
    # the lines that will be drawn through their rows.
    stubs: list[dict] = []
    for i, child in enumerate(stub_groups):
        child_recs = _records(child, groups)
        cx = _child_confluence_x(child_recs, poly, i, len(stub_groups), x0, x1)
        tier = i % 8
        up = tier % 2 == 0
        d = STUB_DEPTHS[tier // 2]
        ex = cx - d
        ey = stem_y - d if up else stem_y + d
        stubs.append(
            {"child": child, "cx": cx, "ex": ex, "ey": ey, "up": up,
             "label": f'{child["name"]} ▸', "row": None}
        )
    segs = [(s["cx"], stem_y, s["ex"], s["ey"]) for s in stubs]

    # Station labels: first row in preference order that no stub line
    # crosses; if every row is crossed, the least-crossed one (better
    # than dropping the label).  Drawn after placement below.  Order:
    # near-up, near-dn, mid-up, mid-dn, far-up, far-dn.
    row_order = (0, 3, 1, 4, 2, 5)
    stations: list[tuple[dict, float, float, float, int | None]] = []
    for rec, frac in layout:
        x = x0 + frac * (x1 - x0)
        w = _text_w(rec["name"], font)

        def station_cost(row: int, x: float = x, w: float = w) -> tuple[int, float]:
            box = _label_box(x - w / 2.0, x + w / 2.0,
                             stem_y + STATION_ROW_Y[row], font)
            total = (0, 0.0)
            for seg in segs:
                h = _seg_box_cost(*seg, box)
                total = (total[0] + h[0], total[1] + h[1])
            return total

        row = st_rows.place(row_order, x - w / 2.0, x + w / 2.0, station_cost)
        if row is not None and station_cost(row) != (0, 0.0):
            crossings += 1
        stations.append((rec, frac, x, w, row))

    station_boxes = [
        _label_box(x - w / 2.0, x + w / 2.0,
                   stem_y + STATION_ROW_Y[row], font)
        for _, _, x, w, row in stations
        if row is not None
    ]

    # Stub labels: avoid *other* stub lines (own line may graze its
    # anchor side) and every station label box.
    for i, s in enumerate(stubs):
        label = s["label"]
        w = _text_w(label, 11, italic=True)
        # Candidate anchors in preference order: left of the stub end
        # (outside the fork), right of the end, canvas-edge pin.  Each
        # is tried until one lands in a free row — flipping the side
        # rescues labels whose preferred side has no room left instead
        # of growing the canvas.
        cands: list[tuple[float, str, float, float]] = []
        anchor_x = s["ex"] - 5.0
        if anchor_x - w >= 4.0:
            # preferred: left of the stub end (outside the fork)
            cands.append((anchor_x, "end", anchor_x - w, anchor_x))
        if s["ex"] + 5.0 + w <= width - 4.0:
            # right of the end — used when left clips or has no free row
            cands.append(
                (s["ex"] + 5.0, "start", s["ex"] + 5.0, s["ex"] + 5.0 + w)
            )
        # canvas-edge pin as the last resort (the only option when both
        # sides clip); may repeat an earlier range, which is harmless
        cands.append((4.0, "start", 4.0, 4.0 + w))
        side_rows = STUB_ROWS_UP if s["up"] else STUB_ROWS_DN

        def stub_cost(
            row: int, i: int, ix0: float, ix1: float
        ) -> tuple[int, float]:
            box = _label_box(ix0, ix1, stem_y + STUB_ROW_Y[row], 11)
            total = (0, 0.0)
            for j, seg in enumerate(segs):
                if j == i:
                    continue
                h = _seg_box_cost(*seg, box)
                total = (total[0] + h[0], total[1] + h[1])
            for b in station_boxes:
                h = _rects_overlap(box, b)
                total = (total[0] + h[0], total[1] + h[1])
            return total

        row = None
        for tx, text_anchor, ix0, ix1 in cands:
            row = stub_rows.place(
                side_rows, ix0, ix1,
                lambda r, i=i, ix0=ix0, ix1=ix1: stub_cost(r, i, ix0, ix1),
            )
            if row is not None:
                s["tx"], s["text_anchor"] = tx, text_anchor
                if stub_cost(row, i, ix0, ix1) != (0, 0.0):
                    crossings += 1
                break
        s["row"] = row
        if row is None:
            dropped += 1

    # Draw pass: stem, stub lines + labels, station nodes + labels.
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height}" '
        f'font-family="Roboto, system-ui, sans-serif" width="{width:.0f}">',
        f'<rect width="{width:.0f}" height="{height}" fill="{PAPER}"/>',
        f'<line x1="{x0}" y1="{stem_y}" x2="{x1:.1f}" y2="{stem_y}" '
        f'stroke="{LINE}" stroke-width="3"/>',
    ]

    # Tributary stubs: 45°, angling upstream (left of the confluence),
    # alternating above / below across four depths; labels width-checked.
    for s in stubs:
        child = s["child"]
        parts.append(
            f'<a href="/rzeki/{child["key"]}/">'
            f'<title>{_esc(child["name"])}</title>'
            f'<path d="M {s["cx"]:.1f} {stem_y} L {s["ex"]:.1f} {s["ey"]:.1f}" '
            f'stroke="{ACCENT}" stroke-width="2" fill="none"/>'
        )
        if s["row"] is None:
            parts.append("</a>")
        else:
            parts.append(
                f'<text x="{s["tx"]:.1f}" y="{stem_y + STUB_ROW_Y[s["row"]]:.1f}" '
                f'text-anchor="{s["text_anchor"]}" font-style="italic" '
                f'font-size="11" fill="{LINE}" {HALO}>{_esc(s["label"])}</text></a>'
            )

    # Station nodes; labels go in the first row (near-first) with room.
    for rec, frac, x, w, row in stations:
        parts.append(
            f'<a href="/stacje/{rec["id"]}/">'
            f'<title>{_esc(rec["name"])}</title>'
            f'<circle cx="{x:.1f}" cy="{stem_y}" r="5" fill="{PAPER}" '
            f'stroke="{LINE}" stroke-width="2.5"/>'
        )
        if row is None:
            dropped += 1
            parts.append("</a>")
        else:
            parts.append(
                f'<text x="{x:.1f}" y="{stem_y + STATION_ROW_Y[row]:.1f}" '
                f'text-anchor="middle" font-size="{font}" '
                f'fill="#1e293b" {HALO}>{_esc(rec["name"])}</text></a>'
            )

    # Outflow stub: the stem continues into the receiving waterbody.
    if parent:
        key, name = parent
        parts.append(
            f'<a href="/rzeki/{key}/">'
            f'<title>{_esc(name)}</title>'
            f'<path d="M {x1:.1f} {stem_y} L {x1 + 22:.1f} {stem_y}" '
            f'stroke="{LINE}" stroke-width="3" fill="none"/>'
            f'<text x="{x1 + 28:.1f}" y="{stem_y + 4:.1f}" text-anchor="start" '
            f'font-style="italic" font-size="11" fill="{LINE}">'
            f'{_esc(name)} ▸</text></a>'
        )

    parts.append("</svg>")
    return "".join(parts), dropped, crossings


# ---------------------------------------------------------- hub children

def hub_children(hub: dict, groups: dict) -> list[dict]:
    """Basin hub stubs: same-basin groups that are children of the hub
    or orphans (parent outside the dataset / unresolved)."""
    out = []
    for name, g in groups.items():
        if name.startswith("_"):
            continue
        if g is hub or not g["stations"]:
            continue
        if g["basin"] != hub["basin"]:
            continue
        if g["parent"] == hub["gid"] or g["parent"] is None:
            out.append(g)
    out.sort(key=lambda g: (-len(g["stations"]), g["name"]))
    return out


# ------------------------------------------------------------ write-out

def _stub(key: str, title: str) -> str:
    return f'---\ntitle: "{title}"\nriver_key: "{key}"\n---\n'


def write_rivers(stations: list[dict], snap: dict) -> dict:
    """Resolve, render all SVGs, content stubs and site/data/rivers.json."""
    res = resolve(snap, stations)
    groups = res["groups"]
    index_stations(stations, groups)

    def renderable(g: dict) -> bool:
        # IMGW has placeholder rivers ("-" with an empty key); they are
        # not rivers and would produce hidden/broken pages.
        return bool(g["stations"]) and bool(g["name"].strip(" -")) and bool(g["key"].strip(" -"))

    public_groups = [
        g for name, g in groups.items() if not name.startswith("_") and renderable(g)
    ]

    # Fixed hub URLs; keep the reserved przymorza slug out of river pages.
    for g in public_groups:
        if g["name"] in HUB_KEYS:
            g["key"] = HUB_KEYS[g["name"]]
        elif g["key"] in RESERVED_KEYS:
            g["key"] = "rzeka-" + g["key"]

    # Unique keys: different display names can share a river_key (e.g.
    # Widna/Świdna -> widna).  Hub keys are assigned first and win.
    seen: set[str] = set()
    for g in sorted(public_groups, key=lambda g: g["name"] not in HUB_KEYS):
        if g["key"] in seen:
            n = 2
            while f"{g['key']}-{n}" in seen:
                n += 1
            g["key"] = f"{g['key']}-{n}"
        seen.add(g["key"])

    def renderable_children(g: dict) -> list[dict]:
        return [
            groups[c]
            for c in g["children"]
            if c in groups and renderable(groups[c])
        ]

    def parent_stub(g: dict) -> tuple[str, str] | None:
        """Receiving waterbody for the mouth-end outflow stub: the parent
        river when it is a renderable group, else the basin hub (never
        the page itself)."""
        if g["parent"] and g["parent"] in groups:
            p = groups[g["parent"]]
            if renderable(p) and p["key"] != g["key"]:
                return (p["key"], p["name"])
        hub_key = g["basin"]
        if hub_key in BASINS and hub_key != g["key"]:
            return (hub_key, BASINS[hub_key])
        return None

    site = site_dir()
    svg_dir = site / "static" / "rzeki"
    content = site / "content" / "rzeki"
    if svg_dir.exists():
        shutil.rmtree(svg_dir)
    if content.exists():
        shutil.rmtree(content)
    svg_dir.mkdir(parents=True)
    content.mkdir(parents=True)

    for g in public_groups:
        is_hub = g["name"] in HUB_KEYS
        stubs = (
            [s for s in hub_children(g, groups) if renderable(s)]
            if is_hub
            else renderable_children(g)
        )
        height = 300 if len(g["stations"]) <= 30 else 340
        (svg_dir / f"{g['key']}.svg").write_text(
            _stem_svg(g, stubs, groups, parent_stub(g), height), encoding="utf-8"
        )
        (content / f"{g['key']}.md").write_text(
            _stub(g["key"], g["name"]), encoding="utf-8"
        )

    # /rzeki/przymorza/ is a plain HTML table in the template (coastal
    # rivers with no resolvable parent group — chains ending at the sea,
    # not at Wisła/Odra); no SVG is generated for it.
    (content / "przymorza.md").write_text(
        _stub("przymorza", "Rzeki Przymorza"), encoding="utf-8"
    )
    (content / "_index.md").write_text('---\ntitle: "Rzeki"\n---\n', encoding="utf-8")

    # rivers.json for templates: light records, no coordinate blobs.
    light = []
    for g in sorted(public_groups, key=lambda g: (g["basin"], g["name"])):
        recs = sorted(
            _records(g, groups),
            key=lambda r: (r.get("km") is None, r.get("km") or 0, r["name"]),
        )
        # g["parent"] is an internal gid; export the display name.
        parent_name = (
            groups[g["parent"]]["name"] if g["parent"] in groups else None
        )
        light.append(
            {
                "name": g["name"],
                "key": g["key"],
                "basin": g["basin"],
                "parent": parent_name,
                "children": [
                    groups[c]["key"]
                    for c in g["children"]
                    if c in groups and renderable(groups[c])
                ],
                "stations": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "km": r.get("km"),
                        "status": r.get("status"),
                    }
                    for r in recs
                ],
            }
        )
    data = {
        "generated": snap.get("fetched"),
        "basins": BASINS,
        "rivers": light,
    }
    (site / "data" / "rivers.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    res["written"] = len(public_groups)
    return res


def command_rivers() -> tuple[int, list[str]]:
    stations = load_stations()
    snap = load_snapshot()
    res = write_rivers(stations, snap)
    return res.get("written", 0), res["unresolved"]
