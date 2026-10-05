"""Read/write cleaned daily series as CSV.GZ."""

from pathlib import Path

import pandas as pd

COLUMNS = ["date", "stage_cm", "discharge_m3s"]


def write_clean(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[COLUMNS].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8", compression="gzip", float_format="%.3f")


def read_clean(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, compression="gzip", encoding="utf-8")
    df["date"] = pd.to_datetime(df["date"])
    df["stage_cm"] = pd.to_numeric(df["stage_cm"], errors="coerce")
    df["discharge_m3s"] = pd.to_numeric(df["discharge_m3s"], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)
