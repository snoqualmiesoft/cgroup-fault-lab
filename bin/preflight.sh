#!/usr/bin/env bash
# Read-only check that this machine can run the lab. Changes nothing and needs no sudo.
# Works on its own, before cloning the repo:
#   curl -fsSL https://raw.githubusercontent.com/snoqualmiesoft/cgroup-fault-lab/main/bin/preflight.sh | bash

ok=true
need() { if eval "$2" >/dev/null 2>&1; then echo "  PASS  $1"; else echo "  FAIL  $1"; ok=false; fi; }
want() { if eval "$2" >/dev/null 2>&1; then echo "  PASS  $1"; else echo "  WARN  $1"; fi; }

echo "Preflight: kernel $(uname -r), $(nproc --all) CPUs"
want "Ubuntu 24.04 (the tested version)"      'grep -q VERSION_ID=\"24.04\" /etc/os-release'
need "not inside a container"                 '! systemd-detect-virt --container'
need "cgroup v2"                              '[ "$(stat -fc %T /sys/fs/cgroup)" = cgroup2fs ]'
need "cpu, cpuset and memory cgroup controllers" 'grep -qw cpu /sys/fs/cgroup/cgroup.controllers && grep -qw cpuset /sys/fs/cgroup/cgroup.controllers && grep -qw memory /sys/fs/cgroup/cgroup.controllers'
want "netem module (setup installs it if missing)" 'modinfo sch_netem'
need "at least 2 CPUs"                        '[ "$(nproc --all)" -ge 2 ]'
need "at least 2 GiB RAM"                     '[ "$(awk "/MemTotal/ {print \$2}" /proc/meminfo)" -ge 2000000 ]'
need "at least 3 GiB free disk under /var/lib" '[ "$(df -k --output=avail /var/lib | tail -1)" -ge 3000000 ]'
want "image server reachable"                 'curl -fsI -m 8 https://images.linuxcontainers.org'

if $ok; then echo "Preflight: OK"; else echo "Preflight: fix the FAIL lines first"; exit 1; fi
