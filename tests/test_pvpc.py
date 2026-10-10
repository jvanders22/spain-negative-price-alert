import datetime as dt
import json

import pytest
from conftest import pvpc_text

from negprice import pvpc
from negprice import tariffs as tf

NO_VALUES = '{"message":"No values for specified archive"}'


def reshaped(day: dt.date, hours: list[int]) -> str:
    """The 9 Oct fixture rewritten as another day with the given local-hour labels; PCB = row position."""
    base = json.loads(pvpc_text("20261009"))["PVPC"][0]
    rows = [
        {**base, "Dia": f"{day:%d/%m/%Y}", "Hora": f"{h:02d}-{h + 1:02d}", "PCB": f"{i},50"}
        for i, h in enumerate(hours)
    ]
    return json.dumps({"PVPC": rows})


def test_parse_24_rows_and_decimal_comma():
    df = pvpc.parse(pvpc_text("20261009"), dt.date(2026, 10, 9))
    assert list(df.columns) == pvpc.COLS and len(df) == 24
    assert list(df["local_hour"]) == list(range(24))
    assert df["pcb_eur_mwh"][0] == 195.09 and df["pmh_eur_mwh"][0] == 182.91
    assert str(df["ts_utc"][0]) == "2026-10-08 22:00:00+00:00"
    assert pvpc._num("1.234,56") == 1234.56 and pvpc._num("-0,76") == -0.76


def test_parse_23_row_day():
    day = dt.date(2026, 3, 29)
    df = pvpc.parse(reshaped(day, [0, 1, *range(3, 24)]), day)
    assert len(df) == 23 and list(df["local_hour"]) == [0, 1, *range(3, 24)]
    assert df["ts_utc"].diff().dropna().nunique() == 1


def test_parse_25_row_day_maps_repeated_hour_by_position():
    day = dt.date(2026, 10, 25)
    df = pvpc.parse(reshaped(day, [0, 1, 2, 2, *range(3, 24)]), day)
    assert len(df) == 25 and list(df["local_hour"][:5]) == [0, 1, 2, 2, 3]
    assert list(df["pcb_eur_mwh"][2:4]) == [2.5, 3.5]
    assert [str(t) for t in df["ts_utc"][2:4]] == ["2026-10-25 00:00:00+00:00", "2026-10-25 01:00:00+00:00"]


def test_no_values_is_none_and_bad_input_raises():
    day = dt.date(2026, 10, 11)
    assert pvpc.parse(NO_VALUES, day) is None
    with pytest.raises(ValueError, match="contains days"):
        pvpc.parse(pvpc_text("20261009"), day)
    with pytest.raises(ValueError, match="expected 23 hourly rows"):
        pvpc.parse(reshaped(dt.date(2026, 3, 29), list(range(24))), dt.date(2026, 3, 29))
    with pytest.raises(ValueError, match="unexpected"):
        pvpc.parse("{}", day)


def test_load_caches_only_published_days(tmp_path, monkeypatch):
    day = dt.date(2026, 10, 10)
    monkeypatch.setattr(pvpc, "fetch_text", lambda d: NO_VALUES)
    assert pvpc.load(tmp_path, day) is None and not pvpc.cache_path(tmp_path, day).exists()
    monkeypatch.setattr(pvpc, "fetch_text", lambda d: pvpc_text("20261010"))
    assert pvpc.load(tmp_path, day, fetch=False) is None
    assert len(pvpc.load(tmp_path, day)) == 24
    assert pvpc.cache_path(tmp_path, day).read_text() == pvpc_text("20261010")
    monkeypatch.setattr(pvpc, "fetch_text", lambda d: pytest.fail("network used"))
    assert len(pvpc.load(tmp_path, day)) == 24


@pytest.mark.parametrize("day", ["20261009", "20261010"])
def test_teu_cross_check(day):
    """ESIOS TEUPCB (peajes + cargos energy term, €/MWh) must equal our 2.0TD tables hour by hour."""
    date = dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))
    df = pvpc.parse(pvpc_text(day), date)
    ours = []
    for ts in df["ts_utc"]:
        peaje, cargo, _ = tf.energy_terms("2.0TD", tf.period_20td(ts))
        ours.append(round((peaje + cargo) * 1000, 2))
    assert ours == list(df["teu_eur_mwh"])
    expected = {"20261009": [3.29] * 8 + [29.27] * 2 + [97.55] * 4 + [29.27] * 4 + [97.55] * 4 + [29.27] * 2,
                "20261010": [3.29] * 24}  # fmt: skip
    assert ours == expected[day]
