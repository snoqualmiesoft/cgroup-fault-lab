#!/usr/bin/env bash
# CPU fault cycle: baseline report -> lower the CPU quota -> report under fault -> recover ->
# recovery report. Each report is the health command sampling for DURATION seconds under the
# same load. Results go to reports/<time>-cpu/ (or $OUT).
source "$(dirname "$0")/common.sh"
OUT="${OUT:-$ROOT/reports/$(date +%Y%m%d-%H%M%S)-cpu}"
mkdir -p "$OUT"

recover() { incus config set "$NAME" limits.cpu.allowance "$CPU_LIMIT"; }
trap recover EXIT          # put the quota back even after an error or Ctrl-C (bin/restore.sh does it after kill -9)

log "1/3 Baseline report: cpu.max $(cat "$CG/cpu.max")"
report "$OUT/1-baseline"

log "Inject: incus config set $NAME limits.cpu.allowance $FAULT_CPU_LIMIT"
incus config set "$NAME" limits.cpu.allowance "$FAULT_CPU_LIMIT"
echo "incus config set $NAME limits.cpu.allowance $FAULT_CPU_LIMIT    (cpu.max now: $(cat "$CG/cpu.max"))" >"$OUT/injection.txt"
log "2/3 Report under fault: cpu.max $(cat "$CG/cpu.max")"
report "$OUT/2-fault"

log "Recover: incus config set $NAME limits.cpu.allowance $CPU_LIMIT"
recover
log "3/3 Recovery report: cpu.max $(cat "$CG/cpu.max")"
report "$OUT/3-recovery"

python3 "$ROOT/bin/summarize.py" "$OUT" | tee "$OUT/summary.md"
