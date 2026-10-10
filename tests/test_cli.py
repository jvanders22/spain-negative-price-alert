import datetime as dt
from pathlib import Path

import pytest
from conftest import fixture_path, pvpc_text

from negprice import cli, omie, pvpc

FILES = [
    "data/history/periods.csv",
    "data/history/daily.csv",
    "site/latest.json",
    "site/feed.xml",
    "site/index.html",
    "site/charts/2026-10-10.png",
    "site/charts/latest.png",
    "drafts/2026-10-10.md",
    "site/estimate/latest.json",
    "site/estimate/2026-10-10-2.0TD.csv",
    "site/estimate/2026-10-10-3.0TD.csv",
    "site/wholesale-vs-bill.html",
]


def snapshot(root: Path) -> dict[str, bytes]:
    return {f: (root / f).read_bytes() for f in FILES}


def run(root, *args):
    return cli.main(["run", "--root", str(root), *args])


def test_run_is_idempotent(tmp_path, capsys, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    golden = str(fixture_path("20261010"))
    assert run(tmp_path, "--file", golden) == 0
    first = snapshot(tmp_path)
    assert "processed=true" in out.read_text()
    assert "18 of 96" in (tmp_path / "summary.md").read_text()

    out.write_text("")
    assert run(tmp_path, "--file", golden) == 0
    assert "already processed" in capsys.readouterr().out
    assert "processed=false" in out.read_text()
    assert snapshot(tmp_path) == first

    assert run(tmp_path, "--file", golden, "--force") == 0
    assert snapshot(tmp_path) == first

    assert cli.main(["rebuild", "--root", str(tmp_path)]) == 0
    assert snapshot(tmp_path) == first


def test_already_processed_skips_network(tmp_path, monkeypatch):
    assert run(tmp_path, "--file", str(fixture_path("20261010"))) == 0
    monkeypatch.setattr(omie, "fetch_text", lambda d: pytest.fail("network used"))
    assert run(tmp_path, "--date", "2026-10-10") == 0


def test_run_fetches_with_version(tmp_path, monkeypatch):
    monkeypatch.setattr(omie, "fetch_text", lambda d: (fixture_path("20261010").read_text(), 2))
    assert run(tmp_path, "--date", "2026-10-10") == 0
    assert "marginalpdbc_20261010.2" in (tmp_path / "site/latest.json").read_text()


def _not_published(day):
    raise omie.NotPublished("404")


@pytest.mark.parametrize("now,code", [("13:20", 0), ("16:00", 1), ("16:17", 1)])
def test_pending_until_deadline(tmp_path, monkeypatch, capsys, now, code):
    monkeypatch.setattr(omie, "fetch_text", _not_published)
    monkeypatch.setattr(cli, "now_madrid", lambda: dt.datetime.fromisoformat(f"2026-10-09T{now}"))
    assert run(tmp_path, "--deadline", "16:00") == code
    if code == 0:
        assert "2026-10-10: pending" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()


def test_other_errors_exit_1(tmp_path, monkeypatch):
    monkeypatch.setattr(omie, "fetch_text", lambda d: ("<html>oops</html>", 1))
    assert run(tmp_path, "--date", "2026-10-10", "--deadline", "16:00") == 1


def test_invalid_file_exits_1(tmp_path):
    bad = tmp_path / "marginalpdbc_20261010.1"
    lines = fixture_path("20261010").read_text().splitlines()
    bad.write_text("\n".join(lines[:-3] + lines[-1:]))  # drop two periods
    assert run(tmp_path, "--file", str(bad)) == 1
    assert not (tmp_path / "data").exists()


def test_backfill_tolerates_failures(tmp_path, monkeypatch, capsys):
    def fake(day):
        if day == dt.date(2026, 3, 30):
            raise RuntimeError("HTTP 503")
        return fixture_path(f"{day:%Y%m%d}").read_text(), 1

    monkeypatch.setattr(omie, "fetch_text", fake)
    assert run(tmp_path, "--file", str(fixture_path("20261010"))) == 0
    code = cli.main(
        ["backfill", "--root", str(tmp_path), "--start", "2026-03-29", "--end", "2026-03-30", "--sleep", "0"]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "1 added, 1 failed" in out and "2026-03-30" in out
    assert "2026-03-29" in (tmp_path / "data/history/daily.csv").read_text()


def test_already_processed_repairs_missing_outputs(tmp_path, monkeypatch):
    assert run(tmp_path, "--file", str(fixture_path("20261010"))) == 0
    draft_md = tmp_path / "drafts/2026-10-10.md"
    before = draft_md.read_bytes()
    draft_md.unlink()
    monkeypatch.setattr(omie, "fetch_text", lambda d: pytest.fail("network used"))
    assert run(tmp_path, "--date", "2026-10-10") == 0
    assert draft_md.read_bytes() == before


def estimate(root, *args):
    return cli.main(["estimate", "--root", str(root), *args])


def test_estimate_command(tmp_path, capsys, monkeypatch):
    assert run(tmp_path, "--file", str(fixture_path("20261010"))) == 0
    monkeypatch.setattr(pvpc, "fetch_text", lambda d: pvpc_text("20261010"))
    capsys.readouterr()
    out_csv = tmp_path / "out" / "est.csv"
    assert estimate(tmp_path, "--date", "2026-10-10", "--csv", str(out_csv)) == 0
    out = capsys.readouterr().out
    assert out.startswith("2.0TD retail price ESTIMATE, Sat 2026-10-10")
    assert "Takeaway: Cheapest wholesale 3h: 13:30–16:30 (-0.01 €/MWh)" in out
    assert "ASSUMPTION" in out and "BOE-A-2025-26705" in out and "Label: estimate." in out
    assert "00:00 P3 " in out and "0.2182" in out
    assert len(out_csv.read_text().splitlines()) == 97
    assert (tmp_path / "data/pvpc/2026-10-10.json").exists()

    monkeypatch.setattr(pvpc, "fetch_text", lambda d: pytest.fail("network used"))
    assert estimate(tmp_path, "--date", "2026-10-10", "--tariff", "3.0TD", "--no-pvpc", "--margin", "0") == 0
    out = capsys.readouterr().out
    assert out.startswith("3.0TD retail price ESTIMATE") and "retailer_margin_eur_mwh = 0.0 " in out
    assert "Not available here." in out


def test_estimate_command_errors(tmp_path, capsys):
    assert run(tmp_path, "--file", str(fixture_path("20251026"))) == 0
    assert estimate(tmp_path, "--date", "2026-10-10") == 1
    assert "is not in" in capsys.readouterr().err
    assert estimate(tmp_path, "--date", "2025-10-26") == 1
    assert "TariffYearError" in capsys.readouterr().err
    assert estimate(tmp_path, "--date", "2025-10-26", "--allow-other-year") == 0
    assert "WARNING: 2025-10-26 is outside" in capsys.readouterr().out


def test_run_caches_pvpc_but_never_fails_on_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(omie, "fetch_text", lambda d, latest=False: (fixture_path("20261010").read_text(), 1))

    def boom(day):
        raise RuntimeError("ESIOS down")

    monkeypatch.setattr(pvpc, "fetch_text", boom)
    assert run(tmp_path, "--date", "2026-10-10") == 0
    assert "pvpc 2026-10-10: skipped (RuntimeError: ESIOS down)" in capsys.readouterr().err
    assert not (tmp_path / "data/pvpc").exists()

    texts = {dt.date(2026, 10, 9): pvpc_text("20261009"), dt.date(2026, 10, 10): '{"message":"No values"}'}
    monkeypatch.setattr(pvpc, "fetch_text", texts.get)
    assert run(tmp_path, "--date", "2026-10-10", "--force") == 0
    out = capsys.readouterr().out
    assert "pvpc 2026-10-09: cached" in out and "pvpc 2026-10-10: not published yet" in out
    assert [p.name for p in (tmp_path / "data/pvpc").iterdir()] == ["2026-10-09.json"]


def test_run_from_file_is_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(pvpc, "fetch_text", lambda d: pytest.fail("network used"))
    assert run(tmp_path, "--file", str(fixture_path("20261010"))) == 0
