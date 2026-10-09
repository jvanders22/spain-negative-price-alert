"""X post drafts (never posted automatically). Plain text, <= 280 characters by ``len()``."""

from __future__ import annotations

import datetime as dt

MAX_CHARS = 280
SOURCE = "Source: OMIE"
OPINIONS = [
    "Cheap power isn't scarce. Flexible demand is.",
    "The cheapest kWh is the one you can move to midday.",
    "Solar keeps pushing noon prices down. That's cannibalisation, one day at a time.",
    "A battery doesn't need cheap power. It needs the gap between noon and 9pm.",
    "My read: timing is worth more than the tariff headline.",
    "Zero-price hours aren't free power. They're a signal that nobody was ready to use it.",
]


def opinion(date: str) -> str:
    return OPINIONS[dt.date.fromisoformat(date).toordinal() % len(OPINIONS)]


def eur(x: float, dp: int = 0) -> str:
    return f"{'-' if x < 0 else ''}€{abs(x):.{dp}f}"


def _day_label(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d:%a} {d.day} {d:%b}"


def _fit(variants: list[list[str]]) -> str:
    """First variant (most to least detailed) that fits; blank lines between non-empty parts."""
    for parts in variants:
        text = "\n\n".join(p for p in parts if p)
        if len(text) <= MAX_CHARS:
            return text
    raise ValueError("no draft variant fits in 280 characters")  # pragma: no cover - last variant is short


def post_text(s: dict) -> str:
    unit = "quarter-hours" if s["mtu_min"] == 15 else "hours"
    year = int(s["date"][:4])
    when = f"{_day_label(s['date'])}:"
    evening = f" Evening: ~{eur(s['evening_mean'])}." if s["evening_mean"] is not None else ""
    if s["is_alert"]:
        wins = s["le0_windows"]
        lead = f"{when} Spain's day-ahead power hits €0 or below for {s['n_le0']} of {s['n_periods']} {unit}"
        low = f". Low: {eur(s['min_price'], 2)}/MWh."
        heads = [f"{lead}, {', '.join(wins)}{low}"]
        if len(wins) > 1:
            heads.append(f"{lead}, {wins[0]} +{len(wins) - 1} more{low}")
        heads.append(f"{lead}{low}")
        stats = f"Zero-price day #{s['ytd_zero_days']} of {year}"
        stats += f" ({s['ytd_zero_days_prev_year']} by this date in {year - 1})."
    else:
        c = s["cheapest_3h"]
        mean = eur(c["mean"], 2 if abs(c["mean"]) < 10 else 0)
        heads = [
            f"{when} Spain's day-ahead power stays above €0. "
            f"Cheapest 3h: {c['start']}–{c['end']} at ~{mean}/MWh."
        ]
        stats = f"Zero-price days in {year}: {s['ytd_zero_days']}"
        stats += f" so far ({s['ytd_zero_days_prev_year']} by this date in {year - 1})."
    op = opinion(s["date"])
    # Most to least detailed: keep the time window over the evening price, both over the opinion line.
    variants = [v for h in heads for v in ([h + evening, stats, op, SOURCE], [h, stats, op, SOURCE])]
    variants += [[heads[-1], stats, op, SOURCE], [heads[-1], stats, SOURCE], [heads[-1], SOURCE]]
    return _fit(variants)
