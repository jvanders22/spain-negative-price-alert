# Static site, local and live

The public face of the project: a static `index.html` (latest day, this week, the estimate section, last
30 days, data links) plus `wholesale-vs-bill.html`, served from `site/` locally and by GitHub Pages at
https://jvanders22.github.io/spain-negative-price-alert/.

## Sub-features

- `site-local` the scratch build serves `index.html` with the expected title and H2s.
- `site-links` every relative link on the page answers 200 locally.
- `site-live` the live Pages site answers 200 and its `latest.json` date equals the committed one.
- `site-explainer` `wholesale-vs-bill.html` renders.

## How to get to it (user POV)

- Browser: https://jvanders22.github.io/spain-negative-price-alert/ (and the local `$URL` during verification).
- It updates when the `daily-alert` workflow processes a new day and its `pages` job deploys `site/`.

## Driving it with verify.sh

Preconditions:

- Baseline from the README; for `site-live`, Doctor's live line is OK.

- **Local page.** Run `$V shot site local-index "${URL}index.html"`. Screenshot shows `Spain day-ahead power: <weekday> <date>`. Then `$V cli site local-h2 -- grep -o '<h2>[^<]*' "$EVID/site/local-index.dom.html"`: `This week`, `What a 2.0TD / 3.0TD customer might pay (estimate)`, `Last 30 days`, `Data`.
- **Local links.** Run `$V cli site local-links -- bash -c 'cd "$ROOT/site" && grep -o "href=\"[^\":]*\"" index.html | cut -d\" -f2 | sort -u | while read -r p; do printf "%s %s\n" "$(curl -s -o /dev/null -w "%{http_code}" "$URL$p")" "$p"; done'`. Every line starts with `200`.
- **Explainer.** Run `$V shot site local-explainer "${URL}wholesale-vs-bill.html"`. Non-empty screenshot and DOM.
- **Live page.** Run `$V fetch site live-index "$LIVE"` and `$V shot site live-index "$LIVE"`. 200; screenshot shows the same heading date as the local one.
- **Live data matches main.** Run `$V fetch site live-latest "${LIVE}latest.json"` then `$V cli site live-vs-main -- bash -c 'jq -r .date "$EVID/site/live-latest.body"; jq -r .date "$REPO/site/latest.json"'`. Both lines equal.

## Gotchas

- Pages deploys only when `daily-alert` processes a new day (`pages` job, gated by repo variable `PAGES_ENABLED=true`). A merge that only touches code or docs does not redeploy; the live site keeps the last data deploy, which is expected.
- Pages is served through a CDN; right after a deploy the live JSON can lag main for a few minutes. Re-fetch before calling it a mismatch.
- `$V shot` uses its own Chrome profile under the scratch dir; never point it at a signed-in browser profile.
- The live site is public: read only. Never trigger workflows or change Pages settings from a verification run.
