#!/usr/bin/env bash
# Delete the lab container. The bridge and the local image stay, so `make setup` works offline.
# OPERATIONS.md shows how to remove everything, Incus included.
source "$(dirname "$0")/common.sh"
incus delete --force "$NAME" && log "Deleted container $NAME"
