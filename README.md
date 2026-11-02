# cgroup fault lab

A small, reproducible operations lab on one Ubuntu host. An LXC container (managed by Incus)
runs a tiny HTTP app under cgroup v2 CPU and memory limits. One command reports health,
diagnostics and configuration drift. Two scripted fault cycles (less CPU, a degraded network
link) show the evidence of each fault and confirm recovery. Bash and Python (standard library
only).

```
 host: Ubuntu 24.04, cgroup v2
 │
 │  bin/loadgen.py ── HTTP ──┐     bin/health.py reads /proc, /sys/fs/cgroup, /sys/class/net,
 │                           │     ip, tc, nstat, journalctl and Incus; changes nothing
 │  labbr0 10.123.0.1/24 (dedicated lab bridge, NAT, managed by Incus)
 │    └─ vethXXXX  ◄── network fault: tc netem (40 ms delay) on this host-side link only
 │         │
 └─ container "lab"   eth0 10.123.0.10/24, MTU 1500, default route via 10.123.0.1
      cgroup /sys/fs/cgroup/lxc.payload.lab/
        cpu.max    200000 100000 (2 CPUs)  ◄── CPU fault: 50000 100000 (0.5 CPU)
        memory.max 1 GiB
      app/server.py on :8080   GET /healthz, GET /work (a few ms of pure CPU)
```

## Prerequisites and privileges

- **Host:** Ubuntu 24.04 LTS on a machine or VM you control, not inside a container (tested on
  GitHub-hosted VMs, x86 and Arm, and on a bare-metal Arm64 machine). Ubuntu 22.04 does not
  package Incus, so it is not covered. At least 2 CPUs, 2 GiB RAM, 3 GiB free disk.
- **cgroup v2** with the cpu, cpuset and memory controllers (the Ubuntu 24.04 default).
  `make preflight` checks all of this read-only.
- **Network:** only during `make setup` (Ubuntu packages and the public `images:ubuntu/24.04`
  image). After setup the lab runs fully offline: the image is kept as a local copy and the app
  needs nothing from outside.
- **Privileges:** root (via `sudo`) for setup, the faults, the recovery command and the health
  command: Incus is root-only by default, and `nsenter` into the container's network namespace and
  the kernel log need root. `make preflight` and `make test` need no root.

**What setup changes on the host:** installs the packages `incus jq curl iproute2 util-linux`
(and `linux-modules-extra-$(uname -r)` only if the kernel has no netem module); creates an Incus
storage pool `default` (a directory under `/var/lib/incus`); creates the dedicated bridge
`labbr0` (10.123.0.1/24, NAT, no IPv6), for which Incus adds and manages its own nftables
rules and dnsmasq; keeps a local copy of the image. Setup logs `net.ipv4.ip_forward` before and
after. It does not change sysctls, boot settings, modules, drivers or firewall rules by hand, and
it does not use Incus's default bridge. `tc` loads the netem module by itself on first use.

## Run it

```bash
git clone https://github.com/snoqualmiesoft/cgroup-fault-lab.git && cd cgroup-fault-lab
make preflight     # read-only check of the machine
make all           # everything below in order, about 6 minutes; results in reports/run-<time>/
```

Or step by step (the demonstration):

```bash
make setup         # install Incus, create labbr0 and a fresh container with its limits, start the app
make inspect       # container, limits (Incus and kernel), interface, address, bridge, route, MTU
make baseline      # record the baseline profile: reports/baseline.json
make health        # health, diagnostics and drift report; exit code = status
make load          # 1 client for 20 s: sample count, errors, req/s, p50, p99
make fault-cpu     # CPU fault cycle    -> reports/<time>-cpu/summary.md
make fault-net     # network fault cycle -> reports/<time>-net/summary.md
make restore       # recovery command: safe at any time, also after an interrupted run
make interrupt     # demo: kill -9 a fault run mid-fault, then show restore fixes it
make smoke         # smoke test: setup, reachability, report creation, cleanup
make test          # unit tests (no container, no root)
make stop / make start / make teardown
```

