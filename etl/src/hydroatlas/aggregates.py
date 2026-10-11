"""Per-station aggregate indicators for the coverage-adaptive station page.

Every block mirrors a section of mocks/single.html. A block is computed only
when the underlying data supports it; otherwise its values are ``null`` and the
template omits or dashes the row.

Method choices (documented per the plan — literature-anchored, boring):

Hydrological year
    Nov 1 (rok hydrologiczny XI–X, per Rocznik) .. Oct 31. Used for year
    classes, annual means, pulse/reversal rates.

Variants (strict Q/H separation)
    Each station yields up to four variants: q_max, q_cal, h_max, h_cal.
    "max" = full record; "cal" = official 1991..2020 calibration window
    (hydrological-year labels), clipped to available data.  When the
    curated metadata carries ``breaks`` (first day of a new segment,
    e.g. after hydro-engineering works), the pooled "max" window is
    REPLACED by segment windows max_1..max_{n+1} (variants q_max_1 ...);
    cal spans the breaks (documented limitation).  A q_* variant
    exists only when the window slice has >= MIN_Q_DAYS valid discharge
    days (plus >= MIN_CAL_YEARS usable hydrological years with >=
    MIN_YEAR_DAYS valid days for cal);
    h_* the same with MIN_STAGE_DAYS.  q_* variants carry only
    discharge-derived blocks; h_* only stage-derived blocks — never a mix.

Flow matrix (przepływy charakterystyczne, two-stage definition)
    First order (internal, per hydrological year with >= MIN_YEAR_DAYS
    valid days): NQ = yearly minimum, SQ = yearly mean, ZQ = yearly
    median (przepływ zwyczajny), WQ = yearly maximum of daily Q.
    Second order (the displayed 4x4 = 16 cells, per IMGW-PIB monograph
    §B.2.1): aggregate the yearly values across years — rows N/S/Z/W =
    min / mean / median / max over years, cols N/S/Z/W = min / mean /
    median / max within the year.  Hence NNQ = minimum of minima,
    NSQ = minimum of averages, NZQ = minimum of medians, …,
    SNQ = average of minima (the low-flow mean), …,
    WWQ = maximum of maxima.  Requires >= MIN_MATRIX_YEARS (20 years —
    Prawo wodne art. 270 ust. 6 defines wielolecie; each year with
    >= MIN_YEAR_DAYS valid days), else null.  Returns
    {"cells": {16 keys}, "period": {start, end, years}} — the period is
    the printed reference period of the matrix.

BFI
    Eckhardt (2005) recursive digital filter, alpha=0.925, BFmax=0.8
    (conservative parameter set for porous aquifers); BFI = sum(BF)/sum(Q).
    The recursion is seeded per run of consecutive calendar days, so
    calendar gaps do not collapse into a single time step.

Lag-1 autocorrelation / CV
    Pearson autocorrelation of daily Q; CV = std(ddof=1)/mean of daily Q.
    Lag-1 pairs consecutive valid observations (calendar gaps ignored —
    documented approximation, negligible per review).

Flashiness
    Richards-Baker index: sum(|dQ|)/sum(Q) over consecutive-day pairs.

Q/P
    Runoff ratio: R = meanQ * 31557.6 / catchment_km2 (mm/y), divided by
    annual precipitation; only when the curated metadata provides both
    catchment_km2 and annual_precip_mm — otherwise null.

Colwell P/C/M
    Contingency table of 5 fixed flow states x 12 months over the whole
    record: 0 = zero flow (bez przepływu, Q == 0), 1 = (0, Q95],
    2 = (Q95, mean], 3 = (mean, Q10], 4 = (Q10, ∞), where Q95/Q10 are the
    5%/90% quantiles of daily Q (non-exceedance probabilities 95%/10%)
    and mean is the record mean.  The class *definitions* are fixed;
    threshold *values* are computed per variant — the variant period is
    the reference period (no shared fixed period exists), so classes are
    comparable by definition, not by absolute thresholds.  Priority:
    zero first (np.select order).  Documented edge cases (no special
    casing): constant Q > 0 → every day in class 1 → C = 1, M = 0;
    Q95 = 0 (intermittent record) → class 1 empty; mean ≤ Q95 → class 2
    empty.  Natural-log entropies, s = ln 5 (COLWELL_STATES), per
    Colwell (1974):
    P = 1 - H(X|Y)/ln(s), C = 1 - H(X)/ln(s), M = (H(X)+H(Y)-H(XY))/ln(s),
    with X = state, Y = month: P = C + M holds exactly with C, M rounded
    first and P derived from the rounded pair.

Annual series and trend
    ``annual.rows``: one row per hydrological year with >= 1 valid day —
    {hy, mean, min, max, n} sorted by hy; partial years are included (n
    shows how thin a year is).  ``annual.trend``: null unless there are
    >= 10 complete years (n >= MIN_YEAR_DAYS); then Theil–Sen slope and
    intercept on the complete years' mean series plus Mann–Kendall z/p
    (both numpy/stdlib only — see theil_sen / mann_kendall).

Low-flow readouts (stats)
    q95_nieosiag / q90_nieosiag = 5% / 10% quantiles of daily Q, in the
    *prawdopodobieństwo nieosiągnięcia* convention (Q95 = not exceeded
    with 95 % probability); SNQ is not computed here — it is matrix cell
    SNQ (average of yearly minima over the printed >= 20-year period).

Annual extremes
    Global min/max of rolling N-day means (N = 1, 3, 7, 30, 90) on daily Q;
    zero-flow days = count of days with Q == 0.

Dynamics
    min_daily/max_daily = min/max of the mean discharge series grouped by
    calendar month and day (Feb 29 excluded); rise/fall = mean positive/negative consecutive-day
    change; pulses use IHA-style thresholds Q75 (high) / Q25 (low), counted
    per hydrological year and averaged, durations averaged over all runs;
    reversals = mean annual count of daily-flow direction changes (zero
    changes ignored); baseflow_ratio = share of days with Q > 0 where
    baseflow/Q >= 0.9 (Eckhardt).

Stage block
    Monthly mean stage, min/max stage, CV, and the 1/3/7/30/90-day
    rolling-mean min/max extremes of stage — the H-only counterpart of
    the Q annual extremes.
"""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

