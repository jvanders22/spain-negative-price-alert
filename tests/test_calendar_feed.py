# ruff: noqa: E501
import datetime as dt
import json

import icalendar
import pandas as pd
import pytest
from ical.calendar_stream import IcsCalendarStream

from negprice import calendar_feed as cf
from negprice import summary as sm


def periods_for(days: dict[str, list[float]]) -> pd.DataFrame:
    rows = []
    for day, prices in days.items():
        d = dt.date.fromisoformat(day)
        start, end = sm.day_bounds_utc(d)
        ts = pd.date_range(start, end, freq="15min", inclusive="left")
        assert len(ts) == len(prices), (day, len(ts))
        for t, p in zip(ts, prices, strict=False):
            rows.append(
                {"date": day, "ts_utc": t, "mtu_min": 15, "price_es": p, "price_pt": p + 1, "source": "t"}
            )
    return pd.DataFrame(rows)


def day_prices(day: str, free_local: list[tuple[str, str]], base: float = 50.0) -> list[float]:
    """Prices for a Madrid day; 0.0 inside each local [start, end) window, ``base`` elsewhere."""
    d = dt.date.fromisoformat(day)
    start, end = sm.day_bounds_utc(d)
    ts = pd.date_range(start, end, freq="15min", inclusive="left").tz_convert("Europe/Madrid")
    out = []
    for t in ts:
        hm = t.strftime("%H:%M")
        out.append(0.0 if any(a <= hm < b for a, b in free_local) else base)
    return out


def read(root, slug="es"):
    return (root / "site" / "calendar" / f"{slug}.ics").read_bytes()


def events(raw: bytes):
    cal = icalendar.Calendar.from_ical(raw)
    return {str(e["UID"]): e for e in cal.walk("VEVENT")}


DAYS = {"2026-10-14": [("13:00", "16:00")], "2026-10-15": [("12:00", "14:00"), ("15:00", "15:30")]}


@pytest.fixture
def built(tmp_path):
    p = periods_for({d: day_prices(d, w) for d, w in DAYS.items()})
    cf.build(tmp_path, p)
    return tmp_path, p


def test_rfc5545_valid_with_two_parsers(built):
    root, _ = built
    for f in cf.FEEDS:
        raw = read(root, f.slug)
        assert raw.endswith(b"\r\n") and b"\n" not in raw.replace(b"\r\n", b"")
        assert all(len(line) <= 75 for line in raw.split(b"\r\n"))
        icalendar.Calendar.from_ical(raw)  # tolerant parser
        cal = IcsCalendarStream.calendar_from_ics(raw.decode())  # strict (Home Assistant) parser
        assert all(ev.dtstart.tzinfo is not None for ev in cal.events)
    text = read(root).decode()
    for header in (
        "VERSION:2.0",
        "PRODID:",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
        "X-WR-TIMEZONE:Europe/Madrid",
        "METHOD:PUBLISH",
    ):
        assert header in text


def test_titles_and_pvpc_line(built):
    root, _ = built
    evs = events(read(root))
    titles = sorted(str(e["SUMMARY"]) for e in evs.values())
    assert titles == [
        "Free power 12:00-14:00 (confirmed)",
        "Free power 13:00-16:00 (confirmed)",
        "Free power 15:00-15:30 (confirmed)",
    ]
    assert all("2.0TD" in str(e["DESCRIPTION"]) for e in evs.values())
    pt = events(read(root, "pt"))
    assert not any("PVPC" in str(e["DESCRIPTION"]) for e in pt.values())
    # PT is UTC+1 in Oct: 13:00 Madrid = 12:00 Lisbon, and PT prices are 1.0 so free PT feed is empty
    assert pt == {}
    cheap_pt = events(read(root, "pt-cheap"))
    assert "Cheap power ≤ €10 12:00-15:00 (confirmed)" in {str(e["SUMMARY"]) for e in cheap_pt.values()}


