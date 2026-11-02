#!/usr/bin/env bash
# Recovery command: put the lab container back to lab.env and remove any netem.
# Safe to run any time, also after an interrupted fault script.
source "$(dirname "$0")/common.sh"
LINK=$(veth)

incus config unset "$NAME" limits.cpu.allowance        # unset first, so Incus writes cpu.max again
incus config set "$NAME" limits.cpu.allowance "$CPU_LIMIT"
incus config unset "$NAME" limits.memory
incus config set "$NAME" limits.memory "$MEMORY_LIMIT"
if [[ -n "$CPU_PIN" ]]; then incus config set "$NAME" limits.cpu "$CPU_PIN"; fi
tc qdisc del dev "$LINK" root 2>/dev/null || true       # back to the default qdisc
log "Restored: cpu.max $(cat "$CG/cpu.max"), memory.max $(cat "$CG/memory.max"), $(tc qdisc show dev "$LINK")"
