#!/usr/bin/env python3
"""Turn the three reports of one fault cycle into summary.md: what was injected, the measurements
of each phase, which counters changed and which did not, and a verdict.
Exit code 0 if the cycle passed, 1 if not.

  python3 bin/summarize.py reports/<time>-cpu
"""

import json
import os
import sys

PHASES = ["baseline", "fault", "recovery"]
FILES = {"baseline": "1-baseline", "fault": "2-fault", "recovery": "3-recovery"}


def data(report, check):
    """The data of one check in a report ({} if the check had no data, e.g. UNKNOWN)."""
    for c in report["checks"]:
        if c["check"] == check:
            return c["data"] or {}
    return {}


def numbers(report):
    """The figures the summary compares, from one report. Missing ones are '?'."""
    app, cpu = data(report, "app.load"), data(report, "container.cpu")
    host, ct = data(report, "network.host_link"), data(report, "network.container_link")
    qd, rt = data(report, "network.qdisc"), data(report, "network.tcp_retrans")

    def errors_drops(link):
        if not link:
            return "?"
        return f"{link['rx_errors'] + link['tx_errors']} / {link['rx_dropped'] + link['tx_dropped']}"

    return {
        "requests": app.get("requests", "?"), "errors": app.get("errors", "?"), "rps": app.get("rps", "?"),
        "p50": app.get("p50_ms", "?"), "p99": app.get("p99_ms", "?"), "max": app.get("max_ms", "?"),
        "server_cpu": app.get("server_cpu_p50_ms", "?"),
        "cpu_max": cpu.get("cpu_max", "?"),
        "throttled": f"{cpu['throttled']} of {cpu['periods']}" if cpu else "?",
        "throttled_count": cpu.get("throttled", "?"),
        "host_link": errors_drops(host), "container_link": errors_drops(ct),
        "qdisc": qd.get("config", "?"), "qdisc_drops": qd.get("drops", "?"),
        "retrans": f"{rt['host']} / {rt['container']}" if rt else "?",
    }


def verdict(reports):
    """List of (passed, sentence). The cycle passes when every item passes."""
    base, fault, rec = (numbers(reports[p]) for p in PHASES)
    status = {p: reports[p]["status"] for p in PHASES}
    injected = (fault["cpu_max"], fault["qdisc"]) != (base["cpu_max"], base["qdisc"])
    restored = (rec["cpu_max"], rec["qdisc"]) == (base["cpu_max"], base["qdisc"])
    measurable = isinstance(fault["p99"], float) and isinstance(base["p99"], float) and fault["p99"] >= 1.5 * base["p99"]
    return [
        (injected, "the fault reached the kernel (cpu.max or qdisc differs from the baseline)"),
        (measurable, f"the effect on the app is measurable: p99 {base['p99']} -> {fault['p99']} ms"),
        (status["fault"] != "HEALTHY", f"the health check saw it: {status['baseline']} -> {status['fault']}"),
        (restored, "the original cpu.max and qdisc are back"),
        (status["recovery"] == status["baseline"], f"health is back to the baseline result: {status['recovery']}"),
    ]


def differs(before, after):
    """Numbers differ when they move by more than 10 % (any move away from 0); text when not equal."""
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return abs(after - before) > 0.1 * abs(before) if before else after != 0
    return before != after


def changes(reports):
    """Which counters changed between baseline and fault, and which did not."""
    base, fault = numbers(reports["baseline"]), numbers(reports["fault"])
    labels = {"cpu_max": "cpu.max", "throttled_count": "throttled periods", "server_cpu": "server CPU per request",
              "host_link": "host link errors/drops", "container_link": "container link errors/drops",
              "qdisc": "qdisc", "qdisc_drops": "qdisc drops", "retrans": "TCP retransmits host/container"}
    changed = [f"{label} {base[key]} -> {fault[key]}" for key, label in labels.items() if differs(base[key], fault[key])]
    same = [f"{label} {base[key]}" for key, label in labels.items() if not differs(base[key], fault[key])]
    return changed, same


def main():
    folder = sys.argv[1]

    def load(name):
        with open(os.path.join(folder, name)) as f:
            return json.load(f)

    reports = {p: load(FILES[p] + ".json") for p in PHASES}
    checks = verdict(reports)
    passed = all(ok for ok, _ in checks)
    settings = reports["baseline"]["load_settings"]
    injection = open(os.path.join(folder, "injection.txt")).read().strip()

    print(f"# {os.path.basename(folder.rstrip('/'))}: {'PASS' if passed else 'FAIL'}\n")
    print(f"Load settings, the same in all three phases: {settings['clients']} client(s), "
          f"{settings['duration_s']:g} s, `GET {settings['url']}`. Each phase is one health report "
          f"({', '.join(FILES[p] + '.json/.txt' for p in PHASES)}).\n")
    print(f"Injected:\n\n```\n{injection}\n```\n")

    rows = {p: numbers(reports[p]) for p in PHASES}
    print("## Application (measured by the load generator)\n")
    print("| phase | requests | errors | req/s | p50 ms | p99 ms | max ms | server CPU per request ms |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for p in PHASES:
        r = rows[p]
        print(f"| {p} | {r['requests']} | {r['errors']} | {r['rps']} | {r['p50']} | {r['p99']} | {r['max']} | {r['server_cpu']} |")

    print("\n## Kernel counters over the same interval\n")
    print("| phase | cpu.max | throttled periods | host link errors / drops | container link errors / drops "
          "| qdisc | qdisc drops | TCP retransmits host / container |")
    print("|---|---|---:|---:|---:|---|---:|---:|")
    for p in PHASES:
        r = rows[p]
        print(f"| {p} | `{r['cpu_max']}` | {r['throttled']} | {r['host_link']} | {r['container_link']} "
              f"| `{r['qdisc']}` | {r['qdisc_drops']} | {r['retrans']} |")

    changed, same = changes(reports)
    print("\nChanged under the fault: " + ("; ".join(changed) or "nothing") + ".  ")
    print("Did not change: " + ("; ".join(same) or "nothing") + ".")

    print("\n## Verdict\n")
    for ok, sentence in checks:
        print(f"- {'yes' if ok else 'NO '}  {sentence}")

    print("\n## Health reports\n")
    for p in PHASES:
        print(f"- {p}: **{reports[p]['status']}**")
        for c in reports[p]["checks"]:
            if c["status"] != "PASS":
                print(f"  - {c['check']} {c['status']}: {c['message']}")

    print("\n## Interpretation\n\n_Your explanation of the numbers above._")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
