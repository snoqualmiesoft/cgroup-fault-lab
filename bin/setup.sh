#!/usr/bin/env bash
# Install Incus, create the lab bridge and a fresh lab container with its limits, start the app.
# Running it again gives the same result: the container is deleted and created again.
#
# Host changes (all listed in README.md): packages incus, jq, curl, iproute2 (and
# linux-modules-extra if the kernel has no netem); an Incus storage pool "default"; the bridge
# $BRIDGE with NAT rules that Incus manages; a local image copy. Nothing else is changed by hand.
source "$(dirname "$0")/common.sh"

log "1/5 Install packages (only the missing ones, so a second setup works offline)"
missing=()
for p in incus jq curl iproute2 util-linux; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
modinfo sch_netem >/dev/null 2>&1 || missing+=("linux-modules-extra-$(uname -r)")   # tc loads netem itself
if [[ ${#missing[@]} -gt 0 ]]; then
  log "  installing: ${missing[*]}"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null ||
    die "could not install ${missing[*]}"
fi
modinfo sch_netem >/dev/null 2>&1 || log "WARNING: no netem module for this kernel, the network fault will not work"
forward_before=$(sysctl -n net.ipv4.ip_forward)

log "2/5 Incus storage (no default network: the lab uses its own bridge)"
if ! incus storage show default >/dev/null 2>&1; then
  incus admin init --preseed <<'EOF'
storage_pools:
- name: default
  driver: dir
profiles:
- name: default
  devices:
    root: {path: /, pool: default, type: disk}
EOF
fi

log "3/5 Lab bridge $BRIDGE ($BRIDGE_ADDRESS, NAT, no IPv6)"
incus network show "$BRIDGE" >/dev/null 2>&1 ||
  incus network create "$BRIDGE" ipv4.address="$BRIDGE_ADDRESS" ipv4.nat=true ipv6.address=none
log "  net.ipv4.ip_forward before setup: $forward_before, now: $(sysctl -n net.ipv4.ip_forward)"

log "4/5 Fresh container $NAME: CPU $CPU_LIMIT, memory $MEMORY_LIMIT, CPUs ${CPU_PIN:-any}, IP $CONTAINER_IP"
incus image show "$IMAGE_ALIAS" >/dev/null 2>&1 ||
  incus image copy "$IMAGE" local: --alias "$IMAGE_ALIAS" --quiet   # the only download
incus delete --force "$NAME" 2>/dev/null || true
pin=(); [[ -n "$CPU_PIN" ]] && pin=(-c "limits.cpu=$CPU_PIN")
incus init --quiet "$IMAGE_ALIAS" "$NAME" --network "$BRIDGE" \
  -c limits.cpu.allowance="$CPU_LIMIT" -c limits.memory="$MEMORY_LIMIT" "${pin[@]}"
incus config device set "$NAME" eth0 ipv4.address "$CONTAINER_IP"
incus start "$NAME"
for _ in $(seq 60); do state | grep -q "\"$CONTAINER_IP\"" && break; sleep 1; done
state | grep -q "\"$CONTAINER_IP\"" || die "the container did not get $CONTAINER_IP"

log "5/5 Start the app in the container"
incus exec "$NAME" -- systemctl is-system-running --wait >/dev/null 2>&1 || true
if incus exec "$NAME" -- sh -c 'command -v python3' >/dev/null; then
  log "  python3 is in the image"
else
  log "  python3 is not in the image: installing it (needs network)"
  incus exec "$NAME" -- sh -c 'apt-get update -qq && apt-get install -y -qq python3' >/dev/null
fi
incus exec "$NAME" -- mkdir -p /opt/lab-app
incus file push --quiet "$ROOT/app/server.py" "$NAME/opt/lab-app/server.py"
incus file push --quiet "$ROOT/app/lab-app.service" "$NAME/etc/systemd/system/lab-app.service"
incus exec "$NAME" -- sh -c "printf 'PORT=$PORT\nWORK=$WORK\n' > /etc/default/lab-app"
incus exec "$NAME" -- systemctl daemon-reload
incus exec "$NAME" -- systemctl enable --now lab-app --quiet

for _ in $(seq 30); do curl -fs "$(app_url)/healthz" >/dev/null && break; sleep 1; done
if ! curl -fs "$(app_url)/healthz" >/dev/null; then
  incus exec "$NAME" -- journalctl -u lab-app -n 20 --no-pager || true
  die "the app did not start (its log is above)"
fi
log "Ready: app at $(app_url), reachable from the host. Next: make baseline"