def test_uids_stable_and_byte_identical_rerun(built):
    root, p = built
    before = {f.slug: read(root, f.slug) for f in cf.FEEDS}
    state = (root / "site/calendar/state.json").read_text()
    cf.build(root, p)
    assert {f.slug: read(root, f.slug) for f in cf.FEEDS} == before
    assert (root / "site/calendar/state.json").read_text() == state
    assert "free-es-20261014-1100Z@jvanders22.github.io" in events(before["es"])


def test_new_day_keeps_old_uids_and_sequence(built):
    root, p = built
    old = events(read(root))
    p2 = pd.concat([p, periods_for({"2026-10-16": day_prices("2026-10-16", [("14:00", "15:00")])})])
    cf.build(root, p2)
    new = events(read(root))
    assert set(old) < set(new)
    for uid, ev in old.items():
        assert new[uid]["SEQUENCE"] == ev["SEQUENCE"] == 0
        assert new[uid]["DTSTAMP"].dt == ev["DTSTAMP"].dt


def test_disappearing_window_is_cancelled_with_bumped_sequence(built):
    root, p = built
    uid = "free-es-20261015-1300Z@jvanders22.github.io"  # 15:00-15:30 Madrid
    assert uid in events(read(root))
    p2 = p.copy()
    p2.loc[p2["price_es"] == 0.0, "price_es"] = 0.0
    mask = (p2["date"] == "2026-10-15") & (p2["ts_utc"] >= pd.Timestamp("2026-10-15T13:00Z"))
    p2.loc[mask, "price_es"] = 50.0
    cf.build(root, p2)
    ev = events(read(root))[uid]
    assert str(ev["STATUS"]) == "CANCELLED" and ev["SEQUENCE"] == 1
    assert str(ev["SUMMARY"]).startswith("Cancelled: ")
    # stays cancelled, no further bumps on rerun
    cf.build(root, p2)
    assert events(read(root))[uid]["SEQUENCE"] == 1
    # comes back -> confirmed, sequence bumps again
    cf.build(root, p)
    ev = events(read(root))[uid]
    assert str(ev["STATUS"]) == "CONFIRMED" and ev["SEQUENCE"] == 2


def test_changed_content_bumps_sequence(built, monkeypatch):
    root, p = built
    uid = "free-es-20261015-1000Z@jvanders22.github.io"
    hours = pd.date_range("2026-10-14T22:00Z", periods=24, freq="h")
    df = pd.DataFrame({"ts_utc": hours, "local_hour": range(24), "pcb_eur_mwh": 80.0})
    monkeypatch.setattr(
        cf.pvpc, "load", lambda root, day, fetch=False: df if day == dt.date(2026, 10, 15) else None
    )
    cf.build(root, p)
    ev = events(read(root))[uid]
    assert ev["SEQUENCE"] == 1 and "about 8.0 c/kWh" in str(ev["DESCRIPTION"])


