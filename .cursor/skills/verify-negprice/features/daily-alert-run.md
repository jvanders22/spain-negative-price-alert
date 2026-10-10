# Daily alert run

Each afternoon the pipeline processes one delivery day: it reads OMIE's day-ahead file, validates it, flags
periods at or below €0, upserts the day into the history CSVs, rebuilds the outputs, prints a one-line
alert, and writes an X draft that is never committed. A rerun of a processed day is a no-network no-op.

## Sub-features

- `daily-file` processes a day from a local OMIE file (`--file`), fully offline.
- `daily-idempotent` reruns a processed day: prints `already processed`, exits 0, no network, no changes.
- `daily-history` the day's row lands in `data/history/daily.csv` with the right counts.
- `daily-draft` writes `drafts/<date>.md`; nothing under `drafts/` is tracked by git.
- `daily-live` fetches tomorrow's real OMIE file (network; optional, scratch root only).

## How to get to it (user POV)

- GitHub Actions `daily-alert` schedule (13:17–16:17 Madrid) or Run workflow with `date`/`force`.
- Terminal: `negprice run [--date YYYY-MM-DD] [--force] [--file PATH] [--deadline HH:MM] [--root DIR]`.

## Driving it with verify.sh

Preconditions:

- Baseline from the README; `2026-10-10` is already in `$ROOT/data/history/daily.csv` (committed).
- Fixture `tests/fixtures/marginalpdbc_20261010.1` exists.

- **Force a day from file, offline.** Run `$V cli --offline daily run-file -- "$NEGPRICE" run --root "$ROOT" --date 2026-10-10 --file tests/fixtures/marginalpdbc_20261010.1 --force`. Exit `0`; stdout starts `ALERT: Sat 2026-10-10 — 18 of 96 quarter-hours at or below €0 (12:30–17:00)`.
- **Second view of history.** Run `$V cli daily history-row -- grep '^2026-10-10,' "$ROOT/data/history/daily.csv"`. One row; fields 2–5 are `96,15,18,6` (n_periods, mtu_min, n_le0, n_neg).
- **Idempotent rerun, offline.** Run `$V cli --offline daily rerun -- "$NEGPRICE" run --root "$ROOT" --date 2026-10-10`. Exit `0`; stdout `2026-10-10: already processed`.
- **Draft written, not tracked.** Run `$V cli daily draft-exists -- test -s "$ROOT/drafts/2026-10-10.md"` (exit `0`) and `$V cli daily drafts-untracked -- git ls-files drafts` (exit `0`, empty stdout). Do not copy the draft's text anywhere public.
- **Checkout untouched.** Run `$V cli daily checkout-clean -- git status --short --untracked-files=no`. Empty stdout on a clean checkout (on a branch with uncommitted edits, only those edits appear; nothing under `data/`, `site/` or `drafts/`).
- **Live fetch (optional, network).** Run `$V cli daily run-live -- "$NEGPRICE" run --root "$ROOT"`. Exit `0` with an `ALERT:`/summary line, `already processed`, or `pending` (OMIE not out yet, normal before ~13:05 Madrid). Exit `1` after the `--deadline` only.

## Gotchas

- Without `--force`, a day already in history is skipped before any fetch; use `--force` to exercise parsing.
- `--file` skips OMIE but the run may still try PVPC for days not cached in `data/pvpc/`; `--offline` makes that visible. 9 and 10 Oct 2026 are cached.
- `latest.json` and `index.html` follow the LATEST stored day (currently 2026-10-11), not the day you forced; assert the forced day through `daily.csv`.
- A rerun rewrites the latest day's draft too (`drafts/<latest>.md`); that is expected.
- Never run against the checkout (`--root` omitted): it would rewrite committed `data/` and `site/`.
