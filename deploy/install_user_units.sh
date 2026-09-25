#!/usr/bin/env bash
# Installs/refreshes Home Hub's background jobs as systemd USER units of the
# home-hub account (no sudo). Run by .github/workflows/deploy.yml on every
# deploy. One-time prerequisite (root): loginctl enable-linger home-hub
#
# Convention for deploy/systemd/:
#   *.timer                      -> enabled + started
#   *.service with [Install]     -> long-running: enabled + restarted (picks up new code)
#   *.service without [Install]  -> timer-driven oneshot: installed only
set -euo pipefail

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then
  echo "No user systemd for $(id -un): run 'loginctl enable-linger $(id -un)' as root once." >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)/systemd"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"

for unit in "$SRC"/*.service "$SRC"/*.timer; do
  install -m 0644 "$unit" "$DEST/"
done
systemctl --user daemon-reload

for timer in "$SRC"/*.timer; do
  systemctl --user enable --now "$(basename "$timer")"
done
for service in "$SRC"/*.service; do
  if grep -q '^\[Install\]' "$service"; then
    name="$(basename "$service")"
    systemctl --user enable "$name"
    systemctl --user restart "$name"
  fi
done
