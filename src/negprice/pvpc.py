"""PVPC (regulated retail tariff) hourly prices from the public ESIOS archive 70. No token needed.

GET https://api.esios.ree.es/archives/70/download_json?locale=es&date=YYYY-MM-DD (200 verified 2026-10-10)
returns ``{"PVPC": [{"Dia": "dd/mm/yyyy", "Hora": "HH-HH", "PCB": "x,yy", ...}, ...]}`` with Spanish decimal
commas, one row per local hour (23/25 on DST days; the fall-back day lists "02-03" twice). Rows are mapped
to UTC by position, so no hour label is ever parsed as a clock time. Before publication (~20:15 Madrid on
D-1) the body is ``{"message": "No values for specified archive"}``.

PCB is the 2.0TD peninsula price in EUR/MWh BEFORE taxes. It is used as a context column only.
The raw response can be cached under ``data/pvpc/<date>.json`` so site rebuilds stay offline.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from . import summary as sm
from .http import NotFound, get_text

URL = "https://api.esios.ree.es/archives/70/download_json"
# ESIOS field -> column. All EUR/MWh. PCB = total price; TEU = peajes + cargos energy term; PMH = day-ahead
# + intraday market cost; SAH = adjustment services; FOM/FOS = market/system operator fees;
# INT = interruptibility.
FIELDS = {
    "PCB": "pcb_eur_mwh",
    "TEUPCB": "teu_eur_mwh",
    "PMHPCB": "pmh_eur_mwh",
    "SAHPCB": "sah_eur_mwh",
    "FOMPCB": "fom_eur_mwh",
    "FOSPCB": "fos_eur_mwh",
    "INTPCB": "int_eur_mwh",
}
COLS = ["ts_utc", "local_hour", *FIELDS.values()]


def _num(s: str | None) -> float:
    """Spanish number ("1.234,56") -> float; missing/blank -> NaN."""
    if s is None or not str(s).strip():
        return float("nan")
    return float(str(s).strip().replace(".", "").replace(",", "."))


def parse(text: str, day: dt.date) -> pd.DataFrame | None:
    """Raw archive JSON -> one row per local hour of ``day`` (``COLS``), or None if not published yet."""
    doc = json.loads(text)
    rows = doc.get("PVPC") if isinstance(doc, dict) else None
    if not rows:
        if isinstance(doc, dict) and "message" in doc:
            return None
        raise ValueError(f"unexpected PVPC response for {day}: {text[:80]!r}")
    days = {r.get("Dia") for r in rows}
    if days != {f"{day:%d/%m/%Y}"}:
        raise ValueError(f"PVPC response for {day} contains days {sorted(map(str, days))}")
    n = sm.expected_periods(day, 60)
    if len(rows) != n:
        raise ValueError(f"PVPC for {day}: expected {n} hourly rows, got {len(rows)}")
    start, _ = sm.day_bounds_utc(day)
    ts = pd.date_range(start, periods=n, freq="60min")
    out = {"ts_utc": ts, "local_hour": ts.tz_convert(sm.TZ).hour}
    for key, col in FIELDS.items():
        out[col] = [_num(r.get(key)) for r in rows]
    return pd.DataFrame(out)[COLS]


def fetch_text(day: dt.date) -> str | None:
    """Raw archive response for ``day``; None on 404."""
    try:
        return get_text(URL, params={"locale": "es", "date": day.isoformat()}, retries=2)
    except NotFound:
        return None


def cache_path(root: Path, day: dt.date) -> Path:
    return Path(root) / "data" / "pvpc" / f"{day}.json"


def load_cached(root: Path, day: dt.date) -> pd.DataFrame | None:
    """Parsed PVPC from the cache; None when the day was never cached. Never touches the network."""
    path = cache_path(root, day)
    return parse(path.read_text(encoding="utf-8"), day) if path.exists() else None


def load(root: Path, day: dt.date, fetch: bool = True) -> pd.DataFrame | None:
    """Cached PVPC for ``day``; otherwise (if ``fetch``) download it, caching the raw response only when it
    holds values. None when it is not published yet."""
    cached = load_cached(root, day)
    if cached is not None or not fetch:
        return cached
    return fetch_and_cache(root, day)


def fetch_and_cache(root: Path, day: dt.date) -> pd.DataFrame | None:
    """Download PVPC for ``day`` and cache the raw response when it holds values (replacing any cache
    file). None when it is not published yet."""
    text = fetch_text(day)
    df = parse(text, day) if text else None
    if df is not None:
        path = cache_path(root, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return df
