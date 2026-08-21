#!/bin/sh
# Entrypoint for the db-backup service: run backup.sh once a day at BACKUP_AT.
#
# A sleep loop rather than busybox crond because the schedule is one line of
# arithmetic and this way the container's logs are the backup log -- `docker
# compose logs db-backup` answers "did last night's backup run", which is the
# only question anybody asks of a backup until the day they need one.
set -eu

BACKUP_AT=${BACKUP_AT:-04:00}
BACKUP_DIR=${BACKUP_DIR:-/backups}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"; }

case "$BACKUP_AT" in
    [0-2][0-9]:[0-5][0-9]) ;;
    *) log "BACKUP_AT must be HH:MM, got '$BACKUP_AT'"; exit 1 ;;
esac

# Wall clock, so the hour means what the operator meant. The container gets TZ
# from the same .env as everything else.
seconds_until_next() {
    now=$(date +%s)
    today=$(date -d "$(date +%Y-%m-%d) $BACKUP_AT" +%s 2>/dev/null) || return 1
    if [ "$today" -gt "$now" ]; then
        echo $((today - now))
    else
        echo $((today + 86400 - now))
    fi
}

mkdir -p "$BACKUP_DIR"

# First start with nothing on disk takes one immediately, so a new deployment
# is covered from day one instead of from the first time the hour comes round.
# Deliberately conditional on the directory being empty: a container that
# restarts often must not turn into a dump generator.
if [ -z "$(find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -type f 2>/dev/null | head -n 1)" ]; then
    log "no backup on disk yet -- taking one now"
    /opt/backup/backup.sh || log "initial backup failed; will retry at $BACKUP_AT"
fi

log "scheduled daily at $BACKUP_AT ($(date '+%Z')), keeping ${BACKUP_KEEP_DAYS:-14} days in $BACKUP_DIR"

while true; do
    sleep "$(seconds_until_next)"
    # Never `set -e` out of the loop on a failed dump: one unreachable database
    # tonight must not mean no backups until somebody notices the container is
    # gone. backup.sh has already logged why.
    /opt/backup/backup.sh || log "backup failed; next attempt at $BACKUP_AT"
done
