#!/usr/bin/env python3
"""Diagnostics, health and drift report for the lab. Run it with sudo.

Read-only: it reads files and runs commands that only show things (the app gets some requests).
It reads the kernel counters at the start of a sampling interval, loads the app during the
interval, reads the counters again at the end, then checks the host, the container, the network,
the application and drift against the saved baseline.

Every check answers PASS, WARN, FAIL or UNKNOWN. UNKNOWN means the evidence is missing (a command
that isn't there, permission denied, a timeout): never a crash, never a quiet PASS. Overall:

  HEALTHY     every check PASS                 exit code 0
  DEGRADED    some WARN, nothing worse          exit code 1
  UNHEALTHY   any FAIL                          exit code 2
  INCOMPLETE  no FAIL, but some UNKNOWN         exit code 3
  (bad command-line arguments)                  exit code 64

Runtime limit: every command and request stops at --timeout seconds, and checks that would start
after it are UNKNOWN. The exit code only reports health; nothing is drained or changed.

  sudo bin/health.py                            sample for HEALTH_INTERVAL seconds, print a summary
  sudo bin/health.py --interval 20 --clients 1 --out report.json
  sudo bin/health.py --json                     JSON on stdout (summary on stderr)
  sudo bin/health.py --save-baseline FILE       record the baseline profile for the drift check
"""

import argparse
import http.client
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import loadgen  # noqa: E402

PASS, WARN, FAIL, UNKNOWN = "PASS", "WARN", "FAIL", "UNKNOWN"
EXIT_CODES = {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "INCOMPLETE": 3}


class Unknown(Exception):
    """Evidence is missing. The check that needs it becomes UNKNOWN with this reason."""


class OutOfTime(Exception):
    """The runtime limit was reached."""


# ---- small helpers (unit tested) ----

def load_env(path):
    """Read KEY=VALUE lines from lab.env (quotes and # comments allowed)."""
    env = {}
    with open(path) as f:
        for line in f:
            words = shlex.split(line, comments=True)
            if words and "=" in words[0]:
                key, value = words[0].split("=", 1)
                env[key] = value
    return env


def overall(statuses):
    """Aggregation rule: any FAIL -> UNHEALTHY, else any UNKNOWN -> INCOMPLETE, else any WARN -> DEGRADED."""
    if FAIL in statuses:
        return "UNHEALTHY"
    if UNKNOWN in statuses:
        return "INCOMPLETE"
    if WARN in statuses:
        return "DEGRADED"
    return "HEALTHY"


def level(value, warn, fail):
    """PASS, WARN or FAIL for a number where higher is worse."""
    if value >= fail:
        return FAIL
    if value >= warn:
        return WARN
    return PASS


def explain(error):
    """A short reason for an UNKNOWN, from whatever went wrong while collecting evidence."""
    if isinstance(error, Unknown):
        return str(error)
    if isinstance(error, OutOfTime):
        return "time limit reached"
    if isinstance(error, subprocess.TimeoutExpired):
        return f"timed out after {error.timeout:.1f} s: {' '.join(error.cmd)}"
    if isinstance(error, FileNotFoundError):
        return f"not found: {error.filename}"
    if isinstance(error, PermissionError):
        return f"permission denied: {error.filename}"
    if isinstance(error, subprocess.CalledProcessError):
        return f"'{' '.join(error.cmd)}' failed: {(error.stderr or '').strip()[:150]}"
    return f"{type(error).__name__}: {error}"


def show(value):
    if isinstance(value, dict):
        return f"kernel {value['kernel']} (Incus {value['incus'] or 'not set'})"
    return str(value)


def drift(field, baseline, now):
    """Compare one baseline field with its value now. For limits, which have an Incus value and a
    kernel value, say which layer changed."""
    if baseline == now:
        return PASS, f"{field}: {show(now)}, same as the baseline"
    if isinstance(now, dict) and now["incus"] == baseline["incus"]:
        return WARN, f"{field} changed outside Incus: kernel {baseline['kernel']} -> {now['kernel']}, Incus still {now['incus'] or 'not set'}"
    return WARN, f"{field} changed: {show(baseline)} -> {show(now)}"


def one_line(status, checks):
    """The one-line reason: the first check that decided the overall status."""
    deciding = {"UNHEALTHY": FAIL, "INCOMPLETE": UNKNOWN, "DEGRADED": WARN}.get(status)
    worst = [c for c in checks if c["status"] == deciding]
    if not worst:
        return f"all {len(checks)} checks passed"
    more = f" (+{len(worst) - 1} more)" if len(worst) > 1 else ""
    return f"{worst[0]['check']} {worst[0]['status']}: {worst[0]['message']}{more}"


