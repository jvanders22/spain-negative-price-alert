"""Weekly (Mon–Sun, Europe/Madrid delivery dates) summary facts. Pure functions, no I/O.

Five headline numbers, all from the stored history: periods at or below €0, the lowest price, the
midday vs evening spread, the cheapest 3-hour windows (estimated 2.0TD and wholesale) and the YTD count of
zero-price days. Buckets and thresholds are the daily ones in ``summary``; the 2.0TD figure comes from
``retail`` with default assumptions and is an estimate.
"""

from __future__ import annotations

import datetime as dt
from fractions import Fraction

import pandas as pd

from . import retail
from . import summary as sm
from . import tariffs as tf

TARIFF = "2.0TD"
WINDOW_MINUTES = 180
WINDOW_NOTE = (
    "Cheapest contiguous 180-minute windows are searched within each delivery day (no window spans "
    "midnight); the week's figure is the lowest of the seven daily windows, compared before rounding, "
    "earliest day on ties."
)
SPREAD_NOTE = (
    "Mean price of all periods of the week starting 10:00–17:59 (midday) and 19:00–22:59 (evening), "
    "Europe/Madrid; spread = evening − midday, computed from the unrounded means and then rounded."
)


def check_sunday(day: dt.date) -> None:
    if day.weekday() != 6:
        raise ValueError(f"{day} is a {day:%A}, not a Sunday: a week runs Monday to Sunday")


def week_dates(sunday: dt.date) -> list[dt.date]:
    """The seven delivery dates Mon..Sun of the week ending ``sunday``."""
    check_sunday(sunday)
    return [sunday - dt.timedelta(days=6 - i) for i in range(7)]


def is_complete(stored: set[dt.date], sunday: dt.date) -> bool:
    return all(d in stored for d in week_dates(sunday))


def latest_complete_sunday(stored: set[dt.date], today: dt.date) -> dt.date | None:
    """Sunday of the most recent Mon–Sun week that ends on or before ``today`` and has all seven days in
    ``stored``; None when there is no such week."""
    if not stored:
        return None
    sunday = today - dt.timedelta(days=(today.weekday() + 1) % 7)
    while sunday >= min(stored):
        if is_complete(stored, sunday):
            return sunday
        sunday -= dt.timedelta(days=7)
    return None


