"""Build site/data/stations.json from clean data + curated refs + IMGW snapshot."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import yaml

from .paths import clean_dir, refs_dir
from .store import read_clean

_SEGMENT_RE = re.compile(r"\s*\(\d+\)$")


def load_curated() -> dict:
    path = refs_dir() / "stations-meta.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def curated_ids() -> list[str]:
    return [str(s["id"]) for s in load_curated()["stations"]]


def _display_name(raw: str) -> str:
    return raw.strip().title()


def _base_river(raw: str) -> str:
    return _SEGMENT_RE.sub("", raw.strip())


STATION_LIST_URL = (
    "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne"
    "/dane_hydrologiczne/lista_stacji_hydro.csv"
)


def download_station_list(dest: Path) -> None:
    import httpx

    resp = httpx.get(STATION_LIST_URL, timeout=60.0, headers={"User-Agent": "hydroatlas-etl"})
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)


def load_station_list(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """id -> (name, river) from lista_stacji_hydro.csv (CP1250)."""
    path = path or (refs_dir() / "stations-list.csv")
    text = path.read_bytes().decode("cp1250")
    out: dict[str, tuple[str, str]] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 3:
            out[row[0].strip()] = (row[1].strip(), row[2].strip())
    return out


def _block(df, col: str) -> dict | None:
    valid = df[col].notna()
    days = int(valid.sum())
    if days == 0:
        return None
    span = df.loc[valid, "date"]
    return {
        "start": int(span.min().year),
        "end": int(span.max().year),
        "days": days,
        "coverage": round(days / len(df), 3),
    }


def availability(sid: str) -> dict | None:
    path = clean_dir() / f"{sid}.csv.gz"
    if not path.is_file():
        return None
    df = read_clean(path)
    if df.empty:
        return None
    return {
        "discharge": _block(df, "discharge_m3s"),
        "stage": _block(df, "stage_cm"),
        "start": int(df["date"].min().year),
        "end": int(df["date"].max().year),
    }


def build_registry(snapshot: dict[str, dict] | None = None) -> list[dict]:
    curated = load_curated()
    snapshot = snapshot or {}
    meta_all = curated.get("metadata") or {}
    curated_by_id = {str(s["id"]): s for s in curated["stations"]}
    try:
        station_list = load_station_list()
    except FileNotFoundError:
        station_list = {}
    try:
        archive_names = json.loads(
            (refs_dir() / "station-names.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError):
        archive_names = {}

    clean_ids = sorted(p.name.removesuffix(".csv.gz") for p in clean_dir().glob("*.csv.gz"))
    ids = sorted(set(clean_ids) | set(curated_by_id))

    stations = []
    for sid in ids:
        entry = curated_by_id.get(sid, {})
        api = snapshot.get(sid, {})
        meta = dict(meta_all.get(sid) or {})
        list_name, list_river = station_list.get(sid, (None, None))
        arc_name, arc_river = archive_names.get(sid, (None, None))

        name = entry.get("name") or api.get("stacja") or list_name or arc_name
        if name:
            name = _display_name(name)
        river = entry.get("river") or api.get("rzeka") or list_river or arc_river
        river = _base_river(river) if river else None

        station = {
            "id": sid,
            "name": name,
            "river": river,
            "river_key": entry.get("river_key")
            or (re.sub(r"[^a-z0-9]+", "-", river.lower()).strip("-") if river else None),
            "voivodeship": api.get("wojewodztwo"),
            "lat": float(api["lat"]) if api.get("lat") else None,
            "lon": float(api["lon"]) if api.get("lon") else None,
            "km": float(api["kilometr_biegu_rzeki"]) if api.get("kilometr_biegu_rzeki") else None,
            "catchment_km2": meta.get("catchment_km2"),
            "regime": meta.get("regime"),
            "station_class": meta.get("station_class"),
            "unit_runoff_dm3s_km2": meta.get("unit_runoff_dm3s_km2"),
            "runoff_mm": meta.get("runoff_mm"),
            "network_density_km_km2": meta.get("network_density_km_km2"),
            "river_length_km": meta.get("river_length_km"),
            "annual_precip_mm": meta.get("annual_precip_mm"),
            "data": availability(sid),
            "map": sid in curated_by_id,
            "status": "active" if sid in snapshot else "historical",
        }
        stations.append(station)
    return stations


def write_registry(stations: list[dict], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(stations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
