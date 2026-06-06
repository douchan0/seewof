# 架构设计详解

## 1. 系统角色

| 角色 | 数量 | 部署位置 | 权限 |
|------|------|----------|------|
| 教室端 Agent | N (每教室一台) | 希沃一体机 (Windows 10) | SYSTEM 服务 |
| 管理端 Server | 1 | 教师办公室 Ubuntu | 普通用户 + systemd |
| 教师 U 盘 | M (每位教师 1 支) | 流动 | — |

## 2. 决策优先级详解

需求中给出的优先级是:

> 教师 U 盘 > 上课时段 > 远程手动解锁

实现为状态机中的硬性短路 (`state.py::decide`):

```python
if ctx.has_valid_usb:
    return UNLOCK(USB)              # 第一
if ctx.schedule.in_session:
    return UNLOCK(SCHEDULE)         # 第二
if ctx.remote.active:
    return UNLOCK(REMOTE)           # 第三
return LOCKED                       # 其他
```

任意时刻只受最高优先级信号控制. 拔出 U 盘后, 进入 **5 秒宽限期**, 期间如果时段或远程激活, 仍保持解锁.

## 3. 时钟同步 (防时间篡改)

教室端从不使用本地 `time.time()` 决策. 流程:

```
启动 ──► 拉取管理端 /agent/time ──► 记录 server_ts, agent_ts_before/after
        ──► SmoothedClock.add_sample (中位数滤波)
心跳 ──► 60s 重新同步
失败 ──► consecutive_failures += 1
        ──► 连续 3 次失败 → 强制时段为空 → 锁定
```

教室端使用 `self._clock.now()` 决策时段, 真实时间永远来自管理端.

## 4. 双进程守护

```
┌─────────────────────┐         ┌─────────────────────┐
│  Main Service       │         │  Watchdog           │
│  (SYSTEM 权限服务)   │         │  (SYSTEM 权限进程)   │
│                     │  心跳    │                     │
│  写 data/heartbeat  │◄────────┤  每 3s 检查心跳     │
│  pid, ts            │         │  超时 15s 则拉起     │
└─────────────────────┘         └─────────────────────┘
              ▲                             ▲
              │   Windows Service           │ 计划任务 (开机)
              │   auto start                │ 每分钟触发
              └─────────────────────────────┘
```

任意一方被 kill, 另一方立即重启. 即便两者都被杀, 计划任务 (1 分钟粒度) 会重启 watchdog, watchdog 再拉起主服务.

### 为什么不"做不可杀"

Windows 没有真正的不可杀进程. 任何用户态程序都能被管理员结束. 我们的目标是:
1. 持续监控 + 秒级恢复
2. 以 SYSTEM 权限运行, 学生普通用户看不到
3. 注册策略屏蔽任务管理器/资源管理器

## 5. 输入拦截 (LL 钩子 + 触摸 HID 禁用)

### 5.1 键盘/鼠标 (LL Hook)

`SetWindowsHookExW(WH_KEYBOARD_LL, ...)` 和 `WH_MOUSE_LL` 是**全局**低级别钩子, 在事件分发到目标窗口前触发.

```c
LRESULT CALLBACK KeyboardProc(int nCode, WPARAM wParam, LPARAM lParam) {
    if (nCode == HC_ACTION && locked) {
        // 吞掉
        return 1;
    }
    return CallNextHookEx(NULL, nCode, wParam, lParam);
}
```

- 锁定时: 键盘鼠标事件全部吞掉
- 解锁时: 全部放行
- 调试快捷键 `Ctrl+Alt+Shift+F12` 永远放行 (供维护)

### 5.2 触摸 (SetupAPI)

通过 `SetupDi*` API 枚举所有 HID 设备, 找到 `HID\VID_...&...TOUCH...` 的设备, 调用 `DIF_PROPERTYCHANGE` + `DICS_DISABLE` 临时禁用. 解锁时再启用.

注意:
- 需要管理员/SYSTEM 权限
- 部分硬件禁用后可能需要插拔才能恢复 → 解锁时一定记得恢复 (`agent.stop` 钩子里)

### 5.3 辅助功能屏蔽

注册表策略 (`agent/protection.py`):
- `DisableTaskMgr=1`
- `NoEaseOfAccess=1`
- `NoLockScreen=1`
- `NoRun=1`
- `NoControlPanel=1`
- `NoViewContextMenu=1`

