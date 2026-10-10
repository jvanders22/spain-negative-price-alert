# Weekly summary

Every Sunday the pipeline summarises the latest complete Monday–Sunday week from history (no network): five
numbers, a 1080x1080 chart, a JSON file, and EN/ES X drafts that are never committed.

## Sub-features

- `weekly-build` `negprice weekly --week-ending <Sunday>` builds `site/weekly/<Sunday>.json` + `.png`.
- `weekly-idempotent` a second build of the same week prints `already built`.
- `weekly-drafts` EN and ES drafts land in `drafts/weekly-<Sunday>-{en,es}.md`, untracked.
- `weekly-guard` a non-Sunday `--week-ending` is refused.

## How to get to it (user POV)

- GitHub Actions `daily-alert` Sunday crons (`17 8 * * 0`, `17 9 * * 0`) or Run workflow with `mode=weekly`.
- Terminal: `negprice weekly [--week-ending YYYY-MM-DD] [--root DIR]`.
- Site: `weekly/latest.json`, `weekly/<Sunday>.png`, and the `This week` section of `index.html`.

## Driving it with verify.sh

Preconditions:

- Baseline from the README; the week ending 2026-10-04 is committed in `$ROOT/site/weekly/`.

- **Build, offline.** Run `$V cli --offline weekly build -- "$NEGPRICE" weekly --root "$ROOT" --week-ending 2026-10-04`. Exit `0`; stdout has `week ending 2026-10-04`, `lowest price: 1.08 €/MWh, Tue 2026-09-29 at 14:00` and `zero-price days YTD as of 2026-10-04: 148`.
- **JSON second view.** Run `$V cli weekly json -- jq -c '{week,lowest}' "$ROOT/site/weekly/2026-10-04.json"`. Values match stdout above.
- **Drafts untracked.** Run `$V cli weekly drafts -- ls "$ROOT/drafts"` (lists `weekly-2026-10-04-en.md` and `-es.md`) and `$V cli weekly drafts-untracked -- git ls-files drafts` (empty).
- **Guard.** Run `$V cli --offline weekly not-sunday -- "$NEGPRICE" weekly --root "$ROOT" --week-ending 2026-10-03`. Exit `1`; stderr `error: ValueError: 2026-10-03 is a Saturday, not a Sunday: a week runs Monday to Sunday`.
- **Served.** Run `$V fetch weekly latest "${URL}weekly/latest.json"`. 200.

## Gotchas

- Against committed data the build says `already built` (identical JSON): that is the idempotent path, still a valid drive. To force a fresh write, delete `$ROOT/site/weekly/2026-10-04.json` first (scratch only).
- Default week = latest complete week whose Sunday is today or earlier and has all seven days in history.
- Week-over-week numbers appear only when the previous week is complete in history.
