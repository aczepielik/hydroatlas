# Atlas of Polish Rivers

## Intent

There are abundant sources for current hydrological measurements and forecasts.
But long-term trends are scattered and available only in specialized,
hard-to-find, often paper-only publications. This project builds a
reference source for them:

- **Stack**: statically generated site on GitHub Pages; Hugo for building the
  site; Python (uv) for ETL.
- **Design**: clean but purposefully boring. Just like paper magazines change
  constantly but reference sources can be safely reprinted without major
  changes, so should be this reference source.
- **Single station view**: mocked in `mocks/single.html`.
- **Index/menu**: topological simplification of the river network (like
  metro/public transit maps are topological simplifications of real networks).
  There are too many stations, placed too unevenly, to place them on a map —
  and a map alone won't show subsidiary relations.
- **Data**: IMGW (Instytut Meteorologii i Gospodarki Wodnej).
- **Scope v1**: only aggregates, as in `mocks/single.html`. Model-based
  analysis postponed to the future.

---

## Current state (2026-10)

**Done and working locally:**

- Full ETL pipeline (uv package in `etl/`) covering **all 1372 stations** found
  in the IMGW archive, 1951–2025.
- Aggregate computation with coverage gating → 1372 JSONs
  (1032 with discharge blocks, 306 stage-only, 34 too short for indicators).
- 7658 static SVG charts (matplotlib), rendered in ~13 min parallel.
- Coverage-adaptive Hugo station pages + all-station directory homepage +
  hand-tuned transit-map index of the curated Wisła-basin subset (16 stations).
- 15 pytest tests green; `hugo` builds 1375 pages.

**Not done / postponed:**

- **GitHub Pages deployment — postponed.** A ready-but-untested workflow is
  drafted in `.github/workflows/deploy.yml` (activate later).
- Only the initial commit exists; the working tree below is not yet committed.
- Curated station metadata (catchment area, regime, …) is `null` everywhere —
  see Issues.

## Repository layout

```
hydroatlas/
├── README.md               # this file
├── LICENSE
├── .gitignore              # raw data, venv, generated site artifacts
├── .github/workflows/
│   └── deploy.yml          # drafted: test + build + (postponed) Pages deploy
├── mocks/                  # design reference (mock single-station page)
│   ├── single.html         #   the page template the site ports
│   ├── styles1.css         #   stylesheet (copied to site/static/css/)
│   └── info.md             #   candidate indicators (future scope)
├── etl/                    # uv Python package — the whole pipeline
│   ├── pyproject.toml      #   deps: httpx, pandas, numpy, matplotlib, pyyaml, click
│   ├── refs/               # committed inputs of the registry step
│   │   ├── stations-meta.yaml    # curated 16-station map set + section-1 metadata
│   │   ├── stations-list.csv     # IMGW station list (CP1250)
│   │   ├── station-names.json    # name/river of every archived station
│   │   └── api-snapshot.json     # IMGW API snapshot (coords, voivodeship)
│   ├── layout/
│   │   └── river-topology.yaml   # hand-tuned transit-map node/edge layout
│   ├── src/hydroatlas/
│   │   ├── cli.py           # fetch | registry | aggregate | charts | stubs | map | build
│   │   ├── ingest/archive.py# IMGW zip download + 3-era CSV parser (+ parse cache)
│   │   ├── ingest/api.py    # API snapshot fetch
│   │   ├── clean.py         # daily reindex + gap report
│   │   ├── store.py         # csv.gz read/write
│   │   ├── registry.py      # stations.json builder (availability per variable)
│   │   ├── aggregates.py    # indicator math (methods documented in docstring)
│   │   ├── charts.py        # 6 matplotlib SVG charts
│   │   ├── topology.py      # transit-map SVG renderer
│   │   └── paths.py         # repo-root discovery
│   └── tests/               # parser fixtures (real archive samples) + 15 tests
├── data/
│   ├── raw/                 # GITIGNORED: archive zips, parse cache, API files
│   └── clean/               # COMMITTED: <station_id>.csv.gz daily series (82 MB)
└── site/                    # Hugo project
    ├── hugo.toml
    ├── content/stacje/      # generated stubs (gitignored)
    ├── data/
    │   ├── stations.json    # COMMITTED: registry output
    │   └── aggregates/      # generated per-station indicator JSON (gitignored)
    ├── layouts/             # baseof, adaptive stacje/single, homepage directory
    ├── static/
    │   ├── css/styles.css   # from mocks/styles1.css + adaptive additions
    │   ├── charts/<id>/     # generated SVGs, 405 MB (gitignored)
    │   └── img/index-map.svg# generated transit map (gitignored)
    └── public/              # hugo output (gitignored)
```

## Setup

