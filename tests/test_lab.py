"""Unit tests. They need no container and no sudo:  python3 -m unittest discover -s tests

Tests marked FIXTURE use made-up inputs (errors, reports) instead of a running lab."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import health      # noqa: E402
import loadgen     # noqa: E402
import summarize   # noqa: E402


class TestHealthAggregation(unittest.TestCase):
    def test_overall_status(self):
        self.assertEqual(health.overall(["PASS", "PASS"]), "HEALTHY")
        self.assertEqual(health.overall(["PASS", "WARN"]), "DEGRADED")
        self.assertEqual(health.overall(["WARN", "UNKNOWN"]), "INCOMPLETE")    # UNKNOWN beats WARN
        self.assertEqual(health.overall(["UNKNOWN", "FAIL"]), "UNHEALTHY")     # FAIL beats everything
        self.assertEqual(health.overall(["PASS", "UNKNOWN"]), "INCOMPLETE")    # missing evidence is never HEALTHY

    def test_exit_codes(self):
        self.assertEqual(health.EXIT_CODES, {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "INCOMPLETE": 3})

    def test_level(self):
        self.assertEqual(health.level(10, 20, 60), "PASS")
        self.assertEqual(health.level(20, 20, 60), "WARN")
        self.assertEqual(health.level(99, 20, 60), "FAIL")

    def test_one_line_reason(self):
        checks = [{"check": "a", "status": "PASS", "message": "ok"},
                  {"check": "b", "status": "WARN", "message": "netem is active"},
                  {"check": "c", "status": "WARN", "message": "x"}]
        self.assertEqual(health.one_line("DEGRADED", checks), "b WARN: netem is active (+1 more)")
        self.assertEqual(health.one_line("HEALTHY", checks[:1]), "all 1 checks passed")


class TestMissingEvidence(unittest.TestCase):
    """Missing evidence must give UNKNOWN with a reason, never a crash or a PASS."""

    def setUp(self):
        health.DEADLINE = time.monotonic() + 10

    def test_command_timeout(self):
        health.DEADLINE = time.monotonic() + 0.5          # half a second left
        start = time.monotonic()
        status, message, _ = health.run_one(lambda: ("PASS", health.run("sleep", "5")))
        self.assertEqual(status, "UNKNOWN")
        self.assertIn("timed out", message)
        self.assertLess(time.monotonic() - start, 2)

    def test_missing_command(self):
        status, message, _ = health.run_one(lambda: ("PASS", health.run("no-such-command-xyz")))
        self.assertEqual((status, message), ("UNKNOWN", "not found: no-such-command-xyz"))

    def test_permission_denied_fixture(self):           # FIXTURE: the error a non-root read gives
        def check():
            raise PermissionError(13, "Permission denied", "/sys/fs/cgroup/lxc.payload.lab/cpu.stat")
        self.assertEqual(health.run_one(check)[:2],
                         ("UNKNOWN", "permission denied: /sys/fs/cgroup/lxc.payload.lab/cpu.stat"))

    def test_failed_command_fixture(self):              # FIXTURE: incus answering with an error
        def check():
            raise subprocess.CalledProcessError(1, ["incus", "query"], stderr="Error: not authorized")
        status, message, _ = health.run_one(check)
        self.assertEqual(status, "UNKNOWN")
        self.assertIn("not authorized", message)

    def test_unexpected_error_fixture(self):            # FIXTURE: a bug in a check must not crash the tool
        status, message, _ = health.run_one(lambda: 1 / 0)
        self.assertEqual(status, "UNKNOWN")
        self.assertIn("ZeroDivisionError", message)

    def test_no_time_left(self):
        start = time.monotonic()
        report = health.collect(interval=5, clients=1, timeout=0, baseline_file="/nonexistent")
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual({c["status"] for c in report["checks"]}, {"UNKNOWN"})
        self.assertEqual((report["status"], report["exit_code"]), ("INCOMPLETE", 3))

    def test_bad_argument_exit_code(self):
        result = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "health.py"), "--nope"],
                                capture_output=True)
        self.assertEqual(result.returncode, 64)


class TestDrift(unittest.TestCase):
    def test_same(self):
        self.assertEqual(health.drift("kernel", "6.8.0", "6.8.0")[0], "PASS")

    def test_changed_through_incus(self):
        before = {"incus": "200ms/100ms", "kernel": "200000 100000"}
        now = {"incus": "50ms/100ms", "kernel": "50000 100000"}
        status, message = health.drift("cpu_limit", before, now)
        self.assertEqual(status, "WARN")
        self.assertIn("50000 100000", message)

    def test_changed_outside_incus(self):
        before = {"incus": "200ms/100ms", "kernel": "200000 100000"}
        now = {"incus": "200ms/100ms", "kernel": "50000 100000"}
        self.assertIn("changed outside Incus", health.drift("cpu_limit", before, now)[1])

    def test_load_env(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write('# comment\nNAME=lab   # note\nFAULT_NETEM="delay 40ms loss 1%"\nCPU_PIN=   # empty\n')
        self.assertEqual(health.load_env(f.name), {"NAME": "lab", "FAULT_NETEM": "delay 40ms loss 1%", "CPU_PIN": ""})
        os.unlink(f.name)


def fake_report(status, p99, cpu_max="200000 100000", qdisc="qdisc noqueue 0: root refcnt 2"):
    """FIXTURE: the parts of a health report that summarize.py reads."""
    return {"status": status, "checks": [
        {"check": "app.load", "data": {"requests": 400, "errors": 0, "rps": 20.0, "p50_ms": 5.0, "p99_ms": p99}},
        {"check": "container.cpu", "data": {"cpu_max": cpu_max, "throttled": 0, "periods": 200}},
        {"check": "network.qdisc", "data": {"config": qdisc, "drops": 0}},
    ]}


class TestSummary(unittest.TestCase):
    def test_pass(self):
        reports = {"baseline": fake_report("HEALTHY", 5.0),
                   "fault": fake_report("UNHEALTHY", 50.0, cpu_max="50000 100000"),
                   "recovery": fake_report("HEALTHY", 5.1)}
        self.assertTrue(all(ok for ok, _ in summarize.verdict(reports)))

    def test_fault_not_removed(self):
        reports = {"baseline": fake_report("HEALTHY", 5.0),
                   "fault": fake_report("DEGRADED", 50.0, qdisc="qdisc netem 8001: root delay 40ms"),
                   "recovery": fake_report("DEGRADED", 50.0, qdisc="qdisc netem 8001: root delay 40ms")}
        result = summarize.verdict(reports)
        self.assertFalse(result[3][0])          # the original qdisc is not back
        self.assertFalse(result[4][0])          # health not back to the baseline result

    def test_changes(self):
        reports = {"baseline": fake_report("HEALTHY", 5.0), "fault": fake_report("UNHEALTHY", 50.0, cpu_max="50000 100000")}
        changed, same = summarize.changes(reports)
        self.assertEqual(changed, ["cpu.max 200000 100000 -> 50000 100000"])
        self.assertIn("qdisc drops 0", same)
        self.assertIn("throttled periods 0", same)
        self.assertTrue(summarize.differs(0, 4) and summarize.differs(6.0, 8.2))
        self.assertFalse(summarize.differs(6.17, 6.19))


class TestAppAndLoadgen(unittest.TestCase):
    """Start the real app on this machine and point the real load generator at it."""

    @classmethod
    def setUpClass(cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        env = dict(os.environ, PORT=str(cls.port), WORK="2000")
        cls.app = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "server.py")], env=env,
                                   stdout=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.app.terminate()
        cls.app.wait()

    def test_percentile(self):
        values = list(range(1, 101))
        self.assertEqual(loadgen.percentile(values, 50), 50)
        self.assertEqual(loadgen.percentile(values, 99), 99)
        self.assertEqual(loadgen.percentile([7], 99), 7)
        self.assertIsNone(loadgen.percentile([], 50))

    def test_load(self):
        r = loadgen.run(f"http://127.0.0.1:{self.port}/work", clients=2, duration=1)
        self.assertGreater(r["requests"], 10)
        self.assertEqual(r["errors"], 0)
        self.assertLessEqual(r["p50_ms"], r["p99_ms"])
        # About 40 ms for under 1 ms of work would mean the Nagle / delayed-ACK stall is back.
        self.assertLess(r["p50_ms"], 20)

    def test_errors_are_counted(self):
        r = loadgen.run(f"http://127.0.0.1:{self.port}/work?n=abc", clients=1, duration=0.3)
        self.assertEqual(r["errors"], r["requests"])


if __name__ == "__main__":
    unittest.main()
