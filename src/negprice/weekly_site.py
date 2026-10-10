"""Weekly summary outputs: site/weekly/<sunday>.json + .png (and latest.*), drafts/weekly-<sunday>-<lang>.md.

Derived from data/history only (no network, no wall clock), so building a week again is byte-identical.
Drafts go to drafts/, which is gitignored: they are never committed or published.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import history, outputs, weekly, weekly_draft  # noqa: E402
from . import summary as sm  # noqa: E402

SCHEMA_VERSION = 1
PX = 1080  # the chart is PX × PX pixels
FOOTER = (
    "Source: OMIE day-ahead (wholesale, not retail). 2.0TD figure is an estimate.",
    "github.com/jvanders22/spain-negative-price-alert",
)
INK, MUTED, RULE = "#0b0b0b", "#52514e", "#e5e4df"


def weekly_dir(root: Path) -> Path:
    return Path(root) / "site" / "weekly"


def draft_path(root: Path, sunday: dt.date, lang: str) -> Path:
    return Path(root) / "drafts" / f"weekly-{sunday}-{lang}.md"


def title(f: dict) -> str:
    return f"Spain day-ahead power, week of {f['week']['label']}"


def weekly_json(f: dict) -> str:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "kind": "weekly",
        "units": "EUR/MWh",
        "timezone": sm.TZ,
        "attribution": outputs.ATTRIBUTION,
        **f,
        "chart": f"weekly/{f['week']['end']}.png",
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _signed(x: float) -> str:
    return f"{x:+.2f}"


def panel_rows(f: dict) -> list[tuple[str, str, str]]:
    """The five headline numbers as (value, label, detail) for the chart panel."""
    z, lo, s = f["zero_or_below"], f["lowest"], f["midday_evening"]
    c, y, wow = f["cheapest_3h"], f["zero_days_ytd"], f["week_over_week"]
    r, ws = c["retail"], c["wholesale"]
    low_day = weekly.day_label(dt.date.fromisoformat(lo["date"]))
    wholesale = f"wholesale {ws['weekday']} {ws['start']}–{ws['end']}, {ws['omie_eur_mwh']:.2f} €/MWh"
    if r["available"]:
        retail_day = weekly.day_label(dt.date.fromisoformat(r["date"]))
        retail = (f"{r['estimate_eur_kwh']:.3f} €/kWh", f"{retail_day} {r['start']}–{r['end']} · {wholesale}")
    else:
        retail = ("n/a", f"no 2026 tariff tables for this week · {wholesale}")
    return [
        (
            f"{z['n_le0']}",
            f"periods at or below €0 ({z['n_neg']} negative)",
            f"on {z['days_with_le0']} of {z['days']} days"
            + (f" · previous week: {wow['n_le0_prev']} ({wow['n_le0_change']:+d})" if wow else ""),
        ),
        (f"{lo['price']:.2f} €/MWh", "lowest price of the week", f"{low_day} at {lo['time']}"),
        (
            f"{_signed(s['spread'])} €/MWh",
            "evening minus midday" + (f" (previous week {_signed(wow['spread_prev'])})" if wow else ""),
            f"midday 10–18h {s['midday_mean']:.2f} · evening 19–23h {s['evening_mean']:.2f} €/MWh",
        ),
        (retail[0], "cheapest 2.0TD 3 hours (estimate, taxes included)", retail[1]),
        (
            f"{y['ytd_zero_days']} days",
            f"with a price at or below €0 in {y['year']} so far",
            f"{y['ytd_zero_days_prev_year']} by the same date in {y['prev_year']}",
        ),
    ]


def chart(days: list, f: dict, paths: list[Path]) -> None:
    """One PX × PX PNG: the week's prices at native resolution (≤ €0 periods highlighted) above a panel
    with the five numbers. ``days`` is ``weekly.week_days`` output."""
    fig = plt.figure(figsize=(PX / 100, PX / 100), dpi=100, facecolor="white")
    fig.text(0.05, 0.955, title(f), fontsize=20.5, fontweight="bold", color=INK, va="center")
    fig.text(
        0.05,
        0.918,
        "Wholesale price per period, €/MWh. Orange = at or below €0.",
        fontsize=14,
        color=MUTED,
        va="center",
    )
    ax = fig.add_axes((0.105, 0.575, 0.865, 0.305))
    start, _ = sm.day_bounds_utc(days[0][0])
    ticks, labels = [], []
    for day, df, _ in days:
        lo, hi = ((t - start) / pd.Timedelta(hours=1) for t in sm.day_bounds_utc(day))
        x = (df["ts_utc"] - start) / pd.Timedelta(hours=1)
        width = df["mtu_min"] / 60
        le0 = df["price_es"] <= 0
        # Prices at zero make their bars invisible, so flagged periods also get a full-height tint.
        for xi, wi in zip(x[le0], width[le0], strict=True):
            ax.axvspan(xi, xi + wi, color=outputs.COLOR_LE0, alpha=0.3, linewidth=0)
        colors = [outputs.COLOR_LE0 if flag else outputs.COLOR_CHEAP for flag in le0]
        ax.bar(x, df["price_es"], width=width, color=colors, align="edge", linewidth=0)
        if lo > 0:
            ax.axvline(lo, color="#ffffff", linewidth=1.5)
        ticks.append((lo + hi) / 2)
        labels.append(f"{day:%a}\n{day.day}")
        end = hi
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_xlim(0, end)
    ax.set_xticks(ticks, labels, fontsize=14, color=INK)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.tick_params(axis="y", labelsize=13, colors=MUTED, length=0)
    ax.grid(axis="y", color=RULE, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)

    top, step = 0.455, 0.083
    for i, (value, label, detail) in enumerate(panel_rows(f)):
        y = top - i * step
        fig.add_artist(plt.Line2D([0.05, 0.95], [y + step / 2] * 2, color=RULE, linewidth=1))
        fig.text(0.05, y, value, fontsize=22, fontweight="bold", color=INK, va="center")
        fig.text(0.35, y + 0.016, label, fontsize=15, fontweight="bold", color=INK, va="center")
        fig.text(0.35, y - 0.018, detail, fontsize=13.5, color=MUTED, va="center")
    fig.add_artist(plt.Line2D([0.05, 0.95], [top - 4.5 * step] * 2, color=RULE, linewidth=1))
    fig.text(0.05, 0.047, FOOTER[0], fontsize=12, color=MUTED, va="center")
    fig.text(0.05, 0.022, FOOTER[1], fontsize=12, color=MUTED, va="center")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, format="png", metadata={"Software": None})
    plt.close(fig)


def _sync_latest(root: Path) -> bool:
    """Make latest.json/latest.png copies of the newest stored week. Returns True if a file changed."""
    folder = weekly_dir(root)
    newest = max(p.stem for p in folder.glob("????-??-??.json"))
    changed = False
    for ext in ("json", "png"):
        src, dst = folder / f"{newest}.{ext}", folder / f"latest.{ext}"
        if src.exists() and (not dst.exists() or dst.read_bytes() != src.read_bytes()):
            dst.write_bytes(src.read_bytes())
            changed = True
    return changed


def write(root: Path, sunday: dt.date) -> tuple[dict, bool]:
    """Build the week ending ``sunday``. Returns ``(facts, changed)``; ``changed`` is False when
    site/weekly/<sunday>.json already exists with identical content (and the PNG and latest.* are in
    place), in which case nothing under site/ is rewritten. Drafts are always (re)written: they are local
    files, not committed."""
    root = Path(root)
    periods = history.read_periods(root)
    f = weekly.facts(periods, history.read_daily(root), sunday)
    text = weekly_json(f)
    json_path, png_path = (weekly_dir(root) / f"{sunday}.{ext}" for ext in ("json", "png"))
    built = json_path.exists() and json_path.read_text(encoding="utf-8") == text and png_path.exists()
    if not built:
        chart(weekly.week_days(periods, sunday), f, [png_path])
        outputs._write_text(json_path, text)
    changed = _sync_latest(root) or not built
    for lang in weekly_draft.LANGS:
        outputs._write_text(draft_path(root, sunday, lang), weekly_draft.draft_md(f, lang))
    return f, changed
