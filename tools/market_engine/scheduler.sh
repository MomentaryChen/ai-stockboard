#!/bin/sh
# Runs the batch once a day at ENGINE_RUN_AT. Same shape as the db-backup
# service, for the same reason: `docker compose logs market-engine` is then the
# complete record of whether last night's run happened.
set -u

ENGINE_RUN_AT=${ENGINE_RUN_AT:-20:00}
ENGINE_OUTPUT=${ENGINE_OUTPUT:-/output/latest.json}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') engine: $*"; }

case "$ENGINE_RUN_AT" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *) log "ENGINE_RUN_AT must be HH:MM, got '$ENGINE_RUN_AT'"; exit 1 ;;
esac

while :; do
    now=$(date +%s)
    next=$(date -d "$(date +%Y-%m-%d) $ENGINE_RUN_AT" +%s)
    [ "$next" -gt "$now" ] || next=$((next + 86400))
    log "next run in $((next - now)) seconds"
    sleep $((next - now))
    # A failed run leaves yesterday's file in place, and the importer then
    # reports it as unchanged, so the loop only needs to keep going.
    python -m market_engine --out "$ENGINE_OUTPUT" || log "run failed"
done