# ---- reading the system ----

ENV = load_env(os.path.join(ROOT, "lab.env"))
NAME = ENV["NAME"]
CGROUP = f"/sys/fs/cgroup/lxc.payload.{NAME}"      # the container's cgroup folder
DEADLINE = 0.0     # time.monotonic() value when the runtime limit is reached
CT = {}            # container facts: status, pid, ip, veth (or "error")
START, END = {}, {}    # counters at the start and the end of the interval
LOAD = None        # load generator result for the interval
BASELINE = {}      # the saved baseline profile


def time_left():
    return DEADLINE - time.monotonic()


def run(*command):
    """Run a read-only command and return its output. Waits at most 5 s or the time left."""
    if time_left() <= 0:
        raise OutOfTime()
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=min(5, time_left()))
    return result.stdout.strip()


def read(path):
    with open(path) as f:
        return f.read().strip()


def cgroup(name):
    """One file from the container's cgroup folder, e.g. cpu.max."""
    return read(f"{CGROUP}/{name}")


def keyvals(text):
    """'key value' lines (cpu.stat, memory.events) as a dict of numbers."""
    return {key: int(value) for key, value in (line.split() for line in text.splitlines())}


def container_facts():
    state = json.loads(run("incus", "query", f"/1.0/instances/{NAME}/state"))
    eth0 = (state.get("network") or {}).get("eth0") or {}
    ips = [a["address"] for a in eth0.get("addresses", []) if a["family"] == "inet"]
    return {"status": state["status"], "pid": state["pid"], "ip": ips[0] if ips else None,
            "veth": eth0.get("host_name")}


def need_container():
    if "error" in CT:
        raise Unknown(f"container facts unavailable: {CT['error']}")
    if CT["status"] != "Running":
        raise Unknown(f"container {NAME} is {CT['status']}")
    return CT


def link(device, pid=None):
    """State, MTU and packet counters of one interface; with pid, inside that process's network namespace."""
    command = ["ip", "-json", "-stats", "link", "show", "dev", device]
    if pid:
        command = ["nsenter", "-t", str(pid), "-n"] + command
    info = json.loads(run(*command))[0]
    rx, tx = info["stats64"]["rx"], info["stats64"]["tx"]
    return {"state": info["operstate"], "mtu": info["mtu"], "master": info.get("master"),
            "rx_packets": rx["packets"], "tx_packets": tx["packets"],
            "rx_errors": rx["errors"], "tx_errors": tx["errors"],
            "rx_dropped": rx["dropped"], "tx_dropped": tx["dropped"]}


def qdisc(device):
    """The root qdisc of an interface: its settings as tc prints them, and its counters."""
    config = run("tc", "qdisc", "show", "dev", device).splitlines()[0]
    stats = json.loads(run("tc", "-json", "-stats", "qdisc", "show", "dev", device))[0]
    return {"config": config, "kind": stats["kind"], "packets": stats.get("packets", 0), "drops": stats.get("drops", 0)}


def retrans(pid=None):
    """TCP segments retransmitted: one counter for a whole network namespace, not per interface."""
    command = ["nstat", "-asz", "TcpRetransSegs"]
    if pid:
        command = ["nsenter", "-t", str(pid), "-n"] + command
    for line in run(*command).splitlines():
        if line.startswith("TcpRetransSegs"):
            return int(line.split()[1])
    raise Unknown("nstat printed no TcpRetransSegs")


def snapshot():
    """Read every counter that is compared between the start and the end of the interval.
    A counter that cannot be read is kept as an Unknown, for the check that needs it."""
    readers = {
        "cpu.stat": lambda: keyvals(cgroup("cpu.stat")),
        "memory.events": lambda: keyvals(cgroup("memory.events")),
        "host_link": lambda: link(need_container()["veth"]),
        "container_link": lambda: link("eth0", need_container()["pid"]),
        "qdisc": lambda: qdisc(need_container()["veth"]),
        "host_retrans": lambda: retrans(),
        "container_retrans": lambda: retrans(need_container()["pid"]),
    }
    snap = {}
    for name, reader in readers.items():
        try:
            snap[name] = reader()
        except Exception as error:
            snap[name] = Unknown(explain(error))
    return snap


