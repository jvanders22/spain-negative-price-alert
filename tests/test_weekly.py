import datetime as dt
import json
import re
import struct
from pathlib import Path

import pandas as pd
import pytest
from conftest import synthetic_day

from negprice import cli, history, retail, weekly, weekly_draft, weekly_site
from negprice import summary as sm
from negprice import tariffs as tf

D = dt.date.fromisoformat
SRC = "marginalpdbc_{:%Y%m%d}.1"


def flat(day: str, price: float = 50.0, **overrides: float) -> pd.DataFrame:
    """A 15-minute day at ``price``; ``overrides`` maps "i<index>" to a price for that period."""
    prices = [price] * sm.expected_periods(D(day), 15)
    for key, value in overrides.items():
        prices[int(key[1:])] = value
    return synthetic_day(day, prices)


def evening(day: str, price: float) -> pd.DataFrame:
    return flat(day, **{f"i{i}": price for i in range(76, 92)})  # 19:00–22:59


def write_history(root: Path, frames: list[pd.DataFrame]) -> None:
    rows = []
    for df in frames:
        day = df["ts_utc"].iloc[0].tz_convert(sm.TZ).date()
        source = SRC.format(day) if int(df["mtu_min"].iloc[0]) == 15 else sm.SEED_SOURCE
        rows.append(history.day_rows(df, source))
    history.write_periods(root, pd.concat(rows))
    history.rebuild_daily(root)


# Week Mon 19 – Sun 25 Oct 2026 (Sunday is the fall-back day: 100 periods), after a complete previous
# week, plus two days of an incomplete following week and three 2025 days for the year-on-year count.
OCT_WEEK = [
    flat("2026-10-19", i48=0.0, i49=0.0, i50=0.0, i51=0.0),  # Mon 12:00–13:00 at 0
    flat("2026-10-20"),
    flat("2026-10-21", i56=-2.0, i57=-2.0, i58=-2.0, i59=-2.0, i60=-5.0),  # Wed 14:00–15:15 negative
    evening("2026-10-22", 100.0),
    evening("2026-10-23", 100.0),
    evening("2026-10-24", 100.0),
    flat("2026-10-25", i12=0.0),  # Sun: index 12 is the first quarter of the repeated hour, 02:00 CET
]
PREVIOUS_WEEK = [flat(f"2026-10-{d}") for d in range(12, 17)] + [
    flat("2026-10-17", **{f"i{i}": 0.0 for i in range(48, 54)}),  # Sat 12:00–13:30 at 0
    flat("2026-10-18"),
]
OTHER = [
    synthetic_day("2025-03-01", [0.0] + [50.0] * 23, mtu=60),  # zero-price day, counted for 2025
    flat("2025-10-20", i50=0.0),  # counted: on or before 25 Oct 2025
    flat("2025-10-26", i50=0.0),  # not counted: after 25 Oct 2025
    flat("2026-10-26"),
    flat("2026-10-27"),
]


@pytest.fixture
def root(tmp_path):
    write_history(tmp_path, OCT_WEEK + PREVIOUS_WEEK + OTHER)
    return tmp_path


def facts(root: Path, sunday: str) -> dict:
    return weekly.facts(history.read_periods(root), history.read_daily(root), D(sunday))


def run(root, *args):
    return cli.main(["weekly", "--root", str(root), *args])


def at(monkeypatch, today: str) -> None:
    monkeypatch.setattr(cli, "now_madrid", lambda: dt.datetime.fromisoformat(f"{today}T10:17"))


# --- week selection ---------------------------------------------------------------------------------


def test_latest_complete_week_skips_the_incomplete_one(root):
    stored = {D(d) for d in history.read_daily(root)["date"]}
    pick = weekly.latest_complete_sunday
    assert pick(stored, D("2026-11-01")) == D("2026-10-25")  # week ending 1 Nov has only Mon and Tue
    assert pick(stored, D("2026-10-28")) == D("2026-10-25")
    assert pick(stored, D("2026-10-25")) == D("2026-10-25")  # the Sunday itself counts
    assert pick(stored, D("2026-10-24")) == D("2026-10-18")  # Saturday: this week's Sunday is in the future
    assert pick(stored, D("2026-10-17")) is None  # nothing complete on or before that date
    assert pick(set(), D("2026-10-25")) is None
    assert weekly.week_dates(D("2026-10-25"))[0] == D("2026-10-19")


