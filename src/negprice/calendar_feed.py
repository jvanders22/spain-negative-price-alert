# ruff: noqa: E501  (inline HTML/CSS template)
"""Subscribable ICS calendars of free (≤ €0/MWh) and cheap (≤ €10/MWh) wholesale power windows.

Writes site/calendar/{es,pt,es-cheap,pt-cheap}.ics, site/calendar/index.html and
site/calendar/state.json. Deterministic: nothing reads the wall clock. The state file is both input
and output; it keeps every published UID with a content hash and SEQUENCE so that

* a re-run with unchanged data produces byte-identical files (stable UIDs, no SEQUENCE churn);
* a window whose content changes (e.g. the PVPC line arriving in the evening) gets SEQUENCE + 1;
* a window that disappears is re-published with STATUS:CANCELLED until it ages out of retention.

Times are written in UTC (``...Z``) so no VTIMEZONE is needed and the DST fold on the last Sunday of
October cannot be ambiguous; titles and descriptions use local wall-clock times.

v1 publishes D+1 only ("confirmed": the OMIE day-ahead result, published ~13:00 Madrid on D-1).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from negprice import history, pvpc, tariffs
from negprice.site_config import goatcounter_tag

BASE_URL = "https://jvanders22.github.io/spain-negative-price-alert"
REPO_URL = "https://github.com/jvanders22/spain-negative-price-alert"
UID_DOMAIN = "jvanders22.github.io"
RETAIN_DAYS = 14  # delivery days kept, counted back from the latest day in history
PRODID = "-//jvanders22//spain-negative-price-alert free power calendar//EN"


@dataclass(frozen=True)
class Feed:
    slug: str
    zone: str  # "es" | "pt"
    tz: str
    col: str
    threshold: float
    calname: str
    caldesc: str

    @property
    def kind(self) -> str:
        return "cheap" if self.threshold > 0 else "free"


FEEDS = (
    Feed(
        "es",
        "es",
        "Europe/Madrid",
        "price_es",
        0.0,
        "Free power (Spain)",
        "Hours when the Spanish day-ahead wholesale price is at or below €0/MWh. OMIE data.",
    ),
    Feed(
        "pt",
        "pt",
        "Europe/Lisbon",
        "price_pt",
        0.0,
        "Free power (Portugal)",
        "Hours when the Portuguese day-ahead wholesale price is at or below €0/MWh. OMIE data.",
    ),
    Feed(
        "es-cheap",
        "es",
        "Europe/Madrid",
        "price_es",
        10.0,
        "Cheap power ≤ €10 (Spain)",
        "Hours when the Spanish day-ahead wholesale price is at or below €10/MWh. OMIE data.",
    ),
    Feed(
        "pt-cheap",
        "pt",
        "Europe/Lisbon",
        "price_pt",
        10.0,
        "Cheap power ≤ €10 (Portugal)",
        "Hours when the Portuguese day-ahead wholesale price is at or below €10/MWh. OMIE data.",
    ),
)

ZONE_NAME = {"es": "Spain", "pt": "Portugal"}
TZ_LABEL = {"Europe/Madrid": "Madrid time", "Europe/Lisbon": "Lisbon time"}


# ---------------------------------------------------------------- windows


def windows(periods: pd.DataFrame, col: str, threshold: float) -> list[dict]:
    """Merge contiguous periods with price <= threshold into windows, per delivery day.
    ``periods`` has date, ts_utc, mtu_min and the price column."""
    out: list[dict] = []
    if periods.empty:
        return out
    df = periods.sort_values("ts_utc")
    for day, g in df.groupby("date", sort=True):
        cur = None
        for row in g.itertuples(index=False):
            ts = pd.Timestamp(row.ts_utc)
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            end = ts + pd.Timedelta(minutes=int(row.mtu_min))
            price = float(getattr(row, col))
            if price <= threshold:
                if cur is not None and cur["end"] == ts:
                    cur["end"] = end
                    cur["prices"].append(price)
                else:
                    cur = {
                        "date": dt.date.fromisoformat(str(day)),
                        "start": ts,
                        "end": end,
                        "prices": [price],
                    }
                    out.append(cur)
            else:
                cur = None
    return out


def _fmt_eur(x: float) -> str:
    return f"€{x:.2f}".replace("€-", "−€")


def _pvpc_line(root: Path, w: dict) -> str:
    """Mean PVPC (regulated tariff, all-in energy term) over the hours the window touches, plus the
    2.0TD period(s). Uses the cache only (fetching is the pvpc-refresh job's business)."""
    local_start = w["start"].tz_convert(tariffs.TZ)
    local_end = w["end"].tz_convert(tariffs.TZ)
    # 2.0TD periods of each 15-min step inside the window
    steps = pd.date_range(w["start"], w["end"], freq="15min", inclusive="left")
    ps = sorted({tariffs.period_20td(t.tz_convert(tariffs.TZ)) for t in steps})
    plabel = " / ".join(f"{p} {tariffs.PERIOD_NAMES_20TD[p]}" for p in ps)
    try:
        df = pvpc.load(root, w["date"], fetch=False)
    except Exception:  # noqa: BLE001 - a broken cache file must not break the calendar
        df = None
    if df is None or len(df) == 0:
        return (
            f"PVPC (regulated 2.0TD tariff): period {plabel}. The PVPC price is added when REE "
            "publishes it, around 20:15 the evening before."
        )
    ts = pd.to_datetime(df["ts_utc"], utc=True)
    hour_start = w["start"].floor("h")
    sel = df[(ts >= hour_start) & (ts < w["end"])]
    if sel.empty:
        return f"PVPC (regulated 2.0TD tariff): period {plabel}."
    cents = float(sel["pcb_eur_mwh"].mean()) / 10
    return (
        f"PVPC (regulated 2.0TD tariff): about {cents:.1f} c/kWh in these hours, period {plabel} "
        f"({local_start:%H:%M}–{local_end:%H:%M}). Grid tolls and charges still apply, so retail power is "
        "never free; it is just at its cheapest."
    )


def event_for(root: Path, feed: Feed, w: dict, latest: dt.date | None = None) -> dict:
    ls = w["start"].tz_convert(feed.tz)
    le = w["end"].tz_convert(feed.tz)
    span = f"{ls:%H:%M}-{le:%H:%M}"
    if feed.kind == "free":
        summary = f"Free power {span} (confirmed)"
        what = "at or below €0/MWh"
    else:
        summary = f"Cheap power ≤ €10 {span} (confirmed)"
        what = f"at or below €{feed.threshold:.0f}/MWh"
    prices = w["prices"]
    lines = [
        f"Wholesale power in {ZONE_NAME[feed.zone]} is {what} from {ls:%H:%M} to {le:%H:%M} "
        f"{TZ_LABEL[feed.tz]} on {ls:%a %d %b %Y}. Lowest {_fmt_eur(min(prices))}/MWh, "
        f"average {_fmt_eur(sum(prices) / len(prices))}/MWh.",
        "Confirmed: OMIE day-ahead market result, published around 13:00 Madrid time the day before.",
    ]
    if feed.zone == "es":
        lines.append(_pvpc_line(root, w))
    lines += [
        "Good hours to run the dishwasher or washing machine, charge an EV or heat water.",
        "Wholesale price, not your retail bill.",
        f"Source: OMIE (omie.es). More: {BASE_URL}/calendar/",
    ]
    return {
        "uid": f"{feed.kind}-{feed.zone}-{ls:%Y%m%d}-{w['start']:%H%M}Z@{UID_DOMAIN}",
        "date": w["date"].isoformat(),
        "start": w["start"].strftime("%Y%m%dT%H%M%SZ"),
        "end": w["end"].strftime("%Y%m%dT%H%M%SZ"),
        "summary": summary,
        "description": "\n".join(lines),
    }


def _hash(ev: dict) -> str:
    keys = ("start", "end", "summary", "description")
    return hashlib.sha256(json.dumps([ev[k] for k in keys], ensure_ascii=False).encode()).hexdigest()[:16]


def publication_stamp(day: dt.date) -> str:
    """D-1 13:00 Madrid in UTC: when OMIE publishes the day-ahead result for ``day``."""
    t = pd.Timestamp(dt.datetime.combine(day - dt.timedelta(days=1), dt.time(13))).tz_localize(
        "Europe/Madrid"
    )
    return t.tz_convert("UTC").strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------- state / sequencing


def reconcile(events: list[dict], state: dict, latest: dt.date) -> tuple[list[dict], dict]:
    """Assign SEQUENCE/DTSTAMP from ``state``; emit cancellations for vanished UIDs.
    Returns (events to publish incl. cancelled ones, new state)."""
    retain_from = (latest - dt.timedelta(days=RETAIN_DAYS - 1)).isoformat()
    stamp_now = publication_stamp(latest)
    new_state: dict = {}
    out: list[dict] = []
    current = {e["uid"]: e for e in events if e["date"] >= retain_from}
    for uid, ev in current.items():
        h = _hash(ev)
        prev = state.get(uid)
        if prev is None:
            seq, stamp = 0, publication_stamp(dt.date.fromisoformat(ev["date"]))
        elif prev["hash"] == h and prev.get("status") != "CANCELLED":
            seq, stamp = prev["seq"], prev["stamp"]
        else:
            seq, stamp = prev["seq"] + 1, max(stamp_now, prev["stamp"])
        ev = {**ev, "seq": seq, "stamp": stamp, "status": "CONFIRMED"}
        new_state[uid] = {
            k: ev[k] for k in ("date", "start", "end", "summary", "description", "seq", "stamp", "status")
        }
        new_state[uid]["hash"] = h
        out.append(ev)
    for uid, prev in state.items():
        if uid in current or prev["date"] < retain_from:
            continue
        if prev.get("status") == "CANCELLED":
            ev = dict(prev)
        else:
            ev = {
                **prev,
                "seq": prev["seq"] + 1,
                "stamp": max(stamp_now, prev["stamp"]),
                "status": "CANCELLED",
            }
        ev["uid"] = uid
        new_state[uid] = {k: v for k, v in ev.items() if k != "uid"}
        out.append(ev)
    out.sort(key=lambda e: (e["start"], e["uid"]))
    return out, dict(sorted(new_state.items()))


# ---------------------------------------------------------------- ICS serialisation


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545 §3.1 without splitting UTF-8 sequences."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # continuation lines start with a space
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def ics(feed: Feed, events: list[dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(feed.calname)}",
        f"NAME:{_escape(feed.calname)}",
        f"X-WR-CALDESC:{_escape(feed.caldesc)}",
        f"DESCRIPTION:{_escape(feed.caldesc)}",
        f"X-WR-TIMEZONE:{feed.tz}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
        f"URL:{BASE_URL}/calendar/{feed.slug}.ics",
    ]
    for ev in events:
        summary = ev["summary"] if ev["status"] != "CANCELLED" else f"Cancelled: {ev['summary']}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{ev['stamp']}",
            f"LAST-MODIFIED:{ev['stamp']}",
            f"DTSTART:{ev['start']}",
            f"DTEND:{ev['end']}",
            f"SEQUENCE:{ev['seq']}",
            f"STATUS:{ev['status']}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(ev['description'])}",
            "TRANSP:TRANSPARENT",
            f"URL:{BASE_URL}/calendar/",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines)