All settings are in [`lab.env`](lab.env). [OPERATIONS.md](OPERATIONS.md) writes up the network
fault as an incident and holds the runbook.

## The lab (Part 1)

- **Container:** `lab`, from a local copy of `images:ubuntu/24.04`, created fresh by every
  `make setup`, so repeat runs start from the same state. Limits are set when it is created:
  `limits.cpu.allowance=200ms/100ms`, `limits.memory=1GiB`, and optionally `limits.cpu=<CPUs>`
  (`CPU_PIN`). `make inspect` shows the configured and the effective values.
- **Network:** the container's `eth0` (10.123.0.10/24, fixed, MTU 1500) is one end of a veth pair.
  The other end, `vethXXXX` on the host (the name changes each time the container starts), is a
  port of the dedicated bridge `labbr0` (10.123.0.1/24). The container's default route is via
  10.123.0.1; Incus NATs it to the outside. The host reaches the app directly at
  `http://10.123.0.10:8080`.
- **App:** `app/server.py`, a standard-library HTTP server run by systemd in the container.
  `GET /work` runs a fixed CPU loop (`WORK` iterations, a few ms) and returns the CPU time it used.
  Because of Python's GIL the app uses at most about one CPU: one client never reaches the 2-CPU
  quota but always reaches the 0.5-CPU fault quota, on any machine.
- **Load generator:** `bin/loadgen.py`. `CLIENTS` clients (fixed concurrency) each send requests
  back to back on one keep-alive connection for `DURATION` seconds. It reports the request count
  (the sample count), errors (non-200 or failed), throughput (successful requests / duration) and
  latency. **Latency** is measured on the client, from sending the request to reading the whole
  response. **p50 and p99** use the nearest-rank method over successful requests: sort the
  latencies and take the value at rank ceil(p/100 × n). With n = 400, p99 is the 4th slowest
  request, so a few slow requests can sit beyond it; the maximum is reported too.

## Health, diagnostics and drift (Parts 2 and 3)

`bin/health.py` reads the counters, loads the app for the sampling interval
(`--interval`, default 5 s, with `--clients` clients), reads the counters again, and runs 20
checks. It writes a JSON report (`--out`, `--json`) and prints a terminal summary whose first line
is the overall status and a one-line reason.

| check | what it reads | PASS / WARN / FAIL |
|---|---|---|
| `host.os` | `/etc/os-release`, kernel release | always PASS (information) |
| `host.cgroup` | `/sys/fs/cgroup/cgroup.controllers` | FAIL if not cgroup v2 with cpu, cpuset, memory |
| `host.cpu` | CPU count, load average | WARN if 1-min load per CPU > 1.0 |
| `host.memory` | `MemAvailable` in `/proc/meminfo` | WARN < 10 % available, FAIL < 5 % |
| `host.disk` | root filesystem use (as `df`) | WARN ≥ 85 %, FAIL ≥ 95 % |
| `host.kernel_log` | `journalctl -k -p warning` over the last 10 min | WARN if a line looks like OOM, hung task, lockup, call trace, NETDEV watchdog or I/O error |
| `container.state` | Incus state: status, PID, address, host-side link | FAIL if missing, stopped or without address |
| `container.cpu` | Incus allowance and limits.cpu; `cpu.max`, `cpuset.cpus.effective`; `cpu.stat` at start and end | WARN if ≥ 20 % of periods throttled, FAIL ≥ 60 % |
| `container.memory` | Incus limits.memory; `memory.max`, `memory.current`, `memory.events` oom_kill delta | WARN ≥ 80 % or any OOM kill, FAIL ≥ 95 % |
| `network.host_link` | `ip -s -j link` on the host-side veth: state, MTU, bridge, RX/TX packets, errors, drops (deltas) | FAIL if not UP, WARN on any error or drop |
| `network.container_link` | the same for `eth0` inside the container's network namespace (`nsenter -n`) | same |
| `network.qdisc` | `tc -s qdisc` on the host-side veth: settings, packets and drops (deltas) | WARN if netem is present |
| `network.tcp_retrans` | `nstat TcpRetransSegs` deltas in the host and in the container network namespace (namespace IDs reported) | always PASS: evidence, not a health signal (see below) |
| `network.http` | `GET /healthz` from the host | FAIL if unreachable or not 200 |
| `app.load` | the load generator over the interval | WARN on any error or p99 > 100 ms, FAIL if every request failed |
| `drift.kernel` | kernel release vs baseline | WARN if changed |
| `drift.cgroup` | cgroup version vs baseline | WARN if changed |
| `drift.cpu_limit` | Incus allowance + `cpu.max` vs baseline | WARN if changed; says whether Incus or only the kernel changed |
| `drift.cpuset` | Incus limits.cpu + `cpuset.cpus.effective` vs baseline | same |
| `drift.memory_limit` | Incus limits.memory + `memory.max` vs baseline | same |

