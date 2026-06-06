#!/usr/bin/env bash
# ============================================================
#   Seewof Server 一键部署脚本 (Ubuntu 20.04/22.04)
#   需以 root 运行
# ============================================================
set -e

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="/opt/seewof"
SERVICE_NAME="seewof"
USER_NAME="seewof"

# ---------- 颜色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info() { echo -e "${GREEN}[*]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || err "请以 root 运行: sudo bash $0"

# ---------- 1. 系统依赖 ----------
info "安装系统依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential \
    libssl-dev libffi-dev nginx curl

# ---------- 2. 复制代码 ----------
info "复制代码到 $APP_DIR ..."
mkdir -p "$APP_DIR"
rsync -a --delete \
  --exclude='venv' --exclude='data' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.git' \
  "$REPO_DIR/" "$APP_DIR/"

# ---------- 3. Python 虚拟环境 ----------
info "创建 Python 虚拟环境..."
sudo -u $USER_NAME test -d "$APP_DIR/venv" || \
  sudo -u $USER_NAME python3 -m venv "$APP_DIR/venv"
info "安装 Python 依赖..."
sudo -u $USER_NAME "$APP_DIR/venv/bin/pip" install --upgrade pip -q
sudo -u $USER_NAME "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/server/requirements.txt"

# ---------- 4. 数据目录 ----------
info "准备数据目录..."
mkdir -p "$APP_DIR/data/logs" "$APP_DIR/data/web"
chown -R $USER_NAME:$USER_NAME "$APP_DIR/data"

# ---------- 5. 创建系统用户 ----------
if ! id "$USER_NAME" &>/dev/null; then
    info "创建系统用户 $USER_NAME ..."
    useradd -r -s /usr/sbin/nologin -d "$APP_DIR" $USER_NAME
fi
chown -R $USER_NAME:$USER_NAME "$APP_DIR"

# ---------- 6. 下载前端静态资源 ----------
if [ ! -f "$APP_DIR/server/web/assets/vue.global.prod.js" ]; then
    info "下载前端静态资源..."
    cd "$APP_DIR/server/web/assets"
    curl -sSL -o vue.global.prod.js https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js
    curl -sSL -o element-plus.css https://unpkg.com/element-plus@2.8.6/dist/index.css
    curl -sSL -o element-plus.full.min.js https://unpkg.com/element-plus@2.8.6/dist/index.full.min.js
    curl -sSL -o axios.min.js https://unpkg.com/axios@1.7.7/dist/axios.min.js
    cd - >/dev/null
fi

# ---------- 7. 写环境变量 ----------
JWT_SECRET=$(openssl rand -hex 32)
BOOTSTRAP_TOKEN=$(openssl rand -hex 16)
cat > /etc/seewof.env <<EOF
SEEWOF_DB=$APP_DIR/data/seewof.db
SEEWOF_LOG_DIR=$APP_DIR/data/logs
SEEWOF_JWT_SECRET=$JWT_SECRET
SEEWOF_BOOTSTRAP_TOKEN=$BOOTSTRAP_TOKEN
SEEWOF_CORS_ORIGINS=*
EOF
chmod 600 /etc/seewof.env

# ---------- 8. systemd 服务 ----------
info "注册 systemd 服务..."
cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=Seewof Control Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/seewof.env
ExecStart=$APP_DIR/venv/bin/uvicorn server.app.main:app \\
  --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$APP_DIR/data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now $SERVICE_NAME
systemctl status $SERVICE_NAME --no-pager

# ---------- 9. Nginx ----------
if [ -f /etc/nginx/sites-available/seewof ]; then
    info "配置 Nginx..."
    ln -sf /etc/nginx/sites-available/seewof /etc/nginx/sites-enabled/seewof
    nginx -t && systemctl reload nginx
fi

# ---------- 10. 完成 ----------
cat <<EOF

============================================
  Seewof Server 部署完成!
============================================
  服务监听:  http://127.0.0.1:8000
  Nginx:     $(nginx -v 2>&1 | head -1)

  首次创建管理员:
    curl -X POST http://127.0.0.1:8000/api/v1/auth/bootstrap_admin \\
      -H 'Content-Type: application/json' \\
      -d '{
        "token": "$BOOTSTRAP_TOKEN",
        "username": "admin",
        "password": "YourStrong!Pass1"
      }'

  Bootstrap Token 已写入: /etc/seewof.env
  创建管理员后请删除 SEEWOF_BOOTSTRAP_TOKEN 行.

  日志:    journalctl -u $SERVICE_NAME -f
  数据库:  $APP_DIR/data/seewof.db
  私钥:    $APP_DIR/data/private.pem (部署后才会生成)
============================================
EOF
