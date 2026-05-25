#!/bin/bash
# Pull ~/.claude/projects/ from remote hosts into the local cache the
# token dashboard scans. Designed to run from cron every few minutes —
# incremental rsync ships only changed bytes.
#
# Host-aware: the HOSTS array names the SOURCE hosts to pull FROM. The
# script runs ON the dashboard host (currently JLAP), and pulls from
# every other machine you use Claude on. The cache lands at
# $HOME/.cache/token-dashboard/remote/<host>/projects/, which is outside
# any Mutagen sync (lesson learned the hard way: caching JLAP files
# inside /mnt/data/dev on Jstation created a 4.2 GB sync loop).
#
# Each host name must match (a) an SSH alias in ~/.ssh/config that
# key-auths non-interactively, and (b) the host label the scanner tags
# rows with (set via CLAUDE_PROJECTS_DIRS in ecosystem.config.js).
#
# Failure mode: a host that's offline / asleep is logged and skipped;
# other hosts still pull. Exit 0 unless EVERY host fails (so cron
# doesn't flap on a single sleepy laptop).

set -u

HOSTS=(jstation)
# Future: HOSTS=(jstation jpc) — JPC needs rsync installed first
# (currently no rsync.exe on the Windows side).

CACHE_ROOT="$HOME/.cache/token-dashboard/remote"
LOG_FILE="$HOME/.cache/token-dashboard/sync-remote.log"
LOCK_FILE="/tmp/token-dashboard-sync-remote.lock"

mkdir -p "$CACHE_ROOT" "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG_FILE"
}

# flock prevents overlapping cron runs — if a previous sync is still
# going (slow disk, big remote churn) the new tick exits silently
# rather than stampeding the SSH connection or doubling I/O.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "skip: previous sync still running"
  exit 0
fi

successes=0
for host in "${HOSTS[@]}"; do
  dest="$CACHE_ROOT/$host/projects/"
  mkdir -p "$dest"
  # -a archive | -z compress over wire | --delete mirror deletions
  # --partial keep partial transfers across reconnects
  # --timeout=30 abort a stalled connection rather than hanging cron
  # ConnectTimeout=10 fail fast on offline hosts
  if rsync -az --delete --partial --timeout=30 \
       -e "ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15" \
       "$host:.claude/projects/" "$dest" >> "$LOG_FILE" 2>&1; then
    log "ok: $host -> $dest"
    successes=$((successes + 1))
  else
    rc=$?
    log "fail: $host rsync exit $rc"
  fi
done

if (( successes == 0 )); then
  log "all hosts failed"
  exit 1
fi
exit 0
