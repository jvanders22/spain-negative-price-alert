"""Official 2026 access-tariff tables and period calendars (2.0TD, 3.0TD, 6.1TD; peninsula). Pure, no I/O.

Energy terms are EUR/kWh, power terms EUR/kW·year. Values were copied from the BOE texts listed in
``SOURCES`` and verified on 2026-10-10; the tables change every year, so dates outside
``VALID_FROM``..``VALID_TO`` raise ``TariffYearError`` unless the caller opts in with ``allow_other_year``.

Calendars follow CNMC Circular 3/2020 art. 7: periods are assigned by Europe/Madrid clock time at the START
of each period. Canarias, Baleares, Ceuta and Melilla have different calendars and are not covered.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

TZ = "Europe/Madrid"
TARIFFS = ("2.0TD", "3.0TD", "6.1TD")
VERIFIED = "2026-10-10"
VALID_FROM = dt.date(2026, 1, 1)
VALID_TO = dt.date(2026, 12, 31)

SOURCES = {
    "peajes": {
        "title": "CNMC, Resolución de 18 de diciembre de 2025: peajes de transporte y distribución 2026",
        "boe_id": "BOE-A-2025-26348",
        "boe": "BOE núm. 306, 22 dic 2025",
        "url": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-26348",
        "table": "Término de energía del peaje de transporte y distribución (€/kWh)",
        "verified": VERIFIED,
    },
    "cargos": {
        "title": "Orden TED/1524/2025, de 23 de diciembre: cargos del sistema eléctrico 2026",
        "boe_id": "BOE-A-2025-26705",
        "boe": "BOE núm. 312, 27 dic 2025",
        "url": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-26705",
        "table": "Término de energía de los cargos (euros/kWh)",
        "verified": VERIFIED,
    },
    "capacity": {
        "title": "Orden TED/1524/2025, de 23 de diciembre: pagos por capacidad 2026",
        "boe_id": "BOE-A-2025-26705",
        "boe": "BOE núm. 312, 27 dic 2025",
        "url": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-26705",
        "table": "Término de energía de los pagos por capacidad (euros/kWh), en b.c.",
        "verified": VERIFIED,
    },
    "calendar": {
        "title": "CNMC, Circular 3/2020, art. 7: periodos horarios de los peajes",
        "boe_id": "BOE-A-2020-1066",
        "boe": "BOE-A-2020-1066",
        "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2020-1066",
        "table": "Artículo 7. Periodos horarios (península)",
        "verified": VERIFIED,
    },
}

# Peajes de transporte y distribución 2026, BOE-A-2025-26348. Energy EUR/kWh by period P1..
PEAJE_ENERGY = {
    "2.0TD": (0.033261, 0.016409, 0.000077),
    "3.0TD": (0.027511, 0.012376, 0.004943, 0.002627, 0.000111, 0.000031),
    "6.1TD": (0.026785, 0.012281, 0.005133, 0.002780, 0.000120, 0.000029),
}
# Same resolution, power term EUR/kW·year. 2.0TD has two power periods (P1 punta+llano hours, P2 valle).
PEAJE_POWER = {
    "2.0TD": (23.324952, 0.443770),
    "3.0TD": (14.935084, 7.894323, 2.502996, 1.907795, 0.535313, 0.535313),
    "6.1TD": (23.946498, 12.687713, 4.747747, 3.339695, 0.070979, 0.062703),
}
# Cargos 2026, Orden TED/1524/2025 (BOE-A-2025-26705). Segment 1 = 2.0TD, 2 = 3.0TD, 3 = 6.1TD.
CARGO_ENERGY = {
    "2.0TD": (0.064292, 0.012858, 0.003215),
    "3.0TD": (0.035841, 0.026538, 0.014336, 0.007168, 0.004595, 0.002867),
    "6.1TD": (0.019489, 0.014436, 0.007795, 0.003898, 0.002499, 0.001559),
}
CARGO_POWER = {
    "2.0TD": (4.379461, 0.281653),
    "3.0TD": (5.441843, 2.723298, 1.978538, 1.978538, 1.978538, 0.906974),
    "6.1TD": (5.648870, 2.826996, 2.054134, 2.054134, 2.054134, 0.941478),
}
# Pagos por capacidad 2026, same Orden, EUR/kWh at busbar ("en b.c."). 2.0TD uses its own P1/P2/P3 hours.
CAPACITY_ENERGY = {
    "2.0TD": (0.000800, 0.000133, 0.0),
    "3.0TD": (0.001082, 0.000500, 0.000333, 0.000250, 0.000250, 0.0),
    "6.1TD": (0.000465, 0.000213, 0.000142, 0.000107, 0.000107, 0.0),
}

# Circular 3/2020 art. 7: "los días festivos de ámbito nacional, definidos como tales en el calendario
# oficial del año correspondiente, con exclusión tanto de los festivos sustituibles como de los que no
# tienen fecha fija", plus 6 January explicitly. So: fixed-date national holidays only. Good Friday (no
# fixed date) and Monday substitutes are normal days. (month, day):
TARIFF_HOLIDAYS = ((1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25))

SEASONS = {
    "alta": (1, 2, 7, 12),
    "media alta": (3, 11),
    "media": (6, 8, 9),
    "baja": (4, 5, 10),
}
_SEASON_DAY_TYPE = {"alta": "A", "media alta": "B", "media": "B1", "baja": "C"}
_FIRST_PERIOD = {"A": 1, "B": 2, "B1": 3, "C": 4}  # weekday peak-hours period of 3.0TD/6.1TD
PERIOD_NAMES_20TD = {"P1": "punta", "P2": "llano", "P3": "valle"}


class TariffYearError(ValueError):
    """The date is outside the year the tables in this module were published for."""


def check_date(day: dt.date, allow_other_year: bool = False) -> str | None:
    """None when ``day`` is covered by the tables; otherwise raise, or return a warning if allowed."""
    if VALID_FROM <= day <= VALID_TO:
        return None
    msg = (
        f"{day} is outside {VALID_FROM}..{VALID_TO}: peajes, cargos and capacity payments are the 2026 "
        "tables and change every year"
    )
    if not allow_other_year:
        raise TariffYearError(f"{msg}; pass allow_other_year=True to estimate with the 2026 tables anyway")
    return f"WARNING: {msg}; this estimate applies the 2026 tables to another year."


def _check_tariff(tariff: str) -> None:
    if tariff not in TARIFFS:
        raise ValueError(f"unknown tariff {tariff!r}; expected one of {', '.join(TARIFFS)}")


def is_tariff_holiday(day: dt.date) -> bool:
    return (day.month, day.day) in TARIFF_HOLIDAYS


def season_peninsula(month: int) -> str:
    """'alta' | 'media alta' | 'media' | 'baja' (3.0TD/6.1TD seasons, peninsula)."""
    for name, months in SEASONS.items():
        if month in months:
            return name
    raise ValueError(f"month must be 1..12, got {month}")


def day_type(day: dt.date) -> str:
    """'A' | 'B' | 'B1' | 'C' for non-holiday weekdays by season; 'D' for Sat, Sun and tariff holidays."""
    if day.weekday() >= 5 or is_tariff_holiday(day):
        return "D"
    return _SEASON_DAY_TYPE[season_peninsula(day.month)]


def _local(ts) -> pd.Timestamp:
    """Europe/Madrid wall-clock time. Naive inputs are taken as already local."""
    t = pd.Timestamp(ts)
    return t if t.tzinfo is None else t.tz_convert(TZ)


def period_20td(ts_local) -> str:
    """2.0TD period of the period starting at ``ts_local``: 'P1' punta, 'P2' llano, 'P3' valle."""
    t = _local(ts_local)
    if day_type(t.date()) == "D" or t.hour < 8:
        return "P3"
    return "P1" if 10 <= t.hour < 14 or 18 <= t.hour < 22 else "P2"


def period_30td(ts_local) -> str:
    """3.0TD / 6.1TD period ('P1'..'P6') of the period starting at ``ts_local``."""
    t = _local(ts_local)
    kind = day_type(t.date())
    if kind == "D" or t.hour < 8:
        return "P6"
    peak = 9 <= t.hour < 14 or 18 <= t.hour < 22
    return f"P{_FIRST_PERIOD[kind] + (0 if peak else 1)}"


def period(tariff: str, ts_local) -> str:
    _check_tariff(tariff)
    return period_20td(ts_local) if tariff == "2.0TD" else period_30td(ts_local)


def period_name(tariff: str, p: str) -> str:
    """'P1 (punta)' for 2.0TD, plain 'P4' for the six-period tariffs."""
    return f"{p} ({PERIOD_NAMES_20TD[p]})" if tariff == "2.0TD" else p


def energy_terms(tariff: str, p: str) -> tuple[float, float, float]:
    """(peaje, cargo, capacity payment at busbar) in EUR/kWh for a tariff period."""
    _check_tariff(tariff)
    i = int(p[1:]) - 1
    return PEAJE_ENERGY[tariff][i], CARGO_ENERGY[tariff][i], CAPACITY_ENERGY[tariff][i]


def energy_table(tariff: str) -> list[dict]:
    """Per-period energy terms (EUR/kWh) for docs and pages."""
    _check_tariff(tariff)
    rows = []
    for i in range(len(PEAJE_ENERGY[tariff])):
        peaje, cargo, cap = energy_terms(tariff, f"P{i + 1}")
        rows.append(
            {
                "period": f"P{i + 1}",
                "peaje_eur_kwh": peaje,
                "cargo_eur_kwh": cargo,
                "regulated_eur_kwh": round(peaje + cargo, 6),
                "capacity_eur_kwh": cap,
            }
        )
    return rows


def power_table(tariff: str) -> list[dict]:
    """Per-period power terms (EUR/kW·year). Not part of the per-kWh estimate: timing doesn't change them."""
    _check_tariff(tariff)
    return [
        {
            "period": f"P{i + 1}",
            "peaje_eur_kw_year": peaje,
            "cargo_eur_kw_year": cargo,
            "total_eur_kw_year": round(peaje + cargo, 6),
        }
        for i, (peaje, cargo) in enumerate(zip(PEAJE_POWER[tariff], CARGO_POWER[tariff], strict=True))
    ]
