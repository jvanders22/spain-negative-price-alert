import datetime as dt

import pandas as pd
import pytest
import requests
from conftest import fixture_path

from negprice import http, omie


@pytest.mark.parametrize(
    "day,n,mtu,first_utc,last_utc",
    [
        ("20261010", 96, 15, "2026-10-09 22:00", "2026-10-10 21:45"),
        ("20260329", 92, 15, "2026-03-28 23:00", "2026-03-29 21:45"),
        ("20251026", 100, 15, "2025-10-25 22:00", "2025-10-26 22:45"),
        ("20250101", 24, 60, "2024-12-31 23:00", "2025-01-01 22:00"),
    ],
)
def test_parse_period_counts_and_utc(day, n, mtu, first_utc, last_utc):
    df = omie.parse_marginalpdbc(fixture_path(day).read_text())
    utc = df["ts"].dt.tz_convert("UTC")
    assert len(df) == n
    assert set(df["mtu_min"]) == {mtu}
    assert utc.iloc[0] == pd.Timestamp(first_utc, tz="UTC")
    assert utc.iloc[-1] == pd.Timestamp(last_utc, tz="UTC")
    assert utc.diff().dropna().eq(pd.Timedelta(minutes=mtu)).all()


class FakeResponse:
    def __init__(self, status: int, text: str = "", url: str = "u"):
        self.status_code, self.text, self.url = status, text, url


@pytest.fixture
def serve(monkeypatch):
    """Map version suffix -> (status, body); record requested versions."""
    calls = []

    def install(responses: dict[int, tuple[int, str]]):
        def fake_get(url, **kw):
            v = int(url.rsplit(".", 1)[1])
            calls.append(v)
            return FakeResponse(*responses.get(v, (404, "")), url=url)

        monkeypatch.setattr(http.requests, "get", fake_get)
        monkeypatch.setattr(http.time, "sleep", lambda s: None)
        return calls

    return install


DAY = dt.date(2026, 10, 10)


def test_version_fallback(serve):
    calls = serve({2: (200, fixture_path("20261010").read_text())})
    text, version = omie.fetch_text(DAY)
    assert version == 2 and calls == [1, 2]
    assert len(omie.parse_marginalpdbc(text)) == 96


def test_all_404_is_not_published(serve):
    calls = serve({})
    with pytest.raises(omie.NotPublished):
        omie.fetch_text(DAY)
    assert calls == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    "body", ["<html><body>Mantenimiento</body></html>", "MARGINALPDBC;\n2026;10;10;1;x;y;\n"]
)
def test_html_or_malformed_200_is_an_error_not_pending(serve, body):
    serve({1: (200, body)})
    with pytest.raises(Exception) as e:
        omie.fetch_text(DAY)
    assert not isinstance(e.value, omie.NotPublished)


def test_wrong_date_in_file_is_an_error(serve):
    serve({1: (200, fixture_path("20251026").read_text())})
    with pytest.raises(ValueError, match="contains dates"):
        omie.fetch_text(DAY)


def test_server_error_is_not_pending(serve):
    serve({1: (503, "busy")})
    with pytest.raises(RuntimeError) as e:
        omie.fetch_text(DAY)
    assert not isinstance(e.value, omie.NotPublished)


def test_connection_error_is_not_pending(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(http.requests, "get", boom)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as e:
        omie.fetch_text(DAY)
    assert not isinstance(e.value, omie.NotPublished)


def test_latest_prefers_corrected_version(serve):
    body = fixture_path("20261010").read_text()
    calls = serve({1: (200, body), 2: (200, body)})
    assert omie.fetch_text(DAY)[1] == 1 and calls == [1]
    calls.clear()
    assert omie.fetch_text(DAY, latest=True)[1] == 2 and calls == [1, 2, 3]
