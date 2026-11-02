# cpu: PASS

Load settings, the same in all three phases: 1 client(s), 20 s, `GET http://10.123.0.10:8080/work`. Each phase is one health report (1-baseline.json/.txt, 2-fault.json/.txt, 3-recovery.json/.txt).

Injected:

```
incus config set lab limits.cpu.allowance 50ms/100ms    (cpu.max now: 50000 100000)
```

## Application (measured by the load generator)

| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 4244 | 0 | 212.2 | 4.63 | 5.35 | 5.58 | 3.88 |
| fault | 2464 | 0 | 123.2 | 4.83 | 47.03 | 51.24 | 3.87 |
| recovery | 4217 | 0 | 210.8 | 4.65 | 5.34 | 5.64 | 3.89 |

## Kernel counters over the same interval

| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops | qdisc | qdisc drops | TCP retransmits host / container |
|---|---|---:|---:|---:|---|---:|---:|
| baseline | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| fault | `50000 100000` | 200 of 201 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| recovery | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |

Changed under the fault: cpu.max 200000 100000 -> 50000 100000; throttled periods 0 -> 200.  
Did not change: server CPU per request 3.88; host link errors/drops 0 / 0; container link errors/drops 0 / 0; qdisc qdisc noqueue 0: root refcnt 2; qdisc drops 0; TCP retransmits host/container 0 / 0.

## Verdict

- yes  the fault reached the kernel (cpu.max or qdisc differs from the baseline)
- yes  the effect on the app is measurable: p99 5.35 -> 47.03 ms
- yes  the health check saw it: HEALTHY -> UNHEALTHY
- yes  the original cpu.max and qdisc are back
- yes  health is back to the baseline result: HEALTHY

## Health reports

- baseline: **HEALTHY**
- fault: **UNHEALTHY**
  - container.cpu FAIL: cpu.max 50000 100000 (Incus allowance 50ms/100ms), cpuset 5-9 (Incus limits.cpu 5-9); 200 of 201 periods throttled (100%), 8168 ms waiting
  - drift.cpu_limit WARN: cpu_limit changed: kernel 200000 100000 (Incus 200ms/100ms) -> kernel 50000 100000 (Incus 50ms/100ms)
- recovery: **HEALTHY**

## Interpretation

The app serves one request at a time (one client, and Python runs one thread at a time), so it needs about one CPU. Under the fault the container may use only 50 ms of CPU in every 100 ms period. Once it has used its 50 ms, the kernel stops scheduling the container's threads, including the one running `burn`, until the next period starts and the quota refills, about 50 ms later. A request caught at that moment waits up to ~50 ms: that is the p99 of 47 ms. Most requests (3.88 ms of CPU each) fit inside the running half, so p50 stays at 4.8 ms. Throughput drops from 212.2 to 123.2 requests/s because only half of each period can be used: 200 of 201 periods were throttled, with 8.2 s of waiting in 20 s. The CPU time per request did not change (3.88 ms), so the extra latency is waiting, not extra work.
