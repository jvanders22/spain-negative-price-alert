"""Classify a delivery day's prices and summarise it. Pure functions, no I/O.

A day frame has one row per period: ``ts_utc`` (tz-aware UTC period start), ``price_es``, ``price_pt``,
``mtu_min``. All period arithmetic happens in UTC; local Europe/Madrid time is only used for display
and for the midday/evening buckets, so DST days (23/25 hours) need no special cases.
"""

from __future__ import annotations

import datetime as dt
import math
import re

import pandas as pd

from .omie import URL

TZ = "Europe/Madrid"
CHEAP_EUR_MWH = 10.0
PRICE_MIN, PRICE_MAX = -500.0, 4000.0
MTU15_SINCE = dt.date(2025, 10, 1)
MIDDAY_HOURS = range(10, 18)  # 10:00-17:59 local
EVENING_HOURS = range(19, 23)  # 19:00-22:59 local


class ValidationError(ValueError):
    pass


def r2(x: float | None) -> float | None:
    """Round to 2 dp; ``+ 0.0`` turns -0.0 into 0.0 so JSON never shows "-0.0"."""
    return None if x is None or pd.isna(x) else round(float(x), 2) + 0.0


def day_bounds_utc(day: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(day).tz_localize(TZ).tz_convert("UTC")
    end = pd.Timestamp(day + dt.timedelta(days=1)).tz_localize(TZ).tz_convert("UTC")
    return start, end


def expected_mtu(day: dt.date) -> int:
    return 15 if day >= MTU15_SINCE else 60


def expected_periods(day: dt.date, mtu_min: int) -> int:
    start, end = day_bounds_utc(day)
    return int((end - start) / pd.Timedelta(minutes=mtu_min))


def validate(df: pd.DataFrame, day: dt.date) -> None:
    """Raise ``ValidationError`` unless ``df`` is a complete, sane day of prices for ``day``."""
    if df.empty:
        raise ValidationError(f"{day}: no periods")
    mtus = set(df["mtu_min"])
    mtu = expected_mtu(day)
    if mtus != {mtu}:
        raise ValidationError(f"{day}: expected mtu {mtu} min, got {sorted(mtus)}")
    n = expected_periods(day, mtu)
    if len(df) != n:
        raise ValidationError(f"{day}: expected {n} periods of {mtu} min, got {len(df)}")
    ts = df["ts_utc"].reset_index(drop=True)
    start, _ = day_bounds_utc(day)
    expected = pd.Series(pd.date_range(start, periods=n, freq=f"{mtu}min"))
    if not ts.is_unique or not ts.is_monotonic_increasing or not (ts == expected).all():
        raise ValidationError(f"{day}: timestamps are not the {n} consecutive {mtu}-min periods of the day")
    prices = df[["price_es", "price_pt"]].to_numpy(dtype=float)
    if not all(math.isfinite(p) and PRICE_MIN <= p <= PRICE_MAX for p in prices.ravel()):
        raise ValidationError(f"{day}: price missing or outside {PRICE_MIN}..{PRICE_MAX} EUR/MWh")


def classify(df: pd.DataFrame) -> pd.DataFrame:
    p = df["price_es"]
    return df.assign(le0=p <= 0, neg=p < 0, cheap=p <= CHEAP_EUR_MWH)


def _hhmm(ts: pd.Timestamp, day: dt.date) -> str:
    local = ts.tz_convert(TZ)
    if local.date() > day:
        return "24:00"
    # On the autumn fall-back day 02:00-02:59 happens twice; tag those times CEST/CET so they're unambiguous.
    if local.hour == 2 and expected_periods(day, 60) == 25:
        return f"{local:%H:%M} {local:%Z}"
    return f"{local:%H:%M}"


def windows(df: pd.DataFrame, mask: pd.Series, day: dt.date) -> list[str]:
    """Contiguous runs of flagged periods as local "HH:MM–HH:MM" (end exclusive)."""
    step = pd.Timedelta(minutes=int(df["mtu_min"].iloc[0]))
    out, start, prev = [], None, None
    for ts, flag in zip(df["ts_utc"], mask, strict=True):
        if flag and start is None:
            start = ts
        elif not flag and start is not None:
            out.append(f"{_hhmm(start, day)}–{_hhmm(prev + step, day)}")
            start = None
        prev = ts
    if start is not None:
        out.append(f"{_hhmm(start, day)}–{_hhmm(prev + step, day)}")
    return out


def cheapest_window_sum(df: pd.DataFrame, minutes: int = 180) -> tuple[int, int, int]:
    """``(first index, number of periods, sum in integer cents)`` of the lowest-mean contiguous ``minutes``
    span (earliest on ties). Integer cents, so ties and comparisons between days are exact."""
    mtu = int(df["mtu_min"].iloc[0])
    k = minutes // mtu
    cents = [round(p * 100) for p in df["price_es"]]
    sums = [sum(cents[i : i + k]) for i in range(len(cents) - k + 1)]
    i = sums.index(min(sums))
    return i, k, sums[i]


def cheapest_window(df: pd.DataFrame, minutes: int = 180) -> dict:
    """Lowest-mean contiguous ``minutes`` span (earliest on ties). Sums integer cents so ties are exact."""
    i, k, total = cheapest_window_sum(df, minutes)
    day = df["ts_utc"].iloc[0].tz_convert(TZ).date()
    start = df["ts_utc"].iloc[i]
    return {
        "start": _hhmm(start, day),
        "end": _hhmm(start + pd.Timedelta(minutes=minutes), day),
        "mean": r2(total / k / 100),
    }


def hourly(df: pd.DataFrame) -> list[dict]:
    """Mean price per clock hour, labelled in local time (23/24/25 entries)."""
    g = df.groupby(df["ts_utc"].dt.floor("h"))["price_es"].mean()
    return [{"hour": f"{ts.tz_convert(TZ):%H:%M}", "mean": r2(v)} for ts, v in g.items()]


SEED_SOURCE = "seed:spain-power-prices hourly.parquet"
_OMIE_FILE = re.compile(r"marginalpdbc_(\d{8})\.(\d+)$")


def source_info(source: str) -> dict:
    """``{file, version, url}`` from a periods.csv ``source`` value (an OMIE file name or a seed tag)."""
    m = _OMIE_FILE.search(source)
    if not m:
        return {"file": source, "version": None, "url": None}
    day = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
    return {"file": m.group(0), "version": int(m.group(2)), "url": URL.format(d=day, v=int(m.group(2)))}


def _same_date_prev_year(day: dt.date) -> dt.date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year - 1, day=28)


