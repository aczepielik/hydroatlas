"""IMGW file-archive ingest.

Historical daily hydrometeorological series live in per-year (2023+)
or per-month (1951-2022) zip archives under::

    https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/
        dane_hydrologiczne/dobowe/YYYY/codz_YYYY[_MM].zip

Each zip holds one headerless CSV. Three variants exist across eras:

* <=2022: CP1250, comma-delimited, quoted fields
* 2023:   UTF-8 (BOM), semicolon-delimited, unquoted
* 2024+:  CP1250, comma-delimited, whole line wrapped in one quoted
          field with ""-escaped quotes

Columns: id, name, river, year, month, day, stage_cm, discharge_m3s,
temp_c, code. Missing values appear as empty strings, 9999 (stage) or
99.9 (temp). The trailing code column redundantly encodes a
March-based month cycle and is ignored.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import httpx
import pandas as pd

BASE_URL = (
    "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne"
    "/dane_hydrologiczne/dobowe"
)
FIRST_YEAR = 1951
MISSING = 9999.0
# Bump when the trimmed frame schema changes so stale pickles are ignored.
CACHE_VERSION = 2


_COLS = [
    "id", "name", "river", "year", "month", "day",
    "stage_cm", "discharge_m3s", "temp_c", "code",
]


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[*_COLS, "date"])


def _decode(data: bytes) -> str:
    for encoding in ("cp1250", "utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _normalize_body(text: str) -> tuple[str, str]:
    """Return (csv_body, delimiter) for any known file era."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    first = text.split("\n", 1)[0]
    if first.count(";") > first.count(","):
        return text, ";"
    if re.match(r'^"[^"]*,', first):
        # 2024+: each line is one outer-quoted field with "" escapes.
        body = re.sub(r'^"(.*)"$', r"\1", text, flags=re.M)
        return body.replace('""', '"'), ","
    return text, ","


def parse_csv_bytes(data: bytes) -> pd.DataFrame:
    text = _decode(data)
    if not text.strip():
        return _empty_frame()
    body, delimiter = _normalize_body(text)
    df = pd.read_csv(
        io.StringIO(body),
        sep=delimiter,
        header=None,
        dtype=str,
        on_bad_lines="skip",
        engine="c",
    )
    if df.shape[1] < 8:
        return _empty_frame()
    if df.shape[1] < 10:
        for i in range(df.shape[1], 10):
            df[i] = pd.NA
    df = df.iloc[:, :10]
    df.columns = _COLS
    df["id"] = df["id"].str.strip()
    df["date"] = pd.to_datetime(
        dict(
            year=pd.to_numeric(df["year"], errors="coerce"),
            month=pd.to_numeric(df["month"], errors="coerce"),
            day=pd.to_numeric(df["day"], errors="coerce"),
        ),
        errors="coerce",
    )
    for col in ("stage_cm", "discharge_m3s"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] >= MISSING, col] = pd.NA
    return df.dropna(subset=["date"])


def _download(url: str, dest: Path, client: httpx.Client) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, follow_redirects=True) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
    tmp.rename(dest)
    return dest


def _candidate_urls(year: int) -> list[str]:
    urls = [f"{BASE_URL}/{year}/codz_{year}.zip"]
    urls += [f"{BASE_URL}/{year}/codz_{year}_{m:02d}.zip" for m in range(1, 13)]
    return urls


def _fetch_year_to_cache(year: int, cache: Path, client: httpx.Client) -> list[Path]:
    """Download whatever archives exist for a year; return local zip paths."""
    yearly = cache / f"codz_{year}.zip"
    if yearly.exists():
        return [yearly]
    monthly = sorted(cache.glob(f"codz_{year}_*.zip"))
    if len(monthly) == 12:
        return monthly
    # Try the yearly archive first, then the twelve monthly ones.
    paths: list[Path] = []
    for i, url in enumerate(_candidate_urls(year)):
        dest = cache / Path(url).name
        try:
            _download(url, dest, client)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            raise
        paths.append(dest)
        if i == 0 and paths:
            break  # yearly archive exists; no monthly files needed
    return [p for p in paths if p.exists()]


def iter_archives(years: list[int], cache: Path):
    """Yield local zip paths for the given years, downloading what is missing."""
    with httpx.Client(timeout=60.0, headers={"User-Agent": "hydroatlas-etl"}) as client:
        for year in sorted(set(years)):
            for zpath in _fetch_year_to_cache(year, cache, client):
                yield zpath


def iter_zip_frames(paths, cache_dir: Path | None = None):
    """Yield (zip_path, DataFrame) for every archive in paths.

    Columns are trimmed to id/date/stage_cm/discharge_m3s. When cache_dir is
    given, each zip's parsed frame is pickled there so the (slow) CSV parse
    happens exactly once per archive.
    """
    for zpath in paths:
        cache_file = None
        if cache_dir is not None:
            cache_file = cache_dir / f"{zpath.stem}.v{CACHE_VERSION}.pkl.gz"
            if cache_file.exists():
                yield zpath, pd.read_pickle(cache_file, compression="gzip")
                continue
        frames = []
        with zipfile.ZipFile(zpath) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    parsed = parse_csv_bytes(zf.read(name))
                    frames.append(
                        parsed[["id", "name", "river", "date", "stage_cm", "discharge_m3s"]]
                    )
        df = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=["id", "name", "river", "date", "stage_cm", "discharge_m3s"])
        )
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache_file, compression="gzip")
        yield zpath, df


def load_years(
    years: list[int],
    cache: Path,
    station_ids: set[str] | None = None,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """Download (cached) and parse the given years; optionally filter stations."""
    frames = []
    for _, df in iter_zip_frames(iter_archives(years, cache), cache_dir=cache_dir):
        if station_ids is not None:
            df = df[df["id"].isin(station_ids)]
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["id", "name", "river", "date", "stage_cm", "discharge_m3s"])
    return pd.concat(frames, ignore_index=True)
