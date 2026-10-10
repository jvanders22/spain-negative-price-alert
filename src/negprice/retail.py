"""Toll-aware retail price ESTIMATE per kWh from OMIE day-ahead prices. Pure functions, no I/O.

Formula, per kWh and per OMIE period (tariff period taken at the period start, Europe/Madrid)::

    energy     = (omie + adjustment_services + capacity_payment) / 1000 × (1 + loss_factor)
                 + retailer_margin / 1000                       [EUR/MWh inputs -> EUR/kWh]
    regulated  = peaje + cargo                                  [official tables, by tariff period]
    pre_tax    = energy + regulated
    iee        = pre_tax × IEE
    estimate   = (pre_tax + iee) × (1 + IVA)                    [IVA is charged on top of IEE]

Negative OMIE prices flow through and can make ``energy`` negative. Power terms (EUR/kW·year), meter
rental and the IEE per-MWh legal minimums are NOT included: they don't change with when you consume.
The market-side inputs are ASSUMPTIONS (see ``Assumptions``); the result is an estimate, not a bill.
PVPC is carried as a context column only (EUR/kWh before taxes, as published).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, fields

import pandas as pd

from . import summary as sm
from . import tariffs as tf

LABEL = "estimate"
DISCLAIMER = (
    "Estimate only, not a bill and not advice: peninsula, 2026 regulated tables, assumed losses, "
    "adjustment services and retailer margin; power term, meter rental and IEE minimums excluded."
)
FORMULA = (
    "energy = (omie + adjustment_services + capacity_payment) / 1000 × (1 + loss_factor) + "
    "retailer_margin / 1000; pre_tax = energy + peaje + cargo; iee = pre_tax × IEE; "
    "estimate = (pre_tax + iee) × (1 + IVA)"
)
PVPC_NOTE = (
    "PVPC = regulated 2.0TD price (ESIOS archive 70, field PCB) in €/kWh BEFORE taxes, as published; "
    "context only, not comparable 1:1 with the after-tax estimate."
)
WINDOW_MINUTES = (60, 180)
COLUMNS = [
    "ts_utc", "local_start", "local_hour", "mtu_min", "tariff", "period", "omie_eur_mwh",
    "energy_eur_kwh", "peaje_eur_kwh", "cargo_eur_kwh", "capacity_eur_kwh", "pre_tax_eur_kwh",
    "iee_eur_kwh", "iva_eur_kwh", "estimate_eur_kwh", "pvpc_eur_kwh",
]  # fmt: skip


@dataclass(frozen=True)
class Assumptions:
    """Configurable inputs. Every default carries a ``note`` saying where it comes from."""

    loss_factor: float = field(
        default=0.16,
        metadata={
            "note": "ASSUMPTION. Busbar energy is grossed up for network losses. Default from the median "
            "ratio PVPC PMH / OMIE hourly mean = 1.161 over the 36 hours with OMIE > 5 €/MWh on 9–10 Oct "
            "2026 (the ratio includes losses and small market adjustments; official hourly loss "
            "coefficients are on ESIOS and need a token)."
        },
    )
    adjustment_services_eur_mwh: float = field(
        default=28.0,
        metadata={
            "note": "ASSUMPTION. Ancillary/adjustment services plus OMIE/REE fees. Default from the median "
            "of PVPC SAH+FOM+FOS+INT over the 48 hours of 9–10 Oct 2026 (≈28; range 18.4–58.3 €/MWh)."
        },
    )
    retailer_margin_eur_mwh: float = field(
        default=10.0,
        metadata={"note": "ASSUMPTION. Illustrative retailer margin; real indexed contracts vary."},
    )
    iee: float = field(
        default=0.0511269632,
        metadata={
            "note": "Impuesto Especial sobre la Electricidad 5.11269632% (Ley 38/1992 art. 99.1; Agencia "
            "Tributaria note of 1 Sep 2026: the reduced 0.5% under RDL 18/2026 did not apply in Aug–Sep "
            "2026). The per-MWh legal minimums are ignored."
        },
    )
    iva: float = field(
        default=0.21,
        metadata={
            "note": "IVA 21% (general rate again from 1 Jun 2026, after the temporary 10% for ≤10 kW from "
            "22 Mar to 31 May 2026; AEAT IVA 2026 manual). The IVA base includes IEE (DGT V0764-26)."
        },
    )

    def describe(self) -> dict:
        """``{name: {value, note}}`` for reports."""
        return {f.name: {"value": getattr(self, f.name), "note": f.metadata["note"]} for f in fields(self)}


def r6(x) -> float | None:
    """Round to 6 dp (EUR/kWh); NaN -> None; never "-0.0"."""
    return None if x is None or pd.isna(x) else round(float(x), 6) + 0.0


def price_stack(omie_eur_mwh, peaje, cargo, capacity, a: Assumptions) -> dict:
    """The formula in the module docstring, for scalars or aligned Series. All outputs EUR/kWh."""
    energy = (omie_eur_mwh + a.adjustment_services_eur_mwh + capacity * 1000) / 1000 * (1 + a.loss_factor)
    energy = energy + a.retailer_margin_eur_mwh / 1000
    pre_tax = energy + (peaje + cargo)
    iee = pre_tax * a.iee
    iva = (pre_tax + iee) * a.iva
    return {
        "energy_eur_kwh": energy,
        "peaje_eur_kwh": peaje,
        "cargo_eur_kwh": cargo,
        "capacity_eur_kwh": capacity,
        "pre_tax_eur_kwh": pre_tax,
        "iee_eur_kwh": iee,
        "iva_eur_kwh": iva,
        "estimate_eur_kwh": pre_tax + iee + iva,
    }


def estimate(
    day_df: pd.DataFrame,
    tariff: str,
    assumptions: Assumptions | None = None,
    pvpc: pd.DataFrame | None = None,
    allow_other_year: bool = False,
) -> pd.DataFrame:
    """Estimated price paid per kWh for every period of a day frame (``COLUMNS``).

    ``capacity_eur_kwh`` is the official busbar value; it is already inside ``energy_eur_kwh`` (grossed up
    for losses), not an extra addend. ``pvpc`` is the frame from ``pvpc.parse`` (hourly, joined on the UTC
    hour so DST days line up); ``pvpc_eur_kwh`` is NaN when it is missing.
    """
    a = assumptions or Assumptions()
    df = day_df.sort_values("ts_utc").reset_index(drop=True)
    local = df["ts_utc"].dt.tz_convert(sm.TZ)
    tf.check_date(local.iloc[0].date(), allow_other_year)
    periods = [tf.period(tariff, t) for t in local]
    terms = pd.DataFrame([tf.energy_terms(tariff, p) for p in periods], columns=["peaje", "cargo", "cap"])
    out = pd.DataFrame(
        {
            "ts_utc": df["ts_utc"],
            "local_start": local.dt.strftime("%H:%M"),
            "local_hour": local.dt.hour,
            "mtu_min": df["mtu_min"].astype(int),
            "tariff": tariff,
            "period": periods,
            "omie_eur_mwh": df["price_es"].astype(float),
            **price_stack(df["price_es"].astype(float), terms["peaje"], terms["cargo"], terms["cap"], a),
        }
    )
    hour = out["ts_utc"].dt.floor("h")
    if pvpc is None or pvpc.empty:
        out["pvpc_eur_kwh"] = float("nan")
    else:
        out["pvpc_eur_kwh"] = hour.map(pvpc.set_index("ts_utc")["pcb_eur_mwh"] / 1000).astype(float)
    return out[COLUMNS]


def hourly(est_df: pd.DataFrame) -> pd.DataFrame:
    """Means per local clock hour (23/24/25 rows; grouped on the UTC hour, so the repeated 02:00 hour of
    the fall-back day gives two rows with ``local_hour`` 2)."""
    g = est_df.groupby(est_df["ts_utc"].dt.floor("h"), sort=True)
    out = g.agg(
        local_hour=("local_hour", "first"),
        tariff=("tariff", "first"),
        period=("period", "first"),
        omie_eur_mwh=("omie_eur_mwh", "mean"),
        estimate_eur_kwh=("estimate_eur_kwh", "mean"),
        pvpc_eur_kwh=("pvpc_eur_kwh", "mean"),
    ).reset_index()
    out.insert(1, "hour", out["ts_utc"].dt.tz_convert(sm.TZ).dt.strftime("%H:%M"))
    return out


def _day(est_df: pd.DataFrame) -> dt.date:
    return est_df["ts_utc"].iloc[0].tz_convert(sm.TZ).date()


def _best_start(est_df: pd.DataFrame, minutes: int, column: str) -> tuple[int, int]:
    """(index of the first period, number of periods) of the lowest-mean contiguous ``minutes`` span;
    earliest wins ties. Sums integers (1e-9 units) so ties are exact, like ``summary.cheapest_window``."""
    k = minutes // int(est_df["mtu_min"].iloc[0])
    if not 0 < k <= len(est_df):
        raise ValueError(f"cannot fit a {minutes}-minute window in {len(est_df)} periods")
    units = [round(v * 1e9) for v in est_df[column]]
    sums = [sum(units[i : i + k]) for i in range(len(units) - k + 1)]
    return sums.index(min(sums)), k


def _window(est_df: pd.DataFrame, i: int, k: int, minutes: int) -> dict:
    day, start = _day(est_df), est_df["ts_utc"].iloc[i]
    w = est_df.iloc[i : i + k]
    return {
        "start": sm._hhmm(start, day),
        "end": sm._hhmm(start + pd.Timedelta(minutes=minutes), day),
        "estimate_eur_kwh": r6(w["estimate_eur_kwh"].mean()),
        "omie_eur_mwh": sm.r2(w["omie_eur_mwh"].mean()),
        "regulated_eur_kwh": r6((w["peaje_eur_kwh"] + w["cargo_eur_kwh"]).mean()),
        "periods": sorted(set(w["period"])),
    }


def cheapest_window(est_df: pd.DataFrame, minutes: int = 180, column: str = "estimate_eur_kwh") -> dict:
    """Lowest-mean contiguous ``minutes`` span of ``column`` (real minutes in UTC, so DST-safe). Use
    ``column="omie_eur_mwh"`` for the wholesale window. Returns local start/end plus the window's mean
    estimate, mean OMIE price, mean peaje+cargo and tariff periods."""
    i, k = _best_start(est_df, minutes, column)
    return _window(est_df, i, k, minutes)


def compare_windows(est_df: pd.DataFrame, minutes: int) -> dict:
    """Cheapest estimated-retail window vs cheapest wholesale window of the same length."""
    retail = cheapest_window(est_df, minutes, "estimate_eur_kwh")
    wholesale = cheapest_window(est_df, minutes, "omie_eur_mwh")
    same = (retail["start"], retail["end"]) == (wholesale["start"], wholesale["end"])
    return {"minutes": minutes, "retail": retail, "wholesale": wholesale, "same_window": same}


def _periods_text(tariff: str, periods: list[str]) -> str:
    return "/".join(tf.period_name(tariff, p) for p in periods)


def takeaway(tariff: str, cmp: dict) -> str:
    """One plain-English line, built only from the numbers in ``compare_windows`` output."""
    r, w = cmp["retail"], cmp["wholesale"]
    span = f"{cmp['minutes'] // 60}h" if cmp["minutes"] % 60 == 0 else f"{cmp['minutes']} min"
    head = (
        f"Cheapest wholesale {span}: {w['start']}–{w['end']} ({w['omie_eur_mwh']:.2f} €/MWh); "
        f"cheapest estimated {tariff} {span}: {r['start']}–{r['end']} ({r['estimate_eur_kwh']:.3f} €/kWh)"
    )
    if cmp["same_window"]:
        return f"{head}: the same window, all in {_periods_text(tariff, r['periods'])}."
    cost = f"the wholesale window would cost an estimated {w['estimate_eur_kwh']:.3f} €/kWh"
    if w["regulated_eur_kwh"] > r["regulated_eur_kwh"]:
        return (
            f"{head} because {w['start']}–{w['end']} is {_periods_text(tariff, w['periods'])} "
            f"(peajes+cargos average {w['regulated_eur_kwh']:.4f} €/kWh vs {r['regulated_eur_kwh']:.4f} in "
            f"{_periods_text(tariff, r['periods'])}); {cost}."
        )
    return f"{head}; {cost}."


def _extreme(est_df: pd.DataFrame, i: int) -> dict:
    row = est_df.loc[i]
    return {
        "estimate_eur_kwh": r6(row["estimate_eur_kwh"]),
        "time": sm._hhmm(row["ts_utc"], _day(est_df)),
        "period": row["period"],
        "omie_eur_mwh": sm.r2(row["omie_eur_mwh"]),
    }


def hourly_records(est_df: pd.DataFrame) -> list[dict]:
    return [
        {
            "hour": h.hour,
            "local_hour": int(h.local_hour),
            "period": h.period,
            "omie_eur_mwh": sm.r2(h.omie_eur_mwh),
            "estimate_eur_kwh": r6(h.estimate_eur_kwh),
            "pvpc_eur_kwh": r6(h.pvpc_eur_kwh),
        }
        for h in hourly(est_df).itertuples()
    ]


def day_report(est_df: pd.DataFrame, assumptions: Assumptions | None = None) -> dict:
    """JSON-ready report for one day of ``estimate`` output."""
    a = assumptions or Assumptions()
    df = est_df.reset_index(drop=True)
    day, tariff = _day(df), str(df["tariff"].iloc[0])
    est = df["estimate_eur_kwh"]
    windows = {str(m): compare_windows(df, m) for m in WINDOW_MINUTES}
    warning = tf.check_date(day, allow_other_year=True)
    has_pvpc = bool(df["pvpc_eur_kwh"].notna().any())
    return {
        "label": LABEL,
        "disclaimer": DISCLAIMER,
        "tariff": tariff,
        "zone": "peninsula",
        "date": day.isoformat(),
        "weekday": f"{day:%a}",
        "day_type": tf.day_type(day),
        "season": tf.season_peninsula(day.month),
        "is_tariff_holiday": tf.is_tariff_holiday(day),
        "n_periods": len(df),
        "mtu_min": int(df["mtu_min"].iloc[0]),
        "units": {"estimate": "EUR/kWh, taxes included", "omie": "EUR/MWh", "pvpc": "EUR/kWh before taxes"},
        "mean_estimate_eur_kwh": r6(est.mean()),
        "min_estimate": _extreme(df, int(est.idxmin())),
        "max_estimate": _extreme(df, int(est.idxmax())),
        "cheapest_windows": windows,
        "takeaway": takeaway(tariff, windows["180"]),
        "pvpc": {
            "available": has_pvpc,
            "mean_eur_kwh": r6(df["pvpc_eur_kwh"].mean()) if has_pvpc else None,
            "note": PVPC_NOTE,
        },
        "hourly": hourly_records(df),
        "formula": FORMULA,
        "assumptions": a.describe(),
        "energy_terms_eur_kwh": tf.energy_table(tariff),
        "power_terms_eur_kw_year": tf.power_table(tariff),
        "power_terms_note": "Not in the per-kWh estimate: fixed per contracted kW, the same at any hour.",
        "sources": tf.SOURCES,
        "warnings": [warning] if warning else [],
    }
