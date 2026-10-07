"""Wikidata river-hierarchy snapshot (P403 mouth-of-waterbody).

Two layers, cleanly separated:

1. ``fetch_snapshot`` (network, ``wikidata --refresh``): SPARQL queries
   that collect, for every distinct river display name in
   ``site/data/stations.json``, the candidate Wikidata items carrying
   that label (restricted to watercourses), their mouth coordinates and
   the transitive P403 parent chain.  Raw data only — committed to
   ``etl/refs/wikidata-p403.json``; afterwards the ETL is fully offline.

2. ``resolve`` (offline, every ``rivers`` run): picks one QID per river
   name (overrides file > unique candidate > nearest-mouth heuristic),
   merges river_keys that share a display name, walks the parent chain
   to a root and classifies the basin: ``wisla`` (chain reaches Q548),
   ``odra`` (reaches Q552), ``przymorza`` (reaches a non-Wisła/Odra
   terminal — Polish coastal rivers), ``unknown`` (no QID).

Ambiguities (the "Wisła" label matches dozens of items) are handled by
``etl/refs/wikidata-overrides.json``: ``{"match": {"Nazwa": "Q…"|null}}``
— null forces unknown; basin/parent overrides are not needed because
chains resolve deterministically once the QID is pinned.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .paths import refs_dir, site_dir

WISLA_QID = "Q548"
ODRA_QID = "Q552"
USER_AGENT = "hydroatlas/0.1 (hydrological reference site; contact via GitHub)"
SNAPSHOT_PATH = refs_dir() / "wikidata-p403.json"
OVERRIDES_PATH = refs_dir() / "wikidata-overrides.json"

_NAME_CHUNK = 80   # VALUES entries per SPARQL query
_QID_CHUNK = 250
_MAX_CHAIN_ROUNDS = 12


# ---------------------------------------------------------------- fetch

def _sparql(client: httpx.Client, query: str) -> list[dict]:
    response = client.post(
        "https://query.wikidata.org/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=90,
    )
    response.raise_for_status()
    return response.json()["results"]["bindings"]


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"@pl'


def _fetch_label_candidates(client: httpx.Client, names: list[str]) -> list[dict]:
    """All watercourse items carrying each name, with mouth coordinates."""
    values = " ".join(_quote(n) for n in names)
    query = f"""
    SELECT ?item ?itemLabel ?coord ?name WHERE {{
      VALUES ?name {{ {values} }}
      ?item rdfs:label ?name.
      ?item wdt:P31/wdt:P279* ?cls.
      VALUES ?cls {{ wd:Q4022 wd:Q3243762 wd:Q11565 }}
      OPTIONAL {{ ?item wdt:P625 ?coord. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,en". }}
    }}
    """
    return _sparql(client, query)


def _fetch_edges(client: httpx.Client, qids: list[str]) -> list[dict]:
    """P403 parent edges + labels for a batch of QIDs (any entity type)."""
    values = " ".join(f"wd:{q}" for q in qids)
    query = f"""
    SELECT ?item ?parent ?parentLabel WHERE {{
      VALUES ?item {{ {values} }}
      OPTIONAL {{ ?item wdt:P403 ?parent. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,en". }}
    }}
    """
    return _sparql(client, query)


def fetch_snapshot(names: list[str]) -> dict:
    """Fetch the raw hierarchy snapshot for ``names`` (network)."""
    candidates: dict[str, list[dict]] = {}
    labels: dict[str, str] = {}
    edges: dict[str, str | None] = {}

    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=90, follow_redirects=True
    ) as client:
        for i in range(0, len(names), _NAME_CHUNK):
            chunk = names[i : i + _NAME_CHUNK]
            for row in _fetch_label_candidates(client, chunk):
                qid = row["item"]["value"].rsplit("/", 1)[-1]
                name = row["name"]["value"]
                labels[qid] = row["itemLabel"]["value"]
                bucket = candidates.setdefault(name, [])
                if any(c["qid"] == qid for c in bucket):
                    continue  # same item, different coord row
                coord = None
                if "coord" in row:
                    # WKT Point(longitude latitude) — swap into [lat, lon].
                    lon_s, lat_s = (
                        row["coord"]["value"]
                        .replace("Point(", "")
                        .replace(")", "")
                        .split()
                    )
                    coord = [float(lat_s), float(lon_s)]
                bucket.append({"qid": qid, "label": labels[qid], "coord": coord})
            time.sleep(0.3)

        # Walk P403 chains to a fixed point (rivers, bays, seas …).
        frontier = {e["qid"] for rows in candidates.values() for e in rows}
        seen = set(frontier)
        for _ in range(_MAX_CHAIN_ROUNDS):
            frontier = {q for q in frontier if q not in edges}
            if not frontier:
                break
            discovered: set[str] = set()
            qids = sorted(frontier)
            for i in range(0, len(qids), _QID_CHUNK):
                batch = qids[i : i + _QID_CHUNK]
                for row in _fetch_edges(client, batch):
                    qid = row["item"]["value"].rsplit("/", 1)[-1]
                    if "parent" in row:
                        parent = row["parent"]["value"].rsplit("/", 1)[-1]
                        edges[qid] = parent
                        discovered.add(parent)
                        if "parentLabel" in row:
                            labels.setdefault(parent, row["parentLabel"]["value"])
                    else:
                        edges.setdefault(qid, None)
                    if "itemLabel" in row:
                        labels.setdefault(qid, row["itemLabel"]["value"])
                time.sleep(0.3)
            frontier = discovered - seen
            seen |= discovered

    return {
        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidates": candidates,
        "labels": labels,
        "edges": edges,
    }


# ------------------------------------------------------------- resolve

def _load_overrides() -> dict:
    if OVERRIDES_PATH.is_file():
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {"match": {}}


def _dist_km(a: list[float], b: list[float]) -> float:
    """Equirectangular approximation — good enough for nearest-mouth picks."""
    import math

    lat = math.radians((a[0] + b[0]) / 2)
    dlat = math.radians(a[0] - b[0])
    dlon = math.radians(a[1] - b[1])
    return 6371 * math.hypot(dlat, math.cos(lat) * dlon)


def pick_qid(
    name: str,
    cands: list[dict],
    centroid: list[float] | None,
    overrides: dict,
) -> str | None:
    match = overrides.get("match", {})
    if name in match:
        return match[name]
    # Dedupe by QID (same item can appear via several label rows).
    uniq: dict[str, dict] = {}
    for c in cands:
        uniq.setdefault(c["qid"], c)
    cands = list(uniq.values())
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]["qid"]
    if centroid:
        with_coord = [c for c in cands if c["coord"]]
        if with_coord:
            return min(with_coord, key=lambda c: _dist_km(centroid, c["coord"]))["qid"]
    return None  # ambiguous without a tiebreaker -> overrides file


def resolve(snapshot: dict, stations: list[dict]) -> dict:
    """Pure offline resolution: river groups, parents and basins.

    Groups are keyed by display name (river_keys sharing a name are
    merged — the data has three such collisions) via their canonical
    river_key: the key carrying the most stations.
    """
    overrides = _load_overrides()
    labels = dict(snapshot.get("labels", {}))
    edges = dict(snapshot.get("edges", {}))
    # Manual P403 fixes for statements Wikidata lacks (rare).
    edges.update(overrides.get("edges", {}))
    candidates = snapshot.get("candidates", {})

    # Group river_keys by display name; canonical = most stations.
    groups: dict[str, dict] = {}
    centroid_acc: dict[str, list[float]] = {}
    for st in stations:
        key = st["river_key"]
        if key.lower().startswith("jez"):
            continue  # lakes are not rivers; they have their own index
        name = st["river"]
        if not name.strip(" -"):
            continue  # IMGW placeholder rows ("-" / empty river name)
        g = groups.setdefault(
            name,
            {"name": name, "keys": set(), "stations": [], "qid": None,
             "parent_qid": None, "parent": None, "basin": "unknown"},
        )
        g["keys"].add(key)
        g["stations"].append(st["id"])
        if st.get("lat") and st.get("lon"):
            centroid_acc.setdefault(name, [0.0, 0.0, 0])
            c = centroid_acc[name]
            c[0] += st["lat"]
            c[1] += st["lon"]
            c[2] += 1

    centroids = {
        n: [c[0] / c[2], c[1] / c[2]] for n, c in centroid_acc.items() if c[2]
    }

    for name, g in groups.items():
        g["qid"] = pick_qid(name, candidates.get(name, []), centroids.get(name), overrides)
        keys = sorted(g["keys"])
        # canonical river_key: most stations, tie -> lexicographic
        counts = {k: sum(1 for s in stations if s["river_key"] == k) for k in keys}
        g["key"] = max(keys, key=lambda k: (counts[k], k))
        del g["keys"]

    # Parent chain walk (memoized, loop-safe).
    memo: dict[str, str | None] = {}

    def terminal_of(qid: str | None, guard: frozenset[str] = frozenset()) -> str | None:
        """Follow P403 to the last reachable QID; None if unresolved."""
        if qid is None:
            return None
        if qid in memo:
            return memo[qid]
        if qid in guard:
            return qid  # cycle: treat current as terminal
        parent = edges.get(qid)
        if parent is None:
            memo[qid] = qid
            return qid
        result = terminal_of(parent, guard | {qid})
        memo[qid] = result
        return result

    chain_cache: dict[str, list[str]] = {}

    def chain_of(qid: str | None) -> list[str]:
        out: list[str] = []
        cur = qid
        seen: set[str] = set()
        while cur and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = edges.get(cur)
        return out

    for name, g in groups.items():
        if g["qid"] is None:
            g["basin"] = "unknown"
            continue
        chain = chain_of(g["qid"])
        chain_cache[name] = chain
        if WISLA_QID in chain:
            g["basin"] = "wisla"
        elif ODRA_QID in chain:
            g["basin"] = "odra"
        elif len(chain) > 1 and terminal_of(g["qid"]) is not None:
            # Chain reaches a non-Wisła/Odra terminal (bay, sea, lake):
            # a coastal river.  A single-node chain means Wikidata has no
            # mouth statement at all -> unknown, not coastal.
            g["basin"] = "przymorza"
        else:
            g["basin"] = "unknown"

    # Immediate parent group: parent_qid must be another group's QID.
    qid_to_group = {
        g["qid"]: g["name"] for g in groups.values() if g["qid"] is not None
    }
    for name, g in groups.items():
        parent_qid = edges.get(g["qid"]) if g["qid"] else None
        g["parent_qid"] = parent_qid
        parent_name = qid_to_group.get(parent_qid)
        if parent_name and parent_name != name:
            g["parent"] = parent_name

    # Children index (by group name).
    for g in groups.values():
        g["children"] = []
    for g in groups.values():
        if g["parent"]:
            groups[g["parent"]]["children"].append(g["name"])
    for g in groups.values():
        g["children"].sort(key=lambda n: (-len(groups[n]["stations"]), n))

    by_key = {g["key"]: g for g in groups.values()}
    unresolved = sorted(n for n, g in groups.items() if g["qid"] is None)
    return {"groups": groups, "by_key": by_key, "unresolved": unresolved}


# ----------------------------------------------------------------- IO

def load_snapshot() -> dict:
    if not SNAPSHOT_PATH.is_file():
        raise FileNotFoundError(
            f"missing {SNAPSHOT_PATH} — run `uv run hydroatlas wikidata --refresh` "
            "(network) once and commit the result"
        )
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def write_snapshot(snapshot: dict) -> Path:
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return SNAPSHOT_PATH


def load_stations() -> list[dict]:
    return json.loads(
        (site_dir() / "data" / "stations.json").read_text(encoding="utf-8")
    )
