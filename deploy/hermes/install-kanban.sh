#!/usr/bin/env bash
# Install the DTM AI → Hermes kanban delegation bridge. Run on the server as a sudo-capable user
# (e.g. ross):  sudo bash deploy/hermes/install-kanban.sh
#
# It installs the wrapper ROOT-OWNED to /usr/local/sbin (so `dtm-ai` can't tamper with it) and the
# scoped sudoers entry, then validates with `visudo -c`. Idempotent.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER_SRC="$SRC_DIR/dtm-ai-kanban.sh"
WRAPPER_DST="/usr/local/sbin/dtm-ai-kanban.sh"
SUDOERS_SRC="$SRC_DIR/../sudoers-dtm-ai-kanban.snippet"
SUDOERS_DST="/etc/sudoers.d/dtm-ai-kanban"

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f "$WRAPPER_SRC" ]] || { echo "missing $WRAPPER_SRC"; exit 1; }

install -o root -g root -m 0755 "$WRAPPER_SRC" "$WRAPPER_DST"
echo "installed $WRAPPER_DST (root:root 0755)"

install -o root -g root -m 0440 "$SUDOERS_SRC" "$SUDOERS_DST"
if visudo -c -f "$SUDOERS_DST" >/dev/null; then
  echo "installed $SUDOERS_DST (validated)"
else
  rm -f "$SUDOERS_DST"; echo "sudoers validation FAILED — removed, no change made"; exit 1
fi

echo
echo "Smoke test (runs as dtm-ai, should print a JSON task object):"
echo "  sudo -u dtm-ai sudo -n $WRAPPER_DST create --title 'install smoke test' --created-by dtm-ai:install"
echo "Then archive it from the board or with:"
echo "  sudo docker exec hermes /opt/hermes/bin/hermes kanban list"
