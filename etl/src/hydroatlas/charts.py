"""Static SVG charts for the station page (matplotlib, no JS).

Six charts per station, matching the figcaptions of mocks/single.html:
raster hydrograph, flow duration curve, Pardé coefficients, spectrogram,
timing of annual extremes, monthly boxplots.

The plotted variable is discharge when >= MIN_CHART_DAYS valid days exist,
otherwise stage (stage-only stations get stage versions of the same six).
Charts needing more structure (timing, spectrogram) simply skip when the
data is too short and the template shows a placeholder instead.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

MIN_CHART_DAYS = 2 * 365
LINE = "#1a4e8a"
ACCENT = "#64748b"
GRID = "#e2e8f0"

CHART_NAMES = ("raster", "fdc", "parde", "spectrogram", "timing", "boxplots")


def _style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8,
            "font.family": "sans-serif",
            "axes.edgecolor": GRID,
            "axes.labelcolor": ACCENT,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.5,
            "xtick.color": ACCENT,
            "ytick.color": ACCENT,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 100,
            "savefig.bbox": "tight",
        }
    )


def _save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg")
    plt.close(fig)


def _clean(dates: pd.Series, values: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"date": dates, "v": values}).dropna()
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def chart_raster(df: pd.DataFrame, path: Path) -> None:
    table = df["v"].groupby([df.index.year, df.index.dayofyear]).mean().unstack()
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad("#ffffff")
    arr = table.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    im = ax.imshow(
        np.ma.masked_invalid(arr),
        aspect="auto",
        extent=[1, 366, table.index.max() + 0.5, table.index.min() - 0.5],
        cmap=cmap,
        interpolation="nearest",
    )
    ax.set_xlabel("dzień roku")
    ax.set_ylabel("rok")
    month_starts = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
    ax.set_xticks(month_starts)
    ax.set_xticklabels(["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"])
    fig.colorbar(im, ax=ax, pad=0.02)
    _save(fig, path)


def chart_fdc(df: pd.DataFrame, path: Path) -> None:
    values = np.sort(df["v"].to_numpy())[::-1]
    exceedance = 100.0 * np.arange(1, len(values) + 1) / len(values)
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.plot(exceedance, values, color=LINE, linewidth=1.2)
    ax.set_xlabel("prawdopodobieństwo przekroczenia [%]")
    ax.set_ylabel("przepływ")
    _save(fig, path)


def chart_parde(df: pd.DataFrame, path: Path) -> None:
    monthly = df["v"].groupby(df.index.month).mean()
    ratio = monthly / df["v"].mean()
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.bar(range(1, 13), ratio.reindex(range(1, 13)), color=LINE, width=0.7)
    ax.axhline(1.0, color=ACCENT, linewidth=0.8, linestyle="--")
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"])
    ax.set_ylabel("Q / Qśr")
    _save(fig, path)


def chart_spectrogram(df: pd.DataFrame, path: Path) -> None:
    series = df["v"].copy()
    # Fill interior gaps so the STFT sees an evenly spaced series (visual only).
    full = series.asfreq("D")
    series = full.interpolate(limit_direction="both").dropna()
    series = series - series.mean()
    if len(series) < 3 * 365:
        return
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.specgram(
        series.to_numpy(),
        NFFT=365,
        Fs=1.0,
        noverlap=300,
        cmap="magma",
        scale="dB",
    )
    ax.set_ylim(1.0 / 400, 1.0 / 4)
    ax.set_yscale("log")
    ax.set_ylabel("okres [dni]")
    ax.set_yticks([1 / 365, 1 / 90, 1 / 30, 1 / 7])
    ax.set_yticklabels(["365", "90", "30", "7"])
    ax.set_xlabel("czas [dni od początku serii]")
    _save(fig, path)


def chart_timing(df: pd.DataFrame, path: Path) -> None:
    rows = []
    for year, grp in df.groupby(df.index.year):
        if len(grp) < 180:
            continue
        rows.append((year, grp["v"].idxmax().dayofyear, grp["v"].idxmin().dayofyear))
    if len(rows) < 2:
        return
    years = [r[0] for r in rows]
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.scatter([r[1] for r in rows], years, s=14, color=LINE, label="maksimum")
    ax.scatter([r[2] for r in rows], years, s=14, color=ACCENT, marker="x", label="minimum")
    ax.set_xlim(1, 366)
    month_starts = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
    ax.set_xticks(month_starts)
    ax.set_xticklabels(["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"])
    ax.set_ylabel("rok")
    ax.legend(frameon=False, fontsize=7)
    _save(fig, path)


def chart_boxplots(df: pd.DataFrame, path: Path) -> None:
    groups = [df.loc[df.index.month == m, "v"].to_numpy() for m in range(1, 13)]
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.boxplot(
        [g for g in groups if len(g)],
        tick_labels=["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"],
        showfliers=False,
        widths=0.6,
    )
    ax.set_ylabel("wartość")
    _save(fig, path)


_CHARTS = {
    "raster": chart_raster,
    "fdc": chart_fdc,
    "parde": chart_parde,
    "spectrogram": chart_spectrogram,
    "timing": chart_timing,
    "boxplots": chart_boxplots,
}


def generate_station(df: pd.DataFrame, sid: str, dest_root: Path) -> list[str]:
    """Render all supported charts for one station; return written names."""
    _style()
    q = _clean(df["date"], df["discharge_m3s"])
    stage = _clean(df["date"], df["stage_cm"])
    source = q if len(q) >= MIN_CHART_DAYS else (stage if len(stage) >= MIN_CHART_DAYS else None)
    if source is None:
        return []
    written = []
    out_dir = dest_root / sid
    for name, fn in _CHARTS.items():
        path = out_dir / f"{name}.svg"
        try:
            fn(source, path)
        except Exception:
            if path.exists():
                path.unlink()
            continue
        if path.exists():
            written.append(name)
    return written
