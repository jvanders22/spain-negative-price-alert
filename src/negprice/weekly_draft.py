"""Weekly X post drafts in English and Spanish (never posted automatically). Plain text, <= 280
characters by ``len()``. Every number in the text comes from the ``weekly.facts`` dict."""

from __future__ import annotations

import datetime as dt

from .draft import MAX_CHARS, eur

LANGS = ("en", "es")
SOURCE = {"en": "Source: OMIE", "es": "Fuente: OMIE"}
# One opinion line per post, picked by week number so EN and ES say the same thing. No digits in these.
OPINIONS = {
    "en": [
        "My read: the value is in moving load to cheap hours, not in cheap power.",
        "I'd plan the week around the cheap hours, not the tariff headline.",
        "My read: storage gets paid on the gap between cheap hours and the evening.",
        "I keep coming back to it: flexibility is the scarce part, not energy.",
        "My take: a zero wholesale price is not a zero bill. Tolls and taxes stay.",
        "If I ran a flexible load, I'd chase the cheap hours, not the daily average.",
    ],
    "es": [
        "Mi lectura: el valor está en mover consumo a las horas baratas, no en la energía barata.",
        "Yo planificaría la semana según las horas baratas, no según el titular de la tarifa.",
        "Mi lectura: al almacenamiento le paga la diferencia entre las horas baratas y la tarde.",
        "Vuelvo siempre a lo mismo: lo escaso es la flexibilidad, no la energía.",
        "Ojo: precio mayorista cero no es factura cero. Peajes, cargos e impuestos siguen ahí.",
        "Con consumo flexible, yo iría a por las horas baratas, no a por la media diaria.",
    ],
}
# Spanish prose uses the full weekday name ("el martes a las 14:00"); months stay short in the date range.
ES_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ES_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
UNITS = {
    "en": {15: ("quarter-hours", "quarter-hour"), 60: ("hours", "hour"), None: ("periods", "period")},
    "es": {
        15: ("cuartos de hora", "cuarto de hora"),
        60: ("horas", "hora"),
        None: ("periodos", "periodo"),
    },
}


