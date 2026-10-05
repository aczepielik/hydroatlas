"""Turn raw daily rows into a clean, gap-annotated station series."""

from __future__ import annotations

import pandas as pd


def clean_station(df: pd.DataFrame, station_id: str) -> pd.DataFrame:
    """Extract one station, dedupe, sort, and reindex to a continuous daily range."""
    sub = df[df["id"] == station_id][["date", "stage_cm", "discharge_m3s"]].copy()
    sub = sub.drop_duplicates(subset=["date"], keep="last")
    sub = sub.sort_values("date").reset_index(drop=True)
    if sub.empty:
        return sub
    full = pd.date_range(sub["date"].min(), sub["date"].max(), freq="D")
    sub = sub.set_index("date").reindex(full).rename_axis("date").reset_index()
    return sub


def gap_report(df: pd.DataFrame, label: str = "") -> str:
    """Human-readable coverage summary for a cleaned series."""
    if df.empty:
        return f"{label}: no data"
    total = len(df)
    valid_q = int(df["discharge_m3s"].notna().sum())
    valid_h = int(df["stage_cm"].notna().sum())
    missing = df.loc[df["discharge_m3s"].isna(), "date"]
    lines = [
        f"{label}: {df['date'].min():%Y-%m-%d} .. {df['date'].max():%Y-%m-%d} "
        f"({total} days)",
        f"  discharge coverage: {valid_q / total:.1%}  stage coverage: {valid_h / total:.1%}",
    ]
    if not missing.empty:
        # longest continuous run of missing discharge
        groups = (missing.diff().dt.days != 1).cumsum()
        runs = missing.groupby(groups).agg(["min", "max", "size"])
        longest = runs.loc[runs["size"].idxmax()]
        lines.append(
            f"  gaps: {len(runs)} runs, longest {int(longest['size'])} days "
            f"({longest['min']:%Y-%m-%d}..{longest['max']:%Y-%m-%d})"
        )
    return "\n".join(lines)
