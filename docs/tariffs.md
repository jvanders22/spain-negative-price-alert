# Retail price estimate: tariffs, formula, assumptions

`negprice estimate` and the "What a 2.0TD / 3.0TD customer might pay (estimate)" section of the site turn
OMIE day-ahead prices into an **estimated** price paid per kWh, taxes included, for the 2.0TD, 3.0TD and
6.1TD access tariffs on the Spanish peninsula. Everything it outputs is labelled "estimate". It is not a
bill and not advice.

Code: `src/negprice/tariffs.py` (official tables, calendars), `retail.py` (formula, cheapest windows,
report), `pvpc.py` (PVPC context column), `retail_site.py` (site files).

## Formula

Per kWh, for each OMIE period (15 minutes since 2025-10-01):

```
energy     = (omie + adjustment_services + capacity_payment) / 1000 × (1 + loss_factor)
             + retailer_margin / 1000                  # €/MWh inputs → €/kWh
regulated  = peaje + cargo                             # by tariff period
pre_tax    = energy + regulated
iee        = pre_tax × IEE
estimate   = (pre_tax + iee) × (1 + IVA)               # IVA is charged on top of IEE
```

- The tariff period is the one in force at the **start** of the OMIE period, Europe/Madrid clock time.
- Negative OMIE prices flow through and can make `energy` (and, in valle, the whole estimate) negative.
- The capacity payment is published at busbar ("en b.c."), so it sits inside the losses gross-up. The
  `capacity_eur_kwh` column shows the official value; it is already part of `energy_eur_kwh`.
- Power terms (€/kW·year) are **not** in the per-kWh price: they are the same whenever you consume. They
  are listed below and in the report.

Worked example, 2.0TD P1 (punta), OMIE = 0 €/MWh, defaults:
`energy = (0 + 28 + 0.8) / 1000 × 1.16 + 0.010 = 0.043408`; `pre_tax = 0.043408 + 0.033261 + 0.064292 =
0.140961`; `iee = 0.007207`; `estimate = (0.140961 + 0.007207) × 1.21 = 0.179283 €/kWh`. The same hour in
valle (P3) comes to 0.058216 €/kWh. That gap is the point of the calculator.

## Official tables, 2026

Energy terms in €/kWh, power terms in €/kW·year. Copied from the BOE and verified on 2026-10-10.

### 2.0TD

| Period | Peaje €/kWh | Cargo €/kWh | Peaje + cargo €/kWh | Capacity payment €/kWh (busbar) |
|---|---|---|---|---|
| P1 (punta) | 0.033261 | 0.064292 | 0.097553 | 0.000800 |
| P2 (llano) | 0.016409 | 0.012858 | 0.029267 | 0.000133 |
| P3 (valle) | 0.000077 | 0.003215 | 0.003292 | 0.000000 |

| Power period | Peaje €/kW·year | Cargo €/kW·year | Total €/kW·year |
|---|---|---|---|
| P1 | 23.324952 | 4.379461 | 27.704413 |
| P2 | 0.443770 | 0.281653 | 0.725423 |

### 3.0TD

| Period | Peaje €/kWh | Cargo €/kWh | Peaje + cargo €/kWh | Capacity payment €/kWh (busbar) |
|---|---|---|---|---|
| P1 | 0.027511 | 0.035841 | 0.063352 | 0.001082 |
| P2 | 0.012376 | 0.026538 | 0.038914 | 0.000500 |
| P3 | 0.004943 | 0.014336 | 0.019279 | 0.000333 |
| P4 | 0.002627 | 0.007168 | 0.009795 | 0.000250 |
| P5 | 0.000111 | 0.004595 | 0.004706 | 0.000250 |
| P6 | 0.000031 | 0.002867 | 0.002898 | 0.000000 |

| Power period | Peaje €/kW·year | Cargo €/kW·year | Total €/kW·year |
|---|---|---|---|
| P1 | 14.935084 | 5.441843 | 20.376927 |
| P2 | 7.894323 | 2.723298 | 10.617621 |
| P3 | 2.502996 | 1.978538 | 4.481534 |
| P4 | 1.907795 | 1.978538 | 3.886333 |
| P5 | 0.535313 | 1.978538 | 2.513851 |
| P6 | 0.535313 | 0.906974 | 1.442287 |

### 6.1TD

| Period | Peaje €/kWh | Cargo €/kWh | Peaje + cargo €/kWh | Capacity payment €/kWh (busbar) |
|---|---|---|---|---|
| P1 | 0.026785 | 0.019489 | 0.046274 | 0.000465 |
| P2 | 0.012281 | 0.014436 | 0.026717 | 0.000213 |
| P3 | 0.005133 | 0.007795 | 0.012928 | 0.000142 |
| P4 | 0.002780 | 0.003898 | 0.006678 | 0.000107 |
| P5 | 0.000120 | 0.002499 | 0.002619 | 0.000107 |
| P6 | 0.000029 | 0.001559 | 0.001588 | 0.000000 |