def test_cli_default_week_and_week_ending_rules(root, monkeypatch, capsys):
    at(monkeypatch, "2026-11-01")
    assert run(root) == 0
    assert "week ending 2026-10-25: built" in capsys.readouterr().out
    assert run(root, "--week-ending", "2026-10-24") == 1
    assert "not a Sunday" in capsys.readouterr().err
    assert run(root, "--week-ending", "2026-11-01") == 1
    err = capsys.readouterr().err
    assert "incomplete" in err and "2026-10-28" in err and "2026-10-27" not in err
    assert not (root / "site/weekly/2026-11-01.json").exists()
    at(monkeypatch, "2026-10-17")
    assert run(root) == 1
    assert "no complete Monday–Sunday week" in capsys.readouterr().err


# --- the five numbers, hand-computed ----------------------------------------------------------------


def test_1_zero_or_below_counts(root):
    f = facts(root, "2026-10-25")
    z = f["zero_or_below"]
    assert f["week"]["n_periods"] == 6 * 96 + 100 == 676 and z["n_periods"] == 676
    assert (z["n_le0"], z["n_neg"], z["days_with_le0"], z["days"]) == (4 + 5 + 1, 5, 3, 7)
    assert [d["n_le0"] for d in z["by_day"]] == [4, 0, 5, 0, 0, 0, 1]
    assert z["by_day"][-1] == {
        "date": "2026-10-25",
        "weekday": "Sun",
        "n_periods": 100,
        "n_le0": 1,
        "n_neg": 0,
    }
    assert f["week"]["label"] == "Mon 19 Oct – Sun 25 Oct" and f["week"]["mtu_min"] == 15
    assert f["week"]["dates"] == [f"2026-10-{d}" for d in range(19, 26)]


def test_2_lowest_price_with_day_and_local_time(root):
    assert facts(root, "2026-10-25")["lowest"] == {
        "price": -5.0,
        "date": "2026-10-21",
        "weekday": "Wed",
        "time": "15:00",
        "n_periods_at_min": 1,
    }
    # ties: the earliest period wins (previous week: six periods at 0 on Saturday from 12:00)
    low = facts(root, "2026-10-18")["lowest"]
    assert (low["price"], low["weekday"], low["time"], low["n_periods_at_min"]) == (0.0, "Sat", "12:00", 6)


def test_3_midday_vs_evening_spread(root):
    s = facts(root, "2026-10-25")["midday_evening"]
    # midday = 32 quarter-hours a day (also on the 25-hour Sunday), 224 in the week:
    # Mon 28×50, Wed 27×50 − 4×2 − 5, the other five days 32×50
    midday = (28 * 50 + (27 * 50 - 13) + 5 * 32 * 50) / 224
    evening_mean = (4 * 16 * 50 + 3 * 16 * 100) / 112  # 16 quarter-hours a day; Thu–Sat at 100
    assert s["midday_mean"] == round(midday, 2) == 47.93
    assert s["evening_mean"] == round(evening_mean, 2) == 71.43
    assert s["spread"] == 23.5


