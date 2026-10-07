"""Per-station aggregate indicators for the coverage-adaptive station page.

Every block mirrors a section of mocks/single.html. A block is computed only
when the underlying data supports it; otherwise its values are ``null`` and the
template omits or dashes the row.

Method choices (documented per the plan — literature-anchored, boring):

Hydrological year
    Oct 1 (rok hydrologiczny) .. Sep 30. Used for year classes, annual
    means, pulse/reversal rates.

Variants (strict Q/H separation)
    Each station yields up to four variants: q_max, q_cal, h_max, h_cal.
    "max" = full record; "cal" = official 1991..2020 calibration window
    (hydrological-year labels), clipped to available data.  A q_* variant
    exists only when the window slice has >= MIN_Q_DAYS valid discharge
    days (plus >= MIN_CAL_YEARS hydrological years with data for cal);
    h_* the same with MIN_STAGE_DAYS.  q_* variants carry only
    discharge-derived blocks; h_* only stage-derived blocks — never a mix.

Flow matrix (przepływy charakterystyczne, two-stage definition)
    First order (internal, per hydrological year with >= 300 valid days):
    NQ = yearly minimum, SQ = yearly mean, WQ = yearly maximum of daily Q.
    Second order (the displayed 3x3): aggregate the yearly values across
    years — rows N/S/W = min / mean / max over years, cols N/S/W =
    min / mean / max within the year.  Hence NNQ = minimum of minima,
    NSQ = minimum of averages, NWQ = minimum of maxima, SNQ = average of
    minima, SSQ = average of averages, ..., WWQ = maximum of maxima.
    Requires >= 5 valid years, else null.

BFI
    Eckhardt (2005) recursive digital filter, alpha=0.925, BFmax=0.8
    (conservative parameter set for porous aquifers); BFI = sum(BF)/sum(Q).

Lag-1 autocorrelation / CV
    Pearson autocorrelation of daily Q; CV = std(ddof=1)/mean of daily Q.

Flashiness
    Richards-Baker index: sum(|dQ|)/sum(Q) over consecutive-day pairs.

Q/P
    Runoff ratio: R = meanQ * 31557.6 / catchment_km2 (mm/y), divided by
    annual precipitation; only when the curated metadata provides both
    catchment_km2 and annual_precip_mm — otherwise null.

Colwell P/C/M
    Contingency table of 4 fixed flow states x 12 months over the whole
    record: 0 = zero flow, 1 = (0, NQ), 2 = [NQ, WQ), 3 = >= WQ, where
    NQ/WQ are the overall 10th/90th percentiles of daily Q (equal-frequency
    binning would force constancy ~0, so classes are anchored to the
    characteristic flows instead).  Natural-log entropies, s = 4, per
    Colwell (1974):
    P = 1 - H(X|Y)/ln(s), C = 1 - H(X)/ln(s), M = (H(X)+H(Y)-H(XY))/ln(s),
    with X = state, Y = month (so P = C + M exactly).

Annual extremes
    Global min/max of rolling N-day means (N = 1, 3, 7, 30, 90) on daily Q;
    zero-flow days = count of days with Q == 0.

Dynamics
    min_daily/max_daily = min/max of the day-of-year mean discharge series
    (Feb 29 excluded); rise/fall = mean positive/negative consecutive-day
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

import numpy as np
import pandas as pd

MIN_Q_DAYS = 5 * 365
MIN_STAGE_DAYS = 365
CAL_YEARS = (1991, 2020)
MIN_CAL_YEARS = 20  # hydrological years with data required for a cal variant
ECKHARDT_ALPHA = 0.925
ECKHARDT_BFMAX = 0.8
COLWELL_STATES = 4  # zero / low / mid / high flow classes


def _round(value, digits=2):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(float(value), digits)


def _q_digits(v: float):
    r = round(float(v), 0 if abs(v) >= 10 else 2)
    return int(r) if r == int(r) and abs(v) >= 10 else r


def hydro_year(ts: pd.Series) -> pd.Series:
    """Hydrological-year label for each date (year of Oct 1 .. Sep 30)."""
    return np.where(ts.dt.month >= 10, ts.dt.year + 1, ts.dt.year)


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


def _contingency(q: np.ndarray, months: np.ndarray) -> np.ndarray:
    """Flow states (fixed classes, see docstring) x 12 months counts."""
    q10, q90 = np.quantile(q, [0.10, 0.90])
    states = np.where(
        q == 0, 0, np.where(q < q10, 1, np.where(q < q90, 2, 3))
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
    p = c + m
    return {"p": _round(p), "c": _round(c), "m": _round(m)}


def flow_matrix(q: pd.Series, hy_year: pd.Series) -> dict | None:
    """Two-stage characteristic flows (see module docstring)."""
    df = pd.DataFrame({"q": q, "hy": hy_year})
    yearly = (
        df.dropna()
        .groupby("hy")["q"]
        .agg(n="count", nq="min", sq="mean", wq="max")
    )
    yearly = yearly[yearly["n"] >= 300]
    if len(yearly) < 5:
        return None
    out = {}
    for row, agg in (("N", np.nanmin), ("S", np.nanmean), ("W", np.nanmax)):
        for col in ("nq", "sq", "wq"):
            out[f"{row}{col[0].upper()}Q"] = _q_digits(agg(yearly[col]))
    return out


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


def variant_frames(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Windowed frames for every gate-passing variant (``{q,h}_{max,cal}``).

    Single source of truth for variant gating: aggregates and charts must
    agree on which variants exist for a station.
    """
    if df.empty:
        return {}
    hy = pd.Series(hydro_year(df["date"]), index=df.index)
    windows: dict[str, pd.DataFrame] = {"max": df}
    cal = df[(hy >= CAL_YEARS[0]) & (hy <= CAL_YEARS[1])]
    if not cal.empty:
        windows["cal"] = cal

    frames: dict[str, pd.DataFrame] = {}
    for win, wdf in windows.items():
        why = hy.loc[wdf.index]
        q_valid = wdf["discharge_m3s"].notna()
        stage_valid = wdf["stage_cm"].notna()
        if int(q_valid.sum()) >= MIN_Q_DAYS and (
            win == "max" or int(why[q_valid].nunique()) >= MIN_CAL_YEARS
        ):
            frames[f"q_{win}"] = wdf
        if int(stage_valid.sum()) >= MIN_STAGE_DAYS and (
            win == "max" or int(why[stage_valid].nunique()) >= MIN_CAL_YEARS
        ):
            frames[f"h_{win}"] = wdf
    return frames


