"""IMGW public API ingest for the station registry snapshot."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import httpx

API_URL = "https://danepubliczne.imgw.pl/api/data/hydro"
CONNECTIONS_URL = "https://hydro-back.imgw.pl/station/connections"


def fetch_snapshot(dest: Path, client: httpx.Client | None = None) -> list[dict]:
    """Download the current-station snapshot into dest (JSON)."""
    own = client is None
    client = client or httpx.Client(timeout=60.0, headers={"User-Agent": "hydroatlas-etl"})
    try:
        resp = client.get(API_URL)
        resp.raise_for_status()
        rows = resp.json()
    finally:
        if own:
            client.close()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def load_snapshot(path: Path) -> dict[str, dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(r.get("id_stacji", "")).strip(): r for r in rows}


def _get_connections(client: httpx.Client, sid: str) -> dict:
    """hydroUp/hydroDown for one station; nulls when IMGW has no data."""
    for attempt in range(3):
        try:
            resp = client.get(CONNECTIONS_URL, params={"dreCode": sid})
            if resp.status_code in (429, 503):
                continue
            resp.raise_for_status()
            data = resp.json()
            return {
                "up": (data.get("hydroUp") or {}).get("code"),
                "down": (data.get("hydroDown") or {}).get("code"),
            }
        except (httpx.HTTPError, ValueError):
            pass
    return {"up": None, "down": None}


def fetch_connections(dest: Path, ids: Iterable[str], workers: int = 8) -> dict:
    """Fetch the prev/next-station graph (hydro.imgw.pl "connections").

    Follows every hydroUp/hydroDown reference to closure, so chains that
    pass through stations outside ``ids`` still link up.  Result:
    ``{station_id: {"up": id|None, "down": id|None}}``.
    """
    out: dict[str, dict] = {}
    seen: set[str] = set()
    frontier = {str(i) for i in ids}
    with httpx.Client(
        timeout=30.0, headers={"User-Agent": "Mozilla/5.0 (hydroatlas ETL)"}
    ) as client, ThreadPoolExecutor(max_workers=workers) as pool:
        while frontier:
            batch = sorted(frontier - seen)
            if not batch:
                break
            seen.update(batch)
            for sid, rec in zip(batch, pool.map(lambda s: _get_connections(client, s), batch)):
                out[sid] = rec
            frontier = {
                nb
                for rec in out.values()
                for nb in (rec["up"], rec["down"])
                if nb and nb not in seen
            }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out