def test_4_cheapest_3h_windows_retail_estimate_and_wholesale(root):
    c = facts(root, "2026-10-25")["cheapest_3h"]
    # Wholesale: Wed, the earliest 12 quarter-hours holding all five negative ones (indices 49..60).
    assert c["wholesale"] == {
        "date": "2026-10-21",
        "weekday": "Wed",
        "start": "12:15",
        "end": "15:15",
        "omie_eur_mwh": round((7 * 50 - 13) / 12, 2),
    }
    # Retail estimate: Wed's cheap hours are punta/llano on 2.0TD, so the all-valle Sunday wins with the
    # earliest window holding its one zero (indices 1..12): 00:15 CEST to 02:15 CET, three real hours.
    r = c["retail"]
    omie = 11 * 50 / 12
    expected = retail.price_stack(omie, *tf.energy_terms("2.0TD", "P3"), retail.Assumptions())
    assert (r["date"], r["weekday"], r["start"], r["end"]) == ("2026-10-25", "Sun", "00:15", "02:15 CET")
    assert r["estimate_eur_kwh"] == pytest.approx(expected["estimate_eur_kwh"], abs=1e-6)
    assert r["omie_eur_mwh"] == round(omie, 2) and r["periods"] == ["P3"]
    assert r["label"] == "estimate" and r["available"] and r["tariff"] == "2.0TD"
    assert r["assumptions"]["loss_factor"] == retail.Assumptions().loss_factor
    assert "within each delivery day" in c["note"] and c["minutes"] == 180
    # Wed's cheapest wholesale window (28.08 €/MWh) is dearer on 2.0TD than Sunday's at 45.83 €/MWh
    wed = retail.cheapest_window(retail.estimate(OCT_WEEK[2], "2.0TD"), 180, "omie_eur_mwh")
    assert (wed["start"], wed["omie_eur_mwh"]) == ("12:15", c["wholesale"]["omie_eur_mwh"])
    assert wed["estimate_eur_kwh"] > r["estimate_eur_kwh"] and wed["periods"] == ["P1", "P2"]


def test_5_zero_price_days_ytd_vs_last_year(root):
    assert facts(root, "2026-10-25")["zero_days_ytd"] == {
        "as_of": "2026-10-25",
        "year": 2026,
        "ytd_zero_days": 4,  # Sat 17, Mon 19, Wed 21, Sun 25
        "prev_year": 2025,
        "prev_year_as_of": "2025-10-25",
        "ytd_zero_days_prev_year": 2,  # 1 Mar and 20 Oct 2025; 26 Oct 2025 is after the same date
    }
    assert facts(root, "2026-10-18")["zero_days_ytd"]["ytd_zero_days"] == 1


def test_week_over_week(root):
    # previous week: 6 periods at 0 on one day; midday (224×50 − 6×50)/224 = 48.66, evening 50
    assert facts(root, "2026-10-25")["week_over_week"] == {
        "previous_week_ending": "2026-10-18",
        "n_le0_prev": 6,
        "n_le0_change": 4,
        "n_neg_prev": 0,
        "n_neg_change": 5,
        "days_with_le0_prev": 1,
        "days_with_le0_change": 2,
        "spread_prev": 1.34,
        "spread_change": 22.16,
    }
    assert facts(root, "2026-10-18")["week_over_week"] is None  # the week before is not in history


def test_spring_forward_week_has_668_periods(tmp_path):
    days = [flat(f"2026-03-{d}") for d in range(23, 29)] + [flat("2026-03-29", i8=-1.0)]
    assert len(days[-1]) == 92
    write_history(tmp_path, days)
    f = facts(tmp_path, "2026-03-29")
    assert f["week"]["n_periods"] == 6 * 96 + 92 == 668
    # index 8 is 01:00 UTC: 02:00 local does not exist that day, so it is 03:00 CEST
    assert f["lowest"] == {
        "price": -1.0,
        "date": "2026-03-29",
        "weekday": "Sun",
        "time": "03:00",
        "n_periods_at_min": 1,
    }
    assert (f["zero_or_below"]["n_le0"], f["zero_or_below"]["days_with_le0"]) == (1, 1)
    assert f["midday_evening"] == {**f["midday_evening"], "midday_mean": 50.0, "evening_mean": 50.0}
    assert f["week_over_week"] is None and f["zero_days_ytd"]["ytd_zero_days"] == 1
    assert f["cheapest_3h"]["retail"]["start"] == "00:00"  # 00:00–04:00 local is three real hours
    assert f["cheapest_3h"]["retail"]["end"] == "04:00"


