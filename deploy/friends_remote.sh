#!/usr/bin/env bash
# Runs ON THE SERVER as root (called by deploy/setup_friends.sh).
# Usage: friends_remote.sh LOOPHOLE_HOSTNAME APP_USER APP_DIR
NAME="${1:?usage: friends_remote.sh LOOPHOLE_HOSTNAME APP_USER APP_DIR}"
APP_USER="${2:?missing APP_USER}"
APP="${3:?missing APP_DIR}"
set -euo pipefail
HOME_DIR="$(getent passwd "$APP_USER" | cut -d: -f6)"
DATA="$HOME_DIR/studyhub-friends/data"
ORIGIN="https://$NAME.loophole.site"
install -d -o "$APP_USER" -g "$APP_USER" -m 700 "$HOME_DIR/studyhub-friends" "$DATA"

# config: share the college archive path and the assistant's model with your personal site
python3 - "$APP/data/config.json" "$DATA/config.json" "$DATA" "$ORIGIN" <<'PY'
import json, os, sys
personal, target, data, origin = sys.argv[1:]
src = json.load(open(personal)) if os.path.exists(personal) else {}
cfg = json.load(open(target)) if os.path.exists(target) else {}
for key in ('ARCHIVE_DATA_DIR', 'STUDY_LLM_BASE_URL', 'STUDY_LLM_MODEL', 'STUDY_LLM_API_KEY',
            'STUDY_LLM_FALLBACK_BASE_URL', 'STUDY_LLM_FALLBACK_MODEL', 'STUDY_LLM_FALLBACK_API_KEY'):
    if key in src and key not in cfg:
        cfg[key] = src[key]
cfg.update({'STUDY_DATA_DIR': data, 'STUDY_MULTIUSER': '1', 'STUDY_PUBLIC_ORIGIN': origin})
for key, value in {'STUDY_SIGNUP': 'open', 'STUDY_MAX_USERS': '50', 'STUDY_USER_QUOTA_MB': '1024',
                   'STUDY_ASSISTANT_DAILY': '40'}.items():
    cfg.setdefault(key, value)
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as out:
    json.dump(cfg, out, indent=2)
print('   config:', {k: ('***' if 'KEY' in k else v) for k, v in cfg.items()})
PY
chown "$APP_USER:$APP_USER" "$DATA/config.json"

cat > /etc/systemd/system/studyhub-friends.service <<UNIT
[Unit]
Description=StudyHub for friends (multi-user)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP
Environment=STUDY_CONFIG_FILE=$DATA/config.json
Environment=STUDY_SECURE_COOKIE=true
ExecStart=$APP/.venv/bin/python -m studyhub serve --host 127.0.0.1 --port 8092
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$DATA

[Install]
WantedBy=multi-user.target
UNIT

KEY=/var/lib/loophole/.loophole/.ssh/studyhub_friends_rsa
if [ ! -f "$KEY" ]; then
  sudo -u loophole ssh-keygen -q -t rsa -b 4096 -N '' -f "$KEY"
fi
cat > /etc/systemd/system/studyhub-friends-tunnel.service <<UNIT
[Unit]
Description=HTTPS address for StudyHub friends site
After=network-online.target studyhub-friends.service
Wants=network-online.target
Requires=studyhub-friends.service

[Service]
Type=simple
User=loophole
WorkingDirectory=/var/lib/loophole
ExecStart=/usr/local/bin/loophole http 8092 --hostname $NAME --identity-file $KEY --disable-old-ciphers --disable-proxy-error-page
KillSignal=SIGINT
TimeoutStopSec=10
Restart=on-failure
RestartSec=15
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now studyhub-friends.service
sleep 3
systemctl enable --now studyhub-friends-tunnel.service
sleep 12
echo "==> Status"
echo -n "app:    "; systemctl is-active studyhub-friends.service
echo -n "tunnel: "; systemctl is-active studyhub-friends-tunnel.service
echo -n "local:  "; curl -s http://127.0.0.1:8092/health; echo
echo -n "public: "; curl -s -m 20 "$ORIGIN/health" || echo "(not reachable yet - see: sudo journalctl -u studyhub-friends-tunnel -n 30)"; echo
echo "Friends site: $ORIGIN"