def day_label(day: dt.date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def week_days(periods: pd.DataFrame, sunday: dt.date) -> list[tuple[dt.date, pd.DataFrame, str]] | None:
    """``(date, validated day frame, source)`` for Mon..Sun, or None if any of the seven days is missing."""
    out = []
    for day in week_dates(sunday):
        df = periods[periods["date"] == day.isoformat()].sort_values("ts_utc").reset_index(drop=True)
        if df.empty:
            return None
        sm.validate(df, day)
        out.append((day, df, str(df["source"].iloc[0]) if "source" in df else ""))
    return out


def zero_or_below(days: list) -> dict:
    by_day = [
        {
            "date": day.isoformat(),
            "weekday": f"{day:%a}",
            "n_periods": len(df),
            "n_le0": int((df["price_es"] <= 0).sum()),
            "n_neg": int((df["price_es"] < 0).sum()),
        }
        for day, df, _ in days
    ]
    return {
        "n_le0": sum(d["n_le0"] for d in by_day),
        "n_neg": sum(d["n_neg"] for d in by_day),
        "n_periods": sum(d["n_periods"] for d in by_day),
        "days_with_le0": sum(d["n_le0"] > 0 for d in by_day),
        "days": len(by_day),
        "by_day": by_day,
    }


def lowest(days: list) -> dict:
    """Lowest price of the week; the earliest period wins ties (``n_periods_at_min`` says how many tie)."""
    best = None
    for day, df, _ in days:
        i = int(df["price_es"].idxmin())
        if best is None or df["price_es"][i] < best[1]["price_es"][best[2]]:
            best = (day, df, i)
    day, df, i = best
    price = df["price_es"][i]
    return {
        "price": sm.r2(price),
        "date": day.isoformat(),
        "weekday": f"{day:%a}",
        "time": sm._hhmm(df["ts_utc"][i], day),
        "n_periods_at_min": sum(int((d["price_es"] == price).sum()) for _, d, _ in days),
    }


def _raw_means(days: list) -> tuple[float, float]:
    """Unrounded (midday, evening) mean prices of the week."""
    week = pd.concat([df for _, df, _ in days], ignore_index=True)
    local_hour = week["ts_utc"].dt.tz_convert(sm.TZ).dt.hour
    midday = float(week["price_es"][local_hour.isin(sm.MIDDAY_HOURS)].mean())
    evening = float(week["price_es"][local_hour.isin(sm.EVENING_HOURS)].mean())
    return midday, evening


def midday_evening(days: list) -> dict:
    """Only the published values are rounded: the spread is the rounded difference of the raw means, so it
    can differ by 0.01 from ``evening_mean - midday_mean``."""
    midday, evening = _raw_means(days)
    return {
        "midday_mean": sm.r2(midday),
        "evening_mean": sm.r2(evening),
        "spread": sm.r2(evening - midday),
        "definition": SPREAD_NOTE,
    }


def _wholesale_window(days: list) -> dict:
    """Days are compared on the exact window mean (integer cents / periods); only the winner is rounded."""
    best = None
    for day, df, _ in days:
        _, k, cents = sm.cheapest_window_sum(df, WINDOW_MINUTES)
        mean = Fraction(cents, k)
        if best is None or mean < best[0]:
            best = (mean, day, df)
    _, day, df = best
    w = sm.cheapest_window(df, WINDOW_MINUTES)
    return {
        "date": day.isoformat(),
        "weekday": f"{day:%a}",
        "start": w["start"],
        "end": w["end"],
        "omie_eur_mwh": w["mean"],
    }


def _retail_window(days: list) -> dict:
    head = {"label": retail.LABEL, "tariff": TARIFF}
    try:
        for day, _, _ in days:
            tf.check_date(day)
    except tf.TariffYearError:
        reason = f"tariff tables cover {tf.VALID_FROM}..{tf.VALID_TO} only"
        return {**head, "available": False, "reason": reason}
    a = retail.Assumptions()
    column = "estimate_eur_kwh"
    best = None
    for day, df, _ in days:
        est = retail.estimate(df, TARIFF, a)
        i, k = retail._best_start(est, WINDOW_MINUTES, column)
        # exact mean in the 1e-9 units of ``retail._best_start``: compared unrounded, earliest day on ties
        mean = Fraction(sum(round(v * 1e9) for v in est[column].iloc[i : i + k]), k)
        if best is None or mean < best[0]:
            best = (mean, day, est)
    _, day, est = best
    w = retail.cheapest_window(est, WINDOW_MINUTES, column)
    return {
        **head,
        "available": True,
        "date": day.isoformat(),
        "weekday": f"{day:%a}",
        "start": w["start"],
        "end": w["end"],
        "estimate_eur_kwh": w["estimate_eur_kwh"],
        "omie_eur_mwh": w["omie_eur_mwh"],
        "periods": w["periods"],
        "period_names": [tf.period_name(TARIFF, p) for p in w["periods"]],
        "units": "EUR/kWh, taxes included",
        "disclaimer": retail.DISCLAIMER,
        "assumptions": {k: v["value"] for k, v in a.describe().items()},
    }


def cheapest_3h(days: list) -> dict:
    return {
        "minutes": WINDOW_MINUTES,
        "note": WINDOW_NOTE,
        "retail": _retail_window(days),
        "wholesale": _wholesale_window(days),
    }


def zero_days_ytd(sunday: dt.date, sunday_is_alert: bool, daily: pd.DataFrame) -> dict:
    c = sm.zero_day_counts(sunday, sunday_is_alert, daily)
    prev = sm._same_date_prev_year(sunday)
    return {
        "as_of": sunday.isoformat(),
        "year": sunday.year,
        "ytd_zero_days": c["ytd_zero_days"],
        "prev_year": prev.year,
        "prev_year_as_of": prev.isoformat(),
        "ytd_zero_days_prev_year": c["ytd_zero_days_prev_year"],
    }


def week_over_week(days: list, previous: list | None, previous_sunday: dt.date) -> dict | None:
    """Change in the zero-or-below counts and the spread against the previous week; None if that week is
    not complete in history. The spread change is the rounded difference of the unrounded spreads."""
    if previous is None:
        return None
    zero, z, s = zero_or_below(days), zero_or_below(previous), midday_evening(previous)
    (midday, evening), (midday_prev, evening_prev) = _raw_means(days), _raw_means(previous)
    return {
        "previous_week_ending": previous_sunday.isoformat(),
        "n_le0_prev": z["n_le0"],
        "n_le0_change": zero["n_le0"] - z["n_le0"],
        "n_neg_prev": z["n_neg"],
        "n_neg_change": zero["n_neg"] - z["n_neg"],
        "days_with_le0_prev": z["days_with_le0"],
        "days_with_le0_change": zero["days_with_le0"] - z["days_with_le0"],
        "spread_prev": s["spread"],
        "spread_change": sm.r2((evening - midday) - (evening_prev - midday_prev)),
    }


def facts(periods: pd.DataFrame, daily: pd.DataFrame, sunday: dt.date) -> dict:
    """JSON-ready facts for the week ending ``sunday``. ``periods``/``daily`` are the history frames."""
    days = week_days(periods, sunday)
    if days is None:
        raise ValueError(f"week ending {sunday} is not complete in history (need all 7 days Mon–Sun)")
    monday, previous_sunday = days[0][0], sunday - dt.timedelta(days=7)
    zero, spread = zero_or_below(days), midday_evening(days)
    mtus = sorted({int(m) for _, df, _ in days for m in df["mtu_min"].unique()})
    return {
        "week": {
            "start": monday.isoformat(),
            "end": sunday.isoformat(),
            "label": f"{day_label(monday)} – {day_label(sunday)}",
            "dates": [d.isoformat() for d, _, _ in days],
            "n_periods": zero["n_periods"],
            "mtu_min": mtus[0] if len(mtus) == 1 else None,
        },
        "zero_or_below": zero,
        "lowest": lowest(days),
        "midday_evening": spread,
        "cheapest_3h": cheapest_3h(days),
        "zero_days_ytd": zero_days_ytd(sunday, zero["by_day"][-1]["n_le0"] > 0, daily),
        "week_over_week": week_over_week(days, week_days(periods, previous_sunday), previous_sunday),
        "sources": {
            "omie": [{"date": d.isoformat(), **sm.source_info(src)} for d, _, src in days],
            "tariffs": tf.SOURCES,
        },
    }
