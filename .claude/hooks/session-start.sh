#!/bin/bash
set -euo pipefail

# Only run in Claude Code remote (web) sessions
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Verify Python 3.8+ is available
python3 -c "
import sys
if sys.version_info < (3, 8):
    print('ERROR: Python 3.8+ required, found', sys.version)
    sys.exit(1)
print('Python', sys.version, 'OK')
"

# Make the project importable without install
echo "export PYTHONPATH=\"${CLAUDE_PROJECT_DIR}:${PYTHONPATH:-}\"" >> "$CLAUDE_ENV_FILE"

echo "Token Dashboard environment ready."
