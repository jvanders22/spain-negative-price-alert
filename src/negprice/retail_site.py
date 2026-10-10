"""Site outputs for the retail estimate: site/estimate/latest.json, per-tariff CSVs, the index.html
section and the "Wholesale is not your bill" page. Derived from history plus the PVPC cache only (no
network, no wall clock), so rebuilds are byte-identical.
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import sys
from pathlib import Path

import pandas as pd

from . import pvpc, retail
from . import summary as sm
from . import tariffs as tf

SCHEMA_VERSION = 1
SITE_TARIFFS = ("2.0TD", "3.0TD")
EXPLAINER = "wholesale-vs-bill.html"
# Real Decreto 446/2023, de 13 de junio: share of the PVPC energy price indexed to futures in 2026.
PVPC_RD_URL = "https://www.boe.es/eli/es/rd/2023/06/13/446"
PVPC_FUTURES_SHARE_2026 = 0.55
SECTION_TITLE = "What a 2.0TD / 3.0TD customer might pay (estimate)"
STYLE = """body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem;
  color: #0b0b0b; background: #fcfcfb; line-height: 1.45; }
h1 { font-size: 1.4rem; margin-bottom: .2rem; }
table { border-collapse: collapse; margin: 1rem 0; }
th, td { text-align: left; padding: .3rem .8rem .3rem 0; border-bottom: 1px solid #e5e4df;
  vertical-align: top; }
th { font-weight: 600; color: #52514e; }
.note, footer { font-size: .85rem; color: #52514e; }
footer { margin-top: 2rem; }"""


def cached_pvpc(root: Path, day: dt.date) -> pd.DataFrame | None:
    """PVPC from the cache, or None (with a line on stderr) when the cache file can't be read or parsed.
    PVPC is a context column only, so a bad cache file must never stop the site build."""
    try:
        return pvpc.load_cached(root, day)
    except Exception as e:  # noqa: BLE001 - PVPC is optional context
        path = pvpc.cache_path(root, day)
        print(f"pvpc {day}: ignoring cache {path} ({type(e).__name__}: {e})", file=sys.stderr)
        return None


def bundle(root: Path, day: dt.date, day_df: pd.DataFrame) -> dict | None:
    """Estimates and reports for ``SITE_TARIFFS`` with default assumptions and cached PVPC. None when the
    day is outside the year of the tariff tables (the site then says so instead of showing numbers)."""
    try:
        tf.check_date(day)
    except tf.TariffYearError:
        return None
    a = retail.Assumptions()
    pv = cached_pvpc(root, day)
    frames = {t: retail.estimate(day_df, t, a, pv) for t in SITE_TARIFFS}
    return {
        "date": day.isoformat(),
        "assumptions": a,
        "frames": frames,
        "reports": {t: retail.day_report(f, a) for t, f in frames.items()},
    }


def estimate_csv(est_df: pd.DataFrame) -> str:
    """Per-period CSV: fixed formats (2 dp €/MWh, 6 dp €/kWh, empty for missing), "\\n" endings."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(retail.COLUMNS)
    for row in est_df.to_dict("records"):
        out = []
        for col in retail.COLUMNS:
            v = row[col]
            if col == "ts_utc":
                out.append(f"{v:%Y-%m-%dT%H:%M:%SZ}")
            elif col == "omie_eur_mwh":
                out.append(f"{sm.r2(v):.2f}")
            elif col.endswith("_eur_kwh"):
                out.append("" if pd.isna(v) else f"{retail.r6(v):.6f}")
            else:
                out.append(str(v))
        w.writerow(out)
    return buf.getvalue()


def estimate_json(b: dict | None, day: dt.date) -> str:
    if b is None:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "label": retail.LABEL,
            "date": day.isoformat(),
            "available": False,
            "reason": f"tariff tables cover {tf.VALID_FROM}..{tf.VALID_TO} only",
        }
    else:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "label": retail.LABEL,
            "date": b["date"],
            "available": True,
            "disclaimer": retail.DISCLAIMER,
            "timezone": sm.TZ,
            "tariffs": b["reports"],
        }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _kwh(v: float | None) -> str:
    return "n/a" if v is None or pd.isna(v) else f"{v:.3f}"


def _window_rows(b: dict) -> str:
    e = html.escape
    rows = []
    for minutes in retail.WINDOW_MINUTES:
        span = f"{minutes // 60} h"
        for t in SITE_TARIFFS:
            c = b["reports"][t]["cheapest_windows"][str(minutes)]
            r, w = c["retail"], c["wholesale"]
            rows.append(
                f"<tr><th>{e(t)}, {span}</th>"
                f"<td>{e(r['start'])}–{e(r['end'])}: {_kwh(r['estimate_eur_kwh'])} €/kWh "
                f"({e('/'.join(r['periods']))})</td>"
                f"<td>{e(w['start'])}–{e(w['end'])}: {w['omie_eur_mwh']:.2f} €/MWh → "
                f"{_kwh(w['estimate_eur_kwh'])} €/kWh ({e('/'.join(w['periods']))})</td></tr>"
            )
    return "\n".join(rows)