策略在锁定时启用, 解锁时还原 (读旧值保存, 解锁时写回).

## 6. U 盘验证 (RSA-PSS)

### 6.1 数据流

```
管理端:  输入 serial + teacher info
         ↓
         生成 UsbKeyPayload
         ↓ canonical_json
         rsa_sign(PSS, SHA256)
         ↓
         pack_teacher_key = payload_json + "." + base64(sig)
         ↓
         输出 teacher.key (写到 U 盘根目录)

教室端:  插入 U 盘
         ↓
         读 serial (WMI PNPDeviceID)
         读 teacher.key
         ↓
         unpack → (payload, sig)
         ↓
         比对 payload.serial == actual_serial
         rsa_verify(pub, sig, payload.canonical_json())
         ↓
         任何一步失败 → 非法 U 盘 → 不解锁 + 上报日志
```

### 6.2 序列号

默认通过 WMI `Win32_DiskDrive.PNPDeviceID` 获取:
```
USBSTOR\DISK&VEN_Kingston&PROD_DataTraveler_3.0\ABCDEF123456&0
```

这是**硬件级**, 不会因格式化改变, 也不能通过复制文件伪造. 备份方案是卷序列号 (易变, 不推荐).

## 7. 通信协议

### 7.1 HMAC 签名

```
secret = classroom.psk (基共享密钥, 48 字节)

headers:
  X-Seewof-Timestamp: <unix sec>
  X-Seewof-Nonce: <base64 16字节>
  X-Seewof-Signature: <base64 HMAC-SHA256(secret, ts.nonce.body)>
```

- 时间戳允许 5 分钟漂移 (防重放)
- 服务端必须用 `verify_signed_request` 校验
- `secret` 永不暴露在 URL / log 中

### 7.2 消息格式

事件 (agent → server):

```json
{
  "event": "lock" | "unlock" | "usb_insert" | ...,
  "classroom_id": "ROOM-101",
  "agent_ts": 1700000000,
  "server_ts": 0,
  "source": "usb" | "schedule" | "remote",
  "detail": { ... }
}
```

控制指令 (server → agent) 通过 `poll` 接口拉取, 简单可靠.

## 8. 日志

### 8.1 本地

- 按天滚动文件, `logs/seewof.log.YYYY-MM-DD`
- 同时维护内存环形缓冲 2000 条
- 启动/停止/锁定/解锁/U盘验证/网络/时间漂移全部记录

### 8.2 上传

- 每 10 秒批量上传一次
- 上传成功清空缓冲
- 网络断开时本地保留, 恢复后批量补传

### 8.3 管理端存储

- SQLite `event_logs` 表
- 索引 `(classroom_id, server_ts)`
- Web 端支持按教室/事件类型/关键字搜索

## 9. 安全建议

1. **教室端用户**: 教室电脑日常登录账户为**无管理员**标准用户.
   - 这样学生不能改时间、不能装驱动
   - 我们的 SYSTEM 服务拥有所有权限, 不受此限制
2. **UAC**: 保持启用, 阻止学生提权
3. **BitLocker**: 教室电脑系统盘加密, 防离线篡改
4. **防火墙**: 仅开放管理端 → 教室端的入站 (管理端主动 push 不需要), 教室端 → 管理端 8443 出站
5. **PSK 轮换**: 定期 (学期初) 在管理端点 "rotate psk" 重新生成
6. **公钥管理**: `public.pem` 与 `private.pem` 必须严格分离; 私钥放在管理端加密分区
7. **U 盘物理管控**: 不发"备用"U 盘; 一旦教师离职, 在管理端 revoke 该 U 盘 serial

## 10. 限制 / 待改进

- [ ] 当前触摸屏禁用是枚举所有 HID, 可能误伤其它 USB HID 设备 (键盘/鼠标), 但键盘/鼠标由 LL 钩子处理, 不会受影响
- [ ] 前端使用 CDN 预编译版本, 完全无构建; 后续可改 Vite 构建以获得 tree-shaking
- [ ] 离线场景 (教室端彻底无法连管理端) → 时段为空, 只能 U 盘解锁, 这是预期安全行为
- [ ] WebSocket 实时推送未实现, 当前用 60s 心跳; 后续可加