MIN_Q_DAYS = 5 * 365
MIN_STAGE_DAYS = 365
CAL_YEARS = (1991, 2020)
MIN_CAL_YEARS = 20  # usable hydrological years (>= MIN_YEAR_DAYS valid days)
MIN_YEAR_DAYS = 300  # usable hydrological year (valid days)
MIN_MATRIX_YEARS = 20  # Prawo wodne art. 270 ust. 6 (wielolecie)
ECKHARDT_ALPHA = 0.925
ECKHARDT_BFMAX = 0.8
COLWELL_STATES = 5  # zero / ≤Q95 / ≤mean / ≤Q10 / >Q10


def _round(value, digits=2):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(float(value), digits)


def _q_digits(v: float):
    r = round(float(v), 0 if abs(v) >= 10 else 2)
    return int(r) if r == int(r) and abs(v) >= 10 else r


def hydro_year(ts: pd.Series) -> pd.Series:
    """Hydrological-year label for each date (year of Nov 1 .. Oct 31)."""
    return np.where(ts.dt.month >= 11, ts.dt.year + 1, ts.dt.year)


def eckhardt_baseflow(q: np.ndarray) -> np.ndarray:
    """Eckhardt (2005) two-parameter filter; b_t is clamped to [0, y_t]."""
    bf = np.empty_like(q, dtype=float)
    bf[0] = q[0]
    for i in range(1, len(q)):
        b = (
            (1.0 - ECKHARDT_BFMAX) * ECKHARDT_ALPHA * bf[i - 1]
            + (1.0 - ECKHARDT_ALPHA) * ECKHARDT_BFMAX * q[i]
        ) / (1.0 - ECKHARDT_ALPHA * ECKHARDT_BFMAX)
        bf[i] = min(max(b, 0.0), q[i])
    return bf


def eckhardt_grid(dates: pd.Series, q: pd.Series) -> np.ndarray:
    """Gap-aware Eckhardt baseflow over a (possibly gappy) daily series.

    Splits the stripped series into runs of consecutive calendar days and
    runs ``eckhardt_baseflow`` independently on each run (re-seeding the
    recursion at every segment start), concatenating results in date order.
    """
    seg = dates.diff().dt.days.ne(1).cumsum()
    return np.concatenate(
        [eckhardt_baseflow(vals.to_numpy()) for _, vals in q.groupby(seg)]
    )


