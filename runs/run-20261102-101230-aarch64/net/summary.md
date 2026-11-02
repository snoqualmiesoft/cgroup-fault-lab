# net: PASS

Load settings, the same in all three phases: 1 client(s), 20 s, `GET http://10.123.0.10:8080/work`. Each phase is one health report (1-baseline.json/.txt, 2-fault.json/.txt, 3-recovery.json/.txt).

Injected:

```
tc qdisc replace dev veth05b65be8 root netem delay 40ms
before: qdisc noqueue 0: root refcnt 2 
after:  qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms
```

## Application (measured by the load generator)

| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 2764 | 0 | 138.2 | 7.22 | 7.33 | 9.41 | 6.92 |
| fault | 419 | 0 | 20.9 | 47.63 | 48.19 | 89.62 | 6.93 |
| recovery | 2755 | 0 | 137.8 | 7.24 | 7.4 | 9.65 | 6.91 |

## Kernel counters over the same interval

| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops | qdisc | qdisc drops | TCP retransmits host / container |
|---|---|---:|---:|---:|---|---:|---:|
| baseline | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| fault | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms` | 0 | 0 / 0 |
| recovery | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 1 |

Changed under the fault: qdisc qdisc noqueue 0: root refcnt 2 -> qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms.  
Did not change: cpu.max 200000 100000; throttled periods 0; server CPU per request 6.92; host link errors/drops 0 / 0; container link errors/drops 0 / 0; qdisc drops 0; TCP retransmits host/container 0 / 0.

## Verdict

- yes  the fault reached the kernel (cpu.max or qdisc differs from the baseline)
- yes  the effect on the app is measurable: p99 7.33 -> 48.19 ms
- yes  the health check saw it: HEALTHY -> DEGRADED
- yes  the original cpu.max and qdisc are back
- yes  health is back to the baseline result: HEALTHY

## Health reports

- baseline: **HEALTHY**
- fault: **DEGRADED**
  - network.qdisc WARN: veth05b65be8: qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms; in the interval 840 packets sent, 0 dropped by the qdisc
- recovery: **HEALTHY**

## Interpretation

_Your explanation of the numbers above._
