#!/bin/bash
# ============================================================
#  KnowledgeDiver 一键部署脚本 (Ubuntu 20.04+)
#  用法: sudo bash deploy/deploy.sh [域名]
# ============================================================
set -e

DOMAIN="${1:-knowledgediver.cloud}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================"
echo "  KnowledgeDiver 生产部署"
echo "  域名: $DOMAIN"
echo "  目录: $APP_DIR"
echo "========================================"

# ── 1. 系统依赖 ──────────────────────────────────────────
echo "[1/6] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx python3-venv python3-pip

# ── 2. Python 虚拟环境 + 依赖 ────────────────────────────
echo "[2/6] 安装 Python 依赖..."
if [ ! -d "$APP_DIR/.venv" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ── 3. 前端构建 ──────────────────────────────────────────
echo "[3/6] 构建前端..."
cd "$APP_DIR/frontend"
npm install --silent
npm run build

# ── 4. Nginx ─────────────────────────────────────────────
echo "[4/6] 配置 Nginx..."

cat > /etc/nginx/sites-available/knowledgediver << NGINXEOF
# HTTP → HTTPS 重定向（无证书时直接服务）
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;

    location / {
        root $APP_DIR/frontend/dist;
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        client_max_body_size 25m;
        proxy_buffering off;
        proxy_read_timeout 86400;
    }
}
NGINXEOF

# 如果已有 SSL 证书，追加 HTTPS server block
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    cat >> /etc/nginx/sites-available/knowledgediver << NGINXSSL

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN www.$DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        root $APP_DIR/frontend/dist;
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        client_max_body_size 25m;
        proxy_buffering off;
        proxy_read_timeout 86400;
    }
}
NGINXSSL
fi

ln -sf /etc/nginx/sites-available/knowledgediver /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
echo "  Nginx 配置完成"

# ── 5. SSL 证书 ──────────────────────────────────────────
echo "[5/6] 申请 SSL 证书..."
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" \
        --non-interactive --agree-tos --email "admin@$DOMAIN" \
        --redirect 2>/dev/null || echo "  SSL 申请跳过（可能域名未解析）"
else
    echo "  SSL 证书已存在，跳过"
fi

# ── 6. Systemd 服务 ──────────────────────────────────────
echo "[6/6] 配置 Systemd 服务..."

cat > /etc/systemd/system/knowledgediver.service << EOF
[Unit]
Description=KnowledgeDiver Backend
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment="PYTHONPATH=$APP_DIR"
Environment="HF_ENDPOINT=https://hf-mirror.com"
Environment="PLAYWRIGHT_BROWSERS_PATH=$APP_DIR/.playwright"
ExecStart=$APP_DIR/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now knowledgediver

echo
echo "========================================"
echo "  部署完成！"
echo "========================================"
echo
echo "  访问: https://$DOMAIN"
echo
echo "  常用命令:"
echo "    查看状态:   systemctl status knowledgediver"
echo "    查看日志:   journalctl -u knowledgediver -f"
echo "    更新部署:   systemctl restart knowledgediver knowledgediver-frontend"
echo "    重启后端:   systemctl restart knowledgediver"
echo "    重启前端:   cd $APP_DIR/frontend && npm run build"
echo
echo "  嵌入模型: 未内置权重，首次使用会自动从 HuggingFace 下载 (约 92MB)"
echo "    推荐手动下载到 $APP_DIR/models/bge-small-zh-v1.5:"
echo "      huggingface-cli download BAAI/bge-small-zh-v1.5 --local-dir $APP_DIR/models/bge-small-zh-v1.5"
echo "    或将本地 models/bge-small-zh-v1.5/ 目录 scp 到服务器"