def _assumption_items(a: retail.Assumptions) -> str:
    e = html.escape
    return "\n".join(
        f"<li><code>{e(k)}</code> = {v['value']!r}. {e(v['note'])}</li>" for k, v in a.describe().items()
    )


def _source_items() -> str:
    e = html.escape
    return "\n".join(
        f'<li><a href="{e(s["url"])}">{e(s["title"])}</a> ({e(s["boe_id"])}; {e(s["boe"])}), table '
        f"“{e(s['table'])}”. Verified {e(s['verified'])}.</li>"
        for s in tf.SOURCES.values()
    )


def index_section(b: dict | None) -> str:
    """HTML block for site/index.html."""
    e = html.escape
    if b is None:
        return (
            f"<h2>{e(SECTION_TITLE)}</h2>\n"
            f'<p class="note">No estimate for this day: the tariff tables in this version cover '
            f"{tf.VALID_FROM} to {tf.VALID_TO} only. "
            f'<a href="{EXPLAINER}">Why wholesale is not your bill</a>.</p>'
        )
    h20, h30 = (b["reports"][t]["hourly"] for t in SITE_TARIFFS)
    rows = "\n".join(
        f"<tr><td>{e(x['hour'])}</td><td>{x['omie_eur_mwh']:.2f}</td>"
        f"<td>{_kwh(x['estimate_eur_kwh'])} ({x['period']})</td>"
        f"<td>{_kwh(y['estimate_eur_kwh'])} ({y['period']})</td><td>{_kwh(x['pvpc_eur_kwh'])}</td></tr>"
        for x, y in zip(h20, h30, strict=True)
    )
    r20 = b["reports"]["2.0TD"]
    pvpc_note = "" if r20["pvpc"]["available"] else " Not available for this day when the page was built."
    return f"""<h2>{e(SECTION_TITLE)}</h2>
<p>Estimated price per kWh, taxes included, if you paid the day-ahead price plus tolls, charges and
taxes on {e(r20["weekday"])} {e(b["date"])} (peninsula; 2.0TD day type {e(r20["day_type"])},
season {e(r20["season"])}). An <strong>estimate</strong> built on stated assumptions, not a bill:
<a href="{EXPLAINER}">Wholesale is not your bill</a>.</p>
<p><strong>2.0TD:</strong> {e(r20["takeaway"])}<br>
<strong>3.0TD:</strong> {e(b["reports"]["3.0TD"]["takeaway"])}</p>
<table>
<tr><th>Cheapest window</th><th>By estimated price paid</th><th>By wholesale price (and what it would
cost)</th></tr>
{_window_rows(b)}
</table>
<table>
<tr><th>Hour</th><th>OMIE €/MWh</th><th>2.0TD estimate €/kWh</th><th>3.0TD estimate €/kWh</th>
<th>PVPC €/kWh (context)</th></tr>
{rows}
</table>
<p class="note">Hourly means by local hour (Europe/Madrid); tariff period in brackets. {e(retail.PVPC_NOTE)}
{pvpc_note}</p>
<p class="note">{e(retail.DISCLAIMER)} Per-period data:
<a href="estimate/latest.json">estimate/latest.json</a>,
<a href="estimate/{e(b["date"])}-2.0TD.csv">2.0TD CSV</a>,
<a href="estimate/{e(b["date"])}-3.0TD.csv">3.0TD CSV</a>.</p>
<p class="note">Assumptions (defaults):</p>
<ul class="note">
{_assumption_items(b["assumptions"])}
</ul>"""


