#!/usr/bin/env bash
# Demonstration: the recovery command works after an interrupted fault run.
# For each fault script: start it, kill -9 its whole process group right after it injects the
# fault (so its own cleanup cannot run), show the fault is still in the kernel, run
# bin/restore.sh, and check that the original cpu.max and qdisc are back.
source "$(dirname "$0")/../bin/common.sh"
DIR="${1:-$ROOT/reports/$(date +%Y%m%d-%H%M%S)-interrupt}"
mkdir -p "$DIR"
LINK=$(veth)
cpu_before=$(cat "$CG/cpu.max")
qdisc_before=$(tc qdisc show dev "$LINK")
injected() { [[ "$(cat "$CG/cpu.max")" != "$cpu_before" ]] || tc qdisc show dev "$LINK" | grep -q netem; }
failed=0

for fault in cpu net; do
  log "Start bin/fault-$fault.sh; kill -9 it as soon as the fault is in the kernel"
  OUT="$DIR/$fault" setsid "$ROOT/bin/fault-$fault.sh" >"$DIR/fault-$fault.log" 2>&1 &
  group=$!
  for _ in $(seq 120); do injected && break; sleep 0.5; done
  kill -9 -- "-$group" 2>/dev/null || true; wait "$group" 2>/dev/null || true
  log "  after kill -9: cpu.max $(cat "$CG/cpu.max"), $(tc qdisc show dev "$LINK")"
  if ! injected; then log "  FAIL: the fault was not left behind, so this test proves nothing"; failed=1; continue; fi

  "$ROOT/bin/restore.sh"
  if [[ "$(cat "$CG/cpu.max")" == "$cpu_before" && "$(tc qdisc show dev "$LINK")" == "$qdisc_before" ]]; then
    log "  PASS: bin/restore.sh removed the $fault fault left by the interrupted run"
  else
    log "  FAIL: still cpu.max $(cat "$CG/cpu.max"), $(tc qdisc show dev "$LINK")"; failed=1
  fi
done
[[ $failed = 0 ]] && log "Recovery after interrupted runs: PASS" || { log "Recovery after interrupted runs: FAIL"; exit 1; }
