#!/usr/bin/env bash
set -euo pipefail

echo "=== Recipe App: VPS Setup ==="

# Install system dependencies
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv nginx

# Create app user
if ! id -u recipe-app >/dev/null 2>&1; then
    sudo useradd -r -s /bin/false recipe-app
fi

# Create app directory
sudo mkdir -p /srv/recipe-app
sudo chown -R $USER:$USER /srv/recipe-app

# Copy files (run from repo root)
cp -r app data requirements.txt /srv/recipe-app/

# Python venv
python3.12 -m venv /srv/recipe-app/venv
/srv/recipe-app/venv/bin/pip install -r /srv/recipe-app/requirements.txt

# Environment file (edit this!)
if [ ! -f /srv/recipe-app/.env ]; then
    cp .env.example /srv/recipe-app/.env
    echo "⚠️  Edit /srv/recipe-app/.env with your secrets before starting!"
fi

# systemd
sudo cp deploy/recipe-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable recipe-app

# nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/recipe-app
sudo ln -sf /etc/nginx/sites-available/recipe-app /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# Start
sudo systemctl start recipe-app

echo "=== Done! ==="
echo "Check status: sudo systemctl status recipe-app"
echo "Check logs: sudo journalctl -u recipe-app -f"
