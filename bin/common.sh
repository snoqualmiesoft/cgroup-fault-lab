# Shared settings and helpers for the bash scripts. Every script that uses it runs as root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/lab.env"
CG="/sys/fs/cgroup/lxc.payload.$NAME"          # the container's cgroup folder

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

# Facts about the container, from the Incus API
state()   { incus query "/1.0/instances/$NAME/state"; }
veth()    { state | jq -r '.network.eth0.host_name'; }    # host side of the container's network link
pid()     { state | jq -r '.pid'; }                       # the container's first process, seen from the host
app_url() { echo "http://$CONTAINER_IP:$PORT"; }

# One report: the health command samples for DURATION seconds while CLIENTS clients load the app.
# Saves <file>.json and <file>.txt (the terminal summary) and shows the summary.
report() {   # report <file without extension>
  python3 "$ROOT/bin/health.py" --interval "$DURATION" --clients "$CLIENTS" \
    --timeout $((DURATION + 20)) --out "$1.json" >"$1.txt" || true
  cat "$1.txt"
}