# ---------------------------------------------------------------- landing page


def _upcoming_html(events: list[dict], feed: Feed, latest: dt.date) -> str:
    rows = [e for e in events if e["status"] != "CANCELLED" and e["date"] == latest.isoformat()]
    day = pd.Timestamp(latest).strftime("%A %d %B")
    if not rows:
        return f"<p>{day}: no hours at or below €0 in {ZONE_NAME[feed.zone]}.</p>"
    items = "".join(f"<li>{html.escape(e['summary'].replace(' (confirmed)', ''))}</li>" for e in rows)
    return f"<p>{day}, {ZONE_NAME[feed.zone]} ({TZ_LABEL[feed.tz]}):</p><ul>{items}</ul>"


def index_html(published: dict[str, list[dict]], latest: dt.date) -> str:
    host = BASE_URL.removeprefix("https://")
    e = html.escape

    def subscribe(slug: str, label: str) -> str:
        https = f"{BASE_URL}/calendar/{slug}.ics"
        webcal = f"webcal://{host}/calendar/{slug}.ics"
        google = "https://calendar.google.com/calendar/r?cid=" + webcal
        return (
            f'<tr><th scope="row">{e(label)}</th>'
            f'<td><a href="{e(webcal)}">Apple / Outlook</a></td>'
            f'<td><a href="{e(google)}">Google</a></td>'
            f'<td><a href="{e(https)}">.ics link</a></td></tr>'
        )

    by = {f.slug: f for f in FEEDS}
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Free power calendar — Spain and Portugal</title>
<meta name="description" content="Subscribe to a calendar of the hours when wholesale electricity in Spain or Portugal costs €0 or less.">
<link rel="alternate" type="text/calendar" href="es.ics" title="Free power (Spain)">
<style>
:root {{ --paper: #fbfaf7; --ink: #1b1b1b; --muted: #5a5853; --accent: #b8461b; --free: #2b6c9e; }}
body {{ margin: 0 auto; max-width: 40rem; padding: 2rem 1.25rem 3rem; background: var(--paper); color: var(--ink);
  font: 17px/1.55 "Source Serif 4", Georgia, serif; }}
h1 {{ font-size: 2rem; line-height: 1.15; margin: 0 0 .5rem; font-weight: 600; }}
h2 {{ font: 600 1rem/1.3 "Source Sans 3", "Helvetica Neue", Arial, sans-serif; margin: 2rem 0 .5rem;
  text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }}
.lede {{ font-size: 1.15rem; }}
.free {{ color: var(--free); font-weight: 600; }}
a {{ color: var(--accent); }}
table {{ border-collapse: collapse; width: 100%; font: 16px/1.4 "Source Sans 3", "Helvetica Neue", Arial, sans-serif; }}
th, td {{ text-align: left; padding: .45rem .6rem .45rem 0; vertical-align: top; }}
th {{ font-weight: 600; }}
ul {{ padding-left: 1.2rem; }}
.small {{ font: 14px/1.45 "Source Sans 3", "Helvetica Neue", Arial, sans-serif; color: var(--muted); }}
</style>
{goatcounter_tag()}</head>
<body>
<h1>Free power calendar</h1>
<p class="lede">Some days, solar pushes the Spanish and Portuguese wholesale electricity price to
<span class="free">€0 or below</span>. Subscribe once and those hours appear in your calendar the day
before, as soon as the market result is out (around 13:00 Madrid time).</p>

<h2>Next</h2>
{_upcoming_html(published["es"], by["es"], latest)}
{_upcoming_html(published["pt"], by["pt"], latest)}

<h2>Subscribe</h2>
<table>
{subscribe("es", "Spain, €0 or less")}
{subscribe("pt", "Portugal, €0 or less")}
{subscribe("es-cheap", "Spain, €10 or less")}
{subscribe("pt-cheap", "Portugal, €10 or less")}
</table>
<p class="small">On a phone or Mac, "Apple / Outlook" opens your calendar app. "Google" opens Google
Calendar's add-by-URL screen; on Android, subscribe on calendar.google.com from a browser, then it syncs.
Anywhere else, copy the .ics link and add it as a calendar "from URL".</p>

<h2>How fresh is it?</h2>
<p>The feed itself updates within the hour after OMIE publishes. Your calendar app decides when to look:
Apple Calendar lets you pick (set Auto-refresh to every hour or 5 minutes);
<strong>Google Calendar refreshes subscribed calendars only every 12 to 24 hours</strong> and has no
refresh button, so with Google an event can show up late, sometimes after the hours have passed. If you
need it on time, use the <a href="../feed.xml">RSS feed</a> or the email alert instead.</p>

<h2>What the events mean</h2>
<ul>
<li><strong>Confirmed</strong>: the OMIE day-ahead result for tomorrow. These prices are final.</li>
<li>The Spain feeds include the PVPC (regulated tariff) price for those hours and the 2.0TD period.
Grid tolls and charges still apply, so your bill is never zero; these are simply the cheapest hours.</li>
<li>If a window is withdrawn it stays in the calendar marked "Cancelled" for a few days.</li>
<li>Events are marked "free" (not busy), so they never block your schedule.</li>
</ul>
<p>Coming next: "likely" windows two days ahead from a price forecast.</p>

<h2>Other ways to follow</h2>
<ul>
<li><a href="../feed.xml">RSS feed</a>, one item per day</li>
<li><a href="{REPO_URL}#email-alert">Email alert</a></li>
<li><a href="../">Today's prices</a></li>
</ul>

<p class="small">Prices: OMIE (omie.es), day-ahead market. PVPC: Red Eléctrica (REE). Wholesale prices,
not your retail bill. <a href="{REPO_URL}">Code and data</a>.</p>
</body>
</html>
"""


# ---------------------------------------------------------------- build


def build(root: Path, periods: pd.DataFrame | None = None) -> dict[str, list[dict]]:
    root = Path(root)
    periods = history.read_periods(root) if periods is None else periods
    out_dir = root / "site" / "calendar"
    out_dir.mkdir(parents=True, exist_ok=True)
    if periods.empty:
        return {}
    latest = max(dt.date.fromisoformat(str(d)) for d in periods["date"])
    retain_from = (latest - dt.timedelta(days=RETAIN_DAYS - 1)).isoformat()
    recent = periods[periods["date"].astype(str) >= retain_from]
    state_path = out_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    new_state: dict = {}
    published: dict[str, list[dict]] = {}
    for feed in FEEDS:
        evs = [event_for(root, feed, w, latest) for w in windows(recent, feed.col, feed.threshold)]
        fstate = state.get(feed.slug, {})
        pub, new_state[feed.slug] = reconcile(evs, fstate, latest)
        published[feed.slug] = pub
        _write(out_dir / f"{feed.slug}.ics", ics(feed, pub), newline="")
    _write(state_path, json.dumps(new_state, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    _write(out_dir / "index.html", index_html(published, latest))
    return published


def _write(path: Path, text: str, newline: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8", newline=newline or "") == text:
        return
    with open(path, "w", encoding="utf-8", newline=newline or "\n") as f:
        f.write(text)
