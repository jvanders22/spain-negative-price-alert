"""``negprice`` command line: run (daily), pvpc-refresh (evening), weekly (Sunday), rebuild, seed, backfill,
estimate."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

import pandas as pd

from . import history, omie, outputs, pvpc, retail, retail_site, weekly, weekly_draft, weekly_site
from . import summary as sm
from . import tariffs as tf


def now_madrid() -> dt.datetime:
    return pd.Timestamp.now(tz=sm.TZ).to_pydatetime()


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _hhmm(s: str) -> dt.time:
    return dt.datetime.strptime(s, "%H:%M").time()


def _gh_append(var: str, text: str) -> None:
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def _gh_output(processed: bool, day: dt.date | None) -> None:
    _gh_append("GITHUB_OUTPUT", f"processed={'true' if processed else 'false'}\ndate={day or ''}\n")


def load_file(path: Path) -> tuple[dt.date, pd.DataFrame, str]:
    """Read a local marginalpdbc file -> (delivery date, day frame, source name)."""
    df = sm.from_omie(omie.parse_marginalpdbc(path.read_text(encoding="latin-1")))
    days = set(df["ts_utc"].dt.tz_convert(sm.TZ).dt.date)
    if len(days) != 1:
        raise ValueError(f"{path} contains {len(days)} delivery dates")
    return days.pop(), df, path.name


def fetch(day: dt.date, latest: bool = False) -> tuple[pd.DataFrame, str]:
    text, version = omie.fetch_text(day, latest=latest) if latest else omie.fetch_text(day)
    return sm.from_omie(omie.parse_marginalpdbc(text)), f"marginalpdbc_{day:%Y%m%d}.{version}"


def store(root: Path, day_df: pd.DataFrame, source: str) -> dict:
    """Validate, summarise and upsert one day into history. Returns the summary."""
    s = sm.summarise(day_df, history.read_daily(root), source)
    history.upsert_periods(root, history.day_rows(day_df, source))
    history.upsert_daily(root, s)
    return s


def step_summary(s: dict) -> str:
    c = s["cheapest_3h"]
    return (
        f"## OMIE day-ahead {s['weekday']} {s['date']}\n\n"
        f"- {'**Zero-price day**' if s['is_alert'] else 'No zero-price periods'}: "
        f"{s['n_le0']} of {s['n_periods']} periods ≤ €0 ({', '.join(s['le0_windows']) or 'none'}), "
        f"{s['n_neg']} negative, {s['n_cheap']} ≤ €{sm.CHEAP_EUR_MWH:.0f}\n"
        f"- Min {s['min_price']:.2f} ({s['min_time']}), mean {s['mean_price']:.2f}, "
        f"max {s['max_price']:.2f} €/MWh\n"
        f"- Cheapest 3h: {c['start']}–{c['end']} at {c['mean']:.2f} €/MWh\n"
        f"- Zero-price days YTD: {s['ytd_zero_days']} (same date last year: {s['ytd_zero_days_prev_year']})\n"
        f"- Source: {s['source']['file']}\n\n"
        # The draft text itself is NOT put in the step summary (public on a public repo); it is only in
        # drafts/<date>.md, which is gitignored and uploaded as a short-lived workflow artifact.
        f"X draft: `drafts/{s['date']}.md` (workflow artifact `x-draft-{s['date']}`, not committed)\n"
    )


def cache_pvpc(root: Path, days: list[dt.date]) -> None:
    """Best effort: cache PVPC (context column of the estimate) for ``days``. Never fails the run; D+1 PVPC
    is only published around 20:15 Madrid, so the delivery day is usually missing at run time."""
    for day in days:
        try:
            found = pvpc.load(root, day) is not None
            print(f"pvpc {day}: {'cached' if found else 'not published yet'}")
        except Exception as e:  # noqa: BLE001 - PVPC is optional context
            print(f"pvpc {day}: skipped ({type(e).__name__}: {e})", file=sys.stderr)


def cmd_run(a: argparse.Namespace) -> int:
    root = Path(a.root)
    day, day_df, source = a.date, None, None
    if a.file:
        file_day, day_df, source = load_file(Path(a.file))
        if day and day != file_day:
            print(f"error: {a.file} is for {file_day}, not {day}", file=sys.stderr)
            return 1
        day = file_day
    day = day or (now_madrid().date() + dt.timedelta(days=1))
    if history.is_processed(root, day) and not a.force:
        if not outputs.complete(root, day):  # e.g. a previous run died after saving history
            outputs.build(root, chart_dates=[day])
            print(f"{day}: already processed; regenerated missing outputs")
            _gh_output(True, day)
            return 0
        print(f"{day}: already processed")
        _gh_output(False, day)
        return 0
    if day_df is None:
        try:
            day_df, source = fetch(day, latest=a.force)
        except omie.NotPublished as e:
            _gh_output(False, day)
            if a.deadline and now_madrid().time() >= a.deadline:
                print(f"error: {day} still not published by {a.deadline:%H:%M} Madrid: {e}", file=sys.stderr)
                return 1
            print(f"{day}: pending ({e})")
            return 0
    s = store(root, day_df, source)
    if not a.file:  # --file is the offline mode
        cache_pvpc(root, [day - dt.timedelta(days=1), day])
    outputs.build(root, chart_dates=[day])
    print(outputs.headline(s))
    _gh_append("GITHUB_STEP_SUMMARY", step_summary(s))
    _gh_output(True, day)
    return 0


def refresh_pvpc(root: Path, days: list[dt.date]) -> list[dt.date]:
    """Fetch and cache PVPC for the ``days`` that have no usable cache file; returns the newly cached days.
    Never raises on PVPC or network errors (prints a warning). An unreadable cache file is fetched again."""
    new = []
    for day in days:
        try:
            if pvpc.load_cached(root, day) is not None:
                print(f"pvpc {day}: already cached")
                continue
        except Exception as e:  # noqa: BLE001 - a bad cache file is replaced below
            print(
                f"warning: pvpc {day}: unusable cache ({type(e).__name__}: {e}); fetching again",
                file=sys.stderr,
            )
        try:
            found = pvpc.fetch_and_cache(root, day) is not None
        except Exception as e:  # noqa: BLE001 - PVPC is optional context
            print(f"warning: pvpc {day}: skipped ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        print(f"pvpc {day}: {'cached' if found else 'not published yet'}")
        if found:
            new.append(day)
    return new


def cmd_pvpc_refresh(a: argparse.Namespace) -> int:
    """Evening job: PVPC for D is published ~20:15 Madrid on D-1, hours after the OMIE run."""
    root = Path(a.root)
    dates = sorted(history.read_daily(root)["date"])
    if not dates:
        print("history is empty: nothing to refresh")
        _gh_output(False, None)
        return 0
    latest = dt.date.fromisoformat(dates[-1])
    new = refresh_pvpc(root, [latest - dt.timedelta(days=1), latest])
    if new:
        outputs.build(root)
        print(f"rebuilt outputs with PVPC context (latest: {latest})")
    _gh_output(bool(new), latest)
    return 0


def cmd_rebuild(a: argparse.Namespace) -> int:
    root = Path(a.root)
    history.rebuild_daily(root)
    s = outputs.build(root)
    print(f"rebuilt outputs (latest: {s['date']})" if s else "history is empty: nothing to build")
    return 0


def cmd_seed(a: argparse.Namespace) -> int:
    root = Path(a.root)
    n, skipped = history.seed_from_parquet(root, Path(a.parquet))
    outputs.build(root)
    print(f"seeded {n} hourly days; skipped {len(skipped)}")
    for line in skipped:
        print(f"  skipped {line}")
    return 0


def cmd_backfill(a: argparse.Namespace) -> int:
    root = Path(a.root)
    done, failed, day = [], [], a.start
    while day <= a.end:
        if history.is_processed(root, day):
            print(f"{day}: already present, skipped")
        else:
            try:
                store(root, *fetch(day))
                done.append(day)
                print(f"{day}: ok")
            except Exception as e:  # noqa: BLE001 - one bad day must not stop the backfill
                failed.append((day, e))
                print(f"{day}: FAILED {type(e).__name__}: {e}", file=sys.stderr)
            time.sleep(a.sleep)
        day += dt.timedelta(days=1)
    if done:
        outputs.build(root)
    print(f"backfill: {len(done)} added, {len(failed)} failed")
    for d, e in failed:
        print(f"  {d}: {type(e).__name__}: {e}")
    return 1 if failed else 0


def _cell(v: float | None, spec: str) -> str:
    return "n/a" if v is None else format(v, spec)


def estimate_text(report: dict) -> str:
    """Compact hourly table plus the report, for the terminal."""
    t = report["tariff"]
    out = [
        f"{t} retail price ESTIMATE, {report['weekday']} {report['date']} (peninsula; day type "
        f"{report['day_type']}, season {report['season']}; {report['n_periods']} periods of "
        f"{report['mtu_min']} min)",
        *report["warnings"],
        "",
        f"{'hour':<11}{'period':<7}{'OMIE €/MWh':>11}{'estimate €/kWh':>16}{'PVPC €/kWh*':>13}",
    ]
    for h in report["hourly"]:
        out.append(
            f"{h['hour']:<11}{h['period']:<7}{h['omie_eur_mwh']:>11.2f}"
            f"{_cell(h['estimate_eur_kwh'], '.4f'):>16}{_cell(h['pvpc_eur_kwh'], '.4f'):>13}"
        )
    lo, hi = report["min_estimate"], report["max_estimate"]
    out += [
        f"* {report['pvpc']['note']}" + ("" if report["pvpc"]["available"] else " Not available here."),
        "",
        f"Estimate mean {report['mean_estimate_eur_kwh']:.4f} €/kWh; min {lo['estimate_eur_kwh']:.4f} at "
        f"{lo['time']} ({lo['period']}); max {hi['estimate_eur_kwh']:.4f} at {hi['time']} ({hi['period']})",
    ]
    for minutes, c in report["cheapest_windows"].items():
        r, w = c["retail"], c["wholesale"]
        out += [
            f"Cheapest {minutes} min by estimate: {r['start']}–{r['end']}  {r['estimate_eur_kwh']:.4f} €/kWh "
            f"({'/'.join(r['periods'])}; OMIE {r['omie_eur_mwh']:.2f} €/MWh)",
            f"Cheapest {minutes} min by OMIE: {w['start']}–{w['end']}  {w['omie_eur_mwh']:.2f} €/MWh "
            f"({'/'.join(w['periods'])}; estimate {w['estimate_eur_kwh']:.4f} €/kWh)",
        ]
    out += ["", f"Takeaway: {report['takeaway']}", "", f"Formula: {report['formula']}", "Assumptions:"]
    out += [f"  {k} = {v['value']!r} — {v['note']}" for k, v in report["assumptions"].items()]
    out += ["Regulated energy terms (€/kWh): " + "; ".join(
        f"{r['period']} peaje {r['peaje_eur_kwh']:.6f} + cargo {r['cargo_eur_kwh']:.6f}"
        for r in report["energy_terms_eur_kwh"]
    )]  # fmt: skip
    out += ["Sources:"]
    out += [f"  {s['boe_id']} {s['url']} — {s['table']} (verified {s['verified']})"
            for s in report["sources"].values()]  # fmt: skip
    out += [f"Label: {report['label']}. {report['disclaimer']}"]
    return "\n".join(out)


def cmd_estimate(a: argparse.Namespace) -> int:
    root = Path(a.root)
    periods = history.read_periods(root)
    day_df = periods[periods["date"] == a.date.isoformat()]
    if day_df.empty:
        print(f"error: {a.date} is not in {history.periods_path(root)}", file=sys.stderr)
        return 1
    pv = None
    if a.pvpc:
        try:
            pv = pvpc.load(root, a.date)
        except Exception as e:  # noqa: BLE001 - PVPC is optional context
            print(f"pvpc {a.date}: skipped ({type(e).__name__}: {e})", file=sys.stderr)
    assumptions = retail.Assumptions(
        loss_factor=a.loss,
        adjustment_services_eur_mwh=a.adj,
        retailer_margin_eur_mwh=a.margin,
        iee=a.iee,
        iva=a.iva,
    )
    est = retail.estimate(day_df, a.tariff, assumptions, pv, allow_other_year=a.allow_other_year)
    print(estimate_text(retail.day_report(est, assumptions)))
    if a.csv:
        path = Path(a.csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(retail_site.estimate_csv(est), encoding="utf-8", newline="\n")
        print(f"wrote {path}")
    return 0


def weekly_step_summary(f: dict) -> str:
    """Numbers only. The draft text is NOT put in the step summary (public on a public repo)."""
    end = f["week"]["end"]
    bullets = "\n".join(f"- {line}" for line in weekly_draft.fact_lines(f)[1:])
    return (
        f"## Weekly summary, {f['week']['label']} (week ending {end})\n\n{bullets}\n"
        f"- Chart: `site/weekly/{end}.png`, data: `site/weekly/{end}.json`\n\n"
        f"X drafts: `drafts/weekly-{end}-en.md`, `drafts/weekly-{end}-es.md` "
        f"(workflow artifact `x-draft-weekly-{end}`, not committed)\n"
    )


def cmd_weekly(a: argparse.Namespace) -> int:
    """Sunday job: summary of the latest complete Mon–Sun week in history. No network."""
    root = Path(a.root)
    stored = {dt.date.fromisoformat(d) for d in history.read_daily(root)["date"]}
    sunday = a.week_ending
    if sunday:
        weekly.check_sunday(sunday)
        if not weekly.is_complete(stored, sunday):
            missing = [str(d) for d in weekly.week_dates(sunday) if d not in stored]
            print(f"error: week ending {sunday} is incomplete; not in history: {', '.join(missing)}",
                  file=sys.stderr)  # fmt: skip
            return 1
    else:
        sunday = weekly.latest_complete_sunday(stored, now_madrid().date())
        if sunday is None:
            print("error: no complete Monday–Sunday week in history", file=sys.stderr)
            return 1
    f, changed = weekly_site.write(root, sunday)
    changed = outputs.build_index(root) or changed
    print(f"week ending {sunday}: {'built' if changed else 'already built'}")
    for line in weekly_draft.fact_lines(f):
        print(f"  {line}")
    folder = weekly_site.weekly_dir(root)
    print(f"json: {folder / f'{sunday}.json'}\npng: {folder / f'{sunday}.png'}")
    print("drafts (not committed): " + ", ".join(
        str(weekly_site.draft_path(root, sunday, lang)) for lang in weekly_draft.LANGS
    ))  # fmt: skip
    if changed:
        _gh_append("GITHUB_STEP_SUMMARY", weekly_step_summary(f))
    _gh_output(changed, sunday)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="negprice", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, func, help: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help)
        sp.add_argument("--root", default=".", help="repo root with data/, site/, drafts/ (default: cwd)")
        sp.set_defaults(func=func)
        return sp

    r = add("run", cmd_run, "process one delivery day (default: tomorrow, Europe/Madrid)")
    r.add_argument("--date", type=_date)
    r.add_argument("--force", action="store_true", help="reprocess even if already in history")
    r.add_argument("--file", help="read a local marginalpdbc file instead of fetching")
    r.add_argument("--deadline", type=_hhmm, help="Madrid HH:MM after which 'not published' is an error")
    add("pvpc-refresh", cmd_pvpc_refresh, "cache PVPC for the latest stored day (and the day before)")
    w = add("weekly", cmd_weekly, "weekly summary of the latest complete Mon-Sun week (no network)")
    w.add_argument("--week-ending", type=_date, help="Sunday YYYY-MM-DD (default: latest complete week)")
    add("rebuild", cmd_rebuild, "regenerate daily.csv and outputs from periods.csv (no network)")
    s = add("seed", cmd_seed, "load hourly history before 2025-10-01 from hourly.parquet")
    s.add_argument("--parquet", required=True)
    b = add("backfill", cmd_backfill, "fetch a date range from OMIE, skipping dates already present")
    b.add_argument("--start", type=_date, required=True)
    b.add_argument("--end", type=_date, required=True)
    b.add_argument("--sleep", type=float, default=1.0, help="seconds between requests")
    d = retail.Assumptions()
    e = add("estimate", cmd_estimate, "toll-aware retail price ESTIMATE for a stored day (not a bill)")
    e.add_argument("--date", type=_date, required=True)
    e.add_argument("--tariff", choices=tf.TARIFFS, default="2.0TD")
    e.add_argument("--margin", type=float, default=d.retailer_margin_eur_mwh, help="retailer margin, €/MWh")
    e.add_argument("--loss", type=float, default=d.loss_factor, help="network loss factor (0.16 = 16%%)")
    e.add_argument(
        "--adj", type=float, default=d.adjustment_services_eur_mwh, help="adjustment services, €/MWh"
    )
    e.add_argument("--iee", type=float, default=d.iee, help="electricity tax rate")
    e.add_argument("--iva", type=float, default=d.iva, help="VAT rate")
    e.add_argument("--pvpc", action=argparse.BooleanOptionalAction, default=True,
                   help="add PVPC as context (data/pvpc cache, else the public ESIOS archive)")  # fmt: skip
    e.add_argument("--csv", help="also write the per-period estimate to this CSV path")
    e.add_argument("--allow-other-year", action="store_true",
                   help="apply the 2026 tables to a date outside 2026 (prints a warning)")  # fmt: skip
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.func(a)
    except Exception as e:  # noqa: BLE001 - CLI boundary: report and exit 1
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
