# Price-paid estimate

Turns OMIE wholesale prices into an ESTIMATE of the per-kWh price a 2.0TD, 3.0TD or 6.1TD customer pays,
adding assumed losses, adjustment services, margin, the 2026 BOE tolls and charges, and taxes. The site
shows 2.0TD and 3.0TD with downloadable CSVs; the CLI also does 6.1TD. Every output says "estimate".

## Sub-features

- `estimate-cli` `negprice estimate --date D --tariff T` prints the hourly estimate, cheapest windows and a takeaway.
- `estimate-csv` `--csv PATH` writes per-period rows identical to the site's `estimate/<D>-<T>.csv`.
- `estimate-61td` the CLI handles `--tariff 6.1TD` (CLI only; not on the page).
- `estimate-site` `estimate/latest.json` and the page section `What a 2.0TD / 3.0TD customer might pay (estimate)`.
- `estimate-label` outputs carry the `estimate` label and disclaimer.

## How to get to it (user POV)

- Site: the estimate section on `index.html`, its CSV links, `estimate/latest.json`, `wholesale-vs-bill.html`.
- Terminal: `negprice estimate --root DIR --date YYYY-MM-DD [--tariff 2.0TD|3.0TD|6.1TD] [--csv PATH] [--no-pvpc]`.

## Driving it with verify.sh

Preconditions:

- Baseline from the README; PVPC for 2026-10-10 cached in `$ROOT/data/pvpc/`.

- **CLI 2.0TD, offline.** Run `$V cli --offline estimate cli-20td -- "$NEGPRICE" estimate --root "$ROOT" --date 2026-10-10 --tariff 2.0TD --csv "$EVID/estimate/2026-10-10-2.0TD.csv"`. Exit `0`; stdout has `2.0TD retail price ESTIMATE, Sat 2026-10-10`, `Cheapest 180 min by estimate: 13:30–16:30` and `Label: estimate.`
- **CSV equals the site's.** Run `$V cli estimate csv-match -- cmp "$EVID/estimate/2026-10-10-2.0TD.csv" "$ROOT/site/estimate/2026-10-10-2.0TD.csv"`. Exit `0`.
- **6.1TD via CLI.** Run `$V cli --offline estimate cli-61td -- "$NEGPRICE" estimate --root "$ROOT" --date 2026-10-10 --tariff 6.1TD --no-pvpc`. Exit `0`; stdout has `6.1TD retail price ESTIMATE`.
- **Site JSON.** Run `$V cli estimate site-json -- jq -e '.available and (.tariffs|keys==["2.0TD","3.0TD"])' "$ROOT/site/estimate/latest.json"`. Stdout `true`.
- **Page section.** Run `$V fetch estimate index "${URL}index.html"` then `$V cli estimate page-section -- grep -c 'customer might pay (estimate)' "$EVID/estimate/index.body"`. Count ≥ 1.

## Gotchas

- The 2026 tables refuse other years unless `--allow-other-year` (prints a warning); use 2026 dates.
- Without cached PVPC the CLI fetches the public ESIOS archive; `--offline` turns that into a visible failure. Use `--no-pvpc` for days without a cache.
- `estimate/latest.json` follows the latest stored day, not the day you passed.
- 2.0TD weekends and holidays are all P3 (valle); test a weekday to see punta/llano differences.
