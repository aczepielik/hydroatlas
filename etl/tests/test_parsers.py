"""Parser tests against real IMGW archive samples (one per file era)."""

from pathlib import Path

import pytest

from hydroatlas.ingest.archive import parse_csv_bytes

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "expected_rows"),
    [
        ("sample_1951.csv", 41),  # CP1250, comma, individually quoted fields
        ("sample_2023.csv", 41),  # UTF-8 BOM, semicolon, unquoted
        ("sample_2024.csv", 41),  # CP1250, comma, outer-quoted with "" escapes
    ],
)
def test_parse_row_count(name, expected_rows):
    df = parse_csv_bytes((FIXTURES / name).read_bytes())
    assert len(df) == expected_rows


def test_parse_1951_sentinels_and_values():
    df = parse_csv_bytes((FIXTURES / "sample_1951.csv").read_bytes())
    assert df["id"].iloc[0] == "149180020"
    # stage 9999 must become missing; discharge stays numeric
    assert df["stage_cm"].isna().all()
    assert df["discharge_m3s"].iloc[0] == pytest.approx(38.1)
    assert df["date"].iloc[0].strftime("%Y-%m-%d") == "1951-01-01"


def test_parse_2023_semicolon_utf8():
    df = parse_csv_bytes((FIXTURES / "sample_2023.csv").read_bytes())
    row = df.iloc[0]
    assert row["id"] == "149180020"
    assert row["name"] == "CHAŁUPKI"  # UTF-8 decoding intact
    assert row["date"].strftime("%Y-%m-%d") == "2023-01-01"
    assert row["discharge_m3s"] == pytest.approx(14.2)


def test_parse_2024_outer_quoted():
    df = parse_csv_bytes((FIXTURES / "sample_2024.csv").read_bytes())
    row = df.iloc[0]
    assert row["id"] == "149180020"
    assert row["name"] == "CHAŁUPKI"
    assert row["river"] == "Odra (1)"
    assert row["date"].strftime("%Y-%m-%d") == "2024-01-01"
    assert row["stage_cm"] == pytest.approx(113.0)
    assert row["discharge_m3s"] == pytest.approx(25.4)


def test_parse_empty():
    assert parse_csv_bytes(b"").empty
