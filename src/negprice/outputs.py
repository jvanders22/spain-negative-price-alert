"""Published outputs, all derived from data/history/ (so ``rebuild`` can regenerate them offline).

site/latest.json, site/feed.xml, site/charts/<date>.png + latest.png, site/index.html, drafts/<date>.md.
Nothing reads the wall clock, so rebuilding from the same history is byte-identical.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import draft, history  # noqa: E402
from . import summary as sm  # noqa: E402

SCHEMA_VERSION = 1
REPO_URL = "https://github.com/jvanders22/spain-negative-price-alert"
FOOTER = "Source: OMIE day-ahead (wholesale, not retail). github.com/jvanders22/spain-negative-price-alert"
ATTRIBUTION = "Prices: OMIE (omie.es), day-ahead market, Spain zone. Wholesale prices, not your retail bill."
FEED_DAYS = 30
COLOR_LE0, COLOR_CHEAP, COLOR_OTHER = "#eb6834", "#2a78d6", "#c3c2b7"
STRIP_COLORS = ["#f0efec", "#b7d3f6", "#6da7ec", "#2a78d6", "#184f95"]  # n_le0: 0, low .. high


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def summaries(root: Path, dates: list[dt.date] | None = None) -> list[dict]:
    """Summaries for ``dates`` (default: all stored), oldest first, with YTD counts from daily.csv."""
    daily = history.read_daily(root)
    wanted = None if dates is None else set(dates)
    return [
        sm.summarise(g, daily, src)
        for day, g, src in history.day_frames(history.read_periods(root))
        if wanted is None or day in wanted
    ]


def unit(s: dict) -> str:
    return "quarter-hours" if s["mtu_min"] == 15 else "hours"


def latest_json(s: dict) -> str:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "units": "EUR/MWh",
        "timezone": sm.TZ,
        "attribution": ATTRIBUTION,
        **s,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def headline(s: dict) -> str:
    label = f"{s['weekday']} {s['date']}"
    if s["is_alert"]:
        wins = ", ".join(s["le0_windows"])
        return f"ALERT: {label} — {s['n_le0']} of {s['n_periods']} {unit(s)} at or below €0 ({wins})"
    c = s["cheapest_3h"]
    return f"Cheapest 3h: {label} — {c['start']}–{c['end']} at {c['mean']:.2f} €/MWh"


def _pubdate(date: str) -> str:
    # Day-ahead results are published around 13:00 Madrid on the day before delivery.
    d = dt.date.fromisoformat(date) - dt.timedelta(days=1)
    return format_datetime(pd.Timestamp(f"{d} 13:00").tz_localize(sm.TZ).to_pydatetime())


def feed_xml(items: list[dict]) -> str:
    """RSS 2.0, newest first. ``items`` are summaries."""
    newest = sorted(items, key=lambda s: s["date"], reverse=True)[:FEED_DAYS]
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        "<title>Spain day-ahead zero-price alert</title>",
        f"<link>{REPO_URL}</link>",
        "<description>Daily OMIE day-ahead prices for Spain: zero/negative and cheap periods. "
        "Wholesale, not retail.</description>",
        "<language>en</language>",
    ]
    if newest:
        out.append(f"<lastBuildDate>{_pubdate(newest[0]['date'])}</lastBuildDate>")
    for s in newest:
        desc = (
            f"{s['n_le0']} of {s['n_periods']} {unit(s)} ≤ €0 ({s['n_neg']} negative), "
            f"{s['n_cheap']} ≤ €{sm.CHEAP_EUR_MWH:.0f}. Min {s['min_price']:.2f} at {s['min_time']}, "
            f"mean {s['mean_price']:.2f}, max {s['max_price']:.2f} €/MWh. "
            f"Cheapest 3h {s['cheapest_3h']['start']}–{s['cheapest_3h']['end']}. {ATTRIBUTION}"
        )
        out += [
            "<item>",
            f"<title>{escape(headline(s))}</title>",
            f"<link>{REPO_URL}</link>",
            f"<description>{escape(desc)}</description>",
            f'<guid isPermaLink="false">{s["date"]}</guid>',
            f"<pubDate>{_pubdate(s['date'])}</pubDate>",
            "</item>",
        ]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def chart(day_df: pd.DataFrame, s: dict, paths: list[Path]) -> None:
    df = sm.classify(day_df.sort_values("ts_utc").reset_index(drop=True))
    colors = [
        COLOR_LE0 if le0 else COLOR_CHEAP if cheap else COLOR_OTHER
        for le0, cheap in zip(df.le0, df.cheap, strict=True)
    ]
    local = df["ts_utc"].dt.tz_convert(sm.TZ)
    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=100)
    # Prices near zero make their bars invisible, so flagged periods also get a full-height tint.
    for i, (le0, cheap) in enumerate(zip(df.le0, df.cheap, strict=True)):
        if cheap:
            ax.axvspan(i, i + 1, color=COLOR_LE0 if le0 else COLOR_CHEAP, alpha=0.15, linewidth=0)
    ax.bar(range(len(df)), df["price_es"], width=0.85, color=colors, align="edge")
    ax.axhline(0, color="#52514e", linewidth=0.8)
    ticks = [i for i, t in enumerate(local) if t.minute == 0]
    ax.set_xticks(ticks, [f"{local[i]:%H}" for i in ticks], fontsize=7)
    ax.set_xlim(0, len(df))
    ax.set_ylabel("€/MWh")
    ax.set_xlabel("Hour (Europe/Madrid)")
    ax.grid(axis="y", color="#e5e4df", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_title(
        f"Spain day-ahead price, {s['weekday']} {s['date']}: {s['n_le0']} of {s['n_periods']} {unit(s)} ≤ €0 "
        f"({s['n_neg']} negative), {s['n_cheap']} ≤ €{sm.CHEAP_EUR_MWH:.0f}",
        fontsize=10,
        loc="left",
    )
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in (COLOR_LE0, COLOR_CHEAP, COLOR_OTHER)]
    labels = ["≤ €0", f"€0–{sm.CHEAP_EUR_MWH:.0f}", "other"]
    ax.legend(handles, labels, fontsize=8, frameon=False, loc="upper left", ncols=3)
    fig.text(0.01, 0.01, FOOTER, fontsize=7, color="#52514e")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, format="png", metadata={"Software": None})
    plt.close(fig)


def _strip_color(n_le0: int) -> str:
    return STRIP_COLORS[0 if n_le0 == 0 else min(4, 1 + n_le0 // 12)]


def index_html(s: dict, recent: list[dict]) -> str:
    e = html.escape
    c = s["cheapest_3h"]
    rows = [
        (f"Periods ≤ €0 ({unit(s)})", f"{s['n_le0']} of {s['n_periods']}"),
        ("Windows ≤ €0", ", ".join(s["le0_windows"]) or "none"),
        ("Negative periods", f"{s['n_neg']} ({', '.join(s['neg_windows']) or 'none'})"),
        (f"Cheap periods (≤ €{sm.CHEAP_EUR_MWH:.0f}/MWh)",
         f"{s['n_cheap']} ({', '.join(s['cheap_windows']) or 'none'})"),
        ("Cheapest 3 hours", f"{c['start']}–{c['end']}, mean {c['mean']:.2f} €/MWh"),
        ("Min / mean / max", f"{s['min_price']:.2f} ({s['min_time']}) / {s['mean_price']:.2f} / "
         f"{s['max_price']:.2f} ({s['max_time']}) €/MWh"),
        ("Midday (10–18) vs evening (19–23)", f"{s['midday_mean']:.2f} vs {s['evening_mean']:.2f} €/MWh "
         f"(spread {s['evening_minus_midday']:.2f})"),
        (f"Zero-price days in {s['date'][:4]} so far", f"{s['ytd_zero_days']} "
         f"(same date last year: {s['ytd_zero_days_prev_year']}; last 365 days: {s['zero_days_last_365']})"),
    ]  # fmt: skip
    table = "\n".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    cells = "\n".join(
        f'<span class="cell" style="background:{_strip_color(r["n_le0"])}" '
        f'title="{e(r["date"])}: {r["n_le0"]} of {r["n_periods"]} periods ≤ €0">{int(r["date"][8:])}</span>'
        for r in recent
    )
    status = "Zero-price day" if s["is_alert"] else "No zero-price periods"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spain day-ahead zero-price alert — {e(s["date"])}</title>
<link rel="alternate" type="application/rss+xml" href="feed.xml" title="Daily feed">
<style>
body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem;
  color: #0b0b0b; background: #fcfcfb; line-height: 1.45; }}
h1 {{ font-size: 1.4rem; margin-bottom: .2rem; }}
.status {{ font-size: 1.1rem; color: #52514e; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ text-align: left; padding: .3rem .8rem .3rem 0; border-bottom: 1px solid #e5e4df;
  vertical-align: top; }}
th {{ font-weight: 600; color: #52514e; }}
img {{ max-width: 100%; height: auto; }}
.strip {{ display: flex; flex-wrap: wrap; gap: 2px; }}
.cell {{ width: 1.8rem; height: 1.8rem; display: inline-flex; align-items: center; justify-content: center;
  font-size: .7rem; border-radius: 4px; color: #0b0b0b; }}
footer {{ margin-top: 2rem; font-size: .85rem; color: #52514e; }}
</style>
</head>
<body>
<h1>Spain day-ahead power: {e(s["weekday"])} {e(s["date"])}</h1>
<p class="status">{status}. Wholesale OMIE prices, Spain zone,
{s["mtu_min"]}-minute periods (Europe/Madrid).</p>
<img src="charts/latest.png"
  alt="Bar chart of {e(s["date"])} day-ahead prices; {s["n_le0"]} periods at or below zero">
<table>
{table}
</table>
<h2>Last {len(recent)} days</h2>
<p>Each cell is a delivery day, shaded by the number of periods at or below €0 (grey = none).</p>
<div class="strip">
{cells}
</div>
<h2>Data</h2>
<ul>
<li><a href="latest.json">latest.json</a> — latest day summary (schema_version {SCHEMA_VERSION})</li>
<li><a href="feed.xml">feed.xml</a> — RSS, one item per day</li>
<li>Full history (CSV): <a href="{REPO_URL}/tree/main/data/history">data/history</a></li>
</ul>
<footer>{e(ATTRIBUTION)} Source file: {e(s["source"]["file"])}. <a href="{REPO_URL}">Code</a>.</footer>
</body>
</html>
"""


