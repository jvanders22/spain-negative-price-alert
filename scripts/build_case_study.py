"""Write the "Case study" block of README.md from data, so no number in it is typed by hand.

Numbers come from data/history (daily.csv, periods.csv) restricted to delivery dates <= ``AS_OF``, plus the
retail calculator (default assumptions, no PVPC) for ``EXAMPLE_DAYS``. ``AS_OF`` is fixed so the text stays
the same while history grows; move it on purpose to refresh the section.

    python scripts/build_case_study.py           # rewrite the block between the markers
    python scripts/build_case_study.py --check   # exit 1 if README.md is stale (CI)
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import textwrap
from pathlib import Path

from negprice import history, retail
from negprice import summary as sm
from negprice import tariffs as tf

AS_OF = dt.date(2026, 10, 10)
EXAMPLE_DAYS = (dt.date(2026, 10, 9), dt.date(2026, 10, 10))
TARIFFS = ("2.0TD", "3.0TD")
WINDOW_MIN = 180
BANDS = ((0, 8), (8, 10), (10, 14), (14, 18), (18, 22), (22, 24))  # local hours; the 2.0TD weekday edges
START, END = "<!-- case-study:start -->", "<!-- case-study:end -->"
ROOT = Path(__file__).resolve().parents[1]


def _d(day: dt.date, year: bool = False) -> str:
    return f"{day:%a} {day.day} {day:%b}" + (f" {day.year}" if year else "")


def _require(ok: bool, what: str) -> None:
    """The prose around the numbers states a conclusion; fail loudly if the data stops supporting it."""
    if not ok:
        raise SystemExit(f"case study: the data no longer supports the text ({what}); edit render()")


def facts(root: Path) -> dict:
    daily = history.read_daily(root)
    daily = daily[daily["date"] <= AS_OF.isoformat()]
    today = daily[daily["date"] == AS_OF.isoformat()]
    _require(len(today) == 1, f"{AS_OF} is in daily.csv")
    counts = sm.zero_day_counts(AS_OF, bool(today["n_le0"].iloc[0] > 0), daily)

    periods = history.read_periods(root)
    periods = periods[periods["date"] <= AS_OF.isoformat()]
    ytd = periods[periods["date"] >= f"{AS_OF.year}-01-01"]
    hour = ytd["ts_utc"].dt.tz_convert(sm.TZ).dt.hour
    le0 = ytd["price_es"] <= 0
    bands = [(lo, hi, float(le0[(hour >= lo) & (hour < hi)].mean())) for lo, hi in BANDS]

    windows = {}
    for day in EXAMPLE_DAYS:
        day_df = periods[periods["date"] == day.isoformat()]
        _require(not day_df.empty, f"{day} is in periods.csv")
        for t in TARIFFS:
            est = retail.estimate(day_df, t)
            windows[day, t] = retail.compare_windows(est, WINDOW_MIN)
            windows[day, t]["p1_windows"] = sm.windows(est, est["period"] == "P1", day)
    return {
        "first": dt.date.fromisoformat(daily["date"].min()),
        "n_days": len(daily),
        "n_periods": len(periods),
        "ytd": counts["ytd_zero_days"],
        "prev": counts["ytd_zero_days_prev_year"],
        "ytd_share": float(le0.mean()),
        "bands": bands,
        "windows": windows,
    }


def _span(w: dict) -> str:
    return f"{w['start']}–{w['end']}"


def _wrap(text: str) -> str:
    """One README paragraph or bullet, wrapped like the rest of the file."""
    indent = "  " if text.startswith("- ") else ""
    return textwrap.fill(text, 108, subsequent_indent=indent, break_long_words=False, break_on_hyphens=False)


def _table(f: dict) -> str:
    head = ["Day", "Tariff", "Cheapest wholesale 3 h", "Estimate for that window", "Cheapest estimated 3 h"]
    rows = [[*head, "Estimate"], ["---"] * (len(head) + 1)]
    for (day, t), c in f["windows"].items():
        w, r = c["wholesale"], c["retail"]
        rows.append(
            [
                _d(day),
                t,
                f"{_span(w)} ({w['omie_eur_mwh']:.2f} €/MWh)",
                f"{w['estimate_eur_kwh']:.3f} €/kWh",
                _span(r),
                f"{r['estimate_eur_kwh']:.3f} €/kWh",
            ]
        )
    return "\n".join(f"| {' | '.join(row)} |" for row in rows)


def render(f: dict) -> str:
    a = retail.Assumptions()
    fri, sat = EXAMPLE_DAYS
    year, last = AS_OF.year, AS_OF.year - 1
    c20, c30 = f["windows"][fri, "2.0TD"], f["windows"][fri, "3.0TD"]
    w, r = c20["wholesale"], c20["retail"]
    w30, r30 = c30["wholesale"], c30["retail"]
    sat20 = f["windows"][sat, "2.0TD"]["retail"]
    punta = [p for p in c20["p1_windows"] if p.split("–")[0] <= w["start"] < p.split("–")[1]]
    _require(not c20["same_window"] and len(punta) == 1, f"{fri} 2.0TD wholesale window starts in punta")
    _require(r["periods"] == ["P2"], f"{fri} 2.0TD cheapest estimated window is llano")
    _require(w["omie_eur_mwh"] == 0, f"{fri} cheapest wholesale window averages €0")
    _require(all(f["windows"][sat, t]["same_window"] for t in TARIFFS), f"{sat} rankings agree")
    _require(set(sat20["periods"]) == {"P3"}, f"{sat} is all valle")
    _require(f["ytd"] > f["prev"], "more zero-price days than last year")
    bands = ", ".join(f"{lo:02d}–{hi:02d} {share:.1%}" for lo, hi, share in f["bands"])
    since = sm.MTU15_SINCE
    blocks = [
        f"_Numbers in this section are generated by `scripts/build_case_study.py` from `data/history/`, as "
        f"of {_d(AS_OF, year=True)}; CI fails if they go stale. Figures in €/kWh are estimates._",
        "**Problem.** Spain's day-ahead power price now sits at €0 for hours at a time, and that gets read "
        'as "free electricity". I wanted to know how often it happens, at which hours, and what a customer '
        "would actually pay in those hours.",
        f"**Data.** OMIE day-ahead prices for the Spanish zone: {f['n_days']} delivery days from "
        f"{_d(f['first'], year=True)[4:]} to {_d(AS_OF, year=True)[4:]} ({f['n_periods']:,} periods; hourly "
        f"at first, {sm.expected_mtu(AS_OF)}-minute since {_d(since, year=True)[4:]}). Network tolls and "
        f"charges are the official {tf.VALID_FROM.year} BOE tables. PVPC from the public ESIOS archive is "
        f"context only.",
        f"**Method.** A scheduled pipeline fetches each day's file, validates it, flags periods at or below "
        f"€0 and appends them to a versioned CSV history. A calculator then adds assumed losses "
        f"({a.loss_factor:.0%}), adjustment services ({a.adjustment_services_eur_mwh:.0f} €/MWh) and "
        f"retailer margin ({a.retailer_margin_eur_mwh:.0f} €/MWh), the toll and charge of each period's "
        f"tariff band, and taxes. The result is an estimate, not a bill.",
        "**Findings.**",
        f"- {f['ytd']} days in {year} had at least one period at or below €0, against {f['prev']} by the "
        f"same date in {last}. The {last} figure is mostly from hourly data, which flags slightly less "
        f"often.",
        f"- {f['ytd_share']:.1%} of {year} periods were at or below €0. By local hour band: {bands}.",
        f"- On {_d(fri)} the cheapest wholesale 3 hours were {_span(w)} at {w['omie_eur_mwh']:.2f} €/MWh. "
        f"On 2.0TD that window costs an estimated {w['estimate_eur_kwh']:.3f} €/kWh, because {punta[0]} is "
        f"punta. The cheapest estimated 3 hours were {_span(r)} (llano) at {r['estimate_eur_kwh']:.3f} "
        f"€/kWh. On 3.0TD the same comparison is {w30['estimate_eur_kwh']:.3f} against "
        f"{r30['estimate_eur_kwh']:.3f} €/kWh.",
        f"- On {_d(sat)}, all valle, both rankings pick {_span(sat20)}.",
        _table(f),
        "**Engineering choices.** Pure functions with I/O at the edges. All period arithmetic is in UTC, so "
        "daylight-saving days need no special cases. Outputs carry no wall-clock time, so a rerun is "
        "byte-identical and git diffs show only real changes. Every assumption has a note and every "
        "regulated value a BOE reference.",
        "**Tests and CI.** pytest on stored fixtures with no network: daylight-saving days, holidays, "
        "hand-computed tariff arithmetic, and a cross-check of my tolls against the values ESIOS publishes. "
        "ruff for lint and format. GitHub Actions runs CI and the daily schedule.",
        "**What I'd do next.** A D+2 forecast of zero-price hours, with a backtest against this history. A "
        "weekly summary. A concierge test with solar installers and asesores energéticos, to see whether "
        "anyone would use it.",
    ]
    out = []
    for prev, block in zip(["", *blocks], blocks, strict=False):
        sep = "\n" if prev.startswith("- ") and block.startswith("- ") else "\n\n"
        out.append((sep if out else "") + (block if block.startswith("|") else _wrap(block)))
    return "".join(out)


def splice(readme: str, body: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1 or readme.index(START) > readme.index(END):
        raise SystemExit(f"README.md needs exactly one {START} ... {END} block")
    head, tail = readme.split(START)[0], readme.split(END)[1]
    return f"{head}{START}\n{body}\n{END}{tail}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=str(ROOT), help="repo root (default: this script's repo)")
    p.add_argument("--check", action="store_true", help="exit 1 if README.md is stale; write nothing")
    a = p.parse_args(argv)
    path = Path(a.root) / "README.md"
    old = path.read_text(encoding="utf-8")
    new = splice(old, render(facts(Path(a.root))))
    if new == old:
        print("case study: up to date")
        return 0
    if a.check:
        print("case study: README.md is stale; run python scripts/build_case_study.py", file=sys.stderr)
        return 1
    path.write_text(new, encoding="utf-8", newline="\n")
    print(f"case study: wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
