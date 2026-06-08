#!/usr/bin/env bash
# =============================================================
#  Seewof Server - 局域网部署 (Windows VM 联调专用)
#
#  与 run_server_mac.sh 区别: 监听 0.0.0.0 而不是 127.0.0.1
#  这样 Windows 教室端可以通过 MacBook 的局域网 IP 访问.
#
#  使用:
#    bash scripts/run_server_lan.sh
#
#  查看本机 IP:
#    ipconfig getifaddr en0
#
#  Windows 教室端配置:
#    base_url = http://192.168.1.5:8000
#    verify_tls = false
# =============================================================
set -e
cd "$(dirname "$0")/.."

# 配置
export SEEWOF_DB="$PWD/dev-data/seewof.db"
export SEEWOF_LOG_DIR="$PWD/dev-data/logs"
export SEEWOF_JWT_SECRET="dev-jwt-secret-not-for-production-$(date +%s)"
export SEEWOF_BOOTSTRAP_TOKEN="dev-bootstrap-token-please-change-in-prod"
export SEEWOF_CORS_ORIGINS="*"

mkdir -p "$PWD/dev-data/logs"

# 拿本机 IP
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "UNKNOWN")

echo "============================================"
echo "  Seewof Server (LAN 联调模式)"
echo "============================================"
echo "  DB:        $SEEWOF_DB"
echo "  Log:       $SEEWOF_LOG_DIR"
echo "  HTTP:      0.0.0.0:8000  (LAN 监听)"
echo "  本机 IP:   $LAN_IP"
echo "  访问 URL:  http://$LAN_IP:8000"
echo "  Bootstrap: $SEEWOF_BOOTSTRAP_TOKEN"
echo "============================================"
echo "  Windows 教室端 agent.json 需配:"
echo "    base_url = http://$LAN_IP:8000"
echo "    verify_tls = false"
echo "============================================"

# 用 venv
if [ -d venv ]; then
    source venv/bin/activate
fi

exec python -m uvicorn server.app.main:app \
    --host 0.0.0.0 --port 8000 --log-level info
