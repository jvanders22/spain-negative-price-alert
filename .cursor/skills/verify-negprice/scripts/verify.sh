#!/usr/bin/env bash
# verify-negprice harness: drive spain-negative-price-alert like a user (CLI + static site) and keep proof.
# Usage (from anywhere inside the repo):
#   verify.sh launch [RUN_ID]          scratch root + local static server; prints READY line
#   verify.sh env                      eval "$(verify.sh env)" -> ROOT, URL, LIVE, EVID, RUN_ID, NEGPRICE
#   verify.sh doctor [--no-live]       read-only health check; exit 1 on any FAIL
#   verify.sh cli [--offline] FEATURE NAME -- CMD...   run CMD, record cmd/stdout/stderr/exit
#   verify.sh fetch FEATURE NAME URL   curl URL, record status, headers, body
#   verify.sh shot FEATURE NAME URL    headless Chrome screenshot (.png) + rendered DOM (.dom.html)
#   verify.sh stop                     stop the static server this run started
#   verify.sh cleanup                  stop + delete scratch root; evidence is kept and listed
set -euo pipefail

REPO=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
EVID_ROOT="$REPO/.verify-evidence"
LIVE="https://jvanders22.github.io/spain-negative-price-alert/"
NEGPRICE="$REPO/.venv/bin/negprice"
PY="$REPO/.venv/bin/python"

die() { echo "verify-negprice: $*" >&2; exit 2; }
# Which run a command targets: $VERIFY_RUN_ID (exported by `env`) wins, else the last launched run.
current() {
  if [ -n "${VERIFY_RUN_ID:-}" ]; then echo "$VERIFY_RUN_ID"; return; fi
  [ -f "$EVID_ROOT/.current" ] || die "no active run; run 'verify.sh launch' first"; cat "$EVID_ROOT/.current"
}
load() {
  RUN_ID=$(current); S="/tmp/verify-negprice-$RUN_ID"
  [ -f "$S/state.env" ] || die "scratch state $S/state.env missing (run cleaned up?)"
  # shellcheck disable=SC1091
  . "$S/state.env"; EVID="$EVID_ROOT/$RUN_ID"
}

cmd_launch() {
  local id="${1:-$(date +%Y%m%d-%H%M%S)-$$}"
  local s="/tmp/verify-negprice-$id"
  [ -e "$s" ] && die "scratch $s already exists; pick another RUN_ID"
  if [ ! -x "$NEGPRICE" ]; then
    python3 -m venv "$REPO/.venv" && "$REPO/.venv/bin/pip" install -q -e "$REPO[dev]"
  fi
  mkdir -p "$s/root" "$s/chrome"
  # Committed state only: data/ and site/ from HEAD, so the run never mutates the checkout.
  git -C "$REPO" archive HEAD data site | tar -x -C "$s/root"
  local port
  port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
  nohup python3 -m http.server "$port" --bind 127.0.0.1 --directory "$s/root/site" >"$s/http.log" 2>&1 &
  local pid=$!
  cat >"$s/state.env" <<STATE
ROOT=$s/root
PORT=$port
PID=$pid
URL=http://127.0.0.1:$port/
HEAD_SHA=$(git -C "$REPO" rev-parse --short HEAD)
STATE
  mkdir -p "$EVID_ROOT/$id"
  echo "$id" >"$EVID_ROOT/.current"
  for _ in $(seq 1 50); do
    if curl -fs -o /dev/null "http://127.0.0.1:$port/index.html" 2>/dev/null; then
      echo "READY run=$id url=http://127.0.0.1:$port/ root=$s/root evidence=$EVID_ROOT/$id head=$(git -C "$REPO" rev-parse --short HEAD)"
      return 0
    fi
    sleep 0.2
  done
  die "static server on port $port did not answer within 10s (see $s/http.log)"
}

cmd_env() {
  load
  printf 'export VERIFY_RUN_ID=%q RUN_ID=%q ROOT=%q URL=%q LIVE=%q EVID=%q NEGPRICE=%q REPO=%q\n' \
    "$RUN_ID" "$RUN_ID" "$ROOT" "$URL" "$LIVE" "$EVID" "$NEGPRICE" "$REPO"
}

