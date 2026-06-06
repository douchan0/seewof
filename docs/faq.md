# 常见问题 (FAQ)

## 教室端

### Q1: 学生按住电源键 5 秒强制关机怎么办?

不可避免. 我们能保证的是: 重启后服务自动恢复 (Windows 服务 + watchdog 计划任务). 教室电脑 BIOS 最好关闭"按电源键关机"改为"按电源键休眠/不操作".

### Q2: 学生拔掉网线/关 WiFi 怎么办?

服务降级但**不会自动解锁**:
- 时钟同步失败累计 3 次 → 强制时段为空 → 锁定
- 仍可通过 U 盘解锁 (不依赖网络)
- 网络恢复后自动补传日志

### Q3: 学生进安全模式怎么办?

由于我们以 Windows 服务运行, 安全模式下也会启动 (因为 SafeBoot\Network 注册了我们的服务). 如需更严:

```reg
Windows Registry Editor Version 5.00
[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal\SeewofAgent]
@="Service"
```

并通过组策略 `Computer Configuration → Windows Settings → Security Settings → Local Policies → Security Options` 禁用 F8 / Shift+F8 启动菜单.

### Q4: 学生用 U 盘启动 Linux Live 怎么办?

BIOS 密码 + 关闭 USB 启动. 我们对这种情况无能为力 (也不应该由软件解决).

### Q5: 班级管理软件需要管理员权限吗?

不需要. 我们用 LL 钩子拦截的是"输入", 不是"应用" — 班级管理软件照常运行, 只是学生不能点它.

### Q6: 教师忘记带 U 盘, 急需上课怎么办?

方案 A: 在管理端**远程解锁** (临时指令)
方案 B: 临时把教师 U 盘文件复制到任意 U 盘 — **不**有效, 因为 serial 不匹配 (这就是 U 盘验证的意义)
方案 C: 应急密码 (后续可加)

## 管理端

### Q7: 数据库用什么? 能用 PostgreSQL 吗?

当前用 SQLite, 适合 1~50 个教室的场景. 上百个教室或需高可用, 改 PostgreSQL: 修改 `server/app/db.py::init_engine` 的 DSN, 重新创建表 (我们的 ORM 与具体方言无关).

### Q8: 多个管理端能部署吗?

可以, 用共享数据库 (PostgreSQL) + 共享数据目录. SQLite 单机 OK, 多机需切换.

### Q9: 私钥丢了怎么办?

**灾难性事件**: 之前签发的所有 teacher.key 全部失效, 必须重新签发.

缓解:
- 私钥加密存储 (用 `cryptography` 的 `BestAvailableEncryption`)
- 定期备份 `data/private.pem` 到加密 U 盘
- 使用 HSM (硬件加密机, 企业级)

### Q10: 时钟同步失败率高, 怎么处理?

- 教室端 `request_timeout_sec` 调大 (默认 8s)
- 检查网络: 教室端 → 管理端 8443 是否可达
- 检查管理端证书: 自签证书需要 `verify_tls: false` 或导入 CA

## 安全

### Q11: 教室端 PSK 泄露了怎么办?

在管理端 **教室设备 → rotate psk**, 教室端重新部署 (修改 `agent.json`).

### Q12: 教室电脑被学生拿走了 / 硬盘被拆走读怎么办?

- BIOS 密码
- BitLocker 加密系统盘
- 公钥 `public.pem` 不含任何机密 (RSA 公钥本来就是公开的), 泄露无影响
- PSK 私钥 `data/private.pem` 仅在管理端, 教室电脑不存

### Q13: 有人伪造管理端指令, 教室端会响应吗?

不会. 所有管理端 → 教室端的请求都带 HMAC 签名, 教室端用 PSK 校验. 没有 PSK 的人无法构造有效签名, 即使截获了合法请求, 5 分钟时间窗口外重放也会被拒.

### Q14: 时钟篡改后能影响决策吗?

不能. 教室端所有时间相关决策 (时段) 都使用 `SmoothedClock.now()`, 它的值由管理端下发的 `server_ts` 决定. 本地 `time.time()` 仅用于非关键场景 (日志时间戳).

## 部署

### Q15: 教室电脑换了, 怎么迁移?

1. 解绑旧电脑: 管理端删除该 classroom
2. 新电脑: 重新安装 agent
3. 新建 classroom 记录, 用新 PSK

旧 U 盘无需重签, 因为 serial 一样.

### Q16: 系统升级 / 补丁后, 服务起不来怎么办?

大概率是 PyQt5 / pywin32 兼容问题. 重新 `pip install --upgrade -r requirements-agent.txt` 即可.
