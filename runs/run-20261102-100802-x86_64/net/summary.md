# net: PASS

Load settings, the same in all three phases: 1 client(s), 20 s, `GET http://10.123.0.10:8080/work`. Each phase is one health report (1-baseline.json/.txt, 2-fault.json/.txt, 3-recovery.json/.txt).

Injected:

```
tc qdisc replace dev veth99631fb4 root netem delay 40ms
before: qdisc noqueue 0: root refcnt 2 
after:  qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms
```

## Application (measured by the load generator)

| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 3145 | 0 | 157.2 | 6.33 | 6.75 | 12.73 | 6.04 |
| fault | 415 | 0 | 20.8 | 48.09 | 49.24 | 89.7 | 7.66 |
| recovery | 3131 | 0 | 156.6 | 6.32 | 7.05 | 14.02 | 6.02 |

## Kernel counters over the same interval

| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops | qdisc | qdisc drops | TCP retransmits host / container |
|---|---|---:|---:|---:|---|---:|---:|
| baseline | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| fault | `200000 100000` | 0 of 199 | 0 / 0 | 0 / 0 | `qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms` | 0 | 0 / 0 |
| recovery | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 1 |

Changed under the fault: server CPU per request 6.04 -> 7.66; qdisc qdisc noqueue 0: root refcnt 2 -> qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms.  
Did not change: cpu.max 200000 100000; throttled periods 0; host link errors/drops 0 / 0; container link errors/drops 0 / 0; qdisc drops 0; TCP retransmits host/container 0 / 0.

## Verdict

- yes  the fault reached the kernel (cpu.max or qdisc differs from the baseline)
- yes  the effect on the app is measurable: p99 6.75 -> 49.24 ms
- yes  the health check saw it: HEALTHY -> DEGRADED
- yes  the original cpu.max and qdisc are back
- yes  health is back to the baseline result: HEALTHY

## Health reports

- baseline: **HEALTHY**
- fault: **DEGRADED**
  - network.qdisc WARN: veth99631fb4: qdisc netem 8001: root refcnt 5 limit 1000 delay 40ms; in the interval 832 packets sent, 0 dropped by the qdisc
- recovery: **HEALTHY**

## Interpretation

_Your explanation of the numbers above._
