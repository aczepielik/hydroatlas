"""Golden-structure and sanity tests for the aggregate computation."""

import numpy as np
import pandas as pd

from hydroatlas.aggregates import (
    MIN_CAL_YEARS,
    compute,
    eckhardt_baseflow,
    flow_matrix,
    variant_frames,
)

Q_BLOCKS = ["flow_matrix", "stats", "monthly_means_m3s", "annual_extremes", "dynamics"]
MATRIX_KEYS = {
    "NNQ", "NSQ", "NWQ",
    "SNQ", "SSQ", "SWQ",
    "WNQ", "WSQ", "WWQ",
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
    for key in Q_BLOCKS:
        assert qv[key] is not None, key
    stats = qv["stats"]
    assert 0 < stats["bfi"] < 1
    assert 0 < stats["flashiness"] < 2
    assert 0 < stats["cv"] < 5
    assert stats["qp"] is None  # no curated catchment/precip metadata
    colwell = stats["colwell"]
    assert abs(colwell["p"] - (colwell["c"] + colwell["m"])) <= 0.01
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


def test_flow_matrix_two_stage_definition():
    # 10 hydrological years, 360 valid days each: first half of the year
    # at 2i, second half at 2i+1 -> nq=2i, sq=2i+0.5, wq=2i+1 (i = 1..10).
    dates, values = [], []
    for i in range(1, 11):
        start = pd.Timestamp(year=2000 + i, month=10, day=1)
        days = pd.date_range(start, periods=360, freq="D")
        dates.append(days)
        values.append(np.concatenate([np.full(180, 2 * i), np.full(180, 2 * i + 1)]))
    frame = pd.DataFrame(
        {"date": pd.DatetimeIndex(np.concatenate(dates)), "q": np.concatenate(values)}
    )
    hy = pd.Series(
        np.where(frame["date"].dt.month >= 10,
                 frame["date"].dt.year + 1, frame["date"].dt.year),
        index=frame.index,
    )
    matrix = flow_matrix(frame["q"], hy)
    assert matrix is not None
    assert set(matrix) == MATRIX_KEYS
    # _q_digits rounds >=10 values to integers (20.5 -> 20, 11.5 -> 12)
    assert matrix == {
        "NNQ": 2, "NSQ": 2.5, "NWQ": 3,
        "SNQ": 11, "SSQ": 12, "SWQ": 12,
        "WNQ": 20, "WSQ": 20, "WWQ": 21,
    }


def test_flow_matrix_monotonicity():
    matrix = compute(make_series(years=30))["variants"]["q_max"]["flow_matrix"]
    assert set(matrix) == MATRIX_KEYS
    # within a row: min over years of nq <= sq <= wq
    assert matrix["NNQ"] <= matrix["NSQ"] <= matrix["NWQ"]
    assert matrix["SNQ"] <= matrix["SSQ"] <= matrix["SWQ"]
    assert matrix["WNQ"] <= matrix["WSQ"] <= matrix["WWQ"]
    # within a column: min <= mean <= max over years
    assert matrix["NNQ"] <= matrix["SNQ"] <= matrix["WNQ"]
    assert matrix["NSQ"] <= matrix["SSQ"] <= matrix["WSQ"]
    assert matrix["NWQ"] <= matrix["SWQ"] <= matrix["WWQ"]


def test_variant_frames_gating():
    assert variant_frames(pd.DataFrame()) == {}
    frames = variant_frames(make_series(years=1))
    assert set(frames) == {"h_max"}
    frames = variant_frames(make_series(years=30))
    assert set(frames) == {"q_max", "q_cal", "h_max", "h_cal"}
    # cal window must be clipped to hydrological years 1991..2020
    cal_dates = frames["q_cal"]["date"]
    hy = np.where(cal_dates.dt.month >= 10, cal_dates.dt.year + 1, cal_dates.dt.year)
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
