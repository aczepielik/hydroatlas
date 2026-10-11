# Atlas of Polish Rivers

## Intent

There are abundant sources for current hydrological measurements and forecasts.
But long-term trends are scattered and available only in specialized,
hard-to-find, often paper-only publications. This project builds a
reference source for them:

- **Stack**: statically generated site; Hugo for building the site;
  Python (uv) for ETL. Publishing is manual — the GitHub Actions workflow
  was removed (see Issues).
- **Design**: clean but purposefully boring. Just like paper magazines change
  constantly but reference sources can be safely reprinted without major
  changes, so should be this reference source.
- **Single station view**: mocked in `mocks/single.html`.
- **Index/menu**: topological simplification of the river network (like
  metro/public transit maps are topological simplifications of real networks).
  There are too many stations, placed too unevenly, to place them on a map —
  and a map alone won't show subsidiary relations. Every river gets its own
  schematic page under `/rzeki/` (Wikidata-derived hierarchy), top-level
  hubs: Wisła, Odra, Rzeki Przymorza.
- **Data**: IMGW (Instytut Meteorologii i Gospodarki Wodnej).
- **Scope v1**: only aggregates, as in `mocks/single.html`. Model-based
  analysis postponed to the future.

---

## Current state (2026-10)

**Done and working locally:**

- Full ETL pipeline (uv package in `etl/`) covering **all 1372 stations** found
  in the IMGW archive, 1951–2025.
- Aggregate computation with coverage gating → 1372 JSONs with up to four
  **variants per station** (`q_max`, `q_cal`, `h_max`, `h_cal`): 1032 stations
  with discharge blocks, 306 stage-only, 34 too short for indicators.
  `max` = full record; `cal` = official 1991–2020 window (≥20 hydrological
  years of ≥300 valid days each). Curated `breaks` dates split `max` into
  segment variants `q_max_1…n` / `h_max_1…n` (none set yet). Q variants
  carry only discharge-derived content, H variants only stage-derived
  content — never a mix.
- 19 280 static SVG charts on disk (matplotlib), rendered per variant
  (~45–50 min parallel; 104 stations have too little data for any chart).
  Q variants draw from a 7-type set (raster/FDC/Pardé/spectrogram/timing/
  boxplots/annual trend), H variants from a 4-type set (same minus FDC/
  Pardé/annual); every Q variant with ≥3 complete years has an
  `annual.svg`.
- Coverage-adaptive Hugo station pages (top-right Q/H version menu,
  coordinates, kilometer, Leaflet marker, both Q and H availability rows) +
  separate station index (`/indeks/`) and lake index (`/jeziora/`).
- 553 per-river schematic pages under `/rzeki/` with basin hubs
  (Wisła, Odra, Rzeki Przymorza) — hierarchy from a committed Wikidata
  P403 snapshot (`etl/refs/wikidata-p403.json`), fully offline afterwards.
- 40 pytest tests green; `hugo` builds 1948 pages.

**Not done / postponed:**

- **No CI.** `.github/workflows/deploy.yml` was removed; tests, build and
  publishing happen locally (`uv run pytest`, `uv run hydroatlas build`,
  `hugo --source site`).
- Curated station metadata (catchment area, regime, …) is `null` everywhere —
  see Issues.

## Repository layout

