"""Read-only MCP server (stdio) over the stored OMIE history. No network, no writes.

The tool functions are plain functions of ``root`` returning JSON-able dicts; ``build_app`` registers them
with FastMCP (``pip install -e '.[mcp]'``). Bad input raises ``ValueError`` with a clear message.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from . import history, retail
from . import summary as sm
from . import tariffs as tf

SUMMARY_KEYS = (
    "date", "weekday", "n_periods", "mtu_min", "n_le0", "n_neg", "le0_windows", "neg_windows",
    "min_price", "min_time", "max_price", "max_time", "mean_price", "cheapest_3h",
)  # fmt: skip
UNITS = "OMIE prices in EUR/MWh; times are local Europe/Madrid"


def _day_df(root: Path, date: str) -> pd.DataFrame:
    try:
        ok = dt.date.fromisoformat(date).isoformat() == date
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    periods = history.read_periods(root)
    df = periods[periods["date"] == date]
    if df.empty:
        stored = periods["date"]
        span = f"; stored: {stored.min()} to {stored.max()}" if len(stored) else ""
        raise ValueError(f"{date} is not in the stored history{span}")
    return df.sort_values("ts_utc").reset_index(drop=True)


def _summary(day_df: pd.DataFrame) -> dict:
    s = sm.summarise(day_df, None, str(day_df["source"].iloc[0]))
    return {k: s[k] for k in SUMMARY_KEYS}


def latest_day(root: Path) -> dict:
    """Summary of the newest stored delivery day."""
    dates = history.read_periods(root)["date"]
    if dates.empty:
        raise ValueError("no history stored")
    return {"units": UNITS, **_summary(_day_df(root, dates.max()))}


def day_prices(root: Path, date: str) -> dict:
    """Per-period OMIE prices (EUR/MWh) for one stored day, plus its summary."""
    df = _day_df(root, date)
    local = df["ts_utc"].dt.tz_convert(sm.TZ)
    prices = [
        {"ts_utc": f"{t:%Y-%m-%dT%H:%M:%SZ}", "local_start": f"{lt:%H:%M}", "price_es": sm.r2(p)}
        for t, lt, p in zip(df["ts_utc"], local, df["price_es"], strict=True)
    ]
    return {"units": UNITS, "summary": _summary(df), "prices": prices}


def cheapest_window(root: Path, date: str, hours: float = 3, tariff: str = "2.0TD") -> dict:
    """Cheapest contiguous window by OMIE price and by estimated retail price (an ESTIMATE, not a bill)."""
    if tariff not in tf.TARIFFS:
        raise ValueError(f"tariff must be one of {', '.join(tf.TARIFFS)}, got {tariff!r}")
    if not 0 < hours <= 24:
        raise ValueError(f"hours must be > 0 and <= 24, got {hours}")
    df = _day_df(root, date)
    minutes, mtu = round(hours * 60), int(df["mtu_min"].iloc[0])
    if abs(hours * 60 - minutes) > 1e-6 or minutes % mtu or minutes > len(df) * mtu:
        raise ValueError(f"hours must be a multiple of {mtu} minutes and fit in the day, got {hours}")
    est = retail.estimate(df, tariff, retail.Assumptions())
    cmp = retail.compare_windows(est, minutes)
    return {
        "date": date,
        "hours": hours,
        "tariff": tariff,
        "omie_window": cmp["wholesale"],
        "retail_estimate_window": cmp["retail"],
        "same_window": cmp["same_window"],
        "takeaway": retail.takeaway(tariff, cmp),
        "label": retail.LABEL,
        "units": "retail estimate in EUR/kWh with taxes; OMIE in EUR/MWh; times local Europe/Madrid",
        "disclaimer": retail.DISCLAIMER,
    }


def zero_price_stats(root: Path, year: int | None = None) -> dict:
    """Days with any period <= 0 EUR/MWh and the share of periods <= 0, for a year (default: latest)."""
    periods = history.read_periods(root)
    if periods.empty:
        raise ValueError("no history stored")
    years = periods["date"].str[:4].astype(int)
    year = int(years.max()) if year is None else year
    df = periods[years == year]
    if df.empty:
        raise ValueError(f"no data stored for {year}; years stored: {sorted(set(years))}")
    le0 = df["price_es"] <= 0
    n_days = df["date"].nunique()
    return {
        "year": year,
        "days_stored": n_days,
        "days_with_zero_or_negative": int(le0.groupby(df["date"]).any().sum()),
        "periods_stored": len(df),
        "periods_zero_or_negative": int(le0.sum()),
        "share_periods_zero_or_negative": round(float(le0.mean()), 4),
    }


def build_app(root: Path):
    from mcp.server.fastmcp import FastMCP

    root = Path(root)
    app = FastMCP("negprice")

    @app.tool(name="latest_day")
    def latest_day_tool() -> dict:
        """Summary of the newest stored Spanish day-ahead (OMIE) delivery day."""
        return latest_day(root)

    @app.tool(name="day_prices")
    def day_prices_tool(date: str) -> dict:
        """Per-period OMIE prices (EUR/MWh) for a stored day (YYYY-MM-DD) plus its summary."""
        return day_prices(root, date)

    @app.tool(name="cheapest_window")
    def cheapest_window_tool(date: str, hours: float = 3, tariff: str = "2.0TD") -> dict:
        """Cheapest contiguous window by OMIE price and by estimated retail price (estimate, not a bill)."""
        return cheapest_window(root, date, hours, tariff)

    @app.tool(name="zero_price_stats")
    def zero_price_stats_tool(year: int | None = None) -> dict:
        """Days with any zero/negative period and share of such periods, for a year (default: latest)."""
        return zero_price_stats(root, year)

    return app