Prerequisites: `git`, [`uv`](https://docs.astral.sh/uv/), Hugo **0.165.x**
extended (e.g. `snap install hugo`), network access to `danepubliczne.imgw.pl`
only for data refreshes.

```bash
cd etl
uv sync                 # create venv, install deps (Python 3.14 via uv)
uv run pytest           # 15 tests
```

## Running

Usual local loop (from `etl/`):

```bash
uv run hydroatlas build         # aggregates + content stubs + map + charts (~15 min)
cd .. && hugo server --source site   # http://localhost:1313
```

`build` regenerates everything gitignored from the committed `data/clean/` and
`etl/refs/` inputs; `hugo` alone only works after a build has run once.

Data refresh (network, ~20 min first time; later runs hit the parse cache):

```bash
uv run hydroatlas fetch --all-stations --to-year 2025   # -> data/clean/*.csv.gz
uv run hydroatlas registry --refresh                    # -> site/data/stations.json
```

Individual steps (what `build` runs): `aggregate`, `stubs`, `map`, `charts`.
`charts` is the slow one (~13 min on 8 cores) — the rest take seconds to minutes.

## Data lineage

```
IMGW danepubliczne.imgw.pl
├── dobowe/YYYY/codz_*.zip        daily series, monthly zips ≤2022 / yearly 2023+
│   (3 CSV eras: cp1250-comma-quoted / utf8-semicolon-2023 / outer-quoted-2024+)
├── api/data/hydro                current-station snapshot (coords, voivodeship)
└── lista_stacji_hydro.csv        station list (CP1250)
        │  fetch --all-stations / registry --refresh
        ▼
data/raw/  [gitignored]           archive zips + parsed-frames cache (.v2.pkl.gz)
etl/refs/  [committed]            stations-list, api-snapshot, station-names,
                                  stations-meta (curated)
        │  fetch
        ▼
data/clean/<id>.csv.gz [committed]        date, stage_cm, discharge_m3s (daily, gapped)
        │  registry
        ▼
site/data/stations.json [committed]       registry + availability {discharge, stage}
        │  aggregate / charts / stubs / map          (== `hydroatlas build`)
        ▼
site/data/aggregates/<id>.json   [gitignored]
site/static/charts/<id>/*.svg    [gitignored]
site/static/img/index-map.svg    [gitignored]
site/content/stacje/<id>.md      [gitignored]
        │  hugo --source site
        ▼
site/public/                     [gitignored]  ← GitHub Pages artifact (when enabled)
```

Committed inputs are small and deterministic; everything derived is
regenerated by `hydroatlas build` (locally or in CI).

## Issues and known gaps

1. **Data coverage is heterogeneous — this drove the format.** Of 1372
   stations: 1098 have some discharge (939 with ≥10 years), 1290 have stage,
   ~20% of modern-era stations are stage-only. Records start/stop and station
   ids churn (Warszawa's discharge is only 1951–1967 + 2013; Płock 1951–1970;
   Gdańska Głowa never published Q). Pages therefore adapt: Q sections, a stage
   fallback section, or a "too short for indicators" notice.
2. **Curated section-1 metadata is empty** (`etl/refs/stations-meta.yaml`,
   `metadata:` map): catchment area, regime, station class, unit runoff, … are
   `null` and hidden on the page. They must be filled from hydrological
   yearbooks/publications — never fabricated. Q/P stays `null` until
   catchment + precipitation are supplied.
3. **`charts` costs ~13 min** (7658 SVGs); a full CI build would run ~30 min.
   Acceptable for now; possible future optimizations: skip unchanged stations,
   fewer charts for short records.
4. **GitHub Pages is postponed** — `deploy.yml` exists but has never run.
5. **Flow matrix** only renders with ≥9 hydrological years of Q (else the
   stats table spans full width); gated indicators require ≥5 years of Q /
   ≥1 year of stage / ≥2 years for charts.
6. **"Min/Max dz" (mock label) interpretation**: we compute min/max of the
   day-of-year mean discharge series. The mock's exact intent is unconfirmed —
   revisit against literature.
7. **Transit map covers only the curated 16-station subset** and is hand-tuned
   YAML; scaling the map to all stations would need geo→schematic automation.
8. **Mock numbers ≠ site numbers**: `mocks/single.html` contains fabricated
   sample values; real pages show computed aggregates (e.g. Warszawa monthly
   means differ from the mock by design).

## Assumptions and decisions made

| Area | Decision |
| --- | --- |
| Scope | v1 = aggregates only, Polish only, no JS frameworks, static output ("boring"). |
| Format | **Coverage-adaptive pages** (user-ratified): sections light up from actual data; long-term goal = all ~1372 stations, map subset = curated 16. |
| Data source | File archives for history (API is snapshot-only, ~913 current stations); both ingested; archive parse cached with schema versioning. |
| Charts | matplotlib → static SVG; discharge series if ≥2 years, else stage series; missing charts degrade to placeholders. |
| Flow matrix | Rows = hydrological-year classes N/S/W (terciles of annual mean Q); cols = characteristic flows of the class's days: NQ=10th pctile (exceedance 90%), SQ=median, WQ=90th pctile — so NNQ = "najniższy z najniższych". |
| Hydrological year | Oct 1 → Sep 30 (year classes, annual means). |
| BFI | Eckhardt (2005), α=0.925, BFmax=0.8, clamped b≤Q; BFI = ΣBF/ΣQ. |
| Colwell P/C/M | 4 fixed flow states (zero / <NQ / NQ–WQ / ≥WQ) × 12 months, ln entropies, P=C+M; quantile binning rejected because it forces C→0. |
| Gating | Q blocks ≥1825 valid days; stage block ≥365; charts ≥730; `null` renders as "—"/omitted row. |
| Git strategy | Commit: `data/clean/`, `etl/refs/`, `site/data/stations.json`. Ignore: raw downloads, parse cache, and all derived site artifacts (regenerated by `build`). |
| Pair labels | Rise/Fall and High/Low render in that order (per mock); annual extremes min/max. |
| Numbers | Polish formatting: grouped thousands, decimal comma (template-side). |
