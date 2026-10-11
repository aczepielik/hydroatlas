"""Golden-structure and sanity tests for the aggregate computation."""

import numpy as np
import pandas as pd

from hydroatlas.aggregates import (
    MIN_CAL_YEARS,
    _contingency,
    colwell,
    compute,
    eckhardt_baseflow,
    eckhardt_grid,
    flow_matrix,
    hydro_year,
    mann_kendall,
    theil_sen,
    variant_frames,
)

Q_BLOCKS = [
    "flow_matrix",
    "annual",
    "stats",
    "monthly_means_m3s",
    "annual_extremes",
    "dynamics",
]
MATRIX_KEYS = {
    "NNQ", "NSQ", "NZQ", "NWQ",
    "SNQ", "SSQ", "SZQ", "SWQ",
    "ZNQ", "ZSQ", "ZZQ", "ZWQ",
    "WNQ", "WSQ", "WZQ", "WWQ",
}


def make_series(years: int = 12, stage: bool = True, discharge: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("1990-01-01", periods=365 * years + 1, freq="D")
    doy = dates.dayofyear.to_numpy()
    seasonal = 50 + 40 * np.sin(2 * np.pi * (doy - 90) / 365)
    q = np.maximum(seasonal + rng.normal(0, 5, len(dates)), 0.5)
    return pd.DataFrame(
        {
            "date": dates,
            "discharge_m3s": q if discharge else np.nan,
            "stage_cm": q * 0.6 + 100 if stage else np.nan,
        }
    )


def test_q_variant_structure():
    result = compute(make_series())
    assert set(result["variants"]) == {"q_max", "h_max"}
    qv = result["variants"]["q_max"]
    stats = qv["stats"]
    assert 0 < stats["bfi"] < 1
    assert 0 < stats["flashiness"] < 2
    assert 0 < stats["cv"] < 5
    assert stats["qp"] is None  # no curated catchment/precip metadata
    colwell = stats["colwell"]
    assert abs(colwell["p"] - (colwell["c"] + colwell["m"])) < 1e-9
    # 12-year fixture: below the >= 20-year matrix floor -> no matrix.
    for key in Q_BLOCKS[1:]:
        assert qv[key] is not None, key
    assert qv["flow_matrix"] is None
    assert len(qv["monthly_means_m3s"]) == 12
    assert all(v is not None for v in qv["monthly_means_m3s"])
    ae = qv["annual_extremes"]
    assert ae["min1"] <= ae["max1"]
    assert ae["min30"] <= ae["max30"]
    assert ae["zero_flow_days"] == 0
    dy = qv["dynamics"]
    assert dy["rise"] > 0
    assert dy["fall"] < 0
    assert dy["reversals"] > 0
    assert 0 <= dy["baseflow_ratio"] <= 1
    assert qv["period"]["n_valid_days"] > 1825


def test_h_variant_carries_only_stage_blocks():
    result = compute(make_series())
    hv = result["variants"]["h_max"]
    assert set(hv) == {"period", "stage"}
    assert hv["stage"]["min_cm"] < hv["stage"]["max_cm"]
    ext = hv["stage"]["extremes"]
    assert ext["min1"] <= ext["max1"]
    assert ext["min90"] <= ext["max90"]
    assert len(hv["stage"]["monthly_means_cm"]) == 12


def test_cal_variants_need_twenty_hydrological_years():
    # 12 years of data: the 1991-2020 window overlaps fewer than
    # MIN_CAL_YEARS hydrological years -> no cal variants.
    assert set(compute(make_series(years=12))["variants"]) == {"q_max", "h_max"}
    # 30 years covering the full calibration window -> all four variants.
    long = make_series(years=30)
    variants = set(compute(long)["variants"])
    assert variants == {"q_max", "q_cal", "h_max", "h_cal"}
    cal = compute(long)["variants"]["q_cal"]
    assert cal["period"]["start"] <= 1991
    assert 2019 <= cal["period"]["end"] <= 2021


def test_short_series_h_only_no_q_content():
    # The review bug: little Q data must yield a page with zero Q blocks.
    result = compute(make_series(years=1))
    assert set(result["variants"]) == {"h_max"}


def test_stage_only_station():
    result = compute(make_series(years=10, discharge=False))
    assert set(result["variants"]) == {"h_max"}
    hv = result["variants"]["h_max"]
    assert hv["stage"] is not None
    assert hv["period"]["n_valid_days"] > 365


def make_matrix_series(years: int = 20) -> tuple[pd.Series, pd.Series]:
    """Per hydrological year i (1..years): 360 valid days, first half at
    2i and second half at 2i+2 -> per-year nq = 2i, sq = zq = 2i+1
    (integer mean/median of equal halves), wq = 2i+2.  Hydrological
    years are labelled 2001+i (Nov 1 of 2000+i .. Oct of 2001+i),
    i.e. 2002..2021 for years=20."""
    dates, values = [], []
    for i in range(1, years + 1):
        start = pd.Timestamp(year=2000 + i, month=11, day=1)
        days = pd.date_range(start, periods=360, freq="D")
        dates.append(days)
        values.append(np.concatenate([np.full(180, 2 * i), np.full(180, 2 * i + 2)]))
    frame = pd.DataFrame(
        {"date": pd.DatetimeIndex(np.concatenate(dates)), "q": np.concatenate(values)}
    )
    hy = pd.Series(
        np.where(frame["date"].dt.month >= 11,
                 frame["date"].dt.year + 1, frame["date"].dt.year),
        index=frame.index,
    )
    return frame["q"], hy


def test_flow_matrix_two_stage_definition():
    # 20 hydrological years (HY 2002..2021), per-year halves at 2i / 2i+2
    # -> first-order nq=2i, sq=zq=2i+1, wq=2i+2 (integers, no rounding
    # ambiguity).  Second order over i=1..20 (all values >= 10 round to
    # integers; the 2..9 range only holds NNQ=2, NSQ=3, NZQ=3, NWQ=4):
    #   N (min):    2, 3, 3, 4      S (mean): 21, 22, 22, 23
    #   Z (median): 21, 22, 22, 23  W (max):  40, 41, 41, 42
    q, hy = make_matrix_series(20)
    matrix = flow_matrix(q, hy)
    assert matrix is not None
    assert set(matrix) == {"cells", "period"}
    assert set(matrix["cells"]) == MATRIX_KEYS
    assert matrix["cells"] == {
        "NNQ": 2, "NSQ": 3, "NZQ": 3, "NWQ": 4,
        "SNQ": 21, "SSQ": 22, "SZQ": 22, "SWQ": 23,
        "ZNQ": 21, "ZSQ": 22, "ZZQ": 22, "ZWQ": 23,
        "WNQ": 40, "WSQ": 41, "WZQ": 41, "WWQ": 42,
    }
    assert matrix["period"] == {"start": 2002, "end": 2021, "years": 20}


def test_flow_matrix_floor():
    # Same construction with only 10 years: below MIN_MATRIX_YEARS -> None.
    q, hy = make_matrix_series(10)
    assert flow_matrix(q, hy) is None


def test_flow_matrix_monotonicity():
    matrix = compute(make_series(years=30))["variants"]["q_max"]["flow_matrix"]
    assert set(matrix["cells"]) == MATRIX_KEYS
    cells = matrix["cells"]
    # per column: N-agg <= {S, Z} <= W-agg (aggregation over years)
    for c in "NSZW":
        assert cells[f"N{c}Q"] <= cells[f"S{c}Q"]
        assert cells[f"N{c}Q"] <= cells[f"Z{c}Q"]
        assert cells[f"S{c}Q"] <= cells[f"W{c}Q"]
        assert cells[f"Z{c}Q"] <= cells[f"W{c}Q"]
    # per row: NQ <= {SQ, ZQ} <= WQ (within-year statistic).
    # Deliberately NOT asserting SQ <= ZQ: mean vs median is not ordered.
    for r in "NSZW":
        assert cells[f"{r}NQ"] <= cells[f"{r}SQ"]
        assert cells[f"{r}NQ"] <= cells[f"{r}ZQ"]
        assert cells[f"{r}SQ"] <= cells[f"{r}WQ"]
        assert cells[f"{r}ZQ"] <= cells[f"{r}WQ"]
    period = matrix["period"]
    assert period["years"] >= 20
    assert period["start"] <= period["end"]


def test_colwell_constant_flow():
    # A perfectly constant positive series puts every day in class 1:
    # H(X) = H(X|Y) = 0, so C = 1, M = 0, P = 1 (identity holds exactly).
    months = (np.arange(2400) % 12) + 1
    res = colwell(np.full(2400, 7.5), months)
    assert res == {"p": 1.0, "c": 1.0, "m": 0.0}


def test_contingency_classes():
    # 21 sorted values: np.quantile 0.05 sits at index 0.05*20 = 1 and
    # 0.90 at index 0.90*20 = 18, so q95 = 2 and q10 = 19 exactly;
    # mean = 339/21 ≈ 16.14.  Priority order (zero first) gives classes:
    #   0: {0}            -> 1   (zero beats q <= q95)
    #   1: {2}            -> 1   (0 < q <= q95)
    #   2: {3..16}        -> 14  (q95 < q <= mean)
    #   3: {17,18,19}     -> 3   (mean < q <= q10)
    #   4: {50,100}       -> 2   (q > q10)
    q = np.array(
        [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 50, 100],
        dtype=float,
    )
    months = (np.arange(21) % 12) + 1
    table = _contingency(q, months)
    assert table.shape == (5, 12)
    assert table.sum() == 21
    assert list(table.sum(axis=1)) == [1, 1, 14, 3, 2]


def test_variant_frames_gating():
    assert variant_frames(pd.DataFrame()) == {}
    frames = variant_frames(make_series(years=1))
    assert set(frames) == {"h_max"}
    frames = variant_frames(make_series(years=30))
    assert set(frames) == {"q_max", "q_cal", "h_max", "h_cal"}
    # cal window must be clipped to hydrological years 1991..2020
    cal_dates = frames["q_cal"]["date"]
    hy = np.where(cal_dates.dt.month >= 11, cal_dates.dt.year + 1, cal_dates.dt.year)
    assert hy.min() >= 1991 and hy.max() <= 2020
    assert len(set(hy)) >= MIN_CAL_YEARS


def test_constant_flow_caps_at_bfmax():
    # The filter's long-run baseflow fraction is bounded by BFImax by design,
    # so a perfectly constant series converges to BFImax * Q.
    from hydroatlas.aggregates import ECKHARDT_BFMAX

    q = np.full(2000, 10.0)
    bf = eckhardt_baseflow(q)
    assert np.allclose(bf[-100:], ECKHARDT_BFMAX * 10.0)
    assert np.all(bf <= q)


def test_empty_frame():
    result = compute(pd.DataFrame())
    assert result["variants"] == {}
    assert result["station_id"] is None
    assert result["generated"] is None


def test_hydro_year_november():
    # Rocznik convention: the hydrological year runs 1 Nov .. 31 Oct.
    ts = pd.Series(pd.to_datetime(["1990-10-31", "1990-11-01", "1991-10-31", "2020-11-01"]))
    assert list(hydro_year(ts)) == [1990, 1991, 1991, 2021]


def test_cal_gate_counts_usable_years():
    # Thin frame: ~90 valid Q days per hydrological year — the total passes
    # MIN_Q_DAYS but no cal-window year reaches MIN_YEAR_DAYS, so no q_cal.
    dates = pd.date_range("1990-11-01", periods=365 * 30, freq="D")
    start_years = np.where(dates.month >= 11, dates.year, dates.year - 1)
    hy_starts = pd.to_datetime(pd.Index(start_years).astype(str) + "-11-01")
    offset = (dates - hy_starts).days
    thin = pd.DataFrame(
        {
            "date": dates,
            "discharge_m3s": np.where(offset < 90, 1.0, np.nan),
            "stage_cm": 100.0,
        }
    )
    assert int(thin["discharge_m3s"].notna().sum()) >= 5 * 365
    assert "q_cal" not in variant_frames(thin)

    # Thick frame: every cal hydrological year has a full 365/366 valid
    # days -> the gate admits q_cal.
    thick = pd.DataFrame(
        {"date": dates, "discharge_m3s": 1.0, "stage_cm": 100.0}
    )
    assert "q_cal" in variant_frames(thick)


def test_rolling_respects_calendar_gaps():
    # Block A (10 days @ 10000) -> 400-day calendar gap -> block B
    # (2000 days @ 5). Rolling over *observations* blends the seam
    # (~1116 for max90); rolling on the daily grid drops any 90-day
    # window that touches the gap, so max90 = 5 exactly.
    dates = pd.date_range("2000-01-01", periods=10 + 400 + 2000, freq="D")
    q = np.concatenate(
        [np.full(10, 10000.0), np.full(400, np.nan), np.full(2000, 5.0)]
    )
    assert int(np.isfinite(q).sum()) >= 5 * 365  # MIN_Q_DAYS gate
    frame = pd.DataFrame({"date": dates, "discharge_m3s": q, "stage_cm": np.nan})
    ae = compute(frame)["variants"]["q_max"]["annual_extremes"]
    assert ae["max90"] == 5

    # Mirrored: short low block + long high block -> min90 must not
    # blend down across the seam (observation-rolling would give 8889).
    q2 = np.concatenate(
        [np.full(10, 1.0), np.full(400, np.nan), np.full(2000, 10000.0)]
    )
    frame2 = pd.DataFrame({"date": dates, "discharge_m3s": q2, "stage_cm": np.nan})
    ae2 = compute(frame2)["variants"]["q_max"]["annual_extremes"]
    assert ae2["min90"] == 10000


def test_daily_climatology_month_day_grouping():
    # 10 years including leap years (2000/2004/2008 -> 02-29 exists):
    # q = 10 except 03-01 -> 1 and 03-02 -> 2. Day-of-year grouping would
    # blend March with Feb 29 after leap years; month-day grouping keeps
    # the classes clean.
    dates = pd.date_range("2000-01-01", periods=365 * 10 + 3, freq="D")
    q = np.full(len(dates), 10.0)
    q[(dates.month == 3) & (dates.day == 1)] = 1.0
    q[(dates.month == 3) & (dates.day == 2)] = 2.0
    frame = pd.DataFrame({"date": dates, "discharge_m3s": q, "stage_cm": 100.0})
    dyn = compute(frame)["variants"]["q_max"]["dynamics"]
    assert dyn["min_daily"] == 1
    assert dyn["max_daily"] == 10


def test_eckhardt_segmented_equals_per_segment():
    # Two runs of consecutive days separated by a calendar gap: the grid
    # helper must equal the plain filter applied run-by-run.
    run1 = pd.date_range("2000-01-01", periods=5, freq="D")
    run2 = pd.date_range("2000-03-01", periods=4, freq="D")
    dates = pd.Series(pd.DatetimeIndex([*run1, *run2]))
    q = pd.Series([10.0, 8.0, 6.0, 4.0, 2.0, 5.0, 7.0, 9.0, 3.0])
    expected = np.concatenate(
        [eckhardt_baseflow(q.iloc[:5].to_numpy()), eckhardt_baseflow(q.iloc[5:].to_numpy())]
    )
    assert np.array_equal(eckhardt_grid(dates, q), expected)

    # Single contiguous run -> identical to the plain filter.
    cont = pd.Series(pd.date_range("2000-01-01", periods=200, freq="D"))
    qc = pd.Series(np.linspace(1.0, 10.0, 200))
    assert np.array_equal(eckhardt_grid(cont, qc), eckhardt_baseflow(qc.to_numpy()))


def test_theil_sen_exact_line():
    x = np.arange(1, 6, dtype=float)
    y = 2.0 * x + 1.0
    slope, intercept = theil_sen(x, y)
    assert abs(slope - 2.0) < 1e-9
    assert abs(intercept - 1.0) < 1e-9


def test_mann_kendall():
    rising = mann_kendall(np.arange(1.0, 21.0))
    assert rising["p"] < 0.05
    flat = mann_kendall(np.full(20, 5.0))
    assert flat["z"] == 0.0
    assert flat["p"] == 1.0


def test_annual_rows_sorted_and_bounded():
    qv = compute(make_series(years=30))["variants"]["q_max"]
    annual = qv["annual"]
    rows = annual["rows"]
    hy = [r["hy"] for r in rows]
    assert hy == sorted(hy)
    assert len(set(hy)) == len(hy)
    for r in rows:
        assert set(r) == {"hy", "mean", "min", "max", "n"}
        assert r["n"] >= 1
        assert r["min"] <= r["mean"] <= r["max"]
    trend = annual["trend"]
    assert trend is not None
    assert set(trend) == {"slope", "intercept", "mk_z", "mk_p", "n_years"}
    assert trend["n_years"] >= 10


def test_annual_partial_years_and_trend_floor():
    # 7 hydrological years (HY 1991..1997) with HY 1993 thinned to 100
    # valid days: the thin year still appears as a row (n = 100, that is
    # what the n column is for) but only 5 complete years remain ->
    # trend must be null (< 10 complete years).
    dates = pd.date_range("1990-11-01", periods=365 * 6 + 50, freq="D")
    hy = np.where(dates.month >= 11, dates.year + 1, dates.year)
    valid = np.ones(len(dates), dtype=bool)
    in_1993 = hy == 1993
    valid[in_1993] = np.cumsum(in_1993[in_1993]) <= 100
    q = 5.0 + 0.1 * (np.arange(len(dates)) % 5)  # slight variation: avoids
    # a degenerate lag1 (std=0) on a perfectly constant series
    frame = pd.DataFrame(
        {"date": dates, "discharge_m3s": np.where(valid, q, np.nan), "stage_cm": np.nan}
    )
    qv = compute(frame)["variants"]["q_max"]
    rows = qv["annual"]["rows"]
    assert [r["hy"] for r in rows] == sorted(r["hy"] for r in rows)
    by_hy = {r["hy"]: r for r in rows}
    assert by_hy[1993]["n"] == 100
    assert qv["annual"]["trend"] is None


def test_low_flow_nieosiag_readouts():
    # 1900 days = 19 full cycles of 0..99.  quantile 0.05 sits at index
    # 0.05*1899 = 94.95 (between values 4 and 5) -> 4.95; quantile 0.10
    # at 189.9 (between 9 and 10) -> 9.9.  Both < 10 keep 2 decimals.
    dates = pd.date_range("1990-01-01", periods=1900, freq="D")
    q = (np.arange(1900) % 100).astype(float)
    frame = pd.DataFrame({"date": dates, "discharge_m3s": q, "stage_cm": np.nan})
    stats = compute(frame)["variants"]["q_max"]["stats"]
    assert stats["q95_nieosiag"] == 4.95
    assert stats["q90_nieosiag"] == 9.9
    assert stats["q95_nieosiag"] <= stats["q90_nieosiag"]


def test_breaks_split_max_windows():
    # 40-year record (1990-01-01 .. 2030-01-01): one in-range break
    # splits the pooled max window into two segments; cal spans the break.
    frames = variant_frames(make_series(years=40), breaks=["2005-07-01"])
    assert {
        "q_max_1", "q_max_2", "h_max_1", "h_max_2", "q_cal", "h_cal",
    } <= set(frames)
    # Segments REPLACE the pooled keys — no fallback to q_max / h_max.
    assert "q_max" not in frames and "h_max" not in frames
    # Boundary: the break date is the FIRST day of segment 2.
    assert frames["q_max_1"]["date"].max() < pd.Timestamp("2005-07-01")
    assert frames["q_max_2"]["date"].min() == pd.Timestamp("2005-07-01")


def test_breaks_out_of_record_ignored():
    # Break after the record end / before the record start / unparsable
    # are all ignored -> unsplit pooled keys.
    frames = variant_frames(
        make_series(years=12),  # 1990-01-01 .. 2002-01-01
        breaks=["2005-07-01", "1980-05-05", "not-a-date"],
    )
    assert set(frames) == {"q_max", "h_max"}


def test_break_at_record_start_ignored():
    frames = variant_frames(make_series(years=12), breaks=["1990-01-01"])
    assert set(frames) == {"q_max", "h_max"}


def test_break_short_segment_dropped_no_pooled_fallback():
    # A 100-day tail fails both MIN_Q_DAYS and MIN_STAGE_DAYS: segment 2
    # is absent, segment 1 stays, and there is NO pooled q_max / h_max.
    df = make_series(years=40)
    tail_break = df["date"].max() - pd.Timedelta(days=100)
    frames = variant_frames(df, breaks=[tail_break])
    assert "q_max_1" in frames and "h_max_1" in frames
    assert "q_max_2" not in frames and "h_max_2" not in frames
    assert "q_max" not in frames and "h_max" not in frames


def test_compute_surfaces_break_variants():
    result = compute(make_series(years=40), {"breaks": ["2005-07-01"]})
    names = set(result["variants"])
    assert "q_max" not in names and "h_max" not in names
    assert {"q_max_1", "q_max_2", "h_max_1", "h_max_2"} <= names
    # Segment blocks carry the full per-variant structure.
    seg = result["variants"]["q_max_1"]
    assert seg["period"]["n_valid_days"] >= 5 * 365
    assert seg["stats"] is not None and seg["dynamics"] is not None


def test_cal_window_spans_breaks():
    # Documented limitation: cal stays whole even when max is split.
    frames = variant_frames(make_series(years=40), breaks=["2005-07-01"])
    cal_dates = frames["q_cal"]["date"]
    assert cal_dates.min() < pd.Timestamp("2005-07-01") <= cal_dates.max()
    cal = compute(make_series(years=40), {"breaks": ["2005-07-01"]})["variants"]["q_cal"]
    assert cal["period"]["start"] <= 2005 <= cal["period"]["end"]
