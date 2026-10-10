---
name: verify-negprice
description: "Verify spain-negative-price-alert (Spanish OMIE day-ahead zero/negative-price alert) the way a user meets it: the `negprice` CLI pipeline (daily run, estimate, weekly) and the static site, served locally and live on GitHub Pages. Use before merging any change to src/, site/, workflows or data outputs, or whenever you need proof that the alert, feed, chart, price-paid estimate, weekly summary or Pages site still works."
---

# verify-negprice

The app is a Python CLI (`negprice`, entry point in `pyproject.toml`) that turns OMIE's day-ahead file into
history CSVs, `site/latest.json`, `site/feed.xml`, charts, a price-paid estimate, a weekly summary and a
static page, plus X drafts in gitignored `drafts/`. GitHub Actions runs it on a schedule and deploys `site/`
to Pages at https://jvanders22.github.io/spain-negative-price-alert/. There is no server and no login.

Surfaces, in order of importance: (1) the CLI/pipeline, (2) the static site served locally from a scratch
build, (3) the live Pages site (read-only). Every command takes `--root DIR`; the harness points it at a
scratch copy so the checkout's `data/` and `site/` are never touched.

Helper (executable, one file): `.cursor/skills/verify-negprice/scripts/verify.sh`. Below, `V` means that path:

```bash
V=$(git rev-parse --show-toplevel)/.cursor/skills/verify-negprice/scripts/verify.sh
```

## Launch

```bash
unset VERIFY_RUN_ID       # drop a pin left over from an earlier run in this shell
$V launch                 # optional: $V launch my-run-id
eval "$($V env)"          # exports VERIFY_RUN_ID RUN_ID ROOT URL LIVE EVID NEGPRICE REPO
```

What it does: installs `.venv` with `pip install -e ".[dev]"` if `.venv/bin/negprice` is missing; extracts
`data/` and `site/` from `git HEAD` into `/tmp/verify-negprice-$RUN_ID/root` (committed state only, so
uncommitted edits to data/site are NOT in the scratch root; rebuild with `negprice rebuild --root "$ROOT"`
when verifying a code change); starts `python3 -m http.server <free port> --bind 127.0.0.1` on
`$ROOT/site`. Ready when it prints `READY run=... url=http://127.0.0.1:<port>/ ...`.

Isolation: each run has its own scratch root, port, evidence dir and Chrome profile. `launch` records the
last launched run in `.verify-evidence/.current`; `eval "$($V env)"` pins your shell to YOUR run by
exporting `VERIFY_RUN_ID`, which every later `$V` command uses first. For runs side by side, give each a
distinct id (`$V launch run-a`) and eval `env` right after its launch, before anyone launches another;
or set `VERIFY_RUN_ID=<id>` explicitly. Never point `--root` at the checkout during verification.
Teardown is `$V cleanup` (see Cleanup).

If your tool runner kills background processes between tool calls (seen with Codex's sandbox), Doctor
reports the server pid gone: run launch, env, doctor, the drive and cleanup in ONE shell invocation.

## Doctor

```bash
$V doctor            # add --no-live when offline
```

Read-only. Prints `OK`/`WARN`/`FAIL` lines and exits 1 on any FAIL. Checks: `negprice` CLI runs; checkout
HEAD vs `origin/main` (WARN when verifying a branch); an active run exists; the static server pid is the
one this run started and owns its port; the local page title is `Spain day-ahead zero-price alert`;
`drafts/` has no tracked files; `.verify-evidence/` is gitignored; the live Pages URL answers 200. Run it
first, and again whenever anything looks off. Do not drive an instance Doctor fails on.

## Drive

Read `.cursor/skills/verify-negprice/features/README.md` (paths in this skill are relative to the repo
root unless they start with `$`), then the matching feature file in the same directory. All CLI actions go through:

```bash
$V cli [--offline] <feature-id> <step-name> -- <command...>
```

It runs the command from the repo root and records `<step>.cmd/.out/.err/.exit`. `--offline` runs it inside
`unshare -rn` (no network namespace): use it for every step that must not touch the network, which proves
the no-network claim instead of trusting it. Pages and local site:

```bash
$V fetch <feature-id> <name> <url>    # status + headers + body
$V shot  <feature-id> <name> <url>    # headless Chrome screenshot + rendered DOM
```

Real handles in this repo: CLI subcommands `run`, `rebuild`, `estimate`, `weekly`, `pvpc-refresh`; fixture
`tests/fixtures/marginalpdbc_20261010.1` (Sat 10 Oct 2026, 96 quarter-hours, 18 at or below €0); page title
`Spain day-ahead zero-price alert — <date>`; H2s `This week`, `What a 2.0TD / 3.0TD customer might pay
(estimate)`, `Last 30 days`, `Data`; files `latest.json`, `feed.xml`, `charts/latest.png`,
`estimate/latest.json`, `estimate/<date>-<tariff>.csv`, `weekly/latest.json`, `wholesale-vs-bill.html`.
Schema: `src/negprice/latest.schema.json`.

## Evidence

Location: `.verify-evidence/<RUN_ID>/<feature-id>/` at the repo root. It is gitignored and survives
cleanup. Never commit it, never paste draft text from it into PRs, READMEs or chat summaries meant for
the public.

Proof standards:

- Drive the real user path: the `negprice` CLI as the workflow calls it, the built HTML as a browser gets
  it. No importing internals, no test-only endpoints.
- Capture the action and the resulting state: the command transcript AND a second, read-only view of what
  it wrote (the CSV row, the JSON field, the served page).
- Verify side effects: files written under `$ROOT`, nothing new in `git status` of the checkout, nothing
  under `drafts/` tracked.
- Mocks only at the existing production boundary: the OMIE fetch is replaced by `--file <fixture>`, which
  the CLI supports for exactly this. Prove "offline" with `--offline`, not by the flag's name.
- Numbers in a verdict come from the evidence files, never from memory.
- A verdict names the feature IDs and entry points driven. An entry point you could not reach is reported
  as BLOCKED with the command and the unmet precondition, never as verified through another path.

## Cleanup

```bash
$V cleanup
```

Stops only the server pid this run started (checked against its command line, never by process name),
deletes `/tmp/verify-negprice-$RUN_ID` (refuses any other path), clears `.verify-evidence/.current` only if
it still points at this run, then
lists the evidence files that remain. Run it after every attempt, including failed ones. Afterwards confirm
`git status --short` in the checkout shows no changes from the run.

## Helpers

`scripts/verify.sh` subcommands: `launch [RUN_ID]`, `env`, `doctor [--no-live]`,
`cli [--offline] FEATURE NAME -- CMD...`, `fetch FEATURE NAME URL`, `shot FEATURE NAME URL`, `stop`,
`cleanup`. Run it with no arguments for usage. Requires bash, python3, curl, ss, unshare, git, and Chrome
or Chromium for `shot`.

Keep the map honest as the app changes: use pstack's `/maintain-verification-skill`.
