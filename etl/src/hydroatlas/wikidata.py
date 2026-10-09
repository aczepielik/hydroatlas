"""Wikidata river-hierarchy snapshot (P403 mouth-of-waterbody).

Two layers, cleanly separated:

1. ``fetch_snapshot`` (network, ``wikidata --refresh``): SPARQL queries
   that collect, for every distinct river display name in
   ``site/data/stations.json``, the candidate Wikidata items carrying
   that label (restricted to watercourses), their mouth coordinates and
   the transitive P403 parent chain.  Candidate matching runs in two
   passes: exact ``@pl`` label equality (index-backed), then the
   ``wbsearchentities`` full-text search for names with no exact hit —
   useful for items labelled under a variant spelling.  Raw data only —
   committed to ``etl/refs/wikidata-p403.json``; afterwards the ETL is
   fully offline.

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
import re
import time
from pathlib import Path

import httpx

from .paths import refs_dir, site_dir

WISLA_QID = "Q548"
ODRA_QID = "Q552"
USER_AGENT = "hydroatlas/0.1 (hydrological reference site; contact via GitHub)"
SNAPSHOT_PATH = refs_dir() / "wikidata-p403.json"
OVERRIDES_PATH = refs_dir() / "wikidata-overrides.json"
CONNECTIONS_PATH = refs_dir() / "imgw-connections.json"

_NAME_CHUNK = 80   # VALUES entries per SPARQL query
_QID_CHUNK = 250
_MAX_CHAIN_ROUNDS = 12
# Graph components of one name closer than this are re-joined: the IMGW
# prev/next chain breaks at archival (null) stations and across odd
# cross-links, so gaps within one river must be healed.  Distinct rivers
# sharing a name sit far further apart than this.
_SAME_RIVER_KM = 50.0
# The two basin hubs are single rivers by definition (and the prev/next
# chain around Warszawa is broken by archival nulls — an 85 km gap no
# sane threshold bridges); never split them.
_SINGLE_RIVER_NAMES = {"Wisła", "Odra"}
# Watercourses with subclasses (Q355304): covers river, stream, canal …
# (the historic filter's Q3243762/Q11565 were typo'd QIDs — a films list
# and an asteroid — so only "river"-classed items ever matched).
_WATERCOURSE_VALUES = "wd:Q355304"
_SEARCH_LIMIT = 50


# ---------------------------------------------------------------- fetch

def _sparql(client: httpx.Client, query: str) -> list[dict]:
    """POST with retry/backoff — the query service rate-limits bursts."""
    last: Exception | None = None
    for attempt in range(6):
        try:
            response = client.post(
                "https://query.wikidata.org/sparql",
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                timeout=90,
            )
            if response.status_code in (429, 503):
                last = RuntimeError(f"HTTP {response.status_code}")
                time.sleep(5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()["results"]["bindings"]
        except httpx.HTTPError as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"SPARQL query failed after retries: {last}")


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"@pl'


def _coord_of(row: dict) -> list[float] | None:
    """WKT Point(longitude latitude) -> [lat, lon]."""
    if "coord" not in row:
        return None
    lon_s, lat_s = (
        row["coord"]["value"].replace("Point(", "").replace(")", "").split()
    )
    return [float(lat_s), float(lon_s)]


def _fetch_label_candidates(client: httpx.Client, names: list[str]) -> list[dict]:
    """All watercourse items carrying each name exactly (@pl), with mouth
    coordinates.  Literal label equality uses the service's label index —
    keep this query exact; the fuzzy fallback goes through the search API."""
    values = " ".join(_quote(n) for n in names)
    query = f"""
    SELECT ?item ?itemLabel ?coord ?name WHERE {{
      VALUES ?name {{ {values} }}
      ?item rdfs:label ?name.
      ?item wdt:P31/wdt:P279* ?cls.
      VALUES ?cls {{ {_WATERCOURSE_VALUES} }}
      OPTIONAL {{ ?item wdt:P625 ?coord. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,en". }}
    }}
    """
    return _sparql(client, query)


def _search_entities(client: httpx.Client, term: str) -> list[str]:
    """QIDs from the full-text search API for one term (pl, then en)."""
    for lang in ("pl", "en"):
        for attempt in range(4):
            try:
                r = client.get(
                    "https://www.wikidata.org/w/api.php",
                    params={
                        "action": "wbsearchentities",
                        "search": term,
                        "language": lang,
                        "uselang": "pl",
                        "type": "item",
                        "limit": _SEARCH_LIMIT,
                        "format": "json",
                    },
                )
                if r.status_code in (429, 503):
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                hits = r.json().get("search", [])
                if hits:
                    return [h["id"] for h in hits]
                break  # no hits for this language; try the next one
            except httpx.HTTPError:
                time.sleep(3 * (attempt + 1))
    return []


def _fetch_items(client: httpx.Client, qids: list[str]) -> list[dict]:
    """Watercourse filter + mouth coordinates for known QIDs (index-backed)."""
    values = " ".join(f"wd:{q}" for q in qids)
    query = f"""
    SELECT ?item ?itemLabel ?coord WHERE {{
      VALUES ?item {{ {values} }}
      ?item wdt:P31/wdt:P279* ?cls.
      VALUES ?cls {{ {_WATERCOURSE_VALUES} }}
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

    def ingest(name: str, row: dict) -> None:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        label = row["itemLabel"]["value"]
        labels[qid] = label
        bucket = candidates.setdefault(name, [])
        if any(c["qid"] == qid for c in bucket):
            return  # same item, different coord row
        bucket.append({"qid": qid, "label": label, "coord": _coord_of(row)})

    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=90, follow_redirects=True
    ) as client:
        # Pass 1: label equality (any language, case-insensitive).
        for i in range(0, len(names), _NAME_CHUNK):
            chunk = names[i : i + _NAME_CHUNK]
            for row in _fetch_label_candidates(client, chunk):
                ingest(row["name"]["value"], row)
            time.sleep(0.3)

        # Pass 2: full-text search for names with no exact @pl hit —
        # wbsearchentities finds variant labels; one batched SPARQL then
        # keeps only watercourses and supplies mouth coordinates.
        missing = [n for n in names if not candidates.get(n)]
        wanted: dict[str, list[str]] = {}
        for n in missing:
            term = re.sub(r"\s*\([^)]*\)", "", n).strip() or n
            found = _search_entities(client, term)
            if found:
                wanted[n] = found
            time.sleep(0.3)
        by_qid: dict[str, list[str]] = {}
        for n, qids in wanted.items():
            for q in qids:
                by_qid.setdefault(q, []).append(n)
        qids = sorted(by_qid)
        for i in range(0, len(qids), _NAME_CHUNK):
            for row in _fetch_items(client, qids[i : i + _NAME_CHUNK]):
                qid = row["item"]["value"].rsplit("/", 1)[-1]
                for n in by_qid.get(qid, []):
                    ingest(n, row)
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


def _load_connections() -> dict:
    """IMGW prev/next graph (refs/imgw-connections.json); {} when absent."""
    if CONNECTIONS_PATH.is_file():
        return json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
    return {}


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


def _coords_of(part: list[dict]) -> list[tuple[float, float]]:
    return [(s["lat"], s["lon"]) for s in part if s.get("lat") and s.get("lon")]


def _merge_close_parts(parts: list[list[dict]], d_km: float) -> list[list[dict]]:
    """Single-linkage re-join of same-name graph fragments whose nearest
    stations are closer than ``d_km`` (heals gaps in the prev/next chain).
    Components without coordinates cannot prove separation and join."""
    merged: list[list[dict] | None] = [list(p) for p in parts]
    coords: list[list[tuple[float, float]] | None] = [_coords_of(p) for p in merged]

    for i in range(len(merged)):
        if merged[i] is None:
            continue
        for j in range(i + 1, len(merged)):
            if merged[j] is None:
                continue
            a, b = coords[i], coords[j]
            close = (
                not a
                or not b
                or any(_dist_km(x, y) < d_km for x in a for y in b)
            )
            if close:
                merged[i].extend(merged[j])
                coords[i] = (coords[i] or []) + (coords[j] or [])
                merged[j] = None
                coords[j] = None
    return [p for p in merged if p]


def resolve(snapshot: dict, stations: list[dict]) -> dict:
    """Pure offline resolution: river groups, parents and basins.

    Stations are bucketed by display name, then split where the IMGW
    prev/next-station graph (refs/imgw-connections.json) proves they belong
    to distinct rivers — the exact topology beats name matching, so two
    different "Wilga" rivers never merge.  Groups are internally keyed by
    a unique ``gid`` (``name`` / ``name#2`` …); ``g["name"]`` stays the
    display name and ``g["parent"]`` holds the parent's gid.  The
    canonical river_key per group is its most-used key.
    """
    overrides = _load_overrides()
    labels = dict(snapshot.get("labels", {}))
    edges = dict(snapshot.get("edges", {}))
    # Manual P403 fixes for statements Wikidata lacks (rare).
    edges.update(overrides.get("edges", {}))
    candidates = snapshot.get("candidates", {})
    conn = _load_connections()

    # --- adjacency from the IMGW prev/next graph -------------------------
    # The graph follows the *flow*: after a confluence the downstream
    # neighbour belongs to the receiving river, so connectivity is judged
    # per display name: walk from a station through stations of the SAME
    # name and through nodes absent from the registry (unknown, so the
    # chain may hop across registry gaps), while any registry station of
    # another river blocks the walk.  Result per bucket: components of
    # provably-connected stations, "lonely" (links exist but none reach
    # the name), and "unlinked" (no API info at all).
    st_by_id = {s["id"]: s for s in stations}
    adj: dict[str, set[str]] = {}
    for sid, rec in conn.items():
        for nb in (rec.get("up"), rec.get("down")):
            if nb:
                adj.setdefault(sid, set()).add(nb)
                adj.setdefault(nb, set()).add(sid)

    # --- bucket stations by display name ---------------------------------
    buckets: dict[str, list[dict]] = {}
    for st in stations:
        key = st["river_key"]
        if key.lower().startswith("jez"):
            continue  # lakes are not rivers; they have their own index
        name = st["river"]
        if not name.strip(" -"):
            continue  # IMGW placeholder rows ("-" / empty river name)
        buckets.setdefault(name, []).append(st)

    groups: dict[str, dict] = {}
    centroid_acc: dict[str, list[float]] = {}

    for name, sts in buckets.items():
        same = {s["id"] for s in sts}
        comp_of: dict[str, int] = {}
        for start in same:
            if start in comp_of:
                continue
            label = len(set(comp_of.values()))
            stack = [start]
            comp_of[start] = label
            seen_unknown: set[str] = set()
            while stack:
                node = stack.pop()
                for nb in adj.get(node, ()):
                    if nb in same:
                        if nb not in comp_of:
                            comp_of[nb] = label
                            stack.append(nb)
                    elif nb not in st_by_id and nb not in seen_unknown:
                        # unknown node: hop through, but do not label it
                        seen_unknown.add(nb)
                        stack.append(nb)
        # NOTE: unknown nodes are traversed but never labelled, so two
        # same-name stations are in the same component exactly when a
        # path of same-name + unknown nodes links them.

        comps: dict[int, list[dict]] = {}
        unlinked: list[dict] = []
        for st in sts:
            sid = st["id"]
            if sid not in adj:
                unlinked.append(st)       # no API info at all
            else:
                # A component of one station whose links never reach its
                # own name ("lonely") stays a component of its own here —
                # proven separate — and the merge step below rejoins it
                # only if it sits within _SAME_RIVER_KM of another part.
                comps.setdefault(comp_of[sid], []).append(st)
        # Re-join fragments the chain could not connect (archival gaps),
        # then park no-information stations with the largest part.
        if name in _SINGLE_RIVER_NAMES:
            parts = [sts]
        else:
            parts = _merge_close_parts(list(comps.values()), _SAME_RIVER_KM)
        if not parts:
            parts = [unlinked]
        elif unlinked and name not in _SINGLE_RIVER_NAMES:
            biggest = max(parts, key=len)
            biggest.extend(unlinked)
        # Deterministic order: largest first, then by smallest station id.
        parts.sort(key=lambda p: (-len(p), min(s["id"] for s in p)))

        for i, part in enumerate(parts):
            gid = name if i == 0 else f"{name}#{i + 1}"
            g = {
                "gid": gid, "name": name, "keys": set(),
                "stations": [s["id"] for s in part],
                "qid": None, "parent_qid": None, "parent": None,
                "basin": "unknown",
            }
            groups[gid] = g
            counts: dict[str, int] = {}
            for s in part:
                g["keys"].add(s["river_key"])
                counts[s["river_key"]] = counts.get(s["river_key"], 0) + 1
                if s.get("lat") and s.get("lon"):
                    acc = centroid_acc.setdefault(gid, [0.0, 0.0, 0])
                    acc[0] += s["lat"]
                    acc[1] += s["lon"]
                    acc[2] += 1
            # canonical river_key: most stations in the group, tie -> lex
            g["key"] = max(sorted(counts), key=lambda k: (counts[k], k))
            del g["keys"]

    centroids = {
        gid: [c[0] / c[2], c[1] / c[2]] for gid, c in centroid_acc.items() if c[2]
    }

    for gid, g in groups.items():
        g["qid"] = pick_qid(
            g["name"], candidates.get(g["name"], []), centroids.get(gid), overrides
        )

    # One group per QID: two same-named groups (or alias names) may pick
    # the same item — keep the claim with the most stations, drop the
    # rest to unresolved (honest) instead of merging fake topology.
    claims: dict[str, list[str]] = {}
    for gid, g in groups.items():
        if g["qid"]:
            claims.setdefault(g["qid"], []).append(gid)
    for owners in claims.values():
        if len(owners) < 2:
            continue
        keeper = sorted(owners, key=lambda n: (-len(groups[n]["stations"]), n))[0]
        for n in owners:
            if n != keeper:
                groups[n]["qid"] = None

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

    # Parent group: the nearest ancestor (following P403 upward through
    # waterbodies absent from the dataset) that is itself a group.
    # Values are gids, not display names (a name may have split).
    qid_to_group = {
        g["qid"]: gid for gid, g in groups.items() if g["qid"] is not None
    }
    for gid, g in groups.items():
        parent_qid = edges.get(g["qid"]) if g["qid"] else None
        # Climb past intermediate waterbodies that are not in the dataset
        # (unmonitored brooks) until we reach a river we can link to.
        seen = {g["qid"]}
        while parent_qid and parent_qid not in qid_to_group and parent_qid not in seen:
            seen.add(parent_qid)
            parent_qid = edges.get(parent_qid)
        g["parent_qid"] = parent_qid
        parent_gid = qid_to_group.get(parent_qid)
        if parent_gid and parent_gid != gid:
            g["parent"] = parent_gid

    # Children index (by gid).
    for g in groups.values():
        g["children"] = []
    for g in groups.values():
        if g["parent"]:
            groups[g["parent"]]["children"].append(g["gid"])
    for g in groups.values():
        g["children"].sort(key=lambda c: (-len(groups[c]["stations"]), groups[c]["name"]))

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
