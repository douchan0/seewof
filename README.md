# 希沃教室电脑使用权限控制与远程管理系统

> 教室端 (Windows 10) + 管理端 (Linux Ubuntu) 双端方案.
> 优先用 **教师 U 盘** 解锁, 其次 **上课时段**, 最后 **远程手动解锁** (辅助).
> 抵御任务管理器、安全模式、系统时间篡改等绕过手段.

---

## 📁 目录

```
seewof/
├── common/                # 共享协议 (HMAC, RSA, time sync, events)
├── agent/                 # 教室端 (Windows 10)
│   ├── main.py            # 主服务
│   ├── watchdog.py        # 守护进程
│   ├── service.py         # Windows Service 封装
│   ├── config.py          # 配置加载
│   ├── usbmgr.py          # U 盘检测 + 签名验证
│   ├── input_blocker.py   # LL 钩子拦截 + 触摸禁用
│   ├── overlay.py         # 遮罩 UI (PyQt5)
│   ├── comm.py            # 与管理端 HTTP 通信
│   ├── protection.py      # 注册表策略 (禁任务管理器等)
│   ├── state.py           # 决策状态机
│   ├── logger.py
│   └── usbdiag.py         # 诊断工具: 读取 U 盘硬件序列号
├── server/                # 管理端 (Linux)
│   ├── app/               # FastAPI 后端
│   │   ├── main.py
│   │   ├── models.py / schemas.py / db.py / auth.py
│   │   └── routers/       # auth, classrooms, schedules, unlock, usbs, logs, agent_api
│   ├── tools/keygen.py    # 离线 U 盘签发 CLI
│   ├── web/               # 前端 (Vue 3 + Element Plus)
│   │   ├── index.html
│   │   └── assets/        # 需下载 vue/element-plus/axios
│   └── requirements.txt
├── deploy/
│   ├── agent/             # 教室端安装脚本
│   └── server/            # 管理端 systemd + nginx
├── tests/                 # 单元测试 (29 个)
└── docs/                  # 详细文档
```

## 🚀 快速开始

### 1. 管理端 (Linux Ubuntu 20.04/22.04)

```bash
git clone <repo> seewof && cd seewof
python3 -m venv venv && source venv/bin/activate
pip install -r server/requirements.txt
# 下载前端静态资源
cd server/web/assets && \
  curl -L -o vue.global.prod.js https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js && \
  curl -L -o element-plus.css https://unpkg.com/element-plus@2.8.6/dist/index.css && \
  curl -L -o element-plus.full.min.js https://unpkg.com/element-plus@2.8.6/dist/index.full.min.js && \
  curl -L -o axios.min.js https://unpkg.com/axios@1.7.7/dist/axios.min.js && \
  cd ../../..
# 生成 TLS 自签证书 (或使用 mkcert / Let's Encrypt)
openssl req -x509 -newkey rsa:4096 -nodes -keyout data/server.key \
  -out data/server.crt -days 3650 \
  -subj "/CN=seewof-server" -addext "subjectAltName=IP:192.168.1.10"
# 生成管理端 RSA 密钥对
SEEWOF_BOOTSTRAP_TOKEN=$(openssl rand -hex 16) python -m server.app.main &  # 后台跑
# 首次创建管理员
curl -X POST http://127.0.0.1:8000/api/v1/auth/bootstrap_admin \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$SEEWOF_BOOTSTRAP_TOKEN\",\"username\":\"admin\",\"password\":\"yourPass123\"}"
```

打开浏览器访问 `https://<管理端IP>:8443/` 即可.

### 2. 教室端 (Windows 10)

参见 [`docs/agent-install.md`](docs/agent-install.md).

简要:

```powershell
cd C:\ProgramData\SeewofAgent
# 复制 agent/ 目录所有 .py, common/ 目录
pip install -r requirements-agent.txt
# 生成 agent.json (从 agent.example.json 复制并修改)
copy agent.example.json agent.json
# 写入 public.pem (从管理端下载: GET /api/v1/usb/public_key)
# 安装 Windows 服务
python -m agent.service install
python -m agent.service start
# 安装 watchdog 计划任务
schtasks /Create /SC ONSTART /TN "SeewofWatchdog" /TR "python -m agent.watchdog --config C:\ProgramData\SeewofAgent\agent.json" /RU SYSTEM
```

## 🧪 测试

```bash
PYTHONPATH=. python -m unittest tests.test_crypto tests.test_state tests.test_config
```

`29 个测试全部通过 ✅`

## 🔐 决策优先级

