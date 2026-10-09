import json
from importlib.resources import files

import feedparser
import jsonschema
import pytest
from conftest import fixture_path

from negprice import cli, draft, outputs
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
    assert len(text) <= 280 and "quarter-hours, 00:00–00:15 +11 more. Low: -€0.01/MWh." in text  # list shortened
    assert text.endswith("Source: OMIE")
