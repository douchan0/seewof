# 教室端 (Windows 10) 安装文档

## 0. 前置条件

- Windows 10 专业版/教育版 1809+
- 以**管理员**账号登录安装一次 (之后用标准用户)
- 教室电脑固定 IP, 与管理端互通
- 班级管理软件已安装, 自启动

## 1. 准备 Python 运行环境

推荐使用 embeddable Python, 避免污染系统 Python:

```powershell
# 下载 Python 3.11 embeddable
Invoke-WebRequest -Uri https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip `
  -OutFile python-embed.zip
Expand-Archive python-embed.zip C:\ProgramData\SeewofAgent\python
# 启用 site-packages: 编辑 python311._pth, 取消 #import site 注释
```

或者用普通 Python 3.11 安装包.

## 2. 准备代码

将 `agent/` 与 `common/` 目录连同所有 `.py` 复制到:

```
C:\ProgramData\SeewofAgent\
├── python\                 # 嵌入式 Python
├── common\
├── agent\
│   ├── *.py
│   └── agent.json          # 见第 3 步
├── public.pem              # 从管理端下载
└── logs\                   # 自动创建
```

## 3. 写入配置 `agent.json`

从 `agent/agent.example.json` 复制, 修改:

```json
{
  "classroom_id": "ROOM-101",
  "server": {
    "base_url": "https://192.168.1.10:8443",
    "psk": "<从管理端 /classrooms 复制 psk>",
    "verify_tls": false,
    "ca_cert": ""
  },
  "usb": {
    "serial_via": "wmi",
    "public_key_path": "C:\\ProgramData\\SeewofAgent\\public.pem",
    "bind_drive_letters": ["E", "F", "G", "H", "I", "J"]
  },
  "lock": {
    "block_keyboard": true,
    "block_mouse": true,
    "block_touch": true,
    "block_accessibility": true,
    "usb_remove_grace_sec": 5,
    "schedule_soft_warn_sec": 30,
    "overlay_opacity": 0.55,
    "overlay_message": "上课期间或插入教师 U 盘解锁"
  },
  "protection": {
    "watchdog_interval_sec": 3,
    "service_name": "SeewofAgent"
  },
  "log_dir": "C:\\ProgramData\\SeewofAgent\\logs",
  "data_dir": "C:\\ProgramData\\SeewofAgent\\data"
}
```

校验配置:

```powershell
C:\ProgramData\SeewofAgent\python\python.exe -m agent.main --config agent.json --check
```

应该输出 `config OK`.

## 4. 安装 Python 依赖

```powershell
cd C:\ProgramData\SeewofAgent
.\python\python.exe -m pip install -r requirements-agent.txt
```

依赖:
- `pywin32` (Windows 服务)
- `psutil` (磁盘枚举)
- `WMI` (USB 序列号)
- `PyQt5` (遮罩 UI)
- `cryptography` (RSA 验签)
- `requests` (HTTP)

注意: WMI 1.5.1 仅支持 Python 3.4-3.12, Python 3.13+ 需用 `wmi` 替代 (我们已在代码中做 import).

## 5. 安装 Windows 服务

```powershell
.\python\python.exe -m agent.service install
.\python\python.exe -m agent.service start
```

或直接用脚本 (`deploy/agent/install.bat`):

```bat
@echo off
setlocal
set ROOT=C:\ProgramData\SeewofAgent
set PY=%ROOT%\python\python.exe
set CONFIG=%ROOT%\agent\agent.json

%PY% -m pip install --upgrade pip
%PY% -m pip install -r %ROOT%\requirements-agent.txt

%PY% -m agent.service install
sc config SeewofAgent start= auto
sc start SeewofAgent

rem 注册 watchdog 计划任务
schtasks /Create /SC ONSTART /TN "SeewofWatchdog" /RL HIGHEST /F ^
  /TR "\"%PY%\" -m agent.watchdog --config \"%CONFIG%\""
schtasks /Run /TN "SeewofWatchdog"

echo Install OK
pause
```

## 6. (可选) 开机自动登录

为确保学生不能登出/重启后卡在登录界面, 启用自动登录:

```powershell
# 用 Sysinternals 的 Autologon
Invoke-WebRequest -Uri https://download.sysinternals.com/files/AutoLogon.zip -OutFile al.zip
Expand-Archive al.zip -DestinationPath C:\Tools\AutoLogon
C:\Tools\AutoLogon\Autologon64.exe
```

或用注册表:

```reg
Windows Registry Editor Version 5.00
[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon]
"AutoAdminLogon"="1"
"DefaultUsername"="classroom"
"DefaultPassword"="<password>"
"DefaultDomainName"="."
```

⚠️ 实际生产中应使用 [Microsoft LAPS](https://www.microsoft.com/en-us/download/details.aspx?id=46899) 或域账户托管.

## 7. 防火墙

教室端不需要入站, 仅允许出站到管理端:

```powershell
New-NetFirewallRule -DisplayName "Seewof Agent to Server" `
  -Direction Outbound -RemoteAddress 192.168.1.10 -RemotePort 8443 `
  -Protocol TCP -Action Allow
```

## 8. 测试

1. 在管理端添加教室, 复制 psk
2. 启动服务, 几秒后管理端应显示"在线"
3. 不插入 U 盘, 锁定
4. 插入任意 U 盘 → 仍锁定
5. 用 keygen 给该 U 盘签发 `teacher.key` → 复制到 U 盘根目录 → 重新插入 → 解锁
6. 在管理端编辑时间表, 包含当前时间 → 解锁
7. 拔 U 盘 + 时段外 → 重新锁定
8. 任务管理器被禁用, 试 `Ctrl+Shift+Esc` → 应该无反应

## 9. 卸载

```bat
@echo off
schtasks /Delete /TN "SeewofWatchdog" /F
sc stop SeewofAgent
sc delete SeewofAgent
echo 已卸载服务. 如需清除程序, 删除 C:\ProgramData\SeewofAgent 即可.
pause
```

## 10. 故障排查

| 现象 | 排查 |
|------|------|
| 服务无法启动 | 检查 `logs/seewof.log`; 大概率是 PyQt5 / pywin32 未装好 |
| 心跳一直 offline | 检查 `server.base_url` / 端口 / 防火墙; 用 curl 测试 |
| U 盘插入不识别 | `python -m agent.usbdiag --drive E` 看 serial 是否能读出 |
| 触摸拦不住 | SetupAPI 需要 SYSTEM 权限, 服务以 LocalSystem 跑应该 OK |
| Win 任务管理器能用 | 检查 `protection.apply` 是否抛 PermissionError; 改用 GPO 推送 |
