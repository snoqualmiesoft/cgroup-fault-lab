#!/usr/bin/env bash
# Load the app with CLIENTS clients for DURATION seconds; print the sample count, throughput and latency.
source "$(dirname "$0")/common.sh"
python3 "$ROOT/bin/loadgen.py" "$(app_url)/work" --clients "$CLIENTS" --duration "$DURATION"
