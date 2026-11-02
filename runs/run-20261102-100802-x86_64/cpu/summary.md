# cpu: PASS

Load settings, the same in all three phases: 1 client(s), 20 s, `GET http://10.123.0.10:8080/work`. Each phase is one health report (1-baseline.json/.txt, 2-fault.json/.txt, 3-recovery.json/.txt).

Injected:

```
incus config set lab limits.cpu.allowance 50ms/100ms    (cpu.max now: 50000 100000)
```

## Application (measured by the load generator)

| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 3151 | 0 | 157.6 | 6.28 | 7.12 | 12.82 | 5.98 |
| fault | 1578 | 0 | 78.9 | 6.34 | 56.91 | 57.76 | 6.01 |
| recovery | 3171 | 0 | 158.6 | 6.27 | 6.74 | 14.22 | 5.97 |

## Kernel counters over the same interval

| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops | qdisc | qdisc drops | TCP retransmits host / container |
|---|---|---:|---:|---:|---|---:|---:|
| baseline | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| fault | `50000 100000` | 200 of 201 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| recovery | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |

Changed under the fault: cpu.max 200000 100000 -> 50000 100000; throttled periods 0 -> 200.  
Did not change: server CPU per request 5.98; host link errors/drops 0 / 0; container link errors/drops 0 / 0; qdisc qdisc noqueue 0: root refcnt 2; qdisc drops 0; TCP retransmits host/container 0 / 0.

## Verdict

- yes  the fault reached the kernel (cpu.max or qdisc differs from the baseline)
- yes  the effect on the app is measurable: p99 7.12 -> 56.91 ms
- yes  the health check saw it: HEALTHY -> UNHEALTHY
- yes  the original cpu.max and qdisc are back
- yes  health is back to the baseline result: HEALTHY

## Health reports

- baseline: **HEALTHY**
- fault: **UNHEALTHY**
  - container.cpu FAIL: cpu.max 50000 100000 (Incus allowance 50ms/100ms), cpuset 0-3 (Incus limits.cpu not set); 200 of 201 periods throttled (100%), 9659 ms waiting
  - drift.cpu_limit WARN: cpu_limit changed: kernel 200000 100000 (Incus 200ms/100ms) -> kernel 50000 100000 (Incus 50ms/100ms)
- recovery: **HEALTHY**

## Interpretation

_Your explanation of the numbers above._