cmd_doctor() {
  local live=1 fail=0; [ "${1:-}" = "--no-live" ] && live=0
  ok() { echo "OK   $*"; }; bad() { echo "FAIL $*"; fail=1; }; warn() { echo "WARN $*"; }
  [ -x "$NEGPRICE" ] && "$NEGPRICE" --help >/dev/null 2>&1 && ok "negprice CLI at $NEGPRICE" || bad "negprice CLI missing (launch installs it)"
  local head origin; head=$(git -C "$REPO" rev-parse --short HEAD); origin=$(git -C "$REPO" rev-parse --short origin/main 2>/dev/null || echo "?")
  [ "$head" = "$origin" ] && ok "checkout HEAD $head = origin/main" || warn "checkout HEAD $head != origin/main $origin (verifying a branch?)"
  local rid; rid=$(current 2>/dev/null || true)
  if [ -n "$rid" ] && [ -f "/tmp/verify-negprice-$rid/state.env" ]; then
    load; ok "run $RUN_ID, scratch root $ROOT (built from $HEAD_SHA)"
    if kill -0 "$PID" 2>/dev/null && tr '\0' ' ' <"/proc/$PID/cmdline" | grep -q "http.server $PORT"; then
      ok "static server pid $PID is ours"
    else bad "static server pid $PID not running or not ours"; fi
    ss -ltnp 2>/dev/null | grep -q "127.0.0.1:$PORT .*pid=$PID," && ok "port $PORT owned by pid $PID" || bad "port $PORT not owned by pid $PID"
    curl -fsS "${URL}index.html" 2>/dev/null | grep -q "<title>Spain day-ahead zero-price alert" \
      && ok "local site answers with the alert page title" || bad "local site title missing at $URL"
  else
    bad "no active run (verify.sh launch)"
  fi
  [ -z "$(git -C "$REPO" ls-files drafts)" ] && ok "drafts/ not tracked by git" || bad "drafts/ has tracked files"
  git -C "$REPO" check-ignore -q .verify-evidence && ok ".verify-evidence/ is gitignored" || bad ".verify-evidence/ is NOT gitignored"
  if [ "$live" = 1 ]; then
    local code; code=$(curl -s -o /dev/null -w '%{http_code}' "$LIVE" || echo 000)
    [ "$code" = 200 ] && ok "live Pages $LIVE -> 200" || bad "live Pages $LIVE -> $code"
  fi
  return "$fail"
}

cmd_cli() {
  local offline=0; [ "${1:-}" = "--offline" ] && { offline=1; shift; }
  local feature="$1" name="$2"; shift 2; [ "${1:-}" = "--" ] && shift
  load; local d="$EVID/$feature"; mkdir -p "$d"
  printf '%q ' "$@" >"$d/$name.cmd"; echo >>"$d/$name.cmd"
  [ "$offline" = 1 ] && echo "# run with: unshare -rn (no network namespace)" >>"$d/$name.cmd"
  local rc=0; export MPLCONFIGDIR="$S/mpl"; mkdir -p "$MPLCONFIGDIR"
  if [ "$offline" = 1 ]; then
    (cd "$REPO" && export ROOT URL LIVE EVID NEGPRICE REPO RUN_ID && unshare -rn "$@") >"$d/$name.out" 2>"$d/$name.err" || rc=$?
  else
    (cd "$REPO" && export ROOT URL LIVE EVID NEGPRICE REPO RUN_ID && "$@") >"$d/$name.out" 2>"$d/$name.err" || rc=$?
  fi
  echo "$rc" >"$d/$name.exit"
  cat "$d/$name.out"; [ -s "$d/$name.err" ] && sed 's/^/[stderr] /' "$d/$name.err" >&2
  echo "[exit $rc] evidence: $d/$name.{cmd,out,err,exit}" >&2
  return "$rc"
}

cmd_fetch() {
  local feature="$1" name="$2" url="$3"; load; local d="$EVID/$feature"; mkdir -p "$d"
  local code; code=$(curl -sS -D "$d/$name.headers" -o "$d/$name.body" -w '%{http_code}' "$url" || echo 000)
  echo "$url $code" >"$d/$name.status"; echo "$url -> $code ($d/$name.body)"
  [ "$code" = 200 ]
}

cmd_shot() {
  local feature="$1" name="$2" url="$3"; load; local d="$EVID/$feature"; mkdir -p "$d"
  local chrome; chrome=$(command -v google-chrome || command -v chromium || command -v chromium-browser) || die "no Chrome/Chromium"
  local flags=(--headless=new --no-sandbox --disable-gpu --hide-scrollbars --user-data-dir="$S/chrome" --window-size=1280,2400)
  timeout 60 "$chrome" "${flags[@]}" --screenshot="$d/$name.png" "$url" >/dev/null 2>&1 || true
  timeout 60 "$chrome" "${flags[@]}" --dump-dom "$url" >"$d/$name.dom.html" 2>/dev/null || true
  [ -s "$d/$name.png" ] && [ -s "$d/$name.dom.html" ] || die "screenshot or DOM empty for $url"
  echo "$url -> $d/$name.png, $d/$name.dom.html"
}

cmd_stop() {
  load
  if kill -0 "$PID" 2>/dev/null && tr '\0' ' ' <"/proc/$PID/cmdline" | grep -q "http.server $PORT"; then
    kill "$PID"; echo "stopped static server pid $PID"
  else echo "static server pid $PID already gone"; fi
}

cmd_cleanup() {
  load; cmd_stop || true
  case "$S" in /tmp/verify-negprice-*) rm -rf "$S" ;; *) die "refusing to delete $S" ;; esac
  # Only clear the "last launched" pointer if it still points at this run.
  [ "$(cat "$EVID_ROOT/.current" 2>/dev/null)" = "$RUN_ID" ] && rm -f "$EVID_ROOT/.current"
  echo "removed scratch $S; evidence kept:"; find "$EVID" -type f | sort
}

sub="${1:-}"; shift || true
case "$sub" in
  launch) cmd_launch "$@" ;; env) cmd_env ;; doctor) cmd_doctor "$@" ;; cli) cmd_cli "$@" ;;
  fetch) cmd_fetch "$@" ;; shot) cmd_shot "$@" ;; stop) cmd_stop ;; cleanup) cmd_cleanup ;;
  *) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
