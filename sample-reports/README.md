# Sample reports

One complete run of `make all` on a bare-metal Arm64 machine (Ubuntu 24.04.5, 20 cores, 121 GiB),
run by the operator on 2026-11-02:
[`bare-metal-arm64-20261102-001825`](bare-metal-arm64-20261102-001825/). It is copied unchanged
except for three generic labels and the two "Interpretation" sections, written afterwards (see its
[`NOTE.md`](bare-metal-arm64-20261102-001825/NOTE.md)).

The `commit:` line in `machine.txt` refers to the development history, which was squashed into
this repository's first commit. The code that produced the reports is the code here; only the
documentation changed since.

Load settings in every report: 1 client, 20 s, `GET http://10.123.0.10:8080/work` (`WORK=60000`).

What is in the folder:

| file | what it is |
|---|---|
| `console.log` | everything the run printed, in order |
| `machine.txt` | OS, CPUs, Incus version, commit, settings, and `make inspect`: limits, interface, address, bridge, route, MTU |
| `baseline.json` | the baseline profile used by the drift checks |
| `health-start.*`, `health-end.*` | health reports before the fault cycles and at the end (JSON and terminal) |
| `cpu/`, `net/` | one fault cycle each: `1-baseline`, `2-fault`, `3-recovery` health reports (`.json` and `.txt`), `injection.txt` (the command and the setting before and after), `summary.md`; `net/netem-stats.txt` is `tc -s qdisc` at the end of the fault phase |
| `interrupt/` | the demonstration that `make restore` recovers after a fault run is killed with `kill -9`; the partial reports of the killed runs are kept as they were |
| `smoke/` | the smoke test's setup log and report |
