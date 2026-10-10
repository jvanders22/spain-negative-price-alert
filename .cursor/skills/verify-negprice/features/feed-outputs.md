# Feed outputs

The pipeline publishes machine-readable outputs for the latest delivery day: `latest.json` (versioned
schema), an RSS feed, and a chart PNG, all with the OMIE attribution line.

## Sub-features

- `outputs-json` `site/latest.json` validates against `src/negprice/latest.schema.json`.
- `outputs-feed` `site/feed.xml` parses as RSS and its newest item names the latest date.
- `outputs-chart` `site/charts/latest.png` and `site/charts/<date>.png` are real PNGs.
- `outputs-rebuild` `negprice rebuild` regenerates them from history with no network, byte-stable.

## How to get to it (user POV)

- Browser or feed reader: `<site>/latest.json`, `<site>/feed.xml`, `<site>/charts/latest.png`.
- Terminal: `negprice rebuild --root DIR` (and every `run`) writes them under `DIR/site/`.

## Driving it with verify.sh

Preconditions:

- Baseline from the README.

- **Rebuild offline.** Run `$V cli --offline outputs rebuild -- "$NEGPRICE" rebuild --root "$ROOT"`. Exit `0`.
- **Byte-stable.** Run `$V cli outputs stable -- git diff --no-index --stat "$REPO/site/latest.json" "$ROOT/site/latest.json"`. Exit `0`, empty stdout (when verifying main; on a branch that changes outputs, the diff is the evidence of the change).
- **Schema.** Run `$V cli outputs schema -- "$REPO/.venv/bin/python" -c "import json,jsonschema,sys;jsonschema.validate(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])));print('valid')" "$ROOT/site/latest.json" src/negprice/latest.schema.json`. Stdout `valid`.
- **Fields.** Run `$V cli outputs fields -- jq -c '{date,n_periods,n_le0,attribution}' "$ROOT/site/latest.json"`. `date` is the latest row of `daily.csv`; `attribution` mentions OMIE.
- **Feed.** Run `$V cli outputs feed -- "$REPO/.venv/bin/python" -c "import feedparser,sys;f=feedparser.parse(sys.argv[1]);assert not f.bozo,f.bozo_exception;print(len(f.entries),f.entries[0].title)" "$ROOT/site/feed.xml"`. Exit `0`; stdout is `<n> <title>` and the title contains the latest date (e.g. `30 Cheapest 3h: Sun 2026-10-11 — ...`).
- **Chart.** Run `$V cli outputs chart -- "$REPO/.venv/bin/python" -c "import struct,sys;b=open(sys.argv[1],'rb').read(24);assert b[:8]==b'\x89PNG\r\n\x1a\n','not a PNG';print('PNG %dx%d'%struct.unpack('>II',b[16:24]))" "$ROOT/site/charts/latest.png"`. Stdout `PNG 1000x450`.
- **Served.** Run `$V fetch outputs latest-json "${URL}latest.json"` and `$V fetch outputs feed-xml "${URL}feed.xml"`. Both 200.

## Gotchas

- `rebuild` also rewrites the latest day's draft in `$ROOT/drafts/`; drafts never reach `site/`.
- Outputs carry no wall-clock time by design; any diff after a no-change rebuild is a real regression.
- The box may have no `file` command; the chart check reads the PNG header with Python instead.
- `feedparser` and `jsonschema` are dev extras; use `.venv/bin/python`, not system python.
