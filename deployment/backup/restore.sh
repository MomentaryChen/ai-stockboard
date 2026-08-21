#!/bin/sh
# Restore the database from one of the dumps in /backups.
#
#     docker compose exec db-backup /opt/backup/restore.sh              # list them
#     docker compose exec db-backup /opt/backup/restore.sh <file.dump>  # restore one
#
# Stop the API first, or it will keep writing into the database being replaced:
#
#     docker compose stop server
#     docker compose exec db-backup /opt/backup/restore.sh stockboard-20260821-040000.dump
#     docker compose start server
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') restore: $*"; }

if [ $# -lt 1 ]; then
    echo "Backups in $BACKUP_DIR:"
    find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -type f -exec ls -lh {} \; \
        | awk '{print "  " $9 "  " $5 "  " $6 " " $7 " " $8}' | sort
    echo
    echo "Usage: $0 <file.dump>"
    exit 1
fi

file=$1
case "$file" in
    /*) ;;
    *) file="$BACKUP_DIR/$file" ;;
esac

[ -f "$file" ] || { log "no such file: $file"; exit 1; }
pg_restore --list "$file" >/dev/null || { log "not a readable dump: $file"; exit 1; }

# Destructive and outward-facing: every account, watchlist and price row in the
# live database is replaced by whatever this file held. Confirmed by hand
# unless the caller has already decided (a scripted disaster-recovery run).
if [ "${BACKUP_RESTORE_ASSUME_YES:-}" != "1" ]; then
    printf 'Replace ALL data in "%s" with %s? Type the database name to confirm: ' \
        "$PGDATABASE" "$(basename "$file")"
    read -r answer || answer=""
    [ "$answer" = "$PGDATABASE" ] || { log "aborted"; exit 1; }
fi

# Anything still connected holds locks on the objects pg_restore is about to
# drop, and would turn the restore into a half-applied mess. The API is the
# usual culprit; this catches a psql someone left open too.
log "disconnecting other sessions from $PGDATABASE"
psql --dbname=postgres --quiet --no-align --tuples-only --command \
    "select pg_terminate_backend(pid) from pg_stat_activity
     where datname = '$PGDATABASE' and pid <> pg_backend_pid()" >/dev/null

log "restoring $file"
# --clean --if-exists rather than dropping the database: it keeps the restore a
# single command against an existing database, and --exit-on-error means a
# failure stops at the first problem instead of leaving a partial schema that
# looks like it worked.
pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error \
    --dbname="$PGDATABASE" "$file"

log "done -- start the API again: docker compose start server"