def counter(snap, name):
    value = snap.get(name, Unknown("not sampled"))
    if isinstance(value, Unknown):
        raise value
    return value


def incus_get(key):
    return run("incus", "config", "get", NAME, key)


def limit(key, file):
    need_container()
    return {"incus": incus_get(key), "kernel": cgroup(file)}


def cgroup_version():
    return "v2" if os.path.exists("/sys/fs/cgroup/cgroup.controllers") else "v1 or hybrid"


# The baseline profile: the fields the drift check compares, and how to read each one now.
PROFILE = {
    "kernel": lambda: os.uname().release,
    "cgroup": cgroup_version,
    "cpu_limit": lambda: limit("limits.cpu.allowance", "cpu.max"),
    "cpuset": lambda: limit("limits.cpu", "cpuset.cpus.effective"),
    "memory_limit": lambda: limit("limits.memory", "memory.max"),
}


# ---- the checks: each returns (status, message) or (status, message, data) ----

def check_os():
    release = load_env("/etc/os-release")
    return PASS, f"{release.get('PRETTY_NAME', 'unknown OS')}, kernel {os.uname().release}"


def check_cgroup():
    if cgroup_version() != "v2":
        return FAIL, "cgroup v2 is not mounted at /sys/fs/cgroup"
    controllers = read("/sys/fs/cgroup/cgroup.controllers").split()
    missing = [c for c in ("cpu", "cpuset", "memory") if c not in controllers]
    if missing:
        return FAIL, f"cgroup v2 without the {', '.join(missing)} controller"
    return PASS, "cgroup v2 with the cpu, cpuset and memory controllers"


def check_host_cpu():
    cpus = os.cpu_count()
    load1, load5, load15 = os.getloadavg()
    status = WARN if load1 / cpus > float(ENV["LOAD_WARN_PER_CPU"]) else PASS
    return status, f"{cpus} CPUs, load average {load1:.2f} {load5:.2f} {load15:.2f} (1, 5, 15 min)", \
        {"cpus": cpus, "load1": load1}


def check_host_memory():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        key, value = line.split()[:2]
        info[key.rstrip(":")] = int(value)            # kB
    percent = 100 * info["MemAvailable"] / info["MemTotal"]
    if percent < float(ENV["MEM_AVAILABLE_FAIL_PCT"]):
        status = FAIL
    elif percent < float(ENV["MEM_AVAILABLE_WARN_PCT"]):
        status = WARN
    else:
        status = PASS
    return status, f"{info['MemAvailable'] >> 10} MiB available of {info['MemTotal'] >> 10} MiB ({percent:.0f}%)"


def check_host_disk():
    fs = os.statvfs("/")
    used = fs.f_blocks - fs.f_bfree
    percent = 100 * used / (used + fs.f_bavail)        # the way df computes Use%
    free_gib = fs.f_bavail * fs.f_frsize / 2**30
    status = level(percent, float(ENV["DISK_WARN_PCT"]), float(ENV["DISK_FAIL_PCT"]))
    return status, f"root filesystem {percent:.0f}% used, {free_gib:.1f} GiB free"


PROBLEMS = re.compile(r"out of memory|oom-kill|blocked for more than|soft lockup|call trace|"
                      r"netdev watchdog|i/o error", re.IGNORECASE)


def check_kernel_log():
    minutes = ENV["LOG_WINDOW_MIN"]
    if time_left() <= 0:
        raise OutOfTime()
    result = subprocess.run(["journalctl", "-k", "-q", "--no-pager", "-p", "warning", "--since", f"-{minutes}min"],
                            capture_output=True, text=True, timeout=min(5, time_left()))
    if result.returncode != 0 or "not seeing messages" in result.stderr:
        raise Unknown(f"kernel log not readable: {result.stderr.strip()[:150]}")
    lines = result.stdout.splitlines()
    problems = [line for line in lines if PROBLEMS.search(line)]
    message = f"last {minutes} min: {len(lines)} kernel messages at warning level or worse, {len(problems)} look like problems"
    if problems:
        return WARN, f"{message}; latest: {problems[-1][:150]}", {"warnings": len(lines), "problems": problems[-5:]}
    return PASS, message + " (oom, hung task, lockup, call trace, watchdog, I/O error)", {"warnings": len(lines)}