def compute(df: pd.DataFrame, meta: dict | None = None) -> dict:
    """Compute the station aggregate JSON (see module docstring for variants)."""
    meta = meta or {}
    result: dict = {"station_id": None, "generated": None, "variants": {}}

    for name, wdf in variant_frames(df).items():
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

    baseflow = eckhardt_baseflow(qq.to_numpy())
    bfi = float(baseflow.sum() / qq.sum()) if qq.sum() > 0 else None

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

    rolling = {n: qq.rolling(n, min_periods=n).mean().dropna() for n in (1, 3, 7, 30, 90)}
    extremes = {}
    for n in (1, 3, 7, 30, 90):
        series = qq if n == 1 else rolling[n]
        extremes[f"min{n}"] = _q_digits(series.min())
        extremes[f"max{n}"] = _q_digits(series.max())
    extremes["zero_flow_days"] = int((qq == 0).sum())

    rise = valid_delta[valid_delta > 0].mean()
    fall = valid_delta[valid_delta < 0].mean()

    clim = qq.groupby(ddates.dt.dayofyear).mean()
    clim = clim[clim.index != 60]  # drop Feb 29

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
        "stats": stats,
        "monthly_means_m3s": monthly_means,
        "annual_extremes": extremes,
        "dynamics": dynamics,
    }


def _stage_block(stage, dates) -> dict:
    sv = stage[stage.notna()]
    sdates = dates[stage.notna()]
    monthly = sv.groupby(sdates.dt.month).mean().reindex(range(1, 13))
    extremes = {}
    for n in (1, 3, 7, 30, 90):
        series = sv if n == 1 else sv.rolling(n, min_periods=n).mean().dropna()
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