def _contingency(q: np.ndarray, months: np.ndarray) -> np.ndarray:
    """Flow states (fixed class definitions, see module docstring) x 12 months counts."""
    q95, q10 = np.quantile(q, [0.05, 0.90])  # prawd. nieosiągnięcia 95% / 10%
    mean = q.mean()
    states = np.select(
        [q == 0, q <= q95, q <= mean, q <= q10], [0, 1, 2, 3], default=4
    )
    table = np.zeros((COLWELL_STATES, 12))
    for st, mo in zip(states, months):
        table[int(st), int(mo) - 1] += 1
    return table


def colwell(q: np.ndarray, months: np.ndarray) -> dict | None:
    table = _contingency(q, months)
    total = table.sum()
    if total == 0:
        return None
    p_ij = table[table > 0] / total
    h_xy = float(-(p_ij * np.log(p_ij)).sum())
    p_x = table.sum(axis=1)
    p_x = p_x[p_x > 0] / total
    h_x = float(-(p_x * np.log(p_x)).sum())
    p_y = table.sum(axis=0)
    p_y = p_y[p_y > 0] / total
    h_y = float(-(p_y * np.log(p_y)).sum())
    ln_s = math.log(COLWELL_STATES)
    c = 1.0 - h_x / ln_s
    m = (h_x + h_y - h_xy) / ln_s
    # Round C and M first, then derive P from the rounded pair so the
    # displayed identity P = C + M holds exactly at 2 decimals.
    c, m = _round(c), _round(m)
    return {"p": _round(c + m), "c": c, "m": m}


def theil_sen(x, y) -> tuple[float, float]:
    """Theil–Sen robust linear fit (numpy only).

    Slope = median of the pairwise slopes (y_j - y_i)/(x_j - x_i) over
    all i < j (x = hydro years, must be unique and ascending);
    intercept = median of y_i - slope * x_i.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    i, j = np.triu_indices(len(x), k=1)
    slope = float(np.median((y[j] - y[i]) / (x[j] - x[i])))
    intercept = float(np.median(y - slope * x))
    return slope, intercept


def mann_kendall(y) -> dict:
    """Mann–Kendall rank trend test (numpy/stdlib only, no scipy).

    S = sum of sign(y_j - y_i) over i < j; variance is tie-corrected
    ([n(n-1)(2n+5) - Σ t(t-1)(2t+5)]/18 over tied value groups);
    z carries the ±1 continuity correction (0 when S == 0);
    p = erfc(|z|/√2) two-sided.  var == 0 (all values tied) → z = 0, p = 1.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    i, j = np.triu_indices(n, k=1)
    s = int(np.sign(y[j] - y[i]).sum())
    _, counts = np.unique(y, return_counts=True)
    tie_term = sum(int(t) * (int(t) - 1) * (2 * int(t) + 5) for t in counts if t > 1)
    var = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var == 0:
        return {"z": 0.0, "p": 1.0}
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"z": float(z), "p": float(p)}


def flow_matrix(q: pd.Series, hy_year: pd.Series) -> dict | None:
    """Two-stage characteristic flows (see module docstring)."""
    df = pd.DataFrame({"q": q, "hy": hy_year})
    yearly = (
        df.dropna()
        .groupby("hy")["q"]
        .agg(n="count", nq="min", sq="mean", zq="median", wq="max")
    )
    yearly = yearly[yearly["n"] >= MIN_YEAR_DAYS]
    if len(yearly) < MIN_MATRIX_YEARS:
        return None
    cells = {}
    for row, agg in (
        ("N", np.nanmin),
        ("S", np.nanmean),
        ("Z", np.nanmedian),
        ("W", np.nanmax),
    ):
        for col in ("nq", "sq", "zq", "wq"):
            cells[f"{row}{col[0].upper()}Q"] = _q_digits(agg(yearly[col]))
    return {
        "cells": cells,
        "period": {
            "start": int(yearly.index.min()),
            "end": int(yearly.index.max()),
            "years": int(len(yearly)),
        },
    }


