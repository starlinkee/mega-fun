#!/bin/bash
# Run this ONCE on your VPS to set everything up
# Usage: bash setup_vps.sh <github-repo-url>

set -e

REPO_URL=$1
if [ -z "$REPO_URL" ]; then
    echo "Usage: bash setup_vps.sh <github-repo-url>"
    echo "Example: bash setup_vps.sh https://github.com/YOUR_USER/mega-fun.git"
    exit 1
fi

echo "=== Updating system ==="
apt update && apt upgrade -y

echo "=== Installing Python, nginx, git ==="
apt install -y python3 python3-venv python3-pip nginx git

echo "=== Cloning repo ==="
git clone "$REPO_URL" /opt/mega-fun
cd /opt/mega-fun

echo "=== Setting up Python venv ==="
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "=== Initializing database ==="
python3 -c "from init_db import init_db; init_db()"

echo "=== Creating systemd service ==="
cat > /etc/systemd/system/mega-fun.service << 'EOF'
[Unit]
Description=Mega Fun Flask App
After=network.target

[Service]
User=root
WorkingDirectory=/opt/mega-fun
ExecStart=/opt/mega-fun/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5000 run:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mega-fun
systemctl start mega-fun

echo "=== Configuring nginx ==="
cat > /etc/nginx/sites-available/mega-fun << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static {
        alias /opt/mega-fun/static;
    }
}
EOF

ln -sf /etc/nginx/sites-available/mega-fun /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "=== Installing rclone (for Google Drive backups) ==="
curl https://rclone.org/install.sh | bash

echo ""
echo "=== DONE! ==="
echo "App is running on http://$(curl -s ifconfig.me)"
echo ""
echo "=== NEXT STEP: Configure Google Drive backups ==="
echo "Run: rclone config"
echo "  - New remote, name: gdrive"
echo "  - Type: drive (Google Drive)"
echo "  - Leave Client ID and Secret empty"
echo "  - Scope: 1 (full access)"
echo "  - Auto config: y (opens browser for Google login)"
echo "Then test: bash /opt/mega-fun/scripts/backup_db.sh"
