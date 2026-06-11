#!/usr/bin/env bash
set -euo pipefail

apt-get update

packages=(ffmpeg python3.12-venv python3-pip)
if ! command -v caddy >/dev/null 2>&1; then
    packages+=(nginx)
fi
apt-get install -y "${packages[@]}"

id -u wmesm >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin wmesm

mkdir -p /opt/wmesm/backend /opt/wmesm/frontend/dist /var/lib/wmesm/data /var/lib/wmesm/models
chown -R wmesm:wmesm /opt/wmesm /var/lib/wmesm

sudo -u wmesm python3.12 -m venv /opt/wmesm/backend/.venv
sudo -u wmesm /opt/wmesm/backend/.venv/bin/python -m pip install --upgrade pip
sudo -u wmesm /opt/wmesm/backend/.venv/bin/python -m pip install -e /opt/wmesm/backend

systemctl daemon-reload
systemctl enable wmesm
systemctl restart wmesm

if command -v caddy >/dev/null 2>&1; then
    install -m 0644 /opt/wmesm/deploy/Caddyfile /etc/caddy/Caddyfile
    systemctl disable --now nginx >/dev/null 2>&1 || true
    caddy fmt --overwrite /etc/caddy/Caddyfile
    caddy validate --config /etc/caddy/Caddyfile
    systemctl reload caddy || systemctl restart caddy
else
    install -m 0644 /opt/wmesm/deploy/nginx-wmesm.conf /etc/nginx/sites-available/wmesm
    rm -f /etc/nginx/sites-enabled/default
    ln -sfn /etc/nginx/sites-available/wmesm /etc/nginx/sites-enabled/wmesm
    nginx -t
    systemctl reload nginx || systemctl restart nginx
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    ufw allow 80/tcp
fi
