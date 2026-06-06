# 管理端 (Linux Ubuntu 20.04/22.04) 部署文档

## 0. 环境

- Ubuntu 20.04 LTS 或 22.04 LTS
- Python 3.10+
- 公网 IP 或可路由的内网 IP
- 域名 (可选, 用于 HTTPS 证书)

## 1. 安装依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential \
  libssl-dev libffi-dev nginx certbot python3-certbot-nginx
```

## 2. 部署代码

```bash
sudo mkdir -p /opt/seewof
sudo chown $USER:$USER /opt/seewof
cd /opt/seewof
git clone <repo> .
python3 -m venv venv
source venv/bin/activate
pip install -r server/requirements.txt
```

## 3. 初始化

```bash
# 创建数据目录
mkdir -p data/web

# 下载前端静态资源 (公网环境)
cd server/web/assets
curl -L -o vue.global.prod.js https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js
curl -L -o element-plus.css https://unpkg.com/element-plus@2.8.6/dist/index.css
curl -L -o element-plus.full.min.js https://unpkg.com/element-plus@2.8.6/dist/index.full.min.js
curl -L -o axios.min.js https://unpkg.com/axios@1.7.7/dist/axios.min.js
cd /opt/seewof

# 生成 TLS 证书 (内网环境用自签)
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout data/server.key \
  -out data/server.crt \
  -days 3650 \
  -subj "/CN=seewof-server" \
  -addext "subjectAltName=IP:192.168.1.10,DNS:seewof.local"

# 生产环境用 Let's Encrypt
sudo certbot certonly --nginx -d seewof.example.com
```

## 4. 配置 systemd

复制 `deploy/server/seewof.service` 到 `/etc/systemd/system/`:

```ini
[Unit]
Description=Seewof Control Server
After=network.target

[Service]
Type=simple
User=seewof
Group=seewof
WorkingDirectory=/opt/seewof
Environment="SEEWOF_DB=/opt/seewof/data/seewof.db"
Environment="SEEWOF_JWT_SECRET=<openssl rand -hex 32>"
Environment="SEEWOF_BOOTSTRAP_TOKEN=<openssl rand -hex 16>"
Environment="SEEWOF_CORS_ORIGINS=https://seewof.example.com"
ExecStart=/opt/seewof/venv/bin/uvicorn server.app.main:app \
  --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin seewof
sudo chown -R seewof:seewof /opt/seewof
sudo systemctl daemon-reload
sudo systemctl enable --now seewof
sudo systemctl status seewof
```

## 5. Nginx 反向代理 + TLS

`/etc/nginx/sites-available/seewof`:

```nginx
server {
    listen 8443 ssl http2;
    server_name seewof.example.com;

    ssl_certificate     /etc/letsencrypt/live/seewof.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/seewof.example.com/privkey.pem;

    client_max_body_size 4m;

    # 教室端 API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # 静态前端
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/seewof /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 6. 初始化管理员

管理端首次启动后, 用 bootstrap 接口创建管理员 (已用 systemd 设置 `SEEWOF_BOOTSTRAP_TOKEN`):

```bash
curl -X POST https://seewof.example.com:8443/api/v1/auth/bootstrap_admin \
  -H 'Content-Type: application/json' \
  -d '{
    "token": "<SEEWOF_BOOTSTRAP_TOKEN 值>",
    "username": "admin",
    "password": "YourStrong!Pass1"
  }'
```

⚠️ 第一次成功创建管理员后, 建议从 systemd 文件中删除 `SEEWOF_BOOTSTRAP_TOKEN` 防止被滥用.

## 7. 添加教室

浏览器打开 `https://seewof.example.com:8443/`, 登录, 依次:

1. **教室设备 → 添加教室**: 填 ID, 名称, IP, PSK
2. **时间表**: 选择教室, 添加周一时段 (8:00-12:00 等)
3. **U 盘授权**: 添加 (先用 Windows 教室端 `python -m agent.usbdiag --drive E` 读出 serial)
4. 下载 `teacher.key` 复制到 U 盘根目录
5. 教室端测试: 启动服务 → 插入 U 盘 → 解锁

## 8. 备份

- 数据库: `data/seewof.db` (SQLite)
- 私钥: `data/private.pem` (核心! 一旦丢失, 已签发的 teacher.key 全部失效)
- 配置: `data/`

建议:

```bash
# 每日凌晨备份
0 2 * * * tar czf /backup/seewof-$(date +\%F).tar.gz /opt/seewof/data
```

## 9. 监控 (可选)

- 健康检查: `GET /api/v1/health` (无需认证)
- 接 Prometheus: 见 `deploy/server/prometheus.yml` 示例
- 接 fail2ban: 5xx 比例异常告警

## 10. 升级

```bash
cd /opt/seewof
git pull
source venv/bin/activate
pip install -r server/requirements.txt
sudo systemctl restart seewof
```

## 11. 常见问题

| 现象 | 原因 / 解决 |
|------|------|
| 前端 502 | uvicorn 未启动 / 端口占用: `ss -tlnp \| grep 8000` |
| 教室端报 401 | PSK 不匹配; 检查 `classrooms.psk` 与 `agent.json` |
| CORS 错误 | 设置 `SEEWOF_CORS_ORIGINS=https://your.domain` 重启 |
| 启动报错 "no such column" | DB schema 旧, 删 `data/seewof.db` 重建 (会丢数据) |
