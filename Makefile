# Run `make` to see the commands. Every command is one script in bin/ or tests/.

help:        ## show this list
	@grep -E '^[a-z-]+:.*## ' Makefile | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

preflight:   ## check this machine can run the lab (read-only, no sudo)
	bin/preflight.sh

setup:       ## install Incus, create the lab bridge and a fresh container, start the app
	sudo bin/setup.sh

start:       ## start the container
	sudo bin/start.sh

stop:        ## stop the container
	sudo bin/stop.sh

inspect:     ## show the container, its limits and its network (read-only)
	sudo bin/inspect.sh

baseline:    ## record the baseline profile for the drift check
	sudo python3 bin/health.py --save-baseline

health:      ## health, diagnostics and drift report (exit 0 HEALTHY, 1 DEGRADED, 2 UNHEALTHY, 3 INCOMPLETE)
	sudo python3 bin/health.py

load:        ## load the app and print p50/p99 latency
	sudo bin/load.sh

fault-cpu:   ## CPU fault cycle (results in reports/)
	sudo bin/fault-cpu.sh

fault-net:   ## network fault cycle (results in reports/)
	sudo bin/fault-net.sh

restore:     ## recovery command: limits back to lab.env, netem removed (safe any time)
	sudo bin/restore.sh

test:        ## unit tests (no container needed)
	python3 -m unittest discover -s tests -v

smoke:       ## smoke test: setup, reachability, report, cleanup
	sudo tests/smoke.sh

interrupt:   ## demo: recovery works after a fault run is killed
	sudo tests/interrupt.sh

all:         ## everything in order (results in reports/run-<time>/)
	sudo bin/run-all.sh

teardown:    ## delete the container (bridge and image stay, for offline rebuilds)
	sudo bin/teardown.sh

.PHONY: help preflight setup start stop inspect baseline health load fault-cpu fault-net restore test smoke interrupt all teardown
