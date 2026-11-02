# Operations

## Incident: lab app responses slowed down (network fault)

This is the network fault from `make fault-net` on a bare-metal Arm64 machine (Ubuntu 24.04,
20 cores), written up as if it were a real incident:
[`sample-reports/bare-metal-arm64-20261102-001825/net`](sample-reports/bare-metal-arm64-20261102-001825/net/summary.md).
Every number below is from that run. The cause was injected on purpose, so this write-up keeps
apart what the measurements show and what is only known because we injected it.

### Symptoms

- The app answered every request (0 errors) but about ten times slower. With one client,
  throughput fell from 213.9 to 22.1 requests/s, p50 latency rose from 4.62 to 45.29 ms and p99 from
  5.35 to 46.37 ms. The slowest request took 86.3 ms (5.7 ms in the baseline).
- The health command went from HEALTHY to **DEGRADED (exit 1)**. One-line reason:
  `network.qdisc WARN: vethb503361d: qdisc netem 8005: root refcnt 21 limit 1000 delay 40ms; in the interval 949 packets sent, 0 dropped by the qdisc`.
  The application check alone stayed PASS: p99 (46.4 ms) was under its 100 ms threshold. The qdisc
  check is what flagged the problem.

### Evidence

All from the same 20 s interval in which the latency was measured (`2-fault.json`), compared
with the baseline interval (`1-baseline.json`).

| layer | signal | baseline | under the fault | what it shows |
|---|---|---|---|---|
| application | p50 / p99 / max latency | 4.62 / 5.35 / 5.74 ms | 45.29 / 46.37 / 86.3 ms | every request about 41 ms slower, all by the same amount |
| application | server CPU per request (median) | 3.87 ms | 3.87 ms | the app does the same work: the extra time is spent outside the app |
| application | `/healthz` from the host, on a new connection | 1.5 ms | 82.5 ms | a new connection pays the delay twice: its opening packet and the request |
| cgroup | throttled CPU periods, `cpu.max` | 0 of 200, `200000 100000` | 0 of 200, `200000 100000` | the CPU limit is not involved |
| host | load, memory, disk, kernel log | normal | normal | the host is not short of anything and logs no problems |
| drift | kernel, cgroup, CPU limit, cpuset, memory limit | as baseline | as baseline | nothing in the configuration changed |
| link | state, MTU, errors / drops on both ends of the veth | UP, 1500, 0 / 0 | UP, 1500, 0 / 0 | the interfaces are fine; delay does not produce RX/TX errors |
| qdisc | root qdisc on the host-side veth | `noqueue` | `netem delay 40ms`: 949 packets sent, 0 dropped | a non-default qdisc is holding every packet the host sends to the container |
| TCP | retransmits, host namespace / container namespace | 0 / 0 | 0 / 0 | nothing is lost; packets arrive late, not missing |

Two observations tie it together. The p50 rise (about 41 ms) matches netem's configured 40 ms
delay. And p99 moved by the same amount as p50 (both about +41 ms): the whole distribution shifted.
A fixed delay on the path does that. CPU contention or packet loss would have slowed only some
requests and widened the gap between p50 and p99. In the network path, the qdisc is the only thing
that changed.

### Safe investigation order

Read-only first, cheapest and widest first, and change nothing until the evidence points to one
place:

1. **Confirm the symptom.** `make health` (the one-line reason) and `make load`: errors or latency?
   Every request or only the tail?
2. **App or not?** Compare the client latency with the server CPU per request. Flat server time and
   higher latency means the time goes outside the app.
3. **CPU and limits.** `container.cpu` (throttled periods, `cpu.max`) and the drift checks: is the
   container being throttled, has a limit changed?
4. **Host.** `host.cpu`, `host.memory`, `host.disk`, `host.kernel_log`: is the whole machine short of
   something or logging errors?
5. **Network, bottom up.** Link state, MTU, errors and drops on both ends (`ip -s link show dev <veth>`;
   `nsenter -t <pid> -n ip -s link show dev eth0`), then the qdisc (`tc -s qdisc show dev <veth>`),
   then TCP retransmits per namespace (`nstat -az TcpRetransSegs`, and the same through `nsenter`),
   then reachability (`curl http://10.123.0.10:8080/healthz`).
6. **Only then change something**, and only the lab's own link or container.

### Known cause versus conclusions from the measurements

- **Known only because we injected it:** at 00:20:23 (local time) the fault script ran
  `tc qdisc replace dev vethb503361d root netem delay 40ms`.
- **Supported by the measurements alone:** a netem qdisc with a 40 ms delay sits on the container's
  host-side link and passed 949 packets in the interval, dropping none; every request was slower by
  that delay, by the same amount; CPU, memory, the host, the interfaces, TCP and the configured
  limits are normal. The slowdown is in the network path, at the qdisc of that one link, in the
  host-to-container direction.
