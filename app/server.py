#!/usr/bin/env python3
"""Lab app. Runs inside the container, standard library only.

GET /healthz      -> {"status": "ok"}
GET /work?n=N     -> runs a CPU-bound loop N times, returns the CPU time it took

The loop runs in the request thread. Python's GIL means the app uses at most about
one CPU, which keeps the load model simple: one client never reaches the container's
2-CPU quota, but it does hit a 0.5-CPU quota.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8080"))
DEFAULT_N = int(os.environ.get("WORK", "60000"))


def burn(n):
    """Pure CPU work: a pseudo-random number generator, no memory or I/O."""
    x = 1
    for _ in range(n):
        x = (x * 48271) % 2147483647
    return x


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"     # keep-alive, so each request is not a new TCP connection
    disable_nagle_algorithm = True    # headers and body are two writes; without TCP_NODELAY the
                                      # second waits ~40 ms for the client's delayed ACK

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/healthz":
            self.reply(200, {"status": "ok"})
        elif url.path == "/work":
            try:
                n = int(parse_qs(url.query).get("n", [DEFAULT_N])[0])
            except ValueError:
                return self.reply(400, {"error": "n must be an integer"})
            if not 1 <= n <= 10_000_000:
                return self.reply(400, {"error": "n must be between 1 and 10000000"})
            start = time.thread_time()
            burn(n)
            cpu_ms = (time.thread_time() - start) * 1000
            self.reply(200, {"n": n, "cpu_ms": round(cpu_ms, 3)})
        else:
            self.reply(404, {"error": "not found"})

    def reply(self, code, body):
        data = (json.dumps(body) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):     # no per-request logging: it would cost CPU under load
        pass


if __name__ == "__main__":
    print(f"lab app listening on :{PORT}, default n={DEFAULT_N}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