| Power period | Peaje €/kW·year | Cargo €/kW·year | Total €/kW·year |
|---|---|---|---|
| P1 | 23.946498 | 5.648870 | 29.595368 |
| P2 | 12.687713 | 2.826996 | 15.514709 |
| P3 | 4.747747 | 2.054134 | 6.801881 |
| P4 | 3.339695 | 2.054134 | 5.393829 |
| P5 | 0.070979 | 2.054134 | 2.125113 |
| P6 | 0.062703 | 0.941478 | 1.004181 |

2.0TD has two power periods: P1 covers the punta and llano hours, P2 the valle hours.

### Sources

| What | Source | BOE | Table |
|---|---|---|---|
| Peajes de transporte y distribución 2026 | CNMC, Resolución de 18 de diciembre de 2025 | [BOE-A-2025-26348](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-26348), BOE núm. 306, 22 dic 2025 | "Término de energía del peaje de transporte y distribución (€/kWh)" |
| Cargos 2026 (segment 1 = 2.0TD, 2 = 3.0TD, 3 = 6.1TD) | Orden TED/1524/2025, de 23 de diciembre | [BOE-A-2025-26705](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-26705), BOE núm. 312, 27 dic 2025 | "Término de energía de los cargos (euros/kWh)" |
| Capacity payments 2026 | Orden TED/1524/2025, de 23 de diciembre | [BOE-A-2025-26705](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-26705) | "Término de energía de los pagos por capacidad (euros/kWh), en b.c." |
| Period calendars | CNMC, Circular 3/2020, art. 7 | [BOE-A-2020-1066](https://www.boe.es/buscar/act.php?id=BOE-A-2020-1066) | Artículo 7, periodos horarios |

Cross-check (a test): the ESIOS PVPC archive field `TEUPCB` (peajes + cargos energy term, €/MWh) for
Fri 9 Oct 2026 is 97.55 (10–14, 18–22), 29.27 (8–10, 14–18, 22–24) and 3.29 (0–8), and 3.29 all day on
Sat 10 Oct 2026. The 2.0TD peaje + cargo above, ×1000 and rounded to 2 dp, matches hour by hour.

The tables change every year. Dates outside 2026 raise `TariffYearError`; `--allow-other-year` applies the
2026 tables anyway and prints a warning. The site shows "no estimate" for such a day instead.

## Period calendars (peninsula)

Local Europe/Madrid clock time; a UTC timestamp is converted first, so DST needs no special case.

**2.0TD**

| Period | Hours |
|---|---|
| P1 punta | Mon–Fri non-holiday, 10–14 and 18–22 |
| P2 llano | Mon–Fri non-holiday, 8–10, 14–18 and 22–24 |
| P3 valle | Mon–Fri 0–8; all day on Saturdays, Sundays, 6 January and tariff holidays |

**3.0TD and 6.1TD** (same calendar)

| Season | Months | Weekday type | 9–14 and 18–22 | 8–9, 14–18 and 22–24 | 0–8 |
|---|---|---|---|---|---|
| alta | Jan, Feb, Jul, Dec | A | P1 | P2 | P6 |
| media alta | Mar, Nov | B | P2 | P3 | P6 |
| media | Jun, Aug, Sep | B1 | P3 | P4 | P6 |
| baja | Apr, May, Oct | C | P4 | P5 | P6 |

Day type D (Saturdays, Sundays, 6 January, tariff holidays): P6 all day.

**Holidays.** Circular 3/2020 counts "los días festivos de ámbito nacional, definidos como tales en el
calendario oficial del año correspondiente, con exclusión tanto de los festivos sustituibles como de los
que no tienen fecha fija", plus 6 January. That is implemented as the fixed-date list in
`tariffs.TARIFF_HOLIDAYS`: 1 Jan, 6 Jan, 1 May, 15 Aug, 12 Oct, 1 Nov, 6 Dec, 8 Dec, 25 Dec. Good Friday
(no fixed date) and Monday substitutes are ordinary days. Regional and local holidays never count.

**DST.** Both 2026 changes fall on Sundays (29 Mar: 92 quarter-hours, no 02:00 hour; 25 Oct: 100
quarter-hours, 02:00 twice), so those days are all valle / P6. Hourly rows are grouped on the UTC hour,
so the repeated 02:00 hour gives two rows.

## Assumptions

All configurable (`retail.Assumptions`, CLI flags). The first three are market-side guesses.

| Name | Default | CLI | Derivation |
|---|---|---|---|
| `loss_factor` | 0.16 | `--loss` | ASSUMPTION. Energy bought at busbar is grossed up for network losses. Median ratio PVPC `PMH` / OMIE hourly mean = 1.161 over the 36 hours with OMIE > 5 €/MWh on 9–10 Oct 2026. The ratio includes losses and small market adjustments. Official hourly loss coefficients are on ESIOS and need a token. |
| `adjustment_services_eur_mwh` | 28.0 | `--adj` | ASSUMPTION. Ancillary/adjustment services plus OMIE/REE fees. Median of PVPC `SAH + FOM + FOS + INT` over the 48 hours of 9–10 Oct 2026 (28.2; range 18.4–58.3 €/MWh), rounded. |
| `retailer_margin_eur_mwh` | 10.0 | `--margin` | ASSUMPTION. Illustrative. Real indexed contracts vary. |
| `iee` | 0.0511269632 | `--iee` | Impuesto Especial sobre la Electricidad, 5.11269632% (Ley 38/1992 art. 99.1). Agencia Tributaria note of 1 Sep 2026: the reduced 0.5% under RDL 18/2026 did not apply in Aug–Sep 2026. Per-MWh legal minimums are ignored. |
| `iva` | 0.21 | `--iva` | General rate, back from 1 Jun 2026 after the temporary 10% for ≤10 kW (22 Mar–31 May 2026; AEAT IVA 2026 manual). The IVA base includes IEE (DGT V0764-26). |

The two derived defaults come from two days in October. Adjustment services in particular swing a lot
hour to hour, so treat the absolute level of the estimate as rough. The ranking of hours within a day
is more robust, because it is driven by OMIE and the published tolls.

## PVPC context column

`pvpc_eur_kwh` is the regulated 2.0TD price from the public ESIOS archive 70
(`https://api.esios.ree.es/archives/70/download_json?locale=es&date=YYYY-MM-DD`, no token), field `PCB`,
in €/kWh **before taxes** as published. It is context only: it is not comparable 1:1 with the after-tax
estimate, and it is shown next to 3.0TD figures only as a reference.

PVPC for day D is published around 20:15 Madrid on D-1, about seven hours after OMIE. Raw responses are
cached in `data/pvpc/<date>.json` when they hold values. `negprice run` tries to cache the delivery day
and the day before, and never fails if ESIOS does; site builds read the cache only, so they stay offline
and deterministic. A cache file that can't be read or parsed is ignored with a warning (the estimate is
built without PVPC). At the afternoon run times the delivery day's PVPC is not out yet, so the scheduled
evening job `negprice pvpc-refresh` (≈ 21:17 Madrid) fetches it for the latest stored day and the day
before and rebuilds the site when something new was cached.

