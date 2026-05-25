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

RTK_CACHE_ROOT="$HOME/.cache/token-dashboard/rtk-state"
LANES_CACHE="$HOME/.cache/token-dashboard/lanes-logs"
mkdir -p "$RTK_CACHE_ROOT" "$LANES_CACHE"

# Per host we pull TWO trees:
#   1. ~/.claude/projects/   → CACHE_ROOT/<host>/projects/   (transcript JSONLs)
#   2. ~/.local/share/rtk/   → RTK_CACHE_ROOT/<host>/rtk/    (rtk history.db)
# Both feed the dashboard. The rtk path mirrors XDG_DATA_HOME layout so the
# server can invoke `XDG_DATA_HOME=$RTK_CACHE_ROOT/<host> rtk gain` and the
# binary picks up the remote DB without needing a --db flag (rtk has none).
ssh_opts="-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15"

successes=0
for host in "${HOSTS[@]}"; do
  host_ok=0

  # Transcript projects
  proj_dest="$CACHE_ROOT/$host/projects/"
  mkdir -p "$proj_dest"
  if rsync -az --delete --partial --timeout=30 -e "ssh $ssh_opts" \
       "$host:.claude/projects/" "$proj_dest" >> "$LOG_FILE" 2>&1; then
    log "ok: $host projects -> $proj_dest"
    host_ok=1
  else
    log "fail: $host projects rsync exit $?"
  fi

  # RTK history (small file, ~tens of MB — quick)
  rtk_dest="$RTK_CACHE_ROOT/$host/rtk/"
  mkdir -p "$rtk_dest"
  if rsync -az --delete --partial --timeout=30 -e "ssh $ssh_opts" \
       "$host:.local/share/rtk/" "$rtk_dest" >> "$LOG_FILE" 2>&1; then
    log "ok: $host rtk -> $rtk_dest"
    host_ok=1
  else
    # rtk may not be installed on every host — log but don't treat as failure
    log "skip: $host rtk rsync exit $? (rtk maybe not installed)"
  fi

  if (( host_ok )); then
    successes=$((successes + 1))
  fi
done

# Lanes invocation logs — pulled from Jstation only (that's where the
# global lanes ledger aggregates per-machine ndjsons). Single best-effort
# rsync, ignored if missing.
if rsync -az --partial --timeout=30 -e "ssh $ssh_opts" \
     "jstation:/mnt/data/lanes-logs/" "$LANES_CACHE/" >> "$LOG_FILE" 2>&1; then
  log "ok: jstation lanes-logs -> $LANES_CACHE"
else
  log "skip: jstation lanes-logs rsync exit $?"
fi

if (( successes == 0 )); then
  log "all hosts failed"
  exit 1
fi
exit 0
