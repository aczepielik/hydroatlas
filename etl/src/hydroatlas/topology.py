"""Render the transit-map index as plain SVG from the hand-tuned YAML layout."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .paths import layout_dir, site_dir

MAIN_COLOR = "#1a4e8a"
TRIB_COLOR = "#3b82f6"
LABEL_COLOR = "#475569"
RIVER_COLOR = "#64748b"


def _polyline(points: list[list[float]]) -> str:
    coords = " ".join(f"{x},{y}" for x, y in points)
    return coords


def render_map(layout_path: Path | None = None, stations_path: Path | None = None) -> str:
    layout = yaml.safe_load(
        (layout_path or (layout_dir() / "river-topology.yaml")).read_text(encoding="utf-8")
    )
    stations_path = stations_path or (site_dir() / "data" / "stations.json")
    names = {}
    if stations_path.is_file():
        names = {s["id"]: s.get("name") or s["id"] for s in json.loads(stations_path.read_text(encoding="utf-8"))}

    canvas = layout["canvas"]
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas["width"]}" '
        f'height="{canvas["height"]}" viewBox="0 0 {canvas["width"]} {canvas["height"]}" '
        'font-family="Roboto, system-ui, sans-serif">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]

    # River lines: every white casing first, then every coloured line on top,
    # so tributary casings cannot notch the main stem at junctions.
    for edge in layout["edges"]:
        width = 6 if edge.get("kind") == "main" else 4
        pts = _polyline(edge["points"])
        out.append(
            f'<polyline points="{pts}" fill="none" stroke="#ffffff" '
            f'stroke-width="{width + 5}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    for edge in layout["edges"]:
        width = 6 if edge.get("kind") == "main" else 4
        color = MAIN_COLOR if edge.get("kind") == "main" else TRIB_COLOR
        pts = _polyline(edge["points"])
        out.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        lp = edge.get("label_pos")
        if lp:
            anchor = lp.get("anchor", "middle")
            out.append(
                f'<text x="{lp["x"]}" y="{lp["y"]}" font-size="13" font-style="italic" '
                f'fill="{RIVER_COLOR}" text-anchor="{anchor}">{edge["label"]}</text>'
            )

    # Stations: linked dots with labels.
    for node in layout["stations"]:
        sid = str(node["id"])
        label = names.get(sid, sid)
        x, y = node["x"], node["y"]
        side = node.get("label", "above")
        if side == "left":
            tx, ty, anchor = x - 14, y + 4, "end"
        elif side == "right":
            tx, ty, anchor = x + 14, y + 4, "start"
        elif side == "below":
            tx, ty, anchor = x, y + 22, "middle"
        else:
            tx, ty, anchor = x, y - 13, "middle"
        out.append(f'<a href="/stacje/{sid}/">')
        out.append(
            f'<circle cx="{x}" cy="{y}" r="10" fill="transparent"/>'
            f'<circle cx="{x}" cy="{y}" r="5.5" fill="#ffffff" stroke="{MAIN_COLOR}" stroke-width="2.5"/>'
        )
        out.append(
            f'<text x="{tx}" y="{ty}" font-size="12" fill="{LABEL_COLOR}" '
            f'text-anchor="{anchor}">{label}</text>'
        )
        out.append("</a>")

    # Interchange rings (confluence stations) on top.
    for node in layout["stations"]:
        if node.get("interchange"):
            out.append(
                f'<circle cx="{node["x"]}" cy="{node["y"]}" r="9" fill="none" '
                f'stroke="{MAIN_COLOR}" stroke-width="2"/>'
            )

    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_map(dest: Path | None = None) -> Path:
    dest = dest or (site_dir() / "static" / "img" / "index-map.svg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_map(), encoding="utf-8")
    return dest
