# Vendored from jvanders22/spain-power-prices (src/spain_power/omie.py @ 3910b0b), MIT, same author.
# Copied rather than installed so this repo has no private-repo dependency. Local changes are noted inline.
"""OMIE day-ahead marginal prices for Spain (public daily files, no token).

Files: https://www.omie.es/es/file-download?parents%5B0%5D=marginalpdbc&filename=marginalpdbc_YYYYMMDD.1
Format: ``YYYY;MM;DD;period;price_PT;price_ES;`` with a ``MARGINALPDBC;`` header and ``*`` footer.
Until 30 Sep 2025 the market used 24 (23/25 on DST days) hourly periods; from 1 Oct 2025 the
day-ahead market moved to a 15-minute MTU (96 periods, 92/100 on DST days).
"""

from __future__ import annotations

import datetime as dt
from io import StringIO

import pandas as pd

from .http import NotFound, get_text

TZ = "Europe/Madrid"
URL = "https://www.omie.es/es/file-download?parents%5B0%5D=marginalpdbc&filename=marginalpdbc_{d:%Y%m%d}.{v}"


def parse_marginalpdbc(text: str) -> pd.DataFrame:
    """Parse one marginalpdbc file into a tidy frame with tz-aware period starts.

    Returns columns: ``ts`` (period start, Europe/Madrid), ``price_es``, ``price_pt``, ``mtu_min``.
    """
    rows = []
    for line in StringIO(text):
        parts = line.strip().rstrip(";").split(";")
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        y, m, d, period = (int(p) for p in parts[:4])
        rows.append((dt.date(y, m, d), period, float(parts[4]), float(parts[5])))
    if not rows:
        raise ValueError("no price rows found in OMIE file")
    df = pd.DataFrame(rows, columns=["date", "period", "price_pt", "price_es"])
    out = []
    for day, g in df.groupby("date"):
        n = int(g["period"].max())
        mtu = 15 if n > 25 else 60
        start = pd.Timestamp(day).tz_localize(TZ).tz_convert("UTC")
        ts = start + pd.to_timedelta((g["period"] - 1) * mtu, unit="min")
        out.append(g.assign(ts=ts.dt.tz_convert(TZ), mtu_min=mtu))
    res = pd.concat(out, ignore_index=True)
    return res[["ts", "price_es", "price_pt", "mtu_min"]]


class NotPublished(RuntimeError):  # local change
    """Every version of the day's file returned 404: OMIE has not published it yet."""


def fetch_text(day: dt.date, max_version: int = 5, latest: bool = False) -> tuple[str, int]:
    """Fetch a day's raw file and the version used. OMIE republishes corrected files as
    ``.2``, ``.3``...; when ``.1`` is gone, try the next versions.

    local change: raises ``NotPublished`` only when every version is a 404; other HTTP failures raise
    ``RuntimeError`` and a 200 body without price rows for ``day`` raises ``ValueError``.
    local change: ``latest=True`` (used by ``run --force``) keeps probing after a hit and returns the highest
    published version, so a corrected ``.2`` wins over a stale ``.1``.
    """
    errors: list[str] = []
    found: tuple[str, int] | None = None
    for v in range(1, max_version + 1):
        try:
            text = get_text(URL.format(d=day, v=v), retries=1 if v > 1 else 3)
        except NotFound:
            if found:
                break
            continue
        except RuntimeError as e:
            errors.append(f".{v}: {e}")
            continue
        dates = set(parse_marginalpdbc(text)["ts"].dt.date)
        if dates != {day}:
            raise ValueError(f"OMIE file .{v} for {day} contains dates {sorted(dates)}")
        found = (text, v)
        if not latest:
            return found
    if found:
        return found
    if errors:
        raise RuntimeError(f"failed to fetch OMIE marginalpdbc for {day}: {'; '.join(errors)}")
    raise NotPublished(f"OMIE marginalpdbc for {day} not published yet (404 for .1-.{max_version})")


def fetch_day(day: dt.date, max_version: int = 5) -> pd.DataFrame:
    """Fetch and parse a day's file (see ``fetch_text``)."""
    return parse_marginalpdbc(fetch_text(day, max_version)[0])  # local change: via fetch_text


def to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Average sub-hourly periods to clock hours (UTC-indexed, so DST is unambiguous)."""
    utc = df["ts"].dt.tz_convert("UTC").dt.floor("h")
    h = df.groupby(utc)[["price_es", "price_pt"]].mean()
    h.index.name = "ts_utc"
    return h.reset_index()
