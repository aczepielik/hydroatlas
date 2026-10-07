"""Per-river schematic pages: one SVG per river (transit-map style).

Not a geographic projection — a horizontal stem with the river's stations
as nodes along it, and direct tributaries as 45° stubs that link to the
tributary's own page (plain <a> navigation, no JS).  Top-level pages:
the Wisła and Odra group pages double as basin hubs (stem + stubs for
their children plus basin orphans); /rzeki/przymorza/ is a synthetic hub
listing the coastal rivers as stub rows.  Everything else is a plain
river page: stem + stations + child stubs.

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
X0, X1 = 70.0, 810.0
LINE = "#1a4e8a"
ACCENT = "#64748b"
PAPER = "#ffffff"
RESERVED_KEYS = {"przymorza"}
# Fixed URLs for the two top-level basin hubs (their groups merge two
# river_keys each, so the canonical key would otherwise be arbitrary).
HUB_KEYS = {"Wisła": "wisla", "Odra": "odra"}


# ------------------------------------------------------------ geometry

def _fraction_by_km(km: list[float]) -> list[float]:
    """Source (max km) at left, mouth (min km) at right — the usual
    river-profile reading direction."""
    lo, hi = min(km), max(km)
    if hi <= lo:
        n = len(km)
        return [0.5 + (i - (n - 1) / 2) * 0.06 for i in range(n)]
    return [(hi - k) / (hi - lo) for k in km]


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
        fractions[rec["id"]] = (i + 1) / (len(leftover) + 1)

    ordered = sorted(records, key=lambda r: (fractions[r["id"]], r["id"]))
    return [(r, fractions[r["id"]]) for r in ordered]


def _child_confluence_x(
    child_recs: list[dict],
    parent_recs: list[dict],
    sibling_i: int,
    sibling_n: int,
) -> float:
    """X coordinate where the child's stub leaves the parent's stem."""
    poly = [
        (r["lat"], r["lon"], f)
        for r, f in layout_stations(parent_recs)
        if r.get("lat") and r.get("lon")
    ]
    probe = next((r for r in child_recs if r.get("lat") and r.get("lon")), None)
    frac = _project(probe["lat"], probe["lon"], poly) if probe else None
    if frac is None:
        frac = 0.08 + 0.84 * (sibling_i + 1) / (sibling_n + 1)
    return X0 + max(0.0, min(1.0, frac)) * (X1 - X0)


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


def _stem_svg(
    group: dict, stub_groups: list[dict], groups: dict, height: int = 300
) -> str:
    records = _records(group, groups)
    layout = layout_stations(records)
    n = len(layout)
    font = 11 if n <= 18 else 9
    stem_y = height / 2.0

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" '
        f'font-family="Roboto, system-ui, sans-serif" width="100%">',
        f'<rect width="{W}" height="{height}" fill="{PAPER}"/>',
        f'<line x1="{X0}" y1="{stem_y}" x2="{X1}" y2="{stem_y}" '
        f'stroke="{LINE}" stroke-width="3"/>',
    ]

    # Tributary stubs (45°, alternating above / below), labels link out.
    # Two depths per side + min-gap skipping keep dense hubs readable.
    stub_tier_d = (44.0, 66.0, 44.0, 66.0)  # up-near, up-far, dn-near, dn-far
    stub_last_x = [-1e9] * 4
    for i, child in enumerate(stub_groups):
        child_recs = _records(child, groups)
        cx = _child_confluence_x(child_recs, records, i, len(stub_groups))
        tier = i % 4
        up = tier < 2
        d = stub_tier_d[tier]
        ex = cx + d
        ey = stem_y - d if up else stem_y + d
        label_y = ey + (-6 if up else 14)
        parts.append(
            f'<a href="/rzeki/{child["key"]}/">'
            f'<title>{_esc(child["name"])}</title>'
            f'<path d="M {cx:.1f} {stem_y} L {ex:.1f} {ey:.1f}" '
            f'stroke="{ACCENT}" stroke-width="2" fill="none"/>'
        )
        if ex + 5 - stub_last_x[tier] >= 70.0:
            stub_last_x[tier] = ex + 5
            parts.append(
                f'<text x="{ex + 5:.1f}" y="{label_y:.1f}" '
                f'text-anchor="start" font-style="italic" font-size="11" '
                f'fill="{LINE}">{_esc(child["name"])} ▸</text></a>'
            )
        else:
            parts.append("</a>")

    # Station nodes; labels cycle through four tiers (near/far × above/
    # below) and are skipped when a tier gets too dense — the node keeps
    # a hover tooltip.
    tier_y = (-16.0, 26.0, -34.0, 44.0)
    tier_last_x = [-1e9] * len(tier_y)
    min_gap = 60.0 if font >= 11 else 46.0
    for i, (rec, frac) in enumerate(layout):
        x = X0 + frac * (X1 - X0)
        tier = i % len(tier_y)
        parts.append(
            f'<a href="/stacje/{rec["id"]}/">'
            f'<title>{_esc(rec["name"])}</title>'
            f'<circle cx="{x:.1f}" cy="{stem_y}" r="5" fill="{PAPER}" '
            f'stroke="{LINE}" stroke-width="2.5"/>'
        )
        if x - tier_last_x[tier] >= min_gap:
            tier_last_x[tier] = x
            parts.append(
                f'<text x="{x:.1f}" y="{stem_y + tier_y[tier]:.1f}" '
                f'text-anchor="middle" font-size="{font}" '
                f'fill="#1e293b">{_esc(rec["name"])}</text></a>'
            )
        else:
            parts.append("</a>")

    parts.append("</svg>")
    return "".join(parts)


def _przymorza_svg(children: list[dict]) -> str:
    """Synthetic hub: rows of stubs, one per coastal river."""
    rows = [children[i : i + 8] for i in range(0, len(children), 8)] or [[]]
    row_h = 70.0
    height = 40 + row_h * len(rows)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height:.0f}" '
        f'font-family="Roboto, system-ui, sans-serif" width="100%">',
        f'<rect width="{W}" height="{height:.0f}" fill="{PAPER}"/>',
    ]
    for ri, row in enumerate(rows):
        y = 45 + ri * row_h
        parts.append(
            f'<line x1="{X0}" y1="{y}" x2="{X1}" y2="{y}" '
            f'stroke="{LINE}" stroke-width="3"/>'
        )
        if ri == 0:
            parts.append(
                f'<text x="{X0}" y="{y - 10}" font-size="12" '
                f'font-weight="600" fill="{ACCENT}">Rzeki Przymorza</text>'
            )
        for i, g in enumerate(row):
            cx = X0 + (X1 - X0) * (i + 0.5) / len(row)
            up = i % 2 == 0
            ey = y - 26 if up else y + 26
            parts.append(
                f'<a href="/rzeki/{g["key"]}/">'
                f'<path d="M {cx:.1f} {y} L {cx:.1f} {ey}" stroke="{ACCENT}" '
                f'stroke-width="2" fill="none"/>'
                f'<text x="{cx:.1f}" y="{ey + (-6 if up else 14):.1f}" '
                f'text-anchor="middle" font-style="italic" font-size="11" '
                f'fill="{LINE}">{_esc(g["name"])}</text></a>'
            )
    parts.append("</svg>")
    return "".join(parts)


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
        if g["parent"] == hub["name"] or g["parent"] is None:
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
            _stem_svg(g, stubs, groups, height), encoding="utf-8"
        )
        (content / f"{g['key']}.md").write_text(
            _stub(g["key"], g["name"]), encoding="utf-8"
        )

    # Synthetic przymorza hub: coastal rivers with no resolvable parent
    # group (their chains end at the sea, not at Wisła/Odra).
    przymorza = sorted(
        (g for g in public_groups if g["basin"] == "przymorza" and g["parent"] is None),
        key=lambda g: (-len(g["stations"]), g["name"]),
    )
    (svg_dir / "przymorza.svg").write_text(_przymorza_svg(przymorza), encoding="utf-8")
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
        light.append(
            {
                "name": g["name"],
                "key": g["key"],
                "basin": g["basin"],
                "parent": g["parent"],
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
        "basins": {"wisla": "Wisła", "odra": "Odra", "przymorza": "Rzeki Przymorza"},
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
