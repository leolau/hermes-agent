#!/usr/bin/env bash
# Launch the agent-home production server on loopback only. Caddy fronts it.
# Requires `npm run build` to have been run first (see deploy/DEPLOY.md).
set -euo pipefail

cd "$(dirname "$0")/.."

# Load nvm if node/npm isn't already on PATH (systemd's PATH is minimal).
if ! command -v npm >/dev/null 2>&1; then
  for nvm_sh in "$HOME/.nvm/nvm.sh" /usr/local/nvm/nvm.sh /opt/nvm/nvm.sh; do
    if [ -s "$nvm_sh" ]; then
      # shellcheck disable=SC1090
      . "$nvm_sh"
      break
    fi
  done
fi

# PORT comes from the systemd EnvironmentFile (agent-home.env). Default 3100
# because :3000 on the prod box is the WhatsApp bridge.
exec npm run start -- -H 127.0.0.1 -p "${PORT:-3100}"
