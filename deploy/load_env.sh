# Sourced by the deploy scripts: reads deploy/deploy.env (your private values).
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy deploy/deploy.env.example to deploy/deploy.env and fill it in." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${SERVER:?set SERVER in deploy.env}" "${REMOTE:?set REMOTE in deploy.env}" "${ORIGIN:?set ORIGIN in deploy.env}"
APP_USER="${APP_USER:-${SERVER%@*}}"
SERVICE="${SERVICE:-studyhub}"
PORT="${PORT:-8091}"
FRIENDS_NAME="${FRIENDS_NAME:-study-friends}"
OWNER_NAME="${OWNER_NAME:-}"
COLLEGE_CODE="${COLLEGE_CODE:-}"