Thresholds are in `lab.env`. Speed is not reported for the veth link because a virtual link has
no meaningful speed. TCP retransmits are counted per network namespace, not per interface: the
host's counter includes all of the host's traffic, so on a busy host it moves without any fault.

**Aggregation:** any FAIL → UNHEALTHY; otherwise any UNKNOWN → INCOMPLETE; otherwise any WARN →
DEGRADED; otherwise HEALTHY. **UNKNOWN** means evidence is missing (a command that isn't there,
permission denied, a timeout, the container not running for a container check), with the reason
in the message. It never crashes the tool and never counts as PASS.

| overall | exit code |
|---|---:|
| HEALTHY | 0 |
| DEGRADED | 1 |
| UNHEALTHY | 2 |
| INCOMPLETE | 3 |
| bad command-line arguments | 64 |

**Runtime limit:** `--timeout` (default 20 s, `HEALTH_TIMEOUT`). The sampling interval is cut to
leave 5 s for the checks, every command waits at most 5 s or the time left, checks that would start
after the limit are UNKNOWN, and a backstop exits with INCOMPLETE if anything still hangs 2 s past
it. The exit code only reports health: nothing is drained, stopped or changed.

**cgroup files read** (in `/sys/fs/cgroup/lxc.payload.lab/`):

| file | meaning |
|---|---|
| `cpu.max` | `quota period` in µs: the group may run `quota` µs of CPU time per `period` (`200000 100000` = 2 CPUs; `max` = no limit) |
| `cpu.stat` | `nr_periods`: periods in which the group wanted CPU; `nr_throttled`: periods in which it used up its quota and had to wait; `throttled_usec`: total waiting time |
| `cpuset.cpus.effective` | the host CPUs the group may actually run on |
| `memory.max` | memory limit in bytes (`max` = none); above it the kernel reclaims, then OOM-kills |
| `memory.current` | memory used now, page cache included |
| `memory.events` | counters of limit events; `oom_kill` = processes killed by the OOM killer |

**Baseline and drift.** `make baseline` (`health.py --save-baseline`) reads the current values and
writes them to `reports/baseline.json`; it changes nothing else. Every health report compares the
current values with it, using the same readers. Why each field matters in production:

| field | why it matters |
|---|---|
| kernel version | cgroup, scheduler and network behaviour, and driver compatibility, change between kernels; nodes in a pool should run the version that was validated |
| cgroup version | v1 and v2 have different files and semantics; tools and container managers that expect v2 misreport or fail on v1 |
| CPU limit | the quota decides how much CPU a job gets and when it is throttled; a lower quota shows up as latency, not as errors |
| cpuset | which CPUs (and so which cores, caches and NUMA node) a job runs on; a changed set changes performance and isolation |
| memory limit | the OOM point; too low kills the job, too high lets it squeeze its neighbours |

Each limit has two layers, so drift says which one moved: "changed" (Incus and the kernel both
show the new value: someone changed the configuration) or "changed outside Incus" (only the
kernel file changed, so the configuration no longer describes what runs). Drift is a WARN, so it
makes the node DEGRADED. The CPU fault demonstrates it: `drift.cpu_limit` goes to WARN while the
quota is lowered and back to PASS after recovery.

