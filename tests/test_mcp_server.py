import asyncio

import pandas as pd
import pytest
from conftest import synthetic_day

from negprice import history, mcp_server, retail


@pytest.fixture
def root(tmp_path):
    prices_a = [10.0 + i for i in range(96)]
    prices_b = [-5.0 if 40 <= i < 50 else 0.0 if i == 60 else 50.0 + i for i in range(96)]
    for day, prices in (("2026-10-10", prices_a), ("2026-10-11", prices_b)):
        history.upsert_periods(tmp_path, history.day_rows(synthetic_day(day, prices), "test"))
    return tmp_path


def test_unknown_and_malformed_date(root):
    with pytest.raises(ValueError, match="not in the stored history"):
        mcp_server.day_prices(root, "2026-01-01")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        mcp_server.cheapest_window(root, "20261010")


def test_bad_tariff_and_hours(root):
    with pytest.raises(ValueError, match="tariff"):
        mcp_server.cheapest_window(root, "2026-10-10", tariff="9.9TD")
    for hours in (0, -1, 25, 0.1, 0.249):
        with pytest.raises(ValueError, match="hours"):
            mcp_server.cheapest_window(root, "2026-10-10", hours=hours)


def test_latest_and_day_prices(root):
    assert mcp_server.latest_day(root)["date"] == "2026-10-11"
    out = mcp_server.day_prices(root, "2026-10-10")
    assert len(out["prices"]) == 96 and out["summary"]["min_price"] == 10.0


def test_cheapest_window_length_and_labels(root):
    out = mcp_server.cheapest_window(root, "2026-10-10", hours=2, tariff="3.0TD")
    assert out["label"] == "estimate" and out["disclaimer"] == retail.DISCLAIMER
    for key in ("omie_window", "retail_estimate_window"):
        w = out[key]
        start, end = (pd.Timedelta(f"{t}:00") for t in (w["start"], w["end"]))
        assert end - start == pd.Timedelta(hours=2)
    assert out["omie_window"]["start"] == "00:00"  # prices rise through the day


def test_zero_price_stats_matches_pandas(root):
    df = history.read_periods(root)
    le0 = df["price_es"] <= 0
    out = mcp_server.zero_price_stats(root)
    assert out["year"] == 2026
    assert out["days_with_zero_or_negative"] == df[le0]["date"].nunique() == 1
    assert out["periods_zero_or_negative"] == int(le0.sum()) == 11
    assert out["share_periods_zero_or_negative"] == round(le0.mean(), 4)
    with pytest.raises(ValueError, match="no data stored for 2020"):
        mcp_server.zero_price_stats(root, 2020)


def test_app_lists_four_tools(root):
    app = mcp_server.build_app(root)
    names = {t.name for t in asyncio.run(app.list_tools())}
    assert names == {"latest_day", "day_prices", "cheapest_window", "zero_price_stats"}
