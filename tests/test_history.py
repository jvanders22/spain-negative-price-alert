import datetime as dt

import pandas as pd
from conftest import synthetic_day

from negprice import history
from negprice import summary as sm


def test_seed_from_parquet_loads_only_hourly_era(tmp_path):
    frames = [
        synthetic_day(d, [float(i % 30) - 5 for i in range(24)], mtu=60) for d in ("2025-09-29", "2025-09-30")
    ]
    frames.append(synthetic_day("2025-10-01", [50.0] * 24, mtu=60))  # after the MTU switch: ignored
    bad = synthetic_day("2025-09-28", [50.0] * 24, mtu=60).iloc[:-2]  # incomplete day: skipped
    pq = tmp_path / "hourly.parquet"
    pd.concat([*frames, bad])[["ts_utc", "price_es", "price_pt"]].to_parquet(pq)

    n, skipped = history.seed_from_parquet(tmp_path, pq)

    assert n == 2 and len(skipped) == 1 and skipped[0].startswith("2025-09-28")
    periods = history.read_periods(tmp_path)
    assert sorted(set(periods["date"])) == ["2025-09-29", "2025-09-30"]
    assert set(periods["source"]) == {sm.SEED_SOURCE} and set(periods["mtu_min"]) == {60}
    daily = history.read_daily(tmp_path)
    assert daily["n_le0"].tolist() == [6, 6]
    assert history.is_processed(tmp_path, dt.date(2025, 9, 30))
    assert not history.is_processed(tmp_path, dt.date(2025, 10, 1))


def test_upsert_replaces_day_and_formats_deterministically(tmp_path):
    day = synthetic_day("2026-10-10", [1.0] * 96)
    history.upsert_periods(tmp_path, history.day_rows(day, "marginalpdbc_20261010.1"))
    history.upsert_periods(tmp_path, history.day_rows(day.assign(price_es=-0.001), "marginalpdbc_20261010.2"))
    text = history.periods_path(tmp_path).read_bytes()
    assert b"\r" not in text
    lines = text.decode().splitlines()
    assert len(lines) == 97
    assert lines[1] == "2026-10-10,2026-10-09T22:00:00Z,15,0.00,1.00,marginalpdbc_20261010.2"