## Fault cycles (Part 4)

Each cycle: baseline report → inject → report under fault → recover → recovery report. A report is
the health command sampling for `DURATION` (20 s) under the same load (`CLIENTS`=1), so the load
settings are identical in all three phases. Each phase leaves `N-phase.json` and `N-phase.txt`;
`summary.md` tables the application and kernel numbers, lists which counters changed and which did
not, and gives a verdict (the fault reached the kernel, the effect is measurable, health saw it,
the original settings are back, health is back to its baseline result). `make all` runs the
network cycle only after the CPU cycle has verified its recovery.

- **CPU:** `incus config set lab limits.cpu.allowance 50ms/100ms` → `cpu.max 50000 100000`.
  Recovery sets it back to `200ms/100ms`.
- **Network:** `tc qdisc replace dev <veth> root netem delay 40ms` on the container's own
  host-side link (never on the bridge). It shapes packets the host sends to the container.
  Recovery deletes it, which brings back the original `noqueue`; the script compares the qdisc with
  the one it recorded before injecting.
- **Interrupted runs:** each script undoes its fault on exit, error or Ctrl-C (`trap`). After
  `kill -9`, which skips that, `make restore` is the recovery command. `make interrupt` demonstrates
  it: it kills each fault script right after injection, shows the fault is still there, runs
  `make restore` and checks that the original `cpu.max` and qdisc are back.

## Results

One run of `make all` on a bare-metal Arm64 machine (Ubuntu 24.04, 20 cores; the container pinned
to five of the fast cores). One client, 20 s per phase. [`sample-reports/`](sample-reports/) has
every report (JSON and terminal), the injection settings and a `summary.md` per fault cycle with
an interpretation. The same `make all` also passes on GitHub-hosted VMs (x86 and Arm) after every
push.

**CPU fault** (`cpu.max` 2 CPUs → 0.5 CPU):

| phase | requests | req/s | p50 ms | p99 ms | server CPU per request ms | throttled periods | health |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 4244 | 212.2 | 4.63 | 5.35 | 3.88 | 0 of 200 | HEALTHY |
| fault | 2464 | 123.2 | 4.83 | 47.03 | 3.87 | 200 of 201 | UNHEALTHY |
| recovery | 4217 | 210.8 | 4.65 | 5.34 | 3.89 | 0 of 200 | HEALTHY |

