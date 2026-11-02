# net: PASS

Load settings, the same in all three phases: 1 client(s), 20 s, `GET http://10.123.0.10:8080/work`. Each phase is one health report (1-baseline.json/.txt, 2-fault.json/.txt, 3-recovery.json/.txt).

Injected:

```
tc qdisc replace dev vethb503361d root netem delay 40ms
before: qdisc noqueue 0: root refcnt 2 
after:  qdisc netem 8005: root refcnt 21 limit 1000 delay 40ms
```

## Application (measured by the load generator)

| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 4279 | 0 | 213.9 | 4.62 | 5.35 | 5.74 | 3.87 |
| fault | 441 | 0 | 22.1 | 45.29 | 46.37 | 86.3 | 3.87 |
| recovery | 4188 | 0 | 209.4 | 4.72 | 5.4 | 5.65 | 3.96 |

## Kernel counters over the same interval

| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops | qdisc | qdisc drops | TCP retransmits host / container |
|---|---|---:|---:|---:|---|---:|---:|
| baseline | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 0 / 0 |
| fault | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc netem 8005: root refcnt 21 limit 1000 delay 40ms` | 0 | 0 / 0 |
| recovery | `200000 100000` | 0 of 200 | 0 / 0 | 0 / 0 | `qdisc noqueue 0: root refcnt 2` | 0 | 1 / 1 |

Changed under the fault: qdisc qdisc noqueue 0: root refcnt 2 -> qdisc netem 8005: root refcnt 21 limit 1000 delay 40ms.  
Did not change: cpu.max 200000 100000; throttled periods 0; server CPU per request 3.87; host link errors/drops 0 / 0; container link errors/drops 0 / 0; qdisc drops 0; TCP retransmits host/container 0 / 0.

## Verdict

- yes  the fault reached the kernel (cpu.max or qdisc differs from the baseline)
- yes  the effect on the app is measurable: p99 5.35 -> 46.37 ms
- yes  the health check saw it: HEALTHY -> DEGRADED
- yes  the original cpu.max and qdisc are back
- yes  health is back to the baseline result: HEALTHY

## Health reports

- baseline: **HEALTHY**
- fault: **DEGRADED**
  - network.qdisc WARN: vethb503361d: qdisc netem 8005: root refcnt 21 limit 1000 delay 40ms; in the interval 949 packets sent, 0 dropped by the qdisc
- recovery: **HEALTHY**

## Interpretation

When healthy, the network adds almost nothing: the veth link is inside one machine, so a request takes about 4.6 ms, almost all of it the 3.87 ms of `burn()`. netem holds every packet the host sends to the container for 40 ms (the responses coming back are not delayed), so every request now waits 40 ms before `burn()` can start: p50 rose from 4.62 to 45.29 ms and p99 from 5.35 to 46.37 ms, the same shift for every request. With one client that sends the next request only after the previous answer, throughput is 1 ÷ latency: 213.9 → 22.1 requests/s (1000 ÷ 45.3 ≈ 22). The slowest request, 86.3 ms, paid the delay twice: most likely the first request of the phase, which also had to open the TCP connection, whose opening packet was delayed too. The health check's `/healthz`, sent on a new connection, took 82.5 ms the same way. The evidence points to the network path: the qdisc changed to netem (949 packets delayed, 0 dropped), while the CPU time per request (3.87 ms), throttling (0 periods), interface errors and drops (0) and TCP retransmits (0) did not change. After the qdisc was removed, the original `noqueue` came back and the numbers returned to the baseline (209.4 requests/s, p50 4.72 ms).
