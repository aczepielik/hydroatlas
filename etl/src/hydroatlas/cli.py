"""Command-line interface for the HydroAtlas ETL pipeline."""

from __future__ import annotations

import json

import click


@click.group()
def main() -> None:
    """HydroAtlas ETL: fetch, clean, aggregate and render IMGW hydrological data."""


@main.command()
@click.option("--station", "stations", multiple=True, help="Station id (repeatable).")
@click.option("--all-stations", "all_stations", is_flag=True,
              help="Fetch every station found in the archive (default: curated set).")
@click.option("--from-year", "year_from", type=int, default=1951, show_default=True)
@click.option("--to-year", "year_to", type=int, default=None, help="Default: current year.")
@click.option("--refresh", is_flag=True, help="Re-download archives even if cached.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress per-station gap reports.")
def fetch(
    stations: tuple[str, ...],
    all_stations: bool,
    year_from: int,
    year_to: int | None,
    refresh: bool,
    quiet: bool,
) -> None:
    """Download IMGW archives and write cleaned daily series to data/clean/."""
    from datetime import date

    import pandas as pd

    from .clean import clean_station, gap_report
    from .ingest.archive import iter_archives, iter_zip_frames, load_years
    from .paths import clean_dir, raw_dir, refs_dir
    from .registry import curated_ids
    from .store import write_clean

    parse_cache = raw_dir() / "parsed"

    if stations:
        ids: set[str] | None = set(stations)
    elif all_stations:
        ids = None
    else:
        ids = set(curated_ids())
    year_to = year_to or date.today().year
    cache = raw_dir() / "archive"
    if refresh:
        for year in range(year_from, year_to + 1):
            for pattern in (f"codz_{year}.zip", f"codz_{year}_*.zip"):
                for path in cache.glob(pattern):
                    path.unlink()

    years = range(year_from, year_to + 1)
    dest_dir = clean_dir()

    if ids is None:
        frames = []
        for i, (_, frame) in enumerate(
            iter_zip_frames(iter_archives(years, cache), cache_dir=parse_cache), 1
        ):
            frames.append(frame)
            if i % 100 == 0:
                click.echo(f"  read {i} archives...")
        click.echo(f"  {len(frames)} archives read; aggregating")
        big = pd.concat(frames, ignore_index=True)
        del frames
        names: dict[str, list[str]] = {}
        no_q = []
        n_written = 0
        for sid, group in big.groupby("id", sort=False):
            if sid not in names:
                names[sid] = [str(group["name"].iloc[0]), str(group["river"].iloc[0])]
            clean = clean_station(group, sid)
            if clean.empty:
                continue
            write_clean(clean, dest_dir / f"{sid}.csv.gz")
            n_written += 1
            if clean["discharge_m3s"].notna().sum() == 0:
                no_q.append(sid)
        names_path = refs_dir() / "station-names.json"
        names_path.write_text(
            json.dumps(names, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
        )
        click.echo(
            f"{n_written} stations written "
            f"({year_from}..{year_to}); {len(no_q)} without any discharge; "
            f"names -> {names_path.name}"
        )
        return

    df = load_years(years, cache, station_ids=ids, cache_dir=parse_cache)
    for sid in sorted(ids):
        clean = clean_station(df, sid)
        if clean.empty:
            click.echo(f"{sid}: no rows found in {year_from}..{year_to}")
            continue
        write_clean(clean, dest_dir / f"{sid}.csv.gz")
        if not quiet:
            click.echo(gap_report(clean, label=sid))


@main.command("registry")
@click.option("--refresh", is_flag=True, help="Re-download IMGW refs (API snapshot, station list).")
def registry_cmd(refresh: bool) -> None:
    """Write site/data/stations.json from refs, API snapshot and clean data."""
    from .ingest.api import fetch_snapshot, load_snapshot
    from .paths import refs_dir, site_dir
    from .registry import build_registry, download_station_list, write_registry

    snapshot_path = refs_dir() / "api-snapshot.json"
    list_path = refs_dir() / "stations-list.csv"
    if refresh or not snapshot_path.exists():
        rows = fetch_snapshot(snapshot_path)
        click.echo(f"snapshot: {len(rows)} active stations")
    if refresh or not list_path.exists():
        download_station_list(list_path)
        click.echo("station list downloaded")
    snapshot = load_snapshot(snapshot_path)
    stations = build_registry(snapshot)
    dest = site_dir() / "data" / "stations.json"
    write_registry(stations, dest)
    click.echo(f"wrote {dest} ({len(stations)} stations)")


@main.command()
@click.option("--station", "stations", multiple=True, help="Station id (repeatable). Default: all.")
def aggregate(stations: tuple[str, ...]) -> None:
    """Compute site/data/aggregates/<id>.json from data/clean/."""
    from datetime import datetime, timezone

    from .aggregates import compute
    from .paths import clean_dir, site_dir
    from .registry import load_curated
    from .store import read_clean

    meta_all = load_curated().get("metadata") or {}
    dest_dir = site_dir() / "data" / "aggregates"
    dest_dir.mkdir(parents=True, exist_ok=True)

    paths = (
        [clean_dir() / f"{sid}.csv.gz" for sid in stations]
        if stations
        else sorted(clean_dir().glob("*.csv.gz"))
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for path in paths:
        sid = path.name.removesuffix(".csv.gz")
        df = read_clean(path)
        result = compute(df, meta_all.get(sid))
        result["station_id"] = sid
        result["generated"] = now
        (dest_dir / f"{sid}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        n += 1
    click.echo(f"wrote {n} aggregate files to {dest_dir}")


def _chart_job(job: tuple[str, str, str]):
    from .charts import generate_station
    from .paths import clean_dir
    from .store import read_clean

    sid, _, dest = job
    df = read_clean(clean_dir() / f"{sid}.csv.gz")
    return sid, generate_station(df, sid, __import__("pathlib").Path(dest))


@main.command()
@click.option("--station", "stations", multiple=True, help="Station id (repeatable). Default: all.")
@click.option("--workers", type=int, default=None, help="Parallel workers (default: CPU count).")
def charts(stations: tuple[str, ...], workers: int | None) -> None:
    """Render static SVG charts to site/static/charts/."""
    import multiprocessing as mp

    from .paths import clean_dir, site_dir

    dest = site_dir() / "static" / "charts"
    paths = (
        [clean_dir() / f"{sid}.csv.gz" for sid in stations]
        if stations
        else sorted(clean_dir().glob("*.csv.gz"))
    )
    jobs = [(p.name.removesuffix(".csv.gz"), str(p), str(dest)) for p in paths]
    total = 0
    empty = 0
    with mp.Pool(workers or mp.cpu_count()) as pool:
        for i, (sid, written) in enumerate(pool.imap_unordered(_chart_job, jobs, chunksize=4), 1):
            total += len(written)
            empty += 1 if not written else 0
            if i % 200 == 0:
                click.echo(f"  {i}/{len(jobs)} stations...")
    click.echo(f"rendered {total} charts for {len(jobs)} stations ({empty} without charts)")


@main.command("stubs")
def stubs_cmd() -> None:
    """Write site/content/stacje/<id>.md front-matter stubs from stations.json."""
    from .paths import site_dir

    stations = json.loads(
        (site_dir() / "data" / "stations.json").read_text(encoding="utf-8")
    )
    content = site_dir() / "content" / "stacje"
    content.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    for s in stations:
        sid = s["id"]
        name = s.get("name") or sid
        river = s.get("river") or ""
        keep.add(f"{sid}.md")
        (content / f"{sid}.md").write_text(
            f'---\ntitle: "{river} – {name}"\n'
            f'river: "{river}"\nname: "{name}"\n---\n',
            encoding="utf-8",
        )
    for path in content.glob("*.md"):
        if path.name not in keep:
            path.unlink()
    click.echo(f"wrote {len(keep)} content stubs")


@main.command()
@click.option("--refresh", is_flag=True,
              help="Re-fetch the Wikidata P403 snapshot (network) and rewrite refs.")
def wikidata(refresh: bool) -> None:
    """Fetch (with --refresh) or load the committed Wikidata hierarchy snapshot."""
    from .wikidata import SNAPSHOT_PATH, fetch_snapshot, load_snapshot, load_stations, write_snapshot

    if refresh:
        names = sorted({s["river"] for s in load_stations()
                        if not s["river_key"].lower().startswith("jez")})
        click.echo(f"fetching {len(names)} river names from Wikidata...")
        snap = fetch_snapshot(names)
        path = write_snapshot(snap)
        click.echo(f"wrote {path} ({len(snap['candidates'])} names, "
                   f"{len(snap['edges'])} edges) — commit it")
    else:
        snap = load_snapshot()
        click.echo(f"loaded {SNAPSHOT_PATH.name} "
                   f"({len(snap.get('candidates', {}))} names, fetched "
                   f"{snap.get('fetched', '?')})")


@main.command()
def rivers() -> None:
    """Render per-river SVG pages, content stubs and site/data/rivers.json."""
    from .rivermap import command_rivers

    n, unresolved = command_rivers()
    click.echo(f"wrote {n} river pages to site/content/rzeki + site/static/rzeki")
    if unresolved:
        click.echo(f"{len(unresolved)} unresolved (see wikidata-overrides.json): "
                   + ", ".join(unresolved[:10])
                   + (" …" if len(unresolved) > 10 else ""))


@main.command()
@click.pass_context
def build(ctx: click.Context) -> None:
    """Run the full site build: aggregates, stubs, wikidata, rivers, charts."""
    ctx.invoke(aggregate)
    ctx.invoke(stubs_cmd)
    ctx.invoke(wikidata)          # offline: errors if the snapshot is missing
    ctx.invoke(rivers)
    ctx.invoke(charts)