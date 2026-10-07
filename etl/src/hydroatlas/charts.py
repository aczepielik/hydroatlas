"""Static SVG charts for the station page (matplotlib, no JS).

Charts are generated per variant ({q,h}_{max,cal}) with the variant's
variable fixed — Q variants only ever plot discharge, H variants only
ever plot stage; there is no cross-variable fallback.  Q charts: raster
hydrograph, flow duration curve, Pardé coefficients, spectrogram, timing
of annual extremes, monthly boxplots.  H charts: the same set minus FDC
and Pardé (no standard stage analogue).  A variant whose series is
shorter than MIN_CHART_DAYS gets no charts; charts needing more structure
(timing, spectrogram) skip themselves when the data is too short.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

from .aggregates import variant_frames  # noqa: E402

MIN_CHART_DAYS = 2 * 365
LINE = "#1a4e8a"
ACCENT = "#64748b"
GRID = "#e2e8f0"

Q_CHARTS = ("raster", "fdc", "parde", "spectrogram", "timing", "boxplots")
H_CHARTS = ("raster", "spectrogram", "timing", "boxplots")

# Hydrological-year month starts (Oct 1 = day 1; non-leap positions,
# leap hydro years shift March..September by one day — fine for ticks).
HY_MONTH_STARTS = [1, 32, 62, 93, 124, 152, 183, 213, 244, 274, 305, 335]
HY_MONTH_LABELS = ["X", "XI", "XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"]


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


def _hy_dayofyear(index: pd.DatetimeIndex) -> np.ndarray:
    """Day within the hydrological year (Oct 1 = 1 .. Sep 30 = 365/366)."""
    start_years = np.where(index.month >= 10, index.year, index.year - 1)
    starts = pd.DatetimeIndex(pd.Index(start_years).astype(str) + "-10-01")
    return (index - starts).days.to_numpy() + 1


def chart_raster(df: pd.DataFrame, path: Path) -> None:
    hy = np.where(df.index.month >= 10, df.index.year + 1, df.index.year)
    doy = _hy_dayofyear(df.index)
    table = df["v"].groupby([hy, doy]).mean().unstack()
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad("#ffffff")
    arr = table.to_numpy(dtype=float)
    finite = arr[np.isfinite(arr)]
    positive = finite[finite > 0]
    norm = None
    masked = np.ma.masked_invalid(arr)
    if positive.size and positive.size >= 0.5 * finite.size:
        # Very skewed results: percentile-clipped log scale keeps the
        # low-flow structure visible instead of washing everything out.
        vmin = max(float(positive.min()), float(np.percentile(positive, 1)))
        vmax = float(np.percentile(positive, 99.5))
        if vmin < vmax:
            norm = LogNorm(vmin=vmin, vmax=vmax)
            masked = np.ma.masked_where(~np.isfinite(arr) | (arr <= 0), arr)
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    ax.grid(False)
    im = ax.imshow(
        masked,
        aspect="auto",
        extent=[1, 366, table.index.max() + 0.5, table.index.min() - 0.5],
        cmap=cmap,
        norm=norm,
        interpolation="nearest",
    )
    ax.set_xlabel("miesiąc roku hydrologicznego")
    ax.set_ylabel("rok hydrologiczny")
    ax.set_xticks(HY_MONTH_STARTS)
    ax.set_xticklabels(HY_MONTH_LABELS)
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
    ax.grid(False)
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
    hy = np.where(df.index.month >= 10, df.index.year + 1, df.index.year)
    doy = _hy_dayofyear(df.index)
    tmp = pd.DataFrame({"v": df["v"].to_numpy(), "hy": hy, "doy": doy})
    rows = []
    for year, grp in tmp.groupby("hy"):
        if len(grp) < 180:
            continue
        imax = grp["v"].idxmax()
        imin = grp["v"].idxmin()
        rows.append((year, int(grp.loc[imin, "doy"]), int(grp.loc[imax, "doy"])))
    if len(rows) < 2:
        return
    years = [r[0] for r in rows]
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    for year, dmin, dmax in rows:
        ax.annotate(
            "",
            xy=(dmax, year),
            xytext=(dmin, year),
            arrowprops=dict(arrowstyle="->", color=LINE, lw=1.1),
        )
    ax.scatter([r[1] for r in rows], years, s=14, color=ACCENT, marker="x", label="minimum")
    ax.scatter([r[2] for r in rows], years, s=14, color=LINE, label="maksimum")
    ax.set_xlim(1, 366)
    ax.set_xticks(HY_MONTH_STARTS)
    ax.set_xticklabels(HY_MONTH_LABELS)
    ax.set_xlabel("miesiąc roku hydrologicznego")
    ax.set_ylabel("rok hydrologiczny")
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
    """Render charts for every gate-passing variant; return written names.

    The variable is fixed per variant (Q variants -> discharge only,
    H variants -> stage only); charts are never substituted across
    variables.  Stale outputs (legacy flat files, removed variants) are
    cleaned up.
    """
    _style()
    frames = variant_frames(df)
    out_dir = dest_root / sid
    written = []

    # One-time cleanup: legacy flat layout + variants that stopped passing gates.
    if out_dir.exists():
        for stale in out_dir.glob("*.svg"):
            stale.unlink()
        for stale_dir in out_dir.iterdir():
            if stale_dir.is_dir() and stale_dir.name not in frames:
                shutil.rmtree(stale_dir)

    for name, wdf in frames.items():
        column = "discharge_m3s" if name[0] == "q" else "stage_cm"
        source = _clean(wdf["date"], wdf[column])
        if len(source) < MIN_CHART_DAYS:
            continue
        names = Q_CHARTS if name[0] == "q" else H_CHARTS
        for chart_name in names:
            path = out_dir / name / f"{chart_name}.svg"
            try:
                _CHARTS[chart_name](source, path)
            except Exception:
                if path.exists():
                    path.unlink()
                continue
            if path.exists():
                written.append(f"{name}/{chart_name}")
    return written