def check_container_state():
    if CT.get("absent"):
        return FAIL, f"container {NAME} does not exist (run make setup)"
    if "error" in CT:
        raise Unknown(CT["error"])
    if CT["status"] != "Running":
        return FAIL, f"container {NAME} is {CT['status']}"
    if not CT["ip"]:
        return FAIL, f"container {NAME} is running but has no IPv4 address"
    return PASS, f"{NAME} running, first process PID {CT['pid']}, address {CT['ip']}, host-side link {CT['veth']}", CT


def check_container_cpu():
    need_container()
    start, end = counter(START, "cpu.stat"), counter(END, "cpu.stat")
    periods = end["nr_periods"] - start["nr_periods"]
    throttled = end["nr_throttled"] - start["nr_throttled"]
    waited_ms = (end["throttled_usec"] - start["throttled_usec"]) / 1000
    percent = 100 * throttled / periods if periods else 0.0
    data = {"cpu_max": cgroup("cpu.max"), "cpuset": cgroup("cpuset.cpus.effective"),
            "incus_allowance": incus_get("limits.cpu.allowance"), "incus_cpus": incus_get("limits.cpu"),
            "periods": periods, "throttled": throttled, "throttled_ms": round(waited_ms, 1),
            "cpu_stat_start": start, "cpu_stat_end": end}
    status = level(percent, float(ENV["THROTTLE_WARN_PCT"]), float(ENV["THROTTLE_FAIL_PCT"]))
    return status, (f"cpu.max {data['cpu_max']} (Incus allowance {data['incus_allowance'] or 'not set'}), "
                    f"cpuset {data['cpuset']} (Incus limits.cpu {data['incus_cpus'] or 'not set'}); "
                    f"{throttled} of {periods} periods throttled ({percent:.0f}%), {waited_ms:.0f} ms waiting"), data


def check_container_memory():
    need_container()
    used, limit_text = int(cgroup("memory.current")), cgroup("memory.max")
    ooms = counter(END, "memory.events").get("oom_kill", 0) - counter(START, "memory.events").get("oom_kill", 0)
    configured = incus_get("limits.memory") or "not set"
    if limit_text == "max":
        return WARN, f"no memory limit (Incus limits.memory {configured}), {used >> 20} MiB used"
    percent = 100 * used / int(limit_text)
    status = level(percent, float(ENV["MEMORY_WARN_PCT"]), float(ENV["MEMORY_FAIL_PCT"]))
    if ooms and status == PASS:
        status = WARN
    return status, (f"memory.max {int(limit_text) >> 20} MiB (Incus limits.memory {configured}), "
                    f"memory.current {used >> 20} MiB ({percent:.0f}%), {ooms} OOM kills in the interval")


def link_check(name, start, end):
    """Interface state and the change of its counters over the interval."""
    delta = {k: end[k] - start[k] for k in ("rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped")}
    data = dict(delta, state=end["state"], mtu=end["mtu"], master=end["master"])
    bad = delta["rx_errors"] + delta["tx_errors"] + delta["rx_dropped"] + delta["tx_dropped"]
    bridge = f", on bridge {end['master']}" if end["master"] else ""
    message = (f"{name} {end['state']}, MTU {end['mtu']}{bridge}; in the interval {delta['rx_packets']} packets in, "
               f"{delta['tx_packets']} out, errors {delta['rx_errors']}/{delta['tx_errors']}, "
               f"drops {delta['rx_dropped']}/{delta['tx_dropped']} (in/out); speed not meaningful (virtual veth link)")
    if end["state"] != "UP":
        return FAIL, message, data
    return (WARN if bad else PASS), message, data


def check_host_link():
    ct = need_container()
    return link_check(f"host side {ct['veth']}", counter(START, "host_link"), counter(END, "host_link"))


def check_container_link():
    need_container()
    return link_check("container eth0", counter(START, "container_link"), counter(END, "container_link"))


def check_qdisc():
    ct = need_container()
    start, end = counter(START, "qdisc"), counter(END, "qdisc")
    data = {"config": end["config"], "kind": end["kind"],
            "packets": end["packets"] - start["packets"], "drops": end["drops"] - start["drops"]}
    if end["kind"] == "noqueue":
        return PASS, f"{ct['veth']}: {end['config']} (the default: no queue, so no qdisc counters)", data
    message = f"{ct['veth']}: {end['config']}; in the interval {data['packets']} packets sent, {data['drops']} dropped by the qdisc"
    return (WARN if end["kind"] == "netem" else PASS), message, data


