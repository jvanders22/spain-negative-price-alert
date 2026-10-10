import json
from importlib.resources import files

import feedparser
import jsonschema
import pytest
from conftest import fixture_path, pvpc_text

from negprice import cli, draft, outputs, retail, retail_site
from negprice import summary as sm

DAYS = ["20250101", "20251026", "20260329", "20261010"]


@pytest.fixture
def site(tmp_path):
    for d in DAYS:
        assert cli.main(["run", "--root", str(tmp_path), "--file", str(fixture_path(d))]) == 0
    return tmp_path


def test_latest_json_validates(site):
    schema = json.loads(files("negprice").joinpath("latest.schema.json").read_text())
    doc = json.loads((site / "site" / "latest.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(doc)
    assert doc["date"] == "2026-10-10" and doc["n_le0"] == 18 and doc["ytd_zero_days"] == 2


def test_feed_parses(site):
    feed = feedparser.parse((site / "site" / "feed.xml").read_text())
    assert not feed.bozo
    assert [e.id for e in feed.entries] == ["2026-10-10", "2026-03-29", "2025-10-26", "2025-01-01"]
    assert feed.entries[0].title.startswith("ALERT:")
    assert feed.entries[2].title.startswith("Cheapest 3h:")


def test_chart_and_html(site):
    for name in ("2026-10-10.png", "latest.png"):
        png = (site / "site" / "charts" / name).read_bytes()
        assert png.startswith(b"\x89PNG") and len(png) > 10_000
    page = (site / "site" / "index.html").read_text()
    assert "charts/latest.png" in page and "latest.json" in page and "feed.xml" in page
    assert outputs.ATTRIBUTION in page
    assert "<script" not in page and "http://" not in page and 'src="http' not in page
    assert page.count('class="cell"') == 4


def test_drafts_live_outside_site(site):
    assert not list((site / "site").rglob("*.md"))
    md = (site / "drafts" / "2026-10-10.md").read_text()
    assert md.startswith("# DRAFT — not posted. Josh approves every post.")


@pytest.mark.parametrize("day", DAYS)
def test_draft_rules(site, day):
    s = outputs.summaries(site, [sm.dt.date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")])[0]
    text = draft.post_text(s)
    assert len(text) <= 280
    assert "OMIE" in text and "#" not in text.replace("Zero-price day #", "")
    assert "day-ahead" in text


def test_draft_hashtag_free_and_trims_long_windows(golden):
    s = sm.summarise(golden)
    s["le0_windows"] = [f"{h:02d}:00–{h:02d}:15" for h in range(0, 24, 2)]
    text = draft.post_text(s)
    # the window list is shortened to "first +N more" rather than dropped
    assert len(text) <= 280 and "quarter-hours, 00:00–00:15 +11 more. Low: -€0.01/MWh." in text
    assert text.endswith("Source: OMIE")


def test_estimate_outputs(site):
    doc = json.loads((site / "site" / "estimate" / "latest.json").read_text())
    assert doc["label"] == "estimate" and doc["available"] and doc["date"] == "2026-10-10"
    assert list(doc["tariffs"]) == ["2.0TD", "3.0TD"]
    assert all(r["label"] == "estimate" and len(r["hourly"]) == 24 for r in doc["tariffs"].values())
    for tariff in ("2.0TD", "3.0TD"):
        lines = (site / "site" / "estimate" / f"2026-10-10-{tariff}.csv").read_text().splitlines()
        assert lines[0].split(",") == retail.COLUMNS and len(lines) == 97
        assert lines[1].startswith(f"2026-10-09T22:00:00Z,00:00,0,15,{tariff},") and lines[1].endswith(",")
    page = (site / "site" / "index.html").read_text()
    assert retail_site.SECTION_TITLE in page and 'href="wholesale-vs-bill.html"' in page
    assert "ASSUMPTION" in page and "13:30–16:30" in page
    explainer = (site / "site" / "wholesale-vs-bill.html").read_text()
    assert "Wholesale is not your bill" in explainer and "estimate" in explainer.lower()
    assert (
        "BOE-A-2025-26348" in explainer and "BOE-A-2025-26705" in explainer and "BOE-A-2020-1066" in explainer
    )
    assert "0.097553" in explainer and "2026-10-10 15:00, cheapest wholesale" in explainer
    assert "<script" not in explainer and "http://" not in explainer


def test_estimate_uses_cached_pvpc_only(site):
    before = (site / "site" / "index.html").read_text()
    assert "Not available for this day" in before
    cache = site / "data" / "pvpc" / "2026-10-10.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(pvpc_text("20261010"))
    assert cli.main(["rebuild", "--root", str(site)]) == 0
    after = (site / "site" / "index.html").read_text()
    assert "Not available for this day" not in after and "<td>0.218</td>" in after
    doc = json.loads((site / "site" / "estimate" / "latest.json").read_text())
    assert doc["tariffs"]["2.0TD"]["pvpc"]["available"]
    assert ",0.218240\n" in (site / "site" / "estimate" / "2026-10-10-2.0TD.csv").read_text()


def test_estimate_unavailable_outside_table_year(tmp_path):
    assert cli.main(["run", "--root", str(tmp_path), "--file", str(fixture_path("20251026"))]) == 0
    doc = json.loads((tmp_path / "site" / "estimate" / "latest.json").read_text())
    assert doc == {
        "schema_version": 1,
        "label": "estimate",
        "date": "2025-10-26",
        "available": False,
        "reason": "tariff tables cover 2026-01-01..2026-12-31 only",
    }
    assert not list((tmp_path / "site" / "estimate").glob("*.csv"))
    assert "No estimate for this day" in (tmp_path / "site" / "index.html").read_text()
    assert "No worked example" in (tmp_path / "site" / "wholesale-vs-bill.html").read_text()
