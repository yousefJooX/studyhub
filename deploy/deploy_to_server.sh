#!/usr/bin/env bash
# Deploy StudyHub from this laptop to the home server, optionally create the MCP/API token,
# restart the service and check that the site + MCP endpoint answer.
# Usage:  bash deploy/deploy_to_server.sh            (deploy + restart)
#         bash deploy/deploy_to_server.sh --token    (deploy + new MCP/API token + restart)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/"
source "$(dirname "$0")/load_env.sh"

echo "==> Copying code to $SERVER"
rsync -av --exclude _backup --exclude __pycache__ --exclude '*.pyc' --exclude .git --exclude docs --exclude deploy/deploy.env \
  "$SRC" "$SERVER:$REMOTE/studyhub/"

if [[ -n "$OWNER_NAME" ]]; then
  ssh "$SERVER" "python3 -c 'import json,sys; p=sys.argv[1]; c=json.load(open(p)); c.setdefault(\"STUDY_OWNER_NAME\", sys.argv[2]); json.dump(c, open(p,\"w\"), indent=2, ensure_ascii=False)' $REMOTE/data/config.json '$OWNER_NAME'"
fi

if [[ "${1:-}" == "--token" ]]; then
  echo "==> Creating a new MCP/API token (the old one stops working)"
  URL=$(ssh "$SERVER" "cd $REMOTE && STUDY_CONFIG_FILE=$REMOTE/data/config.json STUDY_PUBLIC_ORIGIN=$ORIGIN STUDY_TOKEN_URL_ONLY=1 .venv/bin/python -m studyhub token")
  mkdir -p "$HOME/.config/studyhub"
  ( umask 077; printf '%s\n' "$URL" > "$HOME/.config/studyhub/mcp-connector-url.txt" )
  if command -v wl-copy >/dev/null; then printf '%s' "$URL" | wl-copy; echo "    Connector URL copied to the clipboard."; fi
  echo
  echo "    Claude connector URL (private, do not share or paste in chats):"
  echo
  echo "    $URL"
  echo
  echo "    Also saved to ~/.config/studyhub/mcp-connector-url.txt"
  echo "    Bearer token = the part after /mcp/ in that URL."
fi

echo "==> Restarting the service (sudo password may be asked)"
ssh -t "$SERVER" "bash -c \"sudo systemctl restart $SERVICE.service && sleep 2 && systemctl is-active $SERVICE.service; if systemctl cat studyhub-friends.service >/dev/null 2>&1; then sudo systemctl restart studyhub-friends.service && sleep 2 && echo -n friends-site: && systemctl is-active studyhub-friends.service; fi\""

echo "==> Checking"
ssh "$SERVER" "curl -s http://127.0.0.1:$PORT/health; echo; \
  curl -s -o /dev/null -w 'MCP without token -> %{http_code} (401 expected)\n' -X POST -H 'Content-Type: application/json' \
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}' http://127.0.0.1:$PORT/mcp"
echo "Done. Public site: $ORIGIN"