def draft_md(s: dict) -> str:
    text = draft.post_text(s)
    c = s["cheapest_3h"]
    facts = [
        f"date: {s['date']} ({s['weekday']}), {s['n_periods']} periods of {s['mtu_min']} min",
        f"periods <= 0: {s['n_le0']} ({', '.join(s['le0_windows']) or 'none'}); negative: {s['n_neg']}",
        f"periods <= {sm.CHEAP_EUR_MWH:.0f} EUR/MWh: {s['n_cheap']}",
        f"min {s['min_price']:.2f} at {s['min_time']}, mean {s['mean_price']:.2f}, max {s['max_price']:.2f}",
        f"midday mean {s['midday_mean']:.2f}, evening mean {s['evening_mean']:.2f}",
        f"cheapest 3h {c['start']}-{c['end']} mean {c['mean']:.2f}",
        f"zero-price days YTD {s['ytd_zero_days']}, same date last year {s['ytd_zero_days_prev_year']}, "
        f"last 365 days {s['zero_days_last_365']}",
        f"source: {s['source']['file']} {s['source']['url'] or ''}".rstrip(),
    ]
    bullets = "\n".join(f"- {f}" for f in facts)
    return (
        f"# DRAFT — not posted. Josh approves every post.\n\n```text\n{text}\n```\n\n"
        f"Characters: {len(text)} / {draft.MAX_CHARS}\n\n## Facts\n\n{bullets}\n"
    )