def check_retrans():
    ct = need_container()
    host = counter(END, "host_retrans") - counter(START, "host_retrans")
    container = counter(END, "container_retrans") - counter(START, "container_retrans")
    host_ns, container_ns = os.readlink("/proc/self/ns/net"), os.readlink(f"/proc/{ct['pid']}/ns/net")
    return PASS, (f"TCP retransmits in the interval: host namespace {host_ns} +{host}, container namespace "
                  f"{container_ns} +{container} (whole-namespace counters, evidence only)"), \
        {"host": host, "container": container, "host_netns": host_ns, "container_netns": container_ns}


def check_http():
    ct = need_container()
    url = f"http://{ct['ip']}:{ENV['PORT']}/healthz"
    started = time.monotonic()
    conn = http.client.HTTPConnection(ct["ip"], int(ENV["PORT"]), timeout=max(0.1, min(3, time_left())))
    try:
        conn.request("GET", "/healthz")
        status = conn.getresponse().status
    except OSError as error:
        return FAIL, f"{url} not reachable from the host: {error}"
    finally:
        conn.close()
    if status != 200:
        return FAIL, f"{url} answered HTTP {status}"
    return PASS, f"{url} answered 200 in {(time.monotonic() - started) * 1000:.1f} ms"


def check_app():
    if isinstance(LOAD, Exception):
        raise LOAD
    r = LOAD
    if r["requests"] == 0:
        raise Unknown("no requests were sent (interval too short)")
    if r["errors"] == r["requests"]:
        return FAIL, f"all {r['requests']} requests failed", r
    status = WARN if r["errors"] or r["p99_ms"] > float(ENV["SLOW_P99_MS"]) else PASS
    return status, (f"{r['requests']} requests (sample count) from {r['clients']} client(s) in {r['duration_s']:g} s: "
                    f"{r['errors']} errors, {r['rps']} req/s, p50 {r['p50_ms']:.1f} ms, p99 {r['p99_ms']:.1f} ms, "
                    f"max {r['max_ms']:.1f} ms"), r


def drift_check(field):
    def check():
        if "error" in BASELINE:
            raise Unknown(BASELINE["error"])
        if field not in BASELINE:
            raise Unknown(f"{field} is not in the baseline file")
        return drift(field, BASELINE[field], PROFILE[field]())
    return check


CHECKS = [
    ("host.os", check_os),
    ("host.cgroup", check_cgroup),
    ("host.cpu", check_host_cpu),
    ("host.memory", check_host_memory),
    ("host.disk", check_host_disk),
    ("host.kernel_log", check_kernel_log),
    ("container.state", check_container_state),
    ("container.cpu", check_container_cpu),
    ("container.memory", check_container_memory),
    ("network.host_link", check_host_link),
    ("network.container_link", check_container_link),
    ("network.qdisc", check_qdisc),
    ("network.tcp_retrans", check_retrans),
    ("network.http", check_http),
    ("app.load", check_app),
    ("drift.kernel", drift_check("kernel")),
    ("drift.cgroup", drift_check("cgroup")),
    ("drift.cpu_limit", drift_check("cpu_limit")),
    ("drift.cpuset", drift_check("cpuset")),
    ("drift.memory_limit", drift_check("memory_limit")),
]


def run_one(check):
    """Run one check. Missing evidence or any surprise becomes UNKNOWN with the reason."""
    if time_left() <= 0:
        return UNKNOWN, "not run: time limit reached", None
    try:
        result = check()
    except Exception as error:
        return UNKNOWN, explain(error), None
    status, message, data = (tuple(result) + (None,))[:3]
    return status, message, data


def hard_stop(signum, frame):
    """Backstop for the runtime limit. Every command and request already stops at the limit;
    if something still hangs 2 s past it, report INCOMPLETE and exit instead of waiting."""
    print("INCOMPLETE (exit 3): runtime limit reached while collecting evidence", file=sys.stderr)
    os._exit(3)


