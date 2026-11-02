#!/usr/bin/env bash
# Network fault cycle: baseline report -> tc netem on the container's dedicated host-side link ->
# report under fault -> recover the original qdisc -> recovery report. Each report is the health
# command sampling for DURATION seconds under the same load. Results go to reports/<time>-net/ (or $OUT).
source "$(dirname "$0")/common.sh"
OUT="${OUT:-$ROOT/reports/$(date +%Y%m%d-%H%M%S)-net}"
mkdir -p "$OUT"
LINK=$(veth)                                   # only this container's link, never the shared bridge
ORIGINAL=$(tc qdisc show dev "$LINK")          # the qdisc to restore

recover() { tc qdisc del dev "$LINK" root 2>/dev/null || true; }   # back to the default qdisc
trap recover EXIT          # also after an error or Ctrl-C (bin/restore.sh does it after kill -9)

log "1/3 Baseline report: $ORIGINAL"
report "$OUT/1-baseline"

log "Inject: tc qdisc replace dev $LINK root netem $FAULT_NETEM"
# shellcheck disable=SC2086  # FAULT_NETEM holds several words on purpose
tc qdisc replace dev "$LINK" root netem $FAULT_NETEM
{
  echo "tc qdisc replace dev $LINK root netem $FAULT_NETEM"
  echo "before: $ORIGINAL"
  echo "after:  $(tc qdisc show dev "$LINK")"
} >"$OUT/injection.txt"
log "2/3 Report under fault: $(tc qdisc show dev "$LINK")"
report "$OUT/2-fault"
tc -s qdisc show dev "$LINK" >"$OUT/netem-stats.txt"

log "Recover: tc qdisc del dev $LINK root"
recover
if [[ "$(tc qdisc show dev "$LINK")" == "$ORIGINAL" ]]; then
  log "Original qdisc is back: $ORIGINAL"
else
  log "WARNING: qdisc is '$(tc qdisc show dev "$LINK")', not the original '$ORIGINAL'"
fi
log "3/3 Recovery report"
report "$OUT/3-recovery"

python3 "$ROOT/bin/summarize.py" "$OUT" | tee "$OUT/summary.md"