def worked_example(b: dict) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Column heads and rows of the 2.0TD bill stack: the day's cheapest and dearest wholesale periods as
    they fall, then the cheapest wholesale price again in each 2.0TD period (what a weekday would add)."""
    df, a = b["frames"]["2.0TD"], b["assumptions"]
    cases = []
    for label, i in (("cheapest", df["omie_eur_mwh"].idxmin()), ("dearest", df["omie_eur_mwh"].idxmax())):
        row = df.loc[i]
        when = sm._hhmm(row["ts_utc"], dt.date.fromisoformat(b["date"]))
        cases.append((f"{b['date']} {when}, {label} wholesale", row["omie_eur_mwh"], row["period"]))
    low = float(df["omie_eur_mwh"].min())
    for p in ("P3", "P2", "P1"):
        cases.append((f"same {low:.2f} €/MWh in a weekday {tf.PERIOD_NAMES_20TD[p]} hour", low, p))
    stacks = [(p, omie, retail.price_stack(omie, *tf.energy_terms("2.0TD", p), a)) for _, omie, p in cases]
    heads = [c[0] for c in cases]
    services = a.adjustment_services_eur_mwh / 1000
    lines = [
        ("2.0TD period", [tf.period_name("2.0TD", p) for p, _, _ in stacks]),
        ("OMIE day-ahead, €/MWh", [f"{o:.2f}" for _, o, _ in stacks]),
        ("OMIE day-ahead", [f"{o / 1000:.6f}" for _, o, _ in stacks]),
        ("+ adjustment services (assumed)", [f"{services:.6f}" for _ in stacks]),
        ("+ capacity payment", [f"{s['capacity_eur_kwh']:.6f}" for _, _, s in stacks]),
        (
            f"+ losses ({a.loss_factor:.0%}, assumed) on the three lines above",
            [f"{(o / 1000 + services + s['capacity_eur_kwh']) * a.loss_factor:.6f}" for _, o, s in stacks],
        ),
        ("+ retailer margin (assumed)", [f"{a.retailer_margin_eur_mwh / 1000:.6f}" for _ in stacks]),
        ("= energy", [f"{s['energy_eur_kwh']:.6f}" for _, _, s in stacks]),
        ("+ peaje (network toll)", [f"{s['peaje_eur_kwh']:.6f}" for _, _, s in stacks]),
        ("+ cargo (system charge)", [f"{s['cargo_eur_kwh']:.6f}" for _, _, s in stacks]),
        ("= before taxes", [f"{s['pre_tax_eur_kwh']:.6f}" for _, _, s in stacks]),
        (f"+ electricity tax IEE ({a.iee:.4%})", [f"{s['iee_eur_kwh']:.6f}" for _, _, s in stacks]),
        (f"+ IVA ({a.iva:.0%}, on top of IEE)", [f"{s['iva_eur_kwh']:.6f}" for _, _, s in stacks]),
        ("= estimate, €/kWh", [f"{s['estimate_eur_kwh']:.6f}" for _, _, s in stacks]),
    ]
    return heads, lines


def _terms_table(tariff: str) -> str:
    energy = "\n".join(
        f"<tr><td>{tf.period_name(tariff, r['period'])}</td><td>{r['peaje_eur_kwh']:.6f}</td>"
        f"<td>{r['cargo_eur_kwh']:.6f}</td><td>{r['regulated_eur_kwh']:.6f}</td>"
        f"<td>{r['capacity_eur_kwh']:.6f}</td></tr>"
        for r in tf.energy_table(tariff)
    )
    power = "\n".join(
        f"<tr><td>{r['period']}</td><td>{r['peaje_eur_kw_year']:.6f}</td><td>{r['cargo_eur_kw_year']:.6f}</td>"
        f"<td>{r['total_eur_kw_year']:.6f}</td></tr>"
        for r in tf.power_table(tariff)
    )
    return f"""<h3>{tariff}</h3>
<table>
<tr><th>Period</th><th>Peaje €/kWh</th><th>Cargo €/kWh</th><th>Peaje + cargo €/kWh</th>
<th>Capacity payment €/kWh (busbar)</th></tr>
{energy}
</table>
<table>
<tr><th>Power period</th><th>Peaje €/kW·year</th><th>Cargo €/kW·year</th><th>Total €/kW·year</th></tr>
{power}
</table>"""


def explainer_html(b: dict | None, day: dt.date) -> str:
    """The static "Wholesale is not your bill" page; the worked example is computed from the latest day."""
    e = html.escape
    p1, p2, p3 = (r["regulated_eur_kwh"] for r in tf.energy_table("2.0TD"))
    if b is None:
        example = (
            f'<p class="note">No worked example for {day}: the tariff tables in this version cover '
            f"{tf.VALID_FROM} to {tf.VALID_TO} only.</p>"
        )
    else:
        heads, lines = worked_example(b)
        head = "".join(f"<th>{e(h)}</th>" for h in heads)
        body = "\n".join(
            f"<tr><th>{e(k)}</th>{''.join(f'<td>{e(v)}</td>' for v in vals)}</tr>" for k, vals in lines
        )
        r20 = b["reports"]["2.0TD"]
        example = f"""<p>Computed from the OMIE prices for {e(r20["weekday"])} {e(b["date"])} with the
