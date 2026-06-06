#!/usr/bin/env bash
# =============================================================
#  Seewof Server - MacBook 本地开发启动脚本
#
#  与 deploy/server/deploy.sh (Linux 生产) 不同, 这里:
#  - 监听 127.0.0.1 (不暴露公网)
#  - 使用自签证书 (HTTPS 必需, 教室端配置 verify_tls=false)
#  - 数据库/日志在项目本地 dev-data/
#  - CORS 全部开放 (仅本地)
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

echo "============================================"
echo "  Seewof Server (MacBook 开发模式)"
echo "============================================"
echo "  DB:        $SEEWOF_DB"
echo "  Log:       $SEEWOF_LOG_DIR"
echo "  HTTP:      http://127.0.0.1:8000"
echo "  Bootstrap: $SEEWOF_BOOTSTRAP_TOKEN"
echo "============================================"

# 用 venv
if [ -d venv ]; then
    source venv/bin/activate
fi

exec python -m uvicorn server.app.main:app \
    --host 127.0.0.1 --port 8000 --log-level info