def opinion(sunday: str, lang: str) -> str:
    lines = OPINIONS[lang]
    return lines[dt.date.fromisoformat(sunday).toordinal() // 7 % len(lines)]


def es_num(x: float, dp: int = 2) -> str:
    """Spanish decimal comma: 0.058 -> "0,058"."""
    return f"{x:.{dp}f}".replace(".", ",")


def _dp(x: float) -> int:
    return 2 if abs(x) < 10 else 0


def _weekday(date: str, lang: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d:%a}" if lang == "en" else ES_WEEKDAYS[d.weekday()]


def _month(d: dt.date, lang: str) -> str:
    return f"{d:%b}" if lang == "en" else ES_MONTHS[d.month - 1]


def week_range(f: dict, lang: str) -> str:
    """ "28 Sep–4 Oct" / "5–11 Oct"; Spanish "28 sep al 4 oct" / "5 al 11 de oct"."""
    a, b = (dt.date.fromisoformat(f["week"][k]) for k in ("start", "end"))
    same = a.month == b.month
    if lang == "en":
        return f"{a.day}–{b.day} {_month(b, lang)}" if same else f"{a.day} {a:%b}–{b.day} {b:%b}"
    if same:
        return f"{a.day} al {b.day} de {_month(b, lang)}"
    return f"{a.day} {_month(a, lang)} al {b.day} {_month(b, lang)}"


def _lines_en(f: dict) -> dict[str, str]:
    z, lo, s, y = f["zero_or_below"], f["lowest"], f["midday_evening"], f["zero_days_ytd"]
    r = f["cheapest_3h"]["retail"]
    plural, singular = UNITS["en"][f["week"]["mtu_min"]]
    if z["n_le0"]:
        zero = f"{z['n_le0']} {plural if z['n_le0'] != 1 else singular} at or below €0, "
        zero += f"on {z['days_with_le0']} of {z['days']} days."
    else:
        zero = f"No {singular} at or below €0 all week."
    spread = s["spread"]
    return {
        "head": f"Spain day-ahead power, week of {week_range(f, 'en')}:",
        "zero": zero,
        "low": f"Low: {eur(lo['price'], 2)}/MWh, {_weekday(lo['date'], 'en')} {lo['time']}.",
        "spread": f"Evening averaged {eur(abs(spread), _dp(spread))}/MWh "
        f"{'above' if spread >= 0 else 'below'} midday.",
        "retail": f"Cheapest 2.0TD 3h, my estimate: {_weekday(r['date'], 'en')} {r['start']}–{r['end']}, "
        f"~{eur(r['estimate_eur_kwh'], 3)}/kWh."
        if r["available"]
        else "",
        "ytd": f"Zero-price days in {y['year']}: {y['ytd_zero_days']} "
        f"({y['ytd_zero_days_prev_year']} by this date in {y['prev_year']}).",
    }


def _lines_es(f: dict) -> dict[str, str]:
    z, lo, s, y = f["zero_or_below"], f["lowest"], f["midday_evening"], f["zero_days_ytd"]
    r = f["cheapest_3h"]["retail"]
    plural, singular = UNITS["es"][f["week"]["mtu_min"]]
    if z["n_le0"]:
        zero = f"{z['n_le0']} {plural if z['n_le0'] != 1 else singular} a 0 €/MWh o menos, "
        zero += f"en {z['days_with_le0']} de {z['days']} días."
    else:
        zero = f"Ni {'una' if singular == 'hora' else 'un'} {singular} a 0 €/MWh o menos en toda la semana."
    spread = s["spread"]
    return {
        "head": f"Luz en el mercado mayorista español, semana del {week_range(f, 'es')}:",
        "zero": zero,
        "low": f"Mínimo: {es_num(lo['price'])} €/MWh, el {_weekday(lo['date'], 'es')} a las {lo['time']}.",
        "spread": f"La tarde salió {es_num(abs(spread), _dp(spread))} €/MWh más "
        f"{'cara' if spread >= 0 else 'barata'} que el mediodía.",
        "retail": f"Las 3 h más baratas con 2.0TD (estimación mía): el {_weekday(r['date'], 'es')}, "
        f"{r['start']}–{r['end']}, ~{es_num(r['estimate_eur_kwh'], 3)} €/kWh."
        if r["available"]
        else "",
        "ytd": f"Días con precio cero en {y['year']}: {y['ytd_zero_days']} "
        f"({y['ytd_zero_days_prev_year']} a estas alturas de {y['prev_year']}).",
    }


ORDER = ("zero", "low", "spread", "retail", "ytd")  # order in the post


def _assemble(lines: dict[str, str], keep: set[str], closing: list[str]) -> str:
    body = "\n".join([lines["head"], *(lines[k] for k in ORDER if k in keep)])
    return "\n\n".join([body, *closing])


def post_text(f: dict, lang: str) -> str:
    """Header, the zero-or-below line, as many of the optional fact lines as fit (whole lines only, never
    cut mid-word), one opinion line and the source line."""
    lines = (_lines_en if lang == "en" else _lines_es)(f)
    closing = [opinion(f["week"]["end"], lang), SOURCE[lang]]
    # Optional lines, most to least worth keeping. In a week with no zero-price period the low is the news.
    optional = ("retail", "spread", "ytd", "low") if f["zero_or_below"]["n_le0"] else (
        "low", "retail", "spread", "ytd"
    )  # fmt: skip
    keep = {"zero"}
    for key in optional:
        if lines[key] and len(_assemble(lines, keep | {key}, closing)) <= MAX_CHARS:
            keep.add(key)
    text = _assemble(lines, keep, closing)
    if len(text) > MAX_CHARS:  # pragma: no cover - the required lines are short
        raise ValueError(f"weekly {lang} draft does not fit in {MAX_CHARS} characters")
    return text


def fact_lines(f: dict) -> list[str]:
    """The computed facts as plain lines (drafts and the workflow step summary; no draft text here)."""
    w, z, lo, s = f["week"], f["zero_or_below"], f["lowest"], f["midday_evening"]
    c, y, wow = f["cheapest_3h"], f["zero_days_ytd"], f["week_over_week"]
    r, ws = c["retail"], c["wholesale"]
    if r["available"]:
        retail = (
            f"cheapest estimated {r['tariff']} 3h ({r['label']}): {r['weekday']} {r['date']} "
            f"{r['start']}–{r['end']} at {r['estimate_eur_kwh']:.3f} €/kWh ({'/'.join(r['period_names'])}; "
            f"OMIE {r['omie_eur_mwh']:.2f} €/MWh)"
        )
    else:
        retail = f"cheapest estimated {r['tariff']} 3h ({r['label']}): not available ({r['reason']})"
    out = [
        f"week: {w['label']} ({w['start']} to {w['end']}), {w['n_periods']} periods",
        f"periods <= 0: {z['n_le0']} of {z['n_periods']} ({z['n_neg']} negative), on {z['days_with_le0']} "
        f"of {z['days']} days",
        f"lowest price: {lo['price']:.2f} €/MWh, {lo['weekday']} {lo['date']} at {lo['time']}",
        f"midday mean {s['midday_mean']:.2f}, evening mean {s['evening_mean']:.2f}, spread "
        f"{s['spread']:.2f} €/MWh",
        retail,
        f"cheapest wholesale 3h: {ws['weekday']} {ws['date']} {ws['start']}–{ws['end']} at "
        f"{ws['omie_eur_mwh']:.2f} €/MWh",
        f"zero-price days YTD as of {y['as_of']}: {y['ytd_zero_days']} (by {y['prev_year_as_of']}: "
        f"{y['ytd_zero_days_prev_year']})",
    ]
    if wow:
        out.append(
            f"previous week (ending {wow['previous_week_ending']}): periods <= 0: {wow['n_le0_prev']}, on "
            f"{wow['days_with_le0_prev']} of {z['days']} days, spread {wow['spread_prev']:.2f} €/MWh "
            f"(change {wow['spread_change']:+.2f})"
        )
    else:
        out.append("previous week: not available (not complete in history)")
    return out


def draft_md(f: dict, lang: str) -> str:
    text = post_text(f, lang)
    bullets = "\n".join(f"- {line}" for line in fact_lines(f))
    return (
        f"# DRAFT — not posted. Josh approves every post.\n\n"
        f"Weekly summary ({lang.upper()}), week ending {f['week']['end']}.\n\n```text\n{text}\n```\n\n"
        f"Characters: {len(text)} / {MAX_CHARS}\n\n## Facts\n\n{bullets}\n"
    )