def test_dst_end_25_oct_2026(tmp_path):
    # 25 Oct 2026 has 25 local hours (100 quarter-hours); 02:00-03:00 happens twice.
    day = "2026-10-25"
    start, end = sm.day_bounds_utc(dt.date.fromisoformat(day))
    assert (end - start) == pd.Timedelta(hours=25)
    ts = pd.date_range(start, end, freq="15min", inclusive="left")
    # free from 00:00Z (02:00 CEST, before the fold) to 03:00Z (04:00 CET, after the fold) = 3 real hours
    prices = [
        0.0 if t < pd.Timestamp("2026-10-25T03:00Z") and t >= pd.Timestamp("2026-10-25T00:00Z") else 40.0
        for t in ts
    ]
    # and a midday window, which is in CET (UTC+1)
    prices = [
        0.0 if pd.Timestamp("2026-10-25T12:00Z") <= t < pd.Timestamp("2026-10-25T14:00Z") else x
        for t, x in zip(ts, prices, strict=False)
    ]
    cf.build(tmp_path, periods_for({day: prices}))
    evs = events(read(tmp_path))
    by_title = {str(e["SUMMARY"]): e for e in evs.values()}
    assert set(by_title) == {
        "Free power 02:00 CEST-04:00 CET (confirmed)",
        "Free power 13:00-15:00 (confirmed)",
    }
    night = by_title["Free power 02:00 CEST-04:00 CET (confirmed)"]
    assert night["DTEND"].dt - night["DTSTART"].dt == dt.timedelta(hours=3)  # spans the fold
    assert night["DTSTART"].dt == dt.datetime(2026, 10, 25, 0, 0, tzinfo=dt.UTC)
    mid = by_title["Free power 13:00-15:00 (confirmed)"]
    assert mid["DTSTART"].dt == dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.UTC)
    # day before DST (CEST, UTC+2) for contrast
    assert cf.publication_stamp(dt.date(2026, 10, 25)) == "20261024T110000Z"
    assert cf.publication_stamp(dt.date(2026, 10, 26)) == "20261025T120000Z"


def test_landing_page(built):
    root, _ = built
    page = (root / "site/calendar/index.html").read_text()
    assert "webcal://jvanders22.github.io/spain-negative-price-alert/calendar/es.ics" in page
    assert "calendar.google.com/calendar/r?cid=webcal://" in page
    assert "12 to 24 hours" in page and "../feed.xml" in page and "Email alert" in page
    assert "goatcounter" not in page  # empty config -> no tag


def test_goatcounter_tag():
    from negprice.site_config import goatcounter_tag

    assert goatcounter_tag("") == ""
    assert 'data-goatcounter="https://abc.goatcounter.com/count"' in goatcounter_tag("abc")


def test_retention_drops_old_days(built):
    root, p = built
    late = {"2026-10-30": day_prices("2026-10-30", [])}
    cf.build(root, pd.concat([p, periods_for(late)]))
    assert events(read(root)) == {}
    assert json.loads((root / "site/calendar/state.json").read_text())["es"] == {}


def test_window_inside_dst_fold_names_the_clock(tmp_path):
    day = "2026-10-25"
    start, end = sm.day_bounds_utc(dt.date.fromisoformat(day))
    ts = pd.date_range(start, end, freq="15min", inclusive="left")
    lo, hi = pd.Timestamp("2026-10-25T00:30Z"), pd.Timestamp("2026-10-25T01:30Z")
    cf.build(tmp_path, periods_for({day: [0.0 if lo <= t < hi else 40.0 for t in ts]}))
    (ev,) = events(read(tmp_path)).values()
    assert str(ev["SUMMARY"]) == "Free power 02:30 CEST-02:30 CET (confirmed)"


def test_revision_dtstamp_moves_forward(built, monkeypatch):
    root, p = built
    uid = "free-es-20261015-1000Z@jvanders22.github.io"
    before = events(read(root))[uid]["DTSTAMP"].dt
    hours = pd.date_range("2026-10-14T22:00Z", periods=24, freq="h")
    df = pd.DataFrame({"ts_utc": hours, "local_hour": range(24), "pcb_eur_mwh": 80.0})
    monkeypatch.setattr(cf.pvpc, "load", lambda root, day, fetch=False: df)
    cf.build(root, p)
    ev = events(read(root))[uid]
    assert ev["DTSTAMP"].dt > before and ev["LAST-MODIFIED"].dt == ev["DTSTAMP"].dt


def test_rerun_on_existing_files_is_byte_identical_and_crlf(built):
    root, p = built
    path = root / "site/calendar/es.ics"
    mtime = path.stat().st_mtime_ns
    cf.build(root, p)
    assert path.stat().st_mtime_ns == mtime  # unchanged file not rewritten
    assert b"\r\n" in path.read_bytes()
