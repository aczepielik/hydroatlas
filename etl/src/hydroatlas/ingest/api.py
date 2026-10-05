"""IMGW public API ingest for the station registry snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

API_URL = "https://danepubliczne.imgw.pl/api/data/hydro"


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