def collect(interval, clients, timeout, baseline_file):
    """Sample, check and build the report (a dict)."""
    global DEADLINE, LOAD, START, END
    started = time.monotonic()
    DEADLINE = started + timeout
    signal.signal(signal.SIGALRM, hard_stop)
    signal.setitimer(signal.ITIMER_REAL, timeout + 2)

    CT.clear()
    try:
        CT.update(container_facts())
    except subprocess.CalledProcessError as error:     # Incus answered: maybe "Instance not found"
        CT["error"] = explain(error)
        CT["absent"] = "not found" in (error.stderr or "").lower()
    except Exception as error:                          # Incus missing, permission denied, timeout
        CT["error"] = explain(error)

    BASELINE.clear()
    try:
        BASELINE.update(json.loads(read(baseline_file)))
    except FileNotFoundError:
        BASELINE["error"] = f"no baseline file {baseline_file} (run make baseline)"
    except Exception as error:
        BASELINE["error"] = f"baseline file {baseline_file}: {explain(error)}"

    seconds = min(interval, time_left() - 5)          # keep 5 s for the checks
    url = f"http://{CT.get('ip')}:{ENV['PORT']}/work"
    try:
        START = snapshot()
        if seconds <= 0:
            raise Unknown("no time left for the sampling interval")
        need_container()
        LOAD = loadgen.run(url, clients, seconds, timeout=min(5, seconds + 1))
    except Exception as error:
        LOAD = Unknown(explain(error))
    END = snapshot()

    checks = []
    for name, check in CHECKS:
        status, message, data = run_one(check)
        checks.append({"check": name, "status": status, "message": message, "data": data})
    signal.setitimer(signal.ITIMER_REAL, 0)

    status = overall([c["status"] for c in checks])

    def plain(snap):                                  # Unknown values as text, for the JSON file
        return {k: (f"UNKNOWN: {v}" if isinstance(v, Unknown) else v) for k, v in snap.items()}

    return {
        "status": status,
        "exit_code": EXIT_CODES[status],
        "reason": one_line(status, checks),
        "time": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "container": NAME,
        "load_settings": {"url": url, "clients": clients, "duration_s": max(seconds, 0)},
        "interval_s": max(seconds, 0),
        "timeout_s": timeout,
        "runtime_s": round(time.monotonic() - started, 2),
        "checks": checks,
        "samples": {"start": plain(START), "end": plain(END)},
    }


def save_baseline(path, timeout):
    """Record the baseline profile to a file. Reads only; the file is the one thing written."""
    global DEADLINE
    DEADLINE = time.monotonic() + timeout
    try:
        CT.update(container_facts())
        profile = {field: read_now() for field, read_now in PROFILE.items()}
    except Exception as error:
        print(f"baseline not saved: {explain(error)}", file=sys.stderr)
        return 3
    profile.update(recorded=time.strftime("%Y-%m-%d %H:%M:%S %z"), container=NAME)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"Baseline saved to {path}:")
    for field in PROFILE:
        print(f"  {field:<13} {show(profile[field])}")
    return 0


class Parser(argparse.ArgumentParser):
    def error(self, message):                   # argparse would exit 2, which means UNHEALTHY here
        print(f"health.py: {message}", file=sys.stderr)
        sys.exit(64)


def main():
    parser = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=float(ENV["HEALTH_INTERVAL"]), help="sampling seconds")
    parser.add_argument("--clients", type=int, default=int(ENV["CLIENTS"]), help="load generator clients during the interval")
    parser.add_argument("--timeout", type=float, help="runtime limit in seconds (default: HEALTH_TIMEOUT)")
    parser.add_argument("--baseline", default=os.path.join(ROOT, ENV["BASELINE"]), help="baseline file to compare with")
    parser.add_argument("--save-baseline", metavar="FILE", nargs="?", const=os.path.join(ROOT, ENV["BASELINE"]),
                        help="record the baseline profile (default file: BASELINE in lab.env) and exit")
    parser.add_argument("--json", action="store_true", help="print the JSON report on stdout")
    parser.add_argument("--out", help="also write the JSON report to this file")
    args = parser.parse_args()
    timeout = args.timeout if args.timeout is not None else float(ENV["HEALTH_TIMEOUT"])
    if args.interval < 0 or timeout < 0 or args.clients < 1:
        parser.error("--interval and --timeout must be 0 or more, --clients 1 or more")

    if args.save_baseline:
        sys.exit(save_baseline(args.save_baseline, timeout))

    report = collect(args.interval, args.clients, timeout, args.baseline)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
    if args.json:
        print(json.dumps(report, indent=2))

    screen = sys.stderr if args.json else sys.stdout
    print(f"{report['status']} (exit {report['exit_code']}): {report['reason']}", file=screen)
    print(f"Container {NAME}: {report['interval_s']:g} s sample with {args.clients} client(s), "
          f"took {report['runtime_s']} s of a {timeout:g} s limit", file=screen)
    for c in report["checks"]:
        print(f"  {c['status']:<8} {c['check']:<23} {c['message']}", file=screen)
    sys.exit(report["exit_code"])


if __name__ == "__main__":
    main()
