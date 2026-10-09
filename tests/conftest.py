from pathlib import Path

import pandas as pd
import pytest

from negprice import omie
from negprice import summary as sm

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_path(day: str) -> Path:
    return FIXTURES / f"marginalpdbc_{day}.1"


def load_day(day: str) -> pd.DataFrame:
    return sm.from_omie(omie.parse_marginalpdbc(fixture_path(day).read_text()))


def synthetic_day(day: str, prices: list[float], mtu: int = 15) -> pd.DataFrame:
    start, _ = sm.day_bounds_utc(pd.Timestamp(day).date())
    ts = pd.date_range(start, periods=len(prices), freq=f"{mtu}min")
    return pd.DataFrame({"ts_utc": ts, "price_es": prices, "price_pt": prices, "mtu_min": mtu})


@pytest.fixture
def golden() -> pd.DataFrame:
    return load_day("20261010")
