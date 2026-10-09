"""CSV history under ``data/history/``: per-period prices and a one-row-per-day summary.

Files are written deterministically (sorted, fixed float format, "\\n" endings) so re-processing a day
leaves them byte-identical and git diffs stay meaningful.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pandas as pd

from . import summary as sm

PERIOD_COLS = ["date", "ts_utc", "mtu_min", "price_es", "price_pt", "source"]
DAILY_COLS = [
    "date", "n_periods", "mtu_min", "n_le0", "n_neg", "n_cheap", "min", "mean", "max",
    "midday_mean", "evening_mean", "cheapest_3h_start", "cheapest_3h_end", "cheapest_3h_mean",
    "source_version",
]  # fmt: skip


def history_dir(root: Path) -> Path:
    return Path(root) / "data" / "history"


def periods_path(root: Path) -> Path:
    return history_dir(root) / "periods.csv"


def daily_path(root: Path) -> Path:
    return history_dir(root) / "daily.csv"


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v + 0.0:.2f}".replace("-0.00", "0.00")
    return str(v)


def _write(path: Path, cols: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for row in rows:
            w.writerow([_fmt(row[c]) for c in cols])


def read_periods(root: Path) -> pd.DataFrame:
    path = periods_path(root)
    if not path.exists():
        return pd.DataFrame(columns=PERIOD_COLS)
    df = pd.read_csv(path, dtype={"date": str, "source": str})
    return df.assign(ts_utc=pd.to_datetime(df["ts_utc"], utc=True))


def read_daily(root: Path) -> pd.DataFrame:
    path = daily_path(root)
    if not path.exists():
        return pd.DataFrame(columns=DAILY_COLS)
    return pd.read_csv(path, dtype={"date": str, "cheapest_3h_start": str, "cheapest_3h_end": str})


def write_periods(root: Path, df: pd.DataFrame) -> None:
    df = df.sort_values("ts_utc").reset_index(drop=True)
    ts = df["ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = df.assign(ts_utc=ts, mtu_min=df["mtu_min"].astype(int)).to_dict("records")
    _write(periods_path(root), PERIOD_COLS, rows)


def upsert_periods(root: Path, new: pd.DataFrame) -> None:
    """Replace all stored rows for every delivery date present in ``new`` (needs PERIOD_COLS)."""
    old = read_periods(root)
    old = old[~old["date"].isin(set(new["date"]))]
    write_periods(root, pd.concat([df for df in (old, new[PERIOD_COLS]) if not df.empty]))


def day_rows(day_df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Day frame (``ts_utc``...) -> periods.csv rows."""
    day = day_df["ts_utc"].min().tz_convert(sm.TZ).date()
    return day_df.assign(date=day.isoformat(), source=source)[PERIOD_COLS]


def day_frames(periods: pd.DataFrame):
    """Yield ``(date, day_frame, source)`` per stored delivery date, oldest first."""
    for date, g in periods.groupby("date", sort=True):
        yield dt.date.fromisoformat(date), g.reset_index(drop=True), str(g["source"].iloc[0])


def daily_row(s: dict) -> dict:
    c = s["cheapest_3h"]
    return {
        **{k: s[k] for k in ("date", "n_periods", "mtu_min", "n_le0", "n_neg", "n_cheap")},
        **{k: s[k] for k in ("midday_mean", "evening_mean")},
        "min": s["min_price"], "mean": s["mean_price"], "max": s["max_price"],
        "cheapest_3h_start": c["start"], "cheapest_3h_end": c["end"], "cheapest_3h_mean": c["mean"],
        "source_version": s["source"]["version"],
    }  # fmt: skip


def write_daily(root: Path, rows: list[dict]) -> None:
    _write(daily_path(root), DAILY_COLS, sorted(rows, key=lambda r: r["date"]))


def upsert_daily(root: Path, s: dict) -> None:
    rows = [r for r in read_daily(root).to_dict("records") if r["date"] != s["date"]]
    for r in rows:  # pandas reads ints with gaps as floats; restore ints
        for k in ("n_periods", "mtu_min", "n_le0", "n_neg", "n_cheap", "source_version"):
            r[k] = None if pd.isna(r[k]) else int(r[k])
    write_daily(root, [*rows, daily_row(s)])


def rebuild_daily(root: Path) -> None:
    rows = [daily_row(sm.summarise(g, None, src)) for _, g, src in day_frames(read_periods(root))]
    write_daily(root, rows)


def is_processed(root: Path, day: dt.date) -> bool:
    return day.isoformat() in set(read_daily(root)["date"])


def seed_from_parquet(root: Path, path: Path) -> tuple[int, list[str]]:
    """Load hourly history (delivery dates before 2025-10-01) from spain-power-prices' hourly.parquet.

    Returns ``(days_loaded, skipped)`` where skipped lists dates that failed validation.
    """
    raw = pd.read_parquet(path, columns=["ts_utc", "price_es", "price_pt"])
    ts = pd.to_datetime(raw["ts_utc"], utc=True)
    df = raw.assign(ts_utc=ts, mtu_min=60, date=ts.dt.tz_convert(sm.TZ).dt.date)
    df = df[df["date"] < sm.MTU15_SINCE]
    frames, skipped = [], []
    for day, g in df.groupby("date", sort=True):
        g = g.sort_values("ts_utc").reset_index(drop=True)
        try:
            sm.validate(g, day)
        except sm.ValidationError as e:
            skipped.append(f"{day}: {e}")
            continue
        frames.append(day_rows(g, sm.SEED_SOURCE))
    if frames:
        upsert_periods(root, pd.concat(frames))
        rebuild_daily(root)
    return len(frames), skipped
