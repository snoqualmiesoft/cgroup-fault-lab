#!/usr/bin/env python3
"""Load generator: CLIENTS clients send GET requests back to back for DURATION seconds
and we time every request. Prints requests per second and latency percentiles.

  python3 bin/loadgen.py http://10.0.0.5:8080/work --clients 1 --duration 20 [--json]
"""

import argparse
import http.client
import json
import math
import threading
import time
from urllib.parse import urlparse


def percentile(sorted_values, p):
    """The value below which p% of the values fall (nearest-rank method)."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(p / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def client(host, port, path, stop_at, results, timeout):
    """One client: send requests on one connection until stop_at; record (ok, ms, server_cpu_ms)."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    while time.monotonic() < stop_at:
        start = time.monotonic()
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            ms = (time.monotonic() - start) * 1000
            if response.status == 200:
                results.append((True, ms, json.loads(body)["cpu_ms"]))
            else:
                results.append((False, ms, None))
        except Exception:
            results.append((False, (time.monotonic() - start) * 1000, None))
            conn.close()
            time.sleep(0.1)                     # do not spin if the app is down
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.close()


def run(url, clients=1, duration=20.0, timeout=10.0):
    """Load the URL and return the counts and latency percentiles. Latency is the time from
    sending a request to reading the whole response, measured on the client."""
    u = urlparse(url)
    path = u.path + ("?" + u.query if u.query else "")
    results = []
    stop_at = time.monotonic() + duration
    threads = [threading.Thread(target=client, args=(u.hostname, u.port or 80, path, stop_at, results, timeout),
                                daemon=True)
               for _ in range(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    times = sorted(ms for ok, ms, cpu in results if ok)
    cpu = sorted(cpu for ok, ms, cpu in results if ok)

    def two_decimals(x):
        return None if x is None else round(x, 2)

    return {
        "url": url,
        "clients": clients,
        "duration_s": duration,
        "requests": len(results),               # the sample count
        "errors": len(results) - len(times),
        "rps": round(len(times) / duration, 1),
        "p50_ms": two_decimals(percentile(times, 50)),
        "p90_ms": two_decimals(percentile(times, 90)),
        "p99_ms": two_decimals(percentile(times, 99)),
        "max_ms": two_decimals(times[-1] if times else None),
        "server_cpu_p50_ms": two_decimals(percentile(cpu, 50)),    # CPU time the app spent per request
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--clients", type=int, default=1)
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()

    r = run(args.url, args.clients, args.duration)
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"{r['requests']} requests, {r['errors']} errors, {r['rps']} req/s")
        print(f"latency  p50 {r['p50_ms']:.1f} ms   p90 {r['p90_ms']:.1f} ms   p99 {r['p99_ms']:.1f} ms   "
              f"max {r['max_ms']:.1f} ms")
        print(f"server CPU per request (p50) {r['server_cpu_p50_ms']:.1f} ms")


if __name__ == "__main__":
    main()