```
hydroatlas/
├── README.md               # this file
├── LICENSE
├── .gitignore              # raw data, venv, generated site artifacts
├── mocks/                  # design reference (mock single-station page)
│   ├── single.html         #   the page template the site ports
│   ├── styles1.css         #   stylesheet (copied to site/static/css/)
│   └── info.md             #   candidate indicators (future scope)
├── etl/                    # uv Python package — the whole pipeline
│   ├── pyproject.toml      #   deps: httpx, pandas, numpy, matplotlib, pyyaml, click
│   ├── refs/               # committed inputs of the registry / Wikidata steps
│   │   ├── stations-meta.yaml    # curated metadata + section-1 metadata
│   │   ├── stations-list.csv     # IMGW station list (CP1250)
│   │   ├── station-names.json    # name/river of every archived station
│   │   ├── api-snapshot.json     # IMGW API snapshot (coords, voivodeship)
│   │   ├── wikidata-p403.json    # Wikidata river-hierarchy snapshot
│   │   └── wikidata-overrides.json # manual QID/edge fixes
│   ├── src/hydroatlas/
│   │   ├── cli.py           # fetch | registry | aggregate | connections | wikidata | rivers | charts | stubs | build
│   │   ├── ingest/archive.py# IMGW zip download + 3-era CSV parser (+ parse cache)
│   │   ├── ingest/api.py    # API snapshot fetch
│   │   ├── clean.py         # daily reindex + gap report
│   │   ├── store.py         # csv.gz read/write
│   │   ├── registry.py      # stations.json builder (availability per variable)
│   │   ├── aggregates.py    # indicator math + variant gating (docstring)
│   │   ├── charts.py        # per-variant matplotlib SVG charts
│   │   ├── wikidata.py      # Wikidata P403 snapshot fetch + offline resolve
│   │   ├── rivermap.py      # per-river schematic SVG renderer
│   │   └── paths.py         # repo-root discovery
│   └── tests/               # parser fixtures (real archive samples) + 40 tests
├── data/
│   ├── raw/                 # GITIGNORED: archive zips, parse cache, API files
│   └── clean/               # COMMITTED: <station_id>.csv.gz daily series (82 MB)
└── site/                    # Hugo project
    ├── hugo.toml
    ├── content/
    │   ├── stacje/          # generated stubs (gitignored)
    │   ├── rzeki/           # generated river-page stubs (gitignored)
    │   ├── indeks.md        # station index page
    │   └── jeziora.md       # lake index page
    ├── data/
    │   ├── stations.json    # COMMITTED: registry output
    │   ├── rivers.json      # generated river groups (gitignored)
    │   └── aggregates/      # generated per-variant indicator JSON (gitignored)
    ├── layouts/             # baseof, adaptive stacje/rzeki, index pages
    ├── static/
    │   ├── css/styles.css   # from mocks/styles1.css + adaptive additions
    │   ├── js/version-menu.js # station Q/H variant switcher
    │   ├── charts/<id>/     # generated per-variant SVGs (gitignored)
    │   └── rzeki/*.svg      # generated river schematics (gitignored)
    └── public/              # hugo output (gitignored)
```

## Setup

