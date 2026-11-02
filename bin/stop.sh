#!/usr/bin/env bash
# Stop the lab container.
source "$(dirname "$0")/common.sh"
incus stop "$NAME" && log "Stopped $NAME"