def complete(root: Path, day: dt.date) -> bool:
    """True when the per-day and site outputs for ``day`` exist."""
    root = Path(root)
    need = [root / "drafts" / f"{day}.md", root / "site" / "charts" / f"{day}.png"]
    need += [root / "site" / n for n in ("latest.json", "feed.xml", "index.html", "charts/latest.png")]
    return all(p.exists() for p in need)


def build(root: Path, chart_dates: list[dt.date] = ()) -> dict | None:
    """Regenerate site/ from history. Charts and drafts are written for the latest day and ``chart_dates``.
    Returns the latest summary (None if history is empty)."""
    root = Path(root)
    daily = history.read_daily(root)
    if daily.empty:
        return None
    dates = sorted(dt.date.fromisoformat(d) for d in daily["date"])
    recent = summaries(root, dates[-FEED_DAYS:])
    latest = recent[-1]
    site = root / "site"
    _write_text(site / "latest.json", latest_json(latest))
    _write_text(site / "feed.xml", feed_xml(recent))
    _write_text(site / "index.html", index_html(latest, recent))
    periods = history.read_periods(root)
    for day in sorted({*chart_dates, dates[-1]}):
        s = latest if day == dates[-1] else summaries(root, [day])[0]
        paths = [site / "charts" / f"{day}.png"] + (
            [site / "charts" / "latest.png"] if day == dates[-1] else []
        )
        chart(periods[periods["date"] == day.isoformat()], s, paths)
        _write_text(root / "drafts" / f"{day}.md", draft_md(s))
    return latest
