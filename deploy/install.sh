#!/usr/bin/env bash
# On the confirmed VPS: sudo bash /path/to/unpacked-release/deploy/install.sh
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_ID="${CQA_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ "$RELEASE_ID" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 1
command -v nginx >/dev/null
command -v python3 >/dev/null
test -f /etc/letsencrypt/live/ortaresearch.com/fullchain.pem
test -f /etc/letsencrypt/live/ortaresearch.com/privkey.pem
if [[ -e /srv/cqa/releases/"$RELEASE_ID" ]]; then
    echo 'Release already exists; choose a new CQA_RELEASE_ID.' >&2
    exit 1
fi
id cqa >/dev/null 2>&1 || useradd --system --home /srv/cqa --shell /usr/sbin/nologin cqa
install -d -m 755 /srv/cqa/releases
install -d -o cqa -g cqa -m 700 /srv/cqa/shared
RELEASE="/srv/cqa/releases/$RELEASE_ID"
mkdir "$RELEASE"
cp -a "$SOURCE_DIR/." "$RELEASE/"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install -r "$RELEASE/requirements.lock.txt"
cd "$RELEASE"
"$RELEASE/.venv/bin/python" -m pytest -q
# No .env, developer databases, imported runs, or organizer URLs are shipped.
test ! -e "$RELEASE/.env"
chown -R root:root "$RELEASE"
chown -R cqa:cqa "$RELEASE/data"
BACKUP="/srv/cqa/shared/before-$RELEASE_ID"
mkdir -m 700 "$BACKUP"
install -m 600 /etc/nginx/sites-available/ortaresearch.conf "$BACKUP/ortaresearch.conf"
if [[ -L /srv/cqa/current ]]; then
    readlink -f /srv/cqa/current > "$BACKUP/previous-release"
else
    : > "$BACKUP/previous-release"
fi
if [[ -e /etc/systemd/system/cqa.service ]]; then
    cp -a /etc/systemd/system/cqa.service "$BACKUP/cqa.service"
fi
systemctl is-active ortaresearch.service > "$BACKUP/old-service-state" || true
restore() {
    install -m 644 "$BACKUP/ortaresearch.conf" /etc/nginx/sites-available/ortaresearch.conf
    nginx -t && systemctl reload nginx
    PREVIOUS="$(cat "$BACKUP/previous-release")"
    if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
        ln -sfn "$PREVIOUS" /srv/cqa/current
        if [[ -f "$BACKUP/cqa.service" ]]; then
            cp -a "$BACKUP/cqa.service" /etc/systemd/system/cqa.service
            systemctl daemon-reload
        fi
        systemctl restart cqa.service
    else
        systemctl stop cqa.service
    fi
}
trap restore ERR
ln -sfn "$RELEASE" /srv/cqa/current
install -m 644 "$RELEASE/deploy/cqa.service" /etc/systemd/system/cqa.service
systemctl daemon-reload
systemctl enable cqa.service
systemctl restart cqa.service
for attempt in {1..30}; do
    if curl --fail --silent http://127.0.0.1:8100/api/state > "$BACKUP/health.json"; then break; fi
    sleep 1
done
python3 - "$BACKUP/health.json" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
assert state['credential_mode'] == 'per_request' and state['has_key'] is False
assert state['datasets'] and state['active_checklist']
PY
install -m 644 "$RELEASE/deploy/ortaresearch.conf" /etc/nginx/sites-available/ortaresearch.conf
ln -sfn /etc/nginx/sites-available/ortaresearch.conf /etc/nginx/sites-enabled/ortaresearch.conf
nginx -t
systemctl reload nginx
curl --fail --silent --resolve ortaresearch.com:443:127.0.0.1 https://ortaresearch.com/api/state | python3 -c 'import json,sys; s=json.load(sys.stdin); assert s["credential_mode"] == "per_request" and not s["has_key"]'
trap - ERR
# The previous app and its database remain on disk for rollback.
if systemctl cat ortaresearch.service >/dev/null 2>&1; then
    systemctl disable --now ortaresearch.service
fi
echo "CQA is live at https://ortaresearch.com; rollback backup: $BACKUP"