def _pulse_stats(q: pd.Series, dates: pd.Series, hy: pd.Series) -> dict:
    valid = q.notna()
    qq = q[valid]
    dates = dates[valid]
    hy = hy[valid]
    high_thr = qq.quantile(0.75)
    low_thr = qq.quantile(0.25)
    above = (qq > high_thr).to_numpy()
    below = (qq < low_thr).to_numpy()
    consecutive = dates.diff().dt.days.fillna(2).to_numpy() == 1

    def runs(mask: np.ndarray) -> list[tuple[int, int]]:
        out = []
        start = None
        for i, flag in enumerate(mask):
            if flag:
                if start is None or not consecutive[i]:
                    if start is not None:
                        out.append((start, i - 1))
                    start = i
            elif start is not None:
                out.append((start, i - 1))
                start = None
        if start is not None:
            out.append((start, len(mask) - 1))
        return out

    high_runs, low_runs = runs(above), runs(below)
    n_years = max(int(hy.nunique()), 1)

    def per_year(run_list):
        return round(len(run_list) / n_years, 1)

    def mean_dur(run_list):
        if not run_list:
            return 0.0
        return round(float(np.mean([e - s + 1 for s, e in run_list])), 1)

    return {
        "high_pulses": per_year(high_runs),
        "low_pulses": per_year(low_runs),
        "high_dur": mean_dur(high_runs),
        "low_dur": mean_dur(low_runs),
    }


def _period(dates: pd.Series, valid: pd.Series) -> dict:
    return {
        "start": int(dates.min().year),
        "end": int(dates.max().year),
        "n_valid_days": int(valid.sum()),
        "coverage": _round(valid.sum() / len(dates), 3),
    }


def variant_frames(df: pd.DataFrame, breaks=()) -> dict[str, pd.DataFrame]:
    """Windowed frames for every gate-passing variant.

    Single source of truth for variant gating: aggregates and charts must
    agree on which variants exist for a station.

    Windows are ``max`` (full record) and ``cal`` (hydrological years
    1991..2020).  Each window ``w`` yields variants ``q_w`` / ``h_w``:
    max segments need >= MIN_Q_DAYS valid discharge resp. >= MIN_STAGE_DAYS
    valid stage days; ``cal`` additionally needs >= MIN_CAL_YEARS usable
    hydrological years (>= MIN_YEAR_DAYS each).

    ``breaks`` (ISO date strings; date = FIRST day of the new segment,
    from ``metadata.breaks`` in stations-meta.yaml): parsed leniently
    (invalid entries ignored), de-duplicated and sorted, keeping only
    dates strictly after the record start and not later than the record
    end.  If at least one valid break remains, the pooled ``max`` window
    is replaced by segments ``max_1 .. max_{n+1}``
    (``[start, b0), [b0, b1), ..., [bn, end]``) — so the variants are
    ``q_max_1 ...`` / ``h_max_1 ...`` and there is NO pooled ``q_max`` /
    ``h_max`` fallback; empty segments are skipped and segments below
    the MIN_Q_DAYS / MIN_STAGE_DAYS gate simply produce no variant.
    The ``cal`` window always spans the breaks (documented limitation:
    cal indicators are computed over the whole calibration window).
    No valid break → keys stay ``q_max`` / ``h_max`` as before.
    Assumption: <= 9 segments per station (variant keys sort as
    strings, so 10+ would order lexicographically wrong).
    """
    if df.empty:
        return {}
    hy = pd.Series(hydro_year(df["date"]), index=df.index)

    record_min = df["date"].min().date()
    record_max = df["date"].max().date()
    cuts: set = set()
    for raw in breaks or ():
        try:
            ts = pd.Timestamp(raw)
            if pd.isna(ts):
                continue
            cuts.add(ts.date())
        except (ValueError, TypeError, OverflowError):
            continue
    cuts = sorted(x for x in cuts if record_min < x <= record_max)

    if cuts:
        edges = [record_min, *cuts, record_max + timedelta(days=1)]
        windows: dict[str, pd.DataFrame] = {}
        for i, (start, end) in enumerate(zip(edges, edges[1:]), 1):
            seg = df[(df["date"].dt.date >= start) & (df["date"].dt.date < end)]
            if not seg.empty:
                windows[f"max_{i}"] = seg
    else:
        windows = {"max": df}
    cal = df[(hy >= CAL_YEARS[0]) & (hy <= CAL_YEARS[1])]
    if not cal.empty:
        windows["cal"] = cal

    frames: dict[str, pd.DataFrame] = {}
    for win, wdf in windows.items():
        why = hy.loc[wdf.index]
        q_valid = wdf["discharge_m3s"].notna()
        stage_valid = wdf["stage_cm"].notna()
        # The 20-year calibration gate applies to the cal window only;
        # max and max segments need MIN_Q_DAYS / MIN_STAGE_DAYS alone.
        if int(q_valid.sum()) >= MIN_Q_DAYS and (
            win != "cal"
            or int((why[q_valid].value_counts() >= MIN_YEAR_DAYS).sum()) >= MIN_CAL_YEARS
        ):
            frames[f"q_{win}"] = wdf
        if int(stage_valid.sum()) >= MIN_STAGE_DAYS and (
            win != "cal"
            or int((why[stage_valid].value_counts() >= MIN_YEAR_DAYS).sum()) >= MIN_CAL_YEARS
        ):
            frames[f"h_{win}"] = wdf
    return frames