Prerequisites: `git`, [`uv`](https://docs.astral.sh/uv/), Hugo **0.165.x**
extended (e.g. `snap install hugo`), network access to `danepubliczne.imgw.pl`
only for data refreshes and to `query.wikidata.org` only for
`wikidata --refresh`.

```bash
cd etl
uv sync                 # create venv, install deps (Python 3.14 via uv)
uv run pytest           # 40 tests
```

## Running

Usual local loop (from `etl/`):

```bash
uv run hydroatlas build         # aggregates + stubs + connections + wikidata + rivers + charts (~1 h)
cd .. && hugo server --source site   # http://localhost:1313
```

`build` regenerates everything gitignored from the committed `data/clean/`,
`etl/refs/` and `site/data/stations.json` inputs; `hugo` alone only works
after a build has run once. `wikidata` inside `build` reads the committed
snapshot (offline) — run `uv run hydroatlas wikidata --refresh` (network) to
update it.

Data refresh (network, ~20 min first time; later runs hit the parse cache):

```bash
uv run hydroatlas fetch --all-stations --to-year 2025   # -> data/clean/*.csv.gz
uv run hydroatlas registry --refresh                    # -> site/data/stations.json
```

Individual steps (what `build` runs): `aggregate`, `stubs`, `connections`,
`wikidata`, `rivers`, `charts`. `charts` is the slow one (~45–50 min on 8
cores with 2–4 variants per station) — the rest take seconds to minutes.

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
                                  stations-meta (curated),
                                  imgw-connections (station graph),
                                  wikidata-p403 + overrides (hierarchy snapshot)
        │  fetch
        ▼
data/clean/<id>.csv.gz [committed]        date, stage_cm, discharge_m3s (daily, gapped)
        │  registry
        ▼
site/data/stations.json [committed]       registry + availability {discharge, stage}
        │  aggregate / stubs / connections / wikidata / rivers / charts  (== `hydroatlas build`)
        ▼
site/data/aggregates/<id>.json   [gitignored]   variants: q_max/q_cal/h_max/h_cal
                                  (+ q_max_1…n / h_max_1…n when curated `breaks`
                                  are set); per variant: flow_matrix {cells, period},
                                  annual {rows, trend}, stats, monthly, dynamics
site/data/rivers.json            [gitignored]   river groups, basins, parents
site/content/rzeki/<key>.md      [gitignored]
site/static/charts/<id>/*.svg    [gitignored]   per-variant chart dirs
site/static/rzeki/<key>.svg      [gitignored]
site/content/stacje/<id>.md      [gitignored]
        │  hugo --source site
        ▼
site/public/                     [gitignored]   publish manually
```

Committed inputs are small and deterministic; everything derived is
regenerated by `hydroatlas build`.

## Issues and known gaps

1. **Data coverage is heterogeneous — this drove the format.** Of 1372
   stations: 1098 have some discharge (939 with ≥10 years), 1290 have stage,
   ~20% of modern-era stations are stage-only. Records start/stop and station
   ids churn (Warszawa's discharge is only 1951–1967 + 2013; Płock 1951–1970;
   Gdańska Głowa never published Q). Pages therefore adapt: up to four
   Q/H × max/cal variant views, or a "too short for indicators" notice.
2. **Curated station metadata is empty** (`etl/refs/stations-meta.yaml`,
   `metadata:` map): catchment area, regime, station class, unit runoff, … are
   `null` and hidden on the page. They must be filled from hydrological
   yearbooks/publications — never fabricated. Q/P stays `null` until
   catchment + precipitation are supplied. The schema plumbing for the new
   fields is in place — `pz_m_nap` (P.z.), `uwagi`, `breaks` — but **all
   values await transcription**: there is no P.z./uwagi source in the repo
   (authoritative: *Rocznik Hydrologiczny — wykaz stacji*, P.z. col. 8), and
   `catchment_km2` has no usable auto-source either: Wikidata P2053 is the
   **whole-river** area, meaningful only near mouths, so it cannot fill
   per-station gauge catchments.
3. **`charts` costs ~45–50 min** (19 280 SVGs, 2–4 variants per station).
   Possible future optimizations: skip unchanged stations, cache per window.
4. **No CI / no automated publishing** — the workflow was removed; build and
   deploy manually. Reintroduce only when the pipeline is worth its minutes.
5. **Flow matrix** renders only with ≥20 hydrological years having ≥300 valid
   days of Q each (art. 270 ust. 6 defines *wielolecie*) and then prints its
   reference period under the matrix; below that floor the matrix is omitted
   and the stats table renders alone (it no longer depends on the matrix).
   Other gates: Q blocks ≥1825 valid days, stage blocks ≥365 days of stage,
   charts ≥730 days, `cal` variants ≥20 hydrological years with ≥300 valid
   days each in the 1991–2020 window.
6. **"Min/Max dz" (mock label) — resolved.** The row is now labelled
   `Min/Max śr. dobowej`, defined as the min/max of the calendar month-day
   climatological daily-mean series (29 Feb dropped), and lives in the stats
   table — not in the IHA section, of which it is not a part. (The mock's
   original intent was never confirmed; kept here as history.)
7. **Wikidata hierarchy coverage**: 415 of 554 river names matched;
   151 remain in "Bez przypisania" (139 absent from Wikidata, 13 ambiguous
   with no station coordinates to disambiguate). Add entries to
   `etl/refs/wikidata-overrides.json` to pin them.
8. **Mock numbers ≠ site numbers**: `mocks/single.html` contains fabricated
   sample values; real pages show computed aggregates (e.g. Warszawa monthly
   means differ from the mock by design).
9. **Ice phenomena are a known source limitation** — not an implementation
   bug. The archive CSVs (`dobowe/**/codz_*`) carry id/name/river/date/
   stage/discharge/temp/code — **no ice column** — so ice cannot be ingested
   from this source at all; a different source would be needed (ice matters
   because flows in ice periods require reduction coefficients).
10. **Explicitly deferred**: duration flows (10…364 d), half-year
    characteristic values (`półrocze`: XI–IV winter / V–X summer), water
    temperature (parsed at ingest, then discarded — `data/clean/` keeps only
    stage + discharge), and search/sort/filter on `/indeks/` (1372 static
    rows).

## Assumptions and decisions made

| Area | Decision |
| --- | --- |
| Scope | v1 = aggregates only, Polish only, no JS frameworks, static output ("boring"). Small vanilla JS only for the station variant menu and Leaflet. |
| Format | **Coverage-adaptive pages** (user-ratified): sections light up from actual data; long-term goal = all ~1372 stations. |
| Q/H separation | Strict: a Q view shows only discharge-derived content (matrix, BFI/Colwell, IHA, FDC, Pardé, …), an H view only stage-derived content (monthly stages, stage extremes, stage charts). No cross-variable fallback anywhere. |
| Data source | File archives for history (API is snapshot-only, ~913 current stations); both ingested; archive parse cached with schema versioning. |
| Charts | matplotlib → static SVG, per variant: Q variants plot discharge (raster/FDC/Pardé/spectrogram/timing/boxplots/annual trend — 7 types), H variants plot stage (raster/spectrogram/timing/boxplots — 4). Color scale on the raster is log when data is non-negative and skewed. |
| Flow matrix | Two-stage definition: first order = yearly min/mean/max/median of daily Q (per hydrological year, ≥300 valid days); second order = min/mean/max/median of those yearly values → **4×4 = 16 cells** `NNQ…WWQ`, with ZQ (przepływ zwyczajny = mediana) at **both** orders (NNQ = minimum of minima, SNQ = average of minima, WWQ = maximum of maxima). Needs ≥20 years × ≥300 valid days (art. 270 ust. 6); JSON = `{cells, period}` and the period is printed under the matrix. |
| Hydrological year | Nov 1 → Oct 31 (Rocznik XI–X); all year axes, chart month axes, cal window follow it. |
| Calibration period | `cal` variants clip to hydrological years 1991–2020 (= 1990-11-01…2020-10-31), requiring ≥20 hydrological years with ≥300 valid days each. |
| River hierarchy | Wikidata P403 (mouth-of-waterbody) snapshot committed under `etl/refs/`; offline resolution picks one QID per name (overrides > unique > nearest-mouth), merges duplicate display names, classifies basins (wisla / odra / przymorza / unknown). |
| BFI | Eckhardt (2005), α=0.925, BFmax=0.8, clamped b≤Q; BFI = ΣBF/ΣQ. The recursion is seeded per contiguous run of calendar days (gaps don't collapse into one step). Lag-1 autocorrelation pairs consecutive *valid observations* — a documented approximation. |
| Colwell P/C/M | 5 fixed flow states × 12 months: 0 (bez przepływu) / ≤Q95 / ≤Qśr / ≤Q10 / >Q10; class **definitions** fixed, threshold **values** computed per variant (the variant period is the reference period); ln entropies, s = ln 5; P = C + M with C, M rounded first. Pure-quantile binning rejected — it re-pins C. |
| Gating | Q blocks ≥1825 valid days; stage block ≥365; charts ≥730; `null` renders as "—"/omitted row. |
| Git strategy | Commit: `data/clean/`, `etl/refs/`, `site/data/stations.json`. Ignore: raw downloads, parse cache, and all derived site artifacts (regenerated by `build`). |
| Pair labels | Rise/Fall and High/Low render in that order (per mock); annual extremes min/max. |
| Numbers | Polish formatting: grouped thousands, decimal comma (template-side). |
| Annual series & trend | `annual.rows` = one row per hydrological year with ≥1 valid day (`{hy, mean, min, max, n}`, partial years shown — that's what `n` is for); trend computed on **complete** years only (≥300 valid days), needs ≥10 of them: Theil–Sen slope/intercept + Mann–Kendall z/p, numpy/stdlib only (**no scipy**). |
| Q95 / Q90 / SNQ | Low-flow readouts use *prawdopodobieństwo nieosiągnięcia* — `q95_nieosiag`/`q90_nieosiag` = 5 %/10 % quantiles of daily Q (the FDC axis stays exceedance, titles spell out the equivalence). SNQ is never recomputed: it is matrix cell **SNQ** (mean of yearly NQ over the printed ≥20-year period). |
| Curated metadata plumbing | `stations-meta.yaml` accepts `pz_m_nap` (m NAP, 0.01 m), `uwagi` (list) and `breaks` (ISO dates; the date = **first day** of the new segment). Registry exports `pz_m_nap`/`uwagi` into `stations.json`; `breaks` splits the pooled `max` window into `q_max_1…n` / `h_max_1…n` segments (cal window spans breaks — documented limitation). All values stay null/empty until curated: P.z. has no in-repo source (Rocznik transcription), `uwagi` likewise. |
| Chart axes | Raster and timing put the **older hydrological years at the bottom** (ascending, oldest below); spectrogram x-axis = hydrological years with ticks at each 1 Nov; boxplots show outliers. |
| Typography | Roboto is loaded via a Google Fonts `<link>` — one third-party request, the site's only privacy tradeoff; the zero-network alternative is simply dropping Roboto from the font stack. |
| River links | The station profile links `Rzeka` → `/rzeki/<key>/` through a **name→key map built from `rivers.json`** (rivermap rewrites keys with hub remaps/`-2` dedup, but display names are unique, so names are the safe join key). River hubs list subsidiary rivers with a `(dopływ …)` suffix. |
| Deployment | `baseURL = https://aczepielik.github.io/hydroatlas/` (GitHub Pages); publishing stays manual (see Issues). |
| Page ordering | Monthly tables render in **XI–X** order (the underlying aggregates stay calendar-ordered); station-index headers read `Q (okres)` / `H (okres)` (a range, not a year count). |
