# negprice verification map

The maintained source for verifying user-facing behavior of spain-negative-price-alert. Read this index
before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- `$V launch` succeeded and `eval "$($V env)"` exported `ROOT`, `URL`, `LIVE`, `EVID`, `NEGPRICE`.
- `$V doctor` has no FAIL lines (`--no-live` only when the network is genuinely down; then the live-site
  feature is BLOCKED, not verified).
- `$ROOT` is a scratch copy of committed `data/` and `site/`; the checkout is never passed as `--root`.
- When verifying a code change, run `$V cli --offline <feature> rebuild -- "$NEGPRICE" rebuild --root "$ROOT"`
  first so `$ROOT/site` reflects the new code.

## Driving conventions

- Every CLI step goes through `$V cli`; every page through `$V fetch` or `$V shot`.
- Use `--offline` for every step the product claims needs no network (`--file` runs, `rebuild`,
  `estimate` with cached PVPC, `weekly`). Steps that need OMIE or Pages run online and say so.
- Treat commands as literal. Assertions are commands too (`jq -e`, `grep -q`, `cmp`, python one-liners),
  run through `$V cli` so they are recorded.
- Do not edit `$ROOT/data` by hand except where a recipe says so.

## Proof and skip reporting

- CLI proof: command, stdout, stderr, exit code (the `.cmd/.out/.err/.exit` quartet).
- Output proof: a read-only second view of the written file (`jq`, `grep`, `cmp`).
- Site proof: HTTP status + body, plus a screenshot and rendered DOM for the page.
- Draft proof: the draft exists under `$ROOT/drafts/` AND `git ls-files drafts` in the checkout is empty.
  Never copy draft text into PRs or public places.
- Record the feature ID and entry point with every artifact (the harness does this by directory).
- Report an unreachable path with the attempted command and the unmet precondition.

## Feature entry contract

Each feature file: H1 title, one paragraph of user-visible behavior, then exactly four H2s in order:
`Sub-features`, `How to get to it (user POV)`, `Driving it with verify.sh`, `Gotchas`.

## Features

- [Daily alert run](./daily-alert-run.md) (`daily`): process one delivery day, idempotent rerun, draft kept out of git.
- [Feed outputs](./feed-outputs.md) (`outputs`): latest.json against its schema, feed.xml, chart PNG.
- [Price-paid estimate](./price-paid-estimate.md) (`estimate`): 2.0TD/3.0TD/6.1TD estimate from the CLI and the site CSVs.
- [Weekly summary](./weekly-summary.md) (`weekly`): Sunday Mon–Sun summary JSON, chart and drafts.
- [Static site, local and live](./static-site.md) (`site`): the page served locally and on GitHub Pages.
