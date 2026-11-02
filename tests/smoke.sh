#!/usr/bin/env bash
# Smoke test: setup, reachability, report creation, cleanup. Repeatable: it builds a fresh
# container and deletes it at the end. Exit 0 if every check passes.
source "$(dirname "$0")/../bin/common.sh"
DIR="${1:-$ROOT/reports/$(date +%Y%m%d-%H%M%S)-smoke}"
mkdir -p "$DIR"
set +e
failed=0
check() {   # check "description" 'command'
  if eval "$2" >/dev/null 2>&1; then echo "  PASS  $1"; else echo "  FAIL  $1"; failed=1; fi
}
URL="$(app_url)"

echo "Smoke test (logs in $DIR)"
check "setup builds the lab"            '"$ROOT/bin/setup.sh" >"$DIR/setup.log" 2>&1'
check "container is running"            '[ "$(incus list "$NAME" -c s -f csv)" = RUNNING ]'
check "app reachable from the host"     'curl -fs "$URL/healthz"'
check "/work returns cpu_ms"            'curl -fs "$URL/work?n=1000" | grep -q cpu_ms'
check "/work rejects bad input"         '[ "$(curl -s -o /dev/null -w "%{http_code}" "$URL/work?n=abc")" = 400 ]'
check "load generator reports p99"      'python3 "$ROOT/bin/loadgen.py" "$URL/work" --duration 2 --json | grep -q p99_ms'
check "report is created (JSON + text)" 'python3 "$ROOT/bin/health.py" --interval 2 --out "$DIR/report.json" >"$DIR/report.txt"; [ $? -le 3 ] && python3 -m json.tool "$DIR/report.json" && grep -q "exit" "$DIR/report.txt"'
check "report keeps a 3 s runtime limit" 'start=$(date +%s); python3 "$ROOT/bin/health.py" --interval 30 --timeout 3; [ $(( $(date +%s) - start )) -le 5 ]'
check "no netem on the lab link"        '! tc qdisc show dev "$(veth)" | grep -q netem'
check "cleanup deletes the container"   '"$ROOT/bin/teardown.sh" && ! incus info "$NAME"'

if [ "$failed" = 0 ]; then echo "Smoke: all passed"; else echo "Smoke: FAILED"; exit 1; fi
