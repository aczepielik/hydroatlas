"""Golden-structure and sanity tests for the aggregate computation."""

import numpy as np
import pandas as pd
import pytest

from hydroatlas.aggregates import compute, eckhardt_baseflow

Q_BLOCKS = ["flow_matrix", "stats", "monthly_means_m3s", "annual_extremes", "dynamics"]


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


def test_q_block_structure_matches_mock_sections():
    result = compute(make_series())
    for key in Q_BLOCKS:
        assert result[key] is not None, key
    stats = result["stats"]
    assert 0 < stats["bfi"] < 1
    assert 0 < stats["flashiness"] < 2
    assert 0 < stats["cv"] < 5
    assert stats["qp"] is None  # no curated catchment/precip metadata
    colwell = stats["colwell"]
    assert abs(colwell["p"] - (colwell["c"] + colwell["m"])) <= 0.01
    assert len(result["monthly_means_m3s"]) == 12
    assert all(v is not None for v in result["monthly_means_m3s"])
    matrix = result["flow_matrix"]
    assert set(matrix) == {"N", "S", "W"}
    for row in matrix.values():
        assert set(row) == {"nq", "sq", "wq"}
        assert row["nq"] <= row["sq"] <= row["wq"]
    ae = result["annual_extremes"]
    assert ae["min1"] <= ae["max1"]
    assert ae["min30"] <= ae["max30"]
    assert ae["zero_flow_days"] == 0
    dy = result["dynamics"]
    assert dy["rise"] > 0
    assert dy["fall"] < 0
    assert dy["reversals"] > 0
    assert 0 <= dy["baseflow_ratio"] <= 1
    assert result["stage"]["min_cm"] < result["stage"]["max_cm"]


def test_short_series_gates_q_blocks_but_keeps_stage():
    result = compute(make_series(years=1))
    for key in Q_BLOCKS:
        assert result[key] is None, key
    assert result["stage"] is not None
    assert result["period"]["n_valid_days"] == 366


def test_stage_only_station():
    result = compute(make_series(years=10, discharge=False))
    for key in Q_BLOCKS:
        assert result[key] is None, key
    assert result["stage"] is not None
    assert result["period"]["n_valid_days"] == 0


def test_constant_flow_caps_at_bfmax():
    # The filter's long-run baseflow fraction is bounded by BFImax by design,
    # so a perfectly constant series converges to BFImax * Q.
    from hydroatlas.aggregates import ECKHARDT_BFMAX

    q = np.full(2000, 10.0)
    bf = eckhardt_baseflow(q)
    assert np.allclose(bf[-100:], ECKHARDT_BFMAX * 10.0)
    assert np.all(bf <= q)


def test_empty_frame():
    df = make_series(years=0)
    result = compute(df.iloc[:0])
    assert all(result[k] is None for k in Q_BLOCKS + ["period", "stage"])
