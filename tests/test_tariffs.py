import datetime as dt

import pandas as pd
import pytest

from negprice import tariffs as tf

FRI = "2026-10-09"  # weekday, baja season -> day type C


def test_sources_cite_boe_and_verification_date():
    assert tf.VALID_FROM == dt.date(2026, 1, 1)
    assert {s["boe_id"] for s in tf.SOURCES.values()} == {
        "BOE-A-2025-26348",
        "BOE-A-2025-26705",
        "BOE-A-2020-1066",
    }
    for s in tf.SOURCES.values():
        assert s["url"].startswith("https://www.boe.es/") and s["table"] and s["verified"] == "2026-10-10"


def test_tables_have_one_value_per_period():
    for t, n in (("2.0TD", 3), ("3.0TD", 6), ("6.1TD", 6)):
        assert len(tf.PEAJE_ENERGY[t]) == len(tf.CARGO_ENERGY[t]) == len(tf.CAPACITY_ENERGY[t]) == n
        assert len(tf.PEAJE_POWER[t]) == len(tf.CARGO_POWER[t]) == (2 if t == "2.0TD" else 6)
    assert tf.energy_terms("2.0TD", "P1") == (0.033261, 0.064292, 0.0008)
    assert tf.energy_terms("3.0TD", "P6") == (0.000031, 0.002867, 0.0)
    assert tf.energy_terms("6.1TD", "P3") == (0.005133, 0.007795, 0.000142)
    assert tf.power_table("2.0TD")[0]["total_eur_kw_year"] == pytest.approx(27.704413)
    with pytest.raises(ValueError, match="unknown tariff"):
        tf.energy_terms("6.2TD", "P1")


@pytest.mark.parametrize(
    "month,season",
    list(enumerate(["alta", "alta", "media alta", "baja", "baja", "media", "alta", "media", "media", "baja",
                    "media alta", "alta"], start=1)),
)  # fmt: skip
def test_season_for_every_month(month, season):
    assert tf.season_peninsula(month) == season


@pytest.mark.parametrize(
    "day,kind",
    [
        ("2026-01-14", "A"), ("2026-03-11", "B"), ("2026-06-10", "B1"), (FRI, "C"),
        ("2026-10-10", "D"), ("2026-10-11", "D"),  # Sat, Sun
        ("2026-10-12", "D"),  # Mon, Fiesta Nacional
        ("2026-01-06", "D"),  # Tue, 6 January
        ("2026-05-01", "D"), ("2026-12-08", "D"), ("2026-12-25", "D"),
        ("2026-04-03", "C"),  # Good Friday: no fixed date, so a normal weekday
        ("2026-04-06", "C"),  # Easter Monday
    ],
)  # fmt: skip
def test_day_type(day, kind):
    assert tf.day_type(dt.date.fromisoformat(day)) == kind


def test_holiday_list_is_fixed_dates_only():
    assert tf.is_tariff_holiday(dt.date(2026, 10, 12)) and tf.is_tariff_holiday(dt.date(2026, 1, 6))
    assert not tf.is_tariff_holiday(dt.date(2026, 4, 3))
    assert len(tf.TARIFF_HOLIDAYS) == 9


BOUNDARIES = [
    "07:59",
    "08:00",
    "09:00",
    "10:00",
    "13:59",
    "14:00",
    "17:59",
    "18:00",
    "21:59",
    "22:00",
    "23:59",
]


@pytest.mark.parametrize(
    "func,day,expected",
    [
        (tf.period_20td, FRI, ["P3", "P2", "P2", "P1", "P1", "P2", "P2", "P1", "P1", "P2", "P2"]),
        (tf.period_30td, "2026-01-14", ["P6", "P2", "P1", "P1", "P1", "P2", "P2", "P1", "P1", "P2", "P2"]),
        (tf.period_30td, "2026-03-11", ["P6", "P3", "P2", "P2", "P2", "P3", "P3", "P2", "P2", "P3", "P3"]),
        (tf.period_30td, "2026-06-10", ["P6", "P4", "P3", "P3", "P3", "P4", "P4", "P3", "P3", "P4", "P4"]),
        (tf.period_30td, FRI, ["P6", "P5", "P4", "P4", "P4", "P5", "P5", "P4", "P4", "P5", "P5"]),
    ],
)
def test_hour_boundaries(func, day, expected):
    assert [func(pd.Timestamp(f"{day} {t}")) for t in BOUNDARIES] == expected
    assert func(pd.Timestamp(f"{day} 00:00")) == func(pd.Timestamp(f"{day} 07:45"))


@pytest.mark.parametrize("day", ["2026-10-10", "2026-10-11", "2026-10-12", "2026-01-06"])
def test_weekends_and_holidays_are_all_valle(day):
    hours = pd.date_range(day, periods=96, freq="15min")
    assert {tf.period_20td(t) for t in hours} == {"P3"}
    assert {tf.period_30td(t) for t in hours} == {"P6"}


def test_good_friday_is_a_normal_weekday():
    assert tf.period_20td(pd.Timestamp("2026-04-03 12:00")) == "P1"
    assert tf.period_30td(pd.Timestamp("2026-04-03 12:00")) == "P4"


def test_periods_use_madrid_local_time():
    # Wed 1 Jul 2026 08:00 UTC is 10:00 CEST: punta. Read as a naive local 08:00 it would be llano.
    assert tf.period_20td(pd.Timestamp("2026-07-01 08:00", tz="UTC")) == "P1"
    assert tf.period_20td(pd.Timestamp("2026-07-01 08:00")) == "P2"
    assert tf.period_30td(pd.Timestamp("2026-07-01 07:00", tz="UTC")) == "P1"  # 09:00 local, alta
    # Wed 14 Jan 2026 08:30 UTC is 09:30 CET: llano for 2.0TD.
    assert tf.period_20td(pd.Timestamp("2026-01-14 08:30", tz="UTC")) == "P2"
    # The local date decides the day type: Fri 22:30 UTC is already Saturday in Madrid.
    assert tf.period_20td(pd.Timestamp("2026-10-09 21:30", tz="UTC")) == "P2"
    assert tf.period_20td(pd.Timestamp("2026-10-09 22:30", tz="UTC")) == "P3"
    assert tf.period("6.1TD", pd.Timestamp("2026-10-09 12:00")) == "P4"


def test_year_guard():
    assert tf.check_date(dt.date(2026, 12, 31)) is None
    for day in (dt.date(2025, 12, 31), dt.date(2027, 1, 1)):
        with pytest.raises(tf.TariffYearError, match="2026"):
            tf.check_date(day)
        assert tf.check_date(day, allow_other_year=True).startswith("WARNING")
