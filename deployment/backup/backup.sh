#!/bin/sh
# Take one backup of the database and prune the ones that have aged out.
#
# Run on a schedule by scheduler.sh, and by hand whenever you want one now:
#
#     docker compose exec db-backup /opt/backup/backup.sh
#
# The connection comes from the standard PG* variables the compose service
# sets, so nothing here repeats the credentials.
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}

stamp=$(date +%Y%m%d-%H%M%S)
final="$BACKUP_DIR/${PGDATABASE}-${stamp}.dump"
# Written under a different name and moved into place at the end. A dump
# interrupted by a container restart would otherwise sit in the directory
# looking exactly like a good one, and you would find out at restore time.
partial="$final.partial"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"; }

mkdir -p "$BACKUP_DIR"

log "dumping $PGDATABASE from $PGHOST:$PGPORT"
# -Fc: PostgreSQL's own compressed format. Larger than gzipped SQL to read by
# eye, but pg_restore can list it, restore one table out of it, and run in
# parallel -- which is what you want from the copy you reach for in an
# emergency.
pg_dump --format=custom --compress=6 --file="$partial"

# A dump that pg_restore cannot read is not a backup. Checking here means a
# broken one is a failed backup run, visible today, rather than a discovery
# made during a restore.
if ! pg_restore --list "$partial" >/dev/null 2>&1; then
    log "FAILED: pg_restore cannot read the dump just written; keeping it as $partial"
    exit 1
fi

mv "$partial" "$final"
log "wrote $final ($(du -h "$final" | cut -f1))"

# Pruning comes after a verified write, never before: a failed dump must not
# also cost you the older copies it failed to replace.
if [ "$BACKUP_KEEP_DAYS" -gt 0 ]; then
    removed=$(find "$BACKUP_DIR" -maxdepth 1 -name "${PGDATABASE}-*.dump" \
        -type f -mtime "+$BACKUP_KEEP_DAYS" -print -delete | wc -l)
    [ "$removed" -gt 0 ] && log "pruned $removed backup(s) older than $BACKUP_KEEP_DAYS days"
fi

# Leftover partials from an interrupted run are not backups and are not covered
# by the retention window above, so they would otherwise accumulate forever.
find "$BACKUP_DIR" -maxdepth 1 -name '*.partial' -type f -mtime +1 -delete

exit 0
