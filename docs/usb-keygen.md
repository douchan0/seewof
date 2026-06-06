# U 盘签发流程

## 流程概览

```
[教师/管理员]                      [教室电脑]                    [管理端]
  1. 把 U 盘插到教室电脑 ──────►
                                  2. 读出 serial
  3. 把 serial 复制到管理端 ────►
                                                           4. 登记 + 签发
  5. 下载 teacher.key ◄────────
  6. 把 teacher.key 复制到 U 盘根目录
  7. 插入 U 盘到教室电脑 ──────► 8. 验签 + 解锁
```

## 步骤详解

### 1) 在教室电脑读出 U 盘硬件序列号

把待签发的 U 盘插到教室电脑前置 USB 口, 在该电脑上以管理员运行:

```powershell
cd C:\ProgramData\SeewofAgent
.\python\python.exe -m agent.usbdiag --drive E
```

输出形如:

```
drive       : E:
serial      : USBSTOR\DISK&VEN_Kingston&PROD_DataTraveler_3.0\ABCDEF123456&0
```

把 `serial` 整行复制.

> 注意: 同一型号不同 U 盘 serial 不同; 同一支 U 盘格式化不改变 serial.

### 2) 在管理端添加并签发

**方法 A: Web 界面 (推荐)**

1. 登录管理端, 菜单 → **U 盘授权 → 添加**
2. 粘贴 serial, 填教师工号和姓名, 选择过期时间 (默认永不过期)
3. 点 **下载 teacher.key**, 保存到本地

**方法 B: CLI**

```bash
cd /opt/seewof
source venv/bin/activate
python -m server.tools.keygen issue \
  --private data/private.pem \
  --serial "USBSTOR\DISK&VEN_Kingston&PROD_DataTraveler_3.0\ABCDEF123456&0" \
  --teacher-id T001 \
  --teacher-name "张老师" \
  --days 365 \
  --out teacher.key
```

输出 `teacher.key` 文件.

### 3) 复制到 U 盘

把 `teacher.key` 拷贝到 U 盘**根目录** (不要建子目录). U 盘里应只有这一个文件 (或与教学资料共存均可, 不影响).

### 4) 测试

将 U 盘从教室电脑拔出再插入:

- Web 端 **日志中心** 应出现 `usb_verify_ok`
- 教室电脑从锁定变为解锁
- 拔 U 盘, 5 秒后重新锁定

### 5) 多位教师 / 多支 U 盘

重复以上步骤. 每位教师可持多支 U 盘 (每支独立 serial), 每支都签发独立的 `teacher.key` 并放在对应 U 盘根目录.

### 6) 吊销 / 离职

Web 端 → **U 盘授权** → 对应记录 → **吊销**. 吊销后该 serial 的 `teacher.key` 立即失效.

**注意**: 吊销是"逻辑失效", 即教室端会拒绝该 serial 的 teacher.key. 如果担心恶意, 同时在管理端**删除**该记录, 并考虑吊销后物理上销毁 U 盘上的 teacher.key 文件.

### 7) 异常处理

| 现象 | 原因 |
|------|------|
| 教室端日志 `usb_verify_fail: cannot read usb serial` | WMI 未安装或权限不够, 教室端需以 SYSTEM 跑 |
| `usb_verify_fail: teacher.key not found` | teacher.key 不在 U 盘根目录, 或文件名拼写错误 |
| `usb_verify_fail: serial mismatch` | 教室端读出的 serial 与签发时的 serial 不一致. 重新走第 1 步 |
| `usb_verify_fail: rsa signature invalid` | 公钥不匹配, 或 teacher.key 被改过. 重新下载 |
| `usb_verify_fail: teacher.key expired` | 过期. 在 Web 端重签一个 |
| 插入但教室端无任何日志 | USB 监听器 bind_drive_letters 配置不正确 |
