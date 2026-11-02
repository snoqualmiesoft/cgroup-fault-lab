#!/usr/bin/env bash
# Start the lab container (the app starts with it).
source "$(dirname "$0")/common.sh"
incus start "$NAME" && log "Started $NAME"