- **Not supported:** who attached the qdisc or why (the lab keeps no audit trail); anything about
  the container-to-host direction beyond "the responses were not slowed" (netem shapes only one
  direction, and there is no direct round-trip probe); that the slowest request (86.3 ms) was the
  first one of the phase. That is the likely reason, since a new connection pays the delay twice, as
  `/healthz` showed, but the reports keep no per-request order.

### Restoration criteria

- `tc qdisc show dev <veth>` shows the original qdisc again (`qdisc noqueue 0: root refcnt 2`).
- Health is back to HEALTHY: `network.qdisc` PASS.
- The app is back within noise of the baseline: the recovery interval gave 209.4 requests/s,
  p50 4.72 ms, p99 5.40 ms (baseline 213.9, 4.62, 5.35).

All three held in this run (`3-recovery.json`).

### Rollback

- `make restore`: removes any netem from the lab link (back to the default qdisc) and sets the CPU
  and memory limits back to `lab.env`. It is safe to run any number of times and works after an
  interrupted run; `make interrupt` demonstrates this by killing a fault script with `kill -9` right
  after injection.
- If the qdisc cannot be removed: `make stop && make start` gives the container a new veth with the
  default qdisc. Last resort: `make setup` rebuilds the container from the local image.

### Improvements before production use

- Alert on a non-default qdisc on workload interfaces, and record who changed it and when (audit
  `tc` and Incus changes), so "who and why" is answerable.
- Measure RTT between host and container directly (an ICMP or TCP probe) to separate network time
  from application time without inference.
- Attribute retransmits per connection (`ss -ti`) instead of per namespace, and measure both
  directions.
- Judge latency with enough samples and with open-loop load or real traffic, so a slow app does not
  hide its own tail (coordinated omission).
- Run the health command on a schedule and export its results as metrics; let a reviewed policy, not
  the exit code alone, decide whether a node is drained.
- Keep baselines versioned per node pool and compare nodes with each other, not only with their own
  past.

## Runbook

### Health exit codes

| exit | status | what to do |
|---:|---|---|
| 0 | HEALTHY | nothing |
| 1 | DEGRADED | read the WARN lines: drift or a fault left behind → `make restore`; host load or app tail → investigate as above |
| 2 | UNHEALTHY | read the FAIL lines: container or app down → `make start` or `make setup`; throttling → check `cpu.max` |
| 3 | INCOMPLETE | evidence missing (the reason is in the UNKNOWN line): run with sudo, run `make baseline`, or use a longer `--timeout`. Not the same as healthy |
| 64 | | fix the command-line arguments |

### Recovery by hand

```bash
sudo incus config set lab limits.cpu.allowance 200ms/100ms
cat /sys/fs/cgroup/lxc.payload.lab/cpu.max                      # 200000 100000
LINK=$(sudo incus query /1.0/instances/lab/state | jq -r .network.eth0.host_name)
sudo tc qdisc del dev "$LINK" root; tc qdisc show dev "$LINK"   # qdisc noqueue
```

### Removing everything

`make teardown` deletes the container and keeps the bridge and the local image (so `make setup`
works offline). To remove everything, Incus included:

```bash
sudo incus delete --force lab; sudo incus image delete lab-ubuntu-24.04; sudo incus network delete labbr0
sudo systemctl stop incus.service incus.socket incus-lxcfs.service
findmnt -rn -o TARGET | grep '^/var/lib/incus' | sort -r | xargs -r -n1 sudo umount -l
sudo apt purge -y 'incus*' && sudo rm -rf /var/lib/incus
```

### Troubleshooting

| problem | cause | fix |
|---|---|---|
| preflight: "not inside a container" FAIL | Docker, Codespaces, an LXC VPS | use a VM or a real machine |
| preflight: cgroup v2 FAIL | cgroup v1 or hybrid host | use a standard Ubuntu 24.04 |
| setup: "the container did not get 10.123.0.10" | the address range is used elsewhere on the host | change `BRIDGE_ADDRESS` and `CONTAINER_IP` in `lab.env`, `sudo incus network delete labbr0`, `make setup` |
| fault-net: "Specified qdisc kind is unknown" | no netem module | `sudo apt install linux-modules-extra-$(uname -r)` |
| health: drift checks UNKNOWN | no baseline file | `make baseline` |
| health: container checks UNKNOWN, "not found: incus" or "permission denied" | Incus missing, or run without sudo | `make setup`; use `make health` |
| the app does not answer | the app crashed | `sudo incus exec lab -- journalctl -u lab-app`, then `make setup` |
