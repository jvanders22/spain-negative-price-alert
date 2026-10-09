import datetime as dt

import pandas as pd
import pytest
from conftest import load_day, synthetic_day

from negprice import summary as sm


def test_thresholds_exact():
    df = sm.classify(synthetic_day("2026-10-10", [0.0, -0.01, 10.0, 10.01] + [50.0] * 92))
    assert df["le0"].tolist()[:4] == [True, True, False, False]
    assert df["neg"].tolist()[:4] == [False, True, False, False]
    assert df["cheap"].tolist()[:4] == [True, True, True, False]


def test_windows_group_adjacent_periods():
    prices = [50.0] * 96
    for i in (48, 49, 50, 52):  # 12:00-12:45 contiguous, then 13:00 alone
        prices[i] = 0.0
    prices[95] = -1.0  # last period: window ends at 24:00
    df = synthetic_day("2026-10-10", prices)
    assert sm.windows(df, df["price_es"] <= 0, dt.date(2026, 10, 10)) == [
        "12:00–12:45",
        "13:00–13:15",
        "23:45–24:00",
    ]


def test_fall_back_repeated_hour_is_unambiguous():
    d = dt.date(2025, 10, 26)
    df = synthetic_day("2025-10-26", [50.0] * sm.expected_periods(d, 15))
    first = [pd.Timestamp("2025-10-26 00:00", tz="UTC") + pd.Timedelta(minutes=15 * i) for i in range(4)]
    assert sm.windows(df, df["ts_utc"].isin(first), d) == ["02:00 CEST–02:00 CET"]


@pytest.mark.parametrize(
    "day,flag_utc,expected",
    [
        # spring forward: 02:00 local doesn't exist; 00:45-01:00 UTC is 01:45 CET then 03:00 CEST
        ("2026-03-29", ["2026-03-29 00:45", "2026-03-29 01:00"], ["01:45–03:15"]),
        # fall back: 00:45 UTC is 02:45 CEST; 01:00 UTC is the second 02:00 (CET)
        ("2025-10-26", ["2025-10-26 00:45", "2025-10-26 01:00"], ["02:45 CEST–02:15 CET"]),
    ],
)
def test_dst_day_windows(day, flag_utc, expected):
    d = dt.date.fromisoformat(day)
    df = synthetic_day(day, [50.0] * sm.expected_periods(d, 15))
    mask = df["ts_utc"].isin([pd.Timestamp(t, tz="UTC") for t in flag_utc])
    assert sm.windows(df, mask, d) == expected


def test_cheapest_window():
    prices = [50.0] * 96
    prices[20:32] = [5.0] * 12  # 05:00-08:00, mean 5
    prices[60:72] = [5.0] * 11 + [4.0]  # 15:00-18:00, mean slightly lower
    c = sm.cheapest_window(synthetic_day("2026-10-10", prices))
    assert c == {"start": "15:00", "end": "18:00", "mean": 4.92}


def test_cheapest_window_dst_is_180_real_minutes():
    df = synthetic_day("2026-03-29", [50.0] * 4 + [1.0] * 12 + [50.0] * 76)  # 01:00 CET .. +3h
    assert sm.cheapest_window(df) == {"start": "01:00", "end": "05:00", "mean": 1.0}


def test_golden_day(golden):
    s = sm.summarise(golden, None, "marginalpdbc_20261010.1")
    assert (s["n_periods"], s["mtu_min"], s["n_le0"], s["n_neg"], s["min_price"]) == (96, 15, 18, 6, -0.01)
    assert s["is_alert"] and s["weekday"] == "Sat"
    assert s["le0_windows"] == ["12:30–17:00"]
    assert len(s["hourly"]) == 24
    assert s["source"]["version"] == 1


def test_hourly_lengths_on_dst_days():
    assert len(sm.summarise(load_day("20260329"))["hourly"]) == 23
    assert len(sm.summarise(load_day("20251026"))["hourly"]) == 25


def test_wrong_period_count_raises(golden):
    with pytest.raises(sm.ValidationError, match="expected 96"):
        sm.summarise(golden.iloc[:-1])


def test_duplicate_timestamp_raises(golden):
    bad = golden.copy()
    bad.loc[1, "ts_utc"] = bad.loc[0, "ts_utc"]
    with pytest.raises(sm.ValidationError, match="timestamps"):
        sm.validate(bad, dt.date(2026, 10, 10))


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 5000.0, -600.0])
def test_insane_price_raises(golden, price):
    bad = golden.copy()
    bad.loc[3, "price_es"] = price
    with pytest.raises(sm.ValidationError, match="price"):
        sm.validate(bad, dt.date(2026, 10, 10))


def test_hourly_data_after_mtu_switch_raises():
    with pytest.raises(sm.ValidationError, match="mtu"):
        sm.validate(synthetic_day("2026-10-10", [50.0] * 24, mtu=60), dt.date(2026, 10, 10))


def test_zero_day_counts():
    daily = pd.DataFrame(
        {
            "date": ["2025-03-01", "2025-06-01", "2025-06-02", "2025-12-31", "2026-02-01", "2026-06-02"],
            "n_le0": [3, 1, 0, 2, 5, 0],
        }
    )
    c = sm.zero_day_counts(dt.date(2026, 6, 1), True, daily)
    # 2026: 02-01 + the day itself; 2025 up to 06-01: 03-01, 06-01;
    # last 365 days (2025-06-02..2026-06-01): 12-31, 02-01, the day itself
    assert c == {"ytd_zero_days": 2, "ytd_zero_days_prev_year": 2, "zero_days_last_365": 3}
    # a stale history row for the day itself is ignored in favour of is_alert
    stale = pd.concat([daily, pd.DataFrame({"date": ["2026-06-01"], "n_le0": [4]})])
    assert sm.zero_day_counts(dt.date(2026, 6, 1), False, stale)["ytd_zero_days"] == 1