PVPC is only partly a spot price: Real Decreto 446/2023 (<https://www.boe.es/eli/es/rd/2023/06/13/446>)
indexes 55% of the PVPC energy price to a basket of futures in 2026, the rest to the day-ahead and
intraday markets. A €0 wholesale hour therefore moves a PVPC bill less than an indexed free-market
contract.

## Outputs

| Path | What |
|---|---|
| `site/estimate/latest.json` | latest day: one report per tariff (windows, takeaway, hourly rows, assumptions with notes, tables, sources) |
| `site/estimate/<date>-2.0TD.csv`, `-3.0TD.csv` | one row per OMIE period with every component of the stack |
| `site/index.html` | section "What a 2.0TD / 3.0TD customer might pay (estimate)" |
| `site/wholesale-vs-bill.html` | "Wholesale is not your bill" explainer; the worked example is computed from the latest day |

CSV columns: `ts_utc, local_start, local_hour, mtu_min, tariff, period, omie_eur_mwh, energy_eur_kwh,
peaje_eur_kwh, cargo_eur_kwh, capacity_eur_kwh, pre_tax_eur_kwh, iee_eur_kwh, iva_eur_kwh,
estimate_eur_kwh, pvpc_eur_kwh`. €/kWh values have 6 decimals.

The cheapest windows (60 and 180 minutes) are found the same way as the wholesale "cheapest 3h": real
minutes in UTC, earliest wins ties. Each is reported twice: ranked by estimated price paid, and ranked
by OMIE price together with what that wholesale window would cost.

## Limits

- Peninsula only. Canarias, Baleares, Ceuta and Melilla have other calendars.
- The tables are the 2026 ones.
- IEE per-MWh minimums are ignored.
- Power term, meter rental and any contract-specific fees are excluded from the
  per-kWh figure.
- Losses, adjustment services and margin are flat assumptions; the real ones vary by hour and contract.
- Indexed contracts differ in what they pass through and when. Fixed-price contracts don't follow OMIE
  at all.
- Not advice. Check your own contract and bill.