**Network fault** (netem 40 ms delay on the container's host-side link):

| phase | requests | req/s | p50 ms | p99 ms | max ms | packets through netem | health |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 4279 | 213.9 | 4.62 | 5.35 | 5.74 | | HEALTHY |
| fault | 441 | 22.1 | 45.29 | 46.37 | 86.30 | 949 delayed, 0 dropped | DEGRADED |
| recovery | 4188 | 209.4 | 4.72 | 5.40 | 5.65 | | HEALTHY |

What the numbers show:

- **CPU fault: waiting, not working.** The CPU time per request stayed at 3.88 ms, but p99 went from
  5.35 to 47.03 ms and throughput fell from 212.2 to 123.2 requests/s. CPU used = requests/s × CPU
  per request: 212.2 × 3.88 ms = 0.82 CPU before, 123.2 × 3.87 ms = 0.48 CPU under the 0.5-CPU
  quota. In each 100 ms period the container ran for 50 ms and then waited: 200 of 201 periods were
  throttled, 8.2 s of waiting in 20 s, and a request caught at the end of the quota waited up to
  50 ms. Interfaces and the qdisc did not change. Health: `container.cpu` FAIL (100 % throttled) and
  `drift.cpu_limit` WARN, so UNHEALTHY; HEALTHY again after recovery.
- **Network fault: a delay on the path.** When healthy the veth link adds almost nothing (a request is
  about 4.6 ms, almost all of it the 3.87 ms of `burn()`). netem holds every packet going to the
  container for 40 ms, so p50 and p99 both moved by about 41 ms: the same shift for every request.
  One client waits for each answer before sending the next, so throughput is 1 ÷ latency:
  213.9 → 22.1 requests/s (1000 ÷ 45.3 ≈ 22). The slowest request (86.3 ms) paid the delay twice:
  most likely the first one, which also had to open the TCP connection. CPU per request, throttling,
  interface errors and drops, and TCP retransmits did not change. Health: `network.qdisc` WARN, so
  DEGRADED; HEALTHY again after recovery.
- **Recovery after interruption.** `make interrupt` killed each fault script with `kill -9` right
  after injection, found the fault still in place, ran `make restore`, and found the original
  `cpu.max` and qdisc back.

## Tests (Part 5)

- `make test`: 21 unit tests: health aggregation, exit codes, UNKNOWN for a command timeout, a
  missing command, permission denied, a failing command and an unexpected error (the last three
  use fixtures), the runtime limit, bad arguments (exit 64), drift rules, the summary verdict
  (fixtures), and the real app with the real load generator on localhost.
- `make smoke`: builds a fresh lab, checks reachability, the app, the load generator, report
  creation (JSON and text) and the runtime limit, then deletes the container.
- `make interrupt`: recovery after interrupted fault runs, as above.

## Key decisions

- **Incus system container** for real LXC with a simple CLI; limits through Incus, so the
  configuration (Incus) and the enforcement (cgroup files) can both be read and compared.
- **One closed-loop client:** easy to reason about and the same on every machine (with the GIL).
  The cost: it slows down with the app, so it understates the tail (see limitations).
- **The health command runs the load** during its sampling interval, so a fault phase is exactly
  one report: application numbers and kernel counters cover the same seconds.
- **Retransmits as evidence, not a check:** they are namespace-wide and noisy on a shared host.
- **Fault values:** 0.5 CPU is below the roughly one CPU the app needs, so it is throttled on any
  machine, while the 2-CPU baseline never is. A fixed 40 ms delay is about ten times the normal
  request time, so it is plain to see, and it is bounded and the same for every packet. netem's
  packet loss is optional in the assignment and was left out: it adds TCP retransmission stalls on a
  few requests, which make the fault harder to read without adding a different kind of evidence.
- **Fresh container per setup, fixed address, local image:** predictable repeat runs and offline
  rebuilds.
- **Bash for the steps, Python for the logic:** the setup and fault steps are a few commands each;
  measuring, parsing and checking are clearer in Python. Standard library only.

## Limitations

- **Shared-host noise.** On a cloud VM, neighbours share the physical CPU; on a busy host, other
  processes compete with the app and the load generator, and the host's retransmit counter counts
  everyone's traffic. Compare phases of the same run, not runs on different machines.
- The closed-loop client sends fewer requests when the app is slow, so it understates the tail
  under a fault (coordinated omission). Good for before/after comparison, not for capacity planning.
- A 20 s phase gives about 440 samples under the network fault, so p99 is the 5th slowest request;
  rare slow requests can sit beyond it (they always show in `max`).
- netem shapes one direction (host → container), and only delay is injected, no packet loss.
- One container and one app; no memory fault (the optional extensions were not done).
- Ubuntu 24.04 only (22.04 has no Incus package).

## AI use

The code and documentation were written with AI assistant working under my direction:
it drafted the scripts, tests and documents, ran the lab on GitHub-hosted VMs through GitHub
Actions, and fixed what failed there. I set the requirements and the design choices, reviewed the
code, ran the lab on my own machine, and wrote the interpretation of the results. Every number in
this repository comes from these scripts on real machines; none was written by hand or by the AI.

## Files

```
lab.env          all settings
Makefile         the commands
app/             server.py and its systemd unit (run inside the container)
bin/             setup, start, stop, inspect, teardown, load, health (diagnostics + drift),
                 fault-cpu, fault-net, restore, summarize, run-all, preflight; common.sh helpers
tests/           test_lab.py (unit), smoke.sh, interrupt.sh
sample-reports/  results of real runs
.github/         runs `make all` on GitHub's VMs (x86 and Arm) after every push
```