```
┌────────────────────────────────────────────────────┐
│ 1. 教师 U 盘 (最高)  →  立即解锁                    │
│    └ 拔出 + 5 秒延迟                                 │
│ 2. 上课时段 (中等)  →  自动解锁                      │
│ 3. 远程手动解锁 (辅助)  →  倒计时解锁                │
│ 4. 任何时候: 时钟同步失败 ×3 → 强制锁定              │
└────────────────────────────────────────────────────┘
```

## 🛡️ 防绕过

| 攻击 | 防御 |
|------|------|
| 任务管理器 | 注册表 `DisableTaskMgr=1` (应用/撤销式) |
| 强制重启 | Windows 服务 + watchdog 心跳 |
| 安全模式 | 服务注册为 "auto start" + 可选 `SafeBoot` |
| 改本地时间 | **不**使用本地时钟; 连续 3 次同步失败 → 锁定 |
| 拔网线 | 状态降级但不自动解锁; 恢复后重新同步 |
| 辅助功能 (Win+U 等) | LL 钩子 + 注册表 `NoEaseOfAccess=1` |
| 屏幕键盘/讲述人/粘滞键 | 钩子 + 注册表策略 |
| 卸载/退出 | bcrypt 管理员密码 + 服务自我保护 |

## 📋 API 端点 (管理端)

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/v1/auth/login` | POST | 教师登录 |
| `/api/v1/auth/me` | GET | 当前用户 |
| `/api/v1/classrooms` | GET/POST | 教室列表/添加 |
| `/api/v1/classrooms/{id}` | GET/PUT/DELETE | 教室详情/编辑/删除 |
| `/api/v1/classrooms/{id}/schedule` | GET/POST | 时间表 |
| `/api/v1/classrooms/{id}/unlock` | POST | 下发远程解锁 |
| `/api/v1/classrooms/{id}/unlock/active` | GET | 列出活动解锁 |
| `/api/v1/usb` | GET/POST | U 盘授权列表/添加 |
| `/api/v1/usb/{id}/sign` | POST | 签发 teacher.key |
| `/api/v1/usb/public_key` | GET | 下载公钥 (供教室端) |
| `/api/v1/logs` | GET | 查询事件日志 |
| `/api/v1/agent/time` | GET | 时间同步 (教室端) |
| `/api/v1/agent/poll` | GET | 拉取时段+指令 (教室端) |
| `/api/v1/agent/event` | POST | 事件上报 (教室端) |
| `/api/v1/agent/log_batch` | POST | 批量日志 (教室端) |
| `/api/v1/agent/heartbeat` | POST | 心跳+状态 (教室端) |

## 📜 协议

- **HMAC-SHA256 签名**: 教室端 ↔ 管理端 通信, 头 `X-Seewof-{Signature,Timestamp,Nonce}`
- **RSA-PSS 签名**: U 盘 `teacher.key` 文件 (`serial + teacher_id + teacher_name + issued_at + expires_at + nonce`)
- **时间同步**: 教室端使用 `SmoothedClock` 中位数滤波, 连续 3 次失败 → 强制锁定

## 🔑 默认配置 (`agent/agent.example.json`)

```json
{
  "classroom_id": "ROOM-101",
  "server": {
    "base_url": "https://192.168.1.10:8443",
    "psk": "REPLACE_WITH_48_BYTE_BASE64_PSK",
    "verify_tls": false
  },
  "usb": {
    "serial_via": "wmi",
    "teacher_key_filename": "teacher.key",
    "public_key_path": "public.pem"
  },
  "lock": {
    "block_keyboard": true,
    "block_mouse": true,
    "block_touch": true,
    "block_accessibility": true,
    "usb_remove_grace_sec": 5
  }
}
```

## 📦 教室端打包 (PyInstaller)

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole \
  --hidden-import=PyQt5 \
  --add-data "common;common" \
  --add-data "agent/agent.example.json;." \
  --name SeewofAgent \
  agent/main.py
```

将 `dist/SeewofAgent.exe` + `agent.json` + `public.pem` 拷贝到教室电脑部署.

## 📄 文档

- [`docs/architecture.md`](docs/architecture.md) - 架构详解
- [`docs/agent-install.md`](docs/agent-install.md) - 教室端安装
- [`docs/server-install.md`](docs/server-install.md) - 管理端部署
- [`docs/usb-keygen.md`](docs/usb-keygen.md) - U 盘签发流程
- [`docs/faq.md`](docs/faq.md) - 常见问题

## ⚖️ 许可

本项目为教学辅助工具, 仅供合法校园管理用途.