def test_incomplete_week_and_other_tariff_year(tmp_path):
    days = [flat(f"2025-10-{d}") for d in range(20, 27)]  # week ending Sun 26 Oct 2025
    write_history(tmp_path, days[:-1])
    with pytest.raises(ValueError, match="not complete"):
        facts(tmp_path, "2025-10-26")
    write_history(tmp_path, days)
    c = facts(tmp_path, "2025-10-26")["cheapest_3h"]
    assert c["retail"] == {
        "label": "estimate",
        "tariff": "2.0TD",
        "available": False,
        "reason": "tariff tables cover 2026-01-01..2026-12-31 only",
    }
    assert c["wholesale"]["omie_eur_mwh"] == 50.0
    for lang in weekly_draft.LANGS:
        assert "2.0TD" not in weekly_draft.post_text(facts(tmp_path, "2025-10-26"), lang)
    assert run(tmp_path, "--week-ending", "2025-10-26") == 0


# --- outputs ----------------------------------------------------------------------------------------

SITE_FILES = [f"site/weekly/{n}.{ext}" for n in ("2026-10-25", "latest") for ext in ("json", "png")]
DRAFTS = [f"drafts/weekly-2026-10-25-{lang}.md" for lang in ("en", "es")]


def snapshot(root: Path) -> dict[str, bytes]:
    return {f: (root / f).read_bytes() for f in [*SITE_FILES, *DRAFTS, "site/index.html"]}


def test_json_png_and_index(root, capsys):
    assert run(root, "--week-ending", "2026-10-25") == 0
    out = capsys.readouterr().out
    assert "site/weekly/2026-10-25.png" in out and "drafts/weekly-2026-10-25-es.md" in out
    assert "periods <= 0: 10 of 676 (5 negative), on 3 of 7 days" in out
    doc = json.loads((root / "site/weekly/2026-10-25.json").read_text())
    assert doc["schema_version"] == 1 and doc["kind"] == "weekly" and doc["units"] == "EUR/MWh"
    assert doc["week"]["dates"][0] == "2026-10-19" and doc["chart"] == "weekly/2026-10-25.png"
    assert doc["cheapest_3h"]["retail"]["label"] == "estimate"
    assert [s["file"] for s in doc["sources"]["omie"]][-1] == "marginalpdbc_20261025.1"
    assert "omie.es" in doc["sources"]["omie"][0]["url"] and "BOE-A-2025-26705" in json.dumps(doc)
    assert {k: doc[k] for k in facts(root, "2026-10-25")} == facts(root, "2026-10-25")
    png = (root / "site/weekly/2026-10-25.png").read_bytes()
    assert png.startswith(b"\x89PNG") and struct.unpack(">II", png[16:24]) == (1080, 1080)
    for f in SITE_FILES[:2]:
        assert (root / f).read_bytes() == (root / f.replace("2026-10-25", "latest")).read_bytes()
    page = (root / "site/index.html").read_text()
    assert "<h2>This week</h2>" in page and 'src="weekly/latest.png"' in page
    assert 'href="weekly/latest.json"' in page and "Mon 19 Oct – Sun 25 Oct" in page
    assert "<script" not in page and 'src="http' not in page
    assert not list((root / "site").rglob("*.md"))


def test_png_is_byte_identical_on_rerun(root, tmp_path_factory):
    f = facts(root, "2026-10-25")
    days = weekly.week_days(history.read_periods(root), D("2026-10-25"))
    a, b = (tmp_path_factory.mktemp("png") / f"{n}.png" for n in "ab")
    weekly_site.chart(days, f, [a])
    weekly_site.chart(days, f, [b])
    assert a.read_bytes() == b.read_bytes() and len(a.read_bytes()) > 20_000
    assert weekly_site.title(f) == "Spain day-ahead power, week of Mon 19 Oct – Sun 25 Oct"
    rows = weekly_site.panel_rows(f)
    assert len(rows) == 5 and [r[0] for r in rows[:3]] == ["10", "-5.00 €/MWh", "+23.50 €/MWh"]
    assert "estimate" in rows[3][1] and rows[4][0] == "4 days"


