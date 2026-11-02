# About this run

`make all` on a bare-metal Arm64 machine (Ubuntu 24.04.5, 20 cores, 121 GiB), run by the operator on
2026-11-02 with the code in this repository, started with `taskset -c 15-19 make all`.

- The container was pinned to five fast cores with `CPU_PIN=5-9` in `lab.env` (a local setting,
  not committed), and the scripts and load generator ran on five other fast cores (15-19). The
  machine has two kinds of cores (10 Cortex-X925 and 10 Cortex-A725).
- Three strings were replaced with generic labels in every file: the kernel's vendor suffix
  (shown as `7.0.0-1019`), the home directory (`/home/user/`), and the CPU line in `machine.txt`
  and `console.log` (`lscpu` reports only the first core model; it now reads
  `20 cores (Cortex-X925 + Cortex-A725, aarch64)`). Every measurement, counter and status is
  exactly as the run wrote it.
- The "Interpretation" sections of `cpu/summary.md` and `net/summary.md` were written afterwards.
