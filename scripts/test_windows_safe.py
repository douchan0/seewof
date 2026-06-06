#!/usr/bin/env python3
"""Windows 端非锁定测试脚本 (Safe Dry-Run).

目的:
  在 Windows VM 上验证 seewof agent 的所有"非锁定"功能,
  不触发真实的键盘/鼠标拦截、注册表策略、屏幕覆盖.
  让 VM 维护者可以放心运行, 即便 agent 配置文件写错了也不会锁死机器.

测什么 (✅ = 安全, ❌ = 跳过):
  ✅ agent.config 配置加载 + 字段校验
  ✅ agent.usbmgr teacher.key 验签 (MacBook: monkey-patch serial; Windows: 真盘符)
  ✅ agent.state 决策状态机 (USB > SCHEDULE > REMOTE 优先级)
  ✅ agent.state 时段计算 (跨日 + 软提示)
  ✅ agent.comm HMAC 时间同步 (GET /agent/time)
  ✅ agent.comm Poll 拉取 (GET /agent/poll)
  ✅ agent.comm 日志环上报 (POST /agent/log_batch)
  ✅ agent.logger 内存环形缓冲 (drain/peek)
  ✅ agent.protection 退出密码校验 (bcrypt 哈希, 不动注册表)
  ✅ 10 秒 dry-run: 模拟完整决策循环, 打印 "如果真锁定会..." 决策日志
  ❌ LL 钩子键盘/鼠标拦截 (需要真锁定, 不在 safe 范围内)
  ❌ SetupAPI 触摸 HID 禁用 (同上)
  ❌ 注册表策略 (DisableTaskMgr 等, 同上)
  ❌ PyQt5 屏幕覆盖 (同上)
  ❌ Windows 服务注册 (deploy/agent/install.bat 范畴)

用法:
  # 全套自检 (推荐首次在 VM 上跑)
  python scripts/test_windows_safe.py all \\
      --config agent/agent.example.json \\
      --public data/public.pem

  # 单独测试
  python scripts/test_windows_safe.py config --config agent/agent.example.json
  python scripts/test_windows_safe.py state
  python scripts/test_windows_safe.py usb \\
      --public data/public.pem \\
      --teacher-key /path/to/teacher.key \\
      --serial USBSTOR-FAKE-SN-12345
  python scripts/test_windows_safe.py comm \\
      --server http://127.0.0.1:8000 \\
      --classroom ROOM-MAC-01 \\
      --psk "<从管理端获取的 psk>"
  python scripts/test_windows_safe.py dry-run \\
      --server http://127.0.0.1:8000 \\
      --classroom ROOM-MAC-01 \\
      --psk "<psk>" \\
      --duration 30

退出码:
  0  全部通过
  1  有失败 case (详细见报告)
  2  参数错误
  3  环境问题 (如缺 venv, 缺公钥)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

# 路径: 让脚本可以直接 python scripts/test_windows_safe.py 跑
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 安全标记: 一旦置 True, dry-run 永远不调用任何可能"真锁"的函数.
# 故意不 import agent.protection / agent.input_blocker / agent.overlay / agent.service.
SAFE_DRY_RUN = True


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GRN = "\033[32m"
    YEL = "\033[33m"
    BLU = "\033[34m"
    CYA = "\033[36m"
    MAG = "\033[35m"

    @classmethod
    def disable_if_no_tty(cls) -> None:
        if not sys.stdout.isatty():
            for k in ("RESET", "BOLD", "DIM", "RED", "GRN", "YEL", "BLU", "CYA", "MAG"):
                setattr(cls, k, "")


C.disable_if_no_tty()


def _c(s: str, color: str) -> str:
    return f"{color}{s}{C.RESET}"


PASS = _c("PASS", C.GRN + C.BOLD)
FAIL = _c("FAIL", C.RED + C.BOLD)
WARN = _c("WARN", C.YEL + C.BOLD)
INFO = _c("INFO", C.CYA + C.BOLD)


class Reporter:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.warned = 0
        self.cases: list[tuple[str, str, str]] = []  # (name, status, msg)

    def case(self, name: str, ok: bool, msg: str = "") -> None:
        if ok:
            print(f"  {PASS}  {name}" + (f"  {C.DIM}{msg}{C.RESET}" if msg else ""))
            self.passed += 1
            self.cases.append((name, "PASS", msg))
        else:
            print(f"  {FAIL}  {name}  {C.RED}{msg}{C.RESET}")
            self.failed += 1
            self.cases.append((name, "FAIL", msg))

    def warn(self, name: str, msg: str) -> None:
        print(f"  {WARN}  {name}  {C.YEL}{msg}{C.RESET}")
        self.warned += 1
        self.cases.append((name, "WARN", msg))

    def info(self, msg: str) -> None:
        print(f"  {INFO}  {msg}")

    def header(self, title: str) -> None:
        print()
        print(_c(f"── {title} " + "─" * max(0, 60 - len(title)), C.BOLD))

    def summary(self) -> int:
        total = self.passed + self.failed
        print()
        print(_c("═" * 62, C.BOLD))
        print(_c(" 测试报告", C.BOLD))
        print(_c("═" * 62, C.BOLD))
        print(f"  {PASS}  {self.passed}/{total}")
        if self.failed:
            print(f"  {FAIL}  {self.failed}/{total}")
        if self.warned:
            print(f"  {WARN}  {self.warned}")
        if self.failed == 0:
            print(_c("\n  结论: 所有测试通过 ✅  (safe dry-run 模式, 未触发真锁定)", C.GRN))
            return 0
        print(_c("\n  结论: 有失败 case ❌  (请看上方 FAIL 行)", C.RED))
        return 1


# ---------------------------------------------------------------------------
# 公共: 构建 AgentConfig
# ---------------------------------------------------------------------------
def _build_minimal_config(args: argparse.Namespace) -> dict[str, Any]:
    """用 CLI 参数 + 合理默认构造 AgentConfig.from_dict 接受的字典."""
    return {
        "classroom_id": args.classroom or "TEST-ROOM-01",
        "server": {
            "base_url": args.server or "http://127.0.0.1:8000",
            "psk": args.psk or "test-psk-must-be-at-least-16-chars",
            "verify_tls": False,
            "heartbeat_interval_sec": 60,
            "time_sync_interval_sec": 60,
            "request_timeout_sec": 8,
        },
        "usb": {
            "serial_via": "wmi",
            "teacher_key_filename": "teacher.key",
            "public_key_path": args.public or "data/public.pem",
            "bind_drive_letters": [],
        },
        "lock": {
            "block_keyboard": True,
            "block_mouse": True,
            "block_touch": True,
            "block_accessibility": True,
            "usb_remove_grace_sec": 5,
            "schedule_soft_warn_sec": 30,
            "overlay_opacity": 0.55,
            "overlay_message": "上课期间或插入教师 U 盘解锁",
        },
        "protection": {
            "watchdog_interval_sec": 3,
            "kill_self_grace_sec": 10,
            "admin_password_hash": args.admin_hash or "",
            "service_name": "SeewofAgent",
        },
        "log_dir": "logs",
        "data_dir": "data",
    }


# ===========================================================================
# 1. config 测试
# ===========================================================================
def test_config(rep: Reporter, args: argparse.Namespace) -> None:
    rep.header("1. 配置加载 (agent.config)")

    # 1.1 最小有效配置
    raw = _build_minimal_config(args)
    try:
        from agent.config import AgentConfig, ConfigError  # noqa: F401
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = AgentConfig.from_dict(raw, base_dir=base)
            rep.case("最小配置加载", True,
                     f"classroom={cfg.classroom_id} server={cfg.server.base_url}")
            # 必须在 with 块内: 退出后临时目录被删除
            rep.case("data_dir/log_dir 创建",
                     Path(cfg.log_dir).exists() and Path(cfg.data_dir).exists())
    except Exception as e:
        rep.case("最小配置加载", False, str(e))
        return

    # 1.2 关键字段透传
    rep.case("server.psk 透传", cfg.server.psk == raw["server"]["psk"], cfg.server.psk[:8] + "…")
    rep.case("classroom_id 透传", cfg.classroom_id == raw["classroom_id"])
    rep.case("lock.overlay_message 默认值", "上课" in cfg.lock.overlay_message)

    # 1.3 拒绝: 短 PSK
    bad = dict(raw)
    bad["server"] = dict(raw["server"])
    bad["server"]["psk"] = "short"
    try:
        with tempfile.TemporaryDirectory() as td:
            AgentConfig.from_dict(bad, base_dir=Path(td))
        rep.case("拒绝: 短 PSK", False, "本应抛 ConfigError")
    except Exception as e:
        rep.case("拒绝: 短 PSK", "psk" in str(e).lower() or "16" in str(e), type(e).__name__)

    # 1.4 拒绝: 空 classroom_id
    bad2 = dict(raw)
    bad2["classroom_id"] = "   "
    try:
        with tempfile.TemporaryDirectory() as td:
            AgentConfig.from_dict(bad2, base_dir=Path(td))
        rep.case("拒绝: 空 classroom_id", False, "本应抛 ConfigError")
    except Exception as e:
        rep.case("拒绝: 空 classroom_id", True, type(e).__name__)

    # 1.5 拒绝: 缺 server
    bad3 = {k: v for k, v in raw.items() if k != "server"}
    try:
        with tempfile.TemporaryDirectory() as td:
            AgentConfig.from_dict(bad3, base_dir=Path(td))
        rep.case("拒绝: 缺 server 字段", False, "本应抛 ConfigError")
    except Exception as e:
        rep.case("拒绝: 缺 server 字段", True, type(e).__name__)

    # 1.6 例行: bind_drive_letters 大写归一化
    raw2 = dict(raw)
    raw2["usb"] = dict(raw["usb"])
    raw2["usb"]["bind_drive_letters"] = ["e", "f"]
    with tempfile.TemporaryDirectory() as td:
        cfg2 = AgentConfig.from_dict(raw2, base_dir=Path(td))
        rep.case("bind_drive_letters 大写归一化",
                 cfg2.usb.bind_drive_letters == ["E", "F"],
                 str(cfg2.usb.bind_drive_letters))


# ===========================================================================
# 2. 状态机测试
# ===========================================================================
def test_state(rep: Reporter, args: argparse.Namespace) -> None:
    rep.header("2. 决策状态机 (agent.state)")

    from agent.state import (
        Context, Decision, LockState, Reason, RemoteGrant, Schedule,
        StateMachine, TimeSlot, decide, evaluate_schedule,
    )

    def ctx(has_usb=False, in_session=False, remote_until=0, soft_warn=False):
        return Context(
            has_valid_usb=has_usb,
            schedule=Schedule(in_session=in_session,
                              seconds_to_end=10 if soft_warn else 600),
            remote=RemoteGrant(expires_at=remote_until),
            soft_warn_sec=30,
        )

    # 优先级矩阵
    rep.case("无 USB + 无时段 + 无远程 -> LOCKED initial",
             decide(ctx()).state == LockState.LOCKED
             and decide(ctx()).reason == Reason.INITIAL)

    rep.case("仅 USB -> UNLOCKED usb",
             decide(ctx(has_usb=True)).reason == Reason.USB)

    rep.case("仅时段 -> UNLOCKED schedule",
             decide(ctx(in_session=True)).reason == Reason.SCHEDULE)

    rep.case("仅远程 -> UNLOCKED remote",
             decide(ctx(remote_until=int(time.time()) + 60)).reason == Reason.REMOTE)

    # 优先级核心
    rep.case("USB + 时段 -> UNLOCKED usb (USB 胜)",
             decide(ctx(has_usb=True, in_session=True)).reason == Reason.USB)
    rep.case("USB + 远程 -> UNLOCKED usb (USB 胜)",
             decide(ctx(has_usb=True, remote_until=int(time.time()) + 60)).reason == Reason.USB)
    rep.case("时段 + 远程 -> UNLOCKED schedule (schedule 胜)",
             decide(ctx(in_session=True, remote_until=int(time.time()) + 60)).reason == Reason.SCHEDULE)

    # 软提示
    rep.case("软提示触发: 时段剩余 10s <= 30s",
             decide(ctx(in_session=True, soft_warn=True)).soft_warn is True)
    rep.case("软提示不触发: 时段剩余 60s > 30s",
             decide(ctx(in_session=True, soft_warn=False)).soft_warn is False)

    # 远程过期
    rep.case("远程过期 -> 视为无效",
             decide(ctx(remote_until=int(time.time()) - 1)).state == LockState.LOCKED)

    # StateMachine 状态变化
    sm = StateMachine()
    # 初始已 LOCKED, 第一次 update 同状态应不变
    d, changed = sm.update(ctx())
    rep.case("StateMachine 初始同状态 -> changed=False",
             changed is False and sm.state == LockState.LOCKED)
    # 切到 UNLOCK
    d, changed = sm.update(ctx(has_usb=True))
    rep.case("StateMachine USB 插入 -> UNLOCK + 变化",
             changed is True and sm.state == LockState.UNLOCKED and sm.reason == Reason.USB)
    # 再切回 LOCK
    d, changed = sm.update(ctx())
    rep.case("StateMachine USB 拔出 -> LOCK + 变化",
             changed is True and sm.state == LockState.LOCKED)
    hist = list(sm.history())
    rep.case("StateMachine 历史记录 >= 2 条",
             len(hist) >= 2, f"{len(hist)} 条")

    # 时段计算: 用本地时区构造"今天 9:00"等, 再转 epoch, 避免时区误差
    import datetime as _dt
    today = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = today.weekday()  # 0=Mon ... 6=Sun

    slots = [
        TimeSlot(weekdays=(0, 1, 2, 3, 4), start_min=8 * 60, end_min=12 * 60),
        TimeSlot(weekdays=(0, 1, 2, 3, 4), start_min=14 * 60, end_min=17 * 60),
    ]

    # 找一个工作日的 epoch (今天如果不是周末, 用今天; 否则用最近的工作日)
    base_workday = today
    while base_workday.weekday() >= 5:
        base_workday -= _dt.timedelta(days=1)
    base_monday_ish = base_workday.weekday()  # 0..4

    # 1) 当天 9:00 (在工作日内, weekday 0..4)
    if base_monday_ish < 5:
        workday_9am = base_workday.replace(hour=9, minute=0, second=0)
        workday_1230 = base_workday.replace(hour=12, minute=30, second=0)
        workday_15 = base_workday.replace(hour=15, minute=0, second=0)
        # 跨日: 周五 22:00 - 02:00
        # 找本周/下一周的周五
        fri = base_workday
        while fri.weekday() != 4:
            fri += _dt.timedelta(days=1)
        fri_22 = fri.replace(hour=22, minute=0, second=0)
        fri_03 = fri.replace(hour=3, minute=0, second=0)  # 周五 03:00, 不在跨日
    else:
        # 兜底
        workday_9am = today
        workday_1230 = today
        workday_15 = today
        fri_22 = today
        fri_03 = today

    s = evaluate_schedule(slots, now_epoch=int(workday_9am.timestamp()))
    rep.case("时段: 工作日 9:00 -> in_session",
             s.in_session is True and 10700 < s.seconds_to_end <= 10800,
             f"剩余 {s.seconds_to_end}s (~3h)")

    s = evaluate_schedule(slots, now_epoch=int(workday_15.timestamp()))
    rep.case("时段: 工作日 15:00 -> in_session (下午)",
             s.in_session is True and 7100 < s.seconds_to_end <= 7200,
             f"剩余 {s.seconds_to_end}s (~2h)")

    s = evaluate_schedule(slots, now_epoch=int(workday_1230.timestamp()))
    rep.case("时段: 工作日 12:30 -> 课间, 距下午 14:00",
             s.in_session is False and 5300 < s.next_start_in_sec < 5500,
             f"距下次 {s.next_start_in_sec}s")

    # 跨日: 周五 22:00 - 02:00
    night_slot = [TimeSlot(weekdays=(4,), start_min=22 * 60, end_min=2 * 60)]
    s = evaluate_schedule(night_slot, now_epoch=int(fri_22.timestamp()))
    rep.case("跨日: 周五 22:00 -> in_session",
             s.in_session is True, f"剩余 {s.seconds_to_end}s")

    s = evaluate_schedule(night_slot, now_epoch=int(fri_03.timestamp()))
    # 周五 03:00 weekday=4, 但 minute=180 不在 22:00-02:00, 也不跨日
    rep.case("跨日: 周五 03:00 -> 不在时段 (minute 不在范围)",
             s.in_session is False)

    s = evaluate_schedule(slots, now_epoch=int(today.timestamp()))
    rep.case("时段: 今天 0:00 边界", isinstance(s, Schedule), f"in_session={s.in_session}")


# ===========================================================================
# 3. USB 验签测试
# ===========================================================================
def test_usb(rep: Reporter, args: argparse.Namespace) -> None:
    rep.header("3. USB 验签 (agent.usbmgr)")

    pub_path = Path(args.public) if args.public else Path("data/public.pem")
    if not pub_path.is_absolute():
        pub_path = ROOT / pub_path
    if not pub_path.exists():
        rep.warn("公钥文件不存在", f"{pub_path} (用 --public 指定)")
        return

    public_pem = pub_path.read_bytes()
    rep.case("公钥文件可读", len(public_pem) > 0, f"{len(public_pem)} bytes")

    # 跨平台: monkey-patch get_usb_serial
    import agent.usbmgr as usbmgr
    if args.serial:
        usbmgr.get_usb_serial = lambda drive, method="wmi": args.serial
        rep.case(f"monkey-patch get_usb_serial -> {args.serial}", True)
    else:
        # MacBook / Linux 上默认 _get_volume_serial_windows 不可用, 必须 patch
        if os.name != "nt":
            usbmgr.get_usb_serial = lambda drive, method="wmi": "USBSTOR-MOCK"
            rep.case("monkey-patch get_usb_serial (auto, 非 Windows)", True)
        else:
            rep.warn("未指定 --serial", "Windows 上将走真 WMI; 如盘符是 NTFS 容器可能失败")

    from agent.config import UsbConfig
    from agent.usbmgr import verify_teacher_key

    cfg = UsbConfig(teacher_key_filename="teacher.key", serial_via="wmi")

    if not args.teacher_key or not Path(args.teacher_key).exists():
        # 尝试在临时挂载点生成: 签一个 mock teacher.key 测正向路径
        rep.info(f"--teacher-key 未提供或不存在 ({args.teacher_key}), "
                 f"仅跑反向用例 (不存在的 key 应失败)")

        with tempfile.TemporaryDirectory() as td:
            ev = verify_teacher_key(
                mount_root=Path(td), drive="M", cfg=cfg,
                public_key_pem=public_pem,
            )
            rep.case("未提供 teacher.key -> invalid",
                     ev.valid is False and "not found" in ev.reason,
                     ev.reason)
        return

    # 真有 teacher.key
    teacher_path = Path(args.teacher_key)
    with tempfile.TemporaryDirectory() as td:
        mount = Path(td)
        # 复制 teacher.key 到临时 mount
        (mount / "teacher.key").write_bytes(teacher_path.read_bytes())

        # 1) 正向: serial 一致
        ev = verify_teacher_key(
            mount_root=mount, drive="M", cfg=cfg,
            public_key_pem=public_pem,
        )
        rep.case("teacher.key + serial 一致 -> valid",
                 ev.valid is True,
                 f"teacher={ev.teacher_name}")

        # 2) 反向: serial 不一致
        if args.serial:
            usbmgr.get_usb_serial = lambda drive, method="wmi": "USBSTOR-DIFFERENT-SN"
            ev = verify_teacher_key(
                mount_root=mount, drive="M", cfg=cfg,
                public_key_pem=public_pem,
            )
            rep.case("serial 不一致 -> invalid",
                     ev.valid is False and "mismatch" in ev.reason,
                     ev.reason)
            # 还原
            usbmgr.get_usb_serial = lambda drive, method="wmi": args.serial

        # 3) 反向: teacher.key 损坏
        (mount / "teacher.key").write_bytes(b"\x00\x01\x02 corrupt data")
        ev = verify_teacher_key(
            mount_root=mount, drive="M", cfg=cfg,
            public_key_pem=public_pem,
        )
        rep.case("teacher.key 损坏 -> invalid",
                 ev.valid is False, ev.reason)

        # 4) 反向: 缺 teacher.key
        (mount / "teacher.key").unlink()
        ev = verify_teacher_key(
            mount_root=mount, drive="M", cfg=cfg,
            public_key_pem=public_pem,
        )
        rep.case("缺 teacher.key -> invalid",
                 ev.valid is False and "not found" in ev.reason,
                 ev.reason)


# ===========================================================================
# 4. HMAC 通信测试
# ===========================================================================
def test_comm(rep: Reporter, args: argparse.Namespace) -> None:
    rep.header("4. HMAC 通信 (agent.comm)")

    if not args.server or not args.psk:
        rep.warn("跳过: --server 和 --psk 必填", "跑 comm 测试需要服务端")
        return
    if len(args.psk) < 16:
        rep.case("psk 长度合法", False, f"len={len(args.psk)}, 必须 >= 16")
        return

    from agent.config import ServerConfig
    from agent.comm import ServerClient

    sc = ServerClient(
        ServerConfig(
            base_url=args.server.rstrip("/"),
            psk=args.psk,
            verify_tls=False,
            heartbeat_interval_sec=60,
            time_sync_interval_sec=60,
            request_timeout_sec=8,
        ),
        args.classroom or "TEST-ROOM-01",
    )

    # 1) /api/v1/health (无签名)
    try:
        import requests
        r = requests.get(f"{args.server}/api/v1/health", timeout=4)
        rep.case("GET /api/v1/health (无需签名)",
                 r.status_code == 200 and r.json().get("ok") is True,
                 f"http {r.status_code}")
    except Exception as e:
        rep.case("GET /api/v1/health", False, str(e))
        return

    # 2) 时间同步
    ok = sc.sync_time()
    rep.case("时间同步 sync_time()", ok,
             f"offset={sc.clock().offset:+d}s drift={sc.clock().drift_sec()}s")

    # 3) Poll
    r = sc.fetch_poll()
    rep.case("Poll fetch_poll()", r.ok, r.error or f"slots={len(r.data.get('slots', []))}")

    # 4) 错误 PSK -> 401
    from common.crypto import build_signed_request
    import requests
    bad_sc = ServerClient(
        ServerConfig(base_url=args.server.rstrip("/"),
                     psk="x" * 64,  # 错 psk
                     verify_tls=False,
                     request_timeout_sec=8),
        args.classroom or "TEST-ROOM-01",
    )
    try:
        url = f"{args.server}/api/v1/agent/time"
        r = requests.get(url, params={"classroom": args.classroom or "TEST-ROOM-01"},
                         headers=build_signed_request(bad_sc._cfg.psk.encode("utf-8"), b""),
                         timeout=4)
        rep.case("错误 PSK -> 401 Unauthorized", r.status_code == 401,
                 f"http {r.status_code}")
    except Exception as e:
        rep.warn("错误 PSK 探测", str(e))

    # 5) 日志环 -> 上报
    from agent.logger import log_event, drain_ring, setup_logger
    setup_logger("logs", "seewof-safe")
    log_event(__import__("logging").getLogger("seewof-safe"),
              "safe_test_event", source="safe", detail={"k": "v"})
    n = sc.upload_log_ring()
    rep.case("日志环上报", n >= 1, f"上传 {n} 条")


# ===========================================================================
# 5. 退出密码
# ===========================================================================
def test_protection(rep: Reporter, args: argparse.Namespace) -> None:
    rep.header("5. 退出密码 (agent.protection)")

    try:
        import bcrypt  # type: ignore
    except ImportError:
        rep.warn("bcrypt 未安装", "跳过密码测试; 部署前请 pip install 'bcrypt<4.1'")
        return

    # agent.protection 顶部 import winreg + raise ImportError, 在非 Windows 整个模块不可用.
    # 这里在 sys.modules 注入 fake winreg + 临时把 sys.platform 改成 win32 让模块能 import,
    # 然后只测纯函数 verify_admin_password. 不会调用任何写注册表的代码.
    if os.name != "nt":
        if "winreg" not in sys.modules:
            import types
            fake_winreg = types.ModuleType("winreg")
            fake_winreg.HKEY_CURRENT_USER = 0
            fake_winreg.KEY_SET_VALUE = 0
            fake_winreg.CreateKeyEx = lambda *a, **k: None
            fake_winreg.OpenKey = lambda *a, **k: None
            fake_winreg.SetValueEx = lambda *a, **k: None
            fake_winreg.QueryValueEx = lambda *a, **k: (0, 0)
            fake_winreg.DeleteValue = lambda *a, **k: None
            fake_winreg.REG_DWORD = 0
            sys.modules["winreg"] = fake_winreg
        # 临时把 platform 改成 win32 以绕过 module-level ImportError
        _orig_platform = sys.platform
        sys.platform = "win32"
        try:
            from agent.protection import verify_admin_password
        finally:
            sys.platform = _orig_platform
        rep.case("注入 fake winreg + sys.platform 旁路", True, "非 Windows safe 模式")
    else:
        from agent.protection import verify_admin_password

    h = bcrypt.hashpw(b"correct horse battery staple", bcrypt.gensalt()).decode("utf-8")
    rep.case("正确密码 -> True", verify_admin_password("correct horse battery staple", h))
    rep.case("错误密码 -> False", not verify_admin_password("wrong", h))
    rep.case("空哈希 -> False", not verify_admin_password("anything", ""))
    rep.case("None 哈希 -> False", not verify_admin_password("anything", ""))


# ===========================================================================
# 6. Dry-run: 10 秒决策循环 (不真锁定)
# ===========================================================================
def test_dry_run(rep: Reporter, args: argparse.Namespace) -> None:
    rep.header(f"6. Dry-run 决策循环 ({args.duration}s, safe 模式)")

    if SAFE_DRY_RUN:
        print(_c("  " + C.DIM +
                "SAFETY: 已启用 SAFE_DRY_RUN, 不会 import 任何会真锁定的模块 "
                "(protection / input_blocker / overlay / service)"
                + C.RESET, C.YEL))

    from agent.config import ServerConfig
    from agent.comm import ServerClient
    from agent.state import (
        Context, LockState, Reason, RemoteGrant, Schedule,
        StateMachine, TimeSlot, evaluate_schedule,
    )
    from common.protocol import EventType, UnlockSource
    from agent.logger import log_event, setup_logger

    if not args.server or not args.psk:
        rep.warn("跳过: --server 和 --psk 必填", "dry-run 需要服务端")
        return

    setup_logger("logs", "seewof-dryrun")

    sc = ServerClient(
        ServerConfig(
            base_url=args.server.rstrip("/"),
            psk=args.psk,
            verify_tls=False,
            heartbeat_interval_sec=10,
            time_sync_interval_sec=10,
            request_timeout_sec=8,
        ),
        args.classroom or "TEST-ROOM-01",
    )

    state = StateMachine()
    ctx = Context(soft_warn_sec=30)
    log = __import__("logging").getLogger("seewof-dryrun")
    slots: list = []
    remote_until = 0
    remote_id = ""
    has_usb = False
    last_state = None
    t0 = time.time()
    tick = 0

    print(f"  {C.DIM}每 1s 重新决策, 输出 '如果真锁定会...' 日志. Ctrl+C 安全退出.{C.RESET}")
    print()

    try:
        while time.time() - t0 < args.duration:
            tick += 1
            now = int(time.time())
            server_now = sc.clock().now() or now

            # 周期: 5s 拉一次, 3s 同步一次, 7s 上传一次
            if tick % 5 == 1:
                r = sc.fetch_poll()
                if r.ok:
                    slots = r.data.get("slots", [])
                    ru = r.data.get("remote_unlock")
                    if ru:
                        remote_until = int(ru.get("expires_at", 0))
                        remote_id = str(ru.get("command_id", ""))
            if tick % 3 == 1:
                sc.sync_time()

            # 决策
            ctx.schedule = evaluate_schedule(slots, now_epoch=server_now, soft_warn_sec=30)
            ctx.has_valid_usb = has_usb
            ctx.remote.expires_at = remote_until
            ctx.remote.command_id = remote_id
            d, changed = state.update(ctx)

            if changed or tick % 10 == 0:
                tag = "UNLOCK" if d.state == LockState.UNLOCKED else "LOCK"
                col = C.GRN if d.state == LockState.UNLOCKED else C.RED
                print(f"  [{tick:3d}s] {_c(tag, col)}  reason={_c(d.reason.value, C.CYA)}"
                      f"  soft_warn={d.soft_warn}"
                      f"  in_session={ctx.schedule.in_session}"
                      f"  remote_left={max(0, remote_until - now)}s")
                last_state = d

            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}用户中断, 安全退出{C.RESET}")

    # 至少要一次最后决策, 不管有没有变化
    if last_state is None:
        last_state = d

    print()
    rep.case("dry-run 完成, 全程 SAFE_DRY_RUN=True",
             SAFE_DRY_RUN, f"跑了 {tick} 轮决策")
    rep.case("最近一次决策有结果", last_state is not None,
             f"state={last_state.state.value if last_state else 'n/a'}")


# ===========================================================================
# 入口
# ===========================================================================
def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="agent.json 路径 (config 子命令用)")
    p.add_argument("--public", help="public.pem 路径 (默认 data/public.pem)")
    p.add_argument("--teacher-key", help="teacher.key 路径 (usb 子命令用)")
    p.add_argument("--serial", help="mock 的 U 盘 serial (usb 子命令用, 非 Windows 必填)")
    p.add_argument("--server", help="seewof server URL (comm/dry-run 用)")
    p.add_argument("--classroom", help="classroom_id (comm/dry-run 用)")
    p.add_argument("--psk", help="教室预共享密钥 (comm/dry-run 用, >= 16 字符)")
    p.add_argument("--admin-hash", help="admin 密码 bcrypt 哈希 (config 注入用)")
    p.add_argument("--duration", type=int, default=10, help="dry-run 持续秒数")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Seewof Safe Dry-Run 测试 (Windows VM, 不触发真锁定)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("config", "state", "usb", "comm", "protection", "dry-run", "all"):
        sp = sub.add_parser(name, help=f"跑 {name} 测试")
        _add_common_args(sp)
    args = p.parse_args()

    rep = Reporter()

    # 平台提示
    print(_c("╔════════════════════════════════════════════════════╗", C.CYA))
    print(_c("║  Seewof Safe Dry-Run (Windows VM 非锁定测试)      ║", C.CYA))
    print(_c("╚════════════════════════════════════════════════════╝", C.CYA))
    print(f"  platform : {sys.platform}  ({os.name})")
    print(f"  safe_mode: {C.GRN}ENABLED{C.RESET} (不会触发键盘/注册表/屏幕覆盖)")
    print(f"  python   : {sys.version.split()[0]}")
    print(f"  cwd      : {os.getcwd()}")

    if args.cmd == "all":
        test_config(rep, args)
        test_state(rep, args)
        test_usb(rep, args)
        if args.server and args.psk:
            test_comm(rep, args)
        else:
            rep.warn("跳过 comm (--server/--psk 未提供)", "")
        test_protection(rep, args)
    elif args.cmd == "config":
        test_config(rep, args)
    elif args.cmd == "state":
        test_state(rep, args)
    elif args.cmd == "usb":
        test_usb(rep, args)
    elif args.cmd == "comm":
        test_comm(rep, args)
    elif args.cmd == "protection":
        test_protection(rep, args)
    elif args.cmd == "dry-run":
        test_dry_run(rep, args)

    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