default assumptions; €/kWh unless stated. The first two columns are that day's cheapest and most
expensive wholesale periods as they actually fall; the last three put the same cheapest wholesale price
into each 2.0TD period, which is what a working weekday does to it.</p>
<table>
<tr><th>2.0TD, per kWh</th>{head}</tr>
{body}
</table>
<p>{e(r20["takeaway"])}</p>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wholesale is not your bill — Spain day-ahead prices vs what you pay (estimate)</title>
<style>
{STYLE}
</style>
</head>
<body>
<h1>Wholesale is not your bill</h1>
<p class="note">Every number on this page is an estimate. {e(retail.DISCLAIMER)}
<a href="index.html">Back to the latest day</a>.</p>
<h2>What OMIE is</h2>
<p>OMIE runs the Iberian day-ahead electricity market. Around 13:00 each day it publishes the price of
energy for every quarter-hour of the next day, in €/MWh, at the wholesale level. A price of €0/MWh
means generators are paid nothing for the energy in that period. It does not mean electricity is free
at your meter.</p>
<h2>The bill stack</h2>
<ol>
<li><strong>Energy.</strong> The wholesale price, plus adjustment services and operator fees, plus
capacity payments, grossed up for network losses, plus the retailer's margin.</li>
<li><strong>Peajes.</strong> Regulated tolls for the transmission and distribution networks, set by
the CNMC, per kWh and by tariff period.</li>
<li><strong>Cargos.</strong> Regulated system charges, set by the ministry, per kWh and by tariff
period.</li>
<li><strong>Taxes.</strong> The electricity tax (IEE) on all of the above, then IVA on top of that.</li>
<li><strong>Power term.</strong> A fixed yearly charge per contracted kW. It is the same whenever you
consume, so it is left out of the per-kWh estimate; the tables below list it.</li>
</ol>
<p>Formula: <code>{e(retail.FORMULA)}</code></p>
<h2>The 2.0TD calendar (peninsula)</h2>
<table>
<tr><th>Period</th><th>When (Europe/Madrid)</th><th>Peaje + cargo €/kWh</th></tr>
<tr><td>P1 punta</td><td>Mon–Fri 10:00–14:00 and 18:00–22:00</td><td>{p1:.6f}</td></tr>
<tr><td>P2 llano</td><td>Mon–Fri 08:00–10:00, 14:00–18:00 and 22:00–24:00</td><td>{p2:.6f}</td></tr>
<tr><td>P3 valle</td><td>Mon–Fri 00:00–08:00; all day on Saturdays, Sundays, 6 January and
fixed-date national holidays</td><td>{p3:.6f}</td></tr>
</table>
<p>3.0TD and 6.1TD use six periods that also depend on the season; see the tables below and
<a href="https://www.boe.es/buscar/act.php?id=BOE-A-2020-1066">Circular 3/2020, art. 7</a>.</p>
<h2>Why a €0 weekday midday is still punta</h2>
<p>Solar pushes the wholesale price to zero around midday, but on a working day 10:00–14:00 is P1
(punta), where peajes and cargos alone are {p1:.6f} €/kWh before taxes, against {p2:.6f} in llano
and {p3:.6f} in valle. So the cheapest wholesale hours are often not the cheapest hours to consume,
and at weekends (all valle) the two line up.</p>
<p>PVPC, the regulated tariff, follows the spot market only in part: under
<a href="{PVPC_RD_URL}">Real Decreto 446/2023</a>, {PVPC_FUTURES_SHARE_2026:.0%} of its energy price in
2026 is indexed to a basket of futures and the rest to the day-ahead and intraday markets, so a €0
wholesale hour moves a PVPC bill less than it moves an indexed free-market contract.</p>
<h2>Worked example</h2>
{example}
<h2>Assumptions</h2>
<ul>
{_assumption_items(retail.Assumptions())}
</ul>
<h2>Regulated terms, 2026</h2>
{_terms_table("2.0TD")}
{_terms_table("3.0TD")}
<h2>Sources</h2>
<ul>
{_source_items()}
<li>PVPC context column: ESIOS public archive 70 (REE), field PCB.</li>
</ul>
<footer>Prices: OMIE (omie.es), day-ahead market, Spain zone. Estimates are illustrative, peninsula
only, and not advice.</footer>
</body>
</html>
"""


def write(root: Path, day: dt.date, day_df: pd.DataFrame) -> dict | None:
    """Write site/estimate/* and the explainer page for ``day``; returns the bundle for index.html."""
    site = Path(root) / "site"
    b = bundle(root, day, day_df)
    files = {
        site / "estimate" / "latest.json": estimate_json(b, day),
        site / EXPLAINER: explainer_html(b, day),
    }
    if b is not None:
        for t, frame in b["frames"].items():
            files[site / "estimate" / f"{day}-{t}.csv"] = estimate_csv(frame)
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return b
