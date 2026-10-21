#!/usr/bin/env bash
# One-time setup of the multi-user StudyHub site for friends, on the home server.
# Same code as your personal site, separate data, separate port (8092) and its own HTTPS address.
# Needs loophole (https://loophole.cloud) installed on the server with a "loophole" system user.
# Usage (from the laptop):   bash deploy/setup_friends.sh [loophole-hostname]
set -euo pipefail
source "$(dirname "$0")/load_env.sh"
NAME="${1:-$FRIENDS_NAME}"          # public address: https://$NAME.loophole.site
SRC="$(cd "$(dirname "$0")/.." && pwd)/"

echo "==> Copying code"
rsync -a --exclude _backup --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude docs --exclude deploy/deploy.env "$SRC" "$SERVER:$REMOTE/studyhub/"

echo "==> Setting up the friends site on the server (sudo password may be asked)"
ssh -t "$SERVER" "sudo bash $REMOTE/studyhub/deploy/friends_remote.sh '$NAME' '$APP_USER' '$REMOTE'"
