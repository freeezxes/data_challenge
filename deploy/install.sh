#!/usr/bin/env bash
# On the confirmed VPS: sudo bash /path/to/unpacked-release/deploy/install.sh
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE_ID="${CQA_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ "$RELEASE_ID" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 1
command -v nginx >/dev/null
command -v python3 >/dev/null
command -v curl >/dev/null
TLS_READY=0
if [[ -f /etc/letsencrypt/live/ortaresearch.com/fullchain.pem && -f /etc/letsencrypt/live/ortaresearch.com/privkey.pem ]]; then
    TLS_READY=1
fi
if [[ -e /srv/cqa/releases/"$RELEASE_ID" ]]; then
    echo 'Release already exists; choose a new CQA_RELEASE_ID.' >&2
    exit 1
fi
id cqa >/dev/null 2>&1 || useradd --system --home /srv/cqa --shell /usr/sbin/nologin cqa
install -d -m 755 /srv/cqa/releases
install -d -o cqa -g cqa -m 700 /srv/cqa/shared
install -d -m 755 /var/www/cqa-acme
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
if [[ -e /etc/nginx/sites-available/ortaresearch.conf ]]; then
    install -m 600 /etc/nginx/sites-available/ortaresearch.conf "$BACKUP/ortaresearch.conf"
fi
if [[ -L /srv/cqa/current ]]; then
    readlink -f /srv/cqa/current > "$BACKUP/previous-release"
else
    : > "$BACKUP/previous-release"
fi
if [[ -e /etc/systemd/system/cqa.service ]]; then
    cp -a /etc/systemd/system/cqa.service "$BACKUP/cqa.service"
fi
restore() {
    if [[ -f "$BACKUP/ortaresearch.conf" ]]; then
        install -m 644 "$BACKUP/ortaresearch.conf" /etc/nginx/sites-available/ortaresearch.conf
    else
        rm -f /etc/nginx/sites-enabled/ortaresearch.conf /etc/nginx/sites-available/ortaresearch.conf
    fi
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
if [[ "$TLS_READY" -eq 1 ]]; then
    install -m 644 "$RELEASE/deploy/ortaresearch.conf" /etc/nginx/sites-available/ortaresearch.conf
else
    install -m 644 "$RELEASE/deploy/ortaresearch.bootstrap.conf" /etc/nginx/sites-available/ortaresearch.conf
fi
ln -sfn /etc/nginx/sites-available/ortaresearch.conf /etc/nginx/sites-enabled/ortaresearch.conf
nginx -t
systemctl reload nginx
if [[ "$TLS_READY" -eq 1 ]]; then
    curl --fail --silent --resolve ortaresearch.com:443:127.0.0.1 https://ortaresearch.com/api/state | python3 -c 'import json,sys; s=json.load(sys.stdin); assert s["credential_mode"] == "per_request" and not s["has_key"]'
else
    echo 'Backend ready; HTTP serves only ACME and a maintenance response until TLS is configured.'
fi
trap - ERR
echo "CQA installed (TLS ready: $TLS_READY); rollback backup: $BACKUP"