def zero_day_counts(day: dt.date, is_alert: bool, history_daily: pd.DataFrame) -> dict:
    """YTD / same-period-last-year / last-365-days zero-price day counts. ``day`` itself is counted from
    ``is_alert`` (history rows for ``day`` are ignored) so the result doesn't depend on upsert order."""
    if history_daily is None or history_daily.empty:
        dates, alerts = [], []
    else:
        dates = [dt.date.fromisoformat(str(d)) for d in history_daily["date"]]
        alerts = [int(n) > 0 for n in history_daily["n_le0"]]
    zero = {d for d, a in zip(dates, alerts, strict=True) if a and d != day} | ({day} if is_alert else set())
    prev = _same_date_prev_year(day)

    def count(lo: dt.date, hi: dt.date) -> int:
        return sum(lo <= d <= hi for d in zero)

    return {
        "ytd_zero_days": count(dt.date(day.year, 1, 1), day),
        "ytd_zero_days_prev_year": count(dt.date(prev.year, 1, 1), prev),
        "zero_days_last_365": count(day - dt.timedelta(days=364), day),
    }


def summarise(day_df: pd.DataFrame, history_daily: pd.DataFrame | None = None, source: str = "") -> dict:
    df = day_df.sort_values("ts_utc").reset_index(drop=True)
    day = df["ts_utc"].iloc[0].tz_convert(TZ).date()
    validate(df, day)
    df = classify(df)
    local_hour = df["ts_utc"].dt.tz_convert(TZ).dt.hour
    p = df["price_es"]
    imin, imax = int(p.idxmin()), int(p.idxmax())
    midday = r2(p[local_hour.isin(MIDDAY_HOURS)].mean())
    evening = r2(p[local_hour.isin(EVENING_HOURS)].mean())
    n_le0 = int(df["le0"].sum())
    return {
        "date": day.isoformat(),
        "weekday": f"{day:%a}",
        "n_periods": len(df),
        "mtu_min": int(df["mtu_min"].iloc[0]),
        "n_le0": n_le0,
        "n_neg": int(df["neg"].sum()),
        "n_cheap": int(df["cheap"].sum()),
        "cheap_threshold": CHEAP_EUR_MWH,
        "le0_windows": windows(df, df["le0"], day),
        "neg_windows": windows(df, df["neg"], day),
        "cheap_windows": windows(df, df["cheap"], day),
        "min_price": r2(p[imin]),
        "min_time": _hhmm(df["ts_utc"][imin], day),
        "max_price": r2(p[imax]),
        "max_time": _hhmm(df["ts_utc"][imax], day),
        "mean_price": r2(p.mean()),
        "midday_mean": midday,
        "evening_mean": evening,
        "evening_minus_midday": r2(evening - midday),
        "cheapest_3h": cheapest_window(df, 180),
        "is_alert": n_le0 > 0,
        **zero_day_counts(day, n_le0 > 0, history_daily),
        "hourly": hourly(df),
        "source": source_info(source),
    }


def from_omie(parsed: pd.DataFrame) -> pd.DataFrame:
    """Parser output (local ``ts``) -> day frame (``ts_utc``)."""
    out = parsed.assign(ts_utc=parsed["ts"].dt.tz_convert("UTC"))
    return out[["ts_utc", "price_es", "price_pt", "mtu_min"]]