def test_idempotent_and_latest_tracks_the_newest_week(root, monkeypatch, capsys):
    gh, summary = root / "gh_output", root / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    at(monkeypatch, "2026-10-25")
    assert run(root) == 0
    assert gh.read_text() == "processed=true\ndate=2026-10-25\n"
    first = snapshot(root)

    gh.write_text("")
    assert run(root) == 0
    assert "week ending 2026-10-25: already built" in capsys.readouterr().out
    assert gh.read_text() == "processed=false\ndate=2026-10-25\n"
    assert snapshot(root) == first and summary.read_text().count("## Weekly summary") == 1

    # a missing PNG is rebuilt byte-identically; a rebuild of the daily outputs keeps the weekly section
    (root / "site/weekly/2026-10-25.png").unlink()
    assert run(root) == 0 and cli.main(["rebuild", "--root", str(root)]) == 0
    assert snapshot(root) == first

    # an older week is built without taking over latest.*
    gh.write_text("")
    assert run(root, "--week-ending", "2026-10-18") == 0
    assert gh.read_text() == "processed=true\ndate=2026-10-18\n"
    assert (root / "site/weekly/2026-10-18.json").exists() and snapshot(root) == first

    # changed history for the week -> rebuilt
    periods = history.read_periods(root)
    periods.loc[periods["date"] == "2026-10-20", "price_es"] = 40.0
    history.write_periods(root, periods)
    history.rebuild_daily(root)
    assert run(root) == 0
    assert "week ending 2026-10-25: built" in capsys.readouterr().out
    assert snapshot(root)["site/weekly/latest.json"] != first["site/weekly/latest.json"]


# --- drafts -----------------------------------------------------------------------------------------


def post(md: str) -> str:
    return md.split("```text\n")[1].split("\n```")[0]


def fact_numbers(f: dict) -> set[str]:
    """Every way a number of the facts may be written: integers found in any value (dates, times) and
    each float at 0, 2 and 3 decimals, without sign."""
    out = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, bool) or v is None:
            return
        elif isinstance(v, (int, float)):
            out.update(f"{abs(v):.{dp}f}" for dp in (0, 2, 3))
        else:
            out.update(str(int(n)) for n in re.findall(r"\d+", v))

    walk({k: v for k, v in f.items() if k != "sources"})
    return out


def draft_numbers(text: str) -> list[str]:
    """Numbers in a post, normalised to a decimal point. Not counted: the tariff name "2.0TD", the "3h"
    window length and the €0 threshold in the zero-or-below line (all fixed wording, not results)."""
    for fixed in ("2.0TD", "3h", "3 h", "at or below €0", "a 0 €/MWh o menos"):
        text = text.replace(fixed, "")
    found = re.findall(r"\d+(?:[.,]\d+)?", text)
    return [n.replace(",", ".") if re.search(r"[.,]", n) else str(int(n)) for n in found]


@pytest.mark.parametrize("sunday", ["2026-10-25", "2026-10-18"])
@pytest.mark.parametrize("lang", ["en", "es"])
def test_draft_rules(root, sunday, lang):
    f = facts(root, sunday)
    text = weekly_draft.post_text(f, lang)
    assert len(text) <= 280 and post(weekly_draft.draft_md(f, lang)) == text
    assert ("day-ahead" if lang == "en" else "mayorista") in text
    assert text.endswith("\n\nSource: OMIE" if lang == "en" else "\n\nFuente: OMIE")
    assert "#" not in text
    assert all(line == line.strip() and not line.endswith(("…", "...")) for line in text.splitlines())
    opinions = [line for line in text.splitlines() if line in weekly_draft.OPINIONS[lang]]
    assert opinions == [weekly_draft.opinion(sunday, lang)]  # exactly one opinion line
    numbers, allowed = draft_numbers(text), fact_numbers(f)
    assert numbers and [n for n in numbers if n not in allowed] == []