def compute(df: pd.DataFrame, meta: dict | None = None) -> dict:
    """Compute the station aggregate JSON (see module docstring for variants)."""
    meta = meta or {}
    result: dict = {"station_id": None, "generated": None, "variants": {}}

    for name, wdf in variant_frames(df, meta.get("breaks") or ()).items():
        wdates = wdf["date"]
        why = pd.Series(hydro_year(wdates), index=wdf.index)
        if name[0] == "q":
            valid = wdf["discharge_m3s"].notna()
            result["variants"][name] = {
                "period": _period(wdates, valid),
                **_q_blocks(wdf["discharge_m3s"], wdates, why, valid, meta),
            }
        else:
            valid = wdf["stage_cm"].notna()
            result["variants"][name] = {
                "period": _period(wdates, valid),
                "stage": _stage_block(wdf["stage_cm"], wdates),
            }
    return result


def _q_blocks(q, dates, hy, q_valid, meta) -> dict:
    qq = q[q_valid]
    ddates = dates[q_valid]
    dhy = hy[q_valid]
    months = ddates.dt.month.to_numpy()

    matrix = flow_matrix(qq, dhy)

    # Annual series: every hydrological year with >= 1 valid day — partial
    # years are included (that is what the n column is for) — sorted by hy.
    # The trend uses raw means of complete years (n >= MIN_YEAR_DAYS) only.
    annual_rows = []
    complete_xy: list[tuple[int, float]] = []
    for hy_val, vals in qq.groupby(dhy):
        mean_raw = float(vals.mean())
        annual_rows.append(
            {
                "hy": int(hy_val),
                "mean": _q_digits(mean_raw),
                "min": _q_digits(vals.min()),
                "max": _q_digits(vals.max()),
                "n": int(vals.size),
            }
        )
        if vals.size >= MIN_YEAR_DAYS:
            complete_xy.append((int(hy_val), mean_raw))
    trend = None
    if len(complete_xy) >= 10:
        slope, intercept = theil_sen(
            [xy[0] for xy in complete_xy], [xy[1] for xy in complete_xy]
        )
        mk = mann_kendall([xy[1] for xy in complete_xy])
        trend = {
            "slope": _round(slope, 3),
            "intercept": _round(intercept, 1),
            "mk_z": _round(mk["z"], 2),
            "mk_p": _round(mk["p"], 3),
            "n_years": len(complete_xy),
        }
    annual = {"rows": annual_rows, "trend": trend}

    baseflow = eckhardt_grid(ddates, qq)
    bfi = float(baseflow.sum() / qq.sum()) if qq.sum() > 0 else None

    # Lag-1 autocorrelation pairs consecutive *valid observations*: calendar
    # gaps are ignored (documented approximation — negligible per review).
    # Eckhardt, by contrast, is gap-aware via eckhardt_grid.
    lag1 = qq.autocorr(lag=1)
    # Flashiness numerator uses consecutive-day pairs only; denominator is
    # total discharge over the whole valid record (documented approximation).
    delta = qq.diff()
    valid_delta = delta[delta.notna() & (ddates.diff().dt.days == 1)]
    flashiness = float(valid_delta.abs().sum() / qq.sum()) if qq.sum() > 0 else None
    cv = float(qq.std(ddof=1) / qq.mean()) if qq.mean() else None

    qp = None
    catchment = meta.get("catchment_km2")
    precip = meta.get("annual_precip_mm")
    if catchment and precip and qq.mean():
        runoff_mm = float(qq.mean()) * 31557.6 / catchment
        qp = _round(runoff_mm / precip)

    monthly = (
        q[q_valid].groupby(dates[q_valid].dt.month).mean().reindex(range(1, 13))
    )
    monthly_means = [
        _q_digits(v) if pd.notna(v) else None for v in monthly.tolist()
    ]

    # Rolling on the full daily-grid column: windows containing a calendar
    # gap yield NaN and drop out, so each window spans exactly n *days*
    # (n == 1 is equivalent to the stripped series).
    rolling = {n: q.rolling(n, min_periods=n).mean().dropna() for n in (1, 3, 7, 30, 90)}
    extremes = {}
    for n in (1, 3, 7, 30, 90):
        series = rolling[n]
        extremes[f"min{n}"] = _q_digits(series.min())
        extremes[f"max{n}"] = _q_digits(series.max())
    extremes["zero_flow_days"] = int((qq == 0).sum())

    rise = valid_delta[valid_delta > 0].mean()
    fall = valid_delta[valid_delta < 0].mean()

    # Group by (month, day) — day-of-year misaligns after February in leap
    # years; Feb 29 is excluded from the climatology.
    clim = qq.groupby([ddates.dt.month, ddates.dt.day]).mean()
    clim = clim.drop(index=(2, 29), errors="ignore")

    pulses = _pulse_stats(qq, ddates, dhy)

    signs = np.sign(valid_delta.to_numpy())
    signs = signs[signs != 0]
    reversals = int((signs[1:] * signs[:-1] < 0).sum())
    n_years = max(dhy.nunique(), 1)

    q_arr = qq.to_numpy()
    flowing = q_arr > 0
    bf_share = (
        float((baseflow[flowing] >= 0.9 * q_arr[flowing]).mean())
        if flowing.any()
        else None
    )

    stats = {
        "bfi": _round(bfi),
        "lag1": _round(lag1) if pd.notna(lag1) else None,
        "flashiness": _round(flashiness),
        "cv": _round(cv),
        "qp": qp,
        # nieosiągnięcie convention: q95 = not exceeded with 95% probability
        # (= 5% quantile), q90 = 10% quantile; key names spell it out.
        "q95_nieosiag": _q_digits(np.quantile(qq, 0.05)),
        "q90_nieosiag": _q_digits(np.quantile(qq, 0.10)),
        "colwell": colwell(qq.to_numpy(), months),
    }

    dynamics = {
        "min_daily": _q_digits(clim.min()),
        "max_daily": _q_digits(clim.max()),
        "rise": _round(rise, 1) if pd.notna(rise) else None,
        "fall": _round(fall, 1) if pd.notna(fall) else None,
        "high_pulses": pulses["high_pulses"],
        "low_pulses": pulses["low_pulses"],
        "high_dur": pulses["high_dur"],
        "low_dur": pulses["low_dur"],
        "reversals": round(reversals / n_years, 1),
        "baseflow_ratio": _round(bf_share),
    }

    return {
        "flow_matrix": matrix,
        "annual": annual,
        "stats": stats,
        "monthly_means_m3s": monthly_means,
        "annual_extremes": extremes,
        "dynamics": dynamics,
    }


def _stage_block(stage, dates) -> dict:
    sv = stage[stage.notna()]
    sdates = dates[stage.notna()]
    monthly = sv.groupby(sdates.dt.month).mean().reindex(range(1, 13))
    # Rolling on the full daily-grid stage column (windows with gaps drop out).
    rolling = {n: stage.rolling(n, min_periods=n).mean().dropna() for n in (1, 3, 7, 30, 90)}
    extremes = {}
    for n in (1, 3, 7, 30, 90):
        series = rolling[n]
        extremes[f"min{n}"] = _q_digits(series.min())
        extremes[f"max{n}"] = _q_digits(series.max())
    return {
        "monthly_means_cm": [
            _q_digits(v) if pd.notna(v) else None for v in monthly.tolist()
        ],
        "min_cm": _q_digits(sv.min()),
        "max_cm": _q_digits(sv.max()),
        "cv": _round(float(sv.std(ddof=1) / sv.mean())) if sv.mean() else None,
        "extremes": extremes,
    }
