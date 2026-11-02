#!/usr/bin/env bash
# Show the lab container, its limits and its network. Read-only.
source "$(dirname "$0")/common.sh"

echo "== Container"
incus list "$NAME" --columns ns4 --format compact
[[ "$(state | jq -r .status)" = Running ]] || exit 0
echo
echo "== Limits: what Incus was told / what the kernel enforces ($CG)"
echo "  CPU quota   limits.cpu.allowance=$(incus config get "$NAME" limits.cpu.allowance)   cpu.max: $(cat "$CG/cpu.max")"
echo "  CPU set     limits.cpu=$(incus config get "$NAME" limits.cpu)   cpuset.cpus.effective: $(cat "$CG/cpuset.cpus.effective")"
echo "  memory      limits.memory=$(incus config get "$NAME" limits.memory)   memory.max: $(cat "$CG/memory.max"), memory.current: $(cat "$CG/memory.current")"
echo
echo "== Network"
LINK=$(veth)
echo "  container side (inside its network namespace):"
nsenter -t "$(pid)" -n ip -brief address show dev eth0 | sed 's/^/    /'
nsenter -t "$(pid)" -n ip link show dev eth0 | grep -o 'mtu [0-9]*' | sed 's/^/    eth0 /'
nsenter -t "$(pid)" -n ip route | sed 's/^/    route: /'
echo "  host side:"
ip -brief link show dev "$LINK" | sed 's/^/    /'
ip link show dev "$LINK" | grep -oE 'mtu [0-9]+|master [^ ]+' | tr '\n' ' ' | sed "s/^/    $LINK /"; echo
ip -brief address show dev "$BRIDGE" | sed 's/^/    /'
echo "    qdisc: $(tc qdisc show dev "$LINK")"
