# cpu: PASS

Load settings, the same in all three phases: 1 client(s), 20 s, `GET http://10.123.0.10:8080/work`. Each phase is one health report (1-baseline.json/.txt, 2-fault.json/.txt, 3-recovery.json/.txt).

Injected:

```
incus config set lab limits.cpu.allowance 50ms/100ms    (cpu.max now: 50000 100000)
```

## Application (measured by the load generator)

| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2760 | 0 | 138.0 | 7.23 | 7.37 | 9.55 | 6.92 |
| fault | 1412 | 0 | 70.6 | 7.28 | 56.67 | 56.94 | 6.92 |
| recovery | 2761 | 0 | 138.1 | 7.23 | 7.38 | 9.31 | 6.92 |

## Kernel counters over the same interval

| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops | qdisc | qdisc drops | TCP retransmits host / container |
|---|---|---:|---:|---:|---|---:|---:|
| baseline | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| fault | `50000 100000` | 200 of 201 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| recovery | `200000 100000` | 0 of 201 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |

Changed under the fault: cpu.max 200000 100000 -> 50000 100000; throttled periods 0 -> 200.  
Did not change: server CPU per request 6.92; host link errors/drops 0 / 0; container link errors/drops 0 / 0; qdisc qdisc noqueue 0: root refcnt 2; qdisc drops 0; TCP retransmits host/container 0 / 0.

## Verdict

- yes  the fault reached the kernel (cpu.max or qdisc differs from the baseline)
- yes  the effect on the app is measurable: p99 7.37 -> 56.67 ms
- yes  the health check saw it: HEALTHY -> UNHEALTHY
- yes  the original cpu.max and qdisc are back
- yes  health is back to the baseline result: HEALTHY

## Health reports

- baseline: **HEALTHY**
- fault: **UNHEALTHY**
  - container.cpu FAIL: cpu.max 50000 100000 (Incus allowance 50ms/100ms), cpuset 0-3 (Incus limits.cpu not set); 200 of 201 periods throttled (100%), 9699 ms waiting
  - drift.cpu_limit WARN: cpu_limit changed: kernel 200000 100000 (Incus 200ms/100ms) -> kernel 50000 100000 (Incus 50ms/100ms)
- recovery: **HEALTHY**

## Interpretation

_Your explanation of the numbers above._
