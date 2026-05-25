// Token Dashboard PM2 config — runs on JLAP.
//
// JLAP is the dashboard host. JLAP's ~/.claude/projects/ is read live as
// host='jlap'. Other machines (jstation, future jpc) are rsync'd into
// $HOME/.cache/token-dashboard/remote/<host>/projects/ by scripts/sync-remote.sh
// every 5 min (via cron) and read by the scanner with the matching host
// label so the UI breaks usage down per machine.
//
// Paths are absolute + JLAP-specific. DB lives outside ~/Dev/ on purpose —
// ~/Dev/ is in a Mutagen sync with jstation:/mnt/data/dev/, and a 1 GB
// SQLite file syncing back and forth would trash both machines.
module.exports = {
  apps: [
    {
      name: "token-dashboard",
      script: "cli.py",
      interpreter: "python3",
      args: "dashboard --no-open --no-scan --db /home/dvids/.local/share/token-dashboard/token-dashboard.db",
      restart_delay: 5000,
      env: {
        // 0.0.0.0 so the dashboard is reachable from other machines on
        // the LAN (jstation, jpc) — useful when working remotely from JLAP.
        HOST: "0.0.0.0",
        PORT: "4747",
        CLAUDE_PROJECTS_DIRS:
          "jlap=/home/dvids/.claude/projects" +
          ":jstation=/home/dvids/.cache/token-dashboard/remote/jstation/projects",
      },
    },
  ],
};