def test_draft_text_en_and_es(root):
    f = facts(root, "2026-10-25")
    en, es = (weekly_draft.post_text(f, lang) for lang in ("en", "es"))
    assert en.startswith(
        "Spain day-ahead power, week of 19–25 Oct:\n10 quarter-hours at or below €0, on 3 of 7 days.\n"
    )
    assert "Cheapest 2.0TD 3h, my estimate: Sun 00:15–02:15 CET, ~€0.126/kWh." in en
    assert es.startswith(
        "Luz en el mercado mayorista español, semana del 19 al 25 de oct:\n"
        "10 cuartos de hora a 0 €/MWh o menos, en 3 de 7 días.\n"
    )
    # Spanish: comma decimals, "€/MWh" / "€/kWh" after the number, never a decimal point or "€" first
    lines = weekly_draft._lines_es(f)
    every_line = "\n".join(lines.values())
    assert len(re.findall(r"\d,\d+ €/(?:MWh|kWh)", every_line)) == 2
    assert not re.search(r"\d\.\d|€\d", every_line.replace("2.0TD", "")) and set(es.splitlines()) & set(
        lines.values()
    )
    assert lines["low"] == "Mínimo: -5,00 €/MWh, el mié a las 15:00."
    assert lines["spread"] == "La tarde salió 24 €/MWh más cara que el mediodía."
    assert lines["retail"].endswith("dom 00:15–02:15 CET, ~0,126 €/kWh.")
    assert lines["ytd"] == "Días con precio cero en 2026: 4 (2 a estas alturas de 2025)."
    assert weekly_draft._lines_en(f)["low"] == "Low: -€5.00/MWh, Wed 15:00."
    assert weekly_draft.week_range({"week": {"start": "2026-09-28", "end": "2026-10-04"}}, "en") == (
        "28 Sep–4 Oct"
    )
    assert weekly_draft.week_range({"week": {"start": "2026-09-28", "end": "2026-10-04"}}, "es") == (
        "28 sep al 4 oct"
    )


def test_draft_drops_whole_lines_to_fit_and_handles_a_week_without_zero_prices(tmp_path):
    write_history(tmp_path, [flat(f"2026-10-{d:02d}", 80.0, i50=12.5) for d in range(5, 12)])
    f = facts(tmp_path, "2026-10-11")
    for lang, lead in (("en", "No quarter-hour at or below €0 all week."), ("es", "Ni un cuarto de hora")):
        text = weekly_draft.post_text(f, lang)
        lines = (weekly_draft._lines_en if lang == "en" else weekly_draft._lines_es)(f)
        body = text.split("\n\n")[0].splitlines()
        assert len(text) <= 280 and body[1].startswith(lead)
        assert set(body) <= set(lines.values()) and lines["low"] in body  # whole lines only
        assert len("\n".join(lines.values())) + 90 > 280  # not everything fits: something was dropped
        assert len(body) < len(lines)
    assert len(weekly_draft.OPINIONS["en"]) == len(weekly_draft.OPINIONS["es"]) == 6
    assert not any(
        c.isdigit() or c == "#" for lines in weekly_draft.OPINIONS.values() for c in "".join(lines)
    )
    picks = {weekly_draft.opinion(str(D("2026-10-04") + dt.timedelta(weeks=i)), "en") for i in range(6)}
    assert len(picks) == 6  # consecutive weeks rotate through the list


def test_step_summary_has_numbers_but_no_draft_text(root, monkeypatch):
    summary = root / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert run(root, "--week-ending", "2026-10-25") == 0
    text = summary.read_text()
    assert "periods <= 0: 10 of 676 (5 negative), on 3 of 7 days" in text
    assert "x-draft-weekly-2026-10-25" in text and "site/weekly/2026-10-25.png" in text
    for path in DRAFTS:
        md = (root / path).read_text()
        assert md.startswith("# DRAFT — not posted. Josh approves every post.") and "## Facts" in md
        assert f"Characters: {len(post(md))} / 280" in md
        for line in post(md).splitlines():
            assert not line or line not in text
