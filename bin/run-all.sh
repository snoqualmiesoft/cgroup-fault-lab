#!/usr/bin/env bash
# Everything, in order. Stops at the first step that fails. Results go to reports/run-<time>/.
source "$(dirname "$0")/common.sh"
RUN="$ROOT/reports/run-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN"
exec > >(tee "$RUN/console.log") 2>&1        # keep a copy of everything printed
step() { echo; log "== $*"; }

step "Preflight";                  "$ROOT/bin/preflight.sh"
step "Unit tests";                 python3 -m unittest discover -s "$ROOT/tests"
step "Smoke test (setup, reachability, report, cleanup)"; "$ROOT/tests/smoke.sh" "$RUN/smoke"
step "Setup";                      "$ROOT/bin/setup.sh"
step "Machine and lab layout"
{
  echo "os:       $(. /etc/os-release && echo "$PRETTY_NAME"), kernel $(uname -r)"
  echo "cpus:     $(nproc --all) x $(lscpu | awk -F': +' '/^Model name/ {print $2; exit}') ($(uname -m))"
  echo "memory:   $(free -g | awk '/Mem:/ {print $2}') GiB"
  echo "incus:    $(incus version | awk '/Server/ {print $3}')"
  echo "commit:   $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "settings: CLIENTS=$CLIENTS DURATION=$DURATION WORK=$WORK FAULT_CPU_LIMIT=$FAULT_CPU_LIMIT FAULT_NETEM='$FAULT_NETEM'"
  echo
  "$ROOT/bin/inspect.sh"
} | tee "$RUN/machine.txt"
step "Baseline profile";           python3 "$ROOT/bin/health.py" --save-baseline "$ROOT/$BASELINE"
cp "$ROOT/$BASELINE" "$RUN/baseline.json"
step "Health";                     report "$RUN/health-start"
step "CPU fault cycle";            OUT="$RUN/cpu" "$ROOT/bin/fault-cpu.sh"
step "Network fault cycle";        OUT="$RUN/net" "$ROOT/bin/fault-net.sh"
step "Recovery after an interrupted fault run"; "$ROOT/tests/interrupt.sh" "$RUN/interrupt"
step "Health";                     report "$RUN/health-end"

if [[ -n "${SUDO_USER:-}" ]]; then chown -R "$SUDO_USER": "$ROOT/reports"; fi
step "Done. Results: $RUN"
